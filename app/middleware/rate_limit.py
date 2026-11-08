import asyncio
import logging
import os
import ssl
import time
from typing import Callable

import redis.asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = (
    "/ui",
    "/healthz",
    "/readyz",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
)

DEFAULT_RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MINUTE", 60))


def _ssl_kwargs() -> dict:
    if os.environ.get("REDIS_URL", "").startswith("rediss://"):
        return {"ssl_cert_reqs": ssl.CERT_REQUIRED}
    return {}


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-tenant sliding-window rate limiter powered by Redis.
    Limits requests to RATE_LIMIT_PER_MINUTE per tenant.
    Injects X-RateLimit headers and returns 429 when quota is exhausted."""

    def __init__(self, app, limit: int = DEFAULT_RATE_LIMIT):
        super().__init__(app)
        self.limit = limit
        self._redis_client: aioredis.Redis | None = None
        self._redis_loop: asyncio.AbstractEventLoop | None = None

    def _get_client(self) -> aioredis.Redis | None:
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            return None

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._redis_client is None or self._redis_loop != current_loop:
            self._redis_client = aioredis.from_url(
                redis_url,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                **_ssl_kwargs(),
            )
            self._redis_loop = current_loop

        return self._redis_client

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        if not tenant_id:
            return await call_next(request)

        client = self._get_client()
        if client is None:
            return await call_next(request)

        # 60-second window bucket key
        current_minute = int(time.time() // 60)
        key = f"rate_limit:{tenant_id}:{current_minute}"

        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, 90)  # expires slightly after window ends
            results = await pipe.execute()
            count = results[0]

            if count > self.limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "detail": f"Tenant '{tenant_id}' rate limit exceeded ({self.limit} req/min)",
                    },
                    headers={
                        "Retry-After": "60",
                        "X-RateLimit-Limit": str(self.limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )

            response = await call_next(request)
            remaining = max(0, self.limit - count)
            response.headers["X-RateLimit-Limit"] = str(self.limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        except Exception as exc:
            # Fail-open if Redis encounters connection or timeout issues
            logger.warning(f"Rate limiter failed open due to Redis error: {exc}")
            return await call_next(request)
