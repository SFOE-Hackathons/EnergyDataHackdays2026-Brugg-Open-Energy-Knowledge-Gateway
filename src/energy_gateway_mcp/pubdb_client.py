import io
import logging
import re
import sys

import pypdf
import requests
from bs4 import BeautifulSoup

from . import config

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = config.PUBDB_BASE_URL
SEARCH_URL = f"{BASE_URL}/de/suche"
SEARCH_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
LANGUAGE_PREFERENCE = ("de", "fr", "it", "en")
MAX_MATCHED_PAGES = 2
# Table pages that carry a decade of historical rows (the very data these
# tools exist to fetch) can run to ~3.5k+ chars; 3000 was truncating the
# most recent (usually most wanted) row. Give real headroom.
MAX_SNIPPET_CHARS = 6000

_REQUEST_HEADERS = {"User-Agent": "open-energy-gateway-mcp/0.1 (hackathon PoC)"}

_FILENAME_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<slug>.+?)\.pdf$", re.IGNORECASE
)

_WORD_PATTERN = re.compile(r"[a-zA-ZäöüÄÖÜéèàçÉÈÀ0-9]+")
_STOPWORDS = {
    "the", "and", "for", "der", "die", "das", "und", "von", "für", "mit",
    "dem", "den", "des", "ist", "auf", "in", "im", "de", "la", "le", "et",
    "les",
}

# The source PDFs are German (occasionally French/Italian); a claim written
# in English (likely, since callers are usually reasoning in English) won't
# lexically match German page text at all otherwise. If any term in a group
# is present, every term in that group is added as a search keyword.
_SYNONYM_GROUPS = [
    {"deutschland", "germany", "allemagne", "germania"},
    {"frankreich", "france", "francia"},
    {"italien", "italy", "italie", "italia"},
    {"österreich", "austria", "autriche"},
    {"liechtenstein"},
    {"schweiz", "switzerland", "suisse", "svizzera"},
    {"ausfuhr", "export", "exports", "exportation"},
    {"einfuhr", "import", "imports", "importation"},
    {"handel", "trade", "verkehr", "exchange"},
]


def _expand_keywords(keywords):
    expanded = set(keywords)
    for group in _SYNONYM_GROUPS:
        if expanded & group:
            expanded |= group
    return expanded


class PubDbError(Exception):
    """Base class for all pubdb_client errors."""


class PubDbLookupError(PubDbError):
    """Raised when the publication can't be confidently located on pubdb."""


class PubDbDownloadError(PubDbError):
    """Raised when the matched PDF can't be downloaded or its text extracted."""


def parse_document_title(document_title):
    """Turn a KB `_document_title` like
    '2022-03-10_die-neue-rolle-der-wasserkraft.pdf' into a (query, date)
    pair: `query` is a free-text search string derived from the slug,
    `date` is the publication date as DD.MM.YYYY for pubdb's from/to
    filters. Falls back to using the whole title as the query (no date)
    if it doesn't match the expected pattern. Never raises."""
    title = (document_title or "").strip()
    match = _FILENAME_PATTERN.match(title)

    if match:
        year, month, day = match.group("date").split("-")
        slug = match.group("slug")
        date_ddmmyyyy = f"{day}.{month}.{year}"
    else:
        slug = title[:-4] if title.lower().endswith(".pdf") else title
        date_ddmmyyyy = None

    query = re.sub(r"[-_()]+", " ", slug)
    query = re.sub(r"\s+", " ", query).strip()
    return query, date_ddmmyyyy


def search_publication(query, date_ddmmyyyy=None):
    """Search pubdb.bfe.admin.ch for a single confident match.

    Returns {"title", "id", "published", "download_links"}.
    Raises PubDbLookupError if the site can't be reached/parsed, or if
    zero or multiple ambiguous candidates come back."""
    candidates = _search(query, date_ddmmyyyy)

    if not candidates and date_ddmmyyyy:
        logger.info("No hits with date filter %s, retrying without it", date_ddmmyyyy)
        candidates = _search(query, None)

    if not candidates:
        raise PubDbLookupError(
            f"No publication matching '{query}' was found on pubdb.bfe.admin.ch."
        )

    if len(candidates) > 1:
        if date_ddmmyyyy:
            exact = [c for c in candidates if c["published"] == date_ddmmyyyy]
            if len(exact) == 1:
                return exact[0]
        normalized_query = query.strip().rstrip(".").lower()
        title_matches = [
            c for c in candidates
            if c["title"].strip().rstrip(".").lower() == normalized_query
        ]
        if len(title_matches) == 1:
            return title_matches[0]
        finals = [c for c in candidates if "vorabzug" not in c["title"].lower()]
        if len(finals) == 1:
            return finals[0]
        titles = ", ".join(c["title"] for c in candidates[:5])
        raise PubDbLookupError(
            f"Found {len(candidates)} possible matches for '{query}' on "
            f"pubdb.bfe.admin.ch and couldn't confidently pick one "
            f"(candidates: {titles})."
        )

    return candidates[0]


def _search(query, date_ddmmyyyy):
    params = {"q": query}
    if date_ddmmyyyy:
        params["from"] = date_ddmmyyyy
        params["to"] = date_ddmmyyyy

    try:
        response = requests.get(
            SEARCH_URL, params=params, headers=_REQUEST_HEADERS, timeout=SEARCH_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise PubDbLookupError(f"Could not reach pubdb.bfe.admin.ch: {e}") from e

    return _parse_search_results(response.text)


def _parse_search_results(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for item in soup.select("div.list-group-item"):
        title_tag = item.find("strong")
        title = title_tag.get_text(strip=True) if title_tag else None
        if not title:
            continue

        text = item.get_text("\n")
        date_match = re.search(r"Erschienen:\s*(\d{2}\.\d{2}\.\d{4})", text)
        id_match = re.search(r"ID:\s*(\d+)", text)
        pub_id = id_match.group(1) if id_match else None

        download_links = {}
        for link in item.select("a.download-link"):
            href = link.get("href", "")
            lang_match = re.match(r"^/([a-z]{2})/publication/download/\d+", href)
            if lang_match:
                download_links[lang_match.group(1)] = href

        if not pub_id or not download_links:
            continue

        results.append({
            "title": title,
            "id": pub_id,
            "published": date_match.group(1) if date_match else None,
            "download_links": download_links,
        })

    return results


def download_pdf(download_links):
    """Download the PDF, preferring German, then French/Italian/English."""
    href = next(
        (download_links[lang] for lang in LANGUAGE_PREFERENCE if lang in download_links),
        None,
    )
    if not href:
        raise PubDbDownloadError("No download link was found for this publication in any language.")

    url = href if href.startswith("http") else BASE_URL + href
    try:
        response = requests.get(url, headers=_REQUEST_HEADERS, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        raise PubDbDownloadError(f"Could not download the PDF from pubdb.bfe.admin.ch: {e}") from e

    content_type = response.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower():
        raise PubDbDownloadError(
            f"Expected a PDF but got Content-Type '{content_type}' from pubdb.bfe.admin.ch."
        )

    return response.content


def extract_matching_pages(pdf_bytes, claim, max_pages=MAX_MATCHED_PAGES):
    """Extract text per page and return the top `max_pages` pages ranked by
    keyword-overlap score against `claim`. Returns a list of
    (page_number, text) tuples, 1-indexed, best match first."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise PubDbDownloadError(f"Could not parse the downloaded file as a PDF: {e}") from e

    keywords = [
        w for w in (t.lower() for t in _WORD_PATTERN.findall(claim or ""))
        if len(w) > 2 and w not in _STOPWORDS
    ]
    keywords = _expand_keywords(keywords)

    scored = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.warning("extract_text failed on page %d", i + 1)
            text = ""
        if text.strip():
            text_lower = text.lower()
            score = sum(text_lower.count(kw) for kw in keywords) if keywords else 0
            scored.append((i + 1, text, score))

    if not scored:
        raise PubDbDownloadError("No extractable text was found in the downloaded PDF.")

    scored.sort(key=lambda t: t[2], reverse=True)
    return [(page_num, text) for page_num, text, _ in scored[:max_pages]]


MAX_YEAR_SPAN = 10


def get_time_series(topic, claim, start_year, end_year):
    """For each year in [start_year, end_year] (inclusive), search for a
    publication titled "<topic> <year>", download it, and extract the
    single page best matching `claim`. Returns one combined report labeled
    by year; per-year failures are reported inline rather than aborting
    the whole call. Raises PubDbLookupError if the range is invalid or
    exceeds MAX_YEAR_SPAN."""
    if end_year < start_year:
        raise PubDbLookupError("end_year must be >= start_year.")
    if end_year - start_year + 1 > MAX_YEAR_SPAN:
        raise PubDbLookupError(
            f"Requested {end_year - start_year + 1} years, which is more "
            f"than the {MAX_YEAR_SPAN}-year limit per call; narrow the range."
        )

    sections = []
    for year in range(start_year, end_year + 1):
        query = f"{topic} {year}"
        try:
            publication = search_publication(query, None)
            pdf_bytes = download_pdf(publication["download_links"])
            page_num, text = extract_matching_pages(pdf_bytes, claim, max_pages=1)[0]
            sections.append(
                f"=== {year} — \"{publication['title']}\" "
                f"(pubdb ID {publication['id']}) ===\n"
                f"--- Page {page_num} ---\n{text.strip()[:MAX_SNIPPET_CHARS]}"
            )
        except PubDbError as e:
            sections.append(f"=== {year} — could not retrieve: {e} ===")

    header = (
        f"Time series lookup for '{topic}', {start_year}-{end_year}. Each "
        "year below is a real fetched page — read the actual values "
        "yourself rather than assuming a smooth trend; some years may be "
        "reported missing rather than silently skipped.\n"
    )
    return header + "\n\n".join(sections)


def verify_citation(document_title, claim):
    """Locate the original public PDF for a KB citation, download it, and
    return the extracted text of its most claim-relevant page(s)."""
    query, date = parse_document_title(document_title)
    logger.info("Searching pubdb for %r (date filter: %s)", query, date)
    publication = search_publication(query, date)

    pdf_bytes = download_pdf(publication["download_links"])
    pages = extract_matching_pages(pdf_bytes, claim)

    return _format_result(document_title, publication, pages)


def _format_result(document_title, publication, pages):
    lines = [
        f"Source verification for citation: {document_title}",
        f"Matched public SFOE publication: \"{publication['title']}\" "
        f"(published {publication['published'] or 'date unknown'}), "
        f"pubdb.bfe.admin.ch publication ID {publication['id']}.",
        "",
    ]
    for page_num, text in pages:
        lines.append(f"--- Page {page_num} of the original public PDF ---")
        lines.append(text.strip()[:MAX_SNIPPET_CHARS])
        lines.append("")
    lines.append(
        "Compare this original-source text against the claim or passage "
        "you are checking. This tool does not judge correctness itself — "
        "that's your call to make."
    )
    return "\n".join(lines)
