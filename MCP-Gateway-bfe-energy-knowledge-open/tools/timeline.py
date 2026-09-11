"""get_metric_timeline tool."""

from typing import Annotated, Literal

from pydantic import Field

from mcp.server.mcpserver.exceptions import ToolError

from passages import CORE, INDEX, dedupe, intern_sources, project
from tools.base import BaseTool, SOURCES_TABLE_DESCRIPTION, SOURCE_FIELDS_DESCRIPTION


class MetricTimelineTool(BaseTool):
    """Year-by-year passage retrieval for charting a metric's development."""

    # One upstream retrieval per year, so this bounds both the response size
    # and the fan-out a single call causes against the knowledge base. Five
    # years at the default 3 results is a request a caller can afford to
    # repeat; a wide range mostly returns the same passages again, because
    # adjacent years retrieve heavily overlapping text and a passage
    # routinely covers a decade of its own.
    #
    # Stated in the description as well as enforced here: a JSON Schema
    # cannot express "end_year - start_year < 5", so without saying it in
    # prose the only way a model discovers the cap is by tripping it.
    MAX_TIMELINE_YEARS = 5

    name = "get_metric_timeline"
    title = "Get Energy Metric Timeline"
    description = (
        "Query Swiss federal energy publications once per year across a "
        "year range for a given metric (e.g. \"solar PV installed "
        "capacity\") and return a flat list of source-attributed passages, "
        "each tagged with the years it was queried for. "
        "The range is limited to 5 years per call (start_year and end_year "
        "inclusive); to cover a longer span, issue several calls. "
        "queried_years records "
        "only what was asked, NOT what the passage contains - a passage "
        "retrieved for 2021 routinely holds a series spanning 2002-2022 - so "
        "never group, chart or attribute figures by it. Use years_covered, "
        "which is extracted from the passage's own text, and treat "
        "queried_years as a retrieval trace only. It holds more than one year "
        "when the same passage was the best match for several of them, which "
        "is common and is not a sign of an error. "
        "IMPORTANT - by default the verbatim passage text is NOT included: "
        "detail defaults to \"index\", which returns every annotation "
        "(years_covered, bases, is_projection, is_truncated) and full source "
        "attribution but omits the text, for roughly an eighth of the "
        "response size. Read the index first to decide which sources and "
        "years are worth reading, then call again with detail=\"full\" and a "
        "narrower year range to get the text for just those. Ask for "
        "detail=\"full\" over a wide range only when the text is genuinely "
        "needed for all of it. "
        "Passages with identical text are collapsed into a single entry, "
        "keeping the highest-scoring source, because the corpus stores some "
        "publications under more than one document name. No numeric value is "
        "extracted and no reconciliation is performed across different "
        "sources, so two distinct passages covering the same year may still "
        "report differing figures. At detail=\"full\" the caller is expected "
        "to read the actual figures out of each passage's text and, if "
        "useful, chart or summarize them. "
        + SOURCES_TABLE_DESCRIPTION + " "
        "Every entry resolves to full source attribution, including the "
        "document's "
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
        detail: Annotated[
            Literal["index", "full"],
            Field(
                description=(
                    "\"index\" (default) omits each passage's verbatim text "
                    "and returns only its annotations and source, which is "
                    "far smaller; \"full\" includes the text."
                )
            ),
        ] = INDEX,
        source_fields: Annotated[
            Literal["core", "all"],
            Field(description=SOURCE_FIELDS_DESCRIPTION),
        ] = CORE,
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

        # Each year is a separate retrieval, so the client's own per-retrieval
        # deduplication cannot see across them -- and adjacent years return
        # heavily overlapping passages, which is where most of the redundancy
        # in a timeline lives.
        retrieved = len(data)
        data = project(dedupe(data), detail, source_fields)
        data, sources = intern_sources(data)

        return {
            "metric": metric,
            "start_year": start_year,
            "end_year": end_year,
            "detail": detail,
            "source_fields": source_fields,
            "result_count": len(data),
            "duplicates_collapsed": retrieved - len(data),
            "sources": sources,
            "data": data,
        }
