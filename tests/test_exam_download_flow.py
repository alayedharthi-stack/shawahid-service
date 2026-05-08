"""
tests/test_exam_download_flow.py — Phase-13 Exam Download Button.

Scenarios covered (from the brief):
    1. After a successful exam_flow run, the PDF is persisted on disk
       and ``ExamConversationState.last_pdf_path`` points at it.
    2. ``/exams/download/{teacher_id}/{exam_id}`` returns the PDF with
       inline disposition.
    3. The download endpoint serves the HTML fallback when no PDF
       exists, and 404s for unknown exam IDs / traversal attempts.
    4. The new ``INTENT_SEND_LAST_EXAM`` covers every "where is my exam"
       phrase the brief lists.
    5. The GPT router fallback maps that intent to
       ``ACTION_SEND_LAST_EXAM`` (so GPT being down never re-routes the
       request to ``chat_reply``).
    6. ``record_generated_exam`` snapshots the exam metadata
       (subject / grade / exam_type) so the follow-up can label the
       button without re-querying the DB.

Tests are pure: no real OpenAI / WhatsApp / Playwright calls.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.conversation_engine.exam_state import (
    ExamConversationState,
    get_exam_state,
    record_generated_exam,
    reset_all_exam_states,
    update_last_exam_download_url,
)
from app.exam_engine.exam_export import ExamExportResult
from app.exam_engine.exam_flow import handle_exam_request
from app.services.intents import (
    INTENT_SEND_LAST_EXAM,
    detect_intent,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Reset the in-memory exam state and redirect storage to tmp_path."""
    reset_all_exam_states()
    from app.core.config import settings
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    yield
    reset_all_exam_states()


# ──────────────────────────────────────────────────────────────────────
# 1. INTENT_SEND_LAST_EXAM — every Arabic phrase in the brief
# ──────────────────────────────────────────────────────────────────────


class TestSendLastExamIntent:
    @pytest.mark.parametrize("text", [
        "أين رابط الاختبار؟",
        "وين رابط الاختبار",
        "ارسل الاختبار",
        "ارسل لي الاختبار",
        "ابعث الاختبار",
        "حمل الاختبار",
        "حمل لي الاختبار",
        "رابط الاختبار",
        "اعطني رابط الاختبار",
        "أين الاختبار",
        "وين الاختبار",
        "الاختبار الذي أنشأته",
        "الذي سويته قبل قليل",
        "ابغى رابط الاختبار",
    ])
    def test_phrase_maps_to_send_last_exam(self, text):
        intent = detect_intent(text)
        assert intent.intent == INTENT_SEND_LAST_EXAM, (text, intent)
        assert intent.confidence >= 0.8

    def test_safe_phrases_do_not_trigger(self):
        # "اعتمد الاختبار" ≈ "approve the exam" → exam_confirm, NOT send_last.
        assert detect_intent("اعتمد الاختبار").intent != INTENT_SEND_LAST_EXAM
        # "أريد اختبار رياضيات" → create_exam, NOT send_last.
        assert detect_intent("أريد اختبار رياضيات").intent != INTENT_SEND_LAST_EXAM


# ──────────────────────────────────────────────────────────────────────
# 2. GPT router fallback maps the intent to ACTION_SEND_LAST_EXAM
# ──────────────────────────────────────────────────────────────────────


class TestGptRouterFallback:
    def _decide(self, message: str):
        from app.services.gpt_router import RouterContext, decide_next_action
        # Force the fallback path by removing OPENAI key.
        from app.core.config import settings
        original = settings.OPENAI_API_KEY
        settings.OPENAI_API_KEY = ""
        try:
            return asyncio.run(decide_next_action(
                message,
                RouterContext(teacher_id=1),
            ))
        finally:
            settings.OPENAI_API_KEY = original

    def test_fallback_routes_to_send_last_exam(self):
        from app.services.gpt_router import ACTION_SEND_LAST_EXAM

        decision = self._decide("أين رابط الاختبار؟")
        assert decision.action == ACTION_SEND_LAST_EXAM
        assert decision.should_save_evidence is False

    def test_fallback_export_phrase_routes_to_send_last_exam(self):
        from app.services.gpt_router import ACTION_SEND_LAST_EXAM

        decision = self._decide("حمل الاختبار")
        assert decision.action == ACTION_SEND_LAST_EXAM
        assert decision.should_save_evidence is False


# ──────────────────────────────────────────────────────────────────────
# 3. ExamConversationState snapshot
# ──────────────────────────────────────────────────────────────────────


class TestExamStateSnapshot:
    def test_record_generated_exam_stores_metadata(self):
        st = record_generated_exam(
            teacher_id=42,
            exam_id="ex-abc1234567",
            pdf_path="/tmp/x.pdf",
            subject="الرياضيات",
            grade="الصف الرابع",
            exam_type="final",
        )
        assert st.last_exam_id == "ex-abc1234567"
        assert st.last_pdf_path == "/tmp/x.pdf"
        assert st.last_exam_subject == "الرياضيات"
        assert st.last_exam_grade == "الصف الرابع"
        assert st.last_exam_type == "final"
        assert st.has_last_exam is True

    def test_update_download_url_late_assignment(self):
        record_generated_exam(
            teacher_id=42,
            exam_id="ex-abc1234567",
            pdf_path=None,
            subject="العلوم",
        )
        update_last_exam_download_url(
            42, download_url="https://example.test/exams/download/42/ex-abc1234567",
        )
        st = get_exam_state(42)
        assert st.last_exam_download_url == (
            "https://example.test/exams/download/42/ex-abc1234567"
        )

    def test_has_last_exam_default_false(self):
        st = ExamConversationState(teacher_id=99)
        assert st.has_last_exam is False


# ──────────────────────────────────────────────────────────────────────
# 4. exam_flow persists PDF / HTML
# ──────────────────────────────────────────────────────────────────────


class TestPdfPersistence:
    def test_pdf_bytes_are_written_to_disk(self, monkeypatch):
        monkeypatch.setattr(
            "app.exam_engine.exam_export.export_exam_pdf",
            lambda exam: ExamExportResult(
                backend="playwright",
                html="<html>x</html>",
                pdf_bytes=b"%PDF-1.4 some payload",
            ),
        )
        result = handle_exam_request(
            teacher_id=11,
            text="أريد اختبار قصير رياضيات للصف الرابع الفصل الأول",
            render_pdf=True,
        )
        assert result.is_ready
        path = Path(result.pdf_path)
        assert path.is_file()
        assert path.suffix == ".pdf"
        assert path.read_bytes().startswith(b"%PDF")
        # The state must mirror what's on disk.
        st = get_exam_state(11)
        assert st.last_pdf_path == str(path)
        assert st.last_exam_id == result.exam.exam_id

    def test_html_fallback_when_no_pdf_bytes(self, monkeypatch):
        monkeypatch.setattr(
            "app.exam_engine.exam_export.export_exam_pdf",
            lambda exam: ExamExportResult(
                backend="html_only",
                html="<html><body>exam</body></html>",
                pdf_bytes=None,
                notes="playwright_unavailable",
            ),
        )
        result = handle_exam_request(
            teacher_id=12,
            text="أريد اختبار قصير علوم للصف الرابع الفصل الأول",
            render_pdf=True,
        )
        assert result.is_ready
        path = Path(result.pdf_path)
        assert path.is_file()
        assert path.suffix == ".html"
        assert "exam" in path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# 5. /exams/download route end-to-end (TestClient)
# ──────────────────────────────────────────────────────────────────────


class TestDownloadRoute:
    def _client(self):
        from app.main import app
        return TestClient(app)

    def _write_pdf(self, teacher_id: int, exam_id: str, body: bytes):
        from app.core.config import settings
        d = settings.teacher_storage(teacher_id) / "exams"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{exam_id}.pdf"
        p.write_bytes(body)
        return p

    def _write_html(self, teacher_id: int, exam_id: str, body: str):
        from app.core.config import settings
        d = settings.teacher_storage(teacher_id) / "exams"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{exam_id}.html"
        p.write_text(body, encoding="utf-8")
        return p

    def test_pdf_is_served_inline(self):
        self._write_pdf(7, "ex-abc1234567", b"%PDF-1.4 ok")
        client = self._client()
        resp = client.get("/exams/download/7/ex-abc1234567")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert "inline" in resp.headers["content-disposition"]
        assert resp.content.startswith(b"%PDF")

    def test_html_fallback_is_served(self):
        self._write_html(8, "ex-abc7654321", "<html><body>fallback</body></html>")
        client = self._client()
        resp = client.get("/exams/download/8/ex-abc7654321")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "fallback" in resp.text

    def test_missing_exam_returns_404(self):
        client = self._client()
        resp = client.get("/exams/download/9/ex-deadbeef00")
        assert resp.status_code == 404

    def test_invalid_exam_id_pattern_returns_404(self):
        client = self._client()
        # Slashes / dots inside the exam_id MUST be rejected.
        resp = client.get("/exams/download/9/..%2Fevil")
        assert resp.status_code in (404, 400)
        resp2 = client.get("/exams/download/9/not-a-real-id")
        assert resp2.status_code == 404

    def test_pdf_takes_priority_over_html(self):
        self._write_pdf(10, "ex-aaaaaaaaaa", b"%PDF-1.4 priority")
        self._write_html(10, "ex-aaaaaaaaaa", "<html>second</html>")
        client = self._client()
        resp = client.get("/exams/download/10/ex-aaaaaaaaaa")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")


# ──────────────────────────────────────────────────────────────────────
# 6. build_exam_download_url is stable and routable
# ──────────────────────────────────────────────────────────────────────


class TestDownloadUrlBuilder:
    def test_builder_shape(self):
        from app.api.exam_downloads import build_exam_download_url
        url = build_exam_download_url(teacher_id=42, exam_id="ex-abcdef1234")
        assert url.endswith("/exams/download/42/ex-abcdef1234")
        # No double slashes after the host segment.
        assert "//exams" not in url.replace("https://", "").replace("http://", "")


# ──────────────────────────────────────────────────────────────────────
# 7. Messages — warmer ready text + fallback strings
# ──────────────────────────────────────────────────────────────────────


class TestExamMessages:
    def test_ready_message_is_warm(self):
        from app.exam_engine.messages import build_exam_ready_message
        from app.exam_engine.schemas import (
            ExamProfile, ExamQuestion, ExamRequest, GeneratedExam,
            QTYPE_TRUE_FALSE,
        )
        prof = ExamProfile(
            teacher_name="إياد", school_name="مدرسة س",
            education_admin="إدارة س", region="منطقة س",
            subject="الرياضيات", grade="الصف الرابع",
            stage="primary", semester="1", academic_year="1446",
            exam_type="final", duration_minutes=45, total_marks=20,
        )
        req = ExamRequest(
            teacher_id=1, exam_type="final", subject="الرياضيات",
            grade="الصف الرابع", stage="primary", semester="1",
            total_questions=2, total_marks=20, duration_minutes=45,
        )
        q = ExamQuestion(
            id="q1", type=QTYPE_TRUE_FALSE,
            text="السؤال", correct_answer="True", marks=10,
        )
        exam = GeneratedExam(profile=prof, questions=(q, q), request=req)
        msg = build_exam_ready_message(exam)
        # Warm headline + the new "👇 button" hint.
        assert "تم تجهيز اختبارك بنجاح" in msg
        assert "🎉" in msg
        assert "📄" in msg

    def test_no_last_exam_message_is_helpful(self):
        from app.exam_engine.messages import build_no_last_exam_message
        msg = build_no_last_exam_message()
        assert "أنشئ اختبار" in msg
        assert "🌿" in msg or "📘" in msg

    def test_text_fallback_includes_url(self):
        from app.exam_engine.messages import build_exam_download_text_fallback
        msg = build_exam_download_text_fallback(
            download_url="https://x.test/exams/download/1/ex-abc1234567",
            subject="الرياضيات",
            grade="الصف الرابع",
            exam_type="final",
        )
        assert "https://x.test/exams/download/1/ex-abc1234567" in msg
        assert "الرياضيات" in msg
        assert "تحميل" in msg


# ──────────────────────────────────────────────────────────────────────
# 8. Button helper — gracefully falls back to text when API fails
# ──────────────────────────────────────────────────────────────────────


class TestSendExamDownloadButton:
    def test_button_success_skips_text_fallback(self, monkeypatch):
        sent_text: list[str] = []
        sent_button: list[tuple[str, str]] = []

        async def _fake_button(to, *, body_text, button_label, url, teacher_id=None):
            sent_button.append((button_label, url))
            return True

        async def _fake_text(to, body, *, teacher_id=None, context=None):
            sent_text.append(body)
            return True

        monkeypatch.setattr("app.services.whatsapp.send_whatsapp_button", _fake_button)
        monkeypatch.setattr(
            "app.api.webhook.send_whatsapp_message", _fake_text,
        )

        from app.api.webhook import _send_exam_download_button
        asyncio.run(_send_exam_download_button(
            teacher_phone="+966500000000",
            teacher_id=1,
            exam_id="ex-abc1234567",
            download_url="https://x.test/exams/download/1/ex-abc1234567",
            subject="الرياضيات",
            grade="الصف الرابع",
            exam_type="final",
        ))
        assert sent_button == [(
            "تحميل الاختبار 📄",
            "https://x.test/exams/download/1/ex-abc1234567",
        )]
        assert sent_text == []

    def test_button_failure_falls_back_to_text_with_url(self, monkeypatch):
        sent_text: list[str] = []

        async def _fake_button(to, *, body_text, button_label, url, teacher_id=None):
            return False  # WhatsApp API said no.

        async def _fake_text(to, body, *, teacher_id=None, context=None):
            sent_text.append(body)
            return True

        monkeypatch.setattr("app.services.whatsapp.send_whatsapp_button", _fake_button)
        monkeypatch.setattr(
            "app.api.webhook.send_whatsapp_message", _fake_text,
        )

        from app.api.webhook import _send_exam_download_button
        asyncio.run(_send_exam_download_button(
            teacher_phone="+966500000000",
            teacher_id=1,
            exam_id="ex-abc1234567",
            download_url="https://x.test/exams/download/1/ex-abc1234567",
            subject="الرياضيات",
            grade="الصف الرابع",
            exam_type="final",
        ))
        assert len(sent_text) == 1
        assert (
            "https://x.test/exams/download/1/ex-abc1234567"
            in sent_text[0]
        )
