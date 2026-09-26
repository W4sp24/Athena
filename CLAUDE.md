# CLAUDE.md: CryptoLab

CryptoLab is a solo-developer crypto quant research and backtesting platform. It started as a resume project, and **it will eventually trade real capital.** That's why it is safe by default: paper trading and exchange testnet are the only execution modes that exist until Ethan signs off on a live path.

Read before changing anything:
- `docs/sdd/architecture.md`
- `docs/sdd/math.md`
- `docs/sdd/risk-model.md`
- `docs/sdd/decision-log.md`
- `docs/context/CRYPTOLAB_CONTEXT.md` (original context + SRS summary)

## Non-negotiables (verbatim from the project brief; they apply from day one)

- Default mode is paper trading / exchange testnet. Live order execution
  is a separate, explicitly-enabled code path, never the default.
- No API keys, secrets, or credentials in git, ever — environment
  variables or a secrets manager only. Exchange keys used for live
  trading must be trade-only (withdrawals disabled) at the exchange level.
- Every order (paper or live) is logged with enough detail to reconstruct
  what happened and why (signal inputs, timestamp, size, price, fees).
- Hard position-size limits and a max-daily-loss / max-drawdown kill
  switch that halts new orders automatically — these are config values,
  not something a strategy can override.
- Local state and exchange state are reconciled on a schedule; a
  mismatch is a loud failure, not a silent one.
- Nothing gets a path to live capital without passing the specific gate
  criteria defined in Phase 2 below AND a manual go/no-go from Ethan.

("Phase 2" is defined in `docs/sdd/deployment.md`.)

## Live-trading merge rule

**No live-trading code path may be merged without Ethan's explicit sign-off, referenced in `docs/sdd/decision-log.md`.**

- A sign-off is a decision-log entry with status `Accepted`, signed by Ethan, containing a line `LIVE-SIGNOFF: <entry id>`.
- The PR that adds live code must quote that ID in its description.
- `tests/test_safety_guards.py` enforces the mechanical part in CI:
  - `TradingMode` has no live member
  - CCXT sandbox is never turned off
  - no config enables live
  - no `live*` module exists without a `LIVE-SIGNOFF` line
- Never weaken or skip those tests to get a PR green.

## Module map (`cryptolab/`)

| Module | Responsibility | Must not import |
|---|---|---|
| `config/` | YAML + Pydantic settings. Secrets come from env only. Risk limits are frozen. | anything else in `cryptolab` |
| `data/` | CCXT OHLCV download, incremental update, gap/dupe checks, Parquet + DuckDB store | engine, strategies, execution, risk |
| `news/` | headline collection, local-LLM scoring, leak-free per-bar aggregation | engine, strategies, execution, risk |
| `strategies/` | one file per strategy: `on_bar(view) -> target weights` | execution, risk, config, reconcile, ccxt |
| `engine/` | event-driven backtest loop, fills/fees/slippage, portfolio accounting | execution, dashboard |
| `risk/` | RiskGate, position limits, persisted kill switch | strategies, dashboard |
| `execution/` | Broker protocol, PaperBroker, TestnetBroker, append-only order audit log | strategies, dashboard |
| `reconcile/` | scheduled local-vs-exchange reconciliation. A mismatch trips the kill switch. | strategies, dashboard |
| `analytics/` | metrics per `math.md`, BTC buy-and-hold benchmark, reports | execution |
| `dashboard/` | Streamlit (for now). View-models are shaped for the later CryptoLab App v2 port. | ccxt |
| `cli.py` | Typer CLI entry point | — |

Import rules are enforced by `import-linter` (contracts in `pyproject.toml`).

Other directories:
- `prototypes/` holds throwaway spikes and is never imported.
- `ui-reference/` holds the Claude Design source and is never imported.
- `notebooks/` is for research only.

## Conventions

- **Python and tooling:** Python ≥3.12 (D-014). `uv` manages the environment. Code is formatted and linted with `ruff`, and type-checked with `mypy`.
- **Time:** all timestamps are UTC and tz-aware. A bar is identified by its **open** time.
- **Money:**
  - Use `float64` in the backtest.
  - Order sizes sent to an exchange are rounded with the exchange's precision via CCXT. Never round by hand.
  - Fees and slippage are always charged. There's no "gross" mode in reports.
- **Formulas:** every formula in code cites its `math.md` section, e.g. `# math.md §3.2`. If code and `math.md` disagree, that's a bug in one of them, so fix it and log it.
- **Requirement IDs:** requirements are FR-xx / NFR-xx from the SRS. Tests cite them in docstrings where applicable.
- **Git:**
  - Conventional commits (`feat(engine): …`, `fix(risk): …`, `docs(sdd): …`). A commit-msg hook enforces them.
  - One unit of work per branch and PR. No direct pushes to `main`. Self-review via the PR template.
  - Core PRs (`engine/`, `risk/`, `execution/`, `reconcile/`) need Ethan's review.
- **Decisions:** every architectural decision gets a `decision-log.md` entry (append-only, including alternatives considered).

## Running things

```bash
uv sync --all-extras                       # install
uv run pytest                              # all tests
uv run pytest --cov=cryptolab.engine --cov=cryptolab.risk --cov-fail-under=80   # coverage gate
uv run ruff check . && uv run ruff format --check .
uv run mypy cryptolab
uv run lint-imports                        # module boundary contracts
uv run pre-commit run --all-files          # secrets scan + lint + commit-msg rules
```

## Secrets

- `.env` is git-ignored. `.env.example` lists key names only.
- Settings read secrets via `pydantic-settings` with the `CRYPTOLAB_` prefix.
- Never print, log, or echo a secret value. Never paste one into a doc, test, or fixture.
- Gitleaks and detect-secrets run in pre-commit and CI.

## Sub-agents

Role definitions live in `.claude/agents/`: data-ingestion, backtest-engine, news-sentiment, analytics-dashboard, qa-docs.

- Each agent stays inside its module(s).
- qa-docs reviews every other agent's output against the SDD and the non-negotiables. It doesn't write features.
