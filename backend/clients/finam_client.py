"""
Finam Trade API v1 client.

Time handling:
- All internal timestamps are Unix ms UTC.
- Finam expects ISO-8601 UTC strings for interval boundaries.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import FINAM_API_URL, FINAM_TOKEN, MOEX_SYMBOL

logger = logging.getLogger(__name__)

TIME_FRAME_MAP = {
    "5m": "TIME_FRAME_M5",
    "15m": "TIME_FRAME_M15",
    "60m": "TIME_FRAME_H1",
}

_CLIENT_TIMEOUT = httpx.Timeout(120.0, connect=20.0)

# ---------------------------------------------------------------------------
# JWT caching (TTL 5 min)
# ---------------------------------------------------------------------------
_jwt_cache: dict = {}
_JWT_TTL_SECONDS = 300


def _get_jwt() -> str:
    """Obtain (or refresh) JWT via /v1/sessions."""
    now = time.time()
    token = _jwt_cache.get("token")
    expires = _jwt_cache.get("expires_at", 0)
    if token and expires > now + 60:
        return token

    try:
        resp = httpx.post(
            f"{FINAM_API_URL}/sessions",
            json={"secret": FINAM_TOKEN},
            timeout=_CLIENT_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
        new_token = data["token"]
        _jwt_cache["token"] = new_token
        _jwt_cache["expires_at"] = now + _JWT_TTL_SECONDS
        return new_token
    except Exception as exc:
        logger.error("Failed to obtain Finam JWT: %s", exc)
        raise


def _auth_headers() -> dict:
    return {"Authorization": _get_jwt(), "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _parse_decimal(field: Optional[dict]) -> Optional[float]:
    if not field or "value" not in field:
        return None
    try:
        return float(field["value"])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Historical candles
# ---------------------------------------------------------------------------
def fetch_historical(
    timeframe: str,
    from_ms: int,
    to_ms: int,
) -> list[dict]:
    """
    Fetch candles from Finam v1 with 30-day pagination.
    Returns list of {"timestamp_ms": int, "close": float}.
    """
    if timeframe not in TIME_FRAME_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    tf_code = TIME_FRAME_MAP[timeframe]
    results: list[dict] = []
    window_days = 30
    step_ms = window_days * 24 * 60 * 60 * 1000
    current_to = to_ms

    # Walk backwards from newest to oldest so that an empty response
    # means we have reached the first available candle.
    while current_to > from_ms:
        window_from = max(current_to - step_ms, from_ms)

        params = {
            "timeframe": tf_code,
            "interval.start_time": _fmt_utc(_ms_to_dt(window_from)),
            "interval.end_time": _fmt_utc(_ms_to_dt(current_to)),
        }

        try:
            resp = httpx.get(
                f"{FINAM_API_URL}/instruments/{MOEX_SYMBOL}/bars",
                params=params,
                headers=_auth_headers(),
                timeout=_CLIENT_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Finam bars HTTP error for %s %s: %s", MOEX_SYMBOL, timeframe, exc)
            break
        except Exception as exc:
            logger.warning("Finam bars request failed for %s %s: %s", MOEX_SYMBOL, timeframe, exc)
            break

        bars = data.get("bars") or []
        if not bars:
            break  # reached beginning of contract history

        for candle in bars:
            ts_str = candle.get("timestamp")
            if not ts_str:
                continue
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            close_val = _parse_decimal(candle.get("close"))
            if close_val is None:
                continue

            results.append(
                {
                    "timestamp_ms": _dt_to_ms(ts_dt),
                    "close": close_val,
                }
            )

        current_to = window_from
        time.sleep(0.55)

    results.sort(key=lambda x: x["timestamp_ms"])
    return results


# ---------------------------------------------------------------------------
# Current prices
# ---------------------------------------------------------------------------
def fetch_current() -> dict:
    """
    Fetch current MOEX price snapshot.
    Primary: /quotes/latest (bid, ask, last).
    Fallback: /orderbook (best levels).

    Returns dict with keys:
        best_bid, best_ask, last_price, updated_ms, is_orderbook
    """
    snapshot: dict = {
        "best_bid": None,
        "best_ask": None,
        "last_price": None,
        "updated_ms": int(time.time() * 1000),
        "is_orderbook": False,
    }

    headers = _auth_headers()

    # 1) Quotes / latest
    try:
        resp = httpx.get(
            f"{FINAM_API_URL}/instruments/{MOEX_SYMBOL}/quotes/latest",
            headers=headers,
            timeout=_CLIENT_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            quote = data.get("quote") or {}
            bid = _parse_decimal(quote.get("bid"))
            ask = _parse_decimal(quote.get("ask"))
            last = _parse_decimal(quote.get("last"))
            if bid is not None:
                snapshot["best_bid"] = bid
            if ask is not None:
                snapshot["best_ask"] = ask
            if last is not None:
                snapshot["last_price"] = last
            return snapshot
    except Exception as exc:
        logger.debug("Finam quotes/latest failed: %s", exc)

    # 2) Fallback: orderbook
    try:
        resp = httpx.get(
            f"{FINAM_API_URL}/instruments/{MOEX_SYMBOL}/orderbook",
            headers=headers,
            timeout=_CLIENT_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = (data.get("orderbook") or {}).get("rows") or []
            bids = []
            asks = []
            for row in rows:
                price = _parse_decimal(row.get("price"))
                if price is None:
                    continue
                buy_sz = _parse_decimal(row.get("buy_size"))
                sell_sz = _parse_decimal(row.get("sell_size"))
                if buy_sz:
                    bids.append(price)
                if sell_sz:
                    asks.append(price)
            if bids:
                snapshot["best_bid"] = max(bids)
            if asks:
                snapshot["best_ask"] = min(asks)
            snapshot["is_orderbook"] = True
    except Exception as exc:
        logger.warning("Finam orderbook fallback failed: %s", exc)

    return snapshot
