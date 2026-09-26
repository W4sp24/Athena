"""Mechanical guards for the non-negotiables in CLAUDE.md.

These tests must never be skipped, xfailed, or weakened to make a PR pass.
Adding a live execution path requires an Ethan-signed ``LIVE-SIGNOFF: <id>`` line
in docs/sdd/decision-log.md (see CLAUDE.md, "Live-trading merge rule").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cryptolab.config import Settings, TradingMode

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "cryptolab"
CONFIGS = ROOT / "configs"
DECISION_LOG = ROOT / "docs" / "sdd" / "decision-log.md"

SANDBOX_OFF = re.compile(r"""set_sandbox_mode\s*\(\s*False\s*\)|['"]?sandbox['"]?\s*[:=]\s*False""")
LIVE_ENABLED_CFG = re.compile(r"^\s*(live|live_trading_enabled)\s*:\s*(true|yes|on)\b", re.I | re.M)
SIGNOFF = re.compile(r"^LIVE-SIGNOFF:\s*D-\d{3}\s*$", re.M)


def _py_files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def _has_signoff() -> bool:
    return bool(SIGNOFF.search(DECISION_LOG.read_text(encoding="utf-8")))


def test_default_mode_is_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTOLAB_MODE", raising=False)
    assert Settings(_env_file=None).mode is TradingMode.PAPER  # type: ignore[call-arg]


def test_trading_mode_has_no_live_member() -> None:
    names = {m.name.lower() for m in TradingMode} | {m.value.lower() for m in TradingMode}
    if any("live" in n for n in names):
        assert _has_signoff(), "Live TradingMode added without LIVE-SIGNOFF in decision-log.md"


def test_ccxt_sandbox_never_disabled() -> None:
    offenders = [
        str(p.relative_to(ROOT)) for p in _py_files() if SANDBOX_OFF.search(p.read_text("utf-8"))
    ]
    assert not offenders, f"CCXT sandbox disabled in: {offenders}"


def test_no_config_enables_live() -> None:
    if not CONFIGS.exists():
        return
    offenders = [
        str(p.relative_to(ROOT))
        for p in CONFIGS.rglob("*.y*ml")
        if LIVE_ENABLED_CFG.search(p.read_text("utf-8"))
    ]
    assert not offenders, f"Config enables live trading: {offenders}"


def test_no_live_module_without_signoff() -> None:
    live_modules = [p for p in _py_files() if p.stem.lower().startswith("live")]
    if live_modules:
        names = [p.name for p in live_modules]
        assert _has_signoff(), f"Live modules {names} exist without LIVE-SIGNOFF in decision-log.md"


def test_secrets_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CRYPTOLAB_TESTNET_API_SECRET", "not-a-real-secret-123"
    )  # pragma: allowlist secret
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "not-a-real-secret-123" not in repr(s)
    assert "not-a-real-secret-123" not in str(s.model_dump())


def test_guard_regex_catches_sandbox_off() -> None:
    """The guard itself must detect the patterns it claims to (planted-violation check)."""
    for snippet in ("ex.set_sandbox_mode(False)", "{'sandbox': False}", "sandbox=False"):
        assert SANDBOX_OFF.search(snippet), snippet
    assert LIVE_ENABLED_CFG.search("live: true\n")
    assert not SANDBOX_OFF.search("ex.set_sandbox_mode(True)")
