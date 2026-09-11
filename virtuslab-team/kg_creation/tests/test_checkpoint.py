from datetime import datetime, timezone

import pytest

from kb_graph import checkpoint
from kb_graph.documents import DocumentRecord
from kb_graph.entity_extraction import ExtractedGraph
from kb_graph.inventory import ObjectInfo

AT = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path):
    return checkpoint.Paths.under(tmp_path / "checkpoint")


def _doc(key, etag="e1", text="body"):
    return DocumentRecord(
        source_uri=f"s3://b/{key}",
        content_type="application/pdf",
        size_bytes=10,
        text=text,
        properties={"title": key},
        etag=etag,
    )


def _obj(key, etag="e1"):
    return ObjectInfo(key=key, size=10, etag=etag, last_modified="t", extension="pdf")


def test_document_round_trips(paths):
    doc = _doc("a.pdf")
    checkpoint.append_document(paths, doc)
    loaded = checkpoint.load_documents(paths)
    assert list(loaded) == [doc.id]
    assert loaded[doc.id] == doc


def test_extraction_round_trips(paths):
    g = ExtractedGraph(
        doc_id="doc1",
        entities={"Axpo"},
        relations={("Axpo", "operates", "Grid")},
        model="openai/gpt-4o",
        extracted_at=AT,
        mention_counts={"Axpo": 4},
    )
    checkpoint.append_extraction(paths, g)
    loaded = checkpoint.load_extractions(paths)["doc1"]
    assert loaded == g


def test_later_record_supersedes_earlier(paths):
    checkpoint.append_document(paths, _doc("a.pdf", etag="old", text="v1"))
    checkpoint.append_document(paths, _doc("a.pdf", etag="new", text="v2"))
    loaded = list(checkpoint.load_documents(paths).values())
    assert len(loaded) == 1
    assert loaded[0].text == "v2" and loaded[0].etag == "new"


def test_truncated_final_line_is_skipped_not_fatal(paths):
    # What a process killed mid-write leaves behind.
    checkpoint.append_document(paths, _doc("a.pdf"))
    with paths.documents.open("a", encoding="utf-8") as fh:
        fh.write('{"source_uri": "s3://b/b.pdf", "content_ty')
    loaded = checkpoint.load_documents(paths)
    assert len(loaded) == 1
    assert loaded[_doc("a.pdf").id].text == "body"


def test_missing_files_load_empty(paths):
    assert checkpoint.load_documents(paths) == {}
    assert checkpoint.load_extractions(paths) == {}


def test_pending_skips_checkpointed_objects(paths):
    checkpoint.append_document(paths, _doc("a.pdf", etag="e1"))
    docs = checkpoint.load_documents(paths)
    pending = checkpoint.pending_objects([_obj("a.pdf", "e1"), _obj("b.pdf", "e9")], "b", docs)
    assert [o.key for o in pending] == ["b.pdf"]


def test_pending_reingests_when_etag_changed(paths):
    checkpoint.append_document(paths, _doc("a.pdf", etag="old"))
    docs = checkpoint.load_documents(paths)
    pending = checkpoint.pending_objects([_obj("a.pdf", "new")], "b", docs)
    assert [o.key for o in pending] == ["a.pdf"]


def test_pending_treats_etagless_record_as_done(paths):
    doc = _doc("a.pdf")
    object.__setattr__(doc, "etag", None)
    checkpoint.append_document(paths, doc)
    docs = checkpoint.load_documents(paths)
    assert checkpoint.pending_objects([_obj("a.pdf", "whatever")], "b", docs) == []


def test_interrupted_run_resumes_and_loses_nothing(paths):
    """The scenario this whole module exists for: 10 files, killed after 6."""
    objects = [_obj(f"r{i}.pdf", etag=f"e{i}") for i in range(10)]

    # Run 1 dies after 6 documents.
    for obj in objects[:6]:
        checkpoint.append_document(paths, _doc(obj.key, etag=obj.etag))

    # Run 2 sees only the remaining 4 as pending...
    docs = checkpoint.load_documents(paths)
    pending = checkpoint.pending_objects(objects, "b", docs)
    assert [o.key for o in pending] == [f"r{i}.pdf" for i in range(6, 10)]

    for obj in pending:
        checkpoint.append_document(paths, _doc(obj.key, etag=obj.etag))

    # ...and the checkpoint now holds all 10, not just the last 4.
    assert len(checkpoint.load_documents(paths)) == 10
    assert checkpoint.pending_objects(objects, "b", checkpoint.load_documents(paths)) == []


def test_extraction_progress_survives_interruption(paths):
    for i in range(3):
        checkpoint.append_extraction(
            paths,
            ExtractedGraph(f"doc{i}", {f"E{i}"}, set(), "m", AT, {f"E{i}": 1}),
        )
    done = checkpoint.load_extractions(paths)
    assert set(done) == {"doc0", "doc1", "doc2"}
    # A resumed build re-extracts only what is missing.
    all_ids = [f"doc{i}" for i in range(5)]
    assert [i for i in all_ids if i not in done] == ["doc3", "doc4"]


def test_append_after_truncated_line_does_not_lose_the_new_record(paths):
    """A kill mid-write leaves a newline-less fragment. Appending onto it must
    not fuse the fragment and the next record into one unparseable line."""
    checkpoint.append_document(paths, _doc("a.pdf"))
    with paths.documents.open("a", encoding="utf-8") as fh:
        fh.write('{"source_uri": "s3://b/partial.pdf", "content_ty')  # no newline

    checkpoint.append_document(paths, _doc("c.pdf", etag="e3"))

    loaded = checkpoint.load_documents(paths)
    keys = sorted(d.source_uri for d in loaded.values())
    assert keys == ["s3://b/a.pdf", "s3://b/c.pdf"]  # fragment dropped, both records kept
