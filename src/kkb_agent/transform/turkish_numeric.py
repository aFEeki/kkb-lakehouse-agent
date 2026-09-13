"""Parse Turkish-formatted numbers out of BDDK's HTML bulletins.

Turkish notation inverts the Anglo convention: `.` groups thousands and `,` marks the
decimal. `1.234` is one thousand two hundred and thirty-four, not 1.234.

That inversion is the whole risk. A parser that treats the dot as a decimal point returns
a figure a thousand times too small, and it looks entirely plausible sitting next to a
percentage column. So this parser validates the grouping rather than stripping characters
and hoping: a dot that is not separating a group of exactly three digits is rejected, not
silently dropped.

Deliberately not using `locale` - the deployed environment's locale is not guaranteed to
be Turkish, and a parser whose behaviour depends on the host is a parser that works until
it is deployed.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Optional sign, groups of three after the first 1-3 digits, optional decimal part.
#   1.234.567,89   |   1234,5   |   72   |   -3.017
_TURKISH_NUMBER = re.compile(
    r"""^
    (?P<sign>[-+])?
    (?P<int>\d{1,3}(?:\.\d{3})*|\d+)
    (?:,(?P<frac>\d+))?
    $""",
    re.VERBOSE,
)

# Cells BDDK uses for "no value". None of these mean zero.
_NULL_TOKENS = {"", "-", "--", "—", "–", "n/a", "na", ".", "..", "...", "*"}


class TurkishNumberError(ValueError):
    """The text is not a Turkish-formatted number."""


def parse_number(text: str | None, *, strict: bool = True) -> Decimal | None:
    """Parse one cell.

    Returns None for an empty or placeholder cell - **never zero**. A silent zero
    corrupts sums without leaving a trace, which the data contract forbids.

    With `strict=False`, unparseable text returns None instead of raising. Use that only
    where a non-numeric cell is expected, never to paper over a parse failure.
    """
    if text is None:
        return None

    s = str(text).replace("\xa0", " ").strip()
    # Parenthesised negatives: (1.234,56)
    negative_parens = False
    if s.startswith("(") and s.endswith(")"):
        s, negative_parens = s[1:-1].strip(), True

    if s.lower() in _NULL_TOKENS:
        return None

    s = s.replace(" ", "")
    m = _TURKISH_NUMBER.match(s)
    if not m:
        if strict:
            raise TurkishNumberError(f"not a Turkish-formatted number: {text!r}")
        return None

    integer = m.group("int").replace(".", "")
    frac = m.group("frac")
    sign = "-" if (m.group("sign") == "-" or negative_parens) else ""
    try:
        return Decimal(f"{sign}{integer}" + (f".{frac}" if frac else ""))
    except InvalidOperation as exc:  # pragma: no cover - regex already constrains this
        raise TurkishNumberError(f"not a Turkish-formatted number: {text!r}") from exc


def looks_numeric(text: str | None) -> bool:
    """True when `parse_number` would return a value."""
    try:
        return parse_number(text) is not None
    except TurkishNumberError:
        return False


def precision_of(text: str | None) -> int:
    """Decimal places in a cell, 0 if none or unparseable.

    Used to pick between BDDK's precision variants of the same table.
    """
    if text is None:
        return 0
    s = str(text).strip().strip("()")
    m = _TURKISH_NUMBER.match(s.replace("\xa0", " ").replace(" ", ""))
    return len(m.group("frac")) if m and m.group("frac") else 0


def most_precise(variants: list[list[str]]) -> int:
    """Index of the variant carrying the most decimal detail.

    BDDK renders the same weekly table several times at different precisions - 0, 2, 3
    and 5 decimals have all been observed. They are the same figures, so take the one
    that loses least. Do not assume a fixed position: the count varies by table.
    """
    if not variants:
        raise ValueError("no variants to choose from")
    scores = [sum(precision_of(c) for c in cells) for cells in variants]
    return scores.index(max(scores))
