from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"Could not read JSON from {path}")


def extract_document_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return extract_document_ids(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return set()
    if isinstance(value, dict):
        ids = set()
        document_id = value.get("documentId")
        if isinstance(document_id, str) and document_id.startswith("s3://"):
            ids.add(document_id.removeprefix("s3://sandbox-bfe-public-data-pdf/"))
        for child in value.values():
            ids.update(extract_document_ids(child))
        return ids
    if isinstance(value, list):
        ids = set()
        for child in value:
            ids.update(extract_document_ids(child))
        return ids
    return set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract S3 document keys from a saved Gateway response")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("keys.json"))
    args = parser.parse_args()
    existing: set[str] = set()
    if args.output.exists():
        try:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            if isinstance(previous, list):
                existing = {str(item) for item in previous}
        except (OSError, json.JSONDecodeError):
            existing = set()
    keys = sorted(existing | extract_document_ids(load_json(args.input)))
    args.output.write_text(json.dumps(keys, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {len(keys)} document keys to {args.output}")


if __name__ == "__main__":
    main()
