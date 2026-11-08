import pytest

from app.document_engine.a1 import (
    A1Range,
    InvalidRangeError,
    col_index,
    col_letter,
    format_a1,
    parse_a1,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A1", (None, 1, 1, 1, 1)),
        ("Sheet1!A1:D10", ("Sheet1", 1, 1, 10, 4)),
        ("'My Data'!B2", ("My Data", 2, 2, 2, 2)),
        ("'It''s! Data'!AA10:AB12", ("It's! Data", 10, 27, 12, 28)),
        ("'My:Data'!A1", ("My:Data", 1, 1, 1, 1)),
        ("  a1:b2  ", (None, 1, 1, 2, 2)),
        ("A", (None, 1, 1, 0, 1)),
        ("A:A", (None, 1, 1, 0, 1)),
        ("Sheet1!B:D", ("Sheet1", 1, 2, 0, 4)),
        ("2:5", (None, 2, 1, 5, 0)),
        ("SF1", (None, 1, 500, 1, 500)),
        ("SG1", (None, 1, 501, 1, 501)),
        ("ZZZZZZZZZZZZZ1", (None, 1, 2580398988131886038, 1, 2580398988131886038)),
        ("A1000000000", (None, 1000000000, 1, 1000000000, 1)),
    ],
)
def test_parse_a1_valid_ranges(text, expected):
    assert parse_a1(text) == A1Range(*expected)


@pytest.mark.parametrize(
    ("text", "reasons"),
    [
        ("", ("range is empty",)),
        ("A1:", ("end row", "column", "unbounded", "omit the range for the whole tab")),
        ("Sheet1!A1:D", ("end row", "unbounded", "omit the range for the whole tab")),
        ("A1:5", ("end column", "unbounded")),
        ("A:D10", ("start row", "unbounded")),
        ("5:A1", ("start column", "unbounded")),
        ("A:5", ("start row", "end column", "unbounded")),
        ("5:A", ("start column", "end row", "unbounded")),
        ("A1:B2:C3", ("two endpoints",)),
        ("ZZZZZZZZZZZZZZZ1", ("grid limit", "13 letters")),
        ("AAAAAAAAAAAAAA", ("grid limit", "13 letters")),
        ("B2:A1", ("inverted",)),
        ("A2:B1", ("inverted",)),
        ("'Sheet1!A1", ("unclosed",)),
        ("1:0", ("positive",)),
        (":D10", ("start row", "column", "unbounded", "omit the range for the whole tab")),
        ("A:", ("end row", "column", "unbounded", "omit the range for the whole tab")),
        ("A0", ("positive",)),
        ("2", ("two row endpoints",)),
        ("!A1", ("sheet name",)),
        ("Sheet1!", ("range is empty",)),
        ("''!A1", ("sheet name is empty",)),
        ("My Data!A1", ("sheet name", "spaces")),
        ("My:Data!A1", ("sheet name", ":")),
        ("'Bad'Name'!A1", ("quoted sheet name",)),
        (" A1:\n ", ("end row", "column", "unbounded")),
    ],
)
def test_parse_a1_invalid_range_identifies_problem_and_input(text, reasons):
    with pytest.raises(InvalidRangeError) as exc_info:
        parse_a1(text)
    assert text in str(exc_info.value)
    for reason in reasons:
        assert reason in str(exc_info.value)


@pytest.mark.parametrize(("letters", "index"), [("A", 1), ("Z", 26), ("AA", 27), ("SF", 500)])
def test_column_conversion(letters, index):
    assert col_index(letters) == index
    assert col_index(letters.lower()) == index
    assert col_letter(index) == letters


@pytest.mark.parametrize("letters", ["", "A1", "A B", "é", "A\nB"])
def test_col_index_rejects_invalid_letters(letters):
    with pytest.raises(InvalidRangeError) as exc_info:
        col_index(letters)
    assert letters in str(exc_info.value)


@pytest.mark.parametrize("index", [0, -1])
def test_col_letter_rejects_nonpositive_indices(index):
    with pytest.raises(InvalidRangeError, match=str(index)):
        col_letter(index)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (A1Range(None, 1, 1, 1, 1), "A1"),
        (A1Range("Sheet1", 1, 1, 10, 4), "'Sheet1'!A1:D10"),
        (A1Range("My Data", 2, 2, 2, 2), "'My Data'!B2"),
        (A1Range("It's! Data", 10, 27, 12, 28), "'It''s! Data'!AA10:AB12"),
        (A1Range(None, 1, 1, 0, 1), "A:A"),
        (A1Range("Sheet1", 1, 2, 0, 4), "'Sheet1'!B:D"),
        (A1Range(None, 2, 1, 5, 0), "2:5"),
        (A1Range("123", 1, 1, 1, 1), "'123'!A1"),
        (A1Range("1Data", 1, 1, 1, 1), "'1Data'!A1"),
        (A1Range("A1", 1, 1, 1, 1), "'A1'!A1"),
        (A1Range("R1C1", 1, 1, 1, 1), "'R1C1'!A1"),
        (A1Range("r1c1", 1, 1, 1, 1), "'r1c1'!A1"),
        (A1Range("Tab_1", 1, 1, 1, 1), "Tab_1!A1"),
    ],
)
def test_format_a1_round_trips(value, expected):
    assert format_a1(value) == expected
    assert parse_a1(expected) == value
