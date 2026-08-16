# Retraining Per Edit: debounce it, and watch the GIL

Fixed 2026-08-14. Reported symptom: editing a single cell in the Dataset page's "Raw data"
board froze the whole screen for a long time before the change appeared.

## What was actually happening

Three separate costs stacked, and only the first was obvious:

1. **The lead endpoints retrained inline.** `update_lead_event()` / `delete_lead_event()`
   ended with `rebuild_aggregates()` + `train_models()`, both synchronous, inside the
   request. Measured here: **2.2s + 28.6s = ~31s per committed cell.** The ad-performance
   endpoints had already been moved to the background retrain guard (see
   [[Dataset-Page]]); the lead ones were simply never converted, and the comment above them
   in `app.py` said so out loud.
2. **The frontend blocked the entire board for that whole window.** `commitBoardField` set
   `boardBusy`, and `.board-scroll.is-busy` is `opacity: .65; pointer-events: none` — so
   every row went dim and unclickable for ~31s. That is the "whole screen froze" the user
   saw; the optimistic update had already painted the new value underneath it.
3. **Even after backgrounding, edits still cost ~200-400ms** — and this one is the
   non-obvious part. `train_models()` is numpy/pandas in the *same process* as the request
   handler, and it holds the GIL for long stretches. Since every edit scheduled its own
   retrain, an editing session kept a ~30s retrain permanently in flight, taxing each
   subsequent edit. **Backgrounding work does not isolate it from the event loop when the
   work is CPU-bound Python.**

## The fix

- `update_lead_event(..., retrain=False)` / `delete_lead_event(..., retrain=False)` write the
  row and return; the default stays `True` so non-interactive callers are unchanged.
- `_request_retrain()` gained a **debounce** (`RETRAIN_DEBOUNCE_SECONDS = 4.0`,
  `threading.Timer`, restarted on every call). A burst of edits now produces **one** retrain
  after the user stops, not one per edit — verified: 10 rapid edits → exactly 1 training run.
  This is what removed the 200-400ms tail, because no retrain is competing during the burst.
- `_retrain_needs_aggregates` flag: lead edits set it (they change
  `daily_ad_set_aggregates`, which is what the model reads); ad-performance edits don't
  (`rebuild_aggregates()` reads only `lead_events`), so they skip the extra ~2s. It's sticky
  until a run consumes it, so an edit landing mid-retrain still gets a rebuild from the
  queued follow-up pass.
- `retrain-status` reports pending-or-running as `running`, so the UI chip doesn't blink off
  during the debounce window.
- `BackgroundTasks` is gone from all 8 endpoints — the timer thread replaces it.
- Frontend: `commitBoardField` no longer sets `boardBusy` at all (the optimistic update is
  the feedback), and its error rollback is now **per-cell** rather than restoring a
  whole-page snapshot — without the busy lock, two edits can be in flight at once and a
  snapshot rollback would revert the other one's change.

## Two SQLite write-lock traps found while measuring

Both were "compute inside the write transaction", and both are worth not reintroducing:

- `rebuild_aggregates()` opened one `connect()` around the read, a ~2s pandas groupby, **and**
  the writes — holding SQLite's single write lock the whole time, so a concurrent edit blocked
  on `busy_timeout` for ~2.1s. Now read → compute → write, with one `executemany`.
- `refresh_forecast_realizations()` did the same across ~90k rows, plus ~90k individual
  `UPDATE`s. Now it computes outside the transaction, **skips rows whose `actual_leads`
  already matches** (a routine retrain changes a handful), and only takes the lock when there
  is something to write. `realized_at` is CSV-export-only — never read by logic or UI — so
  leaving it at the run that established the value is fine.

Both verified byte-identical against the previous implementation (942 aggregate rows,
104,384 realization rows) before and after.

## Result

| | before | after |
|---|---|---|
| Single cell edit | ~31,000ms | **~19-26ms** |
| 6 consecutive edits | 6 × ~31s, board dim throughout | 23-26ms each, board never dims |
| Retrains per 10 edits | 10 | **1** |

Verified live in the browser against port 8000: 913 samples across a 6-cell editing pass,
`.board-scroll.is-busy` never appeared once, retrain chip shown, no errors, all test edits
restored afterward.

**How to apply:** if an interactive write path ever feels slow again, check in this order —
(1) is `train_models()` or `rebuild_aggregates()` on the request path at all, (2) is the
frontend gating the UI on the response when an optimistic update already covers it, (3) is a
CPU-bound background job stealing the GIL, and (4) is any heavy compute sitting inside a
`with connect()` block that also writes. See [[Dataset-Page]] for the board itself and
[[Change-Event-UI-Recorder]] for the other user of this same guard.
