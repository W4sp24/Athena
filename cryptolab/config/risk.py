"""Frozen risk limits loaded from YAML (risk-model.md §1-§3, FR-24).

Limits are loaded once at runner start and handed only to ``RiskGate``. Strategies never
see this model (import-linter forbids ``strategies -> config``).

Units: every ``*_pct`` field is a percentage (``25`` means 25%). Notionals are in the
quote currency (USDT). Every key is required so a missing or misspelled limit fails
loudly instead of silently defaulting (NFR-07, fail closed).

This module is a leaf: it imports nothing else from ``cryptolab``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

_Pct = Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]
_PosMoney = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_Symbol = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class RiskLimits(BaseModel):
    """Position/order limits (risk-model.md §2) and kill-switch thresholds (§3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # §2. None = "any symbol in the run" (the pairs in the run's data config).
    allowed_symbols: Annotated[tuple[_Symbol, ...], Field(min_length=1)] | None
    # §2: |q_i| C_i / V after the fill, per symbol. <= 100: no leverage in v1 (math.md §5.5).
    max_position_pct_equity: _Pct
    # §2: absolute per-symbol cap in quote. None = no absolute cap.
    max_position_notional: _PosMoney | None
    # §2: sum_i |q_i| C_i / V after the fill.
    max_gross_exposure_pct: _Pct
    # §2: per-order notional; breaching it rejects (never clips). None = no cap.
    max_order_notional: _PosMoney | None
    # §2 / math.md §5.1: dust floor.
    min_order_notional: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    # §2: runaway-loop brake over a trailing hour. None = no rate limit.
    max_orders_per_hour: Annotated[int, Field(gt=0)] | None
    # §2 / D-011: honoured in backtest only; paper/testnet always reject shorts.
    allow_short: bool
    # §2: the latest bar must be at most this old. None = 2 x bar length of the run.
    stale_data_seconds: Annotated[int, Field(gt=0)] | None
    # §3 kill-switch triggers.
    max_daily_loss_pct: _Pct
    max_drawdown_pct: _Pct


def load_risk_limits(path: str | Path) -> RiskLimits:
    """Load and validate a risk profile YAML file (e.g. ``configs/risk.paper.yaml``)."""
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"risk limits file {path} must contain a YAML mapping")
    return RiskLimits.model_validate(raw)
