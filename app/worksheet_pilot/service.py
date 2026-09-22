"""Durable at-most-once pilot; uncertain sends require operator reconciliation.

SQLite and documents MUST live outside every web-served directory. This small
pilot supports one service replica and a persistent private local volume only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.document_templates.renderer import export_pdf, render_document

CONTENT = Path(__file__).parent / "content"
logger = logging.getLogger(__name__)
REQUEST = "ورقة المعادلات"
SCHOOL = "مدرسة ورقة المعادلات:"


def command(text):
    text = text.strip()
    if text == REQUEST:
        return ("generate", None)
    if text.startswith(SCHOOL):
        school = text[len(SCHOOL):].strip()
        if school and len(school) <= 180 and not any(ord(c) < 32 for c in school):
            return ("generate", school)
    return None


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profiles(owner TEXT PRIMARY KEY, school TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS deliveries(
                    job TEXT NOT NULL, role TEXT NOT NULL, state TEXT NOT NULL,
                    media_id TEXT, message_id TEXT UNIQUE, sha256 TEXT,
                    created_at TEXT, accepted_at TEXT, sent_at TEXT, delivered_at TEXT,
                    PRIMARY KEY(job, role));
                CREATE TABLE IF NOT EXISTS receipts(
                    owner TEXT NOT NULL, message_id TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(owner, message_id));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / "pilot.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, phone, message_id, teacher_name, school=None):
        owner = hashlib.sha256(phone.encode()).hexdigest()
        job = hashlib.sha256((phone + "\0" + message_id).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (job,)).fetchone():
                return None
            # Do not overlap jobs for this teacher, including unresolved sends.
            busy = db.execute("SELECT 1 FROM jobs WHERE owner=? AND state IN ('queued','working','uncertain')", (owner,)).fetchone()
            d = json.loads((CONTENT / "worksheet.json").read_text(encoding="utf-8"))
            profile = db.execute("SELECT school FROM profiles WHERE owner=?", (owner,)).fetchone()
            d["profile"]["teacher"] = teacher_name
            d["profile"]["school"] = school if school is not None else (profile[0] if profile else "")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (job, owner, json.dumps(d, ensure_ascii=False), "blocked" if busy else "queued", now(), "owner_busy" if busy else None))
            if busy:
                return None
            if school is not None:
                db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?)", (owner, school))
        return job

    def job(self, job):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            return dict(row) if row else None

    def state(self, job, state, error=None):
        with self.db() as db:
            db.execute("UPDATE jobs SET state=?,error=? WHERE id=?", (state, error, job))
        logger.info("[WORKSHEET JOB] job=%s state=%s", job, state)

    def receipt(self, phone, status):
        owner = hashlib.sha256(phone.encode()).hexdigest()
        state = status.get("status")
        if state not in {"sent", "delivered", "read", "failed"}:
            return
        with self.db() as db:
            # A signed receipt can race the POST /messages response. Retain it
            # so acceptance recording can reconcile it without losing delivery.
            previous = db.execute("SELECT state FROM receipts WHERE owner=? AND message_id=?", (owner, status.get("id"))).fetchone()
            if not previous or previous[0] not in {"delivered", "read"}:
                db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?,?)", (owner, status.get("id"), state))
            row = db.execute("SELECT d.* FROM deliveries d JOIN jobs j ON j.id=d.job WHERE d.message_id=? AND j.owner=?", (status.get("id"), owner)).fetchone()
            if not row:
                return
            # Receipt time is local observation, not a claimed provider timestamp.
            if state in {"delivered", "read"}:
                db.execute("UPDATE deliveries SET state='delivered',sent_at=COALESCE(sent_at,?),delivered_at=COALESCE(delivered_at,?) WHERE message_id=?", (now(), now(), status["id"]))
            elif state == "sent" and not row["delivered_at"]:
                db.execute("UPDATE deliveries SET state='sent',sent_at=COALESCE(sent_at,?) WHERE message_id=?", (now(), status["id"]))
            elif state == "failed" and not row["delivered_at"]:
                db.execute("UPDATE deliveries SET state='failed' WHERE message_id=?", (status["id"],))
            logger.info("[WORKSHEET RECEIPT] job=%s role=%s observed=%s", row["job"], row["role"], state)


async def run_job(store, job, phone, transport, exporter=export_pdf):
    # Ownership belongs at the worker boundary too, not only at ingress.
    owner = hashlib.sha256(phone.encode()).hexdigest()
    with store.db() as db:
        if db.execute("UPDATE jobs SET state='working' WHERE id=? AND owner=? AND state='queued'", (job, owner)).rowcount != 1:
            return
    d = json.loads(store.job(job)["document"])
    answers = json.loads((CONTENT / "answers.json").read_text(encoding="utf-8"))
    folder = store.root / "artifacts" / job
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # Finish BOTH exports before any external upload/send. Blocking Chromium
        # runs on a thread, never inside the webhook's asyncio event loop.
        student = await asyncio.to_thread(exporter, d)
        answer = await asyncio.to_thread(exporter, d, answers=answers)
        for role, data in (("student", student), ("answers", answer)):
            if not data.startswith(b"%PDF-"):
                raise ValueError("invalid_pdf")
            (folder / f"{role}.pdf").write_bytes(data)
            with store.db() as db:
                db.execute("INSERT INTO deliveries(job,role,state,sha256,created_at) VALUES (?,?,'created',?,?)", (job, role, hashlib.sha256(data).hexdigest(), now()))
        (folder / "worksheet.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "student.html").write_text(render_document(d), encoding="utf-8")
    except Exception:
        store.state(job, "export_failed", "export_failed")
        return
    for role in ("student", "answers"):
        try:
            data = (folder / f"{role}.pdf").read_bytes()
            media_id = await transport.upload(data, f"equations-{role}.pdf")
            # Persist BEFORE the network call. On timeout or process death there
            # is no safe automatic retry: Meta may have already accepted it.
            with store.db() as db:
                db.execute("UPDATE deliveries SET state='sending',media_id=? WHERE job=? AND role=?", (media_id, job, role))
            message_id = await transport.send(phone, media_id, f"equations-{role}.pdf", "ورقة الطالب" if role == "student" else "نموذج الإجابة — للمعلم فقط")
            with store.db() as db:
                db.execute("UPDATE deliveries SET state='accepted',message_id=?,accepted_at=? WHERE job=? AND role=?", (message_id, now(), job, role))
                receipt = db.execute("SELECT state FROM receipts WHERE owner=? AND message_id=?", (store.job(job)["owner"], message_id)).fetchone()
            if receipt:
                store.receipt(phone, {"id": message_id, "status": receipt[0]})
        except Exception:
            store.state(job, "uncertain", "delivery_requires_reconciliation")
            return
    store.state(job, "accepted")
