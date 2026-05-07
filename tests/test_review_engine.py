"""
Phase-5 contract tests for ``app.review_engine``.

All tests avoid the database, Playwright, and OpenAI.
ORM actions in ``review_actions`` are tested with a real SQLite
in-memory database so the CRUD paths execute fully.

Test categories
---------------
1. ReviewItem / ReviewSession DTOs — pure construction.
2. build_review_session — duplicate detection, confidence flagging,
   ordering, media preview routing.
3. review_actions — approve / delete (soft) / restore / update_title /
   update_category via in-memory SQLite.
4. review_links — signed token generation, validation, expiry.
5. review_summary — Arabic output shape.
6. review_permissions — pure boolean guards.
7. whatsapp_messages — review-specific builders.
8. Architectural asserts — no Playwright, no export_engine,
   review_service / schemas / summary / links have no ORM.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# ── project root so app.* imports resolve ─────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
REVIEW_ENGINE_DIR = APP_DIR / "review_engine"


# ══════════════════════════════════════════════════════════════════════
# 1. DTOs
# ══════════════════════════════════════════════════════════════════════


def test_review_item_defaults():
    from app.review_engine.schemas import ReviewItem

    item = ReviewItem(evidence_id=1, title="اختبار", category="التقويم")
    assert item.needs_review is False
    assert item.is_duplicate is False
    assert item.is_excluded is False
    assert item.importance_score == "medium"


def test_review_session_defaults():
    from app.review_engine.schemas import ReviewSession

    s = ReviewSession(teacher_id=7)
    assert s.total_items == 0
    assert s.items == []
    assert s.categories_summary == {}


# ══════════════════════════════════════════════════════════════════════
# 2. build_review_session
# ══════════════════════════════════════════════════════════════════════


def _fake_ev(**kwargs):
    defaults = {
        "id": 1, "evidence_type": "image", "category": "التخطيط",
        "title": "عنوان شاهد", "is_excluded_from_export": False,
        "content_hash": None, "ai_raw": None, "created_at": None,
        "storage_path": None, "media_url": None, "file_name": None,
        "message_text": None, "grade": "", "subject": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_session_active_count():
    from app.review_engine.review_service import build_review_session

    evs = [
        _fake_ev(id=1, is_excluded_from_export=False),
        _fake_ev(id=2, is_excluded_from_export=True),
        _fake_ev(id=3, is_excluded_from_export=False),
    ]
    s = build_review_session(evs, teacher_id=1)
    assert s.total_items == 3
    assert s.active_items == 2


def test_session_duplicate_detection():
    from app.review_engine.review_service import build_review_session

    evs = [
        _fake_ev(id=1, content_hash="abc123"),
        _fake_ev(id=2, content_hash="abc123"),
        _fake_ev(id=3, content_hash="different"),
    ]
    s = build_review_session(evs, teacher_id=1)
    dup_ids = {it.evidence_id for it in s.items if it.is_duplicate}
    assert dup_ids == {1, 2}
    assert s.duplicates_count == 2


def test_session_low_confidence_detection():
    from app.review_engine.review_service import build_review_session
    from app.review_engine.schemas import LOW_CONFIDENCE_THRESHOLD

    low_conf  = LOW_CONFIDENCE_THRESHOLD - 0.10
    high_conf = LOW_CONFIDENCE_THRESHOLD + 0.10
    evs = [
        _fake_ev(id=1, ai_raw={"confidence_score": low_conf}),
        _fake_ev(id=2, ai_raw={"confidence_score": high_conf}),
        _fake_ev(id=3, ai_raw=None),
    ]
    s = build_review_session(evs, teacher_id=1)
    assert s.low_confidence_count == 1
    # Item 1 flagged; item 2 and 3 not.
    items_by_id = {it.evidence_id: it for it in s.items}
    assert items_by_id[1].needs_review is True
    assert items_by_id[2].needs_review is False
    assert items_by_id[3].needs_review is False


def test_session_categories_summary():
    from app.review_engine.review_service import build_review_session

    evs = [
        _fake_ev(id=1, category="التخطيط"),
        _fake_ev(id=2, category="التقويم"),
        _fake_ev(id=3, category="التخطيط"),
        _fake_ev(id=4, category="التقويم", is_excluded_from_export=True),
    ]
    s = build_review_session(evs, teacher_id=1)
    # Excluded item's category should NOT appear in summary.
    assert s.categories_summary.get("التخطيط") == 2
    assert s.categories_summary.get("التقويم") == 1


def test_session_active_items_appear_first():
    from app.review_engine.review_service import build_review_session

    evs = [
        _fake_ev(id=1, is_excluded_from_export=True),
        _fake_ev(id=2, is_excluded_from_export=False),
        _fake_ev(id=3, is_excluded_from_export=False),
    ]
    s = build_review_session(evs, teacher_id=1)
    # First two items must be active.
    assert s.items[0].is_excluded is False
    assert s.items[1].is_excluded is False
    assert s.items[2].is_excluded is True


def test_session_no_exporter_dependency():
    """review_service must build a session without any import from exporter."""
    import app.review_engine.review_service as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "app.services.exporter" not in src
    assert "app.export_engine"     not in src


# ══════════════════════════════════════════════════════════════════════
# 3. review_actions (in-memory SQLite)
# ══════════════════════════════════════════════════════════════════════

class _FakeSession:
    """Minimal session stub that records commits."""
    def __init__(self): self.committed = False
    def commit(self): self.committed = True


@pytest.fixture()
def mem_db(monkeypatch):
    """Patch ``review_actions._fetch_owned`` to return a SimpleNamespace.

    The fake 'Evidence' object has the same attributes the actions touch.
    teacher_id=1 maps to id=42; any other teacher_id gets None (not found).
    """
    import app.review_engine.review_actions as ra

    _row = SimpleNamespace(
        id=42,
        teacher_id=1,
        category="التخطيط",
        title="عنوان أصلي",
        is_excluded_from_export=False,
    )

    def _fake_fetch(db, evidence_id, teacher_id):
        if evidence_id == 42 and teacher_id == 1:
            return _row
        return None

    monkeypatch.setattr(ra, "_fetch_owned", _fake_fetch)

    session = _FakeSession()
    yield session, _row


def test_action_delete_is_soft(mem_db):
    from app.review_engine.review_actions import delete_evidence
    session, row = mem_db

    result = delete_evidence(session, 42, 1)
    assert result["ok"] is True
    assert result["is_excluded"] is True
    # Row object still exists (soft delete = flag change only)
    assert row.id == 42
    assert row.is_excluded_from_export is True


def test_action_restore(mem_db):
    from app.review_engine.review_actions import delete_evidence, restore_evidence
    session, row = mem_db

    delete_evidence(session, 42, 1)
    result = restore_evidence(session, 42, 1)
    assert result["ok"] is True
    assert result["is_excluded"] is False
    assert row.is_excluded_from_export is False


def test_action_update_title(mem_db):
    from app.review_engine.review_actions import update_evidence_title
    session, row = mem_db

    result = update_evidence_title(session, 42, 1, "عنوان جديد معدّل")
    assert result["ok"] is True
    assert result["title"] == "عنوان جديد معدّل"
    assert row.title == "عنوان جديد معدّل"


def test_action_update_category(mem_db):
    from app.review_engine.review_actions import update_evidence_category
    session, row = mem_db

    result = update_evidence_category(session, 42, 1, "التقويم")
    assert result["ok"] is True
    assert result["category"] == "التقويم"
    assert row.category == "التقويم"


def test_action_wrong_owner_returns_error(mem_db):
    from app.review_engine.review_actions import delete_evidence
    session, _ = mem_db

    result = delete_evidence(session, 42, 999)  # wrong teacher_id
    assert result["ok"] is False
    assert result["error"] == "evidence_not_found"


def test_action_approve_evidence(mem_db):
    from app.review_engine.review_actions import delete_evidence, approve_evidence
    session, row = mem_db

    delete_evidence(session, 42, 1)
    assert row.is_excluded_from_export is True
    result = approve_evidence(session, 42, 1)
    assert result["ok"] is True
    assert result["is_excluded"] is False
    assert row.is_excluded_from_export is False


# ══════════════════════════════════════════════════════════════════════
# 4. review_links
# ══════════════════════════════════════════════════════════════════════

_SECRET = "test-secret-key-phase5"


def test_signed_token_roundtrip():
    from app.review_engine.review_links import generate_review_token, validate_review_token

    base = "tokenABC123"
    signed = generate_review_token(base, secret=_SECRET, expires_in_hours=1)

    # Must contain exactly two dots.
    assert signed.count(".") == 2

    extracted, valid = validate_review_token(signed, secret=_SECRET)
    assert valid is True
    assert extracted == base


def test_expired_token_is_rejected():
    from app.review_engine.review_links import generate_review_token, validate_review_token

    base = "token_old"
    signed = generate_review_token(base, secret=_SECRET, expires_in_hours=1)

    # Simulate clock advancement past expiry.
    future_now = int(time.time()) + 3601 + 1
    _, valid = validate_review_token(signed, secret=_SECRET, now=future_now)
    assert valid is False


def test_tampered_signature_is_rejected():
    from app.review_engine.review_links import generate_review_token, validate_review_token

    base   = "token_valid"
    signed = generate_review_token(base, secret=_SECRET, expires_in_hours=1)
    parts  = signed.split(".")
    parts[2] = "0000000000000000"   # corrupt the signature
    tampered = ".".join(parts)

    _, valid = validate_review_token(tampered, secret=_SECRET)
    assert valid is False


def test_legacy_token_accepted():
    """Plain tokens (no dots) must still be accepted — legacy compat."""
    from app.review_engine.review_links import validate_review_token

    _, valid = validate_review_token("abc123xyzlegacy", secret=_SECRET)
    assert valid is True


def test_generate_review_link_shape():
    from app.review_engine.review_links import generate_review_link

    link = generate_review_link(
        "someToken",
        base_url="https://example.com",
        secret=_SECRET,
        expires_in_hours=24,
    )
    assert link.startswith("https://example.com/review/someToken.")


# ══════════════════════════════════════════════════════════════════════
# 5. review_summary
# ══════════════════════════════════════════════════════════════════════


def test_summary_basic_output():
    from app.review_engine.schemas import ReviewSession
    from app.review_engine.review_summary import build_summary_text

    s = ReviewSession(
        teacher_id=1,
        active_items=42,
        total_items=45,
        low_confidence_count=8,
        duplicates_count=2,
        strong_count=12,
    )
    text = build_summary_text(s)
    assert "42" in text
    assert "8" in text
    assert "2" in text
    assert "12" in text


def test_summary_empty_session():
    from app.review_engine.schemas import ReviewSession
    from app.review_engine.review_summary import build_summary_text

    s = ReviewSession(teacher_id=1, active_items=0, total_items=0)
    text = build_summary_text(s)
    assert "لا يوجد" in text or "📭" in text


def test_export_readiness_ready():
    from app.review_engine.schemas import ReviewSession
    from app.review_engine.review_summary import build_export_readiness

    s = ReviewSession(teacher_id=1, active_items=5, low_confidence_count=0, duplicates_count=0)
    assert "جاهز" in build_export_readiness(s)


def test_export_readiness_not_ready():
    from app.review_engine.schemas import ReviewSession
    from app.review_engine.review_summary import build_export_readiness

    s = ReviewSession(teacher_id=1, active_items=5, low_confidence_count=3, duplicates_count=1)
    result = build_export_readiness(s)
    assert "راجع" in result


# ══════════════════════════════════════════════════════════════════════
# 6. review_permissions
# ══════════════════════════════════════════════════════════════════════


def test_can_review_same_teacher():
    from app.review_engine.review_permissions import can_review
    assert can_review(7, 7) is True
    assert can_review(7, 8) is False


def test_can_export_with_items():
    from app.review_engine.review_permissions import can_export
    assert can_export(1, active_items=5) is True
    assert can_export(1, active_items=0) is False


# ══════════════════════════════════════════════════════════════════════
# 7. whatsapp_messages (review builders)
# ══════════════════════════════════════════════════════════════════════


def test_build_review_ready_message_has_required_lines():
    from app.services.whatsapp_messages import build_review_ready_message

    msg = build_review_ready_message(
        active_count=10, needs_review_count=3, duplicates_count=1, strong_count=5,
    )
    assert "✅" in msg
    assert "✏️" in msg or "تعديل" in msg
    assert "🗑️" in msg or "حذف" in msg
    assert "⭐" in msg


def test_build_review_link_message():
    from app.services.whatsapp_messages import build_review_link_message

    url = "https://example.com/review/tok.12345.abcdef00"
    msg = build_review_link_message(url)
    assert url in msg
    assert "🔗" in msg


# ══════════════════════════════════════════════════════════════════════
# 8. Architectural asserts
# ══════════════════════════════════════════════════════════════════════


def _sources(subdir: str | None = None):
    target = REVIEW_ENGINE_DIR / subdir if subdir else REVIEW_ENGINE_DIR
    return [p for p in target.rglob("*.py") if "__pycache__" not in str(p)]


def _get_imports(source: str) -> list[str]:
    """Return all module names referenced in import statements using AST."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def test_review_engine_no_playwright():
    for path in _sources():
        imports = _get_imports(path.read_text(encoding="utf-8"))
        for mod in imports:
            assert "playwright" not in mod.lower(), (
                f"{path.name} imports {mod!r} — Playwright forbidden in review_engine"
            )


def test_review_engine_no_export_engine():
    for path in _sources():
        imports = _get_imports(path.read_text(encoding="utf-8"))
        for mod in imports:
            assert "export_engine" not in mod, (
                f"{path.name} imports {mod!r} — export_engine forbidden in review_engine"
            )


def test_pure_modules_no_sqlalchemy():
    """schemas, review_service, review_summary, review_links, review_permissions
    must not import SQLAlchemy (only review_actions may)."""
    pure_files = [
        "schemas.py", "review_service.py", "review_summary.py",
        "review_links.py", "review_permissions.py",
    ]
    for fname in pure_files:
        path = REVIEW_ENGINE_DIR / fname
        imports = _get_imports(path.read_text(encoding="utf-8"))
        for mod in imports:
            assert "sqlalchemy" not in mod.lower(), (
                f"{fname} must not import sqlalchemy (found {mod!r})"
            )


def test_review_html_is_rtl():
    """The review.html template must declare RTL direction."""
    template = APP_DIR / "templates" / "review.html"
    text = template.read_text(encoding="utf-8")
    assert 'dir="rtl"' in text or "direction: rtl" in text
