"""Unit tests for the pure parts of the matcher. No network access."""

from __future__ import annotations

from datetime import date

import pytest

import build_source_map
from build_source_map import (
    CONTENT_THRESHOLD,
    MTIME_CONTENT_THRESHOLD,
    candidates_by_date,
    candidates_by_mtime,
    entry_dates,
    is_pdf,
    parse_last_modified,
    pubdb_url,
    resolve,
)
from source_match import S3Key

INDEX = {
    "7238": {
        "filename": "7238-20251126_Faktenblatt F_rderung_PV_DE.pdf",
        "content_type": "application/pdf",
        "last_modified": "Thu, 27 Nov 2025 16:01:38 GMT",
        "size": 0,
    },
    "10000": {
        "filename": "10000-2020 Leistungsvereinbarung BFE.pdf",
        "content_type": "application/pdf",
        "last_modified": "Tue, 14 Jan 2020 13:01:06 GMT",
        "size": 0,
    },
    "10020": {
        "filename": "10020-SACH2019_final_for_publication_31012020.pdf",
        "content_type": "application/pdf",
        "last_modified": "Fri, 31 Jan 2020 09:00:00 GMT",
        "size": 0,
    },
    "12500": {
        "filename": "12500-Vorlage.docx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "last_modified": "Fri, 11 Apr 2025 08:11:51 GMT",
        "size": 0,
    },
}


def test_pubdb_url_fills_the_template() -> None:
    assert pubdb_url(7238) == "https://pubdb.bfe.admin.ch/de/publication/download/7238"


def test_entry_dates_reads_yyyymmdd() -> None:
    assert date(2025, 11, 26) in entry_dates(INDEX["7238"]["filename"])


def test_entry_dates_reads_ddmmyyyy() -> None:
    assert date(2020, 1, 31) in entry_dates(INDEX["10020"]["filename"])


def test_entry_dates_reads_dotted_iso() -> None:
    assert date(2022, 3, 10) in entry_dates(
        "10822-WASSERKRAFT_Wasserkraft als Enabler_2022.03.10_BFE_Vogel_D.pdf"
    )


def test_entry_dates_ignores_a_bare_year() -> None:
    assert entry_dates(INDEX["10000"]["filename"]) == set()


def test_entry_dates_rejects_impossible_dates() -> None:
    assert entry_dates("9999-20251340_report.pdf") == set()


def test_parse_last_modified_reads_rfc7231() -> None:
    assert parse_last_modified("Thu, 27 Nov 2025 16:01:38 GMT") == date(2025, 11, 27)


def test_parse_last_modified_of_garbage_is_none() -> None:
    assert parse_last_modified("not a date") is None


def test_is_pdf_rejects_word_documents() -> None:
    assert is_pdf(INDEX["7238"]) is True
    assert is_pdf(INDEX["12500"]) is False


def test_candidates_by_date_matches_on_filename_date() -> None:
    assert candidates_by_date(INDEX, date(2025, 11, 26)) == [7238]


def test_candidates_by_date_returns_empty_when_nothing_matches() -> None:
    assert candidates_by_date(INDEX, date(1999, 1, 1)) == []


def test_candidates_by_mtime_uses_the_window() -> None:
    found = candidates_by_mtime(INDEX, date(2020, 1, 20), 14)
    assert 10000 in found
    assert 7238 not in found


def test_thresholds_exist_and_mtime_is_stricter() -> None:
    assert CONTENT_THRESHOLD == 0.5
    assert MTIME_CONTENT_THRESHOLD == 1.0
    assert MTIME_CONTENT_THRESHOLD > CONTENT_THRESHOLD


# --- resolve(): threshold selection by candidate source -------------------
#
# These fixtures put the wanted date only in `last_modified`, never in the
# filename, so `candidates_by_date` finds nothing and every match must come
# from the mtime window.

MTIME_ONLY_INDEX = {
    "30001": {
        "filename": "30001-Bericht_ohne_datum_im_namen.pdf",
        "content_type": "application/pdf",
        "last_modified": "Mon, 25 May 2020 10:00:00 GMT",
        "size": 0,
    },
}


def test_resolve_date_source_single_candidate_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lone date-source candidate is accepted without content scoring."""

    def boom(pubdb_id: int, timeout: float = 60.0) -> str:
        raise AssertionError("fetch_pdf_text must not run on the date path")

    monkeypatch.setattr(build_source_map, "fetch_pdf_text", boom)

    parsed = S3Key(
        key="2025-11-26_faktenblatt-forderung-pv.pdf",
        date=date(2025, 11, 26),
        slug="faktenblatt-forderung-pv",
    )
    outcome = resolve(parsed, INDEX)
    assert outcome == {"pubdb_id": 7238, "match": "date", "score": 1.0}


def test_resolve_mtime_source_single_candidate_requires_perfect_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lone mtime-source candidate must clear MTIME_CONTENT_THRESHOLD."""
    monkeypatch.setattr(
        build_source_map,
        "fetch_pdf_text",
        lambda pubdb_id, timeout=60.0: "Bericht ohne datum im namen",
    )

    parsed = S3Key(
        key="2020-05-20_bericht-ohne-datum-im-namen.pdf",
        date=date(2020, 5, 20),
        slug="bericht-ohne-datum-im-namen",
    )
    outcome = resolve(parsed, MTIME_ONLY_INDEX)
    assert outcome is not None
    assert outcome["pubdb_id"] == 30001
    assert outcome["match"] == "mtime+content"
    assert outcome["score"] == 1.0


def test_resolve_mtime_source_rejects_imperfect_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lone mtime-source candidate with partial coverage must be rejected.

    This is the defect this fix closes: previously a lone mtime candidate
    was auto-accepted regardless of content, inventing matches like
    pubdb 11114 for the solar-offerte-check S3 key.
    """
    monkeypatch.setattr(
        build_source_map,
        "fetch_pdf_text",
        lambda pubdb_id, timeout=60.0: "Bericht ohne irgendetwas Verwandtes",
    )

    parsed = S3Key(
        key="2020-05-20_bericht-ohne-datum-im-namen.pdf",
        date=date(2020, 5, 20),
        slug="bericht-ohne-datum-im-namen",
    )
    outcome = resolve(parsed, MTIME_ONLY_INDEX)
    assert outcome is None
