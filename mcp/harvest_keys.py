#!/usr/bin/env python3
"""Harvest S3 object keys from Gateway retrieval results.

The document bucket is private and cannot be listed, so the only way to learn
which objects exist is to ask the Knowledge Base and read the documentId of
every chunk it returns. Coverage grows with the breadth of the query set.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ADAPTER = Path(__file__).with_name("bfe_mcp_proxy.py")
DEFAULT_KEYS_PATH = Path(__file__).with_name("keys.txt")
BUCKET_PREFIX = "s3://sandbox-bfe-public-data-pdf/"

QUERIES: tuple[str, ...] = (
    "Wasserkraft Schweiz Stromversorgung",
    "Photovoltaik Einmalvergütung Förderung",
    "Windenergie Ausbau Bewilligung",
    "Energiestrategie 2050 Ziele",
    "Geothermie Projekte Schweiz",
    "Wasserstoff Mobilität Energiepolitik",
    "Kernenergie Rückbau Entsorgung",
    "Gebäudeprogramm energetische Sanierung",
    "Stromnetz Ausbau Netzentgelte",
    "Energieforschung Innovation Bericht",
    "Biomasse Biogas Holzenergie",
    "Elektromobilität Ladeinfrastruktur",
    "Fernwärme Wärmenetze Planung",
    "Energieeffizienz Geräte Vorschriften",
    "Speicher Batterien Netzstabilität",
    "Solarenergie Alpine Anlagen",
    "Versorgungssicherheit Winter Strommangellage",
    "CO2 Emissionen Klimaziele Energie",
    "Wärmepumpen Marktentwicklung",
    "Smart Meter Digitalisierung Energie",
    "efficacité énergétique des bâtiments",
    "énergies renouvelables objectifs suisses",
    "hydraulique force production électricité",
    "energy research programme Switzerland",
    "renewable electricity targets Switzerland",
    "efficienza energetica edifici Svizzera",
)


def harvest(queries: Sequence[str], timeout: float = 900.0) -> set[str]:
    """Run every query through the adapter and collect distinct object keys."""
    requests = "\n".join(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": position,
                "method": "tools/call",
                "params": {
                    "name": "bfe-public-knowledge___Retrieve",
                    "arguments": {"retrievalQuery": {"text": query}},
                },
            }
        )
        for position, query in enumerate(queries, start=1)
    )

    completed = subprocess.run(
        [sys.executable, str(ADAPTER)],
        input=requests + "\n",
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=timeout,
    )

    keys: set[str] = set()
    for line in completed.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        for item in (message.get("result") or {}).get("content", []):
            try:
                payload = json.loads(item.get("text", ""))
            except ValueError:
                continue
            for entry in payload.get("retrievalResults", []):
                document_id = entry.get("documentId", "")
                if document_id.startswith(BUCKET_PREFIX):
                    keys.add(document_id[len(BUCKET_PREFIX) :])
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_KEYS_PATH)
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Add to the existing keys file instead of replacing it.",
    )
    args = parser.parse_args()

    keys = harvest(QUERIES)
    if args.merge and args.out.exists():
        previous = {
            line.strip()
            for line in args.out.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        print(f"merging with {len(previous)} existing keys")
        keys |= previous

    args.out.write_text("\n".join(sorted(keys)) + "\n", encoding="utf-8")
    print(f"wrote {len(keys)} keys to {args.out}")


if __name__ == "__main__":
    main()
