"""Entity/relation extraction from document text via kg-gen (LLM-based).

Runs after ingestion, over `DocumentRecord.text`. Best-effort and
per-document: kg-gen calls an LLM once per document, so a single malformed
response, rate limit, or network error must not abort a build that already
paid for the S3 download — log and skip that document instead.

Off by default (`ENTITY_EXTRACTION_ENABLED`) since it adds one LLM call per
document on top of the ingestion cost.

## Identity model (why entity ids are document-scoped)

An entity node is scoped to the document it was extracted from. Two
documents that both mention "Axpo" produce two distinct `:Entity` nodes,
each traceable to exactly one source document. A corpus-wide id keyed on
the label alone — which is what this module used to mint — silently merges
homonyms: two different real-world things sharing a label collapse into one
node, and any consumer asking "which node backs this claim?" gets an answer
that spans documents making unrelated statements.

Cross-document linking is not lost, only made explicit and reversible: every
entity also points at a `:Label` node via `:hasLabelForm`, keyed on the
normalized label text (`label_id`). Grouping by that node reproduces the old
merge behaviour, but as a *lexical* grouping the caller opts into, not an
identity claim baked into the graph. Promoting a lexical group to a real
identity claim needs evidence this pipeline does not have (typing,
disambiguation, an authority list) — so it is left to the consumer.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .documents import DocumentRecord

logger = logging.getLogger(__name__)


@contextmanager
def _heartbeat(label: str, interval: float = 60):
    """Log a "still running" line every `interval` seconds while the wrapped
    block executes, so a hung or silently-crashed call is visible in the log
    instead of an indefinite gap between the "start" and "done" log lines.
    """
    stop = threading.Event()
    start = time.monotonic()

    def _tick() -> None:
        while not stop.wait(interval):
            logger.info("%s still running (%ds elapsed)", label, time.monotonic() - start)

    thread = threading.Thread(target=_tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _normalize(label: str) -> str:
    return label.strip().lower()


def entity_id(label: str, doc_id: str) -> str:
    """Stable id for an entity label *within one document*.

    Case- and whitespace-insensitive, so the same label written differently
    inside a single document still merges. Deliberately NOT corpus-wide —
    see this module's docstring on homonyms. `doc_id` is
    `DocumentRecord.id`.
    """
    return hashlib.sha256(f"{doc_id}|{_normalize(label)}".encode()).hexdigest()[:16]


def label_id(label: str) -> str:
    """Stable id for a normalized label *form*, shared across the corpus.

    Keys the `:Label` node that document-scoped entities hang off. Same
    hash the old corpus-wide `entity_id` used, demoted from an identity to
    a lexical grouping.
    """
    return hashlib.sha256(_normalize(label).encode()).hexdigest()[:16]


def assertion_id(doc_id: str, subject: str, predicate: str, obj: str) -> str:
    """Stable id for one reified relation assertion.

    Keyed on the source document as well as the triple, so the same edge
    asserted by two documents yields two assertion nodes — which is the
    point: each carries its own provenance.
    """
    parts = "|".join((doc_id, _normalize(subject), _normalize(predicate), _normalize(obj)))
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def mention_count(text: str, label: str) -> int:
    """How many times a label occurs in the document body, case-insensitively.

    Ranking signal for the term index: a document naming a term once and one
    naming it fifty times are otherwise indistinguishable. Word-boundary
    matched so "gas" does not count inside "gasification". Counted over the
    *whole* document text, not the slice sent to the LLM.
    """
    label = label.strip()
    if not label or not text:
        return 0
    return len(re.findall(rf"(?<!\w){re.escape(label)}(?!\w)", text, flags=re.IGNORECASE))


@dataclass
class ExtractedGraph:
    doc_id: str
    entities: set[str]
    relations: set[tuple[str, str, str]]
    model: str = ""
    extracted_at: datetime | None = None
    # label -> occurrences in the source document. Covers relation endpoints
    # too, which become term nodes even when the entity pass missed them.
    mention_counts: dict[str, int] = field(default_factory=dict)


def extract_entities(
    docs: list[DocumentRecord],
    model: str,
    api_key: str | None,
    temperature: float,
    max_chars: int,
    context: str,
    chunk_size: int = 5000,
    cluster: bool = True,
    on_graph: "Callable[[ExtractedGraph], None] | None" = None,
) -> list[ExtractedGraph]:
    """Call kg-gen once per non-empty document. Returns one ExtractedGraph per
    document that succeeded; failures are logged and left out.

    `max_chars` of 0 means no cap — the whole document is extracted. kg-gen
    splits it into `chunk_size` chunks on sentence boundaries and runs them in
    parallel, so cost scales with document length rather than being silently
    truncated to the head.

    `on_graph` is called with each document's result as soon as it succeeds —
    the hook the checkpoint uses to persist extraction progress. Extraction is
    by far the most expensive phase, so a run killed partway must not lose the
    documents it already paid for.

    `cluster` turns on kg-gen's reconciliation pass. Without it `generate()`
    runs the entity pass and the relation pass independently and never
    reconciles them, so the relation pass's phrasing ("a hydropower reserve")
    becomes a separate term from the entity pass's ("hydropower reserve") and
    the index fragments. Costs extra LLM calls per document.
    """
    from kg_gen import (
        KGGen,
    )  # heavy import (pulls in torch/transformers) — keep off paths that don't extract

    kg = KGGen(model=model, temperature=temperature, api_key=api_key)

    results: list[ExtractedGraph] = []
    for i, doc in enumerate(docs, start=1):
        text = (doc.text or "").strip()
        if not text:
            continue
        logger.info("Processing file %d of %d: %s", i, len(docs), doc.source_uri)
        payload = text[:max_chars] if max_chars > 0 else text
        try:
            with _heartbeat(f"kg-gen extraction for {doc.source_uri}"):
                graph = kg.generate(
                    input_data=payload,
                    context=context,
                    chunk_size=chunk_size,
                    cluster=cluster,
                )
        except Exception:
            logger.exception(
                "kg-gen extraction failed for %s — skipped", doc.source_uri
            )
            continue
        endpoints = {end for s, _, o in graph.relations for end in (s, o)}
        orphans = endpoints - graph.entities
        if endpoints:
            logger.info(
                "%s: %d entities, %d relations; %d/%d relation endpoints not in "
                "the entity set%s",
                doc.source_uri,
                len(graph.entities),
                len(graph.relations),
                len(orphans),
                len(endpoints),
                "" if cluster else " (clustering is OFF — expect fragmentation)",
            )

        extracted = ExtractedGraph(
            doc_id=doc.id,
            entities=graph.entities,
            relations=graph.relations,
            model=model,
            # Per document, not per run: a long build's later documents were
            # genuinely extracted later, and a resumed build mixes runs.
            extracted_at=datetime.now(timezone.utc),
            mention_counts={
                label: mention_count(text, label)
                for label in graph.entities | endpoints
            },
        )
        if on_graph is not None:
            on_graph(extracted)
        results.append(extracted)
    return results
