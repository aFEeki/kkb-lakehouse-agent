"""SCRUM-42 - turn a Turkish question into concrete, correctly-chosen series.

`retrieval.resolve` answers *what* the question is about: one of 872 measures. This goes
the rest of the way, to a `series_id` that can be queried - and that second step is where
the choices that make a number wrong get made.

    "katılım bankalarının konut kredisi"   -> which of 10 bank groups?
    "İstanbul'da konut kredisi"            -> which of 82 provinces?
    "konut kredisi kullandırımı"           -> a flow, which nobody publishes

Three rules, each of which exists because the alternative produces a confident wrong
answer rather than an error:

**Facets filter on catalog fields, never on the name.** A question naming Katılım is
answered from `sector_scope`, not from a label that happens to contain the word.

**An unstated facet takes a default, and the trace says it was a default.** Most questions
do not name a bank group; answering them for the whole sector is right. Answering them for
the whole sector *silently* is not, because "konut kredisi ne kadar" and "katılım
bankalarının konut kredisi ne kadar" then look identical in the trace.

**An intent the catalog cannot satisfy is surfaced, never substituted.** The demo asks for
*kullandırılan* housing loans - a flow. No source publishes it. Serving the stock and
calling it new lending is the single failure a domain judge will catch, so the resolution
carries the substitution explicitly for the answer to state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kkb_agent.catalog.retrieval import Concept, Hit, Resolution, detect_intent, tokenize
from kkb_agent.catalog.retrieval import resolve as resolve_concept

# Defaults for a facet the question does not name. Each is the broadest reading, which is
# what an unqualified question means - "konut kredisi" is the sector's, nationwide.
DEFAULT_SCOPE = "Sektör"
DEFAULT_CURRENCY = "Toplam"

# Bank groups as a question would name them, mapped to the catalog's canonical spelling.
# Matched on tokens, so "katılım bankalarının" reaches "Katılım".
SCOPE_CUES: dict[str, str] = {
    "sektör": "Sektör",
    "mevduat": "Mevduat",
    "katılım": "Katılım",
    "kalkınma": "Kalkınma ve Yatırım",
    "yatırım": "Kalkınma ve Yatırım",
    "kamu": "Kamu",
    "yabancı": "Yabancı",
    "özel": "Yerli Özel",
    "yerli": "Yerli Özel",
}

CURRENCY_CUES: dict[str, str] = {
    "tp": "TP",
    "yp": "YP",
    "türk": "TP",
    "döviz": "YP",
    "yabancı para": "YP",
}


@dataclass(frozen=True)
class FacetChoice:
    """One facet decision, and whether the question actually made it."""

    facet: str
    value: str | None
    stated: bool
    reason: str

    def __str__(self) -> str:
        where = "soruda belirtildi" if self.stated else "varsayılan"
        return f"{self.facet}={self.value or '—'} ({where}: {self.reason})"


@dataclass(frozen=True)
class Substitution:
    """What was asked for, what is being served instead, and why."""

    asked_for: str
    served: str
    reason: str

    def notice_tr(self) -> str:
        return (
            f"Soru {self.asked_for} istiyor; elimizdeki veri {self.served}. "
            f"{self.reason} Sonuç bu şekilde etiketlenmelidir."
        )


@dataclass(frozen=True)
class SeriesResolution:
    """A question resolved to concrete series, with every choice on the record."""

    query: str
    concept: Concept | None
    series_ids: tuple[str, ...]
    facets: tuple[FacetChoice, ...] = ()
    substitution: Substitution | None = None
    alternatives: tuple[Hit, ...] = ()
    trace: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return bool(self.series_ids)

    @property
    def needs_disclosure(self) -> bool:
        """Whether the answer must say something beyond the number."""
        return self.substitution is not None


def detect_scope(query: str) -> tuple[str, bool]:
    """The bank group the question names, or the default.

    Returns (scope, stated). "Mevduat" is checked last among its compounds so that
    "mevduat bankaları" does not shadow "kamu mevduat bankaları".
    """
    tokens = set(tokenize(query))
    for cue, scope in SCOPE_CUES.items():
        if cue in tokens and scope != "Mevduat":
            return scope, True
    if "mevduat" in tokens:
        return "Mevduat", True
    return DEFAULT_SCOPE, False


def detect_province(query: str, known: frozenset[str]) -> tuple[str | None, bool]:
    """The province the question names, or None for nationwide.

    Matched against the catalog's own province list rather than a hardcoded one, so a
    spelling only BDDK uses still resolves. None is a real answer here, not a failure:
    most questions are about Türkiye.
    """
    tokens = set(tokenize(query))
    for province in known:
        if set(tokenize(province)) <= tokens:
            return province, True
    return None, False


def detect_currency(query: str) -> tuple[str, bool]:
    tokens = set(tokenize(query))
    for cue, basis in CURRENCY_CUES.items():
        if set(tokenize(cue)) <= tokens:
            return basis, True
    return DEFAULT_CURRENCY, False


FACET_SEARCH_DEPTH = 30


def resolve_series(
    concepts: list[Concept],
    query: str,
    *,
    candidates: list[dict],
    provinces: frozenset[str] = frozenset(),
    limit: int = 5,
    ranked_hits: tuple[Hit, ...] | None = None,
    strict: bool = False,
) -> SeriesResolution:
    """Resolve a question to series ids.

    `candidates` are catalog rows - dicts with series_id, source, raw_label, sector_scope,
    province, currency_basis, measure_type. Filtering happens on those fields, never on
    the name, because a label containing "Katılım" is not the same claim as a series whose
    scope is Katılım.
    """
    trace: list[str] = []
    # Search deeper than we display. The measure that can answer at the grain asked for
    # is often ranked below the top few: "Takipteki krediler oranı" puts eight FinTürk
    # per-province measures above the nationwide BDDK one, so a limit of 8 finds nothing
    # that satisfies "Türkiye geneli" and falls through to serving 567 provincial series.
    found = resolve_concept(concepts, query, limit=max(limit, FACET_SEARCH_DEPTH))

    if ranked_hits is not None:
        found = Resolution(ranked_hits, detect_intent(query), True)

    if not found.best:
        trace.append(f"'{query}' katalogdaki hiçbir ölçüme yeterince yakın değil")
        return SeriesResolution(query=query, concept=None, series_ids=(), trace=tuple(trace))

    concept = found.best.concept
    trace.append(
        f"ölçüm: {concept.label} [{concept.source}] "
        f"(skor {found.best.score:.2f}, {concept.series_count} seri)"
    )

    substitution = None
    if found.intent and not found.intent_satisfied:
        asked = "/".join(sorted(found.intent.measure_types))
        served = "/".join(sorted(concept.measure_types)) or "?"
        substitution = Substitution(
            asked_for=asked,
            served=served,
            reason=("Hiçbir kaynak bu ölçümü bu biçimde yayımlamıyor; en yakın ölçüm sunuluyor."),
        )
        trace.append(f"UYARI: {asked} istendi, {served} sunuluyor")

    scope, scope_stated = detect_scope(query)
    province, province_stated = detect_province(query, provinces)
    currency, currency_stated = detect_currency(query)

    facets = (
        FacetChoice("sector_scope", scope, scope_stated, "banka grubu"),
        FacetChoice(
            "province",
            province,
            province_stated,
            "il" if province_stated else "Türkiye geneli",
        ),
        FacetChoice("currency_basis", currency, currency_stated, "para birimi"),
    )
    stated = frozenset(
        name
        for name, was_stated in (
            ("sector_scope", scope_stated),
            ("province", province_stated),
            ("currency_basis", currency_stated),
        )
        if was_stated
    )
    for f in facets:
        trace.append(str(f))

    # The best-scoring measure is not always the one that can answer at the grain asked
    # for. "konut kredisi" scores highest against FinTürk's "Konut Kredisi", which is
    # published per province and has no nationwide row at all - so a question about
    # Türkiye would come back as 82 provincial series. Walk the ranked measures and take
    # the first whose series actually satisfy the facets, saying so when the top one is
    # passed over.
    chosen: Concept | None = None
    rows: list[dict] = []
    for rank, hit in enumerate(found.hits):
        candidate_rows = _rows_for(candidates, hit.concept)
        if strict:
            candidate_rows = _explicit_constraints(candidate_rows, query)
        narrowed, notes = _apply_facets(candidate_rows, scope, province, currency, stated)
        if narrowed:
            chosen = hit.concept
            rows = narrowed
            if rank:
                trace.append(
                    f"en yüksek skorlu ölçüm ({found.best.concept.label}) istenen kırılımı "
                    f"veremiyor; {hit.concept.label} [{hit.concept.source}] seçildi"
                )
            trace.extend(notes)
            break

    if chosen is None and strict:
        return SeriesResolution(
            query=query,
            concept=None,
            series_ids=(),
            facets=facets,
            trace=(*trace, "explicit_metadata_constraints_unsatisfied"),
        )

    if chosen is None:
        # Nothing satisfies every facet. Serve the best measure unfiltered rather than
        # nothing, and record that the breakdown was not honoured.
        chosen = concept
        rows = _rows_for(candidates, concept)
        trace.append("hiçbir ölçüm istenen kırılımı veremedi; daraltma uygulanmadı")

    series_ids = tuple(sorted(str(r["series_id"]) for r in rows))
    trace.append(f"sonuç: {len(series_ids)} seri")

    return SeriesResolution(
        query=query,
        concept=chosen,
        series_ids=series_ids,
        facets=facets,
        substitution=substitution,
        alternatives=tuple(h for h in found.hits if h.concept is not chosen)[:limit],
        trace=tuple(trace),
    )


def _rows_for(candidates: list[dict], concept: Concept) -> list[dict]:
    return [
        r
        for r in candidates
        if r.get("source") == concept.source and (r.get("raw_label") or "") == concept.label
    ]


def _apply_facets(
    rows: list[dict],
    scope: str,
    province: str | None,
    currency: str,
    stated: frozenset[str] = frozenset(),
) -> tuple[list[dict], list[str]]:
    """Narrow by each facet the source actually carries.

    A source that does not carry a facet at all is not filtered on it - FinTürk publishes
    no currency basis, EVDS no bank group - because filtering on an absent field empties
    the result and looks identical to "no such series".

    None is a real value for province, meaning nationwide. Treating it as absent excludes
    exactly the rows a nationwide question wants, which is the bug this comment exists to
    stop coming back.
    """
    notes: list[str] = []
    for facet, wanted in (
        ("sector_scope", scope),
        ("province", province),
        ("currency_basis", currency),
    ):
        present = {(r.get(facet) or None) for r in rows}
        if present <= {None}:
            if facet in stated:
                # The user named it and this source cannot express it. Skipping would let
                # a nationwide series answer a question about İstanbul.
                return [], [*notes, f"{facet}: bu kaynak bu kırılımı yayımlamıyor"]
            notes.append(f"{facet}: bu kaynakta yok, atlandı")
            continue
        narrowed = [r for r in rows if (r.get(facet) or None) == wanted]
        if not narrowed:
            return [], [*notes, f"{facet}={wanted or 'Türkiye'} bu kaynakta yok"]
        rows = narrowed
        notes.append(f"{facet}={wanted or 'Türkiye geneli'} -> {len(rows)} seri")
    return rows, notes


def _explicit_constraints(rows: list[dict], query: str) -> list[dict]:
    """Additional explicit row constraints for semantic candidates; no soft penalties."""
    intent = detect_intent(query)
    tokens = set(tokenize(query))
    frequencies = {
        "aylık": "M",
        "haftalık": "W",
        "günlük": "D",
        "yıllık": "A",
        "çeyreklik": "Q",
    }
    wanted_freq = {value for cue, value in frequencies.items() if cue in tokens}
    sources = {cue for cue in ("bddk", "evds", "tcmb") if cue in tokens}
    exact_ids = {
        r["series_id"] for r in rows if r["series_id"].casefold() in query.casefold().split()
    }
    return [
        r
        for r in rows
        if (not intent or r.get("measure_type") in intent.measure_types)
        and (not wanted_freq or r.get("native_freq") in wanted_freq)
        and (
            not sources
            or any(
                str(r.get("source", "")).lower().startswith("evds" if cue == "tcmb" else cue)
                for cue in sources
            )
        )
        and (not exact_ids or r["series_id"] in exact_ids)
    ]
