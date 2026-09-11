"""Annotate an existing knowledge graph with glossary terms, as a pure addition.

The graph is read, never rewritten. Every entity id, label id and assertion id stays
byte-identical, because a second consumer — the Open Energy Gateway — pins those hashes
and would break if they moved. What this module produces is an *overlay*: a separate
Turtle file of new triples that sit beside the existing nodes and say what a label means.

    kg:entity/1a2b…  kg:canonicalTerm evt:winter-reserve ; kg:termType ev:regulation .

Load the overlay next to the graph and both are queryable together; delete it and you are
exactly where you started. Nothing here re-extracts, re-hashes or re-derives anything, so
a vocabulary change costs one pass over the graph rather than a rebuild.

rdflib rather than maplib on purpose: rdflib is already a dependency, already used in
`verify.py`, and the graph is being *read* here, which is not what the OTTR templates in
`graph_templates.py` are for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import rdflib
from rdflib import Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

# The kg: base is configurable (KG_BASE_URI), so nothing here may hardcode it. Predicates
# are found by their suffix, the way verify.py:20-23 already does.
_SOURCE_URI = "/sourceUri"
_LABEL_TEXT = "/labelText"
_HAS_LABEL_FORM = "/hasLabelForm"
_MENTION_COUNT = "/mentionCount"

# `2025-12-15_energiestrategie-2050-monitoring-bericht-2025-langfassung.pdf`
_DATED_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-](.+?)\.[A-Za-z0-9]+$")

# The unresolved list is meant to be a work queue for the glossary, so it has to contain
# things a person could plausibly add as terms. The extractor mints a great many
# measurements and bare numbers as entities — "10 605 GWh", "0:00", "1% of Grundkapital" —
# and they crowd out the real vocabulary gaps entirely.
_MEASUREMENT = re.compile(r"^[\d\s.,%'’‘\u2013\u2014+/:-]*\d[\d\s.,%'’‘\u2013\u2014+/:-]*$")
_LEADING_NUMBER = re.compile(r"^[\d\s.,'’‘+-]*\d")


def is_glossary_candidate(text: str) -> bool:
    """Could this label plausibly become a glossary term?

    Excludes measurements, bare numbers and number-led phrases. Deliberately generous
    otherwise — a false positive here only adds a line to a report a human reads.
    """
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped.split()) > 8:
        return False
    if _MEASUREMENT.match(stripped) or _LEADING_NUMBER.match(stripped):
        return False
    return any(ch.isalpha() for ch in stripped)


@dataclass
class AnnotationReport:
    """What one annotation pass produced. Printed by the CLI, asserted by the tests."""

    graph_triples: int = 0
    overlay_triples: int = 0
    labels_linked: int = 0
    labels_contested: int = 0
    labels_generic: int = 0
    labels_unresolved: int = 0
    entities_annotated: int = 0
    documents_dated: int = 0
    predicates_annotated: int = 0
    predicates_total: int = 0
    unresolved_head: list[tuple[str, int]] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"graph            {self.graph_triples:>7,} triples read",
            f"overlay          {self.overlay_triples:>7,} triples written",
            f"labels linked    {self.labels_linked:>7,}"
            f"   contested {self.labels_contested}"
            f"   generic {self.labels_generic}"
            f"   unresolved {self.labels_unresolved}",
            f"entities         {self.entities_annotated:>7,} annotated",
            f"documents        {self.documents_dated:>7,} dated",
            f"predicates       {self.predicates_annotated:>7,} of {self.predicates_total} annotated",
        ]
        if self.unresolved_head:
            lines.append("\nmost frequent unresolved labels — the glossary's work queue:")
            lines += [f"  {n:>5}  {text}" for text, n in self.unresolved_head]
        return "\n".join(lines)


def detect_base_uri(graph: rdflib.Graph) -> str:
    """Recover KG_BASE_URI from the graph rather than trusting configuration.

    The overlay has to mint IRIs in the same namespace as the graph it annotates, and the
    graph in hand may have been built by someone else with a different setting.
    """
    for predicate in graph.predicates():
        text = str(predicate)
        if text.endswith(_SOURCE_URI):
            return text[: -len(_SOURCE_URI) + 1]
    raise ValueError(
        "cannot determine the kg: base URI — no predicate ending in /sourceUri. "
        "Is this a knowledge graph produced by kb-graph?"
    )


def _iter_by_suffix(graph: rdflib.Graph, suffix: str):
    """Subject/object pairs for a predicate identified by suffix, not by full IRI."""
    for subject, predicate, obj in graph:
        if str(predicate).endswith(suffix):
            yield subject, obj


def annotate_graph(graph: rdflib.Graph, vocabulary_path: Path) -> tuple[rdflib.Graph, AnnotationReport]:
    """Build the overlay for `graph`. The input graph is not modified."""
    from energy_vocab import Vocabulary

    vocab = Vocabulary.load(vocabulary_path)
    raw = json.loads(Path(vocabulary_path).read_text(encoding="utf-8"))

    base = detect_base_uri(graph)
    kg = Namespace(base)
    evt = Namespace(raw["term_iri_base"])
    ev = Namespace(raw["vocab_iri_base"])

    overlay = rdflib.Graph()
    overlay.bind("kg", kg)
    overlay.bind("evt", evt)
    overlay.bind("ev", ev)

    report = AnnotationReport(graph_triples=len(graph))

    # One shared node carries the vocabulary identity, so every annotation is
    # attributable without repeating the hash on thousands of triples.
    digest = raw["content_hash"].split(":")[-1][:12]
    glossary_node = kg[f"glossary/{digest}"]
    overlay.add((glossary_node, RDF.type, kg.GlossaryVersion))
    overlay.add((glossary_node, kg.contentHash, Literal(raw["content_hash"])))
    overlay.add((glossary_node, kg.schemaVersion, Literal(raw["schema_version"])))
    overlay.add((glossary_node, kg.termIriBase, Literal(raw["term_iri_base"])))

    weights = _label_weights(graph)
    _annotate_labels(graph, overlay, vocab, kg, evt, ev, glossary_node, report, weights)
    _annotate_entities(graph, overlay, kg, evt, ev, report)
    _annotate_documents(graph, overlay, kg, report)
    _annotate_predicates(graph, overlay, vocab, kg, glossary_node, report)

    report.overlay_triples = len(overlay)
    return overlay, report


def _label_weights(graph: rdflib.Graph) -> dict:
    """Total mentions per label form, summed over the entities that carry it.

    Ranking the unresolved list by how often the corpus actually says something is the
    difference between a work queue and an alphabetical dump: every :Label node occurs
    exactly once, so counting nodes ranks nothing.
    """
    counts: dict = {}
    for entity, count in _iter_by_suffix(graph, _MENTION_COUNT):
        counts[entity] = int(count)
    weights: dict = {}
    for entity, label_iri in _iter_by_suffix(graph, _HAS_LABEL_FORM):
        weights[label_iri] = weights.get(label_iri, 0) + counts.get(entity, 0)
    return weights


def _annotate_labels(graph, overlay, vocab, kg, evt, ev, glossary_node, report, weights) -> None:
    """Say what each label form means — or record that its meaning is disputed.

    A contested label gets `kg:contestedTerm` for every claimant and no
    `kg:canonicalTerm`, mirroring `Resolution.best`: the dispute is recorded, the guess is
    not made. A generic label ('Energie', 'Leistung') gets nothing at all, because a
    marker on the node would invite a consumer to treat it as a weak link.
    """
    unresolved: dict[str, int] = {}
    for label_iri, text in _iter_by_suffix(graph, _LABEL_TEXT):
        resolution = vocab.resolve(str(text))
        if resolution.contested:
            for candidate in resolution.candidates:
                overlay.add((label_iri, kg.contestedTerm, evt[candidate.slug]))
            overlay.add((label_iri, kg.accordingTo, glossary_node))
            report.labels_contested += 1
        elif resolution.best is not None:
            best = resolution.best
            overlay.add((label_iri, kg.canonicalTerm, evt[best.slug]))
            overlay.add((label_iri, kg.matchRule, Literal(best.rule)))
            overlay.add((label_iri, kg.termType, ev[best.type]))
            overlay.add((label_iri, kg.accordingTo, glossary_node))
            report.labels_linked += 1
        elif resolution.reason.startswith("generic:"):
            report.labels_generic += 1
        else:
            report.labels_unresolved += 1
            if resolution.is_domain_candidate and is_glossary_candidate(str(text)):
                mentions = weights.get(label_iri, 0)
                unresolved[str(text)] = unresolved.get(str(text), 0) + mentions

    report.unresolved_head = sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0]))[:15]


def _annotate_entities(graph, overlay, kg, evt, ev, report) -> None:
    """Put the same fact beside the entity, because the entity is what gets queried.

    Derived from the label links rather than re-resolved, so the two can never disagree.
    """
    label_terms = {
        label: (term, next(overlay.objects(label, kg.termType), None))
        for label, term in overlay.subject_objects(kg.canonicalTerm)
    }
    for entity, label_iri in _iter_by_suffix(graph, _HAS_LABEL_FORM):
        found = label_terms.get(label_iri)
        if found is None:
            continue
        term, term_type = found
        overlay.add((entity, kg.canonicalTerm, term))
        if term_type is not None:
            overlay.add((entity, kg.termType, term_type))
        report.entities_annotated += 1


def _annotate_documents(graph, overlay, kg, report) -> None:
    """Publication date and title, read out of the S3 key already in the graph.

    The graph carries no document metadata at all today, so "how did X change between the
    reports" has no time ordering to sort by. The key has one, for free.
    """
    for doc, uri in _iter_by_suffix(graph, _SOURCE_URI):
        match = _DATED_KEY.match(str(uri).rsplit("/", 1)[-1])
        if match is None:
            continue
        overlay.add((doc, kg.publicationDate, Literal(match.group(1), datatype=XSD.date)))
        overlay.add((doc, kg.title, Literal(match.group(2).replace("-", " "))))
        report.documents_dated += 1


def _annotate_predicates(graph, overlay, vocab, kg, glossary_node, report) -> None:
    """Annotate the predicate IRI; never rewrite the assertion that uses it.

    Mapping `is_part_of` onto `part-of` by editing 326 assertions would rewrite existing
    triples. Saying it once, about the predicate itself, is 25 triples and leaves every
    assertion byte-identical. A consumer wanting canonical edges joins one hop.
    """
    predicates = {p for p in graph.objects(None, RDF.predicate) if isinstance(p, URIRef)}
    report.predicates_total = len(predicates)
    for predicate in sorted(predicates, key=str):
        raw_name = str(predicate).rsplit("/", 1)[-1]
        canonical, controlled = vocab.canonical_predicate(raw_name)
        if not controlled:
            continue
        overlay.add((predicate, kg.canonicalPredicate, kg[f"rel/{canonical}"]))
        overlay.add((predicate, kg.accordingTo, glossary_node))
        report.predicates_annotated += 1

        declared = vocab.predicate(canonical)
        if declared is None:
            continue
        node = kg[f"rel/{canonical}"]
        overlay.add((node, RDF.type, kg.ControlledPredicate))
        if declared.get("definition"):
            overlay.add((node, kg.definition, Literal(declared["definition"])))
        if declared.get("inverse"):
            overlay.add((node, kg.inversePredicate, kg[f"rel/{declared['inverse']}"]))
        overlay.add((node, kg.symmetric, Literal(bool(declared.get("symmetric")))))


def annotate(
    graph_path: Path,
    vocabulary_path: Path,
    out_path: Path,
    *,
    in_place: bool = False,
) -> AnnotationReport:
    """Read `graph_path`, write the overlay, return what happened.

    `in_place` appends the overlay to the source instead of writing beside it, for
    consumers that can only load one file. The default keeps them separate so that "the
    old graph is unchanged" is literally true on disk.
    """
    graph = rdflib.Graph()
    graph.parse(str(graph_path), format="turtle")
    overlay, report = annotate_graph(graph, vocabulary_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if in_place:
        graph += overlay
        graph.serialize(destination=str(graph_path), format="turtle")
    else:
        overlay.serialize(destination=str(out_path), format="turtle")
    return report
