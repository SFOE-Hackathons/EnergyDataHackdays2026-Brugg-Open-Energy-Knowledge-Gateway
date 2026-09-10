"""Parsing for the machine-readable data embedded in chart/table transcriptions.

Some knowledge base chunks are vision transcriptions of a chart or table image
from a source PDF. Where the source figure carried numeric data, the
transcription includes a `<data>...</data>` block: a loose, comma-separated
dump of a header row followed by one row per (typically) year. Values read
off a bar/line by the transcribing model are given as ranges (`515-525`);
values that were printed on the figure as labels are given as exact numbers.
Blocks often end with free-text commentary that must not be mistaken for
data.

This module is pure and dependency-free (standard library only) so it can be
unit-tested without any of the MCP/network machinery.
"""

import re

_DATA_BLOCK_RE = re.compile(r"<data>(.*?)</data>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)

# A data row starts at a 4-digit year, optionally annotated in parentheses
# (e.g. "2022 (estimated)"), followed by a comma. Everything before the first
# such match is the header; everything between/after subsequent matches is a
# row's cells.
_ROW_START_RE = re.compile(r"\b(?:1[89]\d{2}|20\d{2})(?:\s*\([^)]*\))?\s*,")

# Free-text commentary trailing the data rows. Truncate the block here before
# splitting rows so prose is never mistaken for a row or a cell.
_PROSE_MARKER_RE = re.compile(r"\b(?:Note:|Estimated Data Ranges:|Source:|Sources:)")

_EXACT_RE = re.compile(r"-?\d+(?:\.\d+)?")
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)")

# The transcriber frequently emits single, exact-looking numbers that it read
# off a chart by eye, and discloses that only in the trailing prose ("These
# values are estimated based on visual interpretation of the area chart").
# Cell shape alone therefore cannot establish precision: without this check a
# whole series of eyeballed values is reported as exact, and a caller would
# present them as official figures. When the block says the numbers are
# estimated, that disclosure wins over the cell shapes.
_ESTIMATION_MARKER_RE = re.compile(
    r"estimated|estimates|estimation|approximate|visual interpretation|"
    r"cannot be determined|read off|geschätzt|Schätzung|ungefähr",
    re.IGNORECASE,
)

# Some notes assert the opposite - that the figures were printed on the chart -
# while still using the word "estimate" about something incidental, such as a
# projection mentioned in the caption. An explicit claim of exactness outranks
# a bare keyword hit, so it suppresses the downgrade above.
_EXACTNESS_CLAIM_RE = re.compile(
    r"exact figures|as labell?ed|data labels|printed on|abgelesen aus der Legende",
    re.IGNORECASE,
)


def extract_data_blocks(text: str) -> list[str]:
    """Return the inner content of every `<data>...</data>` block in text."""
    return [block.strip() for block in _DATA_BLOCK_RE.findall(text)]


def extract_title(text: str) -> str | None:
    """Return the inner content of a `<title>...</title>` block, if present."""
    match = _TITLE_RE.search(text)
    if not match:
        return None
    title = match.group(1).strip()
    return title or None


def _normalize_number(token: str) -> str:
    """Strip Swiss thousands apostrophes and surrounding whitespace."""
    return token.strip().replace("'", "")


def _parse_cell(raw: str) -> dict:
    """Classify one cell as an exact number, a range, or opaque text."""
    stripped = raw.strip()
    if not stripped or stripped == "-":
        return {"raw": raw, "value": None, "min": None, "max": None, "kind": "text"}

    normalized = _normalize_number(stripped)

    if _EXACT_RE.fullmatch(normalized):
        return {"raw": raw, "value": float(normalized), "min": None, "max": None, "kind": "exact"}

    range_match = _RANGE_RE.fullmatch(normalized)
    if range_match:
        return {
            "raw": raw,
            "value": None,
            "min": float(range_match.group(1)),
            "max": float(range_match.group(2)),
            "kind": "range",
        }

    return {"raw": raw, "value": None, "min": None, "max": None, "kind": "text"}


def _dedupe_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    deduped = []
    for column in columns:
        seen[column] = seen.get(column, 0) + 1
        deduped.append(column if seen[column] == 1 else f"{column} ({seen[column]})")
    return deduped


def _compute_precision(columns: list[str], rows: list[dict]) -> str:
    """Aggregate precision over all non-label cells.

    The first column is the row label (almost always "Year") and is excluded:
    a bare year is numerically "exact" by the cell-parsing rules but that
    doesn't make the chart's data precise - only the data columns do.
    """
    label_column = columns[0]
    kinds = [
        cell["kind"]
        for row in rows
        for column, cell in row.items()
        if column != label_column and cell["kind"] != "text"
    ]
    if not kinds:
        return "mixed"
    if all(kind == "exact" for kind in kinds):
        return "exact"
    if all(kind == "range" for kind in kinds):
        return "estimated"
    return "mixed"


def parse_data_block(block: str) -> dict | None:
    """Parse one `<data>` block's inner content into columns and rows.

    Returns None if the block cannot be parsed into at least one column and
    one row (e.g. no year-anchored row could be found at all).
    """
    prose_match = _PROSE_MARKER_RE.search(block)
    truncated = block[: prose_match.start()] if prose_match else block
    note = block[prose_match.start() :].strip() if prose_match else None

    row_starts = list(_ROW_START_RE.finditer(truncated))
    if not row_starts:
        return None

    header_text = truncated[: row_starts[0].start()]
    columns = [c.strip() for c in header_text.split(",") if c.strip()]
    if not columns:
        return None
    columns = _dedupe_columns(columns)

    rows = []
    for index, match in enumerate(row_starts):
        end = row_starts[index + 1].start() if index + 1 < len(row_starts) else len(truncated)
        segment = truncated[match.start() : end]

        cells = [c.strip() for c in segment.split(",")]
        if len(cells) > len(columns):
            # Surplus cells are trailing prose that slipped past truncation.
            cells = cells[: len(columns)]
        elif len(cells) < len(columns):
            cells = cells + [""] * (len(columns) - len(cells))

        rows.append({column: _parse_cell(cell) for column, cell in zip(columns, cells)})

    if not rows:
        return None

    precision = _compute_precision(columns, rows)
    if (
        precision == "exact"
        and _ESTIMATION_MARKER_RE.search(block)
        and not _EXACTNESS_CLAIM_RE.search(block)
    ):
        precision = "estimated"

    return {
        "columns": columns,
        "precision": precision,
        "note": note,
        "row_count": len(rows),
        "rows": rows,
    }
