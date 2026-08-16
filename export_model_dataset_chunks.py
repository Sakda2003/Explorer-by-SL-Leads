"""Export the live app's data (data/leadlens.db) as a series of `model_dataset` upload
workbooks, chunked by date, for re-uploading into the deployed LeadLens instance.

Each workbook has two sheets, matching what backend/core.py's `read_model_dataset_workbook`
actually accepts:
  - model_dataset: lead-grain (one row per lead), columns per MODEL_TO_TRAFFIC.
  - ad_set_days:   ad-set x day grain (spend/reach/etc even on zero-lead days), columns per
                   MODEL_TO_AD. Required so spend on zero-lead days isn't lost -- see
                   Vault/Gotchas/Zero-Lead-Days-Vs-Padding.md.

Chunking: first chunk 06-06 to 07-11 inclusive, then weekly chunks (Sat-Fri, matching the
existing Dataset/Datsaa split convention) through the last date present in the DB.
"""

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, ".")
from backend import core  # noqa: E402  (needs sys.path set first)

DB = r"data/leadlens.db"
OUT_DIR = Path("output/model_dataset_export")
OUT_DIR.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(DB)

leads = pd.read_sql_query(
    """SELECT platform, status AS lead_status, created_at, updated_at, customer_name,
              utm_campaign AS campaign_name, utm_campaign_id AS campaign_id,
              utm_ad_set_id AS ad_set_id, utm_ad_id AS ad_id, fb_ad_title,
              amount_spent_usd
       FROM lead_events
       WHERE utm_ad_set_id IS NOT NULL AND utm_ad_set_id <> ''""",
    con,
)
leads["created_at_ts"] = pd.to_datetime(leads["created_at"])
leads["date"] = leads["created_at_ts"].dt.normalize()

ad_days = pd.read_sql_query(
    """SELECT day AS date, ad_set_id, campaign_name, campaign_id, delivery_status,
              amount_spent_usd AS spend, messaging_conversations_started AS messaging_conversations,
              reach, impressions, frequency, cost_per_lead AS cpl,
              ad_set_budget, ad_set_budget_type
       FROM daily_ad_performance""",
    con,
)
ad_days["date"] = pd.to_datetime(ad_days["date"])

# spend context for the lead sheet: the ad set's spend that day (contextual only, the
# importer takes max() per ad-set-day when collapsing, never sum -- see MODEL_TO_TRAFFIC).
spend_by_day = ad_days.groupby(["date", "ad_set_id"])["spend"].sum().rename("day_spend")
leads = leads.merge(spend_by_day, on=["date", "ad_set_id"], how="left")
leads["amount_spent_usd"] = leads["amount_spent_usd"].fillna(leads["day_spend"])

# ---------------------------------------------------------------- declared variables 4/6/7
# Reuse the app's own function so these match exactly what the Dataset page shows -- confirmed
# ad_set_start_dates and confirmed change_events only, no detector fallback (removed 2026-08-06).
_declared_rows = [{"ad_set_id": r.ad_set_id, "day": r.date} for r in ad_days.itertuples()]
core._attach_declared_variables(_declared_rows)
ad_days["days_since_adset_started"] = [r["days_since_adset_started"] for r in _declared_rows]
ad_days["ad_change_recency"] = [r["ad_change_recency"] for r in _declared_rows]
ad_days["ad_set_change_recency"] = [r["ad_set_change_recency"] for r in _declared_rows]

n_starts = len(core._confirmed_ad_set_starts())
n_ad_events = len(core._recorded_change_events("ad"))
n_adset_events = len(core._recorded_change_events("ad_set"))
print(f"confirmed ad_set_start_dates: {n_starts} ad sets | "
      f"confirmed change_events: {n_ad_events} ad-level, {n_adset_events} ad-set-level")

date_min = min(leads["date"].min(), ad_days["date"].min())
date_max = max(leads["date"].max(), ad_days["date"].max())
print(f"DB covers {date_min.date()} -> {date_max.date()}")
print(f"lead_events rows: {len(leads)}, daily_ad_performance rows: {len(ad_days)}")

# ---------------------------------------------------------------- chunk ranges
FIRST_END = pd.Timestamp("2026-07-11")
ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = [(date_min, FIRST_END)]
cursor = FIRST_END + timedelta(days=1)
while cursor <= date_max:
    end = min(cursor + timedelta(days=6), date_max)
    ranges.append((cursor, end))
    cursor = end + timedelta(days=1)

LEAD_COLS = ["platform", "lead_status", "created_at", "updated_at", "customer_name",
             "campaign_name", "campaign_id", "ad_set_id", "ad_id", "fb_ad_title",
             "amount_spent_usd"]
AD_COLS = ["date", "ad_set_id", "campaign_name", "campaign_id", "delivery_status", "spend",
           "messaging_conversations", "reach", "impressions", "frequency", "cpl",
           "ad_set_budget", "ad_set_budget_type",
           "days_since_adset_started", "ad_change_recency", "ad_set_change_recency"]

written = []
for start, end in ranges:
    label = f"{start:%m-%d} to {end:%m-%d}"
    lchunk = leads[(leads["date"] >= start) & (leads["date"] <= end)][LEAD_COLS].copy()
    achunk = ad_days[(ad_days["date"] >= start) & (ad_days["date"] <= end)][AD_COLS].copy()
    achunk = achunk.sort_values(["date", "ad_set_id"]).reset_index(drop=True)
    lchunk = lchunk.sort_values(["created_at"]).reset_index(drop=True)

    out_path = OUT_DIR / f"model_dataset {label}.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        lchunk.to_excel(w, sheet_name="model_dataset", index=False)
        achunk.to_excel(w, sheet_name="ad_set_days", index=False)
        for sheet, df in (("model_dataset", lchunk), ("ad_set_days", achunk)):
            ws = w.book[sheet]
            for i, col in enumerate(df.columns, start=1):
                letter = ws.cell(row=1, column=i).column_letter
                if col in ("ad_set_id", "campaign_id", "ad_id"):
                    for cell in ws[letter][1:]:
                        cell.number_format = "@"
                ws.column_dimensions[letter].width = max(12, min(32, len(col) + 4))
    written.append((out_path.name, len(lchunk), len(achunk)))
    print(f"wrote {out_path.name}: {len(lchunk)} leads, {len(achunk)} ad-set-days")

print("\ntotal files:", len(written))
