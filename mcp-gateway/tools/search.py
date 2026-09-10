"""search_energy_knowledge tool."""

from typing import Annotated

from pydantic import Field

from tools.base import BaseTool


class SearchEnergyKnowledgeTool(BaseTool):
    """Ad-hoc semantic search over Swiss federal energy publications."""

    name = "search_energy_knowledge"
    title = "Search Swiss Federal Energy Knowledge Base"
    description = (
        "Search Swiss federal energy publications - official reports and "
        "statistics from the Swiss Federal Office of Energy (SFOE), largely "
        "in German - and return the most relevant passages, ranked by "
        "semantic relevance to the query. Each result is a verbatim text "
        "passage together with full source attribution: document title, the "
        "date the document was published (published_at), and a public "
        "download_url pointing directly at the original PDF on the SFOE "
        "publication database, which anyone can open without credentials. "
        "download_url is null for the few documents that could not be "
        "matched to a public record; cite those by title and published_at "
        "alone rather than constructing a URL. Page numbers are not "
        "available in this corpus, so results cannot be cited by page."
    )

    def run(
        self,
        query: Annotated[
            str,
            Field(description="Natural-language search query, e.g. \"solar PV installed capacity 2023\"."),
        ],
        max_results: Annotated[
            int,
            Field(description="Maximum number of ranked passages to return.", ge=1, le=25),
        ] = 5,
    ) -> dict:
        results = self.search(query, max_results)
        return {"result_count": len(results), "results": results}
