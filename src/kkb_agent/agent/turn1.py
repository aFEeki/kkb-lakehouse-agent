"""SCRUM-44 - the first published question, answered from computed evidence.

    "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"

The trap in this question is that it contains a false premise and a definitional slip, and
an answer that repeats either of them back sounds fluent and is wrong.

**Volume did rise - in lira.** Housing loan balances more than tripled over the window.
Saying "it did not rise" would be false. Saying "it rose" without saying in what would be
worse, because inflation over the same period was larger, so in constant prices the balance
*fell*. That is the actual answer, and it is a computation, not a reading of a chart.

**The question says hacim - volume - and we do not have volume.** No source publishes
*kullandırılan*, gross new lending. What we have is the balance outstanding, which moves
for reasons other than new lending: repayments, write-offs, revaluation. The answer says
so rather than presenting a balance as if it were lending.

**The rate is a specific rate.** TP.KTF12 is the weekly flow-basis (akım) rate on new
housing loans in TL, a weighted average. It is not the rate on the existing book. Naming it
is the difference between an answer a domain judge accepts and one they do not.

Nothing here asks a model for a number. The findings are arithmetic over the frame, so
every figure is hand-checkable against the gold layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.findings import create_finding
from kkb_agent.agent.handlers import DEFLATION_CONVENTION, deflated_column_key
from kkb_agent.catalog.retrieval import Concept, build_concepts
from kkb_agent.catalog.series_resolver import SeriesResolution, resolve_series
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.frame import (
    AddColumnParameters,
    AnalysisFrame,
    DeflateColumnParameters,
    Operation,
    OperationType,
    Spine,
)

Frame = AnalysisFrame

# The published window. Sixty months exactly, which is what makes "over five years" a
# statement about the data rather than a rounding of it.
WINDOW_START = date(2021, 1, 1)
WINDOW_END = date(2025, 12, 1)

BALANCE_KEY = "konut_kredisi_bakiye"
RATE_KEY = "konut_kredisi_faizi"
CPI_KEY = "tufe"

BALANCE_QUESTION = "konut kredisi"
RATE_QUESTION = "konut kredisi faizi"

# TÜFE is asked for by code rather than by question. Every other column is resolved from
# Turkish text, but the deflator is a convention (DECISIONS #9), not something the user
# asked about, and picking it by retrieval would make the deflation depend on phrasing.
CPI_SERIES_ID = "evds.TP.GENENDEKS.T1"

CATALOG_COLUMNS = (
    "series_id, source, raw_label, name_tr, measure_type, unit_raw, unit_normalized, "
    "scale_factor, sector_scope, native_freq, aggregation_rule, province, currency_basis, "
    "nonzero_observations"
)


@dataclass(frozen=True)
class Evidence:
    """One computed comparison, with the inputs that produced it."""

    label: str
    first: float
    last: float
    change_pct: float | None
    change_abs: float

    def __str__(self) -> str:
        pct = f"{self.change_pct:+.1f}%" if self.change_pct is not None else "—"
        return f"{self.label}: {self.first:,.2f} -> {self.last:,.2f} ({pct})"


@dataclass(frozen=True)
class TurnResult:
    frame: AnalysisFrame
    resolutions: tuple[SeriesResolution, ...]
    evidence: tuple[Evidence, ...]
    caveats: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0

    @property
    def answer_tr(self) -> str:
        return "\n".join(f.statement for f in self.frame.findings)


def _load_catalog(connection: duckdb.DuckDBPyConnection) -> tuple[list[dict], list[Concept]]:
    rows = [
        {k: (None if v is None or (isinstance(v, float) and v != v) else v) for k, v in r.items()}
        for r in connection.execute(f"SELECT {CATALOG_COLUMNS} FROM series_catalog")
        .fetchdf()
        .to_dict("records")
    ]
    return rows, build_concepts(rows)


def _spine_periods(
    connection: duckdb.DuckDBPyConnection, series_id: str, start: date, end: date
) -> tuple[date, ...]:
    return tuple(
        r[0]
        for r in connection.execute(
            "SELECT period FROM series_observations "
            "WHERE series_id = ? AND period BETWEEN ? AND ? AND value IS NOT NULL "
            "ORDER BY period",
            [series_id, start, end],
        ).fetchall()
    )


def _add(executor, frame: Frame, series_id: str, column_key: str) -> Frame:
    return executor.execute(
        frame,
        Operation(
            operation_id=f"add-{column_key}",
            kind=OperationType.ADD_COLUMN,
            parameters=AddColumnParameters(series_reference=series_id, column_key=column_key),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        ),
    )


def _deflate(executor, frame: Frame, column_key: str, base: date) -> Frame:
    return executor.execute(
        frame,
        Operation(
            operation_id=f"deflate-{column_key}",
            kind=OperationType.DEFLATE_COLUMN,
            parameters=DeflateColumnParameters(
                column_key=column_key,
                deflator_column_key=CPI_KEY,
                base_date=base,
                convention_reference=DEFLATION_CONVENTION,
            ),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        ),
    )


def find_decline_window(values: tuple[float | None, ...]) -> tuple[int, int] | None:
    """The span from a series' peak to its last observation, if it declined after peaking.

    The question asserts that rates fell. Over 2021-2025 they did not - they roughly
    doubled - so an answer that accepts the premise is wrong, and one that simply rejects
    it is unhelpful, because rates *did* fall from the 2024 peak.

    Finding the peak and measuring from there is what turns the premise into something
    tested rather than assumed. It is also the window the asker almost certainly means.
    """
    observed = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(observed) < 3:
        return None
    peak_index, peak_value = max(observed, key=lambda pair: pair[1])
    last_index, last_value = observed[-1]
    if peak_index >= last_index or last_value >= peak_value:
        return None
    return peak_index, last_index


def _evidence(
    frame: Frame,
    column_key: str,
    label: str,
    scale: bool = True,
    span: tuple[int, int] | None = None,
) -> Evidence | None:
    """First and last observed values of a column, and the change between them.

    Observed, not first and last row: a column with a gap at either end would otherwise
    report a change against a missing value. `span` restricts it to a slice of the spine,
    which is how the same arithmetic serves both the full window and the decline window.
    """
    column = next((c for c in frame.columns if c.key == column_key), None)
    if column is None:
        return None
    factor = column.unit.scale if (scale and column.unit) else 1.0
    values = column.values
    if span is not None:
        lo, hi = span
        values = tuple(v if lo <= i <= hi else None for i, v in enumerate(values))
    observed = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(observed) < 2:
        return None
    first, last = observed[0][1] * factor, observed[-1][1] * factor
    change = None if first == 0 else (last - first) / abs(first) * 100
    return Evidence(label=label, first=first, last=last, change_pct=change, change_abs=last - first)


def build_turn1(
    catalog: Path | str,
    *,
    start: date = WINDOW_START,
    end: date = WINDOW_END,
) -> TurnResult:
    """Resolve, assemble and compute the answer to the first published question."""
    started = time.perf_counter()
    connection = duckdb.connect(str(catalog), read_only=True)
    source = CatalogSeriesSource(catalog)
    executor = create_operation_executor(series_source=source)

    try:
        rows, concepts = _load_catalog(connection)
        provinces = frozenset(r["province"] for r in rows if r.get("province"))

        balance = resolve_series(
            concepts, BALANCE_QUESTION, candidates=rows, provinces=provinces, limit=8
        )
        rate = resolve_series(
            concepts, RATE_QUESTION, candidates=rows, provinces=provinces, limit=8
        )
        if not balance.resolved or not rate.resolved:
            raise RuntimeError("turn 1 could not resolve both of its series")

        balance_id, rate_id = balance.series_ids[0], rate.series_ids[0]

        # The balance defines the spine: it is the monthly series, and the rate is weekly
        # and will be collapsed onto it. Building the spine from the rate instead would
        # give a weekly axis the balance cannot fill.
        periods = _spine_periods(connection, balance_id, start, end)
        if not periods:
            raise RuntimeError(f"no {balance_id} observations between {start} and {end}")

        frame = Frame(frame_id="turn1", spine=Spine(values=periods, label="Dönem"))
        frame = _add(executor, frame, balance_id, BALANCE_KEY)
        frame = _add(executor, frame, rate_id, RATE_KEY)
        frame = _add(executor, frame, CPI_SERIES_ID, CPI_KEY)
        frame = _deflate(executor, frame, BALANCE_KEY, periods[0])

        real_key = deflated_column_key(BALANCE_KEY, CPI_KEY, periods[0])
        rate_column = next(c for c in frame.columns if c.key == RATE_KEY)
        decline = find_decline_window(rate_column.values)

        evidence = [
            e
            for e in (
                _evidence(frame, RATE_KEY, "Faiz, tüm dönem (%)", scale=False),
                _evidence(frame, BALANCE_KEY, "Nominal bakiye, tüm dönem (TL)"),
                _evidence(frame, real_key, "Reel bakiye, tüm dönem (TL)"),
            )
            if e is not None
        ]
        if decline is not None:
            evidence += [
                e
                for e in (
                    _evidence(frame, RATE_KEY, "Faiz, düşüş dönemi (%)", scale=False, span=decline),
                    _evidence(
                        frame, BALANCE_KEY, "Nominal bakiye, düşüş dönemi (TL)", span=decline
                    ),
                    _evidence(frame, real_key, "Reel bakiye, düşüş dönemi (TL)", span=decline),
                    _evidence(frame, CPI_KEY, "TÜFE, düşüş dönemi", scale=False, span=decline),
                )
                if e is not None
            ]

        frame = _findings(frame, tuple(evidence), real_key, periods, decline)
        caveats = _caveats(frame, balance, rate)
        return TurnResult(
            frame=frame,
            resolutions=(balance, rate),
            evidence=tuple(evidence),
            caveats=caveats,
            elapsed_seconds=time.perf_counter() - started,
        )
    finally:
        source.close()
        connection.close()


def _findings(
    frame: Frame,
    evidence: tuple[Evidence, ...],
    real_key: str,
    periods: tuple[date, ...],
    decline: tuple[int, int] | None,
) -> Frame:
    """Turn the computed comparisons into findings. Every number comes from the frame."""
    by = {e.label: e for e in evidence}
    full_window = f"{periods[0]:%Y-%m} - {periods[-1]:%Y-%m}"

    # 1. Test the premise instead of repeating it.
    faiz_all = by.get("Faiz, tüm dönem (%)")
    if faiz_all is not None:
        verdict = "düşmedi, yükseldi" if faiz_all.change_abs > 0 else "düştü"
        frame = create_finding(
            frame,
            finding_id="f-premise",
            statement=(
                f"Sorunun varsaydığının aksine, {full_window} genelinde konut kredisi faizi "
                f"{verdict}: %{faiz_all.first:.2f} -> %{faiz_all.last:.2f} "
                f"({faiz_all.change_abs:+.2f} puan)."
            ),
            supporting_column_keys=(RATE_KEY,),
            producing_tool="turn1.arithmetic",
            caveats=(
                "Oran TP.KTF12: yeni kullandırılan TL konut kredilerinin haftalık ağırlıklı "
                "ortalama faizi (akım). Mevcut stokun faizi değildir.",
                "Haftalık seri aylık eksene ortalama alınarak indirgendi.",
            ),
        )

    # 2. The premise does hold for the window after the peak, so answer that question.
    faiz_dec = by.get("Faiz, düşüş dönemi (%)")
    nominal_dec = by.get("Nominal bakiye, düşüş dönemi (TL)")
    real_dec = by.get("Reel bakiye, düşüş dönemi (TL)")
    cpi_dec = by.get("TÜFE, düşüş dönemi")
    if decline is not None and faiz_dec and nominal_dec and real_dec and cpi_dec:
        window = f"{periods[decline[0]]:%Y-%m} - {periods[decline[1]]:%Y-%m}"
        flat = abs(real_dec.change_pct or 0) < 2
        shape = (
            "yatay kaldı" if flat else ("büyüdü" if (real_dec.change_pct or 0) > 0 else "küçüldü")
        )
        frame = create_finding(
            frame,
            finding_id="f-decline",
            statement=(
                f"Faizin gerilediği {window} döneminde ({faiz_dec.first:.2f}% -> "
                f"{faiz_dec.last:.2f}%, {faiz_dec.change_abs:+.2f} puan) bakiye nominal "
                f"olarak %{nominal_dec.change_pct:+.0f} arttı; ancak TÜFE aynı dönemde "
                f"%{cpi_dec.change_pct:+.0f} arttı. Sabit fiyatlarla bakiye "
                f"%{real_dec.change_pct:+.1f} ile {shape}. Yani faiz düşüşü kredi "
                "kullanımını reel olarak büyütmedi; nominal artışın tamamına yakını "
                "enflasyondur."
            ),
            supporting_column_keys=(RATE_KEY, BALANCE_KEY, real_key, CPI_KEY),
            producing_tool="turn1.arithmetic",
            caveats=(
                f"Düşüş dönemi hesaplanarak bulundu: faizin zirvesi "
                f"{periods[decline[0]]:%Y-%m}, son gözlem {periods[decline[1]]:%Y-%m}.",
                f"Deflasyon: {DEFLATION_CONVENTION}, baz {periods[0]:%Y-%m}.",
            ),
        )

    # 3. The definitional slip, stated whether or not anyone asks.
    frame = create_finding(
        frame,
        finding_id="f-olcum",
        statement=(
            "Buradaki 'hacim' kullandırılan yeni kredi tutarı değil, dönem sonu kredi "
            "bakiyesidir. Hiçbir kaynak (BDDK Aylık/Haftalık/FinTürk, TCMB EVDS) "
            "kullandırılan konut kredisi tutarını yayımlamıyor; bakiye geri ödeme ve "
            "yeniden değerleme ile de değişir."
        ),
        supporting_column_keys=(BALANCE_KEY,),
        producing_tool="turn1.scope",
        caveats=("DECISIONS #10; 53.792 EVDS serisi arandı, akım serisi bulunamadı.",),
    )
    return frame


def _caveats(frame: Frame, balance: SeriesResolution, rate: SeriesResolution) -> tuple[str, ...]:
    """What the answer has to disclose beyond its findings."""
    out: list[str] = []
    for column in frame.columns:
        if column.missing_count:
            out.append(
                f"{column.key}: {column.missing_count} dönemde gözlem yok; "
                "doldurulmadı, boş bırakıldı."
            )
    for resolution in (balance, rate):
        if resolution.needs_disclosure and resolution.substitution:
            out.append(resolution.substitution.notice_tr())
        for facet in resolution.facets:
            if facet.facet == "sector_scope" and not facet.stated:
                out.append(f"Banka grubu belirtilmedi; {facet.value} kapsamı kullanıldı.")
                break
    return tuple(dict.fromkeys(out))
