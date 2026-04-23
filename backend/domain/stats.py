"""
Statistics computation for spread series.
"""

from statistics import mean, median, stdev
from typing import Optional


def compute_all(spread_values: list[float]) -> dict:
    """
    Compute full statistics for a spread % series.
    Returns: avg, median, stddev, min, max, entry_2sigma_low, entry_2sigma_high
    """
    if not spread_values:
        return {
            "avg": None, "median": None, "stddev": None,
            "min": None, "max": None,
            "entry_low": None, "entry_high": None,
        }

    avg = mean(spread_values)
    med = median(spread_values)
    try:
        sd = stdev(spread_values)
    except Exception:
        sd = 0.0

    min_val = min(spread_values)
    max_val = max(spread_values)

    return {
        "avg": round(avg, 4),
        "median": round(med, 4),
        "stddev": round(sd, 4),
        "min": round(min_val, 4),
        "max": round(max_val, 4),
        "entry_low": round(avg - 2 * sd, 4),
        "entry_high": round(avg + 2 * sd, 4),
    }


def entry_signal(current_spread: float, avg: float, stddev: float) -> dict:
    """
    Determine entry signal based on ±2σ deviation.
    Returns: {signal: 'buy'|'sell'|'neutral', zscore, description}
    """
    if stddev == 0:
        return {"signal": "neutral", "zscore": 0, "description": "Нет данных"}

    z = (current_spread - avg) / stddev

    if z <= -2.0:
        return {"signal": "buy", "zscore": round(z, 2), "description": "BUY: Spread < -2σ"}
    elif z >= 2.0:
        return {"signal": "sell", "zscore": round(z, 2), "description": "SELL: Spread > +2σ"}
    elif abs(z) >= 1.5:
        return {"signal": "watch", "zscore": round(z, 2), "description": "Внимание: |Z| > 1.5"}
    else:
        return {"signal": "neutral", "zscore": round(z, 2), "description": "В пределах нормы"}
