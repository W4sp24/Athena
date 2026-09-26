"""Kill switch state machine and persistence (risk-model.md §3, FR-25, NFR-07)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cryptolab.risk import (
    InMemoryKillSwitch,
    KillSwitch,
    ResetRefusedError,
    RiskMarks,
    SqliteKillSwitch,
    TripReason,
)

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _sqlite_factory(tmp_path: Path) -> Callable[[], KillSwitch]:
    path = tmp_path / "risk_state.sqlite"
    return lambda: SqliteKillSwitch(path, create_if_missing=True)


@pytest.fixture(params=["memory", "sqlite"])
def make_switch(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[], KillSwitch]:
    if request.param == "memory":
        ks = InMemoryKillSwitch()
        return lambda: ks
    return _sqlite_factory(tmp_path)


# ------------------------------------------------------------ shared semantics


def test_starts_armed(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    assert not ks.is_tripped()
    st = ks.state()
    assert st.tripped is False
    assert st.reason is None


def test_trip_then_state_is_tripped(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    ks.trip(TripReason.MANUAL, T0, "operator halt")
    st = make_switch().state()
    assert st.tripped
    assert st.reason is TripReason.MANUAL
    assert st.ts == T0
    assert st.detail == "operator halt"


def test_second_trip_keeps_first_reason_but_is_recorded(
    make_switch: Callable[[], KillSwitch],
) -> None:
    ks = make_switch()
    ks.trip(TripReason.DAILY_LOSS, T0, "a")
    ks.trip(TripReason.MAX_DRAWDOWN, T0 + timedelta(hours=1), "b")
    assert ks.state().reason is TripReason.DAILY_LOSS
    events = [e.event for e in ks.history()]
    assert events[-2:] == ["trip", "trip_while_tripped"]
    assert ks.history()[-1].reason == TripReason.MAX_DRAWDOWN.value


def test_manual_trip_can_be_reset_with_reason(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    ks.trip(TripReason.MANUAL, T0)
    ks.reset("checked, all good", T0 + timedelta(minutes=5))
    assert not make_switch().is_tripped()
    last = ks.history()[-1]
    assert last.event == "reset"
    assert last.detail == "checked, all good"


def test_reset_requires_a_reason(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    ks.trip(TripReason.MANUAL, T0)
    with pytest.raises(ResetRefusedError, match="reason"):
        ks.reset("   ", T0)
    assert ks.is_tripped()


def test_daily_loss_reset_refused_same_utc_day(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    ks.trip(TripReason.DAILY_LOSS, T0)
    with pytest.raises(ResetRefusedError, match="DAILY_LOSS"):
        ks.reset("try", T0 + timedelta(hours=13))  # 23:00 same UTC day
    assert ks.is_tripped()
    ks.reset("new day", datetime(2026, 1, 6, 0, 0, tzinfo=UTC))
    assert not ks.is_tripped()


def test_daily_loss_reset_with_override_is_allowed_and_logged(
    make_switch: Callable[[], KillSwitch],
) -> None:
    ks = make_switch()
    ks.save_marks(RiskMarks(hwm=100.0, anchor_day=T0.date(), anchor_equity=100.0))
    ks.trip(TripReason.DAILY_LOSS, T0)
    ks.reset("override: bad tick", T0 + timedelta(hours=1), override=True)
    assert not ks.is_tripped()
    assert ks.history()[-1].event == "reset_override"
    # an override re-bases both anchors
    assert ks.marks() == RiskMarks(hwm=None, anchor_day=None, anchor_equity=None)


def test_drawdown_reset_refused_without_override(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    ks.trip(TripReason.MAX_DRAWDOWN, T0)
    with pytest.raises(ResetRefusedError, match="MAX_DRAWDOWN"):
        ks.reset("please", T0 + timedelta(days=3))
    ks.reset("reviewed", T0 + timedelta(days=3), override=True)
    assert not ks.is_tripped()


@pytest.mark.parametrize(
    "reason",
    [
        TripReason.RECON_MISMATCH,
        TripReason.BROKER_ERRORS,
        TripReason.STALE_DATA,
        TripReason.CLOCK_SKEW,
        TripReason.AUDIT_WRITE_FAILED,
    ],
)
def test_unverifiable_conditions_need_override(
    make_switch: Callable[[], KillSwitch], reason: TripReason
) -> None:
    ks = make_switch()
    ks.trip(reason, T0)
    with pytest.raises(ResetRefusedError):
        ks.reset("x", T0 + timedelta(days=1))
    ks.reset("x", T0 + timedelta(days=1), override=True)
    assert not ks.is_tripped()


def test_non_override_reset_clears_hwm_but_keeps_daily_anchor(
    make_switch: Callable[[], KillSwitch],
) -> None:
    """risk-model §3: M is the HWM since the last manual reset."""
    ks = make_switch()
    ks.save_marks(RiskMarks(hwm=120.0, anchor_day=T0.date(), anchor_equity=110.0))
    ks.trip(TripReason.MANUAL, T0)
    ks.reset("ok", T0)
    assert ks.marks() == RiskMarks(hwm=None, anchor_day=T0.date(), anchor_equity=110.0)


def test_reset_when_armed_is_a_noop(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    n = len(ks.history())
    ks.reset("nothing to do", T0)
    assert not ks.is_tripped()
    assert len(ks.history()) == n


def test_naive_timestamps_are_rejected(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    with pytest.raises(ValueError, match="UTC"):
        ks.trip(TripReason.MANUAL, datetime(2026, 1, 1))  # noqa: DTZ001


def test_marks_roundtrip(make_switch: Callable[[], KillSwitch]) -> None:
    ks = make_switch()
    assert ks.marks() == RiskMarks(None, None, None)
    m = RiskMarks(hwm=1.5, anchor_day=date(2026, 1, 5), anchor_equity=1.25)
    ks.save_marks(m)
    assert make_switch().marks() == m


# ------------------------------------------------------------ sqlite specifics


def test_sqlite_trip_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    SqliteKillSwitch(path, create_if_missing=True).trip(TripReason.DAILY_LOSS, T0, "-3.2%")
    reopened = SqliteKillSwitch(path)  # a new process: must NOT re-arm
    st = reopened.state()
    assert st.tripped
    assert st.reason is TripReason.DAILY_LOSS
    assert st.detail == "-3.2%"
    assert [e.event for e in reopened.history()] == ["init", "trip"]


def test_sqlite_trip_is_persisted_before_returning(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    ks = SqliteKillSwitch(path, create_if_missing=True)
    ks.trip(TripReason.MANUAL, T0)
    with sqlite3.connect(path) as conn:
        (status,) = conn.execute("SELECT status FROM kill_switch").fetchone()
    assert status == "TRIPPED"


def test_sqlite_missing_file_without_create_is_tripped(tmp_path: Path) -> None:
    ks = SqliteKillSwitch(tmp_path / "nope.sqlite")
    st = ks.state()
    assert st.tripped
    assert st.reason is TripReason.STATE_UNREADABLE
    assert not (tmp_path / "nope.sqlite").exists()  # never silently created


def test_sqlite_corrupt_file_is_tripped(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    path.write_bytes(b"this is not a sqlite database at all" * 100)
    ks = SqliteKillSwitch(path, create_if_missing=True)
    st = ks.state()
    assert st.tripped
    assert st.reason is TripReason.STATE_UNREADABLE
    assert ks.is_tripped()


def test_sqlite_file_corrupted_after_start_is_tripped(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    ks = SqliteKillSwitch(path, create_if_missing=True)
    assert not ks.is_tripped()
    path.write_bytes(b"\x00garbage" * 512)
    assert ks.state().reason is TripReason.STATE_UNREADABLE


def test_sqlite_file_deleted_after_start_is_tripped(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    ks = SqliteKillSwitch(path, create_if_missing=True)
    path.unlink()
    assert ks.state().reason is TripReason.STATE_UNREADABLE
    assert not path.exists()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE kill_switch SET status = 'MAYBE'",
        "UPDATE kill_switch SET status = 'TRIPPED', reason = 'NOT_A_REASON', ts = '2026-01-01'",
        "UPDATE kill_switch SET status = 'TRIPPED', reason = 'MANUAL', ts = 'yesterday'",
        "UPDATE kill_switch SET status = 'TRIPPED', reason = 'MANUAL', ts = '2026-01-01T00:00:00'",
        "DELETE FROM kill_switch",
        "DROP TABLE risk_marks",
    ],
)
def test_sqlite_invalid_rows_are_tripped(tmp_path: Path, sql: str) -> None:
    path = tmp_path / "risk_state.sqlite"
    ks = SqliteKillSwitch(path, create_if_missing=True)
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
    conn.close()
    assert ks.state().reason is TripReason.STATE_UNREADABLE


def test_sqlite_unreadable_state_cannot_be_reset(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    path.write_bytes(b"junk" * 1000)
    ks = SqliteKillSwitch(path)
    with pytest.raises(ResetRefusedError, match="STATE_UNREADABLE"):
        ks.reset("please", T0, override=True)
    assert ks.is_tripped()


def test_sqlite_failed_trip_write_stays_tripped_in_process(tmp_path: Path) -> None:
    """If a trip cannot be persisted, this process must still stop trading."""
    path = tmp_path / "risk_state.sqlite"
    ks = SqliteKillSwitch(path, create_if_missing=True)
    path.write_bytes(b"junk" * 1000)
    ks.trip(TripReason.MAX_DRAWDOWN, T0, "dd")
    # repair the file back to an ARMED state behind the switch's back
    path.unlink()
    SqliteKillSwitch(path, create_if_missing=True)
    st = ks.state()
    assert st.tripped
    assert st.reason is TripReason.MAX_DRAWDOWN


def test_sqlite_marks_unreadable_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    path.write_bytes(b"junk" * 1000)
    ks = SqliteKillSwitch(path)
    assert ks.marks() == RiskMarks(None, None, None)
    assert ks.history() == []


def test_sqlite_existing_file_is_not_overwritten_by_create(tmp_path: Path) -> None:
    path = tmp_path / "risk_state.sqlite"
    SqliteKillSwitch(path, create_if_missing=True).trip(TripReason.MANUAL, T0)
    assert SqliteKillSwitch(path, create_if_missing=True).is_tripped()
