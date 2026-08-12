# Always Take Meta Exports As XLSX, Not CSV

The CSV form arrives with `Campaign ID` / `Ad set ID` / `Ad ID` already converted to
Excel scientific notation (`1.20249E+17`), keeping only 6 of 17 significant digits. In
one real export that collapsed 19 real ad sets into 6 distinct values, one spanning 7
different campaigns. The precision is destroyed in the file itself and can't be
recovered by parsing. CSV spend also arrives as `" $0.05 "` / `" $-   "`, which the
number parser can't handle (strips commas/% but not $), so every row fails as missing
spend.

The XLSX version has full-precision int64 IDs, native float spend, real datetimes —
the whole ID-recovery repair path becomes a no-op.

**Why:** attribution goes from inferred-by-campaign-name to exact.

**How to apply:** ask for XLSX when a new ad export arrives. Keep the CSV path working
for robustness, but treat XLSX as canonical. Related:
[[Guessed-Adset-IDs-Duplicates]], [[Leadlens-Ad-Export-Grain-And-Budget]].
