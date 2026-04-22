"""
Strict timestamp synchronization for two candle series.

Rule: a point is included ONLY if both sources share the exact same timestamp_ms.
No interpolation, no tolerance.
"""

from typing import Optional


def strict_sync(
    moex_candles: list[dict],
    hl_candles: list[dict],
) -> list[dict]:
    """
    Match MOEX and HL candles by identical timestamp_ms.

    Args:
        moex_candles: list of {"timestamp_ms": int, "close": float}
        hl_candles:   list of {"timestamp_ms": int, "close": float}

    Returns:
        Ordered list of {"timestamp_ms": int, "moex_close": float, "hl_close": float}
    """
    if not moex_candles or not hl_candles:
        return []

    # Build lookup from HL series (usually smaller or comparable)
    hl_by_ts: dict[int, float] = {}
    for c in hl_candles:
        ts = c.get("timestamp_ms")
        if ts is not None:
            hl_by_ts[ts] = c["close"]

    synced: list[dict] = []
    for c in moex_candles:
        ts = c.get("timestamp_ms")
        if ts is not None and ts in hl_by_ts:
            synced.append(
                {
                    "timestamp_ms": ts,
                    "moex_close": c["close"],
                    "hl_close": hl_by_ts[ts],
                }
            )

    # Assumes both inputs are individually sorted ascending.
    # The iteration over moex_candles preserves order.
    return synced
