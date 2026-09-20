"""SCRUM-42 - a Turkish question resolved to concrete, correctly-chosen series.

Everything here guards a way of getting a confident wrong answer rather than an error.
"""

from __future__ import annotations

import pytest

from kkb_agent.catalog.retrieval import build_concepts
from kkb_agent.catalog.series_resolver import (
    detect_currency,
    detect_province,
    detect_scope,
    resolve_series,
)

PROVINCES = frozenset({"İSTANBUL", "ANKARA", "VAN"})


def row(series_id: str, source: str, label: str, **kw) -> dict:
    base = {
        "series_id": series_id,
        "source": source,
        "raw_label": label,
        "name_tr": label,
        "measure_type": "stock",
        "unit_normalized": "TRY",
        "sector_scope": None,
        "native_freq": "M",
        "province": None,
        "currency_basis": None,
        "nonzero_observations": 66,
    }
    base.update(kw)
    return base


@pytest.fixture
def catalog() -> list[dict]:
    """Shaped like the real thing: a national measure per bank group, the same subject
    published per province by another source, a rate, and a dead row."""
    rows = [
        row(f"aylik.konut.{s}", "bddk_aylik", "Tüketici Kredileri - Konut", sector_scope=s)
        for s in ("Sektör", "Katılım", "Kamu")
    ]
    rows += [
        row(f"aylik.takip.{s}", "bddk_aylik", "Takipteki Konut Kredileri", sector_scope=s)
        for s in ("Sektör", "Katılım")
    ]
    rows += [
        row(
            f"finturk.konut.{il}",
            "bddk_finturk",
            "Konut Kredisi",
            province=il,
            sector_scope="Sektör",
        )
        for il in PROVINCES
    ]
    rows.append(
        row(
            "evds.rate",
            "evds",
            "Konut Kredisi (TL, Akım, %)",
            measure_type="rate",
            unit_normalized="%",
        )
    )
    # Published every month as 0.0 - a real row and a useless answer.
    rows.append(
        row(
            "aylik.dead",
            "bddk_aylik",
            "Ferdi Kredi Konut",
            sector_scope="Sektör",
            nonzero_observations=0,
        )
    )
    return rows


@pytest.fixture
def concepts(catalog):
    return build_concepts(catalog)


def resolve(concepts, catalog, query: str):
    return resolve_series(concepts, query, candidates=catalog, provinces=PROVINCES, limit=8)


class TestFacetDetection:
    @pytest.mark.parametrize(
        ("query", "scope"),
        [
            ("katılım bankalarının konut kredisi", "Katılım"),
            ("kamu bankaları konut kredisi", "Kamu"),
            ("yabancı bankalar mevduatı", "Yabancı"),
        ],
    )
    def test_a_named_bank_group_is_read_from_the_question(self, query, scope):
        assert detect_scope(query) == (scope, True)

    def test_an_unnamed_group_defaults_to_the_sector_and_says_so(self):
        """The default is right; making it silently is not, because then a question about
        the whole sector and one about Katılım look identical in the trace."""
        assert detect_scope("konut kredisi") == ("Sektör", False)

    def test_a_named_province_is_read_from_the_catalog_list(self):
        assert detect_province("İstanbul konut kredisi", PROVINCES) == ("İSTANBUL", True)

    def test_no_province_means_nationwide_which_is_an_answer_not_a_failure(self):
        assert detect_province("konut kredisi", PROVINCES) == (None, False)

    def test_currency_basis_defaults_to_the_total(self):
        assert detect_currency("konut kredisi") == ("Toplam", False)
        assert detect_currency("döviz mevduatı") == ("YP", True)


class TestResolution:
    def test_a_plain_question_resolves_to_the_sector_series(self, concepts, catalog):
        r = resolve(concepts, catalog, "konut kredisi")
        assert r.series_ids == ("aylik.konut.Sektör",)

    def test_a_named_bank_group_changes_the_series_not_the_measure(self, concepts, catalog):
        r = resolve(concepts, catalog, "katılım bankalarının konut kredisi")
        assert r.series_ids == ("aylik.konut.Katılım",)
        assert r.concept.label == "Tüketici Kredileri - Konut"

    def test_a_named_province_moves_to_the_source_that_has_provinces(self, concepts, catalog):
        r = resolve(concepts, catalog, "İstanbul konut kredisi")
        assert r.series_ids == ("finturk.konut.İSTANBUL",)
        assert r.concept.source == "bddk_finturk"

    def test_a_nationwide_question_avoids_the_province_only_source(self, concepts, catalog):
        """FinTürk has no nationwide row at all, so answering from it returns 82 provincial
        series for a question about Türkiye."""
        r = resolve(concepts, catalog, "konut kredisi")
        assert r.concept.source == "bddk_aylik"

    def test_the_facet_choices_are_on_the_record(self, concepts, catalog):
        r = resolve(concepts, catalog, "konut kredisi")
        stated = {f.facet: f.stated for f in r.facets}
        assert stated == {"sector_scope": False, "province": False, "currency_basis": False}

    def test_the_trace_explains_the_choice(self, concepts, catalog):
        r = resolve(concepts, catalog, "katılım bankalarının konut kredisi")
        joined = " | ".join(r.trace)
        assert "Tüketici Kredileri - Konut" in joined
        assert "Katılım" in joined


class TestWrongAnswersItRefuses:
    def test_a_dead_series_never_outranks_one_with_data(self, concepts, catalog):
        """'Ferdi Kredi Konut' scores higher on the name and is 0.0 in all 66 months."""
        r = resolve(concepts, catalog, "konut kredisi")
        assert r.concept.label != "Ferdi Kredi Konut"

    def test_an_unrequested_qualifier_loses_to_the_plain_measure(self, concepts, catalog):
        """A question that does not say 'takipteki' is not about non-performing loans."""
        assert resolve(concepts, catalog, "konut kredisi").concept.label == (
            "Tüketici Kredileri - Konut"
        )

    def test_the_qualifier_wins_when_it_is_asked_for(self, concepts, catalog):
        r = resolve(concepts, catalog, "takipteki konut kredileri")
        assert r.concept.label == "Takipteki Konut Kredileri"

    def test_a_flow_question_is_flagged_not_silently_served_a_stock(self, concepts, catalog):
        """The demo asks for kullandırılan. No source publishes it, and serving the
        balance as new lending is the failure a domain judge catches."""
        r = resolve(concepts, catalog, "konut kredisi kullandırımı")
        assert r.needs_disclosure
        assert r.substitution.asked_for == "flow"
        assert r.substitution.served == "stock"
        assert "flow" in r.substitution.notice_tr()
        assert r.series_ids, "it still answers - with the substitution labelled"

    def test_a_satisfiable_question_carries_no_disclosure(self, concepts, catalog):
        """An unconditional warning trains people to ignore it."""
        assert not resolve(concepts, catalog, "konut kredisi faizi").needs_disclosure

    def test_an_unmatched_question_resolves_to_nothing(self, concepts, catalog):
        r = resolve(concepts, catalog, "altın rezervleri")
        assert not r.resolved
        assert r.concept is None


class TestFacetsDecideWhichMeasureAnswers:
    """The highest-scoring measure is often not the one published at the grain asked for."""

    def test_a_nationwide_question_is_not_answered_with_provincial_series(self, concepts, catalog):
        """FinTürk publishes "Konut Kredisi" per province and has no nationwide row. The
        walk has to reach past it to the BDDK measure that does, rather than serving one
        province per row."""
        resolution = resolve(concepts, catalog, "konut kredisi")
        assert resolution.concept.source == "bddk_aylik"
        assert all("finturk" not in series_id for series_id in resolution.series_ids)

    def test_a_named_province_is_binding_on_the_source(self, concepts, catalog):
        """A source that does not carry province is skipped rather than filtered - right
        for an unstated facet, wrong for one the user named, or a nationwide series
        answers a question about İstanbul."""
        resolution = resolve(concepts, catalog, "İstanbul konut kredisi")
        assert resolution.series_ids
        assert all("İSTANBUL" in s or "istanbul" in s.casefold() for s in resolution.series_ids)

    def test_an_unstated_facet_still_skips_sources_that_lack_it(self, concepts, catalog):
        """The skip is what lets an EVDS rate answer at all - it carries no bank group."""
        resolution = resolve(concepts, catalog, "konut kredisi faizi")
        assert resolution.series_ids
