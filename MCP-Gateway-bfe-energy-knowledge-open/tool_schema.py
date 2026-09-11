"""Translate this server's MCP tool schemas into AgentCore tool definitions.

An AgentCore Gateway Lambda target does not talk MCP to the function. It holds
a *static* copy of the tool catalogue in its target configuration and invokes
the function directly, so the catalogue has to be handed over at deploy time.
Writing it out by hand would guarantee drift the first time a parameter
changed, so it is generated from the same `MCPServer` the tools register
themselves on -- `python tool_schema.py` prints the payload that
`infra/create-gateway.sh` passes to CreateGatewayTarget.

The translation is lossy, and deliberately so. AgentCore's SchemaDefinition
accepts only `type`, `description`, `properties`, `required` and `items`. The
schemas pydantic derives from the tools' signatures also carry `title`,
`default`, `enum`, `minimum` and `maximum`, and sending those through is a
validation error, not a field the service politely ignores.

Dropping them silently would be worse than the error. `source_fields` would
arrive at the model as a free-text string with no hint that "core" and "all"
are the only accepted values, and the first thing a model does with an
unconstrained string is invent one. So every constraint that cannot be
expressed structurally is appended to the property's description instead: the
model still learns the rule, it just reads it as prose. Enforcement is
unaffected either way -- `MCPServer.call_tool` validates against the real
pydantic model at invocation time, so an invalid value is rejected in the
Lambda regardless of what the gateway's copy of the schema says.
"""

import json
from typing import Any

def _constraint_sentences(schema: dict[str, Any]) -> list[str]:
    """Render the constraints AgentCore cannot express as English."""
    sentences = []

    if "enum" in schema:
        allowed = ", ".join(json.dumps(v) for v in schema["enum"])
        sentences.append(f"Allowed values: {allowed}.")

    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None and maximum is not None:
        sentences.append(f"Must be between {minimum} and {maximum} inclusive.")
    elif minimum is not None:
        sentences.append(f"Must be at least {minimum}.")
    elif maximum is not None:
        sentences.append(f"Must be at most {maximum}.")

    # Stated rather than omitted: with `default` gone from the schema, a caller
    # has no other way to find out what happens when the argument is left out.
    if "default" in schema:
        sentences.append(f"Defaults to {json.dumps(schema['default'])} if omitted.")

    return sentences


def to_schema_definition(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert one JSON Schema node into an AgentCore SchemaDefinition."""
    converted: dict[str, Any] = {}

    # `type` is required by AgentCore but is absent from schemas pydantic
    # generates for untyped or union-typed values. Object is the only choice
    # that keeps such a schema loadable rather than rejected outright.
    converted["type"] = schema.get("type", "object")

    description = schema.get("description", "")
    extra = _constraint_sentences(schema)
    if extra:
        description = " ".join(filter(None, [description.rstrip(), *extra]))
    if description:
        converted["description"] = description

    if "properties" in schema:
        converted["properties"] = {
            name: to_schema_definition(prop)
            for name, prop in schema["properties"].items()
        }
    if schema.get("required"):
        converted["required"] = list(schema["required"])
    if "items" in schema:
        converted["items"] = to_schema_definition(schema["items"])

    return converted


def tool_definitions(tools) -> list[dict[str, Any]]:
    """Build the inlinePayload for an AgentCore Lambda target.

    `tools` is what `MCPServer.list_tools()` returns.

    outputSchema is left off on purpose. It is optional, and these tools return
    a nested structure whose shape varies with `detail` and `source_fields`;
    a schema flattened enough for AgentCore to accept would describe the
    response less accurately than the description already does.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": to_schema_definition(tool.input_schema),
        }
        for tool in tools
    ]


def _main() -> None:
    import asyncio
    import inspect

    from server import mcp

    listed = mcp.list_tools()
    tools = asyncio.run(listed) if inspect.isawaitable(listed) else listed
    print(json.dumps(tool_definitions(tools), indent=2))


if __name__ == "__main__":
    _main()
