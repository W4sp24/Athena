"""PointInTimeView: a strategy at d_t sees bars 0..t only (math.md §1, FR-13)."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from cryptolab.engine import MarketData, PointInTimeView
from cryptolab.engine.types import MarketView
from tests.engine.helpers import T0, make_bars

H = timedelta(hours=1)


def _data() -> MarketData:
    return MarketData(
        {
            "B": make_bars([1, 2, 3, 4, 5], [1.5, 2.5, 3.5, 4.5, 5.5]),
            "A": make_bars([10, 20, 30, 40, 50], drop=[2]),
        },
        3600,
    )


def test_satisfies_protocol() -> None:
    v: MarketView = PointInTimeView(_data(), 0)
    assert v.symbols == ("A", "B")  # sorted, deterministic


def test_now_is_close_of_current_bar() -> None:
    assert PointInTimeView(_data(), 0).now == T0 + H
    assert PointInTimeView(_data(), 4).now == T0 + 5 * H


def test_bars_up_to_t_only() -> None:
    d = _data()
    for t in range(5):
        v = PointInTimeView(d, t)
        b = v.bars("B")
        assert len(b) == t + 1
        assert b["ts"].max() <= v.now - H  # close of every visible bar <= now
        assert b["close"].tolist() == [1.5, 2.5, 3.5, 4.5, 5.5][: t + 1]


def test_missing_bar_is_just_absent() -> None:
    d = _data()
    assert PointInTimeView(d, 2).bars("A")["open"].tolist() == [10, 20]
    assert PointInTimeView(d, 3).bars("A")["open"].tolist() == [10, 20, 40]


def test_lookback() -> None:
    v = PointInTimeView(_data(), 3)
    assert v.bars("B", 2)["close"].tolist() == [3.5, 4.5]
    assert v.bars("B", 100)["close"].tolist() == [1.5, 2.5, 3.5, 4.5]
    assert v.bars("B", 0).empty
    assert list(v.bars("B", 0).columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert list(v.bars("B", 2).index) == [0, 1]
    with pytest.raises(ValueError, match="lookback"):
        v.bars("B", -1)


def test_unknown_symbol() -> None:
    with pytest.raises(KeyError, match="unknown symbol"):
        PointInTimeView(_data(), 0).bars("ZZZ")


def test_out_of_range_t() -> None:
    with pytest.raises(IndexError):
        PointInTimeView(_data(), 5)


def test_returned_frame_is_an_isolated_copy() -> None:
    d = _data()
    v = PointInTimeView(d, 1)
    b = v.bars("B")
    b.loc[0, "close"] = -999.0
    assert PointInTimeView(d, 4).bars("B")["close"].tolist() == [1.5, 2.5, 3.5, 4.5, 5.5]
    # no numpy base array of the returned frame can reach future rows
    for col in ("open", "close"):
        arr = v.bars("B")[col].to_numpy()
        base = arr.base if arr.base is not None else arr
        assert np.asarray(base).size <= 2 * 6  # at most the 2 visible rows (x columns)


@pytest.mark.parametrize("n", [1, 2, 20_000, 26_280, 50_000])
def test_grid_keeps_every_bar_of_long_series(n: int) -> None:
    """Regression: np.arange(start, end + 1, step) on int64 ns sizes the grid in float64 and
    silently dropped the final bar(s) of multi-year hourly series."""
    ts = pd.date_range(T0, periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"ts": ts, **{c: np.ones(n) for c in ("open", "high", "low", "close")}})
    df["volume"] = 1.0
    d = MarketData({"A": df}, 3600)
    assert len(d) == n
    assert d.bar_times[-1] == ts[-1]
    assert int(d.sym["A"].row_at[-1]) == n - 1


def test_ts_is_utc_ns() -> None:
    b = PointInTimeView(_data(), 1).bars("B")
    assert isinstance(b["ts"].dtype, pd.DatetimeTZDtype)
    assert str(b["ts"].dt.tz) == "UTC"
