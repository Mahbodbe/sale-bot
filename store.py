"""SQLite storage for parsed sales."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).with_name("sales.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    sale_date   TEXT,              -- ISO Gregorian date, NULL when unparsed
    item        TEXT,
    event       TEXT,
    raw         TEXT NOT NULL,
    posted_at   TEXT NOT NULL,     -- when the message hit the channel
    UNIQUE (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales (sale_date);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_sale(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    message_id: int,
    amount: int,
    sale_date: date | None,
    item: str,
    event: str,
    raw: str,
    posted_at: str,
) -> None:
    """Insert a sale, or update it when the post is edited."""
    conn.execute(
        """
        INSERT INTO sales
            (chat_id, message_id, amount, sale_date, item, event, raw, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (chat_id, message_id) DO UPDATE SET
            amount    = excluded.amount,
            sale_date = excluded.sale_date,
            item      = excluded.item,
            event     = excluded.event,
            raw       = excluded.raw
        """,
        (
            chat_id,
            message_id,
            amount,
            sale_date.isoformat() if sale_date else None,
            item,
            event,
            raw,
            posted_at,
        ),
    )
    conn.commit()


def delete_sale(conn: sqlite3.Connection, chat_id: int, message_id: int) -> None:
    conn.execute(
        "DELETE FROM sales WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    )
    conn.commit()


def get_by_id(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sales WHERE id = ?", (row_id,)).fetchone()


def delete_by_id(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row | None:
    """Delete one row by its numeric id, returning what was removed."""
    row = get_by_id(conn, row_id)
    if row is None:
        return None
    conn.execute("DELETE FROM sales WHERE id = ?", (row_id,))
    conn.commit()
    return row


def delete_ids(conn: sqlite3.Connection, row_ids: list[int]) -> tuple[int, int]:
    """Delete several rows by id. Returns ``(rows_deleted, amount_removed)``."""
    if not row_ids:
        return 0, 0

    placeholders = ",".join("?" * len(row_ids))
    removed = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count"
        f" FROM sales WHERE id IN ({placeholders})",
        row_ids,
    ).fetchone()
    conn.execute(f"DELETE FROM sales WHERE id IN ({placeholders})", row_ids)
    conn.commit()
    return removed["count"], removed["total"]


def delete_range(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    chat_id: int | None = None,
) -> tuple[int, int]:
    """Delete every sale inside a date window. Returns ``(count, amount)``."""
    params: list[object] = [start.isoformat(), end.isoformat()]
    where = "COALESCE(sale_date, substr(posted_at, 1, 10)) BETWEEN ? AND ?"
    if chat_id is not None:
        where += " AND chat_id = ?"
        params.append(chat_id)

    removed = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count"
        f" FROM sales WHERE {where}",
        params,
    ).fetchone()
    conn.execute(f"DELETE FROM sales WHERE {where}", params)
    conn.commit()
    return removed["count"], removed["total"]


def delete_all(conn: sqlite3.Connection, chat_id: int | None = None) -> tuple[int, int]:
    """Wipe the book. Returns ``(count, amount)`` that was removed."""
    sql_tail = ""
    params: list[object] = []
    if chat_id is not None:
        sql_tail = " WHERE chat_id = ?"
        params.append(chat_id)

    removed = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count"
        f" FROM sales{sql_tail}",
        params,
    ).fetchone()
    conn.execute(f"DELETE FROM sales{sql_tail}", params)
    conn.commit()
    return removed["count"], removed["total"]


def recent(
    conn: sqlite3.Connection,
    limit: int = 10,
    chat_id: int | None = None,
) -> list[sqlite3.Row]:
    """Most recently stored sales — the pick list for delete buttons."""
    sql = "SELECT * FROM sales"
    params: list[object] = []
    if chat_id is not None:
        sql += " WHERE chat_id = ?"
        params.append(chat_id)
    return conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()


def sum_range(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    chat_id: int | None = None,
) -> tuple[int, int, list[sqlite3.Row]]:
    """Total, count and rows for sales in ``[start, end]`` inclusive.

    Rows whose date could not be parsed fall back to the posting date so a
    typo in the message never silently drops money from the report.
    """
    params: list[object] = [start.isoformat(), end.isoformat()]
    where = "COALESCE(sale_date, substr(posted_at, 1, 10)) BETWEEN ? AND ?"
    if chat_id is not None:
        where += " AND chat_id = ?"
        params.append(chat_id)

    rows = conn.execute(
        f"""
        SELECT * FROM sales
        WHERE {where}
        ORDER BY COALESCE(sale_date, substr(posted_at, 1, 10)), id
        """,
        params,
    ).fetchall()

    total = sum(r["amount"] for r in rows)
    return total, len(rows), rows


def unparsed_dates(conn: sqlite3.Connection, chat_id: int | None = None) -> list[sqlite3.Row]:
    """Sales whose date could not be read — worth showing to the operator."""
    sql = "SELECT * FROM sales WHERE sale_date IS NULL"
    params: list[object] = []
    if chat_id is not None:
        sql += " AND chat_id = ?"
        params.append(chat_id)
    return conn.execute(sql + " ORDER BY id DESC LIMIT 20", params).fetchall()


def grand_total(conn: sqlite3.Connection, chat_id: int | None = None) -> tuple[int, int, str | None, str | None]:
    """Total, count, first and last day across every stored sale.

    This is the running book balance — it deliberately ignores any date
    window so a range report can be read against the whole picture.
    """
    sql = """
        SELECT COALESCE(SUM(amount), 0) AS total,
               COUNT(*)                 AS count,
               MIN(COALESCE(sale_date, substr(posted_at, 1, 10))) AS first_day,
               MAX(COALESCE(sale_date, substr(posted_at, 1, 10))) AS last_day
        FROM sales
    """
    params: list[object] = []
    if chat_id is not None:
        sql += " WHERE chat_id = ?"
        params.append(chat_id)

    row = conn.execute(sql, params).fetchone()
    return row["total"], row["count"], row["first_day"], row["last_day"]


def totals_by_day(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    chat_id: int | None = None,
) -> list[sqlite3.Row]:
    """Per-day subtotals inside a window, for the breakdown in reports."""
    params: list[object] = [start.isoformat(), end.isoformat()]
    where = "COALESCE(sale_date, substr(posted_at, 1, 10)) BETWEEN ? AND ?"
    if chat_id is not None:
        where += " AND chat_id = ?"
        params.append(chat_id)

    return conn.execute(
        f"""
        SELECT COALESCE(sale_date, substr(posted_at, 1, 10)) AS day,
               SUM(amount) AS total,
               COUNT(*)    AS count
        FROM sales
        WHERE {where}
        GROUP BY day
        ORDER BY day
        """,
        params,
    ).fetchall()
