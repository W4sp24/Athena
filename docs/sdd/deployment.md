# Deployment and live-capital readiness (Phase 2)

Status: **Draft for Ethan's review. Nothing in this document is decided.**

- Phase 2 does not start until Ethan approves it.
- The live order path is not built until Ethan gives an explicit go/no-go, recorded in `decision-log.md` with a `LIVE-SIGNOFF:` line.

## 1. Go-live gate criteria (proposed; Ethan sets the numbers)

**Every item must pass, with evidence linked, before a LIVE-SIGNOFF can be requested.**

| # | Gate | Proposed threshold | Evidence |
|---|---|---|---|
| G1 | Out-of-sample paper/testnet track record | ≥ **8–12 weeks** of continuous testnet running with the exact strategy + config to be deployed. Out of sample: the strategy's parameters were frozen before that period. | audit log, equity export, weekly notes |
| G2 | Track record vs expectation | Live-forward Sharpe/drawdown within the range the backtest's walk-forward distribution predicts. No unexplained divergence between paper fills and the backtest fill model. | analytics report |
| G3 | Engine correctness | `engine/` + `risk/` coverage ≥ 80%. P2 golden test and look-ahead property tests pass. | CI run |
| G4 | Reconciliation | 0 unexplained mismatches over the last **M = 14** consecutive days of testnet | recon reports |
| G5 | Kill switch under failure | Every trigger in `risk-model.md` §3 exercised by fault injection, with a trip that survives a restart | test report + a manual drill log |
| G6 | Secrets and keys | Live key is trade-only, withdrawals disabled, IP-whitelisted to the deployment host. Key held in env/secrets manager only. Gitleaks history scan clean. | screenshot of key permissions (no key values), scan output |
| G7 | Deployment security review | Host hardened (§3), dependencies pinned, `uv.lock` audited | checklist |
| G8 | Operational readiness | Heartbeat monitoring and alerting tested end to end. Written runbook for halt/flatten/reset. | runbook |
| G9 | Capital plan | Initial live capital is an amount Ethan can lose entirely. Limits in `risk.yaml` set for that amount. A scale-up rule is agreed in advance. | decision-log entry |
| G10 | Regulatory/tax | §4 questions answered by Ethan / a qualified advisor | Ethan's note |

**After sign-off**, the live path is implemented as follows:
- It's a separate `LiveBroker`, behind a `LIVE_TRADING_ENABLED` setting that defaults to `false`.
- It needs a second, runtime confirmation: an interactive CLI flag plus a matching env var.
- It runs with its own, smaller `risk.live.yaml`.
- Its PR must quote the LIVE-SIGNOFF ID.

## 2. Deployment options (no pick made; Ethan decides)

The paper/testnet runner needs to be up at every hourly bar close. Missing bars isn't dangerous, because the system fails closed. It does, however, hurt track-record quality, and in live trading it means unmanaged open positions.

| | A. Always-on home PC | B. Small VPS | C. Hybrid |
|---|---|---|---|
| What | The existing workstation runs the runner 24/7 | The runner runs on a ~1 vCPU / 1–2 GB Linux VPS in Singapore or Tokyo, near exchange endpoints | Research, backtests, and LLM scoring stay local (GPU). Only the paper/live runner goes on the VPS. |
| Cash cost | ₱0 extra, plus electricity for a desktop running 24/7 (can be significant) | ≈ US$4–7/month | ≈ US$4–7/month |
| Reliability | PH brownouts and ISP outages are the main risk. Needs a UPS, BIOS auto-power-on, and the runner as a Windows service with auto-restart. Windows updates can reboot the machine. | Datacenter power and network. Provider SLA usually ≥ 99.9%. | Best of both. The runner doesn't depend on the home connection. |
| Latency to exchange | Home ISP, variable | Low and stable | Low and stable |
| Security | Keys sit on a daily-use machine (browser, downloads). That's a larger attack surface. | Single-purpose box. SSH keys only, firewall, unattended upgrades. Keys on a remote host. | Same as B for the runner. Research machine holds no live keys. |
| Monitoring | Same need either way. An external heartbeat (e.g. a free dead-man's-switch service pinged each bar) alerts when the runner *stops*. Self-monitoring can't catch its own death. | same | same |
| IP whitelisting | Home IP may be dynamic, which makes exchange key whitelisting awkward | Static IP, easy to whitelist | Static IP for the runner |
| Ops burden | Lowest setup, highest babysitting | Moderate setup (Linux, systemd, backups of `state/`) | Highest setup, lowest ongoing risk |

**Honest take, for discussion only:**
- A is fine for the Phase 1 paper/testnet track record, since a missed bar costs nothing.
- For live capital, the static-IP whitelisting and uptime arguments favor B or C.
- The LLM scorer needs the GPU, so C is the natural fit if sentiment strategies go live.

**Backups:** `state/` (audit log, risk state) is the irreplaceable data. Back it up nightly off-host (encrypted), whatever option is picked.

## 3. Host hardening checklist (applies to whichever option)

- A dedicated OS user for the runner, with no admin rights.
- Secrets in env or the OS secret store, never in files under the repo.
- Firewall with no inbound ports except SSH (VPS). SSH is key-only; password login is disabled.
- Automatic security updates, with scheduled reboots outside bar-close windows.
- Time sync (NTP) verified. `CLOCK_SKEW` trips otherwise.
- Pinned dependencies (`uv.lock`). Upgrades happen only via PR with CI.

## 4. Philippines regulatory and tax questions: FLAGGED, NOT RESOLVED

These need a real look from Ethan and a qualified advisor (tax/legal). They're **not** engineering decisions. The list below is questions to ask, not answers.

1. **Tax treatment of gains (BIR).** How are crypto trading gains taxed for an individual (e.g. as ordinary income vs another category)? Which records does the BIR expect? Does frequent algorithmic trading change the classification (e.g. to "engaged in business")? The audit log should be able to produce whatever report is needed.
2. **Which exchanges are lawful to use from the PH.** The SEC has issued advisories against unregistered offshore platforms and has moved to restrict access to at least one major exchange. Is using a given offshore exchange permitted for an individual, and what are the risks (account freezes, access blocks)? This directly affects the "primary exchange" open decision and G6's IP whitelisting.
3. **Peso on/off-ramp.** Using a BSP-registered VASP for PHP deposits and withdrawals, and its AML/KYC implications for moving funds to and from a trading exchange.
4. **Multi-user (currently out of scope).** If CryptoLab ever manages or trades for anyone else, or shares signals commercially, it would likely touch BSP VASP rules, SEC rules on crypto-asset service providers and investment advice/solicitation, and AMLA obligations. That's a **stop-and-get-counsel** event, not a feature.
5. **Record retention.** How long must trade records be kept? The audit log retention policy should follow the answer.

## 5. Go/no-go record template

The decision-log entry that authorizes live trading must contain:

```
## D-0XX — Go-live authorization
Date: YYYY-MM-DD   Decided by: Ethan
Gate evidence: G1 … G10 (links)
Scope: exchange, symbols, strategy + version (git SHA), max capital, risk.live.yaml hash
Rollback: conditions under which live is turned off again
Status: Accepted
LIVE-SIGNOFF: D-0XX
```
