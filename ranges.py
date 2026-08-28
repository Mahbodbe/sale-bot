"""Parse the date range a user asks for.

Accepted, in either language:

    /sum 27 aug - 29 aug            → Gregorian, current year
    /sum 27 august 2026 to 29 august 2026
    /sum 2026-08-27 .. 2026-08-29
    /sum ۵ شهریور تا ۷ شهریور        → Jalali
    /sum 1405/06/05 - 1405/06/07
    /sum today | /sum امروز
    /sum week  | /sum هفته
    /sum month | /sum ماه
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import jdatetime

from parser import JALALI_MONTHS, normalize

GREGORIAN_MONTHS = {
    "jan": 1, "january": 1, "ژانویه": 1,
    "feb": 2, "february": 2, "فوریه": 2,
    "mar": 3, "march": 3, "مارس": 3,
    "apr": 4, "april": 4, "اپریل": 4, "آپریل": 4,
    "may": 5, "می": 5, "مه": 5,
    "jun": 6, "june": 6, "جون": 6, "ژوئن": 6,
    "jul": 7, "july": 7, "جولای": 7, "ژوئیه": 7,
    "aug": 8, "august": 8, "اگوست": 8, "آگوست": 8, "اوت": 8,
    "sep": 9, "sept": 9, "september": 9, "سپتامبر": 9,
    "oct": 10, "october": 10, "اکتبر": 10,
    "nov": 11, "november": 11, "نوامبر": 11,
    "dec": 12, "december": 12, "دسامبر": 12,
}

# Two ISO dates are matched up front so the hyphens inside them are never
# mistaken for the range separator.
_TWO_ISO = re.compile(
    r"(\d{4}-\d{1,2}-\d{1,2})"
    r"\s*(?:-{1,2}|–|—|\.{2,}|to\b|تا|الی)\s*"
    r"(\d{4}-\d{1,2}-\d{1,2})",
    re.I,
)
_SEPARATOR = re.compile(r"\s*(?:-{1,2}|–|—|\.{2,}|to\b|تا|الی)\s*", re.I)

_G_MONTH_ALT = "|".join(sorted(GREGORIAN_MONTHS, key=len, reverse=True))
_J_MONTH_ALT = "|".join(sorted(JALALI_MONTHS, key=len, reverse=True))

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_JALALI_NUM = re.compile(r"^(\d{4})[/](\d{1,2})[/](\d{1,2})$")
_G_WORD = re.compile(rf"^(\d{{1,2}})\s*({_G_MONTH_ALT})\s*(\d{{4}})?$", re.I)
_G_WORD_REV = re.compile(rf"^({_G_MONTH_ALT})\s*(\d{{1,2}})\s*,?\s*(\d{{4}})?$", re.I)
_J_WORD = re.compile(rf"^(\d{{1,2}})\s*({_J_MONTH_ALT})\s*(\d{{2,4}})?$")


class RangeError(ValueError):
    """Raised when the requested range cannot be understood."""


def _one(token: str, *, today: date, jtoday: jdatetime.date) -> date:
    tok = normalize(token).strip().lower()
    if not tok:
        raise RangeError("empty date")

    if m := _ISO.match(tok):
        return date(int(m[1]), int(m[2]), int(m[3]))

    if m := _JALALI_NUM.match(tok):
        return jdatetime.date(int(m[1]), int(m[2]), int(m[3])).togregorian()

    if m := _J_WORD.match(tok):
        year = int(m[3]) if m[3] else jtoday.year
        if year < 100:
            year += 1300
        return jdatetime.date(year, JALALI_MONTHS[m[2]], int(m[1])).togregorian()

    if m := _G_WORD.match(tok):
        year = int(m[3]) if m[3] else today.year
        return date(year, GREGORIAN_MONTHS[m[2].lower()], int(m[1]))

    if m := _G_WORD_REV.match(tok):
        year = int(m[3]) if m[3] else today.year
        return date(year, GREGORIAN_MONTHS[m[1].lower()], int(m[2]))

    raise RangeError(f"unrecognised date: {token!r}")


def parse_range(
    text: str,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    """Turn a free-form range into an inclusive ``(start, end)`` pair."""
    today = today or date.today()
    jtoday = jdatetime.date.fromgregorian(date=today)
    tok = normalize(text).strip().lower()

    if tok in {"today", "امروز"}:
        return today, today
    if tok in {"yesterday", "دیروز"}:
        y = today - timedelta(days=1)
        return y, y
    if tok in {"week", "هفته", "this week", "این هفته"}:
        return today - timedelta(days=6), today
    if tok in {"month", "ماه", "this month", "این ماه"}:
        start = jdatetime.date(jtoday.year, jtoday.month, 1).togregorian()
        return start, today

    if m := _TWO_ISO.search(tok):
        start = _one(m[1], today=today, jtoday=jtoday)
        end = _one(m[2], today=today, jtoday=jtoday)
        return (start, end) if start <= end else (end, start)

    parts = [p for p in _SEPARATOR.split(tok) if p.strip()]
    if len(parts) == 1:
        one = _one(parts[0], today=today, jtoday=jtoday)
        return one, one
    if len(parts) != 2:
        raise RangeError("give exactly two dates, e.g. 27 aug - 29 aug")

    start = _one(parts[0], today=today, jtoday=jtoday)
    end = _one(parts[1], today=today, jtoday=jtoday)

    # A bare month name on the left ("27 aug - 29 aug") keeps the same year;
    # if the user typed them backwards, swap instead of returning nothing.
    if end < start:
        start, end = end, start
    return start, end
