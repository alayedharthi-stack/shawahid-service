"""
Phase-7 storage_engine tests.

Coverage matrix:
    1. paths            — traversal protection, Arabic filenames, ext preservation
    2. hashing          — same bytes → same hash, different → different
    3. dedup            — per-teacher scope, mark_duplicate semantics
    4. file_store       — save/read/exists/size/delete, traversal block
    5. evidence_store   — attach helpers, build_ref DTO
    6. cleanup          — orphan, missing, broken-path detection
    7. validators       — mime / size / scope / safe url
    8. adapter          — services.storage public API still works
    9. architecture     — no Playwright / no export_engine in storage_engine
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
STORAGE_ENGINE_DIR = PROJECT_ROOT / "app" / "storage_engine"


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: redirect storage_root to a per-test tmp directory
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Point ``settings.storage_path`` at a fresh ``tmp_path`` so file
    operations never escape the test sandbox.
    """
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "STORAGE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(cfg.settings, "STORAGE_ROOT", str(tmp_path), raising=False)
    return tmp_path


# ──────────────────────────────────────────────────────────────────────────────
# 1. paths
# ──────────────────────────────────────────────────────────────────────────────


class TestPaths:
    def test_safe_filename_preserves_extension(self, isolated_storage):
        from app.storage_engine.paths import safe_filename
        name = safe_filename("الخطة الأسبوعية.pdf")
        assert name.endswith(".pdf")

    def test_safe_filename_keeps_arabic(self, isolated_storage):
        from app.storage_engine.paths import safe_filename
        name = safe_filename("نشاط_صفي.jpg")
        assert "نشاط" in name or "صفي" in name
        assert name.endswith(".jpg")

    def test_safe_filename_strips_path_traversal(self, isolated_storage):
        from app.storage_engine.paths import safe_filename
        name = safe_filename("../../etc/passwd")
        assert ".." not in name
        assert "/" not in name
        assert "\\" not in name

    def test_safe_filename_uses_mime_when_missing_ext(self, isolated_storage):
        from app.storage_engine.paths import safe_filename
        name = safe_filename(None, mime_type="image/jpeg")
        assert name.endswith((".jpg", ".jpeg"))

    def test_safe_filename_rejects_windows_reserved(self, isolated_storage):
        from app.storage_engine.paths import safe_filename
        name = safe_filename("CON.txt")
        assert name.lower().split("_", 1)[1].split(".")[0] != "con"

    def test_build_teacher_storage_path_default_layout(self, isolated_storage):
        from datetime import datetime, timezone
        from app.storage_engine.paths import build_teacher_storage_path

        fixed = datetime(2026, 5, 7, tzinfo=timezone.utc)
        p = build_teacher_storage_path(
            teacher_id=42,
            media_type="image",
            filename="نشاط.jpg",
            now=fixed,
        )
        parts = p.parts
        assert "teachers" in parts
        idx = parts.index("teachers")
        assert parts[idx + 1] == "42"
        assert parts[idx + 2] == "image"
        assert parts[idx + 3] == "2026"
        assert parts[idx + 4] == "05"
        assert parts[-1].endswith(".jpg")

    def test_build_teacher_storage_path_legacy_layout(self, isolated_storage):
        from app.storage_engine.paths import build_teacher_storage_path
        p = build_teacher_storage_path(
            teacher_id=42,
            media_type="image",
            filename="x.jpg",
            use_legacy_evidences_layout=True,
        )
        assert p.parent.name == "evidences"
        assert p.parent.parent.name == "42"

    def test_build_teacher_storage_path_unknown_media_type_falls_back_to_misc(self, isolated_storage):
        from app.storage_engine.paths import build_teacher_storage_path
        p = build_teacher_storage_path(
            teacher_id=42,
            media_type="weird_thing",
            filename="x.bin",
            partition_by_date=False,
        )
        assert "misc" in p.parts

    def test_build_teacher_storage_path_rejects_invalid_teacher(self, isolated_storage):
        from app.storage_engine.paths import build_teacher_storage_path
        with pytest.raises(ValueError):
            build_teacher_storage_path(teacher_id=0, media_type="image", filename="x.jpg")
        with pytest.raises(ValueError):
            build_teacher_storage_path(teacher_id=-3, media_type="image", filename="x.jpg")

    def test_ensure_within_storage_root_blocks_traversal(self, isolated_storage):
        from app.storage_engine.paths import ensure_within_storage_root
        with pytest.raises(ValueError):
            ensure_within_storage_root("/etc/passwd")
        with pytest.raises(ValueError):
            ensure_within_storage_root(str(isolated_storage / ".." / "outside.txt"))

    def test_ensure_within_storage_root_accepts_inside(self, isolated_storage):
        from app.storage_engine.paths import ensure_within_storage_root
        target = isolated_storage / "teachers" / "1" / "x.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        result = ensure_within_storage_root(target)
        assert result.exists()


# ──────────────────────────────────────────────────────────────────────────────
# 2. hashing
# ──────────────────────────────────────────────────────────────────────────────


class TestHashing:
    def test_compute_content_hash_deterministic(self):
        from app.storage_engine.hashing import compute_content_hash
        assert compute_content_hash(b"hello") == compute_content_hash(b"hello")

    def test_compute_content_hash_distinguishes_inputs(self):
        from app.storage_engine.hashing import compute_content_hash
        assert compute_content_hash(b"hello") != compute_content_hash(b"hello!")

    def test_compute_content_hash_rejects_non_bytes(self):
        from app.storage_engine.hashing import compute_content_hash
        with pytest.raises(TypeError):
            compute_content_hash("hello")  # type: ignore[arg-type]

    def test_hash_text_normalises_arabic(self):
        from app.storage_engine.hashing import hash_text
        # Same text with different hamza variant should hash identically
        assert hash_text("أحمد") == hash_text("احمد")

    def test_hash_url_strips_query(self):
        from app.storage_engine.hashing import hash_url
        assert hash_url("https://youtu.be/abc") == hash_url("https://youtu.be/abc?t=10")

    def test_hash_url_distinguishes_paths(self):
        from app.storage_engine.hashing import hash_url
        assert hash_url("https://x.com/a") != hash_url("https://x.com/b")


# ──────────────────────────────────────────────────────────────────────────────
# 3. dedup (with mocked DB session)
# ──────────────────────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_kw):
        return self

    def order_by(self, *_a, **_kw):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal in-memory mock of a SQLAlchemy session for dedup tests."""

    def __init__(self, evidences=None):
        self.evidences: list[SimpleNamespace] = list(evidences or [])
        self.commits = 0

    def query(self, _model):
        return _FakeQuery(self.evidences)

    def commit(self):
        self.commits += 1


class TestDedup:
    def _make_ev(self, *, ev_id, teacher_id, content_hash, ai_raw=None):
        return SimpleNamespace(
            id=ev_id,
            teacher_id=teacher_id,
            content_hash=content_hash,
            is_excluded_from_export=False,
            ai_raw=ai_raw or {},
        )

    def test_find_duplicate_returns_match(self):
        from app.storage_engine.dedup import find_duplicate_by_hash
        ev = self._make_ev(ev_id=1, teacher_id=42, content_hash="abc")
        db = _FakeSession([ev])
        result = find_duplicate_by_hash(db, teacher_id=42, content_hash="abc")
        assert result is ev

    def test_find_duplicate_returns_none_for_empty_hash(self):
        from app.storage_engine.dedup import find_duplicate_by_hash
        db = _FakeSession([])
        assert find_duplicate_by_hash(db, teacher_id=42, content_hash="") is None

    def test_find_duplicate_returns_none_for_invalid_teacher(self):
        from app.storage_engine.dedup import find_duplicate_by_hash
        db = _FakeSession([])
        assert find_duplicate_by_hash(db, teacher_id=0, content_hash="abc") is None

    def test_is_duplicate_for_teacher(self):
        from app.storage_engine.dedup import is_duplicate_for_teacher
        ev = self._make_ev(ev_id=1, teacher_id=42, content_hash="abc")
        db = _FakeSession([ev])
        assert is_duplicate_for_teacher(db, 42, "abc") is True
        empty = _FakeSession([])
        assert is_duplicate_for_teacher(empty, 42, "abc") is False

    def _make_two_row_session(self, ev_dup, ev_target):
        """Return a fake session whose two consecutive query().filter().first()
        calls yield ``ev_dup`` then ``ev_target`` (the order ``mark_duplicate``
        looks them up).
        """

        class _Q:
            def __init__(self, rows):
                self._rows = rows
            def filter(self, *_a, **_kw):
                return self
            def order_by(self, *_a, **_kw):
                return self
            def first(self):
                return self._rows[0] if self._rows else None

        sequence = iter([_Q([ev_dup]), _Q([ev_target])])

        class _SeqSession:
            def __init__(self):
                self.commits = 0
            def query(self, _model):
                return next(sequence)
            def commit(self):
                self.commits += 1

        return _SeqSession()

    def test_mark_duplicate_sets_flags_and_ai_raw(self):
        """mark_duplicate excludes from export and records duplicate_of_id."""
        from app.storage_engine import dedup as dedup_module

        ev = self._make_ev(ev_id=2, teacher_id=42, content_hash="abc")
        original = self._make_ev(ev_id=1, teacher_id=42, content_hash="abc")
        db = self._make_two_row_session(ev, original)

        ok = dedup_module.mark_duplicate(db, evidence_id=2, duplicate_of_id=1)
        assert ok is True
        assert ev.is_excluded_from_export is True
        assert ev.ai_raw["is_duplicate"] is True
        assert ev.ai_raw["duplicate_of_id"] == 1
        assert db.commits == 1

    def test_mark_duplicate_blocks_cross_teacher(self):
        from app.storage_engine import dedup as dedup_module

        ev = self._make_ev(ev_id=2, teacher_id=99, content_hash="abc")
        original = self._make_ev(ev_id=1, teacher_id=42, content_hash="abc")
        db = self._make_two_row_session(ev, original)

        ok = dedup_module.mark_duplicate(db, evidence_id=2, duplicate_of_id=1)
        assert ok is False
        assert ev.is_excluded_from_export is False  # untouched
        assert db.commits == 0

    def test_mark_duplicate_refuses_self_reference(self):
        from app.storage_engine.dedup import mark_duplicate
        db = _FakeSession([])
        assert mark_duplicate(db, evidence_id=5, duplicate_of_id=5) is False


# ──────────────────────────────────────────────────────────────────────────────
# 4. file_store
# ──────────────────────────────────────────────────────────────────────────────


class TestFileStore:
    def test_save_uploaded_file_creates_file_with_hash(self, isolated_storage):
        from app.storage_engine.file_store import save_uploaded_file
        from pathlib import Path

        stored = save_uploaded_file(
            teacher_id=42,
            raw_bytes=b"hello world",
            original_filename="test.txt",
            mime_type="text/plain",
            media_type="document",
            correct_image_orientation=False,
        )
        assert Path(stored.stored_path).exists()
        assert Path(stored.stored_path).read_bytes() == b"hello world"
        assert stored.file_size == len(b"hello world")
        assert stored.content_hash  # populated

    def test_save_uploaded_file_legacy_uses_evidences_layout(self, isolated_storage):
        from app.storage_engine.file_store import save_uploaded_file_legacy
        from pathlib import Path

        stored = save_uploaded_file_legacy(
            teacher_id=42,
            raw_bytes=b"data",
            original_filename="x.bin",
            mime_type="application/octet-stream",
            correct_image_orientation=False,
        )
        path = Path(stored.stored_path)
        assert path.parent.name == "evidences"
        assert "42" in path.parts

    def test_file_exists_returns_true_for_existing(self, isolated_storage):
        from app.storage_engine.file_store import file_exists, save_uploaded_file
        stored = save_uploaded_file(
            teacher_id=1,
            raw_bytes=b"x",
            original_filename="a.txt",
            mime_type="text/plain",
            media_type="document",
            correct_image_orientation=False,
        )
        assert file_exists(stored.stored_path) is True

    def test_file_exists_false_for_missing(self, isolated_storage):
        from app.storage_engine.file_store import file_exists
        assert file_exists(None) is False
        assert file_exists("/nonexistent/path") is False

    def test_file_exists_blocks_traversal(self, isolated_storage):
        from app.storage_engine.file_store import file_exists
        # Path outside storage root → False, never raises
        assert file_exists("/etc/passwd") is False

    def test_get_file_size(self, isolated_storage):
        from app.storage_engine.file_store import get_file_size, save_uploaded_file
        stored = save_uploaded_file(
            teacher_id=1,
            raw_bytes=b"hello",
            original_filename="a.txt",
            mime_type="text/plain",
            media_type="document",
            correct_image_orientation=False,
        )
        assert get_file_size(stored.stored_path) == 5

    def test_read_stored_file_blocks_outside_root(self, isolated_storage):
        from app.storage_engine.file_store import read_stored_file
        with pytest.raises(ValueError):
            read_stored_file("/etc/passwd")

    def test_read_stored_file_returns_bytes(self, isolated_storage):
        from app.storage_engine.file_store import read_stored_file, save_uploaded_file
        stored = save_uploaded_file(
            teacher_id=1, raw_bytes=b"abc", original_filename="x.bin",
            mime_type="application/octet-stream", media_type="document",
            correct_image_orientation=False,
        )
        assert read_stored_file(stored.stored_path) == b"abc"

    def test_delete_stored_file(self, isolated_storage):
        from app.storage_engine.file_store import (
            delete_stored_file, file_exists, save_uploaded_file,
        )
        stored = save_uploaded_file(
            teacher_id=1, raw_bytes=b"x", original_filename="a.txt",
            mime_type="text/plain", media_type="document",
            correct_image_orientation=False,
        )
        assert delete_stored_file(stored.stored_path) is True
        assert file_exists(stored.stored_path) is False

    def test_delete_stored_file_returns_false_when_missing(self, isolated_storage):
        from app.storage_engine.file_store import delete_stored_file
        target = isolated_storage / "teachers" / "1" / "ghost.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        assert delete_stored_file(target) is False

    def test_same_bytes_two_teachers_yield_same_hash_but_separate_files(self, isolated_storage):
        """Same bytes → same hash, but two different teachers get isolated files."""
        from app.storage_engine.file_store import save_uploaded_file
        from pathlib import Path
        a = save_uploaded_file(
            teacher_id=1, raw_bytes=b"same", original_filename="x.bin",
            mime_type="application/octet-stream", media_type="document",
            correct_image_orientation=False,
        )
        b = save_uploaded_file(
            teacher_id=2, raw_bytes=b"same", original_filename="x.bin",
            mime_type="application/octet-stream", media_type="document",
            correct_image_orientation=False,
        )
        assert a.content_hash == b.content_hash
        assert Path(a.stored_path) != Path(b.stored_path)
        assert "1" in Path(a.stored_path).parts
        assert "2" in Path(b.stored_path).parts


# ──────────────────────────────────────────────────────────────────────────────
# 5. evidence_store
# ──────────────────────────────────────────────────────────────────────────────


class TestEvidenceStore:
    def _make_evidence(self, *, ev_id=1, teacher_id=42, ai_raw=None):
        return SimpleNamespace(
            id=ev_id,
            teacher_id=teacher_id,
            storage_path=None,
            file_name=None,
            mime_type=None,
            content_hash=None,
            ai_raw=ai_raw,
        )

    def test_attach_file_to_evidence(self):
        from app.storage_engine.evidence_store import attach_file_to_evidence
        from app.storage_engine.schemas import StoredFile

        ev = self._make_evidence()
        db = _FakeSession([ev])
        stored = StoredFile(
            original_filename="a.jpg",
            stored_path="/tmp/a.jpg",
            public_path="/files/42/a.jpg",
            mime_type="image/jpeg",
            file_size=10,
            content_hash="abc",
        )
        attach_file_to_evidence(db, ev, stored)
        assert ev.storage_path == "/tmp/a.jpg"
        assert ev.file_name == "a.jpg"
        assert ev.content_hash == "abc"
        assert db.commits == 1

    def test_attach_preview_records_in_ai_raw(self):
        from app.storage_engine.evidence_store import attach_preview_to_evidence
        ev = self._make_evidence(ai_raw={"existing": True})
        db = _FakeSession([ev])
        attach_preview_to_evidence(db, ev, "/tmp/preview.jpg")
        assert ev.ai_raw["preview_path"] == "/tmp/preview.jpg"
        assert ev.ai_raw["existing"] is True

    def test_build_evidence_storage_ref_full(self):
        from app.storage_engine.evidence_store import build_evidence_storage_ref
        ev = SimpleNamespace(
            id=7,
            teacher_id=42,
            storage_path="/tmp/x.jpg",
            content_hash="abc",
            ai_raw={
                "preview_path": "/tmp/p.jpg",
                "thumbnail_path": "/tmp/t.jpg",
                "duplicate_of_id": 3,
                "is_duplicate": True,
            },
        )
        ref = build_evidence_storage_ref(ev)
        assert ref.evidence_id == 7
        assert ref.teacher_id == 42
        assert ref.file_path == "/tmp/x.jpg"
        assert ref.preview_path == "/tmp/p.jpg"
        assert ref.thumbnail_path == "/tmp/t.jpg"
        assert ref.is_duplicate is True
        assert ref.duplicate_of_id == 3

    def test_build_evidence_storage_ref_missing_ai_raw(self):
        from app.storage_engine.evidence_store import build_evidence_storage_ref
        ev = SimpleNamespace(
            id=1, teacher_id=42, storage_path=None,
            content_hash=None, ai_raw=None,
        )
        ref = build_evidence_storage_ref(ev)
        assert ref.is_duplicate is False
        assert ref.duplicate_of_id is None
        assert ref.preview_path is None


# ──────────────────────────────────────────────────────────────────────────────
# 6. cleanup
# ──────────────────────────────────────────────────────────────────────────────


class TestCleanup:
    def test_find_missing_files_detects_gone_paths(self, isolated_storage):
        from app.storage_engine.cleanup import find_missing_files

        ev = SimpleNamespace(
            id=1, teacher_id=42,
            storage_path=str(isolated_storage / "teachers" / "42" / "missing.jpg"),
        )

        class _Q:
            def __init__(self, rows): self.rows = rows
            def filter(self, *_a, **_kw): return self
            def all(self):
                return [(r.id, r.teacher_id, r.storage_path) for r in self.rows]

        class _DB:
            def __init__(self, rows): self.rows = rows
            def query(self, *_a, **_kw): return _Q(self.rows)

        db = _DB([ev])
        missing = find_missing_files(db)
        assert len(missing) == 1
        assert missing[0].evidence_id == 1
        assert missing[0].teacher_id == 42

    def test_find_orphan_files_detects_unreferenced(self, isolated_storage):
        from app.storage_engine.cleanup import find_orphan_files
        # Drop a stray file under teachers/42 with no DB row
        teacher_dir = isolated_storage / "teachers" / "42"
        teacher_dir.mkdir(parents=True, exist_ok=True)
        orphan = teacher_dir / "orphan.bin"
        orphan.write_bytes(b"orphan-bytes")

        class _Q:
            def filter(self, *_a, **_kw): return self
            def all(self): return []

        class _DB:
            def query(self, *_a, **_kw): return _Q()

        db = _DB()
        orphans = find_orphan_files(db)
        paths = [o.path for o in orphans]
        assert str(orphan) in paths

    def test_find_broken_evidence_paths(self, isolated_storage):
        from app.storage_engine.cleanup import find_broken_evidence_paths

        ev = SimpleNamespace(id=1, teacher_id=42, storage_path="/etc/passwd")

        class _Q:
            def __init__(self, rows): self.rows = rows
            def filter(self, *_a, **_kw): return self
            def all(self):
                return [(r.id, r.teacher_id, r.storage_path) for r in self.rows]

        class _DB:
            def __init__(self, rows): self.rows = rows
            def query(self, *_a, **_kw): return _Q(self.rows)

        broken = find_broken_evidence_paths(_DB([ev]))
        assert len(broken) == 1
        assert broken[0].evidence_id == 1
        assert "escapes" in broken[0].reason.lower() or "outside" in broken[0].reason.lower()

    def test_cleanup_module_does_not_delete(self):
        """Architectural: cleanup must not call unlink/rmtree/remove."""
        src = (STORAGE_ENGINE_DIR / "cleanup.py").read_text(encoding="utf-8")
        forbidden = ("unlink(", "rmtree", "os.remove", ".remove(")
        for f in forbidden:
            assert f not in src, f"cleanup.py performs deletion: {f!r}"


# ──────────────────────────────────────────────────────────────────────────────
# 7. validators
# ──────────────────────────────────────────────────────────────────────────────


class TestValidators:
    def test_validate_storage_path_blocks_traversal(self, isolated_storage):
        from app.storage_engine.validators import (
            StorageValidationError, validate_storage_path,
        )
        with pytest.raises(StorageValidationError):
            validate_storage_path("/etc/passwd")

    def test_validate_mime_accepts_common(self):
        from app.storage_engine.validators import validate_mime_type
        assert validate_mime_type("image/jpeg") == "image/jpeg"
        assert validate_mime_type("application/pdf") == "application/pdf"

    def test_validate_mime_rejects_unknown(self):
        from app.storage_engine.validators import (
            StorageValidationError, validate_mime_type,
        )
        with pytest.raises(StorageValidationError):
            validate_mime_type("application/x-random")

    def test_validate_file_size(self):
        from app.storage_engine.validators import (
            StorageValidationError, validate_file_size,
        )
        assert validate_file_size(10) == 10
        with pytest.raises(StorageValidationError):
            validate_file_size(0)
        with pytest.raises(StorageValidationError):
            validate_file_size(200 * 1024 * 1024, max_mb=50)

    def test_validate_teacher_scope(self, isolated_storage):
        from app.storage_engine.validators import (
            StorageValidationError, validate_teacher_scope,
        )
        own = isolated_storage / "teachers" / "42" / "x.txt"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_bytes(b"x")
        validate_teacher_scope(own, 42)  # should not raise
        with pytest.raises(StorageValidationError):
            validate_teacher_scope(own, 99)

    def test_validate_safe_url(self):
        from app.storage_engine.validators import (
            StorageValidationError, validate_safe_url,
        )
        validate_safe_url("https://example.com/x")
        with pytest.raises(StorageValidationError):
            validate_safe_url("ftp://example.com/x")
        with pytest.raises(StorageValidationError):
            validate_safe_url("javascript:alert(1)")
        with pytest.raises(StorageValidationError):
            validate_safe_url("")


# ──────────────────────────────────────────────────────────────────────────────
# 8. adapter — services.storage public API still works
# ──────────────────────────────────────────────────────────────────────────────


class TestServicesStorageAdapter:
    def test_extract_urls_still_works(self):
        from app.services.storage import extract_urls
        urls = extract_urls("شوف هذا https://example.com/a وذا http://x.io")
        assert "https://example.com/a" in urls
        assert "http://x.io" in urls

    def test_detect_evidence_type_still_works(self):
        from app.services.storage import detect_evidence_type
        assert detect_evidence_type("image/jpeg", "x.jpg", None) == "image"
        assert detect_evidence_type("application/pdf", "x.pdf", None) == "pdf"
        assert detect_evidence_type(None, None, "https://x.com") == "url"
        assert detect_evidence_type(None, None, "نص فقط") == "text"

    def test_storage_path_to_file_url_delegates(self):
        from app.services.storage import storage_path_to_file_url
        url = storage_path_to_file_url("/srv/storage/teachers/42/evidences/x.jpg", "https://h.io")
        assert url is None or "/files/" in url

    def test_deduplication_hash_helpers_delegate(self):
        from app.services.deduplication import hash_bytes, hash_text, hash_url
        from app.storage_engine.hashing import (
            compute_content_hash, hash_text as engine_hash_text,
            hash_url as engine_hash_url,
        )
        assert hash_bytes(b"x") == compute_content_hash(b"x")
        assert hash_text("نص") == engine_hash_text("نص")
        assert hash_url("https://x.com/a") == engine_hash_url("https://x.com/a")

    def test_services_storage_is_thin(self):
        """services/storage.py should import from storage_engine, not duplicate logic."""
        src = (PROJECT_ROOT / "app" / "services" / "storage.py").read_text(encoding="utf-8")
        assert "from app.storage_engine" in src

    def test_services_deduplication_is_thin(self):
        src = (PROJECT_ROOT / "app" / "services" / "deduplication.py").read_text(encoding="utf-8")
        assert "from app.storage_engine" in src


# ──────────────────────────────────────────────────────────────────────────────
# 9. architectural contracts
# ──────────────────────────────────────────────────────────────────────────────


class TestArchitecture:
    def _module_imports(self, module_path: pathlib.Path) -> list[str]:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_storage_engine_no_export_engine(self):
        for f in STORAGE_ENGINE_DIR.glob("*.py"):
            for name in self._module_imports(f):
                assert "export_engine" not in name, (
                    f"{f.name} imports export_engine: {name}"
                )

    def test_storage_engine_no_playwright(self):
        for f in STORAGE_ENGINE_DIR.glob("*.py"):
            for name in self._module_imports(f):
                assert "playwright" not in name.lower(), (
                    f"{f.name} imports Playwright: {name}"
                )

    def test_pure_modules_no_sqlalchemy(self):
        """paths, hashing, schemas, validators must be pure."""
        pure = ["paths.py", "hashing.py", "schemas.py", "validators.py"]
        for fname in pure:
            for name in self._module_imports(STORAGE_ENGINE_DIR / fname):
                assert "sqlalchemy" not in name, (
                    f"{fname} must not import SQLAlchemy: {name}"
                )

    def test_storage_engine_does_not_import_review_engine(self):
        for f in STORAGE_ENGINE_DIR.glob("*.py"):
            for name in self._module_imports(f):
                assert "review_engine" not in name, (
                    f"{f.name} imports review_engine: {name}"
                )

    def test_storage_engine_does_not_import_media_engine(self):
        for f in STORAGE_ENGINE_DIR.glob("*.py"):
            for name in self._module_imports(f):
                assert "media_engine" not in name, (
                    f"{f.name} imports media_engine: {name}"
                )

    def test_storage_engine_files_present(self):
        required = {
            "__init__.py", "schemas.py", "paths.py", "hashing.py",
            "dedup.py", "file_store.py", "evidence_store.py",
            "cleanup.py", "validators.py",
        }
        present = {p.name for p in STORAGE_ENGINE_DIR.glob("*.py")}
        missing = required - present
        assert not missing, f"missing storage_engine files: {missing}"

    def test_no_evidence_storage_helper_outside_storage_engine(self):
        """Detect new ad-hoc path-building. Whitelist is allowed call sites only."""
        whitelist = {
            (PROJECT_ROOT / "app" / "core" / "config.py").resolve(),
            (PROJECT_ROOT / "app" / "storage_engine" / "paths.py").resolve(),
            (PROJECT_ROOT / "app" / "storage_engine" / "file_store.py").resolve(),
        }
        offenders: list[str] = []
        for f in (PROJECT_ROOT / "app").rglob("*.py"):
            if f.resolve() in whitelist:
                continue
            text = f.read_text(encoding="utf-8")
            # Only flag *literal* construction patterns, not docstrings.
            if 'storage_path / "teachers" /' in text:
                offenders.append(str(f))
        assert not offenders, f"ad-hoc storage path construction in: {offenders}"
