"""Backtest orchestration: config -> data -> strategy + benchmark -> report (FR-18, FR-21).

Kept outside ``cli`` so the dashboard can reuse it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from cryptolab.analytics import Report, build_report, equity_frame
from cryptolab.config import load_risk_limits
from cryptolab.config.backtest import BacktestConfig
from cryptolab.data.store import OhlcvStore
from cryptolab.engine import run_backtest
from cryptolab.engine.types import BacktestResult
from cryptolab.strategies.buy_and_hold import BuyAndHold
from cryptolab.strategies.registry import get_strategy


class MissingDataError(RuntimeError):
    """The store has no bars for a series the config needs."""


@dataclass(frozen=True)
class BacktestRun:
    config: BacktestConfig
    result: BacktestResult
    benchmark: BacktestResult
    report: Report


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _load_bars(cfg: BacktestConfig, symbols: list[str]) -> dict[str, pd.DataFrame]:
    store = OhlcvStore(cfg.data.root)
    bars: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = store.read(cfg.data.exchange, symbol, cfg.data.timeframe, cfg.data.start, cfg.data.end)
        if df.empty:
            raise MissingDataError(
                f"no {cfg.data.timeframe} bars for {symbol} on {cfg.data.exchange} from "
                f"{cfg.data.start:%Y-%m-%d}; run: cryptolab data download {symbol} "
                f"--exchange {cfg.data.exchange} --timeframe {cfg.data.timeframe} "
                f"--start {cfg.data.start:%Y-%m-%d}"
            )
        bars[symbol] = df
    return bars


def run_config(cfg: BacktestConfig) -> BacktestRun:
    limits = load_risk_limits(cfg.risk_profile)
    common: dict[str, Any] = {
        "timeframe": cfg.data.timeframe,
        "initial_capital": cfg.initial_capital,
        "fee_bps": cfg.costs.fee_bps,
        "slippage_bps": cfg.costs.slippage_bps,
        "risk_limits": limits,
    }
    meta: dict[str, Any] = {
        "run_config_hash": cfg.config_hash(),  # YAML config; engine adds its own config_hash
        "git_sha": git_sha(),
        "config_name": cfg.name,
    }

    bars = _load_bars(cfg, cfg.data.symbols)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    result = run_backtest(bars, strategy, meta=meta, **common)

    # FR-18 / math.md §3.7: BTC buy-and-hold, same capital, period, costs.
    bench_bars = _load_bars(cfg, [cfg.benchmark_symbol])
    benchmark = run_backtest(
        bench_bars,
        BuyAndHold(symbols=[cfg.benchmark_symbol]),
        meta={**meta, "benchmark": True},
        **common,
    )

    report = build_report(
        result,
        benchmark,
        strategy_name=cfg.name,
        risk_free_annual=cfg.risk_free_annual,
        settings={
            "fee_bps": cfg.costs.fee_bps,
            "slippage_bps": cfg.costs.slippage_bps,
            "risk_profile": str(cfg.risk_profile),
        },
    )
    return BacktestRun(cfg, result, benchmark, report)


def save_run(run: BacktestRun, out_root: Path, now: datetime) -> Path:
    """Write report.md, report.json, equity.csv, fills.csv, rejections.csv."""
    slug = "".join(c if c.isalnum() else "-" for c in run.config.name.lower()).strip("-")
    out = out_root / f"{now:%Y%m%d-%H%M%S}-{slug}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.md").write_text(run.report.to_markdown(), encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(run.report.to_dict(), indent=2, allow_nan=False), encoding="utf-8"
    )
    equity_frame(run.result, run.benchmark).to_csv(out / "equity.csv")
    run.result.fills.to_csv(out / "fills.csv", index=False)
    run.result.rejections.to_csv(out / "rejections.csv", index=False)
    return out
