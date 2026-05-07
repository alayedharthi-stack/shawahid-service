"""
Phase-3 intelligence tests.
───────────────────────────

Cover the new modules without touching OpenAI, the database, the
exporter, or the templates:

    • ``app.services.intents`` — semantic intent detection
    • ``app.services.classification`` — smart multi-signal classifier
    • ``app.services.whatsapp_messages`` — Phase-3 reply builders
    • ``app.services.gpt_brain.analyze_evidence_structured`` — mocked

Also includes an emoji-discipline check: every Phase-3 reply uses
at most one emoji per visual block.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.classification import (
    DEFAULT_CATEGORY,
    IMPORTANCE_MEDIUM,
    IMPORTANCE_SIMPLE,
    IMPORTANCE_STRONG,
    classify_evidence,
    score_importance,
)
from app.services.intents import (
    INTENT_CATEGORY_HINT,
    INTENT_DELETE_LAST,
    INTENT_DUPLICATE,
    INTENT_EXPORT,
    INTENT_REVIEW,
    detect_intent,
    looks_like_name_change,
    normalize,
)
from app.services.whatsapp_messages import (
    BatchItem,
    build_batch_summary,
    build_evidence_saved_smart,
    build_file_received_message,
    build_name_confirmation_question,
    build_strong_evidence_callout,
    build_uncertain_classification_note,
)


# ──────────────────────────────────────────────────────────────────────
# Intent detection
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "صدر الآن",
        "صدّر الملف",
        "أبغى ملف الشواهد",
        "جهز ملفي",
        "ابعث الملف",
        "اعطني الرابط",
    ],
)
def test_intent_detects_export_phrases(phrase: str):
    intent = detect_intent(phrase)
    assert intent.intent == INTENT_EXPORT, f"failed for: {phrase}"
    assert intent.confidence >= 0.85


def test_intent_detects_review():
    assert detect_intent("راجع الشواهد").intent == INTENT_REVIEW
    assert detect_intent("ارني ملفي قبل التصدير").intent == INTENT_REVIEW


def test_intent_detects_delete_last():
    assert detect_intent("احذف آخر شاهد").intent == INTENT_DELETE_LAST


def test_intent_detects_duplicate():
    assert detect_intent("هذا مكرر").intent == INTENT_DUPLICATE


def test_intent_detects_category_hint_plan():
    out = detect_intent("هذه خطة")
    assert out.intent == INTENT_CATEGORY_HINT
    assert out.payload == {"category": "التخطيط"}


def test_intent_detects_category_hint_assessment():
    out = detect_intent("هذا اختبار")
    assert out.intent == INTENT_CATEGORY_HINT
    assert out.payload == {"category": "التقويم"}


def test_intent_detects_category_hint_followup():
    out = detect_intent("هذا سجل متابعة")
    assert out.intent == INTENT_CATEGORY_HINT
    assert out.payload == {"category": "سجل المتابعة"}


def test_intent_normalize_handles_diacritics_and_hamza():
    # All five variants should normalise to the same form.
    assert normalize("صَدِّر") == normalize("صدر")
    assert normalize("أبغى") == normalize("ابغى")
    assert normalize("الإختبار") == normalize("الاختبار")


# ──────────────────────────────────────────────────────────────────────
# Classification
# ──────────────────────────────────────────────────────────────────────


def test_classify_pdf_weekly_plan_to_planning():
    text = (
        "خطة أسبوعية للوحدة الأولى — الأسبوع الأول. "
        "نواتج التعلم: يستطيع الطالب تطبيق العمليات الأربع. "
        "توزيع المنهج للفصل الدراسي."
    )
    result = classify_evidence(
        filename="weekly_plan.pdf",
        extracted_text=text,
        evidence_type="pdf",
    )
    assert result.category == "التخطيط"
    assert result.confidence >= 0.6


def test_classify_pdf_test_paper_to_assessment():
    text = (
        "اختبار نهائي مادة الرياضيات. الدرجة الكلية 40. "
        "السؤال الأول: اختر الإجابة الصحيحة. ورقة عمل."
    )
    result = classify_evidence(
        filename="exam_q3.pdf",
        extracted_text=text,
        evidence_type="pdf",
    )
    assert result.category == "التقويم"
    assert result.confidence >= 0.6


def test_classify_school_timetable_to_administrative():
    """Days × periods grid must NOT be planning."""
    text = (
        "الأحد - الحصة الأولى رياضيات - الحصة الثانية عربي\n"
        "الاثنين - الحصة الأولى علوم - الحصة الثانية اجتماعيات\n"
        "الثلاثاء - الحصة الأولى رياضيات\n"
    )
    result = classify_evidence(
        filename="schedule.pdf",
        extracted_text=text,
        evidence_type="pdf",
    )
    assert result.category == "ملفات إدارية"
    assert "جدول مدرسي" in result.reason


def test_classify_falls_back_when_no_signals():
    result = classify_evidence(
        filename="random.pdf",
        extracted_text="",
        evidence_type="pdf",
    )
    assert result.category == DEFAULT_CATEGORY
    assert result.needs_confirmation is True


def test_classify_uses_caption_signal():
    result = classify_evidence(
        filename="IMG_0001.jpg",
        extracted_text="",
        caption="نشاط تعلم تعاوني داخل الصف",
        evidence_type="image",
    )
    assert result.category in {"التعلم النشط", "نشاط صفي", "التعلم التعاوني"}


# ──────────────────────────────────────────────────────────────────────
# Importance scoring
# ──────────────────────────────────────────────────────────────────────


def test_importance_simple_for_short_admin_text():
    assert (
        score_importance(
            category="ملفات إدارية", evidence_type="pdf",
            norm_blob="تعميم", confidence=0.6,
        )
        == IMPORTANCE_SIMPLE
    )


def test_importance_strong_for_video_in_planning():
    assert (
        score_importance(
            category="التعلم النشط", evidence_type="video",
            norm_blob="نشاط تفاعلي", confidence=0.7,
        )
        == IMPORTANCE_STRONG
    )


def test_importance_strong_when_high_confidence_high_value():
    assert (
        score_importance(
            category="التخطيط", evidence_type="pdf",
            norm_blob="نواتج التعلم", confidence=0.9,
        )
        == IMPORTANCE_STRONG
    )


def test_importance_medium_default():
    assert (
        score_importance(
            category="نشاط صفي", evidence_type="image",
            norm_blob="نشاط الصف", confidence=0.7,
        )
        == IMPORTANCE_MEDIUM
    )


# ──────────────────────────────────────────────────────────────────────
# Name confirmation
# ──────────────────────────────────────────────────────────────────────


def test_name_change_triggers_confirmation():
    transcript = "اعتمد اسمي تركي عايد الحارثي"
    assert (
        looks_like_name_change(transcript, current_name="تركي الحارثي")
        is True
    )


def test_name_change_skipped_when_same_name():
    transcript = "اسمي تركي الحارثي"
    assert (
        looks_like_name_change(transcript, current_name="تركي الحارثي")
        is False
    )


def test_name_change_skipped_without_trigger_phrase():
    transcript = "أرسلت لك الملف الجديد"
    assert (
        looks_like_name_change(transcript, current_name="تركي الحارثي")
        is False
    )


def test_name_confirmation_question_preserves_exact_name():
    """Names must NEVER be auto-corrected — the message must contain
    the exact characters the speaker said."""
    msg = build_name_confirmation_question("تركي عايد الحارثي")
    assert "تركي عايد الحارثي" in msg
    assert "نعم، اعتمده" in msg
    assert "لا، سأكتبه من جديد" in msg
    # Unchanged forms — guard against a future "auto-fixer".
    for forbidden in ("عائد", "الحارفي", "حسني"):
        assert forbidden not in msg


# ──────────────────────────────────────────────────────────────────────
# WhatsApp messages — content + emoji discipline
# ──────────────────────────────────────────────────────────────────────


def test_file_received_message_short_and_one_emoji_per_line():
    msg = build_file_received_message("pdf")
    lines = msg.splitlines()
    assert len(lines) == 2
    assert "وصلني" in lines[0]
    assert "✅" in lines[0]
    assert "🔍" in lines[1]


def test_evidence_saved_smart_renders_full_block():
    msg = build_evidence_saved_smart(
        ev_type="pdf",
        category="التخطيط",
        title="خطة الوحدة الأولى",
        importance=IMPORTANCE_STRONG,
    )
    assert msg.startswith("تم حفظ الشاهد وتحليله بنجاح ✅")
    assert "📌 العنوان: خطة الوحدة الأولى" in msg
    assert "🗂️ المحور: التخطيط" in msg
    assert "⭐ قوة الشاهد: قوي" in msg


def test_evidence_saved_simple_omits_strength_line():
    """A 'simple' card shouldn't shame the teacher with a star."""
    msg = build_evidence_saved_smart(
        ev_type="pdf", category="ملفات إدارية",
        importance=IMPORTANCE_SIMPLE,
    )
    assert "⭐" not in msg


def test_evidence_saved_review_hint_when_uncertain():
    msg = build_evidence_saved_smart(
        ev_type="pdf", category="التخطيط",
        importance=IMPORTANCE_MEDIUM, needs_review=True,
    )
    assert "✏️" in msg
    assert "مراجع" in msg


def test_duplicate_message():
    msg = build_evidence_saved_smart(
        ev_type="pdf", category="التخطيط",
        title="نفس الخطة",
        is_duplicate=True,
    )
    assert msg.startswith("⚠️")
    assert "موجود مسبقًا" in msg


def test_strong_evidence_callout_short():
    msg = build_strong_evidence_callout()
    assert "🌟" in msg
    assert len(msg) <= 80


def test_uncertain_classification_note():
    msg = build_uncertain_classification_note()
    assert "✏️" in msg
    assert "مراجع" in msg


def test_batch_summary_sorts_by_count():
    items = [
        BatchItem(category="التخطيط"),
        BatchItem(category="التخطيط"),
        BatchItem(category="التقويم"),
        BatchItem(category="سجل المتابعة"),
        BatchItem(category="نشاط صفي", needs_review=True),
    ]
    msg = build_batch_summary(items)
    lines = msg.splitlines()
    assert lines[0] == "تم استلام 5 شواهد ✅"
    # The largest bucket (التخطيط: 2) must appear before the
    # singleton categories.
    cat_lines = [ln for ln in lines if ln.startswith("📌")]
    assert "2 في التخطيط" in cat_lines[0]
    # Last line mentions the review-needed file.
    assert "يحتاج مراجعة بسيطة" in lines[-1]


def test_emoji_discipline_no_spam():
    """Across every Phase-3 builder, no single line carries more than
    two emojis. Emoji-spam was the #1 complaint about the old flow."""
    import re

    emoji_re = re.compile(
        r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    )

    samples = [
        build_file_received_message("pdf"),
        build_evidence_saved_smart(
            ev_type="pdf", category="التخطيط",
            title="خطة الوحدة", importance=IMPORTANCE_STRONG,
        ),
        build_evidence_saved_smart(
            ev_type="pdf", category="ملفات إدارية",
            importance=IMPORTANCE_SIMPLE,
        ),
        build_strong_evidence_callout(),
        build_uncertain_classification_note(),
        build_name_confirmation_question("تركي الحارثي"),
        build_batch_summary([
            BatchItem(category="التخطيط"),
            BatchItem(category="التقويم"),
        ]),
    ]
    for msg in samples:
        for line in msg.splitlines():
            count = len(emoji_re.findall(line))
            assert count <= 2, (
                f"line carries {count} emojis (max allowed 2): {line!r}"
            )


# ──────────────────────────────────────────────────────────────────────
# GPT analyze_evidence_structured — mocked, no network
# ──────────────────────────────────────────────────────────────────────


def test_analyze_evidence_structured_returns_full_schema(monkeypatch):
    """A mocked OpenAI call must produce every key the webhook reads."""
    from app.services import gpt_brain

    monkeypatch.setattr(
        gpt_brain.settings, "OPENAI_API_KEY", "test-key", raising=False
    )

    fake_payload = {
        "title": "خطة الأسبوع الأول",
        "evidence_type": "pdf",
        "category": "التخطيط",
        "subcategory": "خطة أسبوعية",
        "description": "خطة تفصيلية للأسبوع الأول مع نواتج التعلم.",
        "objective": "إكساب الطلاب مهارة الجمع والطرح.",
        "student_impact": "تحسّن واضح في حل المسائل اللفظية.",
        "teacher_reflection": "سأركّز أكثر على التطبيق العملي.",
        "confidence_score": 0.92,
        "importance_score": "قوي",   # Arabic synonym → coerced.
        "whatsapp_summary": "خطة أسبوعية ممتازة 🌟",
        "needs_teacher_confirmation": False,
        "confirmation_question": "",
    }

    class _FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})

    class _FakeResponse:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]

    class _FakeChat:
        def __init__(self, content):
            self._content = content

        @property
        def completions(self):
            outer = self

            class _C:
                def create(self, **_kw):
                    import json as _json
                    return _FakeResponse(_json.dumps(fake_payload))

            return _C()

    class _FakeOpenAI:
        def __init__(self, *_a, **_kw):
            self.chat = _FakeChat(fake_payload)

    monkeypatch.setattr(
        "app.services.gpt_brain.OpenAI", _FakeOpenAI,
        raising=False,
    )
    # The function does ``from openai import OpenAI`` at call time,
    # so we monkeypatch the import path directly.
    import sys, types

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI
    sys.modules["openai"] = fake_module

    result = gpt_brain.analyze_evidence_structured(
        content="خطة الأسبوع الأول مع نواتج التعلم.",
        evidence_type="pdf",
        filename="weekly_plan.pdf",
    )
    assert result is not None
    for key in gpt_brain.STRUCTURED_EVIDENCE_KEYS:
        assert key in result
    # Arabic "قوي" must be normalised to the canonical English label.
    assert result["importance_score"] == "strong"
    assert 0.0 <= result["confidence_score"] <= 1.0


def test_analyze_evidence_structured_returns_none_without_api_key(
    monkeypatch,
):
    from app.services import gpt_brain

    monkeypatch.setattr(
        gpt_brain.settings, "OPENAI_API_KEY", "", raising=False
    )
    out = gpt_brain.analyze_evidence_structured(
        content="نص", evidence_type="text",
    )
    assert out is None
