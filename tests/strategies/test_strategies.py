"""Baseline strategies (FR-09..FR-12) and the registry (NFR-04)."""

from __future__ import annotations

import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from cryptolab.engine import MarketData, PointInTimeView, run_backtest
from cryptolab.strategies.buy_and_hold import BuyAndHold
from cryptolab.strategies.ma_crossover import MACrossover
from cryptolab.strategies.momentum import Momentum
from cryptolab.strategies.registry import available_strategies, discover, get_strategy
from tests.engine.helpers import T0, make_bars, research_limits

A, B, C = "AAA/USDT", "BBB/USDT", "CCC/USDT"


def view(bars: dict[str, list[float]], t: int, start_offsets: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    frames = {}
    for s, closes in bars.items():
        off = (start_offsets or {}).get(s, 0)
        frames[s] = make_bars(closes, closes, start=T0 + timedelta(hours=off))
    return PointInTimeView(MarketData(frames, 3600), t)


# ------------------------------------------------------------------ buy and hold


def test_buy_and_hold_equal_weight_once() -> None:
    s = BuyAndHold()
    assert s.name == "buy_and_hold"
    assert s.on_bar(view({A: [1, 2, 3], B: [1, 2, 3]}, 0)) == {A: 0.5, B: 0.5}
    assert s.on_bar(view({A: [1, 2, 3], B: [1, 2, 3]}, 1)) == {}  # then holds


def test_buy_and_hold_waits_for_every_symbol() -> None:
    s = BuyAndHold(symbols=[A, B])
    bars = {A: [1, 2, 3, 4], B: [1, 2]}
    assert s.on_bar(view(bars, 0, {B: 2})) == {}
    assert s.on_bar(view(bars, 1, {B: 2})) == {}
    assert s.on_bar(view(bars, 2, {B: 2})) == {A: 0.5, B: 0.5}
    assert s.on_bar(view(bars, 3, {B: 2})) == {}


def test_buy_and_hold_explicit_weights() -> None:
    s = BuyAndHold(weights={A: 0.7, B: 0.2})
    assert s.on_bar(view({A: [1, 2], B: [1, 2], C: [1, 2]}, 0)) == {A: 0.7, B: 0.2}


@pytest.mark.parametrize(
    "kw",
    [
        {"weights": {A: -0.1}},
        {"weights": {A: 0.8, B: 0.3}},
        {"weights": {A: float("nan")}},
        {"weights": {}},
        {"symbols": []},
        {"symbols": [A], "weights": {A: 1.0}},
    ],
)
def test_buy_and_hold_bad_params(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        BuyAndHold(**kw)  # type: ignore[arg-type]


def test_buy_and_hold_matches_closed_form_scaled() -> None:
    """Day-4 requirement: engine buy-and-hold == hand calculation.

    w = 1 is sized at C_0 but filled at p = O_1 (1 + s), so it is scaled for cash (§5.5):
    q = V0 / (p (1 + f)), cash = 0, and V_t = q C_t for t >= 1.
    """
    opens = [100.0, 101.0, 103.0, 99.0, 104.0, 108.0]
    closes = [100.0, 102.0, 101.0, 100.0, 107.0, 110.0]
    v0, f, s = 10_000.0, 0.001, 0.0005
    r = run_backtest(
        {A: make_bars(opens, closes)},
        BuyAndHold(),
        timeframe="1h",
        initial_capital=v0,
        fee_bps=10,
        slippage_bps=5,
        risk_limits=research_limits(),
    )
    p = 101.0 * (1 + s)
    q = v0 / (p * (1 + f))
    expected = [v0] + [q * c for c in closes[1:]]
    assert np.max(np.abs(r.equity.to_numpy() - expected)) <= 1e-8 * v0
    assert len(r.fills) == 1
    assert r.fills.iloc[0]["notes"] == "scaled_for_cash"


def test_buy_and_hold_matches_closed_form_unscaled() -> None:
    """w = 0.5: q = 0.5 V0 / C_0, cash = V0 - q p (1 + f), V_t = cash + q C_t."""
    opens = [200.0, 205.0, 190.0, 210.0]
    closes = [200.0, 198.0, 195.0, 220.0]
    v0, f, s = 5_000.0, 0.00075, 0.001
    r = run_backtest(
        {A: make_bars(opens, closes)},
        BuyAndHold(weights={A: 0.5}),
        timeframe="1h",
        initial_capital=v0,
        fee_bps=7.5,
        slippage_bps=10,
        risk_limits=research_limits(),
    )
    q = 0.5 * v0 / 200.0
    p = 205.0 * (1 + s)
    cash = v0 - q * p - f * q * p
    expected = [v0] + [cash + q * c for c in closes[1:]]
    assert np.max(np.abs(r.equity.to_numpy() - expected)) <= 1e-8 * v0


# ------------------------------------------------------------------ MA crossover


def test_ma_crossover_signals() -> None:
    s = MACrossover(fast=2, slow=3)
    assert s.name == "ma_crossover"
    up = {A: [1.0, 2.0, 3.0, 4.0]}
    assert s.on_bar(view(up, 1)) == {}  # fewer than `slow` bars: no opinion
    assert s.on_bar(view(up, 2)) == {A: 1.0}  # SMA2 2.5 > SMA3 2.0
    down = {A: [4.0, 3.0, 2.0]}
    assert s.on_bar(view(down, 2)) == {A: 0.0}
    flat = {A: [2.0, 2.0, 2.0]}
    assert s.on_bar(view(flat, 2)) == {A: 0.0}  # not strictly greater


def test_ma_crossover_splits_weight_across_symbols() -> None:
    s = MACrossover(fast=1, slow=2, symbols=[A, B])
    bars = {A: [1.0, 2.0], B: [2.0, 1.0], C: [1.0, 5.0]}
    assert s.on_bar(view(bars, 1)) == {A: 0.5, B: 0.0}


@pytest.mark.parametrize(("fast", "slow"), [(3, 3), (5, 2), (0, 2)])
def test_ma_crossover_bad_windows(fast: int, slow: int) -> None:
    with pytest.raises(ValueError):
        MACrossover(fast=fast, slow=slow)


# ------------------------------------------------------------------ momentum


def test_momentum_equal_weight_among_qualifiers() -> None:
    s = Momentum(lookback=2)
    assert s.name == "momentum"
    bars = {A: [1.0, 1.5, 2.0], B: [1.0, 1.1, 1.2], C: [2.0, 1.5, 1.0]}
    assert s.on_bar(view(bars, 1)) == {}  # needs lookback + 1 bars
    assert s.on_bar(view(bars, 2)) == {A: 0.5, B: 0.5, C: 0.0}


def test_momentum_threshold() -> None:
    s = Momentum(lookback=2, threshold=0.5)
    bars = {A: [1.0, 1.5, 2.0], B: [1.0, 1.1, 1.2]}
    assert s.on_bar(view(bars, 2)) == {A: 1.0, B: 0.0}


def test_momentum_nothing_qualifies() -> None:
    s = Momentum(lookback=1)
    assert s.on_bar(view({A: [2.0, 1.0], B: [2.0, 2.0]}, 1)) == {A: 0.0, B: 0.0}


def test_momentum_bad_params() -> None:
    with pytest.raises(ValueError):
        Momentum(lookback=0)
    with pytest.raises(ValueError):
        Momentum(lookback=3, threshold=float("inf"))


# ------------------------------------------------------------------ registry


def test_registry_finds_the_baselines() -> None:
    assert {"buy_and_hold", "ma_crossover", "momentum"} <= set(available_strategies())
    s = get_strategy("ma_crossover", fast=3, slow=10)
    assert isinstance(s, MACrossover)
    assert s.fast == 3
    assert get_strategy("buy_and_hold").name == "buy_and_hold"


def test_registry_unknown_name() -> None:
    with pytest.raises(KeyError, match="momentum"):
        get_strategy("nope")


def test_adding_a_strategy_is_one_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-04: discovery is by module scan; no registration list to edit."""
    pkg = tmp_path / "fakestrats"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "always_long.py").write_text(
        textwrap.dedent(
            """
            class AlwaysLong:
                name = "always_long"

                def on_bar(self, view):
                    return {s: 1.0 / len(view.symbols) for s in view.symbols}


            class NotAStrategy:
                pass
            """
        )
    )
    (pkg / "_private.py").write_text(
        "class Hidden:\n    name = 'hidden'\n    def on_bar(self, v): return {}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    found = discover("fakestrats")
    assert set(found) == {"always_long"}
    sys.modules.pop("fakestrats", None)
    sys.modules.pop("fakestrats.always_long", None)


def test_registry_rejects_duplicate_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dupstrats"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    body = "class S{n}:\n    name = 'same'\n    def on_bar(self, v): return {{}}\n"
    (pkg / "one.py").write_text(body.format(n=1))
    (pkg / "two.py").write_text(body.format(n=2))
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ValueError, match="same"):
        discover("dupstrats")
