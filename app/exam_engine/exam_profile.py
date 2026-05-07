"""
exam_engine.exam_profile — build an ``ExamProfile`` from teacher state.

The webhook will eventually call ``build_exam_profile_from_request``
to fill the header data printed on every exam. We keep this layer
deliberately thin: it only assembles the DTO from already-resolved
inputs (a teacher record and a request). Detection of names/subjects
from raw text lives in ``conversation_engine.profile_context``.

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

from app.exam_engine.schemas import EXAM_TYPE_QUICK, ExamProfile, ExamRequest


def build_exam_profile(
    *,
    request: ExamRequest,
    teacher_name: str | None = None,
    school_name: str | None = None,
    education_admin: str | None = None,
    region: str | None = None,
    academic_year: str | None = None,
) -> ExamProfile:
    """Compose the exam header from caller-supplied pieces.

    All inputs are optional — the renderer fills missing slots with
    dotted placeholders so the printed sheet always looks complete.
    """
    return ExamProfile(
        teacher_name=teacher_name,
        school_name=school_name,
        education_admin=education_admin,
        region=region,
        subject=request.subject,
        grade=request.grade,
        stage=request.stage,
        semester=request.semester,
        academic_year=academic_year,
        exam_type=request.exam_type or EXAM_TYPE_QUICK,
        duration_minutes=request.duration_minutes or 30,
        total_marks=request.total_marks or 20,
    )


def merge_profile(
    base: ExamProfile,
    *,
    teacher_name: str | None = None,
    school_name: str | None = None,
    education_admin: str | None = None,
    region: str | None = None,
    subject: str | None = None,
    grade: str | None = None,
    stage: str | None = None,
    semester: str | None = None,
    academic_year: str | None = None,
    exam_type: str | None = None,
    duration_minutes: int | None = None,
    total_marks: int | None = None,
) -> ExamProfile:
    """Return a new ``ExamProfile`` overriding ``base`` with non-None args.

    Useful when the teacher edits the request mid-flow ("غيّر المادة
    إلى علوم") and we want to rebuild the header without redoing the
    whole pipeline.
    """
    return ExamProfile(
        teacher_name=teacher_name if teacher_name is not None else base.teacher_name,
        school_name=school_name if school_name is not None else base.school_name,
        education_admin=education_admin if education_admin is not None else base.education_admin,
        region=region if region is not None else base.region,
        country=base.country,
        ministry=base.ministry,
        subject=subject if subject is not None else base.subject,
        grade=grade if grade is not None else base.grade,
        stage=stage if stage is not None else base.stage,
        semester=semester if semester is not None else base.semester,
        academic_year=academic_year if academic_year is not None else base.academic_year,
        exam_type=exam_type if exam_type is not None else base.exam_type,
        duration_minutes=duration_minutes if duration_minutes is not None else base.duration_minutes,
        total_marks=total_marks if total_marks is not None else base.total_marks,
    )


__all__ = ["build_exam_profile", "merge_profile"]
