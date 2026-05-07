"""
exam_engine.prompt_builder — structured prompt template for future GPT use.

Phase-10 status: prompt assembly only — no GPT call. The phase brief
explicitly asks for "أسئلة أساسية محدودة ومختبرة"; this module
prepares the *future* prompt so when GPT is wired in we already
control:

    • the system role (generate Saudi-curriculum-aligned exams)
    • mandatory metadata (subject, grade, stage, semester, exam_type)
    • output schema (JSON shape that maps 1:1 to ``ExamQuestion``)
    • constraints (no copyrighted text, ≤ N questions, etc.)

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

from app.exam_engine.schemas import (
    EXAM_TYPE_LABELS_AR,
    QTYPE_LABELS_AR,
    ExamRequest,
)


SYSTEM_ROLE = (
    "أنت مساعد لمعلمي وزارة التعليم بالمملكة العربية السعودية. "
    "مهمتك إنشاء أسئلة اختبارات منظمة، مرتبطة بالمنهج، "
    "تتجنب النسخ من مصادر خارجية، وتلتزم بصياغة محايدة ومناسبة "
    "للمرحلة المطلوبة."
)

OUTPUT_SCHEMA_HINT = (
    'كل سؤال يجب أن يكون JSON بالشكل التالي:\n'
    '{ "type": "mcq|true_false|fill_blank|short|match", '
    '"text": "...", "choices": [...], '
    '"correct_answer": "(فهرس الخيار) أو نص الإجابة", '
    '"marks": <رقم>, "difficulty": "easy|medium|hard", '
    '"learning_outcome": "..." }'
)


def build_exam_prompt(request: ExamRequest, *, max_questions: int | None = None) -> str:
    """Compose the prompt the engine will hand to GPT in a future phase.

    The function returns a single Arabic string — formatted for direct
    use as a chat ``user`` message. The system prompt (``SYSTEM_ROLE``)
    should be sent separately when GPT is finally wired in.
    """
    qtype_labels = ", ".join(
        QTYPE_LABELS_AR.get(t, t) for t in request.question_types
    ) or "اختيار من متعدد"

    n = max_questions or request.total_questions

    lines: list[str] = [
        f"اطلب اختبارًا من نوع: {EXAM_TYPE_LABELS_AR.get(request.exam_type, request.exam_type)}.",
        f"المادة: {request.subject or 'غير محدد'}",
        f"المرحلة: {request.stage or 'غير محدد'}",
        f"الصف: {request.grade or 'غير محدد'}",
        f"الفصل الدراسي: {request.semester or 'غير محدد'}",
    ]
    if request.unit:
        lines.append(f"الوحدة: {request.unit}")
    if request.lesson:
        lines.append(f"الدرس: {request.lesson}")
    if request.week:
        lines.append(f"الأسبوع: {request.week}")
    if request.topic:
        lines.append(f"الموضوع: {request.topic}")

    lines += [
        f"عدد الأسئلة المطلوبة: {n}.",
        f"المجموع الكلي للدرجات: {request.total_marks}.",
        f"الزمن: {request.duration_minutes} دقيقة.",
        f"أنواع الأسئلة: {qtype_labels}.",
        f"الصعوبة: {request.difficulty}.",
        "",
        "قيود مهمة:",
        "1) لا تنسخ سؤالًا حرفيًا من أي مصدر خارجي.",
        "2) كل سؤال موضوعي يجب أن يحتوي على إجابة صحيحة واضحة.",
        "3) مجموع درجات الأسئلة = الدرجة الكلية المذكورة.",
        "4) لا أسئلة مكررة.",
        "5) الصياغة محايدة ومناسبة للمرحلة المطلوبة.",
        "",
        "صيغة الإخراج:",
        OUTPUT_SCHEMA_HINT,
    ]
    return "\n".join(lines)


__all__ = ["SYSTEM_ROLE", "OUTPUT_SCHEMA_HINT", "build_exam_prompt"]
