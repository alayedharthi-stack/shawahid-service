import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import webhook, teachers, evidences, admin, downloads, review
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure base storage directories exist at startup.
    # On Railway the Volume mount at /app/storage will already exist;
    # on local dev this creates ./storage automatically.
    storage_root = settings.storage_path
    storage_root.mkdir(parents=True, exist_ok=True)
    (storage_root / "teachers").mkdir(exist_ok=True)
    logger.info(
        "Shawahid service started | env=%s | port=%s | storage=%s",
        settings.APP_ENV,
        os.environ.get("PORT", "unknown"),
        storage_root.resolve(),
    )
    yield


app = FastAPI(
    title="Shawahid Service — ملف الشواهد",
    description="خدمة مستقلة لإدارة شواهد المعلمين عبر واتساب",
    version="1.0.0",
    lifespan=lifespan,
    # Hide docs in production
    docs_url=None if settings.APP_ENV == "production" else "/docs",
    redoc_url=None if settings.APP_ENV == "production" else "/redoc",
)

# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ── Storage file serving ───────────────────────────────────────────────────────
# Must exist before StaticFiles constructor is called.
# The lifespan handler above creates it, but StaticFiles is mounted at module
# level, so we create it here too (idempotent).
_storage_path = settings.storage_path
_storage_path.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(_storage_path)), name="files")

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(webhook.router)
app.include_router(teachers.router)
app.include_router(evidences.router)
app.include_router(admin.router)
app.include_router(downloads.router)
app.include_router(review.router)


# ── Health endpoints ───────────────────────────────────────────────────────────
# CRITICAL: these must NOT touch DB, OpenAI, WhatsApp, or Moyasar so the
# Railway healthcheck never fails spuriously. Keep them dependency-free.
@app.get("/")
def root():
    return {"status": "ok", "service": "shawahid-service"}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "shawahid-service"}
