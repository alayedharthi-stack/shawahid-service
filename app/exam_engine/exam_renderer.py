"""
exam_engine.exam_renderer — render a ``GeneratedExam`` to HTML.

Pure module. No DB / GPT / network / Playwright. The renderer ONLY
uses templates under ``app/templates/exams/`` — it cannot reach into
``app/templates/exports`` (the shawahid template).
"""
from __future__ import annotations

from app.exam_engine.exam_template import (
    DEFAULT_TEMPLATE_NAME,
    ENTRY_TEMPLATE,
    load_environment,
)
from app.exam_engine.schemas import GeneratedExam


def render_exam_html(
    exam: GeneratedExam,
    *,
    template_name: str = DEFAULT_TEMPLATE_NAME,
) -> str:
    """Render ``exam`` into a self-contained HTML document.

    The template is sandboxed to ``app/templates/exams/<template_name>``
    so it cannot accidentally include the shawahid template.
    """
    env = load_environment(template_name)
    tpl = env.get_template(ENTRY_TEMPLATE)
    return tpl.render(
        profile=exam.profile,
        questions=list(exam.questions),
        exam_id=exam.exam_id,
        generated_at=exam.generated_at.strftime("%Y-%m-%d %H:%M"),
    )


__all__ = ["render_exam_html"]
