import os
import asyncio
import hmac
import time
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flashrank import Ranker, RerankRequest
from mcp.server.mcpserver import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings


DEFAULT_KNOWLEDGE_BASE_NAME = "KB-bfe-public"
DEFAULT_REGION = "eu-central-1"
RETRIEVAL_COUNT = 4 # Number of results to request from the Bedrock retrieval API (low count -> less RAM usage, but may miss relevant results).
DEFAULT_TOP_K = 2 # Number of results to return to the client (low count -> less RAM usage, but may miss relevant results).
DEFAULT_DEV_TOKEN = "local-development-token"
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


# Local HTTP clients use a deterministic bearer token instead of Cognito.
class LocalTokenVerifier:
    """Development-only bearer validation for the local model-facing server."""

    async def verify_token(self, token: str) -> AccessToken | None:
        await asyncio.sleep(0)
        expected_token = os.getenv("MCP_DEV_TOKEN", DEFAULT_DEV_TOKEN)
        if not hmac.compare_digest(token, expected_token):
            return None

        return AccessToken(
            token=token,
            client_id="local-model",
            subject="local-model",
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
            resource=os.getenv("MCP_RESOURCE_URL", f"{DEFAULT_SERVER_URL}/mcp"),
        )

# Keep Bedrock retrieval broad, then reduce the context locally before returning it. (only useful if top_k < RETRIEVAL_COUNT)
reranker = Ranker()

mcp = MCPServer(
    name="open-energy-knowledge-gateway",
    token_verifier=LocalTokenVerifier(),
    auth=AuthSettings(
        issuer_url=os.getenv("MCP_ISSUER_URL", DEFAULT_SERVER_URL),
        resource_server_url=os.getenv(
            "MCP_RESOURCE_URL", f"{DEFAULT_SERVER_URL}/mcp"
        ),
        required_scopes=["mcp"],
        validate_token_resource=True,
    ),
)


@lru_cache(maxsize=1)
def bedrock_agent_client():
    # Runtime operations such as Retrieve belong to bedrock-agent-runtime.
    return boto3.client(
        "bedrock-agent-runtime",
        region_name=os.getenv("AWS_REGION", DEFAULT_REGION),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


@lru_cache(maxsize=1)
def bedrock_control_client():
    # Control-plane operations such as listing Knowledge Bases use bedrock-agent.
    return boto3.client(
        "bedrock-agent",
        region_name=os.getenv("AWS_REGION", DEFAULT_REGION),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


@lru_cache(maxsize=1)
def knowledge_base_id() -> str:
    configured_id = os.getenv("KNOWLEDGE_BASE_ID")
    if configured_id:
        return configured_id

    # Prefer an explicit ID; name lookup is convenient but requires extra IAM access.
    name = os.getenv("KNOWLEDGE_BASE_NAME", DEFAULT_KNOWLEDGE_BASE_NAME)
    client = bedrock_control_client()
    paginator = client.get_paginator("list_knowledge_bases")
    for page in paginator.paginate():
        for knowledge_base in page.get("knowledgeBaseSummaries", []):
            if knowledge_base.get("name") == name:
                return knowledge_base["knowledgeBaseId"]

    raise RuntimeError(
        f"Knowledge Base {name!r} was not found. Set KNOWLEDGE_BASE_ID to its ID."
    )


def format_result(result: dict[str, Any]) -> dict[str, Any]:
    location = result.get("location", {})
    return {
        "text": result.get("content", {}).get("text", ""),
        "score": result.get("score"),
        "source": {
            "type": location.get("type"),
            "uri": location.get("s3Location", {}).get("uri"),
            "webLocation": location.get("webLocation", {}).get("url"),
        },
        "metadata": result.get("metadata", {}),
    }


def select_top_results(
    question: str,
    results: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    # FlashRank scores candidates locally so only the most relevant chunks leave the server.
    passages = [
        {"id": str(index), "text": result["text"]}
        for index, result in enumerate(results)
        if result.get("text")
    ]
    if not passages:
        return []

    reranked = reranker.rerank(
        RerankRequest(query=question, passages=passages)
    )
    by_id = {str(result_index): result for result_index, result in enumerate(results)}
    selected = []
    for passage in reranked[:top_k]:
        result = dict(by_id[passage["id"]])
        result["rerankScore"] = (
            float(passage["score"])
            if passage.get("score") is not None
            else None
        )
        selected.append(result)
    return selected


@mcp.tool(
    name="bfe-public-knowledge___Retrieve",
    description="Retrieve information from the BFE public energy knowledge base.",
)
def retrieve(
    retrievalQuery: dict[str, Any],
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Retrieve relevant public Swiss energy knowledge for a question."""
    question = str(retrievalQuery.get("text", "")).strip()
    if not question:
        raise ValueError("retrievalQuery.text must not be empty")
    if not 1 <= top_k <= 2:
        raise ValueError("top_k must be 1 or 2")

    try:
        # Managed Knowledge Bases require managedSearchConfiguration, not vectorSearchConfiguration.
        response = bedrock_agent_client().retrieve(
            knowledgeBaseId=knowledge_base_id(),
            retrievalQuery={"text": question},
            retrievalConfiguration={
                "managedSearchConfiguration": {
                    "numberOfResults": RETRIEVAL_COUNT
                }
            },
        )
    except (BotoCoreError, ClientError) as error:
        raise RuntimeError(f"Knowledge Base retrieval failed: {error}") from error

    formatted_results = [
        format_result(item)
        for item in response.get("retrievalResults", [])
    ]
    return {
        "question": question,
        "knowledgeBase": os.getenv(
            "KNOWLEDGE_BASE_NAME", DEFAULT_KNOWLEDGE_BASE_NAME
        ),
        "top_k": top_k,
        "results": select_top_results(question, formatted_results, top_k),
    }


if __name__ == "__main__":
    mcp.run(
        transport=os.getenv("MCP_TRANSPORT", "stdio"),
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        stateless_http=True,
    )
