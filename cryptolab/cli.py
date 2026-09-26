"""CryptoLab command-line interface (FR-21). Commands are added as modules land."""

from __future__ import annotations

import typer

from cryptolab.config import Settings

app = typer.Typer(help="CryptoLab: research, backtesting, and paper trading.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """CryptoLab CLI. Modes: backtest | paper | testnet (no live mode exists)."""


@app.command()
def status() -> None:
    """Show the active trading mode."""
    typer.echo(f"mode: {Settings().mode.value}")


if __name__ == "__main__":
    app()
