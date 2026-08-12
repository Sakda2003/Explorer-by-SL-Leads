# Ad-Set + Ad-Lookup Combine Script

`Dataset/Datsaa/combine_datasets.py`, built 2026-08-05, revised same day. One-off
script (not wired into the app) that merges `Ad-Set-Performance-*.xlsx` (ad-set×day
grain, no Ad ID) with `Dataset 06-06 - Current.xlsx` (lead-grain, has Ad ID + FB Ad
Title).

**Grain: one row per ad-set-day, not aggregated.** First version summed each ad set
into a single row across the whole Jun 6–Jul 31 period; user rejected that and asked
for day-by-day rows with a timestamp. Current version emits one output row per input
row from the performance file (879 rows) with all metrics taken as-is from that day
(no re-summing/recomputing) plus a `Day` column.

**Why not explode per ad instead:** an ad set can carry 5-9 different Ad IDs over its
lifetime (confirmed via the `ID Lookup` sheet — 17 of 30 ad sets had duplicate Ad Set
ID keys), but the performance file's Reach/Impressions/Budget are ad-set-level, not
per-ad. Exploding would duplicate those metrics once per Ad ID sharing that ad set.

**Ad ID / FB Ad Title are date-aware, not a static "latest" value.** For each
ad-set-day row, the script builds a per-ad-set timeline of leads (`Created At` in the
`traffic_2026-08-01` sheet, sorted) and picks whichever ad was current as of that
day — the most recent lead on or before that date, or the earliest lead if the day
predates all of them. Falls back to the first `ID Lookup` match only if the ad set has
zero leads in the traffic sheet. Verified against ad set `120235942906970078`: shows
`VF008C1` on Jun 6, `VF008E1` mid-run, `VF008E2` by Jul 31 — matches the actual lead
timeline for that ad set.

**Cost Per Lead uses real lead counts, not Meta's.** Verified against the four
`Dataset Template/Dataset <range>.xlsx` model-dataset files (their `ad_set_days`
sheets tile Jun 6–Jul 31 exactly, no gaps/overlaps): Reach, Impressions, Frequency,
Budget, Budget Type, Campaign Name/ID all matched Meta's export perfectly across all
879 rows, but CPL differed on 693 of them — Meta's own `Cost per lead` column divides
spend by Meta's `Leads` count, which is the same unreliable lead column flagged in
[[Ad-Decision-Engine]]. Fixed by recomputing `Cost Per Lead = Meta spend ÷ real lead
count`, where real lead count comes from the templates' `ad_set_days.leads` field
(built from actual matched lead records). Reverified against the templates' own `cpl`
column after the fix: 0 mismatches across all 879 rows.

Output: `Dataset/Datsaa/Combined-Ad-Set-Dataset.xlsx`, 879 rows (18 ad sets × their
active days within Jun 6–Jul 31; not every ad set ran the full period).

**Weekly split.** `Dataset/Datsaa/Dataset Template/split_combined.py` slices the
combined file by `Day` into 4 non-overlapping ranges: 06-06–07-10, 07-11–07-17,
07-18–07-24, 07-25–07-31 (542/113/112/112 rows), written to
`Dataset Template/Partial Dataset/`.

**Columns: Amount Spent (USD) and Leads.** Added 2026-08-05, placed right before Cost
Per Lead. `Amount Spent` is Meta's own spend figure (reliable); `Leads` is the same
real lead count used to compute Cost Per Lead (see above) — not Meta's Leads column.

**Source file reorganization (2026-08-05):** Sakda moved the real-leads source files
into `Dataset Template/Full Information Dataset/` and renamed/re-bounded them to
match the split's non-overlapping ranges exactly (06-06–07-10, 07-11–07-17,
07-18–07-24, 07-25–08-01 — previously they overlapped by a day at each boundary).
`combine_datasets.py`'s `REAL_LEADS_FILES` paths were updated to match; if this
folder gets reorganized again, that constant is the only thing to fix.

Related: [[Leadlens-Ad-Export-Grain-And-Budget]], [[Meta-Export-XLSX-Not-CSV]],
[[Ad-Decision-Engine]].
