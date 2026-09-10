import os
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flashrank import Ranker, RerankRequest
from mcp.server.mcpserver import MCPServer


DEFAULT_KNOWLEDGE_BASE_NAME = "KB-bfe-public"
DEFAULT_REGION = "eu-central-1"
RETRIEVAL_COUNT = 6
DEFAULT_TOP_K = 2

reranker = Ranker()

mcp = MCPServer(name="open-energy-knowledge-gateway")


@lru_cache(maxsize=1)
def bedrock_agent_client():
    return boto3.client(
        "bedrock-agent-runtime",
        region_name=os.getenv("AWS_REGION", DEFAULT_REGION),
        access_key=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


@lru_cache(maxsize=1)
def knowledge_base_id() -> str:
    configured_id = os.getenv("KNOWLEDGE_BASE_ID")
    if configured_id:
        return configured_id

    name = os.getenv("KNOWLEDGE_BASE_NAME", DEFAULT_KNOWLEDGE_BASE_NAME)
    client = bedrock_agent_client()
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
        result["rerankScore"] = passage.get("score")
        selected.append(result)
    return selected


@mcp.tool()
def ask_question(question: str, top_k: int = DEFAULT_TOP_K) -> dict[str, Any]:
    """Retrieve relevant public Swiss energy knowledge for a question."""
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")
    if not 1 <= top_k <= 2:
        raise ValueError("top_k must be 1 or 2")

    try:
        response = bedrock_agent_client().retrieve(
            knowledgeBaseId=knowledge_base_id(),
            retrievalQuery={"text": question},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": RETRIEVAL_COUNT,
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
