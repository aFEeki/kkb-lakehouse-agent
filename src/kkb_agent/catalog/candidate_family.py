"""Bounded label-family expansion; no source equivalence or preferred identity."""

from kkb_agent.catalog.retrieval import MEASURE_CUES, _pair_score, tokenize


def label_tokens(row: dict) -> tuple[str, ...]:
    # Numeric bases and short abbreviations are not subjects. Keep descriptive
    # qualifiers (including 'genel') so generic units alone cannot connect families.
    return tuple(
        token
        for token in tokenize(row.get("raw_label") or row.get("name_tr") or "")
        if not token.isdigit() and len(token) >= 4 and token not in {"stok", "akım"}
    )


def related(left: dict, right: dict) -> bool:
    if (left.get("source"), left.get("raw_label")) == (right.get("source"), right.get("raw_label")):
        return True
    a, b = label_tokens(left), label_tokens(right)
    # Two subject tokens and containment, not one shared generic word or a fitted
    # similarity threshold. Use the repository's existing Turkish stem relation.
    if min(len(a), len(b)) < 2:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(_pair_score(x, y) > 0 for y in longer) for x in shorter)


def candidate_family(rows: list[dict], seed_ids: tuple[str, ...]) -> list[dict]:
    seeds = [r for r in rows if r["series_id"] in seed_ids]
    # One-hop expansion against original seeds: no transitive drift through broad
    # labels into unrelated topics. Full catalog siblings survive top-k truncation.
    return sorted(
        [r for r in rows if any(related(r, seed) for seed in seeds)],
        key=lambda r: r["series_id"],
    )


def specific_lexical_query(question: str, selected: list[dict]) -> bool:
    subject = tuple(
        token
        for token in tokenize(question)
        if not token.isdigit() and not any(token.startswith(cue) for cue in MEASURE_CUES)
    )
    return len(subject) >= 2 and any(
        all(any(_pair_score(q, t) > 0 for t in label_tokens(row)) for q in subject)
        for row in selected
    )
