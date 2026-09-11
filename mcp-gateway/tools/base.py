"""Base class for MCP tools exposed by this server."""

from abc import ABC, abstractmethod

from mcp.server.mcpserver.exceptions import ToolError

from knowledge_base import KnowledgeBaseClient, KnowledgeBaseError

# Shared by every tool's `source_fields` parameter, so the three descriptions
# cannot drift apart.
SOURCE_FIELDS_DESCRIPTION = (
    "Which source-attribution fields to return. \"core\" (default) returns "
    "title, published_at, download_url and media_type, omitting fields that "
    "are constant across this corpus or describe our ingestion rather than "
    "the document. \"all\" adds file_type, language, created_at and "
    "last_updated_at; ask for it only when you specifically need them, as "
    "they carry no information for most documents. download_url is always "
    "returned at either setting, including when it is null."
)

# Every tool returns attribution the same way, so a caller learns the shape
# once. Repeated in each tool's description because a model reads the tool it
# is calling, not the others.
SOURCES_TABLE_DESCRIPTION = (
    "Source attribution is NOT inlined on each entry. Every response carries "
    "a top-level \"sources\" object mapping a short id to one document's "
    "attribution, and each entry carries \"source_id\" naming its document. "
    "Resolve an entry's source by looking its source_id up in that object. "
    "Results are drawn from far fewer documents than there are entries, so "
    "this avoids repeating the same title and URL dozens of times."
)


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
