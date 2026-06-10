"""
Spread calculations.
"""

from typing import Optional


def mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Calculate mid-price from bid/ask."""
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return None


def historical_spread_pct(hl_close: float, moex_close: float) -> Optional[float]:
    """
    Spread % from historical close candles.
    Formula: (HL - MOEX) / MOEX * 100
    """
    if moex_close == 0:
        return None
    return ((hl_close - moex_close) / moex_close) * 100.0


def current_spread_pct(hl_mid: float, moex_mid: float) -> float:
    """
    Spread % from live mid prices.
    Formula: (HL - MOEX) / MOEX * 100
    """
    return ((hl_mid - moex_mid) / moex_mid) * 100.0


def arb_spread(hl_bid: Optional[float], hl_ask: Optional[float],
               moex_bid: Optional[float], moex_ask: Optional[float]) -> Optional[float]:
    """
    Real executable spread using bid/ask (accounts for slippage).
    Buy MOEX (ask), Sell HL (bid): spread = HL_bid - MOEX_ask
    """
    if hl_bid is None or moex_ask is None:
        return None
    return hl_bid - moex_ask
