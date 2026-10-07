# PRISM — Engineering Master Specification

- **Product:** PRISM
- **Positioning:** The Reality Layer for 24/7 Tokenized Stocks
- **Core technical idea:** Reality-adjusted counterfactual margining
- **Primary product mode:** Read-only AI research/risk workbench
- **Preferred competition positioning:** AI Trading Desk → Open Theme / Portfolio-Aware Research Workbench
- **Primary chain of reasoning:** Market Reality → Account Reality → Decision Reality
- **Owner:** Product decisions belong to the project owner. Coding agents implement, verify and challenge assumptions; they do not silently redefine the product.

The product narrative lives in [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md).

## 0. Purpose of this document

This file is the single source of truth for building PRISM.

Every coding agent must read this document before making architectural or product decisions.

PRISM is not a generic AI trading dashboard.

PRISM exists because Bitget can temporarily have two economically relevant representations of an rToken:

1. the collateral/index value currently recognized by the Unified Trading Account;
2. the price at which the rToken or related markets are discovering information while the underlying U.S. market is closed.

Bitget explicitly documents that eligible stock-token collateral uses an index price multiplied by a collateral ratio and that, during weekends and U.S. market closures, the stock-token index is frozen at the most recent extended-session close. Futures risk continues to update, and the eventual stock-market reopening can rapidly change effective collateral value.

PRISM answers:

> How much evidence should I place on the off-hours price, and what happens to my actual Bitget account if the next recognized stock-token mark moves toward that evidence?

Everything built must support that question.

## 1. Non-negotiable operating rules

### 1.1 No fake data

Never display invented prices, account balances, spreads, collateral ratios, historical outcomes, probabilities or model-performance statistics as real.

Every visible number must contain internal provenance:

```
source
source_timestamp
retrieved_at
data_mode
is_live
is_hypothetical
is_replay
```

Hypothetical accounts are allowed only when clearly marked: `HYPOTHETICAL ACCOUNT`. Historical market data used with them must remain real.

Synthetic market scenarios are allowed only when clearly marked: `STRESS SCENARIO`.

Never merge synthetic numbers into live market data without visibly distinguishing them.

### 1.2 Bitget is authoritative for the current account

PRISM does not claim to replace Bitget's official risk engine.

The following are authoritative when returned by Bitget:

```
effective equity
current margin ratio
maintenance margin
initial margin
balances
positions
mark prices
current recognized account state
```

Bitget's private UTA account WebSocket currently exposes fields including `totalEquity`, `effEquity`, `mmr`, `imr`, `mgnRatio`, `unrealisedPnL` and per-coin USD values.

PRISM generates:

```
shadow equity
shadow margin state
stress scenarios
latent collateral gap
thaw frontier
repair alternatives
```

Those are explicitly PRISM calculations.

### 1.3 No LLM arithmetic

Qwen or any other LLM must never calculate:

```
PnL
collateral values
margin ratios
correlations
VWAP
slippage
stress percentiles
maintenance margin
position exposure
price intervals
repair amounts
```

LLMs may interpret, classify, extract, explain and translate natural language into structured tool requests.

All numerical output comes from deterministic code.

### 1.4 No unearned probability language

V1 must not casually display "34% liquidation probability." The available Bitget rToken weekend history is limited.

V1 uses:

```
deterministic scenarios
historical ranges
stress percentiles
model bands
distance to threshold
```

Probability language is enabled only after sufficient forward calibration demonstrates that the intervals are statistically meaningful.

### 1.5 No silent assumptions

Every unknown exchange behaviour must become `UNVERIFIED_ASSUMPTION` until confirmed through:

```
official documentation
actual API response
controlled account experiment
or direct Bitget clarification
```

The application must degrade rather than invent.

## 2. The four surfaces we are actually building

Do not build 15 equal product modules. The hackathon-quality product contains four primary surfaces.

**Surface A — Dual Reality Ledger.** Shows `BITGET NOW` versus `PRISM SHADOW`, including closure-sensitive collateral.

**Surface B — Thaw Frontier.** Interactive two-dimensional stress surface showing the account across combinations of crypto shocks and rToken/reopen shocks. The region that is acceptable under the current state but breaches a chosen shadow-risk boundary is the Blind Zone.

**Surface C — Pre-Trade Gate.** The user proposes a trade in natural language, e.g. *"Can I add another $10,000 BTC long tonight using my current rToken collateral?"* PRISM calculates how the proposed trade changes both current and shadow risk.

**Surface D — Repair Solver.** If the resulting account is too fragile, PRISM calculates the cheapest feasible alternatives.

Everything else serves those four surfaces.

## 3. Supporting capabilities

These are not equal top-level products. They support the four primary surfaces:

```
Reality Engine
UTA reconciliation
Price provenance
Wrong-Way Collateral detection
Historical Replay
Autopsy
Evidence Lock
Qwen Copilot
Repair Feasibility
```

Do not create separate giant navigation pages for every term.

## 4. Verified Bitget primitives

The implementation may depend on the following because they are currently documented.

**Reality / rToken metadata.** Use `GET /api/v3/reality/market/stock-info`. The response includes `symbol`, `code`, `name`, `tradingPeriod`, `weekendTradable`. `weekendTradable` is explicitly documented as `yes` or `no`.

Use `GET /api/v3/reality/market/states` for U.S. session information. Current documented sessions include `pre_market`, `regular`, `after_hours`, `overnight`.

Use `GET /api/v3/reality/market/calendar` for weekends, holidays and special closures.

**Reality market data.** Use ordinary Bitget instruments/ticker/candle APIs for rTokens where supported. Bitget's Reality guide confirms instruments, tickers and candlesticks are available through the existing market-data infrastructure. rToken candles support `1m 5m 15m 1H 4H 1D`. Volume/turnover before July 9, 2026 may be absent and must not be imputed as zero.

**Full Reality depth.** The dedicated endpoint is `GET /api/v3/account/reality-orderbook`. It returns up to 40 raw bid/ask levels. It requires API authentication + Bitget whitelist/BD approval. Do not assume access exists.

Recent platform fills: `GET /api/v3/account/reality-fills` also require whitelist access and cover up to the last three months.

PRISM must function without these endpoints. If unavailable: `liquidity_mode = LIMITED` and any depth-dependent analysis is disabled or downgraded.

## 5. Bitget account data

Primary live account source: UTA private WebSocket, `topic = account`.

Capture:

```
totalEquity
effEquity
mmr
imr
mgnRatio
positionMgnRatio
unrealisedPnL

coin[].coin
coin[].balance
coin[].balanceOriginal
coin[].equity
coin[].usdValue
coin[].available
coin[].borrow
coin[].debts
```

Bitget added `balanceOriginal` to the account-assets API in September 2026, so do not build around an older frozen schema.

Use REST snapshots to recover after WebSocket reconnects: `GET /api/v3/account/assets`.

## 6. Position data

Use Bitget's UTA position API/WebSocket. Capture at minimum:

```
symbol
category
position side
size
available
frozen
average entry price
leverage
margin mode
mark price
unrealised PnL
liquidation price
maintenance margin rate
margin rate
fees
funding
```

The position WebSocket currently exposes fields including `leverage`, `unrealisedPnl`, `liqPrice`, `mmr`, `marginRate` and `markPrice`.

Persist every received snapshot with timestamp.

## 7. Collateral configuration

Bitget added these UTA APIs on June 25, 2026:

```
GET  /api/v3/account/collateral-type
POST /api/v3/account/set-collateral-type
GET  /api/v3/account/custom-collateral-coins
POST /api/v3/account/pre-set-leverage
```

PRISM is read-only. Do not call `set-collateral-type` from the application. Use the read endpoints only.

Important: do not assume `custom-collateral-coins` returns every collateral-ratio tier we need. Inspect the actual response.

If tiered collateral-ratio data are unavailable through an API, support a versioned configuration source with:

```
coin
tier_lower
tier_upper
collateral_ratio
effective_from
source
retrieved_at
```

No ratio may be hard-coded without provenance. Bitget's published rToken collateral schedules are tiered by notional and can change over time.

## 8. Read-only Bitget integration

Use the Bitget Agent Hub in read-only mode wherever useful. Bitget officially supports `--read-only`, which removes write operations at startup.

Agent Hub can provide market, account and position access, while the separate stock-data MCP provides U.S. equity data and the Signal tools provide crypto/macro/news intelligence.

PRISM must not require trading permissions. Preferred principle:

```
minimum permissions
+
no withdrawal permission
+
no execution requirement
```

## 9. External/Bitget research inputs

**bitget-mcp-server** for: US stock quotes, historical prices, company information, earnings information, financial statements, analyst data, ETF information, news, sentiment. Bitget describes this as a separate read-only service from Signal.

**bitget-signal** for crypto-side context such as: macro, crypto market intelligence, sentiment, news, technical context.

Do not call an external source merely because we can. Each source must have a defined role.

## 10. Repository structure

Use a monorepo.

```
prism/
│
├── AGENTS.md
├── README.md
├── .env.example
├── docker-compose.yml
│
├── apps/
│   └── web/
│       ├── app/
│       ├── components/
│       ├── features/
│       │   ├── ledger/
│       │   ├── frontier/
│       │   ├── pretrade/
│       │   ├── repair/
│       │   ├── reality/
│       │   └── replay/
│       └── lib/
│
├── services/
│   └── api/
│       ├── prism/
│       │   ├── connectors/
│       │   │   ├── bitget/
│       │   │   ├── stock_mcp/
│       │   │   ├── signal/
│       │   │   └── qwen/
│       │   │
│       │   ├── reality/
│       │   ├── reconciliation/
│       │   ├── shadow/
│       │   ├── scenarios/
│       │   ├── frontier/
│       │   ├── wrong_way/
│       │   ├── repairs/
│       │   ├── replay/
│       │   ├── provenance/
│       │   └── api/
│       │
│       ├── tests/
│       └── pyproject.toml
│
├── packages/
│   ├── schemas/
│   └── ui/
│
├── data/
│   ├── fixtures/
│   ├── replay/
│   └── research/
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BITGET_VERIFICATION.md
│   ├── MATH.md
│   ├── DATA_PROVENANCE.md
│   ├── MODEL_CARD.md
│   ├── DEMO.md
│   ├── VALIDATION.md
│   └── OPEN_QUESTIONS.md
│
└── scripts/
    ├── verify_bitget.py
    ├── capture_weekend.py
    ├── reconcile_account.py
    └── build_replay.py
```

## 11. Technology choices

**Frontend:** Next.js, TypeScript, React, Tailwind or equivalent utility CSS, accessible charting library, WebSocket client.

**Backend:** Python 3.12+, FastAPI, Pydantic, NumPy, pandas, SciPy, scikit-learn, CVXPY only if repair optimization genuinely requires it.

**Persistence:** PostgreSQL, TimescaleDB extension if convenient, Redis optional.

Avoid adding infrastructure merely to look sophisticated.

## 12. Canonical domain models

Implement typed models.

**MarketObservation:** `id, symbol, underlying, timestamp, source, price, bid, ask, volume, turnover, session, is_weekend, is_market_closed, raw_payload_hash`

**RealityAsset:** `symbol, underlying, weekend_tradable, trading_periods, collateral_eligible, evidence_mode, current_reference_price, reference_timestamp, live_rtoken_price`

**AccountSnapshot:** `timestamp, total_equity, effective_equity, maintenance_margin, initial_margin, margin_ratio, unrealised_pnl, assets[], positions[], source_hash`

**CollateralAsset:** `coin, quantity, gross_usd_value, collateral_enabled, collateral_tiers[], recognized_value, price_source, price_timestamp`

**StressScenario:** `id, created_at, source, crypto_shocks, rtoken_shocks, factor_shocks, is_hypothetical, description`

**ShadowResult:** `scenario_id, shadow_effective_equity, shadow_maintenance_margin, shadow_margin_ratio, latent_collateral_gap, threshold_state, component_deltas[], warnings[]`

**ProposedTrade:** `instrument, direction, notional, quantity, leverage, order_type, margin_mode, parsed_from_user`

**RepairCandidate:** `action, amount, estimated_cost, estimated_slippage, post_repair_shadow_ratio, exposure_change, liquidity_quality, feasible, reason`

## 13. Raw-data provenance

Store raw API payloads before transformation. Each normalized observation must preserve:

```
provider
endpoint/tool
request timestamp
provider timestamp
raw payload hash
parser version
```

Model output must additionally preserve:

```
model version
feature-set version
input hashes
calculation timestamp
```

This powers the Evidence Lock and historical replay.

## 14. UTA reconciliation engine

This is milestone one. Do not start by building the pretty Thaw Frontier. First prove that PRISM understands the current account.

Bitget documents:

```
Account equity =
Σ(coin equity × coin USD price)
```

Adjusted equity is the amount of eligible asset value remaining after collateral ratios.

Bitget also defines the Advanced-mode cross-margin rate as:

```
Cross Margin Rate =
(Maintenance Margin + Partial Liquidation Fees)
/
Adjusted Equity
```

PRISM should reconstruct as much as the documented/API-exposed information permits.

## 15. Two reconciliation modes

**RECONCILED** — use when PRISM can independently reproduce effective equity, maintenance margin and margin ratio within configured tolerances. Suggested initial tolerances:

```
effective equity error <= 0.50%
margin ratio error <= 0.50 percentage points
```

Do not pretend these tolerances are sacred. Document why they were chosen and tighten them when possible.

**OBSERVED_BASELINE** — use when exact reconstruction is blocked by unavailable collateral tiers, fee treatment or other undocumented details. In this mode Bitget-reported `effEquity`, `mmr` and `mgnRatio` become the base state. PRISM then calculates explicit counterfactual deltas.

The UI must display: *Shadow results are delta-based from Bitget's observed account state.*

This is preferable to fake precision.

## 16. Reconciliation test

Generate:

```
BITGET OBSERVED

Effective equity       X
Maintenance margin     Y
Margin ratio           Z

PRISM RECONSTRUCTION

Effective equity       X'
Maintenance margin     Y'
Margin ratio           Z'

ERROR

Equity                 ...
Maintenance            ...
Margin ratio            ...

MODE                    RECONCILED / OBSERVED_BASELINE
```

Store reconciliation history. No Shadow Account result may be displayed without an attached reconciliation mode.

## 17. Position PnL

For simple linear futures:

```
long unrealized PnL
= (scenario_mark - entry_price) × size

short unrealized PnL
= (entry_price - scenario_mark) × size
```

Do not assume all product types share the same multiplier or quote conversion. Use instrument metadata.

Bitget's own documentation defines cross-margin futures unrealized PnL using direction × price difference × position size.

## 18. Maintenance margin

When full inputs are available, follow the documented Bitget formula. Bitget describes position maintenance margin in Advanced UTA approximately as:

```
position size
×
(mark-related position value)
×
(maintenance margin rate + taker fee rate)
×
quote USD value
```

with tier handling applying where relevant.

Do not hard-code one universal MMR. MMR can vary by instrument, position tier, account/product configuration and time.

If the exact future tier under a hypothetical trade cannot be verified: `simulation_accuracy = CONSERVATIVE_APPROXIMATION` and use the safer available tier assumption.

## 19. Tiered collateral valuation

Collateral ratios can be tiered. Do not use `quantity × price × single_ratio` for every asset unless the asset genuinely has one flat tier.

Implement `tiered_collateral_value(notional, tiers)`. Conceptually:

```
adjusted_value =
Σ value_inside_each_tier × tier_ratio
```

The function must handle 0-ratio tail tiers, changing tier limits and effective dates. Collateral schedules must be versioned.

## 20. Shadow Account calculation

For scenario `s`, define:

```
ShadowAdjustedEquity(s)
=
CurrentAdjustedEquity
+ ΔEligibleCollateral(s)
+ ΔCrossMarginPnL(s)
+ ΔOtherKnownComponents(s)
```

Prefer a delta model anchored to Bitget's observed effective equity unless exact reconstruction is proven.

For each closure-sensitive asset:

```
ΔCollateral_i(s)
=
AdjustedCollateralValue(
    quantity_i,
    shadow_price_i(s),
    collateral_tiers_i
)
-
CurrentRecognizedCollateralValue_i
```

Then:

```
ShadowMarginRatio(s)
=
(
    ShadowMaintenanceMargin(s)
    +
    modeled_partial_liquidation_fee_if_supported
)
/
ShadowAdjustedEquity(s)
```

If partial-liquidation fee modelling is unavailable, calculate `shadow_core_margin_ratio` and clearly distinguish it from Bitget's reported exact `mgnRatio`.

Never silently set an unknown fee to zero and call the result exact.

## 21. Reality Engine

The Reality Engine determines how strongly off-hours market information should influence the shadow-price band. It must not produce an arbitrary LLM score.

Inputs:

```
rToken live return
rToken spread
rToken depth if available
rToken turnover
quote stability
reference-market returns
sector-factor returns
crypto-factor returns
verified events
session state
historical residual behaviour
```

Output:

```
evidence_mode
market_state
market_quality
reference_consensus
reality_center
lower_stress_bound
upper_stress_bound
reason_codes[]
```

## 22. Market states

Use `DISCOVERY`, `DRIFT`, `OVERSHOOT`, `THIN`, `CONFLICT`, `INSUFFICIENT_DATA`.

Do not treat these as predictions. They are descriptions of available evidence.

## 23. Evidence modes

Use `OBSERVED`, `HYBRID`, `INFERRED`.

- **OBSERVED** — a live Bitget off-hours market exists and is usable.
- **HYBRID** — a live market exists but needs significant corroboration/adjustment.
- **INFERRED** — no reliable live Bitget market observation exists; factor/reference modelling is required.

The UI must display the mode beside each shadow asset.

## 24. Market quality

Do not launch with an unexplained `Trust = 0.78`.

Externally expose `HIGH`, `MEDIUM`, `LOW`, `UNAVAILABLE`, derived from transparent rules.

Candidate features:

```
spread percentile
top-of-book depth percentile
turnover percentile
price-impact estimate
quote continuity
reference dispersion
```

Prefer thresholds learned from each asset's own closure-period history or sector/global fallback. Do not hand-tune dozens of weights just because a dashboard looks better.

## 25. Reality centre

Do not automatically pull low-confidence prices toward Friday's close. Low liquidity implies uncertainty, not necessarily mean reversion.

V1 centre should come from a robust combination of available signals. Candidate architecture:

```
observed rToken implied return
external normalized reference returns
factor-model implied return
```

Use a robust median/weighted-median approach. Weight reductions are allowed for objectively weak market quality. Do not use Qwen to numerically move the price.

## 26. Reality interval

Construct the uncertainty band using empirical residuals where possible. Preferred:

```
historical residual quantiles
+
cross-source disagreement
+
market-quality widening
```

For assets with insufficient individual history: `asset → sector → global rToken closure distribution` using hierarchical fallback.

Never claim a token-specific calibrated model from three weekends of history.

## 27. Event evidence

Qwen outputs structured information such as:

```json
{
  "underlying": "NVDA",
  "event_type": "company_specific",
  "materiality": "high",
  "direction": "negative",
  "source_quality": "primary",
  "summary": "...",
  "citations": []
}
```

This evidence may change interpretation, select relevant factors, explain a disagreement, and increase/decrease qualitative confidence.

It must not directly produce *"NVDA should fall 4.71%"* unless a deterministic model calculates that number.

## 28. Price provenance

Every rToken detail view must answer:

```
What is Bitget recognizing?
What is trading now?
How old is the reference?
Is weekend trading enabled?
Which session are we in?
What evidence is available?
Which inputs generated the shadow band?
```

Example:

```
rNVDA

Underlying                  NVDA
US market                   CLOSED
Weekend tradable            YES
Collateral reference        FROZEN
Reference age               34h 18m
Live rToken                 AVAILABLE
Evidence                    HYBRID
Liquidity                   MEDIUM
Independent references      3
```

## 29. Latent Collateral Gap

Define:

```
LCG(s)
=
ShadowAdjustedCollateral(s)
-
CurrentlyRecognizedAdjustedCollateral
```

Display losses as negative values. Example:

```
Recognized rToken collateral       $18,950
Shadow collateral                  $14,640

LATENT COLLATERAL GAP              -$4,310
```

Never call it "missing money." It is a counterfactual difference.

## 30. Thaw-at-Risk

Use as a stress metric, not a probability. For chosen stress percentile/scenario `s`:

```
TaR(s)
=
ShadowMarginRatio(s)
-
CurrentMarginRatio
```

Example:

```
Current                      31.2%
Severe shadow                78.4%

TaR                         +47.2pp
```

## 31. Thaw Frontier

Create a 2D scenario grid. Horizontal dimension: rToken/equity-factor shock. Vertical dimension: crypto shock.

The initial canonical grid can be configurable, e.g.:

```
crypto:   +10 +5 0 -5 -10 -15 -20
rToken basket: +10 +5 0 -5 -10 -15 -20
```

Do not hard-code these as universal. Each cell runs the Shadow Engine. Return:

```
shadow effective equity
shadow margin ratio
collateral delta
PnL delta
threshold state
top risk contributors
```

No LLM is involved in cell calculation.

## 32. Blind Zone

Define a risk boundary. V1 should support:

```
user-selected threshold
Bitget warning threshold reference
100% account-risk boundary where applicable
```

Bitget's current Advanced UTA documentation states that a warning can occur at a cross-margin rate of 80%, while pre-partial-liquidation/partial-liquidation handling begins around the 100% region depending on the account state and order cancellations.

Never treat 80% as liquidation.

The Blind Zone is:

```
scenario currently appears acceptable
AND
shadow scenario exceeds chosen warning/risk boundary
```

## 33. Wrong-Way Collateral

For each derivative risk factor, estimate whether collateral tends to deteriorate under the same shock. Examples:

```
BTC long ↔ rCOIN
BTC long ↔ rMSTR
technology exposure ↔ rNVDA
crypto exposure ↔ ETH collateral
```

Initial implementation should be simple and defensible. Calculate factor sensitivity using historical returns. For asset `i` and risk factor `f`: `β_i,f`.

Wrong-way exposure exists when the sign of the collateral response worsens the same adverse scenario affecting the leveraged position.

Output:

```
wrong_way_assets[]
wrong_way_collateral_share
dominant_factor
```

Do not create an exaggerated network visualization until the calculation works.

## 34. Pre-Trade Gate

Natural-language entry point: *"Can I add another $10k BTC long?"*

Pipeline:

```
1. parse proposal
2. validate instrument
3. fetch latest account state
4. reconcile
5. create baseline shadow
6. insert proposed position
7. recompute initial/maintenance requirements
8. rerun Thaw Frontier
9. detect wrong-way collateral
10. find closest threshold-crossing scenarios
11. generate deterministic result
12. Qwen explains
```

## 35. Structured trade parser

Qwen must emit only schema-valid JSON. Example:

```json
{
  "instrument": "BTCUSDT",
  "category": "USDT-FUTURES",
  "direction": "long",
  "notional_usd": 10000,
  "leverage": null,
  "timing": "now",
  "confidence": "high",
  "missing_fields": []
}
```

If leverage is unspecified, use the actual account/instrument setting where available. Do not invent leverage.

## 36. Pre-Trade output

Example:

```
PROPOSED TRADE
BTCUSDT long
Additional notional            $10,000

CURRENT BITGET
Margin ratio                       31.2%

AFTER TRADE
Observed-state estimate             44.7%

SHADOW STRESS
Central                             61.3%
Severe                              88.4%

BLIND-ZONE ENTRY
BTC shock                           -6.8%
rToken basket                       -4.9%

WRONG-WAY COLLATERAL
Detected

Primary contributor:
rCOIN
```

Every number must be expandable to provenance.

## 37. Repair Solver

Given current/proposed portfolio, target shadow risk threshold and available actions, generate feasible repairs.

Candidate action classes:

```
add stable collateral
reduce futures position
reduce closure-sensitive collateral
add hedge
mixed repair
```

V1 is advisory only. No trade is placed.

## 38. Repair optimization

Each repair has an objective vector:

```
cash_required
exposure_changed
execution_cost
shadow_risk_after
estimated_slippage
liquidity_confidence
```

Support user objectives: minimum cash, minimum exposure change, minimum execution cost, maximum safety.

Do not pretend there is one universally "best" repair.

## 39. Repair feasibility

For an rToken sale, simulate executable depth when depth data are available. For selling quantity `Q`, walk bids until `Q` is filled. Calculate:

```
VWAP
best_bid
slippage_bps
notional
depth_consumed
```

If whitelist depth is unavailable: `execution_estimate = UNAVAILABLE`.

Do not derive fake slippage from candle volume. The system may still compare non-rToken repairs.

## 40. Weekend order constraints

Bitget documents that unfilled weekend limit orders are automatically cancelled when U.S. markets reopen, and that weekend order pricing has protective bounds around the session reference.

Repair feasibility must understand session-specific order limitations. Do not recommend an execution pattern the current Bitget regime cannot support.

## 41. Qwen responsibilities

Qwen is allowed to:

```
parse user intent
extract trade proposals
classify events
summarize evidence
identify missing information
turn scenario results into explanations
challenge the user's trade thesis
answer follow-up questions
```

Qwen is forbidden from:

```
inventing numbers
inventing sources
calculating risk
producing unverified confidence percentages
changing portfolio state
placing trades
```

## 42. Qwen tool contract

Every numerical statement in an answer must originate from a tool result. Provide Qwen with structured outputs such as:

```
get_current_account()
get_reconciliation()
get_reality_envelope(symbol)
run_shadow_scenario()
run_frontier()
analyze_proposed_trade()
get_repairs()
get_evidence()
```

System instruction: *Never generate a numerical market/account claim unless it appears in tool output. If required data are missing, say they are missing.*

## 43. Evidence Lock

Every major conclusion gets a `WHY?`. Example:

```
WHY PRISM FLAGGED THIS

MARKET
rCOIN weekend market              available
live move                         -8.1%
liquidity                         medium
reference agreement               high

ACCOUNT
rCOIN share of adjusted collateral  24%
BTC long exposure                   high

SCENARIO
BTC                               -8%
rCOIN                             -11%

MODEL
risk engine                    v0.4.2
reality engine                 v0.3.1

DATA CUTOFF
...
```

## 44. Live Mode

Live Mode requires live Bitget market data, live Bitget account state and a read-only account connection.

If account access disappears, do not keep presenting the last account as live. Show `ACCOUNT DATA STALE` with timestamp.

## 45. Replay Mode

Record complete closure windows. A replay package should contain:

```
market observations
account template or anonymized snapshot
market-state transitions
rToken candles
crypto candles
relevant references
event evidence
model version
expected results
```

Replay runs deterministically.

## 46. Judge Mode

Judge Mode uses `REAL RECORDED MARKET DATA + CLEARLY LABELED HYPOTHETICAL ACCOUNT`.

It must work offline from frozen replay files after installation. The judge should not need their own Bitget account, API credentials, a weekend or a market crash to see PRISM's strongest workflow.

## 47. Exact index-thaw experiment

We still need to empirically determine the precise transition behaviour. Create `scripts/capture_thaw.py`.

For several weekend→weekday transitions, record:

```
timestamp
rToken live price
underlying market state
Bitget recognized/index-related value if available
account rToken USD value
weekend/regular execution regime
```

Poll/subscription interval must be fast enough to identify the transition. Produce `docs/THAW_EXPERIMENT.md` containing actual observations.

Do not assume the index begins moving exactly at 09:30 ET. Bitget's documentation says traditional-market data are used during U.S. trading hours including pre-market and after-hours, while closure periods use the frozen extended-session close; this is precisely why empirical verification is required.

## 48. Historical validation

Create closure-window datasets. For each observation time `t` during a closure, store only information that was actually knowable at `t`. Prevent lookahead leakage.

For each target asset record:

```
last recognized close
live off-hours rToken price
market quality
external references
factor returns
event evidence
subsequent observed underlying reference
```

## 49. Validation baselines

Compare PRISM against at least:

- **Baseline A:** next mark = frozen reference
- **Baseline B:** next mark = live rToken price
- **Baseline C:** next mark = live rToken price + fixed historical uncertainty
- **PRISM:** market-quality-adjusted, multi-reference/factor, dynamic uncertainty

If PRISM does not outperform the simple live-price baseline, do not bury that finding. Change the model or reduce the claim.

## 50. Validation metrics

Use:

```
median absolute error
mean absolute error
directional accuracy
interval coverage
interval width
error by evidence mode
error by market-quality bucket
```

For account analysis:

```
reconciliation error
Blind-Zone detections
false warnings
missed threshold crossings
repair effectiveness
```

## 51. No random train/test splitting

Financial closure observations are temporally dependent. Use walk-forward validation, leave-one-weekend-out, time-ordered evaluation.

Never random shuffle the full dataset and claim out-of-sample performance.

## 52. Small-data handling

Use hierarchical fallbacks: `token-specific history → (if insufficient) sector history → global rToken history`.

Display:

```
DATA SUPPORT

Token          LOW
Sector         MODERATE
Global         MODERATE

Model mode     SHRUNK
```

Fail gracefully.

## 53. Autopsy

Before a transition, persist model inputs, model output, stress band, account shadow result, model version and timestamp.

After the next recognized reference, append actual result. Never overwrite the original forecast.

Example:

```
FORECAST

Centre                    -5.4%
Band                 -8.1 → -2.7

REALIZED                  -5.9%

Band hit                    yes
Centre error              0.5pp
```

## 54. Frontend design

PRISM should resemble a professional institutional risk terminal.

Not: cyberpunk, neon overload, huge chatbot, random gradient cards, AI robot imagery.

Use: black / near-black background, high-density typography, restrained accent system, precise spacing, clear data hierarchy, minimal glass effects.

Numbers should dominate.

## 55. Main layout

Desktop:

```
┌─────────────────────────────────────────────────────────────────────┐
│ PRISM      MARKET STATE: CLOSED       NEXT TRANSITION: ...         │
├───────────────┬───────────────────────────────┬─────────────────────┤
│ WATCHLIST     │ PRICE / PROVENANCE            │ DUAL REALITY LEDGER │
│               │                               │                     │
│ rNVDA         │            chart              │ BITGET NOW          │
│ DISCOVERY     │                               │                     │
│ HYBRID        │ frozen ref                    │ PRISM SHADOW        │
│               │ live rToken                   │                     │
│ rCOIN         │ events                        │ LCG                 │
│ ...           │                               │ TaR                 │
├───────────────┴───────────────────────────────┴─────────────────────┤
│                         THAW FRONTIER                               │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Ask PRISM: Can I add another $10k BTC long?                        │
└─────────────────────────────────────────────────────────────────────┘
```

## 56. UX hierarchy

The most important values are:

```
current Bitget margin state
shadow margin state
Latent Collateral Gap
nearest Blind-Zone boundary
wrong-way collateral warning
```

Do not make users hunt through tabs.

## 57. Data-quality states

Every module must support `LIVE`, `DELAYED`, `STALE`, `LIMITED`, `UNAVAILABLE`, `REPLAY`, `HYPOTHETICAL`.

Example:

```
Reality depth: LIMITED
Reason: whitelist access unavailable.
```

That is better than silently degrading.

## 58. Backend API

Suggested application endpoints:

```
GET  /health
GET  /market/state
GET  /assets/reality
GET  /assets/{symbol}/reality
GET  /account/current
GET  /account/reconciliation
POST /shadow/scenario
POST /shadow/frontier
POST /pretrade/analyze
POST /repair/solve
GET  /replay
GET  /replay/{id}
POST /copilot
GET  /evidence/{analysis_id}
```

All POST analysis endpoints must be idempotent for identical input hashes.

## 59. Database tables

At minimum:

```
market_observation
raw_provider_event
reality_asset
market_session
account_snapshot
position_snapshot
collateral_config
reconciliation_run
reality_analysis
scenario
shadow_result
frontier_run
trade_proposal
repair_run
evidence_record
model_run
replay_bundle
autopsy
```

## 60. Security

Never expose API secret, passphrase, OAuth token, Qwen key or private account identifiers to the browser. Backend only.

Do not log credentials. Sanitize raw API dumps before including them in public replay bundles.

## 61. Bitget authentication

Prefer Agent Hub OAuth/read-only, or minimally permissioned UTA API credentials.

Bitget's Agentic account architecture supports isolated funds and OAuth, while Agent Hub supports a read-only mode that blocks orders and transfers.

PRISM itself needs no write access.

## 62. Whitelist dependency strategy

Do not block the project waiting for Reality depth access.

**Without whitelist**, ship: Dual Reality Ledger, UTA reconciliation, ticker/candles, Market State, Thaw Frontier, Pre-Trade Gate, non-depth Repair Solver, Qwen, Replay.

**With whitelist**, upgrade: depth-aware market quality, slippage simulation, repair feasibility, microstructure stress.

This separation is mandatory.

## 63. Testing requirements

**Unit tests** cover:

```
tiered collateral calculation
PnL calculations
scenario shocks
shadow-equity delta
margin-ratio calculation
Latent Collateral Gap
Thaw-at-Risk
frontier cell classification
trade parsing schema
repair optimization
VWAP book walking
wrong-way factor logic
```

**Property tests**, e.g.:

```
adding positive stable collateral must not worsen equity
reducing a losing long cannot increase directional long exposure
larger negative collateral shock cannot increase shadow equity
identical current/shadow prices imply zero collateral price delta
```

**Integration tests** — record API fixtures and test:

```
Bitget account parser
positions parser
Reality metadata parser
market-state parser
calendar parser
ticker parser
Qwen structured-output validation
```

**End-to-end test** — canonical user flow:

```
load replay
→ account
→ reconcile
→ reality analysis
→ frontier
→ proposed BTC trade
→ wrong-way warning
→ repair
→ evidence
```

One command must run this deterministically.

## 64. Hard failure tests

PRISM must handle:

```
Bitget API unavailable
WebSocket disconnect
stale account snapshot
no rToken market
missing collateral ratio
no Reality-depth whitelist
Qwen failure
invalid Qwen JSON
external-reference disagreement
zero-liquidity book
negative effective equity
unsupported instrument
```

No unhandled exceptions in the demo.

## 65. Milestone order

**M0 — Repository and truth infrastructure.** Complete: repo, CI, schemas, provenance model, raw payload storage, env handling. Exit condition: no hard-coded visible financial numbers.

**M1 — Bitget verification.** Implement `verify_bitget.py`. Verify with actual responses: stock-info, market states, market calendar, rToken ticker, rToken candles, account assets, account WebSocket, positions, collateral type, custom collateral coins. Try Reality depth. Record whether whitelist access exists. Generate `docs/BITGET_VERIFICATION.md`. Exit condition: every claimed endpoint classified `VERIFIED`, `UNAVAILABLE`, `WHITELIST_REQUIRED` or `UNVERIFIED`.

**M2 — Account reconciliation.** Build current account model. Compare with Bitget. Exit condition: either `RECONCILED` within tolerance, or `OBSERVED_BASELINE` with a documented reason. Do not proceed by pretending failure is success.

**M3 — Reality Engine.** Build session awareness, weekendTradable detection, live/frozen comparison, evidence modes, market-quality features, robust stress band. Exit condition: at least three real rTokens can produce inspectable Reality Envelopes from real data.

**M4 — Shadow Engine.** Build shadow collateral, shadow PnL, shadow effective equity, shadow margin state, LCG, TaR. Exit condition: unit/property tests pass.

**M5 — Thaw Frontier.** Build scenario grid and Blind Zone. Exit condition: each cell is expandable into deterministic component attribution.

**M6 — Pre-Trade Gate.** Natural-language trade proposal → structured trade → shadow analysis. Exit condition: the canonical BTC-long prompt works end-to-end.

**M7 — Wrong-Way Collateral.** Add factor sensitivity and warning. Exit condition: a BTC-long + crypto-sensitive-rToken-collateral replay produces a transparent explanation.

**M8 — Repair Solver.** Implement stable top-up, position reduction, hedge, rToken reduction, mixed repair. Exit condition: at least three feasible alternatives correctly recompute the shadow account.

**M9 — Qwen and Evidence Lock.** Connect explanations to deterministic tool results. Exit condition: no numerical claim can be generated without provenance.

**M10 — Replay and Judge Mode.** Create one canonical real historical weekend package. Exit condition: full demo works without network credentials.

**M11 — Validation.** Run baselines and PRISM model. Exit condition: `VALIDATION.md` states actual results, including negative results.

**M12 — Polish.** Only now: animation, responsive layout, loading states, microinteractions, video, screenshots, pitch assets.

## 66. Definition of Done

PRISM is hackathon-ready only if all of these are true:

```
[ ] real Bitget rToken data
[ ] actual market-state awareness
[ ] actual weekendTradable discovery
[ ] read-only account integration or reproducible account fixture
[ ] current account reconciliation mode visible
[ ] Dual Reality Ledger works
[ ] Reality Engine uses deterministic market features
[ ] Shadow Engine works
[ ] Thaw Frontier works
[ ] Blind Zone works
[ ] Pre-Trade Gate works
[ ] Repair Solver works
[ ] wrong-way warning works
[ ] Qwen cannot manufacture financial numbers
[ ] Evidence Lock works
[ ] historical Judge Mode works
[ ] no unlabeled fake data
[ ] tests pass
[ ] one-command local setup documented
[ ] demo deploy is accessible
[ ] README clearly distinguishes official vs shadow state
```

## 67. Canonical demo

Use one coherent story.

1. **Current account** — show `BITGET NOW`, margin state = healthy. Explain: much of the account's collateral is tokenized U.S. equities.
2. **Pricing regime mismatch** — select rNVDA or another real closure-sensitive rToken. Show traditional reference frozen, live rToken moved, market state closed, evidence mode hybrid/observed.
3. **Dual Reality** — click `SHOW SHADOW`. The shadow account deteriorates. Highlight the Latent Collateral Gap.
4. **Thaw Frontier** — open the heatmap. Show the Blind Zone.
5. **Ask PRISM** — *"Can I add another $10,000 BTC long tonight?"* PRISM analyses the proposed trade.
6. **Hidden correlation** — show `WRONG-WAY COLLATERAL` if the replay account contains an appropriate exposure.
7. **Repair** — click `MAKE THIS SAFER`. Show add USDT, reduce BTC, hedge, reduce rToken, with actual recalculated consequences.
8. **Proof** — open Evidence Lock or a prior Autopsy.

Finish: *Bitget tells you the risk it recognizes now. PRISM shows the account you may carry into the next pricing regime.*

## 68. What not to build

Do not add:

```
autonomous trading
social trading
copy trading
generic sentiment gauge
generic indicators page
AI buy/sell score
portfolio-management CRUD
wallet
token
on-chain component
seven debating agents
exact Monday-price prediction
mobile app
```

unless every P0 item is already excellent.

## 69. Competition alignment

Under the S2 framework, PRISM belongs naturally in AI Trading Desk because that track is explicitly for natural-language research tools in which the human makes the final decision. Its Open Theme specifically gives portfolio-aware research, stress tests and hedge suggestions as an example; judging emphasizes feature depth, research quality, LUI fluency and a personalized thesis.

Do not optimize blindly for S2 because submissions are already closed. Before any future submission, reread that season's live rules.

## 70. Validation story for judges

We must eventually be able to say:

- *We did not assume the weekend rToken price was correct. We tested whether market-quality information improved reopen estimates over simpler baselines.*
- *We did not claim to reproduce Bitget's risk engine without testing it. PRISM first reconciles against Bitget's observed current account.*
- *We do not use Qwen for risk arithmetic. It parses and explains deterministic tools.*
- *Every historical result shown in Judge Mode comes from real captured market data.*

Those four claims matter more than adding ten extra features.

## 71. README opening

Use approximately:

> PRISM is the Reality Layer for 24/7 tokenized stocks.
>
> Tokenized equities can continue trading while the U.S. market is closed, but collateral valuation, derivative PnL and cross-asset risk do not necessarily update on the same clock.
>
> PRISM first evaluates the evidence behind an off-hours rToken price. It then builds a shadow copy of the trader's Bitget Unified Trading Account and asks what happens if closure-sensitive collateral transitions toward those market-implied values.
>
> The result is a Dual Reality Ledger, a Thaw Frontier showing hidden stress states, a Pre-Trade Gate for proposed positions and a Repair Solver that finds feasible ways to restore safety.
>
> PRISM does not replace Bitget's risk engine. It reveals counterfactual risk before the next pricing regime arrives.

## 72. Agent responsibilities

**Implementation agent** — primary implementation agent. Responsibilities: architecture, frontend, backend, connectors, tests, documentation, integration.

The implementation agent must stop implementation of any financial assumption that cannot be supported by documentation/API evidence and mark it in `docs/OPEN_QUESTIONS.md` unless a safe degraded implementation exists.

**Codex** — adversarial reviewer. Responsibilities: audit math, attack assumptions, inspect data leakage, check source provenance, review Bitget API use, write adversarial tests, check numerical edge cases, challenge security, challenge demo claims.

Codex must not independently redefine scope. Keep `docs/CODEX_QUEUE.md` for unresolved review items.

## 73. Required documentation

Before final submission the repo must include:

```
README.md
ARCHITECTURE.md
BITGET_VERIFICATION.md
MATH.md
DATA_PROVENANCE.md
VALIDATION.md
MODEL_CARD.md
DEMO.md
OPEN_QUESTIONS.md
CODEX_QUEUE.md
```

## 74. Open questions that must be experimentally resolved

Keep these open until verified:

- **A. Exact collateral-index thaw timing.** When exactly does the relevant stock-token collateral mark resume updating?
- **B. Collateral-ratio API completeness.** Can we retrieve exact live tiered ratios programmatically, or must part of the configuration be sourced elsewhere?
- **C. Full UTA reconstruction.** Can all components required to reproduce `mgnRatio` be obtained?
- **D. Future position-tier treatment.** Can proposed-position maintenance requirements be modelled exactly from available APIs?
- **E. Weekend forced-collateral conversion.** What exact pricing mechanism is used if a weekend liquidation reaches collateral conversion? PRISM V1 does not need E to work because the objective is pre-liquidation decision support.

## 75. First engineering task

Do not start by designing the UI.

Create `scripts/verify_bitget.py`. It must query/test every needed Bitget primitive and generate a machine-readable capability report:

```json
{
  "timestamp": "...",
  "reality_stock_info": "verified",
  "market_states": "verified",
  "market_calendar": "verified",
  "rtoken_ticker": "verified",
  "rtoken_candles": "verified",
  "reality_depth": "whitelist_required",
  "reality_fills": "whitelist_required",
  "account_assets": "verified",
  "account_ws": "verified",
  "positions": "verified",
  "collateral_type": "verified",
  "custom_collateral_coins": "verified",
  "collateral_ratio_tiers": "unknown",
  "reconciliation": "not_started"
}
```

Then update `docs/BITGET_VERIFICATION.md` with the real results.

## 76. Second engineering task

Capture one complete current-account snapshot. Build `reconciliation/` before any Shadow Account UI exists.

The first meaningful milestone is not *"Dashboard looks good."* It is: *PRISM can explain exactly what Bitget says my account contains and either reconcile its risk metrics or clearly explain why it cannot.*

## 77. Third engineering task

Capture closure data continuously. Build `scripts/capture_weekend.py`. Store:

```
rToken prices
candles
turnover
available depth
crypto factors
market states
reference prices
timestamps
```

This dataset becomes one of PRISM's biggest assets. Do not wait until the UI is finished to start collecting it.

## 78. Quality bar

A feature is not complete because it renders. It is complete only when:

```
source verified
schema typed
failure state handled
unit tests exist
provenance exists
UI explains uncertainty
replay fixture exists
README claim matches reality
```

## 79. Product invariant

Every screen must preserve this distinction:

- **BITGET OBSERVED** — what the exchange currently reports.
- **PRISM SHADOW** — what our counterfactual model calculates.

Never blur those.

## 80. Final invariant

PRISM must always be able to answer: *Where did this number come from?*

If it cannot, the number does not belong in the product.

## 81. Kickoff prompt for the implementation agent

> Read `AGENTS.md` completely before writing code.
>
> You are the primary senior engineer for PRISM. Do not redesign the product. Do not invent financial data, Bitget behaviour, endpoint schemas or risk formulas.
>
> Begin with M0 and M1 only.
>
> First inspect the repository and current environment. Then create the project structure, truth/provenance primitives and `scripts/verify_bitget.py`.
>
> Verify every required Bitget API capability against the current official documentation and, where credentials/access permit, against an actual response. Create `docs/BITGET_VERIFICATION.md` and classify each dependency as `VERIFIED`, `UNAVAILABLE`, `WHITELIST_REQUIRED` or `UNVERIFIED`.
>
> Specifically determine what `/api/v3/account/custom-collateral-coins` actually returns; do not assume it exposes collateral-ratio tiers.
>
> Do not build the Thaw Frontier, Shadow Account UI or Qwen interface yet.
>
> After M1, report:
>
> 1. what was verified;
> 2. what requires authentication;
> 3. what requires Reality whitelist access;
> 4. whether exact collateral tiers are programmatically available;
> 5. what prevents exact UTA reconciliation;
> 6. every assumption that remains open;
> 7. the exact files created or changed.
>
> Stop at that milestone so the product owner can inspect the factual foundation before we build financial logic.

## 82. Kickoff prompt for Codex after M1

> Read `AGENTS.md`, `docs/BITGET_VERIFICATION.md` and all M0/M1 code.
>
> Act as an adversarial financial-engineering reviewer. Do not expand product scope.
>
> Audit:
>
> - every Bitget endpoint and claimed field;
> - authentication/permission assumptions;
> - stale documentation assumptions;
> - Reality whitelist dependencies;
> - whether any fake/default financial value can enter production paths;
> - timestamp/timezone correctness;
> - schema precision;
> - raw-data provenance;
> - error handling;
> - secrets handling;
> - whether anything marked VERIFIED has not actually been verified.
>
> Add unresolved issues to `docs/CODEX_QUEUE.md`. If an assumption is unsupported, mark it clearly. Do not "fix" uncertainty by inventing a value.
>
> End with a `GO / CONDITIONAL GO / NO-GO` recommendation for starting M2 UTA reconciliation.

## 83. Build priority

If time becomes limited, protect the project in this order:

1. Reconciliation
2. Dual Reality Ledger
3. Thaw Frontier
4. Pre-Trade Gate
5. Repair Solver
6. Wrong-Way Collateral
7. Qwen polish
8. Replay/Autopsy polish

Cut anything below those before weakening the mathematical integrity of the first five.

## 84. The standard we are aiming for

The project should make a judge think:

- *"I hadn't realized that a live 24/7 tokenized-stock market and a temporarily frozen collateral reference could create this portfolio state."*
- Then: *"They actually modelled it."*
- Then: *"They proved their starting account against Bitget."*
- Then: *"The AI is doing useful reasoning instead of making up risk numbers."*
- Then: *"I could imagine this becoming part of Bitget itself."*

That is the bar.

PRISM is not another stock dashboard. PRISM is a shadow risk system for a market that runs on multiple clocks.
