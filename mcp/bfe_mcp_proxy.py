#!/usr/bin/env python3
"""Standard MCP stdio adapter for the SFOE AgentCore Knowledge Gateway."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from typing import Any


# Deployment-specific identifiers are read from the environment so that no
# credentials are committed to this public repository. See mcp/README.md.
CLIENT_ID = os.environ.get("BFE_MCP_CLIENT_ID", "")
TOKEN_URL = os.environ.get("BFE_MCP_TOKEN_URL", "")
KEYCHAIN_SERVICE = os.environ.get(
    "BFE_MCP_KEYCHAIN_SERVICE", "codex-mcp-bfe-public-knowledge"
)
# The Gateway URL is published in this repository's readme, so it is safe as a
# default while still being overridable for other deployments.
GATEWAY_URL = os.environ.get(
    "BFE_MCP_GATEWAY_URL",
    "https://sandbox-bfe-public-kb-8thmswsvit."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp",
)
GATEWAY_PROTOCOL_VERSION = "2026-07-28"
TOOL_NAME = "bfe-public-knowledge___Retrieve"
TOKEN_EXPIRY_MARGIN_SEC = 60.0
TOKEN_DEFAULT_TTL_SEC = 3600.0

_token_cache: tuple[str, float] | None = None


def run_curl(args: list[str], *, body: str) -> dict[str, Any]:
    result = subprocess.run(
        ["/usr/bin/curl", "--silent", "--show-error", "--fail", *args],
        input=body,
        check=True,
        capture_output=True,
        text=True,
        timeout=130,
    )
    return json.loads(result.stdout)


def read_client_secret() -> str:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-w",
            "-a",
            CLIENT_ID,
            "-s",
            KEYCHAIN_SERVICE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    secret = result.stdout.rstrip("\n")
    if not secret:
        raise RuntimeError("Cognito client secret is empty in macOS Keychain")
    return secret


def require_config() -> None:
    missing = [
        name
        for name, value in (
            ("BFE_MCP_CLIENT_ID", CLIENT_ID),
            ("BFE_MCP_TOKEN_URL", TOKEN_URL),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing environment variable(s): "
            + ", ".join(missing)
            + ". See mcp/README.md for where to obtain these values."
        )


def fetch_access_token() -> str:
    global _token_cache

    if _token_cache is not None and time.monotonic() < _token_cache[1]:
        return _token_cache[0]

    require_config()

    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": read_client_secret(),
        }
    )
    response = run_curl(
        [
            "--request",
            "POST",
            "--header",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-binary",
            "@-",
            TOKEN_URL,
        ],
        body=form,
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Cognito response did not contain an access token")

    expires_in = response.get("expires_in")
    ttl = (
        float(expires_in)
        if isinstance(expires_in, (int, float))
        else TOKEN_DEFAULT_TTL_SEC
    )
    _token_cache = (token, time.monotonic() + max(ttl - TOKEN_EXPIRY_MARGIN_SEC, 0.0))
    return token


def invalidate_access_token() -> None:
    global _token_cache

    _token_cache = None


def call_gateway(question: str, request_id: Any) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            "arguments": {"retrievalQuery": {"text": question}},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": GATEWAY_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "codex-bfe-mcp-adapter",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    body = json.dumps(payload)

    def post(token: str) -> dict[str, Any]:
        return run_curl(
            [
                "--request",
                "POST",
                "--header",
                "Authorization: Bearer " + token,
                "--header",
                "Content-Type: application/json",
                "--header",
                "Accept: application/json, text/event-stream",
                "--header",
                "MCP-Protocol-Version: " + GATEWAY_PROTOCOL_VERSION,
                "--header",
                "Mcp-Method: tools/call",
                "--header",
                "Mcp-Name: " + TOOL_NAME,
                "--data-binary",
                "@-",
                GATEWAY_URL,
            ],
            body=body,
        )

    token_was_cached = _token_cache is not None and time.monotonic() < _token_cache[1]
    try:
        response = post(fetch_access_token())
    except subprocess.CalledProcessError:
        if not token_was_cached:
            raise
        invalidate_access_token()
        response = post(fetch_access_token())
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Gateway response did not contain an MCP result")
    return result


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
        requested_version = params.get("protocolVersion", "2025-06-18")
        result(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "bfe-public-knowledge", "version": "1.0.0"},
                "instructions": (
                    "Use the retrieval tool for questions about Swiss energy. "
                    "Answer from the returned SFOE passages and cite their source URLs."
                ),
            },
        )
        return

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return

    if method == "ping":
        result(request_id, {})
        return

    if method == "tools/list":
        result(
            request_id,
            {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": (
                            "Retrieve relevant passages, sources, metadata, and relevance "
                            "scores from the public Swiss Federal Office of Energy knowledge base."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "retrievalQuery": {
                                    "type": "object",
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "description": "Question or search query.",
                                        }
                                    },
                                    "required": ["text"],
                                    "additionalProperties": False,
                                }
                            },
                            "required": ["retrievalQuery"],
                            "additionalProperties": False,
                        },
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                            "openWorldHint": True,
                        },
                    }
                ]
            },
        )
        return

    if method == "tools/call":
        if params.get("name") != TOOL_NAME:
            error(request_id, -32601, "Unknown tool")
            return
        arguments = params.get("arguments") or {}
        query = arguments.get("retrievalQuery") or {}
        question = query.get("text")
        if not isinstance(question, str) or not question.strip():
            error(request_id, -32602, "retrievalQuery.text must be a non-empty string")
            return
        result(request_id, call_gateway(question.strip(), request_id))
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
            print(f"BFE MCP adapter error: {exc}", file=sys.stderr, flush=True)
            if request_id is not None:
                error(request_id, -32603, str(exc))


if __name__ == "__main__":
    main()
