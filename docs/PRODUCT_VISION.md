# PRISM — The Reality Layer for 24/7 Tokenized Stocks

Product narrative from the project owner. Engineering rules live in [../AGENTS.md](../AGENTS.md); where the two differ, AGENTS.md governs implementation.

## One-line description

**PRISM is a reality-adjusted shadow risk engine for Bitget traders using tokenized stocks inside a Unified Trading Account. It determines how much an off-hours rToken price deserves to be trusted, then shows what that price could do to the trader's actual account when the collateral pricing regime catches up.**

## The core problem

A U.S. equity can stop normal price discovery when the underlying market closes while its tokenized representation continues trading. At the same time, Bitget allows eligible rTokens to contribute collateral inside the Unified Trading Account.

This creates multiple versions of "the price" at once:

- the underlying U.S. stock's traditional-market reference;
- the rToken's live Bitget market;
- Bitget's collateral engine, which can still be using a frozen stock-token index during a closure;
- crypto derivatives inside the same account that continue trading and generating PnL.

Those values eventually converge when the relevant traditional-market pricing regime resumes. A trader can spend a weekend looking at an account that is healthy according to the exchange's officially recognized collateral marks while the live token market already implies that some of that collateral may be worth substantially less when the reference index starts updating again.

Bitget is not making a mistake — the freeze is part of its documented risk framework. The trader needs to answer a different question:

> **What might my account look like after the next pricing-regime transition?**

Bitget's official risk engine answers: *What is my account state now under the prices Bitget currently recognizes?*

PRISM answers: *What account states emerge if the currently frozen collateral is revalued using the evidence available from the still-trading markets?*

## The thesis

> **The market price being traded and the collateral price being recognized can temporarily describe different market states. The usefulness of the live market price depends on the quality of the market producing it. Therefore off-hours market quality must be evaluated before that price is propagated into portfolio risk.**

PRISM therefore cannot simply replace Bitget's frozen index with the live weekend rToken price. Reality Check alone produces market analysis without account meaning; THAW alone assumes the live price is informative. PRISM connects them:

**Price evidence → market confidence → reopen stress band → collateral revaluation → account stress → decision → repair.**

## Target user

A crypto-native Bitget trader using a Unified Trading Account who holds eligible tokenized stocks as collateral while maintaining leveraged crypto or other derivative exposure across weekends, U.S. holidays or other stock-market closures. A typical account: BTC and ETH perps, rNVDA, rCOIN, rMSTR, rTSLA, USDT and other collateral. The danger is that interconnected assets' values and collateral contributions can deteriorate at the same time.

## Product philosophy

PRISM is not a trading bot. It does not autonomously enter positions, promise exact Monday prices, claim its shadow account is Bitget's liquidation engine, or generate probabilities from an LLM. It is read-only for the core hackathon product. It reveals hidden portfolio state before the trader acts; the trader makes the decision.

## Three layers

- **Market Reality** — *Can this off-hours price be trusted?* A structured assessment of the current price-discovery environment, not "real/fake."
- **Account Reality** — *What happens to my portfolio if that information is reflected in collateral valuation?* A Shadow Account built from the actual Bitget account, revalued across reopen scenarios.
- **Decision Reality** — *Should I make the trade I'm considering, and what could I change if the result is too fragile?* Pre-trade stress and feasible repairs.

These appear as one workflow, not three products.

## Market Reality Engine

Answers: **How much information should this observed price contribute to our shadow valuation?** Five evidence classes:

- **Liquidity integrity** — spread, top-of-book and deeper liquidity (where accessible), volume, estimated impact, spread expansion, changes vs comparable closures.
- **Reference consensus** — agreement with other correctly normalized references to the same exposure (never naive comparisons across share ratios or wrappers).
- **Information support** — Qwen extracts structured event evidence (e.g. `company_specific = true`, `direction = negative`, `materiality = high`, `source_quality = primary`). The quantitative engine decides how that affects the stress model.
- **Cross-asset coherence** — agreement with economically related markets (semis for NVDA, crypto for COIN/MSTR, Nasdaq for broad tech). Coherence, not causation.
- **Microstructure stress** — collapsing depth, imbalance, jumps, wide spreads, temporary dislocations.

### Market states

| State | Meaning |
|---|---|
| **DISCOVERY** | The off-hours market appears to be incorporating information in a reasonably liquid and corroborated way. |
| **DRIFT** | The move has weak informational support and may primarily reflect off-hours market behaviour. |
| **OVERSHOOT** | Direction is supported, but the magnitude appears aggressive relative to corroborating evidence. |
| **THIN** | Market quality is too weak for the observed price to be treated as a strong valuation signal. |
| **CONFLICT** | Credible references materially disagree. |

Labels are explanatory; the model uses the underlying continuous evidence.

### Reality Envelope

Instead of a "Reality Score: 78%", PRISM produces an envelope (illustrative):

```text
rNVDA                                  -6.2%
Market state                       DISCOVERY
Market quality                       Strong
Reference agreement                  Strong
Event support                        Strong
Crypto contamination                   Low
Microstructure stress             Moderate
Observed Bitget weekend price        $164.20
Shadow stress centre                 $165.10
Stress band                    $158.80–$170.40
```

The important output is the range of plausible valuation states propagated into the account model.

### Evidence modes

- **OBSERVED** — a live Bitget weekend market provides a direct off-hours signal.
- **INFERRED** — no suitable live market; valuation derives from factors, related markets, events and historical relationships.
- **HYBRID** — a live market combined with other references and factor evidence.

## Dual Reality Ledger

Two account states side by side. **Bitget Official** is read directly from Bitget and never modified. **PRISM Shadow** is the counterfactual account with closure-sensitive assets revalued per the Reality Envelope, always labelled as a PRISM simulation (illustrative):

```text
                         BITGET NOW       PRISM SHADOW
Effective equity           $43,420            $37,860
Margin ratio                 31.2%              46.8%
rToken collateral          $18,950            $14,640
Maintenance requirement    $9,110             $9,370
Status                       NORMAL            EXPOSED
```

## Reconciliation comes before simulation

Before producing shadow analysis, PRISM attempts to reproduce the current Bitget account state from current prices and configuration. Within tolerance → **RECONCILED**; otherwise **APPROXIMATION MODE** (engineering spec: `OBSERVED_BASELINE`), with shadow calculations disabled or visibly downgraded. This is one of PRISM's most important credibility mechanisms.

## Metrics

- **Latent Collateral Gap (LCG)** — recognized rToken collateral minus collateral under a selected shadow scenario. Not a claim that Bitget's figure is wrong; it describes how much collateral contribution could disappear if marks moved toward the shadow.
- **Shadow Margin State** — the account recalculated across deterministic scenarios (mild / central / severe). Early versions say "stress bands" or "model percentiles", not calibrated probabilities.
- **Thaw-at-Risk (TaR)** — how far the account risk metric moves from the current recognized state to a selected severe shadow state, e.g. `+47.2pp`.

## Thaw Frontier and the Blind Zone

The hero visualization: account state across a grid of crypto shocks × rToken reopen shocks, each cell produced by rerunning the account model (never an LLM judgment). Selecting a cell reveals effective equity, collateral contribution, derivative PnL, maintenance requirements and account risk.

The **Blind Zone** is the region where the current account still appears acceptable but the shadow account breaches a selected risk boundary — market states not yet reflected in frozen collateral marks.

## Wrong-Way Collateral

Collateral is not automatically diversification. A BTC long backed by rCOIN and rMSTR means one crypto sell-off hurts both the leveraged position and the assets supporting it. The Pre-Trade Gate surfaces this explicitly with the share of effective collateral exposed and the primary contributors.

## Pre-Trade Gate

The demo centre. The user asks: **"Can I add another $10,000 BTC long tonight using my current rToken collateral?"** PRISM interprets the trade, reads the account, identifies closure-sensitive collateral, runs the Reality Engine, builds shadow valuations, applies the proposed position, recalculates the Thaw Frontier, checks wrong-way collateral and identifies Blind-Zone entry scenarios — then gives an evidence-backed answer.

## Repair Solver and feasibility

PRISM solves for feasible interventions — add stable collateral, reduce a derivative, reduce closure-sensitive collateral, hedge, or mixed — against objectives such as minimum cash, minimum exposure reduction, minimum execution cost or maximum safety.

A repair is only useful if it can be executed. The Market Reality Engine feeds back in: if selling rNVDA into a THIN weekend book would cost heavy impact, PRISM may prefer reducing the BTC perp. That closes the loop: **market reality → account reality → execution reality.**

## Overnight Replay, Autopsy and calibration

- **Overnight Replay** reconstructs how a closure-period move developed chronologically and connects it to the account's current shadow state.
- **Autopsy** locks every prediction before the transition and compares it to the realized reopen, never hiding losing forecasts.
- **Calibration Board** tracks directional performance, reopen error, band coverage, error by state and evidence mode, reconciliation error and Blind-Zone detections — against baselines (last frozen reference; raw live rToken; live rToken + fixed band). If PRISM doesn't beat them, the team should know.

## Probability discipline

Given an exactly specified market state, the deterministic engine either breaches or it doesn't — there is no "74% probability." Probability belongs to the distribution of market states. Until enough forward observations exist, PRISM uses stress scenarios, model percentiles, historical bands and distances to risk boundaries.

## Qwen's role

Qwen handles natural-language interaction, scenario interpretation, event extraction, evidence classification, thesis challenge and explanation. It does not calculate margin, collateral, PnL, correlation, liquidation state, price impact, intervals or repairs. E.g. "What happens if the AI trade unwinds this weekend?" → Qwen produces a structured scenario → the quantitative system computes consequences → Qwen explains.

## Evidence Lock

Every important conclusion has a **"Why does PRISM think this?"** view listing market evidence, event evidence, account evidence, model versions and data cutoff.

## Data and technical architecture

From Bitget: rToken instruments, weekend-tradability, market state, tickers/candles, depth where permitted, account state, effective equity, margin metrics, positions, per-asset balances, collateral configuration and other UTA risk information. Plus selected stock, sector, crypto and event references. Raw observations are stored so every replay is reproducible. No visible market number may come from a fake hard-coded array.

Stack: Next.js/React mission-control frontend; Python FastAPI backend with Market Reality Engine, UTA Reconciliation Engine, Shadow Risk Engine, Scenario Engine, Repair Optimizer; Qwen for parsing/extraction/explanation; PostgreSQL/Timescale-style storage; optional Redis.

## Mission Control interface

Risk first, not conversation. Left: rToken watchlist (state, evidence mode, off-hours move, market quality, LCG contribution). Centre: price-provenance chart (live rToken vs frozen reference, events, regime transitions). Right: Dual Reality Ledger (Bitget state, shadow state, LCG, TaR, wrong-way warnings). Lower centre: Thaw Frontier. Bottom: natural-language bar.

## Four hackathon surfaces

| Surface | Purpose |
|---|---|
| **Dual Reality Ledger** | Show the official Bitget account beside the PRISM shadow account. |
| **Thaw Frontier** | Show the Blind Zone across joint crypto/rToken shocks. |
| **Pre-Trade Gate** | Analyse a proposed trade against the shadow account. |
| **Repair Solver** | Find the cheapest feasible route back to an acceptable risk state. |

## Modes

- **Live Mode** — real current Bitget market/account information.
- **Replay Mode** — a historical closure window reconstructed from recorded real data.
- **Judge Mode** — a clearly labelled hypothetical account against a real historical market window.

PRISM never pretends a hypothetical account is real or fakes a historical market move.

## Canonical judge demo

A seemingly safe account → much of its collateral is rTokens on a frozen index → one rToken moved during the closure with credible support → **Show Shadow Account** → **Thaw Frontier** reveals a Blind Zone → "Can I add another $10,000 BTC long?" pushes the severe shadow across the boundary, with rCOIN/rMSTR wrong-way exposure → **Repair** finds that selling the thin-book rToken is unattractive and reducing BTC is better per unit cost → a prior locked run is shown beside the realized reopen.

> **The market never closes. Your risk model shouldn't either.**

## Differentiation

Not a research terminal, signal generator, generic stress tester, liquidity monitor or portfolio chatbot. The innovation is **reality-adjusted counterfactual margining**: market-quality inference → off-hours collateral revaluation → actual unified-account propagation → cross-asset dependency risk → feasible repair. The whole chain is the moat.

## Non-goals

No generic TradingView clone, AI bullish/bearish percentages, LLM confidence scores, fictional debating agents, exact Monday prices, blanket "fade the weekend" advice, claims that rTokens equal shares, claims to replace Bitget's liquidation system, autonomous trading, hidden uncertainty, or unlabelled fake data.

## What must be proven first

1. **UTA reconciliation** — reproduce Bitget's reported account state closely enough to anchor the shadow.
2. **Pricing-regime transition measurement** — empirically record when the rToken collateral index leaves the frozen state; do not assume 9:30 ET.

Until both are verified, those parts remain hypotheses.

## Positioning

Avoid "AI trading dashboard" and "AI-powered risk management." Use:

> **PRISM is the reality layer between 24/7 tokenized-stock markets and Bitget's unified risk engine.**
>
> When traditional markets close, the price being traded and the price being recognized as collateral can temporarily represent different market states. PRISM measures how much the live price deserves to be trusted, translates that evidence into shadow valuation scenarios, and propagates those scenarios through the trader's real account before the pricing regimes converge.
>
> **It doesn't predict the market. It shows which version of your account survives it.**

**Bitget tells you the account you have now. PRISM shows you the accounts you may be carrying into the reopen.**
