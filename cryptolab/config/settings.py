"""Runtime settings.

Safety properties (docs/sdd/risk-model.md §1, decision-log D-003):
- ``TradingMode`` deliberately has no LIVE member. A live mode can only be added
  by a PR that references an Ethan LIVE-SIGNOFF entry in decision-log.md;
  tests/test_safety_guards.py enforces this.
- The default mode is PAPER.
- Exchange credentials are read from ``CRYPTOLAB_*`` environment variables (or an
  untracked ``.env``) and held as ``SecretStr`` so they never appear in reprs or logs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    TESTNET = "testnet"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRYPTOLAB_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    mode: TradingMode = TradingMode.PAPER

    # Testnet credentials only. There is intentionally no field for live keys.
    testnet_api_key: SecretStr | None = None
    testnet_api_secret: SecretStr | None = None
