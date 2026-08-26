"""
Database layer — SQLite (legacy/host) and PostgreSQL (Docker).

The backend can run against either engine:
- PostgreSQL when DATABASE_URL is set (Docker Compose).
- SQLite (legacy data/dashboard.db) when DATABASE_URL is empty.
"""

import threading
import time
from pathlib import Path
from typing import Optional

from config import DB_PATH, DEFAULT_CONTRACTS, DATABASE_URL

_IS_PG = bool(DATABASE_URL)
DB_LOCK = threading.Lock()

if _IS_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _placeholder(sql: str) -> str:
    """Return SQL with the right parameter placeholder for the active engine."""
    return sql.replace("?", "%s") if _IS_PG else sql


def _get_conn():
    """Return a database connection (Postgres or SQLite)."""
    if _IS_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _execute_script(conn, sql: str) -> None:
    """Execute a multi-statement SQL script."""
    cur = conn.cursor()
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(_placeholder(stmt))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SQLITE_INIT_SQL = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset TEXT NOT NULL DEFAULT 'brent',
    moex_symbol TEXT NOT NULL,
    hl_coin TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    contract_month INTEGER NOT NULL DEFAULT 0,
    contract_year INTEGER NOT NULL DEFAULT 0,
    contract_start_date TEXT,
    created_ms BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    close REAL NOT NULL,
    UNIQUE(contract_id, source, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS alor_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL DEFAULT 0,
    is_prev_contract INTEGER NOT NULL DEFAULT 0,
    UNIQUE(contract_id, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS current_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    last_price REAL,
    updated_ms BIGINT NOT NULL,
    meta TEXT,
    UNIQUE(contract_id, source, symbol)
);

CREATE TABLE IF NOT EXISTS tick_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    moex_mid REAL,
    hl_mid REAL,
    spread REAL,
    spread_pct REAL,
    zscore REAL
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup 
    ON candles(contract_id, source, symbol, timeframe, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_alor_candles_lookup 
    ON alor_candles(contract_id, symbol, timeframe, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_tick_log_contract 
    ON tick_log(contract_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS paper_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    deposit REAL NOT NULL DEFAULT 15000,
    leverage INTEGER NOT NULL DEFAULT 2,
    entry_levels TEXT NOT NULL DEFAULT '[{"threshold":0.7,"sizePct":0.30},{"threshold":1.0,"sizePct":0.30},{"threshold":1.5,"sizePct":0.40}]',
    max_hold_days INTEGER NOT NULL DEFAULT 10,
    hard_stop REAL NOT NULL DEFAULT 2.0,
    cooldown_days INTEGER NOT NULL DEFAULT 2,
    moex_fee REAL NOT NULL DEFAULT 0.0002,
    hl_fee REAL NOT NULL DEFAULT 0.00035,
    slippage REAL NOT NULL DEFAULT 0.0003,
    lookback_days INTEGER NOT NULL DEFAULT 10,
    mode TEXT NOT NULL DEFAULT 'auto',
    include_funding INTEGER NOT NULL DEFAULT 1,
    updated_ms BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_timestamp_ms BIGINT NOT NULL,
    exit_timestamp_ms BIGINT,
    entry_level REAL,
    entry_deviation REAL,
    entry_spread REAL,
    exit_spread REAL,
    entry_moex REAL,
    entry_hl REAL,
    exit_moex REAL,
    exit_hl REAL,
    size REAL NOT NULL,
    days_held REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    funding_total REAL DEFAULT 0,
    entry_fees REAL DEFAULT 0,
    exit_fees REAL DEFAULT 0,
    net_pnl REAL,
    status TEXT NOT NULL DEFAULT 'open',
    created_ms BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_funding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    rate REAL,
    payment REAL,
    UNIQUE(trade_id, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS paper_equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    equity REAL NOT NULL,
    UNIQUE(contract_id, timestamp_ms)
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_contract ON paper_trades(contract_id, status, entry_timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_paper_funding_trade ON paper_funding(trade_id);
CREATE INDEX IF NOT EXISTS idx_paper_equity_contract ON paper_equity(contract_id, timestamp_ms);
"""

_PG_INIT_SQL = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset TEXT NOT NULL DEFAULT 'brent',
    moex_symbol TEXT NOT NULL,
    hl_coin TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    contract_month INTEGER NOT NULL DEFAULT 0,
    contract_year INTEGER NOT NULL DEFAULT 0,
    contract_start_date TEXT,
    created_ms BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    close REAL NOT NULL,
    UNIQUE(contract_id, source, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS alor_candles (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL DEFAULT 0,
    is_prev_contract INTEGER NOT NULL DEFAULT 0,
    UNIQUE(contract_id, symbol, timeframe, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS current_prices (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    last_price REAL,
    updated_ms BIGINT NOT NULL,
    meta TEXT,
    UNIQUE(contract_id, source, symbol)
);

CREATE TABLE IF NOT EXISTS tick_log (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    moex_mid REAL,
    hl_mid REAL,
    spread REAL,
    spread_pct REAL,
    zscore REAL
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup 
    ON candles(contract_id, source, symbol, timeframe, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_alor_candles_lookup 
    ON alor_candles(contract_id, symbol, timeframe, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_tick_log_contract 
    ON tick_log(contract_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS paper_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    deposit REAL NOT NULL DEFAULT 15000,
    leverage INTEGER NOT NULL DEFAULT 2,
    entry_levels TEXT NOT NULL DEFAULT '[{"threshold":0.7,"sizePct":0.30},{"threshold":1.0,"sizePct":0.30},{"threshold":1.5,"sizePct":0.40}]',
    max_hold_days INTEGER NOT NULL DEFAULT 10,
    hard_stop REAL NOT NULL DEFAULT 2.0,
    cooldown_days INTEGER NOT NULL DEFAULT 2,
    moex_fee REAL NOT NULL DEFAULT 0.0002,
    hl_fee REAL NOT NULL DEFAULT 0.00035,
    slippage REAL NOT NULL DEFAULT 0.0003,
    lookback_days INTEGER NOT NULL DEFAULT 10,
    mode TEXT NOT NULL DEFAULT 'auto',
    include_funding INTEGER NOT NULL DEFAULT 1,
    updated_ms BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_timestamp_ms BIGINT NOT NULL,
    exit_timestamp_ms BIGINT,
    entry_level REAL,
    entry_deviation REAL,
    entry_spread REAL,
    exit_spread REAL,
    entry_moex REAL,
    entry_hl REAL,
    exit_moex REAL,
    exit_hl REAL,
    size REAL NOT NULL,
    days_held REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    funding_total REAL DEFAULT 0,
    entry_fees REAL DEFAULT 0,
    exit_fees REAL DEFAULT 0,
    net_pnl REAL,
    status TEXT NOT NULL DEFAULT 'open',
    created_ms BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_funding (
    id SERIAL PRIMARY KEY,
    trade_id INTEGER NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    rate REAL,
    payment REAL,
    UNIQUE(trade_id, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS paper_equity (
    id SERIAL PRIMARY KEY,
    contract_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    equity REAL NOT NULL,
    UNIQUE(contract_id, timestamp_ms)
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_contract ON paper_trades(contract_id, status, entry_timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_paper_funding_trade ON paper_funding(trade_id);
CREATE INDEX IF NOT EXISTS idx_paper_equity_contract ON paper_equity(contract_id, timestamp_ms);
"""

INIT_SQL = _PG_INIT_SQL if _IS_PG else _SQLITE_INIT_SQL


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Initialize the database schema and seed default contracts."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            _execute_script(conn, INIT_SQL)

            # SQLite-only: ensure new columns exist on older DBs
            if not _IS_PG:
                cur = conn.cursor()
                for col, col_type in [
                    ("contract_month", "INTEGER NOT NULL DEFAULT 0"),
                    ("contract_year", "INTEGER NOT NULL DEFAULT 0"),
                    ("asset", "TEXT NOT NULL DEFAULT 'brent'"),
                    ("contract_start_date", "TEXT"),
                ]:
                    try:
                        cur.execute(f"ALTER TABLE contracts ADD COLUMN {col} {col_type}")
                    except sqlite3.OperationalError:
                        pass

            # Seed default contracts
            cur = conn.cursor()
            now_ms = int(time.time() * 1000)
            for c in DEFAULT_CONTRACTS:
                cur.execute(
                    _placeholder(
                        """
                        INSERT INTO contracts (id, name, asset, moex_symbol, hl_coin, is_active, contract_month, contract_year, contract_start_date, created_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    (
                        c["id"], c["name"], c.get("asset", "brent"), c["moex_symbol"], c["hl_coin"],
                        1 if c.get("is_active") else 0,
                        c.get("contract_month", 0), c.get("contract_year", 0),
                        c.get("contract_start_date"), now_ms,
                    ),
                )
                cur.execute(
                    _placeholder(
                        """
                        UPDATE contracts SET asset = ?, contract_month = ?, contract_year = ?, contract_start_date = ?, is_active = ?
                        WHERE id = ?
                        """
                    ),
                    (
                        c.get("asset", "brent"), c.get("contract_month", 0), c.get("contract_year", 0),
                        c.get("contract_start_date"), 1 if c.get("is_active") else 0, c["id"],
                    ),
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
        cur = conn.cursor()
        cur.execute(_placeholder("SELECT * FROM contracts ORDER BY created_ms"))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_contract(contract_id: str) -> Optional[dict]:
    """Return a single contract by id."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_placeholder("SELECT * FROM contracts WHERE id = ?"), (contract_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_contract(
    contract_id: str,
    name: str,
    moex_symbol: str,
    hl_coin: str,
    asset: str = "brent",
    contract_month: int = 0,
    contract_year: int = 0,
    contract_start_date: str | None = None,
) -> None:
    """Add a new contract."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO contracts (id, name, asset, moex_symbol, hl_coin, is_active, contract_month, contract_year, contract_start_date, created_ms)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                (
                    contract_id, name, asset, moex_symbol, hl_coin,
                    contract_month, contract_year, contract_start_date,
                    int(time.time() * 1000),
                ),
            )
            conn.commit()
    finally:
        conn.close()


def toggle_contract(contract_id: str, is_active: bool) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder("UPDATE contracts SET is_active = ? WHERE id = ?"),
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
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO candles (contract_id, source, symbol, timeframe, timestamp_ms, close)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (contract_id, source, symbol, timeframe, timestamp_ms) DO NOTHING
                    """
                ),
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
            cur = conn.cursor()
            cur.executemany(
                _placeholder(
                    """
                    INSERT INTO candles (contract_id, source, symbol, timeframe, timestamp_ms, close)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (contract_id, source, symbol, timeframe, timestamp_ms) DO NOTHING
                    """
                ),
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

        cur = conn.cursor()
        cur.execute(_placeholder(sql), params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_candles_recent(
    contract_id: str,
    source: str,
    symbol: str,
    timeframe: str,
    from_ms: Optional[int] = None,
    limit: int = 2000,
) -> list[dict]:
    """Return last N candles ordered ASC (oldest first) for chart display."""
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
        sql += " ORDER BY timestamp_ms DESC LIMIT ?"
        params.append(limit)

        cur = conn.cursor()
        cur.execute(_placeholder(sql), params)
        rows = [dict(row) for row in cur.fetchall()]
        rows.reverse()  # oldest first for chart rendering
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alor candles
# ---------------------------------------------------------------------------
def insert_alor_candles_batch(rows: list[tuple]) -> None:
    """Bulk insert Alor OHLCV candles.
    Each row: (contract_id, symbol, timeframe, timestamp_ms, open, high, low, close, volume, is_prev_contract)
    """
    if not rows:
        return
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.executemany(
                _placeholder(
                    """
                    INSERT INTO alor_candles
                        (contract_id, symbol, timeframe, timestamp_ms, open, high, low, close, volume, is_prev_contract)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (contract_id, symbol, timeframe, timestamp_ms) DO NOTHING
                    """
                ),
                rows,
            )
            conn.commit()
    finally:
        conn.close()


def get_alor_candles(
    contract_id: str,
    symbol: str,
    timeframe: str,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Return Alor OHLCV candles ordered ASC."""
    conn = _get_conn()
    try:
        sql = """
            SELECT timestamp_ms, open, high, low, close, volume
            FROM alor_candles
            WHERE contract_id = ? AND symbol = ? AND timeframe = ?
        """
        params: list = [contract_id, symbol, timeframe]
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
        cur = conn.cursor()
        cur.execute(_placeholder(sql), params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_alor_candles(contract_id: str, symbol: str, timeframe: str) -> None:
    """Delete all Alor candles for a contract+timeframe (for reload)."""
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder("DELETE FROM alor_candles WHERE contract_id = ? AND symbol = ? AND timeframe = ?"),
                (contract_id, symbol, timeframe),
            )
            conn.commit()
    finally:
        conn.close()


def has_alor_candles(contract_id: str, symbol: str, timeframe: str) -> bool:
    """Return True if at least one alor_candle exists."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder("SELECT 1 FROM alor_candles WHERE contract_id = ? AND symbol = ? AND timeframe = ? LIMIT 1"),
            (contract_id, symbol, timeframe),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_alor_candles_recent(
    contract_id: str,
    symbol: str,
    timeframe: str,
    from_ms: Optional[int] = None,
    limit: int = 2000,
) -> list[dict]:
    """Return last N alor candles ordered ASC (oldest first) for chart display.
    Format matches get_candles_recent: {timestamp_ms, close}.
    """
    conn = _get_conn()
    try:
        sql = """
            SELECT timestamp_ms, close
            FROM alor_candles
            WHERE contract_id = ? AND symbol = ? AND timeframe = ?
        """
        params: list = [contract_id, symbol, timeframe]
        if from_ms is not None:
            sql += " AND timestamp_ms >= ?"
            params.append(from_ms)
        sql += " ORDER BY timestamp_ms DESC LIMIT ?"
        params.append(limit)

        cur = conn.cursor()
        cur.execute(_placeholder(sql), params)
        rows = [dict(row) for row in cur.fetchall()]
        rows.reverse()  # oldest first for chart rendering
        return rows
    finally:
        conn.close()


def get_last_alor_timestamp(
    contract_id: str,
    symbol: str,
    timeframe: str,
) -> Optional[int]:
    """Return the newest timestamp_ms for alor_candles, or None if empty."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                """
                SELECT MAX(timestamp_ms) as ts
                FROM alor_candles
                WHERE contract_id = ? AND symbol = ? AND timeframe = ?
                """
            ),
            (contract_id, symbol, timeframe),
        )
        row = cur.fetchone()
        return row["ts"] if row and row["ts"] is not None else None
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
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                """
                SELECT MAX(timestamp_ms) as ts
                FROM candles
                WHERE contract_id = ? AND source = ? AND symbol = ? AND timeframe = ?
                """
            ),
            (contract_id, source, symbol, timeframe),
        )
        row = cur.fetchone()
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
            cur = conn.cursor()
            cur.execute(
                _placeholder(
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
                    """
                ),
                (contract_id, source, symbol, best_bid, best_ask, last_price, updated_ms, meta),
            )
            conn.commit()
    finally:
        conn.close()


def get_current(contract_id: str, source: str, symbol: str) -> Optional[dict]:
    """Return the latest price snapshot as a dict, or None."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                """
                SELECT best_bid, best_ask, last_price, updated_ms, meta
                FROM current_prices
                WHERE contract_id = ? AND source = ? AND symbol = ?
                """
            ),
            (contract_id, source, symbol),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tick log
# ---------------------------------------------------------------------------
def insert_tick(
    contract_id: str,
    timestamp_ms: int,
    moex_mid: float,
    hl_mid: float,
    spread: float,
    spread_pct: float,
    zscore: Optional[float] = None,
) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO tick_log (contract_id, timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (contract_id, timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore),
            )
            cur.execute(
                _placeholder(
                    """
                    DELETE FROM tick_log WHERE contract_id = ? AND id NOT IN (
                        SELECT id FROM tick_log WHERE contract_id = ? ORDER BY timestamp_ms DESC LIMIT ?
                    )
                    """
                ),
                (contract_id, contract_id, 2000),
            )
            conn.commit()
    finally:
        conn.close()


def get_ticks(contract_id: str, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                """
                SELECT timestamp_ms, moex_mid, hl_mid, spread, spread_pct, zscore
                FROM tick_log
                WHERE contract_id = ?
                ORDER BY timestamp_ms DESC
                LIMIT ?
                """
            ),
            (contract_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Paper Trading
# ---------------------------------------------------------------------------
def get_paper_settings() -> dict:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_placeholder("SELECT * FROM paper_settings WHERE id = 1"))
        row = cur.fetchone()
        if not row:
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO paper_settings (id, updated_ms)
                    VALUES (1, ?)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                (int(time.time() * 1000),),
            )
            conn.commit()
            cur.execute(_placeholder("SELECT * FROM paper_settings WHERE id = 1"))
            row = cur.fetchone()
        return dict(row)
    finally:
        conn.close()


def update_paper_settings(
    deposit: float | None = None,
    leverage: int | None = None,
    entry_levels: str | None = None,
    max_hold_days: int | None = None,
    hard_stop: float | None = None,
    cooldown_days: int | None = None,
    moex_fee: float | None = None,
    hl_fee: float | None = None,
    slippage: float | None = None,
    lookback_days: int | None = None,
    mode: str | None = None,
    include_funding: int | None = None,
) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            fields = []
            params = []
            mapping = {
                "deposit": deposit,
                "leverage": leverage,
                "entry_levels": entry_levels,
                "max_hold_days": max_hold_days,
                "hard_stop": hard_stop,
                "cooldown_days": cooldown_days,
                "moex_fee": moex_fee,
                "hl_fee": hl_fee,
                "slippage": slippage,
                "lookback_days": lookback_days,
                "mode": mode,
                "include_funding": include_funding,
            }
            for col, value in mapping.items():
                if value is not None:
                    fields.append(f"{col} = ?")
                    params.append(value)
            fields.append("updated_ms = ?")
            params.append(int(time.time() * 1000))
            params.append(1)
            if fields:
                sql = f"UPDATE paper_settings SET {', '.join(fields)} WHERE id = ?"
                cur = conn.cursor()
                cur.execute(_placeholder(sql), params)
                conn.commit()
    finally:
        conn.close()


def get_paper_trades(contract_id: str, status: str | None = None, limit: int = 500) -> list[dict]:
    conn = _get_conn()
    try:
        sql = "SELECT * FROM paper_trades WHERE contract_id = ?"
        params: list = [contract_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY entry_timestamp_ms DESC LIMIT ?"
        params.append(limit)
        cur = conn.cursor()
        cur.execute(_placeholder(sql), params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_paper_trade(trade_id: int) -> dict | None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_placeholder("SELECT * FROM paper_trades WHERE id = ?"), (trade_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_active_paper_trade(contract_id: str) -> dict | None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                "SELECT * FROM paper_trades WHERE contract_id = ? AND status = 'open' ORDER BY entry_timestamp_ms DESC LIMIT 1"
            ),
            (contract_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_paper_trade(
    contract_id: str,
    side: str,
    entry_timestamp_ms: int,
    entry_level: float,
    entry_deviation: float,
    entry_spread: float,
    entry_moex: float,
    entry_hl: float,
    size: float,
    entry_fees: float,
) -> int:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            base_sql = """
                INSERT INTO paper_trades
                (contract_id, side, entry_timestamp_ms, entry_level, entry_deviation,
                 entry_spread, entry_moex, entry_hl, size, entry_fees, status, created_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """
            params = (
                contract_id, side, entry_timestamp_ms, entry_level, entry_deviation,
                entry_spread, entry_moex, entry_hl, size, entry_fees,
                int(time.time() * 1000),
            )
            if _IS_PG:
                cur.execute(_placeholder(base_sql + " RETURNING id"), params)
                trade_id = cur.fetchone()["id"]
            else:
                cur.execute(_placeholder(base_sql), params)
                trade_id = cur.lastrowid
            conn.commit()
            return trade_id
    finally:
        conn.close()


def close_paper_trade(
    trade_id: int,
    exit_timestamp_ms: int,
    exit_spread: float,
    exit_moex: float,
    exit_hl: float,
    days_held: float,
    exit_reason: str,
    gross_pnl: float,
    funding_total: float,
    exit_fees: float,
    net_pnl: float,
) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    UPDATE paper_trades SET
                        exit_timestamp_ms = ?,
                        exit_spread = ?,
                        exit_moex = ?,
                        exit_hl = ?,
                        days_held = ?,
                        exit_reason = ?,
                        gross_pnl = ?,
                        funding_total = ?,
                        exit_fees = ?,
                        net_pnl = ?,
                        status = 'closed'
                    WHERE id = ?
                    """
                ),
                (exit_timestamp_ms, exit_spread, exit_moex, exit_hl, days_held,
                 exit_reason, gross_pnl, funding_total, exit_fees, net_pnl, trade_id),
            )
            conn.commit()
    finally:
        conn.close()


def delete_all_paper_trades(contract_id: str | None = None) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            if contract_id:
                cur.execute(_placeholder("DELETE FROM paper_trades WHERE contract_id = ?"), (contract_id,))
                cur.execute(_placeholder("DELETE FROM paper_equity WHERE contract_id = ?"), (contract_id,))
            else:
                cur.execute(_placeholder("DELETE FROM paper_trades"))
                cur.execute(_placeholder("DELETE FROM paper_funding"))
                cur.execute(_placeholder("DELETE FROM paper_equity"))
            conn.commit()
    finally:
        conn.close()


def insert_paper_funding(trade_id: int, timestamp_ms: int, rate: float, payment: float) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO paper_funding (trade_id, timestamp_ms, rate, payment)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (trade_id, timestamp_ms) DO NOTHING
                    """
                ),
                (trade_id, timestamp_ms, rate, payment),
            )
            conn.commit()
    finally:
        conn.close()


def get_paper_funding(trade_id: int) -> list[dict]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder("SELECT * FROM paper_funding WHERE trade_id = ? ORDER BY timestamp_ms ASC"),
            (trade_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def insert_paper_equity(contract_id: str, timestamp_ms: int, equity: float) -> None:
    conn = _get_conn()
    try:
        with DB_LOCK:
            cur = conn.cursor()
            cur.execute(
                _placeholder(
                    """
                    INSERT INTO paper_equity (contract_id, timestamp_ms, equity)
                    VALUES (?, ?, ?)
                    ON CONFLICT (contract_id, timestamp_ms) DO UPDATE SET equity = excluded.equity
                    """
                ),
                (contract_id, timestamp_ms, equity),
            )
            cur.execute(
                _placeholder(
                    """
                    DELETE FROM paper_equity WHERE contract_id = ? AND id NOT IN (
                        SELECT id FROM paper_equity WHERE contract_id = ? ORDER BY timestamp_ms DESC LIMIT ?
                    )
                    """
                ),
                (contract_id, contract_id, 5000),
            )
            conn.commit()
    finally:
        conn.close()


def get_paper_equity(contract_id: str, limit: int = 5000) -> list[dict]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                "SELECT timestamp_ms, equity FROM paper_equity WHERE contract_id = ? ORDER BY timestamp_ms ASC LIMIT ?"
            ),
            (contract_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_paper_summary(contract_id: str) -> dict:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _placeholder(
                "SELECT COUNT(*) as cnt FROM paper_trades WHERE contract_id = ? AND status = 'closed'"
            ),
            (contract_id,),
        )
        total = cur.fetchone()["cnt"]
        cur.execute(
            _placeholder(
                "SELECT COUNT(*) as cnt FROM paper_trades WHERE contract_id = ? AND status = 'closed' AND net_pnl > 0"
            ),
            (contract_id,),
        )
        wins = cur.fetchone()["cnt"]
        losses = total - wins
        cur.execute(
            _placeholder(
                "SELECT SUM(net_pnl) as total FROM paper_trades WHERE contract_id = ? AND status = 'closed'"
            ),
            (contract_id,),
        )
        pnl = cur.fetchone()["total"]
        cur.execute(
            _placeholder(
                "SELECT SUM(gross_pnl) as total FROM paper_trades WHERE contract_id = ? AND status = 'closed'"
            ),
            (contract_id,),
        )
        gross = cur.fetchone()["total"]
        cur.execute(
            _placeholder(
                "SELECT SUM(funding_total) as total FROM paper_trades WHERE contract_id = ? AND status = 'closed'"
            ),
            (contract_id,),
        )
        funding = cur.fetchone()["total"]
        cur.execute(
            _placeholder(
                "SELECT SUM(entry_fees + exit_fees) as total FROM paper_trades WHERE contract_id = ? AND status = 'closed'"
            ),
            (contract_id,),
        )
        fees = cur.fetchone()["total"]
        return {
            "total_trades": total or 0,
            "wins": wins or 0,
            "losses": losses or 0,
            "winrate": round(wins / total * 100, 1) if total else 0,
            "net_pnl": round(pnl or 0, 2),
            "gross_pnl": round(gross or 0, 2),
            "funding_total": round(funding or 0, 2),
            "fees_total": round(fees or 0, 2),
        }
    finally:
        conn.close()
