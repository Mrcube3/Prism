# Running PRISM

Everything here is read-only: no order, transfer or setting is ever sent to Bitget.

## Where it runs

Beam VPS `ubuntu@16.61.50.207`, project in `~/prism`, two systemd services capped so they cannot starve Beam:

| Service | What | Caps |
| --- | --- | --- |
| `prism-capture` | Closure-window market capture ([CAPTURE.md](CAPTURE.md)) | 25% CPU, 300 MB |
| `prism-api` | API and mission-control page on `172.18.0.1:8790` (Docker bridge only; UFW allows 172.18.0.0/16 to that port) | 50% CPU, 400 MB |
| `prism-validate.timer` | Hourly: `scripts/validate_reality.py` updates `docs/VALIDATION.md` and `data/research/validation.json` from captured closures | oneshot, lowest priority |
| `prism-caddy-attach.timer` | Every 5 min, re-attaches the PRISM site to Beam's Caddy if it is missing (e.g. after a Caddy restart) | oneshot |

## Public site

**https://app.getprismpulse.xyz** — DNS: Namecheap A record `app` → `16.61.50.207` (the `@`/`www` records point elsewhere and are untouched).

- HTTPS comes from Beam's Caddy (Let's Encrypt). Beam's Caddyfile is a read-only bind mount and Beam's files are not edited. `deploy/caddy_attach_prism.sh` loads *Beam's current running Caddyfile + the PRISM block* from Caddy's `/config` volume and is idempotent. To detach PRISM: disable the timer, then reload Caddy with `/etc/caddy/Caddyfile`.
- Note found on 2026-10-07: Beam's running Caddy still uses an older Caddyfile than `/opt/beam/deploy/Caddyfile` (the host file had been replaced after the container started; the new security headers are not live). Left as is; it is Beam's to redeploy.
- Public visitors: Judge Mode and the Bitget (demo) account view are both open (password removed at the owner's request, 2026-10-07; setting `PRISM_ACCOUNT_PASSWORD` in the server `.env.local` re-enables it). Ask PRISM is limited to 6 per visitor per 10 min and 60 per hour overall; Repair to 20 per visitor per 10 min. Requests through the SSH tunnel are unrestricted.

## Private access (tunnel)

From your laptop, open a tunnel and leave it running:

```bash
ssh -i ~/.ssh/beam_vps -N -L 8790:172.18.0.1:8790 ubuntu@16.61.50.207
```

Then browse to http://localhost:8790. Switch **account** between *Judge Mode (hypothetical)* and *Bitget account* (your demo key).

## Surfaces

| Surface | Endpoint | Notes |
| --- | --- | --- |
| Dual Reality Ledger | `GET /api/workbench?account=judge\|bitget` | Baseline plus mild/central/severe stress scenarios, wrong-way report |
| Thaw Frontier | `GET /api/frontier` | 7×7 grid; Blind-Zone cells flagged; per-cell attribution |
| Pre-Trade Gate | `POST /api/pretrade {text, account}` | Qwen parses, deterministic engines decide, Qwen explains (number-checked). About 30 s |
| Repair Solver | `POST /api/repair {account, scenario, target, objective}` | Add USDT / reduce perp / sell rToken / mixed, with order-book cost |
| Research | `GET /api/research/{NVDA}` | Bitget US-stock MCP; returned 503 on 2026-10-07, shown as UNAVAILABLE |
| API docs | `/api/docs` | |

CLI equivalents: `scripts/run_shadow.py`, `scripts/verify_bitget.py`, `scripts/capture_weekend.py --once`.

## Updating the server

From the repo root on the laptop:

```bash
tar --exclude=node_modules --exclude=.next --exclude=dist --exclude=.wrangler --exclude=.vinext --exclude=.sites-runtime --exclude=.venv --exclude=__pycache__ --exclude=data/raw --exclude=data/capture --exclude=.git --exclude=.env.local -czf /tmp/prism.tgz . && scp -i ~/.ssh/beam_vps /tmp/prism.tgz ubuntu@16.61.50.207:/tmp/ && ssh -i ~/.ssh/beam_vps ubuntu@16.61.50.207 'tar -xzf /tmp/prism.tgz -C ~/prism && cd ~/prism && ~/.local/bin/uv sync -q --project services/api && sudo systemctl restart prism-api prism-capture'
```

The update excludes `.env.local`, so the server's secrets and password are kept.

## Known limits

- Judge Mode uses each rToken's last traded price as a stand-in for Bitget's recognized collateral price (Q-RECOGNIZED-PRICE).
- Stress scenarios are fixed, user-adjustable shocks, not Reality-Engine outputs (M3 not built).
- Repair costs include the taker fee where Bitget returns it (`account/all-fee-rate`); rToken spot fees are unavailable in demo and shown as such. Initial margin is not checked.
- History: `GET /api/history/{reconciliation|snapshots|pretrade}` reads the append-only SQLite store `data/prism.db`.
- Thaw experiment: `deploy/prism-thaw.timer` runs `scripts/capture_thaw.py` around 20:00, 04:00 and 09:30 New York; results in `docs/THAW_EXPERIMENT.md`.
- Stress band: `scripts/backfill_closures.py` (also refreshed daily by `prism-validate`).
- With Bitget's real BTC MMR, the shadow core ratio stays low until equity is nearly gone (Q-MMR-SCALE), so breaches appear abruptly on the frontier.
