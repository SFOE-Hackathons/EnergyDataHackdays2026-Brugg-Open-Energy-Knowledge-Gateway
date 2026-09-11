import logging
import os
import sys
import threading
import time

import requests
from dotenv import load_dotenv

from . import config

load_dotenv()

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN_URL = config.TOKEN_URL
GATEWAY_URL = config.GATEWAY_URL

MCP_PROTOCOL_VERSION = "2026-07-28"

SEARCH_TOOL_NAME = "bfe-energy___search_energy_knowledge"
METRIC_TIMELINE_TOOL_NAME = "bfe-energy___get_metric_timeline"
CHART_DATA_TOOL_NAME = "bfe-energy___get_chart_data"

_TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 60


class AuthError(Exception):
    """Raised when fetching a Cognito access token fails."""


class GatewayError(Exception):
    """Raised when the AgentCore Gateway call fails or returns an error."""


class _TokenCache:
    def __init__(self):
        self._token = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self, force_refresh=False):
        with self._lock:
            if force_refresh or self._token is None or time.time() >= self._expires_at:
                self._refresh()
            return self._token

    def _refresh(self):
        client_id = os.environ.get("CLIENT_ID")
        client_secret = os.environ.get("CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthError(
                "CLIENT_ID/CLIENT_SECRET environment variables are not set."
            )

        logger.info("Fetching new Cognito access token")
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise AuthError(f"Could not reach the Cognito token endpoint: {e}") from e
        except ValueError as e:
            raise AuthError("Cognito token response was not valid JSON.") from e

        access_token = data.get("access_token")
        if not access_token:
            raise AuthError("Cognito token response did not include an access_token.")

        self._token = access_token
        expires_in = data.get("expires_in", 3600)
        self._expires_at = time.time() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS


_token_cache = _TokenCache()


def retrieve(question):
    """Search the SFOE knowledge base and return the parsed JSON-RPC response."""
    return _call_tool(SEARCH_TOOL_NAME, {"query": question})


def get_metric_timeline(metric, start_year, end_year, detail="full", max_results_per_year=None):
    """Query the SFOE knowledge base once per year for a metric across a
    year range and return the parsed JSON-RPC response."""
    arguments = {
        "metric": metric,
        "start_year": start_year,
        "end_year": end_year,
        "detail": detail,
    }
    if max_results_per_year is not None:
        arguments["max_results_per_year"] = max_results_per_year
    return _call_tool(METRIC_TIMELINE_TOOL_NAME, arguments)


def get_chart_data(topic, max_charts=5):
    """Fetch structured, parsed chart/table data about a topic and return
    the parsed JSON-RPC response."""
    return _call_tool(CHART_DATA_TOOL_NAME, {"topic": topic, "max_charts": max_charts})


def _call_tool(tool_name, arguments):
    access_token = _token_cache.get()
    response = _call_gateway(access_token, tool_name, arguments)

    if response.status_code == 401:
        logger.info("Gateway returned 401, refreshing token and retrying once")
        access_token = _token_cache.get(force_refresh=True)
        response = _call_gateway(access_token, tool_name, arguments)

    if response.status_code >= 400:
        raise GatewayError(
            f"Gateway request failed with HTTP {response.status_code}: {response.text[:500]}"
        )

    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type and "text/event-stream" not in content_type:
        logger.warning("Unexpected Content-Type from Gateway: %s", content_type)

    if "text/event-stream" in content_type:
        return _parse_sse(response.text)

    try:
        return response.json()
    except ValueError as e:
        raise GatewayError("Gateway response was not valid JSON.") from e


def _call_gateway(access_token, tool_name, arguments):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool_name,
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "tool-request",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "open-energy-gateway-mcp",
                    "version": "0.1.0"
                },
                "io.modelcontextprotocol/clientCapabilities": {}
            }
        }
    }

    try:
        return requests.post(
            GATEWAY_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as e:
        raise GatewayError(f"Could not reach the AgentCore Gateway: {e}") from e


def _parse_sse(text):
    """Parse a text/event-stream body down to the last `data:` JSON payload."""
    data_lines = [
        line[len("data:"):].strip()
        for line in text.splitlines()
        if line.startswith("data:")
    ]
    if not data_lines:
        raise GatewayError("SSE response from Gateway contained no data lines.")

    import json

    try:
        return json.loads(data_lines[-1])
    except ValueError as e:
        raise GatewayError("SSE data payload was not valid JSON.") from e
