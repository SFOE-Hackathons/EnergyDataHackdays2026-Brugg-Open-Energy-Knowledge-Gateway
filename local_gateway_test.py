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
    "https://bfe-energy-knowledge-open-v6rj5uttek."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
)
LOCAL_GATEWAY_URL = "http://127.0.0.1:8000/mcp"
GATEWAY_URL = os.getenv("GATEWAY_URL", LOCAL_GATEWAY_URL)
LOCAL_DEV_TOKEN = os.getenv("MCP_DEV_TOKEN", "local-development-token")
MCP_PROTOCOL_VERSION = "2026-07-28"
# Must match the AgentCore Gateway tool name used by test_gateway.py.
TOOL_NAME = "bfe-public-knowledge___Retrieve"
TOOL_SCHEMA = None
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


def discover_remote_tools(gateway_url: str, access_token: str) -> list[dict]:
    """Return tool definitions advertised by an AgentCore Gateway."""
    response = requests.post(
        gateway_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": "tools/list",
        },
        json={
            "jsonrpc": "2.0",
            "id": "list-tools-request",
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "bfe-hackathon-test",
                        "version": "1.0.0",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"MCP tools/list failed: {payload['error']}")
    return payload.get("result", {}).get("tools", [])


def configure_remote_tool(gateway_url: str, access_token: str) -> None:
    """Discover the current AgentCore tool name and update Ollama's schema."""
    global TOOL_NAME, TOOL_SCHEMA

    available_tools = discover_remote_tools(gateway_url, access_token)
    if not available_tools:
        raise RuntimeError("The remote gateway did not advertise any MCP tools.")

    preferred_tool = next(
        (
            tool for tool in available_tools
            if tool["name"].endswith("___Retrieve")
        ),
        next(
            (
                tool for tool in available_tools
                if "search" in tool["name"].lower()
                and "knowledge" in tool["name"].lower()
            ),
            None,
        ),
    )
    if preferred_tool is None:
        names = [tool["name"] for tool in available_tools]
        raise RuntimeError(
            f"No knowledge-search tool was advertised by the remote gateway: {names}"
        )

    TOOL_NAME = preferred_tool["name"]
    TOOL_SCHEMA = preferred_tool.get("inputSchema", preferred_tool.get("input_schema", {}))
    TOOLS[0]["function"]["name"] = TOOL_NAME
    TOOLS[0]["function"]["description"] = preferred_tool.get(
        "description", TOOLS[0]["function"]["description"]
    )
    TOOLS[0]["function"]["parameters"] = TOOL_SCHEMA
    print(f"Remote tools: {[tool['name'] for tool in available_tools]}")
    print(f"Using remote tool: {TOOL_NAME}")


def select_top_chunks(question: str, result: dict, top_k: int = TOP_K) -> list[dict]:
    # Preserve source metadata while reducing the retrieved context sent to Ollama.
    retrieved = result.get("result", {}).get("content", [])
    source_catalog = {}
    if len(retrieved) == 1 and isinstance(retrieved[0], dict):
        text = retrieved[0].get("text")
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and "results" in payload:
                retrieved = payload["results"]
                source_catalog = payload.get("sources", {})
    passages = []
    for item in retrieved:
        if not isinstance(item, dict) or "text" not in item:
            continue
        passages.append({
            "id": str(len(passages)),
            "text": item["text"],
            "source": item.get("source")
            or source_catalog.get(item.get("source_id"), {}),
            "metadata": item.get("metadata")
            or source_catalog.get(item.get("source_id"), {}),
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
    if TOOL_SCHEMA:
        properties = TOOL_SCHEMA.get("properties", {})
        if "query" in properties:
            query = arguments.get("query") or arguments.get("topic")
            return {"query": str(query or fallback_question).strip()}

    if TOOL_SCHEMA and "topic" in TOOL_SCHEMA.get("properties", {}):
        topic = arguments.get("topic") or arguments.get("query") or fallback_question
        return {"topic": str(topic).strip()}

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
            fallback_arguments = (
                {"query": question}
                if TOOL_SCHEMA and "query" in TOOL_SCHEMA.get("properties", {})
                else {"retrievalQuery": {"text": question}}
            )
            tool_calls = [{
                "function": {
                    "arguments": fallback_arguments,
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
            question = (
                arguments.get("query")
                or arguments.get("topic")
                or arguments.get("retrievalQuery", {}).get("text")
                or original_question
            )
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
    print(f"MCP endpoint: {GATEWAY_URL}")
    if GATEWAY_URL != LOCAL_GATEWAY_URL:
        configure_remote_tool(GATEWAY_URL, access_token)
    else:
        print(f"MCP tool: {TOOL_NAME}")
    run(
        model="qwen3:14b",
        gateway_url=GATEWAY_URL,
        access_token=access_token,
    )