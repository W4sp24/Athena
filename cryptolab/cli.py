"""CryptoLab command-line interface (FR-21). Commands are added as modules land."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from cryptolab.config import Settings
from cryptolab.config.backtest import load_backtest_config
from cryptolab.data.exchange import DEFAULT_EXCHANGE
from cryptolab.data.quality import quality_report
from cryptolab.data.store import OhlcvStore
from cryptolab.data.update import update
from cryptolab.runner import MissingDataError, run_config, save_run
from cryptolab.strategies.registry import available_strategies

app = typer.Typer(help="CryptoLab: research, backtesting, and paper trading.", no_args_is_help=True)
data_app = typer.Typer(help="Download and check market data (FR-01..FR-03).", no_args_is_help=True)
app.add_typer(data_app, name="data")

DataRoot = Annotated[Path, typer.Option("--root", help="Data store directory.")]
Exchange = Annotated[str, typer.Option("--exchange", "-e", help="CCXT exchange id.")]
Timeframe = Annotated[str, typer.Option("--timeframe", "-t", help="Bar size, e.g. 1h or 1d.")]
Symbols = Annotated[list[str], typer.Argument(help="Pairs, e.g. BTC/USDT ETH/USDT.")]


def _utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@app.callback()
def main() -> None:
    """CryptoLab CLI. Modes: backtest | paper | testnet (no live mode exists)."""
    # Windows consoles default to a legacy code page; reports contain non-ASCII (§, ·).
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


@app.command()
def status() -> None:
    """Show the active trading mode."""
    typer.echo(f"mode: {Settings().mode.value}")


@app.command()
def backtest(
    config: Annotated[Path, typer.Argument(help="Backtest YAML, e.g. configs/backtests/*.yaml.")],
    out: Annotated[Path, typer.Option(help="Where to save report + CSVs.")] = Path("reports"),
    save: Annotated[bool, typer.Option(help="Save report files.")] = True,
) -> None:
    """Run a backtest from a config and print the report vs BTC buy-and-hold (FR-21)."""
    cfg = load_backtest_config(config)
    try:
        run = run_config(cfg)
    except MissingDataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo(run.report.to_text())
    if save:
        path = save_run(run, out, datetime.now(UTC))
        typer.echo(f"\nsaved: {path}")


@app.command()
def strategies() -> None:
    """List available strategies."""
    for name in available_strategies():
        typer.echo(name)


@data_app.command("download")
def data_download(
    symbols: Symbols,
    start: Annotated[str, typer.Option(help="First bar date (UTC), YYYY-MM-DD.")] = "2023-01-01",
    exchange: Exchange = DEFAULT_EXCHANGE,
    timeframe: Timeframe = "1h",
    root: DataRoot = Path("data"),
) -> None:
    """Download or incrementally update candles, then print a quality summary."""
    store = OhlcvStore(root)
    added = update(store, exchange, symbols, timeframe, _utc_date(start))
    for symbol in symbols:
        report = quality_report(store.read(exchange, symbol, timeframe), timeframe)
        verdict = "PASS" if report.passed() else "CHECK"
        typer.echo(
            f"{symbol:12s} +{added.get(symbol, 0):>6d} bars  total {report.n_bars:>6d}  "
            f"missing {report.missing_bars} ({report.missing_pct:.3f}%)  {verdict}"
        )


@data_app.command("quality")
def data_quality(
    symbols: Symbols,
    exchange: Exchange = DEFAULT_EXCHANGE,
    timeframe: Timeframe = "1h",
    root: DataRoot = Path("data"),
) -> None:
    """Full quality report (gaps, duplicates, bad values) for stored series."""
    store = OhlcvStore(root)
    failed = False
    for symbol in symbols:
        report = quality_report(store.read(exchange, symbol, timeframe), timeframe)
        typer.echo(f"== {exchange} {symbol} {timeframe}\n{report.to_text()}\n")
        failed |= not report.passed()
    raise typer.Exit(code=1 if failed else 0)


@data_app.command("list")
def data_list(root: DataRoot = Path("data")) -> None:
    """List stored series."""
    store = OhlcvStore(root)
    for exchange, timeframe, symbol in store.list_series():
        last = store.last_ts(exchange, symbol, timeframe)
        typer.echo(f"{exchange:10s} {timeframe:4s} {symbol:12s} last bar {last}")


if __name__ == "__main__":
    app()
