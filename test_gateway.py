import os
import json
import requests

from dotenv import load_dotenv


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Test the current SFOE Open Energy Knowledge MCP Gateway.
#
# The script:
#
# 1. Loads the current Gateway URL from .env.
# 2. Optionally authenticates with Cognito.
# 3. Calls MCP "tools/list".
# 4. Shows all tools currently exposed by the Gateway.
# 5. Automatically looks for a retrieval tool.
# 6. Calls that tool with a test question.
# 7. Displays retrieved SFOE documents in a readable format.
#
#
# WHY THIS VERSION IS BETTER
# ============================================================
# The previous version hardcoded:
#
#     bfe-public-knowledge___Retrieve
#
# But AWS infrastructure can change.
#
# This version first asks the Gateway:
#
#     "Which MCP tools do you currently provide?"
#
# Therefore:
#
# Gateway
#    ↓
# tools/list
#    ↓
# discover tool name
#    ↓
# tools/call
#
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
#
# INPUT
# ------------------------------------------------------------
# Values from .env:
#
# GATEWAY_URL
#
# Optional Cognito values:
# CLIENT_ID
# CLIENT_SECRET
# TOKEN_URL
#
# OUTPUT
# ------------------------------------------------------------
# Configuration values used by the script.
#
# ============================================================

load_dotenv(override=True)

GATEWAY_URL = os.environ["GATEWAY_URL"]

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TOKEN_URL = os.getenv("TOKEN_URL")


# ============================================================
# 2. MCP CONFIGURATION
# ============================================================

MCP_PROTOCOL_VERSION = "2026-07-28"


# ============================================================
# 3. OPTIONAL COGNITO AUTHENTICATION
# ============================================================


def fetch_access_token():
    """
    GOAL
    ----------------------------------------------------------
    Obtain a Cognito OAuth access token if Cognito credentials
    are configured.

    INPUT
    ----------------------------------------------------------
    CLIENT_ID
    CLIENT_SECRET
    TOKEN_URL

    OUTPUT
    ----------------------------------------------------------
    Access token string.

    NOTE
    ----------------------------------------------------------
    The new "bfe-energy-knowledge-open" Gateway currently shows
    "No authorization" for inbound authentication.

    Therefore Cognito may no longer be required.

    This function remains here so the script can still work
    with authenticated gateways if needed.
    """

    # If Cognito configuration is missing, do not authenticate.
    if not all(
        [
            CLIENT_ID,
            CLIENT_SECRET,
            TOKEN_URL,
        ]
    ):
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


def build_headers(
    access_token=None,
    method=None,
    tool_name=None,
):
    """
    GOAL
    ----------------------------------------------------------
    Build HTTP headers for an MCP request.

    INPUT
    ----------------------------------------------------------
    access_token:
        Optional Cognito token.

    method:
        MCP method such as:
        - tools/list
        - tools/call

    tool_name:
        Tool name when using tools/call.

    OUTPUT
    ----------------------------------------------------------
    Dictionary of HTTP headers.
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }

    # Add authentication only if we have a token.
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    # Add MCP method header if supplied.
    if method:
        headers["Mcp-Method"] = method

    # Add MCP tool name only for tools/call.
    if tool_name:
        headers["Mcp-Name"] = tool_name

    return headers


# ============================================================
# 5. SEND GENERIC MCP REQUEST
# ============================================================


def send_mcp_request(
    payload,
    access_token=None,
    method=None,
    tool_name=None,
):
    """
    GOAL
    ----------------------------------------------------------
    Send one JSON-RPC request to the MCP Gateway.

    INPUT
    ----------------------------------------------------------
    payload:
        JSON-RPC request body.

    access_token:
        Optional authentication token.

    method:
        MCP method.

    tool_name:
        Optional tool name.

    OUTPUT
    ----------------------------------------------------------
    Parsed JSON response from the MCP Gateway.

    ERROR HANDLING
    ----------------------------------------------------------
    Raises a clear RuntimeError if the Gateway returns an
    MCP-level "error" object.
    """

    headers = build_headers(
        access_token=access_token,
        method=method,
        tool_name=tool_name,
    )

    response = requests.post(
        GATEWAY_URL,
        headers=headers,
        json=payload,
        timeout=120,
    )

    print(f"HTTP status: {response.status_code}")

    response.raise_for_status()

    data = response.json()

    # --------------------------------------------------------
    # MCP / JSON-RPC ERROR CHECK
    # --------------------------------------------------------

    if "error" in data:
        raise RuntimeError(
            "MCP Gateway returned an error:\n"
            + json.dumps(
                data["error"],
                indent=2,
                ensure_ascii=False,
            )
        )

    if "result" not in data:
        raise RuntimeError(
            "Unexpected MCP response:\n"
            + json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )
        )

    return data


# ============================================================
# 6. DISCOVER AVAILABLE MCP TOOLS
# ============================================================


def list_tools(access_token=None):
    """
    GOAL
    ----------------------------------------------------------
    Ask the Gateway which MCP tools are currently available.

    INPUT
    ----------------------------------------------------------
    Optional access token.

    OUTPUT
    ----------------------------------------------------------
    List of MCP tool definitions.

    Example:
        [
            {
                "name": "...Retrieve",
                "description": "...",
                "inputSchema": {...}
            }
        ]

    WHY
    ----------------------------------------------------------
    This avoids relying on an outdated hardcoded TOOL_NAME.
    """

    payload = {
        "jsonrpc": "2.0",
        "id": "tools-list-request",
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "sfoe-gateway-test",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }

    data = send_mcp_request(
        payload=payload,
        access_token=access_token,
        method="tools/list",
    )

    tools = data["result"].get("tools", [])

    return tools


# ============================================================
# 7. PRINT DISCOVERED TOOLS
# ============================================================


def print_tools(tools):
    """
    GOAL
    ----------------------------------------------------------
    Display available MCP tools in a clean readable format.

    INPUT
    ----------------------------------------------------------
    List returned by list_tools().

    OUTPUT
    ----------------------------------------------------------
    Tool number, name and description.
    """

    print("\n")
    print("=" * 80)
    print("AVAILABLE MCP TOOLS")
    print("=" * 80)

    if not tools:
        print("\nNo MCP tools were returned.")

        return

    for index, tool in enumerate(tools, start=1):
        print(f"\n{index}. {tool.get('name', 'Unknown')}")

        description = tool.get("description")

        if description:
            print(f"   Description: {description}")


# ============================================================
# 8. FIND RETRIEVAL TOOL AUTOMATICALLY
# ============================================================


def find_retrieve_tool(tools):
    """
    GOAL
    ----------------------------------------------------------
    Automatically identify the Gateway tool intended for
    knowledge retrieval.

    INPUT
    ----------------------------------------------------------
    List of MCP tools.

    OUTPUT
    ----------------------------------------------------------
    Tool name string.

    SEARCH LOGIC
    ----------------------------------------------------------
    Prefer tool names containing:
        retrieve
        retrieval
        knowledge

    If no likely tool exists, return None.
    """

    preferred_keywords = [
        "retrieve",
        "retrieval",
        "knowledge",
    ]

    # --------------------------------------------------------
    # First preference: "retrieve"
    # --------------------------------------------------------

    for keyword in preferred_keywords:
        for tool in tools:
            name = tool.get("name", "")

            if keyword in name.lower():
                return name

    return None


# ============================================================
# 9. CALL RETRIEVAL TOOL
# ============================================================


def retrieve(
    access_token,
    tool_name,
    question,
):
    """
    ============================================================
    GOAL
    ============================================================
    Search the current SFOE Energy Knowledge Gateway.

    ============================================================
    INPUT
    ============================================================
    access_token:
        Optional Cognito token.

    tool_name:
        MCP tool discovered with tools/list.

    question:
        Natural-language search query.

    ============================================================
    OUTPUT
    ============================================================
    Complete parsed response from the SFOE search tool.

    The new tool returns an object containing:
        - search results/passages
        - source attribution
        - additional metadata
    ============================================================
    """

    payload = {
        "jsonrpc": "2.0",
        "id": "retrieve-request",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            # ------------------------------------------------
            # NEW SFOE MCP TOOL FORMAT
            # ------------------------------------------------
            "arguments": {"query": question},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "sfoe-gateway-test",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }

    data = send_mcp_request(
        payload=payload,
        access_token=access_token,
        method="tools/call",
        tool_name=tool_name,
    )

    # =========================================================
    # EXTRACT TEXT CONTENT FROM MCP RESPONSE
    # =========================================================

    content = data["result"].get("content", [])

    if not content:
        raise RuntimeError("MCP retrieval returned no content.")

    text_content = None

    for item in content:
        if isinstance(item, dict) and "text" in item:
            text_content = item["text"]
            break

    if text_content is None:
        raise RuntimeError("No text content was found in the MCP result.")

    # =========================================================
    # PARSE JSON RETURNED INSIDE MCP TEXT
    # =========================================================

    try:
        search_data = json.loads(text_content)

    except json.JSONDecodeError:
        raise RuntimeError(
            f"The MCP tool returned text but it was not valid JSON:\n{text_content}"
        )

    return search_data


# ============================================================
# 10. PRINT RETRIEVAL RESULTS
# ============================================================


def print_results(search_data):
    """
    ============================================================
    GOAL
    ============================================================
    Display results returned by the NEW SFOE knowledge-search
    tool in a readable format.

    ============================================================
    INPUT
    ============================================================
    search_data:
        Complete JSON object returned by
        search_energy_knowledge.

    ============================================================
    OUTPUT
    ============================================================
    For every retrieved passage:
        - relevance score
        - document title
        - publication date
        - public PDF URL
        - years covered
        - passage text

    Source information is resolved using source_id and the
    top-level "sources" dictionary.
    ============================================================
    """

    print("\n")
    print("=" * 80)
    print("SFOE RETRIEVAL RESULTS")
    print("=" * 80)

    # =========================================================
    # DEBUG: SHOW TOP-LEVEL RESPONSE STRUCTURE
    # =========================================================
    # This is useful because the new gateway has a different
    # response format from the old Bedrock Retrieve API.
    # =========================================================

    if not isinstance(search_data, dict):
        print("\nUnexpected response type:")

        print(type(search_data))

        print(search_data)

        return

    print("\nResponse fields:", list(search_data.keys()))

    # =========================================================
    # FIND RESULT LIST
    # =========================================================
    #
    # The current API may call the passages:
    #
    #   results
    #   passages
    #   entries
    #
    # We support these possibilities so the test script is
    # resilient to small API changes.
    # =========================================================

    results = (
        search_data.get("results")
        or search_data.get("passages")
        or search_data.get("entries")
        or []
    )

    # =========================================================
    # SOURCE DICTIONARY
    # =========================================================
    #
    # Example idea:
    #
    # sources = {
    #     "s1": {
    #         "title": "...",
    #         "published_at": "...",
    #         "download_url": "..."
    #     }
    # }
    #
    # Each result then contains:
    #
    #     "source_id": "s1"
    #
    # =========================================================

    sources = search_data.get("sources", {})

    if not results:
        print("\nNo result list found.")

        print("\nComplete response:")

        print(
            json.dumps(
                search_data,
                indent=2,
                ensure_ascii=False,
            )
        )

        return

    # =========================================================
    # PRINT EACH RESULT
    # =========================================================

    for index, item in enumerate(results, start=1):
        print("\n")
        print(f"Result {index}")

        print("-" * 70)

        # -----------------------------------------------------
        # Make sure each result is actually an object.
        # -----------------------------------------------------

        if not isinstance(item, dict):
            print("Unexpected result:")

            print(item)

            continue

        # -----------------------------------------------------
        # RESULT METADATA
        # -----------------------------------------------------

        score = item.get("score")

        source_id = item.get("source_id")

        years_covered = item.get("years_covered")

        is_projection = item.get("is_projection")

        is_truncated = item.get("is_truncated")

        # -----------------------------------------------------
        # RESOLVE SOURCE INFORMATION
        # -----------------------------------------------------

        source = {}

        if source_id:
            source = sources.get(source_id, {})

        title = (
            source.get("title") or source.get("document_title") or "Unknown document"
        )

        published_at = source.get("published_at")

        download_url = source.get("download_url")

        # -----------------------------------------------------
        # RETRIEVED PASSAGE
        # -----------------------------------------------------

        text = item.get("text") or item.get("passage") or item.get("content") or ""

        # -----------------------------------------------------
        # PRINT BASIC INFORMATION
        # -----------------------------------------------------

        if score is not None:
            try:
                print(f"Score       : {float(score):.3f}")

            except (TypeError, ValueError):
                print(f"Score       : {score}")

        print(f"Source ID   : {source_id}")

        print(f"Document    : {title}")

        if published_at:
            print(f"Published   : {published_at}")

        if download_url:
            print(f"PDF         : {download_url}")

        if years_covered:
            print(f"Years       : {years_covered}")

        if is_projection is not None:
            print(f"Projection  : {is_projection}")

        if is_truncated is not None:
            print(f"Truncated   : {is_truncated}")

        print("\nText:")

        print(text)


# ============================================================
# 11. MAIN TEST WORKFLOW
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Test the complete current SFOE MCP Gateway.

    INPUT
    ----------------------------------------------------------
    Current GATEWAY_URL from .env.

    Optional Cognito configuration.

    Test question:
        Welche Rolle spielt Wasserkraft in der
        Schweizer Stromversorgung?

    PROCESS
    ----------------------------------------------------------
    1. Optionally authenticate.
    2. Discover available MCP tools.
    3. Print tool names.
    4. Automatically identify retrieval tool.
    5. Send test question.
    6. Print retrieved SFOE evidence.

    OUTPUT
    ----------------------------------------------------------
    Current Gateway tool configuration + retrieval results.
    """

    print("\n")
    print("=" * 80)
    print("SFOE MCP GATEWAY TEST")
    print("=" * 80)

    print(f"\nGateway:\n{GATEWAY_URL}")

    # --------------------------------------------------------
    # STEP 1: OPTIONAL AUTHENTICATION
    # --------------------------------------------------------

    access_token = None

    if all(
        [
            CLIENT_ID,
            CLIENT_SECRET,
            TOKEN_URL,
        ]
    ):
        print("\n1. Cognito configuration found.")

        try:
            access_token = fetch_access_token()

            print("   Authentication successful.")

        except requests.RequestException as error:
            print("\n   Cognito authentication failed.")

            print(f"   {error}")

            print(
                "\n   Continuing without authentication "
                "because the current Gateway may be open."
            )

            access_token = None

    else:
        print("\n1. No Cognito configuration found.")

        print("   Continuing without authentication.")

    # --------------------------------------------------------
    # STEP 2: DISCOVER MCP TOOLS
    # --------------------------------------------------------

    print("\n2. Discovering MCP tools...")

    tools = list_tools(access_token)

    print_tools(tools)

    # --------------------------------------------------------
    # STEP 3: FIND RETRIEVAL TOOL
    # --------------------------------------------------------

    tool_name = find_retrieve_tool(tools)

    if not tool_name:
        print("\nNo retrieval tool was automatically identified.")

        print("Check the AVAILABLE MCP TOOLS list above.")

        return

    print("\n" + "=" * 80)

    print("SELECTED RETRIEVAL TOOL")

    print("=" * 80)

    print(f"\n{tool_name}")

    # --------------------------------------------------------
    # STEP 4: TEST RETRIEVAL
    # --------------------------------------------------------

    question = "Welche Rolle spielt Wasserkraft in der Schweizer Stromversorgung?"

    print("\n" + "=" * 80)

    print("TEST QUESTION")

    print("=" * 80)

    print(f"\n{question}")

    print("\n3. Retrieving SFOE knowledge...")

    results = retrieve(
        access_token=access_token,
        tool_name=tool_name,
        question=question,
    )

    # --------------------------------------------------------
    # STEP 5: DISPLAY RESULTS
    # --------------------------------------------------------

    print_results(results)


# ============================================================
# 12. START SCRIPT
# ============================================================
#
# INPUT:
#
#     python test_gateway.py
#
# OUTPUT:
#
#     1. Current Gateway URL
#     2. Available MCP tools
#     3. Selected retrieval tool
#     4. Real SFOE retrieval results
#
# ============================================================

if __name__ == "__main__":
    main()
