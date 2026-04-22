"""
FastAPI application entry point.

Run from the backend folder:
    uvicorn main:app --reload --port 8000
"""

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

import database
from config import BASE_DIR, MOEX_SYMBOL, HL_COIN, TIMEFRAMES, ZSCORE_WINDOW
from clients import finam_client, hl_client
from domain import sync, spread, zscore

logger = logging.getLogger(__name__)
app = FastAPI(title="Brent Spread Dashboard")

# ---------------------------------------------------------------------------
# Background polling state
# ---------------------------------------------------------------------------
_stop_event = threading.Event()
_poll_thread: threading.Thread | None = None


def _poll_loop() -> None:
    """Poll current prices every ~2 seconds and store in SQLite."""
    while not _stop_event.is_set():
        # Finam
        try:
            finam_data = finam_client.fetch_current()
            database.upsert_current(
                source="finam",
                symbol=MOEX_SYMBOL,
                best_bid=finam_data.get("best_bid"),
                best_ask=finam_data.get("best_ask"),
                last_price=finam_data.get("last_price"),
                updated_ms=finam_data.get("updated_ms") or int(time.time() * 1000),
                meta=json.dumps({"is_orderbook": finam_data.get("is_orderbook", False)}),
            )
        except Exception as exc:
            logger.warning("Polling Finam failed: %s", exc)

        # Hyperliquid
        try:
            hl_data = hl_client.fetch_current()
            database.upsert_current(
                source="hyperliquid",
                symbol=HL_COIN,
                best_bid=hl_data.get("best_bid"),
                best_ask=hl_data.get("best_ask"),
                last_price=hl_data.get("last_price"),
                updated_ms=hl_data.get("updated_ms") or int(time.time() * 1000),
                meta=json.dumps({"is_l2": hl_data.get("is_l2", False)}),
            )
        except Exception as exc:
            logger.warning("Polling HL failed: %s", exc)

        _stop_event.wait(2.0)


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    database.init_db()
    global _poll_thread
    _stop_event.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    _poll_thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop_event.set()
    if _poll_thread is not None:
        _poll_thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------
frontend_dir = BASE_DIR / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
def _root() -> FileResponse:
    return FileResponse(str(frontend_dir / "index.html"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_historical_data(timeframe: str) -> None:
    """
    Incrementally fetch and store historical candles for both sources.
    Only loads the last 20 days to keep startup fast.
    """
    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000

    # --- Finam ---
    last_finam = database.get_last_timestamp("finam", MOEX_SYMBOL, timeframe)
    from_finam = (last_finam + 1) if last_finam else (now_ms - lookback_ms)
    from_finam = max(from_finam, now_ms - lookback_ms)
    if from_finam < now_ms:
        finam_candles = finam_client.fetch_historical(timeframe, from_finam, now_ms)
        if finam_candles:
            rows = [
                ("finam", MOEX_SYMBOL, timeframe, c["timestamp_ms"], c["close"])
                for c in finam_candles
            ]
            database.insert_candles_batch(rows)

    # --- Hyperliquid ---
    last_hl = database.get_last_timestamp("hyperliquid", HL_COIN, timeframe)
    from_hl = (last_hl + 1) if last_hl else (now_ms - lookback_ms)
    from_hl = max(from_hl, now_ms - lookback_ms)
    if from_hl < now_ms:
        hl_candles = hl_client.fetch_historical(timeframe, from_hl, now_ms)
        if hl_candles:
            rows = [
                ("hyperliquid", HL_COIN, timeframe, c["timestamp_ms"], c["close"])
                for c in hl_candles
            ]
            database.insert_candles_batch(rows)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.get("/api/historical/{timeframe}")
def get_historical(timeframe: str):
    """Return synchronized historical spread % series."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    _load_historical_data(timeframe)

    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex = database.get_candles("finam", MOEX_SYMBOL, timeframe, from_ms=now_ms - lookback_ms)
    hl = database.get_candles("hyperliquid", HL_COIN, timeframe, from_ms=now_ms - lookback_ms)
    synced = sync.strict_sync(moex, hl)

    result = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            result.append(
                {
                    "timestamp_ms": row["timestamp_ms"],
                    "spread_pct": round(sp, 4),
                }
            )
    return result


@app.get("/api/current")
def get_current():
    """Return latest best bid/ask/mid for both venues + current spread %."""
    moex = database.get_current("finam", MOEX_SYMBOL) or {}
    hl = database.get_current("hyperliquid", HL_COIN) or {}

    moex_bid = moex.get("best_bid")
    moex_ask = moex.get("best_ask")
    hl_bid = hl.get("best_bid")
    hl_ask = hl.get("best_ask")

    moex_mid = spread.mid(moex_bid, moex_ask)
    hl_mid = spread.mid(hl_bid, hl_ask)
    cur_spread = None
    if moex_mid is not None and hl_mid is not None:
        cur_spread = spread.current_spread_pct(hl_mid, moex_mid)

    return {
        "moex": {
            "best_bid": moex_bid,
            "best_ask": moex_ask,
            "last_price": moex.get("last_price"),
            "mid": moex_mid,
            "is_orderbook": json.loads(moex.get("meta") or "{}").get("is_orderbook", False),
        },
        "hyperliquid": {
            "best_bid": hl_bid,
            "best_ask": hl_ask,
            "last_price": hl.get("last_price"),
            "mid": hl_mid,
            "is_l2": json.loads(hl.get("meta") or "{}").get("is_l2", False),
        },
        "current_spread_pct": round(cur_spread, 4) if cur_spread is not None else None,
        "updated_ms": max(
            moex.get("updated_ms", 0) or 0,
            hl.get("updated_ms", 0) or 0,
        ),
    }


@app.get("/api/zscore/{timeframe}")
def get_zscore(timeframe: str):
    """Return rolling Z-score series for the given timeframe."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    _load_historical_data(timeframe)

    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex = database.get_candles("finam", MOEX_SYMBOL, timeframe, from_ms=now_ms - lookback_ms)
    hl = database.get_candles("hyperliquid", HL_COIN, timeframe, from_ms=now_ms - lookback_ms)
    synced = sync.strict_sync(moex, hl)

    spread_values = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            spread_values.append(sp)

    z_values = zscore.compute_zscore(spread_values, window=ZSCORE_WINDOW)

    result = []
    for i, row in enumerate(synced):
        if i < len(z_values) and z_values[i] is not None:
            result.append(
                {
                    "timestamp_ms": row["timestamp_ms"],
                    "zscore": round(z_values[i], 4),
                }
            )
    return result
