# Forecast Flatness Fix (2026-07-23)

Earlier, unrelated flatness bug — see [[Forecast-Flatness-Is-The-Data]] for the later
2026-08-03 investigation into the *remaining* (correct) flatness.

LeadLens produced flat forecasts vs volatile actuals. Root cause: `_selected_metric` in
`backend/core.py` unconditionally returned the ensemble whenever it had backtest
windows, so the selection score (including the flatness penalty) was never actually
used; ensemble averaging of differently-shaped components also cancelled daily
variance.

**Fixes applied:** ensemble only wins within a small tolerance of the best model's
score; ensemble rescales blended deviations to the weighted component std; flatness
penalty threshold and weight both raised; change-point weekday clip widened.

**Result on live data:** median selected WAPE 0.869 → 0.706, model mix went from 100%
ensemble to a diverse mix (change-point, rolling, weekday models).

**Round 2 (same day):** added a damped-trend momentum candidate and top-down portfolio
reconciliation. **Critical lesson:** per-day reconciliation ratios flattened the shape
again, because the top-down model was flat — reconciliation must be **level-only** (one
scalar ratio over the whole horizon), never per-day.

**Server must be restarted after editing `core.py`** — the launch config has no
`--reload`.
