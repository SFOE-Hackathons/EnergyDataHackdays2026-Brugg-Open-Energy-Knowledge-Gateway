"""Unit tests for semantics. No network - pure functions only.

The fixtures below are real passages from the corpus, trimmed. They are the
cases that motivated the module: the same magnitude meaning different things,
and a retrieved year that has nothing to do with the content's years.
"""

from semantics import (
    classify_extraction_method,
    detect_projection,
    detect_truncation,
    extract_years_covered,
    infer_bases,
    years_from_rows,
)

# One chunk, three different quantities: modules sold, the fraction of those
# assumed installed, and a running total. This is why basis is a list.
MIXED_BASIS_PASSAGE = (
    "Total installierte Leistung (Total installed capacity) "
    "**Numerical Values for 2024:** - Verkauf PV-Anlagen: 1'810.4 MW "
    "- 90% davon installiert: 1'629.4 MW - Übertrag Folgejahr (Rest): 181.0 MW"
)

CUMULATIVE_VS_ANNUAL_PASSAGE = (
    "Die gesamte in der Schweiz installierte Leistung von PV-Anlagen beträgt "
    "Ende 2018 rund 2'170 MWp. Gemäss der Markterhebung Solarenergie wurden in "
    "den Jahren 2014 bis 2018 rund 1'400 MW Photovoltaik hinzugebaut."
)


class TestInferBases:
    def test_reports_every_basis_present_rather_than_collapsing_to_one(self):
        found = {entry["basis"] for entry in infer_bases(MIXED_BASIS_PASSAGE)}
        assert {"sales", "installed_cumulative"} <= found

    def test_separates_cumulative_stock_from_annual_additions(self):
        found = {entry["basis"] for entry in infer_bases(CUMULATIVE_VS_ANNUAL_PASSAGE)}
        assert "installed_cumulative" in found
        assert "installed_annual" in found

    def test_bare_installiert_is_not_enough_to_pick_a_basis(self):
        """"installiert" is the most common marker in the corpus and the least
        specific - it cannot separate this year's additions from the total. A
        confident label here would be a fabricated distinction."""
        assert infer_bases("Die Anlage wurde 2019 installiert.") == []

    def test_production_is_distinguished_from_capacity(self):
        found = {entry["basis"] for entry in infer_bases("Die Stromproduktion betrug 2'066 GWh.")}
        assert found == {"production"}

    def test_every_entry_carries_the_phrase_it_matched(self):
        for entry in infer_bases(MIXED_BASIS_PASSAGE):
            assert entry["evidence"]
            assert entry["evidence"].lower() in MIXED_BASIS_PASSAGE.lower()

    def test_annual_installation_is_not_cumulative(self):
        """A real chart column, "Annual_Installation_MW_Jahr", plotted beside a
        cumulative one. Getting this pair backwards is the exact error the
        basis field exists to catch."""
        assert [e["basis"] for e in infer_bases("Annual_Installation_MW_Jahr")] == [
            "installed_annual"
        ]
        assert [e["basis"] for e in infer_bases("Cumulative_Capacity_MW")] == [
            "installed_cumulative"
        ]

    def test_underscore_joined_column_headers_still_match(self):
        """Transcribed headers read "Wind_Energy_Production_TJ/a". An underscore
        is a word character, so word-boundary markers never fired inside one and
        every such column came back undetermined."""
        found = {entry["basis"] for entry in infer_bases("Wind_Energy_Production_TJ/a")}
        assert found == {"production"}

    def test_empty_text_yields_no_basis(self):
        assert infer_bases("") == []
        assert infer_bases(None) == []


class TestExtractYearsCovered:
    def test_reports_the_span_actually_present(self):
        """A passage retrieved by a query for 2021 can hold a 2002-2022 series.
        The queried year is not the content year."""
        text = "Year,Capacity 2002,2 2010,48 2021,705 2022,1000"
        assert extract_years_covered(text) == {
            "min": 2002,
            "max": 2022,
            "years": [2002, 2010, 2021, 2022],
        }

    def test_a_four_digit_measurement_is_not_read_as_a_year(self):
        assert extract_years_covered("Die Leistung beträgt 2170 MW im Jahr 2018") == {
            "min": 2018,
            "max": 2018,
            "years": [2018],
        }

    def test_swiss_apostrophe_numbers_are_not_years(self):
        assert extract_years_covered("rund 2'170 MWp") is None

    def test_returns_none_when_no_year_is_named(self):
        assert extract_years_covered("Ein Text ohne Jahreszahl.") is None
        assert extract_years_covered("") is None

    def test_a_policy_name_containing_a_year_is_not_a_data_year(self):
        """"Energiestrategie 2050" is a proper noun. Counting its year stretched
        a passage of 2017-2020 figures into a 2017-2050 span."""
        text = (
            "Die 2017 verabschiedete Energiestrategie 2050 hätte der Entwicklung "
            "einen Schub verleihen sollen, mit einem Zwischenziel bis 2020."
        )
        assert extract_years_covered(text) == {"min": 2017, "max": 2020, "years": [2017, 2020]}

    def test_the_energieperspektiven_variant_is_also_excluded(self):
        assert extract_years_covered("Energieperspektiven 2050+ und Daten für 2019") == {
            "min": 2019,
            "max": 2019,
            "years": [2019],
        }

    def test_implausible_years_are_ignored(self):
        assert extract_years_covered("Referenz 1234 und Jahr 2020") == {
            "min": 2020,
            "max": 2020,
            "years": [2020],
        }


class TestYearsFromRows:
    """A raw `<data>` block is bare CSV, so a four-digit measurement sits right
    next to a four-digit year. Text scanning reported a real wind chart as
    spanning 1900-2100 because two of its production values were 1900 and 2100."""

    COLUMNS = ["Year", "Production (TJ/a)"]
    ROWS = [
        {
            "Year": {"raw": "1994", "value": 1994.0, "min": None, "max": None, "kind": "exact"},
            "Production (TJ/a)": {"raw": "1900", "value": 1900.0, "min": None, "max": None, "kind": "exact"},
        },
        {
            "Year": {"raw": "1996", "value": 1996.0, "min": None, "max": None, "kind": "exact"},
            "Production (TJ/a)": {"raw": "2100", "value": 2100.0, "min": None, "max": None, "kind": "exact"},
        },
    ]

    def test_reads_years_only_from_the_label_column(self):
        assert years_from_rows(self.COLUMNS, self.ROWS) == {
            "min": 1994,
            "max": 1996,
            "years": [1994, 1996],
        }

    def test_a_four_digit_measurement_never_widens_the_span(self):
        span = years_from_rows(self.COLUMNS, self.ROWS)
        assert 1900 not in span["years"]
        assert 2100 not in span["years"]

    def test_text_scanning_would_have_got_this_wrong(self):
        """Documents why the two functions differ, so neither is 'simplified'
        into the other later."""
        scanned = extract_years_covered("Year,Production (TJ/a) 1994,1900 1996,2100")
        assert scanned["min"] == 1900 and scanned["max"] == 2100

    def test_a_severed_final_cell_is_skipped_not_guessed(self):
        rows = self.ROWS + [
            {
                "Year": {"raw": "2025", "value": 2025.0, "min": None, "max": None, "kind": "exact"},
                "Production (TJ/a)": {"raw": "", "value": None, "min": None, "max": None, "kind": "text"},
            }
        ]
        assert years_from_rows(self.COLUMNS, rows)["max"] == 2025

    def test_returns_none_for_empty_input(self):
        assert years_from_rows([], []) is None
        assert years_from_rows(self.COLUMNS, []) is None


class TestDetectTruncation:
    def test_flags_a_value_separator_with_nothing_after_it(self):
        """The reported failure: the single most important data point arrived
        as "- 2025: ~" because the chunker cut inside the value."""
        result = detect_truncation("Capacity by year: - 2024: ~1050 MW - 2025: ~")
        assert result["is_truncated"] is True
        assert result["reasons"]

    def test_flags_an_unbalanced_data_block(self):
        assert detect_truncation("<data>Year,V 2001,1")["is_truncated"] is True

    def test_flags_a_passage_that_begins_mid_block(self):
        assert detect_truncation("</description> <data>Year,V 2001,1</data>")["is_truncated"] is True

    def test_a_complete_passage_is_not_flagged(self):
        assert detect_truncation("Die Produktion betrug 2020 rund 2'066 GWh.") == {
            "is_truncated": False,
            "reasons": [],
        }

    def test_a_passage_ending_on_a_closed_tag_is_not_flagged(self):
        assert detect_truncation("<data>Year,V 2001,1</data>")["is_truncated"] is False

    def test_empty_text_is_not_flagged(self):
        assert detect_truncation("")["is_truncated"] is False


class TestClassifyExtractionMethod:
    def test_eyeballed_values_are_named_as_such(self):
        text = "These values are estimated based on visual interpretation of the area chart."
        assert classify_extraction_method(text) == "chart_visual_read"

    def test_printed_labels_are_named_as_such(self):
        text = "All values are presented as exact figures as labeled on the chart."
        assert classify_extraction_method(text) == "chart_label"

    def test_a_transcribed_table_is_named_as_such(self):
        text = "<analysis> <image_type> Data Table </image_type> </analysis>"
        assert classify_extraction_method(text) == "table"

    def test_visual_reading_outranks_a_table_image_type(self):
        text = "<image_type>Table</image_type> values were read off the figure"
        assert classify_extraction_method(text) == "chart_visual_read"

    def test_range_valued_data_without_any_statement_is_a_visual_read(self):
        assert classify_extraction_method("Year,V 1990,0-5", "estimated") == "chart_visual_read"

    def test_silence_yields_none_rather_than_an_unearned_table(self):
        """Defaulting to "table" would launder an eyeballed number into an
        official published figure."""
        assert classify_extraction_method("Year,V 2001,1", "exact") is None
        assert classify_extraction_method("") is None


class TestDetectProjection:
    def test_flags_a_forward_looking_figure(self):
        text = (
            "the additional photovoltaic capacity for 2023 was estimated at 1500 MW "
            "(according to Swissolar, dated 20.12.2023)"
        )
        assert detect_projection(text) is True

    def test_flags_a_hatched_projected_bar(self):
        assert detect_projection("The 2024 bar is hatched to mark a projection.") is True

    def test_measured_figures_are_not_flagged(self):
        assert detect_projection("Die Stromproduktion betrug 2020 rund 2'066 GWh.") is False
