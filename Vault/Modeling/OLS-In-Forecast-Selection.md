# How the Multivariate OLS Panel Relates to Per-Ad-Set Forecasts

Retrained 2026-08-04 (runs 190, 191) on request, after confirming no new data had
landed since the prior retrain (same `lead_events`/`change_events` counts both times —
a second retrain on unchanged data reproduces the same fit, which is correct, not a
sign the retrain silently failed).

**The "Multivariate OLS" panel in Dataset is a diagnostic report on
the declared 10 variables — it is not what every ad set's forecast uses.** See
[[OLS-Declared-Ten-Variables]]. Per-ad-set forecasts are chosen by backtest score
across many candidates (`train_models()`), and `multivariate OLS regression` is only
one of them. Run 191: it won for **3 of 29 ad sets** at both the 7-day and 14-day
horizon — `120235943617030078` (VISA | HK | FOR), `120244603714800078` (VISA | TH |
FOR), `120245239050210078` (VISA | ALL | KHM & FOR). Every other ad set's forecast
came from a different candidate (damped-trend momentum, adaptive weighted ensemble,
recent change-point level, spend-adjusted formulas, etc.) — see
[[Forecast-Flatness-Fix]] for the selection mechanism.

**Why this matters:** asking "does the new OLS result appear in the forecast" only has
a real answer per ad set, not portfolio-wide. The default-selected campaign shown
throughout this project's screenshots (VISA | AU | KHM) uses `damped-trend momentum` /
`adaptive weighted ensemble`, not OLS, at both horizons — that's an ad set OLS simply
didn't win backtest for, not a wiring problem.

**How to apply:** to check whether OLS is driving a specific ad set's forecast, query
`forecasts` for that `training_run_id` + `utm_ad_set_id` and read `model_used` — don't
infer it from Dataset's regression panel, which reports the
declared-variable fit quality, not model selection.
