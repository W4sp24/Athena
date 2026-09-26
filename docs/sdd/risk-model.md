# Risk model

Status: **Draft v0.1, awaiting Ethan's sign-off.** Values marked **[DECIDE]** are proposed defaults.

## 1. Principles

1. **Limits live outside the strategy, and strategies can't override them** (FR-24).
   - Limits are loaded once, at runner start, from `configs/risk.yaml` into a frozen Pydantic model.
   - That model is passed only to `RiskGate`. Strategies never receive it, and import-linter forbids `strategies → risk/config`.
   - Changing a limit means editing the file and restarting the runner. The change is written to the audit log along with the file's hash.
2. **Fail closed** (NFR-07). If the risk state, the audit log, or the market data is unavailable or stale, the answer is *no new orders*.
3. **Loud, not silent.**
   - Every rejection is written to the audit log with a reason code.
   - Every kill-switch trip and every reconciliation mismatch raises an alert (§5).
4. **Paper by default** (FR-26).
   - Modes are `backtest`, `paper`, and `testnet`. There is no live mode (CI-enforced).
   - RiskGate runs in **all** modes, backtest included. Backtests then show the effect of the limits, and the code path under test is the one that will run live.

## 2. Position and order limits (`configs/risk.yaml`)

| Key | Meaning | Proposed default [DECIDE] |
|---|---|---|
| `allowed_symbols` | whitelist; anything else is rejected | the pairs in the data config |
| `max_position_pct_equity` | $\lvert q_i\rvert C_i / V$ after the fill, per symbol | 25% |
| `max_position_notional` | absolute per-symbol cap, in quote | 1,000 USDT (paper) |
| `max_gross_exposure_pct` | $\sum_i \lvert q_i \rvert C_i / V$ | 100% (no leverage) |
| `max_order_notional` | per order | 500 USDT |
| `min_order_notional` | dust floor (math.md §5.1) | 10 USDT |
| `max_orders_per_hour` | runaway-loop brake | 20 |
| `allow_short` | always `false` outside backtest | false |
| `stale_data_seconds` | the latest bar must be at most this old | 2 × bar length |

**Check order** inside `RiskGate.check(intent, portfolio)`. It stops at the first failure and returns `Verdict(approved: bool, reason: str, adjusted_qty: float | None)`:
1. kill switch armed?
2. mode allows the side?
3. symbol whitelisted?
4. data fresh?
5. order-rate limit
6. per-order notional
7. post-fill per-symbol % and notional
8. post-fill gross exposure

Limits never *enlarge* an order.

**Clipping vs rejecting [DECIDE].** Proposed:
- An order that breaches a per-symbol or gross exposure limit is **clipped** down to the limit. The verdict is `approved` with `adjusted_qty`, and it's logged as `clipped`.
- Every other failure is **rejected**.

## 3. Kill switch (FR-25)

**States:**
- `ARMED`: normal operation.
- `TRIPPED(reason, ts, detail)`: every new order is rejected.

**Persistence:** `state/risk_state.sqlite`. It's written *before* the trip is acted on.
- A restart does **not** re-arm the switch.
- If the state file can't be read, the switch is treated as `TRIPPED(STATE_UNREADABLE)`.

**Triggers:**

| Code | Condition | Proposed default [DECIDE] |
|---|---|---|
| `DAILY_LOSS` | $V_{now} / V_{00:00\,UTC} - 1 \le -$`max_daily_loss_pct`, marked to market with unrealized P&L included | 3% |
| `MAX_DRAWDOWN` | $V_{now} / M - 1 \le -$`max_drawdown_pct`, where $M$ is the equity high-water mark since the last manual reset | 15% |
| `RECON_MISMATCH` | reconciliation failed (§4) | — |
| `BROKER_ERRORS` | N consecutive broker errors or rejects | 5 |
| `STALE_DATA` | no fresh bar for `stale_data_seconds` × 3 | — |
| `CLOCK_SKEW` | local clock vs exchange server time > threshold | 5 s |
| `AUDIT_WRITE_FAILED` | an audit-log append failed. If we can't log an order, we don't trade. | — |
| `MANUAL` | operator `cryptolab risk halt --reason ...` | — |

**The daily anchor** $V_{00:00\,UTC}$ is recorded at the first mark after 00:00 UTC and persisted. The PH day (UTC+8) and the exchange day are both UTC-aligned here for consistency.

**While tripped:**
- No new orders.
- Open orders on the exchange get **cancelled** in testnet mode, and that's logged.
- Existing positions are **not** automatically flattened (proposed, D-010 [DECIDE]). Auto-liquidating during an outage or bad-data event can itself cause losses. Flattening is an explicit operator command: `cryptolab risk flatten`. It goes through RiskGate with a `reduce_only` exemption and is logged.

**Reset:** `cryptolab risk reset --reason "<text>"` only.
- It's written to the audit log with the reason.
- It's refused while the triggering condition still holds. For example, you can't reset `DAILY_LOSS` until the next UTC day, unless `--override-daily` is given, and that is logged as well.

**Tests (required before Phase 2):**
- each trigger, driven by simulated faults: API down, partial fills, stale data, clock skew, audit-disk full, corrupted state file
- persistence across a process restart
- reset refusal while the condition still holds

## 4. Reconciliation (FR-28)

**Schedule:**
- at runner start, before the first order
- then every 5 minutes [DECIDE]
- and immediately after any broker error

**What's compared** (local ledger means the positions and cash derived by replaying the audit log):

| Item | Local source | Broker source | Tolerance |
|---|---|---|---|
| per-asset balance | audit-log replay | `fetch_balance()` (testnet) / PaperBroker ledger | abs ≤ exchange min lot **and** rel ≤ 0.1% [DECIDE] |
| open orders | audit log (submitted, not filled/cancelled) | `fetch_open_orders()` | exact set match on order IDs |
| fills since last recon | audit-log fills | `fetch_my_trades(since)` | exact match on trade IDs, qty, price; fee within rounding |

**On mismatch:**
1. Trip `RECON_MISMATCH`.
2. Write `state/recon/<ts>.json` containing both sides and the diff.
3. Send an alert.

Nothing auto-corrects the local ledger. A human decides whether the audit log or the exchange is right.

**Paper mode** reconciles the PaperBroker's internal ledger against an independent replay of the audit log. The mechanism is exercised every day, even with no exchange involved.

## 5. Alerting

- A `Notifier` interface.
- **Default:** a structured ERROR log line plus a Windows desktop toast.
- **Optional:** a Telegram bot, which is free. Its token and chat ID come from env vars only.

Alerts fire on:
- kill-switch trips
- reconciliation mismatches
- a missed runner heartbeat (see `deployment.md`: external dead-man's switch)

## 6. Order audit log (FR-27)

- Append-only SQLite table (the application never issues `UPDATE`/`DELETE`, and a hash chain makes tampering detectable), plus a JSONL mirror.
- **One row per event:** `intent`, `verdict`, `submitted`, `fill`, `partial_fill`, `cancel`, `reject`, `recon`, `killswitch`, `config_loaded`.
- **Fields:**
  - `event_id`, `prev_hash`, `hash`, `ts_utc`, `mode`, `strategy`, `strategy_version` (git SHA)
  - `symbol`, `side`, `qty_requested`, `qty_filled`
  - `ref_price`, `fill_price`, `fee`, `fee_ccy`, `slippage_bps`
  - `bar_ts`, `decision_ts`
  - `signal_inputs` (JSON snapshot: the values the strategy used, including sentiment $S$ and $n$) and `signal_inputs_hash`
  - `verdict`, `reason`
  - `broker_order_id`, `raw_broker_response` (secrets stripped)
- **Replay:** `cryptolab audit replay` rebuilds the positions, cash, and equity curve from the log alone. It's used by reconciliation and by tests.
