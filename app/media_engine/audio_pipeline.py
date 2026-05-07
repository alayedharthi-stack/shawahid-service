"""
Audio pipeline.

Phase 4 keeps audio handling intentionally light — duration probing
and waveform rendering are out of scope (see plan section 7). This
module exists so all audio path resolution flows through one place,
ready to grow in a future phase.

Hard rules:
    • No ORM / DB.
    • No ffmpeg / external subprocess work yet.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTS: tuple[str, ...] = (
    ".ogg", ".mp3", ".m4a", ".wav", ".opus", ".aac", ".flac",
)

# Map evidence_type → reasonable default MIME when nothing else is known.
_DEFAULT_AUDIO_MIME: dict[str, str] = {
    "audio": "audio/mpeg",
    "voice": "audio/ogg",
}


def is_audio_path(path: str | Path | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in AUDIO_EXTS


def resolve_audio_file(
    storage_path: str | None,
    file_name: str | None = None,
) -> str | None:
    """Return the absolute path of an audio file on disk.

    Phase 4 mirrors the historic resolution rules used in
    ``app.api.media`` so behaviour is preserved bit-for-bit:

        1. If ``storage_path`` exists, return it.
        2. Else look for ``<storage_path.parent>/<file_name>``.
        3. Else give up — caller emits the audio fallback card.
    """
    if not storage_path:
        return None
    sp = Path(storage_path)
    if sp.exists():
        return str(sp)
    if file_name:
        candidate = sp.parent / file_name
        if candidate.exists():
            return str(candidate)
    return None


def guess_audio_mime(
    path: str | Path | None,
    stored_mime: str | None,
    evidence_type: str | None = None,
) -> str:
    """Best-effort MIME for audio assets.

    Order of precedence:
        1. ``stored_mime`` (e.g. captured from the WhatsApp payload).
        2. ``mimetypes.guess_type`` based on the path extension.
        3. ``_DEFAULT_AUDIO_MIME[evidence_type]``.
        4. ``audio/mpeg``.
    """
    if stored_mime:
        return stored_mime
    if path:
        guessed = mimetypes.guess_type(Path(path).name)[0]
        if guessed:
            return guessed
    return _DEFAULT_AUDIO_MIME.get(
        (evidence_type or "").lower(), "audio/mpeg"
    )
