"""compute_metrics against hand-computed fixtures (math.md §2-3, FR-17).

Main fixture: daily equity V = [100, 110, 99, 108.9], so P = 365 and N = 3.

    r1 = 110/100 - 1  =  0.1
    r2 =  99/110 - 1  = -0.1
    r3 = 108.9/99 - 1 =  0.1
    mean r = 0.1/3 = 1/30 = 0.0333...
    deviations = [ 1/15, -2/15, 1/15 ]  -> squares [1/225, 4/225, 1/225], sum = 6/225 = 2/75
    s(r) (ddof=1) = sqrt((2/75) / 2) = sqrt(1/75) = 0.1154700538...
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from cryptolab.analytics.metrics import Metrics, compute_metrics, periods_per_year
from tests.analytics.helpers import (
    daily_equity,
    empty_fills,
    fill,
    fills_frame,
    hourly_equity,
)

P_DAILY = 365
V = [100.0, 110.0, 99.0, 108.9]
S = math.sqrt(1 / 75)  # ddof=1 std of [0.1, -0.1, 0.1], derived in the module docstring
MEAN = 1 / 30


@pytest.fixture
def m() -> Metrics:
    return compute_metrics(daily_equity(V), empty_fills(), "1d")


def test_periods_per_year_matches_math_md_section_1() -> None:
    """math.md §1: P = 8760 hourly, 365 daily (24/7 calendar)."""
    assert periods_per_year("1h") == 8760
    assert periods_per_year("1d") == 365
    assert periods_per_year("4h") == 2190
    assert periods_per_year("15m") == 35040
    with pytest.raises(ValueError, match="timeframe"):
        periods_per_year("banana")


def test_total_return(m: Metrics) -> None:
    """§2.3: R = V_T / V_0 - 1 = 108.9/100 - 1 = 0.089."""
    assert m.total_return == pytest.approx(0.089, abs=1e-12)


def test_n_periods_start_end(m: Metrics) -> None:
    """N counts return periods: 4 equity points -> 3 returns."""
    assert m.n_periods == 3
    assert m.start == pd.Timestamp("2026-01-01", tz="UTC")
    assert m.end == pd.Timestamp("2026-01-04", tz="UTC")


def test_cagr(m: Metrics) -> None:
    """§2.4: (V_T/V_0)^(P/N) - 1 = 1.089^(365/3) - 1."""
    assert m.cagr == pytest.approx(1.089 ** (365 / 3) - 1, rel=1e-12)


def test_cagr_one_year_equals_total_return() -> None:
    """§2.4 with N = P: 366 daily points -> N = 365 -> exponent 1 -> CAGR = R = 0.5."""
    values = [100.0 + 50.0 * i / 365 for i in range(366)]
    m = compute_metrics(daily_equity(values), empty_fills(), "1d")
    assert m.n_periods == 365
    assert m.cagr == pytest.approx(0.5, rel=1e-12)


def test_volatility(m: Metrics) -> None:
    """§3.1: s(r) * sqrt(P) = sqrt(1/75) * sqrt(365) = sqrt(365/75) = 2.2060522..."""
    assert m.volatility_ann == pytest.approx(math.sqrt(365 / 75), rel=1e-12)
    assert m.volatility_ann == pytest.approx(2.2060522, rel=1e-7)


def test_sharpe_rf_zero(m: Metrics) -> None:
    """§3.2 with rf = 0: (1/30) / sqrt(1/75) * sqrt(365) = sqrt(75*365)/30 = 5.51513...

    sqrt(27375) = 165.4539...; / 30 = 5.515130...
    """
    assert m.sharpe == pytest.approx(math.sqrt(27375) / 30, rel=1e-12)
    assert m.sharpe == pytest.approx(5.515130, rel=1e-6)


def test_sortino_rf_zero(m: Metrics) -> None:
    """§3.3 with theta = 0. Downside terms min(0, r)^2 = [0, 0.01, 0].

    DD = sqrt(0.01 / N) with N = 3 (ALL periods, not just the one negative) = sqrt(1/300)
    Sortino = (1/30) / sqrt(1/300) * sqrt(365) = sqrt(300*365)/30 = sqrt(109500)/30 = 11.03026...
    (Dividing by only the 1 negative period would give sqrt(100*365)/30 = 6.368: wrong.)
    """
    assert m.sortino == pytest.approx(math.sqrt(109500) / 30, rel=1e-12)
    assert m.sortino == pytest.approx(11.030261, rel=1e-6)


def test_sharpe_and_sortino_with_risk_free_rate() -> None:
    """§3.2/§3.3 with rf = 5%/yr: per-period rf_p = 1.05^(1/365) - 1 = 0.000133681...

    excess mean = 1/30 - rf_p
    Sharpe  = (1/30 - rf_p) / sqrt(1/75) * sqrt(365)   (s(r) unchanged by a constant shift)
    theta = rf_p; downside terms: r - theta = [0.1-rf_p, -0.1-rf_p, 0.1-rf_p]
      only the middle is negative -> DD = sqrt((0.1 + rf_p)^2 / 3) = (0.1 + rf_p)/sqrt(3)
    Sortino = (1/30 - rf_p) / ((0.1 + rf_p)/sqrt(3)) * sqrt(365)
    """
    rf_p = 1.05 ** (1 / 365) - 1
    assert rf_p == pytest.approx(0.000133681, rel=1e-5)
    m = compute_metrics(daily_equity(V), empty_fills(), "1d", risk_free_annual=0.05)
    assert m.sharpe == pytest.approx((MEAN - rf_p) / S * math.sqrt(365), rel=1e-12)
    dd = (0.1 + rf_p) / math.sqrt(3)
    assert m.sortino == pytest.approx((MEAN - rf_p) / dd * math.sqrt(365), rel=1e-12)
    # the arithmetic, not a restated formula:
    assert m.sharpe == pytest.approx(5.49301, rel=1e-5)


def test_max_drawdown_and_duration(m: Metrics) -> None:
    """§3.4: HWM M = [100, 110, 110, 110]; D = [0, 0, 99/110-1, 108.9/110-1] = [0, 0, -0.1, -0.01].

    MDD = -0.1 (negative). Longest run with D < 0 = bars 2..3 = 2 bars.
    """
    assert m.max_drawdown == pytest.approx(99 / 110 - 1, abs=1e-12)
    assert m.max_drawdown == pytest.approx(-0.1, abs=1e-12)
    assert m.max_drawdown_duration_bars == 2


def test_drawdown_duration_is_longest_run_not_total() -> None:
    """V = [100, 90, 100, 95, 94, 93, 100]: D < 0 at bars 1 and 3,4,5 -> runs 1 and 3 -> 3.

    MDD = 90/100 - 1 = -0.1.
    """
    m = compute_metrics(
        daily_equity([100.0, 90.0, 100.0, 95.0, 94.0, 93.0, 100.0]), empty_fills(), "1d"
    )
    assert m.max_drawdown_duration_bars == 3
    assert m.max_drawdown == pytest.approx(-0.1, abs=1e-12)


def test_turnover_hand_calc() -> None:
    """§3.6 hourly: V = [1000, 1010, 990, 1000, 1020] -> N = 4, P = 8760.

    fills: buy 5 @ 100 -> |q p| = 500 ; sell 5 @ 102 -> |q p| = 510 ; sum = 1010
    mean V (all 5 marks) = 5020 / 5 = 1004
    Turnover = 1010 / 1004 * 8760 / 4 = 1.0059761 * 2190 = 2203.0876...
    """
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 5.0, 100.0, 0.5),
            fill(3, "BTC/USDT", "sell", 5.0, 102.0, 0.51),
        ]
    )
    m = compute_metrics(hourly_equity([1000.0, 1010.0, 990.0, 1000.0, 1020.0]), fills, "1h")
    assert m.turnover_ann == pytest.approx(1010 / 1004 * 2190, rel=1e-12)
    assert m.turnover_ann == pytest.approx(2203.0876, rel=1e-7)
    assert m.n_fills == 2
    assert m.total_fees == pytest.approx(1.01, abs=1e-12)


def test_constant_equity_gives_nan_ratios_not_inf() -> None:
    """Zero variance: s(r) = 0 and DD = 0, so Sharpe/Sortino are undefined -> NaN."""
    m = compute_metrics(daily_equity([100.0] * 5), empty_fills(), "1d")
    assert m.total_return == 0.0
    assert m.cagr == 0.0
    assert m.volatility_ann == 0.0
    assert math.isnan(m.sharpe)
    assert math.isnan(m.sortino)
    assert m.max_drawdown == 0.0
    assert m.max_drawdown_duration_bars == 0
    assert m.turnover_ann == 0.0


def test_no_downside_gives_nan_sortino() -> None:
    """Returns all positive: DD = 0 -> Sortino undefined; reported NaN, never inf."""
    m = compute_metrics(daily_equity([100.0, 101.0, 103.0, 104.0]), empty_fills(), "1d")
    assert math.isfinite(m.sharpe)
    assert math.isnan(m.sortino)


def test_no_fills() -> None:
    m = compute_metrics(daily_equity(V), empty_fills(), "1d")
    assert m.n_fills == 0
    assert m.n_round_trips == 0
    assert m.n_open_positions == 0
    assert math.isnan(m.win_rate)
    assert m.total_fees == 0.0
    assert m.turnover_ann == 0.0


def test_fills_frame_without_columns_is_treated_as_no_fills() -> None:
    m = compute_metrics(daily_equity(V), pd.DataFrame(), "1d")
    assert m.n_fills == 0
    assert math.isnan(m.win_rate)


def test_single_bar() -> None:
    """One equity point: N = 0 returns. Nothing is annualisable -> NaN, no crash."""
    m = compute_metrics(daily_equity([100.0]), empty_fills(), "1d")
    assert m.n_periods == 0
    assert m.total_return == 0.0
    assert math.isnan(m.cagr)
    assert math.isnan(m.volatility_ann)
    assert math.isnan(m.sharpe)
    assert math.isnan(m.sortino)
    assert math.isnan(m.turnover_ann)
    assert m.max_drawdown == 0.0
    assert m.max_drawdown_duration_bars == 0


def test_two_bars_volatility_undefined_with_ddof_1() -> None:
    """N = 1 return: the ddof=1 sample std is undefined -> NaN (Sharpe too)."""
    m = compute_metrics(daily_equity([100.0, 110.0]), empty_fills(), "1d")
    assert m.total_return == pytest.approx(0.1)
    assert math.isnan(m.volatility_ann)
    assert math.isnan(m.sharpe)


def test_rejects_bad_equity() -> None:
    with pytest.raises(ValueError, match="empty"):
        compute_metrics(pd.Series([], dtype="float64"), empty_fills(), "1d")
    with pytest.raises(ValueError, match="positive"):
        compute_metrics(daily_equity([0.0, 1.0]), empty_fills(), "1d")
    with pytest.raises(ValueError, match="NaN"):
        compute_metrics(daily_equity([100.0, float("nan"), 101.0]), empty_fills(), "1d")


def test_metrics_is_frozen(m: Metrics) -> None:
    with pytest.raises(AttributeError):
        m.sharpe = 1.0  # type: ignore[misc]
