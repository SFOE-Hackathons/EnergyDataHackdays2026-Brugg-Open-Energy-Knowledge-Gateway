"""MCP tools exposed by this server."""

from knowledge_base import KnowledgeBaseClient
from tools.base import BaseTool
from tools.chart_data import ChartDataTool
from tools.search import SearchEnergyKnowledgeTool
from tools.timeline import MetricTimelineTool


def build_tools(client: KnowledgeBaseClient) -> list[BaseTool]:
    return [
        SearchEnergyKnowledgeTool(client),
        MetricTimelineTool(client),
        ChartDataTool(client),
    ]
