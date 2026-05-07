"""
Phase-6 WhatsApp Flow Integration tests.

Coverage matrix:
  1.  "صدر الآن"     → export flow shows 2-button card        (intent routing)
  2.  "راجع ملفي"    → review link sent with session summary  (intent routing)
  3.  duplicates_count > 0 → pre-export warning shown        (smart warning)
  4.  low_confidence_count > 0 → review hint in warning      (smart warning)
  5.  batch files    → build_batch_summary message           (integration helper)
  6.  classification uncertain → needs_review in save reply  (save reply)
  7.  name confirmation → uses standardised question         (name protection)
  8.  file received  → correct ack for each media type       (file ack)
  9.  webhook still works for legacy export flow             (no regression)
  10. review_engine no regression                            (no regression)
  11. export_engine no regression (no PDF changes)           (no regression)
  12. category hint stored & applied                         (intent routing)
"""
from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fake_evidence(
    *,
    ev_id: int = 1,
    ev_type: str = "image",
    category: str = "نشاط صفي",
    title: str = "شاهد",
    confidence: float = 0.9,
    content_hash: str = "abc123",
    is_duplicate: bool = False,
    is_excluded: bool = False,
    media_url: str | None = None,
    storage_path: str | None = None,
) -> SimpleNamespace:
    # review_service._confidence_from_evidence expects ai_raw to be a dict
    ai_raw_dict = {"confidence_score": confidence}
    return SimpleNamespace(
        id=ev_id,
        evidence_type=ev_type,
        category=category,
        title=title,
        content_hash=content_hash,
        is_duplicate=is_duplicate,
        is_excluded_from_export=is_excluded,
        media_url=media_url,
        storage_path=storage_path,
        ai_raw=ai_raw_dict,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1–2. Intent detection: export + review
# ──────────────────────────────────────────────────────────────────────────────

class TestIntentDetection:
    def test_export_intent_detected(self):
        from app.services.intents import detect_intent, INTENT_EXPORT
        result = detect_intent("صدر الآن")
        assert result.intent == INTENT_EXPORT
        assert result.confidence >= 0.8

    def test_review_intent_detected_arabic(self):
        from app.services.intents import detect_intent, INTENT_REVIEW
        for phrase in ("راجع الشواهد", "ارني ملفي", "ابي اراجع"):
            r = detect_intent(phrase)
            assert r.intent == INTENT_REVIEW, f"Failed for {phrase!r}"

    def test_review_intent_detected_colloquial(self):
        from app.services.intents import detect_intent, INTENT_REVIEW
        assert detect_intent("راجع ملفي").intent == INTENT_REVIEW

    def test_duplicate_intent_detected(self):
        from app.services.intents import detect_intent, INTENT_DUPLICATE
        assert detect_intent("هذا مكرر").intent == INTENT_DUPLICATE

    def test_delete_last_intent_detected(self):
        from app.services.intents import detect_intent, INTENT_DELETE_LAST
        assert detect_intent("احذف آخر شاهد").intent == INTENT_DELETE_LAST

    def test_category_hint_plan(self):
        from app.services.intents import detect_intent, INTENT_CATEGORY_HINT
        r = detect_intent("هذه خطة")
        assert r.intent == INTENT_CATEGORY_HINT
        assert r.payload is not None
        assert "التخطيط" in r.payload.get("category", "")

    def test_category_hint_exam(self):
        from app.services.intents import detect_intent, INTENT_CATEGORY_HINT
        r = detect_intent("هذا اختبار")
        assert r.intent == INTENT_CATEGORY_HINT
        assert r.payload is not None
        assert "التقويم" in r.payload.get("category", "")

    def test_none_intent_for_irrelevant(self):
        from app.services.intents import detect_intent, INTENT_NONE
        assert detect_intent("مرحبا كيف حالك").intent != INTENT_NONE  # greeting
        assert detect_intent("شكراً جزيلاً على الخدمة").intent == INTENT_NONE


# ──────────────────────────────────────────────────────────────────────────────
# 3–4. Pre-export warning (duplicates + low confidence)
# ──────────────────────────────────────────────────────────────────────────────

def _media_urls_mock():
    """Return a context manager that stubs out build_media_urls."""
    from unittest.mock import patch, MagicMock
    mock_urls = MagicMock()
    mock_urls.preview_url = None
    mock_urls.thumbnail_url = None
    mock_urls.public_url = None
    return patch("app.media_engine.media_urls.build_media_urls", return_value=mock_urls)


class TestPreExportWarning:
    def test_no_warning_when_clean(self):
        from app.services.whatsapp_integration import make_pre_export_warning
        evidences = [
            _fake_evidence(ev_id=i, confidence=0.9, content_hash=f"h{i}")
            for i in range(3)
        ]
        with _media_urls_mock():
            result = make_pre_export_warning(
                evidences, teacher_id=1, teacher_name="تركي", base_url="https://x.com"
            )
        assert result is None

    def test_warning_with_duplicates(self):
        from app.services.whatsapp_integration import make_pre_export_warning
        evidences = [
            _fake_evidence(ev_id=1, confidence=0.9, content_hash="same"),
            _fake_evidence(ev_id=2, confidence=0.9, content_hash="same"),
            _fake_evidence(ev_id=3, confidence=0.9, content_hash="unique"),
        ]
        with _media_urls_mock():
            result = make_pre_export_warning(
                evidences, teacher_id=1, teacher_name="تركي", base_url="https://x.com"
            )
        assert result is not None
        assert "مكررة" in result or "مكرر" in result

    def test_warning_with_low_confidence(self):
        from app.services.whatsapp_integration import make_pre_export_warning
        from app.review_engine.schemas import LOW_CONFIDENCE_THRESHOLD
        evidences = [
            _fake_evidence(ev_id=1, confidence=LOW_CONFIDENCE_THRESHOLD - 0.1),
            _fake_evidence(ev_id=2, confidence=0.9, content_hash="h2"),
        ]
        with _media_urls_mock():
            result = make_pre_export_warning(
                evidences, teacher_id=1, teacher_name="تركي", base_url="https://x.com"
            )
        assert result is not None
        assert "ثقتها منخفضة" in result or "مراجعة" in result

    def test_warning_does_not_block_export_text(self):
        """Warning must say 'يمكنك المتابعة' — never tells teacher they can't export."""
        from app.services.whatsapp_integration import make_pre_export_warning
        evidences = [
            _fake_evidence(ev_id=1, confidence=0.9, content_hash="same"),
            _fake_evidence(ev_id=2, confidence=0.9, content_hash="same"),
        ]
        with _media_urls_mock():
            result = make_pre_export_warning(
                evidences, teacher_id=1, teacher_name="تركي", base_url="https://x.com"
            )
        assert result is not None
        assert "يمكنك" in result


# ──────────────────────────────────────────────────────────────────────────────
# 5. Batch summary
# ──────────────────────────────────────────────────────────────────────────────

class TestBatchSummary:
    def test_single_save_returns_none(self):
        from app.services.whatsapp_integration import make_batch_summary_reply
        assert make_batch_summary_reply([("image", "نشاط صفي")]) is None

    def test_empty_list_returns_none(self):
        from app.services.whatsapp_integration import make_batch_summary_reply
        assert make_batch_summary_reply([]) is None

    def test_multi_save_returns_single_summary(self):
        from app.services.whatsapp_integration import make_batch_summary_reply
        saves = [
            ("image", "نشاط صفي"),
            ("pdf",   "التخطيط"),
            ("video", "نشاط صفي"),
        ]
        result = make_batch_summary_reply(saves)
        assert result is not None
        # One summary, not 3 separate messages
        assert "3" in result or "ثلاث" in result or "استلام" in result

    def test_batch_summary_groups_by_category(self):
        from app.services.whatsapp_integration import make_batch_summary_reply
        saves = [
            ("image", "نشاط صفي"),
            ("image", "نشاط صفي"),
            ("pdf",   "التخطيط"),
        ]
        result = make_batch_summary_reply(saves)
        assert result is not None
        assert "نشاط صفي" in result
        assert "التخطيط" in result


# ──────────────────────────────────────────────────────────────────────────────
# 6. Smart save reply — uncertain classification
# ──────────────────────────────────────────────────────────────────────────────

class TestSaveReply:
    def test_low_confidence_shows_review_hint(self):
        from app.services.whatsapp_integration import make_save_reply
        from app.review_engine.schemas import LOW_CONFIDENCE_THRESHOLD
        msg = make_save_reply(
            ev_type="image",
            category="نشاط صفي",
            title="نشاط",
            confidence=LOW_CONFIDENCE_THRESHOLD - 0.05,
        )
        assert "مراجع" in msg or "✏️" in msg

    def test_high_confidence_no_review_hint(self):
        from app.services.whatsapp_integration import make_save_reply
        msg = make_save_reply(
            ev_type="image",
            category="نشاط صفي",
            title="نشاط",
            confidence=0.95,
        )
        assert "✅" in msg
        # High confidence: no review hint
        assert "حفظته في المحور الأقرب" not in msg

    def test_strong_evidence_mentioned(self):
        from app.services.whatsapp_integration import make_save_reply
        from app.services.classification import IMPORTANCE_STRONG
        msg = make_save_reply(
            ev_type="image",
            category="التقويم",
            confidence=0.92,
            ai_raw={"importance_score": IMPORTANCE_STRONG},
        )
        assert "قوي" in msg or "⭐" in msg

    def test_duplicate_returns_warning_not_success(self):
        from app.services.whatsapp_integration import make_save_reply
        msg = make_save_reply(
            ev_type="image",
            category="نشاط صفي",
            is_duplicate=True,
        )
        assert "⚠️" in msg or "مكرر" in msg or "موجود" in msg
        assert "✅" not in msg

    def test_save_reply_never_raises(self):
        from app.services.whatsapp_integration import make_save_reply
        # Garbage input should still return a string
        result = make_save_reply(ev_type="", category="", confidence=None)
        assert isinstance(result, str)


# ──────────────────────────────────────────────────────────────────────────────
# 7. Name confirmation
# ──────────────────────────────────────────────────────────────────────────────

class TestNameConfirmation:
    def test_name_confirmation_question_contains_name(self):
        from app.services.whatsapp_integration import make_name_confirmation_question
        msg = make_name_confirmation_question("تركي الحارثي")
        assert "تركي الحارثي" in msg

    def test_name_confirmation_has_yes_no_options(self):
        from app.services.whatsapp_integration import make_name_confirmation_question
        msg = make_name_confirmation_question("فاطمة")
        # Must offer both yes and no
        assert "نعم" in msg
        assert "لا" in msg

    def test_name_confirmation_never_raises_on_empty(self):
        from app.services.whatsapp_integration import make_name_confirmation_question
        result = make_name_confirmation_question("")
        assert isinstance(result, str)
        assert len(result) > 0


# ──────────────────────────────────────────────────────────────────────────────
# 8. File received ack
# ──────────────────────────────────────────────────────────────────────────────

class TestFileReceivedAck:
    @pytest.mark.parametrize("ev_type,expected_label", [
        ("pdf",      "ملف PDF"),
        ("image",    "صورة"),
        ("video",    "فيديو"),
        ("audio",    "تسجيل صوتي"),
        ("document", "مستند"),
    ])
    def test_ack_contains_type_label(self, ev_type, expected_label):
        from app.services.whatsapp_integration import make_file_received_reply
        msg = make_file_received_reply(ev_type)
        assert expected_label in msg

    def test_ack_contains_analysing_indicator(self):
        from app.services.whatsapp_integration import make_file_received_reply
        for ev_type in ("pdf", "image", "video"):
            msg = make_file_received_reply(ev_type)
            assert "جارٍ" in msg or "تحليل" in msg

    def test_ack_for_unknown_type_does_not_crash(self):
        from app.services.whatsapp_integration import make_file_received_reply
        msg = make_file_received_reply("unknown_type")
        assert isinstance(msg, str)


# ──────────────────────────────────────────────────────────────────────────────
# 9. Review link with session summary
# ──────────────────────────────────────────────────────────────────────────────

class TestReviewLinkReply:
    def test_review_reply_contains_link(self):
        from app.services.whatsapp_integration import make_review_link_reply
        evidences = [_fake_evidence()]
        with _media_urls_mock():
            msg = make_review_link_reply(
                evidences,
                teacher_id=1,
                teacher_name="تركي",
                base_url="https://x.com",
                review_url="https://x.com/review/abc123",
            )
        assert "https://x.com/review/abc123" in msg

    def test_review_reply_contains_summary(self):
        from app.services.whatsapp_integration import make_review_link_reply
        evidences = [_fake_evidence(ev_id=i, content_hash=f"h{i}") for i in range(3)]
        with _media_urls_mock():
            msg = make_review_link_reply(
                evidences,
                teacher_id=1,
                teacher_name="تركي",
                base_url="https://x.com",
                review_url="https://x.com/review/token",
            )
        # Summary should mention the count
        assert "3" in msg or "ثلاث" in msg or "شاهد" in msg

    def test_review_reply_never_raises_on_empty(self):
        from app.services.whatsapp_integration import make_review_link_reply
        with _media_urls_mock():
            msg = make_review_link_reply(
                [],
                teacher_id=1,
                teacher_name=None,
                base_url="",
                review_url="https://x.com/review/token",
            )
        assert "https://x.com/review/token" in msg


# ──────────────────────────────────────────────────────────────────────────────
# 10. Architectural contracts — no regressions
# ──────────────────────────────────────────────────────────────────────────────

class TestArchitecturalContracts:
    def test_whatsapp_integration_no_playwright(self):
        """whatsapp_integration must not import Playwright."""
        import ast, pathlib
        src = pathlib.Path(
            "app/services/whatsapp_integration.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for n in names:
                    assert "playwright" not in (n or "").lower(), (
                        f"whatsapp_integration imports Playwright: {n}"
                    )

    def test_whatsapp_integration_no_export_engine(self):
        """whatsapp_integration must not import export_engine."""
        import ast, pathlib
        src = pathlib.Path(
            "app/services/whatsapp_integration.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for n in names:
                    assert "export_engine" not in (n or ""), (
                        f"whatsapp_integration imports export_engine: {n}"
                    )

    def test_whatsapp_integration_no_db_import(self):
        """whatsapp_integration must not do direct DB queries."""
        import ast, pathlib
        src = pathlib.Path(
            "app/services/whatsapp_integration.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = node.module if isinstance(node, ast.ImportFrom) else ""
                assert "app.db" not in (mod or ""), (
                    "whatsapp_integration imports app.db directly"
                )

    def test_export_engine_files_unchanged(self):
        """export_engine must not be modified by Phase 6."""
        import pathlib
        export_dir = pathlib.Path("app/export_engine")
        assert export_dir.exists(), "export_engine directory missing"
        # Verify it still has its core modules
        assert (export_dir / "__init__.py").exists()
        assert (export_dir / "schemas.py").exists()

    def test_no_pdf_generation_change(self):
        """Playwright PDF generation is not called from whatsapp_integration."""
        import pathlib
        src = pathlib.Path(
            "app/services/whatsapp_integration.py"
        ).read_text(encoding="utf-8")
        assert "generate_pdf" not in src
        assert "run_playwright" not in src
        assert "pdf_to_html" not in src

    def test_review_engine_still_functional(self):
        """Spot-check review_engine is undamaged."""
        from app.review_engine import build_review_session, generate_review_link
        assert callable(build_review_session)
        assert callable(generate_review_link)

    def test_webhook_imports_integration_module(self):
        """webhook.py must import whatsapp_integration."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "whatsapp_integration" in src

    def test_webhook_uses_make_save_reply(self):
        """webhook.py must call make_save_reply (not the old build_file_saved_message)."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "make_save_reply" in src

    def test_webhook_uses_make_file_received_reply(self):
        """webhook.py must call make_file_received_reply for media ack."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "make_file_received_reply" in src

    def test_webhook_uses_make_pre_export_warning(self):
        """webhook.py must call make_pre_export_warning."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "make_pre_export_warning" in src

    def test_webhook_uses_make_review_link_reply(self):
        """webhook.py must call make_review_link_reply."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "make_review_link_reply" in src

    def test_webhook_uses_make_name_confirmation_question(self):
        """webhook.py must call make_name_confirmation_question."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "make_name_confirmation_question" in src

    def test_webhook_has_pending_category_hint_dict(self):
        """webhook.py must declare _PENDING_CATEGORY_HINT."""
        import pathlib
        src = pathlib.Path("app/api/webhook.py").read_text(encoding="utf-8")
        assert "_PENDING_CATEGORY_HINT" in src


# ──────────────────────────────────────────────────────────────────────────────
# 11. whatsapp_messages public API still intact (no regression)
# ──────────────────────────────────────────────────────────────────────────────

class TestWhatsAppMessagesPublicAPI:
    def test_build_file_received_message(self):
        from app.services.whatsapp_messages import build_file_received_message
        assert "صورة" in build_file_received_message("image")
        assert "ملف PDF" in build_file_received_message("pdf")

    def test_build_evidence_saved_smart_no_regression(self):
        from app.services.whatsapp_messages import build_evidence_saved_smart
        from app.services.classification import IMPORTANCE_MEDIUM
        msg = build_evidence_saved_smart(
            ev_type="image", category="نشاط صفي",
            importance=IMPORTANCE_MEDIUM,
        )
        assert "✅" in msg
        assert "نشاط صفي" in msg

    def test_build_batch_summary_no_regression(self):
        from app.services.whatsapp_messages import build_batch_summary, BatchItem
        items = [BatchItem(category="التخطيط"), BatchItem(category="التخطيط")]
        msg = build_batch_summary(items)
        assert "2" in msg
        assert "التخطيط" in msg

    def test_build_review_link_message_no_regression(self):
        from app.services.whatsapp_messages import build_review_link_message
        msg = build_review_link_message("https://example.com/r/abc")
        assert "https://example.com/r/abc" in msg

    def test_build_review_ready_message_no_regression(self):
        from app.services.whatsapp_messages import build_review_ready_message
        msg = build_review_ready_message(active_count=5, strong_count=2)
        assert "5" in msg
        assert "✅" in msg
