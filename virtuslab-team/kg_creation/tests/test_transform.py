from datetime import datetime, timezone

from kb_graph.entity_extraction import ExtractedGraph, assertion_id, entity_id, label_id
from kb_graph.graph_templates import build_templates
from kb_graph.documents import DocumentRecord
from kb_graph.transform import (
    documents_to_dataframe,
    entities_to_dataframe,
    labels_to_dataframe,
    relations_to_dataframe,
)

BASE_URI = "https://example.org/kg/"
AT = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _graph(doc_id, entities, relations=frozenset()):
    return ExtractedGraph(
        doc_id=doc_id,
        entities=set(entities),
        relations=set(relations),
        model="openai/gpt-4o",
        extracted_at=AT,
    )


def test_entity_id_is_case_and_whitespace_insensitive_within_a_document():
    assert entity_id("Josh", "doc1") == entity_id(" josh ", "doc1")
    assert entity_id("Josh", "doc1") != entity_id("Linda", "doc1")


def test_entity_id_does_not_merge_the_same_label_across_documents():
    # The homonym fix: two documents naming "Axpo" are two distinct nodes.
    assert entity_id("Axpo", "doc1") != entity_id("Axpo", "doc2")


def test_label_id_is_shared_across_documents():
    # ...but they still share one lexical grouping node.
    assert label_id("Axpo") == label_id(" axpo ")


def test_entities_to_dataframe_keeps_documents_separate():
    graphs = [_graph("doc1", {"Josh", "Linda"}), _graph("doc2", {"josh", "Andrew"})]
    df = entities_to_dataframe(graphs, BASE_URI)
    assert df.height == 4  # Josh/josh no longer collapse — different documents
    assert set(df["DocIri"]) == {f"{BASE_URI}doc/doc1", f"{BASE_URI}doc/doc2"}
    josh_rows = df.filter(df["Label"].str.to_lowercase() == "josh")
    assert josh_rows.height == 2
    # Distinct entity nodes, one shared label node.
    assert len(set(josh_rows["EntityIri"])) == 2
    assert len(set(josh_rows["LabelIri"])) == 1


def test_labels_to_dataframe_dedupes_across_documents():
    graphs = [_graph("doc1", {"Josh", "Linda"}), _graph("doc2", {"josh", "Andrew"})]
    df = labels_to_dataframe(graphs, BASE_URI)
    assert df.height == 3  # Josh/josh collapse into one label form
    assert f"{BASE_URI}label/{label_id('Josh')}" in set(df["LabelIri"])


def test_entities_cover_relation_endpoints_not_listed_as_entities():
    graphs = [_graph("doc1", {"Linda"}, {("Linda", "is mother of", "Josh")})]
    entities = entities_to_dataframe(graphs, BASE_URI)
    labels = labels_to_dataframe(graphs, BASE_URI)
    assert entities.height == 2  # Josh appears only as a relation object
    assert labels.height == 2


def test_every_posting_carries_the_document_edge():
    """The document -> entity edge is emitted from the entity row, so a
    posting without an edge is unrepresentable. Regression guard: it used to
    come from a separate table that iterated only `entities`, leaving every
    relation-only endpoint with no edge at all."""
    graphs = [_graph("doc1", {"Josh", "Linda"})]
    df = entities_to_dataframe(graphs, BASE_URI)
    assert df.height == 2
    assert df["DocIri"].null_count() == 0
    assert set(df["DocIri"]) == {f"{BASE_URI}doc/doc1"}
    assert set(df["EntityIri"]) == {
        f"{BASE_URI}entity/{entity_id('Josh', 'doc1')}",
        f"{BASE_URI}entity/{entity_id('Linda', 'doc1')}",
    }


def test_relation_only_endpoint_carries_its_own_document_edge():
    """Josh is named only as a relation object. He still gets a posting, and
    its edge points at the document he came from — never across documents."""
    graphs = [
        _graph("doc1", {"Linda"}, {("Linda", "is mother of", "Josh")}),
        _graph("doc2", {"Andrew"}),
    ]
    df = entities_to_dataframe(graphs, BASE_URI)
    assert df["DocIri"].null_count() == 0
    josh = df.filter(df["EntityIri"] == f"{BASE_URI}entity/{entity_id('Josh', 'doc1')}")
    assert josh.height == 1
    assert josh["DocIri"][0] == f"{BASE_URI}doc/doc1"


def test_relations_to_dataframe_builds_subject_predicate_object():
    graphs = [_graph("doc1", {"Linda", "Josh"}, {("Linda", "is mother of", "Josh")})]
    df = relations_to_dataframe(graphs, BASE_URI)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["SubjectIri"] == f"{BASE_URI}entity/{entity_id('Linda', 'doc1')}"
    assert row["ObjectIri"] == f"{BASE_URI}entity/{entity_id('Josh', 'doc1')}"
    assert row["PredicateIri"] == f"{BASE_URI}relprop/is_mother_of"


def test_relations_carry_provenance():
    graphs = [_graph("doc1", {"Linda", "Josh"}, {("Linda", "is mother of", "Josh")})]
    row = relations_to_dataframe(graphs, BASE_URI).row(0, named=True)
    assert row["DocIri"] == f"{BASE_URI}doc/doc1"
    assert row["ExtractionModel"] == "openai/gpt-4o"
    assert row["ExtractedAt"] == AT
    assert row["AssertionIri"] == (
        f"{BASE_URI}assertion/{assertion_id('doc1', 'Linda', 'is mother of', 'Josh')}"
    )


def test_same_edge_from_two_documents_yields_two_assertions():
    rel = {("Linda", "is mother of", "Josh")}
    graphs = [_graph("doc1", {"Linda", "Josh"}, rel), _graph("doc2", {"Linda", "Josh"}, rel)]
    df = relations_to_dataframe(graphs, BASE_URI)
    assert df.height == 2
    assert len(set(df["AssertionIri"])) == 2  # each keeps its own provenance


def test_relations_dataframe_is_typed_when_empty():
    # maplib binds an xsd:dateTime parameter by dtype — a Null column breaks it.
    df = relations_to_dataframe([_graph("doc1", {"Linda"})], BASE_URI)
    assert df.height == 0
    assert df.schema["ExtractedAt"].time_zone == "UTC"


def test_mention_count_is_case_insensitive_and_word_bounded():
    from kb_graph.entity_extraction import mention_count

    assert mention_count("Gas and gasification. GAS again, plus gas.", "gas") == 3
    assert mention_count("hydropower reserve and Hydropower Reserve", "hydropower reserve") == 2
    assert mention_count("anything", "") == 0
    assert mention_count("", "gas") == 0
    # A label with regex metacharacters must not blow up or over-match.
    assert mention_count("the 35,000 GWh target", "35,000 GWh") == 1


def test_entities_carry_mention_counts():
    g = ExtractedGraph(
        doc_id="doc1",
        entities={"Axpo"},
        relations=set(),
        model="m",
        extracted_at=AT,
        mention_counts={"Axpo": 7},
    )
    row = entities_to_dataframe([g], BASE_URI).row(0, named=True)
    assert row["MentionCount"] == 7


def test_missing_mention_count_defaults_to_zero():
    # Relation endpoints the entity pass never saw still get a posting.
    g = _graph("doc1", {"Linda"}, {("Linda", "knows", "Josh")})
    df = entities_to_dataframe([g], BASE_URI)
    josh = df.filter(df["Label"] == "Josh").row(0, named=True)
    assert josh["MentionCount"] == 0


def _doc(text, truncated=False):
    return DocumentRecord(
        source_uri="s3://b/a.pdf",
        content_type="application/pdf",
        size_bytes=2_892_295,
        text=text,
        text_truncated=truncated,
    )


def test_documents_dataframe_columns_match_the_template_parameters():
    """maplib binds template parameters to DataFrame columns by name, and
    rejects a parameter with no column (MissingParameterColumn) as well as a
    column with no parameter (ContainsIrrelevantColumns). Nothing else in the
    suite enforced that contract, so a one-sided edit to either file used to
    fail only at graph-build time."""
    params = {p.variable.name for p in build_templates(BASE_URI)["document"].parameters}
    assert set(documents_to_dataframe([_doc("body")], BASE_URI).columns) == params


def test_documents_dataframe_carries_no_document_body():
    """The body is not in the graph: Bedrock stores and chunks it, and the
    checkpoint keeps it for resume. It used to be 66% of every literal
    character here."""
    df = documents_to_dataframe([_doc("a long body " * 5000)], BASE_URI)
    assert "Text" not in df.columns
    assert not any("text" == c.lower() for c in df.columns)


def test_has_text_separates_no_match_from_never_extracted():
    """With the body gone, :hasText is the only thing telling a consumer that
    an absent term means absent, rather than an image-only PDF that yielded
    nothing. Guard both sides."""
    assert documents_to_dataframe([_doc("real body")], BASE_URI)["HasText"][0] is True
    assert documents_to_dataframe([_doc(None)], BASE_URI)["HasText"][0] is False
    assert documents_to_dataframe([_doc("   \n  ")], BASE_URI)["HasText"][0] is False


def test_text_truncated_passes_through():
    # PDF_TEXT_MAX_CHARS clipped the text — the index is partial for this doc.
    assert documents_to_dataframe([_doc("x", truncated=True)], BASE_URI)["TextTruncated"][0] is True
    assert documents_to_dataframe([_doc("x")], BASE_URI)["TextTruncated"][0] is False
