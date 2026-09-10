from mcp.server.mcpserver import MCPServer

from . import formatting, gateway_client, pubdb_client

mcp = MCPServer("open-energy-gateway")


@mcp.tool()
def ask_energy_question(question: str) -> str:
    """Answer questions about Swiss energy policy, statistics, and SFOE
    (Swiss Federal Office of Energy) publications by retrieving relevant
    passages from the public SFOE knowledge base via the Bedrock AgentCore
    Gateway, with numbered sources for every answer.

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
    whether the claim is correct.

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
    verify the series for you."""
    try:
        return pubdb_client.get_time_series(topic, claim, start_year, end_year)
    except pubdb_client.PubDbError as e:
        return f"Could not build the time series: {e}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
