"""Call one gateway tool from the command line and print what comes back.

    python3 query_gateway.py "Rolle der Wasserkraft in der Stromversorgung"
    python3 query_gateway.py --max-results 10 "Photovoltaik Zubau 2023"
    python3 query_gateway.py --tool get_metric_timeline \
        --arg metric="installierte PV-Leistung" \
        --arg start_year=2020 --arg end_year=2024

The default tool is search_energy_knowledge, whose only required argument is
the query, which is why it can be given positionally. Anything else needs
--tool and explicit --arg pairs; run `python3 list_tools.py` to see what each
tool takes.
"""

import argparse
import json

from gateway_client import GatewayError, call_tool, fetch_access_token


def parse_arg(pair: str) -> tuple[str, object]:
    """Split a `name=value` pair, giving the value its JSON type where it has
    one. Tool schemas are typed, so passing start_year as the string "2020"
    is rejected by the server -- but quoting it on the command line is the
    natural thing to do, so ints, floats, booleans and null are recognized
    and everything else stays a string."""
    if "=" not in pair:
        raise argparse.ArgumentTypeError(f"expected name=value, got {pair!r}")
    name, _, raw = pair.partition("=")
    try:
        return name, json.loads(raw)
    except json.JSONDecodeError:
        return name, raw


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Call one tool on the BFE energy knowledge MCP gateway."
    )
    parser.add_argument("query", nargs="?", help="Query for search_energy_knowledge.")
    parser.add_argument(
        "--tool",
        default="search_energy_knowledge",
        help="Unqualified tool name; the gateway target prefix is added for you.",
    )
    parser.add_argument(
        "--arg",
        action="append",
        type=parse_arg,
        default=[],
        metavar="NAME=VALUE",
        help="An argument for the tool. Repeatable.",
    )
    parser.add_argument("--max-results", type=int, help="Shorthand for --arg max_results=N.")
    args = parser.parse_args()

    arguments = dict(args.arg)
    if args.query is not None:
        arguments["query"] = args.query
    if args.max_results is not None:
        arguments["max_results"] = args.max_results

    if not arguments:
        parser.error("give a query, or arguments with --arg NAME=VALUE")

    token = fetch_access_token()
    try:
        result = call_tool(args.tool, arguments, token)
    except GatewayError as exc:
        raise SystemExit(f"{args.tool} failed:\n{exc}") from exc

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
