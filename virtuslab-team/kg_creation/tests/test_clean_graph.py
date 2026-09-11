"""Tests for the dedup pass.

Unlike annotate.py's overlay, this module rewrites — so the contract under test is
different: predicate spellings fold together, duplicate entities merge, and only
safely-orphaned nodes disappear. The source `rdflib.Graph` object passed in is still
never mutated (`clean_graph` returns a new graph), which the first test checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

rdflib = pytest.importorskip("rdflib")
pytest.importorskip("energy_vocab")

from kb_graph.clean import clean, clean_graph  # noqa: E402

KG = "https://example.org/kg/"
TERM_BASE = "https://virtuslab.github.io/energy-domain-expertise/term/"
VOCAB_BASE = "https://virtuslab.github.io/energy-domain-expertise/vocab#"


def _vocabulary(tmp_path: Path, **overrides) -> Path:
    data = {
        "schema_version": "1.0",
        "content_hash": "sha256:" + "ab" * 32,
        "term_iri_base": TERM_BASE,
        "vocab_iri_base": VOCAB_BASE,
        "normalization": {"rules": [], "stop_suffixes": []},
        "counts": {},
        "terms": {
            "bfe": {
                "term": "Bundesamt für Energie", "type": "org",
                "subdomain": "institutions", "file": "i.md#bfe", "gloss": "",
                "labels": [
                    {"text": "Bundesamt für Energie", "lang": "de", "role": "preferred"},
                    {"text": "BFE", "lang": "de", "role": "alias"},
                ],
                "related": [], "see_also": [], "broader": [], "narrower": [],
            },
        },
        "alias_index": {
            "bundesamt für energie": "bfe",
            "bfe": "bfe",
        },
        "contested_aliases": {},
        "generic_keys": [],
        "sources": {}, "source_alias_index": {}, "contested_source_aliases": {},
        "predicates": {},
        "predicate_map": {
            "is_part_of": "part-of", "part_of": "part-of", "are_part_of": "part-of",
            "ist_teil_von": "part-of",
            "includes": "includes", "include": "includes", "umfasst": "includes",
        },
    }
    data.update(overrides)
    path = tmp_path / "vocabulary.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


PREFIXES = (
    f"@prefix kg: <{KG}> .\n"
    "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
)


def _graph(body: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=PREFIXES + body, format="turtle")
    return g


# --------------------------------------------------------------- the contract

def test_source_graph_is_untouched(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.ttl"
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Schweiz" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 1 .\n'
    )
    graph_path.write_text(PREFIXES + body, encoding="utf-8")
    before = graph_path.read_bytes()
    clean(graph_path, _vocabulary(tmp_path), tmp_path / "clean.ttl")
    assert graph_path.read_bytes() == before


def test_clean_graph_does_not_mutate_input(tmp_path: Path) -> None:
    graph = _graph(
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Schweiz" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 1 .\n'
    )
    before = set(graph)
    clean_graph(graph, _vocabulary(tmp_path))
    assert set(graph) == before


# --------------------------------------------------------------- predicates

PREDICATE_BODY = """
<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .
<https://example.org/kg/entity/e1> a kg:Entity ; kg:mentionCount 1 .
<https://example.org/kg/entity/e2> a kg:Entity ; kg:mentionCount 1 .
<https://example.org/kg/entity/e3> a kg:Entity ; kg:mentionCount 1 .
<https://example.org/kg/entity/e1> <https://example.org/kg/relprop/is_part_of> <https://example.org/kg/entity/e2> .
<https://example.org/kg/assertion/a1> a kg:Assertion ;
    rdf:subject <https://example.org/kg/entity/e1> ;
    rdf:predicate <https://example.org/kg/relprop/is_part_of> ;
    rdf:object <https://example.org/kg/entity/e2> .
<https://example.org/kg/entity/e2> <https://example.org/kg/relprop/are_part_of> <https://example.org/kg/entity/e3> .
<https://example.org/kg/assertion/a2> a kg:Assertion ;
    rdf:subject <https://example.org/kg/entity/e2> ;
    rdf:predicate <https://example.org/kg/relprop/are_part_of> ;
    rdf:object <https://example.org/kg/entity/e3> .
<https://example.org/kg/entity/e1> <https://example.org/kg/relprop/ist> <https://example.org/kg/entity/e3> .
<https://example.org/kg/assertion/a3> a kg:Assertion ;
    rdf:subject <https://example.org/kg/entity/e1> ;
    rdf:predicate <https://example.org/kg/relprop/ist> ;
    rdf:object <https://example.org/kg/entity/e3> .
"""


def test_predicate_spellings_fold_onto_one_canonical_iri(tmp_path: Path) -> None:
    cleaned, report = clean_graph(_graph(PREDICATE_BODY), _vocabulary(tmp_path))
    canonical = rdflib.URIRef(f"{KG}rel/part-of")
    e1, e2, e3 = (rdflib.URIRef(f"{KG}entity/{n}") for n in ("e1", "e2", "e3"))
    assert (e1, canonical, e2) in cleaned
    assert (e2, canonical, e3) in cleaned
    a1 = rdflib.URIRef(f"{KG}assertion/a1")
    a2 = rdflib.URIRef(f"{KG}assertion/a2")
    assert (a1, rdflib.RDF.predicate, canonical) in cleaned
    assert (a2, rdflib.RDF.predicate, canonical) in cleaned
    assert ("part-of", ["are_part_of", "is_part_of"]) in report.predicate_merges


def test_uncontrolled_predicate_is_left_alone(tmp_path: Path) -> None:
    cleaned, report = clean_graph(_graph(PREDICATE_BODY), _vocabulary(tmp_path))
    raw = rdflib.URIRef(f"{KG}relprop/ist")
    e1, e3 = rdflib.URIRef(f"{KG}entity/e1"), rdflib.URIRef(f"{KG}entity/e3")
    assert (e1, raw, e3) in cleaned
    assert report.predicates_uncontrolled == 1
    assert report.uncontrolled_head == [("ist", 1)]


# --------------------------------------------------------------- entity dedup

def test_exact_label_duplicates_merge_and_sum_mentions(tmp_path: Path) -> None:
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/doc/d2> a kg:Document ; kg:sourceUri "s3://b/y.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Schweiz" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 3 .\n'
        '<https://example.org/kg/entity/e2> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 5 .\n'
        '<https://example.org/kg/doc/d1> kg:mentions <https://example.org/kg/entity/e1> .\n'
        '<https://example.org/kg/doc/d2> kg:mentions <https://example.org/kg/entity/e2> .\n'
    )
    cleaned, report = clean_graph(_graph(body), _vocabulary(tmp_path))
    e1, e2 = rdflib.URIRef(f"{KG}entity/e1"), rdflib.URIRef(f"{KG}entity/e2")
    entities = set(cleaned.subjects(rdflib.RDF.type, rdflib.URIRef(f"{KG}Entity")))
    assert entities == {e1}
    assert int(next(cleaned.objects(e1, rdflib.URIRef(f"{KG}mentionCount")))) == 8
    doc2 = rdflib.URIRef(f"{KG}doc/d2")
    assert (doc2, rdflib.URIRef(f"{KG}mentions"), e1) in cleaned
    assert report.label_groups_merged == 1
    assert report.entities_before == 2
    assert report.entities_after == 1


def test_alias_duplicates_merge_via_glossary_resolution(tmp_path: Path) -> None:
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "BFE" .\n'
        '<https://example.org/kg/label/l2> a kg:Label ; kg:labelText "Bundesamt für Energie" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 2 .\n'
        '<https://example.org/kg/entity/e2> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l2> ; kg:mentionCount 4 .\n'
    )
    cleaned, report = clean_graph(_graph(body), _vocabulary(tmp_path))
    entities = set(cleaned.subjects(rdflib.RDF.type, rdflib.URIRef(f"{KG}Entity")))
    assert len(entities) == 1
    assert report.alias_groups_merged == [("bfe", 2)]


def test_contested_or_unresolved_labels_do_not_merge(tmp_path: Path) -> None:
    vocab = _vocabulary(
        tmp_path,
        contested_aliases={"bfe": {"senses": ["bfe"]}},
    )
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "BFE" .\n'
        '<https://example.org/kg/label/l2> a kg:Label ; kg:labelText "Bundesamt für Energie" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 2 .\n'
        '<https://example.org/kg/entity/e2> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l2> ; kg:mentionCount 4 .\n'
    )
    cleaned, report = clean_graph(_graph(body), vocab)
    entities = set(cleaned.subjects(rdflib.RDF.type, rdflib.URIRef(f"{KG}Entity")))
    assert len(entities) == 2
    assert report.alias_groups_merged == []


# --------------------------------------------------------------- zero-degree pruning

def test_unmentioned_and_unconnected_entity_is_dropped(tmp_path: Path) -> None:
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Orphan" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 0 .\n'
    )
    cleaned, report = clean_graph(_graph(body), _vocabulary(tmp_path))
    e1 = rdflib.URIRef(f"{KG}entity/e1")
    assert (e1, None, None) not in cleaned
    assert report.entities_dropped == 1
    assert report.entities_flagged == []


def test_zero_mention_but_connected_entity_is_flagged_not_dropped(tmp_path: Path) -> None:
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Silent" .\n'
        '<https://example.org/kg/label/l2> a kg:Label ; kg:labelText "Loud" .\n'
        '<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l1> ; kg:mentionCount 0 .\n'
        '<https://example.org/kg/entity/e2> a kg:Entity ; kg:hasLabelForm '
        '<https://example.org/kg/label/l2> ; kg:mentionCount 1 .\n'
        '<https://example.org/kg/assertion/a1> a kg:Assertion ;\n'
        '    rdf:subject <https://example.org/kg/entity/e2> ;\n'
        '    rdf:predicate <https://example.org/kg/relprop/erwahnt> ;\n'
        '    rdf:object <https://example.org/kg/entity/e1> .\n'
    )
    cleaned, report = clean_graph(_graph(body), _vocabulary(tmp_path))
    e1 = rdflib.URIRef(f"{KG}entity/e1")
    assert (e1, rdflib.RDF.type, rdflib.URIRef(f"{KG}Entity")) in cleaned
    assert report.entities_dropped == 0
    assert report.entities_flagged == [str(e1)]
