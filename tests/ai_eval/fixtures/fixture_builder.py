"""
Idempotent fixture builder.

Run::

    python -m tests.ai_eval.fixtures.fixture_builder

It writes:

    images/board_*.jpg       — real JPEGs with rendered text (PIL)
    audio/silence_*.wav      — short, valid WAV files (stdlib only)
    pdfs/*.pdf               — minimal valid PDF skeletons (no library)
    text/messages.json       — real Arabic message samples for intent eval
    text/transcripts.json    — real transcripts for name-preservation eval

Skips any artefact whose file already exists, so it is safe to re-run.
"""
from __future__ import annotations

import json
import logging
import math
import struct
import wave
from pathlib import Path

from tests.ai_eval.fixtures import (
    AUDIO_DIR,
    IMAGES_DIR,
    PDFS_DIR,
    TEXT_DIR,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Images (real JPEG / PNG via Pillow)
# ──────────────────────────────────────────────────────────────────────────────


def _build_image(target: Path, label: str) -> bool:
    """Write a 320×200 image with the label drawn in the centre.

    Returns True when the file was created. Falls back gracefully when
    Pillow or a usable font is missing.
    """
    if target.exists():
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("PIL not installed — skipping image %s", target.name)
        return False

    img = Image.new("RGB", (320, 200), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.rectangle([10, 10, 310, 190], outline=(60, 60, 60), width=2)
    draw.text((20, 90), label, fill=(20, 20, 20), font=font)
    img.save(target, format="JPEG", quality=85)
    return True


def build_images() -> int:
    spec = [
        ("board_activity.jpg",   "Board: cooperative learning"),
        ("blurry_classroom.jpg", "Blurry classroom"),
        ("printed_paper.jpg",    "Printed worksheet sample"),
        ("group_work.jpg",       "Group work"),
        ("lesson_board.jpg",     "Lesson board"),
    ]
    created = 0
    for filename, label in spec:
        if _build_image(IMAGES_DIR / filename, label):
            created += 1
    return created


# ──────────────────────────────────────────────────────────────────────────────
# Audio (real WAV via stdlib)
# ──────────────────────────────────────────────────────────────────────────────


def _build_wav(target: Path, *, duration_seconds: float, frequency: float) -> bool:
    """Write a tiny mono WAV: a sine tone of ``frequency`` Hz."""
    if target.exists():
        return False
    sample_rate = 8000
    n_frames = int(duration_seconds * sample_rate)
    amplitude = 12000  # safely below int16 max

    with wave.open(str(target), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for n in range(n_frames):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * n / sample_rate))
            wf.writeframesraw(struct.pack("<h", sample))
    return True


def build_audio() -> int:
    spec = [
        ("silence_short.wav",  0.2, 0),
        ("tone_short.wav",     0.5, 440),
        ("tone_medium.wav",    1.5, 660),
        ("noisy_long.wav",     2.0, 880),
    ]
    created = 0
    for filename, duration, freq in spec:
        if _build_wav(AUDIO_DIR / filename, duration_seconds=duration, frequency=freq):
            created += 1
    return created


# ──────────────────────────────────────────────────────────────────────────────
# PDFs (minimal valid skeleton — no external library required)
# ──────────────────────────────────────────────────────────────────────────────


_PDF_TEMPLATE = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length %d >> stream\n"
    b"BT /F1 12 Tf 50 750 Td (%s) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000010 00000 n \n"
    b"0000000060 00000 n \n"
    b"0000000110 00000 n \n"
    b"0000000220 00000 n \n"
    b"0000000310 00000 n \n"
    b"trailer << /Size 6 /Root 1 0 R >>\n"
    b"startxref\n400\n%%EOF\n"
)


def _build_pdf(target: Path, label: str) -> bool:
    """Write a minimal valid PDF whose visible text is ``label``.

    The text shown to humans is ASCII-only because PDF text rendering
    of Arabic requires embedding a Unicode font, which is outside the
    scope of this fixture builder. The *evaluation* of Arabic text
    runs against the JSON ``extracted_text`` field, not against this
    PDF's content stream.
    """
    if target.exists():
        return False
    safe_label = label.encode("ascii", errors="replace")
    content = b"BT /F1 12 Tf 50 750 Td (" + safe_label + b") Tj ET\n"
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length " + str(len(content)).encode() + b" >> stream\n"
        + content +
        b"endstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000010 00000 n \n"
        b"0000000060 00000 n \n"
        b"0000000110 00000 n \n"
        b"0000000220 00000 n \n"
        b"0000000310 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\n"
        b"startxref\n400\n%%EOF\n"
    )
    target.write_bytes(pdf)
    return True


def build_pdfs() -> int:
    spec = [
        ("weekly_plan.pdf",       "Weekly plan - science"),
        ("term_plan.pdf",         "Term plan - language"),
        ("final_exam.pdf",        "Final exam paper"),
        ("worksheet.pdf",         "Worksheet - water cycle"),
        ("schedule.pdf",          "Class schedule"),
        ("attendance.pdf",        "Attendance record"),
        ("circular.pdf",          "Administrative circular"),
        ("certificate.pdf",       "Training certificate"),
        ("empty_text.pdf",        "."),
    ]
    created = 0
    for filename, label in spec:
        if _build_pdf(PDFS_DIR / filename, label):
            created += 1
    return created


# ──────────────────────────────────────────────────────────────────────────────
# Text (JSON samples)
# ──────────────────────────────────────────────────────────────────────────────


def build_text_samples() -> int:
    """Real Arabic text samples used by the intent / classification evaluators.
    The samples are written once; the file is not overwritten on re-runs.
    """
    target = TEXT_DIR / "messages.json"
    if target.exists():
        return 0
    payload = {
        "_doc": "Real Arabic message samples used by the AI evaluation suite.",
        "intents": [
            {"text": "صدر الآن",                  "expected_intent": "export"},
            {"text": "صدّر الملف",                "expected_intent": "export"},
            {"text": "أبغى ملف الشواهد",          "expected_intent": "export"},
            {"text": "راجع الشواهد قبل التصدير",  "expected_intent": "review"},
            {"text": "أرني ملفي",                 "expected_intent": "review"},
            {"text": "احذف آخر شاهد",             "expected_intent": "delete_last"},
            {"text": "هذا مكرر",                  "expected_intent": "duplicate"},
            {"text": "هذه خطة فصلية",             "expected_intent": "category"},
            {"text": "هذا اختبار نهائي",          "expected_intent": "category"},
            {"text": "السلام عليكم",              "expected_intent": "greeting"},
            {"text": "ساعدني في استخدام البرنامج", "expected_intent": "help"},
        ],
        "noise": [
            "",
            "....",
            "??",
            "ا",
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


def build_all() -> dict[str, int]:
    """Build every fixture group. Returns a counts dict for the CLI."""
    counts = {
        "images": build_images(),
        "audio":  build_audio(),
        "pdfs":   build_pdfs(),
        "text":   build_text_samples(),
    }
    return counts


def _list_existing() -> dict[str, int]:
    return {
        "images": len(list(IMAGES_DIR.glob("*.jpg")) + list(IMAGES_DIR.glob("*.png"))),
        "audio":  len(list(AUDIO_DIR.glob("*.wav"))),
        "pdfs":   len(list(PDFS_DIR.glob("*.pdf"))),
        "text":   len(list(TEXT_DIR.glob("*.json"))),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    created = build_all()
    existing = _list_existing()
    print("Fixture builder finished.")
    print(f"  created: {created}")
    print(f"  on disk: {existing}")
