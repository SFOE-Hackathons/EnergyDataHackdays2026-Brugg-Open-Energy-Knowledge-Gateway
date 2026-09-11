"""Tests for passages.dedupe / passages.project."""

from passages import (
    ALL,
    CORE,
    FULL,
    INDEX,
    dedupe,
    intern_sources,
    passage_key,
    project,
    project_source,
)


def source(**overrides):
    base = {
        "title": "doc.pdf",
        "published_at": "2024-01-01",
        "download_url": "https://pubdb.bfe.admin.ch/de/publication/download/1",
        "file_type": "PDF",
        "language": "en",
        "media_type": None,
        "created_at": "2026-08-31T13:07:36Z",
        "last_updated_at": "2026-08-31T13:07:36Z",
    }
    base.update(overrides)
    return base


def passage(text, score=1.0, title="doc.pdf", **extra):
    return {"text": text, "score": score, "source": {"title": title}, **extra}


class TestPassageKey:
    def test_normalizes_whitespace(self):
        a = passage("a value\nsplit  over lines")
        b = passage("a value split over lines")
        assert passage_key(a, 0) == passage_key(b, 1)

    def test_different_text_gets_different_key(self):
        assert passage_key(passage("one"), 0) != passage_key(passage("two"), 0)

    def test_empty_text_is_keyed_per_position(self):
        assert passage_key(passage(""), 0) != passage_key(passage(""), 1)

    def test_missing_text_field_is_treated_as_empty(self):
        assert passage_key({}, 3) == passage_key(passage(""), 3)


class TestDedupe:
    def test_keeps_distinct_passages(self):
        result = dedupe([passage("one"), passage("two")])
        assert [item["text"] for item in result] == ["one", "two"]

    def test_collapses_identical_text(self):
        result = dedupe([passage("same"), passage("same")])
        assert len(result) == 1

    def test_collapses_across_different_documents(self):
        # The point of keying on text alone: the corpus stores the same
        # publication under two document names.
        result = dedupe(
            [
                passage("same", score=0.4, title="2026-05-01_report.pdf"),
                passage("same", score=0.9, title="2026-07-06_report.pdf"),
            ]
        )
        assert len(result) == 1
        assert result[0]["source"]["title"] == "2026-07-06_report.pdf"

    def test_keeps_the_highest_scoring_copy(self):
        result = dedupe([passage("same", score=0.2), passage("same", score=0.8)])
        assert result[0]["score"] == 0.8

    def test_keeps_position_of_the_first_copy(self):
        result = dedupe(
            [passage("first"), passage("dup", score=0.1), passage("last"), passage("dup", score=0.9)]
        )
        assert [item["text"] for item in result] == ["first", "dup", "last"]
        assert result[1]["score"] == 0.9

    def test_treats_whitespace_variants_as_one(self):
        assert len(dedupe([passage("a  b"), passage("a\nb")])) == 1

    def test_empty_passages_are_never_collapsed(self):
        assert len(dedupe([passage(""), passage("")])) == 2

    def test_missing_score_does_not_crash(self):
        result = dedupe([{"text": "x"}, {"text": "x", "score": 0.5}])
        assert len(result) == 1
        assert result[0]["score"] == 0.5

    def test_does_not_mutate_the_input(self):
        original = passage("same", queried_year=2020)
        dedupe([original, passage("same", queried_year=2021)])
        assert original["queried_year"] == 2020
        assert "queried_years" not in original

    def test_empty_input(self):
        assert dedupe([]) == []


class TestDedupeQueriedYears:
    def test_merges_years_of_collapsed_copies(self):
        result = dedupe(
            [passage("same", queried_year=2016), passage("same", queried_year=2015)]
        )
        assert result[0]["queried_years"] == [2015, 2016]
        assert "queried_year" not in result[0]

    def test_merges_years_onto_the_better_scoring_copy(self):
        # The winning copy arrives second; the year seen against the first
        # must not be lost.
        result = dedupe(
            [
                passage("same", score=0.1, queried_year=2015),
                passage("same", score=0.9, queried_year=2016),
            ]
        )
        assert result[0]["score"] == 0.9
        assert result[0]["queried_years"] == [2015, 2016]

    def test_repeated_year_is_recorded_once(self):
        result = dedupe(
            [passage("same", queried_year=2020), passage("same", queried_year=2020)]
        )
        assert result[0]["queried_years"] == [2020]

    def test_single_year_still_becomes_a_list(self):
        result = dedupe([passage("only", queried_year=2020)])
        assert result[0]["queried_years"] == [2020]

    def test_passages_without_a_queried_year_are_left_alone(self):
        result = dedupe([passage("plain")])
        assert "queried_years" not in result[0]
        assert "queried_year" not in result[0]


class TestProjectSource:
    def test_core_drops_the_constant_fields(self):
        result = project_source(source(), CORE)
        assert "file_type" not in result        # always "PDF"
        assert "language" not in result         # always "en", and wrong
        assert "created_at" not in result       # identical to last_updated_at
        assert "last_updated_at" not in result  # our ingestion time, not the document's

    def test_core_keeps_the_informative_fields(self):
        result = project_source(source(), CORE)
        assert result["title"] == "doc.pdf"
        assert result["published_at"] == "2024-01-01"

    def test_core_omits_a_null_field(self):
        assert "media_type" not in project_source(source(media_type=None), CORE)

    def test_core_keeps_a_populated_optional_field(self):
        assert project_source(source(media_type="image"), CORE)["media_type"] == "image"

    def test_download_url_always_present(self):
        assert project_source(source(), CORE)["download_url"].endswith("/1")

    def test_download_url_kept_even_when_null(self):
        # A null means "could not be matched to a public record", which the
        # caller must be able to tell apart from the field being trimmed.
        result = project_source(source(download_url=None), CORE)
        assert "download_url" in result
        assert result["download_url"] is None

    def test_download_url_kept_even_when_absent_upstream(self):
        result = project_source({"title": "doc.pdf"}, CORE)
        assert "download_url" in result
        assert result["download_url"] is None

    def test_all_returns_every_field(self):
        assert project_source(source(), ALL) == source()

    def test_core_does_not_mutate_the_input(self):
        given = source()
        project_source(given, CORE)
        assert given["file_type"] == "PDF"


class TestProject:
    def test_index_drops_the_text(self):
        result = project([passage("verbatim")], INDEX)
        assert "text" not in result[0]

    def test_index_keeps_everything_else(self):
        result = project([passage("verbatim", years_covered=[2020], bases=[])], INDEX)
        assert result[0]["years_covered"] == [2020]
        assert result[0]["bases"] == []
        assert result[0]["score"] == 1.0

    def test_full_keeps_the_text(self):
        result = project([passage("verbatim")], FULL)
        assert result[0]["text"] == "verbatim"

    def test_trims_the_source_by_default(self):
        given = [{"text": "t", "source": source()}]
        assert "file_type" not in project(given, FULL)[0]["source"]

    def test_source_fields_all_keeps_everything(self):
        given = [{"text": "t", "source": source()}]
        assert project(given, FULL, ALL)[0]["source"] == source()

    def test_handles_a_passage_with_no_source(self):
        assert project([{"text": "t"}], FULL)[0] == {"text": "t"}

    def test_index_does_not_mutate_the_input(self):
        given = [passage("verbatim")]
        project(given, INDEX)
        assert given[0]["text"] == "verbatim"

    def test_does_not_mutate_the_nested_source(self):
        given = [{"text": "t", "source": source()}]
        project(given, INDEX, CORE)
        assert given[0]["source"]["file_type"] == "PDF"

    def test_empty_input(self):
        assert project([], INDEX) == []


class TestInternSources:
    def test_replaces_the_inline_source_with_an_id(self):
        items, sources = intern_sources([{"text": "t", "source": source()}])
        assert "source" not in items[0]
        assert items[0]["source_id"] == "s1"
        assert sources == {"s1": source()}

    def test_repeated_document_reuses_one_entry(self):
        items, sources = intern_sources(
            [{"text": "a", "source": source()}, {"text": "b", "source": source()}]
        )
        assert items[0]["source_id"] == items[1]["source_id"]
        assert len(sources) == 1

    def test_distinct_documents_get_distinct_entries(self):
        items, sources = intern_sources(
            [
                {"text": "a", "source": source(title="one.pdf")},
                {"text": "b", "source": source(title="two.pdf")},
            ]
        )
        assert items[0]["source_id"] != items[1]["source_id"]
        assert len(sources) == 2

    def test_same_pdf_under_two_titles_stays_two_entries(self):
        # The corpus files one publication under more than one document name.
        # Merging them would report a title the passage was not filed under.
        _, sources = intern_sources(
            [
                {"text": "a", "source": source(title="2026-05-01_report.pdf")},
                {"text": "b", "source": source(title="2026-07-06_report.pdf")},
            ]
        )
        assert len(sources) == 2

    def test_ids_follow_first_appearance(self):
        items, _ = intern_sources(
            [
                {"text": "a", "source": source(title="one.pdf")},
                {"text": "b", "source": source(title="two.pdf")},
                {"text": "c", "source": source(title="one.pdf")},
            ]
        )
        assert [item["source_id"] for item in items] == ["s1", "s2", "s1"]

    def test_field_order_does_not_split_an_entry(self):
        reordered = dict(reversed(list(source().items())))
        _, sources = intern_sources(
            [{"text": "a", "source": source()}, {"text": "b", "source": reordered}]
        )
        assert len(sources) == 1

    def test_a_null_field_is_not_confused_with_an_absent_one(self):
        _, sources = intern_sources(
            [
                {"text": "a", "source": {"title": "x", "download_url": None}},
                {"text": "b", "source": {"title": "x"}},
            ]
        )
        assert len(sources) == 2

    def test_item_without_a_source_is_left_alone(self):
        items, sources = intern_sources([{"text": "t"}])
        assert items[0] == {"text": "t"}
        assert sources == {}

    def test_keeps_the_other_fields(self):
        items, _ = intern_sources([{"text": "t", "score": 0.5, "source": source()}])
        assert items[0]["text"] == "t"
        assert items[0]["score"] == 0.5

    def test_does_not_mutate_the_input(self):
        given = [{"text": "t", "source": source()}]
        intern_sources(given)
        assert given[0]["source"]["title"] == "doc.pdf"

    def test_empty_input(self):
        assert intern_sources([]) == ([], {})
