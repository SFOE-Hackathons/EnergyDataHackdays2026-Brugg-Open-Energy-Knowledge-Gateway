"""Unit tests for chart_parsing. No network, no MCP - pure functions only."""

from chart_parsing import extract_data_blocks, extract_title, parse_data_block

# Real transcription fragments captured from the knowledge base corpus.

BLOCK_A_TWO_RANGE_COLUMNS = (
    "Year,Wind Energy Production (TJ/a),Production Range (TJ/a) "
    "1990,0-2,0-5 1992,0-2,0-5 2000,0-5,0-10 2010,95-105,90-110 2024,600-610,590-620"
)

BLOCK_B_SINGLE_RANGE_COLUMN = (
    "Year,Wind Energy Production (TJ/a) 1990,0-5 2004,5-10 2022,605-615"
)

BLOCK_C_EXACT_WITH_TRAILING_PROSE = (
    "Year, Installed PV Capacity (MW) 2002, 2 2010, 48 2021, 705 "
    "2022 (estimated), 1000 Note: All values are presented as exact figures "
    "as labeled on the chart. The 2022 estimate of 1,000 MW is mentioned in "
    "the caption but not shown as a bar in the chart itself."
)

BLOCK_D_MULTI_COLUMN_WITH_COMMA_PROSE = (
    "Year,Number of PV Systems (Anzahl PV-Anlagen),"
    "Installed Capacity (Installierte Leistung in MW) "
    "2019,3000,120 2020,5500,150 2021,8500,250 2022,12200,340 "
    "Estimated Data Ranges: - Number of PV Systems 2019: 2,800 - 3,200"
)


class TestExtractDataBlocks:
    def test_extracts_single_block(self):
        text = f"some passage <data>{BLOCK_A_TWO_RANGE_COLUMNS}</data> more text"
        assert extract_data_blocks(text) == [
            {"content": BLOCK_A_TWO_RANGE_COLUMNS, "is_truncated": False}
        ]

    def test_extracts_multiple_blocks(self):
        text = "<data>first</data> filler <data>second</data>"
        assert [b["content"] for b in extract_data_blocks(text)] == ["first", "second"]

    def test_returns_empty_list_when_no_data_tag(self):
        assert extract_data_blocks("just a plain text passage, no tags here") == []


class TestTruncatedDataBlocksAreRecovered:
    """The upstream chunker splits on size, so a chunk can open a `<data>`
    block and end before closing it. Matching only balanced blocks threw away
    every complete row in such a block, silently."""

    TEXT = "<title>PV</title> <data>Year,Capacity (MW) 2023,1500 2024,1800 2025,"

    def test_partial_block_is_returned_rather_than_dropped(self):
        assert len(extract_data_blocks(self.TEXT)) == 1

    def test_partial_block_is_flagged(self):
        assert extract_data_blocks(self.TEXT)[0]["is_truncated"] is True

    def test_complete_rows_in_a_partial_block_still_parse(self):
        parsed = parse_data_block(extract_data_blocks(self.TEXT)[0]["content"])
        assert parsed["rows"][0]["Capacity (MW)"]["value"] == 1500.0
        assert parsed["rows"][1]["Capacity (MW)"]["value"] == 1800.0

    def test_severed_final_value_is_not_invented(self):
        parsed = parse_data_block(extract_data_blocks(self.TEXT)[0]["content"])
        severed = parsed["rows"][-1]["Capacity (MW)"]
        assert severed["value"] is None
        assert severed["kind"] == "text"

    def test_a_closed_block_followed_by_an_open_one_yields_both(self):
        blocks = extract_data_blocks("<data>Year,V 2001,1</data> mid <data>Year,V 2002,")
        assert [b["is_truncated"] for b in blocks] == [False, True]

    def test_closing_tag_after_the_last_open_tag_means_nothing_is_dangling(self):
        blocks = extract_data_blocks("<data>Year,V 2001,1</data> trailing prose")
        assert [b["is_truncated"] for b in blocks] == [False]


class TestExtractTitle:
    def test_extracts_title(self):
        text = "<title>Installed PV Capacity by Year</title><data>...</data>"
        assert extract_title(text) == "Installed PV Capacity by Year"

    def test_returns_none_when_no_title_tag(self):
        assert extract_title("<data>...</data>") is None

    def test_returns_none_for_empty_title(self):
        assert extract_title("<title>   </title>") is None


class TestParseDataBlockGarbage:
    def test_returns_none_for_unparseable_text(self):
        assert parse_data_block("this has no year-anchored rows at all, sorry") is None

    def test_returns_none_for_empty_string(self):
        assert parse_data_block("") is None


class TestParseDataBlockA:
    """Two range columns."""

    def setup_method(self):
        self.parsed = parse_data_block(BLOCK_A_TWO_RANGE_COLUMNS)

    def test_columns(self):
        assert self.parsed["columns"] == [
            "Year",
            "Wind Energy Production (TJ/a)",
            "Production Range (TJ/a)",
        ]

    def test_row_count(self):
        assert self.parsed["row_count"] == 5
        assert len(self.parsed["rows"]) == 5

    def test_precision_is_estimated(self):
        assert self.parsed["precision"] == "estimated"

    def test_first_row_range_cell(self):
        first_row = self.parsed["rows"][0]
        cell = first_row["Wind Energy Production (TJ/a)"]
        assert cell["kind"] == "range"
        assert cell["min"] == 0.0
        assert cell["max"] == 2.0
        assert cell["value"] is None

    def test_first_row_year_cell(self):
        first_row = self.parsed["rows"][0]
        cell = first_row["Year"]
        assert cell["kind"] == "exact"
        assert cell["value"] == 1990.0


class TestParseDataBlockB:
    """Single range column."""

    def setup_method(self):
        self.parsed = parse_data_block(BLOCK_B_SINGLE_RANGE_COLUMN)

    def test_columns(self):
        assert self.parsed["columns"] == ["Year", "Wind Energy Production (TJ/a)"]

    def test_row_count(self):
        assert self.parsed["row_count"] == 3

    def test_precision_is_estimated(self):
        assert self.parsed["precision"] == "estimated"


class TestParseDataBlockC:
    """Exact values, space after comma, annotated final year, trailing prose."""

    def setup_method(self):
        self.parsed = parse_data_block(BLOCK_C_EXACT_WITH_TRAILING_PROSE)

    def test_columns(self):
        assert self.parsed["columns"] == ["Year", "Installed PV Capacity (MW)"]

    def test_row_count_excludes_prose(self):
        assert self.parsed["row_count"] == 4
        assert len(self.parsed["rows"]) == 4

    def test_precision_is_exact(self):
        assert self.parsed["precision"] == "exact"

    def test_prose_absent_from_every_cell(self):
        for row in self.parsed["rows"]:
            for cell in row.values():
                assert "Note:" not in cell["raw"]
                assert "exact figures" not in cell["raw"]

    def test_last_row_year_raw_preserves_annotation(self):
        last_row = self.parsed["rows"][-1]
        assert last_row["Year"]["raw"] == "2022 (estimated)"

    def test_last_row_capacity_value(self):
        last_row = self.parsed["rows"][-1]
        assert last_row["Installed PV Capacity (MW)"]["value"] == 1000.0
        assert last_row["Installed PV Capacity (MW)"]["kind"] == "exact"


class TestParseDataBlockD:
    """Multi-column exact values with trailing prose containing comma-grouped numbers."""

    def setup_method(self):
        self.parsed = parse_data_block(BLOCK_D_MULTI_COLUMN_WITH_COMMA_PROSE)

    def test_columns(self):
        assert self.parsed["columns"] == [
            "Year",
            "Number of PV Systems (Anzahl PV-Anlagen)",
            "Installed Capacity (Installierte Leistung in MW)",
        ]

    def test_row_count(self):
        assert self.parsed["row_count"] == 4

    def test_precision_is_estimated_because_the_note_gives_ranges_for_these_values(self):
        # The rows read "2019,3000" while the note gives "2019: 2,800 - 3,200"
        # for that same column, so the tabulated values are estimates.
        assert self.parsed["precision"] == "estimated"

    def test_prose_number_range_never_becomes_a_row_or_leaks_into_a_cell(self):
        for row in self.parsed["rows"]:
            for cell in row.values():
                assert cell["raw"] != "2,800 - 3,200"
                assert "2,800" not in cell["raw"]
        # And no stray fifth row was produced from the prose.
        assert [row["Year"]["raw"] for row in self.parsed["rows"]] == [
            "2019",
            "2020",
            "2021",
            "2022",
        ]

    def test_last_row_values(self):
        last_row = self.parsed["rows"][-1]
        assert last_row["Number of PV Systems (Anzahl PV-Anlagen)"]["value"] == 12200.0
        assert last_row["Installed Capacity (Installierte Leistung in MW)"]["value"] == 340.0


BLOCK_E_EYEBALLED_SINGLE_VALUES = (
    "Year,Wind_Energy_Production_TJ/a 1990,2 1991,2 2000,12 2010,125 2024,620 "
    "Note: These values are estimated based on visual interpretation of the "
    "area chart. The exact values cannot be determined from the image alone, "
    "but the estimates capture the overall growth trajectory."
)


class TestDisclosedEstimatesAreNotReportedAsExact:
    """A real corpus block whose cells all look exact, while its own note says
    the values were eyeballed off an area chart. Reporting these as "exact"
    would have a caller present them as official published figures."""

    def setup_method(self):
        self.parsed = parse_data_block(BLOCK_E_EYEBALLED_SINGLE_VALUES)

    def test_precision_is_downgraded_to_estimated(self):
        assert self.parsed["precision"] == "estimated"

    def test_rows_still_parse(self):
        assert self.parsed["row_count"] == 5
        assert self.parsed["rows"][-1]["Wind_Energy_Production_TJ/a"]["value"] == 620.0

    def test_note_is_exposed_to_the_caller(self):
        assert "estimated based on visual interpretation" in self.parsed["note"]

    def test_note_is_not_mistaken_for_data(self):
        for row in self.parsed["rows"]:
            for cell in row.values():
                assert "Note:" not in cell["raw"]


class TestNoteAbsentWhenNoProse:
    def test_note_is_none(self):
        assert parse_data_block(BLOCK_B_SINGLE_RANGE_COLUMN)["note"] is None


class TestExplicitExactnessClaimSuppressesTheDowngrade:
    def test_labeled_values_stay_exact_despite_the_word_estimate(self):
        # Block C's note asserts "exact figures as labeled on the chart" and
        # mentions an "estimate" only about a caption aside. Guards against the
        # estimation check over-firing on genuinely labeled data.
        assert parse_data_block(BLOCK_C_EXACT_WITH_TRAILING_PROSE)["precision"] == "exact"


class TestNumberNormalization:
    def test_swiss_apostrophe_thousands_separator(self):
        block = "Year,Value 2020,1'797.8 2021,1'800.0"
        parsed = parse_data_block(block)
        first_row_value = parsed["rows"][0]["Value"]
        assert first_row_value["kind"] == "exact"
        assert first_row_value["value"] == 1797.8
