"""DocumentRecord list -> polars DataFrames ready for maplib template expansion.

Column names here must exactly match the OTTR Variable names declared in
graph_templates.py (maplib binds template parameters to DataFrame columns
by name, not position) — keep the two files in sync.

Tables:
- documents: one row per Document node. Identification and extraction flags
  only — deliberately no document body, see graph_templates.py. Column names
  must stay in step with document_template's parameters: maplib rejects a
  parameter with no column *and* a column with no parameter.
- properties: long/tidy form (DocIri, PredicateIri, Value) for the generic
  metadata bag — no fixed schema, whatever keys the source actually supplied.
- labels: one row per unique normalized label form across the corpus — the
  lexical grouping node, see entity_extraction.py's "Identity model".
- entities: one row per (document, label) pair — a posting in term-index
  terms, and the sole source of the document -> entity edge (:mentions).
  Entity identity is document-scoped, so the same label in two documents is
  two rows and two nodes, each linked to its document, to the shared label
  form, and carrying how often the label occurs in that document (the
  ranking signal). There is deliberately no second table for the edge: this
  row already covers every posting, including relation endpoints kg-gen
  names without listing as entities, so the edge cannot go missing.
- relations: one row per extracted (subject, predicate, object) triple,
  carrying the provenance needed to reify it — the source document, the
  extraction model, and when it ran. Predicate minted the same way property
  keys are (slug -> IRI, no separate label bookkeeping).
"""
from __future__ import annotations

from datetime import datetime

import polars as pl

from .documents import DocumentRecord
from .entity_extraction import ExtractedGraph, assertion_id, entity_id, label_id


def documents_to_dataframe(docs: list[DocumentRecord], base_uri: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "DocIri": [f"{base_uri}doc/{d.id}" for d in docs],
            "SourceUri": [d.source_uri for d in docs],
            "ContentType": [d.content_type for d in docs],
            "SizeBytes": [d.size_bytes for d in docs],
            # No body column: the document text is not written to the graph.
            # DocumentRecord.text is still read, but only to derive this flag.
            "HasText": [bool(d.text and d.text.strip()) for d in docs],
            "TextTruncated": [d.text_truncated for d in docs],
        }
    )


def properties_to_dataframe(docs: list[DocumentRecord], base_uri: str) -> pl.DataFrame:
    doc_iris: list[str] = []
    predicate_iris: list[str] = []
    values: list[str] = []
    for d in docs:
        for key, value in d.properties.items():
            doc_iris.append(f"{base_uri}doc/{d.id}")
            predicate_iris.append(f"{base_uri}prop/{_slug(key)}")
            values.append(value)
    return pl.DataFrame({"DocIri": doc_iris, "PredicateIri": predicate_iris, "Value": values})


def _slug(key: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in key.strip())


def labels_to_dataframe(graphs: list[ExtractedGraph], base_uri: str) -> pl.DataFrame:
    """One row per normalized label form, deduped across the whole corpus."""
    texts: dict[str, str] = {}  # label_id -> label text, first occurrence wins
    for g in graphs:
        for label in g.entities:
            texts.setdefault(label_id(label), label)
        # Relation endpoints are not guaranteed to appear in `entities` — kg-gen
        # can name a subject or object it did not also list. Cover them, or the
        # entity rows below would reference label nodes that were never written.
        for subject, _, obj in g.relations:
            texts.setdefault(label_id(subject), subject)
            texts.setdefault(label_id(obj), obj)
    return pl.DataFrame(
        {
            "LabelIri": [f"{base_uri}label/{lid}" for lid in texts],
            "LabelText": list(texts.values()),
        },
        schema={"LabelIri": pl.String, "LabelText": pl.String},
    )


def entities_to_dataframe(graphs: list[ExtractedGraph], base_uri: str) -> pl.DataFrame:
    """One row per (document, label). Deduped by entity IRI, which already
    encodes the document — so no cross-document merge happens here."""
    rows: dict[str, tuple[str, str, str, int]] = {}  # EntityIri -> (Label, LabelIri, DocIri, MentionCount)
    for g in graphs:
        doc_iri = f"{base_uri}doc/{g.doc_id}"
        candidates = set(g.entities)
        for subject, _, obj in g.relations:
            candidates.add(subject)
            candidates.add(obj)
        for label in candidates:
            iri = f"{base_uri}entity/{entity_id(label, g.doc_id)}"
            rows.setdefault(
                iri,
                (
                    label,
                    f"{base_uri}label/{label_id(label)}",
                    doc_iri,
                    g.mention_counts.get(label, 0),
                ),
            )
    return pl.DataFrame(
        {
            "EntityIri": list(rows),
            "Label": [r[0] for r in rows.values()],
            "LabelIri": [r[1] for r in rows.values()],
            "DocIri": [r[2] for r in rows.values()],
            "MentionCount": [r[3] for r in rows.values()],
        },
        schema={
            "EntityIri": pl.String,
            "Label": pl.String,
            "LabelIri": pl.String,
            "DocIri": pl.String,
            "MentionCount": pl.Int64,
        },
    )


def relations_to_dataframe(graphs: list[ExtractedGraph], base_uri: str) -> pl.DataFrame:
    """One row per extracted relation, with the provenance the reified
    :Assertion node in graph_templates.py needs.

    Relation endpoints resolve to *this document's* entity nodes — an edge
    extracted from document A can never point at a node owned by document B.
    """
    assertion_iris: list[str] = []
    subject_iris: list[str] = []
    predicate_iris: list[str] = []
    object_iris: list[str] = []
    doc_iris: list[str] = []
    models: list[str] = []
    extracted_at: list[datetime | None] = []
    for g in graphs:
        for subject, predicate, obj in g.relations:
            assertion_iris.append(f"{base_uri}assertion/{assertion_id(g.doc_id, subject, predicate, obj)}")
            subject_iris.append(f"{base_uri}entity/{entity_id(subject, g.doc_id)}")
            predicate_iris.append(f"{base_uri}relprop/{_slug(predicate)}")
            object_iris.append(f"{base_uri}entity/{entity_id(obj, g.doc_id)}")
            doc_iris.append(f"{base_uri}doc/{g.doc_id}")
            models.append(g.model)
            extracted_at.append(g.extracted_at)
    return pl.DataFrame(
        {
            "AssertionIri": assertion_iris,
            "SubjectIri": subject_iris,
            "PredicateIri": predicate_iris,
            "ObjectIri": object_iris,
            "DocIri": doc_iris,
            "ExtractionModel": models,
            "ExtractedAt": extracted_at,
        },
        # Explicit schema: an empty relation set would otherwise give ExtractedAt
        # a Null dtype, which maplib cannot bind to an xsd:dateTime parameter.
        schema={
            "AssertionIri": pl.String,
            "SubjectIri": pl.String,
            "PredicateIri": pl.String,
            "ObjectIri": pl.String,
            "DocIri": pl.String,
            "ExtractionModel": pl.String,
            "ExtractedAt": pl.Datetime(time_unit="us", time_zone="UTC"),
        },
    )
