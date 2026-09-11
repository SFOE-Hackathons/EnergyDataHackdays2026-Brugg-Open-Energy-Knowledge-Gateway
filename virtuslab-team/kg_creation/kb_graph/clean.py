"""Deduplicate an existing knowledge graph into a separate, derived file.

This is deliberately not another `annotate.py`. `annotate.py` never rewrites the graph
because a second consumer — the Open Energy Gateway — pins every entity, label and
assertion id; adding an overlay keeps that contract. This module produces the opposite
kind of artifact on purpose: a graph with duplicate predicates folded together, duplicate
entities merged, and safely-orphaned nodes dropped — useful for analysis or a fresh
consumer with no id pin, but not a drop-in replacement for the pinned graph. It is written
to its own file (`<graph>.clean.ttl` by default); `knowledge_graph.ttl` is read, never
touched.

Two things this module treats as *design*, not bugs, and does not try to undo:

- Entity ids are document-scoped on purpose (see `entity_extraction.py`'s "Identity
  model" docstring) — the same real-world thing mentioned in two documents gets two
  `:Entity` nodes, because merging by label alone would silently collapse homonyms too.
  Grouping those nodes back together is exactly the "consumer opts in" step that
  docstring describes; this module is that consumer.
- A relation is written twice by `graph_templates.py` — as a plain triple and as a
  reified `:Assertion` — deliberately, so provenance survives without breaking simple
  traversal. Canonicalizing a predicate here rewrites both forms, since they always
  carry the same predicate IRI.

rdflib for read/write, same choice as `annotate.py` and for the same reason: the graph is
being read and rewritten, not built from templates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import rdflib
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, XSD

from .annotate import detect_base_uri

_HAS_LABEL_FORM = "/hasLabelForm"
_MENTION_COUNT = "/mentionCount"
_LABEL_TEXT = "/labelText"
_LABEL = "/label"

# Descriptive triples that belong to one entity node and must not be copied onto
# another when entities merge — the canonical node already carries its own.
_ENTITY_OWN_PREDICATES_SUFFIXES = (_HAS_LABEL_FORM, _MENTION_COUNT, _LABEL)


@dataclass
class CleanReport:
    """What one clean pass produced. Printed by the CLI, asserted by the tests."""

    graph_triples: int = 0
    clean_triples: int = 0

    predicates_before: int = 0
    predicates_after: int = 0
    predicate_edges_rewritten: int = 0
    predicate_merges: list[tuple[str, list[str]]] = field(default_factory=list)
    predicates_uncontrolled: int = 0
    uncontrolled_head: list[tuple[str, int]] = field(default_factory=list)

    entities_before: int = 0
    entities_after: int = 0
    label_groups_merged: int = 0
    alias_groups_merged: list[tuple[str, int]] = field(default_factory=list)
    entities_dropped: int = 0
    entities_flagged: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"graph            {self.graph_triples:>7,} triples read",
            f"clean            {self.clean_triples:>7,} triples written",
            "",
            f"predicates       {self.predicates_before:>7,} -> {self.predicates_after:,}"
            f"   ({self.predicate_edges_rewritten:,} edges rewritten)",
        ]
        if self.predicate_merges:
            lines.append("  merged into canonical predicate:")
            lines += [
                f"    {canonical:<20} <- {', '.join(raws)}"
                for canonical, raws in self.predicate_merges
            ]
        lines.append(f"  uncontrolled     {self.predicates_uncontrolled:,} (left as-is)")
        if self.uncontrolled_head:
            lines += [f"    {n:>5}  {name}" for name, n in self.uncontrolled_head]
        lines += [
            "",
            f"entities         {self.entities_before:>7,} -> {self.entities_after:,}",
            f"  exact-label groups merged   {self.label_groups_merged:,}",
            f"  alias groups merged         {len(self.alias_groups_merged):,}",
        ]
        lines += [f"    {slug:<25} ({size} nodes)" for slug, size in self.alias_groups_merged]
        lines.append(f"  dropped (mentionCount=0, no edges)   {self.entities_dropped:,}")
        if self.entities_flagged:
            lines.append(
                f"  flagged (mentionCount=0 but still connected, kept): "
                f"{len(self.entities_flagged):,}"
            )
        return "\n".join(lines)


def _predicate_objects(graph: rdflib.Graph, subject: URIRef):
    return list(graph.predicate_objects(subject))


def _subject_predicates(graph: rdflib.Graph, obj: URIRef):
    return list(graph.subject_predicates(obj))


def _canonicalize_predicates(graph: rdflib.Graph, vocab, kg, report: CleanReport) -> None:
    """Rewrite every controlled predicate spelling onto one canonical IRI.

    Touches both forms `graph_templates.py` writes for a relation — the plain triple and
    the reified `rdf:predicate` object — since both always carry the same predicate IRI.
    """
    predicates = {p for p in graph.objects(None, RDF.predicate) if isinstance(p, URIRef)}
    report.predicates_before = len(predicates)

    folded: dict[str, list[str]] = defaultdict(list)
    uncontrolled: dict[str, int] = {}

    for predicate in sorted(predicates, key=str):
        raw_name = str(predicate).rsplit("/", 1)[-1]
        canonical, controlled = vocab.canonical_predicate(raw_name)
        if not controlled:
            count = len(list(graph.subject_objects(predicate)))
            uncontrolled[raw_name] = count
            continue

        canonical_iri = kg[f"rel/{canonical}"]
        folded[canonical].append(raw_name)
        if canonical_iri == predicate:
            continue

        for s, o in list(graph.subject_objects(predicate)):
            graph.remove((s, predicate, o))
            graph.add((s, canonical_iri, o))
            report.predicate_edges_rewritten += 1
        for a in list(graph.subjects(RDF.predicate, predicate)):
            graph.remove((a, RDF.predicate, predicate))
            graph.add((a, RDF.predicate, canonical_iri))
            report.predicate_edges_rewritten += 1

    report.predicate_merges = sorted(
        ((canonical, sorted(raws)) for canonical, raws in folded.items() if len(raws) > 1),
        key=lambda item: -len(item[1]),
    )
    report.predicates_uncontrolled = len(uncontrolled)
    report.uncontrolled_head = sorted(
        uncontrolled.items(), key=lambda kv: (-kv[1], kv[0])
    )[:20]

    predicates_after = {p for p in graph.objects(None, RDF.predicate) if isinstance(p, URIRef)}
    report.predicates_after = len(predicates_after)


def _merge_entities(graph: rdflib.Graph, group: set, kg) -> int:
    """Collapse `group` (2+ entity IRIs) into its lexicographically-first member.

    Relational triples (anything not describing the entity itself — assertion
    subject/object, plain relprop edges, document `mentions` edges) are repointed at the
    canonical node. Descriptive triples (`hasLabelForm`, `mentionCount`, `label`, `rdf:type`)
    are dropped for every non-canonical member; the canonical node already carries its own,
    and `mentionCount` is replaced with the group's summed total.
    """
    canonical = min(group, key=str)
    total_mentions = 0

    for entity in group:
        mc = next(graph.objects(entity, kg.mentionCount), None)
        if mc is not None:
            total_mentions += int(mc)
        if entity == canonical:
            continue

        for p, o in _predicate_objects(graph, entity):
            graph.remove((entity, p, o))
            suffix = str(p).rsplit("/", 1)
            if f"/{suffix[-1]}" in _ENTITY_OWN_PREDICATES_SUFFIXES or p == RDF.type:
                continue
            graph.add((canonical, p, o))
        for s, p in _subject_predicates(graph, entity):
            graph.remove((s, p, entity))
            graph.add((s, p, canonical))

    graph.remove((canonical, kg.mentionCount, None))
    graph.add((canonical, kg.mentionCount, Literal(total_mentions, datatype=XSD.integer)))
    return len(group) - 1


def _consolidate_entities(graph: rdflib.Graph, vocab, kg, report: CleanReport) -> None:
    report.entities_before = len(list(graph.subjects(RDF.type, kg.Entity)))

    entity_label = dict(graph.subject_objects(kg.hasLabelForm))
    label_text = {label: str(text) for label, text in graph.subject_objects(kg.labelText)}

    by_label: dict = defaultdict(set)
    for entity, label_iri in entity_label.items():
        by_label[label_iri].add(entity)

    # Tier 2: distinct labels that resolve to the same glossary term, confidently
    # (Resolution.best is None for both contested and generic matches).
    term_groups: dict = defaultdict(set)
    for label_iri in by_label:
        text = label_text.get(label_iri)
        if text is None:
            continue
        resolution = vocab.resolve(text)
        if resolution.best is not None:
            term_groups[resolution.best.slug].add(label_iri)

    merge_groups: list[set] = []
    consumed_labels: set = set()
    for slug, label_iris in term_groups.items():
        if len(label_iris) < 2:
            continue
        group: set = set()
        for label_iri in label_iris:
            group |= by_label[label_iri]
        merge_groups.append(group)
        consumed_labels |= label_iris
        report.alias_groups_merged.append((slug, len(group)))

    # Tier 1: whatever is left, grouped by exact (already-normalized) label.
    for label_iri, entities in by_label.items():
        if label_iri in consumed_labels:
            continue
        if len(entities) > 1:
            merge_groups.append(entities)
            report.label_groups_merged += 1

    for group in merge_groups:
        _merge_entities(graph, group, kg)

    report.entities_after = len(list(graph.subjects(RDF.type, kg.Entity)))


def _delete_entity(graph: rdflib.Graph, entity: URIRef) -> None:
    for p, o in _predicate_objects(graph, entity):
        graph.remove((entity, p, o))
    for s, p in _subject_predicates(graph, entity):
        graph.remove((s, p, entity))


def _prune_zero_degree(graph: rdflib.Graph, kg, report: CleanReport) -> None:
    """Drop only entities that are both unmentioned and unconnected.

    `mentionCount == 0` alone is not enough: `graph_templates.py` emits a relation's
    plain triple and its reified `:Assertion` from the same row, so an entity that
    participates in any relation shows up as the subject or object of some `:Assertion`
    regardless of its mention count. Dropping those would leave the relation pointing at
    a node that no longer exists.
    """
    assertion_endpoints = set(graph.objects(None, RDF.subject)) | set(
        graph.objects(None, RDF.object)
    )

    for entity in list(graph.subjects(RDF.type, kg.Entity)):
        mc = next(graph.objects(entity, kg.mentionCount), None)
        if mc is None or int(mc) != 0:
            continue
        if entity in assertion_endpoints:
            report.entities_flagged.append(str(entity))
            continue
        _delete_entity(graph, entity)
        report.entities_dropped += 1

    report.entities_after = len(list(graph.subjects(RDF.type, kg.Entity)))


def clean_graph(graph: rdflib.Graph, vocabulary_path: Path) -> tuple[rdflib.Graph, CleanReport]:
    """Build the deduplicated graph for `graph`. The input graph is not modified."""
    from energy_vocab import Vocabulary

    vocab = Vocabulary.load(vocabulary_path)
    base = detect_base_uri(graph)
    kg = rdflib.Namespace(base)

    cleaned = rdflib.Graph()
    cleaned += graph
    for prefix, namespace in graph.namespaces():
        cleaned.bind(prefix, namespace)

    report = CleanReport(graph_triples=len(graph))

    _canonicalize_predicates(cleaned, vocab, kg, report)
    _consolidate_entities(cleaned, vocab, kg, report)
    _prune_zero_degree(cleaned, kg, report)

    report.clean_triples = len(cleaned)
    return cleaned, report


def clean(graph_path: Path, vocabulary_path: Path, out_path: Path) -> CleanReport:
    """Read `graph_path`, write the deduplicated graph to `out_path`, return what happened.

    `graph_path` is only ever read. There is no in-place option, unlike `annotate()` —
    this module's whole point is to not be the thing a pinned consumer loads.
    """
    graph = rdflib.Graph()
    graph.parse(str(graph_path), format="turtle")
    cleaned, report = clean_graph(graph, vocabulary_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned.serialize(destination=str(out_path), format="turtle")
    return report
