"""
Renderer
────────

Turns an :class:`ExportPayload` into HTML using a theme stored under
``app/templates/exports/<theme>/``.

Hard rules:
    • No DB access, no ORM, no service-layer imports beyond schemas.
    • No classification logic.
    • No HTTP calls. Everything must be inlined into the HTML so
      Playwright can render the PDF without a network.

The Phase-1 ministry_v1 theme intentionally delegates its body to the
existing ``portfolio.html`` template via Jinja ``{% include %}``.
That keeps the visual output bit-identical while still exercising
the new rendering boundary. Phase 2 will inline the markup into
``ministry_v1/template.html`` and split CSS into ``theme.css`` /
``print.css`` and components.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.export_engine.schemas import ExportPayload

_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
    autoescape=select_autoescape(default_for_string=True, default=True),
)


def render_template(theme: str, payload: ExportPayload) -> str:
    """Render the export HTML for ``theme`` using ``payload``.

    The template path is ``exports/<theme>/template.html`` relative to
    ``app/templates``. ``theme`` is opaque to the engine — only the
    folder layout matters.
    """
    template = _jinja_env.get_template(f"exports/{theme}/template.html")
    context = _build_context(payload)
    return template.render(**context)


def _build_context(payload: ExportPayload) -> dict[str, Any]:
    """Merge structured payload + Phase-1 legacy variables.

    The legacy template still reads top-level names like ``teacher``,
    ``categories``, ``leading_categories``, ``stats`` etc. We expose
    them here from ``payload.legacy_context``.

    New themes (Phase 2+) should read everything they need from the
    typed ``payload`` object instead.
    """
    legacy = dict(payload.legacy_context or {})
    legacy.update(
        {
            # New, structured surface (always available).
            "payload": payload,
            "teacher_dto": payload.teacher,
            "school": payload.school,
            "summary": payload.summary,
            "cover": payload.cover,
            "sections": payload.sections,
            "export_mode": payload.export_mode,
            "generated_at": payload.generated_at,
            # Legacy variables the current template expects. We only
            # set them if the legacy_context didn't already provide
            # them — never overwrite the bridged values.
            "teacher": legacy.get("teacher")
            or _legacy_teacher_proxy(payload.teacher, payload.school),
            "total_count": legacy.get("total_count", payload.summary.total_count),
            "include_intro_page": legacy.get("include_intro_page", False),
        }
    )
    return legacy


class _LegacyTeacherProxy:
    """Read-only attribute proxy used only when the legacy template is
    rendered without a real ORM teacher (e.g. in unit tests)."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        return self._data.get(name)


def _legacy_teacher_proxy(
    teacher_dto, school_dto
) -> _LegacyTeacherProxy:  # noqa: ANN001
    """Build a duck-typed object the legacy template can read.

    The current ``portfolio.html`` reaches for attributes like
    ``teacher.name``, ``teacher.subject``, ``teacher.school_name``,
    ``teacher.stage``, ``teacher.grades``, ``teacher.principal_name``.
    """
    return _LegacyTeacherProxy(
        {
            "id": teacher_dto.id,
            "name": teacher_dto.name,
            "subject": teacher_dto.subject,
            "stage": teacher_dto.stage,
            "grades": teacher_dto.grades,
            "school_name": school_dto.name,
            "principal_name": school_dto.principal_name,
        }
    )
