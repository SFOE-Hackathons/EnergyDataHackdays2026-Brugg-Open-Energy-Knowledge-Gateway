from __future__ import annotations

import argparse
import email.message
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

BASE_URL = "https://pubdb.bfe.admin.ch/de/publication/download/{}"
USER_AGENT = "Open-Energy-Knowledge-Gateway/1.0 publication-indexer"
DATE_PATTERN = re.compile(r"20\d{6}")


def filename_from_headers(headers: email.message.Message) -> str:
    value = headers.get("Content-Disposition", "")
    parsed = email.message.Message()
    parsed["Content-Disposition"] = value
    filename = parsed.get_param("filename", header="Content-Disposition")
    if filename:
        return unquote(filename.strip('"'))
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
    return unquote(match.group(1)) if match else ""


def probe(publication_id: int, delay: float) -> dict[str, object] | None:
    if delay:
        time.sleep(delay)
    request = Request(
        BASE_URL.format(publication_id),
        headers={"User-Agent": USER_AGENT},
        method="HEAD",
    )
    try:
        with urlopen(request, timeout=20) as response:
            content_type = response.headers.get_content_type()
            filename = filename_from_headers(response.headers)
            if response.status != 200 or not filename:
                return None
            return {
                "filename": filename,
                "content_type": content_type,
                "last_modified": response.headers.get("Last-Modified", ""),
                "size": int(response.headers.get("Content-Length", "0") or 0),
            }
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def load_index(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Index official BFE publication URLs")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("pubdb_index.json"))
    parser.add_argument("--start", type=int, default=1000)
    parser.add_argument("--end", type=int, default=13000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.05)
    args = parser.parse_args()

    index = load_index(args.output)
    missing = [publication_id for publication_id in range(args.start, args.end + 1) if str(publication_id) not in index]
    print(f"Existing: {len(index)}; probing: {len(missing)}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(probe, publication_id, args.delay): publication_id for publication_id in missing}
        for position, future in enumerate(as_completed(futures), start=1):
            publication_id = futures[future]
            value = future.result()
            if value is not None:
                index[str(publication_id)] = value
            if position % 100 == 0:
                print(f"Checked {position}/{len(missing)}", file=sys.stderr)

    args.output.write_text(json.dumps(dict(sorted(index.items(), key=lambda item: int(item[0]))), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {len(index)} publications to {args.output}")


if __name__ == "__main__":
    main()
