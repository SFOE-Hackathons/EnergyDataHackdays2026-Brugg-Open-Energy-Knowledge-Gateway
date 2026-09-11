from kb_graph.inventory import ObjectInfo
from kb_graph.manifest import mark_processed, pending_objects


def _obj(key: str, etag: str) -> ObjectInfo:
    return ObjectInfo(key=key, size=100, etag=etag, last_modified="2026-01-01T00:00:00", extension="pdf")


def test_pending_objects_includes_new_keys():
    objects = [_obj("a.pdf", "etag-a")]
    assert pending_objects(objects, {}) == objects


def test_pending_objects_excludes_unchanged_keys():
    objects = [_obj("a.pdf", "etag-a")]
    manifest = {"a.pdf": "etag-a"}
    assert pending_objects(objects, manifest) == []


def test_pending_objects_includes_changed_etag():
    objects = [_obj("a.pdf", "etag-a2")]
    manifest = {"a.pdf": "etag-a1"}
    assert pending_objects(objects, manifest) == objects


def test_mark_processed_does_not_mutate_input():
    manifest = {"a.pdf": "old"}
    updated = mark_processed(manifest, _obj("a.pdf", "new"))
    assert manifest == {"a.pdf": "old"}
    assert updated == {"a.pdf": "new"}
