"""List the tools the AgentCore Gateway currently exports.

    python3 list_tools.py          # names, titles and required arguments
    python3 list_tools.py --raw    # the full tools/list result

This is the check to run after `./infra/create-gateway.sh`: the gateway serves a
static copy of the server's tool catalogue, generated at deploy time, so a tool
added to mcp-gateway/tools/ is not visible here until that script has run --
deploying the Lambda alone is not enough. An empty list, or one missing a tool
you just deployed, means the second half of the deploy did not happen.
"""

import json
import sys

from gateway_client import call, fetch_access_token


def main() -> None:
    token = fetch_access_token()
    result = call("tools/list", {}, token, timeout=60)

    if "--raw" in sys.argv:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    tools = result.get("tools", [])
    if not tools:
        print("the gateway exports no tools -- run ./infra/create-gateway.sh")
        return

    for tool in tools:
        schema = tool.get("inputSchema", {})
        required = schema.get("required", [])
        optional = [k for k in schema.get("properties", {}) if k not in required]
        print(f"\n{tool['name']}")
        if tool.get("title"):
            print(f"  {tool['title']}")
        print(f"  required: {', '.join(required) or '(none)'}")
        print(f"  optional: {', '.join(optional) or '(none)'}")

    print(f"\n{len(tools)} tool(s)")


if __name__ == "__main__":
    main()
