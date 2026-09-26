"""NFR-02: 3 years of hourly bars x 10 symbols must backtest in under 60 s.

The full-size run takes several seconds, so it only runs with ``CRYPTOLAB_RUN_BENCH=1``:

    CRYPTOLAB_RUN_BENCH=1 uv run pytest tests/engine/test_performance.py -s

A quick smoke-sized run (1 month) always runs, to catch gross regressions.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

from cryptolab.engine import run_backtest
from cryptolab.strategies.ma_crossover import MACrossover
from tests.engine.helpers import T0, research_limits

N_SYMBOLS = 10
FULL_BARS = 3 * 365 * 24  # 26,280


def synthetic_bars(n_bars: int, n_symbols: int, seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(T0, periods=n_bars, freq="h", tz="UTC")
    out: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars)))
        opens = np.r_[100.0, close[:-1]] * np.exp(rng.normal(0, 0.001, n_bars))
        out[f"S{i:02d}/USDT"] = pd.DataFrame(
            {
                "ts": ts,
                "open": opens,
                "high": np.maximum(opens, close) * 1.002,
                "low": np.minimum(opens, close) * 0.998,
                "close": close,
                "volume": rng.uniform(1, 10, n_bars),
            }
        )
    return out


def _timed_run(n_bars: int) -> tuple[float, int]:
    bars = synthetic_bars(n_bars, N_SYMBOLS)
    t0 = time.perf_counter()
    r = run_backtest(
        bars,
        MACrossover(fast=20, slow=50),
        timeframe="1h",
        initial_capital=100_000.0,
        fee_bps=10,
        slippage_bps=5,
        risk_limits=research_limits(),
    )
    elapsed = time.perf_counter() - t0
    assert len(r.equity) == n_bars
    return elapsed, len(r.fills)


def test_smoke_one_month_ten_symbols() -> None:
    elapsed, fills = _timed_run(30 * 24)
    assert fills > 0
    assert elapsed < 60 / 36 * 3  # generous: 3x the pro-rata NFR-02 budget


@pytest.mark.skipif(
    os.environ.get("CRYPTOLAB_RUN_BENCH") != "1", reason="set CRYPTOLAB_RUN_BENCH=1 to run"
)
def test_nfr02_three_years_hourly_ten_symbols_under_60s() -> None:
    elapsed, fills = _timed_run(FULL_BARS)
    print(f"\nNFR-02: {FULL_BARS} bars x {N_SYMBOLS} symbols: {elapsed:.1f}s, {fills} fills")
    assert elapsed < 60.0
