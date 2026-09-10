"""Client for the Swiss federal energy publications knowledge base.

Implementation note (maintainers only, not exposed to callers): this talks
to an AWS Bedrock AgentCore Gateway that fronts the knowledge base, using a
Cognito client_credentials token, and calls its `Retrieve` tool. The raw,
deeply-nested AgentCore/Bedrock retrieval response is normalized here into a
flat, simple result shape before it ever reaches a tool.
"""

import json
import threading
import time

import requests

from config import Config

MCP_PROTOCOL_VERSION = "2026-07-28"
RETRIEVE_TOOL_NAME = "bfe-public-knowledge___Retrieve"


class KnowledgeBaseError(Exception):
    """A query against the knowledge base failed.

    Its message is surfaced to callers, so it must stay free of any
    implementation detail (see this module's docstring).
    """


class KnowledgeBaseClient:
    """Queries the Swiss federal energy publications knowledge base."""

    def __init__(self, config: Config):
        self._config = config
        self._token_lock = threading.Lock()
        self._token_cache = {"access_token": None, "expires_at": 0.0}

    def search(self, query: str, max_results: int) -> list[dict]:
        """Run a semantic search and return a score-sorted, ranked list of
        normalized passages, truncated to max_results."""
        raw_response = self._call_retrieve(query)
        return self._normalize(raw_response, max_results)

    def _get_access_token(self) -> str:
        with self._token_lock:
            cache = self._token_cache
            if cache["access_token"] and time.monotonic() < cache["expires_at"]:
                return cache["access_token"]

            response = requests.post(
                self._config.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()

            cache["access_token"] = payload["access_token"]
            cache["expires_at"] = time.monotonic() + payload.get("expires_in", 3600) - 30
            return cache["access_token"]

    def _call_retrieve(self, query: str) -> dict:
        access_token = self._get_access_token()

        response = requests.post(
            self._config.gateway_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                "Mcp-Method": "tools/call",
                "Mcp-Name": RETRIEVE_TOOL_NAME,
            },
            json={
                "jsonrpc": "2.0",
                "id": "retrieve-request",
                "method": "tools/call",
                "params": {
                    "name": RETRIEVE_TOOL_NAME,
                    "arguments": {"retrievalQuery": {"text": query}},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "open-energy-knowledge-gateway",
                            "version": "1.0.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _normalize(self, raw_response: dict, max_results: int) -> list[dict]:
        result = raw_response.get("result", {})

        if result.get("isError"):
            raise KnowledgeBaseError("Knowledge base query failed.")

        content_blocks = result.get("content", [])
        if not content_blocks:
            return []

        inner = json.loads(content_blocks[0]["text"])
        retrieval_results = inner.get("retrievalResults", [])

        cleaned = []
        for item in retrieval_results:
            metadata = item.get("metadata", {})
            cleaned.append(
                {
                    "score": item.get("score"),
                    "text": item.get("content", {}).get("text", ""),
                    "source": {
                        "title": metadata.get("_document_title"),
                        "s3_uri": item.get("documentId"),
                        "download_url": item.get("location", {})
                        .get("s3Location", {})
                        .get("uri"),
                        "file_type": metadata.get("_file_type"),
                        "language": metadata.get("_language_code"),
                        "created_at": metadata.get("_created_at"),
                        "last_updated_at": metadata.get("_last_updated_at"),
                    },
                }
            )

        cleaned.sort(key=lambda item: item["score"] or 0, reverse=True)
        cleaned = cleaned[:max_results]

        for rank, item in enumerate(cleaned, start=1):
            item["rank"] = rank

        return cleaned
