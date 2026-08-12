# Budget Scenario

The forecast dashboard has a **Budget Scenario** panel (ad-set drill-down, below the
actual-vs-forecast tracking chart) that lets the user type a hypothetical new daily
budget and see the 14-day lead forecast move vs current pace.

Built across two sessions (2026-07-24):
1. Re-enabled a previously dead panel, added baseline-vs-scenario comparison.
2. Added **dated budget history**: an editable table (From / To / Daily budget) per ad
   set, stored in `ad_set_budget_periods`. The backend **fits spend→leads elasticity**
   from the user's own budget-change periods (weighted OLS of ln(leads/day) on
   ln(budget), clamped [0.20, 1.0], needs ≥2 differing-budget periods), and feeds it
   into the `spend_elasticity` knob (default 0.65) inside `get_forecast_scenario`.

**Key implementation notes:**
- Only the `spend_elasticity` parameter + one `current_budget_override` anchor were
  touched; the core prediction formula body is unchanged.
- The params-rebuild step resets elasticity to 0.65 — the fitted value must be
  re-applied **after** that call, else the fit is silently lost.
- Frontend fires two `/forecast-scenario` calls (baseline + scenario); a refresh key
  bump re-triggers both after save/delete.

See [[Budget-Optimization-Tab]] (reuses the same `ad_set_budget_periods` table) and
[[Leadlens-Ad-Export-Grain-And-Budget]] (the unresolved question of whether the budget
column is real history or a stamped current value).
