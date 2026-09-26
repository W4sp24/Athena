"""Shared fixtures for data-layer tests. No network: a fake CCXT-like exchange."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import ccxt
import pandas as pd

from cryptolab.data.schema import OHLCV_COLUMNS, TIMEFRAME_SECONDS

T0 = datetime(2024, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2030, 1, 1, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class FakeExchange:
    """Serves synthetic candles like ``ccxt.Exchange.fetch_ohlcv`` with a hard page limit."""

    id = "fake"

    def __init__(
        self,
        *,
        start: datetime = T0,
        n_bars: int = 20,
        timeframe: str = "1h",
        max_limit: int = 5,
        skip: Iterable[int] = (),
        fail_times: int = 0,
    ) -> None:
        step = TIMEFRAME_SECONDS[timeframe] * 1000
        skipped = set(skip)
        self.candles: list[list[float]] = []
        for i in range(n_bars):
            if i in skipped:
                continue
            o = 100.0 + i
            self.candles.append([_ms(start) + i * step, o, o + 2.0, o - 1.0, o + 1.0, 10.0 + i])
        self.max_limit = max_limit
        self.fail_times = fail_times
        self.calls: list[tuple[str, str, int | None, int | None]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe, since, limit))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ccxt.NetworkError("transient")
        lim = min(limit or self.max_limit, self.max_limit)
        rows = [c for c in self.candles if since is None or c[0] >= since]
        return [list(r) for r in rows[:lim]]


def make_frame(
    start: datetime = T0,
    n_bars: int = 10,
    timeframe: str = "1h",
    skip: Iterable[int] = (),
) -> pd.DataFrame:
    """Schema-conformant synthetic frame; bars at indices in ``skip`` are absent."""
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    skipped = set(skip)
    idx = [i for i in range(n_bars) if i not in skipped]
    opens = [100.0 + i for i in idx]
    df = pd.DataFrame(
        {
            "ts": pd.DatetimeIndex([start + i * step for i in idx]).astype("datetime64[ns, UTC]"),
            "open": opens,
            "high": [o + 2.0 for o in opens],
            "low": [o - 1.0 for o in opens],
            "close": [o + 1.0 for o in opens],
            "volume": [10.0 + i for i in idx],
        },
        columns=list(OHLCV_COLUMNS),
    )
    return df.astype({c: "float64" for c in OHLCV_COLUMNS[1:]})


def assert_schema(df: pd.DataFrame) -> None:
    assert tuple(df.columns) == OHLCV_COLUMNS
    assert str(df["ts"].dtype) == "datetime64[ns, UTC]"
    for col in OHLCV_COLUMNS[1:]:
        assert df[col].dtype == "float64", col
    assert df["ts"].is_monotonic_increasing
    assert df["ts"].is_unique
