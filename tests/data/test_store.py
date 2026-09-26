"""FR-02/FR-03: Parquet OHLCV store round-trip, idempotent merge, DuckDB view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from cryptolab.data.store import OhlcvStore, duckdb_connect, sanitize_symbol, unsanitize_symbol
from tests.data.conftest import T0, assert_schema, make_frame

H = timedelta(hours=1)


def _files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.parquet"))


def test_symbol_sanitize_round_trip() -> None:
    assert sanitize_symbol("BTC/USDT") == "BTC-USDT"
    assert unsanitize_symbol("BTC-USDT") == "BTC/USDT"
    assert unsanitize_symbol(sanitize_symbol("BTC/USDT:USDT")) == "BTC/USDT:USDT"


def test_layout_is_partitioned_by_year(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    df = make_frame(start=datetime(2023, 12, 31, 22, tzinfo=UTC), n_bars=4)
    store.write("binance", "BTC/USDT", "1h", df)
    rel = [p.relative_to(tmp_path).as_posix() for p in _files(tmp_path)]
    assert rel == [
        "ohlcv/binance/1h/BTC-USDT/year=2023/part.parquet",
        "ohlcv/binance/1h/BTC-USDT/year=2024/part.parquet",
    ]


def test_round_trip_preserves_schema_and_values(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    df = make_frame(n_bars=10)
    assert store.write("binance", "BTC/USDT", "1h", df) == 10
    out = store.read("binance", "BTC/USDT", "1h")
    assert_schema(out)
    pd.testing.assert_frame_equal(out, df)


def test_read_missing_series_returns_typed_empty_frame(tmp_path: Path) -> None:
    out = OhlcvStore(tmp_path).read("binance", "ETH/USDT", "1h")
    assert out.empty
    assert_schema(out)


def test_write_is_idempotent(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    df = make_frame(n_bars=10)
    store.write("binance", "BTC/USDT", "1h", df)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in _files(tmp_path)}
    assert store.write("binance", "BTC/USDT", "1h", df) == 0
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in _files(tmp_path)}
    assert before == after
    pd.testing.assert_frame_equal(store.read("binance", "BTC/USDT", "1h"), df)


def test_write_merges_and_new_data_wins(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    store.write("binance", "BTC/USDT", "1h", make_frame(n_bars=6))
    newer = make_frame(start=T0 + 4 * H, n_bars=6)  # overlaps bars 4, 5
    newer["close"] = 999.0
    assert store.write("binance", "BTC/USDT", "1h", newer) == 4
    out = store.read("binance", "BTC/USDT", "1h")
    assert len(out) == 10
    assert_schema(out)
    assert (out.loc[out["ts"] >= pd.Timestamp(T0 + 4 * H), "close"] == 999.0).all()
    assert (out.loc[out["ts"] < pd.Timestamp(T0 + 4 * H), "close"] != 999.0).all()


def test_write_normalises_unsorted_duplicated_input(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    df = make_frame(n_bars=5)
    messy = pd.concat([df.iloc[::-1], df.iloc[[2]]], ignore_index=True)
    assert store.write("binance", "BTC/USDT", "1h", messy) == 5
    pd.testing.assert_frame_equal(store.read("binance", "BTC/USDT", "1h"), df)


def test_write_empty_frame_is_noop(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    assert store.write("binance", "BTC/USDT", "1h", make_frame(n_bars=0)) == 0
    assert _files(tmp_path) == []


def test_read_filters_start_inclusive_end_exclusive(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    store.write("binance", "BTC/USDT", "1h", make_frame(n_bars=10))
    out = store.read("binance", "BTC/USDT", "1h", start=T0 + 2 * H, end=T0 + 5 * H)
    assert list(out["ts"]) == [pd.Timestamp(T0 + i * H) for i in (2, 3, 4)]
    assert out.index.tolist() == [0, 1, 2]


def test_read_across_years(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    df = make_frame(start=datetime(2023, 12, 31, 20, tzinfo=UTC), n_bars=8)
    store.write("binance", "BTC/USDT", "1h", df)
    pd.testing.assert_frame_equal(store.read("binance", "BTC/USDT", "1h"), df)


def test_read_rejects_naive_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        OhlcvStore(tmp_path).read("binance", "BTC/USDT", "1h", start=datetime(2024, 1, 1))  # noqa: DTZ001


def test_last_ts(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    assert store.last_ts("binance", "BTC/USDT", "1h") is None
    store.write("binance", "BTC/USDT", "1h", make_frame(start=datetime(2023, 12, 31, tzinfo=UTC)))
    last = store.last_ts("binance", "BTC/USDT", "1h")
    assert last == datetime(2023, 12, 31, 9, tzinfo=UTC)
    assert last is not None
    assert last.tzinfo is not None


def test_list_series(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    assert store.list_series() == []
    store.write("binance", "BTC/USDT", "1h", make_frame())
    store.write("binance", "ETH/USDT", "1h", make_frame())
    store.write("okx", "BTC/USDT", "4h", make_frame(timeframe="4h"))
    assert store.list_series() == [
        ("binance", "1h", "BTC/USDT"),
        ("binance", "1h", "ETH/USDT"),
        ("okx", "4h", "BTC/USDT"),
    ]


def test_duckdb_view_exposes_series_columns(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    store.write("binance", "BTC/USDT", "1h", make_frame(n_bars=10))
    store.write("binance", "ETH/USDT", "1h", make_frame(n_bars=4))
    store.write("okx", "BTC/USDT", "4h", make_frame(n_bars=3, timeframe="4h"))
    con = duckdb_connect(tmp_path)
    try:
        rows = con.execute(
            "SELECT exchange, timeframe, symbol, count(*) FROM ohlcv GROUP BY ALL ORDER BY ALL"
        ).fetchall()
        assert rows == [
            ("binance", "1h", "BTC/USDT", 10),
            ("binance", "1h", "ETH/USDT", 4),
            ("okx", "4h", "BTC/USDT", 3),
        ]
        cols = [r[0] for r in con.execute("DESCRIBE ohlcv").fetchall()]
        for c in ("ts", "open", "high", "low", "close", "volume"):
            assert c in cols
        max_close = con.execute(
            "SELECT max(close) FROM ohlcv WHERE symbol = 'BTC/USDT' AND timeframe = '1h'"
        ).fetchone()
        assert max_close == (110.0,)
    finally:
        con.close()


def test_duckdb_session_timezone_is_utc(tmp_path: Path) -> None:
    store = OhlcvStore(tmp_path)
    store.write("binance", "BTC/USDT", "1h", make_frame(n_bars=3))
    con = duckdb_connect(tmp_path)
    try:
        out = con.execute("SELECT ts FROM ohlcv ORDER BY ts").df()
        assert str(out["ts"].dt.tz) == "UTC"
        assert out["ts"].iloc[0] == pd.Timestamp(T0)
    finally:
        con.close()


def test_duckdb_view_on_empty_store(tmp_path: Path) -> None:
    con = duckdb_connect(tmp_path)
    try:
        assert con.execute("SELECT count(*) FROM ohlcv").fetchone() == (0,)
        cols = [r[0] for r in con.execute("DESCRIBE ohlcv").fetchall()]
        assert {"exchange", "timeframe", "symbol", "ts", "close"} <= set(cols)
    finally:
        con.close()
