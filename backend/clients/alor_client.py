"""
Alor OpenAPI V2 client.

Replaces Finam for MOEX data.
Time handling:
- All internal timestamps are Unix ms UTC.
- Alor expects Unix seconds UTC for interval boundaries and returns seconds in candles.
"""

import logging
import time
from typing import Optional

import httpx

from config import ALOR_API_URL, ALOR_OAUTH_URL, ALOR_REFRESH_TOKEN, ALOR_EXCHANGE

logger = logging.getLogger(__name__)

TIME_FRAME_MAP = {
    "5m": "300",
    "15m": "900",
    "60m": "3600",
}

_CLIENT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# ---------------------------------------------------------------------------
# JWT caching (TTL 25 min)
# ---------------------------------------------------------------------------
_jwt_cache: dict = {}
_JWT_TTL_SECONDS = 25 * 60


def _get_jwt() -> str:
    """Obtain (or refresh) JWT via OAuth refresh endpoint."""
    now = time.time()
    token = _jwt_cache.get("token")
    expires = _jwt_cache.get("expires_at", 0)
    if token and expires > now + 60:
        return token

    try:
        resp = httpx.post(
            f"{ALOR_OAUTH_URL}/refresh",
            params={"token": ALOR_REFRESH_TOKEN},
            timeout=_CLIENT_TIMEOUT,
            follow_redirects=True,
        )
        logger.info("Alor /refresh status: %s", resp.status_code)
        if resp.status_code == 401:
            logger.error("Alor 401 Unauthorized — refresh token may be invalid or expired")
            raise RuntimeError("Alor JWT auth failed (401). Check ALOR_REFRESH_TOKEN in .env")
        resp.raise_for_status()
        data = resp.json()
        new_token = data["AccessToken"]
        _jwt_cache["token"] = new_token
        _jwt_cache["expires_at"] = now + _JWT_TTL_SECONDS
        return new_token
    except Exception as exc:
        logger.error("Failed to obtain Alor JWT: %s", exc)
        raise


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_get_jwt()}", "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------
def _normalize_symbol(symbol: str) -> str:
    """Strip Finam-style exchange suffixes (e.g. BMM6@RTSX -> BMM6)."""
    if "@" in symbol:
        return symbol.split("@")[0]
    return symbol


# ---------------------------------------------------------------------------
# Historical candles
# ---------------------------------------------------------------------------
def fetch_historical(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """
    Fetch candles from Alor.
    symbol: e.g. "BMM6" or "BMM6@RTSX" (suffix stripped automatically)
    Returns list of {"timestamp_ms": int, "close": float}.
    """
    if timeframe not in TIME_FRAME_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    interval = TIME_FRAME_MAP[timeframe]
    clean_symbol = _normalize_symbol(symbol)

    # Alor uses seconds
    from_sec = start_ms // 1000
    to_sec = end_ms // 1000

    url = f"{ALOR_API_URL}/md/v2/history"
    params = {
        "symbol": clean_symbol,
        "exchange": ALOR_EXCHANGE,
        "tf": interval,
        "from": from_sec,
        "to": to_sec,
    }

    try:
        resp = httpx.get(url, headers=_auth_headers(), params=params, timeout=_CLIENT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Alor historical request failed: %s", exc)
        return []

    history = data.get("history", [])
    if not isinstance(history, list):
        logger.warning("Unexpected Alor history response format: %s", type(history))
        return []

    results: list[dict] = []
    for candle in history:
        ts_sec = candle.get("time")
        close = candle.get("close")
        if ts_sec is None or close is None:
            continue
        try:
            results.append(
                {
                    "timestamp_ms": int(ts_sec) * 1000,
                    "close": float(close),
                }
            )
        except (ValueError, TypeError):
            continue

    return results


# ---------------------------------------------------------------------------
# Current prices
# ---------------------------------------------------------------------------
def fetch_current(symbol: str) -> Optional[dict]:
    """
    Fetch current best bid/ask/last for a single symbol from Alor.
    symbol: e.g. "BMM6" or "BMM6@RTSX" (suffix stripped automatically)
    Returns dict with best_bid, best_ask, last_price, updated_ms.
    """
    clean_symbol = _normalize_symbol(symbol)
    ticker = f"{ALOR_EXCHANGE}:{clean_symbol}"

    url = f"{ALOR_API_URL}/md/v2/Securities/{ticker}/quotes"

    try:
        resp = httpx.get(url, headers=_auth_headers(), timeout=_CLIENT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Alor current request failed for %s: %s", symbol, exc)
        return None

    if not isinstance(data, list) or len(data) == 0:
        logger.warning("Empty Alor quotes response for %s", symbol)
        return None

    q = data[0]
    try:
        # Alor returns seconds for last_price_timestamp
        ts_sec = q.get("last_price_timestamp")
        updated_ms = int(ts_sec) * 1000 if ts_sec else int(time.time() * 1000)

        # ob_ms_timestamp is in milliseconds (UTC)
        ob_ms = q.get("ob_ms_timestamp")
        if ob_ms:
            updated_ms = int(ob_ms)

        return {
            "best_bid": float(q.get("bid")) if q.get("bid") is not None else None,
            "best_ask": float(q.get("ask")) if q.get("ask") is not None else None,
            "last_price": float(q.get("last_price")) if q.get("last_price") is not None else None,
            "updated_ms": updated_ms,
            "is_orderbook": True,
        }
    except (ValueError, TypeError) as exc:
        logger.warning("Alor quote parse error for %s: %s", symbol, exc)
        return None
