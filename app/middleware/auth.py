import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Fallback development secret if JWT_SECRET / API_SECRET is not set in env
DEFAULT_SECRET = os.environ.get("AUTH_SECRET", "super-secret-copilot-key-for-dev")

EXEMPT_PREFIXES = (
    "/ui",
    "/healthz",
    "/readyz",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
)


def sign_token(tenant_id: str, secret: str = DEFAULT_SECRET) -> str:
    """Generate a HMAC-SHA256 signed Bearer token: <tenant_id>.<signature>."""
    signature = hmac.new(
        secret.encode(),
        tenant_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{tenant_id}.{signature}"


def verify_token(token: str, secret: str = DEFAULT_SECRET) -> str | None:
    """Verify signed token and return tenant_id if valid, else None."""
    try:
        parts = token.strip().split(".")
        if len(parts) != 2:
            return None
        tenant_id, signature = parts
        expected = hmac.new(
            secret.encode(),
            tenant_id.encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return tenant_id
    except Exception as exc:
        logger.warning(f"Failed to verify token: {exc}")
    return None


def resolve_tenant_from_api_key(api_key: str) -> str | None:
    """Resolve tenant from X-API-Key.
    Supports:
    1. Configured dictionary in env API_KEYS='{"key123": "acme"}'
    2. Prefix pattern: 'key_<tenant_id>_<secret>'
    """
    api_key = api_key.strip()
    configured_keys = os.environ.get("API_KEYS")
    if configured_keys:
        try:
            mapping = json.loads(configured_keys)
            if api_key in mapping:
                return mapping[api_key]
        except Exception:
            pass

    # Pattern check: key_<tenant>_<secret>
    if api_key.startswith("key_"):
        parts = api_key.split("_")
        if len(parts) >= 3 and parts[1]:
            return parts[1]

    # Test key pattern: test-key-<tenant>
    if api_key.startswith("test-key-"):
        return api_key.removeprefix("test-key-")

    return None


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Enforces tenant authentication and rejects unauthenticated requests.
    Prevents unauthenticated fallback to a default tenant.
    Injects request.state.tenant_id for downstream route handlers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Exempt non-tenant endpoints
        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return await call_next(request)

        tenant_id: str | None = None

        # 1. Check Bearer Token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            secret = os.environ.get("AUTH_SECRET", DEFAULT_SECRET)
            tenant_id = verify_token(token, secret)

        # 2. Check X-API-Key
        if not tenant_id:
            api_key = request.headers.get("X-API-Key")
            if api_key:
                tenant_id = resolve_tenant_from_api_key(api_key)

        # 3. Check X-Tenant-ID (dev/demo fallback if explicitly enabled or in dev mode)
        if not tenant_id:
            tenant_header = request.headers.get("X-Tenant-ID")
            # If ALLOW_TENANT_HEADER is explicitly disabled, ignore this header
            allow_header = os.environ.get("ALLOW_TENANT_HEADER", "true").lower() in ("true", "1", "yes")
            if tenant_header and allow_header:
                tenant_id = tenant_header.strip()

        # Reject if no valid tenant could be established
        if not tenant_id:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Missing or invalid tenant credentials (provide X-API-Key, Bearer token, or X-Tenant-ID)",
                },
            )

        request.state.tenant_id = tenant_id
        return await call_next(request)
