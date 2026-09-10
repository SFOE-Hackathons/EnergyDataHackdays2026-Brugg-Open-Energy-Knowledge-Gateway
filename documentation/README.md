# Open Energy Gateway MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets an
LLM client (Claude Desktop, Claude Code, ...) query the Swiss Federal Office of Energy's
(SFOE/BFE) public energy knowledge base, and cross-check any figure against the original
public source PDFs on [pubdb.bfe.admin.ch](https://pubdb.bfe.admin.ch/de/suche).

It exposes three tools:

| Tool | Purpose |
|---|---|
| `ask_energy_question` | Retrieves cited passages from the SFOE Bedrock Knowledge Base via the AWS AgentCore Gateway. |
| `verify_source` | Fetches a specific publication directly from pubdb.bfe.admin.ch and returns the real page(s) matching a claim — for double-checking a citation or pulling precise data. |
| `get_time_series` | Repeats that lookup across a range of years for a recurring report series, so a multi-year question gets real fetched numbers instead of guessed/interpolated gaps. |

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

This opens the MCP Inspector, where you can see the three tools' schemas and call them
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

## Troubleshooting

- **Tool returns "Could not authenticate with the SFOE knowledge gateway"**: `CLIENT_ID`/
  `CLIENT_SECRET` are missing or wrong in `.env`.
- **Tool returns "The SFOE knowledge gateway request failed"**: the AgentCore Gateway is
  unreachable or returned an error — check the Gateway/Cognito URLs in `config.ini` are
  still correct.
- **`verify_source`/`get_time_series` say a publication "couldn't be found" or is
  "ambiguous"**: pubdb.bfe.admin.ch's search is a plain title/keyword match; try rephrasing
  the `document_title`/`topic` closer to the publication's actual title.
