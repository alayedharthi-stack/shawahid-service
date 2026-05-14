"""
PDF kind classifier (Phase 1).
──────────────────────────────

Pure, deterministic, side-effect-free pre-classifier that answers a
single question for an incoming PDF inside ``shawahid-service``:

    Is this PDF an exam / worksheet (so the teacher should be asked
    whether to save it as evidence or rewrite it on the school
    template) — or is it a normal evidence document (plan, log,
    circular, certificate, report, attendance, activity, …)?

Hard rules (Phase 1):
    • No DB, no network, no GPT — purely text + filename heuristics.
    • Does NOT change any save flow. Caller only logs the result.
    • Only operates on PDF inputs. Images / video / audio / URLs are
      classified elsewhere and must not call this function.
    • Module is isolated inside ``shawahid-service`` and does not
      import from Nahla AI, campaigns, billing, subscriptions,
      catalog, coexistence, 360dialog, customer segmentation, or any
      shared cross-service logic.

Return contract:

    {
        "pdf_kind": "exam_or_worksheet" | "evidence" | "unknown",
        "confidence": 0.0 - 1.0,
        "reason": "short human-readable explanation",
        "detected_type": "exam" | "worksheet" | "assignment"
                          | "assessment" | None,
    }
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Keyword banks
# ──────────────────────────────────────────────────────────────────────
# All keywords are stored already normalised (NFKC + ا/ي/ه folding +
# diacritic strip + lowercase) so we only need to normalise the input
# once.

# Strong, near-deterministic exam / worksheet signals.
_EXAM_KW: tuple[str, ...] = (
    "اختبار",
    "الاختبار",
    "امتحان",
    "الامتحان",
    "اختبار قصير",
    "اختبار نهائي",
    "اختبار فتري",
    "نموذج اختبار",
    "نموذج امتحان",
    "نموذج قياس",
    "قياس مهارات",
    "اختبار قياس",
)

_WORKSHEET_KW: tuple[str, ...] = (
    "ورقة عمل",
    "اوراق عمل",
    "أوراق عمل",
    "ورقه عمل",
)

_ASSIGNMENT_KW: tuple[str, ...] = (
    "واجب",
    "الواجب",
    "واجب منزلي",
    "تكليف",
)

_ASSESSMENT_KW: tuple[str, ...] = (
    "تقويم",
    "التقويم",
    "تقويم تكويني",
    "تقويم ختامي",
    "قياس",
)

# Supporting question-format signals (must co-occur with a primary
# label or with multiple of themselves to count).
_QUESTION_SIGNALS: tuple[str, ...] = (
    "السؤال الاول",
    "السؤال الثاني",
    "السؤال الثالث",
    "السؤال الرابع",
    "السؤال الخامس",
    "السؤال :",
    "س1",
    "س2",
    "س3",
    "اختر الاجابه الصحيحه",
    "اختر الاجابة الصحيحة",
    "اختر الإجابة الصحيحة",
    "اختر من بين",
    "اختر من متعدد",
    "اختيار من متعدد",
    "ضع علامه صح",
    "ضع علامة صح",
    "ضع علامه (√)",
    "ضع علامة (√)",
    "صح ام خطا",
    "صح أم خطأ",
    "صل بين",
    "اكمل الفراغ",
    "أكمل الفراغ",
    "اكمل ما يلي",
    "أكمل ما يلي",
    "اجب عن",
    "أجب عن",
    "اجب عما يلي",
    "أجب عما يلي",
    "الدرجه",
    "الدرجة",
    "مجموع الدرجات",
    "توزيع الدرجات",
    "علامه السؤال",
    "علامة السؤال",
)

# Evidence (non-exam) signals.
_EVIDENCE_KW: tuple[str, ...] = (
    "خطه",
    "خطة",
    "خطه اسبوعيه",
    "خطة أسبوعية",
    "خطه يوميه",
    "خطة يومية",
    "تحضير",
    "تحضير الدرس",
    "سجل",
    "سجل متابعه",
    "سجل متابعة",
    "سجل الحضور",
    "سجل غياب",
    "تعميم",
    "التعميم",
    "شهاده",
    "شهادة",
    "شهاده شكر",
    "شهادة شكر",
    "شهاده تقدير",
    "شهادة تقدير",
    "تقرير",
    "التقرير",
    "خطاب",
    "الخطاب",
    "حضور",
    "غياب",
    "نشاط",
    "النشاط",
    "نشاط صفي",
    "نشاط لاصفي",
    "نشاط لا صفي",
    "انجاز",
    "إنجاز",
    "الانجاز",
    "الإنجاز",
    "تكريم",
    "توثيق فعاليه",
    "توثيق فعالية",
    "محضر اجتماع",
    "خطه علاجيه",
    "خطة علاجية",
    "خطه اثرائيه",
    "خطة إثرائية",
    "زياره صفيه",
    "زيارة صفية",
)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Hits:
    exam: int = 0
    worksheet: int = 0
    assignment: int = 0
    assessment: int = 0
    questions: int = 0
    evidence: int = 0


def _count_hits(haystack: str, needles: Iterable[str]) -> tuple[int, list[str]]:
    if not haystack:
        return 0, []
    found: list[str] = []
    for n in needles:
        if n and n in haystack:
            found.append(n)
    return len(found), found


def _gather(haystack: str) -> tuple[_Hits, dict[str, list[str]]]:
    exam_n,  exam_hits  = _count_hits(haystack, _EXAM_KW)
    ws_n,    ws_hits    = _count_hits(haystack, _WORKSHEET_KW)
    asg_n,   asg_hits   = _count_hits(haystack, _ASSIGNMENT_KW)
    ass_n,   ass_hits   = _count_hits(haystack, _ASSESSMENT_KW)
    q_n,     q_hits     = _count_hits(haystack, _QUESTION_SIGNALS)
    ev_n,    ev_hits    = _count_hits(haystack, _EVIDENCE_KW)
    hits = _Hits(
        exam=exam_n, worksheet=ws_n, assignment=asg_n,
        assessment=ass_n, questions=q_n, evidence=ev_n,
    )
    matched = {
        "exam": exam_hits,
        "worksheet": ws_hits,
        "assignment": asg_hits,
        "assessment": ass_hits,
        "questions": q_hits,
        "evidence": ev_hits,
    }
    return hits, matched


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────
def classify_pdf_kind(
    *,
    extracted_text: str | None = None,
    filename: str | None = None,
    first_lines: str | None = None,
    has_questions: bool = False,
    has_grades_table: bool = False,
    has_objectives: bool = False,
    detected_keywords: list[str] | None = None,
) -> dict:
    """Classify whether a PDF is an exam/worksheet or a normal evidence.

    Inputs are all optional — pass whatever the caller already has. The
    function is pure: same inputs → same output.

    The function never raises on bad input; on totally empty input it
    returns ``pdf_kind="unknown"`` with confidence ``0.0``.
    """
    text_blob_raw = "\n".join(
        part for part in (extracted_text, first_lines) if part
    )
    text_norm = normalize(text_blob_raw) if text_blob_raw else ""
    name_norm = normalize(filename) if filename else ""

    text_hits, text_matched = _gather(text_norm)
    name_hits, name_matched = _gather(name_norm)

    # Combined matched keywords for reason string.
    def _merge_matched(key: str) -> list[str]:
        return sorted(set(text_matched.get(key, []) + name_matched.get(key, [])))

    matched_all = {
        k: _merge_matched(k)
        for k in ("exam", "worksheet", "assignment", "assessment",
                  "questions", "evidence")
    }

    # ── Scoring ──────────────────────────────────────────────────────
    # Filename hits are deliberately weighted slightly less than body
    # hits — a stray filename can mislead, but the body text is hard
    # to fake.
    exam_score = (
        text_hits.exam       * 3
        + text_hits.worksheet * 3
        + text_hits.assessment * 1
        + text_hits.questions * 1
        + name_hits.exam       * 2
        + name_hits.worksheet  * 2
        + name_hits.assessment * 1
    )
    # An assignment only "feels" like an exam if it carries question
    # markers; on its own it can equally be a normal homework note.
    if text_hits.assignment or name_hits.assignment:
        if text_hits.questions >= 1 or has_questions:
            exam_score += 2

    # Structural signals from the PDF extractor.
    if has_questions:
        exam_score += 2
    if has_grades_table:
        exam_score += 1
    if has_objectives:
        # Objectives line up with lesson plans (evidence) — slight
        # tilt away from exam.
        exam_score -= 1
    for kw in (detected_keywords or []):
        kw_norm = normalize(kw)
        if "اسئله" in kw_norm or "اختبار" in kw_norm:
            exam_score += 1
        if "اهداف" in kw_norm:
            exam_score -= 1

    evidence_score = (
        text_hits.evidence * 3
        + name_hits.evidence * 2
    )
    if has_objectives:
        evidence_score += 1

    # ── Decision ─────────────────────────────────────────────────────
    detected_type = _detect_type(text_hits, name_hits, has_questions)

    pdf_kind: str
    confidence: float
    reason_parts: list[str] = []

    if exam_score >= 3 and exam_score >= evidence_score + 2:
        pdf_kind = "exam_or_worksheet"
        # Cap confidence at 0.98 — we are never 100% certain without GPT.
        confidence = min(0.55 + 0.07 * exam_score, 0.98)
        reason_parts.append(f"exam_score={exam_score}")
        if matched_all["exam"]:
            reason_parts.append("exam_kw=" + ",".join(matched_all["exam"][:3]))
        if matched_all["worksheet"]:
            reason_parts.append("worksheet_kw=" + ",".join(matched_all["worksheet"][:3]))
        if matched_all["questions"]:
            reason_parts.append("q_kw=" + ",".join(matched_all["questions"][:3]))
        if has_grades_table:
            reason_parts.append("grades_table")
        if has_questions:
            reason_parts.append("structural_questions")
    elif evidence_score >= 3 and evidence_score >= exam_score + 2:
        pdf_kind = "evidence"
        confidence = min(0.55 + 0.08 * evidence_score, 0.97)
        reason_parts.append(f"evidence_score={evidence_score}")
        if matched_all["evidence"]:
            reason_parts.append("evidence_kw=" + ",".join(matched_all["evidence"][:3]))
        if has_objectives:
            reason_parts.append("objectives")
    else:
        pdf_kind = "unknown"
        # Confidence reflects "how ambivalent" we are: if both signals
        # are zero we are very unsure; if both are present we are
        # moderately unsure.
        signal_total = exam_score + evidence_score
        if signal_total <= 0:
            confidence = 0.0
            reason_parts.append("no signals")
        else:
            confidence = round(min(0.4, 0.1 + 0.05 * signal_total), 2)
            reason_parts.append(
                f"ambiguous: exam_score={exam_score}, evidence_score={evidence_score}"
            )

    return {
        "pdf_kind": pdf_kind,
        "confidence": round(float(confidence), 2),
        "reason": "; ".join(reason_parts) or "no signals",
        "detected_type": detected_type if pdf_kind == "exam_or_worksheet" else None,
    }


def _detect_type(text_hits: _Hits, name_hits: _Hits, has_questions: bool) -> str | None:
    # Priority: exam > worksheet > assignment (with questions) > assessment.
    if text_hits.exam or name_hits.exam:
        return "exam"
    if text_hits.worksheet or name_hits.worksheet:
        return "worksheet"
    if (text_hits.assignment or name_hits.assignment) and (
        text_hits.questions >= 1 or has_questions
    ):
        return "assignment"
    if text_hits.assessment or name_hits.assessment:
        return "assessment"
    return None


__all__ = ["classify_pdf_kind"]
