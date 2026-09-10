import json
import logging
import re
import sys
from urllib.parse import unquote, urlparse

_AWS_LOCATION_PATTERN = re.compile(r'(s3://[^\s"]+|https?://[^\s"]*amazonaws\.com[^\s"]*)')

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

NO_RESULTS_MESSAGE = (
    "No relevant SFOE information was found for that question."
)


def format_answer(question, raw_response):
    """Turn a raw MCP tools/call JSON-RPC response into a cited answer string.

    The exact shape of the Gateway's Retrieve response hasn't been confirmed
    against real credentials yet (see the investigation step in the plan).
    This parser targets the expected shape (MCP `result.content` wrapping a
    Bedrock `retrievalResults[]` payload) but falls back to a best-effort
    rendering of whatever it gets rather than raising, since a malformed or
    unanticipated response must never crash the tool call.
    """
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

    retrieval_results = _extract_retrieval_results(result)
    if retrieval_results is None:
        logger.warning("Could not find retrievalResults in response: %s", result)
        return _fallback_text(result)

    if not retrieval_results:
        return NO_RESULTS_MESSAGE

    return _render(retrieval_results)


def _extract_retrieval_results(result):
    content = result.get("content")
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text", "")
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "retrievalResults" in parsed:
            return parsed["retrievalResults"]

    return None


_IMAGE_EXTRACTION_CAVEAT = (
    "[Note: the following was extracted from a chart/image via OCR and may "
    "contain transcription errors (e.g. mixed-up years or digits) — verify "
    "important figures with verify_source before relying on them.]"
)


def _render(retrieval_results):
    lines = []
    sources = []

    for result in retrieval_results:
        passage = _passage_text(result)
        if passage:
            if _media_type(result) == "image":
                passage = f"{_IMAGE_EXTRACTION_CAVEAT}\n{passage}"
            lines.append(passage)

        source_line = _source_line(len(sources) + 1, result)
        sources.append(source_line)

    answer = "\n\n".join(lines) if lines else "(no passage text returned)"
    citations = "\n".join(sources)
    note = (
        "Note: base your answer only on the passages above. If part of "
        "the question isn't covered by them, say so explicitly instead "
        "of estimating or interpolating a value."
    )
    return f"{answer}\n\nSources:\n{citations}\n\n{note}"


def _passage_text(result):
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, dict):
        return content.get("text", "")
    return ""


def _source_line(index, result):
    if not isinstance(result, dict):
        return f"[{index}] (unrecognized result shape)"

    metadata = result.get("metadata") or {}
    title = (
        metadata.get("_document_title")
        or metadata.get("title")
        or _filename_from_result(result)
        or "unknown document"
    )

    score = result.get("score")
    score_text = f", relevance: {score:.2f}" if isinstance(score, (int, float)) else ""

    media_type = _media_type(result)
    media_note = f" [from {media_type}]" if media_type else ""

    return f"[{index}] {title}{media_note}{score_text}"


def _media_type(result):
    metadata = result.get("metadata") if isinstance(result, dict) else None
    return metadata.get("_media_type") if isinstance(metadata, dict) else None


def _filename_from_result(result):
    """Best-effort filename fallback, deliberately stripped of any bucket,
    domain, or protocol info (no AWS/S3 details in citations)."""
    location = result.get("location") or {}
    s3_location = location.get("s3Location") or {}
    uri = s3_location.get("uri") or result.get("documentId")
    if not uri:
        return None

    path = urlparse(uri).path or uri
    filename = path.rstrip("/").rsplit("/", 1)[-1]
    return unquote(filename) or None


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
