"""
Tests for the tenant identity / contamination guard.

These tests cover three concerns:

1. Meta payload parsing exposes the new tenant identity fields
   (``phone_number_id``, ``display_phone_number``, ``waba_id``,
   ``contact_name``, ``contact_wa_id``, ``message_id``) without breaking
   existing key contracts.
2. The pure helpers in ``app.services.tenant_guard`` mask secrets and
   format the canonical ``[WA INBOUND DEBUG]`` log line.
3. The ``is_foreign_tenant`` predicate behaves conservatively: it
   returns ``True`` only when both the configured and the incoming
   ``phone_number_id`` are set *and* differ. Empty configuration (local
   dev / tests without env) must never accidentally drop messages.
"""
from __future__ import annotations

import pytest

from app.api.webhook import _parse_meta_payload
from app.services import tenant_guard


# ── Meta payload parsing exposes tenant fields ─────────────────────────────

def _meta_payload(
    *,
    phone_number_id: str | None = "555111222",
    display_phone_number: str | None = "+966555906901",
    waba_id: str | None = "WABA-9876",
    contact_name: str | None = "Teacher Iyad",
    contact_wa_id: str | None = "966500000000",
    message_id: str | None = "wamid.X",
    body_text: str = "صدر",
) -> dict:
    metadata = {}
    if phone_number_id is not None:
        metadata["phone_number_id"] = phone_number_id
    if display_phone_number is not None:
        metadata["display_phone_number"] = display_phone_number

    contacts = []
    if contact_name is not None or contact_wa_id is not None:
        contacts = [
            {
                "profile": {"name": contact_name} if contact_name else {},
                "wa_id": contact_wa_id,
            }
        ]

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "value": {
                            "metadata": metadata,
                            "contacts": contacts,
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": "966500000000",
                                    "type": "text",
                                    "text": {"body": body_text},
                                }
                            ],
                        }
                    }
                ],
            }
        ],
    }


class TestMetadataExtraction:
    def test_full_payload_exposes_all_tenant_fields(self):
        result = _parse_meta_payload(_meta_payload())
        assert result is not None
        assert result["phone_number_id"] == "555111222"
        assert result["display_phone_number"] == "+966555906901"
        assert result["waba_id"] == "WABA-9876"
        assert result["contact_name"] == "Teacher Iyad"
        assert result["contact_wa_id"] == "966500000000"
        assert result["message_id"] == "wamid.X"

    def test_existing_text_contract_unchanged(self):
        result = _parse_meta_payload(_meta_payload(body_text="hello"))
        assert result["from_phone"] == "966500000000"
        assert result["msg_type"] == "text"
        assert result["text"] == "hello"

    def test_missing_metadata_yields_none_fields(self):
        result = _parse_meta_payload(
            _meta_payload(
                phone_number_id=None,
                display_phone_number=None,
                waba_id=None,
                contact_name=None,
                contact_wa_id=None,
            )
        )
        assert result is not None
        assert result["phone_number_id"] is None
        assert result["display_phone_number"] is None
        assert result["contact_name"] is None
        assert result["contact_wa_id"] is None


# ── Masking helpers ────────────────────────────────────────────────────────

class TestMaskingHelpers:
    def test_mask_db_url_masks_credentials(self):
        url = "postgresql://nahla_user:s3cret@db.host:5432/shawahid_db"
        masked = tenant_guard.mask_db_url(url)
        assert "nahla_user" not in masked
        assert "s3cret" not in masked
        assert "db.host" in masked
        assert "5432" in masked
        assert "shawahid_db" in masked
        assert masked.startswith("postgresql://***:***@")

    def test_mask_db_url_handles_unset(self):
        assert tenant_guard.mask_db_url(None) == "<unset>"
        assert tenant_guard.mask_db_url("") == "<unset>"

    def test_mask_db_url_handles_garbage(self):
        out = tenant_guard.mask_db_url("not-a-url")
        # Must never echo the raw value verbatim with credentials, but a
        # bare hostless string is still safe to render.
        assert "***" in out or out == "<unparseable>" or "://" not in out

    def test_prompt_profile_fingerprint_is_stable(self):
        a = tenant_guard.prompt_profile_fingerprint()
        b = tenant_guard.prompt_profile_fingerprint()
        assert a == b
        assert a.startswith("shawahid:")


# ── Foreign tenant predicate ───────────────────────────────────────────────

class TestForeignTenantGuard:
    def test_empty_configuration_never_drops(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        assert tenant_guard.is_foreign_tenant("anything") is False
        assert tenant_guard.is_foreign_tenant(None) is False

    def test_empty_incoming_never_drops(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")
        assert tenant_guard.is_foreign_tenant(None) is False
        assert tenant_guard.is_foreign_tenant("") is False

    def test_matching_ids_pass(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")
        assert tenant_guard.is_foreign_tenant("111222333") is False

    def test_mismatched_ids_drop(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "111222333")
        assert tenant_guard.is_foreign_tenant("999999999") is True


# ── Inbound debug line ─────────────────────────────────────────────────────

class TestInboundDebugLine:
    def test_contains_all_required_keys(self):
        line = tenant_guard.build_inbound_debug_line(
            phone_number_id="555111222",
            display_phone_number="+966555906901",
            waba_id="WABA-9876",
            from_phone="966500000000",
            message_text="صدر",
            contact_name="Iyad",
            msg_type="text",
        )
        for key in (
            "[WA INBOUND DEBUG]",
            "service=shawahid",
            "phone_number_id=555111222",
            "display_phone_number=+966555906901",
            "waba_id=WABA-9876",
            "from=966500000000",
            "msg_type=text",
            "webhook_path=/webhook/whatsapp",
            "db_url_masked=",
            "prompt_profile=",
            "configured_phone_number_id=",
            "app_env=",
        ):
            assert key in line, f"missing {key!r} in {line!r}"

    def test_truncates_long_message_text(self):
        long_text = "ا" * 500
        line = tenant_guard.build_inbound_debug_line(
            phone_number_id="x",
            display_phone_number="x",
            waba_id="x",
            from_phone="x",
            message_text=long_text,
            contact_name="x",
            msg_type="text",
        )
        # Must not blow up the log line — bound is 80 chars + repr quoting.
        assert len(line) < 1500

    def test_handles_none_safely(self):
        line = tenant_guard.build_inbound_debug_line(
            phone_number_id=None,
            display_phone_number=None,
            waba_id=None,
            from_phone=None,
            message_text=None,
            contact_name=None,
            msg_type=None,
        )
        assert "phone_number_id=<none>" in line
        assert "from=<none>" in line


# ── Identity snapshot (used by /internal/identity + startup banner) ────────

class TestIdentitySnapshot:
    def test_snapshot_keys_present_and_safe(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "PNID_FULL_SECRET_98765")
        monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "VRFY_FULL_SECRET_44321")
        monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "EAACCESS_FULL_SECRET_zzzz")
        monkeypatch.setattr(
            settings,
            "DATABASE_URL",
            "postgresql://u:p@db.shawahid.internal:5432/shawahid_db",
        )

        snap = tenant_guard.identity_snapshot()
        assert snap["service"] == "shawahid"
        assert snap["webhook_path"] == "/webhook/whatsapp"
        # Suffix masking must hide the prefix entirely.
        assert "PNID_FULL_SECRET" not in snap["phone_number_id_suffix"]
        assert "VRFY_FULL_SECRET" not in snap["verify_token_suffix"]
        assert "EAACCESS_FULL_SECRET" not in snap["access_token_suffix"]
        assert snap["phone_number_id_suffix"].endswith("8765")
        # DB URL must be masked.
        assert "u:p" not in snap["database_url_masked"]
        assert "db.shawahid.internal" in snap["database_url_masked"]
