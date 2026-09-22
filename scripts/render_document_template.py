"""Render an opt-in template fixture; no service state or network requests.

python scripts/render_document_template.py examples/documents/v1/worksheet.json \
  --answers examples/documents/v1/worksheet.answers.json --output-dir preview
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.document_templates import render_document, export_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--html-only", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.document.read_text(encoding="utf-8"))
    variants = [(args.document.stem, None)]
    if args.answers:
        variants.append((args.document.stem + "-answers", json.loads(args.answers.read_text(encoding="utf-8"))))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, answers in variants:
        (args.output_dir / (name + ".html")).write_text(render_document(document, answers=answers), encoding="utf-8")
        if not args.html_only:
            (args.output_dir / (name + ".pdf")).write_bytes(export_pdf(document, answers=answers))
        print(name)


if __name__ == "__main__":
    main()
