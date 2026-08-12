# Master Dataset

Built 2026-08-01 by `build_master_dataset.py` → `Dataset/master_dataset.xlsx`. Grain is
**ad set × day**, joining the Meta ad-set performance export, the CRM traffic export,
and `holiday_proximity.xlsx`.

**Non-obvious derivations:**
- `leads` comes from the CRM traffic export, counted by UTM Ad Set ID × Created At date
  — not Meta's own Leads column (2,919 vs 526 for Jun 6–Jul 31 2026). See
  [[Ad-Decision-Engine]].
- Ad-set changes are **not stated anywhere** in the exports — `Ad Set Budget` is static
  per ad set (see [[Budget-Optimization-Tab]]). They're inferred from 7-day
  trailing-vs-leading step shifts: spend >35% = budget_change, CPM >35% = bid_change,
  frequency >12% = targeting_change.
- `placement_change` has **no direct signal** — approximated by a CPM step >25% moving
  with a frequency step >8% within 3 days while spend holds (fires 4 times over the
  window). This is a signature guess, not a real placement field.
- Ad-level changes are inferred from UTM Ad ID lead activity in the CRM (no ad-level
  performance export exists): first lead day = ad_added, day after last lead + ad set
  alive ≥10 more days = ad_paused, add+pause within 2 days = ad_swapped.
- `msg_template_change` is weak — the only real signal is a republished creative code
  (5 events total).
- `days_since_adset_started` is left-censored for ad sets already live on 2026-06-06
  (`adset_start_censored=1` marks them).

**Why this matters:** these five variables look like recorded facts but are all
reconstructions. Treating them as ground truth overstates model confidence.

**How to apply:** when the forecast uses change features, check the detection
thresholds in `build_master_dataset.py` before blaming the model. Don't expect
`placement_change` to ever be reliably non-null. A trustworthy value needs either a
placement-segmented export or the manual change log — see [[Model-Dataset-Template]].

**Left-censoring fix shipped 2026-08-06** (pinned 2026-08-06, closed same day). A
confirmed launch date can now be recorded per ad set — see the "Start date" tab in
[[Change-Event-UI-Recorder]] and the new `ad_set_start_dates` table. When present, it
overrides the `date - earliest uploaded date` estimate in `_days_since_start_values()`
for that ad set.

**Superseded same day — the detectors this note describes are gone from the live app.**
Everything above (the 7-day step-shift rules, the placement-change signature guess, the
ad-level UTM-activity inference) still describes what `build_master_dataset.py` does for
the offline `Dataset/master_dataset.xlsx` export, unchanged. But `backend/core.py` — what
actually feeds the running app's correlation matrix, OLS fit, and forecast model — no
longer has any of it: the user found the inferred values wrong, and rather than leave a
fallback in place, `_days_since_start_values`, `_ad_change_features`,
`_ad_set_change_features` were cut down to **confirmed rows only** (2026-08-06, see
[[OLS-Declared-Ten-Variables]]). An ad set with nothing recorded no longer falls back to
the earliest-upload-day estimate — it reports 0 / no event, which the app's zero-variance
handling then drops from the matrix and the fit entirely rather than showing a censored
guess as if it were real. `build_master_dataset.py` was not touched and still produces
its own inferred columns; the two pipelines have diverged on purpose and are no longer
expected to agree on these five variables until real data replaces both.

**Encoding rebuilt to match the app's point-event semantics, 2026-08-07** (detector logic
untouched -- see below). `build_master_dataset.py`'s `attach()` used to carry a detected
event's type forward via `merge_asof`'s natural backward-fill behavior, the same design the
app used before the same-day redesign in [[Change-Event-UI-Recorder]] -- and it had the same
failure mode: an ad set's very first detected step-shift could end up constant across its
entire history. The day-0 seed (`taken.setdefault(d0, "budget_change")`, one artificial
"budget change" asserted on every ad set's literal first calendar day, purely so the old
carry-forward encoding always had *something* to carry) is gone along with it -- under point
semantics a day without a detected shift is legitimately `no_change`, matching
`NO_CHANGE_LABEL` in `backend/core.py`, not a gap needing a fabricated value. `attach()` now
only writes a type on the exact day `_ev_date` matches; recency is unaffected (still counts
up from the most recent event, unchanged). Effect on the real Jun 6 -- Jul 31 export: 836/879
rows now read `no_change` for `ad_set_change_type` (previously every row carried a detected
or seeded type), 43 real event-days spread across the four types. Spot-checked one ad set
with 5 detected bid/budget events: each fires on exactly its own day, recency resets to 0 at
each and counts up between them, confirmed correct.

**What did NOT change: the detector itself.** The 7-day step-shift rules, the
placement-change signature guess, and the ad-level UTM-activity inference described above are
untouched and still the same inferred proxies, not confirmed facts -- this was only a request
to match the *encoding* (point-in-time vs. carry-forward), not to replace the detector with
the app's confirmed-only change log. The two pipelines still diverge on *what* counts as a
change (detected vs. recorded), just no longer on *how* a detected/recorded change is written
into daily rows. See [[OLS-Declared-Ten-Variables]] for the app-side redesign this mirrors.
