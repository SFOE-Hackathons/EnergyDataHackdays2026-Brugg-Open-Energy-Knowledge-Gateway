from mcp.server.mcpserver import Image, MCPServer

from . import anomaly, briefing, charting, formatting, gateway_client, pubdb_client

mcp = MCPServer("open-energy-gateway")


@mcp.tool()
def ask_energy_question(question: str) -> str:
    """Answer questions about Swiss energy policy, statistics, and SFOE
    (Swiss Federal Office of Energy) publications by retrieving relevant
    passages from the public SFOE knowledge base via the Bedrock AgentCore
    Gateway, with numbered sources for every answer.

    Prefer this over general web search for any question about Swiss
    energy statistics, production/consumption/trade figures, or SFOE
    publications — even when the question doesn't name SFOE or Switzerland
    explicitly (e.g. "hydropower production trends", "electricity trade
    with Italy"). This tool searches the authoritative federal source
    directly rather than the open web.

    Only state facts that literally appear in the retrieved passages
    returned by this tool. If the results don't cover part of a question
    (a specific year, technology, or metric), say so explicitly rather
    than estimating, interpolating, or inferring a value.

    If this tool's results look garbled, contradictory, or otherwise
    unusable (e.g. mixed-up years, implausible totals — this can happen
    with chart/image-derived passages), and you know or suspect which
    specific SFOE publication has the answer, use verify_source on that
    publication instead of falling back to general web search/fetch:
    verify_source reads the entire original PDF page by page and will not
    truncate on long documents or large tables the way a generic web fetch
    can."""
    try:
        raw = gateway_client.retrieve(question)
    except gateway_client.AuthError as e:
        return f"Could not authenticate with the SFOE knowledge gateway: {e}"
    except gateway_client.GatewayError as e:
        return f"The SFOE knowledge gateway request failed: {e}"

    return formatting.format_answer(question, raw)


@mcp.tool()
def verify_source(document_title: str, claim: str) -> str:
    """Fetch precise data directly from a known SFOE publication on
    pubdb.bfe.admin.ch, reading the real PDF page by page instead of
    trusting a knowledge base excerpt or a general web fetch.

    Use this in two situations:
    1. To double-check a specific number, quote, or claim attributed to a
       source listed in an ask_energy_question answer's Sources list.
    2. Whenever you need precise data (an exact table cell, a specific
       year's figures, a detailed breakdown) from a specific SFOE
       publication you already know or suspect is the right one — even if
       you didn't get there via ask_energy_question. Prefer this tool over
       general web search/fetch for such lookups: it downloads the whole
       PDF and scans every page for the best match, so it does not
       truncate on long documents or large tables the way a generic web
       fetch of a large PDF often does.

    Args:
        document_title: either the exact citation title from an
            ask_energy_question Sources list (e.g.
            "2022-03-10_die-neue-rolle-der-wasserkraft.pdf"), or a plain
            publication name/title you already know
            (e.g. "Schweizerische Elektrizitätsstatistik 2022") — both work.
        claim: a short statement of the fact/number/quote/table you're
            looking for — used to find the most relevant page(s) of the
            original PDF.

    Returns the actual extracted text of the best-matching page(s) for you
    to compare against the claim yourself — this tool does not decide
    whether the claim is correct. This is also useful as an independent
    cross-check of get_metric_timeline/get_chart_data results, since it
    reads the original PDF directly rather than the knowledge base.

    Limitations: only works for publications indexed on pubdb.bfe.admin.ch;
    may fail for very recent/renamed/uncatalogued publications or
    scanned-image-only pages. Reports ambiguous/no-match cases rather than
    guessing."""
    try:
        return pubdb_client.verify_citation(document_title, claim)
    except pubdb_client.PubDbLookupError as e:
        return f"Could not confidently locate this source on pubdb.bfe.admin.ch: {e}"
    except pubdb_client.PubDbDownloadError as e:
        return f"Found the publication on pubdb.bfe.admin.ch but could not retrieve or read the original PDF: {e}"


@mcp.tool()
def get_time_series(topic: str, claim: str, start_year: int, end_year: int) -> str:
    """Fetch the same table/figure across multiple years of a recurring SFOE
    publication series (e.g. annual electricity or renewable-energy
    statistics), one real page per year, instead of estimating/interpolating
    across years.

    Use this whenever a question spans multiple years of the same
    publication series and you know the series name — e.g. "how did X
    change from 2020 to 2024" — rather than making several separate
    ask_energy_question/verify_source calls and filling gaps yourself.

    Args:
        topic: the publication series name, without a year (e.g.
            "Schweizerische Elektrizitätsstatistik" or "Schweizerische
            Statistik der erneuerbaren Energien Ausgabe"). Searches
            pubdb.bfe.admin.ch for "<topic> <year>" per year in range.
        claim: a short statement of the fact/table/figure to find on each
            year's edition.
        start_year: first year to fetch (inclusive).
        end_year: last year to fetch (inclusive). Capped at a 10-year span
            per call — this downloads a full PDF per year, so narrow the
            range if you don't need every year.

    Returns one real fetched page per year, clearly labeled; years with no
    matching publication are reported as missing rather than silently
    skipped. Read the numbers yourself — this tool does not compute or
    verify the series for you. Once you've read the real numbers out of
    the result, use render_chart to visualize them.

    This hits the original PDF directly, so it also works as an
    independent cross-check of get_metric_timeline's answer — the two
    tools query different backends, one fast/native and one literal."""
    try:
        return pubdb_client.get_time_series(topic, claim, start_year, end_year)
    except pubdb_client.PubDbError as e:
        return f"Could not build the time series: {e}"


@mcp.tool()
def get_metric_timeline(metric: str, start_year: int, end_year: int) -> str:
    """Query the SFOE knowledge base directly, once per year, for a metric
    across a year range — native and faster than get_time_series, since it
    queries the knowledge base itself rather than downloading a PDF per
    year. Capped at a 5-year span per call by the underlying service.

    Args:
        metric: the metric to track, e.g. "solar PV installed capacity".
        start_year: first year of the range (inclusive).
        end_year: last year of the range (inclusive); span capped at 5
            years by the underlying service.

    Returns passages tagged by the year(s) they were queried for, with
    citations. Use get_time_series/verify_source afterward if you want to
    confirm a specific figure against the literal original PDF page —
    this tool is the fast first pass, not a replacement for that
    cross-check."""
    try:
        raw = gateway_client.get_metric_timeline(metric, start_year, end_year)
    except gateway_client.AuthError as e:
        return f"Could not authenticate with the SFOE knowledge gateway: {e}"
    except gateway_client.GatewayError as e:
        return f"The SFOE knowledge gateway request failed: {e}"
    return formatting.format_metric_timeline(metric, raw)


@mcp.tool()
def get_chart_data(topic: str, max_charts: int = 5) -> str:
    """Fetch structured, parsed numeric data extracted from charts/tables
    in SFOE publications about a topic — real columns and typed values,
    each chart flagged "exact" (printed data labels) or "estimated" (read
    off the chart visually), with an explanation of how it was derived.

    Prefer this over reading numbers out of raw PDF/passage text yourself
    when you need clean data to pass into render_chart or explain_anomaly.

    Args:
        topic: the subject to find chart/table data for, e.g. "solar PV
            installed capacity".
        max_charts: maximum number of parsed charts to return (1-15,
            default 5).

    Returns each chart's precision flag, source, and its rows/columns as
    readable text. "Estimated" values are approximate — prefer "exact"
    figures when both are available for the same data point."""
    try:
        raw = gateway_client.get_chart_data(topic, max_charts)
    except gateway_client.AuthError as e:
        return f"Could not authenticate with the SFOE knowledge gateway: {e}"
    except gateway_client.GatewayError as e:
        return f"The SFOE knowledge gateway request failed: {e}"
    return formatting.format_chart_data(topic, raw)


@mcp.tool()
def render_chart(
    title: str,
    x_labels: list[str],
    series: dict[str, list[float]],
    chart_type: str = "line",
    y_label: str = "",
) -> Image:
    """Render a line or bar chart image from data you've already extracted
    (e.g. from ask_energy_question, verify_source, or get_time_series
    results) — use this to visualize a trend instead of just describing
    numbers in text.

    Only call this with numbers you have actually read from a tool result
    or the conversation; do not invent data to chart.

    Args:
        title: chart title.
        x_labels: labels for the x-axis (e.g. years: ["2022", "2023", "2024"]).
        series: one or more named data series, each a list of numbers with
            the same length as x_labels (e.g.
            {"Exports to Italy": [20461, 21467, 22060]}). Multiple series
            are drawn together (multiple lines, or grouped bars) with a
            legend.
        chart_type: "line" (default) or "bar".
        y_label: optional y-axis label (e.g. "GWh").

    Returns a rendered PNG chart image."""
    try:
        return charting.render_chart(title, x_labels, series, chart_type, y_label)
    except charting.ChartError as e:
        return f"Could not render chart: {e}"


@mcp.tool()
def generate_briefing(
    topic: str,
    series_name: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    """Assemble a one-call briefing on a topic: SFOE knowledge base
    findings, a cross-check of the top citation against its original
    public PDF, and (optionally) a multi-year numeric series — instead of
    making several separate ask_energy_question/verify_source/
    get_time_series calls yourself.

    Args:
        topic: the subject to research (e.g. "Swiss hydropower's role in
            the electricity mix"). Used for the knowledge base search and
            as the claim for the time-series lookup, if requested.
        series_name: optional SFOE publication series name (e.g.
            "Schweizerische Elektrizitätsstatistik") to also fetch a
            numeric time series for. Must be given together with
            start_year and end_year to take effect.
        start_year: first year of the optional time series (inclusive).
        end_year: last year of the optional time series (inclusive),
            capped at a 10-year span (same limit as get_time_series).

    Returns a structured Markdown-ish bundle: knowledge base passages and
    citations, a real-page cross-check of the top citation, and (if
    requested) the fetched time series — every section degrades to a
    clear inline note rather than failing the whole call. Use render_chart
    afterward if you want to visualize any numbers found here; this tool
    does not chart automatically."""
    return briefing.generate_briefing(topic, series_name, start_year, end_year)


@mcp.tool()
def explain_anomaly(
    topic: str,
    x_labels: list[str],
    series: dict[str, list[float]],
    threshold_pct: float = 15.0,
) -> str:
    """Given a numeric series you've already extracted (same shape as
    render_chart's inputs), find the year-over-year change that exceeds
    threshold_pct and automatically look up what the SFOE knowledge base
    says about that year — turning a number into a story instead of just
    reporting the delta.

    Use this after get_time_series/verify_source have given you real
    numbers and you want to know why a spike or dip happened, rather than
    manually guessing which year to ask about.

    Args:
        topic: the subject (used to build the follow-up knowledge-base
            query, e.g. "Swiss hydropower production").
        x_labels: labels aligned with each series' values (e.g. years).
        series: one or more named numeric series, same shape as
            render_chart's series argument.
        threshold_pct: minimum absolute year-over-year percentage change
            to count as an anomaly (default 15%).

    Reports the single largest qualifying change plus the real knowledge-
    base passages/citations about that year. If nothing exceeds the
    threshold, says so rather than forcing a story onto normal variation.
    The narrative is only as good as what the knowledge base has indexed
    for that year — it may not always be a specific causal explanation."""
    try:
        return anomaly.explain_anomaly(topic, x_labels, series, threshold_pct)
    except anomaly.AnomalyError as e:
        return f"Could not analyze the series: {e}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
