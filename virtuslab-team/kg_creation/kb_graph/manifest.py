"""Resumability cache: which objects (by ETag) have already been ingested.

Pure logic, no AWS/filesystem side effects beyond the two explicit
load/save functions — kept separate so diffing can be unit tested without
mocking S3.
"""
from __future__ import annotations

import json
from pathlib import Path

from .inventory import ObjectInfo

Manifest = dict[str, str]  # key -> ETag of the version already processed


def load_manifest(path: Path) -> Manifest:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def pending_objects(objects: list[ObjectInfo], manifest: Manifest) -> list[ObjectInfo]:
    """Objects that are new or whose ETag changed since the manifest was written."""
    return [obj for obj in objects if manifest.get(obj.key) != obj.etag]


def mark_processed(manifest: Manifest, obj: ObjectInfo) -> Manifest:
    return {**manifest, obj.key: obj.etag}
