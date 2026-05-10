"""
FastAPI application — multi-contract Brent Spread Dashboard.

Run from the backend folder:
    uvicorn main:app --reload --port 8000
"""

import concurrent.futures
import datetime
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import FileResponse

import database
from config import (
    BASE_DIR, TIMEFRAMES, ZSCORE_WINDOW, DEFAULT_CONTRACTS,
)
from clients import alor_client, hl_client
from domain import sync, spread, zscore, stats as stats_module
import alor_history
# Alor history integration active

_POLL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="poll_")

logger = logging.getLogger(__name__)
app = FastAPI(title="Brent Spread Dashboard — Multi-Contract")

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# ---------------------------------------------------------------------------
# Simple in-memory cache with TTL
# ---------------------------------------------------------------------------
class _TimedCache:
    def __init__(self, default_ttl_seconds: float = 30.0):
        self._data: dict = {}
        self._ttl = default_ttl_seconds

    def get(self, key: str):
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() > entry["expires_at"]:
            del self._data[key]
            return None
        return entry["value"]

    def set(self, key: str, value, ttl: float | None = None):
        self._data[key] = {
            "value": value,
            "expires_at": time.time() + (ttl if ttl is not None else self._ttl),
        }

    def invalidate(self, pattern: str | None = None):
        if pattern is None:
            self._data.clear()
        else:
            for k in list(self._data.keys()):
                if pattern in k:
                    del self._data[k]

_API_CACHE = _TimedCache(default_ttl_seconds=30.0)


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

    def _fetch_moex():
        try:
            return alor_client.fetch_current(moex_symbol)
        except Exception as exc:
            logger.warning("Polling Alor failed for %s: %s", contract_id, exc)
            return None

    def _fetch_hl():
        try:
            return hl_client.fetch_current(hl_coin)
        except Exception as exc:
            logger.warning("Polling HL failed for %s: %s", contract_id, exc)
            return None

    # Fetch both venues in parallel
    fut_moex = _POLL_EXECUTOR.submit(_fetch_moex)
    fut_hl = _POLL_EXECUTOR.submit(_fetch_hl)

    moex_data = fut_moex.result()
    hl_data = fut_hl.result()

    if moex_data:
        database.upsert_current(
            contract_id=contract_id,
            source="moex",
            symbol=moex_symbol,
            best_bid=moex_data.get("best_bid"),
            best_ask=moex_data.get("best_ask"),
            last_price=moex_data.get("last_price"),
            updated_ms=moex_data.get("updated_ms") or int(time.time() * 1000),
            meta=json.dumps({"is_orderbook": moex_data.get("is_orderbook", False)}),
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
        moex = database.get_current(contract_id, "moex", moex_symbol) or {}
        hl = database.get_current(contract_id, "hyperliquid", hl_coin) or {}
        moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
        hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))
        if moex_mid is not None and hl_mid is not None:
            sp = hl_mid - moex_mid
            sp_pct = spread.current_spread_pct(hl_mid, moex_mid)

            database.insert_tick(
                contract_id=contract_id,
                timestamp_ms=int(time.time() * 1000),
                moex_mid=round(moex_mid, 4),
                hl_mid=round(hl_mid, 4),
                spread=round(sp, 4),
                spread_pct=round(sp_pct, 4),
                zscore=None,
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
                        _load_alor_history(contract["id"], contract["moex_symbol"], contract.get("asset", "brent"), tf)
                        _load_hl_historical(contract["id"], contract["hl_coin"], tf)
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
def _contract_start_ms(contract: dict, now_ms: int | None = None) -> int:
    """Return timestamp for the start of historical data loading.

    - Monthly contracts (Brent): 1st day of contract month
    - Quarterly contracts (Gold, Silver): contract_start_date from config
    - If date is in the future, fall back to 7-day lookback
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # Quarterly contracts: use contract_start_date (ISO format)
    start_date = contract.get("contract_start_date")
    if start_date:
        try:
            dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
            start_ms = int(dt.timestamp() * 1000)
            if start_ms > now_ms:
                return now_ms - 7 * 24 * 60 * 60 * 1000
            return start_ms
        except ValueError:
            pass  # fall through to month-based logic

    # Monthly contracts: 1st day of contract month
    month = contract.get("contract_month")
    year = contract.get("contract_year")
    if not month or not year:
        return now_ms - 7 * 24 * 60 * 60 * 1000
    dt = datetime.datetime(year, month, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    start_ms = int(dt.timestamp() * 1000)
    if start_ms > now_ms:
        return now_ms - 7 * 24 * 60 * 60 * 1000
    return start_ms


def _load_hl_historical(contract_id: str, hl_coin: str, timeframe: str) -> None:
    """Incrementally fetch and store HL historical candles."""
    now_ms = int(time.time() * 1000)
    contract = database.get_contract(contract_id) or {}
    start_ms = _contract_start_ms(contract)
    last_hl = database.get_last_timestamp(contract_id, "hyperliquid", hl_coin, timeframe)
    from_hl = (last_hl + 1) if last_hl else start_ms
    from_hl = max(from_hl, start_ms)
    if from_hl < now_ms:
        hl_candles = hl_client.fetch_historical(hl_coin, timeframe, from_hl, now_ms)
        if hl_candles:
            rows = [
                (contract_id, "hyperliquid", hl_coin, timeframe, c["timestamp_ms"], c["close"])
                for c in hl_candles
            ]
            database.insert_candles_batch(rows)


def _load_alor_history(contract_id: str, moex_symbol: str, asset: str, timeframe: str) -> None:
    """Load full previous+current Alor history if not already present."""
    clean_symbol = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_symbol, timeframe):
        return
    try:
        result = alor_history.load_full_history(contract_id, clean_symbol, asset, timeframe)
        logger.info("Alor history loaded for %s %s: %d candles", contract_id, timeframe, result["loaded"])
    except Exception as exc:
        logger.warning("Alor history load failed for %s %s: %s", contract_id, timeframe, exc)


def _get_moex_series(contract_id: str, moex_symbol: str, timeframe: str, from_ms: int | None = None, limit: int = 1500) -> list[dict]:
    """Return MOEX series, preferring alor_candles over legacy candles."""
    clean_symbol = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_symbol, timeframe):
        return database.get_alor_candles_recent(contract_id, clean_symbol, timeframe, from_ms=from_ms, limit=limit)
    return database.get_candles_recent(contract_id, "moex", moex_symbol, timeframe, from_ms=from_ms, limit=limit)


# ---------------------------------------------------------------------------
# API endpoints — Contracts
# ---------------------------------------------------------------------------
@app.get("/api/contracts")
def get_contracts():
    """Return all contracts."""
    return database.get_contracts()


@app.get("/api/assets")
def get_assets():
    """Return asset configuration."""
    from config import ASSETS
    return ASSETS


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

    cache_key = f"hist:{contract_id}:{timeframe}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    clean_sym = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_sym, timeframe):
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=None, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=None, limit=10000)
    else:
        start_ms = _contract_start_ms(contract)
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=start_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=start_ms, limit=10000)
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
    _API_CACHE.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# API endpoints — Raw prices
# ---------------------------------------------------------------------------
@app.get("/api/prices/{contract_id}/{timeframe}")
def get_prices(contract_id: str, timeframe: str):
    """Return synchronized raw MOEX and Hyperliquid close prices."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    cache_key = f"prices:{contract_id}:{timeframe}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    clean_sym = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_sym, timeframe):
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=None, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=None, limit=10000)
    else:
        start_ms = _contract_start_ms(contract)
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=start_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=start_ms, limit=10000)
    synced = sync.strict_sync(moex, hl)

    result = []
    for row in synced:
        result.append(
            {
                "timestamp_ms": row["timestamp_ms"],
                "moex_close": round(row["moex_close"], 4) if row["moex_close"] is not None else None,
                "hl_close": round(row["hl_close"], 4) if row["hl_close"] is not None else None,
            }
        )
    _API_CACHE.set(cache_key, result)
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

    moex = database.get_current(contract_id, "moex", moex_symbol) or {}
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

    cache_key = f"zscore:{contract_id}:{timeframe}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    clean_sym = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_sym, timeframe):
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=None, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=None, limit=10000)
    else:
        start_ms = _contract_start_ms(contract)
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=start_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=start_ms, limit=10000)
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
    _API_CACHE.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# API endpoints — Statistics
# ---------------------------------------------------------------------------
@app.get("/api/stats/{contract_id}/{timeframe}")
def get_stats(contract_id: str, timeframe: str):
    """Return full statistics for the spread series."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    cache_key = f"stats:{contract_id}:{timeframe}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    clean_sym = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_sym, timeframe):
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=None, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=None, limit=10000)
    else:
        start_ms = _contract_start_ms(contract)
        moex = _get_moex_series(contract_id, moex_symbol, timeframe, from_ms=start_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, timeframe, from_ms=start_ms, limit=10000)
    synced = sync.strict_sync(moex, hl)

    spread_values = []
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            spread_values.append(sp)

    result = stats_module.compute_all(spread_values)
    _API_CACHE.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# API endpoints — Signal
# ---------------------------------------------------------------------------
@app.get("/api/signal/{contract_id}")
def get_signal(contract_id: str):
    """Return current entry signal based on latest spread and stats."""
    cache_key = f"signal:{contract_id}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    moex_symbol = contract["moex_symbol"]
    hl_coin = contract["hl_coin"]

    # Get current spread
    moex = database.get_current(contract_id, "moex", moex_symbol) or {}
    hl = database.get_current(contract_id, "hyperliquid", hl_coin) or {}
    moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
    hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))

    if moex_mid is None or hl_mid is None:
        return {"signal": "neutral", "zscore": None, "description": "Нет данных"}

    cur_spread = spread.current_spread_pct(hl_mid, moex_mid)

    # Get stats from 5m for signal calculation
    clean_sym = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_sym, "5m"):
        moex_c = _get_moex_series(contract_id, moex_symbol, "5m", from_ms=None, limit=10000)
        hl_c = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, "5m", from_ms=None, limit=10000)
    else:
        start_ms = _contract_start_ms(contract)
        moex_c = _get_moex_series(contract_id, moex_symbol, "5m", from_ms=start_ms, limit=10000)
        hl_c = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, "5m", from_ms=start_ms, limit=10000)
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
    _API_CACHE.set(cache_key, sig, ttl=10.0)
    return sig


# ---------------------------------------------------------------------------
# API endpoints — Ticks
# ---------------------------------------------------------------------------
@app.get("/api/ticks/{contract_id}")
def get_ticks(contract_id: str, limit: int = 50):
    """Return latest ticks for the contract."""
    return database.get_ticks(contract_id, limit)


# ---------------------------------------------------------------------------
# API endpoints — Full History Load (Alor)
# ---------------------------------------------------------------------------
@app.post("/api/history/load/{contract_id}")
def load_history(contract_id: str, timeframe: str = "15m"):
    """Load full previous+current contract history from Alor."""
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    symbol = contract["moex_symbol"].split("@")[0]
    asset = contract.get("asset", "brent")

    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")

    result = alor_history.load_full_history(contract_id, symbol, asset, timeframe)
    return {
        "status": "ok",
        "contract_id": contract_id,
        "timeframe": timeframe,
        "loaded": result["loaded"],
        "previous_candles": result["previous"],
        "current_candles": result["current"],
        "previous_contract": result["prev_contract"],
        "current_contract": result["curr_contract"],
    }


@app.get("/api/history/alor/{contract_id}/{timeframe}")
def get_alor_history(contract_id: str, timeframe: str):
    """Return merged Alor OHLCV history for a contract."""
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    symbol = contract["moex_symbol"].split("@")[0]
    candles = database.get_alor_candles(contract_id, symbol, timeframe)
    return candles


# ---------------------------------------------------------------------------
# API endpoints — Paper Trading
# ---------------------------------------------------------------------------
@app.get("/api/paper/settings")
def get_paper_settings():
    """Return paper trading settings."""
    return database.get_paper_settings()


class PaperSettingsUpdate(BaseModel):
    deposit: float | None = None
    leverage: int | None = None
    entry_levels: str | None = None
    max_hold_days: int | None = None
    hard_stop: float | None = None
    cooldown_days: int | None = None
    moex_fee: float | None = None
    hl_fee: float | None = None
    slippage: float | None = None
    lookback_days: int | None = None
    mode: str | None = None
    include_funding: int | None = None


@app.post("/api/paper/settings")
def post_paper_settings(body: PaperSettingsUpdate):
    """Update paper trading settings."""
    database.update_paper_settings(
        deposit=body.deposit,
        leverage=body.leverage,
        entry_levels=body.entry_levels,
        max_hold_days=body.max_hold_days,
        hard_stop=body.hard_stop,
        cooldown_days=body.cooldown_days,
        moex_fee=body.moex_fee,
        hl_fee=body.hl_fee,
        slippage=body.slippage,
        lookback_days=body.lookback_days,
        mode=body.mode,
        include_funding=body.include_funding,
    )
    return {"status": "ok"}


@app.get("/api/paper/trades/{contract_id}")
def get_paper_trades(contract_id: str, status: str | None = None, limit: int = 500):
    """Return paper trades for a contract."""
    return database.get_paper_trades(contract_id, status=status, limit=limit)


class PaperEntry(BaseModel):
    contract_id: str
    side: str
    entry_timestamp_ms: int
    entry_level: float
    entry_deviation: float
    entry_spread: float
    entry_moex: float
    entry_hl: float
    size: float
    entry_fees: float


class PaperExit(BaseModel):
    exit_timestamp_ms: int
    exit_spread: float
    exit_moex: float
    exit_hl: float
    days_held: float
    exit_reason: str
    gross_pnl: float
    funding_total: float
    exit_fees: float
    net_pnl: float


class PaperEquityPoint(BaseModel):
    timestamp_ms: int
    equity: float


@app.post("/api/paper/trades/entry")
def post_paper_entry(body: PaperEntry):
    """Open a new paper trade."""
    trade_id = database.insert_paper_trade(
        contract_id=body.contract_id,
        side=body.side,
        entry_timestamp_ms=body.entry_timestamp_ms,
        entry_level=body.entry_level,
        entry_deviation=body.entry_deviation,
        entry_spread=body.entry_spread,
        entry_moex=body.entry_moex,
        entry_hl=body.entry_hl,
        size=body.size,
        entry_fees=body.entry_fees,
    )
    return {"status": "ok", "trade_id": trade_id}


@app.post("/api/paper/trades/exit/{trade_id}")
def post_paper_exit(trade_id: int, body: PaperExit):
    """Close a paper trade."""
    database.close_paper_trade(
        trade_id=trade_id,
        exit_timestamp_ms=body.exit_timestamp_ms,
        exit_spread=body.exit_spread,
        exit_moex=body.exit_moex,
        exit_hl=body.exit_hl,
        days_held=body.days_held,
        exit_reason=body.exit_reason,
        gross_pnl=body.gross_pnl,
        funding_total=body.funding_total,
        exit_fees=body.exit_fees,
        net_pnl=body.net_pnl,
    )
    return {"status": "ok"}


@app.get("/api/paper/active/{contract_id}")
def get_paper_active(contract_id: str):
    """Return active (open) paper trade for a contract."""
    trade = database.get_active_paper_trade(contract_id)
    if not trade:
        return None
    return trade


@app.get("/api/paper/equity/{contract_id}")
def get_paper_equity(contract_id: str, limit: int = 5000):
    """Return equity curve for a contract."""
    return database.get_paper_equity(contract_id, limit=limit)


@app.post("/api/paper/equity/{contract_id}")
def post_paper_equity(contract_id: str, body: PaperEquityPoint):
    """Record an equity snapshot."""
    database.insert_paper_equity(contract_id, body.timestamp_ms, body.equity)
    return {"status": "ok"}


@app.get("/api/paper/summary/{contract_id}")
def get_paper_summary(contract_id: str):
    """Return paper trading summary stats."""
    return database.get_paper_summary(contract_id)


class PaperReset(BaseModel):
    contract_id: str | None = None


@app.post("/api/paper/reset")
def post_paper_reset(body: PaperReset | None = None):
    """Reset paper trading data."""
    cid = body.contract_id if body else None
    database.delete_all_paper_trades(contract_id=cid)
    return {"status": "ok"}


@app.get("/api/funding/{contract_id}")
def get_funding_history(contract_id: str, start_ms: int | None = None):
    """Return Hyperliquid funding history for a contract."""
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    hl_coin = contract["hl_coin"]
    if start_ms is None:
        start_ms = int(time.time() * 1000) - 30 * 24 * 60 * 60 * 1000  # 30 days back

    end_ms = int(time.time() * 1000)
    history = hl_client.fetch_funding_history(hl_coin, start_ms, end_ms)
    return {"contract_id": contract_id, "coin": hl_coin, "count": len(history), "history": history}


# ---------------------------------------------------------------------------
# Pydantic models for Funding Calculator
# ---------------------------------------------------------------------------
class FundingCalcRequest(BaseModel):
    from_ms: int
    to_ms: int
    side: str  # "long" | "short" | "auto"
    position_size: float = 9000.0


# ---------------------------------------------------------------------------
# Helpers for Funding
# ---------------------------------------------------------------------------
def _brent_only_check(contract: dict) -> None:
    if contract.get("asset") != "brent":
        raise HTTPException(status_code=400, detail="Funding analytics only available for Brent contracts")


def _aggregate_funding_by_day(funding: list[dict]) -> list[dict]:
    """Aggregate hourly funding records by calendar day."""
    daily: dict = {}
    for entry in funding:
        ts = entry["timestamp_ms"]
        dt = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)
        day_key = dt.strftime("%Y-%m-%d")
        if day_key not in daily:
            daily[day_key] = {"rates": [], "sum": 0.0}
        daily[day_key]["rates"].append(entry["rate"])
        daily[day_key]["sum"] += entry["rate"]
    result = []
    for day in sorted(daily.keys()):
        result.append({"date": day, "rate_sum": daily[day]["sum"], "count": len(daily[day]["rates"])})
    return result


def _get_spread_for_funding(contract_id: str, moex_symbol: str, hl_coin: str, from_ms: int, to_ms: int) -> dict[int, float]:
    """Return dict {timestamp_ms: spread_pct} aligned to hour for auto-mode."""
    clean_sym = moex_symbol.split("@")[0]
    tf = "60m"
    if database.has_alor_candles(contract_id, clean_sym, tf):
        moex = _get_moex_series(contract_id, moex_symbol, tf, from_ms=from_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, tf, from_ms=from_ms, limit=10000)
    else:
        start_ms = _contract_start_ms(database.get_contract(contract_id))
        moex = _get_moex_series(contract_id, moex_symbol, tf, from_ms=start_ms, limit=10000)
        hl = database.get_candles_recent(contract_id, "hyperliquid", hl_coin, tf, from_ms=start_ms, limit=10000)
    synced = sync.strict_sync(moex, hl)
    result: dict[int, float] = {}
    for row in synced:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        if sp is not None:
            ts = row["timestamp_ms"]
            hour_ts = ts - (ts % (60 * 60 * 1000))
            result[hour_ts] = sp
    return result


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n == 0:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    den_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _autocorr(series: list[float], lag: int = 1) -> float:
    if len(series) <= lag:
        return 0.0
    x = series[:-lag]
    y = series[lag:]
    return _pearson(x, y)


# ---------------------------------------------------------------------------
# API endpoint — Funding Summary (Monitor tab)
# ---------------------------------------------------------------------------
@app.get("/api/funding/summary/{contract_id}")
def get_funding_summary(contract_id: str, position_size: float = 9000.0):
    cache_key = f"funding_summary:{contract_id}:{position_size}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    _brent_only_check(contract)

    hl_coin = contract["hl_coin"]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 60 * 60 * 1000

    history = hl_client.fetch_funding_history_paginated(hl_coin, start_ms, now_ms)
    if not history:
        return {
            "contract_id": contract_id,
            "current_rate": None,
            "current_annualized": None,
            "next_payment_ms": None,
            "last_24h_sum": None,
            "last_24h_usd": None,
            "last_7d_avg_daily": None,
            "positive_pct": None,
            "history_24h": [],
            "history_7d_daily": [],
        }

    history.sort(key=lambda x: x["timestamp_ms"])
    current_rate = history[-1]["rate"]
    current_annualized = current_rate * 24 * 365
    next_hour = ((now_ms // (60 * 60 * 1000)) + 1) * (60 * 60 * 1000)

    last_24h_start = now_ms - 24 * 60 * 60 * 1000
    last_24h = [h for h in history if h["timestamp_ms"] >= last_24h_start]
    last_24h_sum = sum(h["rate"] for h in last_24h)
    last_24h_usd = last_24h_sum * position_size

    daily = _aggregate_funding_by_day(history)
    last_7d_avg_daily = sum(d["rate_sum"] for d in daily) / len(daily) if daily else 0.0
    positive_count = sum(1 for h in history if h["rate"] > 0)
    positive_pct = (positive_count / len(history) * 100) if history else 0.0

    history_24h = [{"timestamp_ms": h["timestamp_ms"], "rate": h["rate"]} for h in last_24h]
    history_7d_daily = [{"date": d["date"], "rate_sum": d["rate_sum"], "positive": d["rate_sum"] > 0} for d in daily]

    result = {
        "contract_id": contract_id,
        "current_rate": round(current_rate, 6),
        "current_annualized": round(current_annualized, 4),
        "next_payment_ms": next_hour,
        "last_24h_sum": round(last_24h_sum, 6),
        "last_24h_usd": round(last_24h_usd, 2),
        "last_7d_avg_daily": round(last_7d_avg_daily, 6),
        "positive_pct": round(positive_pct, 1),
        "history_24h": history_24h,
        "history_7d_daily": history_7d_daily,
    }
    _API_CACHE.set(cache_key, result, ttl=60.0)
    return result


# ---------------------------------------------------------------------------
# API endpoint — Funding Calculator
# ---------------------------------------------------------------------------
@app.post("/api/funding/calc/{contract_id}")
def post_funding_calc(contract_id: str, body: FundingCalcRequest):
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    _brent_only_check(contract)

    if body.side not in ("long", "short", "auto"):
        raise HTTPException(status_code=400, detail="side must be long, short, or auto")

    hl_coin = contract["hl_coin"]
    funding = hl_client.fetch_funding_history_paginated(hl_coin, body.from_ms, body.to_ms)
    if not funding:
        raise HTTPException(status_code=400, detail="No funding data for selected period")

    funding.sort(key=lambda x: x["timestamp_ms"])

    side_per_hour: dict[int, str] = {}
    if body.side == "auto":
        spread_map = _get_spread_for_funding(contract_id, contract["moex_symbol"], hl_coin, body.from_ms, body.to_ms)
        if spread_map:
            spread_values = list(spread_map.values())
            mean_spread = sum(spread_values) / len(spread_values)
            for ts, sp in spread_map.items():
                side_per_hour[ts] = "short" if sp > mean_spread else "long"

    hourly_result = []
    daily: dict = {}
    for entry in funding:
        ts = entry["timestamp_ms"]
        rate = entry["rate"]
        dt = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)
        day_key = dt.strftime("%Y-%m-%d")

        if body.side == "auto":
            hour_ts = ts - (ts % (60 * 60 * 1000))
            side = side_per_hour.get(hour_ts, "long")
        else:
            side = body.side

        sign = 1.0 if side == "short" else -1.0
        payment = body.position_size * rate * sign

        hourly_result.append({"timestamp_ms": ts, "rate": rate, "side": side, "payment": round(payment, 4)})

        if day_key not in daily:
            daily[day_key] = {"date": day_key, "rate_sum": 0.0, "payment_sum": 0.0, "count": 0, "signal": side}
        daily[day_key]["rate_sum"] += rate
        daily[day_key]["payment_sum"] += payment
        daily[day_key]["count"] += 1
        if daily[day_key]["signal"] != side:
            daily[day_key]["signal"] = "mixed"

    daily_breakdown = sorted(daily.values(), key=lambda x: x["date"])
    running = 0.0
    for d in daily_breakdown:
        running += d["payment_sum"]
        d["running_total"] = round(running, 2)

    total_funding = sum(d["payment_sum"] for d in daily_breakdown)
    avg_daily = total_funding / len(daily_breakdown) if daily_breakdown else 0.0
    best_day = max(daily_breakdown, key=lambda x: x["payment_sum"]) if daily_breakdown else None
    worst_day = min(daily_breakdown, key=lambda x: x["payment_sum"]) if daily_breakdown else None

    return {
        "contract_id": contract_id,
        "position_size": body.position_size,
        "side": body.side,
        "total_funding": round(total_funding, 2),
        "avg_daily": round(avg_daily, 2),
        "best_day": {"date": best_day["date"], "payment": round(best_day["payment_sum"], 2), "rate_sum": round(best_day["rate_sum"], 6)} if best_day else None,
        "worst_day": {"date": worst_day["date"], "payment": round(worst_day["payment_sum"], 2), "rate_sum": round(worst_day["rate_sum"], 6)} if worst_day else None,
        "daily_breakdown": daily_breakdown,
        "hourly": hourly_result,
    }


# ---------------------------------------------------------------------------
# API endpoint — Funding Analytics
# ---------------------------------------------------------------------------
@app.get("/api/funding/analytics/{contract_id}")
def get_funding_analytics(contract_id: str):
    cache_key = f"funding_analytics:{contract_id}"
    cached = _API_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    _brent_only_check(contract)

    hl_coin = contract["hl_coin"]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 30 * 24 * 60 * 60 * 1000

    funding = hl_client.fetch_funding_history_paginated(hl_coin, start_ms, now_ms)
    if not funding:
        raise HTTPException(status_code=400, detail="No funding data")

    funding.sort(key=lambda x: x["timestamp_ms"])
    rates = [h["rate"] for h in funding]

    import statistics
    positive_count = sum(1 for r in rates if r > 0)
    negative_count = len(rates) - positive_count
    positive_pct = (positive_count / len(rates) * 100) if rates else 0.0
    negative_pct = (negative_count / len(rates) * 100) if rates else 0.0

    hourly_std = statistics.stdev(rates) if len(rates) > 1 else 0.0
    hourly_min = min(rates) if rates else 0.0
    hourly_max = max(rates) if rates else 0.0
    hourly_mean = sum(rates) / len(rates) if rates else 0.0
    autocorr_1h = _autocorr(rates, lag=1)

    spread_map = _get_spread_for_funding(contract_id, contract["moex_symbol"], hl_coin, start_ms, now_ms)
    correlation = 0.0
    spread_mean = 0.0
    if spread_map:
        matched_rates = []
        matched_spreads = []
        for h in funding:
            ts = h["timestamp_ms"]
            hour_ts = ts - (ts % (60 * 60 * 1000))
            if hour_ts in spread_map:
                matched_rates.append(h["rate"])
                matched_spreads.append(spread_map[hour_ts])
        if matched_rates and matched_spreads:
            correlation = _pearson(matched_rates, matched_spreads)
            spread_mean = sum(matched_spreads) / len(matched_spreads)

    heatmap: list[list[list[float]]] = [[[] for _ in range(7)] for _ in range(24)]
    for h in funding:
        ts = h["timestamp_ms"]
        dt = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)
        heatmap[dt.hour][dt.weekday()].append(h["rate"])

    heatmap_avg = [[round(sum(heatmap[h][d]) / len(heatmap[h][d]), 6) if heatmap[h][d] else 0.0 for d in range(7)] for h in range(24)]

    result = {
        "contract_id": contract_id,
        "positive_pct": round(positive_pct, 1),
        "negative_pct": round(negative_pct, 1),
        "hourly_std": round(hourly_std, 6),
        "hourly_min": round(hourly_min, 6),
        "hourly_max": round(hourly_max, 6),
        "hourly_mean": round(hourly_mean, 6),
        "autocorr_1h": round(autocorr_1h, 3),
        "correlation_with_spread": round(correlation, 3),
        "spread_mean": round(spread_mean, 4),
        "hourly_heatmap": heatmap_avg,
        "sample_count": len(rates),
    }
    _API_CACHE.set(cache_key, result, ttl=300.0)
    return result
