# Codex review queue

Unresolved review items for the adversarial reviewer (AGENTS.md §72, §82). Add items; mark them resolved with evidence instead of deleting.

## Ready for review: M0/M1 (2026-10-06)

Scope: `services/api/prism/**`, `scripts/verify_bitget.py`, `docs/BITGET_VERIFICATION.md`, `docs/OPEN_QUESTIONS.md`.

Items the implementer suggests attacking first:

1. `verify_bitget.py` classification: `VERIFIED` means "returned code 00000", not "schema matches spec". Signed rows came from the demo environment with an empty account. Is the doc clear enough that this does not verify production behaviour or field schemas?
2. `sanitize()` redaction list in `prism/provenance/raw_store.py`: are any identifying fields in the account/settings payloads missed? (`data/research/bitget_capabilities.json` is intended to be committable.)
3. WebSocket login tries a seconds timestamp, then milliseconds. Confirm this cannot leak credentials in error output.
4. `Provenance` validation: does any path allow a demo-environment value to be labelled `DataMode.LIVE`? (No account values are normalized yet, but M2 must enforce this.)
5. The existing TypeScript app (`app/`, `src/`) still contains the pre-spec "Decision Case" flow and an uncommitted Qwen research route. Check it against §1.3/§41: the route asks Qwen for free-form thesis/claims, with no tool contract yet.

## Ready for review: M2/M4/M5 engines and Qwen guards (2026-10-07)

Scope: `prism/account`, `prism/collateral`, `prism/reconciliation`, `prism/shadow`, `prism/workbench.py`, `prism/connectors/qwen`, `scripts/run_shadow.py`.

1. `reconcile()` scores 4 effEquity hypotheses (tier mode × upnl). Can an account match a wrong hypothesis by coincidence, and should RECONCILED require a unique match?
2. Shadow MM rescales linearly with mark and ignores position-tier changes (flagged). Is this conservative in every direction? It is not for shocks that reduce notional.
3. `unsupported_numbers()`: can Qwen slip a number past the guard (spelled-out numbers, dates, ranges like "5-10%")?
4. Judge Mode baseline effEquity is computed by PRISM from hypothetical holdings, so it is internally consistent by construction. Make sure the UI never presents it as reconciled.
5. `factor_for()` puts stock perps on the rToken axis. Is that right for perps that trade 24/7 and have no frozen index?

## Ready for review: M3 Reality Engine (2026-10-07)

Scope: `prism/reality/{engine,history,live}.py`, `GET /api/reality`, `GET /api/reality/{symbol}/series`, `tests/test_reality.py`, UI wiring in `api/static/index.html`.

1. Reference proxy A1: the rToken's 1H candle close at the last 20:00 New York boundary stands in for Bitget's frozen collateral index. How wrong can this be (rToken vs underlying basis at the close)? Validate against the thaw experiment.
2. Thresholds (75 / 200 bps agreement, 0.5% minimum move, 1.5x overshoot, 500-observation history minimum) are hand-set and documented, not learned. Revisit once closure windows accumulate (§24, §49).
3. Live-regime reference agreement compares price levels only when they are within 5%. Is that sufficient to rule out share-ratio mismatches?
4. `closure_windows()` counts a window when the capture holds hours 20–23 New York and data after 04:00 next day. Weekend windows count once per day, not once per weekend.
5. Liquidity percentiles mix overnight, weekend and regular-session books; closure-only percentiles would be more faithful to §24.
6. Yahoo (unofficial) now drives the reference proxy and live agreement. Check the 16-hour session guard in `underlying_reference()`, delayed-quote risk, and that the UI labels the source wherever it appears.
7. SEC 8-K window = reference time − 3 days. Does a stale 8-K wrongly turn an unrelated weekend move into DISCOVERY (`EVENT_SUPPORTED`)? Consider requiring the filing to fall after the last regular close.

## Review outcomes (2026-10-07)

Fixed (regression tests in `services/api/tests/test_audit.py`; 78 tests pass):

| Item | Finding | Fix |
| --- | --- | --- |
| Engines #3 | Spelled-out quantities ("five percent", "two million dollars") passed the explainer's number check. | Number words next to a unit are always rejected. |
| Reality #4 | A weekend closure was counted once per calendar day, inflating "data support". | Windows are counted once per 20:00 boundary and only after the following reopen is observed. |
| Reality #7 | An 8-K filed days before the frozen reference could mark an unrelated weekend move as event-supported DISCOVERY. | Window now starts at the regular close (reference − 4 h); earlier filings are already priced into the reference. |
| New | Pre-Trade Gate netted a proposed long against an existing short even in **hedge mode** (the demo account's mode). | Same-direction merge only in hedge mode; one-way mode still nets. |
| New | `/api/reality` symbols flowed into Yahoo's URL path with only a prefix/suffix check. | Strict `R[A-Z0-9]{1,10}USDT` / ticker patterns. |
| New | `/api/frontier?boundary=NaN` (or absurd values) raised a 500. | Validated to 0.05–5. |
| New | Pre-Trade text was unbounded (Qwen cost) and the response cache and rate-limit table grew without limit under public traffic. | Text 3–500 chars; cache capped at 500 entries; idle visitors pruned. |

Checked, no change needed:

- M0/M1 #1–#4: the `VERIFIED` = "returned 00000" caveat is stated in BITGET_VERIFICATION.md. `sanitize()` covers the identifiers seen in real payloads; the published report holds field paths and permission scope only. WebSocket errors carry the exception type and message, never the signed payload. Demo data is rejected if labelled LIVE (tested).
- M0/M1 #5: the old TypeScript app is not part of PRISM's published repo.
- Engines #1: several hypotheses matching at once still means PRISM reproduced Bitget's number; RECONCILED stays, with the ambiguity reported in the reasons.
- Engines #4: Judge Mode's baseline is labelled `HYPOTHETICAL`, never RECONCILED.
- Reality #6: Yahoo is used only within a 16-hour session guard and a 15-minute freshness limit, labelled "unofficial" in the UI and the provenance.

Still open (design limits, not bugs):

- Engines #2: MM rescales linearly and ignores tier jumps (flagged as CONSERVATIVE_APPROXIMATION in every result).
- Engines #5: stock perps share the rToken/equity axis although their marks trade 24/7.
- Reality #1–#3, #5: proxy accuracy, hand-set thresholds and session-mixed liquidity percentiles all need closure history; revisit with the thaw experiment and M11 validation.
