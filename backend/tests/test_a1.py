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
        ("  a1:b2  ", (None, 1, 1, 2, 2)),
        ("A", (None, 1, 1, 0, 1)),
        ("A:A", (None, 1, 1, 0, 1)),
        ("Sheet1!B:D", ("Sheet1", 1, 2, 0, 4)),
        ("2:5", (None, 2, 1, 5, 0)),
        ("SF1", (None, 1, 500, 1, 500)),
        ("A1000000000", (None, 1000000000, 1, 1000000000, 1)),
    ],
)
def test_parse_a1_valid_ranges(text, expected):
    assert parse_a1(text) == A1Range(*expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "A1:",
        "Sheet1!A1:D",
        "ZZZZZZZZZZZZZZZ1",
        "SG1",
        "B2:A1",
        "A2:B1",
        "'Sheet1!A1",
        "1:0",
        ":D10",
        "A:",
        "A0",
        "2",
        "!A1",
        "'Bad'Name'!A1",
        " A1:\n ",
    ],
)
def test_parse_a1_invalid_range_identifies_problem_and_input(text):
    with pytest.raises(InvalidRangeError) as exc_info:
        parse_a1(text)
    assert text in str(exc_info.value)
    assert len(str(exc_info.value)) > len(text) + 5


@pytest.mark.parametrize(("letters", "index"), [("A", 1), ("Z", 26), ("AA", 27), ("SF", 500)])
def test_column_conversion(letters, index):
    assert col_index(letters) == index
    assert col_index(letters.lower()) == index
    assert col_letter(index) == letters


@pytest.mark.parametrize("letters", ["", "A1", "A B", "é"])
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
        (A1Range("Sheet1", 1, 1, 10, 4), "Sheet1!A1:D10"),
        (A1Range("My Data", 2, 2, 2, 2), "'My Data'!B2"),
        (A1Range("It's! Data", 10, 27, 12, 28), "'It''s! Data'!AA10:AB12"),
        (A1Range(None, 1, 1, 0, 1), "A:A"),
        (A1Range("Sheet1", 1, 2, 0, 4), "Sheet1!B:D"),
        (A1Range(None, 2, 1, 5, 0), "2:5"),
    ],
)
def test_format_a1_round_trips(value, expected):
    assert format_a1(value) == expected
    assert parse_a1(expected) == value
