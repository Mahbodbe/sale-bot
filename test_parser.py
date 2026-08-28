"""Tests for the parsing layer — run with: venv/bin/python -m pytest -q"""

from __future__ import annotations

from datetime import date

import jdatetime
import pytest

import store
from parser import (
    parse_amount,
    parse_amounts,
    parse_jalali_date,
    parse_quantity,
    parse_sale,
)
from ranges import RangeError, parse_range

TODAY_J = jdatetime.date(1405, 6, 6)      # 6 Shahrivar 1405
TODAY_G = TODAY_J.togregorian()           # 2026-08-28


# ── amounts ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("۷۰۰t", 700),
        ("700t", 700),
        ("700 t", 700),
        ("۱,۲۰۰t", 1200),
        ("1.5t", 1500),
        ("۸۵۰تی", 850),
        ("۳۰۰ ت", 300),
        ("سنجاق سینه\n۷۰۰t\nایونت آلما", 700),
        ("بدون عدد", None),
        ("t", None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


# ── multi-piece posts ──────────────────────────────────────────────────
def test_two_pieces_two_prices_are_summed():
    text = "دو تا مگنت یخچال (دوربین، شیشه آبنبات)\n۲۰۰t\n۳۰۰t\nایونت آلما\n۶ شهریور۱۴۰۵"
    assert parse_amounts(text) == [200, 300]
    assert parse_amount(text) == 500


def test_two_pieces_one_price_multiplies_by_count():
    text = "دو تا مگنت یخچال (دوربین، شیشه آبنبات)\n۲۰۰t\nایونت آلما\n۶ شهریور۱۴۰۵"
    assert parse_amounts(text) == [200]
    assert parse_quantity(text) == 2
    assert parse_amount(text) == 400


@pytest.mark.parametrize(
    "title,price,qty,total",
    [
        ("سه تا استیکر", "۱۵۰t", 3, 450),
        ("۴ تا کیچین", "۱۰۰t", 4, 400),
        ("پنج تا مگنت", "۲۰۰t", 5, 1000),
        ("یه دستبند", "۳۵۰t", 1, 350),
        ("سنجاق سینه", "۷۰۰t", 1, 700),
        ("دو عدد پیکسل", "۱۲۰t", 2, 240),
        ("سه دونه گیره", "۸۰t", 3, 240),
    ],
)
def test_quantity_forms(title, price, qty, total):
    text = f"{title}\n{price}\nآلما\n۶ شهریور۱۴۰۵"
    assert parse_quantity(text) == qty
    assert parse_amount(text) == total


def test_four_different_prices_are_summed():
    text = "چهار تا مگنت\n۲۰۰t\n۲۵۰t\n۳۰۰t\n۱۵۰t\nآلما\n۶ شهریور۱۴۰۵"
    assert parse_amounts(text) == [200, 250, 300, 150]
    assert parse_amount(text) == 900


def test_explicit_prices_win_over_stated_count():
    """When both prices are listed, the count in the title must not multiply."""
    text = "دو تا مگنت\n۲۰۰t\n۳۰۰t\nآلما\n۶ شهریور۱۴۰۵"
    assert parse_amount(text) == 500          # not 1000


def test_quantity_word_inside_description_does_not_leak():
    """'تا' as part of ordinary prose must not become a multiplier."""
    text = "سنجاق سینه\n۷۰۰t\nتا آخر ایونت تخفیف داشت\n۶ شهریور۱۴۰۵"
    assert parse_amount(text) == 700


def test_absurd_quantity_is_ignored():
    text = "۵۰ تا مگنت\n۲۰۰t\nآلما\n۶ شهریور۱۴۰۵"
    assert parse_quantity(text) == 1          # guard against runaway totals
    assert parse_amount(text) == 200


# ── dates ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("۶ شهریور۱۴۰۵", jdatetime.date(1405, 6, 6).togregorian()),
        ("6 شهریور 1405", jdatetime.date(1405, 6, 6).togregorian()),
        ("۵ شهریور", jdatetime.date(1405, 6, 5).togregorian()),
        ("1405/06/07", jdatetime.date(1405, 6, 7).togregorian()),
        ("۱۲ آبان ۱۴۰۴", jdatetime.date(1404, 8, 12).togregorian()),
        ("no date here", None),
    ],
)
def test_parse_jalali_date(text, expected):
    assert parse_jalali_date(text, today=TODAY_J) == expected


# ── whole message ──────────────────────────────────────────────────────
def test_parse_sale_full_message():
    text = "سنجاق سینه جا عینکی \n۷۰۰t\nایونت آلما \n ۶ شهریور۱۴۰۵"
    sale = parse_sale(text, today=TODAY_J)
    assert sale is not None
    assert sale.amount == 700
    assert sale.sale_date == jdatetime.date(1405, 6, 6).togregorian()
    assert sale.item == "سنجاق سینه جا عینکی"
    assert sale.event == "ایونت آلما"


def test_parse_sale_no_amount_returns_none():
    assert parse_sale("فقط یه توضیح بدون مبلغ", today=TODAY_J) is None


# ── ranges ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,start,end",
    [
        ("27 aug - 29 aug", date(2026, 8, 27), date(2026, 8, 29)),
        ("27 august 2026 to 29 august 2026", date(2026, 8, 27), date(2026, 8, 29)),
        ("2026-08-27 .. 2026-08-29", date(2026, 8, 27), date(2026, 8, 29)),
        ("۵ شهریور تا ۷ شهریور",
         jdatetime.date(1405, 6, 5).togregorian(),
         jdatetime.date(1405, 6, 7).togregorian()),
        ("1405/06/05 - 1405/06/07",
         jdatetime.date(1405, 6, 5).togregorian(),
         jdatetime.date(1405, 6, 7).togregorian()),
        ("29 aug - 27 aug", date(2026, 8, 27), date(2026, 8, 29)),  # swapped
        ("today", TODAY_G, TODAY_G),
        ("امروز", TODAY_G, TODAY_G),
    ],
)
def test_parse_range(text, start, end):
    assert parse_range(text, today=TODAY_G) == (start, end)


def test_parse_range_rejects_garbage():
    with pytest.raises(RangeError):
        parse_range("سلام خوبی", today=TODAY_G)


# ── storage + summation ────────────────────────────────────────────────
def test_sum_range_adds_only_inside_window(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    rows = [
        (1, 700, date(2026, 8, 27)),
        (2, 300, date(2026, 8, 28)),
        (3, 250, date(2026, 8, 29)),
        (4, 999, date(2026, 8, 30)),   # outside
    ]
    for mid, amount, when in rows:
        store.upsert_sale(
            conn, chat_id=-100, message_id=mid, amount=amount, sale_date=when,
            item="x", event="e", raw=f"{amount}t", posted_at="2026-08-27T10:00:00+00:00",
        )

    total, count, got = store.sum_range(conn, date(2026, 8, 27), date(2026, 8, 29))
    assert (total, count) == (1250, 3)
    assert [r["amount"] for r in got] == [700, 300, 250]


def test_edit_updates_instead_of_duplicating(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    for amount in (700, 800):           # same message edited
        store.upsert_sale(
            conn, chat_id=-100, message_id=42, amount=amount,
            sale_date=date(2026, 8, 27), item="x", event="e",
            raw=f"{amount}t", posted_at="2026-08-27T10:00:00+00:00",
        )
    total, count, _ = store.sum_range(conn, date(2026, 8, 27), date(2026, 8, 27))
    assert (total, count) == (800, 1)


def test_unreadable_date_falls_back_to_posting_date(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.upsert_sale(
        conn, chat_id=-100, message_id=7, amount=500, sale_date=None,
        item="x", event="e", raw="500t", posted_at="2026-08-28T09:00:00+00:00",
    )
    total, count, _ = store.sum_range(conn, date(2026, 8, 28), date(2026, 8, 28))
    assert (total, count) == (500, 1)


# ── grand total & per-day breakdown ────────────────────────────────────
def _seed(conn):
    rows = [
        (1, 700, date(2026, 8, 27)),
        (2, 300, date(2026, 8, 28)),
        (3, 250, date(2026, 8, 28)),
        (4, 999, date(2026, 9, 5)),    # far outside any short window
    ]
    for mid, amount, when in rows:
        store.upsert_sale(
            conn, chat_id=-100, message_id=mid, amount=amount, sale_date=when,
            item=f"item{mid}", event="e", raw=f"{amount}t",
            posted_at="2026-08-27T10:00:00+00:00",
        )


def test_grand_total_ignores_any_window(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed(conn)
    total, count, first_day, last_day = store.grand_total(conn)
    assert (total, count) == (2249, 4)
    assert first_day == "2026-08-27"
    assert last_day == "2026-09-05"


def test_grand_total_empty_book(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    total, count, first_day, last_day = store.grand_total(conn)
    assert (total, count, first_day, last_day) == (0, 0, None, None)


def test_range_plus_outside_equals_grand_total(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed(conn)
    in_range, _, _ = store.sum_range(conn, date(2026, 8, 27), date(2026, 8, 28))
    book, _, _, _ = store.grand_total(conn)
    assert in_range == 1250
    assert book - in_range == 999          # the sale outside the window


def test_totals_by_day_groups_and_orders(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed(conn)
    rows = store.totals_by_day(conn, date(2026, 8, 27), date(2026, 8, 28))
    assert [(r["day"], r["total"], r["count"]) for r in rows] == [
        ("2026-08-27", 700, 1),
        ("2026-08-28", 550, 2),
    ]


# ── deletion ───────────────────────────────────────────────────────────
def _seed_ids(conn):
    """Four sales; returns their row ids in insertion order."""
    ids = []
    rows = [
        (1, 700, date(2026, 8, 27)),
        (2, 300, date(2026, 8, 28)),
        (3, 250, date(2026, 8, 29)),
        (4, 999, date(2026, 9, 5)),
    ]
    for mid, amount, when in rows:
        store.upsert_sale(
            conn, chat_id=-100, message_id=mid, amount=amount, sale_date=when,
            item=f"item{mid}", event="e", raw=f"{amount}t",
            posted_at="2026-08-27T10:00:00+00:00",
        )
        ids.append(conn.execute("SELECT MAX(id) AS i FROM sales").fetchone()["i"])
    return ids


def test_delete_by_id_removes_one_row(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    ids = _seed_ids(conn)

    removed = store.delete_by_id(conn, ids[0])
    assert removed["amount"] == 700

    total, count, _, _ = store.grand_total(conn)
    assert (total, count) == (1549, 3)


def test_delete_by_id_missing_returns_none(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert store.delete_by_id(conn, 999) is None


def test_delete_ids_removes_a_batch(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    ids = _seed_ids(conn)

    count, amount = store.delete_ids(conn, [ids[0], ids[2]])
    assert (count, amount) == (2, 950)

    total, remaining, _, _ = store.grand_total(conn)
    assert (total, remaining) == (1299, 2)


def test_delete_ids_empty_list_is_a_noop(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed_ids(conn)
    assert store.delete_ids(conn, []) == (0, 0)
    _, count, _, _ = store.grand_total(conn)
    assert count == 4


def test_delete_range_removes_only_the_window(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed_ids(conn)

    count, amount = store.delete_range(conn, date(2026, 8, 27), date(2026, 8, 28))
    assert (count, amount) == (2, 1000)

    total, remaining, _, _ = store.grand_total(conn)
    assert (total, remaining) == (1249, 2)      # 250 + 999 survive


def test_delete_all_wipes_the_book(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed_ids(conn)

    count, amount = store.delete_all(conn)
    assert (count, amount) == (4, 2249)

    total, remaining, first, last = store.grand_total(conn)
    assert (total, remaining, first, last) == (0, 0, None, None)


def test_delete_all_on_empty_book(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert store.delete_all(conn) == (0, 0)


def test_recent_returns_newest_first(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    _seed_ids(conn)
    rows = store.recent(conn, limit=2)
    assert [r["amount"] for r in rows] == [999, 250]


def test_reposting_after_delete_restores_the_row(tmp_path):
    """Deleting then seeing the same post again re-adds it exactly once."""
    conn = store.connect(tmp_path / "t.db")
    kwargs = dict(
        chat_id=-100, message_id=1, amount=700, sale_date=date(2026, 8, 27),
        item="x", event="e", raw="700t", posted_at="2026-08-27T10:00:00+00:00",
    )
    store.upsert_sale(conn, **kwargs)
    row_id = conn.execute("SELECT MAX(id) AS i FROM sales").fetchone()["i"]

    store.delete_by_id(conn, row_id)
    _, count, _, _ = store.grand_total(conn)
    assert count == 0

    store.upsert_sale(conn, **kwargs)
    total, count, _, _ = store.grand_total(conn)
    assert (total, count) == (700, 1)


# ── forwarded-post de-duplication ──────────────────────────────────────
def test_forward_of_same_post_does_not_double_count(tmp_path):
    """A forward carries the origin chat+message id, so it upserts one row."""
    conn = store.connect(tmp_path / "t.db")
    origin_chat, origin_mid = -1001, 55

    # Read once from the channel, then the same post is forwarded to the bot.
    for _ in range(2):
        store.upsert_sale(
            conn, chat_id=origin_chat, message_id=origin_mid, amount=700,
            sale_date=date(2026, 8, 27), item="سنجاق", event="آلما",
            raw="۷۰۰t", posted_at="2026-08-27T10:00:00+00:00",
        )

    total, count, _, _ = store.grand_total(conn)
    assert (total, count) == (700, 1)
