"""Terminal rendering for the CLI (presentation only; nothing imports this but ``cli``).

Colour convention: green = the strategy did better than the benchmark on that row,
red = worse, dim = neutral (counts, activity).
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cryptolab.analytics import Report
from cryptolab.data.quality import QualityReport
from cryptolab.data.schema import TIMEFRAME_SECONDS

GOOD, BAD, DIM, ACCENT = "green", "red", "dim", "cyan"
WIDTH = 76  # report width; fits an 80-column terminal

Fmt = Callable[[float], str]


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _pct(x: float) -> str:
    return f"{x * 100:+,.1f}%" if _finite(x) else "—"


def _pct_plain(x: float) -> str:
    return f"{x * 100:,.1f}%" if _finite(x) else "—"


def _pp(x: float) -> str:
    return f"{x * 100:+,.1f} pp" if _finite(x) else "—"


def _ratio(x: float) -> str:
    return f"{x:.2f}" if _finite(x) else "—"


def _sratio(x: float) -> str:
    return f"{x:+.2f}" if _finite(x) else "—"


def _int(x: float) -> str:
    return f"{int(x):,}" if _finite(x) else "—"


def _sint(x: float) -> str:
    return f"{int(x):+,}" if _finite(x) else "—"


def _money(x: float) -> str:
    return f"${x:,.0f}" if _finite(x) else "—"


def _smoney(x: float) -> str:
    if not _finite(x):
        return "—"
    return f"+${x:,.0f}" if x >= 0 else f"-${-x:,.0f}"


def _days(bars: float, timeframe: str) -> float:
    return bars * TIMEFRAME_SECONDS[timeframe] / 86400


@dataclass(frozen=True)
class _Row:
    label: str
    field: str
    fmt: Fmt
    diff_fmt: Fmt
    higher_is_better: bool | None  # None = neutral, no colour


_SECTIONS: tuple[tuple[str, tuple[_Row, ...]], ...] = (
    (
        "Return",
        (
            _Row("Total return", "total_return", _pct, _pp, True),
            _Row("CAGR", "cagr", _pct, _pp, True),
        ),
    ),
    (
        "Risk",
        (
            _Row("Volatility (ann.)", "volatility_ann", _pct_plain, _pp, False),
            _Row("Max drawdown", "max_drawdown", _pct, _pp, True),  # less negative is better
        ),
    ),
    (
        "Risk-adjusted",
        (
            _Row("Sharpe", "sharpe", _ratio, _sratio, True),
            _Row("Sortino", "sortino", _ratio, _sratio, True),
        ),
    ),
    (
        "Trading",
        (
            _Row("Win rate", "win_rate", _pct_plain, _pp, None),
            _Row("Round trips", "n_round_trips", _int, _sint, None),
            _Row("Fills", "n_fills", _int, _sint, None),
            _Row("Turnover (x equity / yr)", "turnover_ann", _ratio, _sratio, None),
            _Row("Fees paid", "total_fees", _money, _smoney, False),
        ),
    ),
)


def _diff_style(diff: float, higher_is_better: bool | None) -> str:
    if higher_is_better is None or not _finite(diff) or abs(diff) < 1e-12:
        return DIM
    return GOOD if (diff > 0) == higher_is_better else BAD


def _years(report: Report) -> float:
    return (report.end - report.start).total_seconds() / (365 * 86400)


def _header(report: Report) -> Panel:
    symbols = report.meta.get("data", {})
    syms = ", ".join(symbols) if isinstance(symbols, dict) and symbols else "?"
    fee = "n/a" if report.fee_bps is None else f"{report.fee_bps:g} bps"
    slip = "n/a" if report.slippage_bps is None else f"{report.slippage_bps:g} bps"
    profile = (report.risk_profile or "n/a").replace("\\", "/").rsplit("/", 1)[-1]
    profile = profile.removeprefix("risk.").removesuffix(".yaml")
    lines = Text()
    lines.append(f"{syms}\n", style="bold")
    lines.append(f"{report.timeframe} candles  ·  ")
    lines.append(f"{report.start:%Y-%m-%d} → {report.end:%Y-%m-%d}")
    lines.append(f"  ({_years(report):.1f} years)\n", style=DIM)
    lines.append(f"Start {_money(report.initial_capital)}  ·  fees {fee}  ·  slippage {slip}")
    lines.append(f"  ·  risk: {profile}", style=DIM)
    return Panel(
        lines,
        width=WIDTH,
        title=f"[bold]{report.strategy_name}[/]",
        title_align="left",
        border_style=ACCENT,
        box=box.ROUNDED,
    )


def _verdict(report: Report) -> Text:
    s, b = report.strategy, report.benchmark
    cap = report.initial_capital
    end_s, end_b = cap * (1 + s.total_return), cap * (1 + b.total_return)
    beat_return = s.total_return > b.total_return
    beat_sharpe = _finite(s.sharpe) and _finite(b.sharpe) and s.sharpe > b.sharpe
    t = Text()
    t.append("  Final equity  ")
    t.append(_money(end_s), style=f"bold {GOOD if end_s >= cap else BAD}")
    t.append(f"   vs {report.benchmark_name} ")
    t.append(_money(end_b), style="bold")
    t.append("\n  Verdict       ")
    if beat_return and beat_sharpe:
        t.append("Beat BTC on return and risk-adjusted return", style=f"bold {GOOD}")
    elif beat_return:
        t.append("Higher return than BTC, but worse Sharpe", style="bold yellow")
    elif beat_sharpe:
        t.append("Lower return than BTC, but better Sharpe", style="bold yellow")
    else:
        t.append("Did not beat buying and holding BTC", style=f"bold {BAD}")
    return t


def _metrics_table(report: Report) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD, pad_edge=False, show_edge=False, header_style="bold", width=WIDTH
    )
    table.add_column("", min_width=26)
    table.add_column("Strategy", justify="right", min_width=11)
    table.add_column(report.benchmark_name, justify="right", min_width=11)
    table.add_column("Difference", justify="right", min_width=12)
    diff = report.difference
    for i, (section, rows) in enumerate(_SECTIONS):
        if i:
            table.add_row()
        table.add_row(Text(section.upper(), style=f"bold {ACCENT}"))
        for r in rows:
            sv = float(getattr(report.strategy, r.field))
            bv = float(getattr(report.benchmark, r.field))
            d = diff[r.field]
            table.add_row(
                f"  {r.label}",
                r.fmt(sv),
                Text(r.fmt(bv), style=DIM),
                Text(r.diff_fmt(d), style=_diff_style(d, r.higher_is_better)),
            )
        if section == "Risk":
            s_days = _days(report.strategy.max_drawdown_duration_bars, report.timeframe)
            b_days = _days(report.benchmark.max_drawdown_duration_bars, report.timeframe)
            table.add_row(
                "  Longest drawdown",
                f"{s_days:,.0f} days",
                Text(f"{b_days:,.0f} days", style=DIM),
                Text(f"{s_days - b_days:+,.0f} days", style=_diff_style(s_days - b_days, False)),
            )
    return table


def _notes(report: Report) -> RenderableType | None:
    items: list[str] = list(report.warnings)
    if report.n_rejections:
        items.append(f"{report.n_rejections:,} orders blocked or clipped by the risk rules")
    if not items:
        return None
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=3, justify="right")
    grid.add_column(width=WIDTH - 4)
    for w in items:
        grid.add_row(Text("!", style="bold yellow"), Text(w, style="yellow"))
    return grid


def backtest_report(report: Report) -> RenderableType:
    parts: list[RenderableType] = [
        _header(report),
        _verdict(report),
        Text(),
        _metrics_table(report),
        Text(),
    ]
    notes = _notes(report)
    if notes is not None:
        parts.append(notes)
    return Group(*parts)


# ------------------------------------------------------------------------------ data


def _status(passed: bool) -> Text:
    return Text("PASS", style=f"bold {GOOD}") if passed else Text("CHECK", style=f"bold {BAD}")


def download_table(rows: Iterable[tuple[str, int, QualityReport]]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("Symbol", no_wrap=True)
    table.add_column("New", justify="right", no_wrap=True)
    table.add_column("Bars", justify="right", no_wrap=True)
    table.add_column("Range (UTC)", no_wrap=True)
    table.add_column("Missing", justify="right", no_wrap=True)
    table.add_column("Quality", justify="center", no_wrap=True)
    for symbol, added, q in rows:
        rng = (
            f"{q.first_ts:%Y-%m-%d} → {q.last_ts:%Y-%m-%d}"
            if q.first_ts is not None and q.last_ts is not None
            else "—"
        )
        table.add_row(
            Text(symbol, style="bold"),
            Text(f"+{added:,}", style=GOOD if added else DIM),
            f"{q.n_bars:,}",
            rng,
            f"{q.missing_bars:,}" + (f" ({q.missing_pct:.3f}%)" if q.missing_bars else ""),
            _status(q.passed()),
        )
    return table


def quality_panel(title: str, q: QualityReport, max_gaps: int = 10) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=DIM)
    grid.add_column()
    rng = (
        f"{q.first_ts:%Y-%m-%d %H:%M} → {q.last_ts:%Y-%m-%d %H:%M} UTC"
        if q.first_ts is not None and q.last_ts is not None
        else "no data"
    )
    grid.add_row("Range", rng)
    grid.add_row("Bars", f"{q.n_bars:,} present / {q.expected_bars:,} expected")
    grid.add_row(
        "Missing",
        Text(
            f"{q.missing_bars:,} ({q.missing_pct:.3f}%) in {len(q.gaps)} gap(s)",
            style=GOOD if q.missing_bars == 0 else "yellow",
        ),
    )
    grid.add_row(
        "Duplicates", Text(f"{q.duplicate_ts:,}", style=GOOD if not q.duplicate_ts else BAD)
    )
    grid.add_row(
        "Bad rows",
        Text(f"{q.bad_value_rows:,}", style=GOOD if not q.bad_value_rows else BAD),
    )
    for g in q.gaps[:max_gaps]:
        start, end, n = g
        grid.add_row("  gap", f"{start:%Y-%m-%d %H:%M} … {end:%Y-%m-%d %H:%M}  ({n} bars)")
    if len(q.gaps) > max_gaps:
        grid.add_row("", Text(f"… {len(q.gaps) - max_gaps} more gaps", style=DIM))
    return Panel(
        grid,
        title=Text.assemble((title, "bold"), "  ", _status(q.passed())),
        title_align="left",
        width=WIDTH,
        border_style=GOOD if q.passed() else BAD,
        box=box.ROUNDED,
    )


def series_table(rows: Iterable[tuple[str, str, str, datetime | None]]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    for name in ("Exchange", "Timeframe", "Symbol", "Last bar (UTC)"):
        table.add_column(name)
    for exchange, timeframe, symbol, last in rows:
        table.add_row(
            exchange,
            timeframe,
            Text(symbol, style="bold"),
            f"{last:%Y-%m-%d %H:%M}" if last is not None else "—",
        )
    return table


# ------------------------------------------------------------------------ strategies


def _signature(cls: type) -> str:
    params = []
    for p in inspect.signature(cls).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        params.append(p.name if p.default is p.empty else f"{p.name}={p.default!r}")
    return ", ".join(params)


def strategies_table(classes: Sequence[tuple[str, type]]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Name", style=f"bold {ACCENT}", no_wrap=True)
    table.add_column("Params")
    table.add_column("What it does", style=DIM)
    for name, cls in classes:
        summary = getattr(cls, "summary", None)
        if not isinstance(summary, str):
            summary = (inspect.getdoc(cls) or "").split("\n\n", 1)[0].replace("\n", " ")
        table.add_row(name, _signature(cls), summary.replace("``", ""))
    return table


def make_console() -> Console:
    return Console(highlight=False, soft_wrap=False)
