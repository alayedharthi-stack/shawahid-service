"""Read-only local diagnostics. No resets, replays, sends, or profile output."""
import argparse
import json
import sqlite3
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--private-dir", required=True, type=Path)
args = p.parse_args()
path = (args.private_dir / "pilot.sqlite3").resolve()
if not path.is_file():
    p.error("Pilot database does not exist")
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
try:
    print(json.dumps({
        "jobs": [dict(r) for r in db.execute("SELECT id,state,created_at,error FROM jobs ORDER BY created_at")],
        "files": [dict(r) for r in db.execute("SELECT job,role,state,sha256,created_at,accepted_at,sent_at,delivered_at FROM deliveries ORDER BY job,role")],
        "classroom_use": "undocumented",
    }, ensure_ascii=False, indent=2))
finally:
    db.close()
