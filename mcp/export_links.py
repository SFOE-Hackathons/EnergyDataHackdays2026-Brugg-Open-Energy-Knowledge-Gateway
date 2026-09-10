from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DOWNLOAD_URL = "https://pubdb.bfe.admin.ch/de/publication/download/{}"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def build_catalog(index_path: Path) -> list[dict[str, Any]]:
    index = read_json(index_path, {})
    links = []
    for publication_id, entry in index.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("content_type") != "application/pdf":
            continue
        links.append(
            {
                "pubdb_id": int(publication_id),
                "pdf_url": DOWNLOAD_URL.format(publication_id),
                "filename": entry.get("filename", ""),
                "last_modified": entry.get("last_modified", ""),
                "size": entry.get("size", 0),
            }
        )
    links.sort(key=lambda item: item["pubdb_id"])
    return links


def build_gateway(keys_path: Path, map_path: Path) -> list[dict[str, Any]]:
    keys = read_json(keys_path, [])
    source_map = read_json(map_path, {})
    links = []
    for key in keys:
        mapping = source_map.get(key, {}) if isinstance(source_map, dict) else {}
        links.append(
            {
                "s3_key": key,
                "pdf_url": mapping.get("pdf_url") if isinstance(mapping, dict) else None,
                "confidence": "verified" if isinstance(mapping, dict) and mapping.get("pdf_url") else "unresolved",
                "pubdb_id": mapping.get("pubdb_id") if isinstance(mapping, dict) else None,
                "candidate_filename": mapping.get("candidate_filename") if isinstance(mapping, dict) else None,
            }
        )
    return links


def main() -> None:
    parser = argparse.ArgumentParser(description="Export BFE PDF links into JSON files")
    parser.add_argument("--index", type=Path, default=Path(__file__).with_name("pubdb_index.json"))
    parser.add_argument("--map", type=Path, default=Path(__file__).with_name("source_map.json"))
    parser.add_argument("--keys", type=Path, default=Path(__file__).with_name("keys.json"))
    parser.add_argument("--catalog-output", type=Path, default=Path(__file__).with_name("bfe_publication_links.json"))
    parser.add_argument("--gateway-output", type=Path, default=Path(__file__).with_name("gateway_source_links.json"))
    parser.add_argument("--all-output", type=Path, default=Path(__file__).with_name("all_source_links.json"))
    args = parser.parse_args()

    catalog = build_catalog(args.index)
    gateway = build_gateway(args.keys, args.map)
    args.catalog_output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.gateway_output.write_text(json.dumps(gateway, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.all_output.write_text(json.dumps({"all_bfe_pdf_links": catalog, "gateway_documents": gateway}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(catalog)} BFE PDF links to {args.catalog_output}")
    print(f"Exported {len(gateway)} Gateway document links to {args.gateway_output}")
    print(f"Exported combined links to {args.all_output}")


if __name__ == "__main__":
    main()
