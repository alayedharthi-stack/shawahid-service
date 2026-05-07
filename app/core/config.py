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
    OPENAI_CLASSIFIER_MODEL: str = "gpt-4o"
    OPENAI_EXPORT_MODEL: str = "gpt-4o"
    OPENAI_TIMEOUT_SECONDS: int = 30
    OPENAI_MODEL: str = "gpt-4o"   # GPT brain primary model (override via env)
    # Deep analysis model — used for evidence enrichment and portfolio analysis.
    # Set to "o3-mini", "o1-mini", or "gpt-4o" depending on budget preference.
    # Higher quality = more cost. gpt-4o is the safe default.
    OPENAI_DEEP_MODEL: str = "gpt-4o"

    # ── Moyasar Payment Gateway ───────────────────────────────────────────────
    MOYASAR_SECRET_KEY: str = ""
    MOYASAR_WEBHOOK_SECRET: str = ""
    MOYASAR_API_BASE: str = "https://api.moyasar.com/v1"
    # Set to "false" to skip signature verification (dev/testing)
    MOYASAR_VERIFY_SIGNATURES: str = "true"
    SHAWAHID_LAUNCH_PRICE_HALALAH: int = 2900   # 29 SAR × 100
    SHAWAHID_LAUNCH_PRICE_SAR: int = 29

    @property
    def moyasar_verify_signatures(self) -> bool:
        return self.MOYASAR_VERIFY_SIGNATURES.lower() not in ("false", "0", "no")

    # ── Payment (legacy / fallback) ───────────────────────────────────────────
    PAYMENT_LINK_TEMPLATE: str = "https://paylink.example.com/teacher/{teacher_id}"
    PAYMENT_PROVIDER: str = "moyasar"
    PAYMENT_SUCCESS_SECRET: str = ""

    # ── WhatsApp Cloud API ────────────────────────────────────────────────────
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_API_VERSION: str = "v20.0"
    # Legacy stubs (kept for backward compat — no longer used by send_whatsapp_message)
    WHATSAPP_SEND_URL: str = ""
    WHATSAPP_API_TOKEN: str = ""

    # ── Service identity ──────────────────────────────────────────────────────
    # BASE_URL is used for building download links in API responses and PDFs.
    # On Railway: set this to the public Railway URL or custom domain.
    BASE_URL: str = "http://localhost:8010"
    PUBLIC_BASE_URL: str = ""   # Railway alias — takes priority if set
    APP_ENV: str = "development"   # set to "production" on Railway
    PORT: int = 8010

    # ── Trust / support identity ──────────────────────────────────────────────
    NAHLA_WEBSITE: str = "https://nahlah.ai"
    SUPPORT_EMAIL: str = "support@nahlah.ai"
    SUPPORT_PHONE: str = "966555906901"
    SUPPORT_PERSON: str = "تركي الحارثي"
    BUSINESS_VERIFICATION_TEXT: str = "موثق عبر السجل التجاري والمركز السعودي للأعمال"

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

    @property
    def trust_info(self) -> dict:
        return {
            "provider": "نحلة AI",
            "website": self.NAHLA_WEBSITE,
            "support_email": self.SUPPORT_EMAIL,
            "support_phone": self.SUPPORT_PHONE,
            "support_person": self.SUPPORT_PERSON,
            "business_verification_text": self.BUSINESS_VERIFICATION_TEXT,
            "trust_box_text": (
                "شواهد AI خدمة تعليمية مقدمة ضمن منظومة نحلة AI، وهي خدمة موثقة تجاريًا "
                "ومرتبطة بسجل تجاري وتوثيق المركز السعودي للأعمال. نحرص على حماية بيانات "
                "المعلمين وتنظيم شواهدهم بشكل آمن، ويمكنكم التواصل معنا لأي استفسار عبر "
                "البريد الرسمي أو رقم المطور."
            ),
        }


settings = Settings()
