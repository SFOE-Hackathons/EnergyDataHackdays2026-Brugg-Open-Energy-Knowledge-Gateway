"""Unit tests for rewriting Gateway responses. No network access."""

from __future__ import annotations

import json
from typing import Any

from source_links import SEARCH_BASE, rewrite_sources, s3_key_from_uri, search_url

BUCKET_HOST = "sandbox-bfe-public-data-pdf.s3.eu-central-1.amazonaws.com"
KEY = "2025-11-26_forderung-von-photovoltaikanlagen.pdf"

SOURCE_MAP = {
    KEY: {
        "pdf_url": "https://pubdb.bfe.admin.ch/de/publication/download/7238",
        "pubdb_id": 7238,
        "match": "date",
        "score": 1.0,
        "verified_at": "2026-09-10T12:00:00Z",
    }
}


def envelope(*keys: str) -> dict[str, Any]:
    """Build a Gateway response with one retrieval result per key."""
    results = [
        {
            "content": {"text": "passage", "type": "TEXT"},
            "documentId": f"s3://sandbox-bfe-public-data-pdf/{key}",
            "location": {
                "s3Location": {"uri": f"https://{BUCKET_HOST}/{key}"},
                "type": "S3",
            },
            "metadata": {"_source_uri": f"https://{BUCKET_HOST}/{key}"},
            "score": 0.56,
        }
        for key in keys
    ]
    return {
        "isError": False,
        "resultType": "TOOL_RESULT",
        "content": [
            {"type": "text", "text": json.dumps({"retrievalResults": results})}
        ],
    }


def payload(result: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON string back out of the MCP envelope."""
    return json.loads(result["content"][0]["text"])


def test_s3_key_from_https_uri_is_percent_decoded() -> None:
    uri = f"https://{BUCKET_HOST}/2022-04-01_zusammenschlusse-%28zev%29.pdf"
    assert s3_key_from_uri(uri) == "2022-04-01_zusammenschlusse-(zev).pdf"


def test_s3_key_from_s3_scheme_uri() -> None:
    uri = "s3://sandbox-bfe-public-data-pdf/2025-11-26_foo.pdf"
    assert s3_key_from_uri(uri) == "2025-11-26_foo.pdf"


def test_s3_key_from_unrelated_uri_is_none() -> None:
    assert s3_key_from_uri("https://example.com/whatever.pdf") is None


def test_search_url_contains_site_filters_and_title() -> None:
    url = search_url("forderung-von-photovoltaikanlagen")
    assert url.startswith("https://www.google.com/search?q=")
    assert "site%3Apubdb.bfe.admin.ch" in url
    assert "forderung+von+photovoltaikanlagen" in url


def test_known_key_gets_the_exact_pubdb_link() -> None:
    entry = payload(rewrite_sources(envelope(KEY), SOURCE_MAP))["retrievalResults"][0]
    expected = "https://pubdb.bfe.admin.ch/de/publication/download/7238"
    assert entry["location"]["s3Location"]["uri"] == expected
    assert entry["metadata"]["_source_uri"] == expected
    assert entry["metadata"]["source_url"] == expected
    assert entry["metadata"]["source_confidence"] == "verified"
    assert entry["metadata"]["pubdb_id"] == 7238


def test_document_id_is_left_untouched() -> None:
    entry = payload(rewrite_sources(envelope(KEY), SOURCE_MAP))["retrievalResults"][0]
    assert entry["documentId"] == f"s3://sandbox-bfe-public-data-pdf/{KEY}"


def test_unknown_key_falls_back_to_search() -> None:
    unknown = "2019-01-01_unbekannter-bericht.pdf"
    rewritten = rewrite_sources(envelope(unknown), SOURCE_MAP)
    entry = payload(rewritten)["retrievalResults"][0]
    metadata = entry["metadata"]
    assert metadata["source_confidence"] == "search"
    assert metadata["source_url"].startswith(SEARCH_BASE)
    assert "pubdb_id" not in metadata
    assert BUCKET_HOST not in entry["location"]["s3Location"]["uri"]


def test_empty_map_still_removes_every_s3_link() -> None:
    rewritten = rewrite_sources(envelope(KEY, "2019-01-01_other.pdf"), {})
    assert BUCKET_HOST not in json.dumps(payload(rewritten)["retrievalResults"])


def test_non_text_content_items_pass_through() -> None:
    original = {"content": [{"type": "image", "data": "abc"}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_text_that_is_not_json_passes_through() -> None:
    original = {"content": [{"type": "text", "text": "plain prose, not JSON"}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_json_without_retrieval_results_passes_through() -> None:
    original = {"content": [{"type": "text", "text": json.dumps({"other": 1})}]}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_malformed_result_is_returned_unchanged() -> None:
    original = {"content": "not a list at all"}
    assert rewrite_sources(original, SOURCE_MAP) == original


def test_result_without_content_is_returned_unchanged() -> None:
    original = {"isError": True}
    assert rewrite_sources(original, SOURCE_MAP) == original
