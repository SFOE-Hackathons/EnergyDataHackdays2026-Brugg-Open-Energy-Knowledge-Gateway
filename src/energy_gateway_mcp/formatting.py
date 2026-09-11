import json
import logging
import re
import sys

_AWS_LOCATION_PATTERN = re.compile(r'(s3://[^\s"]+|https?://[^\s"]*amazonaws\.com[^\s"]*)')

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

NO_RESULTS_MESSAGE = (
    "No relevant SFOE information was found for that question."
)

_IMAGE_EXTRACTION_CAVEAT = (
    "[Note: the following was extracted from a chart/image via OCR and may "
    "contain transcription errors (e.g. mixed-up years or digits) — verify "
    "important figures with verify_source before relying on them.]"
)


def format_answer(question, raw_response):
    """Turn a raw search_energy_knowledge JSON-RPC response into a cited
    answer string. Falls back to a best-effort rendering of whatever it
    gets rather than raising, since a malformed or unanticipated response
    must never crash the tool call."""
    if not isinstance(raw_response, dict):
        logger.warning("Unexpected response type from Gateway: %r", type(raw_response))
        return _fallback_text(raw_response)

    if "error" in raw_response:
        error = raw_response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        return f"The SFOE knowledge gateway returned an error: {message}"

    result = raw_response.get("result")
    if not isinstance(result, dict):
        logger.warning("Response had no usable 'result' field: %s", raw_response)
        return _fallback_text(raw_response)

    if result.get("isError"):
        return f"The SFOE knowledge gateway reported a tool error: {_content_to_text(result.get('content'))}"

    retrieval_results = extract_retrieval_results(result)
    if retrieval_results is None:
        logger.warning("Could not find results in response: %s", result)
        return _fallback_text(result)

    if not retrieval_results:
        return NO_RESULTS_MESSAGE

    return _render(retrieval_results)


def _parse_sourced_response(result, list_key):
    """Parse the common {"sources": {...}, <list_key>: [...]} shape shared
    by search_energy_knowledge/get_metric_timeline, merging each item's
    source info in via its source_id. Returns None if the shape isn't
    found in any text content block."""
    content = result.get("content")
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            parsed = json.loads(block.get("text", ""))
        except ValueError:
            continue
        if isinstance(parsed, dict) and list_key in parsed:
            sources = parsed.get("sources") or {}
            items = []
            for item in parsed[list_key]:
                if not isinstance(item, dict):
                    continue
                source = sources.get(item.get("source_id")) or {}
                merged = dict(item)
                merged["title"] = source.get("title")
                merged["download_url"] = source.get("download_url")
                merged["media_type"] = source.get("media_type")
                merged["published_at"] = source.get("published_at")
                items.append(merged)
            return items

    return None


def extract_retrieval_results(result):
    return _parse_sourced_response(result, "results")


def _render(retrieval_results):
    lines = []
    sources = []

    for result in retrieval_results:
        passage = result.get("text") or ""
        if passage:
            caveats = []
            if result.get("media_type") == "image":
                caveats.append(_IMAGE_EXTRACTION_CAVEAT)
            if result.get("is_truncated"):
                reasons = result.get("truncation_reasons") or []
                reason_text = "; ".join(reasons) if reasons else "reason unknown"
                caveats.append(f"[Note: this passage was truncated during extraction ({reason_text}).]")
            if caveats:
                passage = "\n".join(caveats) + "\n" + passage
            lines.append(passage)

        sources.append(_source_line(len(sources) + 1, result))

    answer = "\n\n".join(lines) if lines else "(no passage text returned)"
    citations = "\n".join(sources)
    note = (
        "Note: base your answer only on the passages above. If part of "
        "the question isn't covered by them, say so explicitly instead "
        "of estimating or interpolating a value."
    )
    return f"{answer}\n\nSources:\n{citations}\n\n{note}"


def _source_line(index, result):
    if not isinstance(result, dict):
        return f"[{index}] (unrecognized result shape)"

    title = result.get("title") or "unknown document"
    download_url = result.get("download_url")
    location_text = f" — {download_url}" if download_url else ""

    score = result.get("score")
    score_text = f", relevance: {score:.2f}" if isinstance(score, (int, float)) else ""

    media_type = result.get("media_type")
    media_note = f" [from {media_type}]" if media_type else ""

    return f"[{index}] {title}{media_note}{location_text}{score_text}"


def format_metric_timeline(metric, raw_response):
    """Turn a raw get_metric_timeline JSON-RPC response into readable,
    year-tagged text with citations."""
    if not isinstance(raw_response, dict):
        return _fallback_text(raw_response)

    if "error" in raw_response:
        error = raw_response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        return f"The SFOE knowledge gateway returned an error: {message}"

    result = raw_response.get("result")
    if not isinstance(result, dict):
        return _fallback_text(raw_response)

    if result.get("isError"):
        return f"The SFOE knowledge gateway reported a tool error: {_content_to_text(result.get('content'))}"

    items = _parse_sourced_response(result, "data")
    if items is None:
        return _fallback_text(result)

    if not items:
        return f"No data was found for '{metric}' in that year range."

    lines = []
    sources = []
    for item in items:
        years = item.get("queried_years") or []
        years_str = ", ".join(str(y) for y in years) if years else "?"
        text = item.get("text")
        if text:
            caveats = []
            if item.get("media_type") == "image":
                caveats.append(_IMAGE_EXTRACTION_CAVEAT)
            if item.get("is_truncated"):
                reasons = item.get("truncation_reasons") or []
                reason_text = "; ".join(reasons) if reasons else "reason unknown"
                caveats.append(f"[Note: this passage was truncated during extraction ({reason_text}).]")
            caveat_text = ("\n".join(caveats) + "\n") if caveats else ""
            lines.append(f"[Year(s) {years_str}]\n{caveat_text}{text}")

        sources.append(_source_line(len(sources) + 1, item))

    body = "\n\n".join(lines) if lines else "(no passage text returned — try requesting full detail)"
    citations = "\n".join(sources)
    note = (
        "Note: base your answer only on the passages above, tagged by the "
        "year(s) they were queried for. If a year is missing, say so "
        "explicitly instead of estimating or interpolating it."
    )
    return f"{body}\n\nSources:\n{citations}\n\n{note}"


def format_chart_data(topic, raw_response):
    """Turn a raw get_chart_data JSON-RPC response into readable table
    text, preserving each chart's exact/estimated precision flag."""
    if not isinstance(raw_response, dict):
        return _fallback_text(raw_response)

    if "error" in raw_response:
        error = raw_response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        return f"The SFOE knowledge gateway returned an error: {message}"

    result = raw_response.get("result")
    if not isinstance(result, dict):
        return _fallback_text(raw_response)

    if result.get("isError"):
        return f"The SFOE knowledge gateway reported a tool error: {_content_to_text(result.get('content'))}"

    content = result.get("content")
    parsed = None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            try:
                candidate = json.loads(block.get("text", ""))
            except ValueError:
                continue
            if isinstance(candidate, dict) and "charts" in candidate:
                parsed = candidate
                break

    if parsed is None:
        return _fallback_text(result)

    charts = parsed.get("charts") or []
    if not charts:
        return f"No chart/table data was found for '{topic}'."

    sources = parsed.get("sources") or {}
    sections = []
    for i, chart in enumerate(charts, start=1):
        if not isinstance(chart, dict):
            continue
        source = sources.get(chart.get("source_id")) or {}
        title = chart.get("title") or "(untitled chart)"
        precision = chart.get("precision", "unknown")
        doc_title = source.get("title", "unknown document")
        download_url = source.get("download_url")
        source_bit = doc_title + (f" — {download_url}" if download_url else "")

        section = [f"[{i}] {title} (precision: {precision}) — {source_bit}"]

        columns = chart.get("columns") or []
        if columns:
            section.append(" | ".join(columns))
        for row in chart.get("rows") or []:
            if not isinstance(row, dict):
                continue
            cells = [str((row.get(col) or {}).get("raw", "")) for col in columns]
            section.append(" | ".join(cells))

        note = chart.get("note")
        if note:
            section.append(note if note.lower().startswith("note") else f"Note: {note}")

        sections.append("\n".join(section))

    body = "\n\n".join(sections)
    note = (
        "Note: each chart is flagged 'exact' (printed data labels) or "
        "'estimated' (read visually) — treat 'estimated' values as "
        "approximate, and prefer 'exact' figures when available."
    )
    return f"{body}\n\n{note}"


def _content_to_text(content):
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return " ".join(t for t in texts if t)
    return str(content)


def _fallback_text(value):
    try:
        dumped = json.dumps(value, indent=2, ensure_ascii=False)
    except TypeError:
        dumped = repr(value)

    scrubbed = _AWS_LOCATION_PATTERN.sub("[source location omitted]", dumped)
    return "Unrecognized response from the SFOE knowledge gateway:\n" + scrubbed
