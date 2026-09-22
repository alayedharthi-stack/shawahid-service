"""Original-file provenance and isolated PostgreSQL upgrade checks.

Never accepts a production URL. No stamp or version-table writes.
"""
import hashlib
import os
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ORIGINAL_SHA256 = "a297e750a65898afd81aa21f36fbb3286c99cbf10c984cee5512226152481100"
MIGRATION = Path("alembic/versions/011_teacher_knowledge_question_bank.py")
NEW_TABLES = {"teacher_knowledge_profiles", "question_banks", "bank_questions", "exam_records"}


class OriginalMigrationTests(unittest.TestCase):
    def test_original_bytes(self):
        self.assertEqual(hashlib.sha256(MIGRATION.read_bytes()).hexdigest(), ORIGINAL_SHA256)

    def test_complete_linear_dependency_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(scripts.get_heads(), ["011"])
        chain = list(scripts.walk_revisions())
        self.assertEqual([r.revision for r in chain], [f"{n:03d}" for n in range(11, 0, -1)])
        self.assertEqual(chain[0].down_revision, "010")
        self.assertIsNone(chain[0].dependencies)
        self.assertFalse(chain[0].branch_labels)

    @unittest.skipUnless(os.environ.get("SHAWAHID_MIGRATION_TESTS") == "1", "isolated PostgreSQL required")
    def test_fresh_database_upgrade_and_existing_011_restart(self):
        url = make_url(os.environ["DATABASE_URL"])
        if url.host not in {"127.0.0.1", "localhost"} or url.database != "shawahid_migration_test":
            self.fail("Only the disposable local migration-test database is allowed")
        engine = create_engine(url)
        self.assertEqual(inspect(engine).get_table_names(), [], "refuse nonempty database")
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "010")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO teachers (id,phone,name) VALUES (1,'966500000001','Synthetic only')"))
        command.upgrade(cfg, "head")
        self.assertTrue(NEW_TABLES <= set(inspect(engine).get_table_names()))
        with engine.begin() as conn:
            self.assertEqual(conn.scalar(text("SELECT version_num FROM alembic_version")), "011")
            self.assertEqual(conn.scalar(text("SELECT name FROM teachers WHERE id=1")), "Synthetic only")
            conn.execute(text("INSERT INTO teacher_knowledge_profiles (teacher_id,cliche_json) VALUES (1,'{\"school\":\"Synthetic\"}')"))
            conn.execute(text("INSERT INTO question_banks (id,teacher_id,name,source_type) VALUES (1,1,'Synthetic bank','original')"))
            conn.execute(text("INSERT INTO bank_questions (bank_id,teacher_id,question_text,question_type,model_answer) VALUES (1,1,'x+1=2','short_answer','1')"))
            conn.execute(text("INSERT INTO exam_records (teacher_id,exam_id,structured_json) VALUES (1,'synthetic-exam','{}')"))
        before = {table: inspect(engine).get_columns(table) for table in NEW_TABLES}
        # The deployed database already reports 011: a new process must resolve
        # it and perform no re-creation, destructive rollback, or metadata reset.
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            for table in NEW_TABLES:
                self.assertEqual(conn.scalar(text(f"SELECT COUNT(*) FROM {table}")), 1)
            self.assertEqual(conn.scalar(text("SELECT version_num FROM alembic_version")), "011")
            self.assertEqual(conn.scalar(text("SELECT model_answer FROM bank_questions")), "1")
        self.assertEqual({t: [c['name'] for c in inspect(engine).get_columns(t)] for t in NEW_TABLES},
                         {t: [c['name'] for c in cols] for t, cols in before.items()})
        engine.dispose()
