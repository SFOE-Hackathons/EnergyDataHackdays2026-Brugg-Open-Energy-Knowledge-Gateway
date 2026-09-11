"""Sanity check: do sampled Bedrock retrieve() results point at documents
that actually made it into the graph? Catches ingestion gaps."""
from __future__ import annotations

from pathlib import Path

import rdflib

_SAMPLE_QUERIES = [
    "overview",
    "summary",
    "introduction",
    "definition",
    "background",
]


def _graph_source_uris(ttl_path: Path) -> set[str]:
    g = rdflib.Graph()
    g.parse(str(ttl_path), format="turtle")
    # Match any predicate ending in /sourceUri rather than a fixed base URI —
    # graph_templates.py mints it under whatever KG_BASE_URI was configured.
    return {
        str(row.uri)
        for row in g.query(
            """
        SELECT ?uri WHERE { ?doc ?p ?uri . FILTER(STRENDS(STR(?p), "/sourceUri")) }
        """
        )
    }


def sample_and_check(runtime, kb_id: str, kb_type: str, ttl_path: Path, sample: int) -> tuple[int, int]:
    graph_uris = _graph_source_uris(ttl_path)

    # Managed KBs reject vectorSearchConfiguration and require
    # managedSearchConfiguration instead — see docs/connecting-to-bedrock-knowledge-base.md
    # section 1 for the underlying AWS behavior this branches on.
    if kb_type == "MANAGED":
        retrieval_configuration = {"managedSearchConfiguration": {"numberOfResults": 3}}
    else:
        retrieval_configuration = {"vectorSearchConfiguration": {"numberOfResults": 3}}

    checked = 0
    matched = 0
    for query in _SAMPLE_QUERIES[:sample]:
        resp = runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration=retrieval_configuration,
        )
        for result in resp.get("retrievalResults", []):
            uri = result.get("location", {}).get("s3Location", {}).get("uri")
            if not uri:
                continue
            checked += 1
            if uri in graph_uris:
                matched += 1

    return matched, checked
