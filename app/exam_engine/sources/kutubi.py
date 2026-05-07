"""
exam_engine.sources.kutubi — كتبي provider.

See ``madati.py`` for the full phase-11 contract — this file just
swaps the URL templates and provider name. Same fail-soft posture,
same anti-copy rules.
"""
from __future__ import annotations

from app.exam_engine.sources._external_base import ExternalProviderBase


class KutubiProvider(ExternalProviderBase):
    name = "kutubi"
    source_url = "https://example.invalid/kutubi"

    url_templates = (
        "https://example.invalid/kutubi/{stage}/{subject}/{semester}",
        "https://example.invalid/kutubi/exams/{subject}/{exam_type}",
    )

    supports_subjects = ()
    supports_stages = ()
    supports_exam_types = ()


__all__ = ["KutubiProvider"]
