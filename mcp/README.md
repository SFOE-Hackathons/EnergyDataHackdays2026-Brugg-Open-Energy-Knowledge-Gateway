# MCP stdio adapter for the Open Energy Knowledge Gateway

`bfe_mcp_proxy.py` exposes the SFOE AgentCore Gateway to any MCP client that speaks
**stdio** (Claude Code, Codex, MCP Inspector, custom clients).

The Gateway itself is a remote MCP endpoint protected by Cognito OAuth 2.0
(`client_credentials`). Many MCP clients cannot yet attach a bearer token to a
remote MCP server, so this adapter sits in between: it speaks plain stdio MCP to
the client, obtains and caches a Cognito access token, and forwards `tools/call`
to the Gateway.

```text
MCP client ──stdio──▶ bfe_mcp_proxy.py ──HTTPS + Bearer──▶ AgentCore Gateway ──▶ Bedrock KB
```

It exposes a single tool, `bfe-public-knowledge___Retrieve`, which returns relevant
passages from the public SFOE document collection together with sources, metadata
and relevance scores.

## Requirements

- Python 3.10 or newer (uses PEP 604 unions). No third-party packages.
- `curl` available on `PATH` (`curl.exe` is supported on Windows).

## Configuration

No credentials are stored in this repository. The adapter reads them at startup:

| Variable | Required | Purpose |
|---|---|---|
| `BFE_MCP_CLIENT_ID` | yes | Cognito app client id. Also the Keychain account name. |
| `BFE_MCP_TOKEN_URL` | yes | Cognito token endpoint, `https://<domain>/oauth2/token`. |
| `BFE_MCP_GATEWAY_URL` | no | Defaults to the sandbox Gateway published in the repository readme. |
| `BFE_MCP_KEYCHAIN_SERVICE` | macOS only, no | Keychain service name. Defaults to `codex-mcp-bfe-public-knowledge`. |
| `BFE_MCP_CLIENT_SECRET` | Windows/Linux, yes | Cognito client secret. Prefer this name; `CLIENT_SECRET` remains supported for compatibility. |

The adapter selects the secret store from the operating system:

| Operating system | Secret source |
|---|---|
| macOS | Keychain lookup by (`BFE_MCP_CLIENT_ID`, `BFE_MCP_KEYCHAIN_SERVICE`) |
| Windows | `BFE_MCP_CLIENT_SECRET` environment variable, normally loaded from the gitignored `.env` file |
| Linux/other | `BFE_MCP_CLIENT_SECRET` environment variable |

### Where to get these values

Client id and client secret are handed out by the organisers during the Hackdays.
If you have access to the sandbox AWS account, you can also read them yourself:

1. Sign in to the AWS console for the sandbox account (the login URL is in the
   repository readme) and switch the region to **eu-central-1**.
2. Open **Amazon Cognito ▸ User pools ▸ App integration ▸ App clients**.
   The **Client ID** is shown there; **Show client secret** reveals the secret.
3. On the same **App integration** tab, find the **Cognito domain**. The token
   endpoint is that domain with `/oauth2/token` appended.
4. The Gateway URL is under **Bedrock AgentCore ▸ Gateways ▸ `sandbox-bfe-public-kb`**,
   and is also printed in the repository readme.

> Never commit the client secret, an access token, or a populated `.mcp.json`
> containing either of them.

### macOS: store the secret in the Keychain

Run this and paste the secret at the prompt, so it does not end up in your shell
history. Replace the account name with your actual client id:

```bash
security add-generic-password -a "<CLIENT_ID>" -s codex-mcp-bfe-public-knowledge -w
```

Verify it can be read back:

```bash
security find-generic-password -w -a "<CLIENT_ID>" -s codex-mcp-bfe-public-knowledge
```

### Windows: store the secret in `.env`

Create `.env` in the repository root. It is ignored by Git:

```dotenv
BFE_MCP_CLIENT_ID=<CLIENT_ID>
BFE_MCP_TOKEN_URL=https://<domain>.auth.eu-central-1.amazoncognito.com/oauth2/token
BFE_MCP_CLIENT_SECRET=<CLIENT_SECRET>
```

The checked-in `.vscode/mcp.json` loads this file. Adjust its Python executable
path if your virtual environment is located elsewhere. Do not commit `.env`.

On Linux, export the same three variables in the environment that starts the MCP
client instead.

## Wiring it into a client

### Claude Code

Create `.mcp.json` in your working directory:

```json
{
  "mcpServers": {
    "bfe-public-knowledge": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/mcp/bfe_mcp_proxy.py"],
      "env": {
        "BFE_MCP_CLIENT_ID": "<CLIENT_ID>",
        "BFE_MCP_TOKEN_URL": "https://<domain>.auth.eu-central-1.amazoncognito.com/oauth2/token"
      }
    }
  }
}
```

On Windows or Linux, also make `BFE_MCP_CLIENT_SECRET` available to the process.
On macOS, leave it out: the adapter reads the secret from Keychain.

### Codex

In `.codex/config.toml`:

```toml
[mcp_servers.bfe_public_knowledge]
command = "python3"
args = ["mcp/bfe_mcp_proxy.py"]
cwd = "/absolute/path/to/repo"
enabled_tools = ["bfe-public-knowledge___Retrieve"]
startup_timeout_sec = 30
tool_timeout_sec = 150
enabled = true

[mcp_servers.bfe_public_knowledge.env]
BFE_MCP_CLIENT_ID = "<CLIENT_ID>"
BFE_MCP_TOKEN_URL = "https://<domain>.auth.eu-central-1.amazoncognito.com/oauth2/token"
```

On Windows or Linux, also make `BFE_MCP_CLIENT_SECRET` available to Codex. On
macOS, leave it out so the adapter uses Keychain.

Retrieval can take a while on long queries, so keep the tool timeout generous.

## Trying it without a client

The adapter is a plain line-based JSON-RPC process, so you can drive it by hand:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | BFE_MCP_CLIENT_ID=... BFE_MCP_TOKEN_URL=... python3 mcp/bfe_mcp_proxy.py
```

## Behaviour notes

- **Token caching.** The access token is cached in memory until 60 s before expiry.
  If a request fails while using a cached token, the adapter invalidates it, fetches
  a fresh one and retries exactly once — this covers tokens revoked server-side.
- **Errors.** Failures are reported as JSON-RPC errors on the offending request and
  logged to stderr. The process stays alive so the client does not lose the server.
- **Read-only.** The single tool is annotated `readOnlyHint` and `idempotentHint`.

## Known limitation: source links point at S3

Retrieval results currently cite their sources as S3 URIs
(`documentId`, `location.s3Location.uri`, `metadata._source_uri`). That bucket is
**not public**, so those links return `403` for end users, and Bedrock's
`_source_uri` is derived from the S3 location rather than from the original
publication.

The design for resolving these to real `pubdb.bfe.admin.ch` PDF links is in
[`docs/2026-09-10-bfe-source-links-design.md`](../docs/2026-09-10-bfe-source-links-design.md).
