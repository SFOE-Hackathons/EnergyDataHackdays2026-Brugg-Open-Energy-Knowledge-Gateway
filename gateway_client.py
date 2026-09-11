"""A minimal MCP client for the AgentCore Gateway, shared by the scripts here.

The three scripts at the repo root (list_tools.py, query_gateway.py,
test_gateway.py) each used to carry their own copy of the Cognito token
exchange and the JSON-RPC envelope. All three copies had drifted onto a
gateway and a Cognito domain that no longer exist, and nothing failed loudly
enough to notice -- an obsolete TOKEN_URL returns a perfectly valid 200 from
a pool that simply is not the one the gateway trusts.

So configuration comes from the environment (or a gitignored .env) and lives
in exactly one place: .env.example documents it, ./infra/create-gateway.sh
--show prints the live values.

GATEWAY_URL is the only variable that is actually required. The gateway is
deployed open (authorizerType NONE), so the Cognito trio is optional and
unset by default; see fetch_access_token.

This is a demonstration client, not the way to consume the gateway in
earnest. A real client should use an MCP SDK, which handles session
management, streaming and reconnection. This exists so the endpoint can be
exercised with nothing but `requests`.
"""

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

# The version this client speaks. It must be one of the gateway's
# `supportedVersions` (see infra/create-gateway.sh) or the request is refused
# outright with "Unsupported protocol version" -- the negotiation is strict,
# not best-effort.
MCP_PROTOCOL_VERSION = "2026-07-28"

# The AgentCore Gateway prefixes every tool with its target's name.
TARGET_NAME = os.environ.get("GATEWAY_TARGET_NAME", "bfe-energy")

CLIENT_INFO = {"name": "bfe-hackathon-client", "version": "1.0.0"}


class GatewayError(RuntimeError):
    """The gateway returned a JSON-RPC error, or a tool reported a failure."""


def _require(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(
            f"{name} is not set. Copy .env.example to .env and fill it in "
            f"(see the file for where each value comes from)."
        ) from None


def qualified(tool_name: str) -> str:
    """Prefix a server-side tool name with the gateway target it lives on."""
    return f"{TARGET_NAME}___{tool_name}"


def fetch_access_token() -> str | None:
    """Exchange the Cognito client credentials for a bearer token, if there are any.

    Returns None when no credentials are configured, and that is the normal
    case: the gateway is deployed with authorizerType NONE, so there is no
    token to send and `call` omits the Authorization header entirely. Sending
    a bearer token to an open gateway is not an error either -- it is ignored.

    Partial configuration is treated as a mistake rather than as "open": if
    any of the three variables is set, all three must be. A half-filled .env
    means someone meant to authenticate, and silently dropping the header
    would turn that into a confusing 403-or-worse further down.

    A 200 here proves only that the credentials are valid for SOME pool. More
    than one pool exists in this account, and a token from the wrong one is
    issued happily and then rejected by the gateway with 403 -- so a failure
    at this step and a failure at the next one mean very different things.
    """
    configured = [n for n in ("TOKEN_URL", "CLIENT_ID", "CLIENT_SECRET") if os.environ.get(n)]
    if not configured:
        return None

    response = requests.post(
        _require("TOKEN_URL"),
        data={
            "grant_type": "client_credentials",
            "client_id": _require("CLIENT_ID"),
            "client_secret": _require("CLIENT_SECRET"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _parse(response: requests.Response) -> dict:
    """Read a JSON-RPC response that may arrive as JSON or as SSE.

    The gateway is configured with enableResponseStreaming false, so this is
    normally plain JSON. The SSE branch is here because that setting is one
    line in infra/create-gateway.sh, and flipping it should not turn every
    script at the repo root into a JSONDecodeError.
    """
    if "text/event-stream" in response.headers.get("Content-Type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise GatewayError("event-stream response carried no data frame")
    return response.json()


def call(method: str, params: dict, access_token: str | None = None, timeout: int = 120) -> dict:
    """Make one JSON-RPC call and return its `result`.

    `access_token` is optional because the gateway is open; pass one only when
    it has been switched back to CUSTOM_JWT.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        # Routing hints AgentCore uses to dispatch without parsing the body.
        "Mcp-Method": method,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]

    payload = {
        "jsonrpc": "2.0",
        "id": method,
        "method": method,
        "params": {
            **params,
            # Required from protocol version 2026-07-28 onward: a tools/call
            # without a _meta block is rejected, which reads as a malformed
            # request rather than a version problem.
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }

    response = requests.post(
        _require("GATEWAY_URL"), headers=headers, json=payload, timeout=timeout
    )
    response.raise_for_status()
    body = _parse(response)

    if "error" in body:
        raise GatewayError(json.dumps(body["error"], indent=2, ensure_ascii=False))
    return body.get("result", {})


def call_tool(tool_name: str, arguments: dict, access_token: str | None = None) -> dict:
    """Call one tool by its unqualified name and return its parsed content.

    A tool failure comes back as a successful JSON-RPC response carrying
    `isError`, not as a JSON-RPC error, so it has to be checked separately or
    an error message is printed as if it were a result.
    """
    result = call(
        "tools/call",
        {"name": qualified(tool_name), "arguments": arguments},
        access_token,
    )

    if result.get("isError"):
        raise GatewayError(_text(result))

    # Tools here return JSON, which MCP transports as text. structuredContent
    # is the parsed form when the server provides it; otherwise parse the text.
    if "structuredContent" in result:
        return result["structuredContent"]
    try:
        return json.loads(_text(result))
    except json.JSONDecodeError:
        return {"text": _text(result)}


def _text(result: dict) -> str:
    return "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if block.get("type") == "text"
    )
