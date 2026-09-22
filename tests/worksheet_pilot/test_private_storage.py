import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.private_storage import PublicStorageFiles, worksheet_private_root, PRIVATE_SUBDIR
from app.worksheet_pilot.service import Store


class PrivateStorageTests(unittest.TestCase):
    def test_public_files_work_but_private_pdfs_and_database_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / PRIVATE_SUBDIR
            private.mkdir()
            (root / "public.pdf").write_bytes(b"%PDF-public")
            (private / "answers.pdf").write_bytes(b"%PDF-secret")
            Store(private).claim("966500000001", "synthetic", "Teacher")
            app = FastAPI()
            app.mount("/files", PublicStorageFiles(directory=root))
            with TestClient(app) as client:
                self.assertEqual(client.get("/files/public.pdf").status_code, 200)
                for path in (".worksheet-private/answers.pdf", ".worksheet-private/pilot.sqlite3",
                             "%2eworksheet-private/answers.pdf", "foo/../.worksheet-private/answers.pdf"):
                    response = client.get("/files/" + path)
                    self.assertEqual(response.status_code, 404, path)
                    self.assertNotIn(b"secret", response.content)
            self.assertIsNone(Store(private).claim("966500000001", "synthetic", "Teacher"))

    def test_public_alias_into_private_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / PRIVATE_SUBDIR
            private.mkdir()
            (private / "answers.pdf").write_bytes(b"%PDF-secret")
            try:
                (root / "alias").symlink_to(private, target_is_directory=True)
            except OSError:
                self.skipTest("symlink privilege unavailable; Linux CI exercises this")
            self.assertEqual(PublicStorageFiles(directory=root).lookup_path("alias/answers.pdf"), ("", None))

    def test_production_requires_real_volume_and_reserved_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = SimpleNamespace(storage_path=root, APP_ENV="production",
                                       WORKSHEET_PILOT_PRIVATE_DIR=str(root / PRIVATE_SUBDIR))
            with self.assertRaisesRegex(ValueError, "persistent_volume"):
                worksheet_private_root(settings)
            with patch.object(Path, "is_mount", return_value=True):
                self.assertEqual(worksheet_private_root(settings), root.resolve() / PRIVATE_SUBDIR)
                settings.WORKSHEET_PILOT_PRIVATE_DIR = str(root / "teachers")
                with self.assertRaises(ValueError):
                    worksheet_private_root(settings)
