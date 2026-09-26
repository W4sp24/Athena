"""OHLCV frame contract shared by data, engine, and analytics.

A bar frame is a pandas DataFrame with exactly these columns, one row per bar,
sorted ascending by ``ts``, ``ts`` unique:

- ``ts``: bar OPEN time, ``datetime64[ns, UTC]``
- ``open``, ``high``, ``low``, ``close``, ``volume``: ``float64``
"""

from __future__ import annotations

OHLCV_COLUMNS: tuple[str, ...] = ("ts", "open", "high", "low", "close", "volume")

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# math.md §1: crypto trades 24/7, so annualize on calendar time.
PERIODS_PER_YEAR: dict[str, int] = {tf: 365 * 86400 // s for tf, s in TIMEFRAME_SECONDS.items()}
