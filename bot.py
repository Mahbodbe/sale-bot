#!/usr/bin/env python3
"""Telegram sales-accounting bot.

Add the bot to a channel as an administrator. It reads every post, pulls out
the amount written before ``t`` and the Jalali sale date, and stores them.
Then ask it for a total over any date range.

Commands (work in the channel, or in a private chat with the bot):

    /sum 27 aug - 29 aug        total for that window
    /sum ۵ شهریور تا ۷ شهریور    same thing in Jalali
    /sum today | week | month
    /list 27 aug - 29 aug       the individual sales behind a total
    /pending                    posts whose date could not be read
    /backfill                   re-parse stored posts after a parser fix
    /help

Environment:
    BOT_TOKEN     required — from @BotFather
    ALLOWED_CHATS optional — comma-separated chat IDs allowed to query totals
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import jdatetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import store
from parser import normalize as normalize_digits
from parser import parse_sale
from ranges import RangeError, parse_range

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("sales-bot")


def _load_env(path: Path = Path(__file__).with_name(".env")) -> None:
    """Read KEY=VALUE lines from a local .env, without overriding real env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ALLOWED_CHATS = {
    int(c) for c in os.environ.get("ALLOWED_CHATS", "").replace(" ", "").split(",") if c
}

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n: int | str) -> str:
    """Format a number with thousands separators and Persian digits."""
    s = f"{n:,}" if isinstance(n, int) else str(n)
    return s.translate(FA_DIGITS)


def fa_plain(n: int | str) -> str:
    """Persian digits with no thousands separator — for years and day numbers."""
    return str(n).translate(FA_DIGITS)


def jalali_str(d: date) -> str:
    j = jdatetime.date.fromgregorian(date=d)
    return f"{fa_plain(j.day)} {j.j_months_fa[j.month - 1]} {fa_plain(j.year)}"


def _allowed(update: Update) -> bool:
    if not ALLOWED_CHATS:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id in ALLOWED_CHATS


def _origin_ids(msg) -> tuple[int, int]:
    """Identity used for de-duplication.

    A forwarded post keeps the identity of the message it came from, so
    forwarding the same sale twice — or forwarding something the bot already
    read in the channel — updates one row instead of double-counting.
    """
    origin = getattr(msg, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    origin_mid = getattr(origin, "message_id", None) if origin else None
    if origin_chat is not None and origin_mid is not None:
        return origin_chat.id, origin_mid

    # Older Bot API payloads.
    fwd_chat = getattr(msg, "forward_from_chat", None)
    fwd_mid = getattr(msg, "forward_from_message_id", None)
    if fwd_chat is not None and fwd_mid is not None:
        return fwd_chat.id, fwd_mid

    return msg.chat_id, msg.message_id


# ── ingest ─────────────────────────────────────────────────────────────
async def on_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store any post, forward, or DM that carries an amount."""
    msg = update.effective_message
    if msg is None:
        return

    text = msg.text or msg.caption or ""
    sale = parse_sale(text)
    if sale is None:
        # Only answer in private chats; stay silent in channels and groups.
        if msg.chat.type == "private":
            await msg.reply_text(
                "مبلغی پیدا نکردم. عدد قبل از `t` لازمه — مثل `۷۰۰t`.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    chat_id, message_id = _origin_ids(msg)
    posted = (
        getattr(getattr(msg, "forward_origin", None), "date", None)
        or msg.date
        or datetime.now(timezone.utc)
    ).astimezone(timezone.utc)

    conn = context.bot_data["conn"]
    store.upsert_sale(
        conn,
        chat_id=chat_id,
        message_id=message_id,
        amount=sale.amount,
        sale_date=sale.sale_date,
        item=sale.item,
        event=sale.event,
        raw=sale.raw,
        posted_at=posted.isoformat(),
    )
    log.info(
        "stored %s t — %s (%s)",
        sale.amount,
        sale.item or "?",
        sale.sale_date or "date unread",
    )

    # Confirm in private chats so forwarding gives visible feedback.
    if msg.chat.type == "private":
        total, count, _, _ = store.grand_total(conn)
        when = jalali_str(sale.sale_date) if sale.sale_date else "تاریخ خوانده نشد"

        if len(sale.items) > 1:
            # Several products: show the arithmetic line by line.
            detail = ["مبلغ: *" + fa(sale.amount) + " t*", ""]
            for it in sale.items:
                if len(it.prices) > 1:
                    calc = " + ".join(fa(p) for p in it.prices)
                elif it.quantity > 1:
                    calc = f"{fa_plain(it.quantity)} × {fa(it.prices[0])} = {fa(it.total)}"
                else:
                    calc = fa(it.total)
                detail.append(f"• {it.label} — {calc}")
            amount_line = "\n".join(detail)
        elif len(sale.prices) > 1:
            breakdown = " + ".join(fa(p) for p in sale.prices)
            amount_line = f"مبلغ: *{fa(sale.amount)} t*  ({breakdown})"
        elif sale.quantity > 1:
            amount_line = (
                f"مبلغ: *{fa(sale.amount)} t*"
                f"  ({fa_plain(sale.quantity)} × {fa(sale.prices[0])})"
            )
        else:
            amount_line = f"مبلغ: *{fa(sale.amount)} t*"

        body = f"ثبت شد ✅\n{amount_line}\n"
        if len(sale.items) == 1:
            body += f"کالا: {sale.item or '؟'}\n"
        body += f"تاریخ: {when}\n\nجمع کل دفتر: *{fa(total)} t* در {fa(count)} فروش"

        await msg.reply_text(body, parse_mode=ParseMode.MARKDOWN)


# ── queries ────────────────────────────────────────────────────────────
async def cmd_sum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return

    arg = " ".join(context.args) if context.args else "today"
    try:
        start, end = parse_range(arg)
    except RangeError as exc:
        await update.effective_message.reply_text(
            f"بازه رو نفهمیدم ({exc}).\nمثال: `/sum 27 aug - 29 aug`"
            " یا `/sum ۵ شهریور تا ۷ شهریور`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    conn = context.bot_data["conn"]
    total, count, _ = store.sum_range(conn, start, end)
    book_total, book_count, first_day, last_day = store.grand_total(conn)
    outside = book_total - total
    share = (total / book_total * 100) if book_total else 0.0

    lines = [
        "*جمع فروش*",
        f"از {jalali_str(start)} تا {jalali_str(end)}",
        f"({start.isoformat()} → {end.isoformat()})",
        "",
        f"مبلغ بازه: *{fa(total)} t*",
        f"تعداد فروش: {fa(count)}",
        "",
        "───────────────",
        f"جمع کل دفتر: *{fa(book_total)} t* در {fa(book_count)} فروش",
        f"خارج از بازه: {fa(outside)} t",
        f"سهم این بازه: {fa(f'{share:.1f}')}٪",
    ]
    if first_day and last_day:
        lines.append(
            f"بازه‌ی کل دفتر: {jalali_str(date.fromisoformat(first_day))}"
            f" تا {jalali_str(date.fromisoformat(last_day))}"
        )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return

    arg = " ".join(context.args) if context.args else "today"
    try:
        start, end = parse_range(arg)
    except RangeError as exc:
        await update.effective_message.reply_text(f"بازه رو نفهمیدم ({exc}).")
        return

    conn = context.bot_data["conn"]
    total, count, rows = store.sum_range(conn, start, end)
    if not rows:
        await update.effective_message.reply_text("در این بازه فروشی ثبت نشده.")
        return

    lines = [f"*ریز فروش* — {jalali_str(start)} تا {jalali_str(end)}", ""]
    for r in rows[:60]:
        when = r["sale_date"] or r["posted_at"][:10]
        day = jalali_str(date.fromisoformat(when))
        item = (r["item"] or "؟").strip()
        lines.append(f"• {fa(r['amount'])} t — {item} — {day}")
    if count > 60:
        lines.append(f"… و {fa(count - 60)} مورد دیگر")
    lines += ["", f"جمع: *{fa(total)} t* در {fa(count)} فروش"]

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The whole book: every sale ever recorded, regardless of date."""
    if not _allowed(update):
        return

    conn = context.bot_data["conn"]
    total, count, first_day, last_day = store.grand_total(conn)
    if not count:
        await update.effective_message.reply_text("هنوز هیچ فروشی ثبت نشده.")
        return

    lines = [
        "*جمع کل دفتر*",
        "",
        f"مبلغ کل: *{fa(total)} t*",
        f"تعداد فروش: {fa(count)}",
        f"میانگین هر فروش: {fa(round(total / count))} t",
    ]
    if first_day and last_day:
        lines += [
            "",
            f"از {jalali_str(date.fromisoformat(first_day))}"
            f" تا {jalali_str(date.fromisoformat(last_day))}",
        ]

    unread = len(store.unparsed_dates(conn))
    if unread:
        lines += ["", f"⚠️ {fa(unread)} پست تاریخ خوانده‌نشده دارد (`/pending`)"]

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Range total, per-day breakdown, and the running book balance."""
    if not _allowed(update):
        return

    arg = " ".join(context.args) if context.args else "week"
    try:
        start, end = parse_range(arg)
    except RangeError as exc:
        await update.effective_message.reply_text(f"بازه رو نفهمیدم ({exc}).")
        return

    conn = context.bot_data["conn"]
    total, count, _ = store.sum_range(conn, start, end)
    per_day = store.totals_by_day(conn, start, end)
    book_total, book_count, _, _ = store.grand_total(conn)

    lines = [
        f"*گزارش* — {jalali_str(start)} تا {jalali_str(end)}",
        "",
    ]
    if per_day:
        for r in per_day:
            day = jalali_str(date.fromisoformat(r["day"]))
            lines.append(f"• {day} — {fa(r['total'])} t ({fa(r['count'])} فروش)")
    else:
        lines.append("در این بازه فروشی ثبت نشده.")

    lines += [
        "",
        f"جمع بازه: *{fa(total)} t* در {fa(count)} فروش",
        "───────────────",
        f"جمع کل دفتر: *{fa(book_total)} t* در {fa(book_count)} فروش",
        f"خارج از بازه: {fa(book_total - total)} t",
    ]

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return

    conn = context.bot_data["conn"]
    rows = store.unparsed_dates(conn)
    if not rows:
        await update.effective_message.reply_text("همه‌ی پست‌ها تاریخ خوانده‌شده دارند ✅")
        return

    lines = ["*پست‌هایی که تاریخشون خوانده نشد*", "(تاریخ ارسال به‌جاش حساب می‌شه)", ""]
    for r in rows:
        lines.append(f"• {fa(r['amount'])} t — {(r['item'] or '؟').strip()}")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_backfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-run the parser over stored raw text (useful after a parser fix)."""
    if not _allowed(update):
        return

    conn = context.bot_data["conn"]
    rows = conn.execute("SELECT * FROM sales").fetchall()
    fixed = 0
    for r in rows:
        sale = parse_sale(r["raw"])
        if sale is None:
            continue
        new_date = sale.sale_date.isoformat() if sale.sale_date else None
        if new_date != r["sale_date"] or sale.amount != r["amount"]:
            conn.execute(
                "UPDATE sales SET amount = ?, sale_date = ?, item = ?, event = ? WHERE id = ?",
                (sale.amount, new_date, sale.item, sale.event, r["id"]),
            )
            fixed += 1
    conn.commit()
    await update.effective_message.reply_text(
        f"بازخوانی شد: {fa(fixed)} رکورد اصلاح شد از {fa(len(rows))}."
    )


def _sale_label(row) -> str:
    """One-line description of a stored sale, for buttons and confirmations."""
    when = row["sale_date"] or row["posted_at"][:10]
    day = jalali_str(date.fromisoformat(when))
    item = (row["item"] or "؟").strip()
    if len(item) > 22:
        item = item[:21] + "…"
    return f"{fa(row['amount'])}t — {item} — {day}"


# ── delete: one record ─────────────────────────────────────────────────
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the recent sales as buttons; tapping one deletes it."""
    if not _allowed(update):
        return

    conn = context.bot_data["conn"]

    # /delete 12 removes record #12 straight away.
    if context.args:
        try:
            row_id = int(normalize_digits(context.args[0]))
        except ValueError:
            await update.effective_message.reply_text("شماره رکورد باید عدد باشه.")
            return

        row = store.get_by_id(conn, row_id)
        if row is None:
            await update.effective_message.reply_text(f"رکورد #{fa_plain(row_id)} پیدا نشد.")
            return

        await update.effective_message.reply_text(
            f"حذف رکورد #{fa_plain(row_id)}؟\n\n{_sale_label(row)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 حذف کن", callback_data=f"del:one:{row_id}"),
                InlineKeyboardButton("انصراف", callback_data="del:cancel"),
            ]]),
        )
        return

    rows = store.recent(conn, limit=10)
    if not rows:
        await update.effective_message.reply_text("هیچ رکوردی برای حذف نیست.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑 {_sale_label(r)}", callback_data=f"del:one:{r['id']}")]
        for r in rows
    ]
    keyboard.append([InlineKeyboardButton("انصراف", callback_data="del:cancel")])

    await update.effective_message.reply_text(
        "*کدوم رکورد حذف بشه؟*\n(۱۰ فروش آخر)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── delete: a batch, by range or by ids ────────────────────────────────
async def cmd_deletemany(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete several records: a date range, or an explicit list of ids."""
    if not _allowed(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "*حذف چند رکورد*\n\n"
            "با بازه:\n`/deletemany 27 aug - 29 aug`\n"
            "`/deletemany ۵ شهریور تا ۷ شهریور`\n\n"
            "با شماره رکورد:\n`/deletemany 12 15 18`\n\n"
            "شماره‌ها رو با `/records` ببین.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    conn = context.bot_data["conn"]
    arg = " ".join(context.args)
    tokens = normalize_digits(arg).split()

    # All-numeric arguments mean "these record ids".
    if all(t.isdigit() for t in tokens):
        ids = [int(t) for t in tokens]
        found = [r for r in (store.get_by_id(conn, i) for i in ids) if r is not None]
        if not found:
            await update.effective_message.reply_text("هیچ‌کدوم از این شماره‌ها پیدا نشد.")
            return

        missing = set(ids) - {r["id"] for r in found}
        total = sum(r["amount"] for r in found)
        lines = [f"*حذف {fa_plain(len(found))} رکورد؟*", ""]
        lines += [f"• #{fa_plain(r['id'])} — {_sale_label(r)}" for r in found]
        if missing:
            lines.append(f"\n(پیدا نشد: {'، '.join(fa_plain(m) for m in sorted(missing))})")
        lines += ["", f"مجموع حذف‌شدنی: *{fa(total)} t*"]

        payload = ",".join(str(r["id"]) for r in found)
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 حذف کن", callback_data=f"del:ids:{payload}"),
                InlineKeyboardButton("انصراف", callback_data="del:cancel"),
            ]]),
        )
        return

    # Otherwise treat it as a date range.
    try:
        start, end = parse_range(arg)
    except RangeError as exc:
        await update.effective_message.reply_text(
            f"نه بازه بود نه شماره رکورد ({exc}).\n"
            "مثال: `/deletemany 27 aug - 29 aug` یا `/deletemany 12 15`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    total, count, _ = store.sum_range(conn, start, end)
    if not count:
        await update.effective_message.reply_text("در این بازه رکوردی نیست.")
        return

    await update.effective_message.reply_text(
        f"*حذف همه‌ی رکوردهای این بازه؟*\n\n"
        f"از {jalali_str(start)} تا {jalali_str(end)}\n"
        f"تعداد: {fa(count)} رکورد\n"
        f"مبلغ: *{fa(total)} t*\n\n"
        f"⚠️ این کار برگشت‌پذیر نیست.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"🗑 حذف {fa_plain(count)} رکورد",
                callback_data=f"del:range:{start.isoformat()}:{end.isoformat()}",
            ),
            InlineKeyboardButton("انصراف", callback_data="del:cancel"),
        ]]),
    )


# ── delete: everything ─────────────────────────────────────────────────
async def cmd_deleteall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe the whole book — two taps, because it cannot be undone."""
    if not _allowed(update):
        return

    conn = context.bot_data["conn"]
    total, count, _, _ = store.grand_total(conn)
    if not count:
        await update.effective_message.reply_text("دفتر همین‌الان خالیه.")
        return

    await update.effective_message.reply_text(
        f"⚠️ *پاک کردن کل دفتر*\n\n"
        f"{fa(count)} رکورد به ارزش *{fa(total)} t* حذف می‌شه.\n"
        f"این کار *برگشت‌پذیر نیست*.\n\n"
        f"مطمئنی؟",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("بله، پاک کن", callback_data="del:all:confirm"),
            InlineKeyboardButton("انصراف", callback_data="del:cancel"),
        ]]),
    )


async def cmd_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent records with their ids, so they can be deleted by number."""
    if not _allowed(update):
        return

    limit = 20
    if context.args:
        try:
            limit = max(1, min(60, int(normalize_digits(context.args[0]))))
        except ValueError:
            pass

    conn = context.bot_data["conn"]
    rows = store.recent(conn, limit=limit)
    if not rows:
        await update.effective_message.reply_text("هیچ رکوردی ثبت نشده.")
        return

    lines = [f"*{fa_plain(len(rows))} رکورد آخر*", ""]
    for r in rows:
        lines.append(f"`#{fa_plain(r['id'])}` — {_sale_label(r)}")
    lines += ["", "حذف: `/delete <شماره>` یا `/deletemany 12 15 18`"]

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


# ── button presses ─────────────────────────────────────────────────────
async def on_delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle every ``del:*`` callback from the inline keyboards above."""
    query = update.callback_query
    await query.answer()

    conn = context.bot_data["conn"]
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        await query.edit_message_text("لغو شد. چیزی حذف نشد.")
        return

    if action == "one":
        row = store.delete_by_id(conn, int(parts[2]))
        if row is None:
            await query.edit_message_text("این رکورد قبلاً حذف شده بود.")
            return
        total, count, _, _ = store.grand_total(conn)
        await query.edit_message_text(
            f"حذف شد 🗑\n\n{_sale_label(row)}\n\n"
            f"جمع کل دفتر: *{fa(total)} t* در {fa(count)} فروش",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "ids":
        ids = [int(x) for x in parts[2].split(",") if x]
        removed, amount = store.delete_ids(conn, ids)
        total, count, _, _ = store.grand_total(conn)
        await query.edit_message_text(
            f"{fa(removed)} رکورد حذف شد 🗑 (*{fa(amount)} t*)\n\n"
            f"جمع کل دفتر: *{fa(total)} t* در {fa(count)} فروش",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "range":
        start, end = date.fromisoformat(parts[2]), date.fromisoformat(parts[3])
        removed, amount = store.delete_range(conn, start, end)
        total, count, _, _ = store.grand_total(conn)
        await query.edit_message_text(
            f"{fa(removed)} رکورد از {jalali_str(start)} تا {jalali_str(end)}"
            f" حذف شد 🗑 (*{fa(amount)} t*)\n\n"
            f"جمع کل دفتر: *{fa(total)} t* در {fa(count)} فروش",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "all":
        removed, amount = store.delete_all(conn)
        await query.edit_message_text(
            f"کل دفتر پاک شد 🗑\n{fa(removed)} رکورد به ارزش *{fa(amount)} t*.\n\n"
            f"جمع کل دفتر: *۰ t*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "*بات حساب‌داری فروش*\n\n"
        "پست‌های کانال رو می‌خونم، عدد قبل از `t` رو به‌عنوان مبلغ و "
        "تاریخ شمسی رو به‌عنوان روز فروش ذخیره می‌کنم.\n"
        "پست رو برام *فوروارد* هم که کنی همین کار رو می‌کنم — "
        "فوروارد تکراری دوباره حساب نمی‌شه.\n\n"
        "*دستورها*\n"
        "`/sum 27 aug - 29 aug` — جمع بازه + جمع کل دفتر\n"
        "`/sum ۵ شهریور تا ۷ شهریور` — همون به شمسی\n"
        "`/sum today` · `/sum week` · `/sum month`\n"
        "`/total` — جمع کل همه‌ی فروش‌ها، بی‌توجه به تاریخ\n"
        "`/report <بازه>` — تفکیک روزانه + جمع بازه + جمع کل\n"
        "`/list <بازه>` — ریز فروش‌ها\n"
        "`/records` — رکوردها با شماره‌شون\n\n"
        "*حذف*\n"
        "`/delete` — دکمه‌ی حذف برای ۱۰ فروش آخر\n"
        "`/delete 12` — حذف رکورد شماره ۱۲\n"
        "`/deletemany 12 15 18` — حذف چند رکورد با شماره\n"
        "`/deletemany 27 aug - 29 aug` — حذف یک بازه\n"
        "`/deleteall` — پاک کردن کل دفتر (با تأیید)\n\n"
        "`/pending` — پست‌هایی که تاریخشون خوانده نشد\n"
        "`/backfill` — بازخوانی رکوردهای قبلی\n",
        parse_mode=ParseMode.MARKDOWN,
    )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set — export it before starting the bot.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["conn"] = store.connect()

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("sum", cmd_sum))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("total", cmd_total))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("records", cmd_records))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("deletemany", cmd_deletemany))
    app.add_handler(CommandHandler("deleteall", cmd_deleteall))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("backfill", cmd_backfill))
    app.add_handler(CallbackQueryHandler(on_delete_button, pattern=r"^del:"))

    # Channel posts, edited channel posts, and plain group/DM messages.
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_post,
        )
    )

    log.info("bot starting — allowed chats: %s", ALLOWED_CHATS or "any")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
