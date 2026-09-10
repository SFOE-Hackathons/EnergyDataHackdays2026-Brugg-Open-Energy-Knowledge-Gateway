"""Pure matching primitives shared by the offline builders and the adapter.

Deliberately free of I/O so the scoring rules can be tested exhaustively
without touching the network.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

# German/English function words carry no signal for matching titles.
STOPWORDS = frozenset(
    "der die das den dem des ein eine einer einen einem und oder von vom "
    "fur zur zum auf mit bei als aus ist sind im in an "
    "the of and for to on at by".split()
)

_DATED_KEY_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_(?P<slug>.+)$"
)
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
# Folded before NFKD because these have no combining-mark decomposition.
_PRE_FOLD = {"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l"}


@dataclass(frozen=True)
class S3Key:
    """An S3 object key split into its publication date and title slug."""

    key: str
    date: date | None
    slug: str


def parse_s3_key(key: str) -> S3Key:
    """Split ``YYYY-MM-DD_<title-slug>.pdf`` into its parts.

    Keys without a parseable date prefix yield ``date=None`` and the whole
    stem as the slug.
    """
    stem = key.rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[: -len(".pdf")]

    match = _DATED_KEY_RE.match(stem)
    if match is None:
        return S3Key(key=key, date=None, slug=stem)

    try:
        parsed = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return S3Key(key=key, date=None, slug=stem)

    return S3Key(key=key, date=parsed, slug=match["slug"])


def deslugify(slug: str) -> str:
    """Turn a hyphenated slug back into a space-separated title."""
    return re.sub(r"[-_]+", " ", slug).strip()


def fold(text: str) -> str:
    """Lowercase and strip diacritics, so ``Förderung`` matches ``forderung``.

    The S3 slugs have already lost their umlauts, while the pubdb filenames
    have kept theirs, so both sides must be folded before comparison.
    """
    lowered = text.lower()
    for source, replacement in _PRE_FOLD.items():
        lowered = lowered.replace(source, replacement)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokens(text: str) -> frozenset[str]:
    """Fold, split on non-alphanumerics and drop function words.

    Short tokens are kept on purpose: ``PV`` and ``DE`` carry real signal.
    """
    parts = _TOKEN_SPLIT_RE.split(fold(text))
    return frozenset(part for part in parts if part and part not in STOPWORDS)


def coverage(needle: str, haystack: str) -> float:
    """Fraction of ``needle``'s tokens that appear in ``haystack``.

    Containment rather than Jaccard: an S3 title slug is long and a pubdb
    filename is short, so symmetric measures punish good matches.
    """
    wanted = tokens(needle)
    if not wanted:
        return 0.0
    return len(wanted & tokens(haystack)) / len(wanted)
