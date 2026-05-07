"""
exam_engine.exam_template — Jinja loader scoped to the exam templates.

We deliberately do NOT use ``app/templates`` as the loader root — we
pin to ``app/templates/exams/<name>/`` so the renderer cannot see (or
accidentally include) the shawahid template at
``app/templates/exports/ministry_v1``.

Pure module. No DB / GPT / network / Playwright.
"""
from __future__ import annotations

import os
from functools import lru_cache

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

DEFAULT_TEMPLATE_NAME = "default_v1"
ENTRY_TEMPLATE = "template.html"


def _exam_templates_root() -> str:
    """Absolute path to ``app/templates/exams``."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "templates", "exams"))


def _template_dir(name: str) -> str:
    return os.path.join(_exam_templates_root(), name)


@lru_cache(maxsize=4)
def load_environment(template_name: str = DEFAULT_TEMPLATE_NAME) -> Environment:
    """Return a Jinja ``Environment`` rooted at the requested exam template.

    Cached per template-name so repeated renders share the same Jinja
    cache. Raises ``FileNotFoundError`` when the template directory or
    the entry file ``template.html`` is missing.
    """
    tpl_dir = _template_dir(template_name)
    if not os.path.isdir(tpl_dir):
        raise FileNotFoundError(f"Exam template not found: {template_name} ({tpl_dir})")

    entry_path = os.path.join(tpl_dir, ENTRY_TEMPLATE)
    if not os.path.isfile(entry_path):
        raise FileNotFoundError(f"Missing {ENTRY_TEMPLATE} in {tpl_dir}")

    env = Environment(
        loader=FileSystemLoader(tpl_dir),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


def reset_environment_cache() -> None:
    """Wipe the Jinja env cache — used by tests."""
    load_environment.cache_clear()


__all__ = [
    "DEFAULT_TEMPLATE_NAME",
    "ENTRY_TEMPLATE",
    "load_environment",
    "reset_environment_cache",
]
