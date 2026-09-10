"""Base class for MCP tools exposed by this server."""

from abc import ABC, abstractmethod

from mcp.server.mcpserver.exceptions import ToolError

from knowledge_base import KnowledgeBaseClient, KnowledgeBaseError


class BaseTool(ABC):
    """Common contract for a self-describing MCP tool.

    Subclasses declare `name`, `title`, and `description` as class
    attributes and implement `run`; `register` wires the tool into an
    MCPServer instance, sourcing its documentation straight from those
    attributes so `tools/list` stays in sync with the implementation.

    Subclasses query the knowledge base through `search`, which translates
    failures into `ToolError`. That matters: the MCP SDK only preserves the
    message of a `ToolError` when reporting back to the caller, and replaces
    the message of any other exception with a generic one.
    """

    name: str
    title: str
    description: str

    def __init__(self, knowledge_base: KnowledgeBaseClient):
        self._knowledge_base = knowledge_base

    def search(self, query: str, max_results: int) -> list[dict]:
        """Query the knowledge base, surfacing failures to the caller."""
        try:
            return self._knowledge_base.search(query, max_results)
        except KnowledgeBaseError as exc:
            raise ToolError(str(exc)) from exc

    @abstractmethod
    def run(self, *args, **kwargs):
        """Execute the tool. Implemented by subclasses."""
        raise NotImplementedError

    def register(self, mcp) -> None:
        mcp.add_tool(
            self.run,
            name=self.name,
            title=self.title,
            description=self.description,
        )
