import json
import os

import ollama
import requests
from flashrank import Ranker, RerankRequest

TOKEN_URL = (
    "https://my-domain-ajdb98m7.auth.eu-central-1.amazoncognito.com/"
    "oauth2/token"
)
REMOTE_GATEWAY_URL = (
    "https://sandbox-bfe-public-kb-8thmswsvit."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
)
LOCAL_GATEWAY_URL = "http://127.0.0.1:8000/mcp"
GATEWAY_URL = os.getenv("GATEWAY_URL", LOCAL_GATEWAY_URL)
LOCAL_DEV_TOKEN = os.getenv("MCP_DEV_TOKEN", "local-development-token")
MCP_PROTOCOL_VERSION = "2026-07-28"
TOOL_NAME = "bfe-public-knowledge___Retrieve"
TOP_K = 2
OLLAMA_CONTEXT_SIZE = 2048
reranker = Ranker()
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
    # Local mode mirrors the model-facing auth flow without requiring Cognito credentials.
    if GATEWAY_URL == LOCAL_GATEWAY_URL:
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
    # The remote gateway uses a direct tools/call contract rather than a standard session handshake.
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
    payload = response.json()
    if "error" in payload:
        # Never turn a JSON-RPC error into apparently valid model context.
        error = payload["error"]
        raise RuntimeError(
            f"MCP retrieval failed: {error.get('message', error)}"
        )

    result = payload.get("result", {})
    if result.get("isError"):
        error_text = "MCP tool returned an error"
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("text"):
                error_text = item["text"]
                break
        raise RuntimeError(error_text)

    return payload


def select_top_chunks(question: str, result: dict, top_k: int = TOP_K) -> list[dict]:
    # Preserve source metadata while reducing the retrieved context sent to Ollama.
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
        passages.append({
            "id": str(len(passages)),
            "text": item["text"],
            "source": item.get("source", {}),
            "metadata": item.get("metadata", {}),
        })

    if not passages:
        return []

    reranked = reranker.rerank(
        RerankRequest(query=question, passages=passages)
    )
    return [
        {
            "text": passage["text"],
            "score": float(passage["score"])
            if passage.get("score") is not None
            else None,
            "source": passage.get("source", {}),
            "metadata": passage.get("metadata", {}),
        }
        for passage in reranked[:top_k]
    ]


def print_sources(chunks: list[dict]) -> None:
    # Show citations separately from the compact context supplied to the model.
    if not chunks:
        print("\nSources: none returned")
        return

    print("\nSources:")
    for index, chunk in enumerate(chunks, start=1):
        source = chunk.get("source", {})
        metadata = chunk.get("metadata", {})
        title = (
            metadata.get("title")
            or metadata.get("document_title")
            or metadata.get("name")
            or "Untitled document"
        )
        location = source.get("webLocation") or source.get("uri") or "Unavailable"
        score = chunk.get("score")
        score_text = f" | score: {score:.3f}" if isinstance(score, float) else ""
        print(f"  [{index}] {title}{score_text}\n      {location}")


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
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant. Use the energy knowledge gateway "
                "to answer questions about Swiss energy."
            ),
        }
    ]

    print("Open Energy chatbot. Type 'exit' or 'quit' to leave.")
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break

        original_question = question
        messages.append({"role": "user", "content": question})
        response = ollama.chat(
            model=model,
            messages=messages,
            tools=TOOLS,
            options={"num_ctx": OLLAMA_CONTEXT_SIZE},
        )
        tool_calls = response.message.tool_calls or []
        model_requested_tool = bool(tool_calls)

        if model_requested_tool:
            messages.append(response.message)
        else:
            # Qwen can reason about a tool without emitting structured tool_calls.
            tool_calls = [{
                "function": {
                    "arguments": {"retrievalQuery": {"text": question}},
                },
            }]

        retrieval_failed = False
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                arguments = tool_call["function"]["arguments"]
            else:
                arguments = tool_call.function.arguments
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            arguments = normalize_tool_arguments(arguments, original_question)
            print("\n[Status] Waiting for MCP response...", flush=True)
            try:
                result = call_remote_tool(gateway_url, access_token, arguments)
            except (requests.RequestException, RuntimeError) as error:
                print(f"[MCP error] {error}", flush=True)
                print("[Status] No answer generated because retrieval failed.")
                retrieval_failed = True
                break

            print("[Status] MCP response received. Selecting relevant context...", flush=True)
            question = arguments["retrievalQuery"]["text"]
            top_chunks = select_top_chunks(question, result)
            messages.append({
                "role": "tool" if model_requested_tool else "user",
                "content": json.dumps({
                    "question": question,
                    "top_k": len(top_chunks),
                    "results": top_chunks,
                }),
            })

        if retrieval_failed:
            continue

        print("[Status] Context ready. Generating the answer...", flush=True)

        print("\nAssistant: ", end="", flush=True)
        response_stream = ollama.chat(
            model=model,
            messages=messages,
            options={"num_ctx": OLLAMA_CONTEXT_SIZE},
            stream=True,
        )
        answer_parts = []
        for response_chunk in response_stream:
            content = response_chunk.message.content or ""
            answer_parts.append(content)
            print(content, end="", flush=True)
        print()
        print_sources(top_chunks)
        messages.append({"role": "assistant", "content": "".join(answer_parts)})


if __name__ == "__main__":    
    access_token = fetch_access_token()
    run(
        model="qwen3:14b",
        gateway_url=GATEWAY_URL,
        access_token=access_token,
    )