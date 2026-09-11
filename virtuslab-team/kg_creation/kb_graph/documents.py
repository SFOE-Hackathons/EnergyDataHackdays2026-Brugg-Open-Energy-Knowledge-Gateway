"""Shared document model + generic (schema-agnostic) metadata handling."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from botocore.exceptions import ClientError


@dataclass
class DocumentRecord:
    source_uri: str
    content_type: str
    size_bytes: int
    text: str | None = None
    text_truncated: bool = False
    properties: dict[str, str] = field(default_factory=dict)  # generic key/value, no fixed schema
    # S3 ETag of the object this was ingested from. Carried so the checkpoint
    # can decide what still needs downloading without consulting the manifest.
    etag: str | None = None

    @property
    def id(self) -> str:
        return hashlib.sha256(self.source_uri.encode()).hexdigest()[:16]


def fetch_sidecar_metadata(s3, bucket: str, key: str) -> dict[str, str]:
    """Bedrock KB convention: `<key>.metadata.json` next to the source object.

    Flattens the conventional {"metadataAttributes": {k: {"value": {"type": ..., ...}}}}
    shape to plain {k: str(v)} — schema of the values is whatever the source
    connector put there, not assumed here.
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{key}.metadata.json")
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise
    try:
        raw = json.loads(obj["Body"].read())
    except json.JSONDecodeError:
        return {}

    attrs = raw.get("metadataAttributes", raw if isinstance(raw, dict) else {})
    flat: dict[str, str] = {}
    for k, v in attrs.items():
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
            if isinstance(v, dict):
                v = v.get("stringValue", v.get("numberValue", v.get("booleanValue", v)))
        flat[k] = str(v)
    return flat


def cap_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def check_disk_headroom(scratch_dir: Path, largest_object_bytes: int, concurrency: int) -> None:
    """Raise if free space is less than the worst-case simultaneous download size.

    Worst case is bounded by concurrency, never by total corpus size — see
    the full-ingest plan's scale note.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    required = largest_object_bytes * concurrency
    free = shutil.disk_usage(scratch_dir).free
    if free < required:
        raise RuntimeError(
            f"Insufficient disk headroom at {scratch_dir}: {free} bytes free, "
            f"need ~{required} bytes ({largest_object_bytes} bytes x {concurrency} concurrency)."
        )
