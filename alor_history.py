"""Alor historical data loader with previous+current contract periods."""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from clients import alor_client
import database
from config import MONTH_LETTERS, QUARTER_LETTERS

logger = logging.getLogger(__name__)

TF_TO_SECONDS = {"5m": 300, "15m": 900, "60m": 3600}


def _month_index(letter: str) -> int:
    return MONTH_LETTERS.index(letter)


def _quarter_index(letter: str) -> int:
    return QUARTER_LETTERS.index(letter)


def get_previous_contract(current: str, asset: str) -> str:
    """Get previous contract code.
    Brent monthly: BRM6 -> BRK6
    Gold/Silver quarterly: GNM6 -> GNH6, GNH6 -> GNZ5
    """
    base = current[:-2]  # "BR" or "GN" or "S1"
    month_letter = current[-2]
    year_digit = int(current[-1])

    if asset == "brent":
        idx = _month_index(month_letter)
        if idx == 0:
            return f"{base}{MONTH_LETTERS[-1]}{year_digit - 1}"
        return f"{base}{MONTH_LETTERS[idx - 1]}{year_digit}"
    else:
        idx = _quarter_index(month_letter)
        if idx == 0:
            return f"{base}{QUARTER_LETTERS[-1]}{year_digit - 1}"
        return f"{base}{QUARTER_LETTERS[idx - 1]}{year_digit}"


def _start_of_month(year: int, month: int) -> datetime:
    return datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)


def _end_of_month(year: int, month: int) -> datetime:
    if month == 12:
        return datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)


def _start_of_quarter(year: int, quarter: int) -> datetime:
    months = [1, 4, 7, 10]
    return datetime(year, months[quarter], 1, 0, 0, 0, tzinfo=timezone.utc)


def _end_of_quarter(year: int, quarter: int) -> datetime:
    months = [1, 4, 7, 10]
    if quarter == 3:
        return datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return datetime(year, months[quarter + 1], 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)


def _resolve_year(year_digit: int) -> int:
    """Map single digit to full year (e.g. 6 -> 2026)."""
    current_year = datetime.now(timezone.utc).year
    current_decade = (current_year // 10) * 10
    year = current_decade + year_digit
    return year


def get_periods(contract_code: str, asset: str, is_active: bool = True) -> dict:
    """Return date range for history loading for a SPECIFIC contract.

    Brent monthly: 2 months from 1st of (expIdx-2) to end of (expIdx-1)
    Gold/Silver quarterly: full quarter
    """
    month_letter = contract_code[-2]
    year_digit = int(contract_code[-1])
    year = _resolve_year(year_digit)

    if asset == "brent":
        exp_idx = _month_index(month_letter)  # 0-11

        # Two months: (exp_idx - 2) to (exp_idx - 1)
        start_month_idx = exp_idx - 2
        end_month_idx = exp_idx - 1

        start_year = year
        if start_month_idx < 0:
            start_month_idx += 12
            start_year -= 1

        end_year = year
        if end_month_idx < 0:
            end_month_idx += 12
            end_year -= 1

        from_dt = _start_of_month(start_year, start_month_idx + 1)

        if is_active:
            to_dt = datetime.now(timezone.utc)
        else:
            to_dt = _end_of_month(end_year, end_month_idx + 1)

        return {
            "contract": contract_code,
            "from_dt": from_dt,
            "to_dt": to_dt,
        }
    else:
        # Quarterly
        exp_idx = _quarter_index(month_letter)  # 0-3

        from_dt = _start_of_quarter(year, exp_idx)

        if is_active:
            to_dt = datetime.now(timezone.utc)
        else:
            to_dt = _end_of_quarter(year, exp_idx)

        return {
            "contract": contract_code,
            "from_dt": from_dt,
            "to_dt": to_dt,
        }


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def fetch_alor_ohlcv(
    symbol: str,
    timeframe: str,
    from_ms: int,
    to_ms: int,
    untraded: bool = False,
) -> list[dict]:
    """Fetch OHLCV candles from Alor with all parameters."""
    tf_sec = TF_TO_SECONDS.get(timeframe, 300)

    # Use internal Alor client but with extended params
    from_sec = from_ms // 1000
    to_sec = to_ms // 1000

    import httpx
    from clients.alor_client import _auth_headers, _CLIENT_TIMEOUT, ALOR_API_URL

    url = f"{ALOR_API_URL}/md/v2/history"
    params = {
        "symbol": symbol,
        "exchange": "MOEX",
        "instrumentGroup": "RFUD",
        "tf": tf_sec,
        "from": from_sec,
        "to": to_sec,
        "format": "Slim",
        "splitAdjust": "false",
    }
    if untraded:
        params["untraded"] = "true"

    try:
        resp = httpx.get(url, headers=_auth_headers(), params=params, timeout=_CLIENT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Alor history request failed for %s: %s", symbol, exc)
        return []

    history = data.get("h", [])
    if not isinstance(history, list):
        logger.warning("Unexpected Alor response format for %s: %s", symbol, type(history))
        return []

    results = []
    for c in history:
        ts_sec = c.get("t")
        if ts_sec is None:
            continue
        results.append({
            "timestamp_ms": int(ts_sec) * 1000,
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": float(c.get("c", 0)),
            "volume": int(c.get("v", 0)),
        })

    return results


def load_full_history(
    contract_id: str,
    current_symbol: str,
    asset: str,
    timeframe: str,
) -> dict:
    """Load previous + current contract history and merge into alor_candles.

    All rows are stored under `current_symbol` so that `get_alor_candles`
    can return a single merged series by (contract_id, current_symbol).
    is_prev_contract flag distinguishes the source contract.
    """
    # Delete old data for this contract+timeframe
    database.delete_alor_candles(contract_id, current_symbol, timeframe)

    total_loaded = 0
    prev_contract = get_previous_contract(current_symbol, asset)

    # Load previous contract (expired, untraded=true)
    prev_period = get_periods(prev_contract, asset, is_active=False)
    logger.info(
        "Loading previous %s (%s) from %s to %s",
        contract_id, prev_period["contract"], prev_period["from_dt"], prev_period["to_dt"],
    )
    prev_candles = fetch_alor_ohlcv(
        prev_period["contract"], timeframe,
        _dt_to_ms(prev_period["from_dt"]), _dt_to_ms(prev_period["to_dt"]),
        untraded=True,
    )
    if prev_candles:
        rows = [
            (contract_id, current_symbol, timeframe, c["timestamp_ms"],
             c["open"], c["high"], c["low"], c["close"], c["volume"], 1)
            for c in prev_candles
        ]
        database.insert_alor_candles_batch(rows)
        total_loaded += len(rows)
        logger.info("Loaded %d previous candles for %s (%s)", len(rows), contract_id, prev_period["contract"])
    else:
        logger.warning("No previous candles returned for %s (%s)", contract_id, prev_period["contract"])

    # Load current contract (active, untraded=false)
    curr_period = get_periods(current_symbol, asset, is_active=True)
    logger.info(
        "Loading current %s (%s) from %s to %s",
        contract_id, curr_period["contract"], curr_period["from_dt"], curr_period["to_dt"],
    )
    curr_candles = fetch_alor_ohlcv(
        curr_period["contract"], timeframe,
        _dt_to_ms(curr_period["from_dt"]), _dt_to_ms(curr_period["to_dt"]),
        untraded=False,
    )
    if curr_candles:
        rows = [
            (contract_id, current_symbol, timeframe, c["timestamp_ms"],
             c["open"], c["high"], c["low"], c["close"], c["volume"], 0)
            for c in curr_candles
        ]
        database.insert_alor_candles_batch(rows)
        total_loaded += len(rows)
        logger.info("Loaded %d current candles for %s (%s)", len(rows), contract_id, curr_period["contract"])
    else:
        logger.warning("No current candles returned for %s (%s)", contract_id, curr_period["contract"])

    return {
        "loaded": total_loaded,
        "previous": len(prev_candles),
        "current": len(curr_candles),
        "prev_contract": prev_contract,
        "curr_contract": current_symbol,
    }
