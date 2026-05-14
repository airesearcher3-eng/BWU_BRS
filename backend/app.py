"""
BRS Automation System — FastAPI Application Entry Point (Backend Service).
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

import config
from logging_config import configure_logging
from middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from models.database import init_db, close_db, get_connection
from routes.admin import router as admin_router
from routes.approval import router as approval_router
from routes.audit import router as audit_router
from routes.auth import get_current_user, router as auth_router
from routes.exceptions import router as exceptions_router
from routes.reconciliation import router as reconciliation_router
from routes.upload import router as upload_router
from routes.chat import router as chat_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

configure_logging(BASE_DIR)
logger = logging.getLogger(__name__)


def _cleanup_old_uploads() -> None:
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
        logger.info("Cleaned up %d uploaded file(s) older than %d days", removed, config.UPLOAD_MAX_AGE_DAYS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for d in (config.UPLOAD_FOLDER, config.OUTPUT_FOLDER, config.LOG_DIR):
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)
    await init_db()
    _cleanup_old_uploads()
    logger.info("BRS backend startup complete (env=%s)", config.ENV)
    yield
    await close_db()
    logger.info("BRS backend shutdown")


app = FastAPI(
    title="BRS Automation System",
    description="Bank Reconciliation Statement Automation — Brainware University",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if config.IS_PRODUCTION else "/docs",
    redoc_url=None if config.IS_PRODUCTION else "/redoc",
    openapi_url=None if config.IS_PRODUCTION else "/openapi.json",
)

# ── Middleware ────────────────────────────────────────────────────
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


# ── Health ────────────────────────────────────────────────────────
@app.get("/healthz", tags=["Health"], include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"], include_in_schema=False)
async def readyz():
    try:
        async with get_connection() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        logger.warning("readyz: database not ready — %s: %s", type(exc).__name__, exc)
        return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=503)
    return {"status": "ready"}


# ── Routers ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(admin_router)

_auth_required = [Depends(get_current_user)]
app.include_router(upload_router, dependencies=_auth_required)
app.include_router(reconciliation_router, dependencies=_auth_required)
app.include_router(exceptions_router, dependencies=_auth_required)
app.include_router(approval_router, dependencies=_auth_required)
app.include_router(audit_router, dependencies=_auth_required)
app.include_router(chat_router, dependencies=_auth_required)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
