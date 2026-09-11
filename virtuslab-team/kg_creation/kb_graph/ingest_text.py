"""Markdown/txt/json ingestion — small, always downloaded and parsed in full."""
from __future__ import annotations

import json

import frontmatter

from .documents import DocumentRecord, fetch_sidecar_metadata
from .inventory import ObjectInfo

TEXT_EXTENSIONS = {"md", "markdown", "txt", "json"}

_CONTENT_TYPES = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "json": "application/json",
}


def ingest_text_object(s3, bucket: str, obj: ObjectInfo) -> DocumentRecord:
    body = s3.get_object(Bucket=bucket, Key=obj.key)["Body"].read()
    properties = fetch_sidecar_metadata(s3, bucket, obj.key)

    if obj.extension in ("md", "markdown"):
        post = frontmatter.loads(body.decode("utf-8", errors="replace"))
        properties = {**{k: str(v) for k, v in post.metadata.items()}, **properties}
        text = post.content
    elif obj.extension == "json":
        text = body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                properties = {**{k: str(v) for k, v in parsed.items() if not isinstance(v, (dict, list))}, **properties}
        except json.JSONDecodeError:
            pass
    else:
        text = body.decode("utf-8", errors="replace")

    source_uri = f"s3://{bucket}/{obj.key}"
    return DocumentRecord(
        source_uri=source_uri,
        content_type=_CONTENT_TYPES.get(obj.extension, "text/plain"),
        size_bytes=obj.size,
        text=text,
        properties=properties,
        etag=obj.etag,
    )
