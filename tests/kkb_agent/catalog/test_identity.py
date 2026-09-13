"""SCRUM-96 - stable series identity for BDDK rows.

The four confirmed real cases are tested by name, plus the two ways normalisation can go
wrong: stripping parentheses that carry meaning, and Turkish casefolding.
"""

from __future__ import annotations

import pytest

from kkb_agent.catalog.identity import identify, normalise_label, turkish_casefold


class TestNormalisation:
    @pytest.mark.parametrize(
        "variants",
        [
            # t12 - the row-number formula shifts as rows are inserted above it
            [
                "Risk Ağırlıklı Kalemler Toplamı (10+27+28)",
                "Risk Ağırlıklı Kalemler Toplamı (10+29+30)",
                "Risk Ağırlıklı Kalemler Toplamı (10+30+31)",
            ],
            # t08 - a Turkish row-range reference
            [
                "Menkul Değerler (2 den 24'e)",
                "Menkul Değerler (2 den 26'ya)",
            ],
            [
                "TOPLAM MENKUL DEĞERLER (2 den 24'e)",
                "TOPLAM MENKUL DEĞERLER (2 den 26'ya)",
            ],
            # t11 - the whole reference block moves
            [
                "Türev İşlemler (12+13+14+15+16+17)",
                "Türev İşlemler (36+37+38+39+40+41)",
            ],
        ],
    )
    def test_label_variants_collapse_to_one_identity(self, variants):
        normalised = {normalise_label(v) for v in variants}
        assert len(normalised) == 1, f"variants did not converge: {normalised}"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Tüketici Kredileri (2+3+4)", "Tüketici Kredileri"),
            ("Taksitli Ticari Krediler (20+21+22)*", "Taksitli Ticari Krediler"),
            ("Kurumsal Kredi Kartları (28+29)**", "Kurumsal Kredi Kartları"),
            ("Kredi Kartlarından Alacaklar***", "Kredi Kartlarından Alacaklar"),
            ("  Toplam   Krediler  ", "Toplam Krediler"),
        ],
    )
    def test_strips_references_and_footnotes(self, raw, expected):
        assert normalise_label(raw) == expected

    @pytest.mark.parametrize(
        "label",
        [
            # Parentheses here carry meaning and must survive.
            "Tüketici Kredileri - Konut (Dövize Endeksli)",
            "Dönem Karı (Zararı)",
            "Kambiyo Karları (Zararları) (Net)",
            "Bankalardan Alınan Faizler (Gelirler)",
            "Mali Kesime (Banka Dışı) Kullandırılan Krediler",
        ],
    )
    def test_meaningful_parentheses_survive(self, label):
        assert normalise_label(label) == label


class TestTurkishCasefold:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("İSTANBUL", "istanbul"),
            ("ISTANBUL", "ıstanbul"),
            ("Konut Kredisi", "konut kredisi"),
            ("TÜKETİCİ", "tüketici"),
        ],
    )
    def test_folds_equal_pairs(self, a, b):
        assert turkish_casefold(a) == turkish_casefold(b)

    def test_dotted_and_dotless_i_stay_distinct(self):
        """The whole point: these are different letters in Turkish.

        str.lower() collapses them and silently merges unrelated series.
        """
        assert turkish_casefold("İSTANBUL") != turkish_casefold("ISTANBUL")


class TestIdentity:
    def test_same_series_across_label_changes_gets_one_id(self):
        a = identify("bddk_aylik", 12, 10001, "Risk Ağırlıklı Kalemler Toplamı (10+27+28)")
        b = identify("bddk_aylik", 12, 10001, "Risk Ağırlıklı Kalemler Toplamı (10+30+31)")
        assert a.series_id == b.series_id

    def test_same_label_in_different_tables_stays_distinct(self):
        """`Tüketici Kredileri` appears in both t03 and t04 and is not the same series."""
        a = identify("bddk_aylik", 3, 10001, "Tüketici Kredileri")
        b = identify("bddk_aylik", 4, 10001, "Tüketici Kredileri")
        assert a.series_id != b.series_id

    def test_same_label_in_different_sector_scopes_stays_distinct(self):
        a = identify("bddk_aylik", 4, 10001, "Tüketici Kredileri - Konut")
        b = identify("bddk_aylik", 4, 10002, "Tüketici Kredileri - Konut")
        assert a.series_id != b.series_id

    def test_raw_label_is_preserved(self):
        raw = "Risk Ağırlıklı Kalemler Toplamı (10+27+28)"
        ident = identify("bddk_aylik", 12, 10001, raw)
        assert ident.raw_label == raw
        assert ident.was_renamed is True

    def test_unchanged_label_is_not_marked_renamed(self):
        ident = identify("bddk_aylik", 4, 10001, "Tüketici Kredileri - Konut")
        assert ident.was_renamed is False

    def test_series_id_is_stable_and_slug_like(self):
        ident = identify("bddk_aylik", 4, 10001, "Tüketici Kredileri - Konut")
        assert ident.series_id.startswith("bddk_aylik.t04.taraf10001.")
        assert " " not in ident.series_id
        # Deterministic: the same label always yields the same id.
        again = identify("bddk_aylik", 4, 10001, "Tüketici Kredileri - Konut")
        assert ident.series_id == again.series_id
