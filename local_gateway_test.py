import json
import os

import ollama
import requests
from flashrank import Ranker, RerankRequest

TOKEN_URL = (
    "https://my-domain-ajdb98m7.auth.eu-central-1.amazoncognito.com/"
    "oauth2/token"
)
GATEWAY_URL = (
    # "https://sandbox-bfe-public-kb-8thmswsvit."
    # "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
    "http://127.0.0.1:8000/mcp"
)
LOCAL_DEV_TOKEN = os.getenv("MCP_DEV_TOKEN", "local-development-token")
MCP_PROTOCOL_VERSION = "2026-07-28"
TOOL_NAME = "bfe-public-knowledge___Retrieve"
TOP_K = 2
TOOLS = [{
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Retrieve information from the BFE public energy knowledge base.",
        "parameters": {
            "type": "object",
            "properties": {
                "retrievalQuery": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            "required": ["retrievalQuery"],
        },
    },
}]


def fetch_access_token() -> str:
    if GATEWAY_URL.startswith("http://127.0.0.1"):
        return LOCAL_DEV_TOKEN

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]

def call_remote_tool(gateway_url: str, access_token: str, arguments: dict) -> dict:
    response = requests.post(
        gateway_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": "tools/call",
            "Mcp-Name": TOOL_NAME,
        },
        json={
            "jsonrpc": "2.0",
            "id": "retrieve-request",
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": arguments,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "bfe-hackathon-test",
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


def select_top_chunks(question: str, result: dict, top_k: int = TOP_K) -> list[dict]:
    retrieved = result.get("result", {}).get("content", [])
    if len(retrieved) == 1 and isinstance(retrieved[0], dict):
        text = retrieved[0].get("text")
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and "results" in payload:
                retrieved = payload["results"]
    passages = []
    for item in retrieved:
        if not isinstance(item, dict) or "text" not in item:
            continue
        passages.append({"id": str(len(passages)), "text": item["text"]})

    if not passages:
        return []

    reranked = Ranker().rerank(
        RerankRequest(query=question, passages=passages)
    )
    return [
        {
            "text": passage["text"],
            "score": float(passage["score"])
            if passage.get("score") is not None
            else None,
        }
        for passage in reranked[:top_k]
    ]


def normalize_tool_arguments(arguments: dict, fallback_question: str) -> dict:
    retrieval_query = arguments.get("retrievalQuery", {})
    if isinstance(retrieval_query, dict):
        query = (
            retrieval_query.get("text")
            or retrieval_query.get("query")
            or retrieval_query.get("question")
        )
    else:
        query = retrieval_query

    return {
        "retrievalQuery": {
            "text": str(query or fallback_question).strip(),
        },
    }


def run(model: str, gateway_url: str, access_token: str):
    question = "How has hydroenergy production developed in Switzerland?"
    original_question = question
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant. Use the energy knowledge gateway "
                "to answer questions about Swiss energy."
            ),
        },
        {"role": "user", "content": question},
    ]
    response = ollama.chat(model=model, messages=messages, tools=TOOLS, options={"num_ctx": 2048})
    tool_calls = response.message.tool_calls or []
    model_requested_tool = bool(tool_calls)

    if model_requested_tool:
        messages.append(response.message)
    else:
        # Some local models reason about a tool but stop without emitting a
        # structured tool call. Use the known tool contract as a fallback.
        tool_calls = [{
            "function": {
                "arguments": {"retrievalQuery": {"text": question}},
            },
        }]

    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            arguments = tool_call["function"]["arguments"]
        else:
            arguments = tool_call.function.arguments
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        arguments = normalize_tool_arguments(arguments, original_question)
        result = call_remote_tool(
            gateway_url,
            access_token,
            arguments,
        )
        question = arguments["retrievalQuery"]["text"]
        top_chunks = select_top_chunks(question, result)
        context_message = json.dumps({
            "question": question,
            "top_k": len(top_chunks),
            "results": top_chunks,
        })
        messages.append({
            "role": "tool" if model_requested_tool else "user",
            "content": context_message,
        })

    print("Ollama response: ", end="", flush=True)
    response_stream = ollama.chat(
        model=model,
        messages=messages,
        options={"num_ctx": 2048},
        stream=True,
    )
    for response_chunk in response_stream:
        content = response_chunk.message.content or ""
        print(content, end="", flush=True)
    print()


if __name__ == "__main__":    
    access_token = fetch_access_token()
    run(
        model="qwen3:14b",
        gateway_url=GATEWAY_URL,
        access_token=access_token,
    )