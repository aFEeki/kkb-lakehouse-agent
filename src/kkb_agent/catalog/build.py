"""Build the series catalog from the bronze layer.

Bronze is the pinned input; the catalog is derived. Running this twice on the same
bronze produces the same catalog, which is what lets the snapshot be shared and everyone
arrive at identical numbers.

Sources handled here: BDDK Aylık (JSON), BDDK Haftalık (HTML), BDDK FinTürk (JSON)
and EVDS (parquet).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from kkb_agent.catalog.identity import identify, normalise_label, turkish_casefold
from kkb_agent.catalog.schema import (
    COUNT_UNITS,
    UNIT_SCALE,
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
    decumulate,
    resolve_by_statement,
)
from kkb_agent.transform.haftalik_html import extract as extract_weekly

# Column index in a BDDK Aylık row's `cell` array.
_LABEL = 2
_FIRST_VALUE = 4

# BDDK's ddlTaraf codes -> the bank-group scope they select. The catalog stores the name
# rather than the code: "10001" tells a reader nothing and embeds as noise, "Sektör" is
# what the question actually asks for.
#
# The groups form three partitions of the sector, each of which must sum back to its
# parent - an independent check on the whole monthly pipeline (SCRUM-28).
TARAF_SCOPE: dict[int, str] = {
    10001: "Sektör",
    10002: "Mevduat",
    10003: "Katılım",
    10004: "Kalkınma ve Yatırım",
    10005: "Yerli Özel",
    10006: "Kamu",
    10007: "Yabancı",
    10008: "Mevduat-Yerli Özel",
    10009: "Mevduat-Kamu",
    10010: "Mevduat-Yabancı",
}

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
    6: "",  # Şubeler (Adet) ve Nüfusa Göre Dağılım (TL) - mixed, see below
    7: "Bin TL",  # Altın Kredileri ve Altın Mevduatı (Bin TL)
}

# Table 6 is the one FinTürk table whose unit is not a property of the table. Its own
# dropdown label says "(TL)", but only four of its six columns are money and one counts
# branches. The measure type is carried here too, because it is what stops a per-capita
# figure being summed across 81 provinces - a branch count may be summed, a per-capita
# amount may not, and both live in this table.
FINTURK_T06_COLUMN: dict[str, tuple[str, MeasureType]] = {
    "Yurtiçi Şube Sayısı": ("Adet", MeasureType.COUNT),
    "Şubeye Düşen Nüfus": ("Kişi", MeasureType.RATIO),
    "Kişi Başı Nakdi Kredi": ("TL", MeasureType.RATIO),
    "Kişi Başı Takipteki Alacak": ("TL", MeasureType.RATIO),
    "Kişi Başı Tasarruf Mevduatı": ("TL", MeasureType.RATIO),
    "Kişi Başı Toplam Mevduat": ("TL", MeasureType.RATIO),
}


# One spelling per scope. BDDK writes bank groups in title case in the monthly bulletin
# ("Sektör", "Kalkınma ve Yatırım") and in upper case in FinTürk ("SEKTÖR", "KALKINMA VE
# YATIRIM"), which put 17 distinct values in a column holding nine scopes - so a filter on
# sector_scope = 'Sektör' silently missed all 41,522 FinTürk rows.
#
# Keyed by Turkish casefold, because "SEKTÖR".lower() is "sektör" but "KATILIM".lower() is
# "katilim" with a dotless ı only under the Turkish rule.
CANONICAL_SCOPE: dict[str, str] = {
    "sektör": "Sektör",
    "mevduat": "Mevduat",
    "katılım": "Katılım",
    "kalkınma ve yatırım": "Kalkınma ve Yatırım",
    "yerli özel": "Yerli Özel",
    "kamu": "Kamu",
    "yabancı": "Yabancı",
    "mevduat-yerli özel": "Mevduat-Yerli Özel",
    "mevduat-kamu": "Mevduat-Kamu",
    "mevduat-yabancı": "Mevduat-Yabancı",
}

CANONICAL_CURRENCY: dict[str, str] = {"tp": "TP", "yp": "YP", "toplam": "Toplam"}


def canonical_scope(raw: str) -> str:
    """One spelling for a bank-group scope. Unknown values pass through unchanged, so a
    group BDDK adds later is visible rather than silently renamed to something wrong."""
    return CANONICAL_SCOPE.get(turkish_casefold(str(raw).strip()), str(raw).strip())


def canonical_currency(raw: str) -> str:
    return CANONICAL_CURRENCY.get(turkish_casefold(str(raw).strip()), str(raw).strip())


@dataclass(frozen=True)
class BronzeProvenance:
    """What bronze state a source's series were built from.

    Per source, not per series. A monthly series is assembled from 66 files, so a
    file-level hash would not identify it; what a reader actually needs to know is which
    acquisition produced this number and when it was fetched. The digest changes if any
    file in that source changes, which is the property that matters for reproducibility.
    """

    source: str
    digest: str
    retrieved_at: datetime | None
    files: int

    @property
    def short(self) -> str:
        return f"{self.source}:{self.digest[:12]}"


def bronze_provenance(manifest: Path, source: str) -> BronzeProvenance | None:
    """Digest one acquisition manifest into a hash and a freshness stamp (I8).

    The digest is over the sorted per-file sha256s, so it does not depend on the order
    the crawler happened to write them - a re-crawl that fetches the same bytes in a
    different order produces the same digest, which is what makes it usable as a
    "same data" check rather than a "same run" one.
    """
    if not manifest.exists():
        return None

    hashes: list[str] = []
    newest: datetime | None = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if sha := row.get("sha256"):
            hashes.append(str(sha))
        stamp = row.get("fetched_at")
        if stamp:
            try:
                parsed = datetime.fromisoformat(str(stamp))
            except ValueError:
                continue
            if newest is None or parsed > newest:
                newest = parsed

    if not hashes:
        return None
    digest = hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest()
    return BronzeProvenance(source=source, digest=digest, retrieved_at=newest, files=len(hashes))


def _measure_from(statement: StatementKind | None, unit_raw: str) -> MeasureType:
    """Infer measure type from the statement kind and unit.

    Deliberately conservative: anything not clearly determined stays UNKNOWN, and an
    UNKNOWN series is not servable. Guessing here is how a balance gets presented as
    new lending.

    A count is decided by its unit before the statement is consulted, because BDDK files
    branch, bank and ATM counts under "Rasyolar" alongside genuine ratios. Reading the
    statement first would label 10,569 branches a ratio.

    The unit is compared after normalisation, not as published. BDDK writes percent as
    "%", "(YÜZDE)" and "(Yüzde)" in different tables; matching the literal "%" silently
    classified the capital-adequacy and FX-position ratios as balance-sheet amounts.
    """
    normalized, _ = normalise_unit(unit_raw)
    if unit_raw.strip().casefold() in COUNT_UNITS:
        return MeasureType.COUNT
    if statement is StatementKind.RATIO:
        return MeasureType.RATIO
    if normalized == "%":
        return MeasureType.RATE
    if statement is StatementKind.INCOME_STATEMENT:
        return MeasureType.FLOW
    if statement in (StatementKind.BALANCE_SHEET, StatementKind.OFF_BALANCE_SHEET):
        return MeasureType.STOCK
    return MeasureType.UNKNOWN


def _caption_unit(caption: str) -> str:
    """'Tüketici Kredileri (milyon TL), Dönem:2025/12' -> 'milyon TL'."""
    if "(" not in caption:
        return ""
    return caption.split("(")[-1].split(")")[0].strip()


# Monthly tables whose caption states no unit and whose rows do not either. Read off the
# published table, not inferred: table 16 counts things, table 17 is entirely ratios
# whose values run 1-5 and are plainly percentages.
AYLIK_TABLE_UNIT: dict[int, str] = {
    16: "Adet",  # Diğer Bilgiler - banka, şube, ATM, personel sayıları
    17: "%",  # Yurt Dışı Şube Rasyoları - labels carry no (%) but the values are
}

# Single rows that contradict their table's caption and do not say so in their label.
# Keyed by (table, normalised label). Found by the bank-group partition check (SCRUM-28):
# a ratio does not add across bank groups, so it stands out against an identity that
# every genuine balance-sheet line satisfies.
#
# Kept as an explicit list of two rather than a rule. The obvious rule - a label
# containing "oran" or "/" is a ratio - would misclassify "TP Mevduat / Katılım Fonları",
# "Gemi/Tekne Yapımı" and eleven other perfectly ordinary balance rows.
AYLIK_ROW_UNIT: dict[tuple[int, str], tuple[str, MeasureType]] = {
    (11, "Likidite Yeterlilik Oranı"): ("%", MeasureType.RATIO),
}

_TRAILING_PAREN = re.compile(r"\(([^()]*)\)\s*$")


def _label_unit(label: str) -> str:
    """Read a unit out of a row label's trailing parenthesis, if it is one.

    Table 15 "Rasyolar" is not one unit. Of its 32 rows, 24 are (%), five are (Bin TL)
    per-employee and per-branch amounts, one is (Kişi) and two are (Gün) maturities. The
    table cannot tell us which; each row says so itself.

    Only a recognised unit counts. Labels end in parentheses all over this source -
    "Risk Ağırlıklı Kalemler Toplamı (10+27+28)", "Toplam Mevduat (Fon)" - and treating
    those as units would be worse than having none.
    """
    m = _TRAILING_PAREN.search(label or "")
    if not m:
        return ""
    candidate = m.group(1).strip()
    return candidate if candidate.casefold() in UNIT_SCALE else ""


_SCOPE = 0  # BDDK repeats the bank-group name in every row's first cell


def scope_matches(rows: list[dict], taraf: int) -> bool:
    """Whether the payload states the bank-group scope we requested.

    The report viewer takes taraf as a form parameter; nothing in the response format
    guarantees it honoured it. Since it names the scope back to us in every row, check.
    An unrecognised taraf code passes - we have no expectation to compare against, and
    inventing one would reject data for a scope BDDK added after this map was written.
    """
    expected = TARAF_SCOPE.get(taraf)
    if expected is None or not rows:
        return True
    stated = str((rows[0].get("cell") or [""])[_SCOPE]).strip()
    return stated == "" or stated == expected


def iter_bddk_aylik(bronze: Path) -> Iterator[tuple[SeriesMeta, pd.Series]]:
    """One (meta, series) pair per BDDK monthly row, per bank-group scope.

    taraf belongs in the key, not just the metadata. The same table and row label exists
    for all ten scopes; keying without taraf lets ten bank groups overwrite each other
    period by period and yields one series carrying the last-read group's figures under
    the first-read group's label.

    Each row states its own scope in cell 0, so the response is trusted over the request:
    a file whose payload disagrees with the taraf we asked for is skipped rather than
    filed under the wrong bank group.
    """
    frames: dict[tuple[int, int, str], dict] = {}
    meta_bits: dict[tuple[int, int, str], dict] = {}

    for period_dir in sorted(p for p in bronze.iterdir() if p.is_dir()):
        y, m = (int(x) for x in period_dir.name.split("-"))
        ts = pd.Timestamp(y, m, 1)
        for f in sorted(period_dir.glob("t*_taraf*.json")):
            table_no = int(f.stem[1:3])
            taraf = int(f.stem.split("taraf")[1])
            payload = json.loads(f.read_text(encoding="utf-8"))["Json"]
            rows = payload["data"]["rows"] if isinstance(payload.get("data"), dict) else []
            unit_raw = _caption_unit(payload.get("caption") or "")
            if not scope_matches(rows, taraf):
                continue
            for r in rows:
                cell = r.get("cell") or []
                if len(cell) <= _FIRST_VALUE:
                    continue
                values = [c for c in cell[_FIRST_VALUE:] if isinstance(c, int | float)]
                if not values:
                    continue
                raw_label = str(cell[_LABEL]).strip()
                key = (table_no, taraf, normalise_label(cell[_LABEL]))
                frames.setdefault(key, {})[ts] = float(values[-1])
                meta_bits.setdefault(
                    key,
                    {
                        "raw_label": raw_label,
                        # The row wins over the caption. A caption states the table's
                        # default; a row naming its own unit is contradicting that default
                        # on purpose, and tables 12 and 13 each carry a "(YÜZDE)" ratio row
                        # inside a table captioned "milyon TL". Reading the caption first
                        # turned those into balance-sheet amounts, which the bank-group
                        # partition check then caught (SCRUM-28).
                        "unit_raw": (
                            override[0]
                            if (override := AYLIK_ROW_UNIT.get((table_no, raw_label)))
                            else (
                                _label_unit(raw_label)
                                or unit_raw
                                or AYLIK_TABLE_UNIT.get(table_no, "")
                            )
                        ),
                        "measure_override": (
                            AYLIK_ROW_UNIT.get((table_no, raw_label), (None, None))[1]
                        ),
                        "row_index": cell[1] if len(cell) > 1 else None,
                    },
                )

    for (table_no, taraf, label), points in frames.items():
        s = pd.Series(points).sort_index()
        bits = meta_bits[(table_no, taraf, label)]
        statement = BDDK_AYLIK_STATEMENT.get(table_no)
        ev = classify(s)
        mode, why = resolve_by_statement(table_no, label, ev.mode)
        unit_norm, scale = normalise_unit(bits["unit_raw"])
        measure = bits.get("measure_override") or _measure_from(statement, bits["unit_raw"])
        ident = identify("bddk_aylik", table_no, taraf, bits["raw_label"])

        yield (
            SeriesMeta(
                series_id=ident.series_id,
                source=Source.BDDK_AYLIK,
                source_ref=f"t{table_no:02d}#row{bits['row_index']}",
                name_tr=label,
                raw_label=bits["raw_label"],
                measure_type=measure,
                statement_kind=statement,
                sector_scope=TARAF_SCOPE.get(taraf, str(taraf)),
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

    The currency directory is part of the key for the same reason taraf is in the monthly
    key. Only one currency is acquired today, so leaving it out would not corrupt anything
    yet - which is precisely why it would go unnoticed until it did.
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
                        key = (currency_dir.name, table_id, norm, col)
                        frames.setdefault(key, {})[ts] = float(value)
                        meta_bits.setdefault(
                            key,
                            {
                                "raw_label": label,
                                "unit_raw": parsed.unit_raw,
                                "table_name": parsed.table_name,
                            },
                        )

    for (currency, table_id, label, col), points in frames.items():
        s = pd.Series(points).sort_index()
        bits = meta_bits[(currency, table_id, label, col)]
        statement = HAFTALIK_STATEMENT.get(table_id, StatementKind.BALANCE_SHEET)
        mode, why = resolve_by_statement(
            table_id, label, CumulativeMode.AMBIGUOUS, statements=HAFTALIK_STATEMENT
        )
        unit_norm, scale = normalise_unit(bits["unit_raw"])
        measure = _measure_from(statement, bits["unit_raw"])
        slug = normalise_label(f"{label} {col}").casefold().replace(" ", "_")
        yield (
            SeriesMeta(
                series_id=f"bddk_haftalik.{currency}.t{table_id}.{slug}"[:200],
                source=Source.BDDK_HAFTALIK,
                source_ref=f"tablo{table_id}#{col}",
                name_tr=f"{bits['table_name']} - {label}",
                raw_label=bits["raw_label"],
                measure_type=measure,
                statement_kind=statement,
                currency_basis=canonical_currency(col),
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
        # Table 6 carries its unit per column rather than per table.
        column_unit, column_measure = FINTURK_T06_COLUMN.get(col, ("", None))
        unit_norm, scale = normalise_unit(column_unit or bits["unit_raw"])
        measure = column_measure or _measure_from(statement, bits["unit_raw"])
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
                sector_scope=canonical_scope(group),
                province=None if province.upper() == "HEPSİ" else province,
                unit_raw=column_unit or bits["unit_raw"],
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


# EVDS's financial accounts publish the same line twice: as a position at the period end
# and as the transactions during the period, distinguished only by "(Konsolide Akım)" or
# "(... Stok)" in the series name. Getting this wrong is the FLOW-versus-STOCK error the
# whole catalog is built to prevent, and here the source states which it is.
#
# Word boundaries matter: "bakım" (maintenance) contains "akım" and appears all over the
# price indices. \b will not match inside it, a plain substring test will.
_AKIM = re.compile(r"\bakım\b")
_STOK = re.compile(r"\bstok\b")


def evds_measure(name: str, unit_raw: str) -> MeasureType:
    """What an EVDS series measures, from its unit first and its name second.

    The unit is the stronger signal because it comes from EVDS's own metadata, while the
    name is prose. The name settles the two things the unit cannot: whether a percentage
    is an interest rate, which averages when downsampled, or a ratio, which takes the
    period end; and whether a money figure is a position or a transaction.

    The unit is compared after normalisation. EVDS writes percent as "Yüzde", "%" and
    "Ağırlıklı ortalama" in different datagroups; testing the raw string sent every
    interest rate whose table is published on a new-business basis - "Konut Kredisi
    (TL, Akım, %)" - down to the money branch, where "Akım" in the name turned it into a
    FLOW. A rate reported as a flow is exactly the error this field exists to prevent.
    """
    unit = unit_raw.strip().casefold()
    normalized, _ = normalise_unit(unit_raw)
    lowered = name.casefold()

    if unit in COUNT_UNITS:
        return MeasureType.COUNT
    if normalized == "endeks":
        return MeasureType.INDEX
    if unit == "ağırlıklı ortalama":
        # Verified across the catalogue: every one of the ten datagroups carrying this
        # unit is a "Faiz Oranları" or "Kâr Oranları" table, so the figure is a rate
        # whether or not the series name happens to say "faiz".
        return MeasureType.RATE
    if normalized == "%":
        return MeasureType.RATE if "faiz" in lowered else MeasureType.RATIO
    if "endeks" in lowered:
        return MeasureType.INDEX
    if unit:
        # The financial accounts say so outright; everything else EVDS publishes in a
        # money unit is a level.
        if _AKIM.search(lowered) and not _STOK.search(lowered):
            return MeasureType.FLOW
        return MeasureType.STOCK
    return MeasureType.UNKNOWN


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
        # The unit comes from the config, which the metadata walk fills in from the
        # DATAGROUP's BIRIMI field. Guessing it from the series name is how 14 series
        # ended up with no unit at all and were refused by is_usable().
        unit_raw = str(d.get("unit", "")).strip()
        unit_norm, scale = normalise_unit(unit_raw)
        measure = evds_measure(name, unit_raw)
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
                unit_raw=unit_raw,
                unit_normalized=unit_norm,
                scale_factor=scale,
                notes=" | ".join(p for p in (d.get("category", ""), d.get("datagroup", "")) if p),
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
        # Counted before the row is written, not after: a series published as 0.0 every
        # month looks as well-covered as any other under `observations` alone.
        meta.nonzero_observations = int((s.notna() & (s != 0)).sum())
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
                "nonzero_observations": meta.nonzero_observations,
                "retrieved_at": meta.retrieved_at,
                "source_hash": meta.source_hash,
                "notes": meta.notes,
            }
        )
        # `value` is this period's own figure for every series in the catalog, so a join
        # cannot accidentally read a year-to-date total as a monthly one. The published
        # figure is kept alongside it: dropping it would make the year-end closure check
        # impossible and would lose the provenance the trust layer needs to show.
        periodised = decumulate(s, meta.cumulative_mode)
        for period, reported in s.items():
            value = periodised.get(period)
            observations.append(
                {
                    "series_id": meta.series_id,
                    "period": period.date() if hasattr(period, "date") else period,
                    "value": None if value is None or pd.isna(value) else float(value),
                    "value_reported": None if pd.isna(reported) else float(reported),
                }
            )
    return pd.DataFrame(catalog), pd.DataFrame(observations)


def _unused() -> date | datetime | None:  # pragma: no cover - keeps imports honest
    return None
