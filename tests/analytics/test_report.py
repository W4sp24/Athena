"""Backtest report: strategy vs BTC buy-and-hold side by side (FR-17, FR-18, math.md §3.7)."""

from __future__ import annotations

import json
import math
from dataclasses import fields
from typing import Any

import pandas as pd
import pytest

from cryptolab.analytics.metrics import Metrics, compute_metrics
from cryptolab.analytics.report import Report, build_report, equity_frame
from cryptolab.engine.types import BacktestResult
from tests.analytics.helpers import daily_equity, empty_fills, fill, fills_frame


def same_metrics(a: Metrics, b: Metrics) -> bool:
    """Field-wise equality where NaN == NaN."""
    for f in fields(Metrics):
        x, y = getattr(a, f.name), getattr(b, f.name)
        if isinstance(x, float) and math.isnan(x):
            if not (isinstance(y, float) and math.isnan(y)):
                return False
        elif x != y:
            return False
    return True


SETTINGS = {"fee_bps": 10.0, "slippage_bps": 5.0, "risk_profile": "conservative"}


def make_result(
    values: list[float],
    fills: pd.DataFrame | None = None,
    *,
    n_rejections: int = 0,
    meta: dict[str, Any] | None = None,
    timeframe: str = "1d",
) -> BacktestResult:
    rejections = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-02", periods=n_rejections, freq="D", tz="UTC"),
            "symbol": ["BTC/USDT"] * n_rejections,
            "reason": ["position_limit"] * n_rejections,
            "detail": [""] * n_rejections,
        }
    )
    return BacktestResult(
        equity=daily_equity(values),
        fills=empty_fills() if fills is None else fills,
        rejections=rejections,
        initial_capital=values[0],
        timeframe=timeframe,
        meta=meta or {},
    )


@pytest.fixture
def strategy() -> BacktestResult:
    """Equity [100, 110, 99, 108.9]; one closed long round trip (win)."""
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "buy", 0.5, 100.0, 0.05),
            fill(2, "BTC/USDT", "sell", 0.5, 110.0, 0.055),
        ]
    )
    return make_result([100.0, 110.0, 99.0, 108.9], fills, n_rejections=3)


@pytest.fixture
def benchmark() -> BacktestResult:
    """Buy-and-hold: one buy, never sold -> open position, win rate NaN. R = 0.0404."""
    fills = fills_frame([fill(1, "BTC/USDT", "buy", 1.0, 100.0, 0.1)])
    return make_result([100.0, 105.0, 102.0, 104.04], fills)


@pytest.fixture
def report(strategy: BacktestResult, benchmark: BacktestResult) -> Report:
    return build_report(strategy, benchmark, strategy_name="ma_cross", settings=SETTINGS)


def test_benchmark_is_mandatory(strategy: BacktestResult) -> None:
    """FR-18: every report shows BTC buy-and-hold; a missing benchmark is an error."""
    with pytest.raises(ValueError, match="benchmark"):
        build_report(strategy, None, strategy_name="x")  # type: ignore[arg-type]


def test_benchmark_timeframe_must_match(strategy: BacktestResult) -> None:
    other = make_result([100.0, 101.0], timeframe="1h")
    with pytest.raises(ValueError, match="timeframe"):
        build_report(strategy, other, strategy_name="x")


def test_metrics_side_by_side(
    report: Report, strategy: BacktestResult, benchmark: BacktestResult
) -> None:
    assert same_metrics(report.strategy, compute_metrics(strategy.equity, strategy.fills, "1d"))
    bench = compute_metrics(benchmark.equity, benchmark.fills, "1d")
    assert report.benchmark.total_return == pytest.approx(0.0404)
    assert report.benchmark.n_open_positions == bench.n_open_positions == 1
    assert math.isnan(report.benchmark.win_rate)
    assert report.benchmark_name == "BTC buy & hold"


def test_header_info(report: Report) -> None:
    assert report.strategy_name == "ma_cross"
    assert report.timeframe == "1d"
    assert report.initial_capital == 100.0
    assert report.start == pd.Timestamp("2026-01-01", tz="UTC")
    assert report.end == pd.Timestamp("2026-01-04", tz="UTC")
    assert report.fee_bps == 10.0
    assert report.slippage_bps == 5.0
    assert report.risk_profile == "conservative"
    assert report.risk_free_annual == 0.0
    assert report.n_rejections == 3
    assert report.warnings == ()


def test_risk_free_rate_is_applied_to_both(
    strategy: BacktestResult, benchmark: BacktestResult
) -> None:
    r = build_report(
        strategy, benchmark, strategy_name="x", risk_free_annual=0.05, settings=SETTINGS
    )
    assert r.risk_free_annual == 0.05
    assert same_metrics(r.strategy, compute_metrics(strategy.equity, strategy.fills, "1d", 0.05))
    assert same_metrics(r.benchmark, compute_metrics(benchmark.equity, benchmark.fills, "1d", 0.05))


def test_short_warning(benchmark: BacktestResult) -> None:
    """D-011 / math.md §5.6: shorts carry no borrow fee; every report using them says so."""
    fills = fills_frame(
        [
            fill(1, "BTC/USDT", "sell", 0.5, 100.0, 0.05),
            fill(2, "BTC/USDT", "buy", 0.5, 95.0, 0.05),
        ]
    )
    r = build_report(
        make_result([100.0, 102.0, 103.0, 101.0], fills),
        benchmark,
        strategy_name="x",
        settings=SETTINGS,
    )
    assert any("shorts simulated without borrow cost (D-011)" in w for w in r.warnings)
    assert "shorts simulated without borrow cost (D-011)" in r.to_markdown()


def test_carried_forward_marks_warning(benchmark: BacktestResult) -> None:
    """math.md §4: the report counts bars marked with a carried-forward close."""
    res = make_result([100.0, 101.0, 102.0, 103.0], meta={"carried_forward_marks": 7})
    r = build_report(res, benchmark, strategy_name="x", settings=SETTINGS)
    assert any("7" in w and "carried-forward" in w for w in r.warnings)


def test_zero_carried_forward_marks_no_warning(benchmark: BacktestResult) -> None:
    res = make_result([100.0, 101.0, 102.0, 103.0], meta={"carried_forward_marks": 0})
    r = build_report(res, benchmark, strategy_name="x", settings=SETTINGS)
    assert r.warnings == ()


def test_missing_cost_settings_are_flagged(
    strategy: BacktestResult, benchmark: BacktestResult
) -> None:
    r = build_report(strategy, benchmark, strategy_name="x")
    assert r.fee_bps is None
    assert r.slippage_bps is None
    assert any("fee" in w and "slippage" in w for w in r.warnings)
    assert "not provided" in r.to_markdown()


def test_mismatched_benchmark_period_or_capital_warns(strategy: BacktestResult) -> None:
    """math.md §3.7: same initial capital and period; a mismatch is flagged, not hidden."""
    bench = make_result([200.0, 201.0])
    r = build_report(strategy, bench, strategy_name="x", settings=SETTINGS)
    assert any("initial capital" in w for w in r.warnings)
    assert any("period" in w for w in r.warnings)


def test_markdown_table(report: Report) -> None:
    md = report.to_markdown()
    assert "| Metric | ma_cross | BTC buy & hold | Difference |" in md
    # total return: 8.90% vs 4.04% -> +4.86 pp
    assert "| Total return | 8.90% | 4.04% | +4.86 pp |" in md
    # max drawdown: 99/110-1 = -10.00% vs 102/105-1 = -2.86%
    assert "| Max drawdown | -10.00% | -2.86% | -7.14 pp |" in md
    # win rate: strategy 100%, benchmark has no closed round trip
    assert "| Win rate | 100.00% | n/a | n/a |" in md
    assert "Risk-free rate: 0.00%" in md
    assert "10 bps" in md
    assert "5 bps" in md
    assert "conservative" in md
    assert "Rejections" in md
    assert "3" in md
    assert "2026-01-01" in md
    assert "2026-01-04" in md
    assert report.to_text()


def test_to_dict_is_json_serialisable(report: Report) -> None:
    d = report.to_dict()
    text = json.dumps(d, allow_nan=False)  # NaN must already be None
    back = json.loads(text)
    assert back["metrics"]["benchmark"]["win_rate"] is None
    assert back["metrics"]["difference"]["win_rate"] is None
    assert back["metrics"]["strategy"]["total_return"] == pytest.approx(0.089)
    assert back["metrics"]["difference"]["total_return"] == pytest.approx(0.0486)
    assert back["header"]["start"] == "2026-01-01T00:00:00+00:00"
    assert back["header"]["risk_free_annual"] == 0.0
    assert back["header"]["n_rejections"] == 3
    assert back["benchmark_name"] == "BTC buy & hold"
    assert back["warnings"] == []


def test_equity_frame(strategy: BacktestResult, benchmark: BacktestResult) -> None:
    df = equity_frame(strategy, benchmark)
    assert list(df.columns) == [
        "strategy",
        "benchmark",
        "strategy_drawdown",
        "benchmark_drawdown",
    ]
    assert df["strategy"].tolist() == pytest.approx([1.0, 1.1, 0.99, 1.089])
    assert df["benchmark"].tolist() == pytest.approx([1.0, 1.05, 1.02, 1.0404])
    assert df["strategy_drawdown"].tolist() == pytest.approx([0.0, 0.0, -0.1, -0.01])
    assert df["benchmark_drawdown"].iloc[2] == pytest.approx(102 / 105 - 1)
    assert isinstance(df.index, pd.DatetimeIndex)
