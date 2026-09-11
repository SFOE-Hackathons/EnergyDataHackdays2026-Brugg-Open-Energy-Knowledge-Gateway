"""Append-only checkpoint of everything a build has produced, so an
interrupted build resumes instead of starting over.

Two JSONL files under the checkpoint directory:

- `documents.jsonl`   — one `DocumentRecord` per line, appended the moment
  that object is ingested. Holds the extracted text, so resuming never
  re-downloads from S3.
- `extractions.jsonl` — one `ExtractedGraph` per line, appended the moment a
  document's entity/relation extraction succeeds. This is the expensive
  artifact: ~54 LLM calls per document, so losing it is what makes an
  interrupted build painful.

Why append-only JSONL rather than a single JSON document: a build that is
killed mid-write must not corrupt what was already saved. Appending one
self-contained line per record means a partial write damages only the final
line, and `load_*` skips a malformed trailing line with a warning. Each
append is flushed and fsynced, so a record that the log says was saved
really is on disk.

Replay semantics: files are read in order and keyed by document id, so a
later record for the same document supersedes an earlier one. Re-extracting
a document appends a new line rather than rewriting history.

The graph is always built from the *whole* checkpoint, never from just the
current run's results. That is what makes `build` idempotent: `build_graph`
writes a fresh graph every time, so feeding it only the newly-processed
documents would silently drop everything ingested by earlier runs.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .documents import DocumentRecord
from .entity_extraction import ExtractedGraph
from .inventory import ObjectInfo

logger = logging.getLogger(__name__)

DOCUMENTS_FILE = "documents.jsonl"
EXTRACTIONS_FILE = "extractions.jsonl"


@dataclass(frozen=True)
class Paths:
    documents: Path
    extractions: Path

    @classmethod
    def under(cls, directory: Path) -> "Paths":
        return cls(directory / DOCUMENTS_FILE, directory / EXTRACTIONS_FILE)


def _append(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # A run killed mid-write can leave a final line with no trailing newline.
    # Appending straight onto it would fuse that fragment and this record into
    # one unparseable line, losing *both* — the fragment was already lost, but
    # silently taking a good record with it is the dangerous part. Terminate
    # the orphan before writing.
    needs_newline = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            needs_newline = fh.read(1) != b"\n"

    with path.open("a", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Expected on the last line if a previous run was killed
                # mid-write. Anything earlier means real corruption.
                logger.warning(
                    "%s:%d is not valid JSON — skipped (a truncated final line "
                    "is normal after an interrupted run)",
                    path,
                    lineno,
                )
    return records


def append_document(paths: Paths, doc: DocumentRecord) -> None:
    _append(
        paths.documents,
        {
            "source_uri": doc.source_uri,
            "content_type": doc.content_type,
            "size_bytes": doc.size_bytes,
            "text": doc.text,
            "text_truncated": doc.text_truncated,
            "properties": doc.properties,
            "etag": doc.etag,
        },
    )


def append_extraction(paths: Paths, graph: ExtractedGraph) -> None:
    _append(
        paths.extractions,
        {
            "doc_id": graph.doc_id,
            "entities": sorted(graph.entities),
            "relations": [list(r) for r in sorted(graph.relations)],
            "model": graph.model,
            "extracted_at": graph.extracted_at.isoformat() if graph.extracted_at else None,
            "mention_counts": graph.mention_counts,
        },
    )


def load_documents(paths: Paths) -> dict[str, DocumentRecord]:
    """Document id -> record, last line for an id winning."""
    docs: dict[str, DocumentRecord] = {}
    for raw in _read_lines(paths.documents):
        doc = DocumentRecord(
            source_uri=raw["source_uri"],
            content_type=raw["content_type"],
            size_bytes=raw["size_bytes"],
            text=raw.get("text"),
            text_truncated=raw.get("text_truncated", False),
            properties=raw.get("properties") or {},
            etag=raw.get("etag"),
        )
        docs[doc.id] = doc
    return docs


def load_extractions(paths: Paths) -> dict[str, ExtractedGraph]:
    """Document id -> extraction, last line for an id winning."""
    graphs: dict[str, ExtractedGraph] = {}
    for raw in _read_lines(paths.extractions):
        at = raw.get("extracted_at")
        graphs[raw["doc_id"]] = ExtractedGraph(
            doc_id=raw["doc_id"],
            entities=set(raw.get("entities") or []),
            relations={tuple(r) for r in raw.get("relations") or []},
            model=raw.get("model") or "",
            extracted_at=datetime.fromisoformat(at) if at else None,
            mention_counts=raw.get("mention_counts") or {},
        )
    return graphs


def pending_objects(
    objects: list[ObjectInfo], bucket: str, docs: dict[str, DocumentRecord]
) -> list[ObjectInfo]:
    """Objects with no checkpointed document at their current ETag.

    The checkpoint, not the manifest, is the skip oracle: it is the thing that
    actually holds the ingested text, so "already done" and "recoverable
    without re-downloading" mean the same thing. An object whose ETag changed
    is re-ingested; its new record supersedes the old one on replay.

    A record checkpointed without an ETag (ingested before ETags were
    recorded) counts as done — re-downloading it would cost S3 transfer to
    learn nothing.
    """
    by_uri = {doc.source_uri: doc for doc in docs.values()}
    pending: list[ObjectInfo] = []
    for obj in objects:
        doc = by_uri.get(f"s3://{bucket}/{obj.key}")
        if doc is None or (doc.etag is not None and doc.etag != obj.etag):
            pending.append(obj)
    return pending
