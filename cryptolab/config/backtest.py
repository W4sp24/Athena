"""Backtest run configuration (FR-12, FR-21): one YAML file fully describes a run.

Example: configs/backtests/ma_crossover_btc.yaml
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exchange: str = "binance"
    timeframe: str = "1h"
    symbols: list[str] = Field(min_length=1)
    start: datetime
    end: datetime | None = None
    root: Path = Path("data")

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class StrategySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class CostSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fee_bps: float = Field(default=10.0, ge=0)  # taker fee, math.md §5.3
    slippage_bps: float = Field(default=5.0, ge=0)  # math.md §5.2


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    data: DataSpec
    strategy: StrategySpec
    costs: CostSpec = CostSpec()
    initial_capital: float = Field(default=10_000.0, gt=0)
    risk_free_annual: float = 0.0  # math.md §3.2
    risk_profile: Path = Path("configs/risk.backtest.yaml")
    benchmark_symbol: str = "BTC/USDT"  # FR-18

    def config_hash(self) -> str:
        """Stable hash of the resolved config, recorded in every result (NFR-01)."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]


def load_backtest_config(path: str | Path) -> BacktestConfig:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return BacktestConfig.model_validate(raw)
