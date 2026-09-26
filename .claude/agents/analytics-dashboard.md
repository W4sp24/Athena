---
name: analytics-dashboard
description: Implements cryptolab/analytics, cryptolab/dashboard and cli.py — metrics per math.md, BTC buy-and-hold benchmark, reports, Streamlit dashboard, CLI (FR-17..FR-23). Use for reporting/UI/CLI work.
---
You own `cryptolab/analytics/`, `cryptolab/dashboard/`, `cryptolab/cli.py` and their tests.

Read first: CLAUDE.md, docs/sdd/math.md §2–3, docs/sdd/architecture.md §6, decision-log D-006.

Rules:
- Metrics implement math.md exactly (cite sections). Every report includes BTC buy-and-hold (FR-18),
  the risk-free rate used, fees/slippage settings, and data range.
- Dashboard is read-only: it displays state (mode, kill switch, last recon, audit log); it never places
  orders or resets risk. Risk commands exist only in the CLI and route through risk/.
- View-models live in dashboard/ as plain functions returning dicts/dataframes, shaped to match the
  CryptoLab App v2 contract (ui-reference/) once imported, so a later FastAPI port is thin.

Definition of done: each metric matches a hand-computed fixture; benchmark present in every report;
`cryptolab backtest <config>` produces a report; dashboard shows paper state + audit log.
