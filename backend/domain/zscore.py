"""
Z-score calculation over a rolling window of historical spread values.

Formula for point i (0-indexed):
  window = series[i - N + 1 : i + 1]   (inclusive, length N)
  z = (series[i] - mean(window)) / stdev(window)

If len(series) < N -> None for all points.
If stdev(window) == 0 -> 0.0 (avoid division by zero).
"""

import statistics
from typing import Optional


def compute_zscore(
    spread_series: list[float],
    window: int = 50,
) -> list[Optional[float]]:
    """
    Compute rolling Z-score for a chronological spread series.

    Args:
        spread_series: list of spread_pct floats in ascending time order.
        window: rolling window size (default 50).

    Returns:
        List of same length; None for points where window is not yet full.
    """
    n = len(spread_series)
    if n < window:
        return [None] * n

    result: list[Optional[float]] = [None] * (window - 1)

    for i in range(window - 1, n):
        w = spread_series[i - window + 1 : i + 1]
        current = spread_series[i]
        mean_w = statistics.mean(w)
        try:
            std_w = statistics.stdev(w)
        except statistics.StatisticsError:
            std_w = 0.0

        if std_w == 0:
            result.append(0.0)
        else:
            result.append((current - mean_w) / std_w)

    return result
