"""Fix during_holiday bucketing in the Cambodia holiday calendar.

Every date with is_holiday=1 should read holiday_proximity='during_holiday'. 14 single-day
holidays (Jan 1, Jan 7, Mar 8, May 5, May 14, Jun 18, Sep 24, Oct 15, Oct 29, Dec 29 x2,
Jan 7 '27, May 1 '27, Nov 9 '27) were left at their stale distance bucket instead -- the
multi-day holiday clusters (Khmer New Year, Pchum Ben, Labor Day weekend) were bucketed
correctly, so whatever produced this file simply skipped the isolated single-day holidays.

Scope: only the day-of bucket is corrected here. The 0_14_days / 15_30_days / 31_60_days
bands leading up to and away from these 14 dates were never computed either (confirmed by
inspecting the days around 2026-06-18: flat 60_plus_or_none right up to and after the
holiday, unlike the correct decay visible around Khmer New Year). Recomputing those bands
would mean reverse-engineering an inconsistent original algorithm (bridging behaviour differs
for a Friday holiday vs. a Thursday one) and is deliberately left out -- a bigger, separate
fix if wanted.

Fixes two files:
  - Dataset/Dataset/holiday_proximity.xlsx  (source; read by every build_*.py script)
  - data/holiday_proximity.csv              (live copy backend/core.py actually reads)
"""

import pandas as pd

SOURCE_XLSX = r"Dataset/Dataset/holiday_proximity.xlsx"
LIVE_CSV = r"data/holiday_proximity.csv"


def fix(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    frame = frame.copy()
    mismatched = (frame["is_holiday"] == 1) & (frame["holiday_proximity"] != "during_holiday")
    frame.loc[mismatched, "holiday_proximity"] = "during_holiday"
    return frame, int(mismatched.sum())


xlsx = pd.read_excel(SOURCE_XLSX)
xlsx_fixed, xlsx_n = fix(xlsx)
xlsx_fixed.to_excel(SOURCE_XLSX, index=False)

csv = pd.read_csv(LIVE_CSV)
csv_fixed, csv_n = fix(csv)
csv_fixed.to_csv(LIVE_CSV, index=False)

print(f"{SOURCE_XLSX}: fixed {xlsx_n} rows")
print(f"{LIVE_CSV}: fixed {csv_n} rows")

for path, frame in ((SOURCE_XLSX, xlsx_fixed), (LIVE_CSV, csv_fixed)):
    remaining = ((frame["is_holiday"] == 1) & (frame["holiday_proximity"] != "during_holiday")).sum()
    print(f"{path}: remaining mismatches = {int(remaining)}")
