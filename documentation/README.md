# Open Energy Gateway MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets an
LLM client (Claude Desktop, Claude Code, ...) query the Swiss Federal Office of Energy's
(SFOE/BFE) public energy knowledge base, and cross-check any figure against the original
public source PDFs on [pubdb.bfe.admin.ch](https://pubdb.bfe.admin.ch/de/suche).

It exposes eight tools:

| Tool | Purpose |
|---|---|
| `ask_energy_question` | Retrieves cited passages from the SFOE knowledge base via the AWS AgentCore Gateway's `search_energy_knowledge` tool. The default entry point for a general question. |
| `verify_source` | Fetches a specific publication directly from pubdb.bfe.admin.ch and returns the real page(s) matching a claim — for double-checking a citation, or pulling precise data straight from the original PDF. |
| `get_time_series` | Repeats `verify_source`-style lookups across a range of years for a recurring report series, downloading each year's edition, so a multi-year question gets real fetched numbers instead of guessed/interpolated gaps. |
| `get_metric_timeline` | Native, faster equivalent of `get_time_series` — queries the knowledge base directly once per year (no PDF download), capped at a 5-year span per call. |
| `get_chart_data` | Fetches genuinely structured, parsed chart/table data (real columns and typed values) for a topic, each chart honestly flagged "exact" (printed data label) or "estimated" (read off the chart visually). |
| `render_chart` | Renders a line/bar chart image (PNG) from numbers already extracted from another tool's result — a real visual, not just a description. |
| `generate_briefing` | One-call orchestrator: knowledge base findings + a cross-check of the top citation against its original PDF + (optionally) a numeric time series, bundled into a single response. |
| `explain_anomaly` | Given a numeric series already extracted, finds the biggest year-over-year change and automatically looks up what the knowledge base says about that year — turning a number into a story. |

`get_time_series`/`verify_source` and `get_metric_timeline`/`get_chart_data` are deliberately
two independent tiers, not redundant duplicates: the former reads the literal original PDF
page (slower, but undeniable ground truth), the latter queries the knowledge base natively
(fast, and for `get_chart_data`, gives real parsed numeric data with a precision flag). Use
the fast tier first, and the literal-PDF tier to cross-check a specific figure.

## Prerequisites

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/) installed and on your `PATH`
- Cognito `CLIENT_ID` / `CLIENT_SECRET` for the AgentCore Gateway (provided by whoever manages the sandbox infrastructure)

## Setup

```bash
cd open-energy-mcp
uv sync
cp .env.example .env
# edit .env and fill in CLIENT_ID / CLIENT_SECRET
```

`.env` holds credentials only and is gitignored. Non-secret configuration (the Cognito
token endpoint, the Gateway URL, the pubdb.bfe.admin.ch base URL) lives in `config.ini` at
the repo root and is checked into version control — edit it if any of those endpoints ever
change.

## Running it standalone (for development/testing)

```bash
uv run mcp dev src/energy_gateway_mcp/server.py
```

This opens the MCP Inspector, where you can see all eight tools' schemas and call them
directly without needing a full LLM client.

## Connecting to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and add an
entry under `mcpServers`:

```json
{
  "mcpServers": {
    "open-energy-gateway": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory",
        "/absolute/path/to/open-energy-mcp",
        "run",
        "energy-gateway-mcp"
      ]
    }
  }
}
```

Notes:
- Use the **absolute path** to the `uv` binary (find it with `which uv`) — GUI apps on macOS
  often launch with a minimal `PATH` that doesn't include Homebrew's `bin` directory, so a
  bare `"command": "uv"` can fail with "command not found".
- No secrets need to go in this config: `--directory` sets the subprocess's working
  directory to the repo root, so `python-dotenv` picks up `.env` from there automatically.
- Restart Claude Desktop after editing the config.

## Connecting to Claude Code

```bash
claude mcp add open-energy-gateway -- /absolute/path/to/uv --directory /absolute/path/to/open-energy-mcp run energy-gateway-mcp
```

Or add an equivalent entry to a project-level `.mcp.json`:

```json
{
  "mcpServers": {
    "open-energy-gateway": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/open-energy-mcp", "run", "energy-gateway-mcp"]
    }
  }
}
```

If this fails with `Cannot add MCP server "...": not allowed by enterprise policy`, your
organization's Claude Code admin needs to allow custom/local MCP servers — this is a
separate policy from allowing pre-approved remote connectors (e.g. Atlassian), so ask
specifically for the former if the latter is already enabled.

## Connecting to ChatGPT

**This currently isn't possible without extra work**, and it's worth understanding why
before trying: ChatGPT's MCP support (Settings → Apps → Developer mode → Create app) only
accepts a **remote, HTTPS-reachable** MCP server URL — it cannot spawn a local process the
way Claude Desktop/Code do, so a plain `command`/`args` config like the ones above has no
equivalent in ChatGPT.

This server currently only implements the `stdio` transport (see `main()` in
`src/energy_gateway_mcp/server.py`). To make it reachable from ChatGPT you would need to:

1. Run it with a network transport instead of stdio — the underlying `mcp` SDK supports
   this (`mcp.run(transport="streamable-http")`), but the server doesn't currently expose a
   way to choose that at startup.
2. Expose it at a public HTTPS URL — for quick testing, a tunnel (e.g. `ngrok`,
   Cloudflare Tunnel) pointed at a local `streamable-http` instance; for anything durable,
   an actual deployment (Cloudflare Workers, Fly.io, a small VM, etc.) behind HTTPS.
3. Add authentication in front of it — a public HTTP endpoint holding a tool that calls out
   to the Cognito-secured Gateway needs its own access control, since anyone who finds the
   URL could otherwise use your Cognito credentials by proxy.

None of that is currently built. Treat this as a known gap / future-work item rather than
a supported path today.

## Tool selection reliability

Whether the LLM actually reaches for these tools depends heavily on how a question is
phrased, and this is not something the server can fully control:

- **Explicit tool naming, or mentioning SFOE/Switzerland/official statistics, reliably
  works** — e.g. "According to the Schweizerische Elektrizitätsstatistik series, ..." or
  "Use ask_energy_question to find out ...".
- **Fully generic phrasing is unreliable.** A question like "How did Swiss hydropower
  production change between 2020 and 2024?" can go to the client's own general web search
  instead of this server's tools, even after strengthening `ask_energy_question`'s docstring
  to explicitly claim priority over web search for exactly this kind of question — tested
  live, no change in outcome. Tool selection is shaped by more than one server's tool
  descriptions (the client's own system prompt, competing built-in tools, etc.), so this
  looks like a ceiling docstring wording alone can't push past.

For a reliable demo, phrase prompts to name the tool, the SFOE/Swiss official statistics,
or a specific publication series rather than asking a fully generic factual question.

## Example test prompts

Two prompts per tool: an **explicit** one that names the tool directly, and an **implicit**
one that doesn't. They test different things, and both are worth running:

- **Explicit prompts isolate the tool itself.** If an explicit call fails, the bug is in
  this server (wiring, credentials, the underlying API, formatting) — not in the model's
  behavior. Use these first, and whenever debugging.
- **Implicit prompts test real-world tool selection.** A real user won't say "use
  get_metric_timeline"; they'll ask a natural question. Since tool selection is
  probabilistic and only partly steerable via docstrings (see "Tool selection reliability"
  above), the only way to know whether a given natural phrasing actually triggers a tool is
  to try it without naming it. Use these to build intuition for how to phrase things in a
  live demo, and to catch regressions in tool-selection behavior after a docstring change.

Always test in a **fresh chat** — an existing conversation's history can bias which tool
gets picked, masking the real answer either way. Most of the prompts below were actually run
against a live client this session; `get_metric_timeline`/`get_chart_data` are the exception
— new this session, verified directly and through the MCP harness, but not yet run through a
live client conversation.

| Tool | Explicit prompt | Implicit prompt | What testing found |
|---|---|---|---|
| `ask_energy_question` | "Use ask_energy_question to find out what role hydropower plays in Switzerland's electricity supply." | "What role does hydropower play in Switzerland's electricity supply?" | The original, most-exercised tool; reliable once the Gateway's `search_energy_knowledge` API was matched correctly. |
| `verify_source` | "Use verify_source to check what the Schweizerische Elektrizitätsstatistik 2022 report says about Switzerland's electricity exports and imports with Germany in 2022." | "How much electricity did Switzerland import and export with Germany in 2022?" (no tool or publication named at all) | The implicit form is the hardest version and the one that mattered most: the model self-diagnosed garbled `ask_energy_question` output and autonomously fell back to `verify_source` without being told to — full success, and the real-world case this tool was built for. |
| `get_time_series` | "Use get_time_series to fetch Switzerland's electricity trade with Italy from the Schweizerische Elektrizitätsstatistik series for 2019 through 2022." | "According to the Schweizerische Elektrizitätsstatistik series, how did Switzerland's electricity trade with Italy change between 2022 and 2024?" | This exact implicit prompt, re-run across several fix iterations, is what surfaced three real bugs in sequence: a pubdb search false-positive ("Formular" document vs. the real edition), an English/German keyword-language mismatch in page-matching, and a snippet-truncation cap cutting off the most recent year's row. Each fix was verified by re-running the identical prompt. |
| `get_metric_timeline` | "Use get_metric_timeline to look up Swiss hydropower production for 2020 through 2024." | "How has Swiss hydropower production changed over the last five years?" | Added in the same session as the Gateway migration; verified directly (Python calls + MCP round-trip) but **not yet exercised through a live client conversation** — treat as harness-tested, not field-tested. |
| `get_chart_data` | "Use get_chart_data to pull structured chart data on Switzerland's electricity trade with Italy." | "Can you show me the actual chart data behind Switzerland's electricity trade with Italy over the years?" | Same status as `get_metric_timeline` — harness-tested only, not yet run live. |
| `render_chart` | "Please use render_chart to chart those same Italy export/import figures again — 2022: 20461/1089, 2023: 21467/1046, 2024: 22060/1404." | "...Show me a chart of it." (appended to an Italy trade question) | Surfaced two client-side (not server-side) issues: a one-time multi-minute cold start (macOS Gatekeeper/matplotlib font-cache scan of freshly-installed wheels, confirmed one-off by a fast second call) and a known Claude Desktop/claude.ai limitation where MCP `ImageContent` results are collapsed by default and not auto-surfaced in the visible reply — the image was real and correct once the tool-call accordion was expanded. |
| `generate_briefing` | "Use generate_briefing to give me a briefing on Switzerland's electricity trade with Italy, using the Schweizerische Elektrizitätsstatistik series from 2022 to 2024." | "Give me a full briefing on Switzerland's electricity trade with Italy from 2022 to 2024 — background, the actual numbers, and make sure the figures are verified against the original source." | The implicit form is a genuine success case: the model chose this single tool over its old habit of chaining `ask_energy_question`/`verify_source`/`get_time_series` itself. |
| `explain_anomaly` | "Fetch Swiss hydropower production figures for 2020 to 2024, then use explain_anomaly to tell me what happened in the year with the biggest change." | "How did Swiss hydropower production change between 2020 and 2024, and what happened in the most unusual year?" | The explicit form works correctly end-to-end. The implicit form is a documented **failure case**: it went to the client's general web search both before and after strengthening `ask_energy_question`'s docstring to explicitly claim priority — evidence that docstring wording alone has a real ceiling on tool-selection behavior. |

## Troubleshooting

- **Tool returns "Could not authenticate with the SFOE knowledge gateway"**: `CLIENT_ID`/
  `CLIENT_SECRET` are missing or wrong in `.env`.
- **Tool returns "The SFOE knowledge gateway request failed"**: the AgentCore Gateway is
  unreachable or returned an error — check the Gateway/Cognito URLs in `config.ini` are
  still correct.
- **`verify_source`/`get_time_series` say a publication "couldn't be found" or is
  "ambiguous"**: pubdb.bfe.admin.ch's search is a plain title/keyword match; try rephrasing
  the `document_title`/`topic` closer to the publication's actual title.
