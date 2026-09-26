"""CryptoLab command-line interface (FR-21). Commands are added as modules land."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from cryptolab import terminal
from cryptolab.config import Settings
from cryptolab.config.backtest import load_backtest_config
from cryptolab.data.exchange import DEFAULT_EXCHANGE
from cryptolab.data.quality import quality_report
from cryptolab.data.store import OhlcvStore
from cryptolab.data.update import update
from cryptolab.runner import MissingDataError, run_config, save_run
from cryptolab.strategies.registry import discover

app = typer.Typer(help="CryptoLab: research, backtesting, and paper trading.", no_args_is_help=True)
data_app = typer.Typer(help="Download and check market data (FR-01..FR-03).", no_args_is_help=True)
app.add_typer(data_app, name="data")

DataRoot = Annotated[Path, typer.Option("--root", help="Data store directory.")]
Exchange = Annotated[str, typer.Option("--exchange", "-e", help="CCXT exchange id.")]
Timeframe = Annotated[str, typer.Option("--timeframe", "-t", help="Bar size, e.g. 1h or 1d.")]
Symbols = Annotated[list[str], typer.Argument(help="Pairs, e.g. BTC/USDT ETH/USDT.")]

console = terminal.make_console()


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
    mode = Settings().mode.value
    console.print(f"Mode: [bold green]{mode}[/]  [dim](no live trading mode exists)[/]")


@app.command()
def backtest(
    config: Annotated[Path, typer.Argument(help="Backtest YAML, e.g. configs/backtests/*.yaml.")],
    out: Annotated[Path, typer.Option(help="Where to save report + CSVs.")] = Path("reports"),
    save: Annotated[bool, typer.Option(help="Save report files.")] = True,
    markdown: Annotated[bool, typer.Option(help="Print the plain markdown report.")] = False,
) -> None:
    """Run a backtest from a config and print the report vs BTC buy-and-hold (FR-21)."""
    cfg = load_backtest_config(config)
    try:
        with console.status(f"Backtesting [bold]{cfg.name}[/] …"):
            run = run_config(cfg)
    except MissingDataError as exc:
        console.print(f"[bold red]error:[/] {exc}")
        raise typer.Exit(code=2) from None
    if markdown:
        typer.echo(run.report.to_markdown())
    else:
        console.print(terminal.backtest_report(run.report))
    if save:
        path = save_run(run, out, datetime.now(UTC))
        console.print(f"  [dim]Saved report, equity curve and trades to[/] {path}\n")


@app.command()
def strategies() -> None:
    """List available strategies and their parameters."""
    console.print(terminal.strategies_table(sorted(discover().items())))


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
    added: dict[str, int] = {}
    with console.status("") as status:
        for symbol in symbols:
            status.update(f"Downloading [bold]{symbol}[/] {timeframe} from {exchange} …")
            added.update(update(store, exchange, [symbol], timeframe, _utc_date(start)))
    rows = [
        (s, added.get(s, 0), quality_report(store.read(exchange, s, timeframe), timeframe))
        for s in symbols
    ]
    console.print(terminal.download_table(rows))


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
        console.print(terminal.quality_panel(f"{exchange} {symbol} {timeframe}", report))
        failed |= not report.passed()
    raise typer.Exit(code=1 if failed else 0)


@data_app.command("list")
def data_list(root: DataRoot = Path("data")) -> None:
    """List stored series."""
    store = OhlcvStore(root)
    rows = [(e, tf, s, store.last_ts(e, s, tf)) for e, tf, s in store.list_series()]
    if not rows:
        console.print("[yellow]No data yet.[/] Try: cryptolab data download BTC/USDT")
        return
    console.print(terminal.series_table(rows))


if __name__ == "__main__":
    app()
