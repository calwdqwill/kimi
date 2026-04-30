"""
SQLite persistence layer — multi-contract.

Tables:
- contracts: registered contracts (BMM6, BMK6, etc.)
- candles: historical close prices per contract/source/symbol/timeframe.
- current_prices: latest best_bid/best_ask/last_price per contract/source/symbol.
- tick_log: last N ticks for display.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from config import DB_PATH, DEFAULT_CONTRACTS

DB_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
INIT_SQL = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    moex_symbol TEXT NOT NULL,
    hl_coin TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    close REAL NOT NULL,
    UNIQUE(contract_id, source, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS current_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    last_price REAL,
    updated_ms INTEGER NOT NULL,
    meta TEXT,
    UNIQUE(contract_id, source, symbol)
);

CREATE TABLE IF NOT EXISTS tick_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    moex_mid REAL,
    hl_mid REAL,
    spread REAL,
    spread_pct REAL,
    zscore REAL
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup 
    ON candles(contract_id, source, symbol, timeframe, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_tick_log_contract 
    ON tick_log(contract_id, timestamp_ms);
"""


def _get_conn() -> sqlite3.Connection:
    """Return a connection with row factory set and WAL mode enabled."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Initialize the database schema and seed default contracts."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.executescript(INIT_SQL)
            # Seed default contracts if none exist
            for c in DEFAULT_CONTRACTS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO contracts (id, name, moex_symbol, hl_coin, is_active, created_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (c["id"], c["name"], c["moex_symbol"], c["hl_coin"], 1 if c["is_active"] else 0,
                     int(__import__('time').time() * 1000)),
                )
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
def get_contracts() -> list[dict]:
    """Return all contracts."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT * FROM contracts ORDER BY created_ms")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_contract(contract_id: str) -> Optional[dict]:
    """Return a single contract by id."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_contract(contract_id: str, name: str, moex_symbol: str, hl_coin: str) -> None:
    """Add a new contract."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.execute(
                """
                INSERT OR IGNORE INTO contracts (id, name, moex_symbol, hl_coin, is_active, created_ms)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (contract_id, name, moex_symbol, hl_coin, int(__import__('time').time() * 1000)),
            )
            conn.commit()
    finally:
        conn.close()


def toggle_contract(contract_id: str, is_active: bool) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.execute(
                "UPDATE contracts SET is_active = ? WHERE id = ?",
                (1 if is_active else 0, contract_id),
            )
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------
def insert_candle(
    contract_id: str,
    source: str,
    symbol: str,
    timeframe: str,
    timestamp_ms: int,
    close: float,
) -> None:
    """Insert a single candle, ignoring duplicates."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.execute(
                """
                INSERT OR IGNORE INTO candles
                    (contract_id, source, symbol, timeframe, timestamp_ms, close)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (contract_id, source, symbol, timeframe, timestamp_ms, close),
            )
            conn.commit()
    finally:
        conn.close()


def insert_candles_batch(
    rows: list[tuple[str, str, str, str, int, float]],
) -> None:
    """Bulk insert candles. Each row:
    (contract_id, source, symbol, timeframe, timestamp_ms, close).
    """
    if not rows:
        return
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.executemany(
                """
                INSERT OR IGNORE INTO candles
                    (contract_id, source, symbol, timeframe, timestamp_ms, close)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
    finally:
        conn.close()


def get_candles(
    contract_id: str,
    source: str,
    symbol: str,
    timeframe: str,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Return ordered list of candles as dicts."""
    conn = _get_conn()
    try:
        sql = """
            SELECT timestamp_ms, close
            FROM candles
            WHERE contract_id = ? AND source = ? AND symbol = ? AND timeframe = ?
        """
        params: list = [contract_id, source, symbol, timeframe]
        if from_ms is not None:
            sql += " AND timestamp_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(to_ms)
        sql += " ORDER BY timestamp_ms ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_last_timestamp(
    contract_id: str,
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
            WHERE contract_id = ? AND source = ? AND symbol = ? AND timeframe = ?
            """,
            (contract_id, source, symbol, timeframe),
        ).fetchone()
        return row["ts"] if row and row["ts"] is not None else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Current prices
# ---------------------------------------------------------------------------
def upsert_current(
    contract_id: str,
    source: str,
    symbol: str,
    best_bid: Optional[float],
    best_ask: Optional[float],
    last_price: Optional[float],
    updated_ms: int,
    meta: Optional[str] = None,
) -> None:
    """Insert or replace the latest price snapshot."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.execute(
                """
                INSERT INTO current_prices
                    (contract_id, source, symbol, best_bid, best_ask, last_price, updated_ms, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contract_id, source, symbol) DO UPDATE SET
                    best_bid = excluded.best_bid,
                    best_ask = excluded.best_ask,
                    last_price = excluded.last_price,
                    updated_ms = excluded.updated_ms,
                    meta = excluded.meta
                """,
                (contract_id, source, symbol, best_bid, best_ask, last_price, updated_ms, meta),
            )
            conn.commit()
    finally:
        conn.close()


def get_current(contract_id: str, source: str, symbol: str) -> Optional[dict]:
    """Return the latest price snapshot as a dict, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT best_bid, best_ask, last_price, updated_ms, meta
            FROM current_prices
            WHERE contract_id = ? AND source = ? AND symbol = ?
            """,
            (contract_id, source, symbol),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tick log
# ---------------------------------------------------------------------------
def insert_tick(contract_id: str, timestamp_ms: int, moex_mid: float,
                hl_mid: float, spread: float, spread_pct: float,
                zscore: Optional[float] = None) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            conn.execute(
                """
                INSERT INTO tick_log (contract_id, timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (contract_id, timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore),
            )
            # Keep only last 2000 ticks per contract
            conn.execute(
                """
                DELETE FROM tick_log WHERE contract_id = ? AND id NOT IN (
                    SELECT id FROM tick_log WHERE contract_id = ? ORDER BY timestamp_ms DESC LIMIT 2000
                )
                """,
                (contract_id, contract_id),
            )
            conn.commit()
    finally:
        conn.close()


def get_ticks(contract_id: str, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            SELECT timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore
            FROM tick_log
            WHERE contract_id = ?
            ORDER BY timestamp_ms DESC
            LIMIT ?
            """,
            (contract_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
