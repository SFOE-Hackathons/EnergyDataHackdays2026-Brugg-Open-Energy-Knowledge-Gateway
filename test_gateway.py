"""End-to-end smoke test against the deployed gateway.

    python3 test_gateway.py

Exercises the whole path in one go -- client -> gateway (open) -> Lambda
invocation (IAM) -> Bedrock Retrieve (IAM) -- once per tool, and exits
non-zero if any leg fails. Run it after `./infra/deploy-lambda.sh` and
`./infra/create-gateway.sh`.

The only credential in the chain is the gateway's own IAM role, on the
outbound leg. The inbound leg has none: GATEWAY_URL is all this needs.

This is a live-infrastructure check, not a unit test: it costs real Bedrock
retrievals and takes on the order of a minute, mostly in get_metric_timeline,
which issues one retrieval per year in the range. pytest deliberately does not
collect it -- the offline tests live in mcp-gateway/tests/.
"""

import sys

from gateway_client import call, call_tool, fetch_access_token

# One call per tool, with the cheapest arguments that still prove the tool
# reached the corpus. The timeline range is two years on purpose: each year is
# a separate sequential retrieval inside a single 60-second Lambda budget.
CASES = [
    (
        "search_energy_knowledge",
        {"query": "Rolle der Wasserkraft in der Schweizer Stromversorgung", "max_results": 3},
    ),
    (
        "get_metric_timeline",
        {"metric": "installierte Photovoltaik-Leistung", "start_year": 2022, "end_year": 2023},
    ),
    (
        "get_chart_data",
        {"topic": "Stromproduktion nach Energieträger", "max_charts": 2},
    ),
]


def summarize(payload: dict) -> str:
    """One line describing a tool result, without dumping the whole thing."""
    # get_metric_timeline's list is called `data`, not `timeline` -- the name
    # this once guessed at, which made a passing run report "keys: ..." as if
    # it had not understood the result.
    for key in ("results", "data", "charts"):
        if isinstance(payload.get(key), list):
            sources = len(payload.get("sources", {}) or {})
            return f"{len(payload[key])} {key} from {sources} source(s)"
    return f"keys: {', '.join(sorted(payload))}"


def main() -> None:
    failures = []

    token = fetch_access_token()
    print("==> inbound auth: " + ("Cognito bearer token" if token else "none (open gateway)"))

    print("==> tools/list")
    try:
        tools = [t["name"] for t in call("tools/list", {}, token, timeout=60).get("tools", [])]
        print(f"    {len(tools)} tool(s): {', '.join(tools)}")
    except Exception as exc:
        print(f"    FAILED: {exc}")
        failures.append("tools/list")
        tools = []

    for name, arguments in CASES:
        print(f"==> {name}")
        try:
            payload = call_tool(name, arguments, token)
            print(f"    ok -- {summarize(payload)}")
        except Exception as exc:
            print(f"    FAILED: {exc}")
            failures.append(name)

    if failures:
        print(f"\n{len(failures)} failure(s): {', '.join(failures)}")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
