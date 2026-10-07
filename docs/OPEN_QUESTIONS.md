# Open questions

Every item is an `UNVERIFIED_ASSUMPTION` (AGENTS.md §1.5) until closed by documentation, an actual API response, a controlled account experiment or Bitget clarification. Close an item by recording the evidence and date, never by deleting it.

## From the spec (§74)

| ID | Question | Status | Evidence so far |
| --- | --- | --- | --- |
| A | When exactly does the stock-token collateral mark resume updating after a closure? | OPEN | Needs `scripts/capture_thaw.py` across real weekend→weekday transitions with a production account holding an rToken. |
| B | Can exact live tiered collateral ratios be retrieved programmatically? | PARTIAL | Public `market/discount-rate` returns `{tierStartValue, discountRate}` tiers for ~200 rTokens (2026-10-06). See Q-TIER-UNIT, Q-TIER-APPLY, Q-OVERRIDE. |
| C | Can all components required to reproduce `mgnRatio` be obtained? | OPEN | Partial-liquidation fee term and taker fee not yet observed. |
| D | Can proposed-position maintenance requirements be modelled exactly? | PARTIAL | Public `market/position-tier` returns `tier, minTierValue, maxTierValue, leverage, mmr`. Taker-fee term and tier-boundary behaviour unverified. |
| E | Weekend forced-collateral-conversion pricing. | OPEN (not needed for V1) | — |

## Raised by M1 (2026-10-06)

| ID | Question | Why it matters | How to close |
| --- | --- | --- | --- |
| Q-KEY | Will a production, read-only UTA key be provided? | All signed M1 results are from the demo (paper) environment with an empty account; reconciliation (M2) cannot start on it. The current key also has `uta_trade` permission. | Create a production key with read-only UTA permission; re-run `verify_bitget.py` without `BITGET_DEMO_TRADING`. |
| Q-TIER-UNIT | Is `tierStartValue` USD notional, USDT, or coin quantity? | Collateral valuation (§19) is wrong by the price factor if units are misread. | Bitget docs, or a controlled account holding an rToken near a tier boundary. |
| Q-TIER-APPLY | Do tiers apply marginally (value inside each tier × ratio) or as one ratio for the whole holding? | Changes `effEquity` materially for large holdings. | Same controlled experiment; compare against Bitget-reported `effEquity`. |
| Q-OVERRIDE | Do account-specific discount rates override the public schedule? | `account/eligible-discount-rate` exists in CCXT's list but returned 404 in demo. | Probe with a production key. |
| Q-COLLAT-MODE | In `collateralType = custom`, can rTokens be selected as collateral? Which mode does a typical rToken-collateral user run? | The demo account's custom set is `USDT,USDC`, so rTokens contribute nothing there. If many users are in custom mode, PRISM must check per-account eligibility before computing LCG. | Production key; Bitget docs on collateral modes. |
| Q-DEPTH | Is the public `market/orderbook` for rTokens the same book as the whitelisted `account/reality-orderbook` ("raw" 40 levels)? | If the public book is adequate, depth-aware market quality and repair feasibility (§39, §62) need no whitelist. | Compare both books at the same timestamp once whitelist access exists; ask Bitget. |
| Q-WHITELIST | Do `reality-orderbook` / `reality-fills` require whitelist in production? | Demo returned 404, which tells us nothing about whitelist. | Probe with a production key. |
| Q-TURNOVER | Does rToken `turnover24h` describe the underlying U.S. market while `platformTurnover24h` describes Bitget's book? | RNVDA showed `turnover24h` ≈ 20,000× `platformTurnover24h`. Using the wrong one would badly overstate Bitget liquidity. | Bitget docs; compare against candle turnover sums. |
| Q-TZ | `reality/market/states` labels sessions `EST` with `daylightType = standard` during U.S. daylight time. Are the clock times New York local time or fixed UTC−5? | A one-hour error in session boundaries corrupts closure detection and thaw timing. | Poll `states` across the November DST change; compare against observed rToken behaviour at the documented boundaries. |
| Q-WS-SCHEMA | Do `coin[]` items in the account WS (`balance, balanceOriginal, equity, usdValue, available, borrow, debts`) and position WS fields match the spec? | Demo account was empty, so these could not be observed. | Production key with holdings. |
| Q-PRICE-SOURCE | Which price does Bitget use to value each collateral coin (index, mark, last)? | Needed to reproduce `effEquity` (M2) and to define "recognized" collateral for LCG. | Docs, or compare per-coin `usdValue / balance` with candidate prices. |
| Q-FEE | Taker fee rate for MMR (§18): `account/fee-rate` returned 404 in demo. | MMR reconstruction. | Production key. |

## Raised by M2/M4 (2026-10-07)

| ID | Question | Why it matters | How to close |
| --- | --- | --- | --- |
| Q-RATIO-UNIT | Is `mgnRatio` a fraction (0.19) or a percent (19)? | Comparing PRISM's core ratio with Bitget's. `reconcile()` infers it from `mmr/effEquity` when an account has positions. | Funded demo account. |
| Q-UPNL | Does per-coin `usdValue` already include unrealised PnL, or is account `unrealisedPnl` added on top? | `effEquity` reconstruction. Both are tested as hypotheses. | Funded demo account with an open position. |
| Q-NEGATIVE-BALANCE | How do negative coin balances (borrowings) enter adjusted equity? | Assumed to count in full. | Docs, or an account with a borrow. |
| Q-RECOGNIZED-PRICE | Which price does Bitget use for an rToken's recognized collateral during a closure? | Judge Mode uses the rToken last price at build time as a stand-in, labelled as an assumption. | Thaw experiment with a held rToken. |
| Q-MMR-SCALE | With Bitget's real BTC MMR (0.4% tier 1), the spec's illustrative margin ratios (31% → 78%) imply very high leverage. | For realistic leverage, shadow stress comes mainly from equity erosion, not from MM growth. The demo narrative should say so. | Compare with a funded demo account's reported `mgnRatio`. |

## Closed or narrowed (2026-10-07, funded demo account: 50,000 USDT, no positions)

- **Q-WS-SCHEMA (account): CLOSED.** WS `account` push contains every spec field (`totalEquity, effEquity, mmr, imr, mgnRatio, positionMgnRatio, unrealisedPnL, coin[].{coin, balance, balanceOriginal, equity, usdValue, available, borrow, debts}`) plus `baseDebt, bonus, locked`. REST `account/assets` per-coin fields are `available, balance, balanceOriginal, bonus, coin, debt, equity, interestBase, locked, usdValue` (REST says `debt`, WS says `debts`). Position fields remain unverified (no positions yet).
- **First reconciliation: RECONCILED, but trivially.** PRISM reproduced `effEquity` = $49,984.13 with 0.0000% error. USDT is valued at ≈ $0.99968 (usdValue / balance), not $1. With one coin at ratio 1 and no positions, all four hypotheses match, so Q-TIER-APPLY, Q-UPNL and Q-RATIO-UNIT stay open until the account holds an rToken and a perp.
- **Demo rToken trading (2026-10-07 16:51 UTC, U.S. regular session open).** Live: RNVDAUSDT and RSPYUSDT instruments `status=online`. Demo (`paptrading: 1`): RNVDAUSDT has a ticker but **no instrument record**, and RSPYUSDT is `status=limit_open`. The demo UI shows rTokens as closed. rToken collateral behaviour (LCG, thaw timing) therefore cannot be tested in demo; it needs a live account holding an rToken (§47). The demo account is still usable for BTC-perp reconciliation (MM, Q-RATIO-UNIT, Q-UPNL).

## Funded demo account with positions (2026-10-07 ~17:00 UTC: 50k USDT, BTCUSDT long 0.5, NVDAUSDT long ~42, two unfilled limit buys)

- **Q-RATIO-UNIT: CLOSED.** `mgnRatio` is a fraction: 0.0271 = `mmr` / `effEquity` (1354.4 / 49953.25 = 0.02711). At this level no partial-liquidation fee term is visible.
- **Q-UPNL: CLOSED.** Per-coin `equity` = `balance` + unrealised PnL (49970.39 + 0.889 = 49971.28), and `usdValue` = `equity` × USD rate (0.99964). So `usdValue` includes PnL and the coin's USD rate is `usdValue / equity`.
- **Q-ORDER-MARGIN (new, OPEN).** Bitget's `positionValue`, `imr` and `mmr` include unfilled orders. IM ≈ Σ(position + order notional) / leverage (−2.5% error). MM is not reproduced: positions × tier gives −55%, (positions + orders) × order-inclusive tier gives +12%. Position `mmr` fields report the tier reached *including* open orders (BTC 0.005 with orders, 0.004 without). To close: compare with no open orders, then with one order of known size.
- **Demo tiers differ from live.** Signed (`paptrading`) `market/position-tier` returns different schedules: NVDAUSDT demo 0.02 / 0.03 / 0.04 vs live 0.005 / 0.0066 / 0.01; BTCUSDT demo tier-1 cap 150k vs live 200k. PRISM now uses demo-environment tiers, books and marks for demo accounts.
- **Hold mode:** this account is `hedge_mode`. A short hedge would be a separate position, not a reduction.
- **Q-FEE: narrowed.** Position `openFeeTotal` / entry notional ≈ 0.06% for both BTCUSDT (25.005 / 41,675) and NVDAUSDT. This is an observed demo taker fee; repair costs still exclude fees until it is confirmed for live accounts.
- **Positions schema verified:** `category, symbol, marginCoin, holdMode, posSide, marginMode, positionBalance, available, frozen, total, leverage, curRealisedPnl, avgPrice, positionStatus, unrealisedPnl, liquidationPrice (0 in cross), mmr (rate), profitRate, markPrice, breakEvenPrice, totalFunding, openFeeTotal, closeFeeTotal, createdTime, updatedTime, cashDividend`.

## External references added (2026-10-07)

Bitget's US-stock MCP still returns 503 for every data query, so two read-only sources were added (the same Yahoo backup NEXUS uses):

- **Yahoo Finance chart** (`query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}`, 5d, 1m, pre/post market). **Unofficial**, possibly delayed, Yahoo's terms. Used for (a) the frozen-reference proxy: the underlying's last extended-hours trade at or before 20:00 New York (A1, now closer to Bitget's documented rule than the rToken's own close; e.g. NVDA 240.43 vs rNVDA 239.99 at the Oct 6 boundary), and (b) live reference agreement when the trade is under 15 minutes old. It is never used as a Bitget price. Reachable from the VPS.
- **SEC EDGAR submissions** (`data.sec.gov`). **Official, primary source.** 8-K filings accepted after the regular close (16:00 New York, four hours before the 20:00 reference; changed from 3 days in review on 2026-10-07) become event evidence with item codes (e.g. MSTR 8-K 7.01/8.01 on 2026-10-05). ETFs and funds without 8-Ks show "no 8-K". The User-Agent contact is `PRISM_SEC_CONTACT` (project address, never personal).
- **Still open:** news headlines (no official free source wired), and the real overnight venue prices for the underlying (Yahoo's pre/post data excludes 20:00–04:00 trading).
