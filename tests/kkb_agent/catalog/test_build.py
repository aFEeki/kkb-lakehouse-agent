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
    AYLIK_ROW_UNIT,
    FINTURK_T06_COLUMN,
    _label_unit,
    _measure_from,
    evds_measure,
    iter_bddk_aylik,
    iter_bddk_haftalik,
    scope_matches,
    to_frames,
)
from kkb_agent.catalog.schema import MeasureType, normalise_unit
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


class TestDecumulationIsApplied:
    """SCRUM-24 - classifying a series as accumulating is not the same as acting on it.

    599 series accumulate within the year. Before this, `value` held the running total,
    so a join reading June's "Dönem Karı" got six months of profit and called it one -
    a 4.4x error with no exception anywhere.
    """

    def write_profit(self, bronze: Path, period: str, ytd_total: int) -> None:
        """Table 2 is the income statement, which BDDK publishes year-to-date."""
        payload = {
            "Json": {
                "caption": f"Kar Zarar (milyon TL), Dönem:{period.replace('-0', '/')}",
                "colNames": ["", "", "", "BasitFont", "Toplam"],
                "data": {"rows": [{"cell": ["Sektör", 1, "Dönem Karı (Zararı)", "", ytd_total]}]},
            }
        }
        d = bronze / period
        d.mkdir(parents=True, exist_ok=True)
        (d / "t02_taraf10001.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    @pytest.fixture
    def profit_bronze(self, tmp_path: Path) -> Path:
        b = tmp_path / "aylik"
        for month, ytd in enumerate([100, 250, 420, 600], start=1):
            self.write_profit(b, f"2021-{month:02d}", ytd)
        return b

    def test_the_series_is_recognised_as_accumulating(self, profit_bronze: Path):
        meta, _ = next(iter(iter_bddk_aylik(profit_bronze)))
        assert str(meta.cumulative_mode) == "ytd"

    def test_value_is_the_period_not_the_running_total(self, profit_bronze: Path):
        pairs = list(iter_bddk_aylik(profit_bronze))
        catalog, observations = to_frames(pairs)
        assert len(catalog) == 1

        by_period = {str(r["period"]): r for r in observations.to_dict("records")}
        assert [by_period[f"2021-{m:02d}-01"]["value"] for m in (1, 2, 3, 4)] == [
            100,
            150,
            170,
            180,
        ]

    def test_the_published_total_is_kept_alongside(self, profit_bronze: Path):
        """Dropping it would make the year-end closure check impossible and lose the
        provenance the trust layer has to show."""
        _, observations = to_frames(list(iter_bddk_aylik(profit_bronze)))
        by_period = {str(r["period"]): r for r in observations.to_dict("records")}
        assert [by_period[f"2021-{m:02d}-01"]["value_reported"] for m in (1, 2, 3, 4)] == [
            100,
            250,
            420,
            600,
        ]

    def test_the_year_first_observation_is_its_own_value(self, profit_bronze: Path):
        """January has no prior month to difference against, so it IS January."""
        _, observations = to_frames(list(iter_bddk_aylik(profit_bronze)))
        january = next(
            r for r in observations.to_dict("records") if str(r["period"]).endswith("01-01")
        )
        assert january["value"] == january["value_reported"] == 100

    def test_a_level_series_is_left_alone(self, bronze: Path):
        """Balance-sheet rows do not accumulate, so both columns hold the same figure."""
        _, observations = to_frames(list(iter_bddk_aylik(bronze)))
        for r in observations.to_dict("records"):
            assert r["value"] == r["value_reported"]


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


class TestRowUnitBeatsCaption:
    """SCRUM-28 - a row that names its own unit is contradicting the caption on purpose.

    Tables 12 and 13 carry capital-adequacy and FX-position ratios labelled "(YÜZDE)"
    inside tables captioned "milyon TL". Reading the caption first made them stocks, and
    the bank-group partition check caught it: a ratio does not add across bank groups.
    """

    def test_a_percent_row_inside_a_lira_table_is_a_ratio(self, tmp_path: Path):
        payload = {
            "Json": {
                "caption": "Sermaye Yeterliliği (milyon TL), Dönem:2021/1",
                "colNames": ["", "", "", "BasitFont", "Toplam"],
                "data": {
                    "rows": [
                        {
                            "cell": [
                                "Sektör",
                                1,
                                "Sermaye Yeterliliği Standart Rasyosu ((5/7)*100) (YÜZDE)",
                                "",
                                18.5,
                            ]
                        },
                        {"cell": ["Sektör", 2, "Özkaynak", "", 1_500_000]},
                    ]
                },
            }
        }
        d = tmp_path / "aylik" / "2021-01"
        d.mkdir(parents=True)
        (d / "t12_taraf10001.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

        got = {m.name_tr: m for m, _ in iter_bddk_aylik(tmp_path / "aylik")}
        ratio = next(m for k, m in got.items() if "Rasyosu" in k)
        amount = next(m for k, m in got.items() if "Özkaynak" in k)

        assert ratio.unit_normalized == "%"
        assert ratio.measure_type is MeasureType.RATE
        # The caption still governs every row that does not override it.
        assert amount.unit_raw == "milyon TL"
        assert amount.measure_type is MeasureType.STOCK

    def test_the_one_row_that_declares_nothing_is_listed_explicitly(self):
        """'Likidite Yeterlilik Oranı' names no unit anywhere, so it needs an entry.

        A rule instead of a list - "a label containing oran or / is a ratio" - would
        misclassify 'TP Mevduat / Katılım Fonları' and 'Gemi/Tekne Yapımı'.
        """
        assert AYLIK_ROW_UNIT[(11, "Likidite Yeterlilik Oranı")] == ("%", MeasureType.RATIO)


class TestMeasureFromUnit:
    def test_a_count_is_a_count_even_under_a_ratio_table(self):
        """BDDK files ATM and branch counts inside "Rasyolar"; the unit has to win."""
        assert _measure_from(StatementKind.RATIO, "Adet") is MeasureType.COUNT

    def test_a_ratio_table_beats_the_percent_shortcut(self):
        """An NPL ratio takes the period end, not the mean of the months in it."""
        assert _measure_from(StatementKind.RATIO, "%") is MeasureType.RATIO

    @pytest.mark.parametrize("published", ["%", "YÜZDE", "Yüzde", "yüzde"])
    def test_percent_is_matched_after_normalising_not_as_written(self, published: str):
        """BDDK writes percent four ways across its tables. Comparing the raw string to
        "%" classified the capital-adequacy and FX-position ratios as balance amounts."""
        assert _measure_from(StatementKind.BALANCE_SHEET, published) is MeasureType.RATE

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


class TestEvdsMeasure:
    """SCRUM-99 - EVDS states its own unit, so stop inferring from the series name."""

    @pytest.mark.parametrize(
        ("name", "unit", "expected"),
        [
            ("Konut kredisi faizi (TL, akım)", "Yüzde", MeasureType.RATE),
            ("Takipteki alacaklar oranı", "Yüzde", MeasureType.RATIO),
            ("Tüketici Fiyat Endeksi", "2003=100", MeasureType.INDEX),
            ("Konut Fiyat Endeksi", "Endeks", MeasureType.INDEX),
            ("Bankacılık sektörü kredileri", "bin TL", MeasureType.STOCK),
            ("Toplam uluslararası rezervler", "milyon ABD doları", MeasureType.STOCK),
            ("Şube sayısı", "Adet", MeasureType.COUNT),
        ],
    )
    def test_unit_decides(self, name: str, unit: str, expected: MeasureType):
        assert evds_measure(name, unit) is expected

    def test_percent_splits_on_the_name_because_the_unit_cannot(self):
        """Yüzde covers both. A rate averages when downsampled; a ratio takes the end."""
        assert evds_measure("Ağırlıklı ortalama faiz", "Yüzde") is MeasureType.RATE
        assert evds_measure("Sermaye yeterlilik", "Yüzde") is MeasureType.RATIO

    def test_no_unit_means_unknown_rather_than_a_guess(self):
        """164 of 678 datagroups publish no unit. Those stay unservable, not assumed."""
        assert evds_measure("Bankacılık sektörü kredileri", "") is MeasureType.UNKNOWN

    def test_financial_accounts_flow_is_a_flow(self):
        """The only loan FLOW published anywhere we hold (SCRUM-95, DECISIONS #10)."""
        name = "F.4.Krediler, Hanehalkı (Konsolide Akım)"
        assert evds_measure(name, "bin TL") is MeasureType.FLOW

    def test_the_stock_twin_of_the_same_line_stays_a_stock(self):
        name = "VF.4.Krediler, Hanehalkı (Konsolide Olmayan, Stok)"
        assert evds_measure(name, "bin TL") is MeasureType.STOCK

    @pytest.mark.parametrize(
        "name",
        [
            "Konutun Tamir ve Bakımı",
            "Ev Bakımı ve Hizmetleri",
            "Sağlık ve Kişisel Bakım",
        ],
    )
    def test_bakim_is_not_read_as_akim(self, name: str):
        """'bakım' contains 'akım'. A substring test turns maintenance into a flow, and
        2,585 price-index series match it."""
        assert evds_measure(name, "bin TL") is MeasureType.STOCK


class TestEvdsUnits:
    @pytest.mark.parametrize(
        ("published", "expected"),
        [
            ("Yüzde", ("%", 1.0)),
            ("bin TL", ("TRY", 1_000.0)),
            ("milyon ABD doları", ("USD", 1_000_000.0)),
            ("bin ABD doları", ("USD", 1_000.0)),
            ("2003=100", ("endeks", 1.0)),
            ("Bin kişi", ("kişi", 1_000.0)),
        ],
    )
    def test_evds_spellings_normalise(self, published: str, expected: tuple[str, float]):
        assert normalise_unit(published) == expected

    @pytest.mark.parametrize("published", ["Yüzde, TL", "bin TL veya yüzde", "TL/m2"])
    def test_a_unit_naming_two_possibilities_is_refused(self, published: str):
        """The datagroup cannot say which applies, so neither can we. Unservable beats
        a coin flip that produces a plausible wrong number."""
        assert normalise_unit(published) == ("", 1.0)


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
