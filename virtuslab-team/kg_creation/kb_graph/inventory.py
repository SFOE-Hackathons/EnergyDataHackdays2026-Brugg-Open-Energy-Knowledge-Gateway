"""Cheap, metadata-only S3 listing — always run before any download."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str
    last_modified: str
    extension: str  # lowercase, no leading dot; "" if none

    @classmethod
    def from_s3_object(cls, obj: dict) -> "ObjectInfo":
        key = obj["Key"]
        ext = PurePosixPath(key).suffix.lstrip(".").lower()
        return cls(
            key=key,
            size=obj["Size"],
            etag=obj["ETag"].strip('"'),
            last_modified=obj["LastModified"].isoformat(),
            extension=ext,
        )


def list_objects(s3, bucket: str, prefix: str = "") -> list[ObjectInfo]:
    objects: list[ObjectInfo] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            # Skip Bedrock KB sidecar metadata files from the primary inventory —
            # they're fetched per-document, not treated as documents themselves.
            if obj["Key"].endswith(".metadata.json"):
                continue
            objects.append(ObjectInfo.from_s3_object(obj))
    return objects


def summarize_by_extension(objects: list[ObjectInfo]) -> dict[str, dict[str, int]]:
    """{"pdf": {"count": N, "bytes": B}, "md": {...}, ...}"""
    summary: dict[str, dict[str, int]] = {}
    for obj in objects:
        bucket = summary.setdefault(obj.extension or "(none)", {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += obj.size
    return summary


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
