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

from kkb_agent.catalog.build import iter_bddk_aylik, iter_bddk_haftalik, scope_matches

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
