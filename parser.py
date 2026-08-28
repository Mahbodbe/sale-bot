"""Parsing helpers for the sales-accounting bot.

A channel post looks like this (Persian, free-form, order may vary):

    سنجاق سینه جا عینکی
    ۷۰۰t
    ایونت آلما
    ۶ شهریور۱۴۰۵

We need two things out of it:
  * the amount — the number written right before the letter ``t``
  * the sale date — a Jalali (Persian) date such as ``۶ شهریور۱۴۰۵``

Both Persian (۰-۹) and Arabic-Indic (٠-٩) digits are accepted, and the
separator between day / month / year may be a space, no space at all, ``/``
or ``-``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import jdatetime

# ── digit normalisation ────────────────────────────────────────────────
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(PERSIAN_DIGITS)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(ARABIC_DIGITS)})

# Arabic vs Persian letter variants that users mix constantly.
_LETTER_MAP = {ord("ي"): "ی", ord("ك"): "ک", ord("\u200c"): " "}


def normalize(text: str) -> str:
    """Fold Persian/Arabic digits to ASCII and unify letter variants."""
    return text.translate(_DIGIT_MAP).translate(_LETTER_MAP)


# ── amount ─────────────────────────────────────────────────────────────
# 700t · 700 t · 1,200t · 1.5t · ۷۰۰تی — a number immediately before "t".
#
# The trailing lookahead is what keeps "۲ تا مگنت" (two magnets) from being
# read as "2t": after the "ت" comes "ا", a Persian letter, so the match fails.
_AMOUNT_RE = re.compile(
    r"(?<![\w.])"          # not glued to a word/decimal on the left
    r"(\d[\d,._]*)"        # the number itself, thousands separators allowed
    r"\s*"                 # optional space
    r"(?:t|T|تی|ت)"        # the unit marker
    r"(?![\w\u0600-\u06FF])",  # not followed by more letters
)

# "دو تا مگنت" / "۳ تا استیکر" / "2 عدد" — how many pieces the post covers.
_WORD_QUANTITIES = {
    "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "چار": 4, "پنج": 5,
    "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
}
_QTY_WORD_ALT = "|".join(sorted(_WORD_QUANTITIES, key=len, reverse=True))
_COUNTER = r"(?:تا|عدد|دونه|دانه|جفت)"

_QTY_RE = re.compile(rf"(?:^|\s)({_QTY_WORD_ALT}|\d{{1,2}})\s*{_COUNTER}(?:\s|$)")


def _to_int(raw: str) -> int:
    """Turn one captured amount into an integer of thousands."""
    # A single dot with 1-2 trailing digits is a decimal (1.5t = 1500).
    if re.fullmatch(r"\d+\.\d{1,2}", raw):
        return int(round(float(raw) * 1000))
    cleaned = raw.replace(",", "").replace("_", "").replace(".", "")
    return int(cleaned) if cleaned else 0


def parse_amounts(text: str) -> list[int]:
    """Every amount written before a ``t``, in the order they appear.

    A post with two differently-priced pieces lists both prices:

        دو تا مگنت یخچال (دوربین، شیشه آبنبات)
        ۲۰۰t
        ۳۰۰t
    """
    norm = normalize(text)
    return [n for n in (_to_int(m.group(1)) for m in _AMOUNT_RE.finditer(norm)) if n]


def parse_quantity(text: str) -> int:
    """How many pieces the post mentions — 1 when it does not say."""
    m = _QTY_RE.search(normalize(text))
    if not m:
        return 1
    token = m.group(1)
    if token.isdigit():
        qty = int(token)
    else:
        qty = _WORD_QUANTITIES.get(token, 1)
    # Guard against nonsense like "۵۰ تا" in a description.
    return qty if 1 <= qty <= 20 else 1


def parse_amount(text: str) -> int | None:
    """Total value of a post, or ``None`` when it carries no amount.

    Three shapes show up in the channel:

    * one piece, one price — ``۷۰۰t`` → 700
    * several pieces at *different* prices, each listed — ``۲۰۰t`` ``۳۰۰t`` → 500
    * several pieces at the *same* price, written once, with the count in the
      title — ``دو تا مگنت`` + ``۲۰۰t`` → 400
    """
    amounts = parse_amounts(text)
    if not amounts:
        return None

    if len(amounts) > 1:
        # Prices are spelled out per piece; trust the list.
        return sum(amounts)

    # Single price: multiply by the stated piece count.
    return amounts[0] * parse_quantity(text)


# ── Jalali date ────────────────────────────────────────────────────────
JALALI_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3,
    "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "اذر": 9, "آذر": 9,
    "دی": 10, "بهمن": 11, "اسفند": 12,
}
_MONTH_ALT = "|".join(sorted(JALALI_MONTHS, key=len, reverse=True))

# "۶ شهریور۱۴۰۵" / "6 شهریور 1405" / "6شهریور1405"
_DATE_WORD_RE = re.compile(rf"(\d{{1,2}})\s*({_MONTH_ALT})\s*(\d{{2,4}})?")
# "1405/06/06" or "1405-6-6"
_DATE_NUM_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")


def _full_year(year: int | None, fallback: int) -> int:
    if year is None:
        return fallback
    return year + 1300 if year < 100 else year


def parse_jalali_date(text: str, today: jdatetime.date | None = None) -> date | None:
    """Extract a Jalali date from ``text`` and return it as a Gregorian date."""
    today = today or jdatetime.date.today()
    norm = normalize(text)

    m = _DATE_NUM_RE.search(norm)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return jdatetime.date(y, mo, d).togregorian()
        except ValueError:
            return None

    m = _DATE_WORD_RE.search(norm)
    if m:
        day = int(m.group(1))
        month = JALALI_MONTHS[m.group(2)]
        year = _full_year(int(m.group(3)) if m.group(3) else None, today.year)
        try:
            return jdatetime.date(year, month, day).togregorian()
        except ValueError:
            return None

    return None


# ── whole message ──────────────────────────────────────────────────────
@dataclass
class Sale:
    """One parsed sale line."""

    amount: int
    sale_date: date | None
    item: str
    event: str
    raw: str
    prices: list[int]     # the individual prices found in the post
    quantity: int         # pieces the post covers


def parse_sale(text: str, today: jdatetime.date | None = None) -> Sale | None:
    """Parse a channel post into a :class:`Sale`, or ``None`` if no amount."""
    prices = parse_amounts(text)
    if not prices:
        return None

    quantity = len(prices) if len(prices) > 1 else parse_quantity(text)
    amount = sum(prices) if len(prices) > 1 else prices[0] * quantity

    sale_date = parse_jalali_date(text, today=today)

    # Classify the remaining lines: the amount line and the date line are
    # known, so the first leftover line is the item and the next is the event.
    leftovers: list[str] = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if _AMOUNT_RE.search(normalize(line)):
            continue
        if parse_jalali_date(line, today=today) is not None:
            continue
        leftovers.append(line)

    return Sale(
        amount=amount,
        sale_date=sale_date,
        item=leftovers[0] if leftovers else "",
        event=leftovers[1] if len(leftovers) > 1 else "",
        raw=text.strip(),
        prices=prices,
        quantity=quantity,
    )
