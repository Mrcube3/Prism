# Bitget verification (M1)

Run: `BITGET_DEMO_TRADING=1 uv run --project services/api python scripts/verify_bitget.py` (drop the flag for a production key).

Verification run: 2026-10-06 (UTC), from this workstation. Every request was a GET or a private-WebSocket subscribe. Raw payloads are in `data/raw/` (git-ignored).

## Read this first: the configured key is a demo-trading key

- Signed requests without the `paptrading: 1` header fail with `40099 exchange environment is incorrect`; the private WebSocket fails with `30017 Current environment does not match the API Key`. With the header (REST) and `wss://wspap.bitget.com/v3/ws/private` (WS), they succeed. This is an environment mismatch, not an IP or region block.
- **Every signed result below describes Bitget's paper-trading environment, not a real account.** Demo data must never be labelled `LIVE` (AGENTS.md §1.1). Settings flag: `BITGET_DEMO_TRADING=1`.
- The demo account is empty: `assets` contains no coins, the position list is empty, and the WS `account` push has an empty coin array. Endpoints are reachable, but **per-coin and per-position field schemas are not verified**.
- `GET /api/v3/account/info` reports `permType = read_and_write`, `permissions = [uta_mgt, uta_trade]`. This exceeds AGENTS.md §8/§61 (minimum permissions, no execution). PRISM's client is GET-only so it cannot trade, but **a production key should be created read-only.**

## Findings by kickoff question

### 1. Verified (live response, this run)

Public, production environment: `reality/market/stock-info`, `reality/market/states`, `reality/market/calendar`, rToken `market/instruments`, `market/tickers`, `market/candles` (1m), `market/orderbook` for an rToken, BTCUSDT futures ticker, `market/position-tier`, `market/discount-rate`.

Signed, demo environment: `account/info`, `account/settings`, `account/assets`, `position/current-position`, `account/collateral-type`, `account/custom-collateral-coins`, private WS `account` and `position` topics (login timestamp in **seconds**; the v3 docs do not state the unit).

Observed facts worth carrying forward:

- **Reality universe:** 2,810 rTokens in `stock-info`; 90 have `weekendTradable = yes` (includes RNVDA, RTSLA, RCOIN, RMSTR, RSPY, RQQQ). Values seen: `yes`, `no`. `name` is sometimes `null`.
- **Sessions:** `states` returns `pre_market 04:00–09:30`, `regular 09:30–16:00`, `after_hours 16:00–20:00`, `overnight 20:00–04:00`, labelled `timeZone = EST`, `daylightType = standard`, even though 2026-10-06 falls in US daylight time. See Q-TZ.
- **Calendar:** `regularConfig = [SATURDAY, SUNDAY]` plus three `specificConfig` closure windows (2026-06-18 20:00 → 06-19 20:00, 07-02 20:00 → 07-03 20:00, 09-06 20:00 → 09-07 20:00, "EST"). No upcoming holidays are listed beyond September.
- **rToken ticker** fields: `lastPrice, bid1Price, ask1Price, bid1Size, ask1Size, volume24h, turnover24h, platformTurnover24h, price24hPcnt, ts, …`. For RNVDAUSDT, `turnover24h` (~1.4e10) is roughly 20,000× `platformTurnover24h` (~7.1e5), so `turnover24h` appears to describe the underlying market, not Bitget's book. Only `platformTurnover24h` should feed Bitget liquidity metrics until confirmed (Q-TURNOVER).
- **Public rToken order book:** `GET /api/v3/market/orderbook?category=SPOT&symbol=RNVDAUSDT` returns real levels **without whitelist** (40 per side at `limit=40`; 51 bids / 54 asks at `limit=100`). The spec assumed depth is whitelist-only (§4, §62). Whether this public book equals the "raw" Reality book is unknown (Q-DEPTH).
- **Position tiers** (`market/position-tier`): `tier, minTierValue, maxTierValue, leverage, mmr` per symbol, public.
- **Account assets** top-level fields: `accountEquity, effEquity, usdtEquity, btcEquity, unrealisedPnl, usdtUnrealisedPnl, btcUnrealizedPnl, imr, mmr, mgnRatio, positionMgnRatio, positionValue, leverage, assets`. REST uses `accountEquity` where WS uses `totalEquity`; REST spells `unrealisedPnl`, WS `unrealisedPnL`; `btcUnrealizedPnl` uses "z".
- **Account WS push** top-level fields: `totalEquity, effEquity, imr, mmr, mgnRatio, positionMgnRatio, unrealisedPnL, coin[]`. `coin[]` was empty, so `balance, balanceOriginal, equity, usdValue, available, borrow, debts` are **unverified**.
- **Position WS push** arrived with an empty data array, so `leverage, unrealisedPnl, liqPrice, mmr, marginRate, markPrice` are **unverified**.
- **Account settings** fields: `accountLevel, accountMode, assetMode, coinConfigList, deltaSwitch, holdMode, repayMode, stpMode, symbolConfigList, uid`.

### 2. Requires authentication

`account/*`, `position/*`, the private WS, and **`account/custom-collateral-coins`**: CCXT lists it as public, but unsigned calls return `40006 Invalid ACCESS_KEY`.

### 3. Requires Reality whitelist access

**Not established by this run.** `account/reality-orderbook` and `account/reality-fills` returned `404 Request URL NOT FOUND` in the demo environment, as did `account/eligible-discount-rate` and `account/fee-rate`. A 404 says the paper environment doesn't serve the route; it says nothing about whitelist status. These stay `UNVERIFIED` until probed with a production key. The whitelist requirement remains a **documented** claim, not an observed one.

### 4. Are exact collateral tiers programmatically available?

**Mostly yes, publicly — with caveats.**

- `GET /api/v3/market/discount-rate` (public) returns, per coin, `list[] = {tierStartValue, discountRate}`: 520 coins including about 200 rTokens, in 15 distinct schedules. Examples: the rSPY/rMSFT/rNVDA class `0→0.95, 500k→0.94, 1M→0.93 … 30M→0.70, 50M→0`; a steeper rToken class (rRKLB, rHOOD, …) `0→0.95, 10k→0.90, 50k→0.80 …`; USDT/USDC `0→1, 1B→0`; BTC/ETH `0→0.98, 1M→0.97, 5M→0.96 …`.
- This is tiered data with an explicit 0-ratio tail, as §19 requires. **Unverified:** the unit of `tierStartValue` (USD notional assumed), whether tiers apply marginally (value inside each tier) or to the whole holding, and whether account-specific overrides exist (`eligible-discount-rate` is 404 in demo).
- `account/custom-collateral-coins` returns **only** `[{collateralCoin}]`, with no ratios or tiers. In demo it lists 14 coins, including test coins (`BGTEST001`, `TESTC`, `TESTZEUS`) and **no rTokens**.
- `account/collateral-type` returns `{collateralType: "custom", collateralCoins: "USDT,USDC"}` for the demo account, so rTokens would not count as collateral there at all. Whether production custom mode can include rTokens is unknown (Q-COLLAT-MODE).

### 5. What prevents exact UTA reconciliation today

1. No production account: the demo account is empty, so there is no per-coin `usdValue`/`equity`, no positions, nothing to reconcile.
2. `tierStartValue` unit and marginal-vs-whole application are unverified; `effEquity` reconstruction depends on both.
3. The per-coin collateral price source (index vs mark vs last) is not exposed in any verified field.
4. The partial-liquidation fee term in the cross-margin-rate formula is not exposed by any verified API output (§20).
5. Taker fee rate for maintenance margin: `account/fee-rate` was 404 in demo.
6. Account-specific discount overrides: `eligible-discount-rate` was 404 in demo.

Until these close, M2 should plan for `OBSERVED_BASELINE` mode.

### 6. Open assumptions

See [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

## Bitget US-stock data MCP (2026-10-07)

- `https://agent.bitget.com/mcp`: `initialize` and `tools/list` work without credentials (server `bitget-mcp-server` 4.0.5). Tools: `guide` (catalog) and `do_query`.
- The catalog lists 22 equity entries (quote, historical K-lines, profile, earnings calendar, financial statements, …), 3 ETF, 1 news (`news_label_search`, needs an integer label), 2 sentiment and 39 crypto entries.
- **Every `do_query` returned upstream HTTP 503** from both the workstation and the VPS. Status: catalog VERIFIED, data UNAVAILABLE. Connector: `prism/connectors/bitget_data/`. PRISM shows these results as UNAVAILABLE and substitutes nothing.
- Demo environment market data: rNVDA, rSPY, rQQQ, rAAPL, the BTC perp and the NVDA stock perp exist with `paptrading: 1`; rCOIN, rTSLA and rMSTR do not.

## Generated capability table

<!-- AUTO:verify_bitget START -->
_Generated by `scripts/verify_bitget.py` at 2026-10-06T23:55:47.557735+00:00. Re-run to refresh; edit outside the markers._

Signed-request environment: **DEMO_PAPTRADING**.

| Capability | Status | Request | Result |
| --- | --- | --- | --- |
| `reality_stock_info` | **VERIFIED** | `GET /api/v3/reality/market/stock-info` | Response returned code 00000. |
| `market_states` | **VERIFIED** | `GET /api/v3/reality/market/states` | Response returned code 00000. |
| `market_calendar` | **VERIFIED** | `GET /api/v3/reality/market/calendar` | Response returned code 00000. |
| `rtoken_instruments` | **VERIFIED** | `GET /api/v3/market/instruments?category=SPOT&symbol=RNVDAUSDT` | Response returned code 00000. |
| `rtoken_ticker` | **VERIFIED** | `GET /api/v3/market/tickers?category=SPOT&symbol=RNVDAUSDT` | Response returned code 00000. |
| `rtoken_candles` | **VERIFIED** | `GET /api/v3/market/candles?category=SPOT&symbol=RNVDAUSDT&interval=1m&limit=5` | Response returned code 00000. |
| `rtoken_public_orderbook` | **VERIFIED** | `GET /api/v3/market/orderbook?category=SPOT&symbol=RNVDAUSDT&limit=5` | Response returned code 00000. |
| `crypto_perp_ticker` | **VERIFIED** | `GET /api/v3/market/tickers?category=USDT-FUTURES&symbol=BTCUSDT` | Response returned code 00000. |
| `position_tier` | **VERIFIED** | `GET /api/v3/market/position-tier?category=USDT-FUTURES&symbol=BTCUSDT` | Response returned code 00000. |
| `discount_rate` | **VERIFIED** | `GET /api/v3/market/discount-rate` | Response returned code 00000. |
| `custom_collateral_coins` | **UNVERIFIED** | `GET /api/v3/account/custom-collateral-coins` | HTTP 400, code 40006: Invalid ACCESS_KEY |
| `account_info` | **VERIFIED** | `GET /api/v3/account/info` (signed) | Response returned code 00000. |
| `account_settings` | **VERIFIED** | `GET /api/v3/account/settings` (signed) | Response returned code 00000. |
| `account_assets` | **VERIFIED** | `GET /api/v3/account/assets` (signed) | Response returned code 00000. |
| `positions` | **VERIFIED** | `GET /api/v3/position/current-position?category=USDT-FUTURES` (signed) | Response returned code 00000. |
| `collateral_type` | **VERIFIED** | `GET /api/v3/account/collateral-type` (signed) | Response returned code 00000. |
| `custom_collateral_coins_auth` | **VERIFIED** | `GET /api/v3/account/custom-collateral-coins` (signed) | Response returned code 00000. |
| `eligible_discount_rate` | **UNVERIFIED** | `GET /api/v3/account/eligible-discount-rate` (signed) | Endpoint not served to this key/environment (404); this is not evidence of a whitelist refusal. HTTP 404, code 40404: Request URL NOT FOUND |
| `fee_rate` | **UNVERIFIED** | `GET /api/v3/account/fee-rate?category=USDT-FUTURES&symbol=BTCUSDT` (signed) | Endpoint not served to this key/environment (404); this is not evidence of a whitelist refusal. HTTP 404, code 40404: Request URL NOT FOUND |
| `reality_depth` | **UNVERIFIED** | `GET /api/v3/account/reality-orderbook?symbol=RNVDAUSDT` (signed) | Endpoint not served to this key/environment (404); this is not evidence of a whitelist refusal. HTTP 404, code 40404: Request URL NOT FOUND |
| `reality_fills` | **UNVERIFIED** | `GET /api/v3/account/reality-fills?symbol=RNVDAUSDT` (signed) | Endpoint not served to this key/environment (404); this is not evidence of a whitelist refusal. HTTP 404, code 40404: Request URL NOT FOUND |
| `ws_account` | **VERIFIED** | private WS `account` topic | Login ok (s timestamp); received a 'account' push. |
| `ws_position` | **VERIFIED** | private WS `position` topic | Login ok (s timestamp); received a 'position' push. |

Machine-readable report: [`data/research/bitget_capabilities.json`](../data/research/bitget_capabilities.json).
<!-- AUTO:verify_bitget END -->
