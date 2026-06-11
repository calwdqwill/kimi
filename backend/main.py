"""
FastAPI application — multi-contract Brent Spread Dashboard.

Run from the backend folder:
    uvicorn main:app --reload --port 8000
"""

import concurrent.futures
import datetime
import json
import logging
import re
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
from clients import alor_client, hl_client, telegram_client, rapira_client
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

# Telegram signal state
_telegram_state: dict[str, dict] = {}
_selected_contract_id: str | None = None

# Rapira cache
_rapira_cache: dict | None = None
_rapira_cache_ts: float = 0.0
_RAPIRA_TTL = 10.0  # seconds

_SIGNAL_LEVELS = [0.5, 1.0, 1.5]
_SIGNAL_COOLDOWN_MS = 5 * 60 * 1000  # 5 minutes


def _zone(spread_pct: float) -> int:
    """Return zone based on absolute spread percentage."""
    abs_sp = abs(spread_pct)
    if abs_sp < 0.5:
        return 0
    elif abs_sp < 1.0:
        return 1
    elif abs_sp < 1.5:
        return 2
    else:
        return 3


def _send_telegram_signal(contract_id: str, spread_pct: float, level: float) -> None:
    """Format and send a Telegram signal message."""
    emoji_map = {0.5: "🟡", 1.0: "🟢", 1.5: "🔴"}
    name_map = {0.5: "Жёлтый", 1.0: "Зелёный", 1.5: "Красный"}
    emoji = emoji_map.get(level, "⚪")
    name = name_map.get(level, "Неизвестный")
    text = f"{emoji} {contract_id.upper()} | Спред: {spread_pct:+.2f}% | Сигнал: {name}"
    telegram_client.send_message(text)


def _check_telegram_signal(contract_id: str, spread_pct: float) -> None:
    """
    Check if spread crossed a signal level and send Telegram alert if needed.
    Antispam: max 1 signal per level per 5 minutes per contract.
    Only signals for the currently selected contract.
    """
    global _telegram_state

    if _selected_contract_id and contract_id != _selected_contract_id:
        return

    zone = _zone(spread_pct)
    state = _telegram_state.get(contract_id)
    if state is None:
        _telegram_state[contract_id] = {"last_zone": zone, "last_fired_ms": {}}
        return

    old_zone = state["last_zone"]
    if old_zone == zone:
        return

    # Determine which levels were crossed
    if zone > old_zone:
        crossed_indices = range(old_zone, zone)
    else:
        crossed_indices = range(zone, old_zone)

    now_ms = int(time.time() * 1000)
    for i in crossed_indices:
        level = _SIGNAL_LEVELS[i]
        last_fired = state["last_fired_ms"].get(level, 0)
        if now_ms - last_fired >= _SIGNAL_COOLDOWN_MS:
            _send_telegram_signal(contract_id, spread_pct, level)
            state["last_fired_ms"][level] = now_ms

    state["last_zone"] = zone


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
_telegram_bot_thread: threading.Thread | None = None


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

            # Check Telegram signals
            _check_telegram_signal(contract_id, sp_pct)
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
# Telegram bot command loop
# ---------------------------------------------------------------------------
def _telegram_bot_loop() -> None:
    """Poll Telegram messages and respond to commands."""
    _stop_event.wait(5)
    offset = 0
    logger.info("Telegram bot loop started")
    while not _stop_event.is_set():
        try:
            updates = telegram_client.get_updates(offset)
            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                msg = update.get("message", {})
                raw_text = (msg.get("text") or "").strip()
                # Strip bot mention in groups: /spread@botname -> /spread
                text = re.sub(r"@\w+$", "", raw_text.strip().lower())
                chat_id = msg.get("chat", {}).get("id")
                if not text or not chat_id:
                    continue

                if text in ("/spread", "/спред", "спред"):
                    _reply_spread(chat_id)
                elif text in ("/all", "/все", "все"):
                    _reply_all_spreads(chat_id)
                elif text.startswith("/select "):
                    cid = text.split(maxsplit=1)[1].strip().lower()
                    _reply_select_contract(cid, chat_id)
                elif text in ("/help", "/помощь", "помощь", "?"):
                    _reply_help(chat_id)
        except Exception as exc:
            logger.debug("Telegram bot loop error: %s", exc)
        _stop_event.wait(5)


def _reply_spread(chat_id: int) -> None:
    global _selected_contract_id
    cid = _selected_contract_id
    if not cid:
        telegram_client.send_message("❌ Не выбран контракт. Используйте /select <contract_id>", chat_id=str(chat_id))
        return
    contract = database.get_contract(cid)
    if not contract:
        telegram_client.send_message(f"❌ Контракт {cid} не найден", chat_id=str(chat_id))
        return
    _send_current_spread(contract, chat_id)


def _send_current_spread(contract: dict, chat_id: int) -> None:
    cid = contract["id"]
    moex = database.get_current(cid, "moex", contract["moex_symbol"]) or {}
    hl = database.get_current(cid, "hyperliquid", contract["hl_coin"]) or {}
    moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
    hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))
    if moex_mid is None or hl_mid is None:
        telegram_client.send_message(f"⚠️ {cid.upper()}: нет данных", chat_id=str(chat_id))
        return
    sp_pct = spread.current_spread_pct(hl_mid, moex_mid)
    emoji = "🔴" if abs(sp_pct) >= 1.5 else "🟢" if abs(sp_pct) >= 1.0 else "🟡" if abs(sp_pct) >= 0.5 else "⚪"
    text = f"{emoji} <b>{cid.upper()}</b>\nСпред: <b>{sp_pct:+.2f}%</b>\nMOEX: {moex_mid:.4f}\nHL: {hl_mid:.4f}"
    telegram_client.send_message(text, chat_id=str(chat_id))


def _reply_all_spreads(chat_id: int) -> None:
    contracts = database.get_contracts()
    active = [c for c in contracts if c.get("is_active")]
    lines = ["📊 <b>Активные контракты</b>"]
    for contract in active:
        cid = contract["id"]
        moex = database.get_current(cid, "moex", contract["moex_symbol"]) or {}
        hl = database.get_current(cid, "hyperliquid", contract["hl_coin"]) or {}
        moex_mid = spread.mid(moex.get("best_bid"), moex.get("best_ask"))
        hl_mid = spread.mid(hl.get("best_bid"), hl.get("best_ask"))
        if moex_mid is not None and hl_mid is not None:
            sp_pct = spread.current_spread_pct(hl_mid, moex_mid)
            emoji = "🔴" if abs(sp_pct) >= 1.5 else "🟢" if abs(sp_pct) >= 1.0 else "🟡" if abs(sp_pct) >= 0.5 else "⚪"
            marker = " ✅" if cid == _selected_contract_id else ""
            lines.append(f"{emoji} {cid.upper()}: {sp_pct:+.2f}%{marker}")
        else:
            lines.append(f"⚪ {cid.upper()}: нет данных")
    telegram_client.send_message("\n".join(lines), chat_id=str(chat_id))


def _reply_select_contract(contract_id: str, chat_id: int) -> None:
    global _selected_contract_id
    contract = database.get_contract(contract_id)
    if not contract:
        telegram_client.send_message(f"❌ Контракт {contract_id} не найден", chat_id=str(chat_id))
        return
    if not contract.get("is_active"):
        telegram_client.send_message(f"⚠️ Контракт {contract_id} не активен", chat_id=str(chat_id))
        return
    _selected_contract_id = contract_id
    logger.info("Selected contract changed to: %s (via Telegram)", contract_id)
    telegram_client.send_message(f"✅ Выбран контракт: <b>{contract_id.upper()}</b>", chat_id=str(chat_id))


def _reply_help(chat_id: int) -> None:
    text = (
        "🤖 <b>Команды бота</b>\n\n"
        "/spread — текущий спред выбранного контракта\n"
        "/all — спреды всех активных контрактов\n"
        "/select &lt;id&gt; — выбрать контракт для сигналов\n"
        "/help — эта справка\n\n"
        f"Сейчас выбран: <b>{(_selected_contract_id or '—').upper()}</b>"
    )
    telegram_client.send_message(text, chat_id=str(chat_id))


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    database.init_db()
    global _poll_thread, _history_thread, _telegram_bot_thread, _selected_contract_id
    _stop_event.clear()

    # Initialize selected contract to first active one
    contracts = database.get_contracts()
    active = [c for c in contracts if c.get("is_active")]
    if active:
        _selected_contract_id = active[0]["id"]
        logger.info("Initial selected contract: %s", _selected_contract_id)

    _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    _poll_thread.start()
    _history_thread = threading.Thread(target=_history_loop, daemon=True)
    _history_thread.start()
    _telegram_bot_thread = threading.Thread(target=_telegram_bot_loop, daemon=True)
    _telegram_bot_thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop_event.set()
    if _poll_thread is not None:
        _poll_thread.join(timeout=3.0)
    if _history_thread is not None:
        _history_thread.join(timeout=5.0)
    if _telegram_bot_thread is not None:
        _telegram_bot_thread.join(timeout=3.0)


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
    """Load full or incremental Alor history."""
    clean_symbol = moex_symbol.split("@")[0]
    if not database.has_alor_candles(contract_id, clean_symbol, timeframe):
        try:
            result = alor_history.load_full_history(contract_id, clean_symbol, asset, timeframe)
            logger.info("Alor history loaded for %s %s: %d candles", contract_id, timeframe, result["loaded"])
        except Exception as exc:
            logger.warning("Alor history load failed for %s %s: %s", contract_id, timeframe, exc)
        return

    # Incremental: check if we're missing recent candles
    last_ts = database.get_last_alor_timestamp(contract_id, clean_symbol, timeframe)
    if not last_ts:
        return

    now_ms = int(time.time() * 1000)
    tf_sec = alor_history.TF_TO_SECONDS.get(timeframe, 900)
    if now_ms - last_ts > 2 * tf_sec * 1000:
        try:
            candles = alor_history.fetch_alor_ohlcv(
                clean_symbol, timeframe,
                last_ts + 1, now_ms,
                untraded=False,
            )
            if candles:
                rows = [
                    (contract_id, clean_symbol, timeframe, c["timestamp_ms"],
                     c["open"], c["high"], c["low"], c["close"], c["volume"], 0)
                    for c in candles
                ]
                database.insert_alor_candles_batch(rows)
                logger.info("Alor incremental for %s %s: +%d candles", contract_id, timeframe, len(rows))
        except Exception as exc:
            logger.warning("Alor incremental load failed for %s %s: %s", contract_id, timeframe, exc)


def _get_moex_series(contract_id: str, moex_symbol: str, timeframe: str, from_ms: int | None = None, limit: int = 1500) -> list[dict]:
    """Return MOEX series, preferring alor_candles over legacy candles."""
    clean_symbol = moex_symbol.split("@")[0]
    if database.has_alor_candles(contract_id, clean_symbol, timeframe):
        return database.get_alor_candles_recent(contract_id, clean_symbol, timeframe, from_ms=from_ms, limit=limit)
    return database.get_candles_recent(contract_id, "moex", moex_symbol, timeframe, from_ms=from_ms, limit=limit)


# ---------------------------------------------------------------------------
# API endpoints — Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health_check():
    """Check DB, Alor token and Hyperliquid API availability."""
    import time as _time
    result = {
        "status": "ok",
        "checks": {},
        "timestamp": int(_time.time()),
    }

    # 1. Database
    try:
        contracts = database.get_contracts()
        result["checks"]["database"] = {
            "status": "ok",
            "detail": f"{len(contracts)} contract(s)",
        }
    except Exception as exc:
        result["checks"]["database"] = {"status": "error", "detail": str(exc)}
        result["status"] = "degraded"

    # 2. Alor token
    try:
        jwt = alor_client._get_jwt()
        if jwt:
            result["checks"]["alor_token"] = {
                "status": "ok",
                "detail": "JWT valid",
            }
        else:
            result["checks"]["alor_token"] = {
                "status": "error",
                "detail": "Empty JWT",
            }
            result["status"] = "degraded"
    except Exception as exc:
        result["checks"]["alor_token"] = {"status": "error", "detail": str(exc)}
        result["status"] = "degraded"

    # 3. Hyperliquid API
    try:
        hl_data = hl_client._post({"type": "allMids"})
        if hl_data and isinstance(hl_data, dict) and len(hl_data) > 0:
            result["checks"]["hl_api"] = {
                "status": "ok",
                "detail": "API reachable",
            }
        else:
            result["checks"]["hl_api"] = {
                "status": "error",
                "detail": "Empty or invalid response",
            }
            result["status"] = "degraded"
    except Exception as exc:
        result["checks"]["hl_api"] = {"status": "error", "detail": str(exc)}
        result["status"] = "degraded"

    return result


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
    moex_last = moex.get("last_price")
    hl_bid = hl.get("best_bid")
    hl_ask = hl.get("best_ask")
    hl_last = hl.get("last_price")

    moex_mid = spread.mid(moex_bid, moex_ask)
    hl_mid = spread.mid(hl_bid, hl_ask)

    # Fallback to last_price if bid/ask are stale (>1% deviation)
    if moex_last and moex_mid and abs(moex_mid - moex_last) / moex_last > 0.01:
        moex_mid = moex_last
    if hl_last and hl_mid and abs(hl_mid - hl_last) / hl_last > 0.01:
        hl_mid = hl_last

    cur_spread = None
    arb = None
    if moex_mid is not None and hl_mid is not None:
        cur_spread = spread.current_spread_pct(hl_mid, moex_mid)
        # Only compute arb if bid/ask look reasonable (<1% from last)
        moex_bid_ok = not (moex_last and moex_bid and abs(moex_bid - moex_last) / moex_last > 0.01)
        moex_ask_ok = not (moex_last and moex_ask and abs(moex_ask - moex_last) / moex_last > 0.01)
        hl_bid_ok = not (hl_last and hl_bid and abs(hl_bid - hl_last) / hl_last > 0.01)
        hl_ask_ok = not (hl_last and hl_ask and abs(hl_ask - hl_last) / hl_last > 0.01)
        if moex_bid_ok and moex_ask_ok and hl_bid_ok and hl_ask_ok:
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


# ---------------------------------------------------------------------------
# API endpoints — Rapira USDT/RUB
# ---------------------------------------------------------------------------
@app.get("/api/rapira/usdt-rub")
def get_rapira_usdt_rub():
    """Return latest Rapira USDT/RUB spot price."""
    global _rapira_cache, _rapira_cache_ts
    now = time.time()
    if _rapira_cache is not None and now - _rapira_cache_ts < _RAPIRA_TTL:
        return _rapira_cache
    data = rapira_client.fetch_usdt_rub()
    if data is None:
        raise HTTPException(status_code=503, detail="Rapira unavailable")
    _rapira_cache = data
    _rapira_cache_ts = now
    return data


# ---------------------------------------------------------------------------
# API endpoints — Telegram
# ---------------------------------------------------------------------------
@app.get("/api/test-telegram")
def test_telegram(chat_id: str | None = None):
    """Send a test message to verify Telegram bot configuration."""
    text = "🧪 Тестовое сообщение от mo-ex.online | Бот работает корректно."
    ok = telegram_client.send_message(text, chat_id=chat_id)
    if ok:
        return {"status": "ok", "message": "Test message sent"}
    return {"status": "error", "message": "Failed to send test message. Check logs for details."}


@app.get("/api/selected-contract")
def get_selected_contract():
    """Return the currently selected contract for Telegram signals."""
    return {"selected_contract_id": _selected_contract_id}


@app.post("/api/selected-contract")
def set_selected_contract(contract_id: str):
    """Set the contract that should receive Telegram signals."""
    global _selected_contract_id
    contract = database.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if not contract.get("is_active"):
        raise HTTPException(status_code=400, detail="Contract is not active")
    _selected_contract_id = contract_id
    logger.info("Selected contract changed to: %s", contract_id)
    return {"status": "ok", "selected_contract_id": contract_id}
