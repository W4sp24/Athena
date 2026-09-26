"""End-to-end: config -> store -> engine -> benchmark -> report -> saved files (FR-18, FR-21)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from cryptolab.cli import app
from cryptolab.config.backtest import load_backtest_config
from cryptolab.data.store import OhlcvStore
from cryptolab.runner import MissingDataError, run_config, save_run

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _frame(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pd.DataFrame(
        {
            "ts": pd.date_range(T0, periods=n, freq="1h", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.full(n, 10.0),
        }
    )


def _config(tmp_path: Path, strategy: str, params: str = "{}") -> Path:
    store = OhlcvStore(tmp_path / "data")
    store.write("binance", "BTC/USDT", "1h", _frame(300, 1))
    store.write("binance", "ETH/USDT", "1h", _frame(300, 2))
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        f"name: test {strategy}\n"
        f"data: {{symbols: [BTC/USDT, ETH/USDT], start: 2024-01-01, root: {tmp_path / 'data'}}}\n"
        f"strategy: {{name: {strategy}, params: {params}}}\n"
        f"risk_profile: {ROOT / 'configs' / 'risk.backtest.yaml'}\n",
        encoding="utf-8",
    )
    return cfg


def test_run_config_includes_benchmark_and_saves(tmp_path: Path) -> None:
    run = run_config(load_backtest_config(_config(tmp_path, "ma_crossover", "{fast: 5, slow: 20}")))
    assert run.report.benchmark.n_fills == 1  # BTC buy-and-hold buys once
    assert len(run.result.equity) == len(run.benchmark.equity) == 300
    assert run.result.meta["run_config_hash"] == run.config.config_hash()
    out = save_run(run, tmp_path / "reports", T0 + timedelta(days=1))
    for name in ("report.md", "report.json", "equity.csv", "fills.csv", "rejections.csv"):
        assert (out / name).exists()
    assert "BTC buy & hold" in (out / "report.md").read_text(encoding="utf-8")
    json.loads((out / "report.json").read_text(encoding="utf-8"))


def test_buy_and_hold_btc_equals_benchmark(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "buy_and_hold")
    text = cfg.read_text(encoding="utf-8").replace("[BTC/USDT, ETH/USDT]", "[BTC/USDT]")
    cfg.write_text(text, encoding="utf-8")
    run = run_config(load_backtest_config(cfg))
    pd.testing.assert_series_equal(run.result.equity, run.benchmark.equity, check_names=False)


def test_missing_data_is_a_clear_error(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "momentum", "{lookback: 24}")
    text = cfg.read_text(encoding="utf-8").replace("ETH/USDT", "SOL/USDT")
    cfg.write_text(text, encoding="utf-8")
    with pytest.raises(MissingDataError, match="cryptolab data download SOL/USDT"):
        run_config(load_backtest_config(cfg))


def test_cli_backtest(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "momentum", "{lookback: 24}")
    res = CliRunner().invoke(app, ["backtest", str(cfg), "--out", str(tmp_path / "reports")])
    assert res.exit_code == 0, res.output
    assert "BTC buy & hold" in res.output
    assert "Saved report" in res.output
