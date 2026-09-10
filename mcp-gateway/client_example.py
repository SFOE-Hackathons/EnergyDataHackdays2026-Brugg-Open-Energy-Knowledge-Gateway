import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(url: str, query: str, max_results: int) -> None:
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Available tools:", [tool.name for tool in tools.tools])

            result = await session.call_tool(
                "search_energy_knowledge",
                {"query": query, "max_results": max_results},
            )
            for block in result.content:
                if block.type == "text":
                    print(json.dumps(json.loads(block.text), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Example client for the local energy knowledge gateway.")
    parser.add_argument("query", help="Question to search for")
    parser.add_argument("--url", default="http://localhost:8000/mcp", help="Gateway MCP endpoint")
    parser.add_argument("--max-results", type=int, default=5)
    args = parser.parse_args()

    asyncio.run(run(args.url, args.query, args.max_results))


if __name__ == "__main__":
    main()
