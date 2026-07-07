from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import settings

# Always public (UI, health checks, static assets, sign-in)
PUBLIC_PREFIXES = (
    "/",
    "/health",
    "/static",
    "/login",
    "/docs",
    "/openapi.json",
    "/redoc",
)

# Ephemeral fallback signing secret — regenerated each boot, which simply
# forces everyone to sign in again after a restart/redeploy.
_BOOT_SECRET = secrets.token_hex(32)


def _signing_secret() -> bytes:
    secret = settings.auth_secret or settings.app_api_key or _BOOT_SECRET
    return secret.encode("utf-8")


def _sign(payload: str) -> str:
    digest = hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def roster_enabled() -> bool:
    return bool(settings.roster_emails)


def email_on_roster(email: str) -> bool:
    return (email or "").strip().lower() in settings.roster_emails


def issue_login_token(email: str) -> tuple[str, int]:
    """Return (token, expires_at_epoch) for a roster-approved email."""
    normalized = email.strip().lower()
    expires_at = int(time.time()) + settings.auth_token_hours * 3600
    payload = f"{normalized}|{expires_at}"
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign(payload)}", expires_at


def verify_login_token(token: str) -> str | None:
    """Return the email for a valid, unexpired, roster-approved token."""
    try:
        encoded, signature = token.rsplit(".", 1)
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        if not hmac.compare_digest(_sign(payload), signature):
            return None
        email, expires_str = payload.rsplit("|", 1)
        if int(expires_str) < time.time():
            return None
        if not email_on_roster(email):
            return None
        return email
    except Exception:
        return None


def _extract_credential(request: Request) -> str | None:
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Plain <a href> downloads can't send headers — allow ?token= there.
    token = request.query_params.get("token")
    if token:
        return token.strip()
    return None


def _credential_ok(provided: str | None) -> bool:
    if settings.app_api_key and provided == settings.app_api_key:
        return True
    if roster_enabled() and provided and verify_login_token(provided):
        return True
    return False


def auth_required() -> bool:
    return bool(settings.app_api_key) or roster_enabled()


def require_api_key(request: Request) -> None:
    if not auth_required():
        return
    if not _credential_ok(_extract_credential(request)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required. Your session may have expired — sign in again.",
        )


def is_public_path(path: str) -> bool:
    if path == "/":
        return True
    return any(
        path == prefix or (prefix != "/" and path.startswith(prefix + "/"))
        for prefix in PUBLIC_PREFIXES
        if prefix != "/"
    )


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not auth_required() or is_public_path(request.url.path):
            return await call_next(request)

        if not _credential_ok(_extract_credential(request)):
            return Response(
                content='{"detail":"Sign in required. Your session may have expired."}',
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)
