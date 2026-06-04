from __future__ import annotations

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import settings

# Always public (UI, health checks, static assets)
PUBLIC_PREFIXES = (
    "/",
    "/health",
    "/static",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _extract_api_key(request: Request) -> str | None:
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_api_key(request: Request) -> None:
    if not settings.app_api_key:
        return
    provided = _extract_api_key(request)
    if not provided or provided != settings.app_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Use header X-API-Key.",
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
        if not settings.app_api_key or is_public_path(request.url.path):
            return await call_next(request)

        provided = _extract_api_key(request)
        if not provided or provided != settings.app_api_key:
            return Response(
                content='{"detail":"Invalid or missing API key."}',
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)
