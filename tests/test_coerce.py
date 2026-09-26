"""Parsing the PUF's text-typed numeric columns.

The distinction these tests protect: unreported is not zero.
"""
import pytest

from app.coerce import to_int, to_text


@pytest.mark.parametrize("raw,expected", [
    ("1234", 1234),
    ("1,234", 1234),
    ("  567  ", 567),
    ("0", 0),
    (890, 890),
    (12.0, 12),
    ("45%", 45),
    ("$1,000", 1000),
])
def test_real_numbers_parse(raw, expected):
    assert to_int(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", " ", "N/A", "n/a", "NA", "--", "*", "Not Applicable", None],
)
def test_missing_markers_become_none_not_zero(raw):
    """The whole point. A blank denial count is unknown, not 'denied nothing'."""
    assert to_int(raw) is None


def test_zero_is_preserved_and_is_not_missing():
    assert to_int("0") == 0
    assert to_int("0") is not None


@pytest.mark.parametrize("raw", ["abc", "12.5", "1.2.3", True, False])
def test_unparseable_values_become_none(raw):
    assert to_int(raw) is None


def test_text_missing_markers_become_none():
    assert to_text("N/A") is None
    assert to_text("  ") is None
    assert to_text(" Blue Cross ") == "Blue Cross"
