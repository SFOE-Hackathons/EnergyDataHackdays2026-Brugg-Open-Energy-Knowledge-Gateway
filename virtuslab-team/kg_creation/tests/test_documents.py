import pytest

from kb_graph.documents import cap_text, check_disk_headroom


def test_cap_text_under_limit_not_truncated():
    text, truncated = cap_text("hello", 100)
    assert text == "hello"
    assert truncated is False


def test_cap_text_over_limit_truncated():
    text, truncated = cap_text("hello world", 5)
    assert text == "hello"
    assert truncated is True


def test_check_disk_headroom_raises_when_insufficient(tmp_path, monkeypatch):
    import shutil

    class FakeUsage:
        free = 100

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: FakeUsage())
    with pytest.raises(RuntimeError):
        check_disk_headroom(tmp_path, largest_object_bytes=1000, concurrency=3)


def test_check_disk_headroom_passes_when_sufficient(tmp_path, monkeypatch):
    import shutil

    class FakeUsage:
        free = 10**12

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: FakeUsage())
    check_disk_headroom(tmp_path, largest_object_bytes=1000, concurrency=3)  # no raise
