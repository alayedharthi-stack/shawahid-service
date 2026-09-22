import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from app.worksheet_pilot.service import Store, command, run_job, CONTENT
from app.worksheet_pilot.webhook import handle_payload, private_root
from app.document_templates.renderer import render_document, export_pdf

PHONE = "966500000001"  # synthetic, never contact
OTHER = "966500000002"


class FakeTransport:
    def __init__(self, fail=False):
        self.uploads = []
        self.sends = []
        self.fail = fail

    async def upload(self, data, filename):
        self.uploads.append((data, filename))
        return str(len(self.uploads))

    async def send(self, *args):
        self.sends.append(args)
        if self.fail:
            raise TimeoutError()
        return "wamid." + str(len(self.sends))


def fake_export(document, *, answers=None):
    return b"%PDF-" + (b"answers" if answers else b"student")


class PilotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "private")
        self.transport = FakeTransport()
        self.settings = SimpleNamespace(
            WORKSHEET_PILOT_ENABLED=True, WORKSHEET_PILOT_PHONE=PHONE,
            WORKSHEET_PILOT_VERIFICATION_REF="synthetic-test-only",
            WORKSHEET_PILOT_VERIFIED_AT="2026-09-22T00:00:00Z",
            WORKSHEET_PILOT_PRIVATE_DIR=str(self.store.root),
            WHATSAPP_APP_SECRET="synthetic-secret", WHATSAPP_PHONE_NUMBER_ID="synthetic-business",
            WHATSAPP_ACCESS_TOKEN="synthetic-token", storage_path=self.root / "public",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, text="ورقة المعادلات", mid="in-1", phone=PHONE):
        return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "synthetic-business"}, "messages": [{"id": mid, "from": phone, "type": "text", "text": {"body": text}}]}}]}]}

    async def ingest(self, body, signature=None):
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(b"synthetic-secret", raw, hashlib.sha256).hexdigest()
        tasks = BackgroundTasks()
        result = await handle_payload(body, raw, sig if signature is None else signature, tasks, self.settings)
        return result, tasks

    async def generate(self, mid="in-1", phone=PHONE, school=None, transport=None, exporter=fake_export):
        job = self.store.claim(phone, mid, "معلم تجريبي", school)
        await run_job(self.store, job, phone, transport or self.transport, exporter)
        return job

    async def test_signed_request_to_two_files_and_no_duplicate_after_restart(self):
        with patch("app.worksheet_pilot.webhook.MetaTransport", return_value=self.transport):
            _, tasks = await self.ingest(self.payload())
        self.assertEqual(len(tasks.tasks), 1)
        task = tasks.tasks[0]
        await run_job(*task.args, exporter=fake_export)
        _, repeated = await self.ingest(self.payload())
        self.assertEqual(len(repeated.tasks), 0)
        self.assertIsNone(Store(self.store.root).claim(PHONE, "in-1", "different"))
        self.assertEqual(len(self.transport.sends), 2)
        self.assertTrue(all(item[0] == PHONE for item in self.transport.sends))
        self.assertEqual({x[1] for x in self.transport.uploads}, {"equations-student.pdf", "equations-answers.pdf"})

    async def test_edit_school_regenerates_without_changing_questions(self):
        first = await self.generate()
        with patch("app.worksheet_pilot.webhook.MetaTransport", return_value=self.transport):
            _, tasks = await self.ingest(self.payload("مدرسة ورقة المعادلات: مدرسة التجربة", "in-2"))
        await run_job(*tasks.tasks[0].args, exporter=fake_export)
        second = tasks.tasks[0].args[1]
        a, b = (json.loads(self.store.job(j)["document"]) for j in (first, second))
        self.assertEqual(a["questions"], b["questions"])
        self.assertEqual(a["profile"]["school"], "")
        self.assertEqual(b["profile"]["school"], "مدرسة التجربة")
        self.assertEqual(len(self.transport.sends), 4)

    async def test_tenant_isolation_and_receipt_ownership(self):
        a = await self.generate(school="مدرسة أ")
        b = await self.generate(mid="in-1", phone=OTHER)
        self.assertNotEqual(a, b)
        self.assertEqual(json.loads(self.store.job(b)["document"])["profile"]["school"], "")
        self.store.receipt(OTHER, {"id": "wamid.1", "status": "delivered"})
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT state FROM deliveries WHERE message_id='wamid.1'").fetchone()[0], "accepted")

    async def test_states_are_distinct_and_monotonic(self):
        await self.generate()
        with self.store.db() as db:
            row = db.execute("SELECT * FROM deliveries WHERE message_id='wamid.1'").fetchone()
            self.assertTrue(row["created_at"])
            self.assertTrue(row["accepted_at"])
            self.assertIsNone(row["sent_at"])
            self.assertIsNone(row["delivered_at"])
        for status in ("sent", "delivered", "sent", "failed", "read"):
            self.store.receipt(PHONE, {"id": "wamid.1", "status": status})
        with self.store.db() as db:
            row = db.execute("SELECT * FROM deliveries WHERE message_id='wamid.1'").fetchone()
            self.assertEqual(row["state"], "delivered")
            self.assertTrue(row["delivered_at"])
            self.assertNotIn("classroom", row.keys())

    async def test_export_failure_sends_nothing_even_if_student_export_succeeds(self):
        def broken(document, *, answers=None):
            if answers:
                raise ValueError("layout")
            return fake_export(document)
        job = await self.generate(exporter=broken)
        self.assertEqual(self.store.job(job)["state"], "export_failed")
        self.assertEqual(self.transport.uploads, [])
        self.assertIsNone(self.store.claim(PHONE, "in-1", "x"))

    async def test_uncertain_send_never_retries_or_sends_key(self):
        transport = FakeTransport(fail=True)
        job = await self.generate(transport=transport)
        await run_job(self.store, job, PHONE, transport, fake_export)
        self.assertEqual(len(transport.sends), 1)
        self.assertEqual(self.store.job(job)["state"], "uncertain")
        self.assertIsNone(self.store.claim(PHONE, "new-id", "x"))

    async def test_concurrent_duplicate_claims_only_one(self):
        jobs = await asyncio.gather(*[asyncio.to_thread(self.store.claim, PHONE, "same", "x") for _ in range(8)])
        self.assertEqual(sum(j is not None for j in jobs), 1)

    async def test_disabled_preserves_payload_without_storage_or_tasks(self):
        self.settings.WORKSHEET_PILOT_ENABLED = False
        body = self.payload()
        result, tasks = await self.ingest(body, "bad")
        self.assertIs(result, body)
        self.assertEqual(tasks.tasks, [])

    async def test_no_verification_reference_fails_closed(self):
        self.settings.WORKSHEET_PILOT_VERIFICATION_REF = ""
        body = self.payload()
        result, tasks = await self.ingest(body)
        self.assertEqual(tasks.tasks, [])
        self.assertIs(result, body)

    async def test_wrong_recipient_or_business_cannot_generate(self):
        body = self.payload(phone=OTHER)
        result, tasks = await self.ingest(body, "not-needed-for-nonparticipant")
        self.assertFalse(tasks.tasks)
        self.assertEqual(result, body)
        body = self.payload()
        body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "foreign"
        result, tasks = await self.ingest(body)
        self.assertFalse(tasks.tasks)
        self.assertEqual(result, body)

    async def test_worker_rejects_foreign_recipient_without_mutating_job(self):
        job = self.store.claim(PHONE, "owned-by-a", "معلم أ")
        before = self.store.job(job)
        await run_job(self.store, job, OTHER, self.transport, fake_export)
        self.assertEqual(self.store.job(job), before)
        self.assertFalse(self.transport.uploads)
        self.assertFalse(self.transport.sends)
        self.assertFalse((self.store.root / "artifacts").exists())

    async def test_same_command_in_mixed_batch_preserves_nonparticipant(self):
        body = self.payload()
        nonparticipant = self.payload(phone=OTHER, mid="other-in")["entry"][0]["changes"][0]["value"]["messages"][0]
        body["entry"][0]["changes"][0]["value"]["messages"].insert(0, nonparticipant)
        result, tasks = await self.ingest(body)
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(result["entry"][0]["changes"][0]["value"]["messages"], [nonparticipant])

    async def test_bad_signature_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            await self.ingest(self.payload(), "sha256=bad")
        self.assertEqual(caught.exception.status_code, 403)

    async def test_meta_transport_uploads_private_bytes_and_uses_media_id(self):
        import httpx
        from app.worksheet_pilot.transport import MetaTransport
        self.settings.WHATSAPP_API_VERSION = "v20.0"
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"id": "media-1"} if request.url.path.endswith("/media") else {"messages": [{"id": "msg-1"}]})
        original_client = httpx.AsyncClient
        with patch("app.worksheet_pilot.transport.httpx.AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
            transport = MetaTransport(self.settings)
            media = await transport.upload(b"%PDF-private-test", "student.pdf")
            message = await transport.send(PHONE, media, "student.pdf", "ورقة الطالب")
        self.assertEqual(message, "msg-1")
        self.assertIn(b"%PDF-private-test", requests[0].content)
        sent = json.loads(requests[1].content)
        self.assertEqual(sent["to"], PHONE)
        self.assertEqual(sent["document"]["id"], "media-1")
        self.assertNotIn("link", sent["document"])

    async def test_missing_credentials_never_report_stub_success(self):
        from app.worksheet_pilot.transport import MetaTransport
        self.settings.WHATSAPP_ACCESS_TOKEN = ""
        with self.assertRaises(RuntimeError):
            await MetaTransport(self.settings).upload(b"%PDF", "x.pdf")

    async def test_legacy_message_preserved_in_mixed_batch(self):
        body = self.payload()
        legacy = {"id": "legacy", "from": OTHER, "type": "text", "text": {"body": "مرحبا"}}
        body["entry"][0]["changes"][0]["value"]["messages"].append(legacy)
        result, tasks = await self.ingest(body)
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(result["entry"][0]["changes"][0]["value"]["messages"], [legacy])

    def test_public_storage_rejected(self):
        self.settings.WORKSHEET_PILOT_PRIVATE_DIR = str(self.settings.storage_path / "answers")
        with self.assertRaises(ValueError):
            private_root(self.settings)

    def test_original_content_and_answer_separation(self):
        d = json.loads((CONTENT / "worksheet.json").read_text(encoding="utf-8"))
        answers = json.loads((CONTENT / "answers.json").read_text(encoding="utf-8"))
        self.assertEqual(len(d["questions"]), 8)
        self.assertFalse(d["show_marks"])
        self.assertTrue(all("marks" not in q for q in d["questions"]))
        student = render_document(d)
        for answer in answers.values():
            self.assertNotIn(answer["text"], student)
        self.assertIn("<math", student)
        self.assertIn("<table", student)
        self.assertIn("data:image/png;base64,", student)

    @unittest.skipUnless(os.environ.get("SHAWAHID_PDF_TESTS") == "1", "explicit real PDF test")
    async def test_real_pdf_pair_and_private_artifacts(self):
        import fitz
        job = await self.generate(exporter=export_pdf)
        self.assertEqual(self.store.job(job)["state"], "accepted")
        for data, filename in self.transport.uploads:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                self.assertEqual(len(pdf), 1)
                self.assertGreater(len(pdf[0].get_text()), 400)
                if filename == "equations-student.pdf":
                    self.assertGreater(len(pdf[0].get_images()), 0)
        self.assertNotEqual(self.transport.uploads[0][0], self.transport.uploads[1][0])

    async def test_receipt_arriving_before_send_response_is_reconciled(self):
        self.store.receipt(PHONE, {"id": "wamid.1", "status": "delivered"})
        await self.generate()
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT state FROM deliveries WHERE message_id='wamid.1'").fetchone()[0], "delivered")

    @unittest.skipUnless(os.environ.get("SHAWAHID_PDF_TESTS") == "1", "explicit real HTTP/PDF test")
    def test_real_http_webhook_to_pdf_and_status_receipt(self):
        from fastapi.testclient import TestClient
        # Import service with isolated storage and no production credentials.
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "STORAGE_DIR": str(self.root / "public"), "OPENAI_API_KEY": "", "WHATSAPP_ACCESS_TOKEN": ""}):
            from app.main import app
            from app.core.config import settings
        values = {k: v for k, v in vars(self.settings).items() if k != "storage_path"}
        with patch.multiple(settings, **values), patch("app.worksheet_pilot.webhook.MetaTransport", return_value=self.transport):
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").json()["status"], "healthy")
                body = self.payload(mid="http-1")
                raw = json.dumps(body).encode()
                headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"synthetic-secret", raw, hashlib.sha256).hexdigest()}
                self.assertEqual(client.post("/webhook/whatsapp", content=raw, headers=headers).status_code, 200)
                self.assertEqual(len(self.transport.sends), 2)
                self.assertEqual(client.post("/webhook/whatsapp", content=raw, headers=headers).status_code, 200)
                self.assertEqual(len(self.transport.sends), 2)
                self.assertEqual(client.get("/files/../private/pilot.sqlite3").status_code, 404)
                value = body["entry"][0]["changes"][0]["value"]
                value.pop("messages")
                value["statuses"] = [{"id": "wamid.1", "status": "delivered", "recipient_id": PHONE}]
                raw = json.dumps(body).encode()
                headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"synthetic-secret", raw, hashlib.sha256).hexdigest()
                self.assertEqual(client.post("/webhook/whatsapp", content=raw, headers=headers).status_code, 200)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT state FROM deliveries WHERE message_id='wamid.1'").fetchone()[0], "delivered")


if __name__ == "__main__":
    unittest.main()
