# Replacing S3 source URIs with links to BFE publications

Date: 2026-09-10
Status: agreed, ready for planning

## Problem

The `bfe-public-knowledge` MCP server (adapter `mcp/bfe_mcp_proxy.py`) forwards the
AgentCore Gateway response to the client unchanged. Every retrieved chunk states its
source in three fields, and all three point into S3:

- `documentId` — `s3://sandbox-bfe-public-data-pdf/<key>`
- `location.s3Location.uri` — `https://sandbox-bfe-public-data-pdf.s3.eu-central-1.amazonaws.com/<key>`
- `metadata._source_uri` — the same value, auto-derived by Bedrock from the S3 location

The bucket is not public: a GET on an object returns `403 AccessDenied`, and so does
a listing. Cited sources therefore open for nobody except the owners of the sandbox
account. `_source_uri` does not help — it is not a pointer to the original
publication, only a restatement of the same S3 location.

This is exactly points 3 ("Improve Source Transparency") and 4 ("Improve Metadata")
of the challenge: linking retrieved knowledge back to the original BFE publication.

## What we know about the sources

The canonical link to a PDF on the BFE side is
`https://pubdb.bfe.admin.ch/de/publication/download/<id>`. `www.bfe.admin.ch` itself
links to publications this way, wrapping that URL in `.exturl.html/<base64>`, so the
intermediate wrapper page adds no value.

Established by investigation:

- The pubdb id space runs roughly `1000…13000` and is sparse (some ids return 404).
  Not every entry is a PDF; `.docx` occurs too.
- pubdb offers no search and no API — only `/de/publication/download/<id>` works.
- A `HEAD` on that URL returns `Content-Disposition` carrying the original filename
  (`7238-20251126_Faktenblatt Förderung_PV_DE.pdf`), plus `Content-Type` and
  `Last-Modified`. A catalogue of all publications therefore costs ~12k HEAD
  requests and no body downloads.
- `www.bfe.admin.ch` has moved to a new structure (`/de/...`; the old
  `/bfe/de/home/*.html` paths return 404) and requires a browser User-Agent,
  otherwise it answers 404 to everything.

**The join key is the date.** An S3 key has the form `YYYY-MM-DD_<title-slug>.pdf`,
and for the verified example the date `2025-11-26` matched the date `20251126`
embedded in the pubdb filename (id 7238), whose `Last-Modified` is 2025-11-27.

Matching on filenames alone is weak and must not be relied upon: the S3 slug is
derived from the document **title**, whereas the pubdb name is an internal one.
`forderung-von-photovoltaikanlagen-einmalvergutung-gleitende-marktpramie-und-boni`
against `Faktenblatt Förderung_PV_DE` overlaps in one token out of seven. Ambiguity
is therefore resolved by content, not by name.

## Solution

Three components tied together by a single artefact, `mcp/source_map.json`.
Phase 1 fixes the output of our own adapter; phase 2 reuses the same map for the
Knowledge Base metadata.

### 1. `mcp/pubdb_index.py` — publication catalogue

An offline scanner. It issues `HEAD` across the id range (default `1000…13000`,
bounds configurable) and collects `mcp/pubdb_index.json`:

```json
{"7238": {"filename": "7238-20251126_Faktenblatt Förderung_PV_DE.pdf",
          "content_type": "application/pdf",
          "last_modified": "2025-11-27T16:01:38Z",
          "size": 501211}}
```

Requirements: idempotency (a rerun fills gaps rather than refetching everything),
a polite rate limit and bounded concurrency, a browser User-Agent, correct parsing
of `filename*=utf-8''` (umlauts in filenames are percent-encoded), and tolerance of
404s and timeouts.

### 2. `mcp/build_source_map.py` — matching

Input: the list of bucket keys plus `pubdb_index.json`. Output: `mcp/source_map.json`:

```json
{"2025-11-26_forderung-von-photovoltaikanlagen-....pdf": {
    "pdf_url": "https://pubdb.bfe.admin.ch/de/publication/download/7238",
    "pubdb_id": 7238, "match": "date+content", "score": 0.91,
    "verified_at": "2026-09-10T12:00:00Z"}}
```

For each S3 key:

1. Split the key into a date and a title slug.
2. Candidates are pubdb entries whose filename contains that date; if there are
   none, widen to a `Last-Modified` window of ±14 days, then ±90.
3. Exactly one candidate — accept it.
4. More than one — download the candidates, extract the PDF metadata title and the
   first-page text with `pypdf`, rank by similarity against the de-slugified title
   from the S3 key, and take the best one above the threshold.
5. Zero candidates, or all below the threshold — leave unresolved.

Normalisation for comparison: lowercase, umlaut folding (`ö→o`, `ä→a`, `ü→u`,
`ß→ss`), tokenisation on non-letters, and token-set comparison. This is needed
because the S3 slugs have already lost their umlauts (`forderung`, `marktpramie`).

Values of `match`: `date` — the date yielded a single candidate; `date+content` —
there were several candidates and the choice was made on PDF content; `mtime` and
`mtime+content` — the same, with candidates gathered from the `Last-Modified`
window. Unresolved documents are not written to the map at all.

Every recorded link is verified with a `HEAD`: `200` and `application/pdf`.
Unverified links do not enter the map.

**Confidence gate.** The script is first run over a sample of ~30 documents, and the
match rate and disputable cases are reviewed by eye. Only then does the full run
follow. Should the date hypothesis fail to hold, the matcher is reconsidered; the
other components are unaffected.

### 3. Patch to `mcp/bfe_mcp_proxy.py` — rewriting the response

`rewrite_sources(result)` is called in the `tools/call` handler between
`call_gateway()` and `result()`.

**Shape of the Gateway response** (confirmed by an actual call): the payload does not
arrive flat, but inside the standard MCP envelope

```json
{"isError": false, "resultType": "...",
 "content": [{"type": "text", "text": "{\"retrievalResults\":[...]}"}]}
```

that is, the entire useful JSON sits as a **string** inside `content[0].text`. The
rewrite must therefore parse that string, modify the object, serialise it back and
put it in place. `content` elements whose `type != "text"`, and strings that do not
parse as JSON containing a `retrievalResults` key, are passed through untouched.

Then, for each entry in `retrievalResults`:

- `location.s3Location.uri` → `pdf_url`
- `metadata._source_uri` → `pdf_url`
- `metadata.source_url` and `metadata.source_confidence` are added
  (`verified` — an exact link from the map, `search` — the search fallback)
- `metadata.pubdb_id` is added when `verified`
- `documentId` is left alone: it is the stable identifier of the object in the
  Knowledge Base, and it is convenient for deduplicating chunks of one document
  when rendering citations

The fallback for unresolved documents is a deterministic search URL built from the
de-slugified title:

```
https://www.google.com/search?q=<urlencode(site:pubdb.bfe.admin.ch OR site:bfe.admin.ch "<title>")>
```

It is always clickable and always honestly marked `source_confidence: "search"`, so a
client can tell an exact link from a hint for searching. The search engine base URL
is kept in a constant.

The map is read once at process start. After that it is a dictionary lookup: no
network calls on the hot path, and no added response latency.

### Error handling

A missing or corrupt `source_map.json` must not bring the adapter down: the map is
loaded inside a `try`, stays empty on failure, and the adapter then behaves exactly
as it does today. `rewrite_sources` as a whole is likewise wrapped so that any
exception returns the original `result` untouched. A working S3 link beats a broken
MCP response.

### Testing

- `mcp/test_rewrite.py` — unit tests for `rewrite_sources` against a recorded sample
  of a real Gateway response: exact match, fallback, empty map, malformed response
  structure, invalid JSON inside `content[0].text`, and `content` holding non-text
  elements. No network access.
- Tests for normalisation and scoring on the "S3 slug ↔ pubdb filename" pair found
  during investigation.
- Smoke check: N random links from the finished map return `200 application/pdf`.
- End-to-end: a real `tools/call` through the adapter, inspected by eye for the
  absence of any `s3.eu-central-1.amazonaws.com` in the link fields.

## Phase 2 — Knowledge Base metadata

The same map is reused: for each object a sibling `<key>.metadata.json` is generated,

```json
{"metadataAttributes": {"source_url": "...", "publication_date": "...", "language": "de"}}
```

followed by a sync to the bucket and a data source re-ingest. This fixes sources for
**every** client of the Gateway rather than only ours, and along the way corrects the
incorrect `_language_code: "en"` on German documents.

It requires write access to the bucket and permission to start an ingestion job. It
comes after phase 1 so that the quick win does not depend on those permissions.

## Deliberately out of scope

- No resolver that queries a search engine at runtime: that means latency, API keys
  and an external dependency on the hot path, for a case the map already covers.
- No `.exturl.html` wrapper pages: an extra click to reach the PDF, and a brittle URL.
- No rewriting of `documentId`.
- No fixing of search on the new `www.bfe.admin.ch`: it has no working search
  endpoint that we could find, and pubdb serves the purpose better.
