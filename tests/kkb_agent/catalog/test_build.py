"""SCRUM-98 - the catalog must keep bank-group scopes apart.

The bug this guards against produced no error and no duplicate: with taraf missing from
the frame key, ten bank groups wrote into one frame and the last one read won, under the
first one's label. The catalog reported Mevduat-Yabancı's balance sheet as Sektör's -
a 5x error that looks entirely reasonable on a chart.

Payload shape is copied from data/bronze/bddk/aylik/*/t01_taraf*.json:
cell = [scope, row_index, label, font, TP, YP, Toplam].
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kkb_agent.catalog.build import (
    FINTURK_T06_COLUMN,
    _label_unit,
    _measure_from,
    iter_bddk_aylik,
    iter_bddk_haftalik,
    scope_matches,
)
from kkb_agent.catalog.schema import MeasureType
from kkb_agent.transform.cumulative import StatementKind

SCOPES = {10001: "Sektör", 10002: "Mevduat", 10003: "Katılım"}


def write_month(
    bronze: Path, period: str, taraf: int, total: int, *, scope: str | None = None
) -> None:
    """One monthly table file: a single row, TOPLAM AKTİFLER, with the given total."""
    payload = {
        "Json": {
            "caption": f"Bilanço (milyon TL), Dönem:{period.replace('-0', '/').replace('-', '/')}",
            "colNames": ["", "", "", "BasitFont", "TP", "YP", "Toplam"],
            "data": {
                "rows": [
                    {
                        "cell": [
                            scope if scope is not None else SCOPES[taraf],
                            1,
                            "TOPLAM AKTİFLER",
                            "",
                            total // 2,
                            total - total // 2,
                            total,
                        ]
                    }
                ]
            },
        }
    }
    d = bronze / period
    d.mkdir(parents=True, exist_ok=True)
    (d / f"t01_taraf{taraf}.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


@pytest.fixture
def bronze(tmp_path: Path) -> Path:
    """Two months of the same table under three bank-group scopes."""
    b = tmp_path / "aylik"
    for period, base in (("2021-01", 1_000), ("2021-02", 1_100)):
        write_month(b, period, 10001, base * 6)  # Sektör
        write_month(b, period, 10002, base * 5)  # Mevduat
        write_month(b, period, 10003, base * 1)  # Katılım
    return b


class TestScopesStayApart:
    def test_each_scope_yields_its_own_series(self, bronze: Path):
        got = list(iter_bddk_aylik(bronze))
        assert len(got) == 3, "one series per bank-group scope, not one shared series"

    def test_values_are_not_overwritten_across_scopes(self, bronze: Path):
        by_scope = {m.sector_scope: s for m, s in iter_bddk_aylik(bronze)}
        jan = pd.Timestamp(2021, 1, 1)
        assert by_scope["Sektör"][jan] == 6_000
        assert by_scope["Mevduat"][jan] == 5_000
        assert by_scope["Katılım"][jan] == 1_000

    def test_series_ids_are_distinct(self, bronze: Path):
        ids = [m.series_id for m, _ in iter_bddk_aylik(bronze)]
        assert len(set(ids)) == len(ids)

    def test_scope_is_named_not_coded(self, bronze: Path):
        """'10001' embeds as noise; the question asks about 'Sektör'."""
        scopes = {m.sector_scope for m, _ in iter_bddk_aylik(bronze)}
        assert scopes == {"Sektör", "Mevduat", "Katılım"}

    def test_each_scope_keeps_its_full_history(self, bronze: Path):
        for _, s in iter_bddk_aylik(bronze):
            assert len(s) == 2


class TestScopeGuard:
    def test_payload_stating_our_scope_passes(self):
        assert scope_matches([{"cell": ["Sektör", 1, "x"]}], 10001)

    def test_payload_stating_another_scope_is_rejected(self):
        """The viewer ignoring our taraf parameter and serving Sektör for all of them."""
        assert not scope_matches([{"cell": ["Sektör", 1, "x"]}], 10002)

    def test_unknown_taraf_code_is_not_rejected(self):
        """A scope BDDK adds later has no expectation to compare against."""
        assert scope_matches([{"cell": ["Yeni Grup", 1, "x"]}], 19999)

    def test_empty_rows_pass_through(self):
        assert scope_matches([], 10001)

    def test_mismatched_file_contributes_nothing(self, tmp_path: Path):
        b = tmp_path / "aylik"
        write_month(b, "2021-01", 10002, 5_000, scope="Sektör")  # viewer served the wrong scope
        assert list(iter_bddk_aylik(b)) == []


class TestLabelUnit:
    """SCRUM-25 - table 15 "Rasyolar" is four units, and each row says which."""

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Takipteki Alacaklar (Brüt) / Toplam Nakdi Krediler (%)", "%"),
            ("Toplam Mevduat / Ortalama Toplam Personel Sayısı (Bin TL)", "Bin TL"),
            ("Toplam Personel Sayısı / Toplam Şube Sayısı (Kişi)", "Kişi"),
            ("Menkul Değerlerin Ağırlıklı Ortalama Vadesi (Gün)", "Gün"),
        ],
    )
    def test_reads_the_declared_unit(self, label: str, expected: str):
        assert _label_unit(label) == expected

    @pytest.mark.parametrize(
        "label",
        [
            "Risk Ağırlıklı Kalemler Toplamı (10+27+28)",  # a row-number reference
            "Yurt Dışı Şubeler Mevduatı (Fon) / Toplam Mevduat (Fon)",  # a qualifier
            "Banka Sayısı",  # no parenthesis at all
        ],
    )
    def test_a_parenthesis_that_is_not_a_unit_yields_nothing(self, label: str):
        """Labels end in parentheses all over this source. Only a known unit counts."""
        assert _label_unit(label) == ""

    def test_the_inner_parenthesis_is_not_mistaken_for_the_unit(self):
        label = "Yüksek Montanlı (1 Milyon TL ve Üzeri) Mevduat / Toplam Mevduat (%)"
        assert _label_unit(label) == "%"


class TestMeasureFromUnit:
    def test_a_count_is_a_count_even_under_a_ratio_table(self):
        """BDDK files ATM and branch counts inside "Rasyolar"; the unit has to win."""
        assert _measure_from(StatementKind.RATIO, "Adet") is MeasureType.COUNT

    def test_a_ratio_table_beats_the_percent_shortcut(self):
        """An NPL ratio takes the period end, not the mean of the months in it."""
        assert _measure_from(StatementKind.RATIO, "%") is MeasureType.RATIO

    def test_balance_sheet_in_lira_is_still_a_stock(self):
        assert _measure_from(StatementKind.BALANCE_SHEET, "milyon TL") is MeasureType.STOCK

    def test_nothing_determined_stays_unknown(self):
        assert _measure_from(None, "") is MeasureType.UNKNOWN


class TestFinturkTable6:
    """The one FinTürk table whose unit is a property of the column, not the table."""

    def test_every_column_is_accounted_for(self):
        assert len(FINTURK_T06_COLUMN) == 6

    def test_branch_counts_may_be_summed_across_provinces(self):
        unit, measure = FINTURK_T06_COLUMN["Yurtiçi Şube Sayısı"]
        assert (unit, measure) == ("Adet", MeasureType.COUNT)

    @pytest.mark.parametrize(
        "col",
        [
            "Şubeye Düşen Nüfus",
            "Kişi Başı Nakdi Kredi",
            "Kişi Başı Takipteki Alacak",
            "Kişi Başı Tasarruf Mevduatı",
            "Kişi Başı Toplam Mevduat",
        ],
    )
    def test_per_capita_figures_are_ratios_so_they_cannot_be_summed(self, col: str):
        """Adding per-capita lending across 81 provinces is meaningless, so mark it."""
        assert FINTURK_T06_COLUMN[col][1] is MeasureType.RATIO

    def test_per_capita_money_is_lira_not_thousand_lira(self):
        """Verified against table 1: kişi başı x nüfus reproduces Nakdi Krediler to
        within 0.02% across all 81 provinces. scripts/check_finturk_units.py re-runs it."""
        assert FINTURK_T06_COLUMN["Kişi Başı Nakdi Kredi"][0] == "TL"


class TestWeeklyCurrency:
    def test_currency_is_part_of_the_weekly_identity(self, tmp_path: Path):
        """Latent today - one currency is acquired - so the id is the only thing to assert."""
        html = """<html><body><table id="TabloExcel2">
            <tr><th></th><th>Sektör / Krediler ( 8 Ocak 2021 Cuma ) (Milyon TL)</th>
                <th>TP</th><th>YP</th><th>TOPLAM</th></tr>
            <tr><td>1</td><td>Toplam Krediler (2+10)</td>
                <td>2.336.746</td><td>1.208.032</td><td>3.544.778</td></tr>
        </table></body></html>"""
        for cur in ("TL", "USD"):
            d = tmp_path / cur / "2021-01-08"
            d.mkdir(parents=True)
            (d / "tablo289.html").write_text(html, "utf-8")

        ids = [m.series_id for m, _ in iter_bddk_haftalik(tmp_path)]
        assert len(set(ids)) == len(ids)
        assert any(".TL." in i for i in ids) and any(".USD." in i for i in ids)
