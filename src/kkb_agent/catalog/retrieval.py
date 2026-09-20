"""Resolve a Turkish question to concrete series in the catalog.

The catalog holds 47,015 series. Almost none of that is semantic variety: there are only
**872 distinct measures**, each repeated across provinces, bank groups, currency bases and
frequencies. "Kişi Başı Nakdi Kredi - ADANA - SEKTÖR" and "... - VAN - MEVDUAT" are the
same concept; the province and the group are facets of *which* one you want, not of *what*
it is.

So retrieval happens in two stages, and mixing them is the mistake to avoid:

    1. semantic   a question names a MEASURE      -> search 872 concepts
    2. structural a question names FACETS         -> filter, never score

Searching all 47,015 rows instead would embed the word "ADANA" 42 times under different
measures and let province names dominate the similarity of a question that never mentions
a province. It is also 54x the embedding cost for no gain.

Nothing here calls the network. Lexical scoring runs on the catalog as it stands, which is
what SCRUM-38 asks to measure before assuming embeddings are needed; embeddings attach to
the same concepts later as a second signal, not a replacement.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from kkb_agent.catalog.identity import turkish_casefold

# Tokens that carry no discriminating power in a Turkish financial question.
STOPWORDS = frozenset(
    {
        "ve",
        "ile",
        "için",
        "göre",
        "olan",
        "de",
        "da",
        "bir",
        "bu",
        "en",
        "net",
        "toplam",
        # Question and time vocabulary. Scoring is a harmonic mean of query and concept
        # coverage, so words the catalog can never match pull the query side down: the
        # same phrase that scores 1.000 alone matched nothing once "son 5 yılda nasıl
        # değişti" was appended. These carry no subject matter.
        "nasıl",
        "ne",
        "nedir",
        "kaç",
        "hangi",
        "kadar",
        "mı",
        "mi",
        "mu",
        "mü",
        "son",
        "yıl",
        "yılda",
        "yılın",
        "yıllarda",
        "arası",
        "arasında",
        "değişti",
        "değişim",
        "oldu",
        "göster",
        "durumu",
        "seyri",
        "nedeni",
    }
)

# Shortest prefix that may stand in for a whole word. Turkish agglutinates, so "kredi"
# has to reach "kredileri" and "kredisi" - but three letters would let "kar" reach
# "karşılık", which is a different thing entirely.
MIN_STEM = 4

_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

# A parenthetical made only of digits, operators and nested parens: a formula or a set of
# row references, never subject matter. "((5/7)*100)", "(10+27+28)", "(2+10)".
_FORMULA = re.compile(r"\(\s*[\d+\-*/.,\s]*\s*\)")


def strip_formulas(label: str) -> str:
    """Remove formula and row-reference parentheticals from a published label.

    BDDK writes "Sermaye Yeterliliği Standart Rasyosu ((5/7)*100) (YÜZDE)". Those digits
    are not subject matter, but they are four of the label's eight tokens, and a label
    diluted that far loses to a shorter one that happens to share a word - "Çekirdek
    Sermaye" outranked it for the query "sermaye yeterliliği".

    Applied only here, not in identity.normalise_label: series ids are derived from that
    and changing it would rename series. Nested groups need repeating, because the inner
    "(5/7)" has to go before "(*100)" is a formula on its own.
    """
    prev = None
    out = label or ""
    while out != prev:
        prev = out
        out = _FORMULA.sub(" ", out)
    return out.strip()


def tokenize(text: str) -> tuple[str, ...]:
    """Turkish-correct tokens, stopwords removed.

    Casefolding goes through turkish_casefold because str.lower() maps 'I' to 'i' where
    Turkish needs 'ı'. "İHTİYAÇ" and "ihtiyaç" are the same word; under str.lower() they
    are not, and the match silently fails.
    """
    folded = turkish_casefold(unicodedata.normalize("NFC", text or ""))
    return tuple(t for t in _WORD.findall(folded) if t not in STOPWORDS)


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _pair_score(query_token: str, concept_token: str) -> float:
    """How well one query token matches one concept token.

    Compared by shared prefix rather than by one being a prefix of the other, because
    Turkish inflects both sides of the comparison. A user writes "konut kredi*si*", BDDK
    writes "Konut Kredi*leri*" - they share the stem "kredi" and neither contains the
    other. A one-directional prefix test scores that pair zero.

    A stem match scores in a band below an exact one rather than in proportion to the
    shared prefix. Turkish suffixes carry grammatical case, not meaning: "kredisi" and
    "kredileri" are the same word for retrieval, and scoring them 5/9 made
    "Takipteki Konut Kredileri" outrank "Konut Kredisi" for the query "konut kredileri" -
    the extra word cost the right answer less than the inflection cost it.

    MIN_STEM is the floor that stops "kar" reaching "karşılık", which are different things.
    """
    if query_token == concept_token:
        return 1.0
    shared = _shared_prefix(query_token, concept_token)
    if shared < MIN_STEM:
        return 0.0
    return 0.6 + 0.4 * shared / max(len(query_token), len(concept_token))


@dataclass(frozen=True)
class Concept:
    """One distinct measure, independent of who reports it or where.

    `series_count` is how many concrete series carry this measure. A concept with 574
    series behind it is a province-level measure; one with 10 is a national one published
    per bank group.
    """

    source: str
    label: str
    tokens: tuple[str, ...]
    series_count: int
    measure_types: frozenset[str] = field(default_factory=frozenset)
    units: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    frequencies: frozenset[str] = field(default_factory=frozenset)
    has_provinces: bool = False
    # Whether any series behind this measure carries a figure. 4,496 of 47,015 series are
    # published as 0.0 or null every period - real rows, useless answers - and a measure
    # made only of those must never outrank one with data.
    has_data: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.label)

    def embedding_text(self) -> str:
        """What a vector index would embed for this concept.

        The semantics travel with the name, because the name alone cannot separate a
        housing loan balance from a housing loan rate.
        """
        parts = [
            self.label,
            f"kaynak: {self.source}",
            f"ölçüm: {'/'.join(sorted(self.measure_types))}" if self.measure_types else "",
            f"birim: {'/'.join(sorted(self.units))}" if self.units else "",
            "il bazında" if self.has_provinces else "Türkiye geneli",
        ]
        return " | ".join(p for p in parts if p)


# Words that name a KIND of measure rather than a subject. These are the reason a purely
# lexical ranking gets "konut kredisi faizi" wrong: "faizi" appears in no BDDK label, so it
# contributes nothing to the score and the housing loan *balance* wins on tightness.
#
# A word like this is intent. It belongs on the catalog's measure_type field, which is a
# structured column - so it filters, and does not merely nudge a score.
MEASURE_CUES: dict[str, frozenset[str]] = {
    "faiz": frozenset({"rate"}),
    "getiri": frozenset({"rate"}),
    "endeks": frozenset({"index"}),
    "oran": frozenset({"ratio", "rate"}),  # a percentage either way
    "rasyo": frozenset({"ratio", "rate"}),
    "pay": frozenset({"ratio"}),
    "sayı": frozenset({"count"}),
    "adet": frozenset({"count"}),
    "bakiye": frozenset({"stock"}),
    "stok": frozenset({"stock"}),
    "tutar": frozenset({"stock", "flow"}),
    "akım": frozenset({"flow"}),
    "kullandırım": frozenset({"flow"}),
    "kullandırılan": frozenset({"flow"}),
}


@dataclass(frozen=True)
class Intent:
    """What the question asks for, beyond the subject it names."""

    measure_types: frozenset[str] = field(default_factory=frozenset)
    cues: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.measure_types)


def detect_intent(query: str) -> Intent:
    """Read measure-type intent out of a Turkish question.

    Matching is by stem so "faizi", "faizler", "faiz oranı" all land on the same cue, and
    "endeksi" on "endeks". Where two cues disagree - "konut fiyat endeksi oranı" - the
    union is kept rather than one being picked, because guessing which the user meant is
    how a wrong series gets served confidently.
    """
    types: set[str] = set()
    found: list[str] = []
    for token in tokenize(query):
        for cue, cue_types in MEASURE_CUES.items():
            if token == cue or (len(cue) >= MIN_STEM and token.startswith(cue)):
                types |= set(cue_types)
                found.append(cue)
                break
    return Intent(measure_types=frozenset(types), cues=tuple(dict.fromkeys(found)))


@dataclass(frozen=True)
class Hit:
    concept: Concept
    score: float
    matched: tuple[str, ...]


def build_concepts(rows: list[dict]) -> list[Concept]:
    """Collapse catalog rows into the distinct measures behind them.

    `rows` are catalog records - dicts or anything with these keys: source, raw_label,
    measure_type, unit_normalized, sector_scope, native_freq, province.
    """
    grouped: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "n": 0,
            "measure_types": set(),
            "units": set(),
            "scopes": set(),
            "frequencies": set(),
            "provinces": False,
            "nonzero": 0,
        }
    )

    for r in rows:
        label = (r.get("raw_label") or r.get("name_tr") or "").strip()
        if not label:
            continue
        g = grouped[(r.get("source", ""), label)]
        g["n"] += 1
        for key, column in (
            ("measure_types", "measure_type"),
            ("units", "unit_normalized"),
            ("scopes", "sector_scope"),
            ("frequencies", "native_freq"),
        ):
            value = r.get(column)
            if value:
                g[key].add(str(value))
        if r.get("province"):
            g["provinces"] = True
        g["nonzero"] += int(r.get("nonzero_observations") or 0)

    concepts = [
        Concept(
            source=source,
            label=label,
            tokens=tokenize(strip_formulas(label)),
            series_count=g["n"],
            measure_types=frozenset(g["measure_types"]),
            units=frozenset(g["units"]),
            scopes=frozenset(g["scopes"]),
            frequencies=frozenset(g["frequencies"]),
            has_provinces=g["provinces"],
            has_data=g["nonzero"] > 0,
        )
        for (source, label), g in grouped.items()
    ]
    concepts.sort(key=lambda c: (c.source, c.label))
    return concepts


# Words that make a label narrower rather than describing a different subject. A question
# that does not say "takipteki" is not asking about non-performing loans, and a question
# that does not say "dövize endeksli" is not asking about the FX-indexed slice.
#
# Without this, "konut kredisi" tied "Takipteki Konut Kredileri" with "Tüketici Kredileri
# - Konut" - both have exactly one unmatched token - and the tie broke alphabetically, so
# the answer to "how big are housing loans" was the non-performing ones.
#
# An explicit list, not a rule. These are domain facts about what restricts a series, and
# a general "longer label loses" rule would also demote "Tüketici Kredileri - Konut",
# which is the right answer.
RESTRICTIVE_QUALIFIERS = frozenset(
    {
        "takipteki",  # non-performing
        "tasfiye",  # in liquidation
        "endeksli",  # FX-indexed slice
        "arşiv",  # superseded publication
        "reeskont",  # rediscount facility, not lending
        "dışı",  # yurt dışı - the foreign-branch subset
    }
)

# How much of its score a concept keeps per unrequested qualifier. Enough to lose a tie
# decisively, not enough to bury a concept that is genuinely the only match.
_QUALIFIER_PENALTY = 0.65


def lexical_score(query_tokens: tuple[str, ...], concept: Concept) -> tuple[float, tuple[str, ...]]:
    """Score one concept against a tokenized query, and say which tokens matched.

    The harmonic mean of two coverages, not just how much of the query was found:

        query coverage    how much of what the user asked for is present
        concept coverage  how much of the label is accounted for

    Query coverage alone ranks "Tüketici Kredileri - Konut (Dövize Endeksli)" level with
    "Konut", because both contain every word of "konut". Requiring the concept to be
    covered too prefers the tight match, which is almost always the one meant. Returning
    the matched tokens keeps the ranking explainable rather than a bare number.
    """
    if not query_tokens or not concept.tokens:
        return 0.0, ()

    matched: list[str] = []
    query_total = 0.0
    for q in query_tokens:
        best = max((_pair_score(q, c) for c in concept.tokens), default=0.0)
        query_total += best
        if best > 0:
            matched.append(q)

    concept_total = sum(
        max((_pair_score(q, c) for q in query_tokens), default=0.0) for c in concept.tokens
    )

    query_coverage = query_total / len(query_tokens)
    concept_coverage = concept_total / len(concept.tokens)
    if query_coverage == 0 or concept_coverage == 0:
        return 0.0, ()

    harmonic = 2 * query_coverage * concept_coverage / (query_coverage + concept_coverage)

    unrequested = RESTRICTIVE_QUALIFIERS & set(concept.tokens) - set(query_tokens)
    harmonic *= _QUALIFIER_PENALTY ** len(unrequested)

    return harmonic, tuple(matched)


def search(
    concepts: list[Concept],
    query: str,
    *,
    limit: int = 10,
    sources: frozenset[str] | None = None,
    measure_types: frozenset[str] | None = None,
    min_score: float = 0.35,
) -> list[Hit]:
    """Rank concepts against a Turkish question.

    `sources` and `measure_types` are filters, not scoring signals. A question that asks
    for a rate should not merely prefer rates - it should not be offered balances at all,
    because a balance presented for a rate question is wrong rather than second-best.

    `min_score` is deliberately not near zero. A weak best match is worse than none: at
    0.15, "konut kredisi kullandırımı" filtered to flows returned "Kredilerden Alınan
    Ücret ve Komisyonlar" at 0.223 and presented it as the answer, where returning nothing
    lets the caller fall back and say the intent could not be met.
    """
    query_tokens = tokenize(query)
    hits: list[Hit] = []

    for c in concepts:
        if sources and c.source not in sources:
            continue
        if measure_types and not (c.measure_types & measure_types):
            continue
        score, matched = lexical_score(query_tokens, c)
        if score >= min_score:
            hits.append(Hit(concept=c, score=score, matched=matched))

    # Ties broken towards the measure with fewer series behind it: a concept carried by
    # 10 national series is a more specific answer than one spread over 574 provinces.
    # An empty measure sorts below every measure with data, whatever it scored. "konut
    # kredisi" matched BDDK's "Ferdi Kredi Konut" above "Tüketici Kredileri - Konut" -
    # and Ferdi Kredi Konut is 0.0 in all 66 months, so the better-scoring answer was an
    # empty chart.
    hits.sort(
        key=lambda h: (not h.concept.has_data, -h.score, h.concept.series_count, h.concept.label)
    )
    return hits[:limit]


@dataclass(frozen=True)
class Resolution:
    """What a question resolved to, and what had to be given up to get there."""

    hits: tuple[Hit, ...]
    intent: Intent
    intent_satisfied: bool

    @property
    def best(self) -> Hit | None:
        return self.hits[0] if self.hits else None


def resolve(concepts: list[Concept], query: str, *, limit: int = 10) -> Resolution:
    """Search with the question's own measure-type intent applied as a filter.

    Two stages, and the order matters. The intent filter runs first: a question about a
    rate is not offered balances, because a balance served for a rate question is wrong
    rather than second-best.

    If the filter empties the result, the search is retried without it and
    `intent_satisfied` comes back False. That is not a silent fallback - it is the signal
    for the answer to say "we hold no rate for this; here is the balance instead", which
    is the honest response when the data does not contain what was asked for.
    """
    intent = detect_intent(query)

    if intent:
        hits = search(concepts, query, limit=limit, measure_types=intent.measure_types)
        if hits:
            return Resolution(hits=tuple(hits), intent=intent, intent_satisfied=True)

    hits = search(concepts, query, limit=limit)
    return Resolution(hits=tuple(hits), intent=intent, intent_satisfied=not intent)
