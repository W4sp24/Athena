"""The baseline strategies are leak-free end to end (math.md §7.5 analogue, FR-13)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cryptolab.engine import run_backtest
from cryptolab.engine.types import MarketView, Strategy
from cryptolab.strategies.buy_and_hold import BuyAndHold
from cryptolab.strategies.ma_crossover import MACrossover
from cryptolab.strategies.momentum import Momentum
from tests.engine.helpers import T0, research_limits
from tests.engine.test_lookahead import scenario

H = timedelta(hours=1)

FACTORIES: dict[str, Callable[[], Strategy]] = {
    "buy_and_hold": BuyAndHold,
    "ma_crossover": lambda: MACrossover(fast=2, slow=4),
    "momentum": lambda: Momentum(lookback=3),
}


class Recording:
    name = "recording"

    def __init__(self, inner: Strategy) -> None:
        self.inner = inner
        self.decisions: list[tuple[datetime, dict[str, float]]] = []

    def on_bar(self, view: MarketView) -> dict[str, float]:
        w = self.inner.on_bar(view)
        self.decisions.append((view.now, dict(w)))
        return w


def _run(bars: dict[str, pd.DataFrame], s: Recording) -> Any:
    return run_backtest(
        bars,
        s,
        timeframe="1h",
        initial_capital=10_000.0,
        fee_bps=10,
        slippage_bps=5,
        risk_limits=research_limits(),
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sc=scenario(), which=st.sampled_from(sorted(FACTORIES)))
def test_baselines_ignore_the_future(sc: dict[str, Any], which: str) -> None:
    k = sc["k"]
    tau_k = T0 + k * H
    s1, s2 = Recording(FACTORIES[which]()), Recording(FACTORIES[which]())
    r1, r2 = _run(sc["bars"], s1), _run(sc["perturbed"], s2)
    assert [d for d in s1.decisions if d[0] <= tau_k + H] == [
        d for d in s2.decisions if d[0] <= tau_k + H
    ]
    pd.testing.assert_series_equal(r1.equity.loc[:tau_k], r2.equity.loc[:tau_k])
    pd.testing.assert_frame_equal(
        r1.fills[r1.fills["ts"] <= tau_k].reset_index(drop=True),
        r2.fills[r2.fills["ts"] <= tau_k].reset_index(drop=True),
    )
