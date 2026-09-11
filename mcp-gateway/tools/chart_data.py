"""get_chart_data tool."""

from typing import Annotated, Literal

from pydantic import Field

from mcp.server.mcpserver.exceptions import ToolError

from chart_parsing import extract_data_blocks, extract_title, parse_data_block
from passages import CORE, dedupe, intern_sources, project_source
from semantics import (
    classify_extraction_method,
    detect_projection,
    infer_bases,
    years_from_rows,
)
from tools.base import BaseTool, SOURCES_TABLE_DESCRIPTION, SOURCE_FIELDS_DESCRIPTION


class ChartDataTool(BaseTool):
    """Extracts machine-readable numeric series from chart/table transcriptions.

    A subset of chunks in the knowledge base are transcriptions of a chart or
    table image and carry a `<data>` block with the figure's underlying
    values. Coverage of any given topic is opportunistic - most search
    results have no such block - so this tool issues several query variants
    per call and pools the results to maximize the chance of finding one.
    """

    name = "get_chart_data"
    title = "Get Chart Data"
    description = (
        "Return numeric series extracted from charts and tables in official "
        "Swiss federal energy publications, ready to plot, each with full "
        "source attribution. Every chart carries a precision flag: "
        "\"exact\" means the values were printed on the source figure as "
        "data labels; \"estimated\" means they were read off the figure "
        "(e.g. a bar's height) and are either given as a min/max range or "
        "declared as estimates in the chart's note field. is_estimate is the "
        "boolean form of that, and extraction_method says how the numbers "
        "were obtained - \"table\" (transcribed from a printed table), "
        "\"chart_label\" (numbers drawn on the figure) or "
        "\"chart_visual_read\" (estimated from the geometry); it is null when "
        "the transcription does not say. Callers MUST NOT "
        "present estimated values as precise figures, must not invent the "
        "midpoint of a range, and should surface the note when reporting an "
        "estimated series. is_projection flags a chart that also carries "
        "forward-looking figures (a forecast for a year that had not "
        "finished), which must not be reported as measured. column_bases "
        "says what each column measures - sales, installed_annual, "
        "installed_cumulative or production - because the same magnitude "
        "means different things across these, and a column with no entry is "
        "one the source did not disambiguate. years_covered gives the span of "
        "years the chart's own rows contain. is_truncated marks a chart whose "
        "transcription was cut off by the upstream chunker: its earlier rows "
        "are sound but the last one may be incomplete. A chart's raw_block "
        "holds the untouched transcription as a fallback if the structured "
        "rows look wrong. Coverage is opportunistic: only some "
        "figures were transcribed with usable data, so a topic may "
        "legitimately return zero charts - falling back to "
        "search_energy_knowledge is the right move when that happens. "
        + SOURCES_TABLE_DESCRIPTION + " "
        "Every chart resolves to full source attribution, including the "
        "document's "
        "publication date (published_at) and a public download_url pointing "
        "directly at the original PDF, which anyone can open without "
        "credentials; download_url is null for the few documents that could "
        "not be matched to a public record. Page numbers are not available "
        "in this corpus, so results cannot be cited by page."
    )

    # Query variants to probe for chart/table figures related to a topic.
    #
    # The publications are German, but the `<data>` blocks are written by the
    # vision transcriber in English, with headers like "Year,Installed PV
    # Capacity (MW)". Probing only in German therefore retrieves the prose
    # around a figure while missing the chunk holding its numbers - measured
    # over seven topics, an all-German probe set found 3 charts where these
    # find 30. The English and unit/year-shaped probes are what reach the
    # transcriptions; the German ones still matter for topical relevance.
    #
    # Kept as a class attribute so the probe set is easy to re-tune; changes
    # here should be measured against a spread of topics, not assumed.
    QUERY_PROBE_TEMPLATES = (
        "{topic}",
        "{topic} Abbildung Grafik Entwicklung",
        "Year {topic} data values by year",
        "{topic} chart extracted data year capacity production",
        "Year {topic} MW GWh TJ 2010 2015 2020 2024",
    )

    # How many results to keep from each probe before pooling.
    #
    # This was 5 because the previous gateway pinned every search to 5 results
    # and asking for more was pointless. The current gateway returns up to ~40
    # (infra/create-gateway.sh), so a low value here now silently discards most
    # of what was already fetched and paid for - and chart chunks are exactly
    # the kind of result that ranks below the prose it illustrates, so they sat
    # in the part being thrown away.
    #
    # Raising this costs no extra requests; the probes are unchanged and the
    # work is local parsing over a larger pool.
    RESULTS_PER_PROBE = 25

    def run(
        self,
        topic: Annotated[
            str,
            Field(description="The subject to find chart or table data for, e.g. \"solar PV installed capacity\"."),
        ],
        max_charts: Annotated[
            int,
            Field(description="Maximum number of parsed charts to return.", ge=1, le=15),
        ] = 5,
        source_fields: Annotated[
            Literal["core", "all"],
            Field(description=SOURCE_FIELDS_DESCRIPTION),
        ] = CORE,
    ) -> dict:
        if not topic.strip():
            raise ToolError("topic must not be empty.")

        pooled = self._pool_results(topic)
        charts = self._extract_charts(pooled)
        charts.sort(key=lambda chart: chart["score"] or 0, reverse=True)
        charts = charts[:max_charts]

        # Trimmed after the cut to max_charts, so the work is done only for
        # the charts actually being returned.
        for chart in charts:
            if chart.get("source"):
                chart["source"] = project_source(chart["source"], source_fields)
        charts, sources = intern_sources(charts)

        return {
            "topic": topic,
            "probes_run": len(self.QUERY_PROBE_TEMPLATES),
            "source_fields": source_fields,
            "chart_count": len(charts),
            "sources": sources,
            "charts": charts,
        }

    def _pool_results(self, topic: str) -> list[dict]:
        """Run every query probe and deduplicate the pooled results.

        The probes overlap heavily by design, so the same chunk comes back
        from several of them; `dedupe` keeps the highest-scoring copy.

        This used to key on (document title, text prefix). Including the title
        defeated the purpose: the corpus stores the same publication under more
        than one document name, so two copies of one chart got different keys
        and both survived, each then parsed into a separate chart. Keying on
        the text alone is what actually collapses them.
        """
        pooled = []
        for template in self.QUERY_PROBE_TEMPLATES:
            pooled.extend(self.search(template.format(topic=topic), self.RESULTS_PER_PROBE))
        return dedupe(pooled)

    def _extract_charts(self, results: list[dict]) -> list[dict]:
        charts = []
        for result in results:
            text = result.get("text", "")
            blocks = extract_data_blocks(text)
            if not blocks:
                continue

            title = extract_title(text)
            for block in blocks:
                content = block["content"]
                parsed = parse_data_block(content)
                if parsed is None:
                    continue

                # Basis is inferred per column, not per chart: a single figure
                # routinely plots capacity sold against capacity installed, and
                # one chart-wide label would mis-describe at least one series.
                #
                # The first column holds the row labels (the years) and is not
                # a measurement, so it gets no basis: falling back to the
                # passage would otherwise label the Year column "production".
                #
                # The surrounding passage is consulted only when there is a
                # single data column, because it cannot tell two columns apart:
                # on a chart plotting annual additions against cumulative
                # stock, passage context labelled the annual column
                # "installed_cumulative" - precisely the confusion this field
                # exists to prevent. With several columns, only the header
                # speaks for its own series.
                columns = parsed["columns"]
                fallback = infer_bases(text) if len(columns) == 2 else []
                column_bases = {
                    column: (infer_bases(column) or fallback) if index else []
                    for index, column in enumerate(columns)
                }

                charts.append(
                    {
                        "title": title,
                        "columns": columns,
                        "column_bases": column_bases,
                        "years_covered": years_from_rows(columns, parsed["rows"]),
                        "precision": parsed["precision"],
                        "is_estimate": parsed["precision"] != "exact",
                        "extraction_method": classify_extraction_method(
                            f"{text}\n{content}", parsed["precision"]
                        ),
                        "is_projection": detect_projection(f"{text}\n{content}"),
                        "is_truncated": block["is_truncated"],
                        "note": parsed["note"],
                        "row_count": parsed["row_count"],
                        "rows": parsed["rows"],
                        "raw_block": content,
                        "score": result.get("score"),
                        "source": result.get("source"),
                    }
                )
        return charts
