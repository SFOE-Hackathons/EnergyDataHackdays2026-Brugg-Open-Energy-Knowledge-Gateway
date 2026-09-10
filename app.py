import os
import json
import requests
from dotenv import load_dotenv


# ============================================================
# GOAL OF THIS APPLICATION
# ============================================================
# This app allows a user to ask a question about Swiss energy.
#
# Data flow:
#
# User question
#     ↓
# Cognito authentication
#     ↓
# MCP Gateway
#     ↓
# SFOE / BFE Knowledge Base
#     ↓
# Relevant document chunks
#     ↓
# Display results to the user
#
# Important:
# At this stage, this is still mainly a RETRIEVAL application.
# We have NOT yet added an LLM that generates a final answer.
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION / SECRETS
# ============================================================
# INPUT:
# Values stored locally in the .env file:
#
# CLIENT_ID
# CLIENT_SECRET
# TOKEN_URL
#
# OUTPUT:
# Python variables that will be used for authentication.
#
# The .env file must NOT be committed to GitHub.
# ============================================================

load_dotenv(override=True)

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
TOKEN_URL = os.environ["TOKEN_URL"]


# ============================================================
# 2. MCP GATEWAY CONFIGURATION
# ============================================================
# This is the AWS Bedrock AgentCore Gateway.
#
# The Python application sends MCP requests to this endpoint.
# The Gateway then connects to the SFOE Knowledge Base.
# ============================================================

GATEWAY_URL = (
    "https://sandbox-bfe-public-kb-8thmswsvit."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
)

MCP_PROTOCOL_VERSION = "2026-07-28"

# MCP tool exposed by the SFOE Gateway
TOOL_NAME = "bfe-public-knowledge___Retrieve"


# ============================================================
# 3. AUTHENTICATION
# ============================================================


def fetch_access_token():
    """
    Goal:
        Authenticate the application with Amazon Cognito.

    Input:
        CLIENT_ID
        CLIENT_SECRET
        TOKEN_URL

    Output:
        OAuth access token.

    The access token is later sent to the MCP Gateway
    as proof that our application is authorized.
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

    # Stop the application if authentication failed
    response.raise_for_status()

    # Extract only the access token from the Cognito response
    return response.json()["access_token"]


# ============================================================
# 4. RETRIEVE INFORMATION FROM SFOE
# ============================================================


def retrieve(gateway_url, access_token, question):
    """
    Goal:
        Search the SFOE Knowledge Base through MCP.

    Input:
        gateway_url:
            Address of the MCP Gateway.

        access_token:
            Authentication token received from Cognito.

        question:
            User's natural-language question.

    Example input:
        "Welche Rolle spielt Wasserkraft in der Schweiz?"

    Output:
        A list of relevant document chunks retrieved
        from the SFOE Knowledge Base.

    Example output:
        [
            {
                "content": {...},
                "metadata": {...},
                "score": 0.59
            },
            ...
        ]
    """

    # --------------------------------------------------------
    # HTTP headers
    # --------------------------------------------------------
    # The Bearer token authenticates us.
    # MCP headers tell the Gateway which protocol/tool we use.
    # --------------------------------------------------------

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": TOOL_NAME,
    }

    # --------------------------------------------------------
    # MCP request
    # --------------------------------------------------------
    # We ask the MCP Gateway to call the SFOE Retrieve tool.
    #
    # The important input is:
    #
    # retrievalQuery.text = user's question
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Send request to AWS AgentCore Gateway
    # --------------------------------------------------------

    response = requests.post(
        gateway_url,
        headers=headers,
        json=payload,
        timeout=120,
    )

    # Stop if the Gateway returns an error
    response.raise_for_status()

    # --------------------------------------------------------
    # Parse MCP response
    # --------------------------------------------------------

    data = response.json()

    # The MCP response contains another JSON string
    # inside result -> content -> text.
    text = data["result"]["content"][0]["text"]

    # Convert that JSON string into a Python dictionary
    retrieval_data = json.loads(text)

    # Return only the useful retrieval results
    return retrieval_data["retrievalResults"]


# ============================================================
# 5. DISPLAY RETRIEVED RESULTS
# ============================================================


def print_results(results):
    """
    Goal:
        Present retrieval results in a readable form.

    Input:
        List of retrieval results from the Knowledge Base.

    Output shown to user:
        - Result number
        - Relevance score
        - Document name
        - Source URL
        - Retrieved text
    """

    print("\n======================================")
    print("         SFOE RETRIEVAL RESULTS")
    print("======================================")

    if not results:
        print("\nNo relevant documents were found.")
        return

    for i, item in enumerate(results, start=1):
        # Metadata contains information about the source document
        metadata = item.get("metadata", {})

        score = item.get("score", 0)

        document = metadata.get("_document_title", "Unknown document")

        source = metadata.get("_source_uri", "No source available")

        retrieved_text = item["content"]["text"]

        # ----------------------------------------------------
        # Clean result shown in terminal
        # ----------------------------------------------------

        print(f"\nResult {i}")
        print("-" * 70)

        print(f"Score    : {score:.3f}")
        print(f"Document : {document}")
        print(f"Source   : {source}")

        print("\nRetrieved text:")
        print(retrieved_text)

        print("-" * 70)


# ============================================================
# 6. MAIN APPLICATION
# ============================================================


def main():
    """
    Main workflow of the application.

    INPUT:
        User types a question in the terminal.

    PROCESS:
        1. Get access token from Cognito
        2. Send question through MCP
        3. Retrieve relevant SFOE documents
        4. Display them

    OUTPUT:
        Relevant SFOE document chunks and their sources.
    """

    print("\n======================================")
    print("   SFOE Open Energy Knowledge Gateway")
    print("======================================")

    # --------------------------------------------------------
    # USER INPUT
    # --------------------------------------------------------

    question = input("\nAsk SFOE: ").strip()

    # Do not continue if the user entered nothing
    if not question:
        print("\nPlease enter a question.")
        return

    try:
        # ----------------------------------------------------
        # STEP 1: Authenticate
        # ----------------------------------------------------

        print("\n1. Authenticating with AWS Cognito...")

        access_token = fetch_access_token()

        print("   Authentication successful.")

        # ----------------------------------------------------
        # STEP 2: Retrieve SFOE knowledge
        # ----------------------------------------------------

        print("\n2. Searching SFOE Knowledge Base...")

        results = retrieve(
            GATEWAY_URL,
            access_token,
            question,
        )

        print(f"   Found {len(results)} retrieval results.")

        # ----------------------------------------------------
        # STEP 3: Show retrieved information
        # ----------------------------------------------------

        print_results(results)

    # --------------------------------------------------------
    # ERROR HANDLING
    # --------------------------------------------------------

    except requests.exceptions.HTTPError as error:
        print("\nHTTP error:")
        print(error)

    except requests.exceptions.RequestException as error:
        print("\nConnection error:")
        print(error)

    except KeyError as error:
        print("\nUnexpected response format.")
        print(f"Missing field: {error}")

    except Exception as error:
        print("\nUnexpected error:")
        print(error)


# ============================================================
# 7. START APPLICATION
# ============================================================
# Python runs main() only when we directly execute:
#
# python app.py
#
# If another Python file imports app.py,
# main() will NOT automatically run.
# ============================================================

if __name__ == "__main__":
    main()
