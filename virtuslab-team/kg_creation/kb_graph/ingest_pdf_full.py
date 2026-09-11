"""Full PDF ingestion: stream-download one at a time (bounded concurrency),
extract text, delete the temp file immediately, resumable via manifest.

This is the expensive path (see kg_creation_complex vs. kg_creation_simple) —
every safeguard here exists because the source corpus can be ~100GB of PDFs:
peak disk usage is bounded by concurrency (never by total corpus size), and
a run can be killed and resumed without re-downloading finished files.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .documents import DocumentRecord, cap_text, check_disk_headroom, fetch_sidecar_metadata
from .inventory import ObjectInfo
from .manifest import Manifest, mark_processed

logger = logging.getLogger(__name__)

MIN_CHARS_BEFORE_FALLBACK = 200  # below this, assume pypdf under-extracted and try pdfplumber


def extract_pdf_text(path: Path) -> str:
    text = _extract_with_pypdf(path)
    if len(text.strip()) >= MIN_CHARS_BEFORE_FALLBACK:
        return text
    fallback = _extract_with_pdfplumber(path)
    return fallback if len(fallback.strip()) > len(text.strip()) else text


def _extract_with_pypdf(path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        logger.exception("pypdf extraction failed for %s", path)
        return ""


def _extract_with_pdfplumber(path: Path) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        logger.exception("pdfplumber extraction failed for %s", path)
        return ""


def download_and_extract_one(
    s3, bucket: str, obj: ObjectInfo, scratch_dir: Path, text_max_chars: int
) -> DocumentRecord:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=scratch_dir, suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        s3.download_file(bucket, obj.key, str(tmp_path))
        raw_text = extract_pdf_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)  # never keep the PDF body on disk past extraction

    text, truncated = cap_text(raw_text, text_max_chars)
    properties = fetch_sidecar_metadata(s3, bucket, obj.key)
    if not text.strip():
        properties.setdefault("extractionWarning", "no text extracted — likely image-only PDF")

    return DocumentRecord(
        source_uri=f"s3://{bucket}/{obj.key}",
        content_type="application/pdf",
        size_bytes=obj.size,
        text=text,
        text_truncated=truncated,
        properties=properties,
        etag=obj.etag,
    )


def run_full_ingest(
    s3,
    bucket: str,
    objects: list[ObjectInfo],
    manifest: Manifest,
    scratch_dir: Path,
    text_max_chars: int,
    concurrency: int,
    on_document: Callable[[DocumentRecord], None] | None = None,
) -> tuple[list[DocumentRecord], Manifest]:
    """Download, extract and return one DocumentRecord per object.

    `on_document` is called with each record the moment it is extracted,
    before the object is marked processed — the hook the checkpoint uses to
    persist progress as it happens. It runs on the main thread (inside the
    `as_completed` loop), so it needs no locking, and if it raises, the object
    is deliberately left unmarked so the next run retries it.
    """
    if not objects:
        return [], manifest

    largest = max(obj.size for obj in objects)
    check_disk_headroom(scratch_dir, largest, concurrency)

    documents: list[DocumentRecord] = []
    manifest = dict(manifest)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(download_and_extract_one, s3, bucket, obj, scratch_dir, text_max_chars): obj
            for obj in objects
        }
        for future in as_completed(futures):
            obj = futures[future]
            try:
                document = future.result()
                if on_document is not None:
                    on_document(document)
                documents.append(document)
                manifest = mark_processed(manifest, obj)
            except Exception:
                logger.exception("Failed to ingest %s — left out of manifest, will retry next run", obj.key)

    return documents, manifest
