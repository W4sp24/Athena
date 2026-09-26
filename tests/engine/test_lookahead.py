"""Look-ahead property tests (math.md §7.5 analogue for bars, FR-13).

1. Perturbing any bars after t changes no decision at or before d_t, no fill at or before
   tau_t, no equity mark at or before t, and no log row at or before tau_t.
2. A strategy never sees a bar whose close is after ``view.now``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cryptolab.config import TradingMode
from cryptolab.engine import run_backtest
from cryptolab.engine.types import BacktestResult, MarketView
from tests.engine.helpers import T0, make_bars, paper_limits, research_limits

H = timedelta(hours=1)
SYMS = ("AAA/USDT", "BBB/USDT", "CCC/USDT")


class Recorder:
    """Deterministic trend-follower that records what it saw and what it decided."""

    name = "recorder"

    def __init__(self) -> None:
        self.decisions: list[tuple[datetime, dict[str, float]]] = []
        self.violations: list[str] = []

    def on_bar(self, view: MarketView) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in view.symbols:
            b = view.bars(s, 4)
            full = view.bars(s)
            for frame in (b, full):
                if len(frame) and frame["ts"].max() + H > view.now:
                    self.violations.append(f"{s}: saw bar {frame['ts'].max()} at {view.now}")
            if len(b) >= 2:
                r = b["close"].iloc[-1] / b["close"].iloc[0] - 1
                out[s] = float(np.clip(r * 20, -0.4, 0.4))
        self.decisions.append((view.now, out))
        return out


@st.composite
def scenario(draw: st.DrawFn) -> dict[str, Any]:
    n = draw(st.integers(3, 30))
    k = draw(st.integers(0, n - 1))
    n_sym = draw(st.integers(1, 3))
    bars: dict[str, pd.DataFrame] = {}
    perturbed: dict[str, pd.DataFrame] = {}
    for s in SYMS[:n_sym]:
        rets = draw(st.lists(st.floats(-0.15, 0.15), min_size=n, max_size=n))
        close = 100 * np.exp(np.cumsum(rets))
        gap = draw(st.lists(st.floats(-0.03, 0.03), min_size=n, max_size=n))
        opens = np.r_[100.0, close[:-1]] * np.exp(gap)
        drop = draw(st.sets(st.integers(1, n - 1), max_size=n // 3))
        base = make_bars(opens, close, drop=sorted(drop))
        bars[s] = base

        # perturb every bar after t = k: new prices, and maybe delete/restore rows
        after = base["ts"] > T0 + k * H
        mult = draw(st.lists(st.floats(0.5, 2.0), min_size=len(base), max_size=len(base)))
        p = base.copy()
        m = np.where(after, np.asarray(mult), 1.0)
        for c in ("open", "high", "low", "close"):
            p[c] = p[c] * m
        p.loc[after, "volume"] = p.loc[after, "volume"] * 3
        if draw(st.booleans()):
            keep = ~after | np.asarray(
                draw(st.lists(st.booleans(), min_size=len(p), max_size=len(p)))
            )
            p = p[keep].reset_index(drop=True)
        perturbed[s] = p
    return {"bars": bars, "perturbed": perturbed, "k": k}


def _run(bars: dict[str, pd.DataFrame], strat: Recorder, paper: bool) -> BacktestResult:
    return run_backtest(
        bars,
        strat,
        timeframe="1h",
        initial_capital=10_000.0,
        fee_bps=10,
        slippage_bps=5,
        risk_limits=paper_limits() if paper else research_limits(),
        mode=TradingMode.PAPER if paper else TradingMode.BACKTEST,
    )


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sc=scenario(), paper=st.booleans())
def test_future_bars_do_not_change_the_past(sc: dict[str, Any], paper: bool) -> None:
    k: int = sc["k"]
    tau_k = T0 + k * H
    d_k = tau_k + H

    s1, s2 = Recorder(), Recorder()
    r1 = _run(sc["bars"], s1, paper)
    r2 = _run(sc["perturbed"], s2, paper)

    # Deleting every row after k can end the perturbed run's bar grid early. A shorter horizon
    # is not information: compare only the span both runs cover.
    tau_k = min(tau_k, r2.equity.index[-1])
    d_k = min(d_k, s2.decisions[-1][0]) if s2.decisions else d_k

    # every decision at or before d_k is identical
    dec1 = [d for d in s1.decisions if d[0] <= d_k]
    dec2 = [d for d in s2.decisions if d[0] <= d_k]
    assert dec1 == dec2

    # equity marks at or before bar k are identical
    pd.testing.assert_series_equal(r1.equity.loc[:tau_k], r2.equity.loc[:tau_k])

    # fills at or before tau_k are identical
    f1 = r1.fills[r1.fills["ts"] <= tau_k].reset_index(drop=True)
    f2 = r2.fills[r2.fills["ts"] <= tau_k].reset_index(drop=True)
    pd.testing.assert_frame_equal(f1, f2)

    # log rows at or before tau_k are identical
    j1 = r1.rejections[r1.rejections["ts"] <= tau_k].reset_index(drop=True)
    j2 = r2.rejections[r2.rejections["ts"] <= tau_k].reset_index(drop=True)
    pd.testing.assert_frame_equal(j1, j2)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sc=scenario())
def test_strategy_never_sees_a_future_bar(sc: dict[str, Any]) -> None:
    s = Recorder()
    _run(sc["bars"], s, paper=False)
    assert s.violations == []
    assert len(s.decisions) >= 1


@settings(max_examples=100, deadline=None)
@given(sc=scenario())
def test_fills_are_never_before_their_decision(sc: dict[str, Any]) -> None:
    r = _run(sc["bars"], Recorder(), paper=False)
    if len(r.fills):
        # fill at the open of the bar that starts exactly at the decision time (math.md §5.2)
        assert (r.fills["ts"] == r.fills["decision_ts"]).all()
