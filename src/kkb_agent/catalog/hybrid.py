"""Candidate enrichment only: the existing series resolver remains the decision boundary."""

import re
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Literal

from kkb_agent.catalog.candidate_family import candidate_family, specific_lexical_query
from kkb_agent.catalog.retrieval import Hit, build_concepts, detect_intent, search
from kkb_agent.catalog.series_resolver import (
    SeriesResolution,
    _apply_facets,
    _explicit_constraints,
    detect_currency,
    detect_province,
    detect_scope,
    resolve_series,
)
from kkb_agent.catalog.vector_index import IndexUnavailable, SemanticIndex
from kkb_agent.llm.embeddings import EmbeddingError

RetrievalMode = Literal["off", "selective", "always"]


@dataclass(frozen=True)
class RetrievalAmbiguity:
    reason: str
    candidate_ids: tuple[str, ...]
    differing_fields: tuple[str, ...]
    user_message: str = (
        "Birden fazla uyumlu seri bulundu. Kaynak, kapsam veya endeks bazını "
        "belirtmeden güvenilir biçimde tek seri seçilemiyor."
    )


@dataclass(frozen=True)
class RetrievalTrace:
    path: str
    lexical_candidates: int
    vector_candidates: int
    merged_candidates: int
    selected_series_ids: tuple[str, ...]
    reason: str | None = None
    embedding_seconds: float = 0.0
    search_seconds: float = 0.0
    rerank_seconds: float = 0.0
    total_seconds: float = 0.0
    mode: RetrievalMode = "off"
    lexical_confidence: str = "not_evaluated"
    vector_invoked: bool = False
    ambiguity_detected: bool = False
    family_ids: tuple[str, ...] = ()
    family_reason: str | None = None
    family_size: int = 0
    ambiguity_competitors: tuple[str, ...] = ()
    decision_source: str | None = None
    family_seconds: float = 0.0


@dataclass(frozen=True)
class RetrievalResult:
    resolution: SeriesResolution
    trace: RetrievalTrace
    ambiguity: RetrievalAmbiguity | None = None


class HybridRetrieval:
    def __init__(
        self,
        index: SemanticIndex | None = None,
        *,
        top_k: int = 20,
        mode: RetrievalMode | None = None,
    ):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        if mode not in (None, "off", "selective", "always"):
            raise ValueError("invalid retrieval mode")
        self.mode = mode or ("always" if index is not None else "off")
        self.index = index
        self.top_k = top_k

    def resolve(self, question: str, rows: list[dict]) -> RetrievalResult:
        result = self._resolve(question, rows)
        return replace(result, trace=replace(result.trace, mode=self.mode))

    def _resolve(self, question: str, rows: list[dict]) -> RetrievalResult:
        start = perf_counter()
        concepts = build_concepts(rows)
        provinces = frozenset(r["province"] for r in rows if r.get("province"))
        lexical = resolve_series(concepts, question, candidates=rows, provinces=provinces, limit=8)
        intent = detect_intent(question)
        lexical_hits = search(concepts, question, limit=30, measure_types=intent.measure_types)
        scores = {hit.concept.key: hit.score for hit in lexical_hits}
        union = {r["series_id"]: r for r in rows if (r.get("source"), r.get("raw_label")) in scores}
        count = len(union)
        if self.mode == "off":
            return RetrievalResult(
                lexical,
                RetrievalTrace(
                    "lexical",
                    count,
                    0,
                    count,
                    lexical.series_ids,
                    reason="disabled",
                    total_seconds=perf_counter() - start,
                ),
            )
        # Exact canonical identifiers are authoritative, without an embedding request.
        requested_ids = {
            token.casefold().rstrip(".")
            for token in re.findall(r"(?:evds|bddk[\w]*)\.[\w.]+", question, flags=re.IGNORECASE)
        }
        exact = [
            r
            for r in rows
            if r["series_id"].casefold() in requested_ids
            or r["series_id"].casefold() in question.casefold().split()
        ]
        if exact or requested_ids:
            constraint_query = question
            for row in exact:
                constraint_query = re.sub(
                    re.escape(row["series_id"]), "", constraint_query, flags=re.IGNORECASE
                )
            exact = _explicit_constraints(exact, constraint_query)
            for facet, (value, stated) in (
                ("sector_scope", detect_scope(constraint_query)),
                ("province", detect_province(constraint_query, provinces)),
                ("currency_basis", detect_currency(constraint_query)),
            ):
                if stated:
                    exact = [r for r in exact if r.get(facet) == value]
            if requested_ids and {r["series_id"].casefold() for r in exact} != requested_ids:
                exact = []
            resolved = SeriesResolution(
                question,
                None,
                tuple(sorted(r["series_id"] for r in exact)),
                trace=("exact_series_id",),
            )
            return RetrievalResult(
                resolved,
                RetrievalTrace(
                    "lexical",
                    count,
                    0,
                    len(exact),
                    resolved.series_ids,
                    reason="exact_series_id",
                    total_seconds=perf_counter() - start,
                ),
            )
        family_start = perf_counter()
        compatible_catalog = self._compatible(question, rows, provinces)
        compatible = candidate_family(compatible_catalog, lexical.series_ids)
        compatible_ids = {r["series_id"] for r in compatible}
        selected_rows = [r for r in compatible if r["series_id"] in lexical.series_ids]
        strong = (
            len(compatible_ids) == 1
            and set(lexical.series_ids) == compatible_ids
            and specific_lexical_query(question, selected_rows)
            and lexical.substitution is None
        )
        family_seconds = perf_counter() - family_start
        confidence = "strong" if strong else "weak" if lexical.resolved else "absent"
        if self.mode == "selective" and strong:
            return RetrievalResult(
                lexical,
                RetrievalTrace(
                    "lexical",
                    count,
                    0,
                    count,
                    lexical.series_ids,
                    reason="strong_lexical",
                    family_ids=tuple(sorted(compatible_ids)),
                    family_size=len(compatible_ids),
                    family_reason="lexical_seed_label_containment",
                    decision_source="lexical_unique",
                    family_seconds=family_seconds,
                    lexical_confidence=confidence,
                    total_seconds=perf_counter() - start,
                ),
            )
        try:
            if self.index is None:
                raise IndexUnavailable("index_missing")
            vectors = self.index.search(question, rows, top_k=self.top_k)
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, (IndexUnavailable, EmbeddingError))
                else "vector_unavailable"
            )
            # Only fixed codes may reach the trace, including with injected providers.
            if reason not in {
                "index_missing",
                "index_empty",
                "index_stale",
                "index_unavailable",
                "embedding_unavailable",
                "invalid_embedding_response",
                "invalid_index_distance",
            }:
                reason = "vector_unavailable"
            return RetrievalResult(
                lexical,
                RetrievalTrace(
                    "lexical_fallback",
                    count,
                    0,
                    count,
                    lexical.series_ids,
                    reason=reason,
                    lexical_confidence=confidence,
                    vector_invoked=self.index is not None,
                    total_seconds=perf_counter() - start,
                ),
            )
        rerank_start = perf_counter()
        by_id = {r["series_id"]: r for r in rows}
        semantic = {}
        for candidate in vectors.candidates:
            if candidate.series_id in by_id:
                union[candidate.series_id] = by_id[candidate.series_id]
                semantic[candidate.series_id] = max(0.0, min(1.0, 1 - candidate.distance / 2))
        # Lexical harmonic coverage is already [0,1]. Cosine distance [0,2] becomes
        # [0,1]. Equal contribution, no learned weights or metadata penalties.
        ranked = []
        for concept in concepts:
            members = [
                r for r in union.values() if (r.get("source"), r.get("raw_label")) == concept.key
            ]
            if not members:
                continue
            # Filter BEFORE reducing series scores to a concept, so an incompatible
            # facet's strong vector cannot promote a different member of the concept.
            scope, ss = detect_scope(question)
            province, ps = detect_province(question, provinces)
            currency, cs = detect_currency(question)
            stated = frozenset(
                k
                for k, yes in (("sector_scope", ss), ("province", ps), ("currency_basis", cs))
                if yes
            )
            members, _ = _apply_facets(
                _explicit_constraints(members, question), scope, province, currency, stated
            )
            if not members:
                continue
            score = max(
                (scores.get(concept.key, 0.0) + semantic.get(r["series_id"], 0.0)) / 2
                for r in members
            )
            ranked.append((Hit(concept, score, ()), min(r["series_id"] for r in members)))
        ranked.sort(key=lambda pair: (not pair[0].concept.has_data, -pair[0].score, pair[1]))
        resolution = resolve_series(
            concepts,
            question,
            candidates=list(union.values()),
            provinces=provinces,
            ranked_hits=tuple(hit for hit, _ in ranked),
            strict=True,
            limit=8,
        )
        ambiguity = None
        if self.mode == "selective":
            family_start = perf_counter()
            allowed = {r["series_id"] for r in compatible_catalog}
            vector_seed = next(
                (c.series_id for c in vectors.candidates if c.series_id in allowed), None
            )
            # Resolver and nearest compatible semantic evidence must agree on a
            # family; disagreement stays visible, rather than overriding either.
            seeds = tuple(
                sorted(set(resolution.series_ids) | ({vector_seed} if vector_seed else set()))
            )
            compatible = candidate_family(compatible_catalog, seeds)
            compatible_ids = {r["series_id"] for r in compatible}
            family_seconds += perf_counter() - family_start
            if len(compatible_ids) > 1:
                fields = (
                    "source",
                    "raw_label",
                    "measure_type",
                    "unit_raw",
                    "unit_normalized",
                    "sector_scope",
                    "native_freq",
                    "currency_basis",
                    "adjustment",
                )
                ambiguity = RetrievalAmbiguity(
                    "financial_identity_ambiguous",
                    tuple(sorted(compatible_ids)),
                    tuple(f for f in fields if len({r.get(f) for r in compatible}) > 1),
                )
                resolution = replace(
                    resolution,
                    series_ids=(),
                    trace=(
                        *resolution.trace,
                        ambiguity.reason,
                    ),
                )
            elif set(resolution.series_ids) != compatible_ids:
                resolution = replace(resolution, series_ids=())
        return RetrievalResult(
            resolution,
            RetrievalTrace(
                "hybrid",
                count,
                len(vectors.candidates),
                len(union),
                resolution.series_ids,
                lexical_confidence=confidence,
                vector_invoked=True,
                ambiguity_detected=ambiguity is not None,
                family_ids=tuple(sorted(compatible_ids)),
                family_size=len(compatible_ids),
                family_reason="resolver_and_vector_seeds_label_containment",
                ambiguity_competitors=tuple(sorted(compatible_ids)) if ambiguity else (),
                decision_source="ambiguity"
                if ambiguity
                else "semantic_unique"
                if resolution.resolved
                else None,
                family_seconds=family_seconds,
                embedding_seconds=vectors.embedding_seconds,
                search_seconds=vectors.search_seconds,
                rerank_seconds=perf_counter() - rerank_start,
                total_seconds=perf_counter() - start,
            ),
            ambiguity,
        )

    @staticmethod
    def _compatible(question: str, rows: list[dict], provinces: frozenset[str]) -> list[dict]:
        scope, ss = detect_scope(question)
        province, ps = detect_province(question, provinces)
        currency, cs = detect_currency(question)
        stated = frozenset(
            key
            for key, yes in (("sector_scope", ss), ("province", ps), ("currency_basis", cs))
            if yes
        )
        # Apply facets per row so absence of one source's metadata cannot be hidden
        # by a different source's populated field.
        return [
            row
            for row in _explicit_constraints(rows, question)
            if _apply_facets([row], scope, province, currency, stated)[0]
        ]
