"""Tests for platform-specific Cognito client-secret storage."""

import pytest

import bfe_mcp_proxy as proxy


def test_macos_reads_secret_from_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(proxy, "read_macos_keychain_secret", lambda: "keychain-secret")
    monkeypatch.setattr(
        proxy,
        "read_environment_secret",
        lambda: pytest.fail("macOS must not read the secret from the environment"),
    )

    assert proxy.read_client_secret() == "keychain-secret"


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_non_macos_reads_secret_from_environment(
    monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    monkeypatch.setattr(proxy.platform, "system", lambda: system)
    monkeypatch.setattr(proxy, "read_environment_secret", lambda: "environment-secret")
    monkeypatch.setattr(
        proxy,
        "read_macos_keychain_secret",
        lambda: pytest.fail(f"{system} must not invoke macOS Keychain"),
    )

    assert proxy.read_client_secret() == "environment-secret"


def test_environment_secret_prefers_namespaced_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BFE_MCP_CLIENT_SECRET", "namespaced-secret")
    monkeypatch.setenv("CLIENT_SECRET", "legacy-secret")

    assert proxy.read_environment_secret() == "namespaced-secret"


def test_environment_secret_accepts_legacy_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BFE_MCP_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("CLIENT_SECRET", "legacy-secret")

    assert proxy.read_environment_secret() == "legacy-secret"


def test_environment_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BFE_MCP_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CLIENT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="BFE_MCP_CLIENT_SECRET"):
        proxy.read_environment_secret()
