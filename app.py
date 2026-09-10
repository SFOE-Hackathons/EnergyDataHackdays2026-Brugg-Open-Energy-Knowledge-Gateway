import os
import json

import boto3
import requests

from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# GOAL OF THIS APPLICATION
# ============================================================
# Build a simple SFOE / BFE knowledge assistant.
#
# DATA FLOW
# ------------------------------------------------------------
# User question
#     ↓
# SFOE MCP Gateway
#     ↓
# search_energy_knowledge
#     ↓
# Ranked SFOE passages + source metadata
#     ↓
# Amazon Bedrock LLM
#     ↓
# Grounded answer with [1], [2], ... citations
#     ↓
# Official SFOE source links
#
# NOTE
# ------------------------------------------------------------
# The current "bfe-energy-knowledge-open" Gateway is configured
# without inbound authorization. Cognito is therefore optional
# in this version. If Cognito variables exist in .env, the app
# can still obtain and send a token.
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
# GOAL
# ------------------------------------------------------------
# Load runtime configuration from .env.
#
# INPUT
# ------------------------------------------------------------
# Required:
#   GATEWAY_URL
#   BEDROCK_MODEL_ID
#
# Optional:
#   AWS_REGION
#   TOP_K_RESULTS
#   CLIENT_ID
#   CLIENT_SECRET
#   TOKEN_URL
#
# OUTPUT
# ------------------------------------------------------------
# Python variables used by the application.
#
# IMPORTANT
# ------------------------------------------------------------
# .env contains secrets and must NOT be committed to Git.
# ============================================================

load_dotenv(override=True)

GATEWAY_URL = os.environ["GATEWAY_URL"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")
TOP_K_RESULTS = int(os.getenv("TOP_K_RESULTS", "5"))

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TOKEN_URL = os.getenv("TOKEN_URL")


# ============================================================
# 2. MCP CONFIGURATION
# ============================================================
# GOAL
# ------------------------------------------------------------
# Define the current SFOE MCP search tool and protocol.
#
# OUTPUT
# ------------------------------------------------------------
# TOOL_NAME:
#   General semantic search over SFOE energy publications.
# ============================================================

MCP_PROTOCOL_VERSION = "2026-07-28"

TOOL_NAME = "bfe-energy___search_energy_knowledge"


# ============================================================
# 3. OPTIONAL COGNITO AUTHENTICATION
# ============================================================


def fetch_access_token():
    """
    GOAL
    ----------------------------------------------------------
    Obtain a Cognito OAuth access token when Cognito
    configuration is available.

    INPUT
    ----------------------------------------------------------
    CLIENT_ID, CLIENT_SECRET, TOKEN_URL from .env.

    OUTPUT
    ----------------------------------------------------------
    Access token string, or None when Cognito is not configured.
    """

    if not all([CLIENT_ID, CLIENT_SECRET, TOKEN_URL]):
        return None

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    response.raise_for_status()

    return response.json()["access_token"]


# ============================================================
# 4. BUILD MCP HEADERS
# ============================================================


def build_mcp_headers(access_token=None):
    """
    GOAL
    ----------------------------------------------------------
    Build HTTP headers for the MCP tools/call request.

    INPUT
    ----------------------------------------------------------
    access_token:
        Optional Cognito token.

    OUTPUT
    ----------------------------------------------------------
    Dictionary of HTTP headers.
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": TOOL_NAME,
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    return headers


# ============================================================
# 5. SEARCH SFOE KNOWLEDGE THROUGH MCP
# ============================================================


def retrieve(question, access_token=None):
    """
    GOAL
    ----------------------------------------------------------
    Search official SFOE publications through the current MCP
    tool.

    INPUT
    ----------------------------------------------------------
    question:
        Natural-language user question.

    access_token:
        Optional Cognito token.

    OUTPUT
    ----------------------------------------------------------
    Complete search response from search_energy_knowledge.

    Current response structure:
        {
            "result_count": ...,
            "sources": {...},
            "results": [...]
        }
    """

    payload = {
        "jsonrpc": "2.0",
        "id": "sfoe-search-request",
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            # The NEW SFOE search tool expects "query".
            "arguments": {"query": question},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "sfoe-knowledge-app",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }

    response = requests.post(
        GATEWAY_URL,
        headers=build_mcp_headers(access_token),
        json=payload,
        timeout=120,
    )

    response.raise_for_status()

    data = response.json()

    # Catch JSON-RPC / MCP errors clearly.
    if "error" in data:
        raise RuntimeError(
            "MCP Gateway error:\n"
            + json.dumps(data["error"], indent=2, ensure_ascii=False)
        )

    if "result" not in data:
        raise RuntimeError(
            "Unexpected MCP response:\n"
            + json.dumps(data, indent=2, ensure_ascii=False)
        )

    content = data["result"].get("content", [])

    if not content:
        raise RuntimeError("MCP response contains no content.")

    # Find the first text content block.
    text_content = None

    for item in content:
        if isinstance(item, dict) and "text" in item:
            text_content = item["text"]
            break

    if text_content is None:
        raise RuntimeError("MCP response contains no text content.")

    try:
        search_data = json.loads(text_content)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"The SFOE MCP tool did not return valid JSON:\n{text_content}"
        ) from error

    return search_data


# ============================================================
# 6. NORMALIZE SEARCH RESULTS
# ============================================================


def prepare_evidence(search_data):
    """
    GOAL
    ----------------------------------------------------------
    Convert the new SFOE response structure into a simple list
    that the LLM and display functions can use.

    INPUT
    ----------------------------------------------------------
    search_data:
        {
            "result_count": ...,
            "sources": {...},
            "results": [...]
        }

    OUTPUT
    ----------------------------------------------------------
    List of normalized evidence items containing:
        citation_number
        score
        text
        source_id
        title
        published_at
        download_url
        years_covered
        bases
        is_projection
        is_truncated
        truncation_reasons

    PROCESS
    ----------------------------------------------------------
    Each result has a source_id such as "s1".
    We resolve that ID through the top-level "sources" object.
    """

    if not isinstance(search_data, dict):
        raise RuntimeError("Unexpected SFOE search response type.")

    results = search_data.get("results", [])
    sources = search_data.get("sources", {})

    evidence = []

    for item in results[:TOP_K_RESULTS]:
        if not isinstance(item, dict):
            continue

        source_id = item.get("source_id")
        source = sources.get(source_id, {}) if source_id else {}

        text = item.get("text") or item.get("passage") or item.get("content") or ""

        if not text.strip():
            continue

        evidence.append(
            {
                "citation_number": len(evidence) + 1,
                "score": item.get("score", 0),
                "text": text,
                "source_id": source_id,
                "title": (
                    source.get("title")
                    or source.get("document_title")
                    or "Unknown document"
                ),
                "published_at": source.get("published_at"),
                "download_url": source.get("download_url"),
                "years_covered": item.get("years_covered"),
                "bases": item.get("bases", []),
                "is_projection": item.get("is_projection", False),
                "is_truncated": item.get("is_truncated", False),
                "truncation_reasons": item.get("truncation_reasons", []),
            }
        )

    return evidence


# ============================================================
# 7. BUILD GROUNDED CONTEXT FOR THE LLM
# ============================================================


def build_context(evidence):
    """
    GOAL
    ----------------------------------------------------------
    Turn normalized SFOE evidence into numbered context blocks.

    INPUT
    ----------------------------------------------------------
    evidence:
        Normalized search results.

    OUTPUT
    ----------------------------------------------------------
    One text string containing [Source 1], [Source 2], ...
    """

    context_parts = []

    for item in evidence:
        number = item["citation_number"]

        context_parts.append(
            f"""
[Source {number}]
Document: {item["title"]}
Published: {item["published_at"] or "Unknown"}
URL: {item["download_url"] or "Not available"}
Relevance score: {item["score"]}
Years covered: {item["years_covered"]}
Projection: {item["is_projection"]}
Truncated: {item["is_truncated"]}

Passage:
{item["text"]}
"""
        )

    return "\n".join(context_parts)


# ============================================================
# 8. GENERATE A GROUNDED ANSWER WITH BEDROCK
# ============================================================


def generate_answer(question, evidence):
    """
    GOAL
    ----------------------------------------------------------
    Generate one clear answer using only retrieved SFOE
    evidence.

    INPUT
    ----------------------------------------------------------
    question:
        User question.

    evidence:
        Normalized SFOE passages and metadata.

    OUTPUT
    ----------------------------------------------------------
    Final answer with [1], [2], ... citations.
    """

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
    )

    context = build_context(evidence)

    system_prompt = """
You are an assistant for the Swiss Federal Office of Energy
(SFOE/BFE) Open Energy Knowledge Gateway.

Answer the user's question using ONLY the provided SFOE
evidence.

Rules:
- Do not invent facts.
- Do not use unsupported external knowledge.
- Cite factual claims with [1], [2], [3], etc.
- Citation numbers must refer to the numbered sources provided.
- If the evidence is insufficient, clearly say so.
- Answer in the same language as the user's question.
- Be concise, clear and factual.
- If a source has Projection: True, do NOT present its
  forward-looking statements as measured historical facts.
- If a source has Truncated: True, treat the passage cautiously
  because it may be incomplete.
- Do not create page numbers because page numbers are not
  available in this corpus.
"""

    user_prompt = f"""
USER QUESTION:

{question}


RETRIEVED SFOE EVIDENCE:

{context}


Answer the question using only this evidence.
"""

    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user_prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": 1000,
            "temperature": 0.1,
        },
    )

    # Extract the first text block robustly.
    content = response["output"]["message"]["content"]

    for item in content:
        if isinstance(item, dict) and "text" in item:
            return item["text"]

    raise RuntimeError("Bedrock returned no text answer.")


# ============================================================
# 9. DISPLAY SUPPORTING SOURCES
# ============================================================


def print_sources(evidence):
    """
    GOAL
    ----------------------------------------------------------
    Show the official SFOE sources supporting the answer.

    INPUT
    ----------------------------------------------------------
    evidence:
        Normalized evidence list.

    OUTPUT
    ----------------------------------------------------------
    Numbered source list matching the LLM citations.
    """

    print("\n======================================")
    print("              SOURCES")
    print("======================================")

    if not evidence:
        print("\nNo sources found.")
        return

    for item in evidence:
        number = item["citation_number"]

        print(f"\n[{number}] {item['title']}")

        if item["published_at"]:
            print(f"    Published : {item['published_at']}")

        try:
            print(f"    Score     : {float(item['score']):.3f}")
        except (TypeError, ValueError):
            print(f"    Score     : {item['score']}")

        if item["download_url"]:
            print(f"    PDF       : {item['download_url']}")

        if item["is_projection"]:
            print("    Note      : Contains projection / forward-looking information")

        if item["is_truncated"]:
            print("    Warning   : Retrieved passage may be truncated")


# ============================================================
# 10. OPTIONAL: DISPLAY RETRIEVED EVIDENCE
# ============================================================


def print_retrieved_evidence(evidence):
    """
    GOAL
    ----------------------------------------------------------
    Display the exact retrieved passages for debugging and
    evaluation.

    INPUT
    ----------------------------------------------------------
    evidence:
        Normalized evidence list.

    OUTPUT
    ----------------------------------------------------------
    Retrieved source text and metadata.
    """

    print("\n======================================")
    print("        RETRIEVED EVIDENCE")
    print("======================================")

    for item in evidence:
        number = item["citation_number"]

        print(f"\n--- Source {number} ---")
        print(f"Document: {item['title']}")
        print(f"Projection: {item['is_projection']}")
        print(f"Truncated: {item['is_truncated']}")
        print()
        print(item["text"])
        print("-" * 70)


# ============================================================
# 11. MAIN APPLICATION
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Run the complete SFOE RAG workflow.

    INPUT
    ----------------------------------------------------------
    Natural-language question entered by the user.

    PROCESS
    ----------------------------------------------------------
    1. Read question.
    2. Optionally authenticate with Cognito.
    3. Search SFOE publications through MCP.
    4. Normalize the new response structure.
    5. Send top-ranked evidence to Bedrock.
    6. Display grounded answer.
    7. Display official SFOE sources.

    OUTPUT
    ----------------------------------------------------------
    Answer + citations + source links.
    """

    print("\n======================================")
    print("   SFOE Open Energy Knowledge Gateway")
    print("======================================")

    question = input("\nAsk SFOE: ").strip()

    if not question:
        print("\nPlease enter a question.")
        return

    try:
        # ----------------------------------------------------
        # STEP 1: OPTIONAL AUTHENTICATION
        # ----------------------------------------------------

        access_token = None

        if all([CLIENT_ID, CLIENT_SECRET, TOKEN_URL]):
            print("\n1. Authenticating with Cognito...")
            access_token = fetch_access_token()
            print("   Authentication successful.")
        else:
            print("\n1. Gateway authentication not required/configured.")

        # ----------------------------------------------------
        # STEP 2: SFOE SEARCH
        # ----------------------------------------------------

        print("\n2. Searching SFOE energy knowledge...")

        search_data = retrieve(
            question=question,
            access_token=access_token,
        )

        evidence = prepare_evidence(search_data)

        print(
            f"   Gateway returned "
            f"{search_data.get('result_count', len(evidence))} results."
        )

        print(f"   Using top {len(evidence)} passages for the answer.")

        if not evidence:
            print("\nNo relevant information was found in the SFOE knowledge base.")
            return

        # ----------------------------------------------------
        # STEP 3: BEDROCK GENERATION
        # ----------------------------------------------------

        print(f"\n3. Generating grounded answer with {BEDROCK_MODEL_ID}...")

        answer = generate_answer(
            question=question,
            evidence=evidence,
        )

        # ----------------------------------------------------
        # STEP 4: DISPLAY ANSWER
        # ----------------------------------------------------

        print("\n======================================")
        print("               ANSWER")
        print("======================================")

        print("\n" + answer)

        # ----------------------------------------------------
        # STEP 5: DISPLAY SOURCES
        # ----------------------------------------------------

        print_sources(evidence)

        # Uncomment during development if you want to inspect
        # the exact passages sent to the LLM:
        #
        # print_retrieved_evidence(evidence)

    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except requests.exceptions.HTTPError as error:
        print("\nHTTP error:")
        print(error)

    except requests.exceptions.RequestException as error:
        print("\nConnection error:")
        print(error)

    except (ClientError, BotoCoreError) as error:
        print("\nAWS Bedrock error:")
        print(error)

    except KeyError as error:
        print("\nUnexpected response format.")
        print(f"Missing field: {error}")

    except Exception as error:
        print("\nUnexpected error:")
        print(error)


# ============================================================
# 12. START APPLICATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Run:
#
#     python app.py
#
# OUTPUT
# ------------------------------------------------------------
# Starts the interactive SFOE assistant.
# ============================================================

if __name__ == "__main__":
    main()
