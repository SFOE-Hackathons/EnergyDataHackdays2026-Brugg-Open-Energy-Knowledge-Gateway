"""Tests for the glossary overlay.

The contract is "pure addition": the source graph is read and never written, and every
existing id survives byte-identically because another consumer pins those hashes. The
first two tests below are the ones that matter; everything else is detail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

rdflib = pytest.importorskip("rdflib")
pytest.importorskip("energy_vocab")

from kb_graph.annotate import (  # noqa: E402
    annotate,
    annotate_graph,
    detect_base_uri,
    is_glossary_candidate,
)

KG = "https://example.org/kg/"
TERM_BASE = "https://virtuslab.github.io/energy-domain-expertise/term/"
VOCAB_BASE = "https://virtuslab.github.io/energy-domain-expertise/vocab#"


def _vocabulary(tmp_path: Path, **overrides) -> Path:
    """A three-term vocabulary, so these tests do not move when the glossary does."""
    data = {
        "schema_version": "1.0",
        "content_hash": "sha256:" + "ab" * 32,
        "term_iri_base": TERM_BASE,
        "vocab_iri_base": VOCAB_BASE,
        "normalization": {"rules": [], "stop_suffixes": []},
        "counts": {},
        "terms": {
            "winter-reserve": {
                "term": "Winter reserve", "type": "regulation",
                "subdomain": "markets", "file": "m.md#winter-reserve", "gloss": "",
                "labels": [
                    {"text": "Winter reserve", "lang": "en", "role": "preferred"},
                    {"text": "Winterreserve", "lang": "de", "role": "alias"},
                ],
                "related": [], "see_also": [], "broader": [], "narrower": [],
            },
            "energy": {
                "term": "Energy", "type": "quantity",
                "subdomain": "quantities", "file": "q.md#energy", "gloss": "",
                "labels": [{"text": "Energy", "lang": "en", "role": "preferred", "generic": True}],
                "related": [], "see_also": [], "broader": [], "narrower": [],
            },
            "charging-curve": {
                "term": "Charging curve", "type": "concept",
                "subdomain": "e-mobility", "file": "e.md#charging-curve", "gloss": "",
                "labels": [{"text": "Charging curve", "lang": "en", "role": "preferred"}],
                "related": [], "see_also": [], "broader": [], "narrower": [],
            },
        },
        "alias_index": {
            "winter reserve": "winter-reserve",
            "winterreserve": "winter-reserve",
            "energy": "energy",
        },
        "contested_aliases": {},
        "generic_keys": ["energy"],
        "sources": {}, "source_alias_index": {}, "contested_source_aliases": {},
        "predicates": {
            "part-of": {
                "inverse": "includes", "symmetric": False,
                "definition": "The subject is a constituent of the object.",
                "maps_from": ["is_part_of", "teil_von"],
            }
        },
        "predicate_map": {"is_part_of": "part-of", "teil_von": "part-of", "part_of": "part-of"},
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


BASIC = """
<https://example.org/kg/doc/d1> a kg:Document ;
    kg:sourceUri "s3://b/2025-12-15_energiestrategie-bericht.pdf" .
<https://example.org/kg/label/l1> a kg:Label ; kg:labelText "Winterreserve" .
<https://example.org/kg/label/l2> a kg:Label ; kg:labelText "Energy" .
<https://example.org/kg/label/l3> a kg:Label ; kg:labelText "Kleinwasserkraft" .
<https://example.org/kg/entity/e1> a kg:Entity ; kg:hasLabelForm <https://example.org/kg/label/l1> ; kg:mentionCount 7 .
<https://example.org/kg/entity/e2> a kg:Entity ; kg:hasLabelForm <https://example.org/kg/label/l3> ; kg:mentionCount 40 .
<https://example.org/kg/assertion/a1> rdf:predicate <https://example.org/kg/relprop/is_part_of> .
<https://example.org/kg/assertion/a2> rdf:predicate <https://example.org/kg/relprop/Teil_von> .
<https://example.org/kg/assertion/a3> rdf:predicate <https://example.org/kg/relprop/zeigt_unbekanntes> .
"""


# --------------------------------------------------------------- the contract

def test_source_graph_is_untouched(tmp_path: Path) -> None:
    """The whole design rests on this: annotating must not write the source."""
    graph_path = tmp_path / "graph.ttl"
    graph_path.write_text(PREFIXES + BASIC, encoding="utf-8")
    before = graph_path.read_bytes()
    annotate(graph_path, _vocabulary(tmp_path), tmp_path / "overlay.ttl")
    assert graph_path.read_bytes() == before


def test_union_keeps_every_original_triple(tmp_path: Path) -> None:
    graph = _graph(BASIC)
    overlay, _ = annotate_graph(graph, _vocabulary(tmp_path))
    union = graph + overlay
    assert set(graph) <= set(union)
    assert len(union) > len(graph)


def test_overlay_is_deterministic(tmp_path: Path) -> None:
    vocab = _vocabulary(tmp_path)
    first, _ = annotate_graph(_graph(BASIC), vocab)
    second, _ = annotate_graph(_graph(BASIC), vocab)
    assert set(first) == set(second)


# --------------------------------------------------------------- annotation

def test_label_and_entity_both_carry_term_and_type(tmp_path: Path) -> None:
    overlay, report = annotate_graph(_graph(BASIC), _vocabulary(tmp_path))
    label, entity = rdflib.URIRef(f"{KG}label/l1"), rdflib.URIRef(f"{KG}entity/e1")
    term = rdflib.URIRef(f"{TERM_BASE}winter-reserve")
    assert (label, rdflib.URIRef(f"{KG}canonicalTerm"), term) in overlay
    assert (entity, rdflib.URIRef(f"{KG}canonicalTerm"), term) in overlay
    assert (entity, rdflib.URIRef(f"{KG}termType"), rdflib.URIRef(f"{VOCAB_BASE}regulation")) in overlay
    assert report.labels_linked == 1
    assert report.entities_annotated == 1


def test_generic_labels_get_no_triple(tmp_path: Path) -> None:
    """'Energy' resolves, but is too common in prose to identify the term."""
    overlay, report = annotate_graph(_graph(BASIC), _vocabulary(tmp_path))
    label = rdflib.URIRef(f"{KG}label/l2")
    assert list(overlay.predicate_objects(label)) == []
    assert report.labels_generic == 1


def test_contested_labels_record_the_dispute_and_make_no_guess(tmp_path: Path) -> None:
    vocab = _vocabulary(
        tmp_path,
        contested_aliases={"ccs": {"senses": ["winter-reserve", "charging-curve"]}},
    )
    body = (
        '<https://example.org/kg/doc/d1> a kg:Document ; kg:sourceUri "s3://b/x.pdf" .\n'
        '<https://example.org/kg/label/l9> a kg:Label ; kg:labelText "CCS" .'
    )
    overlay, report = annotate_graph(_graph(body), vocab)
    label = rdflib.URIRef(f"{KG}label/l9")
    contested = set(overlay.objects(label, rdflib.URIRef(f"{KG}contestedTerm")))
    assert len(contested) == 2
    assert list(overlay.objects(label, rdflib.URIRef(f"{KG}canonicalTerm"))) == []
    assert report.labels_contested == 1


def test_predicates_are_annotated_not_rewritten(tmp_path: Path) -> None:
    graph = _graph(BASIC)
    overlay, report = annotate_graph(graph, _vocabulary(tmp_path))
    canonical = rdflib.URIRef(f"{KG}rel/part-of")
    for spelling in ("is_part_of", "Teil_von"):
        raw = rdflib.URIRef(f"{KG}relprop/{spelling}")
        assert (raw, rdflib.URIRef(f"{KG}canonicalPredicate"), canonical) in overlay
        # the original assertion still uses its own IRI
        assert (None, rdflib.RDF.predicate, raw) in graph
    unmapped = rdflib.URIRef(f"{KG}relprop/zeigt_unbekanntes")
    assert list(overlay.objects(unmapped, rdflib.URIRef(f"{KG}canonicalPredicate"))) == []
    assert report.predicates_annotated == 2
    assert report.predicates_total == 3


def test_controlled_predicate_carries_its_definition(tmp_path: Path) -> None:
    overlay, _ = annotate_graph(_graph(BASIC), _vocabulary(tmp_path))
    node = rdflib.URIRef(f"{KG}rel/part-of")
    assert (node, rdflib.URIRef(f"{KG}inversePredicate"), rdflib.URIRef(f"{KG}rel/includes")) in overlay
    assert list(overlay.objects(node, rdflib.URIRef(f"{KG}definition")))


def test_documents_get_a_date_and_title_from_the_key(tmp_path: Path) -> None:
    overlay, report = annotate_graph(_graph(BASIC), _vocabulary(tmp_path))
    doc = rdflib.URIRef(f"{KG}doc/d1")
    assert str(next(overlay.objects(doc, rdflib.URIRef(f"{KG}publicationDate")))) == "2025-12-15"
    assert "energiestrategie" in str(next(overlay.objects(doc, rdflib.URIRef(f"{KG}title"))))
    assert report.documents_dated == 1


def test_unresolved_work_queue_is_ranked_by_mentions(tmp_path: Path) -> None:
    """Every :Label node occurs once, so ranking by node count ranks nothing."""
    _, report = annotate_graph(_graph(BASIC), _vocabulary(tmp_path))
    assert report.unresolved_head[0] == ("Kleinwasserkraft", 40)


# --------------------------------------------------------------- helpers

def test_base_uri_is_read_from_the_graph_not_assumed(tmp_path: Path) -> None:
    assert detect_base_uri(_graph(BASIC)) == KG


def test_base_uri_failure_is_loud() -> None:
    with pytest.raises(ValueError, match="sourceUri"):
        detect_base_uri(_graph('<https://example.org/kg/x/1> a kg:Thing .'))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Kleinwasserkraft", True),
        ("security of supply", True),
        ("10 605 GWh", False),
        ("0:00", False),
        ("-15%", False),
        ("1% of Grundkapital", False),
        ("2024", False),
        ("aa", False),
        ("a b c d e f g h i", False),
    ],
)
def test_glossary_candidate_filter(text: str, expected: bool) -> None:
    assert is_glossary_candidate(text) is expected
