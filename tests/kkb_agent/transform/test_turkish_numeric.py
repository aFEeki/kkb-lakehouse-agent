"""SCRUM-17 - Turkish numeric parsing.

Every value here is a real cell shape observed in the acquired weekly bulletin files
under data/bronze/bddk/haftalik/.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kkb_agent.transform.turkish_numeric import (
    TurkishNumberError,
    looks_numeric,
    most_precise,
    parse_number,
    precision_of,
)


class TestTheInversionTrap:
    """The dot groups thousands. Getting this backwards is a 1000x error."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1.234", "1234"),
            ("7.535.809", "7535809"),
            ("12.694.338", "12694338"),
            ("440.671", "440671"),
            ("3.017", "3017"),
        ],
    )
    def test_dot_is_a_thousands_separator_not_a_decimal_point(self, text, expected):
        assert parse_number(text) == Decimal(expected)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1,234", "1.234"),
            ("40,71", "40.71"),
            ("0,5", "0.5"),
        ],
    )
    def test_comma_is_the_decimal_separator(self, text, expected):
        assert parse_number(text) == Decimal(expected)

    def test_the_two_are_not_the_same_number(self):
        assert parse_number("1.234") == Decimal("1234")
        assert parse_number("1,234") == Decimal("1.234")
        assert parse_number("1.234") != parse_number("1,234")


class TestRealCells:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("7.535.809,72", "7535809.72"),
            ("7.535.809,71518", "7535809.71518"),
            ("440.671,291", "440671.291"),
            ("12.694.338,76219", "12694338.76219"),
            ("11.228.402,00244", "11228402.00244"),
            ("90.089,29", "90089.29"),
            ("72", "72"),
            ("1", "1"),
        ],
    )
    def test_parses_observed_shapes(self, text, expected):
        assert parse_number(text) == Decimal(expected)

    def test_precision_is_preserved_exactly(self):
        """Decimal, not float - 5 decimal places on a trillion-lira figure."""
        assert str(parse_number("11.228.402,00244")) == "11228402.00244"


class TestNullsAreNeverZero:
    @pytest.mark.parametrize("text", ["", "   ", None, "-", "—", "n/a", "...", "*"])
    def test_placeholder_cells_become_none(self, text):
        assert parse_number(text) is None

    def test_none_is_not_zero(self):
        """A silent zero corrupts sums without leaving a trace."""
        assert parse_number("") is not Decimal(0)
        assert parse_number("") is None
        assert parse_number("0") == Decimal(0)  # a real zero still parses


class TestNegatives:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("-1.234,56", "-1234.56"), ("(1.234,56)", "-1234.56"), ("(72)", "-72")],
    )
    def test_signed_and_parenthesised(self, text, expected):
        assert parse_number(text) == Decimal(expected)


class TestRejection:
    @pytest.mark.parametrize(
        "text",
        [
            "Toplam Krediler",
            "Tüketici Kredileri - Konut",
            "12.34.56",  # not valid thousands grouping
            "1.23",  # a dot not grouping three digits
            "1.2345",
            "1,2,3",
            "abc",
            "%12,5",
        ],
    )
    def test_non_numbers_raise_rather_than_coerce(self, text):
        with pytest.raises(TurkishNumberError):
            parse_number(text)
        assert looks_numeric(text) is False

    def test_non_strict_returns_none_instead_of_raising(self):
        assert parse_number("Toplam Krediler", strict=False) is None


class TestPrecisionVariantSelection:
    def test_picks_the_variant_that_loses_least(self):
        """BDDK renders the same table at several precisions. Take the richest."""
        zero = ["1", "Toplam Krediler", "7.535.809", "3.692.592"]
        two = ["1", "Toplam Krediler", "7.535.809,72", "3.692.592,29"]
        five = ["1", "Toplam Krediler", "7.535.809,71518", "3.692.592,28726"]
        assert most_precise([zero, two, five]) == 2
        assert most_precise([five, zero, two]) == 0

    def test_position_is_not_assumed(self):
        """Decimal count varies by table, so selection must be measured not hardcoded."""
        three = ["440.671,291"]
        two = ["440.671,29"]
        assert most_precise([two, three]) == 1

    @pytest.mark.parametrize(
        ("text", "n"),
        [("7.535.809", 0), ("7.535.809,72", 2), ("440.671,291", 3), ("11.228.402,00244", 5)],
    )
    def test_precision_of(self, text, n):
        assert precision_of(text) == n

    def test_empty_variant_list_raises(self):
        with pytest.raises(ValueError, match="no variants"):
            most_precise([])
