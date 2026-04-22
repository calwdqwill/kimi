"""
Spread calculation rules.

Historical:
  spread_pct = (HL_close - MOEX_close) / MOEX_close * 100

Current:
  MOEX_mid = (best_bid + best_ask) / 2
  HL_mid   = (best_bid + best_ask) / 2
  spread_pct = (HL_mid - MOEX_mid) / MOEX_mid * 100

These are intentionally separate functions — never mix historical close with live mid.
"""

from typing import Optional


def mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Calculate mid price. Returns None if either leg is missing."""
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def historical_spread_pct(hl_close: float, moex_close: float) -> Optional[float]:
    """
    Spread % calculated from candle closes.
    Returns None if MOEX_close is zero to avoid division by zero.
    """
    if moex_close == 0:
        return None
    return (hl_close - moex_close) / moex_close * 100.0


def current_spread_pct(hl_mid: float, moex_mid: float) -> Optional[float]:
    """
    Spread % calculated from live mids.
    Returns None if MOEX_mid is zero to avoid division by zero.
    """
    if moex_mid == 0:
        return None
    return (hl_mid - moex_mid) / moex_mid * 100.0
