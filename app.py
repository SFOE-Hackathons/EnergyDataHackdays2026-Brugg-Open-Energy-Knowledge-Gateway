import os
import json
import requests
import boto3

from dotenv import load_dotenv


# ============================================================
# GOAL OF THIS APPLICATION
# ============================================================
# Build a simple SFOE / BFE knowledge assistant.
#
# The application:
#
# 1. Takes a natural-language question from the user.
# 2. Authenticates with Amazon Cognito.
# 3. Calls the SFOE MCP retrieval tool.
# 4. Retrieves relevant chunks from the SFOE Knowledge Base.
# 5. Sends the retrieved chunks to an LLM in Amazon Bedrock.
# 6. Generates one clear answer grounded in the retrieved sources.
# 7. Displays the final answer and the supporting sources.
#
#
# DATA FLOW
# ============================================================
#
# User question
#     ↓
# Cognito authentication
#     ↓
# MCP Gateway
#     ↓
# SFOE Bedrock Knowledge Base
#     ↓
# Relevant document chunks
#     ↓
# Bedrock LLM
#     ↓
# Final answer + citations
#
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION FROM .env
# ============================================================
# INPUT:
#   CLIENT_ID
#   CLIENT_SECRET
#   TOKEN_URL
#   BEDROCK_MODEL_ID
#   AWS_REGION
#
# OUTPUT:
#   Python variables used by the application.
#
# IMPORTANT:
#   .env contains secrets and must NOT be committed to GitHub.
# ============================================================

load_dotenv(override=True)

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
TOKEN_URL = os.environ["TOKEN_URL"]

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")


# ============================================================
# 2. MCP GATEWAY CONFIGURATION
# ============================================================
# GOAL:
#   Define how the application connects to the SFOE MCP Gateway.
#
# OUTPUT:
#   Gateway endpoint, protocol version, and retrieval tool name.
# ============================================================

GATEWAY_URL = (
    "https://sandbox-bfe-public-kb-8thmswsvit."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
)

MCP_PROTOCOL_VERSION = "2026-07-28"

TOOL_NAME = "bfe-public-knowledge___Retrieve"


# ============================================================
# 3. AUTHENTICATION
# ============================================================


def fetch_access_token():
    """
    GOAL
    ----------------------------------------------------------
    Authenticate the application with Amazon Cognito.

    INPUT
    ----------------------------------------------------------
    CLIENT_ID
    CLIENT_SECRET
    TOKEN_URL

    OUTPUT
    ----------------------------------------------------------
    OAuth access token as a string.

    PROCESS
    ----------------------------------------------------------
    1. Send CLIENT_ID and CLIENT_SECRET to Cognito.
    2. Use OAuth client_credentials flow.
    3. Receive an access token.
    4. Return the token to the application.
    """

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
# 4. RETRIEVE SFOE KNOWLEDGE THROUGH MCP
# ============================================================


def retrieve(gateway_url, access_token, question):
    """
    GOAL
    ----------------------------------------------------------
    Search the SFOE Knowledge Base through the MCP Gateway.

    INPUT
    ----------------------------------------------------------
    gateway_url:
        URL of the MCP Gateway.

    access_token:
        Cognito access token.

    question:
        User's natural-language question.

    OUTPUT
    ----------------------------------------------------------
    List of retrieved SFOE document chunks.

    Each result typically contains:
        - retrieved text
        - document metadata
        - source URL
        - relevance score

    PROCESS
    ----------------------------------------------------------
    1. Build the MCP request.
    2. Send the user's question to the Retrieve tool.
    3. Receive the MCP response.
    4. Parse the nested JSON response.
    5. Return only the retrieval results.
    """

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": TOOL_NAME,
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "retrieve-request",
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            "arguments": {"retrievalQuery": {"text": question}},
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
        gateway_url,
        headers=headers,
        json=payload,
        timeout=120,
    )

    response.raise_for_status()

    data = response.json()

    # MCP returns the retrieval result as a JSON string
    # inside result -> content -> text.
    text = data["result"]["content"][0]["text"]

    retrieval_data = json.loads(text)

    return retrieval_data["retrievalResults"]


# ============================================================
# 5. GENERATE A FINAL ANSWER WITH AN LLM
# ============================================================


def generate_answer(question, results):
    """
    GOAL
    ----------------------------------------------------------
    Convert retrieved SFOE document chunks into one clear,
    user-friendly answer using an LLM in Amazon Bedrock.

    INPUT
    ----------------------------------------------------------
    question:
        Original question entered by the user.

    results:
        Retrieved SFOE chunks returned by retrieve().

    OUTPUT
    ----------------------------------------------------------
    One grounded answer as a string.

    Example:
        "Hydropower remains a central pillar of Swiss
        electricity supply [1] ..."

    PROCESS
    ----------------------------------------------------------
    1. Convert retrieved chunks into structured context.
    2. Number the sources as [1], [2], [3], ...
    3. Send question + context to the Bedrock LLM.
    4. Instruct the LLM to use only retrieved SFOE sources.
    5. Return the generated answer.
    """

    # --------------------------------------------------------
    # Create Bedrock Runtime client
    # --------------------------------------------------------
    # IMPORTANT:
    # boto3 needs AWS credentials with permission to invoke
    # the selected Bedrock model.
    # --------------------------------------------------------

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
    )

    # --------------------------------------------------------
    # Build trusted context for the LLM
    # --------------------------------------------------------

    context_parts = []

    for i, item in enumerate(results, start=1):
        metadata = item.get("metadata", {})

        document = metadata.get("_document_title", "Unknown document")

        source = metadata.get("_source_uri", "Unknown source")

        retrieved_text = item["content"]["text"]

        context_parts.append(
            f"""
[Source {i}]
Document: {document}
URL: {source}

{retrieved_text}
"""
        )

    context = "\n".join(context_parts)

    # --------------------------------------------------------
    # System instructions for grounding
    # --------------------------------------------------------

    system_prompt = """
You are an assistant for the Swiss Federal Office of Energy
(SFOE/BFE) Open Energy Knowledge Gateway.

Answer the user's question using ONLY the provided retrieved
SFOE sources.

Rules:
- Do not invent information.
- Do not add unsupported external knowledge.
- Cite factual statements using [1], [2], [3], etc.
- If the retrieved sources are insufficient, clearly say so.
- Answer in the same language as the user's question.
- Be concise, clear, and factual.
"""

    # --------------------------------------------------------
    # User message sent to the LLM
    # --------------------------------------------------------

    user_prompt = f"""
USER QUESTION:

{question}


RETRIEVED SFOE SOURCES:

{context}


Please answer the user's question using only these sources.
"""

    # --------------------------------------------------------
    # Call Bedrock LLM
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Extract generated answer
    # --------------------------------------------------------

    answer = response["output"]["message"]["content"][0]["text"]

    return answer


# ============================================================
# 6. DISPLAY SUPPORTING SOURCES
# ============================================================


def print_sources(results):
    """
    GOAL
    ----------------------------------------------------------
    Show the documents used to generate the answer.

    INPUT
    ----------------------------------------------------------
    results:
        Retrieved SFOE chunks.

    OUTPUT
    ----------------------------------------------------------
    Clean source list shown in the terminal.

    Example:

        [1] document.pdf
            Score: 0.594
            URL: https://...
    """

    print("\n======================================")
    print("              SOURCES")
    print("======================================")

    if not results:
        print("\nNo sources found.")
        return

    for i, item in enumerate(results, start=1):
        metadata = item.get("metadata", {})

        document = metadata.get("_document_title", "Unknown document")

        source = metadata.get("_source_uri", "No source available")

        score = item.get("score", 0)

        print(f"\n[{i}] {document}")
        print(f"    Score : {score:.3f}")
        print(f"    URL   : {source}")


# ============================================================
# 7. OPTIONAL: DISPLAY FULL RETRIEVED EVIDENCE
# ============================================================


def print_retrieved_evidence(results):
    """
    GOAL
    ----------------------------------------------------------
    Show the actual text chunks retrieved from the Knowledge
    Base.

    INPUT
    ----------------------------------------------------------
    results:
        Retrieved SFOE chunks.

    OUTPUT
    ----------------------------------------------------------
    Retrieved text for inspection/debugging.

    NOTE:
    This is useful for developers and evaluation.
    A normal end user may not need to see this by default.
    """

    print("\n======================================")
    print("        RETRIEVED EVIDENCE")
    print("======================================")

    for i, item in enumerate(results, start=1):
        retrieved_text = item["content"]["text"]

        print(f"\n--- Source {i} ---")
        print(retrieved_text)
        print("-" * 70)


# ============================================================
# 8. MAIN APPLICATION
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Control the complete application workflow.

    INPUT
    ----------------------------------------------------------
    Natural-language question entered by the user.

    PROCESS
    ----------------------------------------------------------
    1. Read the user question.
    2. Authenticate with Cognito.
    3. Retrieve SFOE knowledge through MCP.
    4. Send retrieved knowledge to the Bedrock LLM.
    5. Display the generated answer.
    6. Display supporting sources.

    OUTPUT
    ----------------------------------------------------------
    - Final grounded answer
    - Supporting SFOE sources
    """

    print("\n======================================")
    print("   SFOE Open Energy Knowledge Gateway")
    print("======================================")

    # --------------------------------------------------------
    # USER INPUT
    # --------------------------------------------------------

    question = input("\nAsk SFOE: ").strip()

    if not question:
        print("\nPlease enter a question.")
        return

    try:
        # ----------------------------------------------------
        # STEP 1: AUTHENTICATION
        # ----------------------------------------------------

        print("\n1. Authenticating...")

        access_token = fetch_access_token()

        print("   Authentication successful.")

        # ----------------------------------------------------
        # STEP 2: RETRIEVAL / RAG SEARCH
        # ----------------------------------------------------

        print("\n2. Searching SFOE Knowledge Base...")

        results = retrieve(
            GATEWAY_URL,
            access_token,
            question,
        )

        print(f"   Found {len(results)} relevant document chunks.")

        # ----------------------------------------------------
        # Handle empty retrieval
        # ----------------------------------------------------

        if not results:
            print("\nNo relevant information was found in the SFOE Knowledge Base.")
            return

        # ----------------------------------------------------
        # STEP 3: GENERATE LLM ANSWER
        # ----------------------------------------------------

        print("\n3. Generating grounded answer...")

        answer = generate_answer(
            question,
            results,
        )

        # ----------------------------------------------------
        # STEP 4: DISPLAY FINAL ANSWER
        # ----------------------------------------------------

        print("\n======================================")
        print("               ANSWER")
        print("======================================")

        print("\n" + answer)

        # ----------------------------------------------------
        # STEP 5: DISPLAY SOURCES
        # ----------------------------------------------------

        print_sources(results)

        # ----------------------------------------------------
        # OPTIONAL:
        # Uncomment the line below if you want to inspect
        # the full retrieved chunks during development.
        # ----------------------------------------------------

        # print_retrieved_evidence(results)

    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except requests.exceptions.HTTPError as error:
        print("\nHTTP error:")
        print(error)

    except requests.exceptions.RequestException as error:
        print("\nConnection error:")
        print(error)

    except KeyError as error:
        print("\nUnexpected response format.")
        print(f"Missing field: {error}")

    except boto3.exceptions.Boto3Error as error:
        print("\nAWS Bedrock error:")
        print(error)

    except Exception as error:
        print("\nUnexpected error:")
        print(error)


# ============================================================
# 9. START APPLICATION
# ============================================================
# INPUT:
#   Running:
#
#       python app.py
#
# OUTPUT:
#   Starts the interactive SFOE assistant.
# ============================================================

if __name__ == "__main__":
    main()
