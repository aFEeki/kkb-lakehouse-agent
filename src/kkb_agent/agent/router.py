"""Route a natural-language question to one of the brief's tools (deck p.10).

Model picks the tool under a schema that makes an invented name unrepresentable; Turkish
keyword rules take over when MIA is down, and `chosen_by` says which ran. A question
matching no tool is refused rather than sent to the nearest one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# The brief's six names. web_search is selectable and refuses until SCRUM-67 builds it.
TOOLS = (
    "lakehouse",
    "anomaly",
    "causality",
    "change_detection",
    "url_agent",
    "web_search",
)

UNIMPLEMENTED = {"web_search"}

_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# Narrow on purpose: a loose cue mis-routes, and the wrong answer still looks computed.
_CUES: dict[str, tuple[str, ...]] = {
    "lakehouse": (
        "göster",
        "getir",
        "listele",
        "sorgula",
        "grafik",
        "tabloya",
        "hangi seri",
    ),
    "anomaly": (
        "anomali",
        "aykırı",
        "olağandışı",
        "olağan dışı",
        "sapma",
        "uç değer",
        "sıra dışı",
        "outlier",
    ),
    "change_detection": (
        "kırılma",
        "kırılım",
        "rejim",
        "değişim noktası",
        "trend değiş",
        "seviye değiş",
        "yapısal değişim",
        "break",
    ),
    "causality": (
        "nedensellik",
        "neden oldu",
        "yol açtı",
        "etkiliyor mu",
        "etkiledi mi",
        "sebep oldu",
        "granger",
        "neden sonuç",
        "neden-sonuç",
    ),
}


@dataclass(frozen=True)
class ToolChoice:
    """Which tool a question wants, and how that was decided."""

    tool: str | None
    reason: str
    chosen_by: str  # "planner" | "rules"

    @property
    def routable(self) -> bool:
        return self.tool is not None and self.tool not in UNIMPLEMENTED


def tool_choice_schema() -> dict:
    """Enum over the tool names. Nullable: forcing a choice makes "none of these"
    unsayable, so the model returns the closest name instead."""
    return {
        "type": "object",
        "properties": {
            "tool": {"type": ["string", "null"], "enum": [*TOOLS, None]},
            "reason": {"type": "string"},
        },
        "required": ["tool", "reason"],
        "additionalProperties": False,
    }


_SYSTEM = (
    "Bir finansal veri ajanının araç seçicisisin. Kullanıcının sorusunu oku ve hangi "
    "aracın çalıştırılması gerektiğini seç.\n\n"
    "lakehouse: kataloğdaki seriyi sorgulama, birleştirme, gösterme\n"
    "anomaly: aykırı değer, olağandışı hareket, sapma tespiti\n"
    "causality: iki seri arasında neden-sonuç ilişkisi olup olmadığı\n"
    "change_detection: seviye, trend veya rejim kırılması tespiti\n"
    "url_agent: soruda verilen bir URL'deki içeriği okuma\n"
    "web_search: internette arama\n\n"
    "Hiçbiri uymuyorsa tool alanını null bırak. Emin değilsen null bırak: yanlış araç "
    "seçmek, seçmemekten kötüdür."
)


def rule_choice(question: str) -> ToolChoice:
    """Deterministic fallback. A URL settles it first: the content has to be read
    before anything can be said about it."""
    text = question.casefold()

    if _URL.search(question):
        return ToolChoice("url_agent", "soruda bir URL var", "rules")

    for tool, cues in _CUES.items():
        hit = next((cue for cue in cues if cue in text), None)
        if hit is not None:
            return ToolChoice(tool, f"anahtar ifade: {hit!r}", "rules")

    return ToolChoice(None, "hiçbir araç bu soruyla eşleşmedi", "rules")


def select_tool(question: str, *, planner=None, mia_client=None) -> ToolChoice:
    """Ask the model which tool to run; fall back to the keyword rules."""
    if not question.strip():
        raise ValueError("question must not be empty")

    client = mia_client if mia_client is not None else getattr(planner, "_mia_client", None)
    if client is None:
        return rule_choice(question)

    try:
        response = client.get_client().chat.completions.create(
            model=client.chat_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": question},
            ],
            max_tokens=200,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "tool_choice",
                    "schema": tool_choice_schema(),
                    "strict": True,
                },
            },
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception:
        return rule_choice(question)  # weaker; chosen_by keeps that visible

    tool = payload.get("tool")
    if tool is not None and tool not in TOOLS:
        return rule_choice(question)
    reason = str(payload.get("reason") or "").strip() or "model seçimi"
    return ToolChoice(tool, reason, "planner")


# Dispatch. Tools take values and dates, never a question; this supplies them.

# Observations per seasonal cycle. Wrong here reports seasonality as an outlier.
_PERIOD: dict[str, int] = {"D": 7, "W": 52, "M": 12, "Q": 4}

# Shortest run that counts as a regime; below this every wobble is a break.
_MIN_SEGMENT: dict[str, int] = {"D": 14, "W": 8, "M": 6, "Q": 3}

_CATALOG_COLUMNS = (
    "series_id, source, raw_label, name_tr, measure_type, unit_raw, unit_normalized, "
    "scale_factor, sector_scope, native_freq, aggregation_rule, province, currency_basis, "
    "nonzero_observations"
)


@dataclass(frozen=True)
class ToolRun:
    """What the router did. `refusal` is Turkish and user-facing: a refusal is a
    complete answer, not an error."""

    choice: ToolChoice
    series_ids: tuple[str, ...] = ()
    result: object | None = None
    refusal: str | None = None

    @property
    def ran(self) -> bool:
        return self.result is not None and self.refusal is None


def _observations(source, series_id: str):
    """(values, dates) for one catalog series, oldest first, gaps preserved as None."""
    loaded = source.fetch(series_id)
    if loaded is None:
        return None, None
    ordered = sorted(loaded.values.items())
    return [value for _, value in ordered], [when for when, _ in ordered], loaded


def _resolve(catalog, question: str, wanted: int):
    """Resolve a question to `wanted` distinct series using the existing retrieval."""
    import duckdb

    from kkb_agent.catalog.retrieval import build_concepts
    from kkb_agent.catalog.series_resolver import resolve_series

    connection = duckdb.connect(str(catalog), read_only=True)
    try:
        rows = [
            {
                k: (None if v is None or (isinstance(v, float) and v != v) else v)
                for k, v in r.items()
            }
            for r in connection.execute(f"SELECT {_CATALOG_COLUMNS} FROM series_catalog")
            .fetchdf()
            .to_dict("records")
        ]
    finally:
        connection.close()

    concepts = build_concepts(rows)
    provinces = frozenset(r["province"] for r in rows if r.get("province"))
    resolution = resolve_series(
        concepts, question, candidates=rows, provinces=provinces, limit=max(8, wanted * 4)
    )
    if not resolution.resolved:
        return ()
    # Distinct, in rank order: a series does not cause itself.
    seen: list[str] = []
    for series_id in resolution.series_ids:
        if series_id not in seen:
            seen.append(series_id)
    return tuple(seen[:wanted])


# Turkish ways of naming two things in one breath: "A ile B arasında", "A ve B".
_PAIR = re.compile(r"\s+(?:ile|ve|arasında|arasindaki|karşı)\s+", re.IGNORECASE)


def _resolve_pair(catalog, question: str) -> tuple[str, ...]:
    """Two distinct series. Retrieval resolves one concept per sentence, so the
    question is split where Turkish joins two things and each side resolved separately."""
    parts = [part.strip(" ?.,") for part in _PAIR.split(question) if part.strip(" ?.,")]
    found: list[str] = []
    for part in parts:
        for series_id in _resolve(catalog, part, 1):
            if series_id not in found:
                found.append(series_id)
    if len(found) >= 2:
        return tuple(found[:2])
    return _resolve(catalog, question, 2)


def _penalties(values) -> tuple[float, float]:
    """Scale PELT penalties to the series: the signal is not normalised, so a fixed
    penalty means different things for a lira balance and a percentage."""
    import math
    import statistics

    present = [float(v) for v in values if v is not None]
    if len(present) < 3:
        return 1.0, 1.0
    spread = statistics.pvariance(present) or 1.0
    size = math.log(max(len(present), 2))
    return 3.0 * spread * size, 3.0 * (
        statistics.pvariance([b - a for a, b in zip(present, present[1:], strict=False)]) or 1.0
    ) * size


def run_tool(question: str, catalog, *, mia_client=None, fetcher=None, choice=None) -> ToolRun:
    """Choose a tool, give it what it needs, run it.

    `choice` lets a caller that has already selected the tool pass it in, so it can tell
    the user which tool is running before the run starts rather than afterwards.
    """
    if choice is None:
        choice = select_tool(question, mia_client=mia_client)

    if choice.tool is None:
        return ToolRun(choice, refusal="Bu soru mevcut araçlardan hiçbirine yönlendirilemedi.")
    if choice.tool in UNIMPLEMENTED:
        return ToolRun(
            choice,
            refusal=f"{choice.tool} aracı henüz geliştirilmedi; bu soru yanıtlanamıyor.",
        )

    if choice.tool == "url_agent":
        return _run_url_agent(question, choice, fetcher)

    from kkb_agent.catalog.series_source import CatalogSeriesSource

    wanted = 2 if choice.tool == "causality" else 1
    series_ids = _resolve_pair(catalog, question) if wanted == 2 else _resolve(catalog, question, 1)
    if len(series_ids) < wanted:
        return ToolRun(
            choice,
            series_ids,
            refusal=(
                "Soru, kataloğumuzdaki "
                f"{wanted} ayrı seriye çözümlenemedi; hangi seriyi kastettiğiniz belirsiz."
            ),
        )

    source = CatalogSeriesSource(catalog)
    try:
        loaded = [_observations(source, series_id) for series_id in series_ids]
    finally:
        source.close()
    if any(item[0] is None for item in loaded):
        return ToolRun(choice, series_ids, refusal="Seri verisi okunamadı.")

    try:
        return ToolRun(choice, series_ids, result=_call(choice.tool, loaded))
    except Exception as exc:  # a tool's own refusal or a genuine input problem
        return ToolRun(choice, series_ids, refusal=f"Araç bu veriyle çalışamadı: {exc}")


def _call(tool: str, loaded):
    from kkb_agent.tools import analyze_anomalies, analyze_causality, detect_changes

    values, dates, meta = loaded[0]

    if tool == "anomaly":
        return analyze_anomalies(values, dates, period=_PERIOD.get(str(meta.native_freq), 12))

    if tool == "change_detection":
        level, trend = _penalties(values)
        return detect_changes(
            [0.0 if v is None else float(v) for v in values],
            dates,
            level_penalty=level,
            trend_penalty=trend,
            minimum_segment_length=_MIN_SEGMENT.get(str(meta.native_freq), 6),
        )

    if tool == "causality":
        other_values, other_dates, _ = loaded[1]
        # Shared dates only: misaligned axes compare a series against another's calendar.
        shared = sorted(set(dates) & set(other_dates))
        if len(shared) < 12:
            raise ValueError("iki seri yalnızca çok az ortak dönemde kesişiyor")
        first = dict(zip(dates, values, strict=False))
        second = dict(zip(other_dates, other_values, strict=False))
        return analyze_causality([first[d] for d in shared], [second[d] for d in shared], shared)

    if tool == "lakehouse":
        return meta  # which series, from where, in what unit

    raise ValueError(f"no dispatch for {tool!r}")


def _run_url_agent(question: str, choice: ToolChoice, fetcher) -> ToolRun:
    from kkb_agent.tools.url_agent import create_content_type_router
    from kkb_agent.tools.url_safety import SafeURLFetcher

    match = _URL.search(question)
    if match is None:
        return ToolRun(choice, refusal="Soruda okunabilir bir URL bulunamadı.")
    url = match.group(0).rstrip(".,;)")

    owned = fetcher is None
    client = SafeURLFetcher() if owned else fetcher
    try:
        return ToolRun(choice, result=create_content_type_router().route(client.fetch(url)))
    except Exception as exc:
        return ToolRun(choice, refusal=f"URL okunamadı: {exc}")
    finally:
        if owned:
            client.close()
