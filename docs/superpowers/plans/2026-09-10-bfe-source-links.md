# BFE Source Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unreachable S3 source URIs in Gateway retrieval results with real, clickable `pubdb.bfe.admin.ch` PDF links.

**Architecture:** An offline scanner builds a catalogue of every pubdb publication from `HEAD` responses. A matcher joins S3 object keys to that catalogue on the publication date, disambiguating by PDF content when a date yields several candidates, and emits `source_map.json`. The adapter loads that map once at startup and rewrites the Gateway response in place; anything unresolved falls back to a deterministic search URL, so no dead S3 link ever reaches a client.

**Tech Stack:** Python 3.10+ (host has 3.14.2), stdlib only for the adapter, `pypdf` 6.6.0 for content disambiguation, `pytest` 9.0.3 for tests.

**Design doc:** `docs/2026-09-10-bfe-source-links-design.md`

## Global Constraints

- Branch: `feat/mcp-proxy-and-source-links`. All work lands there.
- The repository is **public**. No credentials, client ids, tokens or account numbers in any committed file. The Cognito client id and token URL come from `BFE_MCP_CLIENT_ID` / `BFE_MCP_TOKEN_URL` — never hardcode them.
- The adapter (`bfe_mcp_proxy.py` and anything it imports at runtime) uses the **standard library only**. `pypdf` is allowed exclusively in the offline build scripts.
- Python 3.10+ syntax (PEP 604 unions), matching the existing adapter.
- Type hints on every function and method.
- Formatting `ruff format`, linting `ruff check` — both must pass before each commit.
- `mcp/` is a flat directory, not a package. Modules import each other by bare name (`import source_match`), which works because Python puts the script's own directory on `sys.path`.
- Any network client must send a browser User-Agent: `www.bfe.admin.ch` and `pubdb.bfe.admin.ch` return 404 to default Python/curl agents.
- Generated artefacts (`pubdb_index.json`, `source_map.json`, `keys.txt`) are **committed** — they are the deliverable the adapter reads at runtime.

## File Structure

| File | Responsibility |
|---|---|
| `mcp/source_match.py` | Pure matching primitives: S3 key parsing, umlaut folding, tokenising, coverage scoring. No I/O, no network. |
| `mcp/source_links.py` | Runtime rewriting: load the map, build fallback search URLs, rewrite the Gateway envelope. Stdlib only. |
| `mcp/bfe_mcp_proxy.py` | Modified: calls `rewrite_sources()` in the `tools/call` handler. |
| `mcp/pubdb_index.py` | Offline scanner producing `pubdb_index.json`. |
| `mcp/build_source_map.py` | Offline matcher producing `source_map.json`. Uses `pypdf`. |
| `mcp/test_source_match.py` | Unit tests for the primitives. |
| `mcp/test_rewrite.py` | Unit tests for the rewriting. |

`source_match.py` is deliberately free of I/O so the scoring rules can be tested exhaustively without a network. `source_links.py` is separate from the adapter so the tests never import the adapter's OAuth machinery.

---

### Task 1: Matching primitives

**Files:**
- Create: `mcp/source_match.py`
- Test: `mcp/test_source_match.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `S3Key` — frozen dataclass with fields `key: str`, `date: datetime.date | None`, `slug: str`
  - `parse_s3_key(key: str) -> S3Key`
  - `deslugify(slug: str) -> str`
  - `fold(text: str) -> str`
  - `tokens(text: str) -> frozenset[str]`
  - `coverage(needle: str, haystack: str) -> float`

- [ ] **Step 1: Write the failing tests**

Create `mcp/test_source_match.py`:

```python
"""Unit tests for the pure matching primitives."""

from __future__ import annotations

from datetime import date

from source_match import S3Key, coverage, deslugify, fold, parse_s3_key, tokens


def test_parse_s3_key_splits_date_and_slug() -> None:
    parsed = parse_s3_key("2025-11-26_forderung-von-photovoltaikanlagen.pdf")
    assert parsed == S3Key(
        key="2025-11-26_forderung-von-photovoltaikanlagen.pdf",
        date=date(2025, 11, 26),
        slug="forderung-von-photovoltaikanlagen",
    )


def test_parse_s3_key_keeps_parentheses_in_slug() -> None:
    parsed = parse_s3_key("2022-04-01_evaluation-der-zusammenschlusse-(zev)-2018.pdf")
    assert parsed.slug == "evaluation-der-zusammenschlusse-(zev)-2018"


def test_parse_s3_key_without_date_prefix() -> None:
    parsed = parse_s3_key("annual-report.pdf")
    assert parsed.date is None
    assert parsed.slug == "annual-report"


def test_parse_s3_key_with_invalid_date() -> None:
    parsed = parse_s3_key("2025-13-45_something.pdf")
    assert parsed.date is None
    assert parsed.slug == "2025-13-45_something"


def test_deslugify_replaces_separators() -> None:
    assert deslugify("forderung-von-photovoltaikanlagen") == (
        "forderung von photovoltaikanlagen"
    )


def test_fold_removes_umlauts() -> None:
    assert fold("Förderung") == "forderung"
    assert fold("Grösse") == "grosse"
    assert fold("Straße") == "strasse"
    assert fold("Réalité") == "realite"


def test_tokens_drops_stopwords_but_keeps_short_acronyms() -> None:
    assert tokens("Förderung von PV im Jahr") == frozenset({"forderung", "pv", "jahr"})


def test_tokens_splits_on_punctuation_and_underscores() -> None:
    assert tokens("7238-20251126_Faktenblatt Förderung_PV_DE.pdf") == frozenset(
        {"7238", "20251126", "faktenblatt", "forderung", "pv", "de", "pdf"}
    )


def test_coverage_full_containment() -> None:
    score = coverage(
        "Förderung von Photovoltaikanlagen",
        "Faktenblatt Förderung Photovoltaikanlagen DE 2025",
    )
    assert score == 1.0


def test_coverage_partial() -> None:
    score = coverage(
        "Förderung Photovoltaik Einmalvergütung",
        "Faktenblatt Förderung PV DE",
    )
    assert score == 1 / 3


def test_coverage_of_empty_needle_is_zero() -> None:
    assert coverage("von der und", "anything at all") == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd mcp && python3 -m pytest test_source_match.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'source_match'`

- [ ] **Step 3: Write the implementation**

Create `mcp/source_match.py`:

```python
"""Pure matching primitives shared by the offline builders and the adapter.

Deliberately free of I/O so the scoring rules can be tested exhaustively
without touching the network.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

# German/English function words carry no signal for matching titles.
STOPWORDS = frozenset(
    "der die das den dem des ein eine einer einen einem und oder von vom "
    "fur zur zum auf mit bei als aus ist sind im in an "
    "the of and for to on at by".split()
)

_DATED_KEY_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_(?P<slug>.+)$"
)
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
# Folded before NFKD because these have no combining-mark decomposition.
_PRE_FOLD = {"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l"}


@dataclass(frozen=True)
class S3Key:
    """An S3 object key split into its publication date and title slug."""

    key: str
    date: date | None
    slug: str


def parse_s3_key(key: str) -> S3Key:
    """Split ``YYYY-MM-DD_<title-slug>.pdf`` into its parts.

    Keys without a parseable date prefix yield ``date=None`` and the whole
    stem as the slug.
    """
    stem = key.rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[: -len(".pdf")]

    match = _DATED_KEY_RE.match(stem)
    if match is None:
        return S3Key(key=key, date=None, slug=stem)

    try:
        parsed = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return S3Key(key=key, date=None, slug=stem)

    return S3Key(key=key, date=parsed, slug=match["slug"])


def deslugify(slug: str) -> str:
    """Turn a hyphenated slug back into a space-separated title."""
    return re.sub(r"[-_]+", " ", slug).strip()


def fold(text: str) -> str:
    """Lowercase and strip diacritics, so ``Förderung`` matches ``forderung``.

    The S3 slugs have already lost their umlauts, while the pubdb filenames
    have kept theirs, so both sides must be folded before comparison.
    """
    lowered = text.lower()
    for source, replacement in _PRE_FOLD.items():
        lowered = lowered.replace(source, replacement)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokens(text: str) -> frozenset[str]:
    """Fold, split on non-alphanumerics and drop function words.

    Short tokens are kept on purpose: ``PV`` and ``DE`` carry real signal.
    """
    parts = _TOKEN_SPLIT_RE.split(fold(text))
    return frozenset(part for part in parts if part and part not in STOPWORDS)


def coverage(needle: str, haystack: str) -> float:
    """Fraction of ``needle``'s tokens that appear in ``haystack``.

    Containment rather than Jaccard: an S3 title slug is long and a pubdb
    filename is short, so symmetric measures punish good matches.
    """
    wanted = tokens(needle)
    if not wanted:
        return 0.0
    return len(wanted & tokens(haystack)) / len(wanted)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mcp && python3 -m pytest test_source_match.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Lint and format**

Run: `ruff format mcp/source_match.py mcp/test_source_match.py && ruff check mcp/source_match.py mcp/test_source_match.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add mcp/source_match.py mcp/test_source_match.py
git commit -m "Add matching primitives for S3 key to publication joining

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Response rewriting

**Files:**
- Create: `mcp/source_links.py`
- Test: `mcp/test_rewrite.py`

**Interfaces:**
- Consumes: `parse_s3_key`, `deslugify` from `source_match` (Task 1).
- Produces:
  - `SEARCH_BASE: str`
  - `DEFAULT_MAP_PATH: pathlib.Path`
  - `load_source_map(path: pathlib.Path | None = None) -> dict[str, dict]`
  - `s3_key_from_uri(uri: str) -> str | None`
  - `search_url(slug: str) -> str`
  - `rewrite_sources(result: dict, source_map: dict[str, dict]) -> dict`

The `source_map.json` entry shape consumed here, and produced by Task 5:
`{"pdf_url": str, "pubdb_id": int, "match": str, "score": float, "verified_at": str}`

- [ ] **Step 1: Write the failing tests**

Create `mcp/test_rewrite.py`:

```python
"""Unit tests for rewriting Gateway responses. No network access."""

from __future__ import annotations

import json
from typing import Any

from source_links import SEARCH_BASE, rewrite_sources, s3_key_from_uri, search_url

BUCKET_HOST = "sandbox-bfe-public-data-pdf.s3.eu-central-1.amazonaws.com"
KEY = "2025-11-26_forderung-von-photovoltaikanlagen.pdf"

SOURCE_MAP = {
    KEY: {
        "pdf_url": "https://pubdb.bfe.admin.ch/de/publication/download/7238",
        "pubdb_id": 7238,
        "match": "date",
        "score": 1.0,
        "verified_at": "2026-09-10T12:00:00Z",
    }
}


def envelope(*keys: str) -> dict[str, Any]:
    """Build a Gateway response with one retrieval result per key."""
    results = [
        {
            "content": {"text": "passage", "type": "TEXT"},
            "documentId": f"s3://sandbox-bfe-public-data-pdf/{key}",
            "location": {
                "s3Location": {"uri": f"https://{BUCKET_HOST}/{key}"},
                "type": "S3",
            },
            "metadata": {"_source_uri": f"https://{BUCKET_HOST}/{key}"},
            "score": 0.56,
        }
        for key in keys
    ]
    return {
        "isError": False,
        "resultType": "TOOL_RESULT",
        "content": [
            {"type": "text", "text": json.dumps({"retrievalResults": results})}
        ],
    }


def payload(result: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON string back out of the MCP envelope."""
    return json.loads(result["content"][0]["text"])


def test_s3_key_from_https_uri_is_percent_decoded() -> None:
    uri = f"https://{BUCKET_HOST}/2022-04-01_zusammenschlusse-%28zev%29.pdf"
    assert s3_key_from_uri(uri) == "2022-04-01_zusammenschlusse-(zev).pdf"


def test_s3_key_from_s3_scheme_uri() -> None:
    uri = "s3://sandbox-bfe-public-data-pdf/2025-11-26_foo.pdf"
    assert s3_key_from_uri(uri) == "2025-11-26_foo.pdf"


def test_s3_key_from_unrelated_uri_is_none() -> None:
    assert s3_key_from_uri("https://example.com/whatever.pdf") is None


def test_search_url_contains_site_filters_and_title() -> None:
    url = search_url("forderung-von-photovoltaikanlagen")
    assert url.startswith("https://www.google.com/search?q=")
    assert "site%3Apubdb.bfe.admin.ch" in url
    assert "forderung+von+photovoltaikanlagen" in url


def test_known_key_gets_the_exact_pubdb_link() -> None:
    entry = payload(rewrite_sources(envelope(KEY), SOURCE_MAP))["retrievalResults"][0]
    expected = "https://pubdb.bfe.admin.ch/de/publication/download/7238"
    assert entry["location"]["s3Location"]["uri"] == expected
    assert entry["metadata"]["_source_uri"] == expected
    assert entry["metadata"]["source_url"] == expected
    assert entry["metadata"]["source_confidence"] == "verified"
    assert entry["metadata"]["pubdb_id"] == 7238


def test_document_id_is_left_untouched() -> None:
    entry = payload(rewrite_sources(envelope(KEY), SOURCE_MAP))["retrievalResults"][0]
    assert entry["documentId"] == f"s3://sandbox-bfe-public-data-pdf/{KEY}"


def test_unknown_key_falls_back_to_search() -> None:
    unknown = "2019-01-01_unbekannter-bericht.pdf"
    rewritten = rewrite_sources(envelope(unknown), SOURCE_MAP)
    entry = payload(rewritten)["retrievalResults"][0]
    metadata = entry["metadata"]
    assert metadata["source_confidence"] == "search"
    assert metadata["source_url"].startswith(SEARCH_BASE)
    assert "pubdb_id" not in metadata
    assert BUCKET_HOST not in entry["location"]["s3Location"]["uri"]


def test_empty_map_still_removes_every_s3_link() -> None:
    rewritten = rewrite_sources(envelope(KEY, "2019-01-01_other.pdf"), {})
    assert BUCKET_HOST not in json.dumps(payload(rewritten)["retrievalResults"])


def test_non_text_content_items_pass_through() -> None:
    original = {"content": [{"type": "image", "data": "abc"}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_text_that_is_not_json_passes_through() -> None:
    original = {"content": [{"type": "text", "text": "plain prose, not JSON"}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_json_without_retrieval_results_passes_through() -> None:
    original = {"content": [{"type": "text", "text": json.dumps({"other": 1})}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_malformed_result_is_returned_unchanged() -> None:
    original = {"content": "not a list at all"}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_result_without_content_is_returned_unchanged() -> None:
    original = {"isError": True}
    assert rewrite_sources(original, SOURCE_MAP) == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd mcp && python3 -m pytest test_rewrite.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'source_links'`

- [ ] **Step 3: Write the implementation**

Create `mcp/source_links.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mcp && python3 -m pytest test_rewrite.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Lint and format**

Run: `ruff format mcp/source_links.py mcp/test_rewrite.py && ruff check mcp/source_links.py mcp/test_rewrite.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add mcp/source_links.py mcp/test_rewrite.py
git commit -m "Rewrite retrieval sources to BFE publication links

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire the rewriting into the adapter

**Files:**
- Modify: `mcp/bfe_mcp_proxy.py` (imports near line 6-12; the `tools/call` branch of `handle()`)

**Interfaces:**
- Consumes: `load_source_map`, `rewrite_sources` from `source_links` (Task 2).
- Produces: no new public names. After this task the adapter emits no S3 URIs in link fields.

This task has no unit test of its own — Task 2 covers the logic. Its deliverable is verified end-to-end against the live Gateway.

- [ ] **Step 1: Add the import**

In `mcp/bfe_mcp_proxy.py`, after the `from typing import Any` line, add:

```python
from source_links import load_source_map, rewrite_sources
```

- [ ] **Step 2: Load the map once at startup**

Immediately after the `_token_cache: tuple[str, float] | None = None` line, add:

```python
_source_map = load_source_map()
```

- [ ] **Step 3: Rewrite the result before returning it**

In `handle()`, in the `tools/call` branch, replace this line:

```python
        result(request_id, call_gateway(question.strip(), request_id))
```

with:

```python
        gateway_result = call_gateway(question.strip(), request_id)
        result(request_id, rewrite_sources(gateway_result, _source_map))
```

- [ ] **Step 4: Verify no S3 link survives a real call**

With an empty map this must already replace every S3 URI with a search URL.

Run, substituting your own credentials:

```bash
cd mcp && BFE_MCP_CLIENT_ID=... BFE_MCP_TOKEN_URL=... python3 -c "
import json, subprocess, os
req = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'bfe-public-knowledge___Retrieve','arguments':{'retrievalQuery':{'text':'Wasserkraft Schweiz'}}}}
out = subprocess.run(['python3','bfe_mcp_proxy.py'], input=json.dumps(req)+chr(10), capture_output=True, text=True, env=os.environ)
msg = json.loads(out.stdout.splitlines()[0])
inner = json.loads(msg['result']['content'][0]['text'])
blob = json.dumps(inner)
print('chunks:', len(inner['retrievalResults']))
print('s3 links left:', blob.count('s3.eu-central-1.amazonaws.com'))
print('confidences:', {r['metadata']['source_confidence'] for r in inner['retrievalResults']})
print('sample:', inner['retrievalResults'][0]['metadata']['source_url'][:100])
"
```

Expected: a non-zero chunk count, `s3 links left: 0`, confidences `{'search'}`, and a `https://www.google.com/search?q=...` sample.

- [ ] **Step 5: Confirm the adapter survives a corrupt map**

```bash
cd mcp && echo 'not json at all' > source_map.json && python3 -c "
from source_links import load_source_map
print('loaded:', load_source_map())
" && rm source_map.json
```

Expected: `loaded: {}` with no traceback.

- [ ] **Step 6: Lint and format**

Run: `ruff format mcp/bfe_mcp_proxy.py && ruff check mcp/bfe_mcp_proxy.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add mcp/bfe_mcp_proxy.py
git commit -m "Rewrite Gateway sources before returning them to the client

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Key corpus from the Gateway

**Revision note (2026-09-10).** The original Task 4 built a pubdb catalogue scanner.
It is **cancelled**: teammate dariazorina already scanned pubdb and pushed
`mcp/pubdb_index.json` with 7132 entries (commit `3e5d02b`). Rescanning would
duplicate ~20 minutes of crawling for no gain. Task 4 is re-scoped to the thing
that is actually missing — the list of S3 object keys to resolve.

We cannot list the bucket: it returns `403 AccessDenied` on both GET and listing,
and no AWS credentials are configured on the host. The only available source of
object keys is the Gateway itself, whose retrieval results carry `documentId`
values of the form `s3://sandbox-bfe-public-data-pdf/<key>`. Harvesting many
varied queries yields a growing corpus. Measured rate: 11 queries produced 40
distinct documents.

**Files:**
- Create: `mcp/harvest_keys.py`
- Create (generated, committed): `mcp/keys.txt`

**Interfaces:**
- Consumes: nothing from earlier tasks. Talks to `mcp/bfe_mcp_proxy.py` as a subprocess.
- Produces:
  - `QUERIES: tuple[str, ...]`
  - `harvest(queries: Sequence[str], timeout: float = 900.0) -> set[str]`
  - `mcp/keys.txt` — one S3 object key per line, sorted, unique

- [ ] **Step 1: Write the harvester**

Create `mcp/harvest_keys.py`:

```python
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
```

- [ ] **Step 2: Run the harvest**

The adapter needs credentials. Load them from the local MCP config rather than
typing them out, and never commit them:

```bash
cd mcp && export BFE_MCP_CLIENT_ID=$(python3 -c "import json;print(json.load(open('/Users/vlak/OEKG/.mcp.json'))['mcpServers']['bfe-public-knowledge']['env']['BFE_MCP_CLIENT_ID'])") && export BFE_MCP_TOKEN_URL=$(python3 -c "import json;print(json.load(open('/Users/vlak/OEKG/.mcp.json'))['mcpServers']['bfe-public-knowledge']['env']['BFE_MCP_TOKEN_URL'])") && python3 harvest_keys.py
```

Expected: `wrote N keys to .../keys.txt` with N of at least 80. The 26 queries
above are broader than the 11-query probe that produced 40 keys.

- [ ] **Step 3: Sanity-check the corpus**

```bash
cd mcp && wc -l keys.txt && head -3 keys.txt && python3 -c "
keys = [k.strip() for k in open('keys.txt') if k.strip()]
dated = sum(1 for k in keys if len(k) > 10 and k[4] == '-' and k[7] == '-' and k[10] == '_')
print('keys:', len(keys)); print('with YYYY-MM-DD_ prefix:', dated)
"
```

Expected: every key ending in `.pdf`, and nearly all carrying the `YYYY-MM-DD_` prefix.
That prefix is what the matcher joins on, so a low count here would undermine Task 5.

- [ ] **Step 4: Lint and format**

Run: `ruff format mcp/harvest_keys.py && ruff check mcp/harvest_keys.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add mcp/harvest_keys.py mcp/keys.txt
git commit -m "Harvest S3 object keys from Gateway retrieval results

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Rewrite the matcher

**Revision note (2026-09-10).** `mcp/build_source_map.py` already exists from commit
`3e5d02b`. This task **replaces its contents**. The existing version resolved 3 of 3
keys, but its key list held only 3 entries, so that rate is not evidence of anything.

**Files:**
- Rewrite: `mcp/build_source_map.py`
- Create: `mcp/test_source_map_build.py`
- Create (generated, committed): `mcp/source_map.json`

**Interfaces:**
- Consumes: `parse_s3_key`, `deslugify`, `coverage` from `source_match` (Task 1);
  `mcp/pubdb_index.json` (from dariazorina's commit `3e5d02b`); `mcp/keys.txt` (Task 4).
- Produces: `mcp/source_map.json`, whose entries MUST contain `pdf_url` and `pubdb_id`
  because `source_links.py` (Task 2) reads exactly those two fields.

**Four properties of the existing index that the matcher must respect.** Getting any
of these wrong silently produces garbage:

1. `pubdb_index.py` defines `BASE_URL = "https://pubdb.bfe.admin.ch/de/publication/download/{}"`
   — a **format template**, not a prefix. `f"{BASE_URL}{pubdb_id}"` yields
   `.../download/{}7238`. Always use `.format(pubdb_id)`.
2. Index filenames **lost their umlauts**: entry `7238` reads
   `7238-20251126_Faktenblatt F_rderung_PV_DE.pdf`, with `ö` replaced by `_`, because
   the scanner read the plain `filename` parameter rather than `filename*=utf-8''`.
   So `F_rderung` tokenises to `f` and `rderung` and will never match `forderung`.
   Filename similarity is therefore weak evidence; the date and the PDF content carry
   the match.
3. Every entry has `"size": 0` — the scanner did not record `Content-Length`. Do not
   use size for anything.
4. `last_modified` is an RFC 7231 string such as `"Thu, 27 Nov 2025 16:01:38 GMT"`.
   Parse it with `email.utils.parsedate_to_datetime`, not `strptime` with `%Z`.

**Dates in pubdb filenames use several layouts.** All three of these are real entries:

| Filename fragment | Layout |
|---|---|
| `7238-20251126_Faktenblatt…` | `YYYYMMDD` |
| `10020-SACH2019_final_for_publication_31012020.pdf` | `DDMMYYYY` |
| `10822-WASSERKRAFT_…_2022.03.10_BFE_Vogel_D.pdf` | `YYYY.MM.DD` |

A matcher that only understands `YYYYMMDD` misses the majority. Extract every
plausible date and treat the entry as a candidate if any of them matches.

- [ ] **Step 1: Write the failing tests**

Create `mcp/test_source_map_build.py`:

```python
"""Unit tests for the pure parts of the matcher. No network access."""

from __future__ import annotations

from datetime import date

from build_source_map import (
    candidates_by_date,
    candidates_by_mtime,
    entry_dates,
    is_pdf,
    parse_last_modified,
    pubdb_url,
)

INDEX = {
    "7238": {
        "filename": "7238-20251126_Faktenblatt F_rderung_PV_DE.pdf",
        "content_type": "application/pdf",
        "last_modified": "Thu, 27 Nov 2025 16:01:38 GMT",
        "size": 0,
    },
    "10000": {
        "filename": "10000-2020 Leistungsvereinbarung BFE.pdf",
        "content_type": "application/pdf",
        "last_modified": "Tue, 14 Jan 2020 13:01:06 GMT",
        "size": 0,
    },
    "10020": {
        "filename": "10020-SACH2019_final_for_publication_31012020.pdf",
        "content_type": "application/pdf",
        "last_modified": "Fri, 31 Jan 2020 09:00:00 GMT",
        "size": 0,
    },
    "12500": {
        "filename": "12500-Vorlage.docx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "last_modified": "Fri, 11 Apr 2025 08:11:51 GMT",
        "size": 0,
    },
}


def test_pubdb_url_fills_the_template() -> None:
    assert pubdb_url(7238) == "https://pubdb.bfe.admin.ch/de/publication/download/7238"


def test_entry_dates_reads_yyyymmdd() -> None:
    assert date(2025, 11, 26) in entry_dates(INDEX["7238"]["filename"])


def test_entry_dates_reads_ddmmyyyy() -> None:
    assert date(2020, 1, 31) in entry_dates(INDEX["10020"]["filename"])


def test_entry_dates_reads_dotted_iso() -> None:
    assert date(2022, 3, 10) in entry_dates(
        "10822-WASSERKRAFT_Wasserkraft als Enabler_2022.03.10_BFE_Vogel_D.pdf"
    )


def test_entry_dates_ignores_a_bare_year() -> None:
    assert entry_dates(INDEX["10000"]["filename"]) == set()


def test_entry_dates_rejects_impossible_dates() -> None:
    assert entry_dates("9999-20251340_report.pdf") == set()


def test_parse_last_modified_reads_rfc7231() -> None:
    assert parse_last_modified("Thu, 27 Nov 2025 16:01:38 GMT") == date(2025, 11, 27)


def test_parse_last_modified_of_garbage_is_none() -> None:
    assert parse_last_modified("not a date") is None


def test_is_pdf_rejects_word_documents() -> None:
    assert is_pdf(INDEX["7238"]) is True
    assert is_pdf(INDEX["12500"]) is False


def test_candidates_by_date_matches_on_filename_date() -> None:
    assert candidates_by_date(INDEX, date(2025, 11, 26)) == [7238]


def test_candidates_by_date_returns_empty_when_nothing_matches() -> None:
    assert candidates_by_date(INDEX, date(1999, 1, 1)) == []


def test_candidates_by_mtime_uses_the_window() -> None:
    found = candidates_by_mtime(INDEX, date(2020, 1, 20), 14)
    assert 10000 in found
    assert 7238 not in found
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd mcp && python3 -m pytest test_source_map_build.py -v`
Expected: FAIL — the current `build_source_map.py` defines none of these names,
so collection fails with `ImportError`.

- [ ] **Step 3: Rewrite the matcher**

Replace the entire contents of `mcp/build_source_map.py` with:

```python
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
DEFAULT_INDEX_PATH = Path(__file__).with_name("pubdb_index.json")
DEFAULT_KEYS_PATH = Path(__file__).with_name("keys.txt")
DEFAULT_MAP_PATH = Path(__file__).with_name("source_map.json")
# Candidates from an exact filename-date match are a small, trustworthy set.
CONTENT_THRESHOLD = 0.5
# The Last-Modified window yields up to 198 candidates, so demand that every
# title token appears in the PDF before believing the match.
MTIME_CONTENT_THRESHOLD = 1.0
MTIME_WINDOWS_DAYS = (14, 90)

# The python.org framework build ships no CA bundle: ssl.get_default_verify_paths()
# reports cafile=None and every HTTPS call fails CERTIFICATE_VERIFY_FAILED, even
# though curl to the same host succeeds. certifi supplies the trust store.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

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

        # Only the date path may trust a lone candidate; an mtime window hit
        # is weak evidence and still has to earn it on content.
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mcp && python3 -m pytest test_source_map_build.py -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Confirm nothing else broke**

Run: `cd mcp && python3 -m pytest -q`
Expected: PASS. The suite held 30 tests before this task; it must now hold 42,
with no failures.

- [ ] **Step 6: Run the confidence gate on a sample**

This is the decision point of the whole plan. Do not skip it.

```bash
cd mcp && python3 build_source_map.py --sample 30
```

Expected: a per-key OK/MISS line and a final resolved rate.

**Stop and judge the result:**
- Rate at or above ~70%: the date hypothesis holds. Continue to Step 7.
- Rate below ~40%: **stop, report the actual number and a handful of MISS keys, and
  do not run the full build.** Tasks 1-3 are unaffected and already deliver
  search-URL fallbacks, so nothing regresses by stopping here.
- In between: report the rate and the MISS keys, and ask before continuing.

- [ ] **Step 7: Run the full build**

```bash
cd mcp && python3 build_source_map.py
```

Expected: a final resolved count and `wrote .../source_map.json`.

- [ ] **Step 8: Smoke-check random links from the finished map**

```bash
cd mcp && python3 -c "
import json, random
from build_source_map import verify
m = json.load(open('source_map.json'))
sample = random.sample(sorted(m), min(15, len(m)))
bad = [k for k in sample if not verify(m[k]['pubdb_id'])]
print('checked', len(sample), 'links; failures:', bad)
"
```

Expected: `failures: []`

- [ ] **Step 9: Verify the adapter now emits verified links**

```bash
cd mcp && export BFE_MCP_CLIENT_ID=$(python3 -c "import json;print(json.load(open('/Users/vlak/OEKG/.mcp.json'))['mcpServers']['bfe-public-knowledge']['env']['BFE_MCP_CLIENT_ID'])") && export BFE_MCP_TOKEN_URL=$(python3 -c "import json;print(json.load(open('/Users/vlak/OEKG/.mcp.json'))['mcpServers']['bfe-public-knowledge']['env']['BFE_MCP_TOKEN_URL'])") && python3 -c "
import json, subprocess, os, sys
req = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'bfe-public-knowledge___Retrieve','arguments':{'retrievalQuery':{'text':'Photovoltaik Einmalvergütung'}}}}
out = subprocess.run([sys.executable,'bfe_mcp_proxy.py'], input=json.dumps(req)+chr(10), capture_output=True, text=True, env=os.environ)
inner = json.loads(json.loads(out.stdout.splitlines()[0])['result']['content'][0]['text'])
for r in inner['retrievalResults']:
    print(r['metadata']['source_confidence'], r['metadata']['source_url'][:80])
print('s3 links left:', json.dumps(inner).count('s3.eu-central-1.amazonaws.com'))
"
```

Expected: `s3 links left: 0`, and at least some lines reading `verified` with a
`https://pubdb.bfe.admin.ch/...` URL.

- [ ] **Step 10: Lint and format**

Run: `ruff format mcp/build_source_map.py mcp/test_source_map_build.py && ruff check mcp/build_source_map.py mcp/test_source_map_build.py`
Expected: `All checks passed!`

- [ ] **Step 11: Confirm no credentials are being committed**

```bash
git add -A && git diff --cached | grep -inE 'BFE_MCP_CLIENT_ID *= *"[^"$]|client_secret|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}' || echo "clean"
```

Expected: `clean`

- [ ] **Step 12: Commit**

```bash
git commit -m "Rebuild the matcher around the existing pubdb index

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Document the outcome

**Files:**
- Modify: `mcp/README.md` (the "Known limitation: source links point at S3" section at the end)

**Interfaces:**
- Consumes: the resolved rate reported by Task 5.
- Produces: no code.

- [ ] **Step 1: Replace the limitation section**

Delete the `## Known limitation: source links point at S3` section and put this in its place, filling in the real numbers from Task 5:

```markdown
## Source links

Retrieval results cite `pubdb.bfe.admin.ch` publication PDFs rather than the
private S3 objects Bedrock reports. Every result carries:

- `metadata.source_url` — the link to show the user
- `metadata.source_confidence` — `verified` for an exact publication link,
  `search` for a search URL when the document could not be resolved
- `metadata.pubdb_id` — present only when `verified`

`documentId` still holds the S3 URI: it is the stable identifier of the object
in the Knowledge Base and is useful for deduplicating chunks of one document.

The mapping lives in `source_map.json`, currently covering N of M documents
(P%). To rebuild it:

```bash
python3 pubdb_index.py && python3 build_source_map.py
```

Both scripts are idempotent and safe to rerun. `pubdb_index.py` needs no
credentials; `build_source_map.py` needs `keys.txt`, the list of bucket object
keys, which requires read access to the S3 bucket to regenerate.
```

- [ ] **Step 2: Verify the internal link still resolves**

```bash
cd mcp && grep -n 'docs/2026-09-10' README.md && ls ../docs/2026-09-10-bfe-source-links-design.md
```

Expected: the design-doc reference still present and the file existing.

- [ ] **Step 3: Commit**

```bash
git add mcp/README.md
git commit -m "Document the resolved source links

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Out of scope for this plan

Phase 2 of the design — writing `source_url` into the Knowledge Base metadata and
re-ingesting the data source — is a separate subsystem. It needs write access to
the bucket and permission to start an ingestion job, and it consumes this plan's
`source_map.json` as its input. It gets its own plan once those permissions exist.
