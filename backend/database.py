"""
SQLite persistence layer.

Tables:
- candles: historical close prices per source/symbol/timeframe.
- current_prices: latest best_bid/best_ask/last_price per source/symbol.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from config import DB_PATH


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
INIT_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    close REAL NOT NULL,
    UNIQUE(source, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS current_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    last_price REAL,
    updated_ms INTEGER NOT NULL,
    meta TEXT,
    UNIQUE(source, symbol)
);
"""


def _get_conn() -> sqlite3.Connection:
    """Return a connection with row factory set."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the database schema if it does not exist."""
    conn = _get_conn()
    try:
        conn.executescript(INIT_SQL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------
def insert_candle(
    source: str,
    symbol: str,
    timeframe: str,
    timestamp_ms: int,
    close: float,
) -> None:
    """Insert a single candle, ignoring duplicates."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO candles
                (source, symbol, timeframe, timestamp_ms, close)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, symbol, timeframe, timestamp_ms, close),
        )
        conn.commit()
    finally:
        conn.close()


def insert_candles_batch(
    rows: list[tuple[str, str, str, int, float]],
) -> None:
    """Bulk insert candles for performance. Each row:
    (source, symbol, timeframe, timestamp_ms, close).
    """
    if not rows:
        return
    conn = _get_conn()
    try:
        conn.executemany(
            """
            INSERT OR IGNORE INTO candles
                (source, symbol, timeframe, timestamp_ms, close)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def get_candles(
    source: str,
    symbol: str,
    timeframe: str,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
) -> list[dict]:
    """Return ordered list of candles as dicts."""
    conn = _get_conn()
    try:
        sql = """
            SELECT timestamp_ms, close
            FROM candles
            WHERE source = ? AND symbol = ? AND timeframe = ?
        """
        params: list = [source, symbol, timeframe]
        if from_ms is not None:
            sql += " AND timestamp_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(to_ms)
        sql += " ORDER BY timestamp_ms ASC"

        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_last_timestamp(
    source: str,
    symbol: str,
    timeframe: str,
) -> Optional[int]:
    """Return the newest timestamp_ms for the series, or None if empty."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT MAX(timestamp_ms) as ts
            FROM candles
            WHERE source = ? AND symbol = ? AND timeframe = ?
            """,
            (source, symbol, timeframe),
        ).fetchone()
        return row["ts"] if row and row["ts"] is not None else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Current prices
# ---------------------------------------------------------------------------
def upsert_current(
    source: str,
    symbol: str,
    best_bid: Optional[float],
    best_ask: Optional[float],
    last_price: Optional[float],
    updated_ms: int,
    meta: Optional[str] = None,
) -> None:
    """Insert or replace the latest price snapshot for a source/symbol."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO current_prices
                (source, symbol, best_bid, best_ask, last_price, updated_ms, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, symbol) DO UPDATE SET
                best_bid = excluded.best_bid,
                best_ask = excluded.best_ask,
                last_price = excluded.last_price,
                updated_ms = excluded.updated_ms,
                meta = excluded.meta
            """,
            (source, symbol, best_bid, best_ask, last_price, updated_ms, meta),
        )
        conn.commit()
    finally:
        conn.close()


def get_current(source: str, symbol: str) -> Optional[dict]:
    """Return the latest price snapshot as a dict, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT best_bid, best_ask, last_price, updated_ms, meta
            FROM current_prices
            WHERE source = ? AND symbol = ?
            """,
            (source, symbol),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
