#!/usr/bin/env python3
"""
Migrate data from the legacy SQLite database to PostgreSQL.

Usage (inside Docker Compose or from a host with DATABASE_URL set):
    DATABASE_URL=postgresql://moex:moex@db:5432/moex \
    python scripts/migrate_sqlite_to_postgres.py /opt/mo-ex/data/dashboard.db
"""

import sqlite3
import sys
from pathlib import Path

import psycopg2

# Ensure config/database use PostgreSQL
import os
os.environ.setdefault("POSTGRES_HOST", "db")

# Import after setting defaults; config.py will build DATABASE_URL from POSTGRES_*
from config import DATABASE_URL
import database


TABLES = [
    "contracts",
    "candles",
    "alor_candles",
    "current_prices",
    "tick_log",
    "paper_settings",
    "paper_trades",
    "paper_funding",
    "paper_equity",
]

# Per-table conflict handling for upserts.
CONFLICT_CLAUSES = {
    "contracts": """
        ON CONFLICT (id) DO UPDATE SET
            name = excluded.name,
            asset = excluded.asset,
            moex_symbol = excluded.moex_symbol,
            hl_coin = excluded.hl_coin,
            is_active = excluded.is_active,
            contract_month = excluded.contract_month,
            contract_year = excluded.contract_year,
            contract_start_date = excluded.contract_start_date,
            created_ms = excluded.created_ms
    """,
    "candles": "ON CONFLICT (contract_id, source, symbol, timeframe, timestamp_ms) DO NOTHING",
    "alor_candles": "ON CONFLICT (contract_id, symbol, timeframe, timestamp_ms) DO NOTHING",
    "current_prices": """
        ON CONFLICT (contract_id, source, symbol) DO UPDATE SET
            best_bid = excluded.best_bid,
            best_ask = excluded.best_ask,
            last_price = excluded.last_price,
            updated_ms = excluded.updated_ms,
            meta = excluded.meta
    """,
    "tick_log": "",  # no unique constraint; plain INSERT
    "paper_settings": """
        ON CONFLICT (id) DO UPDATE SET
            deposit = excluded.deposit,
            leverage = excluded.leverage,
            entry_levels = excluded.entry_levels,
            max_hold_days = excluded.max_hold_days,
            hard_stop = excluded.hard_stop,
            cooldown_days = excluded.cooldown_days,
            moex_fee = excluded.moex_fee,
            hl_fee = excluded.hl_fee,
            slippage = excluded.slippage,
            lookback_days = excluded.lookback_days,
            mode = excluded.mode,
            include_funding = excluded.include_funding,
            updated_ms = excluded.updated_ms
    """,
    "paper_trades": """
        ON CONFLICT (id) DO UPDATE SET
            contract_id = excluded.contract_id,
            side = excluded.side,
            entry_timestamp_ms = excluded.entry_timestamp_ms,
            exit_timestamp_ms = excluded.exit_timestamp_ms,
            entry_level = excluded.entry_level,
            entry_deviation = excluded.entry_deviation,
            entry_spread = excluded.entry_spread,
            exit_spread = excluded.exit_spread,
            entry_moex = excluded.entry_moex,
            entry_hl = excluded.entry_hl,
            exit_moex = excluded.exit_moex,
            exit_hl = excluded.exit_hl,
            size = excluded.size,
            days_held = excluded.days_held,
            exit_reason = excluded.exit_reason,
            gross_pnl = excluded.gross_pnl,
            funding_total = excluded.funding_total,
            entry_fees = excluded.entry_fees,
            exit_fees = excluded.exit_fees,
            net_pnl = excluded.net_pnl,
            status = excluded.status,
            created_ms = excluded.created_ms
    """,
    "paper_funding": "ON CONFLICT (trade_id, timestamp_ms) DO NOTHING",
    "paper_equity": "ON CONFLICT (contract_id, timestamp_ms) DO UPDATE SET equity = excluded.equity",
}

SERIAL_TABLES = [
    "candles",
    "alor_candles",
    "current_prices",
    "tick_log",
    "paper_trades",
    "paper_funding",
    "paper_equity",
]


def migrate(sqlite_path: str) -> None:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Make sure POSTGRES_* variables or DATABASE_URL are configured.")
        sys.exit(1)

    sqlite_path = Path(sqlite_path)
    if not sqlite_path.exists():
        print(f"ERROR: SQLite database not found: {sqlite_path}")
        sys.exit(1)

    print(f"Target PostgreSQL: {DATABASE_URL.replace('://', '://***:***@')}")
    print(f"Source SQLite:     {sqlite_path}")

    # Ensure Postgres schema and default contracts exist
    database.init_db()

    lite_conn = sqlite3.connect(str(sqlite_path))
    lite_conn.row_factory = sqlite3.Row
    lite_cur = lite_conn.cursor()

    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor()

    # Wipe existing data so the SQLite dump is authoritative and ids never clash.
    for table in reversed(TABLES):
        pg_cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    pg_conn.commit()
    print("Target tables truncated.")

    for table in TABLES:
        lite_cur.execute(f"SELECT * FROM {table}")
        rows = lite_cur.fetchall()
        if not rows:
            print(f"  {table}: no rows to migrate")
            continue

        cols = list(rows[0].keys())
        col_names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        conflict = CONFLICT_CLAUSES.get(table, "").strip()

        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
        if conflict:
            sql += " " + conflict

        values = [tuple(row[c] for c in cols) for row in rows]

        pg_cur.executemany(sql, values)
        pg_conn.commit()
        print(f"  {table}: migrated {len(values)} rows")

    # Update serial sequences after explicit-id inserts
    for table in SERIAL_TABLES:
        pg_cur.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, 'id'), (SELECT COALESCE(MAX(id), 1) FROM {table}))",
            (table,),
        )
    pg_conn.commit()

    lite_conn.close()
    pg_conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_sqlite_to_postgres.py <path_to_dashboard.db>")
        sys.exit(1)
    migrate(sys.argv[1])
