from kb_graph.inventory import ObjectInfo, human_bytes, summarize_by_extension


def _obj(key: str, size: int) -> ObjectInfo:
    ext = key.rsplit(".", 1)[-1] if "." in key else ""
    return ObjectInfo(key=key, size=size, etag="e", last_modified="2026-01-01T00:00:00", extension=ext)


def test_summarize_by_extension_groups_and_sums():
    objects = [_obj("a.pdf", 1000), _obj("b.pdf", 2000), _obj("c.md", 50)]
    summary = summarize_by_extension(objects)
    assert summary["pdf"] == {"count": 2, "bytes": 3000}
    assert summary["md"] == {"count": 1, "bytes": 50}


def test_human_bytes_scales_units():
    assert human_bytes(500) == "500.0B"
    assert human_bytes(1024) == "1.0KB"
    assert human_bytes(100 * 1024**3) == "100.0GB"
