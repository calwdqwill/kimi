"""
Rolling Z-score computation.
"""

from statistics import mean, stdev
from typing import Optional


def compute_zscore(values: list[float], window: int = 50) -> list[Optional[float]]:
    """
    Return a list of Z-scores aligned with *values*.
    The first (window-1) entries are None because the window is not full.
    """
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
            continue
        window_vals = values[i - window + 1 : i + 1]
        m = mean(window_vals)
        try:
            sd = stdev(window_vals)
        except Exception:
            sd = 0.0
        if sd == 0:
            result.append(0.0)
        else:
            result.append((values[i] - m) / sd)
    return result
