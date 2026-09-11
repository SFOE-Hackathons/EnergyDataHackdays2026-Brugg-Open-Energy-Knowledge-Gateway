"""Resolution of corpus documents to their public SFOE publication URLs.

Documents in the corpus are named `<YYYY-MM-DD>_<title-slug>.pdf`. That name
carries two things worth surfacing: the publication date, and enough of the
title to find the same publication in the Swiss Federal Office of Energy's
public publication database, which serves a permanent, credential-free
download URL for it.

Resolution is best-effort by design. A document that cannot be matched with
confidence yields `None` rather than a guessed link: handing a caller a URL
for the wrong publication is worse than handing it none, because a wrong
citation is not visibly wrong to whoever reads it.

Matching combines two signals:

* **Bidirectional title similarity.** Token overlap is scored as an F1 rather
  than as coverage of the slug alone. One-directional coverage produced a real
  false positive during development: the two tokens of
  `energieperspektiven-2050+` both appear in the much longer title of an
  unrelated heat-pump report, scoring a perfect 1.0. Penalizing tokens the
  candidate has but the slug does not demotes that candidate below the correct
  one.
* **Publication date agreement**, as a bonus rather than a requirement. Nearly
  every document's filename date equals its `Erschienen` date upstream, so
  agreement is strong confirmation. It is deliberately not a gate: a
  re-issued publication legitimately changes date, and gating would discard a
  correct link. One corpus document already resolves correctly on title alone.
"""

import html
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

SEARCH_URL = "https://pubdb.bfe.admin.ch/de/suche"
DOWNLOAD_URL_TEMPLATE = "https://pubdb.bfe.admin.ch/de/publication/download/{id}"

# Minimum combined score for a candidate to be accepted as the same document.
MATCH_THRESHOLD = 0.65
DATE_AGREEMENT_BONUS = 0.5

# A publication is often listed twice: once as the report PDF and once as a
# companion spreadsheet of its data tables, under near-identical titles and the
# same date. Every document in this corpus is a PDF, so preferring PDF breaks
# that tie toward the file that was actually indexed. Without it the caller can
# be handed a spreadsheet in place of the report it is citing.
PDF_PREFERENCE_BONUS = 0.25

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)\.pdf$", re.IGNORECASE)
_ITEM_RE = re.compile(r'<div class="list-group-item">(.*?)</div>', re.DOTALL)
_TITLE_RE = re.compile(r"<strong>(.*?)</strong>", re.DOTALL)
_ERSCHIENEN_RE = re.compile(r"Erschienen:\s*(\d{2})\.(\d{2})\.(\d{4})")
_DATEITYP_RE = re.compile(r"Dateityp:\s*([A-Za-z]+)")
_DOWNLOAD_RE = re.compile(r'href="/[a-z]{2}/publication/download/(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")

# German function words carry no identifying signal and would inflate overlap
# between unrelated titles.
_STOPWORDS = frozenset(
    {"der", "die", "das", "und", "von", "fur", "des", "dem", "den", "aus", "mit", "zur", "zum", "bis", "auf", "eine"}
)


def parse_document_name(filename: str) -> tuple[str | None, str | None]:
    """Split `<date>_<slug>.pdf` into an ISO date and a spaced-out title slug.

    Returns `(None, None)` for names that do not follow the convention.
    """
    if not filename:
        return None, None
    match = _FILENAME_RE.match(filename)
    if not match:
        return None, None
    return match.group(1), match.group(2).replace("-", " ")


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so `Wärmepumpen` and `warmepumpen` match."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _fold(text).split() if len(t) > 2 and t not in _STOPWORDS}


def _similarity(wanted: set[str], candidate: set[str]) -> float:
    """F1 over the two token sets. See this module's docstring for why F1."""
    if not wanted or not candidate:
        return 0.0
    shared = len(wanted & candidate)
    if not shared:
        return 0.0
    precision = shared / len(candidate)
    recall = shared / len(wanted)
    return 2 * precision * recall / (precision + recall)


class PublicSourceResolver:
    """Maps corpus document names to public publication download URLs.

    Results are cached in-process and keyed by document name, including
    negative results, so an unresolvable document is not retried on every
    query. The corpus is small and its documents recur heavily across
    searches, so the cache absorbs almost all lookups after warm-up.
    """

    def __init__(self, timeout: float = 15.0, max_workers: int = 6):
        self._timeout = timeout
        self._max_workers = max_workers
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def resolve_many(self, filenames: list[str]) -> dict[str, str | None]:
        """Resolve several documents at once, looking up only uncached names."""
        unique = {name for name in filenames if name}
        with self._lock:
            pending = [name for name in unique if name not in self._cache]

        if pending:
            workers = min(self._max_workers, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                resolved = list(executor.map(self._resolve_uncached, pending))
            with self._lock:
                self._cache.update(zip(pending, resolved))

        with self._lock:
            return {name: self._cache.get(name) for name in unique}

    def _resolve_uncached(self, filename: str) -> str | None:
        """Find the public URL for one document. Never raises."""
        try:
            date, slug = parse_document_name(filename)
            if not slug:
                return None

            wanted = _tokens(slug)
            if not wanted:
                return None

            # The full slug is the best query. If it finds nothing, retry with
            # only its most distinctive tokens, which tolerates a slug that was
            # truncated when the document was named.
            longest_tokens = " ".join(sorted(wanted, key=len, reverse=True)[:3])

            for query in dict.fromkeys([slug, longest_tokens]):
                match = self._best_match(query, wanted, date)
                if match:
                    return DOWNLOAD_URL_TEMPLATE.format(id=match)
            return None
        except Exception:
            # A resolution failure must never take down a search: the caller
            # still gets its passages, just without a public link.
            return None

    def _best_match(self, query: str, wanted: set[str], date: str | None) -> str | None:
        best_id, best_score = None, 0.0
        for candidate in self._search(query):
            score = _similarity(wanted, _tokens(candidate["title"]))
            if date and candidate["date"] == date:
                score += DATE_AGREEMENT_BONUS
            if candidate["file_type"] == "PDF":
                score += PDF_PREFERENCE_BONUS
            if score > best_score:
                best_id, best_score = candidate["id"], score
        return best_id if best_score >= MATCH_THRESHOLD else None

    def _search(self, query: str) -> list[dict]:
        response = requests.get(SEARCH_URL, params={"q": query}, timeout=self._timeout)
        response.raise_for_status()
        return self._parse_results(response.text)

    @staticmethod
    def _parse_results(page: str) -> list[dict]:
        results = []
        for block in _ITEM_RE.findall(page):
            title_match = _TITLE_RE.search(block)
            download_match = _DOWNLOAD_RE.search(block)
            if not title_match or not download_match:
                continue
            date_match = _ERSCHIENEN_RE.search(block)
            type_match = _DATEITYP_RE.search(block)
            results.append(
                {
                    "title": html.unescape(_TAG_RE.sub("", title_match.group(1))).strip(),
                    "date": (
                        f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
                        if date_match
                        else None
                    ),
                    "file_type": type_match.group(1).upper() if type_match else None,
                    "id": download_match.group(1),
                }
            )
        return results
