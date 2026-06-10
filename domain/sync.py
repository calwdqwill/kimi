"""
Strict timestamp synchronization between two candle series.
"""


def strict_sync(series_a: list[dict], series_b: list[dict]) -> list[dict]:
    """
    Return rows where both series have the exact same timestamp_ms.
    Each row: {timestamp_ms, moex_close, hl_close}
    """
    # Build lookup from timestamp_ms -> close for series_a (moex)
    lookup_a = {row["timestamp_ms"]: row["close"] for row in series_a}

    result = []
    for row in sorted(series_b, key=lambda r: r["timestamp_ms"]):
        ts = row["timestamp_ms"]
        if ts in lookup_a:
            result.append({
                "timestamp_ms": ts,
                "moex_close": lookup_a[ts],
                "hl_close": row["close"],
            })
    return result
