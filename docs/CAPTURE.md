# Closure-window capture

Continuous read-only collection for the thaw experiment (AGENTS.md §47) and historical validation (§48, §77). Code: `services/api/prism/capture/`, entry point `scripts/capture_weekend.py`.

## What is collected

| Stream (`data/capture/<UTC date>/`) | Cadence | Source |
| --- | --- | --- |
| `tickers.jsonl` | 60 s | `market/tickers` for 14 weekend-tradable rTokens (SPOT) and BTC/ETH plus NVDA/TSLA/COIN/MSTR stock perps (USDT-FUTURES: index, mark, funding) |
| `orderbooks.jsonl` | 60 s | public `market/orderbook`, 15 levels, rTokens only (not the whitelisted Reality book, Q-DEPTH) |
| `candles_1m.jsonl` | 15 min | last 20 one-minute candles per symbol |
| `session.jsonl` | 15 min | `reality/market/states` and `calendar` |
| `account.jsonl` | 5 min | signed `account/assets` summary, labelled with `account_environment` (currently `DEMO_PAPTRADING`) |
| `collateral_tiers.jsonl` | daily | public `market/discount-rate` |

Every row carries `meta` (source endpoint, retrieval time, provider time, raw payload hash, capture version, ok/error). Failed requests are recorded as failures, never filled in. Every ticker row also carries the U.S. session phase under **both** time-zone readings (New York local and fixed UTC−5) because Bitget's labelling is ambiguous (Q-TZ). Raw responses go to `data/raw/`. Finished days are gzipped. Both directories are git-ignored.

## Running

```bash
uv run --project services/api python scripts/capture_weekend.py --once
```

## Production host

Runs on the Beam VPS (`ubuntu@16.61.50.207`, `~/prism`) as the systemd unit [`deploy/prism-capture.service`](../deploy/prism-capture.service), capped at 25% CPU and 300 MB memory, at lowest CPU and I/O priority, so it cannot starve Beam. Started 2026-10-07 00:33 UTC.

```bash
ssh -i ~/.ssh/beam_vps ubuntu@16.61.50.207 'systemctl status prism-capture --no-pager; cat ~/prism/data/capture/heartbeat.json'
```

## Not yet captured

- The rToken **collateral** index that Bitget's UTA uses. No verified endpoint exposes it, so thaw timing must come from account `usdValue` changes for a held rToken. That needs a production account that holds one.
- Whitelisted Reality depth and fills.
