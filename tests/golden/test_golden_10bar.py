"""Golden test (math.md §8): a hand-computed 10-bar, single-symbol backtest.

This stands in for the P2 spreadsheet until Ethan provides it; it is then *replaced*, not
supplemented (math.md §8). Tolerance: |V_engine - V_hand| <= 1e-8 * V0 per bar, and total
return equal to 6 decimal places.

Setup
-----
V0 = 10,000 USDT, fee_bps = 10 -> f = 0.001, slippage_bps = 20 -> s = 0.002,
research risk profile (configs/risk.backtest.yaml), hourly bars from 2026-01-01 00:00 UTC.

    t   open   close
    0   100    100
    1   102    104
    2   104    103
    3   103    106
    4   106    108
    5   108    107
    6   105    104
    7   104    106
    8   106    105
    9   105    110

Strategy: at d_0 (close of bar 0) target w = 0.5; at d_5 target w = 0; at d_9 (the last
bar) target w = 1.0, which must be dropped and logged (math.md §5.7). Otherwise no opinion.

Hand calculation
----------------
Bar 0 (math.md §4): no position, V_0 = c = 10,000.
  Decision d_0 (§5.1): q* = w V_0 / C_0 = 0.5 * 10,000 / 100 = 50; dq = +50.
  Notional 50 * 100 = 5,000 >= 10 (dust floor), so an OrderIntent goes to RiskGate: approved.

Bar 1: fill at O_1 (§5.2): p = 102 * (1 + 0.002) = 102.204
  fee (§5.3) = 0.001 * 50 * 102.204 = 5.1102
  cash (§5.4) = 10,000 - 50 * 102.204 - 5.1102 = 10,000 - 5,110.2 - 5.1102 = 4,884.6898
  V_1 = 4,884.6898 + 50 * 104 = 10,084.6898
Bar 2: V_2 = 4,884.6898 + 50 * 103 = 10,034.6898
Bar 3: V_3 = 4,884.6898 + 50 * 106 = 10,184.6898
Bar 4: V_4 = 4,884.6898 + 50 * 108 = 10,284.6898
Bar 5: V_5 = 4,884.6898 + 50 * 107 = 10,234.6898
  Decision d_5: q* = 0 -> dq = -50 (sell 50), notional 50 * 107 = 5,350. Approved.
Bar 6: fill at O_6: p = 105 * (1 - 0.002) = 104.79
  fee = 0.001 * 50 * 104.79 = 5.2395
  cash = 4,884.6898 + 50 * 104.79 - 5.2395 = 4,884.6898 + 5,239.5 - 5.2395 = 10,118.9503
  V_6 = 10,118.9503 (flat)
Bars 7, 8, 9: V = 10,118.9503.
  Decision d_9: q* = 1.0 * 10,118.9503 / 110 = 91.99045727...; there is no bar 10 to fill at,
  so the signal is dropped and logged.

Total return (§2.3) R = 10,118.9503 / 10,000 - 1 = 0.01189503
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptolab.config import load_risk_limits
from cryptolab.engine import run_backtest
from cryptolab.engine.types import MarketView

ROOT = Path(__file__).resolve().parents[2]
SYM = "BTC/USDT"
V0 = 10_000.0
T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)

OPENS = [100, 102, 104, 103, 106, 108, 105, 104, 106, 105]
CLOSES = [100, 104, 103, 106, 108, 107, 104, 106, 105, 110]

# hand-computed, see module docstring
EXPECTED_EQUITY = [
    10_000.0,
    10_084.6898,
    10_034.6898,
    10_184.6898,
    10_284.6898,
    10_234.6898,
    10_118.9503,
    10_118.9503,
    10_118.9503,
    10_118.9503,
]
EXPECTED_TOTAL_RETURN = 0.01189503


class GoldenScript:
    name = "golden_script"

    def on_bar(self, view: MarketView) -> dict[str, float]:
        t = len(view.bars(SYM)) - 1
        return {0: {SYM: 0.5}, 5: {SYM: 0.0}, 9: {SYM: 1.0}}.get(t, {})


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.date_range(T0, periods=10, freq="h", tz="UTC"),
            "open": np.array(OPENS, dtype=float),
            "high": np.maximum(OPENS, CLOSES).astype(float) + 1,
            "low": np.minimum(OPENS, CLOSES).astype(float) - 1,
            "close": np.array(CLOSES, dtype=float),
            "volume": np.full(10, 5.0),
        }
    )


@pytest.fixture(scope="module")
def result():  # type: ignore[no-untyped-def]
    return run_backtest(
        {SYM: _bars()},
        GoldenScript(),
        timeframe="1h",
        initial_capital=V0,
        fee_bps=10,
        slippage_bps=20,
        risk_limits=load_risk_limits(ROOT / "configs" / "risk.backtest.yaml"),
    )


def test_equity_matches_hand_calculation_per_bar(result) -> None:  # type: ignore[no-untyped-def]
    eq = result.equity
    assert list(eq.index) == list(pd.date_range(T0, periods=10, freq="h", tz="UTC"))
    diff = np.abs(eq.to_numpy() - np.array(EXPECTED_EQUITY))
    assert diff.max() <= 1e-8 * V0, diff


def test_total_return_to_6_decimals(result) -> None:  # type: ignore[no-untyped-def]
    r = result.equity.iloc[-1] / V0 - 1
    assert round(r, 6) == round(EXPECTED_TOTAL_RETURN, 6)


def test_fills_match_hand_calculation(result) -> None:  # type: ignore[no-untyped-def]
    f = result.fills
    assert len(f) == 2
    buy, sell = f.iloc[0], f.iloc[1]

    assert buy["ts"] == T0 + 1 * H  # open of bar 1
    assert buy["decision_ts"] == T0 + 1 * H  # d_0 = close of bar 0
    assert buy["side"] == "buy"
    assert buy["qty"] == pytest.approx(50, abs=1e-12)
    assert buy["price"] == pytest.approx(102.204, abs=1e-12)
    assert buy["fee"] == pytest.approx(5.1102, abs=1e-12)

    assert sell["ts"] == T0 + 6 * H
    assert sell["decision_ts"] == T0 + 6 * H  # d_5
    assert sell["side"] == "sell"
    assert sell["qty"] == pytest.approx(50, abs=1e-12)
    assert sell["price"] == pytest.approx(104.79, abs=1e-12)
    assert sell["fee"] == pytest.approx(5.2395, abs=1e-12)


def test_last_bar_signal_dropped_and_logged(result) -> None:  # type: ignore[no-untyped-def]
    rej = result.rejections
    assert list(rej["reason"]) == ["DROPPED_LAST_BAR"]
    assert rej.iloc[0]["symbol"] == SYM
    assert rej.iloc[0]["ts"] == T0 + 10 * H  # d_9


def test_result_metadata(result) -> None:  # type: ignore[no-untyped-def]
    assert result.initial_capital == V0
    assert result.timeframe == "1h"
    assert result.meta["carried_forward_marks"] == {SYM: 0}
