"""
Unified preview dispatcher.

Given a ``MediaAsset`` (or its raw fields) returns the inline preview
URL the renderer should embed. Internally delegates to the
type-specific pipelines so the public surface stays tiny.

Used by the exporter to fill ``ExportItem.media.preview_url`` without
caring whether the asset is an image, a video thumbnail, or a PDF
page raster.
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


def build_preview(asset: MediaAsset) -> str | None:
    """Return an inline ``data:`` preview URL, or ``None`` on failure.

    The function never raises — every failure mode resolves to a
    ``None`` so the caller can decide whether to emit a fallback card.
    """
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
    except Exception as exc:  # never let a render bug cascade
        logger.warning(
            "[MEDIA] preview build failed type=%s path=%s err=%s",
            mt, asset.file_path, exc,
        )
    return None
