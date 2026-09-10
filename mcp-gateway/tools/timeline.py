"""get_metric_timeline tool."""

from typing import Annotated

from pydantic import Field

from mcp.server.mcpserver.exceptions import ToolError

from tools.base import BaseTool


class MetricTimelineTool(BaseTool):
    """Year-by-year passage retrieval for charting a metric's development."""

    MAX_TIMELINE_YEARS = 15

    name = "get_metric_timeline"
    title = "Get Energy Metric Timeline"
    description = (
        "Query Swiss federal energy publications once per year across a "
        "year range for a given metric (e.g. \"solar PV installed "
        "capacity\") and return a flat list of source-attributed passages, "
        "each tagged with the year it was queried for. queried_year records "
        "only what was asked, NOT what the passage contains - a passage "
        "retrieved for 2021 routinely holds a series spanning 2002-2022 - so "
        "never group, chart or attribute figures by it. Use years_covered, "
        "which is extracted from the passage's own text, and treat "
        "queried_year as a retrieval trace only. Multiple entries can "
        "share the same queried_year when different source documents cover "
        "it; entries are returned as-is with no numeric value extracted and "
        "no deduplication or reconciliation performed across sources, so "
        "different passages for the same year may report differing "
        "figures. The caller is expected to read the actual figures out of "
        "each passage's text and, if useful, chart or summarize them. Every "
        "entry carries full source attribution, including the document's "
        "publication date (published_at) and a public download_url pointing "
        "directly at the original PDF, which anyone can open without "
        "credentials; download_url is null for the few documents that could "
        "not be matched to a public record. Page numbers are not available "
        "in this corpus, so results cannot be cited by page. Entries also "
        "carry bases (what the numbers measure: sales, installed_annual, "
        "installed_cumulative or production, each with its evidence; empty "
        "when the text did not disambiguate), is_projection for "
        "forward-looking figures, and is_truncated for passages the upstream "
        "chunker cut mid-content."
    )

    def run(
        self,
        metric: Annotated[
            str,
            Field(description="The metric to track, e.g. \"solar PV installed capacity\"."),
        ],
        start_year: Annotated[
            int,
            Field(description="First year of the range to query (inclusive)."),
        ],
        end_year: Annotated[
            int,
            Field(description="Last year of the range to query (inclusive)."),
        ],
        max_results_per_year: Annotated[
            int,
            Field(description="Maximum number of ranked passages to return for each year.", ge=1, le=25),
        ] = 3,
    ) -> dict:
        if start_year > end_year:
            raise ToolError(
                f"start_year ({start_year}) must be less than or equal to "
                f"end_year ({end_year})."
            )
        if end_year - start_year + 1 > self.MAX_TIMELINE_YEARS:
            raise ToolError(
                f"Year range {start_year}-{end_year} spans "
                f"{end_year - start_year + 1} years; at most "
                f"{self.MAX_TIMELINE_YEARS} years can be queried per call."
            )

        data = []
        for year in range(start_year, end_year + 1):
            for item in self.search(f"{metric} {year}", max_results_per_year):
                item["queried_year"] = year
                data.append(item)

        return {
            "metric": metric,
            "start_year": start_year,
            "end_year": end_year,
            "result_count": len(data),
            "data": data,
        }
