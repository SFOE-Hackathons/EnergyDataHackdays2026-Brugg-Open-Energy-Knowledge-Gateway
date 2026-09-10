from __future__ import annotations

import argparse
import io
import email.utils
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pypdf import PdfReader

DOWNLOAD_URL = "https://pubdb.bfe.admin.ch/de/publication/download/{}"
DATE_PATTERN = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def normalize(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return set(TOKEN_PATTERN.findall(folded.replace("_", " ").replace("-", " ")))


def read_keys(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [str(item) for item in value.get("keys", [])]
    raise ValueError("Keys file must contain a JSON list or {\"keys\": [...]}")


def document_date(key: str) -> str | None:
    match = DATE_PATTERN.search(Path(key).name)
    return "".join(match.groups()) if match else None


def entry_date(filename: str) -> str | None:
    match = re.search(r"20\d{2}[-_.]?\d{2}[-_.]?\d{2}", filename)
    return match.group(0).replace("-", "").replace("_", "").replace(".", "") if match else None


def entry_datetime(entry: dict[str, object]) -> datetime | None:
    value = entry.get("last_modified")
    if not isinstance(value, str):
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def score(key: str, filename: str) -> float:
    source_tokens = normalize(Path(key).stem)
    candidate_tokens = normalize(Path(filename).stem)
    if not source_tokens or not candidate_tokens:
        return 0.0
    return len(source_tokens & candidate_tokens) / len(source_tokens | candidate_tokens)


def content_score(key: str, filename: str, first_page: str) -> float:
    source_tokens = normalize(Path(key).stem)
    candidate_tokens = normalize(f"{filename} {first_page}")
    if not source_tokens:
        return 0.0
    return len(source_tokens & candidate_tokens) / len(source_tokens)


def first_page_text(publication_id: str) -> str:
    request = Request(
        DOWNLOAD_URL.format(publication_id),
        headers={"User-Agent": "Open-Energy-Knowledge-Gateway/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        reader = PdfReader(io.BytesIO(response.read()))
        if not reader.pages:
            return ""
        return reader.pages[0].extract_text() or ""


def verify_pdf(publication_id: str) -> bool:
    request = Request(
        DOWNLOAD_URL.format(publication_id),
        headers={"User-Agent": "Open-Energy-Knowledge-Gateway/1.0"},
        method="HEAD",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status == 200 and response.headers.get_content_type() == "application/pdf"
    except (HTTPError, URLError, TimeoutError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Build verified S3-to-BFE publication links")
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=Path(__file__).with_name("pubdb_index.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("source_map.json"))
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N keys")
    parser.add_argument("--min-score", type=float, default=0.2, help="Accept the best candidate at or above this score")
    args = parser.parse_args()

    keys = read_keys(args.keys)
    if args.limit > 0:
        keys = keys[:args.limit]
    index = json.loads(args.index.read_text(encoding="utf-8"))
    source_map: dict[str, dict[str, object]] = {}
    for key in keys:
        wanted_date = document_date(key)
        exact_candidates = [
            (publication_id, entry)
            for publication_id, entry in index.items()
            if isinstance(entry, dict)
            and entry.get("content_type") == "application/pdf"
            and wanted_date
            and entry_date(str(entry.get("filename", ""))) == wanted_date
        ]
        candidates = exact_candidates
        match_type = "date+filename"
        if not candidates and wanted_date:
            target = datetime.strptime(wanted_date, "%Y%m%d")
            candidates = [
                (publication_id, entry)
                for publication_id, entry in index.items()
                if isinstance(entry, dict)
                and entry.get("content_type") == "application/pdf"
                and entry_datetime(entry) is not None
                and abs(entry_datetime(entry) - target) <= timedelta(days=14)
            ]
            match_type = "mtime+filename"
        if not candidates:
            continue
        scored = []
        for candidate_id, candidate_entry in candidates:
            try:
                page_text = first_page_text(str(candidate_id))
            except Exception:
                page_text = ""
            scored.append(
                (
                    content_score(key, str(candidate_entry.get("filename", "")), page_text),
                    candidate_id,
                    candidate_entry,
                )
            )
        ranked = sorted(scored, reverse=True, key=lambda item: item[0])
        if not ranked or ranked[0][0] < args.min_score:
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.10:
            continue
        best_score, best_id, best_entry = ranked[0]
        if not verify_pdf(str(best_id)):
            continue
        source_map[Path(key).name] = {
            "pdf_url": DOWNLOAD_URL.format(best_id),
            "pubdb_id": int(best_id),
            "candidate_filename": best_entry.get("filename", ""),
            "match": match_type if len(ranked) == 1 else "best-of-candidates",
            "candidate_count": len(ranked),
            "score": round(best_score, 4),
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

    args.output.write_text(json.dumps(source_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Mapped {len(source_map)} of {len(keys)} documents to verified PDF URLs")


if __name__ == "__main__":
    main()
