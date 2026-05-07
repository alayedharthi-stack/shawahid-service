"""
Unified thumbnail dispatcher.

Compact-grid layouts in the exporter need a *small* representation of
each asset. For images this is the same data URI as the preview; for
videos it is the pre-rendered ``.thumb.jpg``; for PDFs we currently
reuse the first-page raster (Phase 5 may downsample further).
"""
from __future__ import annotations

import logging

from app.media_engine.image_pipeline import image_to_data_uri
from app.media_engine.pdf_pipeline import first_page_data_uri
from app.media_engine.schemas import (
    MEDIA_DOCUMENT,
    MEDIA_IMAGE,
    MEDIA_PDF,
    MEDIA_VIDEO,
    MediaAsset,
)
from app.media_engine.video_pipeline import thumbnail_to_data_uri

logger = logging.getLogger(__name__)


def build_thumbnail(asset: MediaAsset) -> str | None:
    """Return a small preview URL suitable for compact cards."""
    if not asset:
        return None
    mt = (asset.media_type or "").lower()

    try:
        if mt == MEDIA_IMAGE:
            return image_to_data_uri(asset.file_path)
        if mt == MEDIA_VIDEO:
            return thumbnail_to_data_uri(asset.file_path)
        if mt in (MEDIA_PDF, MEDIA_DOCUMENT):
            return first_page_data_uri(asset.file_path)
    except Exception as exc:
        logger.warning(
            "[MEDIA] thumbnail build failed type=%s path=%s err=%s",
            mt, asset.file_path, exc,
        )
    return None
