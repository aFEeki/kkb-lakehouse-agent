"""Stable series identity for BDDK rows.

BDDK row labels embed references to *other row numbers*, and those numbers shift whenever
a row is inserted above. The same measure therefore appears under different labels over
time:

    2021-01   Risk Ağırlıklı Kalemler Toplamı (10+27+28)
    2022-06   Risk Ağırlıklı Kalemler Toplamı (10+30+31)

Match on the label and you silently split one series into several, or join two that are
not the same thing. Row *order* is no safer - insertion is exactly what shifts it.

Identity is therefore a composite of (source, table, sector scope, normalised label),
with an explicit rename map for cases normalisation cannot reach. The raw label is always
preserved alongside; normalisation is recorded as a transform, never applied invisibly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# "(10+27+28)" or "(2+3+4)" - a pure arithmetic reference to other rows.
_FORMULA_ARITHMETIC = re.compile(r"\s*\(\s*\d+(?:\s*[+\-]\s*\d+)+\s*\)\s*$")

# "(2 den 24'e)" / "(2 den 26'ya)" - a Turkish row-range reference.
_FORMULA_RANGE = re.compile(r"\s*\(\s*\d+\s*den\s+\d+['’][a-zçğıöşü]+\s*\)\s*$", re.IGNORECASE)

# Footnote markers BDDK appends to labels: one or more * or (*) at the end.
_FOOTNOTE = re.compile(r"\s*\(?\*+\)?\s*$")

# Labels whose identity normalisation cannot recover, mapped to a canonical name.
# Seeded with the cases confirmed in the 2021-01..2026-06 range. Add to this rather
# than loosening the patterns above - a broader regex strips legitimate parentheses.
RENAME_MAP: dict[tuple[int, str], str] = {}


def turkish_casefold(s: str) -> str:
    """Casefold correctly for Turkish.

    Python's str.lower() maps 'I' to 'i' where Turkish needs 'ı', and leaves a combining
    dot on 'İ'. Any key comparison routed through .lower() silently fails to match, so
    every comparison in the catalog goes through this instead.

    Composed first: the same 'İ' arrives precomposed from one source and as 'I' plus a
    combining dot from another, and only the precomposed form matches the replacement.
    """
    s = unicodedata.normalize("NFC", s).replace("İ", "i").replace("I", "ı")
    return unicodedata.normalize("NFC", s.lower())


def slugify(text: str) -> str:
    """The one slug policy for every source's series id.

    Three sources had grown three policies, each wrong in its own way: stripping every
    character outside [a-z0-9] deleted the Turkish letters and turned "Tüketici Kredileri
    - Konut" into "t_ketici_kredileri_konut", while .lower() and .casefold() left a
    combining dot behind on 'İ' and produced ids like "c)_i̇htiyaç_tp" carrying an
    invisible character and a stray bracket.

    Neither broke a lookup - ids are written once and passed by reference - but an
    identifier nobody can retype is a trap, and three policies mean the next id built by
    hand matches none of them. Turkish letters are kept, everything else separates.
    """
    return re.sub(r"\W+", "_", turkish_casefold(text), flags=re.UNICODE).strip("_")


def normalise_label(label: str) -> str:
    """Strip row-number references and footnote markers, keeping meaningful text.

    Only trailing references are removed. A label whose parentheses carry meaning -
    "Tüketici Kredileri - Konut (Dövize Endeksli)" - is left intact.
    """
    out = str(label).strip()
    # All patterns cycle together until nothing more strips. Running each to convergence
    # separately is wrong: "Kurumsal Kredi Kartları (28+29)**" only exposes the formula
    # once the footnote is gone, and by then the formula pattern has had its turn.
    prev = None
    while prev != out:
        prev = out
        for pattern in (_FORMULA_ARITHMETIC, _FORMULA_RANGE, _FOOTNOTE):
            out = pattern.sub("", out).strip()
    return " ".join(out.split())


@dataclass(frozen=True)
class SeriesIdentity:
    """What identifies a BDDK series, and what it was called at the time."""

    source: str  # "bddk_aylik" | "bddk_haftalik"
    table_no: int
    taraf: int
    normalised_label: str
    raw_label: str

    @property
    def series_id(self) -> str:
        slug = slugify(self.normalised_label)
        return f"{self.source}.t{self.table_no:02d}.taraf{self.taraf}.{slug}"

    @property
    def was_renamed(self) -> bool:
        return self.normalised_label != self.raw_label.strip()


def identify(
    source: str,
    table_no: int,
    taraf: int,
    raw_label: str,
) -> SeriesIdentity:
    """Resolve one row label to a stable identity."""
    normalised = normalise_label(raw_label)
    normalised = RENAME_MAP.get((table_no, normalised), normalised)
    return SeriesIdentity(
        source=source,
        table_no=table_no,
        taraf=taraf,
        normalised_label=normalised,
        raw_label=str(raw_label).strip(),
    )
