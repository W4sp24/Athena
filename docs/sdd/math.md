# Math specification

Status: **Draft v0.1, awaiting Ethan's sign-off** (D-001 requires sign-off before engine code is written).
Every formula implemented in code cites its section here (`# math.md §x.y`). Items marked **[DECIDE]** need Ethan's confirmation.

## 1. Notation and timing

| Symbol | Meaning |
|---|---|
| $t = 0,\dots,T$ | bar index, strictly increasing in time |
| $\tau_t$ | bar **open** time (UTC). Bars are identified by open time. |
| $\Delta$ | bar length (1h primary, 1d secondary) |
| $O_t, H_t, L_t, C_t, V^{vol}_t$ | open, high, low, close, volume of bar $t$ |
| $d_t = \tau_t + \Delta$ | **decision time** for bar $t$: the instant bar $t$ closes |
| $V_t$ | portfolio equity marked at $C_t$ |
| $P$ | periods per year: $P = 365 \times 24 = 8760$ (hourly), $P = 365$ (daily). Crypto trades 24/7, so there's no 252-day convention. |
| $N$ | number of return periods in the sample |

**Timing rule (FR-13):**
- At decision time $d_t$ a strategy sees bars $0..t$ only.
- Orders it causes are filled at $O_{t+1}$, the open of the next bar (the SRS assumption).
- Nothing from bar $t+1$ or later is visible when the decision is made.

## 2. Returns

**2.1 Per-period simple return of equity:**
$$r_t = \frac{V_t}{V_{t-1}} - 1, \quad t = 1..T$$

**2.2 Log return** (used for aggregation and statistical tests only, never for reporting P&L):
$$\ell_t = \ln(1 + r_t)$$

**2.3 Total return:**
$$R = \frac{V_T}{V_0} - 1$$
$V_0$ is initial capital, before any fees.

**2.4 Annualized return (CAGR):**
$$R_{ann} = \left(\frac{V_T}{V_0}\right)^{P/N} - 1$$

## 3. Risk and risk-adjusted metrics

**3.1 Volatility (annualized):**
$$\sigma_{ann} = s(r) \sqrt{P}, \qquad s(r) = \sqrt{\tfrac{1}{N-1}\textstyle\sum_t (r_t - \bar r)^2}$$
This uses the sample std (ddof = 1) of simple returns, scaled by the square-root-of-time rule. Autocorrelation is ignored, which is a known approximation.

**3.2 Sharpe ratio (annualized):**
$$\text{Sharpe} = \frac{\overline{r - r_f^{(p)}}}{s(r)} \sqrt{P}, \qquad r_f^{(p)} = (1 + r_f)^{1/P} - 1$$
- $r_f$ = annual risk-free rate. **Default 0 [DECIDE]:** crypto has no natural risk-free rate. The default can be changed in config, and the value used is printed in every report.
- The mean is arithmetic.

**3.3 Sortino ratio (annualized):**
$$\text{Sortino} = \frac{\overline{r - \theta}}{DD} \sqrt{P}, \qquad DD = \sqrt{\tfrac{1}{N}\textstyle\sum_{t} \min(0,\ r_t - \theta)^2}$$
- $\theta = r_f^{(p)}$ is the minimum acceptable return.
- The downside deviation divides by **all $N$ periods**, not just the negative ones (the Sortino & Price convention). This keeps the ratio from exploding when losses are rare.

**3.4 Drawdown.** Define:
- high-water mark $M_t = \max_{s \le t} V_s$
- drawdown $D_t = V_t / M_t - 1 \le 0$

Then:
- **Max drawdown** $\text{MDD} = \min_t D_t$, reported as a negative percentage.
- **Max drawdown duration** is the longest run of consecutive bars with $D_t < 0$.

**3.5 Win rate.**
- Trades are paired into round trips per symbol **FIFO**.
- A round trip's P&L is net of both legs' fees and slippage.
- Win rate = (number of round trips with net P&L > 0) / (number of closed round trips).
- Open positions at the end of the sample are excluded, and their count is reported separately.

**3.6 Turnover (annualized):**
$$\text{Turnover} = \frac{\sum_{fills} |q \cdot p|}{\bar V} \cdot \frac{P}{N}$$
$\bar V$ is the mean equity over the sample. A value of 1.0 means trading the full book once per year.

**3.7 Benchmark (FR-18).**
- BTC buy-and-hold uses the same initial capital and the same period.
- It buys at $O_1$ (the first possible fill) under the same fee and slippage model (§5), and holds to $T$.
- All metrics above are computed identically for it, and every report shows it side by side.

## 4. Portfolio accounting

Notation:
- For each symbol $i$: quantity $q_i$ (base units) and cash $c$ (quote currency, USDT).
- Positions are marked at the bar close.

$$V_t = c_t + \sum_i q_{i,t}\, C_{i,t}$$

A bar with no data for symbol $i$ carries forward the last close. The data-quality gate (FR-03) keeps this rare, and the report counts the occurrences.

## 5. Fill, fee and slippage model

**5.1 Targets to orders.**
- The strategy returns target weights $w^*_i$.
- The engine converts them to target quantities using the information available at $d_t$:
  $$q^*_i = \frac{w^*_i\, V_t}{C_{i,t}}, \qquad \Delta q_i = q^*_i - q_{i,t}$$
- Orders with $|\Delta q_i| \cdot C_{i,t} <$ `min_order_notional` are skipped. This is a config value, and it keeps dust rebalances out.
- Each resulting `OrderIntent` goes through RiskGate (`risk-model.md`) before it can fill.

**5.2 Fill price (market order at the next open).** With slippage fraction $s = \text{slippage\_bps}/10^4$:
$$p^{buy} = O_{t+1}(1 + s), \qquad p^{sell} = O_{t+1}(1 - s)$$
- Slippage always works against us.
- Default model: constant bps. **[DECIDE]** A later option is $s_t = k \cdot (H_t - L_t)/C_t$, a fraction of the bar's range.

**5.3 Fee.** With taker fee rate $f = \text{fee\_bps}/10^4$, since all simulated orders are market/taker:
$$\text{fee} = f \cdot |\Delta q| \cdot p$$
Fees are charged in the quote currency.

**5.4 Cash update:**
$$\text{buy: } c \mathrel{-}= \Delta q\, p + \text{fee}, \qquad \text{sell: } c \mathrel{+}= |\Delta q|\, p - \text{fee}$$

**5.5 Affordability (long-only, no leverage).**
- If a buy would make $c < 0$, scale it down to $\Delta q = c / (p (1 + f))$.
- Buys are processed after sells within a bar, so cash from sells is available.
- A scaled order is logged as `scaled_for_cash`.

**5.6 Shorts [D-011].**
- Negative weights are allowed **only in backtest**, as simulated shorts: $q_i < 0$, with the sale proceeds credited to cash.
- There is no borrow fee in v1. That's a known optimism, stated in every report that uses shorts.
- Spot accounts can't short, so RiskGate rejects any short in paper and testnet mode.

**5.7 Edge cases.**
- Signals at the final bar $T$ have no next open. They're dropped and logged.
- If bar $t+1$ is missing for a symbol, the order expires unfilled and is logged. It's never filled at a stale or future price.

## 6. Reproducibility (NFR-01)

- The engine is deterministic: there's no randomness in fills.
- Any randomness elsewhere (bootstrap, sensitivity sampling) uses a seed from config, and the seed is written into the report.
- Every result records:
  - the config hash
  - the data snapshot range and row counts
  - the code version (git SHA)

## 7. Sentiment aggregation, leak-free (FR-07)

**7.1 Inputs.** Each headline $h$ has:
- `published_at` $p_h$ (UTC)
- `first_seen_at` $a_h$: when our collector first stored it
- sentiment $s_h \in [-1, 1]$
- relevance $\rho_{h,c} \in [0, 1]$ to coin $c$
- the model name and prompt version (FR-08)

**7.2 Availability time.** A headline counts as known at:
$$k_h = \max(p_h,\ a_h) + \delta$$
- $\delta$ is a delay buffer. **Default 15 min [DECIDE].**
- Historical datasets have no $a_h$, so there $k_h = p_h + \delta$. Datasets can backdate or revise timestamps, so this is flagged as a residual leakage risk in the research note.

**7.3 Window.** The SRS says to aggregate only news published before the bar **opens**. The bar in question is the one whose open we trade at: bar $t+1$. That bar opens exactly at decision time, since $\tau_{t+1} = d_t$. So the sentiment used for the decision at $d_t$ is:
$$\mathcal{H}_{c,t} = \{\, h : d_t - W \le k_h < d_t,\ \rho_{h,c} > 0 \,\}$$
- $W$ is the lookback window. **Default 24h [DECIDE].**
- The inequality $k_h < d_t$ is **strict**.

**7.4 Aggregate:** an exponentially decayed, relevance-weighted mean.
$$S_{c,t} = \frac{\sum_{h \in \mathcal H} w_h\, \rho_{h,c}\, s_h}{\sum_{h \in \mathcal H} w_h\, \rho_{h,c}}, \qquad w_h = 2^{-(d_t - k_h)/\lambda}$$
- $\lambda$ is the half-life. **Default 6h [DECIDE].**
- If $\mathcal H$ is empty, $S_{c,t}$ is *missing* (not 0), and the count $n_{c,t} = |\mathcal H|$ is exposed alongside it. Strategies must handle a missing value explicitly.

**7.5 Leakage test (required).**
- A property test inserts headlines with $k_h \ge d_t$.
- It asserts that $S_{c,t}$ and every strategy decision at $d_t$ are unchanged.
- A second property test perturbs all bars after $t$ and asserts the same.

## 8. P2 validation (golden test)

The P2 prototype checked a single-file MA crossover on BTC against a spreadsheet, and passed when returns matched within rounding, fees included.

- The engine must reproduce that spreadsheet's equity series within an absolute tolerance of $10^{-8} \times V_0$ per bar, and match its total return to 6 decimal places.
- **Until Ethan provides the P2 spreadsheet**, the golden fixture is a hand-computed 10-bar example written into `tests/golden/` with every intermediate value shown. It gets replaced, not supplemented, when the real spreadsheet arrives.
