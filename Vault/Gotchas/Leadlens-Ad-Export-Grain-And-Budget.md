# Ad Export Grain & Budget Column

Shipped 2026-07-26 (plan was the now-deleted `UPLOAD_V2_PROMPT.md`). The Upload Data
tab accepts Meta's ad-level export.

**1. Grain.** Newer exports carry `Delivery level = 'ad'` (~2.9 rows per ad-set-day).
These roll up to ad-set grain in `_rollup_ad_rows_to_ad_sets()`. Before the fix,
naive deduplication kept one arbitrary ad row and silently discarded 44% of spend.
Aggregation is metric-specific: additive counters sum, **`Reach` is nulled rather than
summed** (it's a deduplicated person count), CPL/CPC recomputed from summed totals.
Grain is detected only from explicit signals (delivery level or a populated Ad ID) —
repeated grain keys deliberately don't count, since ad-set exports with
scientific-notation IDs collide by key for a different reason (see
[[Meta-Export-XLSX-Not-CSV]]).

**2. Budget.** `Ad Set Budget` becomes dated periods in `ad_set_budget_periods` (one
per contiguous run of the same value), `source='meta_export'`. Manual periods are
never overwritten; editing a derived period flips it to `manual`.

**Unresolved (may since be settled — check [[Budget-Optimization-Tab]], which found
the column IS a static snapshot):** on one export the column was constant per ad set
across all days while spend showed clear level shifts — a daily budget can't be
exceeded on average, so it looked like the *current* budget stamped onto every
historical row. Periods carry a `spend_conflict` flag for this — see
[[Budget-Conflict-Trailing-Window]] for how the flag's window was tuned.

**Why:** budget history feeds the Budget Scenario elasticity fit, so a wrong budget
silently corrupts a forecast input.

Related: [[Meta-Export-XLSX-Not-CSV]], [[Ad-Decision-Engine]], [[Budget-Scenario]],
[[Guessed-Adset-IDs-Duplicates]].
