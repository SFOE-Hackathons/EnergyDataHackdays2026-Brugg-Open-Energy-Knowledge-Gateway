#!/usr/bin/env python3
"""Join S3 object keys to pubdb publications and emit source_map.json.

The join key is the publication date, which appears both as the S3 key's prefix
and inside the pubdb filename. Where a date yields several candidates the choice
is made on the PDF's own content: the index filenames lost their umlauts to
underscores, so filename similarity alone is far too weak to decide.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import certifi
from pypdf import PdfReader

from source_match import S3Key, coverage, deslugify, parse_s3_key

DOWNLOAD_URL = "https://pubdb.bfe.admin.ch/de/publication/download/{}"
USER_AGENT = "Open-Energy-Knowledge-Gateway/1.0 source-map-builder"
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
DEFAULT_INDEX_PATH = Path(__file__).with_name("pubdb_index.json")
DEFAULT_KEYS_PATH = Path(__file__).with_name("keys.txt")
DEFAULT_MAP_PATH = Path(__file__).with_name("source_map.json")
# Candidates from an exact filename-date match are a small, trustworthy set.
CONTENT_THRESHOLD = 0.5
# The Last-Modified window yields up to 198 candidates, so demand that every
# title token appears in the PDF before believing the match.
MTIME_CONTENT_THRESHOLD = 1.0
MTIME_WINDOWS_DAYS = (14, 90)

# pubdb filenames carry dates in several layouts; all of these occur.
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)"), "dmy"),
    (re.compile(r"(?<!\d)(20\d{2})[.\-_](\d{2})[.\-_](\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](20\d{2})(?!\d)"), "dmy"),
)


def pubdb_url(pubdb_id: int | str) -> str:
    """Build a download URL. BASE is a template, so .format is mandatory."""
    return DOWNLOAD_URL.format(pubdb_id)


def entry_dates(filename: str) -> set[date]:
    """Every plausible date in a pubdb filename, across all known layouts."""
    found: set[date] = set()
    for pattern, order in _DATE_PATTERNS:
        for match in pattern.finditer(filename):
            first, second, third = match.groups()
            if order == "ymd":
                year, month, day = first, second, third
            else:
                day, month, year = first, second, third
            try:
                found.add(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return found


def parse_last_modified(value: str) -> date | None:
    """Parse an RFC 7231 Last-Modified value into a date."""
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def is_pdf(entry: dict[str, Any]) -> bool:
    return str(entry.get("content_type", "")).startswith("application/pdf")


def candidates_by_date(index: dict[str, dict], wanted: date) -> list[int]:
    """Publications whose filename carries the wanted date in any layout."""
    return sorted(
        int(pubdb_id)
        for pubdb_id, entry in index.items()
        if wanted in entry_dates(str(entry.get("filename", "")))
    )


def candidates_by_mtime(
    index: dict[str, dict], wanted: date, window_days: int
) -> list[int]:
    """Publications last modified within a window around the wanted date."""
    span = timedelta(days=window_days)
    found = []
    for pubdb_id, entry in index.items():
        modified = parse_last_modified(str(entry.get("last_modified", "")))
        if modified is not None and abs(modified - wanted) <= span:
            found.append(int(pubdb_id))
    return sorted(found)


def fetch_pdf_text(pubdb_id: int, timeout: float = 60.0) -> str:
    """Return the PDF title plus first-page text, or '' on any failure."""
    request = urllib.request.Request(
        pubdb_url(pubdb_id), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_SSL_CONTEXT
        ) as response:
            body = response.read()
        reader = PdfReader(io.BytesIO(body))
        title = (reader.metadata or {}).get("/Title", "") or ""
        first_page = reader.pages[0].extract_text() if reader.pages else ""
        return f"{title} {first_page}"
    except Exception:  # noqa: BLE001 - pypdf raises many unrelated types
        return ""


def verify(pubdb_id: int, timeout: float = 20.0) -> bool:
    """Confirm the link really serves a PDF before recording it."""
    request = urllib.request.Request(
        pubdb_url(pubdb_id), method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_SSL_CONTEXT
        ) as response:
            content_type = response.headers.get("Content-Type", "")
            return response.status == 200 and content_type.startswith("application/pdf")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def _candidate_sources(
    wanted: date, index: dict[str, dict]
) -> Iterator[tuple[str, list[int]]]:
    """Yield candidate sets cheapest first, so later scans are skipped."""
    yield "date", candidates_by_date(index, wanted)
    for window in MTIME_WINDOWS_DAYS:
        yield "mtime", candidates_by_mtime(index, wanted, window)


def resolve(parsed: S3Key, index: dict[str, dict]) -> dict[str, Any] | None:
    """Resolve one S3 key to a pubdb publication, or None."""
    if parsed.date is None:
        return None

    title = deslugify(parsed.slug)

    for source, ids in _candidate_sources(parsed.date, index):
        ids = [i for i in ids if is_pdf(index[str(i)])]
        if not ids:
            continue

        if source == "date" and len(ids) == 1:
            return {"pubdb_id": ids[0], "match": source, "score": 1.0}

        threshold = CONTENT_THRESHOLD if source == "date" else MTIME_CONTENT_THRESHOLD
        scored = [(coverage(title, fetch_pdf_text(i)), i) for i in ids]
        best_score, best_id = max(scored)
        if best_score >= threshold:
            return {
                "pubdb_id": best_id,
                "match": f"{source}+content",
                "score": round(best_score, 3),
                "candidate_count": len(ids),
            }

    return None


def build(keys: list[str], index: dict[str, dict]) -> dict[str, dict]:
    resolved: dict[str, dict] = {}
    for position, key in enumerate(keys, start=1):
        outcome = resolve(parse_s3_key(key), index)
        if outcome and verify(outcome["pubdb_id"]):
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            resolved[key] = {
                "pdf_url": pubdb_url(outcome["pubdb_id"]),
                "pubdb_id": outcome["pubdb_id"],
                "match": outcome["match"],
                "score": outcome["score"],
                "candidate_count": outcome.get("candidate_count", 1),
                "verified_at": now,
            }
        print(
            f"[{position}/{len(keys)}] {'OK  ' if key in resolved else 'MISS'} {key}",
            flush=True,
        )
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys", type=Path, default=DEFAULT_KEYS_PATH)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_MAP_PATH)
    parser.add_argument(
        "--sample", type=int, default=0, help="Only process the first N keys."
    )
    args = parser.parse_args()

    with args.index.open(encoding="utf-8") as handle:
        index = json.load(handle)
    keys = [
        line.strip()
        for line in args.keys.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.sample:
        keys = keys[: args.sample]

    resolved = build(keys, index)

    rate = len(resolved) / len(keys) if keys else 0.0
    print(f"\nresolved {len(resolved)}/{len(keys)} ({rate:.0%})")

    if args.sample:
        print("sample run: not writing the map")
        return

    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(resolved, handle, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
