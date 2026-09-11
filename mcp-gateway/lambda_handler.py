"""AWS Lambda entrypoint for the AgentCore Gateway Lambda target.

The gateway does not speak MCP to this function. It holds a static copy of the
tool catalogue (see tool_schema.py) and invokes the function directly through
the Lambda Invoke API, passing the tool's arguments as the event and the tool's
name in the client context.

That indirection exists because the obvious design does not work. The gateway
can also front a real MCP server over HTTP, and this project was built that way
first: the same image behind a Lambda Function URL with AWS_IAM auth, the
gateway signing SigV4 as its service role. It fails. AgentCore's outbound
signer computes the signature over an *empty* payload while sending the request
body, so every POST is rejected with a signature mismatch before it reaches the
function -- and MCP's streamable HTTP transport is POST-only. The symptom is a
target stuck in FAILED with "Authorization error when sending message" and, on
the Lambda side, Function URL requests that 4xx with zero invocations. No IAM
arrangement fixes it; the same request signed correctly by hand succeeds.
Invoking the function directly sidesteps the broken signer entirely.

Requests are still served by the same `MCPServer` instance the HTTP transport
uses, via `call_tool`, rather than by calling the tool functions directly. That
is what keeps the two entrypoints honest: argument validation, defaulting and
error translation all run exactly as they do over HTTP, so a tool cannot behave
one way locally and another way in the gateway.
"""

import asyncio
import json
import logging

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from server import mcp

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

# The gateway prefixes every tool with its target name, e.g.
# "bfe-energy___search_energy_knowledge". The prefix is the gateway's
# namespacing and means nothing here, so it is stripped back to the name the
# tool actually registered under.
TOOL_NAME_DELIMITER = "___"

# One loop reused across invocations. Lambda freezes the process between
# invocations rather than tearing it down, so `asyncio.run` would build and
# discard a fresh event loop every call, and would also break any client that
# caches a loop-bound resource across a warm invocation.
_loop = asyncio.new_event_loop()


class ToolInvocationError(Exception):
    """A tool call that failed for a reason the caller should see.

    Raised rather than returned. A Lambda that returns an error-shaped dict
    still looks like a success to the gateway, which would report the failure
    to the model as a normal result; raising is what marks the tool call failed.
    """


def _tool_name(context) -> str:
    """Read the tool name out of the Lambda client context."""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    qualified = custom.get("bedrockAgentCoreToolName")
    if not qualified:
        raise ToolInvocationError(
            "no bedrockAgentCoreToolName in the client context -- this function "
            "is invoked by an AgentCore Gateway Lambda target, not directly"
        )

    # Partition, not split: a tool name is free to contain the delimiter itself,
    # and only the first occurrence separates the target prefix from the name.
    _, found, bare = qualified.partition(TOOL_NAME_DELIMITER)
    return bare if found else qualified


def _payload(result):
    """Reduce a CallToolResult to something JSON-serialisable.

    structured_content is preferred: it is the tool's actual return value,
    while `content` is the rendering of it. In practice it is always None here
    -- the SDK only populates it for tools that declare an output schema, and
    it derives one from the return annotation, which a bare `dict` is too
    vague to produce. Annotating the tools `dict[str, Any]` would produce one,
    but only a contentless `{"type": "object", "additionalProperties": true}`,
    so it would buy this nothing and would push a useless schema into the
    gateway catalogue and every MCP client's tool listing.

    So the text blocks are the real path, and text that parses as a JSON
    object or array is unwrapped back into it. Every tool here returns a dict;
    MCP renders that dict as JSON text because its content protocol has no
    other way to carry it, and returning that rendering verbatim would hand
    the gateway a *string* to serialise again -- the caller would then have to
    parse twice to get the dict back, and a caller that parses once gets a
    string where it expected an object.

    Anything that does not parse into a container is left as text, which is
    the correct answer for a tool that genuinely returns prose. The container
    check matters: bare `json.loads` would silently turn a tool returning the
    string "42" into the integer 42.
    """
    if result.structured_content is not None:
        return result.structured_content

    text = "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return parsed if isinstance(parsed, (dict, list)) else text


def handler(event, context):
    tool = _tool_name(context)
    arguments = event if isinstance(event, dict) else {}

    # Logged at info because this is the only record of what the gateway asked
    # for; the argument values are a user's query text, not credentials.
    logger.info("tool=%s arguments=%s", tool, json.dumps(arguments, default=str))

    try:
        result = _loop.run_until_complete(mcp.call_tool(tool, arguments))
    except UnexpectedToolError as exc:
        # The tool crashed. `__cause__` carries the real exception; the SDK
        # replaces the message with a generic one, so log the cause and give
        # the caller something truthful but non-specific.
        #
        # This clause MUST come first: UnexpectedToolError subclasses ToolError,
        # so the other order silently swallows it and loses the traceback.
        logger.exception("tool %s crashed", tool)
        raise ToolInvocationError(f"tool {tool} failed") from exc
    except ToolError as exc:
        # Unknown tool, failed argument validation, or a failure the tool
        # itself chose to report. The message is written for a caller to read,
        # so it is passed through unchanged.
        raise ToolInvocationError(str(exc)) from exc

    if result.is_error:
        raise ToolInvocationError(str(_payload(result)))

    return _payload(result)


# Named to match both conventions: AWS examples and much of the ecosystem
# expect `lambda_handler`, while the Dockerfile's CMD names `handler`.
lambda_handler = handler
