#!/usr/bin/env python3
"""Standard MCP stdio adapter for the open SFOE energy knowledge gateway.

The gateway is a remote MCP endpoint. Many MCP clients speak stdio only, so
this adapter sits in between and forwards JSON-RPC both ways.

It deliberately handles no credentials: the gateway it targets is open, so
there is no token to hold, log or leak. Point it at a protected gateway and
it will surface that gateway's own authorization error rather than pretend
to cope.

Tools are not hardcoded either. `tools/list` is answered by the gateway, so
whatever the gateway exposes is what the client sees.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

GATEWAY_URL = os.environ.get(
    "BFE_MCP_GATEWAY_URL",
    "https://bfe-energy-knowledge-open-v6rj5uttek."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp",
)
GATEWAY_PROTOCOL_VERSION = "2026-07-28"
CLIENT_NAME = "bfe-open-mcp-adapter"
CLIENT_VERSION = "1.0.0"
CURL_TIMEOUT_SEC = 130
FORWARDED_METHODS = frozenset({"tools/list", "tools/call"})


def gateway_meta() -> dict[str, Any]:
    """The _meta block the gateway requires on every request."""
    return {
        "io.modelcontextprotocol/protocolVersion": GATEWAY_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {
            "name": CLIENT_NAME,
            "version": CLIENT_VERSION,
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def build_headers(method: str, params: dict[str, Any]) -> list[str]:
    """Headers for one forwarded call.

    The gateway rejects any request without Mcp-Method, and rejects
    tools/call in particular without Mcp-Name.
    """
    headers = [
        "Content-Type: application/json",
        "Accept: application/json, text/event-stream",
        "MCP-Protocol-Version: " + GATEWAY_PROTOCOL_VERSION,
        "Mcp-Method: " + method,
    ]
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("tools/call requires a tool name")
        headers.append("Mcp-Name: " + name)
    return headers


def call_gateway(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Forward one JSON-RPC call to the gateway and return its result."""
    forwarded = dict(params)
    forwarded["_meta"] = gateway_meta()
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": forwarded}
    )

    args: list[str] = []
    for header in build_headers(method, forwarded):
        args += ["--header", header]

    # No --fail: it discards the response body, which is exactly where the
    # gateway explains why it refused.
    completed = subprocess.run(
        [
            "/usr/bin/curl",
            "--silent",
            "--show-error",
            "--request",
            "POST",
            "--write-out",
            "\n%{http_code}",
            "--data-binary",
            "@-",
            *args,
            GATEWAY_URL,
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=CURL_TIMEOUT_SEC,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"curl failed with exit {completed.returncode}: "
            f"{completed.stderr.strip() or 'no stderr'}"
        )

    payload, _, status = completed.stdout.rpartition("\n")
    try:
        response = json.loads(payload)
    except ValueError:
        raise RuntimeError(
            f"gateway returned HTTP {status.strip()} with a non-JSON body: "
            f"{payload[:200]}"
        ) from None

    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
    result_value = response.get("result")
    if not isinstance(result_value, dict):
        raise RuntimeError(f"gateway returned no result (HTTP {status.strip()})")
    return result_value


def send(message: dict[str, Any]) -> None:
    print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)


def result(request_id: Any, value: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id: Any, code: int, message: str) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "bfe-energy-knowledge",
                    "version": CLIENT_VERSION,
                },
                "instructions": (
                    "Search Swiss Federal Office of Energy publications. Every result "
                    "carries a download_url pointing at the original publication on "
                    "pubdb.bfe.admin.ch - cite that alongside the passage."
                ),
            },
        )
        return

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return

    if method == "ping":
        result(request_id, {})
        return

    if method in FORWARDED_METHODS:
        result(request_id, call_gateway(method, params))
        return

    if method in {"resources/list", "resources/templates/list"}:
        key = (
            "resourceTemplates" if method == "resources/templates/list" else "resources"
        )
        result(request_id, {key: []})
        return

    if method == "prompts/list":
        result(request_id, {"prompts": []})
        return

    if request_id is not None:
        error(request_id, -32601, "Method not found: " + str(method))


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id: Any = None
        try:
            message = json.loads(line)
            request_id = message.get("id")
            handle(message)
        except Exception as exc:
            print(f"BFE open MCP adapter error: {exc}", file=sys.stderr, flush=True)
            if request_id is not None:
                error(request_id, -32603, str(exc))


if __name__ == "__main__":
    main()
