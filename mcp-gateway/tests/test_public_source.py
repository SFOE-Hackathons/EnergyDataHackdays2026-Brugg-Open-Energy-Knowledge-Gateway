"""Unit tests for public_source. No network - parsing and scoring only."""

from public_source import (
    PublicSourceResolver,
    _similarity,
    _tokens,
    parse_document_name,
)

# A trimmed but structurally faithful pair of result blocks, as returned for
# "schweizerische statistik der erneuerbaren energien 2025 vorabzug": the same
# publication listed twice, once as a spreadsheet of data tables and once as
# the report PDF, with identical dates.
SEARCH_PAGE = """
<div class="list-group-item">
    <strong>Schweizerische Statistik der erneuerbaren Energien 2025 Vorabzug &#8211; Datentabellen</strong>
    <p>Erschienen: 26.06.2026<br />Dateityp: XLSX<br />Gr&ouml;&szlig;e: 256 KB<br />
    <a class="download-link" href="/de/publication/download/8787">DE</a>
</div>
<div class="list-group-item">
    <strong>Schweizerische Statistik der erneuerbaren Energien 2025 Vorabzug. Ausgabe 2025</strong>
    <p>Erschienen: 26.06.2026<br />Dateityp: PDF<br />Gr&ouml;&szlig;e: 1000 KB<br />
    <a class="download-link" href="/de/publication/download/12663">DE</a>
</div>
"""


class TestParseDocumentName:
    def test_splits_date_and_slug(self):
        assert parse_document_name("2025-08-01_fakten-zur-windenergie.pdf") == (
            "2025-08-01",
            "fakten zur windenergie",
        )

    def test_rejects_name_without_date_prefix(self):
        assert parse_document_name("fakten-zur-windenergie.pdf") == (None, None)

    def test_rejects_non_pdf(self):
        assert parse_document_name("2025-08-01_something.xlsx") == (None, None)

    def test_tolerates_empty_input(self):
        assert parse_document_name("") == (None, None)
        assert parse_document_name(None) == (None, None)


class TestSimilarity:
    def test_identical_titles_score_one(self):
        assert _similarity(_tokens("fakten zur windenergie"), _tokens("Fakten zur Windenergie")) == 1.0

    def test_diacritics_are_folded(self):
        # Corpus filenames strip umlauts; upstream titles keep them.
        assert _similarity(_tokens("warmepumpen"), _tokens("Wärmepumpen")) == 1.0

    def test_disjoint_titles_score_zero(self):
        assert _similarity(_tokens("windenergie"), _tokens("geothermie statistik")) == 0.0

    def test_penalizes_a_candidate_carrying_many_extra_tokens(self):
        """The false positive this scoring exists to prevent: both tokens of
        the query appear in a much longer, unrelated title. One-directional
        coverage scores that a perfect 1.0."""
        query = _tokens("energieperspektiven 2050")
        wrong = _tokens(
            "Regeneration von Sole-Wasser Waermepumpen Zusatzbericht zu den "
            "Energieperspektiven 2050 Grundlagen"
        )
        right = _tokens("Energieperspektiven 2050. Kurzbericht")
        assert _similarity(query, wrong) < _similarity(query, right)


class TestParseResults:
    def setup_method(self):
        self.results = PublicSourceResolver._parse_results(SEARCH_PAGE)

    def test_finds_both_entries(self):
        assert len(self.results) == 2

    def test_unescapes_titles(self):
        assert "–" in self.results[0]["title"]

    def test_reformats_date_to_iso(self):
        assert self.results[0]["date"] == "2026-06-26"

    def test_captures_file_type(self):
        assert [r["file_type"] for r in self.results] == ["XLSX", "PDF"]

    def test_captures_download_id(self):
        assert [r["id"] for r in self.results] == ["8787", "12663"]


class TestBestMatchPrefersThePdf:
    """Both records share a title and a date; only file type separates them.
    Picking the spreadsheet would hand a caller a different file than the
    document that was actually indexed."""

    def test_pdf_wins_over_the_data_table_spreadsheet(self, monkeypatch):
        resolver = PublicSourceResolver()
        monkeypatch.setattr(
            resolver, "_search", lambda query: PublicSourceResolver._parse_results(SEARCH_PAGE)
        )
        wanted = _tokens("schweizerische statistik der erneuerbaren energien 2025 vorabzug")
        assert resolver._best_match("q", wanted, "2026-06-26") == "12663"


class TestResolveIsBestEffort:
    def test_returns_none_and_does_not_raise_when_search_fails(self, monkeypatch):
        resolver = PublicSourceResolver()

        def boom(query):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(resolver, "_search", boom)
        assert resolver._resolve_uncached("2025-08-01_fakten-zur-windenergie.pdf") is None

    def test_unparseable_name_resolves_to_none(self):
        assert PublicSourceResolver()._resolve_uncached("not-a-corpus-name.txt") is None

    def test_negative_results_are_cached(self, monkeypatch):
        resolver = PublicSourceResolver()
        calls = []

        def counting_search(query):
            calls.append(query)
            return []

        monkeypatch.setattr(resolver, "_search", counting_search)
        name = "2025-08-01_fakten-zur-windenergie.pdf"
        assert resolver.resolve_many([name]) == {name: None}
        before = len(calls)
        assert resolver.resolve_many([name]) == {name: None}
        assert len(calls) == before, "a cached miss must not be retried upstream"
