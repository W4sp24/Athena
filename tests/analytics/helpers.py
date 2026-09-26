"""Hand-built fixtures for analytics tests. No engine or data layer is needed."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from cryptolab.engine.types import Fill

FILL_COLUMNS = [f.name for f in fields(Fill)]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def daily_equity(values: list[float], start: datetime = T0) -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx, dtype="float64", name="equity")


def hourly_equity(values: list[float], start: datetime = T0) -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="h", tz="UTC")
    return pd.Series(values, index=idx, dtype="float64", name="equity")


def fill(  # noqa: PLR0917 - mirrors the Fill field order for readable fixtures
    i: int,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    fee: float,
    notes: str = "",
) -> dict[str, Any]:
    """One fill row; ``i`` is the bar index used for ts (decision one bar earlier)."""
    ts = T0 + timedelta(days=i)
    return {
        "ts": ts,
        "decision_ts": ts - timedelta(days=1),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "fee": fee,
        "notes": notes,
    }


def fills_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=FILL_COLUMNS)


def empty_fills() -> pd.DataFrame:
    return pd.DataFrame(columns=FILL_COLUMNS)
