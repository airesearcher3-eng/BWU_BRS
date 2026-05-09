"""
BRS Automation System — FastAPI Application Entry Point.
"""
import os
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from models.database import init_db
from routes.upload import router as upload_router
from routes.reconciliation import router as reconciliation_router
from routes.exceptions import router as exceptions_router
from routes.approval import router as approval_router
from routes.audit import router as audit_router
from routes.auth import router as auth_router
from routes.admin import router as admin_router


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
logger = logging.getLogger(__name__)

UPLOAD_MAX_AGE_DAYS = 60  # Auto-delete uploaded files older than 2 months


def _cleanup_old_uploads():
    """Delete uploaded files older than UPLOAD_MAX_AGE_DAYS."""
    uploads_dir = Path(BASE_DIR) / "uploads"
    if not uploads_dir.exists():
        return
    cutoff = time.time() - (UPLOAD_MAX_AGE_DAYS * 86400)
    removed = 0
    for filepath in uploads_dir.rglob("*"):
        if filepath.is_file() and filepath.stat().st_mtime < cutoff:
            try:
                filepath.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not delete %s: %s", filepath, exc)
    if removed:
        logger.info("Cleaned up %d uploaded file(s) older than %d days", removed, UPLOAD_MAX_AGE_DAYS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    for d in ["uploads", "output", "db"]:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)
    init_db()
    _cleanup_old_uploads()
    yield


app = FastAPI(
    title="BRS Automation System",
    description="Bank Reconciliation Statement Automation — Brainware University",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Register routers
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(upload_router)
app.include_router(reconciliation_router)
app.include_router(exceptions_router)
app.include_router(approval_router)
app.include_router(audit_router)


# Serve the SPA
@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


# Separate superadmin portal
@app.get("/admin")
async def admin_portal():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
