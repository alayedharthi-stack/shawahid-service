"""
Media URL resolution for the export engine.

In Phase 1 this module is a thin adapter: it reads URLs the legacy
exporter has already produced (``media_src``, ``pdf_preview_src``,
``media_viewer_url``, ``download_url``, ``file_public_url``) and
packs them into an :class:`ExportMedia` object.

In Phase 2 the actual resolution (PDF preview generation, thumbnail
extraction, fallback URL choice) will move here from
``app.services.storage`` and ``app.services.exporter``.
"""
from __future__ import annotations

from typing import Any

from app.export_engine.schemas import ExportMedia


def resolve_media_for_evidence(ev: dict[str, Any]) -> ExportMedia:
    """Build an ``ExportMedia`` from a normalised evidence dict.

    ``ev`` is the output of the legacy
    ``exporter._normalize_evidence_for_export`` helper. We do not
    re-derive any of the URLs here in Phase 1 — we only re-pack them.
    """
    evidence_type = (ev.get("evidence_type") or "").lower()

    # Pre-resolved data URIs / public URLs from the legacy pipeline.
    media_src = ev.get("media_src")
    pdf_preview_src = ev.get("pdf_preview_src")
    media_viewer_url = ev.get("media_viewer_url")
    public_media_url = ev.get("public_media_url")
    file_public_url = ev.get("file_public_url")
    download_url = ev.get("download_url") or public_media_url
    link_href = ev.get("link_href")

    # Per-type packing.
    if evidence_type == "image":
        return ExportMedia(
            file_url=download_url,
            preview_url=media_src,
            thumbnail_url=media_src,
            player_url=media_viewer_url,
            fallback_url=file_public_url,
        )

    if evidence_type == "video":
        return ExportMedia(
            file_url=download_url,
            preview_url=media_src,
            thumbnail_url=media_src,
            player_url=media_viewer_url,
            fallback_url=file_public_url,
        )

    if evidence_type in ("audio", "voice"):
        return ExportMedia(
            file_url=download_url,
            preview_url=None,
            thumbnail_url=None,
            player_url=media_viewer_url,
            fallback_url=file_public_url,
        )

    if evidence_type in ("pdf", "document"):
        return ExportMedia(
            file_url=download_url,
            preview_url=pdf_preview_src,
            thumbnail_url=pdf_preview_src,
            player_url=media_viewer_url,
            fallback_url=file_public_url,
        )

    if evidence_type == "url":
        return ExportMedia(
            file_url=link_href or download_url,
            preview_url=None,
            thumbnail_url=None,
            player_url=link_href or media_viewer_url,
            fallback_url=link_href,
        )

    # Text / unknown — no playable media but keep viewer URL so the
    # WhatsApp /media/{id} page still works.
    return ExportMedia(
        file_url=download_url,
        preview_url=None,
        thumbnail_url=None,
        player_url=media_viewer_url,
        fallback_url=file_public_url,
    )
