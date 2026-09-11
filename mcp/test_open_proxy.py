"""Unit tests for the open-gateway stdio adapter. No network access."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import bfe_open_mcp_proxy as proxy
import pytest


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_meta_carries_the_protocol_version() -> None:
    meta = proxy.gateway_meta()
    assert (
        meta["io.modelcontextprotocol/protocolVersion"]
        == proxy.GATEWAY_PROTOCOL_VERSION
    )
    assert meta["io.modelcontextprotocol/clientInfo"]["name"] == proxy.CLIENT_NAME


def test_every_call_carries_mcp_method() -> None:
    headers = proxy.build_headers("tools/list", {})
    assert "Mcp-Method: tools/list" in headers
    assert not any(h.startswith("Mcp-Name:") for h in headers)


def test_tools_call_also_carries_mcp_name() -> None:
    headers = proxy.build_headers("tools/call", {"name": "some___tool"})
    assert "Mcp-Method: tools/call" in headers
    assert "Mcp-Name: some___tool" in headers


def test_tools_call_without_a_name_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="requires a tool name"):
        proxy.build_headers("tools/call", {})


def test_result_is_returned_and_meta_is_injected(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        seen["body"] = json.loads(kwargs["input"])
        seen["argv"] = argv
        return completed(json.dumps({"result": {"tools": []}}) + "\n200")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert proxy.call_gateway("tools/list", {}) == {"tools": []}
    assert "_meta" in seen["body"]["params"]


def test_gateway_error_body_reaches_the_caller(monkeypatch) -> None:
    """The reason the gateway refused must not be swallowed."""
    body = json.dumps({"error": {"code": -32602, "message": "Unknown tool: x"}})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(body + "\n400"))

    with pytest.raises(RuntimeError, match="Unknown tool: x"):
        proxy.call_gateway("tools/list", {})


def test_non_json_body_is_reported_with_its_status(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: completed("<html>gateway down</html>\n502")
    )
    with pytest.raises(RuntimeError, match="502"):
        proxy.call_gateway("tools/list", {})


def test_curl_failure_reports_stderr_not_the_command(monkeypatch) -> None:
    """A failure message must never carry the argv, which can hold headers."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: completed("", returncode=6, stderr="Could not resolve host"),
    )
    with pytest.raises(RuntimeError) as raised:
        proxy.call_gateway("tools/list", {})

    message = str(raised.value)
    assert "Could not resolve host" in message
    assert "--header" not in message
    assert "Authorization" not in message


def test_initialize_echoes_the_requested_protocol_version(capsys) -> None:
    proxy.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    sent = json.loads(capsys.readouterr().out)
    assert sent["result"]["protocolVersion"] == "2025-06-18"
    assert sent["result"]["serverInfo"]["name"] == "bfe-energy-knowledge"


def test_notifications_produce_no_output(capsys) -> None:
    proxy.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert capsys.readouterr().out == ""


def test_unknown_method_is_reported(capsys) -> None:
    proxy.handle({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"})
    sent = json.loads(capsys.readouterr().out)
    assert sent["error"]["code"] == -32601


def test_tools_list_is_forwarded_verbatim(monkeypatch, capsys) -> None:
    monkeypatch.setattr(proxy, "call_gateway", lambda m, p: {"tools": [{"name": "t"}]})
    proxy.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    sent = json.loads(capsys.readouterr().out)
    assert sent["result"]["tools"] == [{"name": "t"}]
