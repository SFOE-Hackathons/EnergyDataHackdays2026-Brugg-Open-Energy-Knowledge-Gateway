"""Unit tests for the pure matching primitives."""

from __future__ import annotations

from datetime import date

from source_match import S3Key, coverage, deslugify, fold, parse_s3_key, tokens


def test_parse_s3_key_splits_date_and_slug() -> None:
    parsed = parse_s3_key("2025-11-26_forderung-von-photovoltaikanlagen.pdf")
    assert parsed == S3Key(
        key="2025-11-26_forderung-von-photovoltaikanlagen.pdf",
        date=date(2025, 11, 26),
        slug="forderung-von-photovoltaikanlagen",
    )


def test_parse_s3_key_keeps_parentheses_in_slug() -> None:
    parsed = parse_s3_key("2022-04-01_evaluation-der-zusammenschlusse-(zev)-2018.pdf")
    assert parsed.slug == "evaluation-der-zusammenschlusse-(zev)-2018"


def test_parse_s3_key_without_date_prefix() -> None:
    parsed = parse_s3_key("annual-report.pdf")
    assert parsed.date is None
    assert parsed.slug == "annual-report"


def test_parse_s3_key_with_invalid_date() -> None:
    parsed = parse_s3_key("2025-13-45_something.pdf")
    assert parsed.date is None
    assert parsed.slug == "2025-13-45_something"


def test_deslugify_replaces_separators() -> None:
    assert deslugify("forderung-von-photovoltaikanlagen") == (
        "forderung von photovoltaikanlagen"
    )


def test_fold_removes_umlauts() -> None:
    assert fold("Förderung") == "forderung"
    assert fold("Grösse") == "grosse"
    assert fold("Straße") == "strasse"
    assert fold("Réalité") == "realite"


def test_tokens_drops_stopwords_but_keeps_short_acronyms() -> None:
    assert tokens("Förderung von PV im Jahr") == frozenset({"forderung", "pv", "jahr"})


def test_tokens_splits_on_punctuation_and_underscores() -> None:
    assert tokens("7238-20251126_Faktenblatt Förderung_PV_DE.pdf") == frozenset(
        {"7238", "20251126", "faktenblatt", "forderung", "pv", "de", "pdf"}
    )


def test_coverage_full_containment() -> None:
    score = coverage(
        "Förderung von Photovoltaikanlagen",
        "Faktenblatt Förderung Photovoltaikanlagen DE 2025",
    )
    assert score == 1.0


def test_coverage_partial() -> None:
    score = coverage(
        "Förderung Photovoltaik Einmalvergütung",
        "Faktenblatt Förderung PV DE",
    )
    assert score == 1 / 3


def test_coverage_of_empty_needle_is_zero() -> None:
    assert coverage("von der und", "anything at all") == 0.0
