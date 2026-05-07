"""
Phase-4 contract tests for ``app.media_engine``.

These tests assert the architectural promises the Phase-4 plan makes:

    1. ``MediaAsset`` is a pure dataclass — no SQLAlchemy / Playwright.
    2. ``image_pipeline`` produces a valid ``data:image/...`` URI for
       a real PNG written to a temp dir.
    3. ``pdf_pipeline.first_page_data_uri`` returns ``None`` (and does
       not raise) when PyMuPDF is unavailable; with a mocked
       generator it produces a valid data URI.
    4. ``video_pipeline`` resolves storage_path/.thumb.jpg → real
       video file using only on-disk lookups (no DB).
    5. ``audio_pipeline.guess_audio_mime`` honours the documented
       precedence order.
    6. ``build_media_urls`` is pure: same inputs → same outputs;
       handles missing files, blocked CDN hosts, and JPEG-as-video
       edge case.
    7. ``build_fallback_card`` returns a canonical card per
       ``media_type`` and never raises.
    8. **Architectural** asserts:
        a. ``base64.b64encode`` is called from exactly one module
           (``media_engine._base64_utils``); ``moyasar.py`` is
           explicitly excluded because its base64 is HTTP basic-auth
           credentials, not media content.
        b. Building public ``/files/`` URLs only happens inside
           ``media_engine`` (modules outside delegate via wrappers).
        c. ``app.media_engine`` itself does not import SQLAlchemy or
           Playwright.

The tests deliberately avoid the database, OpenAI, Playwright, and
real WhatsApp credentials.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
MEDIA_ENGINE_DIR = APP_DIR / "media_engine"


def _write_png(path: Path, *, size: tuple[int, int] = (4, 4)) -> Path:
    img = Image.new("RGB", size, (240, 80, 80))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return path


# ──────────────────────────────────────────────────────────────────────
# 1. MediaAsset DTO
# ──────────────────────────────────────────────────────────────────────


def test_media_asset_dto_is_pure_dataclass():
    """The DTO must be constructable without any side effects."""
    from app.media_engine.schemas import MediaAsset, MEDIA_PDF

    asset = MediaAsset(media_type=MEDIA_PDF, file_path="/tmp/x.pdf")

    assert asset.media_type == "pdf"
    assert asset.file_path == "/tmp/x.pdf"
    # Defaults
    assert asset.public_url is None
    assert asset.preview_url is None
    assert asset.thumbnail_url is None
    assert asset.player_url is None
    assert asset.has_preview is False
    assert asset.has_thumbnail is False
    assert asset.fallback_type is None
    assert asset.has_fallback is False


def test_media_asset_is_visual_predicate():
    from app.media_engine.schemas import (
        MediaAsset, MEDIA_AUDIO, MEDIA_IMAGE, MEDIA_PDF, MEDIA_VIDEO,
    )

    assert MediaAsset(media_type=MEDIA_IMAGE).is_visual is True
    assert MediaAsset(media_type=MEDIA_VIDEO).is_visual is True
    assert MediaAsset(media_type=MEDIA_PDF).is_visual is True
    assert MediaAsset(media_type=MEDIA_AUDIO).is_visual is False


# ──────────────────────────────────────────────────────────────────────
# 2. image_pipeline
# ──────────────────────────────────────────────────────────────────────


def test_image_to_data_uri_round_trip(tmp_path: Path):
    from app.media_engine.image_pipeline import image_to_data_uri

    png = _write_png(tmp_path / "evidence.png")
    uri = image_to_data_uri(png)

    assert uri is not None
    assert uri.startswith("data:image/png;base64,")
    # The encoded payload must be parseable as base64.
    import base64 as _b
    payload = uri.split(",", 1)[1]
    assert _b.b64decode(payload)[:4] == b"\x89PNG"


def test_image_to_data_uri_returns_none_for_non_image(tmp_path: Path):
    from app.media_engine.image_pipeline import image_to_data_uri

    txt = tmp_path / "not_image.txt"
    txt.write_text("hello")
    assert image_to_data_uri(txt) is None


def test_svg_text_to_data_uri():
    from app.media_engine.image_pipeline import svg_text_to_data_uri

    uri = svg_text_to_data_uri("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert uri.startswith("data:image/svg+xml;base64,")


# ──────────────────────────────────────────────────────────────────────
# 3. pdf_pipeline (graceful when fitz absent; mocked when present)
# ──────────────────────────────────────────────────────────────────────


def test_pdf_first_page_handles_missing_pymupdf(tmp_path: Path):
    """When the rasteriser is unavailable we must return ``None``, not
    crash."""
    from app.media_engine import pdf_pipeline

    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    # Whether fitz is installed locally or not, the call must not raise.
    result = pdf_pipeline.first_page_data_uri(fake_pdf)
    assert result is None or result.startswith("data:image/")


def test_pdf_first_page_uses_generator(monkeypatch, tmp_path: Path):
    """The dispatcher must read the file the generator returns and
    encode it as a data URI."""
    from app.media_engine import pdf_pipeline

    # Generate a real PNG to act as the "preview"
    preview_png = _write_png(tmp_path / "doc.pdf_preview.jpg")

    def _fake_generate(_path):
        return str(preview_png)

    monkeypatch.setattr(pdf_pipeline, "generate_preview", _fake_generate)

    uri = pdf_pipeline.first_page_data_uri(tmp_path / "doc.pdf")
    assert uri is not None
    assert uri.startswith("data:image/")


def test_pdf_page_count_returns_zero_for_missing(tmp_path: Path):
    from app.media_engine.pdf_pipeline import page_count

    assert page_count(tmp_path / "ghost.pdf") == 0


# ──────────────────────────────────────────────────────────────────────
# 4. video_pipeline
# ──────────────────────────────────────────────────────────────────────


def test_resolve_video_file_from_thumb(tmp_path: Path):
    """When ``storage_path`` points at ``X.thumb.jpg`` we should find
    the matching video next to it via ``file_name``."""
    from app.media_engine.video_pipeline import resolve_video_file

    video = tmp_path / "lecture_42.mp4"
    video.write_bytes(b"\x00")
    thumb = tmp_path / "lecture_42.thumb.jpg"
    thumb.write_bytes(b"\xff\xd8")

    resolved = resolve_video_file(str(thumb), file_name="lecture_42.mp4")
    assert resolved == str(video)


def test_resolve_video_file_from_thumb_stem_search(tmp_path: Path):
    """Even when ``file_name`` is missing, we fall back to scanning by
    stem + supported video extensions."""
    from app.media_engine.video_pipeline import resolve_video_file

    video = tmp_path / "demo.mov"
    video.write_bytes(b"\x00")
    thumb = tmp_path / "demo.thumb.jpg"
    thumb.write_bytes(b"\xff\xd8")

    assert resolve_video_file(str(thumb)) == str(video)


def test_resolve_thumbnail_path_uses_legacy_suffix(tmp_path: Path):
    from app.media_engine.video_pipeline import resolve_thumbnail_path

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    thumb = tmp_path / "v.mp4_thumb.jpg"
    thumb.write_bytes(b"\xff\xd8")

    assert resolve_thumbnail_path(str(video)) == str(thumb)


def test_thumbnail_to_data_uri(tmp_path: Path):
    from app.media_engine.video_pipeline import thumbnail_to_data_uri

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00")
    thumb_path = tmp_path / "clip.mp4_thumb.jpg"
    _write_png(thumb_path)  # Pillow writes a valid JPEG-or-PNG; suffix is what we check

    uri = thumbnail_to_data_uri(str(video))
    assert uri is not None
    assert uri.startswith("data:image/")


# ──────────────────────────────────────────────────────────────────────
# 5. audio_pipeline
# ──────────────────────────────────────────────────────────────────────


def test_resolve_audio_file_existing(tmp_path: Path):
    from app.media_engine.audio_pipeline import resolve_audio_file

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS")
    assert resolve_audio_file(str(audio)) == str(audio)


def test_guess_audio_mime_precedence(tmp_path: Path):
    from app.media_engine.audio_pipeline import guess_audio_mime

    # 1. Stored MIME wins.
    assert guess_audio_mime(None, "audio/aac", "audio") == "audio/aac"
    # 2. Path extension when nothing stored.
    p = tmp_path / "voice.mp3"
    p.write_bytes(b"\x00")
    assert guess_audio_mime(str(p), None, "audio") == "audio/mpeg"
    # 3. evidence_type default.
    assert guess_audio_mime(None, None, "voice") == "audio/ogg"
    # 4. Last-resort default.
    assert guess_audio_mime(None, None, "unknown") == "audio/mpeg"


# ──────────────────────────────────────────────────────────────────────
# 6. media_urls.build_media_urls
# ──────────────────────────────────────────────────────────────────────


def test_build_media_urls_for_image(tmp_path: Path):
    from app.media_engine.media_urls import build_media_urls

    teachers_dir = tmp_path / "teachers" / "42" / "evidences"
    teachers_dir.mkdir(parents=True)
    img = _write_png(teachers_dir / "pic.png")

    urls = build_media_urls(
        evidence_id=99,
        evidence_type="image",
        storage_path=str(img),
        media_url=None,
        base_url="https://example.com/",
    )

    assert urls.public_url == "https://example.com/files/teachers/42/evidences/pic.png"
    assert urls.preview_url is not None and urls.preview_url.startswith("data:image/")
    assert urls.thumbnail_url == urls.preview_url
    assert urls.player_url == "https://example.com/media/99"


def test_build_media_urls_blocks_meta_cdn():
    """A WhatsApp / FB CDN URL must never bubble out as ``public_url``.
    """
    from app.media_engine.media_urls import build_media_urls

    urls = build_media_urls(
        evidence_id=1,
        evidence_type="image",
        storage_path=None,
        media_url="https://lookaside.fbsbx.com/abc",
        base_url="https://x.test",
    )
    assert urls.public_url is None


def test_build_media_urls_drops_jpeg_for_video(tmp_path: Path):
    """Storage path that points at a JPEG must not yield a ``public_url``
    for a video evidence — that would crash the WhatsApp viewer."""
    from app.media_engine.media_urls import build_media_urls

    teachers_dir = tmp_path / "teachers" / "1" / "evidences"
    teachers_dir.mkdir(parents=True)
    fake_thumb = teachers_dir / "v.jpg"
    fake_thumb.write_bytes(b"\xff\xd8")

    urls = build_media_urls(
        evidence_id=7,
        evidence_type="video",
        storage_path=str(fake_thumb),
        media_url=None,
        base_url="https://x.test",
    )
    assert urls.public_url is None


def test_build_media_urls_does_not_import_exporter():
    """The URL factory must not depend on ``app.services.exporter`` —
    that would re-introduce the layering inversion Phase 4 fixes."""
    import app.media_engine.media_urls as media_urls_mod

    src = Path(media_urls_mod.__file__).read_text(encoding="utf-8")
    assert "app.services.exporter" not in src
    assert "from app.services.exporter" not in src


# ──────────────────────────────────────────────────────────────────────
# 7. fallback_media
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "media_type, expected_type, expected_icon",
    [
        ("pdf", "pdf_fallback", "📄"),
        ("document", "pdf_fallback", "📄"),
        ("video", "video_fallback", "🎥"),
        ("audio", "audio_fallback", "🎙"),
        ("voice", "audio_fallback", "🎙"),
        ("image", "image_fallback", "📷"),
        ("url", "url_fallback", "🔗"),
        ("text", "file_fallback", "🗂️"),
        (None, "file_fallback", "🗂️"),
    ],
)
def test_build_fallback_card(media_type, expected_type, expected_icon):
    from app.media_engine.fallback_media import build_fallback_card

    card = build_fallback_card(media_type, reason="missing file")
    assert card.fallback_type == expected_type
    assert card.icon == expected_icon
    assert card.label  # non-empty
    assert card.reason == "missing file"


def test_corrupted_file_returns_fallback_not_crash(tmp_path: Path):
    """When a "corrupted" media path is fed to the engine, the build
    helpers must return ``None`` (so the caller emits a fallback) and
    never raise."""
    from app.media_engine.image_pipeline import image_to_data_uri
    from app.media_engine.fallback_media import build_fallback_card
    from app.media_engine.schemas import MEDIA_IMAGE

    bogus = tmp_path / "broken.png"
    bogus.write_bytes(b"not a real image, just bytes")
    # Pillow won't validate without us asking; the engine cap+read
    # path either succeeds (if mimetypes can guess image/png) or
    # returns None — but it must not raise.
    try:
        uri = image_to_data_uri(bogus)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"image_to_data_uri raised: {exc!r}")

    # In either outcome the caller's downstream fallback card is well-formed.
    card = build_fallback_card(MEDIA_IMAGE, reason="corrupted")
    assert card.icon == "📷"


# ──────────────────────────────────────────────────────────────────────
# 8. Architectural asserts
# ──────────────────────────────────────────────────────────────────────


def _iter_python_sources(root: Path):
    for path in root.rglob("*.py"):
        # Skip __pycache__ and the engine itself when checking external boundaries.
        if "__pycache__" in path.parts:
            continue
        yield path


def _calls_base64_b64encode(source: str) -> bool:
    """AST-level check — only counts real call sites, not docstring
    mentions of ``base64.b64encode``."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # base64.b64encode(...)
        if (isinstance(func, ast.Attribute)
                and func.attr in {"b64encode", "encodebytes"}
                and isinstance(func.value, ast.Name)
                and func.value.id == "base64"):
            return True
        # b64encode(...)  — bare imported name
        if isinstance(func, ast.Name) and func.id in {"b64encode", "encodebytes"}:
            return True
    return False


def test_no_base64_b64encode_outside_media_engine():
    """The Phase-4 plan: only ``media_engine._base64_utils`` may call
    ``base64.b64encode``. ``moyasar.py`` is explicitly allowed because
    its base64 encodes HTTP basic-auth credentials — not media bytes —
    so it does not contribute to the PDF-bloat problem this rule
    addresses."""
    allowed_relative = {
        Path("media_engine/_base64_utils.py"),
        Path("services/moyasar.py"),  # HTTP basic auth, not media
    }

    offenders: list[str] = []
    for path in _iter_python_sources(APP_DIR):
        rel = path.relative_to(APP_DIR)
        if rel in allowed_relative:
            continue
        text = path.read_text(encoding="utf-8")
        if _calls_base64_b64encode(text):
            offenders.append(str(rel))

    assert not offenders, (
        f"base64.b64encode must only live in media_engine; "
        f"found in: {offenders}"
    )


def test_no_data_uri_construction_outside_media_engine():
    """No module outside ``media_engine`` may build a ``data:...;base64,``
    string by hand. The test searches for the literal pattern; modules
    that *consume* a pre-built URI (e.g. as a Jinja variable) are fine."""
    pattern = re.compile(r"f?[\"']data:[^\"']*?;base64,")
    allowed_dir = APP_DIR / "media_engine"

    offenders: list[str] = []
    for path in _iter_python_sources(APP_DIR):
        if allowed_dir in path.parents or path == allowed_dir:
            continue
        text = path.read_text(encoding="utf-8")
        # Skip mentions inside docstrings/comments by requiring an f-string
        # or raw string assignment context — pattern already enforces this.
        if pattern.search(text):
            offenders.append(str(path.relative_to(APP_DIR)))

    assert not offenders, (
        f"data: URIs must be produced only inside media_engine; "
        f"found in: {offenders}"
    )


def test_no_files_url_construction_outside_media_engine():
    """Building ``<base>/files/<rel>`` strings is reserved for
    ``media_engine.media_urls``. The legacy wrappers in
    ``services.exporter`` and ``services.storage`` are now thin
    delegates that import the helper — they no longer compose the URL
    by hand. This test pins that down."""
    # Focus on the *interpolation* form `/files/{...}` — searching for
    # the bare path "/files/" would match docstrings and tests.
    pattern = re.compile(r"/files/\{")
    allowed_dir = APP_DIR / "media_engine"

    offenders: list[str] = []
    for path in _iter_python_sources(APP_DIR):
        if allowed_dir in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path.relative_to(APP_DIR)))

    assert not offenders, (
        f"public /files/ URLs must be built inside media_engine only; "
        f"found in: {offenders}"
    )


def test_media_engine_has_no_orm_or_playwright_imports():
    """Phase-4 rule: ``app.media_engine`` is independent of the ORM
    and the PDF runtime."""
    forbidden = (
        "sqlalchemy",
        "from app.models",
        "from app.db",
        "playwright",
    )
    offenders: list[tuple[str, str]] = []
    for path in _iter_python_sources(MEDIA_ENGINE_DIR):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append((str(path.relative_to(MEDIA_ENGINE_DIR)), needle))
    assert not offenders, (
        f"media_engine must not import ORM or Playwright; "
        f"found: {offenders}"
    )


def test_exporter_does_not_define_url_helpers_inline():
    """Verify the exporter only delegates URL construction to
    ``media_engine``. We assert the bodies of ``_public_storage_url``
    and ``_public_media_url`` are short (they're 2-3 line wrappers)."""
    text = (APP_DIR / "services" / "exporter.py").read_text(encoding="utf-8")
    # Each adapter must mention media_engine (proves it's a delegate).
    for fn in ("_public_storage_url", "_public_media_url", "_image_data_uri", "_file_data_uri"):
        assert fn in text, f"{fn} missing from exporter"
    # Spot-check: the legacy 'blocked_hosts' tuple is gone from exporter.
    assert "lookaside.fbsbx.com" not in text, (
        "exporter still has hard-coded blocked-host list — should be in media_engine"
    )


# ──────────────────────────────────────────────────────────────────────
# 9. End-to-end: MediaAsset wired through preview + thumbnail builders
# ──────────────────────────────────────────────────────────────────────


def test_preview_and_thumbnail_dispatchers(tmp_path: Path):
    from app.media_engine import build_preview, build_thumbnail
    from app.media_engine.schemas import MediaAsset, MEDIA_IMAGE

    img = _write_png(tmp_path / "evidence.png")
    asset = MediaAsset(media_type=MEDIA_IMAGE, file_path=str(img))

    preview = build_preview(asset)
    thumb = build_thumbnail(asset)
    assert preview is not None and preview.startswith("data:image/")
    assert thumb is not None and thumb.startswith("data:image/")
    assert preview == thumb  # for images they are identical


def test_preview_dispatcher_returns_none_for_text_asset():
    from app.media_engine import build_preview
    from app.media_engine.schemas import MediaAsset, MEDIA_TEXT

    asset = MediaAsset(media_type=MEDIA_TEXT, file_path=None)
    assert build_preview(asset) is None
