from datetime import UTC
from pathlib import Path

import pytest
from pydantic import ValidationError

from cryptolab.config.backtest import load_backtest_config

CONFIGS = Path(__file__).resolve().parents[2] / "configs" / "backtests"


@pytest.mark.parametrize("path", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.name)
def test_shipped_configs_load(path: Path) -> None:
    cfg = load_backtest_config(path)
    assert cfg.data.start.tzinfo is UTC
    assert cfg.costs.fee_bps > 0  # never a zero-cost default in shipped configs
    assert len(cfg.config_hash()) == 16


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        "name: x\ndata: {symbols: [BTC/USDT], start: 2024-01-01}\n"
        "strategy: {name: buy_and_hold}\nlive: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_backtest_config(p)


def test_hash_changes_with_params(tmp_path: Path) -> None:
    base = "name: x\ndata: {symbols: [BTC/USDT], start: 2024-01-01}\n"
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text(base + "strategy: {name: ma_crossover, params: {fast: 5}}\n", encoding="utf-8")
    b.write_text(base + "strategy: {name: ma_crossover, params: {fast: 6}}\n", encoding="utf-8")
    assert load_backtest_config(a).config_hash() != load_backtest_config(b).config_hash()
