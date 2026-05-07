"""
exam_engine.sources.madati — مادتي provider.

Phase-11 status: real fetch path through the pluggable HttpClient.
Defaults to ``DisabledHttpClient`` so no network IO happens until an
operator opts in.

Hard rules (carried over from phase-10):
    • respect robots.txt + the site's terms of service
    • cache results via ``source_cache.SourceCache``
    • route through ``quality_check`` before exposing
    • NEVER copy a full sample sheet verbatim — anti-copy must run

The URL templates use placeholders the operator can override per
deploy by editing this file or subclassing the provider.
"""
from __future__ import annotations

from app.exam_engine.sources._external_base import ExternalProviderBase


class MadatiProvider(ExternalProviderBase):
    name = "madati"
    source_url = "https://example.invalid/madati"

    # Reasonable defaults — populated for testing via InMemoryHttpClient.
    url_templates = (
        "https://example.invalid/madati/exams"
        "?subject={subject}&grade={grade}&semester={semester}",
        "https://example.invalid/madati/exam-bank"
        "?subject={subject}&exam_type={exam_type}",
    )

    # Subjects this provider claims to cover. Empty == "any".
    supports_subjects = ()
    supports_stages = ()
    supports_exam_types = ()


__all__ = ["MadatiProvider"]
