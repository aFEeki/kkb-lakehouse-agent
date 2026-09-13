"""Build the series catalog from the bronze layer.

Bronze is the pinned input; the catalog is derived. Running this twice on the same
bronze produces the same catalog, which is what lets the snapshot be shared and everyone
arrive at identical numbers.

Sources handled here: BDDK Aylık (JSON), BDDK Haftalık (HTML), BDDK FinTürk (JSON)
and EVDS (parquet).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from kkb_agent.catalog.identity import identify, normalise_label
from kkb_agent.catalog.schema import (
    Frequency,
    MeasureType,
    SeriesMeta,
    Source,
    default_aggregation,
    normalise_unit,
)
from kkb_agent.transform.cumulative import (
    BDDK_AYLIK_STATEMENT,
    CumulativeMode,
    StatementKind,
    classify,
    resolve_by_statement,
)
from kkb_agent.transform.haftalik_html import extract as extract_weekly

# Column index in a BDDK Aylık row's `cell` array.
_LABEL = 2
_FIRST_VALUE = 4

# FinTürk tables are quarterly province data; all are balance-sheet positions except
# the ratio and branch-count tables.
FINTURK_STATEMENT: dict[int, StatementKind] = {
    1: StatementKind.BALANCE_SHEET,  # Krediler
    2: StatementKind.BALANCE_SHEET,  # Mevduat
    3: StatementKind.BALANCE_SHEET,  # Bireysel Bankacılık
    4: StatementKind.BALANCE_SHEET,  # Seçilmiş Sektörel Krediler
    5: StatementKind.RATIO,  # Oranlar
    6: StatementKind.RATIO,  # Şubeler (Adet) ve Nüfusa Göre Dağılım
    7: StatementKind.BALANCE_SHEET,  # Altın Kredileri ve Altın Mevduatı
}

# FinTürk responses carry NO caption, so the unit is not in the data at all - it exists
# only in the page's table dropdown. Recorded here from those option labels, because a
# series whose unit we cannot state is a series we cannot safely serve.
FINTURK_TABLE_UNIT: dict[int, str] = {
    1: "Bin TL",  # Krediler (Bin TL)
    2: "Bin TL",  # Mevduat (Bin TL)
    3: "Bin TL",  # Bireysel Bankacılık (Bin TL)
    4: "Bin TL",  # Seçilmiş Sektörel Krediler (Bin TL)
    5: "%",  # Oranlar (%)
    6: "",  # Şubeler (Adet) ve Nüfusa Göre Dağılım (TL) - mixed, per column
    7: "Bin TL",  # Altın Kredileri ve Altın Mevduatı (Bin TL)
}


def _measure_from(statement: StatementKind | None, unit_raw: str) -> MeasureType:
    """Infer measure type from the statement kind and unit.

    Deliberately conservative: anything not clearly determined stays UNKNOWN, and an
    UNKNOWN series is not servable. Guessing here is how a balance gets presented as
    new lending.
    """
    if unit_raw.strip() == "%":
        return MeasureType.RATE
    if statement is StatementKind.INCOME_STATEMENT:
        return MeasureType.FLOW
    if statement in (StatementKind.BALANCE_SHEET, StatementKind.OFF_BALANCE_SHEET):
        return MeasureType.STOCK
    if statement is StatementKind.RATIO:
        return MeasureType.RATIO
    return MeasureType.UNKNOWN


def _caption_unit(caption: str) -> str:
    """'Tüketici Kredileri (milyon TL), Dönem:2025/12' -> 'milyon TL'."""
    if "(" not in caption:
        return ""
    return caption.split("(")[-1].split(")")[0].strip()


def iter_bddk_aylik(bronze: Path) -> Iterator[tuple[SeriesMeta, pd.Series]]:
    """One (meta, series) pair per BDDK monthly row."""
    frames: dict[tuple[int, str], dict] = {}
    meta_bits: dict[tuple[int, str], dict] = {}

    for period_dir in sorted(p for p in bronze.iterdir() if p.is_dir()):
        y, m = (int(x) for x in period_dir.name.split("-"))
        ts = pd.Timestamp(y, m, 1)
        for f in sorted(period_dir.glob("t*_taraf*.json")):
            table_no = int(f.stem[1:3])
            taraf = int(f.stem.split("taraf")[1])
            payload = json.loads(f.read_text(encoding="utf-8"))["Json"]
            rows = payload["data"]["rows"] if isinstance(payload.get("data"), dict) else []
            unit_raw = _caption_unit(payload.get("caption") or "")
            for r in rows:
                cell = r.get("cell") or []
                if len(cell) <= _FIRST_VALUE:
                    continue
                values = [c for c in cell[_FIRST_VALUE:] if isinstance(c, int | float)]
                if not values:
                    continue
                key = (table_no, normalise_label(cell[_LABEL]))
                frames.setdefault(key, {})[ts] = float(values[-1])
                meta_bits.setdefault(
                    key,
                    {
                        "raw_label": str(cell[_LABEL]).strip(),
                        "taraf": taraf,
                        "unit_raw": unit_raw,
                        "row_index": cell[1] if len(cell) > 1 else None,
                    },
                )

    for (table_no, label), points in frames.items():
        s = pd.Series(points).sort_index()
        bits = meta_bits[(table_no, label)]
        statement = BDDK_AYLIK_STATEMENT.get(table_no)
        ev = classify(s)
        mode, why = resolve_by_statement(table_no, label, ev.mode)
        unit_norm, scale = normalise_unit(bits["unit_raw"])
        measure = _measure_from(statement, bits["unit_raw"])
        ident = identify("bddk_aylik", table_no, bits["taraf"], bits["raw_label"])

        yield (
            SeriesMeta(
                series_id=ident.series_id,
                source=Source.BDDK_AYLIK,
                source_ref=f"t{table_no:02d}#row{bits['row_index']}",
                name_tr=label,
                raw_label=bits["raw_label"],
                measure_type=measure,
                statement_kind=statement,
                sector_scope=str(bits["taraf"]),
                currency_basis="Toplam",
                unit_raw=bits["unit_raw"],
                unit_normalized=unit_norm,
                scale_factor=scale,
                cumulative_mode=mode,
                cumulative_evidence=f"{ev.summary()[:180]} || {why}",
                native_freq=Frequency.MONTHLY,
                aggregation_rule=default_aggregation(measure),
                coverage_start=s.index.min().date(),
                coverage_end=s.index.max().date(),
                observations=len(s),
            ),
            s,
        )


# Weekly table id -> statement kind. Same accounting rule as monthly: positions do not
# accumulate, and the weekly bulletin publishes only positions.
HAFTALIK_STATEMENT: dict[int, StatementKind] = {
    289: StatementKind.BALANCE_SHEET,  # Krediler
    290: StatementKind.BALANCE_SHEET,  # Takipteki Alacaklar
    291: StatementKind.BALANCE_SHEET,  # Menkul Değerler
    292: StatementKind.BALANCE_SHEET,  # Mevduat
    293: StatementKind.BALANCE_SHEET,  # Diğer Bilanço Kalemleri
    294: StatementKind.OFF_BALANCE_SHEET,  # Bilanço Dışı İşlemler
    295: StatementKind.BALANCE_SHEET,  # Bankalarda Saklanan Menkul Değerler - 1
    296: StatementKind.BALANCE_SHEET,  # Bankalarda Saklanan Menkul Değerler - 2
    297: StatementKind.BALANCE_SHEET,  # Yabancı Para Pozisyonu
}


def iter_bddk_haftalik(bronze: Path) -> Iterator[tuple[SeriesMeta, pd.Series]]:
    """One series per (table, row label, value column) in the weekly bulletin.

    The page header states its own period, so it is trusted over the directory name -
    a file whose header disagrees is skipped rather than filed under the wrong week.
    """
    frames: dict[tuple, dict] = {}
    meta_bits: dict[tuple, dict] = {}

    for currency_dir in sorted(p for p in bronze.iterdir() if p.is_dir()):
        for period_dir in sorted(p for p in currency_dir.iterdir() if p.is_dir()):
            for f in sorted(period_dir.glob("tablo*.html")):
                table_id = int(f.stem.replace("tablo", ""))
                parsed = extract_weekly(f.read_text(encoding="utf-8", errors="replace"))
                if parsed is None or parsed.period is None or not len(parsed):
                    continue
                ts = pd.Timestamp(parsed.period)
                for label, values in parsed.rows:
                    norm = normalise_label(label)
                    for col, value in zip(parsed.columns, values, strict=False):
                        if value is None:
                            continue
                        key = (table_id, norm, col)
                        frames.setdefault(key, {})[ts] = float(value)
                        meta_bits.setdefault(
                            key,
                            {
                                "raw_label": label,
                                "unit_raw": parsed.unit_raw,
                                "table_name": parsed.table_name,
                                "currency": currency_dir.name,
                            },
                        )

    for (table_id, label, col), points in frames.items():
        s = pd.Series(points).sort_index()
        bits = meta_bits[(table_id, label, col)]
        statement = HAFTALIK_STATEMENT.get(table_id, StatementKind.BALANCE_SHEET)
        mode, why = resolve_by_statement(
            table_id, label, CumulativeMode.AMBIGUOUS, statements=HAFTALIK_STATEMENT
        )
        unit_norm, scale = normalise_unit(bits["unit_raw"])
        measure = _measure_from(statement, bits["unit_raw"])
        slug = normalise_label(f"{label} {col}").casefold().replace(" ", "_")
        yield (
            SeriesMeta(
                series_id=f"bddk_haftalik.t{table_id}.{slug}"[:200],
                source=Source.BDDK_HAFTALIK,
                source_ref=f"tablo{table_id}#{col}",
                name_tr=f"{bits['table_name']} - {label}",
                raw_label=bits["raw_label"],
                measure_type=measure,
                statement_kind=statement,
                currency_basis=col,
                unit_raw=bits["unit_raw"],
                unit_normalized=unit_norm,
                scale_factor=scale,
                cumulative_mode=mode,
                cumulative_evidence=why,
                native_freq=Frequency.WEEKLY,
                aggregation_rule=default_aggregation(measure),
                coverage_start=s.index.min().date(),
                coverage_end=s.index.max().date(),
                observations=len(s),
            ),
            s,
        )


def iter_bddk_finturk(bronze: Path) -> Iterator[tuple[SeriesMeta, pd.Series]]:
    """FinTürk: quarterly, one series per (table, column, province, bank group)."""
    frames: dict[tuple, dict] = {}
    meta_bits: dict[tuple, dict] = {}

    for period_dir in sorted(p for p in bronze.iterdir() if p.is_dir()):
        y, q = (int(x) for x in period_dir.name.split("-"))
        ts = pd.Timestamp(y, q, 1)
        for f in sorted(period_dir.glob("tablo*.json")):
            table_no = int(f.stem.replace("tablo", ""))
            payload = json.loads(f.read_text(encoding="utf-8"))["Json"]
            rows = payload["data"]["rows"] if isinstance(payload.get("data"), dict) else []
            col_names = payload.get("colNames") or []
            unit_raw = FINTURK_TABLE_UNIT.get(table_no, "")
            for r in rows:
                cell = r.get("cell") or []
                if len(cell) < 6:
                    continue
                province, group = str(cell[3]), str(cell[4])
                for ci in range(5, len(cell)):
                    if not isinstance(cell[ci], int | float):
                        continue
                    col = col_names[ci] if ci < len(col_names) else f"col{ci}"
                    key = (table_no, col, province, group)
                    frames.setdefault(key, {})[ts] = float(cell[ci])
                    meta_bits.setdefault(key, {"unit_raw": unit_raw})

    for (table_no, col, province, group), points in frames.items():
        s = pd.Series(points).sort_index()
        bits = meta_bits[(table_no, col, province, group)]
        statement = FINTURK_STATEMENT.get(table_no, StatementKind.BALANCE_SHEET)
        # Quarterly series are too short for the pattern test; the statement decides.
        mode, why = resolve_by_statement(
            table_no, col, CumulativeMode.AMBIGUOUS, statements=FINTURK_STATEMENT
        )
        unit_norm, scale = normalise_unit(bits["unit_raw"])
        measure = _measure_from(statement, bits["unit_raw"])
        slug = normalise_label(f"{col} {province} {group}").lower().replace(" ", "_")
        yield (
            SeriesMeta(
                series_id=f"bddk_finturk.t{table_no:02d}.{slug}"[:200],
                source=Source.BDDK_FINTURK,
                source_ref=f"tablo{table_no}#{col}",
                name_tr=f"{col} - {province} - {group}",
                raw_label=col,
                measure_type=measure,
                statement_kind=statement,
                sector_scope=group,
                province=None if province.upper() == "HEPSİ" else province,
                unit_raw=bits["unit_raw"],
                unit_normalized=unit_norm,
                scale_factor=scale,
                cumulative_mode=mode,
                cumulative_evidence=why,
                native_freq=Frequency.QUARTERLY,
                aggregation_rule=default_aggregation(measure),
                coverage_start=s.index.min().date(),
                coverage_end=s.index.max().date(),
                observations=len(s),
            ),
            s,
        )


def iter_evds(silver: Path, config: Path | None = None) -> Iterator[tuple[SeriesMeta, pd.Series]]:
    """EVDS parquet, with names and frequencies from the committed config."""
    declared: dict[str, dict] = {}
    if config and config.exists():
        cfg = json.loads(config.read_text(encoding="utf-8"))
        declared = {x["code"]: x for x in cfg.get("series", [])}

    freq_map = {
        "daily": Frequency.DAILY,
        "weekly": Frequency.WEEKLY,
        "monthly": Frequency.MONTHLY,
        "quarterly": Frequency.QUARTERLY,
    }

    for f in sorted(silver.glob("*.parquet")):
        code = f.stem
        df = pd.read_parquet(f)
        if df.empty:
            continue
        s = (
            pd.Series(
                pd.to_numeric(df["value"], errors="coerce").values,
                index=pd.to_datetime(df["period"]),
            )
            .dropna()
            .sort_index()
        )
        if s.empty:
            continue
        d = declared.get(code, {})
        name = d.get("name", code)
        is_rate = "faiz" in name.casefold() or "oran" in name.casefold()
        is_index = "endeks" in name.casefold()
        measure = (
            MeasureType.RATE if is_rate else MeasureType.INDEX if is_index else MeasureType.STOCK
        )
        yield (
            SeriesMeta(
                series_id=f"evds.{code}",
                source=Source.EVDS,
                source_ref=code,
                name_tr=name,
                raw_label=name,
                measure_type=measure,
                # EVDS publishes levels and rates, not year-to-date accumulations.
                cumulative_mode=CumulativeMode.NONE,
                cumulative_evidence="EVDS publishes levels and rates, not accumulations",
                unit_raw="%" if is_rate else "",
                unit_normalized="%" if is_rate else "",
                scale_factor=1.0,
                native_freq=freq_map.get(d.get("frequency", "monthly"), Frequency.MONTHLY),
                aggregation_rule=default_aggregation(measure),
                coverage_start=s.index.min().date(),
                coverage_end=s.index.max().date(),
                observations=len(s),
                retrieved_at=(
                    pd.to_datetime(df["retrieved_at"].iloc[0]).to_pydatetime()
                    if "retrieved_at" in df
                    else None
                ),
            ),
            s,
        )


def to_frames(pairs: list[tuple[SeriesMeta, pd.Series]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Catalog rows and long-format observations, ready for DuckDB."""
    catalog, observations = [], []
    for meta, s in pairs:
        catalog.append(
            {
                "series_id": meta.series_id,
                "source": str(meta.source),
                "source_ref": meta.source_ref,
                "name_tr": meta.name_tr,
                "raw_label": meta.raw_label,
                "measure_type": str(meta.measure_type),
                "statement_kind": str(meta.statement_kind) if meta.statement_kind else None,
                "sector_scope": meta.sector_scope,
                "currency_basis": meta.currency_basis,
                "province": meta.province,
                "unit_raw": meta.unit_raw,
                "unit_normalized": meta.unit_normalized,
                "scale_factor": meta.scale_factor,
                "cumulative_mode": str(meta.cumulative_mode),
                "cumulative_evidence": meta.cumulative_evidence,
                "cumulative_verified_by": meta.cumulative_verified_by,
                "native_freq": str(meta.native_freq),
                "aggregation_rule": str(meta.aggregation_rule),
                "coverage_start": meta.coverage_start,
                "coverage_end": meta.coverage_end,
                "observations": meta.observations,
                "retrieved_at": meta.retrieved_at,
                "source_hash": meta.source_hash,
                "notes": meta.notes,
            }
        )
        for period, value in s.items():
            observations.append(
                {
                    "series_id": meta.series_id,
                    "period": period.date() if hasattr(period, "date") else period,
                    "value": None if pd.isna(value) else float(value),
                }
            )
    return pd.DataFrame(catalog), pd.DataFrame(observations)


def _unused() -> date | datetime | None:  # pragma: no cover - keeps imports honest
    return None
