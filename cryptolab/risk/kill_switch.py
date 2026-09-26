"""Kill switch (risk-model.md §3, FR-25, NFR-07).

States: ``ARMED`` (normal) or ``TRIPPED(reason, ts, detail)`` (every new order rejected).

- ``InMemoryKillSwitch`` is for backtests: one fresh switch per run.
- ``SqliteKillSwitch`` is for paper/testnet: the trip is committed to SQLite *before*
  ``trip()`` returns, a restart never re-arms it, and a state file that is missing,
  corrupt, or malformed reads as ``TRIPPED(STATE_UNREADABLE)``. Every transition is
  appended to a ``history`` table.

The switch also persists the ``on_mark`` anchors (§3): the equity high-water mark since
the last manual reset and the daily 00:00 UTC anchor.

Reset rules (§3 "refused while the triggering condition still holds"), conservative reading:

- ``MANUAL``: allowed with a non-empty reason.
- ``DAILY_LOSS``: refused until the next UTC day, unless ``override=True``.
- ``MAX_DRAWDOWN``: refused unless ``override=True``. The condition is measured against the
  HWM since the last reset, so it keeps holding until a reset re-bases it.
- every other trigger (recon, broker errors, stale data, clock skew, audit failure): the
  switch cannot verify the condition itself, so ``override=True`` is required.
- ``STATE_UNREADABLE``: never reset through the API; the state file must be repaired by hand.

Every successful reset clears the HWM ("since the last manual reset"). An override reset
also clears the daily anchor, so the next mark re-bases both.
"""

from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


class TripReason(StrEnum):
    """Kill-switch trigger codes (risk-model.md §3 table) plus STATE_UNREADABLE."""

    DAILY_LOSS = "DAILY_LOSS"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    RECON_MISMATCH = "RECON_MISMATCH"
    BROKER_ERRORS = "BROKER_ERRORS"
    STALE_DATA = "STALE_DATA"
    CLOCK_SKEW = "CLOCK_SKEW"
    AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
    MANUAL = "MANUAL"
    STATE_UNREADABLE = "STATE_UNREADABLE"


@dataclass(frozen=True)
class KillSwitchState:
    tripped: bool
    reason: TripReason | None = None
    ts: datetime | None = None
    detail: str = ""


ARMED = KillSwitchState(tripped=False)


@dataclass(frozen=True)
class RiskMarks:
    """Persisted ``on_mark`` anchors (risk-model.md §3)."""

    hwm: float | None = None  # equity high-water mark since the last manual reset
    anchor_day: date | None = None  # UTC day of the daily anchor
    anchor_equity: float | None = None  # first mark at/after 00:00 UTC of anchor_day


EMPTY_MARKS = RiskMarks()


@dataclass(frozen=True)
class KillSwitchEvent:
    """One row of the transition history."""

    seq: int
    recorded_at: datetime
    event: str  # init | trip | trip_while_tripped | reset | reset_override
    status: str  # ARMED | TRIPPED, after the event
    reason: str | None
    detail: str


class ResetRefusedError(RuntimeError):
    """A manual reset was refused because its triggering condition may still hold."""


@runtime_checkable
class KillSwitch(Protocol):
    def state(self) -> KillSwitchState: ...

    def is_tripped(self) -> bool: ...

    def trip(self, reason: TripReason, ts: datetime, detail: str = "") -> None: ...

    def reset(self, reason: str, now: datetime, *, override: bool = False) -> None: ...

    def marks(self) -> RiskMarks: ...

    def save_marks(self, marks: RiskMarks) -> None: ...

    def history(self) -> list[KillSwitchEvent]: ...


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"timestamp must be tz-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


def _reset_refusal(st: KillSwitchState, reason: str, now: datetime, override: bool) -> str | None:
    """Return why a reset is refused, or None if it may proceed (risk-model.md §3)."""
    r = st.reason
    refusal: str | None
    if not reason.strip():
        refusal = "a non-empty reset reason is required"
    elif r is TripReason.STATE_UNREADABLE:
        refusal = "STATE_UNREADABLE: repair or restore the state file by hand"
    elif r is TripReason.MANUAL or override:
        refusal = None
    elif r is TripReason.DAILY_LOSS:
        same_day = st.ts is None or now.date() <= st.ts.astimezone(UTC).date()
        refusal = "DAILY_LOSS: refused until the next UTC day (use override)" if same_day else None
    elif r is TripReason.MAX_DRAWDOWN:
        refusal = "MAX_DRAWDOWN: condition holds until re-based; reset needs override"
    else:
        refusal = f"{r}: the switch cannot verify this condition has cleared; needs override"
    return refusal


class _BaseKillSwitch(ABC):
    """Shared trip/reset logic. Subclasses provide storage."""

    # --- storage hooks
    @abstractmethod
    def state(self) -> KillSwitchState: ...

    @abstractmethod
    def marks(self) -> RiskMarks: ...

    @abstractmethod
    def save_marks(self, marks: RiskMarks) -> None: ...

    @abstractmethod
    def history(self) -> list[KillSwitchEvent]: ...

    @abstractmethod
    def _commit(
        self,
        new_state: KillSwitchState,
        *,
        event: str,
        ts: datetime,
        reason: str | None,
        detail: str,
        marks: RiskMarks | None = None,
    ) -> None:
        """Atomically write state (+ optional marks) and append a history row."""

    # --- behaviour
    def is_tripped(self) -> bool:
        return self.state().tripped

    def trip(self, reason: TripReason, ts: datetime, detail: str = "") -> None:
        ts = _require_utc(ts)
        reason = TripReason(reason)
        current = self.state()
        if current.tripped and current.reason is not TripReason.STATE_UNREADABLE:
            # keep the first reason; still record the new trigger for the audit trail
            self._commit(
                current, event="trip_while_tripped", ts=ts, reason=reason.value, detail=detail
            )
            return
        new = KillSwitchState(tripped=True, reason=reason, ts=ts, detail=detail)
        self._commit(new, event="trip", ts=ts, reason=reason.value, detail=detail)
        log.error("KILL SWITCH TRIPPED: %s at %s (%s)", reason.value, ts.isoformat(), detail)

    def reset(self, reason: str, now: datetime, *, override: bool = False) -> None:
        now = _require_utc(now)
        current = self.state()
        if not current.tripped:
            return
        refusal = _reset_refusal(current, reason, now, override)
        if refusal is not None:
            raise ResetRefusedError(refusal)
        m = self.marks()
        new_marks = (
            EMPTY_MARKS
            if override
            else RiskMarks(hwm=None, anchor_day=m.anchor_day, anchor_equity=m.anchor_equity)
        )
        event = "reset_override" if override else "reset"
        prev = current.reason.value if current.reason else None
        self._commit(ARMED, event=event, ts=now, reason=prev, detail=reason, marks=new_marks)
        log.warning("kill switch reset (%s, override=%s): %s", prev, override, reason)


class InMemoryKillSwitch(_BaseKillSwitch):
    """Kill switch for backtests. Not persisted: a backtest run starts ARMED."""

    def __init__(self) -> None:
        self._state = ARMED
        self._marks = EMPTY_MARKS
        self._history: list[KillSwitchEvent] = []

    def state(self) -> KillSwitchState:
        return self._state

    def is_tripped(self) -> bool:  # hot path in the backtest loop
        return self._state.tripped

    def marks(self) -> RiskMarks:
        return self._marks

    def save_marks(self, marks: RiskMarks) -> None:
        self._marks = marks

    def history(self) -> list[KillSwitchEvent]:
        return list(self._history)

    def _commit(
        self,
        new_state: KillSwitchState,
        *,
        event: str,
        ts: datetime,
        reason: str | None,
        detail: str,
        marks: RiskMarks | None = None,
    ) -> None:
        self._state = new_state
        if marks is not None:
            self._marks = marks
        status = "TRIPPED" if new_state.tripped else "ARMED"
        self._history.append(
            KillSwitchEvent(len(self._history) + 1, ts, event, status, reason, detail)
        )


_SCHEMA = """
CREATE TABLE kill_switch (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,
    reason TEXT,
    ts TEXT,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE risk_marks (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hwm REAL,
    anchor_day TEXT,
    anchor_equity REAL
);
CREATE TABLE history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    detail TEXT NOT NULL
);
"""


def _parse_ts(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"bad timestamp {raw!r}")
    ts = datetime.fromisoformat(raw)
    return _require_utc(ts)


def _opt_float(raw: object) -> float | None:
    if raw is None:
        return None
    if not isinstance(raw, int | float):
        raise ValueError(f"bad number {raw!r}")
    return float(raw)


class SqliteKillSwitch(_BaseKillSwitch):
    """Persisted kill switch in ``state/risk_state.sqlite`` (D-005).

    The file is re-read on every call so a trip written by another process (for example
    ``cryptolab risk halt``) is honoured immediately. Any read failure is TRIPPED.
    """

    def __init__(self, path: str | Path, *, create_if_missing: bool = False) -> None:
        self._path = Path(path)
        # set when a trip could not be persisted: this process must stay halted
        self._sticky: KillSwitchState | None = None
        if create_if_missing and not self._path.exists():
            self._create()

    # --- connection helpers
    def _connect(self) -> sqlite3.Connection:
        # mode=rw: never silently create an empty (ARMED) database for a missing file.
        uri = self._path.resolve().as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)

    def _create(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self._path, isolation_level=None)) as conn:
            conn.execute("BEGIN")
            for stmt in filter(str.strip, _SCHEMA.split(";")):
                conn.execute(stmt)
            conn.execute("INSERT INTO kill_switch (id, status, detail) VALUES (1, 'ARMED', '')")
            conn.execute("INSERT INTO risk_marks (id) VALUES (1)")
            conn.execute(
                "INSERT INTO history (recorded_at, event, status, reason, detail) "
                "VALUES (?, 'init', 'ARMED', NULL, 'created')",
                (now,),
            )
            conn.execute("COMMIT")

    def _read(self) -> tuple[KillSwitchState, RiskMarks]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT status, reason, ts, detail FROM kill_switch").fetchall()
            mrows = conn.execute("SELECT hwm, anchor_day, anchor_equity FROM risk_marks").fetchall()
        if len(rows) != 1 or len(mrows) != 1:
            raise ValueError("state tables must hold exactly one row")
        status, reason, ts, detail = rows[0]
        if status == "ARMED":
            state = ARMED
        elif status == "TRIPPED":
            state = KillSwitchState(
                tripped=True,
                reason=TripReason(reason),
                ts=_parse_ts(ts),
                detail=str(detail),
            )
        else:
            raise ValueError(f"unknown kill switch status {status!r}")
        hwm, anchor_day, anchor_equity = mrows[0]
        marks = RiskMarks(
            hwm=_opt_float(hwm),
            anchor_day=None if anchor_day is None else date.fromisoformat(anchor_day),
            anchor_equity=_opt_float(anchor_equity),
        )
        return state, marks

    # --- KillSwitch
    def state(self) -> KillSwitchState:
        if self._sticky is not None:
            return self._sticky
        try:
            return self._read()[0]
        except Exception as exc:  # fail closed on anything (NFR-07)
            return KillSwitchState(
                tripped=True,
                reason=TripReason.STATE_UNREADABLE,
                ts=None,
                detail=f"{type(exc).__name__}: {exc}",
            )

    def reset(self, reason: str, now: datetime, *, override: bool = False) -> None:
        if self._sticky is not None:
            raise ResetRefusedError(
                "a trip could not be persisted in this process; restart after repairing state"
            )
        super().reset(reason, now, override=override)

    def marks(self) -> RiskMarks:
        try:
            return self._read()[1]
        except Exception:
            return EMPTY_MARKS

    def save_marks(self, marks: RiskMarks) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE risk_marks SET hwm = ?, anchor_day = ?, anchor_equity = ? WHERE id = 1",
                (
                    marks.hwm,
                    None if marks.anchor_day is None else marks.anchor_day.isoformat(),
                    marks.anchor_equity,
                ),
            )

    def history(self) -> list[KillSwitchEvent]:
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT seq, recorded_at, event, status, reason, detail "
                    "FROM history ORDER BY seq"
                ).fetchall()
        except Exception:
            return []
        return [
            KillSwitchEvent(int(s), _parse_ts(r), str(e), str(st), rs, str(d))
            for s, r, e, st, rs, d in rows
        ]

    def _commit(
        self,
        new_state: KillSwitchState,
        *,
        event: str,
        ts: datetime,
        reason: str | None,
        detail: str,
        marks: RiskMarks | None = None,
    ) -> None:
        status = "TRIPPED" if new_state.tripped else "ARMED"
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE kill_switch SET status = ?, reason = ?, ts = ?, detail = ? "
                    "WHERE id = 1",
                    (
                        status,
                        new_state.reason.value if new_state.reason else None,
                        new_state.ts.isoformat() if new_state.ts else None,
                        new_state.detail,
                    ),
                )
                if marks is not None:
                    conn.execute(
                        "UPDATE risk_marks SET hwm = ?, anchor_day = ?, anchor_equity = ? "
                        "WHERE id = 1",
                        (
                            marks.hwm,
                            None if marks.anchor_day is None else marks.anchor_day.isoformat(),
                            marks.anchor_equity,
                        ),
                    )
                conn.execute(
                    "INSERT INTO history (recorded_at, event, status, reason, detail) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts.isoformat(), event, status, reason, detail),
                )
                conn.execute("COMMIT")
        except Exception:
            if new_state.tripped:
                # Could not persist the trip: halt this process regardless (NFR-07).
                self._sticky = new_state
                log.exception("kill switch trip could not be persisted; halting in-process")
                return
            raise
