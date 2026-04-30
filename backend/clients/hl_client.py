"""
Hyperliquid API client.

All timestamps are Unix ms UTC (Hyperliquid natively uses this format).
"""

import logging
import time
from typing import Optional

import httpx

from config import HL_API_URL

logger = logging.getLogger(__name__)

TIME_FRAME_MAP = {
    "5m": "5m",
    "15m": "15m",
    "60m": "1h",
}

_CLIENT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _post(payload: dict) -> Optional[dict]:
    """Raw POST to HL info endpoint. Returns parsed JSON or None on failure."""
    try:
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.post(HL_API_URL, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Hyperliquid request failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Historical candles
# ---------------------------------------------------------------------------
def fetch_historical(
    coin: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """
    Fetch candles from Hyperliquid.
    coin: e.g. "xyz:BRENTOIL"
    Returns list of {"timestamp_ms": int, "close": float}.
    """
    if timeframe not in TIME_FRAME_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    interval = TIME_FRAME_MAP[timeframe]

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    data = _post(payload)
    if data is None:
        return []

    if not isinstance(data, list):
        logger.warning("Unexpected HL candle response format: %s", type(data))
        return []

    results: list[dict] = []
    for candle in data:
        ts = candle.get("t")
        close_str = candle.get("c")
        if ts is None or close_str is None:
            continue
        try:
            results.append(
                {
                    "timestamp_ms": int(ts),
                    "close": float(close_str),
                }
            )
        except (ValueError, TypeError):
            continue

    return results


# ---------------------------------------------------------------------------
# Current prices
# ---------------------------------------------------------------------------
def fetch_current(coin: str) -> dict:
    """
    Fetch current HL price snapshot.
    coin: e.g. "xyz:BRENTOIL"
    Primary source: l2Book (best bid / best ask).
    Fallback: allMids (treated as last_price, not true bid/ask).

    Returns dict with keys:
        best_bid, best_ask, last_price, updated_ms, is_l2
    """
    snapshot: dict = {
        "best_bid": None,
        "best_ask": None,
        "last_price": None,
        "updated_ms": int(time.time() * 1000),
        "is_l2": False,
    }

    # 1) l2Book
    l2_payload = {"type": "l2Book", "coin": coin}
    l2_data = _post(l2_payload)
    if l2_data and isinstance(l2_data, dict):
        levels = l2_data.get("levels")
        if (
            isinstance(levels, list)
            and len(levels) >= 2
            and len(levels[0]) > 0
            and len(levels[1]) > 0
        ):
            try:
                best_bid = float(levels[0][0]["px"])
                best_ask = float(levels[1][0]["px"])
                snapshot["best_bid"] = best_bid
                snapshot["best_ask"] = best_ask
                snapshot["is_l2"] = True
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("HL l2Book parse error: %s", exc)

    # 2) allMids fallback (useful if l2 empty or for sanity check)
    if not snapshot["is_l2"]:
        am_payload = {"type": "allMids"}
        am_data = _post(am_payload)
        if am_data and isinstance(am_data, dict):
            mid_str = am_data.get(coin)
            if mid_str is not None:
                try:
                    snapshot["last_price"] = float(mid_str)
                except (ValueError, TypeError):
                    pass

    return snapshot
