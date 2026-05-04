from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/shawahid_db"

    # ── Storage ───────────────────────────────────────────────────────────────
    # Railway: set STORAGE_DIR=/app/storage and mount a Volume on /app/storage
    # Local: defaults to ./storage
    STORAGE_ROOT: str = "./storage"
    # Alias accepted from Railway env (takes priority if set)
    STORAGE_DIR: str = ""

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change_me"

    # ── OpenAI ───────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_CLASSIFIER_MODEL: str = "gpt-4o-mini"
    OPENAI_EXPORT_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: int = 30
    OPENAI_MODEL: str = "gpt-4o-mini"  # legacy alias

    # ── Payment ───────────────────────────────────────────────────────────────
    PAYMENT_LINK_TEMPLATE: str = "https://paylink.example.com/teacher/{teacher_id}"
    PAYMENT_PROVIDER: str = "manual"
    PAYMENT_SUCCESS_SECRET: str = ""

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    WHATSAPP_SEND_URL: str = ""
    WHATSAPP_API_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""

    # ── Service identity ──────────────────────────────────────────────────────
    # BASE_URL is used for building download links in API responses and PDFs.
    # On Railway: set this to the public Railway URL or custom domain.
    BASE_URL: str = "http://localhost:8010"
    PUBLIC_BASE_URL: str = ""   # Railway alias — takes priority if set
    APP_ENV: str = "development"   # set to "production" on Railway
    PORT: int = 8010

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def effective_base_url(self) -> str:
        """Public-facing base URL. PUBLIC_BASE_URL takes priority."""
        return (self.PUBLIC_BASE_URL or self.BASE_URL).rstrip("/")

    @property
    def storage_path(self) -> Path:
        """Effective storage root. STORAGE_DIR takes priority over STORAGE_ROOT."""
        root = self.STORAGE_DIR if self.STORAGE_DIR else self.STORAGE_ROOT
        return Path(root)

    def teacher_storage(self, teacher_id: int) -> Path:
        """Isolated directory for one teacher's files. Never shares across teachers."""
        p = self.storage_path / "teachers" / str(teacher_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def evidence_storage(self, teacher_id: int) -> Path:
        p = self.teacher_storage(teacher_id) / "evidences"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def export_storage(self, teacher_id: int) -> Path:
        p = self.teacher_storage(teacher_id) / "exports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
