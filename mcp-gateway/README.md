# Open Energy Knowledge Gateway — MCP Server

An MCP server exposing official Swiss federal energy publications (Swiss Federal
Office of Energy, SFOE / BFE) as structured, source-attributed tools that any
MCP-compatible client can consume.

Runs locally in Docker, unauthenticated on the inbound side, so a client can be
pointed at it with no credential setup.

## Tools

### `search_energy_knowledge`

Ad-hoc semantic search. Returns ranked, verbatim passages with full source
attribution.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Natural-language search query |
| `max_results` | int | 5 | 1–25 |

```jsonc
{
  "result_count": 2,
  "results": [
    {
      "rank": 1,
      "score": 0.49,
      "text": "…verbatim passage…",
      "source": {
        "title": "2026-05-04_stand-der-wasserkraftnutzung….pdf",
        "s3_uri": "s3://sandbox-bfe-public-data-pdf/….pdf",
        "download_url": "https://….s3.eu-central-1.amazonaws.com/….pdf",
        "file_type": "PDF",
        "language": "en",
        "created_at": "2026-08-31T13:08:21Z",
        "last_updated_at": "2026-08-31T13:08:21Z"
      }
    }
  ]
}
```

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
entry has the same shape as a search result plus `queried_year`.

Deliberately **not** done by this tool, and left to the calling agent:

- **No numeric extraction.** Figures stay inside the passage text.
- **No deduplication or reconciliation.** Several sources may report the same
  year differently; both are returned so the caller can compare them.
- **No visualization.** Charting is the client's concern.

At most 15 years per call (`MetricTimelineTool.MAX_TIMELINE_YEARS`); each year
is a separate sequential query, so wide ranges are slow.

## Known limitations

- **No page numbers.** The corpus carries no page-level metadata, so results
  cannot be cited by page. Cite by document title and `download_url` instead.
  Fixing this would require re-ingesting the knowledge base with a parser that
  preserves page anchors — outside this service.
- **`language` is unreliable.** Most documents are German but are tagged `"en"`.
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

Smoke test:

```bash
.venv/bin/python mcp-gateway/client_example.py "Wasserkraft Anteil Stromerzeugung"
```

### Claude Code

`.mcp.json` at the repo root registers the server as `energy-knowledge-gateway`.
Approve it once when prompted, then the tools appear natively.

## Layout

```
config.py           Config.from_env() — env-var configuration
knowledge_base.py   KnowledgeBaseClient: auth, query, response normalization
tools/
  base.py           BaseTool — self-describing tool contract
  search.py         SearchEnergyKnowledgeTool
  timeline.py       MetricTimelineTool
  __init__.py       build_tools()
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
shape above, sorts by score descending, truncates, then assigns `rank` from 1.

Configuration (`TOKEN_URL`, `GATEWAY_URL`, `CLIENT_ID`, `CLIENT_SECRET`, `HOST`,
`PORT`) comes from the environment; the two URLs default to the hackathon
sandbox endpoints.
