"""
curriculum_engine.saudi_curriculum — Saudi MOE curriculum metadata.

A *foundation* helper for downstream modules. Contains lightweight
look-ups (subject names, stages, common heading keywords) that the
document-intent and outcomes detectors share.

Pure module. No DB / network.
"""
from __future__ import annotations

from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────────
# Stages and subjects
# ──────────────────────────────────────────────────────────────────────

STAGE_PRIMARY = "primary"      # ابتدائي
STAGE_INTERMEDIATE = "intermediate"  # متوسط
STAGE_SECONDARY = "secondary"  # ثانوي
STAGE_KG = "kg"                # رياض أطفال

STAGE_LABELS: dict[str, str] = {
    STAGE_PRIMARY: "المرحلة الابتدائية",
    STAGE_INTERMEDIATE: "المرحلة المتوسطة",
    STAGE_SECONDARY: "المرحلة الثانوية",
    STAGE_KG: "رياض الأطفال",
}


@dataclass(frozen=True)
class Subject:
    code: str
    arabic: str


SUBJECTS: tuple[Subject, ...] = (
    Subject("math", "الرياضيات"),
    Subject("science", "العلوم"),
    Subject("arabic", "اللغة العربية"),
    Subject("english", "اللغة الإنجليزية"),
    Subject("social", "الاجتماعيات"),
    Subject("religion", "التربية الإسلامية"),
    Subject("art", "التربية الفنية"),
    Subject("physical", "التربية البدنية"),
    Subject("computer", "الحاسب"),
    Subject("history", "التاريخ"),
    Subject("geography", "الجغرافيا"),
    Subject("physics", "الفيزياء"),
    Subject("chemistry", "الكيمياء"),
    Subject("biology", "الأحياء"),
    Subject("quran", "القرآن الكريم"),
)


def stage_from_label(label: str | None) -> str | None:
    """Map a teacher-supplied stage label to a canonical code."""
    if not label:
        return None
    text = label.strip()
    for code, ar in STAGE_LABELS.items():
        if ar in text or ar.replace("ال", "") in text:
            return code
    return None


def subject_arabic(code: str) -> str | None:
    for subj in SUBJECTS:
        if subj.code == code:
            return subj.arabic
    return None


# ──────────────────────────────────────────────────────────────────────
# Heading keywords commonly found in MOE-style planning documents.
# Used by document_intent.py (kept here so multiple detectors can share).
# All entries are pre-normalised (no diacritics, hamza/yaa/taa unified).
# ──────────────────────────────────────────────────────────────────────

PLANNING_HEADINGS: tuple[str, ...] = (
    "نواتج التعلم", "نواتج تعلم", "نتائج التعلم",
    "اهداف الدرس", "الاهداف العامه", "الاهداف",
    "التهيئه", "التهيئة الحافزه",
    "العرض", "الاجراءات",
    "التقويم", "تقويم الدرس", "التقويم الختامي",
    "الواجب", "الواجب المنزلي",
    "استراتيجيات التدريس", "استراتيجيه التدريس",
    "وسائل التعلم", "الوسائل التعليميه", "المصادر",
    "الاسبوع الاول", "الاسبوع الثاني", "الاسبوع الثالث",
    "توزيع المنهج", "التوزيع الزمني",
    "خطه فصليه", "خطه اسبوعيه", "خطه يوميه",
    "الفصل الدراسي", "الوحده", "الموضوع",
)

ASSESSMENT_HEADINGS: tuple[str, ...] = (
    "اسئله الاختبار", "اختبار نهائي", "اختبار قصير",
    "ورقه عمل", "ورقه نشاط",
    "كشف الدرجات", "رصد الدرجات", "توزيع الدرجات",
    "مهمه ادائيه", "تقويم بنائي",
)

FOLLOWUP_HEADINGS: tuple[str, ...] = (
    "سجل المتابعه", "كشف الحضور", "كشف الغياب",
    "متابعه يوميه", "متابعه الطلاب", "حضور وغياب",
)

TIMETABLE_HEADINGS: tuple[str, ...] = (
    "جدول الحصص", "جدول مدرسي",
)

ADMIN_HEADINGS: tuple[str, ...] = (
    "تعميم", "تعميم رقم", "خطاب رسمي", "قرار رقم",
    "نحيطكم علما", "المديريه العامه",
)

CERTIFICATE_HEADINGS: tuple[str, ...] = (
    "شهاده اتمام", "شهاده تقدير", "اجتاز بنجاح",
    "يشهد بان", "certificate",
)


__all__ = [
    "STAGE_PRIMARY",
    "STAGE_INTERMEDIATE",
    "STAGE_SECONDARY",
    "STAGE_KG",
    "STAGE_LABELS",
    "Subject",
    "SUBJECTS",
    "stage_from_label",
    "subject_arabic",
    "PLANNING_HEADINGS",
    "ASSESSMENT_HEADINGS",
    "FOLLOWUP_HEADINGS",
    "TIMETABLE_HEADINGS",
    "ADMIN_HEADINGS",
    "CERTIFICATE_HEADINGS",
]
