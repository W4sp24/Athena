"""FR-02: incremental update resumes from the last stored bar and is idempotent."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from cryptolab.data.store import OhlcvStore
from cryptolab.data.update import update
from tests.data.conftest import T0, FakeExchange, assert_schema

H = timedelta(hours=1)


def test_first_update_downloads_from_start(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    ex = FakeExchange(n_bars=12)
    added = update(store, "fake", ["BTC/USDT"], "1h", T0, exchange=ex, now=T0 + 12 * H)
    assert added == {"BTC/USDT": 12}
    df = store.read("fake", "BTC/USDT", "1h")
    assert len(df) == 12
    assert_schema(df)


def test_update_resumes_after_last_stored_bar(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    ex = FakeExchange(n_bars=30)
    update(store, "fake", ["BTC/USDT"], "1h", T0, exchange=ex, now=T0 + 10 * H)
    assert store.last_ts("fake", "BTC/USDT", "1h") == T0 + 9 * H

    ex.calls.clear()
    added = update(store, "fake", ["BTC/USDT"], "1h", T0, exchange=ex, now=T0 + 25 * H)
    assert added == {"BTC/USDT": 15}
    first_since = ex.calls[0][2]
    assert first_since == int((T0 + 10 * H).timestamp() * 1000)
    df = store.read("fake", "BTC/USDT", "1h")
    assert list(df["ts"]) == [pd.Timestamp(T0 + i * H) for i in range(25)]


def test_rerun_with_no_new_bars_adds_nothing(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    ex = FakeExchange(n_bars=12)
    update(store, "fake", ["BTC/USDT"], "1h", T0, exchange=ex, now=T0 + 12 * H)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")}
    added = update(store, "fake", ["BTC/USDT"], "1h", T0, exchange=ex, now=T0 + 12 * H)
    assert added == {"BTC/USDT": 0}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.parquet")} == before


def test_multiple_symbols_are_stored_separately(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    ex = FakeExchange(n_bars=6)
    added = update(store, "fake", ["BTC/USDT", "ETH/USDT"], "1h", T0, exchange=ex, now=T0 + 6 * H)
    assert added == {"BTC/USDT": 6, "ETH/USDT": 6}
    assert store.list_series() == [("fake", "1h", "BTC/USDT"), ("fake", "1h", "ETH/USDT")]
