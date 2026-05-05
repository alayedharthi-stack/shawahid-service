"""
Audio / Video transcription for Shawahid Service.

Uses OpenAI Whisper API for speech-to-text.
Uses ffmpeg for audio extraction from video and format conversion.

Supported via Whisper natively:
  .flac  .mp3  .mp4  .mpeg  .mpga  .m4a  .ogg  .wav  .webm

WhatsApp voice notes arrive as audio/ogg (Opus) — natively supported.
Videos: audio track extracted to .mp3 first (smaller + always compatible).

Logs:
  [AUDIO RECEIVED]       — audio message detected
  [VIDEO RECEIVED]       — video message detected
  [TRANSCRIBE REQUEST]   — Whisper API call started
  [TRANSCRIBE SUCCESS]   — transcript received (with char count)
  [TRANSCRIBE ERROR]     — failure reason
  [VIDEO AUDIO EXTRACT]  — ffmpeg extracting audio from video
  [VIDEO THUMBNAIL]      — ffmpeg extracting frame from video
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-1"
MAX_WHISPER_MB = 24.0          # Whisper hard limit is 25 MB; stay just under

# Formats Whisper API accepts without conversion
_WHISPER_NATIVE = frozenset({
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a",
    ".ogg", ".wav", ".webm",
})


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def _run_ffmpeg(args: list[str], timeout: int = 90) -> bool:
    """
    Execute ffmpeg with the given args + implicit -y (overwrite).
    Returns True on success, False on any failure.
    Never raises.
    """
    cmd = ["ffmpeg", "-loglevel", "error"] + args + ["-y"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0:
            return True
        logger.error("[FFMPEG] exit=%d stderr=%s", result.returncode, result.stderr.decode()[:400])
        return False
    except FileNotFoundError:
        logger.error("[FFMPEG] ffmpeg not found in PATH — install it (apt-get install ffmpeg)")
        return False
    except subprocess.TimeoutExpired:
        logger.error("[FFMPEG] timed out after %ds", timeout)
        return False
    except Exception as exc:
        logger.error("[FFMPEG] unexpected error: %s", exc)
        return False


def extract_audio_from_video(video_path: Path) -> Path | None:
    """
    Extract the audio track from a video file and write it as .mp3
    (mono, 16 kHz — optimal for Whisper).

    Returns the path to the extracted audio file, or None on failure.
    """
    out = video_path.with_suffix(".audio.mp3")
    logger.info("[VIDEO AUDIO EXTRACT] %s → %s", video_path.name, out.name)

    ok = _run_ffmpeg([
        "-i", str(video_path),
        "-vn",              # no video
        "-ar", "16000",     # 16 kHz sample rate
        "-ac", "1",         # mono
        "-q:a", "4",        # decent quality (0=best, 9=worst)
        str(out),
    ])
    if ok and out.exists() and out.stat().st_size > 100:
        logger.info("[VIDEO AUDIO EXTRACT] success: %.2f MB", out.stat().st_size / 1e6)
        return out

    logger.warning("[VIDEO AUDIO EXTRACT] failed for %s", video_path.name)
    return None


def extract_video_thumbnail(video_path: Path, at_seconds: float = 3.0) -> Path | None:
    """
    Extract a single JPEG frame from the video at `at_seconds`.
    Falls back to the first frame if the video is shorter.

    Returns path to the .jpg thumbnail, or None on failure.
    """
    out = video_path.with_suffix(".thumb.jpg")
    logger.info("[VIDEO THUMBNAIL] extracting frame at %.1fs from %s", at_seconds, video_path.name)

    ok = _run_ffmpeg([
        "-i", str(video_path),
        "-ss", str(at_seconds),
        "-vframes", "1",
        "-q:v", "3",    # JPEG quality (lower = better, 2-5 is fine)
        str(out),
    ], timeout=30)
    if ok and out.exists() and out.stat().st_size > 500:
        return out

    # Fallback: very first frame (for short videos)
    ok2 = _run_ffmpeg([
        "-i", str(video_path),
        "-ss", "0",
        "-vframes", "1",
        "-q:v", "3",
        str(out),
    ], timeout=30)
    if ok2 and out.exists() and out.stat().st_size > 500:
        return out

    logger.warning("[VIDEO THUMBNAIL] failed for %s", video_path.name)
    return None


def _ensure_whisper_compatible(audio_path: Path) -> Path:
    """
    If the file extension is natively supported by Whisper, return it unchanged.
    Otherwise convert to mp3 with ffmpeg and return the converted path.
    Falls back to original path if conversion fails (Whisper may still accept it).
    """
    if audio_path.suffix.lower() in _WHISPER_NATIVE:
        return audio_path

    out = audio_path.with_suffix(".converted.mp3")
    logger.info("[TRANSCRIBE CONVERT] %s → mp3 (not natively supported)", audio_path.suffix)
    ok = _run_ffmpeg([
        "-i", str(audio_path),
        "-ar", "16000",
        "-ac", "1",
        str(out),
    ])
    if ok and out.exists():
        return out

    logger.warning("[TRANSCRIBE CONVERT] conversion failed — trying original format")
    return audio_path


# ── Whisper transcription ─────────────────────────────────────────────────────

async def transcribe_audio(audio_path: Path) -> str | None:
    """
    Transcribe an audio file using OpenAI Whisper API.

    Steps:
      1. Convert to Whisper-compatible format if needed.
      2. Check file size (reject if > 24 MB).
      3. POST to Whisper API with language="ar" hint (improves Arabic accuracy).
      4. Return the transcript text, or None on any failure.

    Never raises — caller receives None on any error.

    Logs:
      [TRANSCRIBE REQUEST]   — API call started
      [TRANSCRIBE SUCCESS]   — with char count
      [TRANSCRIBE ERROR]     — reason for failure
    """
    from app.core.config import settings

    if not settings.OPENAI_API_KEY:
        logger.error("[TRANSCRIBE ERROR] OPENAI_API_KEY not configured")
        return None

    if not audio_path.exists():
        logger.error("[TRANSCRIBE ERROR] file not found: %s", audio_path)
        return None

    # Ensure format is compatible
    work_path = _ensure_whisper_compatible(audio_path)

    # Size guard
    size_mb = work_path.stat().st_size / 1_048_576
    if size_mb > MAX_WHISPER_MB:
        logger.warning(
            "[TRANSCRIBE ERROR] file too large: %.1f MB > %.0f MB — skipping",
            size_mb, MAX_WHISPER_MB,
        )
        return None

    logger.info(
        "[TRANSCRIBE REQUEST] model=%s file=%s size=%.2f MB",
        WHISPER_MODEL, work_path.name, size_mb,
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=120)

        with open(work_path, "rb") as f:
            result = await client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                language="ar",   # Arabic hint — improves accuracy
            )

        transcript = (result.text or "").strip()

        if not transcript:
            logger.warning("[TRANSCRIBE ERROR] Whisper returned empty transcript")
            return None

        logger.info(
            "[TRANSCRIBE SUCCESS] chars=%d preview=%r",
            len(transcript), transcript[:100],
        )
        return transcript

    except Exception as exc:
        logger.error("[TRANSCRIBE ERROR] %s: %s", work_path.name, exc)
        return None
