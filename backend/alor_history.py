"""Alor historical data loader with previous+current contract periods."""

import logging
import time
from datetime import datetime, timezone
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
        return datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _start_of_quarter(year: int, quarter: int) -> datetime:
    months = [1, 4, 7, 10]
    return datetime(year, months[quarter], 1, 0, 0, 0, tzinfo=timezone.utc)


def _end_of_quarter(year: int, quarter: int) -> datetime:
    months = [1, 4, 7, 10]
    if quarter == 3:
        return datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return datetime(year, months[quarter + 1], 1, 0, 0, 0, tzinfo=timezone.utc)


def get_periods(current_contract: str, asset: str) -> dict:
    """Return previous and current date ranges for history loading.
    
    Brent monthly:
      - previous: full month (M-2) from expired contract
      - current: month (M-1) from active contract
      
    Gold/Silver quarterly:
      - previous: full previous quarter from expired contract  
      - current: current quarter from active contract
    """
    month_letter = current_contract[-2]
    year_digit = int(current_contract[-1])
    import datetime as _dt
    current_decade = (_dt.datetime.now().year // 10) * 10
    year = current_decade + year_digit
    
    if asset == "brent":
        exp_month = _month_index(month_letter) + 1  # 1-12
        
        # Previous period: month M-2 (full calendar month)
        prev_month = exp_month - 2
        prev_year = year
        if prev_month <= 0:
            prev_month += 12
            prev_year -= 1
        
        # Current period: month M-1 (from start to now)
        curr_month = exp_month - 1
        curr_year = year
        if curr_month <= 0:
            curr_month += 12
            curr_year -= 1
        
        prev_contract = get_previous_contract(current_contract, asset)
        
        return {
            "previous": {
                "contract": prev_contract,
                "from_dt": _start_of_month(prev_year, prev_month),
                "to_dt": _end_of_month(prev_year, prev_month),
            },
            "current": {
                "contract": current_contract,
                "from_dt": _start_of_month(curr_year, curr_month),
                "to_dt": datetime.now(timezone.utc),
            },
        }
    else:
        # Quarterly
        exp_month = _quarter_index(month_letter)  # 0-3 (H=0, M=1, U=2, Z=3)
        
        # Previous quarter
        prev_quarter = exp_month - 1
        prev_year = year
        if prev_quarter < 0:
            prev_quarter = 3
            prev_year -= 1
        
        # Current quarter
        curr_quarter = exp_month
        
        prev_contract = get_previous_contract(current_contract, asset)
        
        return {
            "previous": {
                "contract": prev_contract,
                "from_dt": _start_of_quarter(prev_year, prev_quarter),
                "to_dt": _end_of_quarter(prev_year, prev_quarter),
            },
            "current": {
                "contract": current_contract,
                "from_dt": _start_of_quarter(year, curr_quarter),
                "to_dt": datetime.now(timezone.utc),
            },
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
    symbol: str,
    asset: str,
    timeframe: str,
) -> dict:
    """Load previous + current contract history and merge.
    Returns {loaded: int, previous: int, current: int}
    """
    periods = get_periods(symbol, asset)
    
    # Delete old data for this contract+timeframe
    database.delete_alor_candles(contract_id, symbol, timeframe)
    
    total_loaded = 0
    
    # Load previous contract (expired, untraded=true)
    prev = periods["previous"]
    prev_candles = fetch_alor_ohlcv(
        prev["contract"], timeframe,
        _dt_to_ms(prev["from_dt"]), _dt_to_ms(prev["to_dt"]),
        untraded=True,
    )
    if prev_candles:
        rows = [
            (contract_id, symbol, timeframe, c["timestamp_ms"],
             c["open"], c["high"], c["low"], c["close"], c["volume"], 1)
            for c in prev_candles
        ]
        database.insert_alor_candles_batch(rows)
        total_loaded += len(rows)
        logger.info("Loaded %d previous candles for %s (%s)", len(rows), contract_id, prev["contract"])
    
    # Load current contract (active, untraded=false)
    curr = periods["current"]
    curr_candles = fetch_alor_ohlcv(
        curr["contract"], timeframe,
        _dt_to_ms(curr["from_dt"]), _dt_to_ms(curr["to_dt"]),
        untraded=False,
    )
    if curr_candles:
        rows = [
            (contract_id, symbol, timeframe, c["timestamp_ms"],
             c["open"], c["high"], c["low"], c["close"], c["volume"], 0)
            for c in curr_candles
        ]
        database.insert_alor_candles_batch(rows)
        total_loaded += len(rows)
        logger.info("Loaded %d current candles for %s (%s)", len(rows), contract_id, curr["contract"])
    
    return {
        "loaded": total_loaded,
        "previous": len(prev_candles),
        "current": len(curr_candles),
        "prev_contract": prev["contract"],
        "curr_contract": curr["contract"],
    }
