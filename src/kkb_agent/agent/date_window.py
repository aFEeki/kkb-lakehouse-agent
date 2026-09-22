"""Bounded Turkish calendar windows; no relative-date inference."""

import calendar
import re
from datetime import date

MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "ocak",
            "şubat",
            "mart",
            "nisan",
            "mayıs",
            "haziran",
            "temmuz",
            "ağustos",
            "eylül",
            "ekim",
            "kasım",
            "aralık",
        ),
        1,
    )
}


class DateWindowError(ValueError):
    pass


def parse_window(question: str, default: tuple[date, date]) -> tuple[date, date]:
    text = question.lower().replace("ı", "i")
    names = {name.replace("ı", "i"): month for name, month in MONTHS.items()}
    month_matches = re.findall(r"(" + "|".join(names) + r")\s+(\d{4})\b", text)
    years = re.findall(r"\b((?:19|20)\d{2})\b", text)
    if not years:
        return default
    if len(years) > 2 or (month_matches and len(month_matches) != len(years)):
        raise DateWindowError("Tarih aralığı belirsiz; başlangıç ve bitiş dönemini belirtin.")
    if month_matches:
        first, last = month_matches[0], month_matches[-1]
        start = date(int(first[1]), names[first[0]], 1)
        end = date(
            int(last[1]), names[last[0]], calendar.monthrange(int(last[1]), names[last[0]])[1]
        )
    else:
        start, end = date(int(years[0]), 1, 1), date(int(years[-1]), 12, 31)
    if start > end:
        raise DateWindowError("Başlangıç dönemi bitiş döneminden sonra olamaz.")
    return start, end
