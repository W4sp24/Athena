# CryptoLab: Project Context

> Imported verbatim on 2026-09-26 from the context doc Ethan supplied (last updated 2026-09-23).
> Where this document and `docs/sdd/` disagree, the difference is recorded in
> `docs/sdd/decision-log.md`. Notable supersessions: real money is no longer
> permanently out of scope (D-003), and agents implement the engine core (D-001).

Last updated: 2026-09-23 Owner: Ethan (CS student, UST) Role of Claude: quant consultant. Analyze the system, help brainstorm, assess feasibility, write plans and the SRS, review code.

Full planning doc (feasibility, architecture, SRS, phased plan): https://claude.ai/code/artifact/b806db1e-e0e5-446d-b5e0-2646a984ee2c

## Current status

Phase: Ideate done. Next up is prototyping.

Decisions made:

- Project type: modular research and backtesting platform with an LLM news sentiment signal as the flagship module.
- Market: crypto (spot only).
- Purpose: a tool I'll keep using, and a resume project.
- Timeline: build the MVP in about 1 week, with the AI module possibly spilling into week 2.
- Build approach: Claude drafts and explains. Ethan writes or rewrites the engine core himself so he can defend it in interviews. Coding happens in Claude Code, and planning and review happen in chat.

Open decisions:

- Primary exchange (after checking access from the Philippines).
- News source, or a historical news dataset (for example from Kaggle), and how far back it goes.
- Main timeframe: daily or hourly candles.
- Local model for sentiment scoring.
- Public or private repo until the MVP.

## What the system is

Pull crypto market data and news, turn them into trading signals, test them honestly on history, and report whether they work after costs.

Success is not a high return number. Success is results that include costs, have no lookahead, are tested out of sample, and are compared against buy-and-hold BTC.

Architecture (data flows one way):

```
Exchange APIs (CCXT) -> Data store (Parquet + DuckDB)
News feeds -> LLM sentiment scorer -> Data store
Data store -> Strategy modules -> Backtest engine (event-driven) -> Analytics -> Dashboard
```

Modules:

- Data ingestion: OHLCV candles, incremental updates, quality checks.
- News + sentiment: headlines with UTC publish times, local LLM scores (-1 to +1 sentiment, plus relevance per coin), aggregated per bar using only news published before the bar opens.
- Strategy interface: one small interface for every strategy. Baselines are buy-and-hold, moving average crossover, and momentum.
- Backtest engine: bars processed strictly in time order, with fees, slippage, cash, positions, and a trade log.
- Analytics: return, volatility, Sharpe, Sortino, max drawdown, win rate, turnover, and the BTC buy-and-hold comparison. Walk-forward testing comes later.

Tech stack: Python 3.11+, pandas or Polars, NumPy, CCXT, Parquet, DuckDB, a local LLM through an OpenAI-compatible server, SciPy, statsmodels, YAML + Pydantic configs, pytest, ruff, GitHub Actions, and Streamlit for the first dashboard.

Repo layout:

```
cryptolab/
  data/  news/  strategies/  engine/  analytics/  dashboard/  cli.py
configs/  tests/  notebooks/  docs/  prototypes/
```

## SRS summary (v1.0)

In scope: spot crypto, daily and hourly candles, simulated long-only and long/short strategies, the LLM sentiment signal, backtesting, reporting, and paper trading.

Out of scope: real money, derivatives, order book or tick data, HFT, and multi-user accounts.

MVP functional requirements:

- Download and incrementally update OHLCV data, with gap and duplicate detection.
- One strategy interface and three baseline strategies, with parameters set in config.
- An engine with strict time ordering, fees, slippage, position tracking, and a trade log.
- A metrics report that always includes the buy-and-hold BTC comparison.
- A CLI that runs a backtest from a config file.

V1 additions: news collection, LLM scoring with logged model and prompt version, leak-free sentiment series, sentiment strategies, position sizing, walk-forward and parameter sensitivity checks, the dashboard, paper trading, and a fallback exchange.

Non-functional requirements:

- Reproducibility: the same config and data give the same result.
- Performance: a 3-year hourly backtest on 10 coins runs in under 60 seconds.
- Engine test coverage of at least 80%.
- Modularity: a new strategy is one new file.
- A README with an architecture diagram.
- API keys stay in environment variables only.

Constraints: one developer, near-zero budget, and data sources that must be accessible from the Philippines.

Key risks: lookahead bias, news timestamp leakage, overfitting, survivorship bias, short news history, and scope creep.

## 1-week build plan

- Day 1: Repo setup, check exchange access, CCXT downloader + Parquet storage, about 10 pairs.
- Day 2: Data quality checks and the strategy interface. Study returns and volatility.
- Day 3: Backtest engine (Ethan writes it, Claude reviews).
- Day 4: Engine tests (buy-and-hold must match a hand calculation), then the three baseline strategies.
- Day 5: Metrics report, BTC comparison, and CLI. MVP done.
- Day 6: News dataset, local LLM scoring, and leak-free aggregation.
- Day 7: Sentiment strategy, train/test split, README, and a findings write-up.

If anything slips, ship the MVP and move the AI module to week 2.

## Prototyping phase plan

Goal: answer the questions that could sink the project before building it properly. All code lives in prototypes/ and is never imported by the real codebase.

- P1, data spike (half a day): 2 years of hourly BTC and ETH from two exchanges. Pass if at least one exchange works from the Philippines with under 1% missing candles.
- P2, minimal backtest loop (1 day): a single-file MA crossover on BTC, checked against a spreadsheet. Pass if returns match within rounding, fees included.
- P3, sentiment spike (1 day): score 500 headlines from an existing dataset, re-score 50 to check consistency, hand-check 20, and plot daily sentiment against next-day returns. Pass if scores are consistent and plausible.
- P4, UI prototype (half a day): generate the dashboard in Claude Design. Pass if a backtest result can be explained from the design in under 30 seconds.

Rule: every prototype ends with a 3 to 5 line note covering what was tried, what was found, and what changes in the plan.

Exit criteria: a confirmed data source, a verified backtest method, a go or no-go on the sentiment signal, a chosen UI layout, and an SRS updated with the findings.
