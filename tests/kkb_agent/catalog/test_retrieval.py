"""SCRUM-38 / SCRUM-39 - resolving a Turkish question to series in the catalog.

The catalog holds 47,015 series but only 872 distinct measures. Everything here rests on
that: the province and the bank group are facets of *which* series you want, not of *what*
it is, so they filter and never score.
"""

from __future__ import annotations

import pytest

from kkb_agent.catalog.retrieval import (
    Concept,
    build_concepts,
    detect_intent,
    lexical_score,
    resolve,
    search,
    tokenize,
)


def row(source: str, label: str, **kw) -> dict:
    base = {
        "source": source,
        "raw_label": label,
        "measure_type": "stock",
        "unit_normalized": "TRY",
        "sector_scope": "Sektör",
        "native_freq": "M",
        "province": None,
    }
    base.update(kw)
    return base


@pytest.fixture
def concepts() -> list[Concept]:
    """A slice shaped like the real catalog: one measure repeated across provinces,
    alongside the national rate that answers a question about the same subject."""
    rows = [
        *[
            row("bddk_finturk", "Konut Kredisi", province=il, sector_scope=g, native_freq="Q")
            for il in ("ADANA", "İSTANBUL", "VAN")
            for g in ("SEKTÖR", "MEVDUAT")
        ],
        row("bddk_aylik", "Tüketici Kredileri - Konut"),
        row("evds", "Konut Kredisi (TL, Akım, %)", measure_type="rate", unit_normalized="%"),
        row("evds", "Konut Fiyat Endeksi (KFE)", measure_type="index", unit_normalized="endeks"),
        row("bddk_finturk", "Yurtiçi Şube Sayısı", measure_type="count", unit_normalized="adet"),
    ]
    return build_concepts(rows)


class TestTokenize:
    def test_turkish_casefold_not_str_lower(self):
        """str.lower() maps 'I' to 'i' where Turkish needs 'ı'. A question typed in caps
        would silently match nothing."""
        assert tokenize("İHTİYAÇ KREDİSİ") == tokenize("ihtiyaç kredisi")

    def test_stopwords_are_dropped(self):
        assert "ve" not in tokenize("krediler ve mevduat")

    def test_punctuation_does_not_become_a_token(self):
        assert tokenize("Konut Kredisi (TL, Akım, %)") == ("konut", "kredisi", "tl", "akım")


class TestBuildConcepts:
    def test_one_measure_repeated_across_provinces_is_one_concept(self, concepts):
        """Six FinTürk rows - three provinces x two bank groups - are one measure."""
        konut = next(c for c in concepts if c.key == ("bddk_finturk", "Konut Kredisi"))
        assert konut.series_count == 6
        assert konut.has_provinces is True

    def test_the_same_label_from_a_different_source_stays_separate(self, concepts):
        """BDDK's balance and EVDS's rate are different measures, not one concept."""
        labels = {c.key for c in concepts}
        assert ("bddk_finturk", "Konut Kredisi") in labels
        assert ("evds", "Konut Kredisi (TL, Akım, %)") in labels

    def test_facets_are_collected_not_flattened_into_the_name(self, concepts):
        konut = next(c for c in concepts if c.key == ("bddk_finturk", "Konut Kredisi"))
        assert konut.scopes == frozenset({"SEKTÖR", "MEVDUAT"})
        assert konut.frequencies == frozenset({"Q"})

    def test_a_row_with_no_label_is_skipped_rather_than_keyed_on_empty(self):
        assert build_concepts([row("evds", "")]) == []


class TestLexicalScore:
    def test_a_tight_match_beats_a_label_that_merely_contains_the_query(self, concepts):
        """Query coverage alone ranks these equal - both contain 'konut'. Requiring the
        label to be covered too prefers the one that is actually about housing loans."""
        hits = search(concepts, "konut kredisi", limit=3)
        assert hits[0].concept.label == "Konut Kredisi"

    def test_turkish_suffixes_still_match_the_stem(self):
        """A user writes 'konut kredisi', BDDK writes 'Konut Kredileri'. They share the
        stem and neither contains the other, so a one-directional prefix test scores this
        zero — which is what it did before _pair_score compared shared prefixes."""
        c = Concept(
            source="x", label="Konut Kredileri", tokens=tokenize("Konut Kredileri"), series_count=1
        )
        score, matched = lexical_score(tokenize("konut kredisi"), c)
        assert score > 0.0
        assert set(matched) == {"konut", "kredisi"}

    def test_an_inflected_match_ranks_below_an_exact_one(self):
        """Partial stem agreement is genuinely less certain, and the score says so."""
        exact = Concept(
            source="x", label="Konut Kredisi", tokens=tokenize("Konut Kredisi"), series_count=1
        )
        inflected = Concept(
            source="x", label="Konut Kredileri", tokens=tokenize("Konut Kredileri"), series_count=1
        )
        query = tokenize("konut kredisi")
        assert lexical_score(query, exact)[0] > lexical_score(query, inflected)[0]

    def test_an_inflected_match_still_beats_an_unrelated_label(self):
        inflected = Concept(
            source="x", label="Konut Kredileri", tokens=tokenize("Konut Kredileri"), series_count=1
        )
        unrelated = Concept(
            source="x", label="Menkul Değerler", tokens=tokenize("Menkul Değerler"), series_count=1
        )
        query = tokenize("konut kredisi")
        assert lexical_score(query, inflected)[0] > lexical_score(query, unrelated)[0]

    def test_a_short_stem_does_not_match_across_meanings(self):
        """'kar' must not reach 'karşılık'. MIN_STEM is what stops it."""
        c = Concept(source="x", label="Karşılık", tokens=tokenize("Karşılık"), series_count=1)
        score, _ = lexical_score(tokenize("kar"), c)
        assert score == 0.0

    def test_no_overlap_scores_zero(self, concepts):
        assert search(concepts, "altın rezervleri") == []


class TestDetectIntent:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("konut kredisi faizi", {"rate"}),
            ("tüketici fiyat endeksi", {"index"}),
            ("şube sayısı", {"count"}),
            ("konut kredisi kullandırımı", {"flow"}),
            ("takipteki alacak oranı", {"ratio", "rate"}),
        ],
    )
    def test_reads_the_kind_of_measure_asked_for(self, query: str, expected: set[str]):
        assert detect_intent(query).measure_types == expected

    def test_a_question_naming_no_measure_kind_has_no_intent(self):
        assert not detect_intent("konut kredisi")

    def test_conflicting_cues_keep_both_rather_than_picking(self):
        """Guessing which one the user meant is how a wrong series gets served
        confidently."""
        assert detect_intent("konut fiyat endeksi oranı").measure_types == {
            "index",
            "ratio",
            "rate",
        }


class TestResolve:
    def test_intent_filters_rather_than_nudging(self, concepts):
        """The bug this exists to fix: 'faizi' appears in no BDDK label, so it added
        nothing to the score and the housing loan BALANCE outranked the rate."""
        r = resolve(concepts, "konut kredisi faizi")
        assert r.intent_satisfied
        assert r.best.concept.source == "evds"
        assert "rate" in r.best.concept.measure_types

    def test_every_offered_hit_satisfies_the_intent(self, concepts):
        """Not just the top one - a balance served for a rate question is wrong rather
        than second-best, so it should not be in the list at all."""
        r = resolve(concepts, "konut kredisi faizi")
        assert all("rate" in h.concept.measure_types for h in r.hits)

    def test_an_unsatisfiable_intent_is_flagged_not_hidden(self, concepts):
        """No source publishes gross new lending. Falling back to the balance is right;
        doing it silently is not - the flag is what lets the answer say so."""
        r = resolve(concepts, "konut kredisi kullandırımı")
        assert r.intent.measure_types == {"flow"}
        assert r.intent_satisfied is False
        assert r.best.concept.label == "Konut Kredisi"

    def test_no_intent_means_nothing_was_given_up(self, concepts):
        r = resolve(concepts, "konut kredisi")
        assert r.intent_satisfied is True

    def test_a_question_matching_nothing_resolves_to_nothing(self, concepts):
        r = resolve(concepts, "altın rezervleri")
        assert r.hits == ()
        assert r.best is None


def test_filler_words_do_not_sink_a_question_that_matches():
    """Scoring is a harmonic mean of query and concept coverage, so words the catalog can
    never match pull the query side down. "Toplam mevduat" scored 1.000 while "Toplam
    mevduat son 5 yılda nasıl değişti?" matched nothing at all."""
    from kkb_agent.catalog.retrieval import tokenize

    # A bare digit survives - labels do carry numbers - but the question vocabulary goes,
    # which is what took this from no match at all to resolving.
    assert tokenize("Toplam mevduat son 5 yılda nasıl değişti?") == ("mevduat", "5")
    assert tokenize("Konut kredisi ne kadar arttı") == ("konut", "kredisi", "arttı")
