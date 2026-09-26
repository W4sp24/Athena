"""Engine behaviour: fills, fees, slippage, cash, affordability, shorts, edge cases.

FR-13 (timing), FR-14/15 (fees, slippage), FR-16 (accounting), NFR-01 (determinism).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from cryptolab.config import TradingMode
from cryptolab.engine import run_backtest
from cryptolab.engine.types import BacktestResult, MarketView
from tests.engine.helpers import T0, Script, make_bars, paper_limits, research_limits

H = timedelta(hours=1)
A, B = "AAA/USDT", "BBB/USDT"


def bt(bars: dict[str, pd.DataFrame], strat: Any, **kw: Any) -> BacktestResult:
    args: dict[str, Any] = {
        "timeframe": "1h",
        "initial_capital": 10_000.0,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
        "risk_limits": research_limits(),
    }
    return run_backtest(bars, strat, **(args | kw))


# ------------------------------------------------------------------ fill model


def test_zero_costs_fill_at_next_open() -> None:
    bars = {A: make_bars([10, 12, 13, 14], [11, 12.5, 13.5, 14])}
    r = bt(bars, Script({0: {A: 0.5}}), fee_bps=0, slippage_bps=0)
    f = r.fills.iloc[0]
    assert f["ts"] == T0 + H  # O_1, not C_0
    assert f["price"] == 12.0
    assert f["fee"] == 0.0
    assert f["qty"] == pytest.approx(0.5 * 10_000 / 11)  # sized at C_0 (§5.1)


def test_slippage_and_fee_formulas() -> None:
    """§5.2 p = O(1 +/- s); §5.3 fee = f |dq| p; §5.4 cash."""
    bars = {A: make_bars([100, 100, 100, 100, 100])}
    r = bt(bars, Script({0: {A: 0.5}, 2: {A: 0.0}}), fee_bps=25, slippage_bps=50)
    buy, sell = r.fills.iloc[0], r.fills.iloc[1]
    q = 50.0
    assert buy["price"] == pytest.approx(100 * 1.005)
    assert buy["fee"] == pytest.approx(0.0025 * q * 100.5)
    assert sell["price"] == pytest.approx(100 * 0.995)
    assert sell["fee"] == pytest.approx(0.0025 * q * 99.5)
    cash = 10_000 - q * 100.5 - 0.0025 * q * 100.5 + q * 99.5 - 0.0025 * q * 99.5
    assert r.equity.iloc[-1] == pytest.approx(cash, abs=1e-9)
    # while holding, V = c + q C
    assert r.equity.iloc[1] == pytest.approx(10_000 - q * 100.5 * 1.0025 + q * 100)


def test_equity_indexed_by_bar_open_time() -> None:
    bars = {A: make_bars([1, 2, 3])}
    r = bt(bars, Script({}))
    assert list(r.equity.index) == list(pd.date_range(T0, periods=3, freq="h", tz="UTC"))
    assert str(r.equity.index.tz) == "UTC"
    assert (r.equity == 10_000).all()


# ------------------------------------------------------------ affordability


def test_buy_scaled_for_cash_when_open_gaps_up() -> None:
    """§5.5: dq = c / (p (1 + f)), logged as scaled_for_cash."""
    bars = {A: make_bars([100, 110, 110], [100, 110, 110])}
    r = bt(bars, Script({0: {A: 1.0}}), fee_bps=10, slippage_bps=0)
    f = r.fills.iloc[0]
    assert "scaled_for_cash" in f["notes"]
    assert f["qty"] == pytest.approx(10_000 / (110 * 1.001))
    assert r.equity.iloc[1] == pytest.approx(f["qty"] * 110, rel=1e-12)  # cash ~ 0


def test_no_cash_left_for_second_buy() -> None:
    bars = {A: make_bars([100, 200, 200]), B: make_bars([100, 200, 200])}
    r = bt(bars, Script({0: {A: 0.5, B: 0.5}}), fee_bps=0, slippage_bps=0)
    # A (alphabetical) costs 50 * 200 = 10,000: exactly all cash, so it is not scaled
    assert r.fills["symbol"].tolist() == [A]
    assert r.fills.iloc[0]["notes"] == ""
    assert r.rejections["reason"].tolist() == ["NO_CASH"]
    assert r.rejections.iloc[0]["ts"] == T0 + H


def test_scaled_buy_below_dust_is_not_filled() -> None:
    # Both approved at C_0 = 100 (gross 100%). The open gaps to 199.9: A costs 50 * 199.9 =
    # 9,995, leaving 5 USDT, so B scales to 5 / 199.9 units = 5 USDT < 10 dust floor.
    bars = {A: make_bars([100, 199.9, 199.9]), B: make_bars([100, 199.9, 199.9])}
    r = bt(bars, Script({0: {A: 0.5, B: 0.5}}), fee_bps=0, slippage_bps=0)
    assert r.fills["symbol"].tolist() == [A]
    assert r.rejections["reason"].tolist() == ["SCALED_BELOW_MIN_NOTIONAL"]


def test_sells_processed_before_buys() -> None:
    """§5.5: cash from sells is available to buys in the same bar."""
    bars = {A: make_bars([100] * 5), B: make_bars([50] * 5)}
    r = bt(bars, Script({0: {A: 0.95}, 1: {A: 0.0, B: 0.95}}), fee_bps=10, slippage_bps=10)
    same_bar = r.fills[r.fills["ts"] == T0 + 2 * H]
    assert same_bar["side"].tolist() == ["sell", "buy"]
    assert same_bar["symbol"].tolist() == [A, B]
    assert "scaled_for_cash" not in same_bar.iloc[1]["notes"]


# ------------------------------------------------------------------ shorts


def test_short_in_backtest_credits_cash() -> None:
    """§5.6: q < 0 with sale proceeds credited to cash; no borrow fee."""
    bars = {A: make_bars([100, 100, 90, 90])}
    r = bt(bars, Script({0: {A: -0.5}}), fee_bps=0, slippage_bps=0)
    f = r.fills.iloc[0]
    assert f["side"] == "sell"
    assert f["qty"] == pytest.approx(50)
    # V = (10,000 + 50*100) + (-50)*90 = 10,500
    assert r.equity.iloc[2] == pytest.approx(10_500)
    assert r.meta["uses_shorts"] is True
    assert "borrow" in r.meta["short_note"]


@pytest.mark.parametrize("mode", [TradingMode.PAPER, TradingMode.TESTNET])
def test_short_rejected_in_paper_and_testnet(mode: TradingMode) -> None:
    bars = {A: make_bars([100] * 4)}
    lim = research_limits(allow_short=True)  # config says yes; mode must still say no
    r = bt(bars, Script({0: {A: -0.5}}), mode=mode, risk_limits=lim)
    assert r.fills.empty
    assert r.rejections["reason"].tolist() == ["SHORT_NOT_ALLOWED"]
    assert r.meta["uses_shorts"] is False


# ------------------------------------------------------------ §5.7 edge cases


def test_missing_next_bar_expires_order_and_carries_mark() -> None:
    bars = {A: make_bars([100, 101, 102, 103, 104], [100, 101, 102, 103, 104], drop=[2])}
    r = bt(bars, Script({1: {A: 0.5}}))
    assert r.fills.empty
    rej = r.rejections
    assert rej["reason"].tolist() == ["EXPIRED_NO_NEXT_BAR"]
    assert rej.iloc[0]["ts"] == T0 + 2 * H
    assert r.meta["carried_forward_marks"] == {A: 1}
    assert len(r.equity) == 5


def test_carry_forward_uses_last_close() -> None:
    bars = {
        A: make_bars([100, 100, 100, 100], [100, 110, 999, 120], drop=[2]),
    }
    r = bt(bars, Script({0: {A: 0.5}}), fee_bps=0, slippage_bps=0)
    q = r.fills.iloc[0]["qty"]
    cash = 10_000 - q * 100
    assert r.equity.iloc[2] == pytest.approx(cash + q * 110)  # bar 2 missing -> C_1
    assert r.equity.iloc[3] == pytest.approx(cash + q * 120)


def test_bar_missing_for_every_symbol_is_still_on_the_grid() -> None:
    bars = {A: make_bars([1, 2, 3, 4], drop=[1]), B: make_bars([1, 2, 3, 4], drop=[1])}
    r = bt(bars, Script({}))
    assert len(r.equity) == 4
    assert r.meta["carried_forward_marks"] == {A: 1, B: 1}
    assert r.meta["carried_forward_marks_total"] == 2


def test_single_bar_run_drops_signal() -> None:
    r = bt({A: make_bars([100])}, Script({0: {A: 1.0}}))
    assert r.equity.tolist() == [10_000]
    assert r.rejections["reason"].tolist() == ["DROPPED_LAST_BAR"]


def test_symbol_without_data_yet_has_no_price() -> None:
    late = make_bars([100, 100], start=T0 + 2 * H)
    bars = {A: make_bars([100] * 4), B: late}
    r = bt(bars, Script({0: {B: 0.5}}))
    assert r.rejections["reason"].tolist() == ["NO_PRICE"]


def test_unknown_symbol_and_bad_weights_are_logged() -> None:
    bars = {A: make_bars([100] * 3)}
    r = bt(bars, Script({0: {"ZZZ/USDT": 0.5, A: float("nan")}}))
    assert sorted(r.rejections["reason"]) == ["INVALID_WEIGHT", "UNKNOWN_SYMBOL"]
    r2 = bt(bars, Script({0: {A: "lots"}}))  # type: ignore[dict-item]
    assert r2.rejections["reason"].tolist() == ["INVALID_WEIGHT"]


def test_strategy_returning_non_mapping_is_an_error() -> None:
    with pytest.raises(TypeError, match="mapping"):
        bt({A: make_bars([100] * 3)}, Script(lambda t, v: [0.5]))  # type: ignore[arg-type,return-value]


def test_dust_rebalances_are_skipped() -> None:
    """§5.1: |dq| C < min_order_notional is skipped, not logged as a rejection."""
    bars = {A: make_bars([100, 100, 100.01, 100.02, 100.03])}
    r = bt(bars, Script(lambda t, v: {A: 0.5}), fee_bps=0, slippage_bps=0)
    assert len(r.fills) == 1
    assert r.rejections.empty
    assert r.meta["skipped_dust_orders"] >= 1


def test_omitted_symbol_keeps_position() -> None:
    bars = {A: make_bars([100] * 5)}
    r = bt(bars, Script({0: {A: 0.5}}), fee_bps=0, slippage_bps=0)
    assert len(r.fills) == 1


# ------------------------------------------------------------ RiskGate in the loop


def test_paper_profile_clips_position_and_logs_clip() -> None:
    bars = {A: make_bars([100] * 4)}
    # w = 0.4 -> 400 USDT order (<= 500 per-order cap), then clipped to 25% of 1,000
    r = bt(bars, Script({0: {A: 0.4}}), initial_capital=1_000.0, risk_limits=paper_limits())
    f = r.fills.iloc[0]
    assert f["notes"] == "clipped"
    assert f["qty"] == pytest.approx(2.5)  # 25% of 1,000 at 100
    assert r.rejections["reason"].tolist() == ["clipped"]


def test_paper_profile_rejects_large_order() -> None:
    bars = {A: make_bars([100] * 4)}
    r = bt(bars, Script({0: {A: 0.25}}), initial_capital=10_000.0, risk_limits=paper_limits())
    assert r.fills.empty
    assert r.rejections["reason"].tolist() == ["ORDER_NOTIONAL"]


def test_gross_exposure_accounts_for_earlier_intents_in_same_bar() -> None:
    bars = {A: make_bars([100] * 4), B: make_bars([100] * 4)}
    r = bt(bars, Script({0: {A: 0.7, B: 0.7}}), fee_bps=0, slippage_bps=0)
    qty = dict(zip(r.fills["symbol"], r.fills["qty"], strict=True))
    assert qty[A] == pytest.approx(70)
    assert qty[B] == pytest.approx(30)  # clipped by projected gross exposure


def test_kill_switch_trips_and_halts_new_orders() -> None:
    closes = [100, 100, 50, 50, 50, 50]
    bars = {A: make_bars([100, 100, 50, 50, 50, 50], closes)}
    lim = research_limits(max_drawdown_pct=15)
    r = bt(bars, Script({0: {A: 0.9}, 3: {A: 0.0}}), risk_limits=lim)
    assert r.meta["kill_switch"]["tripped"] is True
    assert r.meta["kill_switch"]["reason"] in {"MAX_DRAWDOWN", "DAILY_LOSS"}
    assert r.fills["side"].tolist() == ["buy"]  # the exit was blocked (no auto-flatten, D-010)
    assert r.rejections["reason"].tolist() == ["KILL_SWITCH"]


def test_rate_limit_counts_in_loop() -> None:
    bars = {A: make_bars([100] * 6)}
    lim = research_limits(max_orders_per_hour=1)

    def flip(t: int, v: MarketView) -> dict[str, float]:
        return {A: 0.5 if t % 2 == 0 else 0.1}

    r = bt(bars, Script(flip), risk_limits=lim)
    # decisions are exactly 1h apart; the trailing-hour window includes its start
    assert "ORDER_RATE" in set(r.rejections["reason"])


# ------------------------------------------------------------ determinism, meta


def test_deterministic() -> None:
    rng = np.random.default_rng(7)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 200)))
    bars = {A: make_bars(px), B: make_bars(px[::-1].copy())}

    def plan(t: int, v: MarketView) -> dict[str, float]:
        return {A: (t % 7) / 10, B: (t % 5) / 10}

    r1, r2 = bt(bars, Script(plan)), bt(bars, Script(plan))
    pd.testing.assert_series_equal(r1.equity, r2.equity)
    pd.testing.assert_frame_equal(r1.fills, r2.fills)
    pd.testing.assert_frame_equal(r1.rejections, r2.rejections)
    assert r1.meta == r2.meta


def test_meta_contents() -> None:
    r = bt({A: make_bars([100] * 3)}, Script({}), meta={"git_sha": "abc", "bars": "spoof"})
    m = r.meta
    assert m["git_sha"] == "abc"
    assert m["bars"] == 3  # engine keys win
    assert len(m["config_hash"]) == 64
    assert m["risk_limits"]["max_position_pct_equity"] == 100
    assert m["data"][A]["rows"] == 3
    assert m["mode"] == "backtest"
    assert m["kill_switch"]["tripped"] is False
    assert (
        bt({A: make_bars([100] * 3)}, Script({}), fee_bps=11).meta["config_hash"]
        != (m["config_hash"])
    )


def test_result_frames_have_contract_columns_when_empty() -> None:
    r = bt({A: make_bars([100] * 3)}, Script({}))
    assert list(r.fills.columns) == [
        "ts",
        "decision_ts",
        "symbol",
        "side",
        "qty",
        "price",
        "fee",
        "notes",
    ]
    assert list(r.rejections.columns) == ["ts", "symbol", "reason", "detail"]


def test_daily_timeframe() -> None:
    bars = {A: make_bars([100, 110, 120], freq="D")}
    r = bt(bars, Script({0: {A: 0.5}}), timeframe="1d")
    assert r.fills.iloc[0]["ts"] == T0 + timedelta(days=1)


# ------------------------------------------------------------ input validation


def _ok() -> pd.DataFrame:
    return make_bars([100, 101, 102])


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.drop(columns=["volume"]), "missing OHLCV"),
        (lambda d: d.assign(ts=d["ts"].dt.tz_localize(None)), "tz-aware"),
        (lambda d: d.iloc[::-1].reset_index(drop=True), "strictly increasing"),
        (lambda d: pd.concat([d, d.iloc[[-1]]]).reset_index(drop=True), "strictly increasing"),
        (lambda d: d.assign(ts=d["ts"] + pd.Timedelta(minutes=1)), "aligned"),
        (lambda d: d.assign(close=[1.0, np.nan, 2.0]), "close"),
        (lambda d: d.assign(open=[1.0, 0.0, 2.0]), "open"),
        (lambda d: d.iloc[0:0], "no bars"),
    ],
)
def test_bad_frames_are_rejected(mutate: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        bt({A: mutate(_ok())}, Script({}))


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"timeframe": "7m"}, "timeframe"),
        ({"initial_capital": 0.0}, "initial_capital"),
        ({"initial_capital": float("inf")}, "initial_capital"),
        ({"fee_bps": -1.0}, "fee_bps"),
        ({"slippage_bps": float("nan")}, "slippage_bps"),
    ],
)
def test_bad_params_are_rejected(kw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        bt({A: _ok()}, Script({}), **kw)


def test_empty_bars_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        bt({}, Script({}))


def test_non_utc_input_is_converted() -> None:
    d = _ok()
    d["ts"] = d["ts"].dt.tz_convert("Asia/Manila")
    r = bt({A: d}, Script({}))
    assert str(r.equity.index.tz) == "UTC"
    assert r.equity.index[0] == T0
