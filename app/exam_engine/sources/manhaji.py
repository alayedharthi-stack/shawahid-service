"""
exam_engine.sources.manhaji — منهجي provider.

Same phase-11 contract as ``madati.py`` — fail-soft fetch through the
pluggable HttpClient, anti-copy mandatory before downstream exposure.
"""
from __future__ import annotations

from app.exam_engine.sources._external_base import ExternalProviderBase


class ManhajiProvider(ExternalProviderBase):
    name = "manhaji"
    source_url = "https://example.invalid/manhaji"

    url_templates = (
        "https://example.invalid/manhaji/{subject}/{grade}/{semester}",
        "https://example.invalid/manhaji/exam/{subject}/{exam_type}",
    )

    supports_subjects = ()
    supports_stages = ()
    supports_exam_types = ()


__all__ = ["ManhajiProvider"]
