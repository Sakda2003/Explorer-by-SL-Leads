Fixed 2026-08-06. The shared `money()` formatter in `frontend/src/App.tsx` used
`maximumFractionDigits: 0`, so every dollar amount rendered through it (Amount
Spent column in Combined export, spend totals, budgets, chart labels) rounded
to whole dollars — e.g. $7.62 showed as $8.

Changed to `minimumFractionDigits: 2, maximumFractionDigits: 2` to match
`cplMoney()`'s precision. `money()` is used in ~25 places across the app, so
this one change restored cents everywhere consistently rather than needing a
per-callsite fix.
