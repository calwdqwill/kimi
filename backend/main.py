"""
FastAPI application — multi-contract Brent Spread Dashboard.

Run from the backend folder:
    uvicorn main:app --reload --port 8000
"""

import concurrent.futures
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

import database
from config import (
    BASE_DIR, TIMEFRAMES, ZSCORE_WINDOW, DEFAULT_CONTRACTS,
)
from clients import finam_client, hl_client
from domain import sync, spread, zscore, stats as stats_module

_POLL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="poll_")

logger = logging.getLogger(__name__)
app = FastAPI(title="Brent Spread Dashboard — Multi-Contract")

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


@app.middleware("http")
async def log_errors(request, call_next):
    """Log all unhandled exceptions."""
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        logger.error("REQUEST ERROR %s %s: %s", request.method, request.url.path, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Background polling state
# ---------------------------------------------------------------------------
_stop_event = threading.Event()
_poll_thread: threading.Thread | None = None
_history_thread: threading.Thread | None = None


def _poll_contract(contract: dict) -> None:
    """Poll current prices for a single contract and store in DB."""
    contract_id = contract["id"]
    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    def _fetch_finam():
        try:
            return finam_client.fetch_current(moex_symbol)
        except Exception as exc:
            logger.warning("Polling Finam failed for %s: %s", contract_id, exc)
            return None

    def _fetch_hl():
        try:
            return hl_client.fetch_current(hl_coin)
        except Exception as exc:
            logger.warning("Polling HL failed for %s: %s", contract_id, exc)
            return None

    # Fetch both venues in parallel
    fut_finam = _POLL_EXECUTOR.submit(_fetch_finam)
    fut_hl = _POLL_EXECUTOR.submit(_fetch_hl)

    finam_data = fut_finam.result()
    hl_data = fut_hl.result()

    if finam_data:
        database.upsert_current(
            contract_id=contract_id,
            source="finam",
            symbol=moex_symbol,
            best_bid=finam_data.get("best_bid"),
            best_ask=finam_data.get("best_ask"),
            last_price=finam_data.get("last_price"),
            updated_ms=finam_data.get("updated_ms") or int(time.time() * 1000),
            meta=json.dumps({"is_orderbook": finam_data.get("is_orderbook", False)}),
        )

    if hl_data:
        database.upsert_current(
            contract_id=contract_id,
            source="hyperliquid",
            symbol=hl_coin,
            best_bid=hl_data.get("best_bid"),
            best_ask=hl_data.get("best_ask"),
            last_price=hl_data.get("last_price"),
            updated_ms=hl_data.get("updated_ms") or int(time.time() * 1000),
            meta=json.dumps({"is_l2": hl_data.get("is_l2", False)}),
        )

    # Compute and log tick
    try:
        moex = database.get_current(contract_id, "finam", moex_symbol) or {}
        hl = database.get_current(contract_id, "hyperliquid", hl_coin) or {}
        moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
        hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))
        if moex_mid is not None and hl_mid is not None:
            sp = hl_mid - moex_mid
            sp_pct = spread.current_spread_pct(hl_mid, moex_mid)
            # Get recent zscore for context
            z = None
            try:
                _s = database.get_candles(contract_id, "finam", moex_symbol, "5m")
                _h = database.get_candles(contract_id, "hyperliquid", hl_coin, "5m")
                _sy = sync.strict_sync(_s, _h)
                _vals = []
                for r in _sy:
                    _sp = spread.historical_spread_pct(r["hl_close"], r["moex_close"])
                    if _sp is not None:
                        _vals.append(_sp)
                _zs = zscore.compute_zscore(_vals, window=ZSCORE_WINDOW)
                if _zs and _zs[-1] is not None:
                    z = round(_zs[-1], 4)
            except Exception:
                pass

            database.insert_tick(
                contract_id=contract_id,
                timestamp_ms=int(time.time() * 1000),
                moex_mid=round(moex_mid, 4),
                hl_mid=round(hl_mid, 4),
                spread=round(sp, 4),
                spread_pct=round(sp_pct, 4),
                zscore=z,
            )
    except Exception as exc:
        logger.debug("Tick logging failed for %s: %s", contract_id, exc)


def _poll_loop() -> None:
    """Poll current prices every ~2 seconds for all active contracts."""
    while not _stop_event.is_set():
        try:
            contracts = database.get_contracts()
            active = [c for c in contracts if c.get("is_active")]
            for contract in active:
                if _stop_event.is_set():
                    break
                _poll_contract(contract)
                # small delay between contracts to avoid rate limits
                _stop_event.wait(0.5)
        except Exception as exc:
            logger.warning("Poll loop error: %s", exc)

        _stop_event.wait(1.5)


# ---------------------------------------------------------------------------
# Background history loading
# ---------------------------------------------------------------------------
def _history_loop() -> None:
    """Periodically fetch historical candles for all active contracts."""
    _stop_event.wait(10)  # let polling settle first
    while not _stop_event.is_set():
        try:
            contracts = database.get_contracts()
            active = [c for c in contracts if c.get("is_active")]
            for contract in active:
                if _stop_event.is_set():
                    break
                for tf in TIMEFRAMES:
                    try:
                        _load_historical_data(contract["id"], contract["moex_symbol"], contract["hl_coin"], tf)
                    except Exception as exc:
                        logger.warning("History load failed for %s %s: %s", contract["id"], tf, exc)
                    _stop_event.wait(0.5)
                    if _stop_event.is_set():
                        break
        except Exception as exc:
            logger.warning("History loop error: %s", exc)
        _stop_event.wait(300)  # refresh every 5 minutes


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    database.init_db()
    global _poll_thread, _history_thread
    _stop_event.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    _poll_thread.start()
    _history_thread = threading.Thread(target=_history_loop, daemon=True)
    _history_thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop_event.set()
    if _poll_thread is not None:
        _poll_thread.join(timeout=3.0)
    if _history_thread is not None:
        _history_thread.join(timeout=5.0)


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
def _load_historical_data(contract_id: str, moex_symbol: str, hl_coin: str, timeframe: str) -> None:
    """Incrementally fetch and store historical candles."""
    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000

    # Finam
    last_finam = database.get_last_timestamp(contract_id, "finam", moex_symbol, timeframe)
    from_finam = (last_finam + 1) if last_finam else (now_ms - lookback_ms)
    from_finam = max(from_finam, now_ms - lookback_ms)
    if from_finam < now_ms:
        finam_candles = finam_client.fetch_historical(moex_symbol, timeframe, from_finam, now_ms)
        if finam_candles:
            rows = [
                (contract_id, "finam", moex_symbol, timeframe, c["timestamp_ms"], c["close"])
                for c in finam_candles
            ]
            database.insert_candles_batch(rows)

    # Hyperliquid
    last_hl = database.get_last_timestamp(contract_id, "hyperliquid", hl_coin, timeframe)
    from_hl = (last_hl + 1) if last_hl else (now_ms - lookback_ms)
    from_hl = max(from_hl, now_ms - lookback_ms)
    if from_hl < now_ms:
        hl_candles = hl_client.fetch_historical(hl_coin, timeframe, from_hl, now_ms)
        if hl_candles:
            rows = [
                (contract_id, "hyperliquid", hl_coin, timeframe, c["timestamp_ms"], c["close"])
                for c in hl_candles
            ]
            database.insert_candles_batch(rows)


# ---------------------------------------------------------------------------
# API endpoints — Contracts
# ---------------------------------------------------------------------------
@app.get("/api/contracts")
def get_contracts():
    """Return all contracts."""
    return database.get_contracts()


@app.post("/api/contracts")
def add_contract(contract_id: str, name: str, moex_symbol: str, hl_coin: str):
    """Add a new contract."""
    database.add_contract(contract_id, name, moex_symbol, hl_coin)
    return {"status": "ok", "id": contract_id}


@app.patch("/api/contracts/{contract_id}")
def toggle_contract(contract_id: str, is_active: bool):
    """Toggle contract active state."""
    database.toggle_contract(contract_id, is_active)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API endpoints — Historical
# ---------------------------------------------------------------------------
@app.get("/api/historical/{contract_id}/{timeframe}")
def get_historical(contract_id: str, timeframe: str):
    """Return synchronized historical spread % series with mean and sigma bands."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex = database.get_candles(contract_id, "finam", moex_symbol, timeframe, from_ms=now_ms - lookback_ms)
    hl = database.get_candles(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=now_ms - lookback_ms)
    synced = sync.strict_sync(moex, hl)

    # Compute statistics for mean and sigma lines
    spread_values = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            spread_values.append(sp)

    import statistics
    avg = statistics.mean(spread_values) if spread_values else 0
    try:
        sd = statistics.stdev(spread_values) if len(spread_values) > 1 else 0
    except Exception:
        sd = 0

    result = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            result.append(
                {
                    "timestamp_ms": row["timestamp_ms"],
                    "spread_pct": round(sp, 4),
                    "mean": round(avg, 4),
                    "plus_2sigma": round(avg + 2 * sd, 4),
                    "minus_2sigma": round(avg - 2 * sd, 4),
                }
            )
    return result


# ---------------------------------------------------------------------------
# API endpoints — Current prices
# ---------------------------------------------------------------------------
@app.get("/api/current/{contract_id}")
def get_current(contract_id: str):
    """Return latest best bid/ask/mid for both venues + current spread % + arb spread."""
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    moex = database.get_current(contract_id, "finam", moex_symbol) or {}
    hl = database.get_current(contract_id, "hyperliquid", hl_coin) or {}

    moex_bid = moex.get("best_bid")
    moex_ask = moex.get("best_ask")
    hl_bid = hl.get("best_bid")
    hl_ask = hl.get("best_ask")

    moex_mid = spread.mid(moex_bid, moex_ask)
    hl_mid = spread.mid(hl_bid, hl_ask)
    cur_spread = None
    arb = None
    if moex_mid is not None and hl_mid is not None:
        cur_spread = spread.current_spread_pct(hl_mid, moex_mid)
        arb = spread.arb_spread(hl_bid, hl_ask, moex_bid, moex_ask)

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
        "arb_spread": round(arb, 4) if arb is not None else None,
        "arb_direction": "Sell HL → Buy MOEX" if (arb or 0) < 0 else "Buy HL → Sell MOEX" if (arb or 0) > 0 else None,
        "updated_ms": max(
            moex.get("updated_ms", 0) or 0,
            hl.get("updated_ms", 0) or 0,
        ),
    }


# ---------------------------------------------------------------------------
# API endpoints — Z-Score
# ---------------------------------------------------------------------------
@app.get("/api/zscore/{contract_id}/{timeframe}")
def get_zscore(contract_id: str, timeframe: str):
    """Return rolling Z-score series."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex = database.get_candles(contract_id, "finam", moex_symbol, timeframe, from_ms=now_ms - lookback_ms)
    hl = database.get_candles(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=now_ms - lookback_ms)
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


# ---------------------------------------------------------------------------
# API endpoints — Statistics
# ---------------------------------------------------------------------------
@app.get("/api/stats/{contract_id}/{timeframe}")
def get_stats(contract_id: str, timeframe: str):
    """Return full statistics for the spread series."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex = database.get_candles(contract_id, "finam", moex_symbol, timeframe, from_ms=now_ms - lookback_ms)
    hl = database.get_candles(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=now_ms - lookback_ms)
    synced = sync.strict_sync(moex, hl)

    spread_values = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            spread_values.append(sp)

    return stats_module.compute_all(spread_values)


# ---------------------------------------------------------------------------
# API endpoints — Signal
# ---------------------------------------------------------------------------
@app.get("/api/signal/{contract_id}")
def get_signal(contract_id: str):
    """Return current entry signal based on latest spread and stats."""
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    # Get current spread
    moex = database.get_current(contract_id, "finam", moex_symbol) or {}
    hl = database.get_current(contract_id, "hyperliquid", hl_coin) or {}
    moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
    hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))

    if moex_mid is None or hl_mid is None:
        return {"signal": "neutral", "zscore": None, "description": "Нет данных"}

    cur_spread = spread.current_spread_pct(hl_mid, moex_mid)

    # Get stats from 5m for signal calculation
    now_ms = int(time.time() * 1000)
    lookback_ms = 20 * 24 * 60 * 60 * 1000
    moex_c = database.get_candles(contract_id, "finam", moex_symbol, "5m", from_ms=now_ms - lookback_ms)
    hl_c = database.get_candles(contract_id, "hyperliquid", hl_coin, "5m", from_ms=now_ms - lookback_ms)
    synced = sync.strict_sync(moex_c, hl_c)

    spread_values = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            spread_values.append(sp)

    s = stats_module.compute_all(spread_values)
    if s["avg"] is None or s["stddev"] is None:
        return {"signal": "neutral", "zscore": None, "description": "Недостаточно данных"}

    sig = stats_module.entry_signal(cur_spread, s["avg"], s["stddev"])
    sig["current_spread_pct"] = round(cur_spread, 4)
    sig["avg"] = s["avg"]
    sig["entry_low"] = s["entry_low"]
    sig["entry_high"] = s["entry_high"]
    return sig


# ---------------------------------------------------------------------------
# API endpoints — Ticks
# ---------------------------------------------------------------------------
@app.get("/api/ticks/{contract_id}")
def get_ticks(contract_id: str, limit: int = 50):
    """Return latest ticks for the contract."""
    return database.get_ticks(contract_id, limit)
