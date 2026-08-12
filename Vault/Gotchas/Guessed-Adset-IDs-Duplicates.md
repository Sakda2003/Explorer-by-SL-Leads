# Guessed Ad-Set IDs Leave Duplicates

When an export with scientific-notation IDs was imported, the attribution repair
**guessed** each row's `ad_set_id`/`campaign_id` from the campaign name + lead history.
A later export carrying real IDs did not overwrite those guesses — the upsert key
includes both IDs, so a corrected row got inserted alongside the guess, and the guess
survived as an orphan that double-counted spend (and inverted a boost/cut verdict in
[[Ad-Decision-Engine]]).

**Fixed by `_remove_superseded_ad_rows()`**, run at the end of an ad-performance
import: deletes rows from other uploads for any campaign-day the incoming upload
covers. Two guards make it safe — only runs when the incoming file reports its own ad
set IDs, and is scoped to campaign-days that upload actually covers.

**Match on `campaign_name`, not `campaign_id`.** Joining on campaign ID silently did
nothing, because the repair guesses the campaign ID from the same lookup — the IDs on
a superseded row are precisely the fields that can't be trusted to join on.

**How to apply:** the count surfaces as `superseded_rows_removed` in the import
result. If unexpectedly large, check whether the export was filtered to a subset of ad
sets — a partial export would look like a supersede for campaigns it merely omitted.
Related: [[Meta-Export-XLSX-Not-CSV]], [[Leadlens-Ad-Export-Grain-And-Budget]].
