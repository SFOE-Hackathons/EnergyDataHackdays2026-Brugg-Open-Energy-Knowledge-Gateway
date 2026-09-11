# MCP stdio adapter for the open energy knowledge gateway

`bfe_open_mcp_proxy.py` exposes the SFOE energy knowledge gateway to any MCP client
that speaks **stdio** (Claude Code, Codex, MCP Inspector, custom clients).

The gateway is a remote MCP endpoint. Several MCP clients cannot yet talk to a remote
server directly, so this adapter sits in between: plain stdio JSON-RPC on one side,
HTTPS on the other.

```text
MCP client ──stdio──▶ bfe_open_mcp_proxy.py ──HTTPS──▶ AgentCore Gateway ──▶ Bedrock KB
```

## No credentials

The gateway it targets is **open** — it answers without an `Authorization` header — so
this adapter holds no client id, no secret and no token. There is nothing to store,
nothing to rotate, and nothing that can leak into a log or a client transcript.

Point it at a protected gateway and it will surface that gateway's own authorization
error rather than pretend to cope.

## Requirements

- Python 3.10 or newer. No third-party packages.
- `curl` at `/usr/bin/curl`.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `BFE_MCP_GATEWAY_URL` | no | Gateway endpoint. Defaults to the open SFOE gateway. |

## Wiring it into a client

### Claude Code

`.mcp.json` in your working directory:

```json
{
  "mcpServers": {
    "bfe-energy-knowledge": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/mcp/bfe_open_mcp_proxy.py"]
    }
  }
}
```

### Codex

`.codex/config.toml`:

```toml
[mcp_servers.bfe_energy_knowledge]
command = "python3"
args = ["mcp/bfe_open_mcp_proxy.py"]
cwd = "/absolute/path/to/repo"
startup_timeout_sec = 30
tool_timeout_sec = 150
enabled = true
```

Retrieval can be slow on long queries, so keep the tool timeout generous.

## Tools

Tool names are **not** hardcoded. `tools/list` is answered by the gateway itself, so
whatever it exposes is what the client sees. At the time of writing that is:

| Tool | Purpose |
|---|---|
| `bfe-energy___search_energy_knowledge` | Search SFOE publications |
| `bfe-energy___get_chart_data` | Numeric series from charts and tables |
| `bfe-energy___get_metric_timeline` | One metric across a range of years |

## Source attribution

Every search result references an entry in a `sources` block by `source_id`, and each
source carries the link to the original publication:

```json
"s1": {
  "title": "2026-05-04_stand-der-wasserkraftnutzung-in-der-schweiz-31-dezember-2025.pdf",
  "published_at": "2026-05-04",
  "download_url": "https://pubdb.bfe.admin.ch/de/publication/download/12601"
}
```

`download_url` points at `pubdb.bfe.admin.ch`, so citations resolve to a PDF anyone can
open. Cite that URL alongside the passage — the passage alone is not attributable.

Pass `source_fields: "all"` to a search call for `file_type`, `language`, `created_at`
and `last_updated_at`. They are constant or near-constant across this corpus, so the
default `"core"` omits them.

## Trying it without a client

The adapter is a line-based JSON-RPC process, so you can drive it by hand:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp/bfe_open_mcp_proxy.py
```

## Behaviour notes

- **Errors are not swallowed.** `curl` runs without `--fail`, because that flag discards
  the response body — which is exactly where the gateway explains why it refused. A
  gateway error reaches the client with its own message; a transport failure reports
  curl's exit code and stderr, never the command line.
- **The process stays alive** after a failed request, so the client does not lose the
  server.
- **Unsupported methods** (`resources/*`, `prompts/list`) return empty results rather
  than errors, which keeps strict clients happy.

## Tests

```bash
cd mcp && python3 -m pytest test_open_proxy.py -v
```

Twelve tests, no network access.
