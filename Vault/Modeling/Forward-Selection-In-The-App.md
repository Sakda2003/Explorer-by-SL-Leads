# Forward Selection In The App

The Multivariate OLS card does not fit "every declared variable that varies". It fits whatever
`_forward_select_declared_features` (`backend/core.py`) selects — a greedy forward search over
the declared drivers, added 2026-08-13 and given a second entry gate plus a visible trace
2026-08-16. This is the app-side counterpart of [[Forward-Selection-Notebook]]; that note is
about the two standalone notebooks, this one is about the code the live page runs.

## What the search does

Candidates are **whole declared variables**, not columns: `DECLARED_OLS_GROUPS` maps each of
variables 2–8 to the feature columns it is expressed through, so Holiday_Proximity's four
buckets and the seven weekday indicators are one candidate each. Selecting individual dummies
would fit leaner but leaves the declared-variable displays reporting partial credit
("Wednesday but not Thursday"), which is not a statement the declared framework can make.

Each round fits every remaining candidate on top of the model so far and keeps the best one.
A candidate enters only if it clears **both** gates:

- **Adjusted R² gain > `FORWARD_SELECTION_MIN_GAIN`** (1e-6, a floating-point-noise floor).
  Adjusted R² also decides the ranking within the round.
- **Block F test p < `FORWARD_SELECTION_MAX_P`** (0.10), added 2026-08-16.

## Three things that were not obvious

- **The p-value gate is doing real work, because adjusted R² alone is a very weak filter.**
  Adjusted R² rises whenever the block F exceeds 1, and F = 1 sits around p ≈ 0.32 — so the
  old adjusted-R²-only rule would admit any variable in the whole F ∈ (1, 2.7) band, which is
  exactly the band where a term is indistinguishable from noise. Observed live at campaign
  scope: `Spent` was refused in round 2 at +0.0176 adjusted R², p = 0.104, then earned its
  place legitimately in round 3 at p = 0.018 once `frequency` was in.
- **The p-value has to be a partial F on the block, not a t test on a column**
  (`_partial_f_p_value`). A seven-column weekday block carries seven t statistics and not one
  of them asks "does day-of-week belong here". Δdf is the *rank* the block adds, not its
  column count — the seventh weekday indicator is the intercept's exact complement and adds
  nothing, so day-of-week is tested on 6 df.
- **0.10, not 0.05, and deliberately so.** Greedy search picks the best of up to seven
  candidates each round, so the winner's nominal p-value is optimistically biased and reading
  it at 0.05 would be false precision. It is a junk filter, not a certificate.

## The backward glance

After each addition the search re-tests everything already selected and gives a seat back if
the model is better without it (`try_drop`), which pure forward selection cannot do. Both
moves strictly raise adjusted R², so the walk terminates; a round cap exists purely as a
backstop and should never bind. **Honest caveat: this has not been observed firing** on this
data or in any synthetic case built for it — with adjusted R² as both the add and drop
criterion, the situations where it triggers are narrow (suppressor structures). It is a
safety net, not a load-bearing feature.

## Where it renders

`get_ols_model_summaries` returns a `selection` block (method, alpha, min_gain, order, steps,
final R²/adjusted R²) alongside the fits. `OlsSelectionPath` in `frontend/src/App.tsx` renders
it behind a "Show selection path" toggle on the Multivariate OLS card, on **both** the Forecast
page and the Dataset page (one shared `OlsResultCards`, one shared `/api/ols-summary` payload).
Each round lists every candidate tried — R², adjusted R², Δ adjusted R², block p, outcome — not
just the winner. Rejections are also reported per-variable in the Variable dictionary via
`_declared_variable_coverage`, measured against the **final** model rather than whichever round
the variable lost in, since a variable's marginal value depends on what else ended up in the fit.

Portfolio scope today (2026-08-16, 70 days, 30 ad sets): 2 rounds, `Spent` then
`ad_change_recency`, adjusted R² 0.497 → 0.511. Campaign scope on VISA | AU | KHM: 4 rounds,
`days_since_adset_started` → `frequency` → `Spent` → `Holiday_Proximity`, 0.284 → 0.424.

## The forecast path does not use this, and that was measured — 2026-08-16

`_ols_forecast` calls `_select_multivariate_ols_features` (every declared variable that varies
and adds rank) with the ridge penalty scaled to that feature count. The selection above is
**diagnostics only**. The switch exists — `OLS_FORECAST_USES_FORWARD_SELECTION` in `core.py`,
one constant, both branches live and tested — and it is **False** because forward selection was
backtested against the incumbent and forecast worse.

`backtest_forward_selection.py` (project root, read-only, ~2.5 min, writes
`output/forward_selection_backtest.json`) runs four configurations through the real per-ad-set
pipeline `_forecast_for_series`, so the rolling-origin cutoffs, the shape post-processing and
the model competition are production's, not a hand-rolled loop's. Multivariate OLS candidate,
14-day pooled WAPE across all 30 ad sets:

| config | 14d pooled | 14d median | terms |
| --- | --- | --- | --- |
| all-declared + ridge (shipping) | **51.0%** | 70.6% | 9.1 |
| forward + ridge | 58.0% | 69.8% | 1.4 |
| forward, no ridge | 59.6% | 69.7% | 1.4 |
| all-declared, no ridge | 56.5% | 82.4% | 9.1 |

**Why selection loses here.** On **34% of backtest windows (55 of 164) nothing clears the entry
gates at all** — no declared variable is individually significant on 14–40 days of noisy counts.
The all-declared model still pulls a weak signal out of many shrunken coefficients on those
windows, which is exactly what the ridge is for; hard selection discards it. Same lesson as
[[Forecast-Flatness-Is-The-Data]]: on this data regularise-everything beats select-then-fit.
Note the two are not in conflict with the card — a diagnostic is allowed to say "nothing is
significant", a forecast still has to produce fourteen numbers.

> [!warning] The survivorship trap that nearly sold the opposite conclusion
> An empty selection originally raised, `_rolling_origin_backtest` swallows exceptions, and the
> model was therefore scored **only on the 66% of windows where it found signal**. That read as
> 46.3% pooled — a 4.5pp "win" over the incumbent, entirely an artifact of the model being
> allowed to skip its hard windows. `_ols_forecast` now returns the intercept (the series mean)
> when selection is empty, which keeps `_forecast_candidate` total and forces every window to be
> scored. **Any re-test must keep that property or it will lie the same way.** Falling back to
> the spend line instead of the intercept was also tried: 56.8%, still worse than the incumbent.

One thing that would improve regardless of WAPE, and is the honest argument for revisiting this
later: fewer terms means thin ad sets clear the `max(12, terms + 6)` observation floor and get a
multivariate fit at all. Re-run the harness when the dataset is materially larger — the greedy
path and the significance gates both get more reliable with more days.

**Why:** the user asked for forward selection that reports R², adjusted R² and p-values at every
step, so the chosen variable list stops reading as an unexplained verdict — and then asked for it
to drive the model, which is why the backtest above exists.
**How to apply:** `build_regression_report.py` calls the same selector, so the standalone report
and the app can never disagree. If you change a gate constant, expect both to move. The card and
the forecast describe **different models on purpose** — don't "fix" that without re-running
`backtest_forward_selection.py`. See [[Per-Ad-Set-Regression-Report]] and
[[OLS-In-Forecast-Selection]].
