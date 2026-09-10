"""Rewrite Gateway retrieval sources from S3 URIs to BFE publication links.

Standard library only: this module is imported by the adapter at runtime.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

from source_match import deslugify, parse_s3_key

SEARCH_BASE = "https://www.google.com/search?q="
DEFAULT_MAP_PATH = Path(__file__).with_name("source_map.json")
BUCKET_NAME = "sandbox-bfe-public-data-pdf"


def load_source_map(path: Path | None = None) -> dict[str, dict]:
    """Load the key-to-publication map, tolerating its absence.

    A missing or corrupt map must never take the adapter down: an empty map
    simply means every source falls back to a search URL.
    """
    target = DEFAULT_MAP_PATH if path is None else path
    try:
        with target.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def s3_key_from_uri(uri: str) -> str | None:
    """Extract the object key from either an ``s3://`` or an HTTPS S3 URI.

    Keys contain characters such as parentheses that arrive percent-encoded
    in the HTTPS form, so the path is always decoded.
    """
    if not isinstance(uri, str):
        return None
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == "s3":
        if parsed.netloc != BUCKET_NAME:
            return None
        key = parsed.path
    elif parsed.scheme in {"http", "https"}:
        if not parsed.netloc.startswith(f"{BUCKET_NAME}.s3"):
            return None
        key = parsed.path
    else:
        return None

    key = urllib.parse.unquote(key).lstrip("/")
    return key or None


def search_url(slug: str) -> str:
    """Build a deterministic search link for an unresolved publication."""
    query = f'site:pubdb.bfe.admin.ch OR site:bfe.admin.ch "{deslugify(slug)}"'
    return SEARCH_BASE + urllib.parse.quote_plus(query)


def _rewrite_entry(entry: dict[str, Any], source_map: dict[str, dict]) -> None:
    """Point one retrieval result at its publication, in place."""
    location = entry.get("location")
    s3_location = location.get("s3Location") if isinstance(location, dict) else None
    uri = s3_location.get("uri") if isinstance(s3_location, dict) else None

    key = s3_key_from_uri(uri) if uri else None
    if key is None:
        key = s3_key_from_uri(entry.get("documentId", ""))
    if key is None:
        return

    metadata = entry.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        return

    mapped = source_map.get(key)
    if mapped:
        url = mapped["pdf_url"]
        metadata["source_confidence"] = "verified"
        metadata["pubdb_id"] = mapped["pubdb_id"]
    else:
        url = search_url(parse_s3_key(key).slug)
        metadata["source_confidence"] = "search"

    metadata["source_url"] = url
    metadata["_source_uri"] = url
    if isinstance(s3_location, dict):
        s3_location["uri"] = url


def rewrite_sources(result: dict, source_map: dict[str, dict]) -> dict:
    """Rewrite every source URI inside an MCP tool result.

    The Gateway wraps its payload in the standard MCP envelope, where the
    retrieval JSON is a *string* inside ``content[0].text``. That string is
    parsed, rewritten and serialised back. Anything unexpected is passed
    through untouched: a working S3 link beats a broken MCP response.
    """
    try:
        content = result.get("content")
        if not isinstance(content, list):
            return result

        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw = item.get("text")
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            entries = payload.get("retrievalResults")
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if isinstance(entry, dict):
                    _rewrite_entry(entry, source_map)

            item["text"] = json.dumps(payload, ensure_ascii=False)

        return result
    except Exception:  # noqa: BLE001 - never break the response
        return result
