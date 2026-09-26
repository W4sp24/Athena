"""RiskLimits config model and the two risk profiles (risk-model.md §2/§3, D-012, D-015)."""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest
import yaml

from cryptolab.config import RiskLimits, load_risk_limits

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "configs" / "risk.paper.yaml"
BACKTEST = ROOT / "configs" / "risk.backtest.yaml"


def _valid() -> dict[str, object]:
    return {
        "allowed_symbols": ["BTC/USDT"],
        "max_position_pct_equity": 25,
        "max_position_notional": 1000,
        "max_gross_exposure_pct": 100,
        "max_order_notional": 500,
        "min_order_notional": 10,
        "max_orders_per_hour": 20,
        "allow_short": False,
        "stale_data_seconds": 7200,
        "max_daily_loss_pct": 3,
        "max_drawdown_pct": 15,
    }


def test_paper_profile_is_the_signed_off_defaults() -> None:
    """D-012: 25% / 1,000 / 100% / 500 / 10 / 20, 3% daily loss, 15% drawdown."""
    lim = load_risk_limits(PAPER)
    assert lim.allowed_symbols is None
    assert lim.max_position_pct_equity == 25
    assert lim.max_position_notional == 1000
    assert lim.max_gross_exposure_pct == 100
    assert lim.max_order_notional == 500
    assert lim.min_order_notional == 10
    assert lim.max_orders_per_hour == 20
    assert lim.allow_short is False
    assert lim.stale_data_seconds is None  # = 2 x bar length (risk-model §2)
    assert lim.max_daily_loss_pct == 3
    assert lim.max_drawdown_pct == 15


def test_backtest_profile_is_the_research_profile() -> None:
    """D-015: explicit, visible research values, not a disabled flag."""
    lim = load_risk_limits(BACKTEST)
    assert lim.max_position_pct_equity == 100
    assert lim.max_gross_exposure_pct == 100
    assert lim.max_position_notional is None
    assert lim.max_order_notional is None
    assert lim.min_order_notional == 10
    assert lim.max_orders_per_hour is None
    assert lim.allow_short is True
    assert lim.max_daily_loss_pct == 100
    assert lim.max_drawdown_pct == 100


def test_limits_are_frozen() -> None:
    lim = RiskLimits.model_validate(_valid())
    with pytest.raises(pydantic.ValidationError):
        lim.max_position_pct_equity = 99  # type: ignore[misc]
    assert isinstance(lim.allowed_symbols, tuple)  # no mutable list to poke at


def test_unknown_key_is_rejected() -> None:
    """A typo'd limit must fail loudly, not silently fall back to nothing."""
    bad = _valid() | {"max_postion_pct_equity": 5}
    with pytest.raises(pydantic.ValidationError):
        RiskLimits.model_validate(bad)


@pytest.mark.parametrize("key", list(_valid()))
def test_every_key_is_required(key: str) -> None:
    bad = _valid()
    del bad[key]
    with pytest.raises(pydantic.ValidationError):
        RiskLimits.model_validate(bad)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_position_pct_equity", 0),
        ("max_position_pct_equity", 101),
        ("max_gross_exposure_pct", -1),
        ("max_gross_exposure_pct", 150),
        ("max_position_notional", 0),
        ("max_order_notional", -5),
        ("min_order_notional", -1),
        ("max_orders_per_hour", 0),
        ("stale_data_seconds", 0),
        ("max_daily_loss_pct", 0),
        ("max_daily_loss_pct", 101),
        ("max_drawdown_pct", 0),
        ("allowed_symbols", []),
        ("allowed_symbols", [""]),
        ("max_position_pct_equity", float("nan")),
    ],
)
def test_out_of_range_values_are_rejected(key: str, value: object) -> None:
    bad = _valid() | {key: value}
    with pytest.raises(pydantic.ValidationError):
        RiskLimits.model_validate(bad)


def test_load_rejects_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_risk_limits(p)


def test_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(_valid()), encoding="utf-8")
    assert load_risk_limits(str(p)) == RiskLimits.model_validate(_valid())
