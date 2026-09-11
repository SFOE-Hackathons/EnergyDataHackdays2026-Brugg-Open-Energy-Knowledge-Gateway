from . import formatting, gateway_client, pubdb_client


def generate_briefing(topic, series_name=None, start_year=None, end_year=None):
    """Assemble a one-call briefing: knowledge base findings, a cross-check
    of the top citation against its original public PDF, and (optionally)
    a multi-year numeric series. Never raises — every sub-step degrades to
    an inline note rather than aborting the whole briefing."""
    sections = [f"# Briefing: {topic}", ""]

    sections.append("## Knowledge base findings")
    top_citation = None
    try:
        raw = gateway_client.retrieve(topic)
        result = raw.get("result") if isinstance(raw, dict) else None
        retrieval_results = (
            formatting.extract_retrieval_results(result)
            if isinstance(result, dict) else None
        )
        if retrieval_results:
            top_citation = retrieval_results[0]
        sections.append(formatting.format_answer(topic, raw))
    except (gateway_client.AuthError, gateway_client.GatewayError) as e:
        sections.append(f"(Could not retrieve knowledge base passages: {e})")
    sections.append("")

    if top_citation:
        document_title = top_citation.get("title")
        passage = (top_citation.get("text") or "")[:300]
        if document_title:
            sections.append("## Source verification (top citation)")
            try:
                sections.append(
                    pubdb_client.verify_citation(document_title, passage or topic)
                )
            except pubdb_client.PubDbError as e:
                sections.append(
                    f"(Could not verify top citation against pubdb.bfe.admin.ch: {e})"
                )
            sections.append("")

    if series_name and start_year is not None and end_year is not None:
        sections.append(f"## {series_name} time series ({start_year}-{end_year})")
        try:
            sections.append(
                pubdb_client.get_time_series(series_name, topic, start_year, end_year)
            )
        except pubdb_client.PubDbError as e:
            sections.append(f"(Could not build time series: {e})")
        sections.append("")

    return "\n".join(sections)
