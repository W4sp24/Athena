"""Backtest report: the strategy next to BTC buy-and-hold (FR-17, FR-18, math.md §3.7).

Every report carries the benchmark, the risk-free rate used, fee/slippage settings,
the data range, the RiskGate rejection count, and any caveats (shorts without borrow
cost per D-011, carried-forward marks per math.md §4).

The engine reports carried-forward marks as an int in
``BacktestResult.meta["carried_forward_marks"]`` (a per-symbol mapping or a
sequence of events is also accepted and totalled).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from cryptolab.analytics.metrics import (
    Metrics,
    compute_metrics,
    drawdown_series,
    fifo_round_trips,
)
from cryptolab.engine.types import BacktestResult

__all__ = ["BENCHMARK_NAME", "SHORT_WARNING", "Report", "build_report", "equity_frame"]

BENCHMARK_NAME = "BTC buy & hold"
SHORT_WARNING = "shorts simulated without borrow cost (D-011)"
CARRIED_FORWARD_KEY = "carried_forward_marks"
_NOT_PROVIDED = "not provided"


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _pp(x: float) -> str:
    return f"{x * 100:+.2f} pp"


def _num(x: float) -> str:
    return f"{x:.2f}"


def _signed_num(x: float) -> str:
    return f"{x:+.2f}"


def _int(x: float) -> str:
    return f"{int(x)}"


def _signed_int(x: float) -> str:
    return f"{int(x):+d}"


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _signed_money(x: float) -> str:
    return f"{x:+,.2f}"


# (label, Metrics field, value formatter, difference formatter)
_ROWS: tuple[tuple[str, str, Callable[[float], str], Callable[[float], str]], ...] = (
    ("Total return", "total_return", _pct, _pp),
    ("CAGR", "cagr", _pct, _pp),
    ("Volatility (ann.)", "volatility_ann", _pct, _pp),
    ("Sharpe", "sharpe", _num, _signed_num),
    ("Sortino", "sortino", _num, _signed_num),
    ("Max drawdown", "max_drawdown", _pct, _pp),
    ("Max drawdown duration (bars)", "max_drawdown_duration_bars", _int, _signed_int),
    ("Win rate", "win_rate", _pct, _pp),
    ("Closed round trips", "n_round_trips", _int, _signed_int),
    ("Open positions at end", "n_open_positions", _int, _signed_int),
    ("Turnover (ann.)", "turnover_ann", _num, _signed_num),
    ("Fills", "n_fills", _int, _signed_int),
    ("Total fees", "total_fees", _money, _signed_money),
    ("Return periods (N)", "n_periods", _int, _signed_int),
)

_NUMERIC_FIELDS = tuple(f.name for f in fields(Metrics) if f.name not in ("start", "end"))


def _fmt(x: float, formatter: Callable[[float], str]) -> str:
    return formatter(x) if math.isfinite(x) else "n/a"


def _jsonable(value: Any) -> Any:
    """Convert to plain JSON types; NaN/inf become None."""
    if isinstance(value, np.generic):
        value = value.item()
    out: Any
    if value is None or isinstance(value, bool | int | str):
        out = value
    elif isinstance(value, float):
        out = value if math.isfinite(value) else None
    elif isinstance(value, datetime | date):  # pd.Timestamp is a datetime
        out = value.isoformat()
    elif isinstance(value, Mapping):
        out = {str(k): _jsonable(v) for k, v in value.items()}
    elif isinstance(value, list | tuple | set | frozenset):
        out = [_jsonable(v) for v in value]
    else:
        out = str(value)
    return out


def _metrics_dict(m: Metrics) -> dict[str, Any]:
    return {f.name: _jsonable(getattr(m, f.name)) for f in fields(m)}


def _difference(a: Metrics, b: Metrics) -> dict[str, float]:
    return {name: float(getattr(a, name)) - float(getattr(b, name)) for name in _NUMERIC_FIELDS}


@dataclass(frozen=True)
class Report:
    """Strategy and benchmark metrics side by side plus the run's header info."""

    strategy_name: str
    strategy: Metrics
    benchmark: Metrics
    timeframe: str
    initial_capital: float
    start: pd.Timestamp
    end: pd.Timestamp
    fee_bps: float | None
    slippage_bps: float | None
    risk_profile: str | None
    risk_free_annual: float
    n_rejections: int
    warnings: tuple[str, ...]
    benchmark_name: str = BENCHMARK_NAME
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def difference(self) -> dict[str, float]:
        """Strategy minus benchmark for every numeric metric (NaN if either is NaN)."""
        return _difference(self.strategy, self.benchmark)

    def to_markdown(self) -> str:
        fee = _NOT_PROVIDED if self.fee_bps is None else f"{self.fee_bps:g} bps"
        slip = _NOT_PROVIDED if self.slippage_bps is None else f"{self.slippage_bps:g} bps"
        lines = [
            f"# Backtest report: {self.strategy_name}",
            "",
            f"- Period: {self.start.isoformat()} to {self.end.isoformat()} "
            f"({self.strategy.n_periods} return periods, timeframe {self.timeframe})",
            f"- Initial capital: {self.initial_capital:,.2f}",
            f"- Fees: {fee} · Slippage: {slip} (fees and slippage always charged)",
            f"- Risk profile: {self.risk_profile or _NOT_PROVIDED}",
            f"- Risk-free rate: {_pct(self.risk_free_annual)} p.a. (math.md §3.2)",
            f"- Rejections / clips by RiskGate: {self.n_rejections}",
            f"- Benchmark: {self.benchmark_name}, same capital and period (math.md §3.7, FR-18)",
        ]
        if self.warnings:
            lines += ["", "## Warnings", ""]
            lines += [f"- {w}" for w in self.warnings]
        diff = self.difference
        lines += [
            "",
            "## Metrics",
            "",
            f"| Metric | {self.strategy_name} | {self.benchmark_name} | Difference |",
            "|---|---:|---:|---:|",
        ]
        for label, name, fmt, diff_fmt in _ROWS:
            lines.append(
                f"| {label} "
                f"| {_fmt(float(getattr(self.strategy, name)), fmt)} "
                f"| {_fmt(float(getattr(self.benchmark, name)), fmt)} "
                f"| {_fmt(diff[name], diff_fmt)} |"
            )
        return "\n".join(lines) + "\n"

    def to_text(self) -> str:
        """Terminal rendering. Markdown tables read fine in a terminal."""
        return self.to_markdown()

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for the dashboard / a later API. NaN becomes None."""
        return {
            "strategy_name": self.strategy_name,
            "benchmark_name": self.benchmark_name,
            "header": {
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "timeframe": self.timeframe,
                "initial_capital": _jsonable(self.initial_capital),
                "fee_bps": _jsonable(self.fee_bps),
                "slippage_bps": _jsonable(self.slippage_bps),
                "risk_profile": self.risk_profile,
                "risk_free_annual": _jsonable(self.risk_free_annual),
                "n_rejections": self.n_rejections,
            },
            "warnings": list(self.warnings),
            "metrics": {
                "strategy": _metrics_dict(self.strategy),
                "benchmark": _metrics_dict(self.benchmark),
                "difference": _jsonable(self.difference),
            },
            "meta": _jsonable(self.meta),
        }


def _count_carried_forward(meta: Mapping[str, Any]) -> int:
    raw = meta.get(CARRIED_FORWARD_KEY)
    if raw is None:
        return 0
    if isinstance(raw, Mapping):
        return int(sum(int(v) for v in raw.values()))
    if isinstance(raw, list | tuple):
        return len(raw)
    return int(raw)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def build_report(
    result: BacktestResult,
    benchmark: BacktestResult,
    *,
    strategy_name: str,
    risk_free_annual: float = 0.0,
    settings: Mapping[str, Any] | None = None,
) -> Report:
    """Compute metrics for the run and its BTC buy-and-hold benchmark (FR-18).

    ``settings`` supplies ``fee_bps``, ``slippage_bps`` and ``risk_profile``; missing
    cost settings are shown as "not provided" and raise a warning.
    """
    if benchmark is None:
        raise ValueError("a BTC buy-and-hold benchmark is required in every report (FR-18)")
    if benchmark.timeframe != result.timeframe:
        raise ValueError(
            f"benchmark timeframe {benchmark.timeframe!r} != strategy timeframe "
            f"{result.timeframe!r}"
        )
    settings = settings or {}

    strat_m = compute_metrics(result.equity, result.fills, result.timeframe, risk_free_annual)
    bench_m = compute_metrics(
        benchmark.equity, benchmark.fills, benchmark.timeframe, risk_free_annual
    )

    fee_bps = _optional_float(settings.get("fee_bps"))
    slippage_bps = _optional_float(settings.get("slippage_bps"))
    risk_profile = settings.get("risk_profile")

    warnings: list[str] = []
    if fifo_round_trips(result.fills).has_shorts:
        warnings.append(SHORT_WARNING)
    n_cf = _count_carried_forward(result.meta)
    if n_cf > 0:
        warnings.append(
            f"{n_cf} bar mark(s) used a carried-forward close because data was missing (math.md §4)"
        )
    if fee_bps is None or slippage_bps is None:
        warnings.append("fee and/or slippage settings not provided to the report")
    if not math.isclose(result.initial_capital, benchmark.initial_capital, rel_tol=1e-12):
        warnings.append(
            f"benchmark initial capital {benchmark.initial_capital:,.2f} differs from "
            f"strategy {result.initial_capital:,.2f} (math.md §3.7)"
        )
    if (strat_m.start, strat_m.end) != (bench_m.start, bench_m.end):
        warnings.append(
            f"benchmark period {bench_m.start.isoformat()} to {bench_m.end.isoformat()} "
            "differs from the strategy period (math.md §3.7)"
        )

    return Report(
        strategy_name=strategy_name,
        strategy=strat_m,
        benchmark=bench_m,
        timeframe=result.timeframe,
        initial_capital=float(result.initial_capital),
        start=strat_m.start,
        end=strat_m.end,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        risk_profile=None if risk_profile is None else str(risk_profile),
        risk_free_annual=float(risk_free_annual),
        n_rejections=len(result.rejections),
        warnings=tuple(warnings),
        meta=dict(result.meta),
    )


def equity_frame(result: BacktestResult, benchmark: BacktestResult) -> pd.DataFrame:
    """Both equity curves normalised to 1.0 at their first mark, plus drawdowns (§3.4).

    Columns: strategy, benchmark, strategy_drawdown, benchmark_drawdown; outer-joined on ts.
    """
    s = result.equity.astype("float64")
    b = benchmark.equity.astype("float64")
    return pd.concat(
        {
            "strategy": s / float(s.iloc[0]),
            "benchmark": b / float(b.iloc[0]),
            "strategy_drawdown": drawdown_series(s),
            "benchmark_drawdown": drawdown_series(b),
        },
        axis=1,
        join="outer",
    )
