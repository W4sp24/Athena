"""FR-01: paginated, closed-bar-only OHLCV download (network mocked)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import ccxt
import pandas as pd
import pytest

from cryptolab.data.download import fetch_ohlcv_range
from tests.data.conftest import FAR_FUTURE, T0, FakeExchange, assert_schema

H = timedelta(hours=1)


def test_paginates_over_all_pages() -> None:
    ex = FakeExchange(n_bars=23, max_limit=5)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE, limit=5)
    assert len(df) == 23
    assert_schema(df)
    assert df["ts"].iloc[0] == pd.Timestamp(T0)
    assert df["ts"].iloc[-1] == pd.Timestamp(T0 + 22 * H)
    assert len(ex.calls) >= 5  # 23 bars at 5 per page


def test_exchange_page_cap_below_requested_limit_still_paginates() -> None:
    ex = FakeExchange(n_bars=23, max_limit=3)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE, limit=1000)
    assert len(df) == 23


def test_respects_start_and_exclusive_end() -> None:
    ex = FakeExchange(n_bars=20)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0 + 3 * H, T0 + 8 * H, now=FAR_FUTURE)
    assert list(df["ts"]) == [pd.Timestamp(T0 + i * H) for i in range(3, 8)]


def test_drops_forming_bar() -> None:
    ex = FakeExchange(n_bars=20)
    # now is mid-way through bar 10: bars 0..9 are closed, bar 10 is forming.
    now = T0 + 10 * H + timedelta(minutes=30)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=now)
    assert df["ts"].iloc[-1] == pd.Timestamp(T0 + 9 * H)
    assert len(df) == 10


def test_bar_closing_exactly_at_now_is_kept() -> None:
    ex = FakeExchange(n_bars=20)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=T0 + 10 * H)
    assert df["ts"].iloc[-1] == pd.Timestamp(T0 + 9 * H)


def test_dedupes_overlapping_pages() -> None:
    class Overlapping(FakeExchange):
        def fetch_ohlcv(
            self,
            symbol: str,
            timeframe: str = "1m",
            since: int | None = None,
            limit: int | None = None,
            params: dict[str, Any] | None = None,
        ) -> list[list[float]]:
            rows = super().fetch_ohlcv(symbol, timeframe, since, limit, params)
            if since is not None and since > self.candles[0][0]:
                prev = [c for c in self.candles if c[0] < since][-1:]
                rows = [list(r) for r in prev] + rows
            return rows

    ex = Overlapping(n_bars=12)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE)
    assert len(df) == 12
    assert_schema(df)


def test_empty_when_no_data() -> None:
    ex = FakeExchange(n_bars=0)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE)
    assert df.empty
    assert_schema(df)


def test_start_after_end_of_data_returns_empty() -> None:
    ex = FakeExchange(n_bars=5)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0 + 100 * H, now=FAR_FUTURE)
    assert df.empty


def test_gaps_in_source_are_not_filled() -> None:
    ex = FakeExchange(n_bars=12, skip={4, 5})
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE)
    assert len(df) == 10
    assert pd.Timestamp(T0 + 4 * H) not in set(df["ts"])


def test_retries_transient_network_errors() -> None:
    ex = FakeExchange(n_bars=7, fail_times=2)
    sleeps: list[float] = []
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE, sleep=sleeps.append)
    assert len(df) == 7
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # backoff grows


def test_gives_up_after_max_retries() -> None:
    ex = FakeExchange(n_bars=7, fail_times=10)
    with pytest.raises(ccxt.NetworkError):
        fetch_ohlcv_range(
            ex, "BTC/USDT", "1h", T0, now=FAR_FUTURE, max_retries=3, sleep=lambda _s: None
        )
    assert len(ex.calls) == 4  # first try + 3 retries


def test_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone"):
        fetch_ohlcv_range(FakeExchange(), "BTC/USDT", "1h", datetime(2024, 1, 1))  # noqa: DTZ001


def test_rejects_unknown_timeframe() -> None:
    with pytest.raises(ValueError, match="timeframe"):
        fetch_ohlcv_range(FakeExchange(), "BTC/USDT", "7m", T0)
