# Open Energy Knowledge Gateway — MCP Server

An MCP server exposing official Swiss federal energy publications (Swiss Federal
Office of Energy, SFOE / BFE) as structured, source-attributed tools that any
MCP-compatible client can consume.

Runs locally in Docker, unauthenticated on the inbound side, so a client can be
pointed at it with no credential setup.

## Source attribution

Every result from every tool carries the same `source` object. Two fields make
a result citable by whoever reads it:

- **`published_at`** — the document's publication date, in ISO form.
- **`download_url`** — a permanent, public link to the original PDF on the SFOE
  publication database, openable by anyone with no credentials.

`download_url` is `null` when a document could not be matched to a public
record with confidence. That is deliberate: a link to the wrong publication is
worse than no link, because a wrong citation does not look wrong to the reader.
Roughly 6% of the corpus currently resolves to `null`. Cite those by `title`
and `published_at`.

```jsonc
"source": {
  "title": "2025-08-01_fakten-zur-windenergie.pdf",
  "published_at": "2025-08-01",
  "download_url": "https://pubdb.bfe.admin.ch/de/publication/download/12259",
  "file_type": "PDF",
  "language": "en",
  "media_type": "image",
  "created_at": "2026-08-31T13:08:21Z",
  "last_updated_at": "2026-08-31T13:08:21Z"
}
```

## Tools

### `search_energy_knowledge`

Ad-hoc semantic search. Returns ranked, verbatim passages.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Natural-language search query |
| `max_results` | int | 5 | 1–25 |

Returns `{result_count, results}`, each result `{rank, score, text, source}`.

### `get_metric_timeline`

Queries once per year across a range, returning a flat array tagged with
`queried_year` — intended for tracking a metric's development over time.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `metric` | string | — | e.g. `"solar PV installed capacity"` |
| `start_year` | int | — | inclusive |
| `end_year` | int | — | inclusive |
| `max_results_per_year` | int | 3 | 1–25 |

Returns `{metric, start_year, end_year, result_count, data}`, where each `data`
entry is a search result plus `queried_year`.

Deliberately **not** done by this tool, and left to the calling agent:

- **No numeric extraction.** Figures stay inside the passage text.
- **No deduplication or reconciliation.** Several sources may report the same
  year differently; both are returned so the caller can compare them.
- **No visualization.** Charting is the client's concern.

At most 15 years per call (`MetricTimelineTool.MAX_TIMELINE_YEARS`); each year
is a separate sequential query, so wide ranges are slow.

### `get_chart_data`

Returns numeric series lifted out of charts and tables, ready to plot.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `topic` | string | — | e.g. `"Windenergie Produktion"` |
| `max_charts` | int | 5 | 1–15 |

Returns `{topic, probes_run, chart_count, charts}`. Each chart:

```jsonc
{
  "title": null,
  "columns": ["Year", "Wind_Energy_Production_TJ/a"],
  "precision": "estimated",
  "note": "Note: These values are estimated based on visual interpretation…",
  "row_count": 35,
  "rows": [{"Year": {"raw": "1990", "value": 1990.0, "min": null, "max": null, "kind": "exact"},
            "Wind_Energy_Production_TJ/a": {"raw": "2", "value": 2.0, "min": null, "max": null, "kind": "exact"}}],
  "raw_block": "Year,Wind_Energy_Production_TJ/a 1990,2 …",
  "score": 0.51,
  "source": { }
}
```

**`precision` is the field that matters.** `"exact"` means the numbers were
printed on the figure as data labels. `"estimated"` means they were read off
the figure by eye — either given as a `min`/`max` range, or delivered as
single numbers that the transcription's own `note` admits are estimates.
Callers must not present estimated values as precise figures, must not invent
the midpoint of a range, and should surface `note` alongside an estimated
series.

Coverage is opportunistic and **thin**: only a minority of figures were
transcribed with usable data, and in this corpus they cluster heavily in the
*Schweizerische Statistik der erneuerbaren Energien* series. A topic returning
zero charts is a normal outcome, not an error — fall back to
`search_energy_knowledge`.

## Known limitations

- **No page numbers.** The corpus carries no page-level metadata, so results
  cannot be cited by page. Fixing this would require re-ingesting the knowledge
  base with a parser that preserves page anchors — outside this service.
- **`language` is unreliable.** Most documents are German but are tagged `"en"`.
- **Chart data is sparse.** See `get_chart_data` above.
- Passages are extracted from PDFs including chart/table descriptions, so text
  quality varies.

## Running

Requires `CLIENT_ID` and `CLIENT_SECRET` in a gitignored `.env` at the repo root.

```bash
docker build -t open-energy-knowledge-gateway ./mcp-gateway
docker run -d --name energy-gateway --restart unless-stopped \
  -p 8000:8000 --env-file .env open-energy-knowledge-gateway
```

Endpoint: `http://localhost:8000/mcp` (streamable-HTTP).

Local dev without Docker, from the repo root:

```bash
.venv/bin/python mcp-gateway/dev_run.py
```

Tests (no network, no credentials needed):

```bash
.venv/bin/python -m pytest mcp-gateway/tests/ -q
```

### Claude Code

`.mcp.json` at the repo root registers the server as `energy-knowledge-gateway`.
Approve it once when prompted, then the tools appear natively.

## Layout

```
config.py           Config.from_env() — env-var configuration
knowledge_base.py   KnowledgeBaseClient: auth, query, response normalization
public_source.py    PublicSourceResolver: document -> public download URL
chart_parsing.py    pure parsing of embedded chart/table data blocks
tools/
  base.py           BaseTool — self-describing tool contract
  search.py         SearchEnergyKnowledgeTool
  timeline.py       MetricTimelineTool
  chart_data.py     ChartDataTool
  __init__.py       build_tools()
tests/              unit tests for the pure modules
server.py           entrypoint: wires config → client → tools → MCPServer
```

### Adding a tool

1. Subclass `BaseTool` in a new module under `tools/`.
2. Declare `name`, `title`, `description` as class attributes, and annotate every
   parameter of `run` with `Annotated[T, Field(description=...)]`. `register`
   feeds these straight into `tools/list`, so documentation cannot drift from the
   implementation.
3. Add it to `build_tools()`.

Query the knowledge base via `self.search(...)` rather than the client directly —
it translates failures into `ToolError`, which is the only exception type whose
message the MCP SDK preserves for the caller. Any other exception reaches the
caller as a bare `"Error executing tool <name>"` with no reason, which a model
cannot act on.

Everything reaching a caller — tool `title`/`description`, `Field` descriptions,
server `instructions`, and exception messages — describes the service on its own
terms and stays free of implementation detail. Internal comments and module
docstrings stay technically accurate.

## Internals

<!-- Maintainer-facing. Nothing here is exposed through MCP. -->

Inbound MCP requests are served locally. To answer them, `KnowledgeBaseClient`
obtains a Cognito access token via the OAuth 2.0 `client_credentials` flow
(cached in-process until ~30s before expiry, guarded by a lock), calls the
`bfe-public-knowledge___Retrieve` tool on the AWS Bedrock AgentCore Gateway, and
normalizes the result.

That raw response is doubly nested: the JSON-RPC envelope's
`result.content[0].text` is itself a JSON string containing `retrievalResults`.
Normalization parses it, flattens each hit into the `{score, text, source}`
shape above, sorts by score descending, truncates, resolves public download
URLs, then assigns `rank` from 1.

The S3 object URL that upstream returns is **not** publicly fetchable — it
403s without AWS credentials — so it is not exposed. `public_source.py` instead
matches each document against the public SFOE publication database by title and
date and returns a real, credential-free URL. Its module docstring explains the
scoring; the short version is that title similarity is scored bidirectionally
(one-directional coverage produced a false positive), publication-date agreement
is a bonus rather than a gate (a re-issued document legitimately changes date),
and PDFs are preferred over the companion spreadsheets that share a title and
date with their report. Lookups are cached in-process, including misses.
Resolution is best-effort and never raises: if it fails, the search still
returns its passages, just without links.

Upstream caps retrieval at 5 results per query regardless of what is requested,
which is why `get_chart_data` issues several query variants and pools them.

Configuration (`TOKEN_URL`, `GATEWAY_URL`, `CLIENT_ID`, `CLIENT_SECRET`, `HOST`,
`PORT`) comes from the environment; the two URLs default to the hackathon
sandbox endpoints.
