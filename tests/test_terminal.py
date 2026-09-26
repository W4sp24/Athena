"""Terminal renderers: render without error, including NaN metrics, and show key facts."""

from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from cryptolab import terminal
from cryptolab.config.backtest import load_backtest_config
from cryptolab.data.quality import quality_report
from cryptolab.runner import run_config
from cryptolab.strategies.registry import discover
from tests.data.conftest import make_frame
from tests.test_runner import _config


def _render(renderable: object, width: int = 80) -> str:
    console = Console(width=width, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_backtest_report_fits_80_columns(tmp_path: Path) -> None:
    run = run_config(load_backtest_config(_config(tmp_path, "ma_crossover", "{fast: 5, slow: 20}")))
    out = _render(terminal.backtest_report(run.report))
    assert "Final equity" in out and "Verdict" in out
    assert "BTC buy & hold" in out and "Sharpe" in out
    assert max(len(line.rstrip()) for line in out.splitlines()) <= 80


def test_verdicts() -> None:
    assert "Did not beat" in _render(terminal._verdict(_fake(0.1, 0.5, 0.2, 1.0)))
    assert "Beat BTC" in _render(terminal._verdict(_fake(0.9, 2.0, 0.2, 1.0)))
    assert "worse Sharpe" in _render(terminal._verdict(_fake(0.9, 0.5, 0.2, 1.0)))
    assert "better Sharpe" in _render(terminal._verdict(_fake(0.1, 2.0, 0.2, 1.0)))


def _fake(ret: float, sharpe: float, b_ret: float, b_sharpe: float):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        strategy=SimpleNamespace(total_return=ret, sharpe=sharpe),
        benchmark=SimpleNamespace(total_return=b_ret, sharpe=b_sharpe),
        initial_capital=10_000.0,
        benchmark_name="BTC buy & hold",
    )


def test_diff_colouring() -> None:
    assert terminal._diff_style(0.1, True) == terminal.GOOD
    assert terminal._diff_style(0.1, False) == terminal.BAD
    assert terminal._diff_style(0.1, None) == terminal.DIM
    assert terminal._diff_style(float("nan"), True) == terminal.DIM


def test_data_renderers() -> None:
    q = quality_report(make_frame(n_bars=48, skip=(3,)), "1h")
    assert "1 (2.083%)" in _render(terminal.download_table([("BTC/USDT", 48, q)]))
    panel = _render(terminal.quality_panel("binance BTC/USDT 1h", q))
    assert "CHECK" in panel and "gap" in panel


def test_strategies_table_lists_all() -> None:
    out = _render(terminal.strategies_table(sorted(discover().items())), width=120)
    for name in ("buy_and_hold", "ma_crossover", "momentum"):
        assert name in out
    assert "``" not in out
