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
    "https://localhost:8080/mcp"
)
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
        {"text": passage["text"], "score": passage.get("score")}
        for passage in reranked[:top_k]
    ]


def run(model: str, gateway_url: str, access_token: str):
    messages = [
        {"role": "system", "content": "You are a helpful assistant, that can help with retrieving information from the energy knowledge gateway."},
        {"role": "user", "content": "{retrievalQuery: {\"text\": \"How has photovoltaic production developed in Switzerland?\"}}"},
    ]
    response = ollama.chat(model=model, messages=messages, tools=TOOLS, options={"num_ctx": 2048})
    tool_calls = response.message.tool_calls or []

    if not tool_calls:
        print("Ollama response:", response)
        return

    messages.append(response.message)
    for tool_call in tool_calls:
        result = call_remote_tool(
            gateway_url,
            access_token,
            tool_call.function.arguments,
        )
        question = tool_call.function.arguments["retrievalQuery"]["text"]
        top_chunks = select_top_chunks(question, result)
        messages.append({
            "role": "tool",
            "content": json.dumps({
                "question": question,
                "top_k": len(top_chunks),
                "results": top_chunks,
            }),
        })

    response = ollama.chat(model=model, messages=messages, options={"num_ctx": 2048})
    print("Ollama response:", response)


if __name__ == "__main__":    
    access_token = fetch_access_token()
    run(
        model="qwen3:14b",
        gateway_url=GATEWAY_URL,
        access_token=access_token,
    )