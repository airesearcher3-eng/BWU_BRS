"""
BRS Automation System — FastAPI Application Entry Point.
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

import config
from logging_config import configure_logging
from middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from models.database import get_connection, init_db
from routes.admin import router as admin_router
from routes.approval import router as approval_router
from routes.audit import router as audit_router
from routes.auth import get_current_user, router as auth_router
from routes.exceptions import router as exceptions_router
from routes.reconciliation import router as reconciliation_router
from routes.upload import router as upload_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

configure_logging(BASE_DIR)
logger = logging.getLogger(__name__)


def _cleanup_old_uploads() -> None:
    """Delete uploaded files older than UPLOAD_MAX_AGE_DAYS."""
    uploads_dir = Path(BASE_DIR) / config.UPLOAD_FOLDER
    if not uploads_dir.exists():
        return
    cutoff = time.time() - (config.UPLOAD_MAX_AGE_DAYS * 86400)
    removed = 0
    for filepath in uploads_dir.rglob("*"):
        if filepath.is_file() and filepath.stat().st_mtime < cutoff:
            try:
                filepath.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not delete %s: %s", filepath, exc)
    if removed:
        logger.info(
            "Cleaned up %d uploaded file(s) older than %d days",
            removed,
            config.UPLOAD_MAX_AGE_DAYS,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    for d in (config.UPLOAD_FOLDER, config.OUTPUT_FOLDER, "db", config.LOG_DIR):
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)
    init_db()
    _cleanup_old_uploads()
    logger.info("BRS startup complete (env=%s)", config.ENV)
    yield
    logger.info("BRS shutdown")


app = FastAPI(
    title="BRS Automation System",
    description="Bank Reconciliation Statement Automation — Brainware University",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if config.IS_PRODUCTION else "/docs",
    redoc_url=None if config.IS_PRODUCTION else "/redoc",
    openapi_url=None if config.IS_PRODUCTION else "/openapi.json",
)


# ── Middleware (order matters: outermost first) ──────────────────
# Starlette runs the LAST-added middleware first on requests, so list
# them inside-out. We want: TrustedHost → CORS → SecurityHeaders →
# RequestContext → RateLimit → BodySize → app.

app.add_middleware(BodySizeLimitMiddleware, max_bytes=config.MAX_UPLOAD_BYTES)
app.add_middleware(
    RateLimitMiddleware,
    default=config.DEFAULT_RATE_LIMIT,
    route_overrides={
        "/api/auth/login": config.LOGIN_RATE_LIMIT,
        "/api/auth/change-password": config.LOGIN_RATE_LIMIT,
    },
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)
if config.ALLOWED_HOSTS and config.ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)


# ── Static / SPA ─────────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)


# ── Health endpoints (unauthenticated, no rate-limit drama) ──────
@app.get("/healthz", tags=["Health"], include_in_schema=False)
async def healthz():
    """Liveness probe — process is up."""
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"], include_in_schema=False)
async def readyz():
    """Readiness probe — DB reachable."""
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        logger.exception("readyz failure")
        return JSONResponse(
            {"status": "degraded", "error": str(exc)}, status_code=503
        )
    return {"status": "ready"}


# ── Routers ──────────────────────────────────────────────────────
# Auth and admin routers manage their own dependencies. All other API
# routers are gated at include time so any new handler added under them
# inherits authentication automatically.
app.include_router(auth_router)
app.include_router(admin_router)

_auth_required = [Depends(get_current_user)]
app.include_router(upload_router, dependencies=_auth_required)
app.include_router(reconciliation_router, dependencies=_auth_required)
app.include_router(exceptions_router, dependencies=_auth_required)
app.include_router(approval_router, dependencies=_auth_required)
app.include_router(audit_router, dependencies=_auth_required)


# ── SPA entrypoints ──────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


@app.get("/admin", include_in_schema=False)
async def admin_portal():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
