"""Offline message-to-PDF review artifact. Never constructs a Meta client."""
import argparse
import asyncio
import hashlib
import hmac
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import BackgroundTasks
from app.worksheet_pilot.webhook import handle_payload


async def simulate(out):
    out.mkdir(parents=True, exist_ok=True)
    if (out / "private").exists():
        raise ValueError("Use a new output directory; preserve previous trial records")
    phone = "966500000001"  # synthetic only
    s = SimpleNamespace(WORKSHEET_PILOT_ENABLED=True, WORKSHEET_PILOT_PHONE=phone,
        WORKSHEET_PILOT_VERIFICATION_REF="local-simulation-only",
        WORKSHEET_PILOT_VERIFIED_AT="2026-09-22T00:00:00Z",
        WORKSHEET_PILOT_PRIVATE_DIR=str(out / "private"), storage_path=out / "public",
        WHATSAPP_APP_SECRET="simulation", WHATSAPP_ACCESS_TOKEN="simulation",
        WHATSAPP_PHONE_NUMBER_ID="simulation")
    calls = []

    class SimulatedTransport:
        async def upload(self, data, filename):
            return hashlib.sha256(data).hexdigest()

        async def send(self, recipient, media, filename, caption):
            assert recipient == phone
            calls.append({"filename": filename, "sha256": media})
            return "simulated-" + str(len(calls))

    for index, text in enumerate(("ورقة المعادلات", "مدرسة ورقة المعادلات: مدرسة تجريبية للمراجعة"), 1):
        body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "simulation"}, "messages": [{"id": f"simulation-{index}",
            "from": phone, "type": "text", "text": {"body": text}}]}}]}]}
        raw = json.dumps(body).encode()
        signature = "sha256=" + hmac.new(b"simulation", raw, hashlib.sha256).hexdigest()
        tasks = BackgroundTasks()
        with patch("app.worksheet_pilot.webhook.MetaTransport", return_value=SimulatedTransport()):
            await handle_payload(body, raw, signature, tasks, s)
        await tasks()
        job = tasks.tasks[0].args[1]
        folder = out / "private" / "artifacts" / job
        target = out / ("initial" if index == 1 else "school-change-synthetic")
        shutil.copytree(folder, target)
        import fitz
        for pdf_path in target.glob("*.pdf"):
            with fitz.open(pdf_path) as pdf:
                assert len(pdf) == 1
                pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(pdf_path.with_suffix(".png")))
    (out / "simulation.json").write_text(json.dumps({"mode": "OFFLINE_SIMULATION", "real_whatsapp_sent": False,
        "classroom_use": "undocumented", "simulated_calls": calls}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(repo):
        p.error("Output must be outside the public repository")
    asyncio.run(simulate(args.output.resolve()))
