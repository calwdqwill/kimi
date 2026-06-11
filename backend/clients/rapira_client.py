"""
Rapira API client — USDT/RUB spot orderbook.
"""

import json
import logging
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

RAPIRA_API_URL = "https://api.rapira.net/market/exchange-plate-mini"


def fetch_usdt_rub() -> dict | None:
    """Fetch best bid/ask for USDT/RUB from Rapira spot orderbook."""
    try:
        data = urllib.parse.urlencode({"symbol": "USDT/RUB"}).encode("utf-8")
        req = urllib.request.Request(RAPIRA_API_URL, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            payload = json.loads(body)

        bid = payload.get("bid", {})
        ask = payload.get("ask", {})

        bid_items = bid.get("items", [])
        ask_items = ask.get("items", [])

        best_bid = bid_items[0]["price"] if bid_items else None
        best_ask = ask_items[0]["price"] if ask_items else None

        if best_bid is None or best_ask is None:
            return None

        mid = round((best_bid + best_ask) / 2, 4)

        return {
            "best_bid": round(best_bid, 4),
            "best_ask": round(best_ask, 4),
            "mid": mid,
            "updated_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        logger.warning("Rapira fetch failed: %s", exc)
        return None
