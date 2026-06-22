"""WebAuthn / YubiKey access control with an admin-managed allowlist.

Enabled only when WEBAUTHN_RP_ID and WEBAUTHN_ORIGIN are set (so local dev
stays open and the hosted deployment is gated). Enrollment is gated by a
shared WEBAUTHN_ENROLL_CODE that the admin gives to authorized users; once a
user enrolls their security key it is added to the allowlist and they can log
in with a tap thereafter.

Credentials are stored in a small SQLite database (put it on a persistent
volume in production). Sessions and the per-ceremony challenge are carried in
signed, http-only cookies.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import settings

SESSION_COOKIE = "spk_session"
CHALLENGE_COOKIE = "spk_chal"
CHALLENGE_MAX_AGE = 300  # seconds


# ---------------------------------------------------------------- credential store


def _db() -> sqlite3.Connection:
    path = Path(settings.auth_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS credentials (
            credential_id TEXT PRIMARY KEY,
            public_key    BLOB NOT NULL,
            sign_count    INTEGER NOT NULL DEFAULT 0,
            label         TEXT,
            created_at    REAL NOT NULL,
            last_used     REAL
        )
        """
    )
    return conn


def credential_count() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0])


def all_credential_ids() -> list[bytes]:
    with _db() as conn:
        rows = conn.execute("SELECT credential_id FROM credentials").fetchall()
    return [base64url_to_bytes(r[0]) for r in rows]


def get_credential(credential_id_b64: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT credential_id, public_key, sign_count, label FROM credentials WHERE credential_id = ?",
            (credential_id_b64,),
        ).fetchone()
    if not row:
        return None
    return {"credential_id": row[0], "public_key": row[1], "sign_count": row[2], "label": row[3]}


def add_credential(credential_id_b64: str, public_key: bytes, sign_count: int, label: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO credentials (credential_id, public_key, sign_count, label, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (credential_id_b64, public_key, sign_count, label, time.time()),
        )


def update_sign_count(credential_id_b64: str, sign_count: int) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE credentials SET sign_count = ?, last_used = ? WHERE credential_id = ?",
            (sign_count, time.time(), credential_id_b64),
        )


# ---------------------------------------------------------------- cookie signing


def _secret() -> str:
    # Fall back to an ephemeral secret if unset (sessions won't survive restarts,
    # but the app still functions). Production should set SESSION_SECRET.
    return settings.session_secret or "dev-ephemeral-" + str(os.getpid())


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt=salt)


def _secure_cookies() -> bool:
    return settings.webauthn_origin.lower().startswith("https://")


def _set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=_secure_cookies(),
        samesite="strict",
        path="/",
    )


def issue_session(resp: Response, credential_id_b64: str) -> None:
    token = _serializer("session").dumps({"cid": credential_id_b64})
    _set_cookie(resp, SESSION_COOKIE, token, settings.session_max_age_hours * 3600)


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _serializer("session").loads(token, max_age=settings.session_max_age_hours * 3600)
        return True
    except (BadSignature, SignatureExpired):
        return False


# ---------------------------------------------------------------- request models


class RegisterBegin(BaseModel):
    label: str = ""
    enroll_code: str = ""


# ---------------------------------------------------------------- router


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
def auth_status(request: Request) -> dict[str, Any]:
    return {
        "enabled": settings.webauthn_enabled,
        "authenticated": (not settings.webauthn_enabled) or is_authenticated(request),
        "registered_keys": credential_count() if settings.webauthn_enabled else 0,
        "enrollment_open": bool(settings.webauthn_enroll_code),
    }


def _require_enabled() -> None:
    if not settings.webauthn_enabled:
        raise HTTPException(status_code=400, detail="WebAuthn is not enabled on this server.")


@router.post("/register/begin")
def register_begin(request: Request, body: RegisterBegin) -> Response:
    _require_enabled()
    if not settings.webauthn_enroll_code:
        raise HTTPException(status_code=403, detail="Enrollment is closed (no enrollment code set).")
    if body.enroll_code.strip() != settings.webauthn_enroll_code:
        raise HTTPException(status_code=403, detail="Invalid enrollment code.")

    user_id = secrets.token_bytes(16)
    label = (body.label or "Security key").strip()[:80]

    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user_id,
        user_name=label,
        user_display_name=label,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in all_credential_ids()
        ],
    )

    resp = Response(content=options_to_json(options), media_type="application/json")
    payload = _serializer("register").dumps(
        {"challenge": bytes_to_base64url(options.challenge), "label": label}
    )
    _set_cookie(resp, CHALLENGE_COOKIE, payload, CHALLENGE_MAX_AGE)
    return resp


@router.post("/register/complete")
async def register_complete(request: Request) -> Response:
    _require_enabled()
    cookie = request.cookies.get(CHALLENGE_COOKIE)
    if not cookie:
        raise HTTPException(status_code=400, detail="Registration session expired. Try again.")
    try:
        data = _serializer("register").loads(cookie, max_age=CHALLENGE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="Registration session expired. Try again.")

    credential = await request.json()
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(data["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        raise HTTPException(status_code=400, detail=f"Registration failed: {exc}")

    cid_b64 = bytes_to_base64url(verification.credential_id)
    add_credential(cid_b64, verification.credential_public_key, verification.sign_count, data["label"])

    resp = JSONResponse({"status": "registered", "label": data["label"]})
    issue_session(resp, cid_b64)
    resp.delete_cookie(CHALLENGE_COOKIE, path="/")
    return resp


@router.post("/login/begin")
def login_begin(request: Request) -> Response:
    _require_enabled()
    creds = all_credential_ids()
    if not creds:
        raise HTTPException(status_code=403, detail="No security keys are enrolled yet.")

    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in creds],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    resp = Response(content=options_to_json(options), media_type="application/json")
    payload = _serializer("login").dumps({"challenge": bytes_to_base64url(options.challenge)})
    _set_cookie(resp, CHALLENGE_COOKIE, payload, CHALLENGE_MAX_AGE)
    return resp


@router.post("/login/complete")
async def login_complete(request: Request) -> Response:
    _require_enabled()
    cookie = request.cookies.get(CHALLENGE_COOKIE)
    if not cookie:
        raise HTTPException(status_code=400, detail="Login session expired. Try again.")
    try:
        data = _serializer("login").loads(cookie, max_age=CHALLENGE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="Login session expired. Try again.")

    credential = await request.json()
    cid_b64 = credential.get("id") or credential.get("rawId")
    record = get_credential(cid_b64) if cid_b64 else None
    if not record:
        raise HTTPException(status_code=403, detail="This security key is not authorized.")

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(data["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=record["public_key"],
            credential_current_sign_count=record["sign_count"],
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Login failed: {exc}")

    update_sign_count(cid_b64, verification.new_sign_count)
    resp = JSONResponse({"status": "authenticated", "label": record["label"]})
    issue_session(resp, cid_b64)
    resp.delete_cookie(CHALLENGE_COOKIE, path="/")
    return resp


@router.post("/logout")
def logout() -> Response:
    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- gating middleware

# Paths reachable without a session so the login page and ceremony can run.
_PUBLIC_EXACT = {"/", "/health", "/openapi.json", "/docs", "/redoc", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/static/", "/auth/")


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


class WebAuthnMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.webauthn_enabled or _is_public(request.url.path):
            return await call_next(request)
        if is_authenticated(request):
            return await call_next(request)
        return JSONResponse(
            {"detail": "Authentication required. Sign in with your security key."},
            status_code=401,
        )
