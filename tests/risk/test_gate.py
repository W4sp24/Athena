"""RiskGate checks in risk-model.md §2 order, and on_mark kill-switch triggers (§3).

FR-24 (limits outside the strategy), FR-25 (kill switch), FR-26 (no shorts outside backtest),
NFR-07 (fail closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from cryptolab.config import RiskLimits, TradingMode
from cryptolab.risk import (
    InMemoryKillSwitch,
    KillSwitch,
    KillSwitchState,
    OrderIntent,
    PortfolioState,
    Reason,
    RiskGate,
    RiskMarks,
    TripReason,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
BAR = timedelta(hours=1)


def limits(**over: Any) -> RiskLimits:
    base: dict[str, Any] = {
        "allowed_symbols": None,
        "max_position_pct_equity": 100,
        "max_position_notional": None,
        "max_gross_exposure_pct": 100,
        "max_order_notional": None,
        "min_order_notional": 10,
        "max_orders_per_hour": None,
        "allow_short": True,
        "stale_data_seconds": None,
        "max_daily_loss_pct": 100,
        "max_drawdown_pct": 100,
    }
    return RiskLimits.model_validate(base | over)


def gate(
    mode: TradingMode = TradingMode.BACKTEST,
    ks: KillSwitch | None = None,
    universe: tuple[str, ...] | None = ("BTC/USDT", "ETH/USDT"),
    **over: Any,
) -> RiskGate:
    return RiskGate(
        limits(**over),
        mode,
        ks if ks is not None else InMemoryKillSwitch(),
        universe=universe,
        bar_seconds=3600,
    )


def intent(
    side: str = "buy",
    qty: float = 1.0,
    price: float = 100.0,
    symbol: str = "BTC/USDT",
    bar_ts: datetime | None = None,
) -> OrderIntent:
    return OrderIntent(
        strategy="t",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=qty,
        ref_price=price,
        decision_ts=NOW,
        bar_ts=bar_ts if bar_ts is not None else NOW - BAR,
        signal_inputs={},
    )


def state(
    positions: Mapping[str, float] | None = None,
    equity: float = 10_000.0,
    marks: Mapping[str, float] | None = None,
) -> PortfolioState:
    pos = dict(positions or {})
    mk = dict(marks) if marks is not None else dict.fromkeys(pos, 100.0)
    cash = equity - sum(q * mk.get(s, 0.0) for s, q in pos.items())
    return PortfolioState(cash=cash, positions=pos, marks=mk, equity=equity)


# ------------------------------------------------------------------ happy path


def test_plain_order_is_approved_unchanged() -> None:
    v = gate().check(intent(qty=10), state(), NOW)
    assert v.approved
    assert v.reason is Reason.APPROVED
    assert v.adjusted_qty is None
    assert v.final_qty(intent(qty=10)) == 10


# ------------------------------------------------------------ 1. kill switch


def test_tripped_kill_switch_rejects_everything() -> None:
    ks = InMemoryKillSwitch()
    ks.trip(TripReason.MANUAL, NOW)
    g = gate(ks=ks)
    v = g.check(intent(), state(), NOW)
    assert not v.approved
    assert v.reason is Reason.KILL_SWITCH
    assert v.final_qty(intent()) == 0.0
    # even a risk-reducing sell is blocked (flatten is a separate, explicit command, D-010)
    assert not g.check(intent("sell", 1), state({"BTC/USDT": 5}), NOW).approved


class _ExplodingSwitch(InMemoryKillSwitch):
    def state(self) -> KillSwitchState:
        raise OSError("disk gone")

    def is_tripped(self) -> bool:
        raise OSError("disk gone")


def test_kill_switch_read_error_fails_closed() -> None:
    v = gate(ks=_ExplodingSwitch()).check(intent(), state(), NOW)
    assert not v.approved
    assert v.reason is Reason.INTERNAL_ERROR


# ------------------------------------------------------------ 2. mode / side


@pytest.mark.parametrize("mode", [TradingMode.PAPER, TradingMode.TESTNET])
def test_short_rejected_in_paper_and_testnet_even_if_config_allows(mode: TradingMode) -> None:
    v = gate(mode=mode, allow_short=True).check(intent("sell", 1), state(), NOW)
    assert not v.approved
    assert v.reason is Reason.SHORT_NOT_ALLOWED


@pytest.mark.parametrize("mode", [TradingMode.PAPER, TradingMode.TESTNET])
def test_selling_more_than_held_rejected_in_paper(mode: TradingMode) -> None:
    v = gate(mode=mode).check(intent("sell", 3), state({"BTC/USDT": 2}), NOW)
    assert v.reason is Reason.SHORT_NOT_ALLOWED


@pytest.mark.parametrize("mode", [TradingMode.PAPER, TradingMode.TESTNET])
def test_selling_a_long_is_fine_in_paper(mode: TradingMode) -> None:
    v = gate(mode=mode).check(intent("sell", 2), state({"BTC/USDT": 2}), NOW)
    assert v.approved


def test_short_rejected_in_backtest_when_config_forbids() -> None:
    v = gate(allow_short=False).check(intent("sell", 1), state(), NOW)
    assert v.reason is Reason.SHORT_NOT_ALLOWED


def test_short_allowed_in_backtest_when_config_allows() -> None:
    assert gate(allow_short=True).check(intent("sell", 1), state(), NOW).approved


def test_covering_a_short_is_not_a_short() -> None:
    v = gate(allow_short=False).check(intent("buy", 1), state({"BTC/USDT": -2}), NOW)
    assert v.approved


@pytest.mark.parametrize(
    "bad",
    [
        {"qty": 0.0},
        {"qty": -1.0},
        {"qty": float("nan")},
        {"qty": float("inf")},
        {"price": 0.0},
        {"price": float("nan")},
        {"side": "hold"},
    ],
)
def test_malformed_intent_rejected(bad: dict[str, Any]) -> None:
    v = gate().check(intent(**bad), state(), NOW)
    assert not v.approved
    assert v.reason is Reason.INVALID_ORDER


# ------------------------------------------------------------ 3. symbol whitelist


def test_symbol_not_in_explicit_whitelist() -> None:
    g = gate(allowed_symbols=["ETH/USDT"])
    assert g.check(intent(symbol="BTC/USDT"), state(), NOW).reason is Reason.SYMBOL_NOT_ALLOWED
    assert g.check(intent(symbol="ETH/USDT"), state(), NOW).approved


def test_null_whitelist_means_run_universe() -> None:
    g = gate(universe=("BTC/USDT",))
    assert g.check(intent(symbol="ETH/USDT"), state(), NOW).reason is Reason.SYMBOL_NOT_ALLOWED


def test_null_whitelist_without_universe_rejects_all() -> None:
    """Fail closed: no whitelist and no run universe means nothing is allowed."""
    g = gate(universe=None)
    assert g.check(intent(), state(), NOW).reason is Reason.SYMBOL_NOT_ALLOWED


# ------------------------------------------------------------ 4. data freshness


def test_fresh_bar_ok_up_to_two_bar_lengths() -> None:
    assert gate().check(intent(bar_ts=NOW - 2 * BAR), state(), NOW).approved


def test_stale_bar_rejected() -> None:
    v = gate().check(intent(bar_ts=NOW - 2 * BAR - timedelta(seconds=1)), state(), NOW)
    assert v.reason is Reason.STALE_DATA


def test_explicit_stale_seconds() -> None:
    g = gate(stale_data_seconds=60)
    assert g.check(intent(bar_ts=NOW - BAR), state(), NOW).reason is Reason.STALE_DATA


def test_future_bar_rejected() -> None:
    v = gate().check(intent(bar_ts=NOW + timedelta(seconds=1)), state(), NOW)
    assert v.reason is Reason.INVALID_ORDER


def test_null_stale_seconds_needs_bar_length() -> None:
    with pytest.raises(ValueError, match="bar_seconds"):
        RiskGate(limits(), TradingMode.BACKTEST, InMemoryKillSwitch(), universe=("BTC/USDT",))


def test_naive_now_rejected() -> None:
    v = gate().check(intent(), state(), datetime(2026, 3, 1, 12))  # noqa: DTZ001
    assert v.reason is Reason.INVALID_ORDER


# ------------------------------------------------------------ 5. order rate


def test_order_rate_limit() -> None:
    g = gate(max_orders_per_hour=2)
    assert g.check(intent(), state(), NOW).approved
    assert g.check(intent(), state(), NOW + timedelta(minutes=10)).approved
    v = g.check(intent(), state(), NOW + timedelta(minutes=20))
    assert v.reason is Reason.ORDER_RATE
    # the window is a trailing hour, inclusive of its start
    assert g.check(intent(), state(), NOW + BAR).reason is Reason.ORDER_RATE
    later = NOW + BAR + timedelta(seconds=1)
    assert g.check(intent(bar_ts=later - BAR), state(), later).approved


def test_rejected_orders_do_not_count_toward_rate() -> None:
    g = gate(max_orders_per_hour=1, max_order_notional=50)
    assert not g.check(intent(qty=1), state(), NOW).approved  # 100 > 50
    assert g.check(intent(qty=0.4), state(), NOW).approved


# ------------------------------------------------------------ 6. per-order notional


def test_order_notional_rejects_not_clips() -> None:
    v = gate(max_order_notional=500).check(intent(qty=5.01), state(), NOW)
    assert not v.approved
    assert v.reason is Reason.ORDER_NOTIONAL
    assert gate(max_order_notional=500).check(intent(qty=5), state(), NOW).approved


# ------------------------------------------------------------ 7. post-fill per symbol


def test_position_pct_clips_buy() -> None:
    # V = 10,000, 25% cap = 2,500 = 25 units at 100; already hold 10 -> may add 15
    v = gate(max_position_pct_equity=25).check(intent(qty=40), state({"BTC/USDT": 10}), NOW)
    assert v.approved
    assert v.reason is Reason.CLIPPED
    assert v.adjusted_qty == pytest.approx(15)


def test_position_notional_clips_buy() -> None:
    v = gate(max_position_notional=1000).check(intent(qty=40), state(), NOW)
    assert v.reason is Reason.CLIPPED
    assert v.adjusted_qty == pytest.approx(10)


def test_tighter_of_pct_and_notional_wins() -> None:
    v = gate(max_position_pct_equity=5, max_position_notional=1000).check(
        intent(qty=40), state(), NOW
    )
    assert v.adjusted_qty == pytest.approx(5)


def test_already_at_position_limit_rejects_increase() -> None:
    v = gate(max_position_pct_equity=25).check(intent(qty=1), state({"BTC/USDT": 25}), NOW)
    assert not v.approved
    assert v.reason is Reason.POSITION_LIMIT


def test_reducing_an_oversized_position_is_never_blocked() -> None:
    v = gate(max_position_pct_equity=25).check(intent("sell", 5), state({"BTC/USDT": 50}), NOW)
    assert v.approved
    assert v.adjusted_qty is None


def test_short_position_limit_clips() -> None:
    v = gate(max_position_pct_equity=25).check(intent("sell", 40), state(), NOW)
    assert v.reason is Reason.CLIPPED
    assert v.adjusted_qty == pytest.approx(25)


def test_flip_long_to_short_is_capped_on_the_short_side() -> None:
    # long 10, sell 60 -> would be short 50; cap 25 -> may sell 35
    v = gate(max_position_pct_equity=25).check(intent("sell", 60), state({"BTC/USDT": 10}), NOW)
    assert v.adjusted_qty == pytest.approx(35)


def test_clip_never_enlarges() -> None:
    v = gate(max_position_pct_equity=25).check(intent(qty=3), state(), NOW)
    assert v.adjusted_qty is None
    assert v.final_qty(intent(qty=3)) == 3


@pytest.mark.parametrize("eq", [0.0, -5.0, float("nan")])
def test_non_positive_equity_rejects(eq: float) -> None:
    v = gate().check(intent(), PortfolioState(cash=eq, positions={}, marks={}, equity=eq), NOW)
    assert v.reason is Reason.NON_POSITIVE_EQUITY


# ------------------------------------------------------------ 8. gross exposure


def test_gross_exposure_clips() -> None:
    # hold 70 ETH at 100 = 7,000 of 10,000; gross cap 100% -> BTC may add 3,000 = 30 units
    st = state({"ETH/USDT": 70})
    v = gate().check(intent(qty=50), st, NOW)
    assert v.reason is Reason.CLIPPED
    assert v.adjusted_qty == pytest.approx(30)


def test_gross_exposure_counts_shorts() -> None:
    st = state({"ETH/USDT": -80})
    v = gate().check(intent(qty=50), st, NOW)
    assert v.adjusted_qty == pytest.approx(20)


def test_gross_exposure_full_rejects() -> None:
    st = state({"ETH/USDT": 100})
    v = gate().check(intent(qty=1), st, NOW)
    assert v.reason is Reason.GROSS_EXPOSURE


def test_missing_mark_for_held_symbol_fails_closed() -> None:
    st = state({"ETH/USDT": 10}, marks={})
    v = gate().check(intent(qty=1), st, NOW)
    assert v.reason is Reason.MISSING_MARK


# ------------------------------------------------------------ 9. dust floor


def test_below_min_notional_rejected() -> None:
    v = gate().check(intent(qty=0.05), state(), NOW)  # 5 USDT < 10
    assert v.reason is Reason.BELOW_MIN_NOTIONAL


def test_clip_below_dust_rejected() -> None:
    # may only add 0.05 units (5 USDT) before hitting the cap -> dust
    v = gate(max_position_pct_equity=25).check(intent(qty=5), state({"BTC/USDT": 24.95}), NOW)
    assert v.reason is Reason.BELOW_MIN_NOTIONAL


# ------------------------------------------------------------ check order


def test_check_order_kill_switch_before_everything() -> None:
    ks = InMemoryKillSwitch()
    ks.trip(TripReason.MANUAL, NOW)
    g = gate(mode=TradingMode.PAPER, ks=ks, allowed_symbols=["ETH/USDT"])
    assert g.check(intent("sell", 1e9), state(), NOW).reason is Reason.KILL_SWITCH


def test_check_order_side_before_symbol() -> None:
    g = gate(mode=TradingMode.PAPER, allowed_symbols=["ETH/USDT"])
    assert g.check(intent("sell", 1), state(), NOW).reason is Reason.SHORT_NOT_ALLOWED


def test_check_order_symbol_before_staleness() -> None:
    g = gate(allowed_symbols=["ETH/USDT"])
    v = g.check(intent(bar_ts=NOW - 10 * BAR), state(), NOW)
    assert v.reason is Reason.SYMBOL_NOT_ALLOWED


def test_check_order_staleness_before_notional() -> None:
    g = gate(max_order_notional=1)
    assert g.check(intent(bar_ts=NOW - 10 * BAR), state(), NOW).reason is Reason.STALE_DATA


# ------------------------------------------------------------ on_mark triggers


def test_drawdown_trips_at_threshold() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks, max_drawdown_pct=15, max_daily_loss_pct=100)
    g.on_mark(100.0, NOW)
    g.on_mark(120.0, NOW + BAR)  # HWM
    g.on_mark(102.01, NOW + 2 * BAR)  # -14.99%
    assert not ks.is_tripped()
    g.on_mark(102.0, NOW + 3 * BAR)  # exactly -15%
    st = ks.state()
    assert st.tripped
    assert st.reason is TripReason.MAX_DRAWDOWN
    assert st.ts == NOW + 3 * BAR
    assert g.check(intent(bar_ts=NOW + 2 * BAR), state(), NOW + 3 * BAR).reason is (
        Reason.KILL_SWITCH
    )


def test_daily_loss_trips_against_first_mark_of_utc_day() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks, max_daily_loss_pct=3, max_drawdown_pct=100)
    day1 = datetime(2026, 3, 1, 22, 0, tzinfo=UTC)
    g.on_mark(100.0, day1)
    g.on_mark(90.0, day1 + BAR)  # 23:00 same day: -10% -> trips
    assert ks.state().reason is TripReason.DAILY_LOSS


def test_daily_anchor_rolls_at_midnight_utc() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks, max_daily_loss_pct=3, max_drawdown_pct=100)
    g.on_mark(100.0, datetime(2026, 3, 1, 23, 0, tzinfo=UTC))
    g.on_mark(98.0, datetime(2026, 3, 2, 0, 0, tzinfo=UTC))  # new day anchor = 98 (first mark)
    g.on_mark(95.1, datetime(2026, 3, 2, 5, 0, tzinfo=UTC))  # -2.96% vs 98
    assert not ks.is_tripped()
    assert ks.marks().anchor_equity == 98.0
    g.on_mark(95.0, datetime(2026, 3, 2, 6, 0, tzinfo=UTC))  # -3.06% vs 98
    assert ks.state().reason is TripReason.DAILY_LOSS


def test_daily_anchor_uses_utc_even_for_other_tz() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks, max_daily_loss_pct=3, max_drawdown_pct=100)

    manila = ZoneInfo("Asia/Manila")  # 07:00 Manila on 2 Mar == 23:00 UTC on 1 Mar
    g.on_mark(100.0, datetime(2026, 3, 1, 23, 0, tzinfo=UTC))
    g.on_mark(96.0, datetime(2026, 3, 2, 7, 30, tzinfo=manila))  # still 1 Mar UTC
    assert ks.state().reason is TripReason.DAILY_LOSS


def test_zero_equity_trips() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks)  # research profile thresholds of 100%
    g.on_mark(100.0, NOW)
    g.on_mark(0.0, NOW + BAR)
    assert ks.is_tripped()


def test_on_mark_rejects_non_finite_equity() -> None:
    with pytest.raises(ValueError, match="finite"):
        gate().on_mark(float("nan"), NOW)


def test_hwm_persisted_in_switch_marks() -> None:
    ks = InMemoryKillSwitch()
    g = gate(ks=ks)
    g.on_mark(100.0, NOW)
    g.on_mark(130.0, NOW + BAR)
    g.on_mark(110.0, NOW + 2 * BAR)
    assert ks.marks() == RiskMarks(hwm=130.0, anchor_day=NOW.date(), anchor_equity=100.0)


def test_hwm_survives_new_gate_on_same_switch() -> None:
    """A runner restart builds a new gate over the persisted switch; HWM must carry over."""
    ks = InMemoryKillSwitch()
    gate(ks=ks, max_drawdown_pct=15).on_mark(200.0, NOW)
    g2 = gate(ks=ks, max_drawdown_pct=15)
    g2.on_mark(169.0, NOW + BAR)  # -15.5% vs persisted HWM 200
    assert ks.state().reason is TripReason.MAX_DRAWDOWN


def test_on_mark_while_tripped_does_not_retrip() -> None:
    ks = InMemoryKillSwitch()
    ks.trip(TripReason.MANUAL, NOW)
    g = gate(ks=ks, max_drawdown_pct=15)
    g.on_mark(100.0, NOW)
    g.on_mark(10.0, NOW + BAR)
    assert ks.state().reason is TripReason.MANUAL
