"""SCRUM-17 follow-up - extracting tables from the weekly bulletin's HTML.

Markup here is trimmed from real acquired files under data/bronze/bddk/haftalik/.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from kkb_agent.transform.haftalik_html import extract, parse_header, period_matches

HEADER = "Sektör / Krediler                     (\n8 Ocak 2021 Cuma ) (Milyon TL)"


def page(rows_html: str, *, with_variants: bool = True) -> str:
    """A weekly page, optionally with all three precision variants."""

    def table(tid: str, body: str) -> str:
        return f"""<table id="{tid}">
            <tr><th></th><th>{HEADER}</th><th>TP</th><th>YP</th><th>TOPLAM</th></tr>
            {body}</table>"""

    coarse = table("Tablo", rows_html.replace(",03094", "").replace(",60672", ""))
    if not with_variants:
        return f"<html><body>{coarse}</body></html>"
    mid = table("TabloExcel", rows_html.replace(",03094", ",03").replace(",60672", ",61"))
    fine = table("TabloExcel2", rows_html)
    return f"<html><body>{coarse}{mid}{fine}</body></html>"


ROW = (
    "<tr><td>1</td><td>Toplam Krediler (2+10)</td>"
    "<td>2.336.746,03094</td><td>1.208.032,60672</td><td>3.544.778,63766</td></tr>"
)


class TestHeader:
    def test_reads_name_period_and_unit(self):
        name, period, unit = parse_header(HEADER)
        assert name == "Krediler"
        assert period == date(2021, 1, 8)
        assert unit == "Milyon TL"

    def test_missing_date_yields_none_rather_than_a_guess(self):
        _, period, _ = parse_header("Sektör / Krediler (Milyon TL)")
        assert period is None


class TestExtraction:
    def test_picks_the_richest_precision_variant(self):
        t = extract(page(ROW))
        assert t.source_table_id == "TabloExcel2"
        assert t.rows[0][1][0] == Decimal("2336746.03094")

    def test_falls_back_when_the_rich_variant_is_absent(self):
        t = extract(page(ROW, with_variants=False))
        assert t.source_table_id == "Tablo"
        assert t.rows[0][1][0] == Decimal("2336746")

    def test_columns_and_period_are_read_from_the_page(self):
        t = extract(page(ROW))
        assert t.columns == ["TP", "YP", "TOPLAM"]
        assert t.period == date(2021, 1, 8)
        assert t.unit_raw == "Milyon TL"

    def test_rows_with_no_numeric_cells_are_dropped(self):
        spacer = "<tr><td></td><td>BÖLÜM BAŞLIĞI</td><td></td><td></td><td></td></tr>"
        t = extract(page(ROW + spacer))
        assert len(t) == 1

    def test_returns_none_when_there_is_no_data_table(self):
        assert extract("<html><body><p>bakım</p></body></html>") is None


class TestPeriodGuard:
    def test_matching_period_passes(self):
        assert period_matches(date(2021, 1, 8), date(2021, 1, 8))

    def test_wrong_period_is_caught(self):
        """A report viewer serving the current week instead of the requested one."""
        assert not period_matches(date(2026, 6, 26), date(2021, 1, 8))

    def test_absent_period_never_counts_as_a_match(self):
        assert not period_matches(None, date(2021, 1, 8))
