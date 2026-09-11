import os
import re
import json
import copy
from typing import Any, Dict, List, Optional, Tuple

import boto3
import requests

from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Build a simple MULTI-TOOL AI AGENT for the SFOE Open Energy
# Knowledge Gateway.
#
# INPUT
# ------------------------------------------------------------
# A user question, for example:
#
#   "What role does hydropower play in Switzerland?"
#
#   "How did photovoltaic production develop from 2020 to 2024?"
#
#   "What values are shown in the relevant SFOE chart?"
#
# MAIN PROCESS
# ------------------------------------------------------------
# 1. Load AWS / MCP configuration from .env.
# 2. Discover the currently available MCP tools dynamically.
# 3. Ask the selected Bedrock LLM to choose the best MCP tool.
# 4. Let the LLM produce arguments that match that tool schema.
# 5. Call the MCP tool.
# 6. Normalize the returned source references.
# 7. Ask the LLM to generate a grounded answer.
# 8. Display the official SFOE sources.
#
# OUTPUT
# ------------------------------------------------------------
# A grounded answer with citations such as [1], [2], ...
# plus the SFOE source titles / links.
#
# IMPORTANT
# ------------------------------------------------------------
# This file is intentionally separate from app.py.
#
# app.py       = stable single-tool RAG baseline
# agent_app.py = multi-tool agent that decides which MCP tool
#                to use for each question
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Required / recommended .env values:
#
# GATEWAY_URL=https://...gateway.bedrock-agentcore.../mcp
# AWS_REGION=eu-central-1
# BEDROCK_MODEL_ID=nvidia.nemotron-super-3-120b
#
# Optional Cognito values:
#
# CLIENT_ID=...
# CLIENT_SECRET=...
# TOKEN_URL=https://.../oauth2/token
#
# The current gateway may not require inbound authorization.
# Therefore Cognito is optional in this script.
#
# IMPORTANT:
# Never commit .env or AWS/Cognito secrets to Git.
# ============================================================

load_dotenv(override=True)

GATEWAY_URL = os.getenv(
    "GATEWAY_URL",
    "",
).strip()

AWS_REGION = os.getenv(
    "AWS_REGION",
    "eu-central-1",
).strip()

BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "nvidia.nemotron-super-3-120b",
).strip()

CLIENT_ID = os.getenv(
    "CLIENT_ID",
    "",
).strip()

CLIENT_SECRET = os.getenv(
    "CLIENT_SECRET",
    "",
).strip()

TOKEN_URL = os.getenv(
    "TOKEN_URL",
    "",
).strip()

# Current MCP protocol version used by the SFOE gateway.
MCP_PROTOCOL_VERSION = os.getenv(
    "MCP_PROTOCOL_VERSION",
    "2026-07-28",
).strip()

# Limit the amount of retrieved material sent to the final LLM.
TOP_K_RESULTS = int(
    os.getenv(
        "TOP_K_RESULTS",
        "5",
    )
)

MAX_TOOL_CONTEXT_CHARS = int(
    os.getenv(
        "MAX_TOOL_CONTEXT_CHARS",
        "30000",
    )
)

MAX_ROUTER_TOKENS = int(
    os.getenv(
        "MAX_ROUTER_TOKENS",
        "1000",
    )
)

MAX_ANSWER_TOKENS = int(
    os.getenv(
        "MAX_ANSWER_TOKENS",
        "1000",
    )
)


# ============================================================
# 2. KNOWN SFOE MCP TOOLS
# ============================================================
# These names are NOT hardcoded as the only allowed tools.
# The script still discovers the actual tools dynamically with
# tools/list.
#
# They are listed here only to help the router understand the
# intended use of the current SFOE gateway.
# ============================================================

SEARCH_TOOL_HINT = (
    "bfe-energy___search_energy_knowledge"
)

TIMELINE_TOOL_HINT = (
    "bfe-energy___get_metric_timeline"
)

CHART_TOOL_HINT = (
    "bfe-energy___get_chart_data"
)


# ============================================================
# 3. VALIDATE BASIC CONFIGURATION
# ============================================================

def validate_configuration() -> None:
    """
    GOAL
    ----------------------------------------------------------
    Stop early if an essential value is missing.

    INPUT
    ----------------------------------------------------------
    Values loaded from .env.

    OUTPUT
    ----------------------------------------------------------
    No return value.
    Raises a clear exception if configuration is incomplete.
    """

    if not GATEWAY_URL:
        raise RuntimeError(
            "GATEWAY_URL is missing. Add it to your .env file."
        )

    if not BEDROCK_MODEL_ID:
        raise RuntimeError(
            "BEDROCK_MODEL_ID is missing."
        )


# ============================================================
# 4. CREATE BEDROCK CLIENT
# ============================================================

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
)


# ============================================================
# 5. OPTIONAL COGNITO AUTHENTICATION
# ============================================================

def cognito_is_configured() -> bool:
    """
    GOAL
    ----------------------------------------------------------
    Check whether all Cognito client-credentials values exist.

    OUTPUT
    ----------------------------------------------------------
    True  -> use Cognito
    False -> call gateway without Authorization header
    """

    return all(
        [
            CLIENT_ID,
            CLIENT_SECRET,
            TOKEN_URL,
        ]
    )


def fetch_access_token() -> Optional[str]:
    """
    GOAL
    ----------------------------------------------------------
    Obtain a Cognito access token when Cognito is configured.

    INPUT
    ----------------------------------------------------------
    CLIENT_ID
    CLIENT_SECRET
    TOKEN_URL

    OUTPUT
    ----------------------------------------------------------
    Access token string, or None if Cognito is not configured.
    """

    if not cognito_is_configured():
        return None

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
        },
        auth=(
            CLIENT_ID,
            CLIENT_SECRET,
        ),
        timeout=30,
    )

    response.raise_for_status()

    payload = response.json()

    token = payload.get(
        "access_token"
    )

    if not token:
        raise RuntimeError(
            "Cognito response did not contain access_token."
        )

    return token


# ============================================================
# 6. BUILD MCP HTTP HEADERS
# ============================================================

def build_mcp_headers(
    access_token: Optional[str],
    method: str,
    tool_name: Optional[str] = None,
) -> Dict[str, str]:
    """
    GOAL
    ----------------------------------------------------------
    Build the MCP headers expected by the SFOE AgentCore
    gateway.

    INPUT
    ----------------------------------------------------------
    access_token:
        Optional Cognito token.

    method:
        MCP method, e.g. "tools/list" or "tools/call".

    tool_name:
        Required only for "tools/call".

    OUTPUT
    ----------------------------------------------------------
    HTTP header dictionary.

    IMPORTANT
    ----------------------------------------------------------
    The working SFOE gateway expects the MCP method in the
    "Mcp-Method" header. For tools/call it also expects the
    selected tool name in "Mcp-Name".
    """

    headers = {
        "Content-Type":
            "application/json",
        "Accept":
            "application/json, text/event-stream",
        "MCP-Protocol-Version":
            MCP_PROTOCOL_VERSION,
        "Mcp-Method":
            method,
    }

    if (
        method == "tools/call"
        and tool_name
    ):
        headers[
            "Mcp-Name"
        ] = tool_name

    if access_token:
        headers[
            "Authorization"
        ] = f"Bearer {access_token}"

    return headers


# ============================================================
# 7. PARSE MCP HTTP / SSE RESPONSE
# ============================================================

def parse_mcp_http_response(
    response: requests.Response,
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Parse either normal JSON or MCP Server-Sent Events (SSE).

    INPUT
    ----------------------------------------------------------
    HTTP response from the gateway.

    OUTPUT
    ----------------------------------------------------------
    Parsed JSON-RPC dictionary.
    """

    # Show the gateway response body when an MCP request fails.
    # This is much more useful than a generic "400 Bad Request".
    if not response.ok:
        raise RuntimeError(
            f"MCP gateway HTTP {response.status_code}:\n"
            f"{response.text[:4000]}"
        )

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    if (
        "application/json"
        in content_type
    ):
        return response.json()

    text = response.text.strip()

    # Some MCP gateways return SSE:
    #
    # event: message
    # data: {"jsonrpc":"2.0", ...}
    #
    # We collect all "data:" records and use the last JSON one.
    data_lines = []

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith(
            "data:"
        ):
            data_lines.append(
                stripped[5:].strip()
            )

    for candidate in reversed(
        data_lines
    ):
        try:
            return json.loads(
                candidate
            )
        except json.JSONDecodeError:
            continue

    # Final fallback: perhaps the body is plain JSON even if the
    # Content-Type header was unusual.
    try:
        return json.loads(
            text
        )
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Could not parse MCP gateway response as JSON/SSE.\n"
            f"Raw response:\n{text[:2000]}"
        ) from error


# ============================================================
# 8. GENERIC MCP JSON-RPC CALL
# ============================================================

_request_counter = 0


def next_request_id() -> int:
    """
    GOAL
    ----------------------------------------------------------
    Produce a simple increasing JSON-RPC request ID.
    """

    global _request_counter

    _request_counter += 1

    return _request_counter


def mcp_request(
    method: str,
    params: Optional[Dict[str, Any]],
    access_token: Optional[str],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Send a JSON-RPC request to the MCP gateway.

    INPUT
    ----------------------------------------------------------
    method:
        e.g. "tools/list" or "tools/call"

    params:
        JSON-RPC params dictionary.

    access_token:
        Optional Cognito token.

    OUTPUT
    ----------------------------------------------------------
    Parsed JSON-RPC response.
    """

    # --------------------------------------------------------
    # Prepare MCP params and include the metadata block used by
    # the working SFOE gateway calls.
    # --------------------------------------------------------

    request_params = (
        copy.deepcopy(params)
        if params is not None
        else {}
    )

    request_params.setdefault(
        "_meta",
        {
            "io.modelcontextprotocol/protocolVersion":
                MCP_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name":
                    "sfoe-energy-knowledge-agent",
                "version":
                    "1.0.0",
            },
            "io.modelcontextprotocol/clientCapabilities":
                {},
        },
    )

    body = {
        "jsonrpc": "2.0",
        "id": next_request_id(),
        "method": method,
        "params": request_params,
    }

    # For tools/call the AgentCore gateway also expects the
    # selected tool name in the HTTP header.
    tool_name = None

    if method == "tools/call":
        tool_name = request_params.get(
            "name"
        )

    response = requests.post(
        GATEWAY_URL,
        headers=build_mcp_headers(
            access_token=access_token,
            method=method,
            tool_name=tool_name,
        ),
        json=body,
        timeout=90,
    )

    payload = parse_mcp_http_response(
        response
    )

    if payload.get(
        "error"
    ):
        raise RuntimeError(
            "MCP JSON-RPC error:\n"
            + json.dumps(
                payload["error"],
                indent=2,
                ensure_ascii=False,
            )
        )

    return payload


# ============================================================
# 9. DISCOVER MCP TOOLS
# ============================================================

def list_mcp_tools(
    access_token: Optional[str],
) -> List[Dict[str, Any]]:
    """
    GOAL
    ----------------------------------------------------------
    Discover the tools available from the current gateway.

    INPUT
    ----------------------------------------------------------
    MCP gateway connection.

    OUTPUT
    ----------------------------------------------------------
    List of MCP tool definitions, including names, descriptions
    and input schemas.

    WHY THIS MATTERS
    ----------------------------------------------------------
    We do not want agent_app.py to depend on stale hardcoded
    argument schemas.
    """

    payload = mcp_request(
        method="tools/list",
        params={},
        access_token=access_token,
    )

    result = payload.get(
        "result",
        {},
    )

    tools = result.get(
        "tools",
        [],
    )

    if not tools:
        raise RuntimeError(
            "The MCP gateway returned no tools."
        )

    return tools


# ============================================================
# 10. EXTRACT TEXT FROM BEDROCK CONVERSE RESPONSE
# ============================================================

def extract_generated_text(
    response: Dict[str, Any],
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Extract all text blocks from a Bedrock Converse response.

    OUTPUT
    ----------------------------------------------------------
    Combined text string.
    """

    content = (
        response[
            "output"
        ][
            "message"
        ][
            "content"
        ]
    )

    parts = []

    for item in content:
        if (
            isinstance(
                item,
                dict,
            )
            and "text" in item
        ):
            parts.append(
                item["text"]
            )

    if not parts:
        raise ValueError(
            "Bedrock model returned no text."
        )

    return "\n".join(
        parts
    )


# ============================================================
# 11. ROBUST JSON EXTRACTION FROM LLM OUTPUT
# ============================================================

def extract_json_object(
    text: str,
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Parse a JSON object even if the model accidentally adds a
    code fence or a small amount of surrounding text.

    INPUT
    ----------------------------------------------------------
    Raw model text.

    OUTPUT
    ----------------------------------------------------------
    Parsed Python dictionary.
    """

    cleaned = text.strip()

    try:
        return json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
    ).strip()

    try:
        return json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        pass

    start = cleaned.find(
        "{"
    )

    if start == -1:
        raise ValueError(
            "No JSON object found in model response."
        )

    depth = 0
    in_string = False
    escape = False

    for index in range(
        start,
        len(cleaned),
    ):
        char = cleaned[index]

        if escape:
            escape = False
            continue

        if (
            char == "\\"
            and in_string
        ):
            escape = True
            continue

        if char == '"':
            in_string = (
                not in_string
            )
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                candidate = cleaned[
                    start:index + 1
                ]

                return json.loads(
                    candidate
                )

    raise ValueError(
        "Could not extract complete JSON object "
        "from model response."
    )


# ============================================================
# 12. PREPARE TOOL DESCRIPTIONS FOR THE ROUTER
# ============================================================

def build_tool_catalog_text(
    tools: List[Dict[str, Any]],
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Convert MCP tool definitions into text the LLM can use for
    tool selection and argument generation.

    INPUT
    ----------------------------------------------------------
    Dynamic tools/list response.

    OUTPUT
    ----------------------------------------------------------
    Compact tool catalog containing:
        - tool name
        - description
        - exact input schema
    """

    blocks = []

    for tool in tools:
        blocks.append(
            "\n".join(
                [
                    "TOOL NAME:",
                    str(
                        tool.get(
                            "name",
                            "",
                        )
                    ),
                    "",
                    "DESCRIPTION:",
                    str(
                        tool.get(
                            "description",
                            "",
                        )
                    ),
                    "",
                    "INPUT SCHEMA:",
                    json.dumps(
                        tool.get(
                            "inputSchema",
                            {},
                        ),
                        indent=2,
                        ensure_ascii=False,
                    ),
                ]
            )
        )

    return (
        "\n\n"
        "============================================\n"
        "\n\n"
    ).join(
        blocks
    )


# ============================================================
# 13. BUILD AGENT ROUTER PROMPT
# ============================================================

def build_router_prompt(
    question: str,
    tools: List[Dict[str, Any]],
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Ask the selected LLM to choose exactly one MCP tool and
    provide valid arguments for it.

    INPUT
    ----------------------------------------------------------
    User question + dynamically discovered tool schemas.

    OUTPUT
    ----------------------------------------------------------
    Router prompt.

    ROUTING INTENT
    ----------------------------------------------------------
    General semantic knowledge:
        search_energy_knowledge

    Metric / multi-year evolution:
        get_metric_timeline

    Values from charts/tables/publication graphics:
        get_chart_data
    """

    tool_catalog = (
        build_tool_catalog_text(
            tools
        )
    )

    return f"""
You are the TOOL ROUTER for the SFOE Open Energy Knowledge
Gateway.

Your task is NOT to answer the user's question yet.

Choose EXACTLY ONE of the available MCP tools below and create
arguments that match that tool's input schema.

Use ONLY tool names and fields that actually appear in the
provided tool catalog.

============================================================
ROUTING GUIDANCE
============================================================

Prefer a general semantic search tool when the user asks about:

- role
- importance
- policy
- explanation
- concept
- qualitative information
- general SFOE knowledge

Prefer a metric timeline tool when the user explicitly asks
about:

- development over multiple years
- trend
- yearly values
- time series
- change between years
- evolution from year X to year Y

Prefer a chart-data tool when the user explicitly asks for:

- values extracted from a chart
- values extracted from a table
- chart series
- numeric data shown in a publication graphic/table
- exact data behind a chart/table

Do not choose a chart or timeline tool merely because the
question contains one number.

If the question can be answered by normal publication passages,
prefer the general semantic search tool.

============================================================
KNOWN CURRENT SFOE TOOL INTENT HINTS
============================================================

General search hint:
{SEARCH_TOOL_HINT}

Timeline hint:
{TIMELINE_TOOL_HINT}

Chart-data hint:
{CHART_TOOL_HINT}

These are only intent hints.
The ACTUAL tool catalog below is authoritative.

============================================================
USER QUESTION
============================================================

{question}

============================================================
AVAILABLE MCP TOOLS
============================================================

{tool_catalog}

============================================================
RETURN FORMAT
============================================================

Return ONLY valid JSON.

Do not use Markdown.

Use this structure:

{{
  "tool_name": "exact MCP tool name",
  "arguments": {{
    "field_from_selected_tool_schema": "value"
  }},
  "reason": "one short explanation of why this tool is best"
}}
"""


# ============================================================
# 14. LET THE LLM CHOOSE THE TOOL
# ============================================================

def choose_tool(
    question: str,
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Use the selected production model as the agent/router.

    INPUT
    ----------------------------------------------------------
    User question + available MCP tools.

    OUTPUT
    ----------------------------------------------------------
    Dictionary:
        tool_name
        arguments
        reason
    """

    prompt = build_router_prompt(
        question=question,
        tools=tools,
    )

    response = (
        bedrock_runtime.converse(
            modelId=
                BEDROCK_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens":
                    MAX_ROUTER_TOKENS,
                "temperature":
                    0,
            },
        )
    )

    text = extract_generated_text(
        response
    )

    selection = extract_json_object(
        text
    )

    validate_tool_selection(
        selection,
        tools,
    )

    return selection


# ============================================================
# 15. VALIDATE TOOL SELECTION
# ============================================================

def validate_tool_selection(
    selection: Dict[str, Any],
    tools: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Catch obvious router errors before calling MCP.

    INPUT
    ----------------------------------------------------------
    Router JSON + discovered tools.

    OUTPUT
    ----------------------------------------------------------
    No return value.
    Raises a clear error if selection is invalid.
    """

    if not isinstance(
        selection,
        dict,
    ):
        raise ValueError(
            "Router output is not a JSON object."
        )

    tool_name = selection.get(
        "tool_name"
    )

    arguments = selection.get(
        "arguments"
    )

    valid_names = {
        tool.get(
            "name"
        )
        for tool in tools
    }

    if tool_name not in valid_names:
        raise ValueError(
            "Router selected unknown tool:\n"
            f"{tool_name}\n\n"
            "Available tools:\n"
            + "\n".join(
                sorted(
                    name
                    for name in valid_names
                    if name
                )
            )
        )

    if not isinstance(
        arguments,
        dict,
    ):
        raise ValueError(
            "Router 'arguments' must be a JSON object."
        )

    if "reason" not in selection:
        selection[
            "reason"
        ] = ""


# ============================================================
# 16. CALL THE SELECTED MCP TOOL
# ============================================================

def call_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    access_token: Optional[str],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Execute the tool selected by the agent.

    INPUT
    ----------------------------------------------------------
    Exact MCP tool name + arguments matching its schema.

    OUTPUT
    ----------------------------------------------------------
    Parsed tool payload.
    """

    payload = mcp_request(
        method="tools/call",
        params={
            "name": tool_name,
            "arguments": arguments,
        },
        access_token=access_token,
    )

    result = payload.get(
        "result",
        {},
    )

    if result.get(
        "isError"
    ):
        raise RuntimeError(
            "MCP tool returned an error:\n"
            + json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

    # Some MCP implementations provide structuredContent.
    structured = result.get(
        "structuredContent"
    )

    if isinstance(
        structured,
        dict,
    ):
        return structured

    # Current gateway commonly returns JSON as a text block.
    content = result.get(
        "content",
        [],
    )

    text_parts = []

    for item in content:
        if (
            isinstance(
                item,
                dict,
            )
            and "text" in item
        ):
            text_parts.append(
                item["text"]
            )

    if not text_parts:
        # Fall back to the result object itself.
        return result

    combined_text = "\n".join(
        text_parts
    ).strip()

    # Prefer parsing the text payload as JSON.
    try:
        parsed = json.loads(
            combined_text
        )

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

        return {
            "data": parsed,
        }

    except json.JSONDecodeError:
        return {
            "text": combined_text,
        }


# ============================================================
# 17. OPTIONAL ARGUMENT REPAIR
# ============================================================

def find_tool_definition(
    tool_name: str,
    tools: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    GOAL
    ----------------------------------------------------------
    Find one tool's exact definition from tools/list.
    """

    for tool in tools:
        if (
            tool.get(
                "name"
            )
            == tool_name
        ):
            return tool

    return None


def repair_tool_arguments(
    question: str,
    selection: Dict[str, Any],
    error_message: str,
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Give the agent one chance to repair invalid tool arguments.

    INPUT
    ----------------------------------------------------------
    Original question
    selected tool/arguments
    MCP error
    exact tool schema

    OUTPUT
    ----------------------------------------------------------
    Corrected router selection.

    IMPORTANT
    ----------------------------------------------------------
    This retries only once. We do not want an uncontrolled loop.
    """

    tool_name = selection[
        "tool_name"
    ]

    tool = find_tool_definition(
        tool_name,
        tools,
    )

    if not tool:
        raise RuntimeError(
            "Cannot repair arguments because the tool "
            "definition could not be found."
        )

    prompt = f"""
You are repairing arguments for an SFOE MCP tool call.

Do NOT answer the user's question.

Keep the SAME tool unless the error proves it is impossible to
use.

============================================================
USER QUESTION
============================================================

{question}

============================================================
SELECTED TOOL
============================================================

{tool_name}

============================================================
TOOL DESCRIPTION
============================================================

{tool.get("description", "")}

============================================================
EXACT INPUT SCHEMA
============================================================

{json.dumps(
    tool.get("inputSchema", {}),
    indent=2,
    ensure_ascii=False,
)}

============================================================
FAILED ARGUMENTS
============================================================

{json.dumps(
    selection.get("arguments", {}),
    indent=2,
    ensure_ascii=False,
)}

============================================================
MCP ERROR
============================================================

{error_message}

============================================================
RETURN FORMAT
============================================================

Return ONLY valid JSON:

{{
  "tool_name": "{tool_name}",
  "arguments": {{
    "valid_schema_field": "corrected value"
  }},
  "reason": "short explanation of the correction"
}}
"""

    response = (
        bedrock_runtime.converse(
            modelId=
                BEDROCK_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens":
                    MAX_ROUTER_TOKENS,
                "temperature":
                    0,
            },
        )
    )

    repaired = extract_json_object(
        extract_generated_text(
            response
        )
    )

    validate_tool_selection(
        repaired,
        tools,
    )

    return repaired


# ============================================================
# 18. COMPACT LARGE TOOL RESULTS
# ============================================================

def compact_tool_payload(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Avoid sending unnecessarily large tool responses to the
    answer-generation model.

    INPUT
    ----------------------------------------------------------
    Parsed MCP tool result.

    OUTPUT
    ----------------------------------------------------------
    Copy of the result with common result arrays limited to
    TOP_K_RESULTS.

    IMPORTANT
    ----------------------------------------------------------
    Top-level source metadata is retained.
    """

    compact = copy.deepcopy(
        payload
    )

    for key in [
        "results",
        "items",
        "matches",
        "records",
    ]:
        value = compact.get(
            key
        )

        if isinstance(
            value,
            list,
        ):
            compact[key] = value[
                :TOP_K_RESULTS
            ]

    return compact


# ============================================================
# 19. BUILD DEDUPLICATED SOURCE NUMBER MAP
# ============================================================

def build_source_number_map(
    payload: Dict[str, Any],
) -> Tuple[
    Dict[str, int],
    List[Dict[str, Any]],
]:
    """
    GOAL
    ----------------------------------------------------------
    Convert MCP source IDs such as s1, s2 into clean citation
    numbers [1], [2], ...

    INPUT
    ----------------------------------------------------------
    Tool payload, normally containing:
        "sources": {"s1": {...}, "s2": {...}}

    OUTPUT
    ----------------------------------------------------------
    1. source_id -> citation number map
    2. deduplicated normalized source list

    DEDUPLICATION
    ----------------------------------------------------------
    Prefer download_url as the unique identity.

    If no URL exists, use:
        title + publication date

    Duplicate source IDs that refer to the same publication
    receive the SAME citation number.
    """

    source_number_map = {}
    normalized_sources = []
    identity_to_number = {}

    sources = payload.get(
        "sources",
        {},
    )

    def normalize_metadata(
        metadata: Any,
    ) -> Dict[str, Any]:

        if isinstance(
            metadata,
            dict,
        ):
            return metadata

        return {
            "value": metadata
        }

    def source_identity(
        source_id: str,
        metadata: Dict[str, Any],
    ) -> str:
        """
        Build a stable identity for source deduplication.
        """

        url = (
            metadata.get("download_url")
            or metadata.get("url")
        )

        if url:
            return (
                "url:"
                + str(url)
                .strip()
                .lower()
            )

        title = (
            metadata.get("title")
            or metadata.get("document")
        )

        published_at = metadata.get(
            "published_at"
        )

        if title:
            return (
                "title:"
                + str(title)
                .strip()
                .lower()
                + "|published:"
                + str(
                    published_at
                    or ""
                )
                .strip()
                .lower()
            )

        return (
            "source_id:"
            + str(source_id)
        )

    def register_source(
        source_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Register one source.

        If it is a duplicate publication, reuse the existing
        citation number instead of creating a new one.
        """

        identity = source_identity(
            source_id,
            metadata,
        )

        if identity in identity_to_number:
            source_number_map[
                source_id
            ] = identity_to_number[
                identity
            ]
            return

        number = (
            len(normalized_sources)
            + 1
        )

        identity_to_number[
            identity
        ] = number

        source_number_map[
            source_id
        ] = number

        normalized_sources.append(
            {
                "number":
                    number,
                "source_id":
                    source_id,
                "title":
                    metadata.get("title")
                    or metadata.get("document")
                    or "SFOE source",
                "published_at":
                    metadata.get(
                        "published_at"
                    ),
                "download_url":
                    metadata.get(
                        "download_url"
                    )
                    or metadata.get(
                        "url"
                    ),
            }
        )

    if isinstance(
        sources,
        dict,
    ):
        for (
            source_id,
            metadata,
        ) in sources.items():

            register_source(
                source_id=
                    str(source_id),
                metadata=
                    normalize_metadata(
                        metadata
                    ),
            )

    elif isinstance(
        sources,
        list,
    ):
        for index, metadata in enumerate(
            sources,
            start=1,
        ):

            metadata = (
                normalize_metadata(
                    metadata
                )
            )

            source_id = str(
                metadata.get(
                    "source_id",
                    index,
                )
            )

            register_source(
                source_id=
                    source_id,
                metadata=
                    metadata,
            )

    return (
        source_number_map,
        normalized_sources,
    )

# ============================================================
# 20. ANNOTATE SOURCE IDS WITH [N] CITATIONS
# ============================================================

def annotate_source_citations(
    value: Any,
    source_number_map: Dict[str, int],
) -> Any:
    """
    GOAL
    ----------------------------------------------------------
    Help the final LLM understand how source_id values map to
    citation labels [1], [2], ...

    INPUT
    ----------------------------------------------------------
    Tool result + source ID map.

    OUTPUT
    ----------------------------------------------------------
    Recursively copied structure where dictionaries containing
    source_id also receive "_citation": "[N]".
    """

    if isinstance(
        value,
        dict,
    ):
        output = {}

        for key, item in value.items():
            output[key] = (
                annotate_source_citations(
                    item,
                    source_number_map,
                )
            )

        source_id = value.get(
            "source_id"
        )

        if source_id is not None:
            number = source_number_map.get(
                str(source_id)
            )

            if number is not None:
                output[
                    "_citation"
                ] = f"[{number}]"

        return output

    if isinstance(
        value,
        list,
    ):
        return [
            annotate_source_citations(
                item,
                source_number_map,
            )
            for item in value
        ]

    return value


# ============================================================
# 21. BUILD FINAL EVIDENCE CONTEXT
# ============================================================

def prepare_agent_context(
    tool_name: str,
    tool_payload: Dict[str, Any],
) -> Tuple[
    str,
    List[Dict[str, Any]],
]:
    """
    GOAL
    ----------------------------------------------------------
    Convert any of the SFOE MCP tool outputs into one generic
    context format for final answer generation.

    INPUT
    ----------------------------------------------------------
    Selected tool name + parsed tool payload.

    OUTPUT
    ----------------------------------------------------------
    1. Context text for the final LLM
    2. Normalized source list for terminal display

    WHY THIS IS GENERIC
    ----------------------------------------------------------
    The three MCP tools return different structures. Rather
    than hardcoding every field, we retain the structured JSON
    and annotate source IDs with [N] citation labels.
    """

    compact = compact_tool_payload(
        tool_payload
    )

    (
        source_number_map,
        normalized_sources,
    ) = build_source_number_map(
        compact
    )

    annotated = (
        annotate_source_citations(
            compact,
            source_number_map,
        )
    )

    source_legend_lines = []

    for source in normalized_sources:
        source_legend_lines.append(
            (
                f"[{source['number']}] "
                f"{source['title']} | "
                f"Published: "
                f"{source['published_at']} | "
                f"URL: "
                f"{source['download_url']}"
            )
        )

    source_legend = (
        "\n".join(
            source_legend_lines
        )
        if source_legend_lines
        else (
            "No separate top-level source map "
            "was returned by this tool."
        )
    )

    result_json = json.dumps(
        annotated,
        indent=2,
        ensure_ascii=False,
    )

    if (
        len(result_json)
        > MAX_TOOL_CONTEXT_CHARS
    ):
        result_json = (
            result_json[
                :MAX_TOOL_CONTEXT_CHARS
            ]
            + "\n\n[TOOL RESULT TRUNCATED BY AGENT_APP "
            "FOR CONTEXT SIZE]"
        )

    context = f"""
============================================================
MCP TOOL USED
============================================================

{tool_name}


============================================================
SOURCE LEGEND
============================================================

{source_legend}


============================================================
MCP TOOL RESULT
============================================================

{result_json}
"""

    return (
        context,
        normalized_sources,
    )


# ============================================================
# 22. BUILD TOOL-SPECIFIC ANSWER INSTRUCTIONS
# ============================================================

def build_tool_specific_answer_instructions(
    question: str,
    tool_name: str,
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Give the answer model formatting rules that depend on which
    MCP tool the agent selected.

    INPUT
    ----------------------------------------------------------
    question:
        Original user question.

    tool_name:
        MCP tool selected by the router.

    OUTPUT
    ----------------------------------------------------------
    A short instruction block for final answer generation.

    WHY
    ----------------------------------------------------------
    The three MCP tools serve different purposes:

    search_energy_knowledge
        -> qualitative / explanatory answers

    get_metric_timeline
        -> year-by-year development / trends

    get_chart_data
        -> chart or table values, often with precision metadata
    """

    if tool_name == TIMELINE_TOOL_HINT:
        return """
TIMELINE-SPECIFIC RULES

- The user asked for development over time.
- If the question gives a year range and the evidence contains
  annual values within that range, include EVERY available year
  in that range.
- Do not summarize only the first and last year when intermediate
  annual values are available.
- Present the yearly values in chronological order.
- Prefer one short bullet per year.
- Put the supporting citation directly after each year's value.
- After the year-by-year list, give at most one short trend
  summary sentence with its citation(s).
- If a requested year is missing from the evidence, say that
  explicitly instead of inventing a value.
"""

    if tool_name == CHART_TOOL_HINT:
        return """
CHART-DATA-SPECIFIC RULES

- Preserve the chart/table metadata exactly.
- If values are estimates or ranges, explicitly say so.
- Preserve ranges as ranges; do not create a midpoint.
- If the result marks a value as exact, do not call it estimated.
- Include units exactly as supported by the tool result.
- When several values are requested and available, list them
  clearly rather than compressing them into a vague summary.
- Put the supporting citation directly after each factual bullet
  or sentence.
"""

    if tool_name == SEARCH_TOOL_HINT:
        return """
GENERAL-SEARCH-SPECIFIC RULES

- Answer the qualitative question directly and concisely.
- Prefer 2-5 short factual sentences or bullets when several
  distinct claims are needed.
- Put the supporting citation immediately after EACH factual
  sentence or bullet.
- Do not attach one large citation block only at the end of a
  paragraph containing multiple distinct claims.
- If the exact requested information is missing but related SFOE
  evidence was retrieved, cite the related supported facts before
  explaining that the exact requested information is not present
  in the supplied evidence.
- Do not invent a citation for the absence itself.
"""

    # Fallback for any future MCP tool dynamically discovered.
    return """
GENERAL ANSWER RULES

- Answer directly from the supplied MCP result.
- Keep factual claims short and individually cited.
"""


# ============================================================
# 23. BUILD GROUNDED ANSWER PROMPT
# ============================================================

def build_answer_prompt(
    question: str,
    tool_name: str,
    context: str,
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Generate a precise grounded answer from the selected MCP
    tool output.

    INPUT
    ----------------------------------------------------------
    User question
    selected tool name
    normalized tool context

    OUTPUT
    ----------------------------------------------------------
    Strict answer-generation prompt.

    IMPORTANT
    ----------------------------------------------------------
    This version adds:
    - tool-specific answer behavior
    - complete year-by-year timeline handling
    - stronger claim-level citation placement
    """

    tool_specific_instructions = (
        build_tool_specific_answer_instructions(
            question=question,
            tool_name=tool_name,
        )
    )

    return f"""
You are the answer-generation component of the SFOE Open
Energy Knowledge Gateway.

Answer the USER QUESTION using ONLY the MCP TOOL RESULT supplied
below.

Do NOT use external knowledge.

============================================================
STRICT GROUNDING RULES
============================================================

1. Every important factual claim must be supported by the
   supplied SFOE tool result.

2. Use citations such as [1], [2], [1][3] whenever the result
   provides a source mapping.

3. CLAIM-LEVEL CITATION RULE:
   Every sentence or bullet containing a factual claim should
   have its supporting citation immediately after that claim.

   GOOD:
       In 2024, PV production reached X GWh [2].

       Hydropower represented Y% of production [3].

   AVOID:
       Sentence one with a fact. Sentence two with another fact.
       Sentence three with another fact. [1][2][3]

4. A citation at the end of a paragraph does NOT automatically
   support several earlier factual sentences. Cite each factual
   sentence separately.

5. Each citation must support the specific claim beside it.
   Do not cite a source merely because it discusses the same
   general topic.

6. Use ONLY citation numbers that appear in the SOURCE LEGEND.

7. Do not make a claim stronger, broader or more specific than
   the evidence.

8. Be precise with:
   - percentages
   - years
   - units
   - targets
   - production vs demand
   - installed capacity vs annual generation
   - national totals vs individual values

9. If "is_projection" or "Projection" is true, clearly present
   the information as projected, expected, planned, a target
   or a scenario rather than as an observed historical fact.

10. If "is_estimate" is true OR precision/extraction metadata
    indicates an estimate, clearly describe the value as
    estimated.

    If the tool gives a range, preserve the range.
    Do NOT invent an exact value or midpoint.

11. If the tool result says a source/result is truncated, be
    cautious and do not make conclusions that require missing
    information.

12. If the available result cannot answer the user's question
    accurately, explicitly say that the available SFOE evidence
    is insufficient. Do not guess.

    IMPORTANT FOR ABSTENTION / INSUFFICIENT-EVIDENCE ANSWERS:

    - If relevant sources are present in the SOURCE LEGEND, do
      NOT return a completely uncited abstention.
    - Cite the factual statements about what the retrieved SFOE
      evidence DOES contain.
    - If the evidence contains current values, historical values,
      general policy information, scenario descriptions or other
      related facts, cite those statements immediately.
    - Then clearly state that the specifically requested value,
      year, projection or detail is not provided in the supplied
      evidence.
    - Phrase absence carefully as:
          "The supplied/retrieved evidence does not provide ..."
      rather than making the broader claim that no such
      information exists anywhere in SFOE publications.
    - Do NOT cite an unrelated source merely to create a citation.
    - Do NOT claim that a source proves the absence of information
      unless the source itself explicitly supports that claim.
    - When relevant source mappings exist, an abstention answer
      should normally contain at least one valid citation.

    EXAMPLE:

        The retrieved SFOE evidence reports nuclear generation of
        X TWh in 2022 [1] and discusses general future nuclear
        scenarios [2]. However, the supplied evidence does not
        provide a specific projection for nuclear production in
        2040. Therefore, that value cannot be determined from the
        retrieved evidence.

13. Do NOT invent page numbers.

14. Answer in the same language as the user's question.

15. Be concise, clear, factual and professional.

16. Do not create a separate bibliography. The application will
    display only the sources actually cited in the answer.

============================================================
TOOL-SPECIFIC INSTRUCTIONS
============================================================

{tool_specific_instructions}

============================================================
FINAL SELF-CHECK BEFORE RETURNING THE ANSWER
============================================================

Before returning the answer, verify:

- Did I answer the exact user question?
- Is every factual sentence/bullet directly supported?
- Is each factual sentence/bullet cited immediately?
- For a timeline, did I include every available requested year?
- Did I preserve production vs demand and capacity vs generation?
- Did I preserve estimate/projection/truncation meaning?
- Did I avoid unsupported conclusions?
- If I am abstaining because evidence is insufficient and relevant
  sources were retrieved, did I cite the supported facts that the
  evidence DOES contain?
- Did I avoid turning "not present in the supplied evidence" into
  the stronger claim "does not exist anywhere"?

Return only the final answer after this check.

============================================================
TOOL USED
============================================================

{tool_name}

============================================================
USER QUESTION
============================================================

{question}

============================================================
SFOE EVIDENCE / TOOL RESULT
============================================================

{context}

============================================================
ANSWER
============================================================
"""

# ============================================================
# 24. GENERATE FINAL ANSWER
# ============================================================

def generate_answer(
    question: str,
    tool_name: str,
    context: str,
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Ask the selected production LLM to write the final grounded
    answer.

    INPUT
    ----------------------------------------------------------
    User question + selected MCP evidence.

    OUTPUT
    ----------------------------------------------------------
    Final natural-language answer with citations.
    """

    prompt = build_answer_prompt(
        question=question,
        tool_name=tool_name,
        context=context,
    )

    response = (
        bedrock_runtime.converse(
            modelId=
                BEDROCK_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens":
                    MAX_ANSWER_TOKENS,
                "temperature":
                    0,
            },
        )
    )

    return extract_generated_text(
        response
    ).strip()


# ============================================================
# 25. CLEAN CITATIONS AND KEEP ONLY CITED SOURCES
# ============================================================

CITATION_BLOCK_PATTERN = re.compile(
    r"\[((?:\d+\s*,\s*)*\d+)\]"
)


def extract_citation_numbers(
    answer: str,
) -> List[int]:
    """
    GOAL
    ----------------------------------------------------------
    Find all citation numbers used in the final answer.

    INPUT
    ----------------------------------------------------------
    Answer text containing citations such as:
        [1]
        [2][4]
        [1, 3]

    OUTPUT
    ----------------------------------------------------------
    Unique citation numbers in first-use order.
    """

    used_numbers = []

    for match in CITATION_BLOCK_PATTERN.finditer(
        answer
    ):
        numbers = re.findall(
            r"\d+",
            match.group(1),
        )

        for number_text in numbers:
            number = int(
                number_text
            )

            if number not in used_numbers:
                used_numbers.append(
                    number
                )

    return used_numbers


def clean_answer_and_sources(
    answer: str,
    sources: List[Dict[str, Any]],
) -> Tuple[
    str,
    List[Dict[str, Any]],
]:
    """
    GOAL
    ----------------------------------------------------------
    Keep only the sources actually cited by the answer and
    renumber them consecutively.

    INPUT
    ----------------------------------------------------------
    answer:
        Generated answer.

    sources:
        Already deduplicated source list.

    OUTPUT
    ----------------------------------------------------------
    1. Answer with clean consecutive citation numbers.
    2. Cited-only source list.

    EXAMPLE
    ----------------------------------------------------------
    Before:
        Answer cites [4][7].
        Source list contains [1]...[8].

    After:
        Answer cites [1][2].
        Source list contains only those two publications.
    """

    source_by_number = {
        int(
            source["number"]
        ):
            source
        for source in sources
        if source.get(
            "number"
        ) is not None
    }

    used_numbers = [
        number
        for number in extract_citation_numbers(
            answer
        )
        if number in source_by_number
    ]

    if not used_numbers:
        return (
            answer,
            [],
        )

    old_to_new = {
        old_number:
            new_number
        for new_number, old_number in enumerate(
            used_numbers,
            start=1,
        )
    }

    def replace_citation_block(
        match,
    ) -> str:

        old_numbers = [
            int(value)
            for value in re.findall(
                r"\d+",
                match.group(1),
            )
        ]

        new_numbers = []

        for old_number in old_numbers:
            new_number = old_to_new.get(
                old_number
            )

            if (
                new_number is not None
                and new_number
                not in new_numbers
            ):
                new_numbers.append(
                    new_number
                )

        # Keep invalid citation blocks visible rather than
        # silently hiding a model error.
        if not new_numbers:
            return match.group(0)

        return "".join(
            f"[{number}]"
            for number in new_numbers
        )

    cleaned_answer = (
        CITATION_BLOCK_PATTERN.sub(
            replace_citation_block,
            answer,
        )
    )

    cleaned_sources = []

    for old_number in used_numbers:

        source = copy.deepcopy(
            source_by_number[
                old_number
            ]
        )

        source[
            "original_number"
        ] = old_number

        source[
            "number"
        ] = old_to_new[
            old_number
        ]

        cleaned_sources.append(
            source
        )

    return (
        cleaned_answer,
        cleaned_sources,
    )


# ============================================================
# 26. DISPLAY ONLY CITED SOURCES
# ============================================================

def print_sources(
    sources: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Show only the official SFOE publications actually cited in
    the final answer.

    INPUT
    ----------------------------------------------------------
    Cleaned / cited-only source list.

    OUTPUT
    ----------------------------------------------------------
    Human-readable terminal source list.
    """

    if not sources:
        print(
            "\nSources:\n"
            "No valid source citation was detected in the "
            "final answer."
        )

        return

    print(
        "\nSources:"
    )

    for source in sources:
        print(
            f"\n[{source['number']}] "
            f"{source['title']}"
        )

        if source.get(
            "published_at"
        ):
            print(
                "    Published: "
                f"{source['published_at']}"
            )

        if source.get(
            "download_url"
        ):
            print(
                "    "
                f"{source['download_url']}"
            )

# ============================================================
# 27. PROCESS ONE QUESTION
# ============================================================

def answer_question(
    question: str,
    tools: List[Dict[str, Any]],
    access_token: Optional[str],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Run the complete agent flow for one user question.

    INPUT
    ----------------------------------------------------------
    question
    dynamically discovered MCP tools
    optional auth token

    MAIN PROCESS
    ----------------------------------------------------------
    Question
        ↓
    LLM router
        ↓
    MCP tool + arguments
        ↓
    MCP result
        ↓
    normalized evidence
        ↓
    final grounded answer
        ↓
    source display
    """

    selection = choose_tool(
        question=question,
        tools=tools,
    )

    print(
        "\nSelected tool:"
    )

    print(
        selection[
            "tool_name"
        ]
    )

    print(
        "\nRouting reason:"
    )

    print(
        selection.get(
            "reason",
            "",
        )
    )

    print(
        "\nTool arguments:"
    )

    print(
        json.dumps(
            selection.get(
                "arguments",
                {},
            ),
            indent=2,
            ensure_ascii=False,
        )
    )

    # --------------------------------------------------------
    # First tool-call attempt
    # --------------------------------------------------------

    try:
        tool_payload = call_mcp_tool(
            tool_name=
                selection[
                    "tool_name"
                ],
            arguments=
                selection[
                    "arguments"
                ],
            access_token=
                access_token,
        )

    except Exception as first_error:
        # ----------------------------------------------------
        # One controlled repair attempt
        # ----------------------------------------------------

        print(
            "\nInitial MCP tool call failed."
        )

        print(
            "Trying one automatic argument repair..."
        )

        repaired = repair_tool_arguments(
            question=
                question,
            selection=
                selection,
            error_message=
                str(first_error),
            tools=
                tools,
        )

        print(
            "\nRepaired arguments:"
        )

        print(
            json.dumps(
                repaired.get(
                    "arguments",
                    {},
                ),
                indent=2,
                ensure_ascii=False,
            )
        )

        selection = repaired

        tool_payload = call_mcp_tool(
            tool_name=
                selection[
                    "tool_name"
                ],
            arguments=
                selection[
                    "arguments"
                ],
            access_token=
                access_token,
        )

    (
        context,
        sources,
    ) = prepare_agent_context(
        tool_name=
            selection[
                "tool_name"
            ],
        tool_payload=
            tool_payload,
    )

    answer = generate_answer(
        question=question,
        tool_name=
            selection[
                "tool_name"
            ],
        context=context,
    )

    # --------------------------------------------------------
    # CLEAN USER-FACING CITATIONS / SOURCES
    # --------------------------------------------------------
    # INPUT:
    #   Raw final answer + deduplicated source list
    #
    # OUTPUT:
    #   Clean answer with [1], [2], ... and only cited sources
    # --------------------------------------------------------

    (
        answer,
        sources,
    ) = clean_answer_and_sources(
        answer=answer,
        sources=sources,
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "ANSWER"
    )

    print(
        "=" * 80
    )

    print(
        "\n"
        + answer
    )

    print_sources(
        sources
    )


# ============================================================
# 28. PRINT DISCOVERED TOOLS
# ============================================================

def print_discovered_tools(
    tools: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Show which MCP tools the current gateway actually exposes.
    """

    print(
        "\nDiscovered MCP tools:"
    )

    for tool in tools:
        print(
            " - "
            + str(
                tool.get(
                    "name"
                )
            )
        )


# ============================================================
# 29. MAIN INTERACTIVE APPLICATION
# ============================================================

def main() -> None:
    """
    GOAL
    ----------------------------------------------------------
    Start an interactive SFOE multi-tool agent.

    INPUT
    ----------------------------------------------------------
    Questions typed in the terminal.

    OUTPUT
    ----------------------------------------------------------
    Tool routing + grounded SFOE answers + sources.
    """

    validate_configuration()

    print(
        "\n"
        + "=" * 80
    )

    print(
        "SFOE OPEN ENERGY KNOWLEDGE AGENT"
    )

    print(
        "=" * 80
    )

    print(
        "\nProduction model:"
    )

    print(
        BEDROCK_MODEL_ID
    )

    # --------------------------------------------------------
    # Optional authentication
    # --------------------------------------------------------

    if cognito_is_configured():
        print(
            "\nAuthenticating with Cognito..."
        )

        access_token = fetch_access_token()

        print(
            "Authentication successful."
        )

    else:
        access_token = None

        print(
            "\nCognito is not configured. "
            "Calling gateway without Authorization header."
        )

    # --------------------------------------------------------
    # Discover tools only once at application startup.
    # --------------------------------------------------------

    print(
        "\nDiscovering MCP tools..."
    )

    tools = list_mcp_tools(
        access_token
    )

    print_discovered_tools(
        tools
    )

    print(
        "\nType a question."
    )

    print(
        "Type 'exit' or 'quit' to stop."
    )

    # --------------------------------------------------------
    # Interactive question loop
    # --------------------------------------------------------

    while True:
        print(
            "\n"
            + "-" * 80
        )

        try:
            question = input(
                "\nQuestion: "
            ).strip()

        except (
            EOFError,
            KeyboardInterrupt,
        ):
            print(
                "\nStopping."
            )

            break

        if question.lower() in {
            "exit",
            "quit",
        }:
            print(
                "\nStopping."
            )

            break

        if not question:
            continue

        try:
            answer_question(
                question=
                    question,
                tools=
                    tools,
                access_token=
                    access_token,
            )

        except (
            ClientError,
            BotoCoreError,
        ) as error:
            print(
                "\nAWS Bedrock error:"
            )

            print(
                error
            )

        except requests.RequestException as error:
            print(
                "\nGateway / HTTP error:"
            )

            print(
                error
            )

        except Exception as error:
            print(
                "\nAgent error:"
            )

            print(
                error
            )


# ============================================================
# 30. START APPLICATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Run:
#
#     python agent_app.py
#
# Example questions:
#
#     What role does hydropower play in Switzerland?
#
#     How has photovoltaic production developed from
#     2020 to 2024?
#
#     Welche Werte zeigt das BFE für die erneuerbare
#     Stromproduktion in den Jahren 2020 bis 2024?
#
# OUTPUT
# ------------------------------------------------------------
# The agent:
#     1. selects an MCP tool
#     2. calls the tool
#     3. generates a grounded answer
#     4. prints official SFOE sources
# ============================================================

if __name__ == "__main__":
    main()
