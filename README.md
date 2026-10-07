# PRISM — the Reality Layer for 24/7 tokenized stocks

**Live:** https://app.getprismpulse.xyz

Tokenized U.S. equities (Bitget rTokens) keep trading while the U.S. market is closed, but the collateral value Bitget's Unified Trading Account recognizes for them is frozen at the last extended-session close. Collateral valuation, derivative PnL and cross-asset risk stop updating on the same clock.

PRISM first evaluates the evidence behind an off-hours rToken price. It then builds a shadow copy of the trader's Bitget account and asks what happens if closure-sensitive collateral moves toward those market-implied values when the reference thaws.

The result is a **Dual Reality Ledger**, a **Thaw Frontier** showing hidden stress states (the Blind Zone), a **Pre-Trade Gate** for proposed positions, and a **Repair Solver** that finds feasible ways to restore safety.

PRISM does not replace Bitget's risk engine and never trades. It reveals counterfactual risk before the next pricing regime arrives.

## Bitget observed vs PRISM shadow

| | Bitget observed | PRISM shadow |
| --- | --- | --- |
| What | What the exchange reports now: effective equity, maintenance margin, margin ratio, balances, positions | What PRISM's deterministic model calculates under labelled stress scenarios |
| Source | Bitget UTA v3 API (read-only) | `services/api/prism/shadow`, `reality`, `repair` |
| Authority | Authoritative for the current account | Counterfactual; never presented as Bitget's numbers |

Every account baseline carries a reconciliation mode: `RECONCILED` (PRISM reproduced Bitget's numbers within 0.5%) or `OBSERVED_BASELINE` (shadow results are deltas from Bitget's reported state, with the reason shown).

## What is inside

- **Reality Engine** (`prism/reality`): regime (frozen vs live reference), move against the frozen reference, liquidity quality ranked against each rToken's own captured order-book history, agreement with the underlying stock and the Bitget stock perp, SEC 8-K event evidence, evidence mode and market state. Deterministic rules with reason codes.
- **Reconciliation** (`prism/reconciliation`): tests competing readings of Bitget's collateral rules against the observed account.
- **Shadow engine and Thaw Frontier** (`prism/shadow`): tiered collateral from Bitget's published discount-rate tiers, linear perp PnL, maintenance margin from published position tiers.
- **Wrong-way collateral** (`prism/wrong_way.py`): historical betas of rToken collateral to BTC (e.g. rCOIN, rMSTR).
- **Pre-Trade Gate and Repair Solver** (`prism/pretrade.py`, `prism/repair.py`): order-book-walked execution cost, advisory only.
- **Qwen** (`prism/connectors/qwen`): parses natural-language trade proposals into a schema and explains results. Any explanation containing a number PRISM did not produce is rejected.
- **Capture** (`prism/capture`): continuous read-only recording of rToken, stock-perp and crypto markets for closure-window validation.

## Data sources

| Source | Role | Status |
| --- | --- | --- |
| Bitget UTA v3 REST/WebSocket | Market data, Reality metadata, account (read-only) | Verified ([docs/BITGET_VERIFICATION.md](docs/BITGET_VERIFICATION.md)) |
| Yahoo Finance chart (unofficial) | Underlying extended-hours close (frozen-reference proxy) and live reference | Labelled unofficial and possibly delayed |
| SEC EDGAR | 8-K material-event filings (primary source) | Official |
| Bitget US-stock data MCP | Quotes, earnings, news | Returned 503 at time of writing; used again when available |

## Run it

```bash
cp .env.example .env.local        # add a READ-ONLY Bitget key and a Qwen key
uv run --project services/api pytest -q services/api/tests
uv run --project services/api python scripts/verify_bitget.py
uv run --project services/api uvicorn prism.api.app:app --app-dir services/api --port 8790
```

Then open http://localhost:8790. Judge Mode (a clearly labelled hypothetical account priced with real Bitget data) needs no credentials.

Deployment, capture and operations: [docs/RUNNING.md](docs/RUNNING.md). Open assumptions: [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md). Engineering specification: [AGENTS.md](AGENTS.md). Product narrative: [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md).

## Limits

- Bitget's frozen collateral index is not exposed by any API; PRISM uses a labelled proxy until the thaw experiment measures it.
- Stress scenarios are labelled stress tests, not forecasts. No calibrated stress band is shown until enough closure windows with a realized reopen are captured.
- Trading fees are excluded from repair costs until confirmed for live accounts.
