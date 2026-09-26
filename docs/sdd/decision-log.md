# Decision log

**Append-only.** Never edit or delete an accepted entry. To change a decision, add a new entry that supersedes it and points back to the old one.

**Statuses:**
- `Proposed`: waiting on Ethan.
- `Accepted`
- `Superseded by D-xxx`

**Live-capital authorization** needs an entry that meets all of these:
- decided by Ethan
- status `Accepted`
- a line starting with the `LIVE-SIGNOFF` marker followed by its own ID (template in `deployment.md` §5)

CI (`tests/test_safety_guards.py`) looks for that line. **Nothing below is a sign-off.**

---

## D-001: Agents implement the engine core
Date: 2026-09-26 · Decided by: Ethan · Status: Accepted
- **Context:** The original plan had Ethan hand-write the engine core so he could defend it in interviews. The scope has since grown to production and, eventually, real capital.
- **Decision:** Sub-agents implement the engine core (event loop, fills/fees/slippage, accounting, metrics) along with everything else.
- **Alternatives considered:**
  - (a) Ethan hand-writes the core and agents do the scaffolding, tests, and docs. Best for interviews, slowest.
  - (b) A hybrid, where Ethan writes the money-touching parts.
- **Safeguards that come with this choice:**
  - Ethan signs off `math.md` and `risk-model.md` before engine code is written.
  - Every PR to engine/risk/execution/reconcile needs Ethan's review (CODEOWNERS).
  - The P2 spreadsheet is a golden test.
  - Code cites `math.md` sections, so Ethan can defend the design from the SDD.

## D-002: Tech stack as in the context doc
Date: 2026-09-26 · Decided by: Ethan (context doc), recorded by Claude · Status: Accepted
- **Decision:**
  - Python ≥3.11, pandas/NumPy, CCXT, Parquet + DuckDB
  - a local LLM via an OpenAI-compatible server
  - YAML + Pydantic, pytest, ruff, GitHub Actions, Streamlit
- **Additions:** `uv` (environment and lockfile), mypy (strict), import-linter (boundary enforcement), hypothesis (property tests for look-ahead), pre-commit with gitleaks, detect-secrets, and conventional-commit checks.
- **Alternatives considered:**
  - Polars instead of pandas: deferred. pandas is fine unless NFR-02 performance fails.
  - Poetry/pip-tools: `uv` is faster and already installed.
- **Layout:** the package is the flat `cryptolab/` from the context doc (not a `src/` layout). The new safety modules are `config/`, `risk/`, `execution/`, and `reconcile/`.

## D-003: Paper/testnet is the default; live is a gated Phase 2
Date: 2026-09-26 · Decided by: Ethan (project brief) · Status: Accepted
- **Supersedes:** the context doc's "real money: out of scope". Real capital is now an eventual goal.
- **Decision:**
  - `TradingMode` = {backtest, paper, testnet}. No live member exists.
  - Live execution needs the Phase 2 gates (`deployment.md` §1) plus a LIVE-SIGNOFF entry here.
  - CI guards enforce the mechanical parts.
- **Alternatives considered:** a live mode behind a config flag from the start, defaulting off. Rejected: code that exists tends to get used, and a flag can be flipped by accident. The live path is only written after sign-off.

## D-004: RiskGate sits outside the strategy layer
Date: 2026-09-26 · Decided by: Claude, pending Ethan's review · Status: Proposed
- **Decision:**
  - Strategies return target weights only.
  - The engine/runner converts targets to `OrderIntent`s, and each one passes through `RiskGate`, which holds the frozen limits.
  - Import-linter forbids strategies from importing risk, config, execution, reconcile, or ccxt.
  - RiskGate runs in every mode, including backtest.
- **Alternatives considered:**
  - (a) Strategies call a risk helper themselves. Rejected: a strategy could skip or alter it.
  - (b) Risk only in paper/live. Rejected: backtests would then test a different code path and overstate performance.

## D-005: Runtime safety state in SQLite
Date: 2026-09-26 · Decided by: Claude, pending Ethan's review · Status: Proposed
- **Decision:** The kill-switch state, daily anchors, and the order audit log live in SQLite files under `state/`. Market and news data stay in Parquet + DuckDB.
- **Why:** Transactional single-row appends, standard library, no server, and it survives crashes. DuckDB is optimized for analytics, not for many small transactional writes from a long-running process.
- **Alternatives considered:**
  - DuckDB for everything
  - plain JSONL. Kept as a mirror of the audit log, but it isn't transactional.

## D-006: Dashboard is Streamlit now; the CryptoLab App v2 design is ported later
Date: 2026-09-26 · Decided by: Ethan · Status: Accepted
- **Decision:**
  - Phase 1 dashboard in Streamlit (SRS FR-22).
  - The Claude Design prototype "CryptoLab App v2" is imported read-only into `ui-reference/` once the design MCP is connected.
  - `dashboard/` view-models are shaped to its `api.js`/`data.js` data contract, so a later port is a thin FastAPI layer.
- **Alternatives considered:**
  - Port the design immediately, with FastAPI serving its JSON.
  - Rebuild the design in Streamlit and abandon the HTML design.

## D-007: Hourly candles are the primary timeframe
Date: 2026-09-26 · Decided by: Ethan · Status: Accepted
- **Decision:** 1h is the primary timeframe; 1d is also supported. Annualization uses P = 8760 for hourly and 365 for daily (`math.md` §1).
- **Alternative considered:** daily primary. It's simpler and less noisy, but further from how news moves prices.

## D-008: Private repository
Date: 2026-09-26 · Decided by: Ethan · Status: Accepted
- **Decision:** The GitHub repo is private.
  - Branch protection on `main`: PRs required, CI required, no force-push.
  - Required approvals are set to **0**, because GitHub doesn't let an author approve their own PR. Self-review is enforced through the PR template checklist instead.
- **Why:** Once real capital is involved, the strategy logic, risk limits, and operational details shouldn't be public.

## D-009: Sentiment window anchored at decision time
Date: 2026-09-26 · Decided by: Claude, pending Ethan's review · Status: Proposed
- **Decision:** The "before the bar opens" rule (FR-07) applies to the bar we trade into, t+1, which opens at decision time d_t.
  - A headline is usable only if `max(published_at, first_seen_at) + δ < d_t`.
  - Proposed defaults: δ = 15 min, W = 24 h, half-life = 6 h (`math.md` §7).
- **Alternatives considered:** anchoring at the open of bar t. That's stricter by a full bar, and it discards up to an hour of legitimately available news.

## D-010: Kill switch halts but does not auto-flatten
Date: 2026-09-26 · Decided by: Claude, pending Ethan's review · Status: Proposed
- **Decision:** A trip blocks new orders and cancels open orders. Flattening existing positions is a separate, explicit operator command.
- **Why:** Trips often happen during outages or bad data. Auto-liquidating at that moment can realize losses at the worst prices.
- **Alternative considered:** auto-flatten on the `DAILY_LOSS`/`MAX_DRAWDOWN` triggers only. Worth reconsidering for live capital.

## D-011: Shorts exist in backtest simulation only
Date: 2026-09-26 · Decided by: Claude, pending Ethan's review · Status: Proposed
- **Context:** The SRS allows long/short *simulated* strategies, but the market is spot only, and spot accounts can't short without margin.
- **Decision:**
  - Negative weights are allowed in backtest only, with no borrow cost. That optimism is disclosed in the report.
  - RiskGate rejects shorts in paper and testnet.
- **Alternative considered:** removing shorts entirely. That would lose a research capability the SRS keeps.
