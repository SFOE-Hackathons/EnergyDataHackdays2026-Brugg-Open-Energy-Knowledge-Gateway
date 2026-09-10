"""Semantic annotation of retrieved passages.

The knowledge base returns prose and chart transcriptions with no indication
of what its numbers actually *mean*. The same figure - 704.9 MW - appears in
one document as photovoltaic modules *sold* and in another as capacity
*installed*, and elsewhere the distinction is between capacity added in a
single year and the cumulative stock at year end. Nothing in the upstream
payload separates these, so a consumer reading two passages has no way to
tell it is comparing incompatible quantities.

This module derives that missing context from the passage text itself. It is
deliberately conservative: every annotation is evidence-backed, and where the
text does not support a conclusion the answer is "unknown" rather than a
guess. A wrong basis label is worse than no basis label, because it looks
authoritative.

Nothing here reaches the network and nothing here mutates its input.
"""

import re

# ---------------------------------------------------------------------------
# Basis - what a number is measuring
# ---------------------------------------------------------------------------

# Marker vocabulary, drawn from the frequencies actually observed in the
# corpus (German dominates; English appears in vision-model transcriptions).
#
# The hard case is "installiert", which is both the most common marker and
# the least specific: on its own it cannot separate capacity added this year
# from the total standing at year end. It is therefore NOT a marker. Only the
# qualified forms below are, which is why a passage saying merely "installiert"
# yields no basis at all - the correct answer, since the text does not say.
_BASIS_MARKERS: tuple[tuple[str, str], ...] = (
    # sales - units shipped or sold, which is NOT the same as units running
    ("sales", r"Verkauf\w*"),
    ("sales", r"verkauft\w*"),
    ("sales", r"Absatz\w*"),
    ("sales", r"\bsold\b"),
    ("sales", r"\bsales\b"),
    # installed_annual - capacity added during one period
    ("installed_annual", r"Zubau\w*"),
    ("installed_annual", r"hinzugebaut\w*"),
    ("installed_annual", r"zugebaut\w*"),
    ("installed_annual", r"neu\s+installiert\w*"),
    ("installed_annual", r"neu\s+erstellt\w*"),
    ("installed_annual", r"newly\s+installed"),
    ("installed_annual", r"annual\s+install\w*"),
    ("installed_annual", r"j(?:ä|a)hrlich\w*\s+install\w*"),
    ("installed_annual", r"newly\s+added"),
    ("installed_annual", r"annual\s+additions?"),
    ("installed_annual", r"additional\s+\w+\s+capacity"),
    # installed_cumulative - the stock standing at a point in time
    ("installed_cumulative", r"Total\s+installiert\w*"),
    ("installed_cumulative", r"Gesamtleistung"),
    ("installed_cumulative", r"gesamte\s+installiert\w*"),
    ("installed_cumulative", r"kumuliert\w*"),
    ("installed_cumulative", r"cumulative"),
    ("installed_cumulative", r"total\s+installed"),
    ("installed_cumulative", r"installiert\w*\s+Leistung\s+\w{0,12}\s{0,1}Ende\s+\d{4}"),
    ("installed_cumulative", r"Ende\s+\d{4}\s+rund"),
    # production - energy generated over a period, not capacity
    ("production", r"Stromproduktion"),
    ("production", r"Energieproduktion"),
    ("production", r"Produktion"),
    ("production", r"produziert\w*"),
    ("production", r"Erzeugung"),
    ("production", r"\bproduction\b"),
    ("production", r"\bgenerated\b"),
    ("production", r"\bgeneration\b"),
)

_COMPILED_BASIS = tuple(
    (basis, re.compile(pattern, re.IGNORECASE)) for basis, pattern in _BASIS_MARKERS
)

# Ordered worst-to-best so a caller wanting a single label can take the last,
# but the real output is the full list - see infer_bases.
BASIS_VALUES = ("sales", "installed_annual", "installed_cumulative", "production")


def infer_bases(text: str) -> list[dict]:
    """Return every measurement basis the passage gives evidence for.

    A list, not a single value, because passages routinely mix bases: one
    corpus chunk states PV systems sold, the 90% of those assumed installed,
    and a running total, all within a few lines. Collapsing that to one label
    would invent a discrimination the source does not make.

    Each entry is ``{"basis": ..., "evidence": "<the matched phrase>"}`` so a
    consumer can see why the label was applied and overrule it.
    """
    if not text:
        return []

    # Transcribed column headers join words with underscores
    # ("Wind_Energy_Production_TJ/a"). An underscore is a word character, so
    # word-boundary markers would never fire inside one.
    haystack = text.replace("_", " ")

    found: dict[str, str] = {}
    for basis, pattern in _COMPILED_BASIS:
        if basis in found:
            continue
        match = pattern.search(haystack)
        if match:
            found[basis] = " ".join(match.group(0).split())

    return [
        {"basis": basis, "evidence": found[basis]}
        for basis in BASIS_VALUES
        if basis in found
    ]


# ---------------------------------------------------------------------------
# Years actually covered by the passage
# ---------------------------------------------------------------------------

# A bare four-digit run is only a year if it is not carrying a unit: Swiss
# texts write "2'170 MWp" with an apostrophe, but an unseparated "2170 MW"
# would otherwise read as the year 2170. Excluding a following unit removes
# that class of false positive.
#
# Commas are deliberately NOT treated as boundaries. Comma-grouped thousands
# always put exactly three digits after the comma, so they can never produce a
# spurious four-digit run - while data rows are full of "2002,2" and
# "2020,150", every one of which opens on a real year.
_YEAR_RE = re.compile(
    r"(?<![\d'.])(1[89]\d{2}|20\d{2}|21\d{2})(?!['\d]|\.\d)"
    r"(?!\s*(?:MW|MWp|MWh|GW|GWh|kW|kWh|TW|TWh|TJ|GJ|PJ|MJ|m2|m²|km2)\b)",
    re.IGNORECASE,
)

# Years outside this window are almost certainly not a data point in an energy
# statistics corpus; they are page numbers, reference codes or OCR noise.
_MIN_PLAUSIBLE_YEAR = 1900
_MAX_PLAUSIBLE_YEAR = 2100

# Swiss energy policy names carry a year: "Energiestrategie 2050",
# "Energieperspektiven 2050+". These are proper nouns, not data points, and
# they are pervasive in this corpus - a passage of 2017-2020 figures that
# merely cites the strategy would otherwise report a span reaching to 2050.
# The names are removed before years are read.
_PROGRAMME_NAME_RE = re.compile(
    r"\b(?:Energiestrategie|Energieperspektiven|Perspektiven|Strategie|"
    r"Szenarien|Szenario|Programm|Agenda)\s+\d{4}\+?",
    re.IGNORECASE,
)


def extract_years_covered(text: str) -> dict | None:
    """Return the span of years the passage's content actually mentions.

    This exists because a passage retrieved by a query for 2021 routinely
    contains a series running 2002-2022. Tagging it "2021" records what was
    asked, not what was found, and anything downstream that groups by that tag
    will attribute the whole series to the wrong year.

    Returns ``{"min": int, "max": int, "years": [int, ...]}`` or ``None`` when
    the passage names no plausible year.
    """
    if not text:
        return None

    scannable = _PROGRAMME_NAME_RE.sub(" ", text)
    years = sorted(
        {
            year
            for year in (int(m.group(1)) for m in _YEAR_RE.finditer(scannable))
            if _MIN_PLAUSIBLE_YEAR <= year <= _MAX_PLAUSIBLE_YEAR
        }
    )
    if not years:
        return None

    return {"min": years[0], "max": years[-1], "years": years}


def years_from_rows(columns: list[str], rows: list[dict]) -> dict | None:
    """Return the year span of parsed chart rows, read from their label column.

    Prefer this over ``extract_years_covered`` for anything already parsed
    into rows. A raw `<data>` block is bare CSV, so scanning its text cannot
    tell a year from a measurement: "1994,1900" and "2018,1945" put a
    four-digit *value* right next to a four-digit year, and text scanning
    duly reported a chart spanning 1900-2100. The row labels are unambiguous.
    """
    if not columns or not rows:
        return None

    label_column = columns[0]
    years = sorted(
        {
            int(cell["value"])
            for row in rows
            for column, cell in row.items()
            if column == label_column
            and cell["kind"] == "exact"
            and cell["value"] is not None
            and float(cell["value"]).is_integer()
            and _MIN_PLAUSIBLE_YEAR <= cell["value"] <= _MAX_PLAUSIBLE_YEAR
        }
    )
    if not years:
        return None

    return {"min": years[0], "max": years[-1], "years": years}


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

# Upstream chunking splits on size, not on structure, so a chunk boundary can
# land inside a figure transcription or mid-value. The gateway cannot fix the
# chunking, but it can refuse to present a severed passage as if it were
# whole.

# A value marker with nothing after it: "- 2025: ~", "2024," or "Total:".
_DANGLING_VALUE_RE = re.compile(r"[:,=~]\s*$|[-–]\s*$")

# A chunk that opens with a closing tag was cut out of the middle of a block.
_ORPHAN_CLOSING_TAG_RE = re.compile(r"^\s*</\w+>")

# A complete passage ends on a tag, sentence punctuation, a digit or a percent.
_CLEAN_ENDING_RE = re.compile(r"(?:>|[.!?%\"')\]]|\d)\s*$")


def detect_truncation(text: str) -> dict:
    """Report whether the passage looks severed by the upstream chunker.

    Returns ``{"is_truncated": bool, "reasons": [str, ...]}``. Detection is
    heuristic and errs toward silence: a passage flagged truncated is worth a
    caller's suspicion, but an unflagged passage is not a guarantee.
    """
    reasons: list[str] = []
    if not text or not text.strip():
        return {"is_truncated": False, "reasons": reasons}

    stripped = text.strip()

    if _ORPHAN_CLOSING_TAG_RE.match(stripped):
        reasons.append("starts with a closing tag, so the passage begins mid-block")

    if text.count("<data>") != text.count("</data>"):
        reasons.append("unbalanced <data> tags, so a figure transcription is cut")

    if _DANGLING_VALUE_RE.search(stripped):
        reasons.append("ends on a value separator with no value after it")
    elif not _CLEAN_ENDING_RE.search(stripped):
        reasons.append("ends mid-token rather than at a sentence or tag boundary")

    return {"is_truncated": bool(reasons), "reasons": reasons}


# ---------------------------------------------------------------------------
# How a figure's numbers were obtained
# ---------------------------------------------------------------------------

_IMAGE_TYPE_RE = re.compile(r"<image_type>(.*?)</image_type>", re.DOTALL | re.IGNORECASE)

_TABLE_HINT_RE = re.compile(r"\btable\b|\btabelle\b|\bdatentabelle\b", re.IGNORECASE)

# The transcriber said it read numbers printed on the figure.
_LABEL_HINT_RE = re.compile(
    r"data labels?|as labell?ed|labell?ed on the (?:chart|figure|graph)|"
    r"printed on|values are (?:shown|displayed|given) (?:on|in) the|"
    r"abgelesen aus der Legende|exact figures",
    re.IGNORECASE,
)

# The transcriber said it eyeballed the geometry.
_VISUAL_HINT_RE = re.compile(
    r"visual interpretation|visually estimat\w+|read off|reading off|"
    r"estimated from the (?:chart|figure|graph|image)|bar heights?|"
    r"cannot be determined from the image|approximate\w* from the",
    re.IGNORECASE,
)

def classify_extraction_method(text: str, precision: str | None = None) -> str | None:
    """Say how a figure's numbers were obtained, or ``None`` if unclear.

    ``table`` values were transcribed from a printed table, ``chart_label``
    from numbers drawn on the figure, ``chart_visual_read`` by estimating from
    the geometry. The distinction matters because all three arrive formatted
    identically, and only the first two are the publisher's own figures.

    ``None`` is returned rather than a fallback guess whenever the text gives
    no signal, since an unearned ``table`` would launder an eyeballed number
    into an official one.
    """
    if not text:
        return None

    if _VISUAL_HINT_RE.search(text):
        return "chart_visual_read"
    if _LABEL_HINT_RE.search(text):
        return "chart_label"

    image_type = _IMAGE_TYPE_RE.search(text)
    if image_type and _TABLE_HINT_RE.search(image_type.group(1)):
        return "table"

    # No explicit statement. A block whose own values are ranges was plainly
    # estimated off the figure; anything else stays unknown.
    if precision == "estimated":
        return "chart_visual_read"
    return None


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

# A projection is not an estimate of something already measured - it is a
# figure for a period that had not finished when the source was written. The
# corpus mixes these in without visual distinction: a Swissolar forecast of
# 1500 MW for 2023 sits in the same list as measured values.
_PROJECTION_RE = re.compile(
    r"\bprognose\w*|\bvoraussichtlich\w*|\berwartet\w*|\bhochrechnung\w*|"
    r"\bprojected\b|\bprojection\b|\bforecast\w*|\bestimated at\b|"
    r"\bexpected to\b|\bwird geschätzt\b|geschätzt.{0,30}\b20\d{2}\b|"
    r"hatched|schraffiert",
    re.IGNORECASE,
)


def detect_projection(text: str) -> bool:
    """Whether the passage presents forward-looking figures as well as measured ones."""
    return bool(text) and bool(_PROJECTION_RE.search(text))
