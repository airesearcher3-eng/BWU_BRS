"""
HTTP middleware: request IDs, security headers, body-size limit, rate limiting.

The rate limiter is intentionally in-process (no Redis dependency). It uses
a sliding window per (client-ip, route-prefix) and is sufficient for a
single-host deployment. For multi-worker deployments behind a reverse proxy,
move to Redis-backed slowapi.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

import config

logger = logging.getLogger(__name__)


# ── Request ID + access logs ────────────────────────────────────

class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tag each request with an ID and emit a structured access log line."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_error",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            return JSONResponse(
                {"detail": "Internal server error", "request_id": rid},
                status_code=500,
                headers={"X-Request-ID": rid},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        logger.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
            },
        )
        # Attach timing for observability without overwriting existing values.
        response.headers.setdefault("X-Response-Time-ms", f"{elapsed_ms:.1f}")
        return response


# ── Security headers ────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative defaults; tighten CSP per app needs."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        if config.IS_PRODUCTION:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        return response


# ── Body-size limit ─────────────────────────────────────────────

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured cap.

    This catches oversized uploads before FastAPI buffers them. Streaming
    requests without Content-Length are passed through; per-route handlers
    enforce their own limits where needed.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    return JSONResponse(
                        {
                            "detail": (
                                f"Request body too large "
                                f"(limit: {self.max_bytes} bytes)"
                            )
                        },
                        status_code=413,
                    )
            except ValueError:
                pass
        return await call_next(request)


# ── Sliding-window rate limiter ─────────────────────────────────

def _parse_rate(spec: str) -> tuple[int, float]:
    """Parse '5/minute', '120/minute', '10/second' into (limit, window_seconds)."""
    try:
        count_s, unit = spec.split("/", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid rate spec: {spec!r}") from exc
    count = int(count_s)
    unit = unit.strip().lower()
    if unit in ("s", "sec", "second"):
        window = 1.0
    elif unit in ("m", "min", "minute"):
        window = 60.0
    elif unit in ("h", "hr", "hour"):
        window = 3600.0
    elif unit in ("d", "day"):
        window = 86400.0
    else:
        raise ValueError(f"Unknown rate unit: {unit!r}")
    return count, window


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding-window limiter.

    Default cap applies to all paths; ``route_overrides`` lets specific
    path prefixes get tighter limits (e.g. '/api/auth/login').
    """

    def __init__(
        self,
        app: ASGIApp,
        default: str,
        route_overrides: dict[str, str] | None = None,
    ):
        super().__init__(app)
        self._default_count, self._default_window = _parse_rate(default)
        self._overrides = [
            (prefix, *_parse_rate(spec))
            for prefix, spec in (route_overrides or {}).items()
        ]
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _limit_for(self, path: str) -> tuple[str, int, float]:
        for prefix, count, window in self._overrides:
            if path.startswith(prefix):
                return prefix, count, window
        return "*", self._default_count, self._default_window

    def _client_id(self, request: Request) -> str:
        # Honor X-Forwarded-For only when the immediate peer is a trusted
        # proxy. We accept it unconditionally here because production should
        # run behind a reverse proxy that strips spoofed headers.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        prefix, limit, window = self._limit_for(request.url.path)
        client = self._client_id(request)
        key = (client, prefix)
        now = time.monotonic()

        with self._lock:
            q = self._buckets[key]
            cutoff = now - window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                retry_after = max(1, int(window - (now - q[0])))
                return JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            q.append(now)

        return await call_next(request)
