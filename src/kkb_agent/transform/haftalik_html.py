"""Extract tables from the BDDK weekly bulletin's HTML.

The monthly bulletin returns JSON. Weekly renders HTML, so the values arrive as
Turkish-formatted text and the structure has to be read rather than deserialised.

Three things make that manageable, all discovered by looking at the files:

**The same table is rendered three times at different precisions**, under the ids
`Tablo`, `TabloExcel` and `TabloExcel2`. They are the same figures; `TabloExcel2` carries
the most decimals. The id is used as a hint but the choice is *measured*, so a renamed id
does not silently cost precision.

**The header carries the period and the unit**: `Sektör / Krediler (8 Ocak 2021 Cuma)
(Milyon TL)`. That gives weekly the same self-verification the monthly captions provide -
the response states which period it is, so a report viewer silently serving the current
week is detectable.

**Row labels embed row-number references** exactly as monthly does
(`Toplam Krediler (2+10)`), so identity normalisation is shared rather than duplicated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from bs4 import BeautifulSoup

from kkb_agent.transform.turkish_numeric import most_precise, parse_number

# Preference order when several precision variants are present. Measured, not assumed.
PRECISION_TABLE_IDS = ("TabloExcel2", "TabloExcel", "Tablo")

TURKISH_MONTHS = {
    "ocak": 1,
    "şubat": 2,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "agustos": 8,
    "eylül": 9,
    "eylul": 9,
    "ekim": 10,
    "kasım": 11,
    "kasim": 11,
    "aralık": 12,
    "aralik": 12,
}

# "( 8 Ocak 2021 Cuma )" -> day, month name, year
_HEADER_DATE = re.compile(r"(\d{1,2})\s+([A-Za-zçğıöşüÇĞİÖŞÜ]+)\s+(\d{4})")
# trailing "(Milyon TL)" / "(Bin TL)" / "(%)"
_HEADER_UNIT = re.compile(r"\(([^()]*(?:TL|%)[^()]*)\)\s*$")


@dataclass
class WeeklyTable:
    """One parsed weekly table."""

    table_name: str  # "Krediler"
    period: date | None  # read back from the header, not the filename
    unit_raw: str  # "Milyon TL"
    columns: list[str] = field(default_factory=list)  # ["TP", "YP", "TOPLAM"]
    rows: list[tuple[str, list[Decimal | None]]] = field(default_factory=list)
    source_table_id: str = ""  # which precision variant was used

    def __len__(self) -> int:
        return len(self.rows)


def _cells(tr) -> list[str]:
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def parse_header(text: str) -> tuple[str, date | None, str]:
    """'Sektör / Krediler ( 8 Ocak 2021 Cuma ) (Milyon TL)' -> name, date, unit."""
    flat = " ".join(text.split())

    unit = ""
    m = _HEADER_UNIT.search(flat)
    if m:
        unit = m.group(1).strip()
        flat = flat[: m.start()].strip()

    period = None
    m = _HEADER_DATE.search(flat)
    if m:
        month = TURKISH_MONTHS.get(m.group(2).casefold())
        if month:
            period = date(int(m.group(3)), month, int(m.group(1)))
        flat = flat[: m.start()].rstrip(" (")

    name = flat.split("/")[-1].strip(" ()")
    return name, period, unit


def extract(html: str) -> WeeklyTable | None:
    """Parse the richest precision variant out of one weekly page.

    Returns None when the page contains no data table, rather than an empty result that
    could be mistaken for a period with no activity.
    """
    soup = BeautifulSoup(html, "lxml")

    candidates = []
    for tid in PRECISION_TABLE_IDS:
        t = soup.find("table", id=tid)
        if t is None:
            continue
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue
        candidates.append((tid, t, trs))
    if not candidates:
        return None

    # Choose by measured decimal detail, so a renamed id cannot silently cost precision.
    first_data_row = [_cells(trs[1]) for _, _, trs in candidates]
    best = most_precise(first_data_row)
    table_id, table, trs = candidates[best]

    headers = _cells(trs[0])
    label_header = headers[1] if len(headers) > 1 else ""
    name, period, unit = parse_header(label_header)
    value_columns = [h for h in headers[2:] if h]

    rows: list[tuple[str, list[Decimal | None]]] = []
    for tr in trs[1:]:
        cells = _cells(tr)
        if len(cells) < 3:
            continue
        label = " ".join(cells[1].split())
        if not label:
            continue
        values = [parse_number(c, strict=False) for c in cells[2:]]
        if all(v is None for v in values):
            continue  # a spacer or section heading, not an observation
        rows.append((label, values))

    return WeeklyTable(
        table_name=name,
        period=period,
        unit_raw=unit,
        columns=value_columns,
        rows=rows,
        source_table_id=table_id,
    )


def period_matches(parsed: date | None, expected: date) -> bool:
    """Whether the page states the period we asked for.

    The same guard the monthly crawler applies to captions: trust the response over the
    request, so a viewer serving the current week instead of the requested one is caught.
    """
    return parsed is not None and parsed == expected
