"""FIFO round-trip pairing and win rate (math.md §3.5).

Convention (see cryptolab/analytics/metrics.py): each FIFO match of an exit quantity
against an open entry lot is one round trip. Fees of both legs are allocated
pro rata to the matched quantity: matched_qty / fill_qty * fill_fee.
"""

from __future__ import annotations

import math

import pytest

from cryptolab.analytics.metrics import compute_metrics, fifo_round_trips
from tests.analytics.helpers import daily_equity, fill, fills_frame

EQ = daily_equity([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])


def test_simple_long_win() -> None:
    """buy 1 @ 100 (fee 0.10), sell 1 @ 110 (fee 0.11).

    gross = 1 * (110 - 100) = 10 ; fees = 0.10 + 0.11 = 0.21 ; net = 9.79 > 0 -> win
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.10),
            fill(2, "BTC/USDT", "sell", 1.0, 110.0, 0.11),
        ]
    )
    res = fifo_round_trips(fills)
    assert len(res.round_trips) == 1
    rt = res.round_trips[0]
    assert rt.direction == "long"
    assert rt.qty == pytest.approx(1.0)
    assert rt.fees == pytest.approx(0.21)
    assert rt.pnl == pytest.approx(9.79, abs=1e-12)
    assert rt.entry_ts < rt.exit_ts
    assert res.open_positions == {}
    assert not res.has_shorts

    m = compute_metrics(EQ, fills, "1d")
    assert m.win_rate == 1.0
    assert m.n_round_trips == 1
    assert m.n_open_positions == 0


def test_long_loss_after_fees() -> None:
    """buy 1 @ 100 (fee 0.10), sell 1 @ 100.15 (fee 0.10015).

    gross = +0.15, fees = 0.20015, net = -0.05015 -> a LOSS: fees turn a gross win negative.
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.10),
            fill(2, "BTC/USDT", "sell", 1.0, 100.15, 0.10015),
        ]
    )
    res = fifo_round_trips(fills)
    assert res.round_trips[0].pnl == pytest.approx(-0.05015, abs=1e-12)
    assert compute_metrics(EQ, fills, "1d").win_rate == 0.0


def test_one_sell_matched_across_two_buys() -> None:
    """buy 1 @ 100 (fee 0.10), buy 1 @ 110 (fee 0.11), sell 2 @ 105 (fee 0.21).

    FIFO: the sell's 2 units close lot 1 first, then lot 2. Exit fee split 1/2 : 1/2.
      RT1: 1 * (105 - 100) = 5    ; fees 0.10 + 0.21*1/2 = 0.205 ; net =  4.795 (win)
      RT2: 1 * (105 - 110) = -5   ; fees 0.11 + 0.105     = 0.215 ; net = -5.215 (loss)
    win rate = 1 / 2 = 0.5
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.10),
            fill(2, "BTC/USDT", "buy", 1.0, 110.0, 0.11),
            fill(3, "BTC/USDT", "sell", 2.0, 105.0, 0.21),
        ]
    )
    res = fifo_round_trips(fills)
    assert [rt.pnl for rt in res.round_trips] == pytest.approx([4.795, -5.215], abs=1e-12)
    assert [rt.entry_price for rt in res.round_trips] == [100.0, 110.0]
    m = compute_metrics(EQ, fills, "1d")
    assert m.n_round_trips == 2
    assert m.win_rate == 0.5


def test_one_buy_closed_by_two_partial_sells() -> None:
    """buy 2 @ 100 (fee 0.20), sell 0.5 @ 120 (fee 0.06), sell 1.5 @ 90 (fee 0.135).

    Entry fee allocated pro rata: 0.20 * 0.5/2 = 0.05 and 0.20 * 1.5/2 = 0.15.
      RT1: 0.5 * (120 - 100) =  10 ; fees 0.05 + 0.06  = 0.11  ; net =   9.89  (win)
      RT2: 1.5 * ( 90 - 100) = -15 ; fees 0.15 + 0.135 = 0.285 ; net = -15.285 (loss)
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 2.0, 100.0, 0.20),
            fill(2, "BTC/USDT", "sell", 0.5, 120.0, 0.06),
            fill(3, "BTC/USDT", "sell", 1.5, 90.0, 0.135),
        ]
    )
    res = fifo_round_trips(fills)
    assert [rt.pnl for rt in res.round_trips] == pytest.approx([9.89, -15.285], abs=1e-12)
    assert [rt.fees for rt in res.round_trips] == pytest.approx([0.11, 0.285], abs=1e-12)
    assert res.open_positions == {}


def test_short_round_trip() -> None:
    """sell 1 @ 100 (fee 0.10) first, then buy 1 @ 90 (fee 0.09) -> short.

    gross = 1 * (100 - 90) = 10 ; fees 0.19 ; net = 9.81 -> win
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "sell", 1.0, 100.0, 0.10),
            fill(2, "BTC/USDT", "buy", 1.0, 90.0, 0.09),
        ]
    )
    res = fifo_round_trips(fills)
    assert res.has_shorts
    rt = res.round_trips[0]
    assert rt.direction == "short"
    assert rt.pnl == pytest.approx(9.81, abs=1e-12)
    assert compute_metrics(EQ, fills, "1d").win_rate == 1.0


def test_flip_long_to_short_in_one_fill() -> None:
    """buy 1 @ 100 (fee 0.10); sell 3 @ 110 (fee 0.33); buy 2 @ 120 (fee 0.24).

    The sell closes the 1-unit long and opens a 2-unit short with the rest.
      RT1 long : 1 * (110 - 100) =  10 ; fees 0.10 + 0.33*1/3 = 0.21 ; net =   9.79
      RT2 short: 2 * (110 - 120) = -20 ; fees 0.33*2/3 + 0.24 = 0.46 ; net = -20.46
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.10),
            fill(2, "BTC/USDT", "sell", 3.0, 110.0, 0.33),
            fill(3, "BTC/USDT", "buy", 2.0, 120.0, 0.24),
        ]
    )
    res = fifo_round_trips(fills)
    assert [rt.direction for rt in res.round_trips] == ["long", "short"]
    assert [rt.pnl for rt in res.round_trips] == pytest.approx([9.79, -20.46], abs=1e-12)
    assert res.has_shorts
    assert res.open_positions == {}


def test_open_positions_excluded_and_counted() -> None:
    """BTC: closed win (+9.79). ETH: buy 2, never sold -> open.
    SOL: buy 3 @ 5 (fee 0.015), sell 1 @ 4 (fee 0.004) -> 1 closed loss, 2 units still open.
      SOL RT: 1 * (4 - 5) = -1 ; fees 0.015/3 + 0.004 = 0.009 ; net = -1.009
    Closed round trips = 2 (BTC win, SOL loss) -> win rate 0.5.
    Open positions = {ETH: 2, SOL: 2} -> 2 symbols; their unrealised P&L is not in win rate.
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.10),
            fill(1, "ETH/USDT", "buy", 2.0, 10.0, 0.02),
            fill(1, "SOL/USDT", "buy", 3.0, 5.0, 0.015),
            fill(2, "BTC/USDT", "sell", 1.0, 110.0, 0.11),
            fill(2, "SOL/USDT", "sell", 1.0, 4.0, 0.004),
        ]
    )
    res = fifo_round_trips(fills)
    assert len(res.round_trips) == 2
    sol = next(rt for rt in res.round_trips if rt.symbol == "SOL/USDT")
    assert sol.pnl == pytest.approx(-1.009, abs=1e-12)
    assert res.open_positions == pytest.approx({"ETH/USDT": 2.0, "SOL/USDT": 2.0})

    m = compute_metrics(EQ, fills, "1d")
    assert m.n_round_trips == 2
    assert m.win_rate == 0.5
    assert m.n_open_positions == 2


def test_open_short_residual_is_negative() -> None:
    fills = fills_frame([fill(1, "BTC/USDT", "sell", 0.5, 100.0, 0.05)])
    res = fifo_round_trips(fills)
    assert res.open_positions == {"BTC/USDT": pytest.approx(-0.5)}
    assert res.has_shorts
    m = compute_metrics(EQ, fills, "1d")
    assert math.isnan(m.win_rate)
    assert m.n_open_positions == 1


def test_float_dust_does_not_count_as_open_position() -> None:
    """0.1 + 0.1 + 0.1 != 0.3 in float64; the residual (~5e-17) must not look like a position."""
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 0.1, 100.0, 0.0),
            fill(2, "BTC/USDT", "buy", 0.1, 100.0, 0.0),
            fill(3, "BTC/USDT", "buy", 0.1, 100.0, 0.0),
            fill(4, "BTC/USDT", "sell", 0.3, 101.0, 0.0),
        ]
    )
    res = fifo_round_trips(fills)
    assert res.open_positions == {}
    assert len(res.round_trips) == 3


def test_zero_pnl_is_not_a_win() -> None:
    """§3.5: a win is net P&L > 0 strictly."""
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.0),
            fill(2, "BTC/USDT", "sell", 1.0, 100.0, 0.0),
        ]
    )
    assert compute_metrics(EQ, fills, "1d").win_rate == 0.0


def test_rejects_bad_side() -> None:
    fills = fills_frame([fill(1, "BTC/USDT", "hold", 1.0, 100.0, 0.0)])
    with pytest.raises(ValueError, match="side"):
        fifo_round_trips(fills)
