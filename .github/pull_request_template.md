## What / why
<!-- one paragraph; link FR/NFR ids and decision-log entries -->

## Self-review checklist
- [ ] Conventional-commit title; one unit of work
- [ ] No secrets, keys, or real account data anywhere in the diff
- [ ] Default mode is still paper/testnet; no live path added
      (if one is: LIVE-SIGNOFF id from decision-log.md → `D-___`)
- [ ] Every new order path goes through RiskGate and writes the audit log
- [ ] Formulas cite `math.md` sections and match them
- [ ] Tests added/updated; `pytest`, `ruff`, `mypy`, `lint-imports` pass locally
- [ ] SDD / decision-log updated if architecture or math changed
- [ ] Core module touched (engine/risk/execution/reconcile)? → Ethan has reviewed
