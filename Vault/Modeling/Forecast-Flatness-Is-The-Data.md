# Forecast Flatness Is The Data

The LeadLens forecast draws as a near-flat line at daily ad-set grain, which looks
obviously wrong next to volatile actuals. **Three real bugs were hiding behind it, all
fixed 2026-08-03; what remains is the data, not a defect.**

**The bugs** (all in `backend/core.py`):
- **Trailing partial day.** A mid-morning export left the final day ~40% collected but
  counted whole, dragging every trailing average down and putting a cliff on the
  actuals line. Now excluded from the modelled series.
- **Flat-path shaping matched a model name.** Only one specific model name got weekday
  shaping, so several other constant-returning models shipped dead flat while carrying
  a good weekday factor (11 of 29 ad sets). Now tested on the produced path directly.
- **Dead ad sets borrowed campaign volume.** The fallback forecast gave 80% weight to
  campaign/portfolio averages, so zero-spend ad sets with 1-2 lifetime leads forecast
  up to 34 leads. Borrowing weight is now gated on spend.

**Then the flatness is correct.** Measured six independent ways — do not re-open
without new data:
- 88.5% of daily per-ad-set error is irreducible: an oracle knowing the true 14-day
  level still scores WAPE 0.557 vs the model's 0.630.
- Oracle level *plus* perfect weekday shape is **worse** than oracle level flat.
- Weekday explains only 11% of daily variance (median), 3.8% on the largest ad set.
- Feeding true future spend: +0.8% via the OLS, -4.1% accuracy via a leads-per-dollar
  path (shape is purchasable, but only by paying accuracy).
- Pooling all ad-set-days with fixed effects is 5.2% worse than per-ad-set fits.
- Paired test, declared-10 OLS vs flat trailing mean: statistically tied (t = -0.15).

**Ridge tuning is a trap here.** An apparent sweep optimum vanished under the mean and
under both split-halves independently — a median artifact. The per-feature ridge
penalty stays at 10; always check mean + split-half before adopting a hyperparameter
here.

**Why:** every route to a shapelier line costs accuracy — a flat line at the
conditional mean is optimal when day-to-day movement is unpredictable.

**How to apply:** the binding constraint is ~56 days of history against 14 regressors,
not model configuration. Real gains come from coarser grain (same forecast scored
weekly is 0.63 → 0.38) or more data. Daily stays the headline metric per user
preference, so the grain fix isn't available. See [[OLS-Declared-Ten-Variables]] for
the model this was measured against, and [[Forecast-Flatness-Fix]] for the earlier,
separate bug this is not the same issue as.
