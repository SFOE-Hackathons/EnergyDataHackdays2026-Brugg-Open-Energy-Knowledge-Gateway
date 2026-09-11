"""Entrypoint for the Swiss federal energy knowledge MCP server."""

from mcp.server.mcpserver import MCPServer

from config import Config
from knowledge_base import KnowledgeBaseClient
from tools import build_tools

config = Config.from_env()
knowledge_base = KnowledgeBaseClient(config)

mcp = MCPServer(
    name="open-energy-knowledge-gateway",
    instructions=(
        "Search and explore official Swiss federal energy publications "
        "(Swiss Federal Office of Energy, SFOE), largely German-language "
        "reports and statistics. Offers search_energy_knowledge for ad-hoc "
        "semantic search, and get_metric_timeline for pulling a "
        "source-attributed passage per year across a year range (e.g. to "
        "chart a metric's development over time). Results are ranked "
        "passages with full source attribution; page numbers are not "
        "available in this corpus."
    ),
)

for tool in build_tools(knowledge_base):
    tool.register(mcp)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=config.host,
        port=config.port,
        json_response=True,
        stateless_http=True,
    )
