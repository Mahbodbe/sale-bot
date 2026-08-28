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

# "دو تا مگنت" / "۳ تا استیکر" / "2 عدد" — how many pieces a line covers.
_WORD_QUANTITIES = {
    "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "چار": 4, "پنج": 5,
    "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
}
_QTY_WORD_ALT = "|".join(sorted(_WORD_QUANTITIES, key=len, reverse=True))

# A "جفت" (pair) is two pieces, so the counter itself carries a multiplier.
_COUNTERS = {"تا": 1, "عدد": 1, "دونه": 1, "دانه": 1, "جفت": 2, "ست": 1}
_COUNTER_ALT = "|".join(sorted(_COUNTERS, key=len, reverse=True))

_QTY_RE = re.compile(
    rf"(?:^|\s)({_QTY_WORD_ALT}|\d{{1,2}})\s*({_COUNTER_ALT})(?:\s|$|\u200c)"
)

# "دونه‌ای ۲۰۰t" / "هرکدوم ۲۰۰t" / "هر دونه ۲۰۰t" — the price is per piece,
# so it must be multiplied by the quantity even when only one price is given.
_EACH_RE = re.compile(
    r"(?:دونه\s*ای|دانه\s*ای|عددی|هر\s*کدوم|هرکدوم|هر\s*دونه|هر\s*عدد|هر\s*یک)"
)


def _to_int(raw: str) -> int:
    """Turn one captured amount into an integer of thousands."""
    # A single dot with 1-2 trailing digits is a decimal (1.5t = 1500).
    if re.fullmatch(r"\d+\.\d{1,2}", raw):
        return int(round(float(raw) * 1000))
    cleaned = raw.replace(",", "").replace("_", "").replace(".", "")
    return int(cleaned) if cleaned else 0


def parse_amounts(text: str) -> list[int]:
    """Every amount written before a ``t``, in the order they appear."""
    norm = normalize(text)
    return [n for n in (_to_int(m.group(1)) for m in _AMOUNT_RE.finditer(norm)) if n]


def _line_quantity(line: str) -> int | None:
    """Pieces this line declares, or ``None`` when it declares none."""
    m = _QTY_RE.search(line)
    if not m:
        return None

    token, counter = m.group(1), m.group(2)
    base = int(token) if token.isdigit() else _WORD_QUANTITIES.get(token, 1)
    qty = base * _COUNTERS.get(counter, 1)
    # Guard against a stray big number in prose blowing up a total.
    return qty if 1 <= qty <= 40 else None


def parse_quantity(text: str) -> int:
    """Total pieces the post covers — 1 when nothing is stated."""
    return sum(item.quantity for item in parse_items(text)) or 1


# ── line items ─────────────────────────────────────────────────────────
@dataclass
class LineItem:
    """One priced line of a post: how many pieces, at what price(s)."""

    quantity: int
    prices: list[int]
    each: bool          # price was written as "per piece"
    label: str          # the descriptive line this item came from

    @property
    def total(self) -> int:
        if len(self.prices) > 1:
            # Each piece got its own price; the list is already the whole truth.
            return sum(self.prices)
        return self.prices[0] * self.quantity


def parse_items(text: str, today: "jdatetime.date | None" = None) -> list[LineItem]:
    """Split a post into priced line items.

    A post may describe several different products, each with its own count and
    price, and the price may be written per piece::

        یک جفت گیره پهن        → 2 pieces
        دونه‌ای ۲۰۰t           → 200 each  → 400
        یک عدد گیره باریک کوتاه → 1 piece
        ۱۵۰t                    → 150      → 150
                                              ────
                                              550

    Consecutive price lines belong to the same product, so a count in the title
    with several prices under it means one price per piece::

        دو تا مگنت
        ۲۰۰t
        ۳۰۰t                    → 500, not 1000
    """
    items: list[LineItem] = []
    pending_qty = 1
    pending_label = ""
    open_item: LineItem | None = None       # the item still collecting prices

    for raw_line in normalize(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue

        prices = [p for p in (_to_int(m.group(1)) for m in _AMOUNT_RE.finditer(line)) if p]
        qty = _line_quantity(line)

        if not prices:
            # A descriptive line closes any item that was collecting prices and
            # remembers its own count for the price line(s) that follow.
            open_item = None
            if parse_jalali_date(line, today=today) is not None:
                continue
            if qty is not None:
                pending_qty, pending_label = qty, line
            else:
                pending_label = pending_label or line
            continue

        # A priced line without its own count continues the previous product.
        if qty is None and open_item is not None:
            open_item.prices.extend(prices)
            continue

        line_qty = qty if qty is not None else pending_qty
        open_item = LineItem(
            quantity=line_qty,
            prices=list(prices),
            each=bool(_EACH_RE.search(line)),
            label=pending_label or line,
        )
        items.append(open_item)
        pending_qty, pending_label = 1, ""

    return items


def parse_amount(text: str) -> int | None:
    """Total value of a post, or ``None`` when it carries no amount.

    Shapes seen in the channel:

    * one piece, one price — ``۷۰۰t`` → 700
    * several pieces, one price each listed — ``۲۰۰t`` ``۳۰۰t`` → 500
    * several pieces at the same price — ``دو تا مگنت`` + ``۲۰۰t`` → 400
    * several *products*, each with its own count and price::

          یک جفت گیره پهن / دونه‌ای ۲۰۰t / یک عدد گیره باریک / ۱۵۰t → 550
    """
    items = parse_items(text)
    if not items:
        return None
    return sum(item.total for item in items)


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
    items: list[LineItem] # one entry per priced product line


def parse_sale(text: str, today: jdatetime.date | None = None) -> Sale | None:
    """Parse a channel post into a :class:`Sale`, or ``None`` if no amount."""
    line_items = parse_items(text, today=today)
    if not line_items:
        return None

    amount = sum(it.total for it in line_items)
    prices = [p for it in line_items for p in it.prices]
    quantity = sum(it.quantity for it in line_items)

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

    # With several products, name them all so the confirmation is readable.
    if len(line_items) > 1:
        item = " + ".join(it.label for it in line_items)
        event = leftovers[-1] if len(leftovers) > len(line_items) else ""
    else:
        item = leftovers[0] if leftovers else ""
        event = leftovers[1] if len(leftovers) > 1 else ""

    return Sale(
        amount=amount,
        sale_date=sale_date,
        item=item,
        event=event,
        raw=text.strip(),
        prices=prices,
        quantity=quantity,
        items=line_items,
    )
