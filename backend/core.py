from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("LEADLENS_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("LEADLENS_DB_PATH", DATA_DIR / "leadlens.db"))
UPLOAD_DIR = DATA_DIR / "uploads"
PREVIEW_DIR = DATA_DIR / "previews"

REQUIRED_COLUMNS = [
    "Platform", "Status", "Created At", "Updated At", "Customer Name",
    "UTM Campaign", "UTM Campaign ID", "UTM Ad Set ID", "UTM Ad ID",
    "FB Ad Title", "Amount spent (USD)",
]
ID_COLUMNS = ["UTM Campaign ID", "UTM Ad Set ID", "UTM Ad ID"]
SOURCE_REQUIRED_COLUMNS = ["Created At", "Customer Name", "UTM Ad Set ID"]
SOURCE_ID_COLUMN = "Lead ID"
RECOVERY_COLUMNS = ["FB Ad ID", "FB Post ID", "FB Ad Title"]
OPTIONAL_SOURCE_COLUMNS = [
    "ID", "Customer ID", "UTM Source", "UTM Medium", "UTM Content", "UTM Term",
    "FB Click ID", "Post Tag", "FB Post ID", "FB Ad ID", "Messenger Ad Context (raw)", "Referrer",
]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CUSTOMER_TRAFFIC_TYPE = "customer_traffic"
AD_PERFORMANCE_TYPE = "ad_performance"
CHANGE_LOG_TYPE = "change_log"
MODEL_DATASET_TYPE = "model_dataset"
LEADLENS_DERIVED_TYPE = "leadlens_derived_variables"
HOLIDAY_PROXIMITY_TYPE = "holiday_proximity"

AD_PERFORMANCE_COLUMNS = [
    "Campaign name", "Campaign ID", "Ad set ID", "Ad ID", "Day", "Delivery status", "Delivery level",
    "Amount spent (USD)", "Messaging conversations started", "Cost per messaging conversation started",
    "Reach", "Impressions", "Frequency", "Leads", "Cost per lead", "Link clicks",
    "CPC (cost per link click)", "Unique link clicks", "Cost per unique link click",
    "Ad Set Budget", "Ad Set Budget Type", "Reporting starts", "Reporting ends",
]
AD_PERFORMANCE_REQUIRED = ["Campaign ID", "Ad set ID", "Day", "Amount spent (USD)"]
AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS = [
    "days_since_adset_started", "ad_set_change_recency", "ad_change_recency",
]
AD_ID_COLUMNS = ["Campaign ID", "Ad set ID"]
AD_TEXT_COLUMNS = ["Campaign name", "Delivery status", "Delivery level", "Ad Set Budget Type"]
AD_DATE_COLUMNS = ["Day", "Reporting starts", "Reporting ends"]
AD_NUMERIC_COLUMNS = [
    "Amount spent (USD)", "Messaging conversations started", "Cost per messaging conversation started",
    "Reach", "Impressions", "Frequency", "Leads", "Cost per lead", "Link clicks",
    "CPC (cost per link click)", "Unique link clicks", "Cost per unique link click",
    "Ad Set Budget",
]
# Newer Meta exports break out one row per ad. These roll up to the ad-set grain the rest of
# the pipeline expects: additive counters sum, deduplicated counters cannot, rates are recomputed.
AD_ADDITIVE_COLUMNS = [
    "Amount spent (USD)", "Impressions", "Leads", "Messaging conversations started",
    "Link clicks", "Unique link clicks",
]
# Reach counts distinct people, so summing it across ads double-counts anyone the ads shared.
# Frequency is impressions/reach and is meaningless once reach is unknown.
AD_NON_ADDITIVE_COLUMNS = ["Reach", "Frequency"]
# Derived rates, recomputed from the summed totals as (numerator column, denominator column).
AD_DERIVED_RATE_COLUMNS = {
    "Cost per lead": ("Amount spent (USD)", "Leads"),
    "CPC (cost per link click)": ("Amount spent (USD)", "Link clicks"),
    "Cost per unique link click": ("Amount spent (USD)", "Unique link clicks"),
    "Cost per messaging conversation started": ("Amount spent (USD)", "Messaging conversations started"),
}
AD_ROLLUP_KEY = ["Day", "Campaign ID", "Ad set ID", "Campaign name"]
DELIVERY_STATUS_PRIORITY = ["active", "not_delivering", "inactive", "archived"]
AD_INTERNAL_COLUMNS = {
    "Campaign name": "campaign_name",
    "Campaign ID": "campaign_id",
    "Ad set ID": "ad_set_id",
    "Day": "day",
    "Delivery status": "delivery_status",
    "Delivery level": "delivery_level",
    "Amount spent (USD)": "amount_spent_usd",
    "Messaging conversations started": "messaging_conversations_started",
    "Cost per messaging conversation started": "cost_per_messaging_conversation_started",
    "Reach": "reach",
    "Impressions": "impressions",
    "Frequency": "frequency",
    "Leads": "leads",
    "Cost per lead": "cost_per_lead",
    "Link clicks": "link_clicks",
    "CPC (cost per link click)": "cpc",
    "Unique link clicks": "unique_link_clicks",
    "Cost per unique link click": "cost_per_unique_link_click",
    "Ad Set Budget": "ad_set_budget",
    "Ad Set Budget Type": "ad_set_budget_type",
    "Reporting starts": "reporting_starts",
    "Reporting ends": "reporting_ends",
}

HOLIDAY_PROXIMITY_COLUMNS = ["date", "day", "is_holiday", "holiday_name", "holiday_proximity"]
HOLIDAY_PROXIMITY_REQUIRED = ["date", "holiday_proximity"]
HOLIDAY_PROXIMITY_BASELINE_BUCKET = "60_plus_or_none"

DEFAULT_FORECAST_PARAMETERS = {
    "historical_signal_share": 0.65,
    "spend_signal_share": 0.20,
    "weekday_share": 0.10,
    "error_share": 0.05,
    "spend_elasticity": 0.65,
    # Backward-compatible label used by existing UI/export copy.
    "history_spend_share": 0.85,
}
FORECAST_WEIGHT_CANDIDATES = [
    ("80/15/5", {"historical_signal_share": 0.60, "spend_signal_share": 0.20,
                 "weekday_share": 0.15, "error_share": 0.05, "history_spend_share": 0.80}),
    ("70/20/10", {"historical_signal_share": 0.52, "spend_signal_share": 0.18,
                  "weekday_share": 0.20, "error_share": 0.10, "history_spend_share": 0.70}),
    ("65/20/15", {"historical_signal_share": 0.49, "spend_signal_share": 0.16,
                  "weekday_share": 0.20, "error_share": 0.15, "history_spend_share": 0.65}),
    ("60/25/15", {"historical_signal_share": 0.45, "spend_signal_share": 0.15,
                  "weekday_share": 0.25, "error_share": 0.15, "history_spend_share": 0.60}),
    ("75/10/15", {"historical_signal_share": 0.56, "spend_signal_share": 0.19,
                  "weekday_share": 0.10, "error_share": 0.15, "history_spend_share": 0.75}),
]
ENSEMBLE_SCORE_TOLERANCE = 0.06
# How much of the last observed week's day-to-day amplitude the delivered forecast carries.
# 0 = the conditional mean, which is the most accurate daily forecast and draws as a flat
# line; 1 = as volatile as the actuals. Horizon totals are identical at every setting, so
# this only trades daily placement. Measured on rolling-origin backtests (daily WAPE / shape,
# where shape 1.0 matches the actuals): 0.00 -> 0.630/0.00, 0.40 -> 0.682/0.37,
# 0.50 -> 0.713/0.46, 0.60 -> 0.730/0.56, 1.00 -> 0.800/0.93.
FORECAST_SHAPE_STRENGTH = 0.9
# Weight on the flatness penalty in model selection, against 0.35 MAE / 0.25 WAPE /
# 0.15 RMSE / 0.15 bias / 0.10 interval. Raising it buys forecast shape with accuracy;
# see the sweep recorded in the accompanying notes before changing it.
FLATNESS_SELECTION_WEIGHT = 0.20
CALIBRATION_MIN_RETENTION = 0.35
SPEND_ADJUSTED_MODEL_PREFIX = "spend-adjusted formula"
BREAKOUT_MODEL_NAME = "recent change-point level"
ENSEMBLE_MODEL_NAME = "adaptive weighted ensemble"
TREND_MODEL_NAME = "damped-trend momentum"
OLS_SPEND_MODEL_NAME = "OLS spend regression"
OLS_MULTIVARIATE_MODEL_NAME = "multivariate OLS regression"
# Ridge strength per standardised regressor in the multivariate forecast fit. Swept over
# rolling-origin backtests: 0 gives WAPE 0.88, this gives 0.72, and heavier shrinkage keeps
# improving only because it collapses the model onto the intercept, which is not a model.
MULTIVARIATE_RIDGE_PER_FEATURE = 10.0
RECONCILIATION_BLEND = 0.5
RECONCILIATION_RATIO_RANGE = (0.7, 1.5)
PORTFOLIO_MIN_HISTORY_DAYS = 28
SPEND_LAG_CANDIDATES = (0, 1, 2)
SPEND_ADJUSTED_MODEL_NAMES = [
    f"{SPEND_ADJUSTED_MODEL_PREFIX} {label} lag{lag}"
    for label, _ in FORECAST_WEIGHT_CANDIDATES
    for lag in SPEND_LAG_CANDIDATES
]

def _header_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().casefold())


KNOWN_HEADERS = {
    _header_key(column): column
    for column in [*REQUIRED_COLUMNS, *OPTIONAL_SOURCE_COLUMNS]
}
# Some traffic exports (e.g. trimmed/partial pulls) shorten "Created At"/"Updated At" to just
# "Created"/"Updated". Same field, different header -- alias it like the ad-performance exports
# below, rather than rejecting the file outright.
for _alias, _canonical in {
    "Created": "Created At",
    "Updated": "Updated At",
    "Customer": "Customer Name",
    "Campaign": "UTM Campaign",
    "Campaign ID": "UTM Campaign ID",
    "Ad set ID": "UTM Ad Set ID",
    "Ad ID": "UTM Ad ID",
    "Ad title": "FB Ad Title",
    "Amount": "Amount spent (USD)",
}.items():
    KNOWN_HEADERS[_header_key(_alias)] = _canonical
AD_KNOWN_HEADERS = {
    _header_key(column): column
    for column in AD_PERFORMANCE_COLUMNS
}
# "Combined-Ad-Set-Dataset" exports (Sakda's per-ad-per-day workbook) use shorter header
# names for the same fields as the standard Meta ads-manager export. Map those aliases onto
# the canonical column names so the same ingest path handles both without a second importer.
for _alias, _canonical in {
    "Impression": "Impressions",
    "Messaging Conversation": "Messaging conversations started",
    "Cost Per Message": "Cost per messaging conversation started",
    "Campaign": "Campaign name",
    "Spend": "Amount spent (USD)",
    "CPL": "Cost per lead",
    "Budget": "Ad Set Budget",
}.items():
    AD_KNOWN_HEADERS[_header_key(_alias)] = _canonical


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard keep reading while an upload or a retrain writes; the default
    # rollback journal blocks readers for the whole write. It is persisted in the database
    # file, so this is a no-op after the first call.
    conn.execute("PRAGMA journal_mode = WAL")
    # Without this, a read that lands mid-write fails instantly with "database is locked"
    # instead of waiting. Model retraining holds its write long enough for that to matter.
    conn.execute("PRAGMA busy_timeout = 5000")
    # NORMAL is the standard companion to WAL: still durable across an application crash,
    # and only risks the last commits on OS crash or power loss, which nightly backups cover.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    # Pointing at a different database invalidates every cached read of the old one.
    _clear_change_caches()
    _holiday_proximity_map.cache_clear()
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS raw_uploads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_name TEXT NOT NULL,
          stored_path TEXT NOT NULL,
          file_sha256 TEXT NOT NULL,
          file_type TEXT NOT NULL DEFAULT 'customer_traffic',
          uploaded_at TEXT NOT NULL,
          row_count INTEGER NOT NULL,
          imported_count INTEGER NOT NULL DEFAULT 0,
          duplicate_count INTEGER NOT NULL DEFAULT 0,
          updated_count INTEGER NOT NULL DEFAULT 0,
          cleaned_count INTEGER NOT NULL DEFAULT 0,
          excluded_count INTEGER NOT NULL DEFAULT 0,
          rejected_count INTEGER NOT NULL DEFAULT 0,
          recovered_count INTEGER NOT NULL DEFAULT 0,
          total_spend_usd REAL,
          date_min TEXT,
          date_max TEXT,
          status TEXT NOT NULL DEFAULT 'imported'
        );
        CREATE TABLE IF NOT EXISTS lead_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_hash TEXT NOT NULL UNIQUE,
          platform TEXT, status TEXT, created_at TEXT NOT NULL, updated_at TEXT,
          customer_name TEXT, utm_campaign TEXT,
          utm_campaign_id TEXT, utm_ad_set_id TEXT NOT NULL, utm_ad_id TEXT,
          fb_ad_title TEXT, amount_spent_usd REAL, raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_lead_adset_date ON lead_events(utm_ad_set_id, created_at);
        CREATE TABLE IF NOT EXISTS upload_lead_links (
          upload_id INTEGER NOT NULL REFERENCES raw_uploads(id) ON DELETE CASCADE,
          lead_id INTEGER NOT NULL REFERENCES lead_events(id) ON DELETE CASCADE,
          PRIMARY KEY(upload_id, lead_id)
        );
        CREATE TABLE IF NOT EXISTS daily_ad_set_aggregates (
          aggregate_date TEXT NOT NULL, utm_ad_set_id TEXT NOT NULL,
          utm_campaign_id TEXT, lead_count INTEGER NOT NULL, ad_id_count INTEGER NOT NULL,
          new_count INTEGER NOT NULL, existing_count INTEGER NOT NULL,
          status_mix_json TEXT NOT NULL, spend_context_usd REAL,
          PRIMARY KEY(aggregate_date, utm_ad_set_id)
        );
        CREATE TABLE IF NOT EXISTS daily_ad_performance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          upload_id INTEGER NOT NULL REFERENCES raw_uploads(id) ON DELETE CASCADE,
          day TEXT NOT NULL,
          campaign_id TEXT NOT NULL,
          campaign_name TEXT,
          ad_set_id TEXT NOT NULL,
          delivery_status TEXT,
          delivery_level TEXT,
          amount_spent_usd REAL,
          messaging_conversations_started REAL,
          cost_per_messaging_conversation_started REAL,
          reach REAL,
          impressions REAL,
          frequency REAL,
          leads REAL,
          cost_per_lead REAL,
          link_clicks REAL,
          cpc REAL,
          unique_link_clicks REAL,
          cost_per_unique_link_click REAL,
          days_since_adset_started_imported REAL,
          ad_set_change_recency_imported TEXT,
          ad_change_recency_imported TEXT,
          reporting_starts TEXT,
          reporting_ends TEXT,
          raw_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(day, campaign_id, ad_set_id)
        );
        CREATE INDEX IF NOT EXISTS ix_daily_ad_performance_adset_day
          ON daily_ad_performance(ad_set_id, day);
        CREATE INDEX IF NOT EXISTS ix_daily_ad_performance_campaign_day
          ON daily_ad_performance(campaign_id, day);
        -- Per-ad rows kept alongside the ad-set rollup. The rollup is still what the rest of
        -- the pipeline reads; this table exists solely so ad_added / ad_paused / ad_swapped
        -- can be derived, which is impossible once rows are collapsed onto the ad-set grain.
        CREATE TABLE IF NOT EXISTS daily_ad_level_performance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          upload_id INTEGER NOT NULL REFERENCES raw_uploads(id) ON DELETE CASCADE,
          day TEXT NOT NULL,
          campaign_id TEXT NOT NULL,
          ad_set_id TEXT NOT NULL,
          ad_id TEXT NOT NULL,
          delivery_status TEXT,
          amount_spent_usd REAL,
          impressions REAL,
          leads REAL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(day, ad_set_id, ad_id)
        );
        CREATE INDEX IF NOT EXISTS ix_daily_ad_level_adset_day
          ON daily_ad_level_performance(ad_set_id, day);
        CREATE INDEX IF NOT EXISTS ix_daily_ad_level_ad_day
          ON daily_ad_level_performance(ad_id, day);
        -- Human-recorded ad set and ad change events, uploaded from the changelog sheets of
        -- MODEL_DATASET_TEMPLATE.xlsx. Meta's export carries no change log at all, so without
        -- this table variables 6, 7, 9 and 10 are entirely inferred from delivery signatures.
        -- Only rows with source='confirmed' are treated as fact; anything still marked
        -- inferred or not_recorded is the system's own guess coming back and is stored but
        -- never fed to the model, which would launder a guess into a recorded event.
        CREATE TABLE IF NOT EXISTS change_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          upload_id INTEGER REFERENCES raw_uploads(id) ON DELETE CASCADE,
          scope TEXT NOT NULL,
          event_date TEXT NOT NULL,
          ad_set_id TEXT NOT NULL,
          ad_id TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'confirmed',
          confirmed_by TEXT,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(scope, event_date, ad_set_id, ad_id)
        );
        CREATE INDEX IF NOT EXISTS ix_change_events_scope_set
          ON change_events(scope, ad_set_id, event_date);
        -- Human-recorded true launch date, one row per ad set. Declared variable 4
        -- (days_since_adset_started) is left-censored for any ad set already running on the
        -- first day of the earliest upload -- its age reports from that upload date, not its
        -- real launch. A confirmed row here overrides that estimate for its ad set, same
        -- "recorded fact beats detector" convention as change_events, but keyed on the ad set
        -- itself (a launch date is one fact, not a dated range or a repeatable event).
        CREATE TABLE IF NOT EXISTS ad_set_start_dates (
          ad_set_id TEXT PRIMARY KEY,
          start_date TEXT NOT NULL,
          confirmed_by TEXT,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_training_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          completed_at TEXT, status TEXT NOT NULL, training_rows INTEGER NOT NULL DEFAULT 0,
          ad_set_count INTEGER NOT NULL DEFAULT 0, mean_backtest_accuracy REAL,
          notes TEXT
        );
        CREATE TABLE IF NOT EXISTS forecasts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          training_run_id INTEGER NOT NULL REFERENCES model_training_runs(id) ON DELETE CASCADE,
          generated_at TEXT NOT NULL, utm_ad_set_id TEXT NOT NULL, utm_campaign_id TEXT,
          horizon_days INTEGER NOT NULL, predicted_leads REAL NOT NULL,
          lower_estimate REAL NOT NULL, upper_estimate REAL NOT NULL,
          confidence_score INTEGER NOT NULL, model_used TEXT NOT NULL,
          backtest_accuracy REAL NOT NULL, sparse_warning INTEGER NOT NULL,
          explanation TEXT NOT NULL,
          UNIQUE(training_run_id, utm_ad_set_id, horizon_days)
        );
        CREATE TABLE IF NOT EXISTS forecast_daily_predictions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          training_run_id INTEGER NOT NULL REFERENCES model_training_runs(id) ON DELETE CASCADE,
          generated_at TEXT NOT NULL, utm_ad_set_id TEXT NOT NULL, utm_campaign_id TEXT,
          forecast_date TEXT NOT NULL, day_index INTEGER NOT NULL,
          weekday_name TEXT, weekday_factor REAL,
          predicted_leads REAL NOT NULL, lower_estimate REAL NOT NULL, upper_estimate REAL NOT NULL,
          confidence_score INTEGER NOT NULL, model_used TEXT NOT NULL,
          sparse_warning INTEGER NOT NULL, explanation TEXT NOT NULL,
          actual_leads REAL, error REAL, absolute_error REAL, squared_error REAL,
          interval_hit INTEGER, realized_at TEXT,
          UNIQUE(training_run_id, utm_ad_set_id, day_index)
        );
        CREATE TABLE IF NOT EXISTS model_backtest_metrics (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          training_run_id INTEGER NOT NULL REFERENCES model_training_runs(id) ON DELETE CASCADE,
          utm_ad_set_id TEXT NOT NULL,
          model_used TEXT NOT NULL,
          horizon_days INTEGER NOT NULL,
          backtest_windows INTEGER NOT NULL,
          mae REAL,
          rmse REAL,
          wape REAL,
          mase REAL,
          bias REAL,
          r2_out_of_sample REAL,
          interval_coverage REAL,
          average_interval_width REAL,
          selection_score REAL,
          weekday_wape REAL,
          weekend_wape REAL,
          weekday_bias REAL,
          weekend_bias REAL,
          weekday_seasonality_strength REAL,
          forecast_variance_ratio REAL,
          flatness_penalty REAL,
          recency_weighted_mae REAL,
          recency_weighted_rmse REAL,
          recency_weighted_wape REAL,
          recency_weighted_bias REAL,
          UNIQUE(training_run_id, utm_ad_set_id, model_used, horizon_days)
        );
        CREATE INDEX IF NOT EXISTS ix_backtest_metrics_run_adset
          ON model_backtest_metrics(training_run_id, utm_ad_set_id, horizon_days);
        CREATE TABLE IF NOT EXISTS ad_set_budget_periods (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ad_set_id TEXT NOT NULL,
          start_date TEXT NOT NULL,
          end_date TEXT NOT NULL,
          daily_budget REAL NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_budget_periods_adset
          ON ad_set_budget_periods(ad_set_id, start_date);
        CREATE TABLE IF NOT EXISTS app_users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE,
          full_name TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'staff',
          status TEXT NOT NULL DEFAULT 'active',
          password_hash TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_login_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_app_users_status_role
          ON app_users(status, role);
        CREATE TABLE IF NOT EXISTS app_user_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          actor_email TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          target_email TEXT NOT NULL DEFAULT '',
          detail TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_app_user_audit_created
          ON app_user_audit(created_at);
        """)
        from . import auth
        auth.ensure_basic_user(db)
        existing_upload_columns = {row[1] for row in db.execute("PRAGMA table_info(raw_uploads)")}
        for name, definition in (
            ("file_type", "TEXT NOT NULL DEFAULT 'customer_traffic'"),
            ("updated_count", "INTEGER NOT NULL DEFAULT 0"),
            ("rejected_count", "INTEGER NOT NULL DEFAULT 0"),
            ("total_spend_usd", "REAL"),
            ("cleaned_count", "INTEGER NOT NULL DEFAULT 0"),
            ("excluded_count", "INTEGER NOT NULL DEFAULT 0"),
            ("recovered_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in existing_upload_columns:
                db.execute(f"ALTER TABLE raw_uploads ADD COLUMN {name} {definition}")
        existing_daily_columns = {row[1] for row in db.execute("PRAGMA table_info(forecast_daily_predictions)")}
        for name, definition in (
            ("weekday_name", "TEXT"), ("weekday_factor", "REAL"),
            ("actual_leads", "REAL"), ("error", "REAL"), ("absolute_error", "REAL"),
            ("squared_error", "REAL"), ("interval_hit", "INTEGER"), ("realized_at", "TEXT"),
        ):
            if name not in existing_daily_columns:
                db.execute(f"ALTER TABLE forecast_daily_predictions ADD COLUMN {name} {definition}")
        existing_ad_columns = {row[1] for row in db.execute("PRAGMA table_info(daily_ad_performance)")}
        for name, definition in (
            ("ad_set_budget", "REAL"),
            ("ad_set_budget_type", "TEXT"),
            ("days_since_adset_started_imported", "REAL"),
            ("ad_set_change_recency_imported", "TEXT"),
            ("ad_change_recency_imported", "TEXT"),
        ):
            if name not in existing_ad_columns:
                db.execute(f"ALTER TABLE daily_ad_performance ADD COLUMN {name} {definition}")
        existing_budget_columns = {row[1] for row in db.execute("PRAGMA table_info(ad_set_budget_periods)")}
        for name, definition in (
            ("source", "TEXT NOT NULL DEFAULT 'manual'"),
            ("budget_type", "TEXT"),
            ("spend_conflict", "INTEGER NOT NULL DEFAULT 0"),
            ("mean_daily_spend", "REAL"),
            ("observed_days", "INTEGER"),
            ("recent_mean_daily_spend", "REAL"),
            ("recent_days", "INTEGER"),
        ):
            if name not in existing_budget_columns:
                db.execute(f"ALTER TABLE ad_set_budget_periods ADD COLUMN {name} {definition}")
        existing_change_columns = {row[1] for row in db.execute("PRAGMA table_info(change_events)")}
        for name, definition in (
            # The UI records a change as a dated range, because that is how a human recalls
            # one. The model only ever needs the start -- the state carries forward from
            # there -- so end_date is stored for coverage reporting, not read as a feature.
            ("end_date", "TEXT"),
        ):
            if name not in existing_change_columns:
                db.execute(f"ALTER TABLE change_events ADD COLUMN {name} {definition}")
        # Change type was removed from the model on 2026-08-11 and the column with it. An
        # event is a date; nothing reads a kind any more. Dropped rather than left in place
        # so no future reader can mistake stale values for live ones. SQLite has supported
        # DROP COLUMN since 3.35 -- guarded anyway so an older runtime degrades to leaving
        # the column present and unread rather than failing to open the database at all.
        if "change_type" in existing_change_columns:
            try:
                db.execute("ALTER TABLE change_events DROP COLUMN change_type")
            except sqlite3.OperationalError:
                pass
        _backfill_imported_ad_performance_derived_values(db)
        existing_metric_columns = {row[1] for row in db.execute("PRAGMA table_info(model_backtest_metrics)")}
        for name in ("weekday_wape", "weekend_wape", "weekday_bias", "weekend_bias",
                     "weekday_seasonality_strength", "forecast_variance_ratio", "flatness_penalty",
                     "recency_weighted_mae", "recency_weighted_rmse",
                     "recency_weighted_wape", "recency_weighted_bias"):
            if name not in existing_metric_columns:
                db.execute(f"ALTER TABLE model_backtest_metrics ADD COLUMN {name} REAL")
        existing_lead_columns = {row[1] for row in db.execute("PRAGMA table_info(lead_events)")}
        for name, definition in (
            # A pipeline stage the CRM side records by hand, not derived from anything
            # imported -- there's no data source for it yet, so every existing and new row
            # starts at "Intake" (LEAD_QUALITY_OPTIONS[0]) until someone updates it via the
            # board. NOT NULL + a constant DEFAULT means SQLite backfills every existing row
            # with that value as part of the ALTER, not just future inserts.
            ("lead_quality", "TEXT NOT NULL DEFAULT 'Intake'"),
        ):
            if name not in existing_lead_columns:
                db.execute(f"ALTER TABLE lead_events ADD COLUMN {name} {definition}")


def _safe_id(value: object, column: str, row_number: int) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if _looks_like_scientific_id(text):
        return ""
    return text


def _looks_like_scientific_id(value: object) -> bool:
    text = "" if pd.isna(value) else str(value).strip()
    return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?e[+-]?\d+", text, flags=re.IGNORECASE))


def _clean_fb_ad_title(value: object) -> str:
    """Normalize whitespace only. Export group suffixes such as _Group_1 are preserved."""
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def _lookup_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\|\s*", " | ", text)
    return text.casefold()


def _identity_timestamp(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        parsed = pd.to_datetime(value, format="mixed", errors="coerce")
    except Exception:
        parsed = pd.NaT
    if pd.notna(parsed):
        return parsed.isoformat()
    return str(value).strip()


def _lead_identity_parts(
    created_at: object,
    customer_name: object,
    status: object,
    campaign_id: object,
    ad_set_id: object,
    ad_id: object,
    fb_ad_title: object,
) -> list[str]:
    return [
        _identity_timestamp(created_at),
        str(customer_name or "").strip().casefold(),
        str(status or "").strip().casefold(),
        str(campaign_id or "").strip(),
        str(ad_set_id or "").strip(),
        str(ad_id or "").strip(),
        _clean_fb_ad_title(fb_ad_title).casefold(),
    ]


def _lead_identity_hash(
    created_at: object,
    customer_name: object,
    status: object,
    campaign_id: object,
    ad_set_id: object,
    ad_id: object,
    fb_ad_title: object,
) -> str:
    parts = _lead_identity_parts(created_at, customer_name, status, campaign_id, ad_set_id, ad_id, fb_ad_title)
    return hashlib.sha256(("lead-identity|" + "|".join(parts)).encode("utf-8")).hexdigest()


def _unique_attribution_map(frame: pd.DataFrame, key: str) -> dict[str, tuple[str, str, str]]:
    known = frame[(frame[key] != "") & (frame["UTM Ad Set ID"] != "")][
        [key, "UTM Ad Set ID", "UTM Campaign ID", "UTM Campaign"]
    ].drop_duplicates()
    if known.empty:
        return {}
    unambiguous = known.groupby(key)["UTM Ad Set ID"].nunique()
    allowed = set(unambiguous[unambiguous == 1].index)
    known = known[known[key].isin(allowed)].drop_duplicates(key)
    return {
        str(row[key]): (str(row["UTM Ad Set ID"]), str(row["UTM Campaign ID"]), str(row["UTM Campaign"]))
        for _, row in known.iterrows()
    }


def _historical_attribution_map() -> dict[tuple[str, str], tuple[str, str, str, str]]:
    if not DB_PATH.exists():
        return {}
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT utm_campaign, utm_campaign_id, fb_ad_title, utm_ad_set_id, utm_ad_id, COUNT(*) lead_count
                   FROM lead_events
                   WHERE TRIM(COALESCE(utm_campaign, '')) <> ''
                     AND TRIM(COALESCE(fb_ad_title, '')) <> ''
                     AND TRIM(COALESCE(utm_ad_set_id, '')) <> ''
                   GROUP BY utm_campaign, utm_campaign_id, fb_ad_title, utm_ad_set_id, utm_ad_id
                   ORDER BY lead_count DESC"""
            ).fetchall()
    except sqlite3.Error:
        return {}

    grouped: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    for row in rows:
        key = (_lookup_text(row["utm_campaign"]), _lookup_text(row["fb_ad_title"]))
        grouped.setdefault(key, []).append((
            str(row["utm_ad_set_id"] or "").strip(),
            str(row["utm_campaign_id"] or "").strip(),
            str(row["utm_campaign"] or "").strip(),
            str(row["utm_ad_id"] or "").strip(),
        ))
    return {
        key: values[0]
        for key, values in grouped.items()
        if len({(ad_set, campaign_id, ad_id) for ad_set, campaign_id, _, ad_id in values}) == 1
    }


def _historical_campaign_map() -> dict[str, tuple[str, str, str]]:
    if not DB_PATH.exists():
        return {}
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT utm_campaign, utm_campaign_id, utm_ad_set_id, COUNT(*) lead_count
                   FROM lead_events
                   WHERE TRIM(COALESCE(utm_campaign, '')) <> ''
                     AND TRIM(COALESCE(utm_ad_set_id, '')) <> ''
                   GROUP BY utm_campaign, utm_campaign_id, utm_ad_set_id
                   ORDER BY lead_count DESC"""
            ).fetchall()
    except sqlite3.Error:
        return {}

    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for row in rows:
        key = _lookup_text(row["utm_campaign"])
        grouped.setdefault(key, []).append((
            str(row["utm_ad_set_id"] or "").strip(),
            str(row["utm_campaign_id"] or "").strip(),
            str(row["utm_campaign"] or "").strip(),
        ))
    return {
        key: values[0]
        for key, values in grouped.items()
        if len({(ad_set, campaign_id) for ad_set, campaign_id, _ in values}) == 1
    }


def _historical_campaign_ad_set_options() -> dict[str, list[dict[str, object]]]:
    if not DB_PATH.exists():
        return {}
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT utm_campaign, utm_campaign_id, utm_ad_set_id, COUNT(*) lead_count
                   FROM lead_events
                   WHERE TRIM(COALESCE(utm_campaign, '')) <> ''
                     AND TRIM(COALESCE(utm_campaign_id, '')) <> ''
                     AND TRIM(COALESCE(utm_ad_set_id, '')) <> ''
                   GROUP BY utm_campaign, utm_campaign_id, utm_ad_set_id
                   ORDER BY lead_count DESC, utm_ad_set_id"""
            ).fetchall()
    except sqlite3.Error:
        return {}

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(_lookup_text(row["utm_campaign"]), []).append({
            "campaign_id": str(row["utm_campaign_id"] or "").strip(),
            "ad_set_id": str(row["utm_ad_set_id"] or "").strip(),
            "lead_count": int(row["lead_count"] or 0),
        })
    return grouped


def _read_raw_frame(path_or_buffer, extension: str) -> tuple[pd.DataFrame, list[str]]:
    extension = extension.lower()
    if extension == ".xlsx":
        try:
            frame = pd.read_excel(path_or_buffer, sheet_name="Corrected Traffic", dtype=str, keep_default_na=False)
        except ValueError:
            frame = pd.read_excel(path_or_buffer, sheet_name=0, dtype=str, keep_default_na=False)
    elif extension == ".csv":
        try:
            frame = pd.read_csv(path_or_buffer, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            frame = pd.read_csv(path_or_buffer, dtype=str, keep_default_na=False, encoding="cp1252")
    else:
        raise ValueError("Only .xlsx and .csv files are supported.")

    source_columns = [str(column).strip() for column in frame.columns]
    return frame, source_columns


def _workbook_sheet_names(path_or_buffer, extension: str) -> list[str]:
    if extension.lower() != ".xlsx":
        return []
    try:
        # context-managed: an unclosed handle keeps the file locked on Windows, which
        # breaks the move out of the preview directory on import
        with pd.ExcelFile(path_or_buffer) as workbook:
            return [str(name) for name in workbook.sheet_names]
    except Exception:
        return []


def is_change_log_workbook(path_or_buffer, extension: str) -> bool:
    """True for the template workbook, identified by its changelog sheets.

    Sheet names, not columns: the template's first sheet is a README, so the column-based
    detector would never see the change log at all.
    """
    names = {name.strip().lower() for name in _workbook_sheet_names(path_or_buffer, extension)}
    return bool(names & set(CHANGE_LOG_SHEETS))


def read_change_log_workbook(path_or_buffer, extension: str) -> pd.DataFrame:
    """Confirmed change events from the changelog sheets, one row per event.

    Rows whose source is not 'confirmed' are kept and returned so the counts reconcile
    against the file, but they are marked and the feature layer ignores them.
    """
    if extension.lower() != ".xlsx":
        raise ValueError("The change log must be the .xlsx template workbook.")
    sheets = {name.strip().lower(): name for name in _workbook_sheet_names(path_or_buffer, extension)}
    present = [key for key in CHANGE_LOG_SHEETS if key in sheets]
    if not present:
        raise ValueError(
            "No changelog sheets found. Upload MODEL_DATASET_TEMPLATE.xlsx, which carries "
            "'changelog_ad_set' and 'changelog_ad'."
        )
    collected: list[pd.DataFrame] = []
    report: dict[str, object] = {"sheets_read": [], "skipped_rows": {}, "by_scope": {}}
    skipped_total = 0
    for key in present:
        scope = CHANGE_LOG_SHEETS[key]
        raw = pd.read_excel(path_or_buffer, sheet_name=sheets[key], dtype=str, keep_default_na=False)
        raw.columns = [str(column).strip().lower() for column in raw.columns]
        type_column = "ad_set_change_type" if scope == "ad_set" else "ad_change_type"
        missing = [c for c in ("date", "ad_set_id", type_column) if c not in raw.columns]
        if missing:
            raise ValueError(f"Sheet '{sheets[key]}' is missing required columns: " + ", ".join(missing))
        def text(column: str, *, lower: bool = False, default: str = "") -> pd.Series:
            if column not in raw.columns:
                return pd.Series([default] * len(raw), index=raw.index)
            values = raw[column].fillna("").astype(str).str.strip()
            values = values.str.lower() if lower else values
            return values.replace({"nan": "", "nat": ""})

        frame = pd.DataFrame({
            "scope": scope,
            "event_date": pd.to_datetime(raw["date"], errors="coerce"),
            "ad_set_id": text("ad_set_id"),
            "ad_id": text("ad_id"),
            "change_type": text(type_column, lower=True),
            "source": text("source", lower=True, default=CONFIRMED_SOURCE),
            "confirmed_by": text("confirmed_by"),
            "notes": text("notes"),
            "_row": range(2, len(raw) + 2),
        })
        # A baseline cell is legal input, not a bad value -- it just says "nothing changed",
        # which is already the default for any day without an event. Counted under its own
        # name so the import report doesn't accuse the sheet of an unknown change type.
        baseline = frame["change_type"].isin(UPLOAD_BASELINE_VALUES)
        reasons = {
            "blank_date": frame["event_date"].isna(),
            "blank_ad_set_id": frame["ad_set_id"].eq(""),
            "no_change_rows": baseline,
        }
        drop = (reasons["blank_date"] | reasons["blank_ad_set_id"]
                | reasons["no_change_rows"])
        # A wholly blank trailing row is the template's own padding, not a mistake worth reporting.
        blank_row = (frame["event_date"].isna() & frame["ad_set_id"].eq("") & frame["change_type"].eq(""))
        for name, mask in reasons.items():
            count = int((mask & ~blank_row).sum())
            if count:
                report["skipped_rows"][f"{key}:{name}"] = count
        skipped_total += int((drop & ~blank_row).sum())
        frame = frame[~drop].copy()
        report["sheets_read"].append(sheets[key])
        collected.append(frame)

    events = pd.concat(collected, ignore_index=True) if collected else pd.DataFrame()
    if events.empty:
        raise ValueError(
            "The changelog sheets hold no usable events. Every row needs a date, an ad set ID "
            "and a valid change type."
        )
    events["event_date"] = events["event_date"].dt.normalize()
    events["source"] = events["source"].replace({"": CONFIRMED_SOURCE})
    duplicates = int(events.duplicated(["scope", "event_date", "ad_set_id", "ad_id"], keep="last").sum())
    events = events.drop_duplicates(["scope", "event_date", "ad_set_id", "ad_id"], keep="last").reset_index(drop=True)

    confirmed = events["source"].eq(CONFIRMED_SOURCE)
    for scope in sorted(events["scope"].unique()):
        sub = events[events["scope"] == scope]
        confirmed_sub = sub[sub["source"].eq(CONFIRMED_SOURCE)]
        report["by_scope"][scope] = {
            "rows": int(len(sub)),
            "confirmed": int(len(confirmed_sub)),
            "not_confirmed": int(len(sub) - len(confirmed_sub)),
            # ad sets the model will actually switch over, so counted on confirmed rows only
            "ad_sets": int(confirmed_sub["ad_set_id"].nunique()),
            "ad_sets_in_file": int(sub["ad_set_id"].nunique()),
        }
    report.update({
        "source_rows": int(len(events) + skipped_total + duplicates),
        "clean_rows": int(len(events)),
        "confirmed_rows": int(confirmed.sum()),
        "unconfirmed_rows": int((~confirmed).sum()),
        "excluded_rows": skipped_total,
        "duplicates_removed": duplicates,
        "unique_ad_sets": int(events["ad_set_id"].nunique()),
        "date_min": events["event_date"].min().date().isoformat(),
        "date_max": events["event_date"].max().date().isoformat(),
    })
    events.attrs["cleaning_report"] = report
    return events


LEADLENS_DERIVED_REQUIRED = [
    "Day", "Campaign name", "Ad set ID", "days_since_adset_started",
    "ad_set_change_recency", "ad_change_recency",
]


def is_leadlens_derived_columns(columns: Iterable[object]) -> bool:
    ad_keys = {_header_key(AD_KNOWN_HEADERS.get(_header_key(column), column)) for column in columns}
    return all(_header_key(column) in ad_keys for column in LEADLENS_DERIVED_REQUIRED)


def _candidate_ad_set_signatures(dates: pd.Series) -> dict[str, dict[str, object]]:
    starts = dict(_confirmed_ad_set_starts())
    date_index = pd.DatetimeIndex(pd.to_datetime(dates, errors="coerce").dropna().dt.normalize().unique())
    signatures: dict[str, dict[str, object]] = {}
    options = _historical_campaign_ad_set_options()
    for candidates in options.values():
        for candidate in candidates:
            ad_set_id = str(candidate["ad_set_id"])
            if ad_set_id in signatures:
                continue
            start = starts.get(ad_set_id)
            days_by_date: dict[pd.Timestamp, int] = {}
            if start is not None:
                for day in date_index:
                    days_by_date[pd.Timestamp(day).normalize()] = int((pd.Timestamp(day).normalize() - start).days)
            signatures[ad_set_id] = {
                "start": start,
                "days_since": days_by_date,
                "ad_set_change_recency": {
                    pd.Timestamp(day).normalize(): _change_state_as_of(_recorded_change_events("ad_set", ad_set_id), pd.Timestamp(day))
                    for day in date_index
                },
                "ad_change_recency": {
                    pd.Timestamp(day).normalize(): _change_state_as_of(_recorded_change_events("ad", ad_set_id), pd.Timestamp(day))
                    for day in date_index
                },
            }
    return signatures


def _historical_campaign_day_ad_sets() -> dict[tuple[str, pd.Timestamp], set[str]]:
    if not DB_PATH.exists():
        return {}
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT campaign_name, day, ad_set_id
                   FROM daily_ad_performance
                   WHERE TRIM(COALESCE(campaign_name, '')) <> ''
                     AND TRIM(COALESCE(day, '')) <> ''
                     AND TRIM(COALESCE(ad_set_id, '')) <> ''"""
            ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[tuple[str, pd.Timestamp], set[str]] = {}
    for row in rows:
        day = pd.to_datetime(row["day"], errors="coerce")
        if pd.isna(day):
            continue
        out.setdefault((_lookup_text(row["campaign_name"]), day.normalize()), set()).add(str(row["ad_set_id"]))
    return out


def _resolve_derived_ad_set_ids(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    options_by_campaign = _historical_campaign_ad_set_options()
    active_ad_sets = _historical_campaign_day_ad_sets()
    signatures = _candidate_ad_set_signatures(frame["Day"])
    recovered = 0
    unresolved = 0
    ambiguous = 0

    for index, row in frame.iterrows():
        ad_set = _safe_id(row["Ad set ID"], "Ad set ID", int(row["_source_row"]))
        campaign_id = _safe_id(row["Campaign ID"], "Campaign ID", int(row["_source_row"]))
        frame.at[index, "Ad set ID"] = ad_set
        frame.at[index, "Campaign ID"] = campaign_id
        if ad_set:
            continue
        candidates = options_by_campaign.get(_lookup_text(row["Campaign name"]), [])
        if not candidates:
            unresolved += 1
            continue
        matches = candidates
        day = row["Day"]
        if pd.notna(day):
            active = active_ad_sets.get((_lookup_text(row["Campaign name"]), day))
            if active:
                active_matches = [candidate for candidate in matches if str(candidate["ad_set_id"]) in active]
                if active_matches:
                    matches = active_matches
        age = row["days_since_adset_started"]
        if pd.notna(day) and not pd.isna(age):
            age_matches = [
                candidate for candidate in matches
                if signatures.get(str(candidate["ad_set_id"]), {}).get("days_since", {}).get(day) == int(age)
            ]
            if age_matches:
                matches = age_matches
        for column in ("ad_set_change_recency", "ad_change_recency"):
            value = str(row[column]).strip()
            if pd.isna(day) or not value:
                continue
            recency_matches = [
                candidate for candidate in matches
                if signatures.get(str(candidate["ad_set_id"]), {}).get(column, {}).get(day) == value
            ]
            if recency_matches:
                matches = recency_matches
        if len(matches) == 1:
            frame.at[index, "Ad set ID"] = str(matches[0]["ad_set_id"])
            frame.at[index, "Campaign ID"] = str(matches[0]["campaign_id"])
            recovered += 1
        else:
            ambiguous += int(len(matches) > 1)
            unresolved += int(len(matches) <= 1)

    blank = frame["Ad set ID"].eq("")
    for (campaign, day), group in frame.loc[blank & frame["Day"].notna()].groupby(["Campaign name", "Day"], sort=False):
        active = active_ad_sets.get((_lookup_text(campaign), day))
        if not active:
            continue
        candidates = [
            candidate for candidate in options_by_campaign.get(_lookup_text(campaign), [])
            if str(candidate["ad_set_id"]) in active
        ]
        if len(candidates) != len(group):
            continue
        for index, candidate in zip(group.index, sorted(candidates, key=lambda item: str(item["ad_set_id"]))):
            frame.at[index, "Ad set ID"] = str(candidate["ad_set_id"])
            frame.at[index, "Campaign ID"] = str(candidate["campaign_id"])
            recovered += 1
            if ambiguous:
                ambiguous -= 1

    return frame, {
        "recovered_ad_set_ids": recovered,
        "unresolved_attribution_rows": unresolved,
        "ambiguous_attribution_rows": ambiguous,
    }


def _event_dates_from_recency_buckets(group: pd.DataFrame, column: str) -> tuple[list[pd.Timestamp], int]:
    events: list[pd.Timestamp] = []
    ambiguous_runs = 0
    low: pd.Timestamp | None = None
    high: pd.Timestamp | None = None

    def flush() -> None:
        nonlocal low, high, ambiguous_runs
        if low is None or high is None:
            return
        if low == high:
            events.append(low)
        else:
            ambiguous_runs += 1
        low = high = None

    for row in group.sort_values("Day").itertuples():
        bucket = str(getattr(row, column)).strip()
        if bucket == NO_RECENT_CHANGE_BUCKET or bucket not in RECENCY_BUCKET_DAY_RANGES:
            flush()
            continue
        start, end = RECENCY_BUCKET_DAY_RANGES[bucket]
        row_low = pd.Timestamp(row.Day).normalize() - pd.Timedelta(days=end)
        row_high = pd.Timestamp(row.Day).normalize() - pd.Timedelta(days=start)
        if low is None or high is None:
            low, high = row_low, row_high
            continue
        next_low = max(low, row_low)
        next_high = min(high, row_high)
        if next_low <= next_high:
            low, high = next_low, next_high
            continue
        flush()
        low, high = row_low, row_high
    flush()
    return sorted(set(events)), ambiguous_runs


def read_leadlens_derived_tabular(path_or_buffer, extension: str) -> dict[str, object]:
    frame, source_columns = _read_raw_frame(path_or_buffer, extension)
    renamed: dict[object, str] = {}
    for column in frame.columns:
        canonical = AD_KNOWN_HEADERS.get(_header_key(column))
        if canonical:
            renamed[column] = canonical
    frame = frame.rename(columns=renamed)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in LEADLENS_DERIVED_REQUIRED if column not in frame.columns]
    if missing:
        raise ValueError("Missing required LeadLens derived-variable columns: " + ", ".join(missing))
    if "Campaign ID" not in frame.columns:
        frame["Campaign ID"] = ""

    source_rows = len(frame)
    frame["_source_row"] = range(2, source_rows + 2)
    frame = frame.dropna(how="all").copy()
    for column in ["Campaign name", "Campaign ID", "Ad set ID", "ad_set_change_recency", "ad_change_recency"]:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame["Day"] = pd.to_datetime(frame["Day"], format="mixed", errors="coerce").dt.normalize()
    frame["days_since_adset_started"] = pd.to_numeric(frame["days_since_adset_started"], errors="coerce")
    for column in ["ad_set_change_recency", "ad_change_recency"]:
        frame[column] = frame[column].str.strip()
    invalid_bucket = ~(
        frame["ad_set_change_recency"].isin(RECENCY_BUCKETS)
        & frame["ad_change_recency"].isin(RECENCY_BUCKETS)
    )
    frame, recovery = _resolve_derived_ad_set_ids(frame)
    missing_required = (
        frame["Day"].isna()
        | frame["Campaign name"].eq("")
        | frame["Ad set ID"].eq("")
        | frame["days_since_adset_started"].isna()
        | invalid_bucket
    )
    clean = frame.loc[~missing_required].copy().reset_index(drop=True)
    if clean.empty:
        raise ValueError(
            "No valid LeadLens derived-variable rows remain after cleaning. Check Day, Campaign, "
            "Ad set ID, days_since_adset_started, and recency bucket values."
        )

    start_candidates = clean.assign(
        start_date=clean["Day"] - pd.to_timedelta(clean["days_since_adset_started"].astype(int), unit="D")
    )
    starts = (
        start_candidates.groupby("Ad set ID", sort=True)["start_date"]
        .agg(lambda values: values.value_counts().index[0])
        .reset_index()
        .rename(columns={"Ad set ID": "ad_set_id"})
    )
    starts["start_date"] = pd.to_datetime(starts["start_date"]).dt.normalize()
    starts["confirmed_by"] = "LeadLens derived CSV"
    starts["notes"] = "Imported from LeadLens derived-variable CSV."

    event_frames = []
    ambiguous_runs = 0
    for ad_set_id, group in clean.groupby("Ad set ID", sort=True):
        for source_column, scope in (("ad_set_change_recency", "ad_set"), ("ad_change_recency", "ad")):
            dates, ambiguous = _event_dates_from_recency_buckets(group, source_column)
            ambiguous_runs += ambiguous
            if dates:
                event_frames.append(pd.DataFrame({
                    "scope": scope,
                    "event_date": dates,
                    "ad_set_id": str(ad_set_id),
                    "ad_id": "",
                    "source": CONFIRMED_SOURCE,
                    "confirmed_by": "LeadLens derived CSV",
                    "notes": f"Reconstructed from {source_column} bucket sequence.",
                }))
    changes = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame(columns=[
        "scope", "event_date", "ad_set_id", "ad_id", "source", "confirmed_by", "notes",
    ])
    if len(changes):
        changes = changes.drop_duplicates(["scope", "event_date", "ad_set_id", "ad_id"], keep="last")
        changes["event_date"] = pd.to_datetime(changes["event_date"]).dt.normalize()

    report = {
        "source_rows": int(source_rows),
        "clean_rows": int(len(clean)),
        "excluded_rows": int(missing_required.sum()),
        "invalid_dates": int(frame["Day"].isna().sum()),
        "invalid_bucket_rows": int(invalid_bucket.sum()),
        "invalid_start_age_rows": int(frame["days_since_adset_started"].isna().sum()),
        "unique_ad_sets": int(clean["Ad set ID"].nunique()),
        "date_min": clean["Day"].min().date().isoformat(),
        "date_max": clean["Day"].max().date().isoformat(),
        "start_dates": int(len(starts)),
        "change_events": int(len(changes)),
        "change_events_by_scope": changes.groupby("scope").size().to_dict() if len(changes) else {},
        "ambiguous_recency_runs": int(ambiguous_runs),
        "source_columns": source_columns,
        "recognized_columns": list(dict.fromkeys(
            AD_KNOWN_HEADERS[_header_key(column)] for column in source_columns if _header_key(column) in AD_KNOWN_HEADERS
        )),
        "ignored_columns": [column for column in source_columns if _header_key(column) not in AD_KNOWN_HEADERS],
        **recovery,
    }
    return {"rows": clean, "starts": starts, "changes": changes, "report": report}


MODEL_DATASET_SHEET = "model_dataset"
# Optional companion sheet at ad set x day grain. A lead-grain sheet can only describe days
# that produced a lead, so days that spent and got nothing are invisible in it. This sheet
# carries the complete delivery picture; when present it replaces the collapsed lead context.
AD_SET_DAY_SHEET = "ad_set_days"
AD_SET_DAY_REQUIRED = ["date", "ad_set_id", "spend"]
# One row per lead, so the lead's own identity plus the ad set x day context it arrived in.
MODEL_DATASET_REQUIRED = ["created_at", "customer_name", "ad_set_id"]
# model dataset column -> the CRM traffic column the existing cleaner expects
MODEL_TO_TRAFFIC = {
    "platform": "Platform", "lead_status": "Status", "created_at": "Created At",
    "updated_at": "Updated At", "customer_name": "Customer Name",
    "campaign_name": "UTM Campaign", "campaign_id": "UTM Campaign ID",
    "ad_set_id": "UTM Ad Set ID", "ad_id": "UTM Ad ID", "fb_ad_title": "FB Ad Title",
}
# model dataset column -> the Meta ad-performance column the existing cleaner expects
MODEL_TO_AD = {
    "campaign_name": "Campaign name", "campaign_id": "Campaign ID", "ad_set_id": "Ad set ID",
    "delivery_status": "Delivery status", "spend": "Amount spent (USD)",
    "messaging_conversations": "Messaging conversations started", "reach": "Reach",
    "impressions": "Impressions", "frequency": "Frequency", "cpl": "Cost per lead",
    "ad_set_budget": "Ad Set Budget", "ad_set_budget_type": "Ad Set Budget Type",
}


def _model_dataset_columns(path_or_buffer, extension: str) -> list[str]:
    if extension.lower() != ".xlsx":
        return []
    names = {n.strip().lower(): n for n in _workbook_sheet_names(path_or_buffer, extension)}
    if MODEL_DATASET_SHEET not in names:
        return []
    header = pd.read_excel(path_or_buffer, sheet_name=names[MODEL_DATASET_SHEET], nrows=0)
    return [str(column).strip().lower() for column in header.columns]


def is_model_dataset_workbook(path_or_buffer, extension: str) -> bool:
    """True for a lead-grain model dataset: a `model_dataset` sheet with one row per lead.

    The ad-set-day form of the same sheet has a `leads` count and no lead identity, so it is
    deliberately not accepted here - importing it would invent leads that have no names.
    """
    columns = set(_model_dataset_columns(path_or_buffer, extension))
    return bool(columns) and all(name in columns for name in MODEL_DATASET_REQUIRED)


def read_model_dataset_workbook(path_or_buffer, extension: str) -> dict:
    """Split a lead-grain model dataset into the frames the pipeline already ingests.

    The file carries two grains at once: the lead rows, and the ad set x day context repeated
    on every lead of that day. Rather than re-implement cleaning, each half is rendered back
    into the shape its existing reader expects and passed through it, so model datasets get
    the same ID protection, date parsing and attribution repair as the raw exports.
    """
    columns = _model_dataset_columns(path_or_buffer, extension)
    if not columns:
        raise ValueError(f"No '{MODEL_DATASET_SHEET}' sheet found in this workbook.")
    missing = [name for name in MODEL_DATASET_REQUIRED if name not in columns]
    if missing:
        raise ValueError(
            "The model_dataset sheet is missing required columns: " + ", ".join(missing) +
            ". It must be the lead-grain form, one row per lead."
        )
    names = {n.strip().lower(): n for n in _workbook_sheet_names(path_or_buffer, extension)}
    raw = pd.read_excel(path_or_buffer, sheet_name=names[MODEL_DATASET_SHEET],
                        dtype=str, keep_default_na=False)
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    source_rows = len(raw)

    def column(name: str, default: str = "") -> pd.Series:
        if name not in raw.columns:
            return pd.Series([default] * len(raw), index=raw.index)
        return raw[name].fillna("").astype(str).str.strip().replace({"nan": "", "nat": ""})

    stamps = pd.to_datetime(column("created_at"), errors="coerce", format="mixed")
    day = stamps.dt.normalize()

    # ---- leads
    traffic = pd.DataFrame({target: column(source) for source, target in MODEL_TO_TRAFFIC.items()})
    traffic["Created At"] = stamps
    traffic["Updated At"] = pd.to_datetime(column("updated_at"), errors="coerce", format="mixed")
    if not column("platform").ne("").any():
        traffic["Platform"] = "messenger"
    # spend is the ad set's whole day repeated per lead; the traffic cleaner stores it as
    # contextual only and summarizes with max, never sum, so passing it through is safe
    traffic["Amount spent (USD)"] = column("spend")
    traffic = traffic.loc[stamps.notna() & column("ad_set_id").ne("")].copy()

    buffer = io.BytesIO()
    traffic.to_csv(buffer, index=False)
    buffer.seek(0)
    traffic_clean = read_tabular(buffer, ".csv")

    # ---- ad set x day context
    # Prefer the dedicated sheet when the file carries one: collapsing the lead rows can only
    # ever recover days that produced a lead, which silently drops spend that produced none.
    ad_source = "lead_rows"
    zero_lead_days = 0
    ad_day_sheet = _read_ad_set_day_sheet(path_or_buffer, extension, names)
    if ad_day_sheet is not None:
        ad = ad_day_sheet
        repeated_rows = ad_day_rows = int(len(ad))
        ad_source = AD_SET_DAY_SHEET
        with_leads = set(zip(column("ad_set_id")[day.notna()], day[day.notna()]))
        zero_lead_days = int(sum(
            1 for set_id, stamp in zip(ad["Ad set ID"], ad["Day"])
            if (str(set_id), pd.Timestamp(stamp)) not in with_leads))
    else:
        ad = pd.DataFrame({target: column(source) for source, target in MODEL_TO_AD.items()})
        ad["Day"] = day
        ad["Delivery level"] = "adset"
        ad["Ad ID"] = ""
        ad = ad.loc[day.notna() & column("ad_set_id").ne("") & column("spend").ne("")].copy()
        repeated_rows = int(len(ad))
        ad = ad.drop_duplicates(["Day", "Ad set ID"], keep="first").reset_index(drop=True)
        ad_day_rows = int(len(ad))

    ad_clean = None
    ad_report: dict = {}
    if ad_day_rows:
        buffer = io.BytesIO()
        ad.to_csv(buffer, index=False)
        buffer.seek(0)
        ad_clean = read_ad_performance_tabular(buffer, ".csv")
        ad_report = ad_clean.attrs.get("cleaning_report", {})

    # ---- change events, when the columns have been filled in
    changes = _model_dataset_change_events(raw, column, day)

    report = {
        "source_rows": source_rows,
        "lead_rows": int(len(traffic_clean)),
        "lead_rows_skipped": int(source_rows - len(traffic)),
        "ad_set_day_rows": int(len(ad_clean)) if ad_clean is not None else 0,
        "context_rows_collapsed": repeated_rows - ad_day_rows,
        "ad_context_source": ad_source,
        "zero_lead_days": zero_lead_days,
        "unique_ad_sets": int(traffic_clean["UTM Ad Set ID"].nunique()) if len(traffic_clean) else 0,
        "total_spend": float(ad_clean["Amount spent (USD)"].fillna(0).sum()) if ad_clean is not None else 0.0,
        "date_min": stamps.min().date().isoformat() if stamps.notna().any() else None,
        "date_max": stamps.max().date().isoformat() if stamps.notna().any() else None,
        "change_events": int(len(changes)),
        "change_events_by_scope": (
            changes.groupby("scope").size().to_dict() if len(changes) else {}),
        "traffic_report": traffic_clean.attrs.get("cleaning_report", {}),
        "ad_report": ad_report,
        "columns_present": columns,
    }
    return {"traffic": traffic_clean, "ad": ad_clean, "changes": changes, "report": report}


def _read_ad_set_day_sheet(path_or_buffer, extension: str, names: dict[str, str]) -> pd.DataFrame | None:
    """The optional ad_set_days sheet, rendered into the Meta ad-performance shape.

    Returns None when the sheet is absent, which leaves the caller collapsing the lead rows.
    """
    if AD_SET_DAY_SHEET not in names:
        return None
    raw = pd.read_excel(path_or_buffer, sheet_name=names[AD_SET_DAY_SHEET],
                        dtype=str, keep_default_na=False)
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    missing = [name for name in AD_SET_DAY_REQUIRED if name not in raw.columns]
    if missing:
        raise ValueError(
            f"The '{AD_SET_DAY_SHEET}' sheet is missing required columns: " + ", ".join(missing))

    def column(name: str) -> pd.Series:
        if name not in raw.columns:
            return pd.Series([""] * len(raw), index=raw.index)
        return raw[name].fillna("").astype(str).str.strip().replace({"nan": "", "nat": ""})

    frame = pd.DataFrame({target: column(source) for source, target in MODEL_TO_AD.items()})
    frame["Day"] = pd.to_datetime(column("date"), errors="coerce", format="mixed").dt.normalize()
    frame["Delivery level"] = "adset"
    frame["Ad ID"] = ""
    frame = frame.loc[frame["Day"].notna() & column("ad_set_id").ne("") & column("spend").ne("")]
    return frame.drop_duplicates(["Day", "Ad set ID"], keep="last").reset_index(drop=True)


def _model_dataset_change_events(raw: pd.DataFrame, column, day: pd.Series) -> pd.DataFrame:
    """Change events carried inline on the model dataset, if the columns have been filled.

    Each filled cell names what changed **on that day** -- a point event, matching how the
    popover records one. Rows are marked confirmed: unlike the template's seeded guesses, a
    value typed into this sheet is an assertion.

    Baseline cells ("no change" / "no_recent_change") are skipped rather than stored: under
    point semantics that is the default for every unrecorded day, so a row asserting it would
    add nothing while occupying the one-event-per-day slot a real change may need.

    Until 2026-08-07 the type columns were read as the ad set's *state* on that day, so only
    days where the state differed from the previous row were kept. That dedup is gone: with
    point events, two budget changes a week apart are two events, and collapsing them would
    silently drop the second.
    """
    frames = []
    for source, scope in (
        ("ad_set_change_type", "ad_set"),
        ("ad_change_type", "ad"),
    ):
        values = column(source).str.lower()
        # Any non-blank, non-baseline cell marks a change on that day. The value itself is no
        # longer stored -- what the sheet is being read for is the DATE.
        usable = (
            values.ne("") & ~values.isin(UPLOAD_BASELINE_VALUES)
            & day.notna() & column("ad_set_id").ne("")
        )
        if not usable.any():
            continue
        frame = pd.DataFrame({
            "scope": scope,
            "event_date": day[usable],
            "ad_set_id": column("ad_set_id")[usable],
            "ad_id": column("ad_id")[usable] if scope == "ad" else "",
            "source": CONFIRMED_SOURCE,
            "confirmed_by": "",
            "notes": "model dataset upload",
        })
        frame = frame.sort_values(["ad_set_id", "event_date"])
        # one event per ad set per day -- the table's own grain, and the popover's
        frames.append(frame.drop_duplicates(["ad_set_id", "event_date"], keep="first"))
    if not frames:
        return pd.DataFrame(columns=[
            "scope", "event_date", "ad_set_id", "ad_id", "source",
            "confirmed_by", "notes"])
    return pd.concat(frames, ignore_index=True)


def is_holiday_proximity_columns(columns: Iterable[object]) -> bool:
    keys = {_header_key(column) for column in columns}
    return all(_header_key(column) in keys for column in HOLIDAY_PROXIMITY_REQUIRED)


def read_holiday_proximity_tabular(path_or_buffer, extension: str) -> pd.DataFrame:
    frame, source_columns = _read_raw_frame(path_or_buffer, extension)
    renamed: dict[object, str] = {}
    for column in frame.columns:
        key = _header_key(column)
        canonical = next((name for name in HOLIDAY_PROXIMITY_COLUMNS if _header_key(name) == key), None)
        if canonical:
            renamed[column] = canonical
    frame = frame.rename(columns=renamed)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in HOLIDAY_PROXIMITY_REQUIRED if column not in frame.columns]
    if missing:
        raise ValueError("Missing required holiday proximity columns: " + ", ".join(missing))

    source_rows = len(frame)
    cleaned = pd.DataFrame()
    cleaned["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    cleaned["holiday_proximity"] = frame["holiday_proximity"].fillna("").astype(str).str.strip()
    allowed = {HOLIDAY_PROXIMITY_BASELINE_BUCKET, *HOLIDAY_PROXIMITY_BUCKETS}
    invalid_bucket = cleaned["holiday_proximity"].ne("") & ~cleaned["holiday_proximity"].isin(allowed)
    if invalid_bucket.any():
        examples = sorted(cleaned.loc[invalid_bucket, "holiday_proximity"].dropna().astype(str).unique())[:5]
        raise ValueError("Unknown holiday_proximity bucket(s): " + ", ".join(examples))

    if "day" in frame.columns:
        cleaned["day"] = frame["day"].fillna("").astype(str).str.strip()
    else:
        cleaned["day"] = cleaned["date"].dt.day_name()
    if "is_holiday" in frame.columns:
        holiday_flag = pd.to_numeric(frame["is_holiday"], errors="coerce")
        cleaned["is_holiday"] = holiday_flag.fillna(0).astype(int).clip(lower=0, upper=1)
    else:
        cleaned["is_holiday"] = cleaned["holiday_proximity"].eq("during_holiday").astype(int)
    if "holiday_name" in frame.columns:
        cleaned["holiday_name"] = frame["holiday_name"].fillna("").astype(str).str.strip()
    else:
        cleaned["holiday_name"] = ""

    valid = cleaned["date"].notna() & cleaned["holiday_proximity"].ne("")
    cleaned = cleaned.loc[valid, HOLIDAY_PROXIMITY_COLUMNS].copy()
    cleaned = cleaned.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    cleaned.attrs["cleaning_report"] = {
        "source_rows": source_rows,
        "clean_rows": int(len(cleaned)),
        "excluded_rows": int(source_rows - int(valid.sum())),
        "duplicate_dates": int(valid.sum() - len(cleaned)),
        "source_columns": source_columns,
        "recognized_columns": [column for column in HOLIDAY_PROXIMITY_COLUMNS if column in frame.columns],
        "ignored_columns": [column for column in frame.columns if column not in HOLIDAY_PROXIMITY_COLUMNS],
        "holiday_count": int(cleaned["is_holiday"].sum()) if len(cleaned) else 0,
        "bucket_counts": cleaned["holiday_proximity"].value_counts().to_dict(),
        "date_min": cleaned["date"].min().date().isoformat() if len(cleaned) else None,
        "date_max": cleaned["date"].max().date().isoformat() if len(cleaned) else None,
    }
    return cleaned


def detect_upload_type_from_columns(columns: Iterable[object]) -> str:
    # Canonicalize through KNOWN_HEADERS first so aliased headers (e.g. "Created" for
    # "Created At") count toward detection the same way they do once read_tabular renames them --
    # otherwise a file the importer can actually handle gets rejected before it gets that far.
    keys = {_header_key(KNOWN_HEADERS.get(_header_key(column), column)) for column in columns}
    ad_keys = {_header_key(AD_KNOWN_HEADERS.get(_header_key(column), column)) for column in columns}
    customer_score = sum(1 for column in SOURCE_REQUIRED_COLUMNS if _header_key(column) in keys)
    ad_score = sum(1 for column in ["Ad set ID", "Day"] if _header_key(column) in ad_keys)
    # Either campaign column will do. Meta's ad-set-level exports routinely carry only
    # `Campaign name`, and _repair_ad_performance_attribution reconstructs the ID from it
    # against known campaigns -- so requiring the ID here rejected files the importer could
    # already handle, before the repair ever got a chance to run. Rows whose campaign still
    # cannot be resolved are rejected later, so nothing unattributed reaches the database.
    has_campaign = _header_key("Campaign ID") in ad_keys or _header_key("Campaign name") in ad_keys
    has_spend = _header_key("Amount spent (USD)") in ad_keys
    if customer_score == len(SOURCE_REQUIRED_COLUMNS):
        return CUSTOMER_TRAFFIC_TYPE
    if ad_score == 2 and has_campaign and has_spend:
        return AD_PERFORMANCE_TYPE
    if is_leadlens_derived_columns(columns):
        return LEADLENS_DERIVED_TYPE
    if is_holiday_proximity_columns(columns):
        return HOLIDAY_PROXIMITY_TYPE
    raise ValueError(
        "File type could not be detected. Upload a customer traffic export, Meta ad performance "
        "CSV with Amount spent (USD), LeadLens derived-variable CSV, holiday proximity workbook, "
        "or change-log workbook."
    )


def read_tabular(path_or_buffer, extension: str) -> pd.DataFrame:
    frame, source_columns = _read_raw_frame(path_or_buffer, extension)
    renamed: dict[object, str] = {}
    for column in frame.columns:
        canonical = KNOWN_HEADERS.get(_header_key(column))
        if canonical:
            renamed[column] = canonical
    frame = frame.rename(columns=renamed)
    frame.columns = [str(column).strip() for column in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("The file contains duplicate columns after header normalization.")
    missing = [column for column in SOURCE_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))

    source_rows = len(frame)
    frame["_source_row"] = range(2, source_rows + 2)
    for column in [*REQUIRED_COLUMNS, *OPTIONAL_SOURCE_COLUMNS]:
        if column not in frame.columns:
            frame[column] = np.nan if column == "Amount spent (USD)" else ""
    text_columns = [column for column in frame.columns if column not in {"_source_row", "Amount spent (USD)"}]
    for column in text_columns:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    original_ad_titles = frame["FB Ad Title"].copy()
    frame["FB Ad Title"] = frame["FB Ad Title"].map(_clean_fb_ad_title)
    ad_titles_cleaned = int((original_ad_titles != frame["FB Ad Title"]).sum())

    id_like_columns = [*ID_COLUMNS, "FB Ad ID", "FB Post ID"]
    scientific_id_values = int(sum(
        frame[column].map(_looks_like_scientific_id).sum()
        for column in id_like_columns
        if column in frame.columns
    ))
    for column in ID_COLUMNS:
        frame[column] = [
            _safe_id(value, column, int(row_number))
            for value, row_number in zip(frame[column].tolist(), frame["_source_row"].tolist())
        ]
    for column in ["FB Ad ID", "FB Post ID"]:
        frame[column] = [
            _safe_id(value, column, int(row_number))
            for value, row_number in zip(frame[column].tolist(), frame["_source_row"].tolist())
        ]

    frame[SOURCE_ID_COLUMN] = frame["ID"].fillna("").astype(str).str.strip()
    duplicate_source_id = frame[SOURCE_ID_COLUMN].ne("") & frame[SOURCE_ID_COLUMN].duplicated(keep="first")
    filled_ad_id = frame["UTM Ad ID"].eq("") & frame["FB Ad ID"].ne("")
    frame.loc[filled_ad_id, "UTM Ad ID"] = frame.loc[filled_ad_id, "FB Ad ID"]

    recovery_method = pd.Series("", index=frame.index, dtype="string")
    missing_ad_set = frame["UTM Ad Set ID"].eq("")
    for key in ["UTM Ad ID", *RECOVERY_COLUMNS]:
        mapping = _unique_attribution_map(frame, key)
        candidates = missing_ad_set & recovery_method.eq("") & frame[key].ne("") & frame[key].isin(mapping)
        for index in frame.index[candidates]:
            ad_set, campaign_id, campaign = mapping[str(frame.at[index, key])]
            frame.at[index, "UTM Ad Set ID"] = ad_set
            if not frame.at[index, "UTM Campaign ID"]:
                frame.at[index, "UTM Campaign ID"] = campaign_id
            if not frame.at[index, "UTM Campaign"]:
                frame.at[index, "UTM Campaign"] = campaign
            recovery_method.at[index] = key

    historical_mapping = _historical_attribution_map()
    historical_candidates = recovery_method.eq("") & frame["UTM Campaign"].ne("") & frame["FB Ad Title"].ne("")
    for index in frame.index[historical_candidates]:
        key = (_lookup_text(frame.at[index, "UTM Campaign"]), _lookup_text(frame.at[index, "FB Ad Title"]))
        attribution = historical_mapping.get(key)
        if not attribution:
            continue
        ad_set, campaign_id, campaign, ad_id = attribution
        recovered_any = False
        if not frame.at[index, "UTM Ad Set ID"]:
            frame.at[index, "UTM Ad Set ID"] = ad_set
            recovered_any = True
        if not frame.at[index, "UTM Campaign ID"]:
            frame.at[index, "UTM Campaign ID"] = campaign_id
            recovered_any = True
        if not frame.at[index, "UTM Ad ID"] and ad_id:
            frame.at[index, "UTM Ad ID"] = ad_id
            recovered_any = True
        if not frame.at[index, "UTM Campaign"] and campaign:
            frame.at[index, "UTM Campaign"] = campaign
            recovered_any = True
        if recovered_any:
            recovery_method.at[index] = "history:campaign+ad_title"

    campaign_mapping = _historical_campaign_map()
    campaign_candidates = recovery_method.eq("") & frame["UTM Campaign"].ne("") & frame["UTM Ad Set ID"].eq("")
    for index in frame.index[campaign_candidates]:
        attribution = campaign_mapping.get(_lookup_text(frame.at[index, "UTM Campaign"]))
        if not attribution:
            continue
        ad_set, campaign_id, campaign = attribution
        frame.at[index, "UTM Ad Set ID"] = ad_set
        if not frame.at[index, "UTM Campaign ID"]:
            frame.at[index, "UTM Campaign ID"] = campaign_id
        if not frame.at[index, "UTM Campaign"] and campaign:
            frame.at[index, "UTM Campaign"] = campaign
        recovery_method.at[index] = "history:campaign"

    parsed = pd.to_datetime(frame["Created At"], format="mixed", errors="coerce")
    frame["Created At"] = parsed
    frame["Updated At"] = pd.to_datetime(frame["Updated At"], format="mixed", errors="coerce")
    spend_text = frame["Amount spent (USD)"].astype(str).str.replace(r"[$,]", "", regex=True).str.strip()
    frame["Amount spent (USD)"] = pd.to_numeric(spend_text, errors="coerce")

    invalid_date = frame["Created At"].isna()
    unattributed = frame["UTM Ad Set ID"].eq("")
    identity_keys = frame.apply(
        lambda row: "|".join(_lead_identity_parts(
            row["Created At"], row["Customer Name"], row["Status"], row["UTM Campaign ID"],
            row["UTM Ad Set ID"], row["UTM Ad ID"], row["FB Ad Title"],
        )),
        axis=1,
    )
    duplicate_lead_identity = identity_keys.duplicated(keep="first")
    duplicate_rows = duplicate_source_id | duplicate_lead_identity
    keep = ~(duplicate_rows | invalid_date | unattributed)
    cleaned = frame.loc[keep, [SOURCE_ID_COLUMN, *REQUIRED_COLUMNS]].copy().reset_index(drop=True)
    if cleaned.empty:
        if scientific_id_values:
            raise ValueError(
                "No model-ready rows remain because one or more IDs use scientific notation. "
                "Export identifier columns as text and upload the file again."
            )
        raise ValueError("No model-ready rows remain after cleaning. Check Created At and UTM Ad Set ID values.")

    recovered = int(recovery_method.ne("").sum())
    report = {
        "source_rows": int(source_rows),
        "clean_rows": int(len(cleaned)),
        "excluded_rows": int((~keep).sum()),
        "recovered_rows": recovered,
        "duplicates_removed": int(duplicate_rows.sum()),
        "duplicate_source_ids": int(duplicate_source_id.sum()),
        "duplicate_lead_identities": int(duplicate_lead_identity.sum()),
        "ad_titles_cleaned": ad_titles_cleaned,
        "scientific_id_values": scientific_id_values,
        "invalid_dates": int(invalid_date.sum()),
        "unattributed_rows": int(unattributed.sum()),
        "ad_ids_filled": int(filled_ad_id.sum()),
        "source_columns": source_columns,
        "recognized_columns": list(dict.fromkeys(
            KNOWN_HEADERS[_header_key(column)] for column in source_columns if _header_key(column) in KNOWN_HEADERS
        )),
        "ignored_columns": [column for column in source_columns if _header_key(column) not in KNOWN_HEADERS],
        "recovery_methods": {str(key): int(value) for key, value in recovery_method[recovery_method.ne("")].value_counts().items()},
    }
    cleaned.attrs["cleaning_report"] = report
    return cleaned


CURRENCY_SYMBOLS = "$€£¥₩₹"
UNICODE_SPACES = ("\xa0", " ", " ", " ")
DASH_CHARACTERS = {"-", "‐", "‑", "‒", "–", "—", "―"}


def _safe_number(value: object) -> float | None:
    """Parse a metric cell, including the accounting-formatted currency Excel writes.

    Meta exports opened in Excel arrive as ' $0.05 ', ' $-   ' or '($5.00)'. A bare dash is
    the accounting notation for zero, so it only reads as 0.0 when a currency symbol was
    present; a dash anywhere else stays ambiguous and returns None.
    """
    if pd.isna(value):
        return None
    text = str(value)
    for space in UNICODE_SPACES:
        text = text.replace(space, " ")
    text = text.strip()
    if text == "" or text.casefold() in {"nan", "none", "null", "n/a", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    had_currency = any(symbol in text for symbol in CURRENCY_SYMBOLS)
    if had_currency:
        text = "".join(character for character in text if character not in CURRENCY_SYMBOLS).strip()
    text = text.replace(",", "").strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    if text in DASH_CHARACTERS:
        return 0.0 if had_currency else None
    if text == "":
        return None
    parsed = pd.to_numeric(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return -float(parsed) if negative else float(parsed)


def _split_ad_set_budget(value: object) -> tuple[object, str]:
    """Return (budget_amount, budget_type) from exports like "$3.50 / Daily"."""
    if pd.isna(value):
        return value, ""
    text = str(value).strip()
    if not text:
        return value, ""
    if "/" not in text:
        return value, ""
    amount, _, budget_type = text.partition("/")
    return amount.strip(), re.sub(r"\s+", " ", budget_type.strip())


def _status_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    return re.sub(r"\s+", " ", text).casefold()


def _repair_ad_performance_attribution(frame: pd.DataFrame) -> dict[str, int]:
    options_by_campaign = _historical_campaign_ad_set_options()
    recovered_campaign_ids = 0
    recovered_ad_set_ids = 0
    inferred_multi_ad_sets = 0
    unresolved_rows = 0

    def sort_metric(index: int, column: str) -> float:
        value = frame.at[index, column]
        return 0.0 if pd.isna(value) else float(value)

    for campaign_key, campaign_options in options_by_campaign.items():
        if not campaign_options:
            continue
        campaign_mask = frame["Campaign name"].map(_lookup_text).eq(campaign_key)
        if not campaign_mask.any():
            continue
        campaign_id = str(campaign_options[0]["campaign_id"])
        missing_campaign = campaign_mask & frame["Campaign ID"].eq("")
        recovered_campaign_ids += int(missing_campaign.sum())
        frame.loc[missing_campaign, "Campaign ID"] = campaign_id

        missing_ad_set = campaign_mask & frame["Ad set ID"].eq("")
        if not missing_ad_set.any():
            continue
        ordered_options = sorted(
            campaign_options,
            key=lambda item: (-int(item["lead_count"]), str(item["ad_set_id"])),
        )
        for _, group in frame.loc[missing_ad_set].groupby("Day", sort=False):
            option_count = len(ordered_options)
            if option_count == 1:
                frame.loc[group.index, "Ad set ID"] = str(ordered_options[0]["ad_set_id"])
                recovered_ad_set_ids += len(group)
                continue
            sorted_indexes = sorted(
                group.index,
                key=lambda idx: (
                    -sort_metric(idx, "Amount spent (USD)"),
                    -sort_metric(idx, "Leads"),
                    int(frame.at[idx, "_source_row"]),
                ),
            )
            if len(sorted_indexes) > option_count:
                unresolved_rows += len(sorted_indexes)
                continue
            for index, option in zip(sorted_indexes, ordered_options):
                frame.at[index, "Ad set ID"] = str(option["ad_set_id"])
                recovered_ad_set_ids += 1
                inferred_multi_ad_sets += 1

    unresolved_rows += int((frame["Campaign ID"].eq("") | frame["Ad set ID"].eq("")) .sum())
    return {
        "recovered_campaign_ids": recovered_campaign_ids,
        "recovered_ad_set_ids": recovered_ad_set_ids,
        "inferred_multi_ad_sets": inferred_multi_ad_sets,
        "unresolved_attribution_rows": unresolved_rows,
    }


def _delivery_status_rank(value: object) -> int:
    text = str(value or "").strip()
    return DELIVERY_STATUS_PRIORITY.index(text) if text in DELIVERY_STATUS_PRIORITY else len(DELIVERY_STATUS_PRIORITY)


def _is_ad_grain(frame: pd.DataFrame) -> bool:
    """True when the export breaks spend out per ad rather than per ad set.

    Only explicit signals count. Repeated grain keys are deliberately not treated as evidence:
    exports whose IDs arrived as scientific notation have those columns blanked, so their rows
    collide by key and it is `_repair_ad_performance_attribution` that separates them again.
    """
    return bool(frame["Delivery level"].eq("ad").any() or frame["Ad ID"].astype(str).str.strip().ne("").any())


def _extract_ad_level_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-ad rows to persist alongside the ad-set rollup, or empty for ad-set-grain exports."""
    empty = pd.DataFrame(columns=[
        "day", "campaign_id", "ad_set_id", "ad_id", "delivery_status",
        "amount_spent_usd", "impressions", "leads",
    ])
    if not _is_ad_grain(frame):
        return empty
    working = frame[frame["Ad ID"].astype(str).str.strip().ne("")].copy()
    if working.empty:
        return empty
    out = pd.DataFrame({
        "day": pd.to_datetime(working["Day"], errors="coerce"),
        "campaign_id": working["Campaign ID"].astype(str).str.strip(),
        "ad_set_id": working["Ad set ID"].astype(str).str.strip(),
        "ad_id": working["Ad ID"].astype(str).str.strip(),
        "delivery_status": working["Delivery status"].astype(str).str.strip(),
        "amount_spent_usd": pd.to_numeric(working["Amount spent (USD)"], errors="coerce"),
        "impressions": pd.to_numeric(working["Impressions"], errors="coerce"),
        "leads": pd.to_numeric(working["Leads"], errors="coerce"),
    })
    out = out[out["day"].notna() & out["ad_set_id"].ne("") & out["ad_id"].ne("")]
    return out.drop_duplicates(["day", "ad_set_id", "ad_id"], keep="last").reset_index(drop=True)


def _store_ad_level_rows(db: sqlite3.Connection, upload_id: int, rows: pd.DataFrame) -> int:
    if rows is None or rows.empty:
        return 0
    now = utc_now()
    written = 0
    for _, row in rows.iterrows():
        db.execute(
            """INSERT INTO daily_ad_level_performance(
                 upload_id, day, campaign_id, ad_set_id, ad_id, delivery_status,
                 amount_spent_usd, impressions, leads, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(day, ad_set_id, ad_id) DO UPDATE SET
                 upload_id=excluded.upload_id, campaign_id=excluded.campaign_id,
                 delivery_status=excluded.delivery_status,
                 amount_spent_usd=excluded.amount_spent_usd,
                 impressions=excluded.impressions, leads=excluded.leads,
                 updated_at=excluded.updated_at""",
            (
                upload_id,
                pd.Timestamp(row["day"]).date().isoformat(),
                str(row["campaign_id"]),
                str(row["ad_set_id"]),
                str(row["ad_id"]),
                str(row["delivery_status"]),
                _float_ready(row["amount_spent_usd"]),
                _float_ready(row["impressions"]),
                _float_ready(row["leads"]),
                now,
                now,
            ),
        )
        written += 1
    return written




def _load_ad_level_frame(db: sqlite3.Connection | None = None) -> pd.DataFrame:
    owns = db is None
    conn = sqlite3.connect(DB_PATH) if owns else db
    try:
        rows = conn.execute(
            "SELECT day, ad_set_id, ad_id, delivery_status FROM daily_ad_level_performance"
        ).fetchall()
    except sqlite3.OperationalError:
        return pd.DataFrame(columns=["day", "ad_set_id", "ad_id", "delivery_status"])
    finally:
        if owns:
            conn.close()
    if not rows:
        return pd.DataFrame(columns=["day", "ad_set_id", "ad_id", "delivery_status"])
    frame = pd.DataFrame(rows, columns=["day", "ad_set_id", "ad_id", "delivery_status"])
    frame["day"] = pd.to_datetime(frame["day"], errors="coerce")
    return frame[frame["day"].notna()]


# Change TYPE was removed entirely on 2026-08-11, by request. Declared variables 9
# (ad_set_change_type) and 10 (ad_change_type) are gone from the model, every table, the
# correlation matrix, the variable dictionary and the recorder; the `change_type` column was
# dropped from `change_events` and its seven recorded values deleted. What survives is the
# EVENT DATE, which is all variables 6 and 7 ever needed -- recency is derived from "when did
# something last change", never from what changed. The declared set is now eight variables,
# numbered 1-8. Backup taken before the wipe: data/leadlens.db.bak-before-type-removal-*.
#
# A change is still a POINT event (2026-08-07): it happened on a date, it is not a state
# spanning a range.
CHANGE_SCOPES = ("ad_set", "ad")

# Variables 6 and 7 report a BUCKET, not a day count (requested 2026-08-11). Edges are
# inclusive upper bounds and contiguous, so every non-negative day lands in exactly one.
# Anything at 60+ days has worn off and folds back into the baseline, which is also what a
# day *before* an ad set's first recorded event reports -- "nothing recent" covers both
# "never changed" and "changed too long ago to matter", deliberately one category.
RECENCY_BUCKET_EDGES = ((3, "0_3_days"), (7, "4_7_days"), (14, "8_14_days"), (59, "15_59_days"))
NO_RECENT_CHANGE_BUCKET = "no_recent_change"
# Ordered oldest-known-first for the correlation matrix's ordinal encoding: the scale is
# monotone in time-since-change, with the baseline as the far end.
RECENCY_BUCKETS = (*(name for _, name in RECENCY_BUCKET_EDGES), NO_RECENT_CHANGE_BUCKET)
RECENCY_BUCKET_DAY_RANGES = {
    "0_3_days": (0, 3),
    "4_7_days": (4, 7),
    "8_14_days": (8, 14),
    "15_59_days": (15, 59),
}
RECENCY_BUCKET_FEATURE_VALUES = {
    name: (low + high) / 2.0
    for name, (low, high) in RECENCY_BUCKET_DAY_RANGES.items()
}
RECENCY_BUCKET_FEATURE_VALUES[NO_RECENT_CHANGE_BUCKET] = 60.0


def recency_bucket(days: object) -> str:
    """Bucket a days-since-last-change count. `None`/NaN means no prior event: the baseline."""
    if days is None or pd.isna(days):
        return NO_RECENT_CHANGE_BUCKET
    for edge, name in RECENCY_BUCKET_EDGES:
        if days <= edge:
            return name
    return NO_RECENT_CHANGE_BUCKET


# Sheet names in MODEL_DATASET_TEMPLATE.xlsx, and the scope each one records.
CHANGE_LOG_SHEETS = {"changelog_ad_set": "ad_set", "changelog_ad": "ad"}
# Uploaded sheets still carry a change-type column. Since the type removal it is read for one
# purpose only -- telling "a change happened here" from "I checked, nothing changed" -- and
# then discarded. Any other non-blank value counts as an event regardless of vocabulary, so a
# sheet still using the pre-2026-08-11 names (budget_change, ...) imports its DATES cleanly
# instead of being rejected wholesale.
UPLOAD_BASELINE_VALUES = ("no_change", "no_recent_change")
# Only this marks a row as a recorded fact. A row still carrying the detector's own label
# is stored for the audit trail but excluded from every feature.
CONFIRMED_SOURCE = "confirmed"


@lru_cache(maxsize=64)
def _recorded_change_events(scope: str, ad_set_id: str | None = None) -> tuple[pd.Timestamp, ...]:
    """Confirmed change DATES for one scope, ascending and deduplicated.

    Since the type removal (2026-08-11) an event is just a date -- there is no kind to carry,
    so this returns bare timestamps rather than (day, kind) pairs. Returns empty when nothing
    has been confirmed for the ad set; recording events for one ad set does not affect others.
    """
    try:
        with connect() as db:
            sql = "SELECT event_date FROM change_events WHERE scope=? AND source=?"
            params: list[object] = [scope, CONFIRMED_SOURCE]
            if ad_set_id is not None:
                sql += " AND ad_set_id=?"
                params.append(str(ad_set_id))
            sql += " ORDER BY event_date"
            rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return ()
    days = set()
    for row in rows:
        day = pd.to_datetime(row["event_date"], errors="coerce")
        if not pd.isna(day):
            days.add(day.normalize())
    return tuple(sorted(days))


def _has_recorded_changes(scope: str) -> bool:
    return bool(_recorded_change_events(scope))


def _clear_change_caches() -> None:
    _recorded_change_events.cache_clear()
    _confirmed_ad_set_starts.cache_clear()


def _resolve_change_state(
    events: Iterable[pd.Timestamp], dates: pd.DatetimeIndex,
) -> np.ndarray:
    """Days since the most recent change on or before each date, carried forward.

    0 on the event day, 1 the next day, and so on. A date with no prior event reports 0 as
    well -- callers that need to tell "changed today" from "never changed" bucket the value
    through `recency_bucket`, where the two land in `0_3_days` and `no_recent_change`
    respectively.

    Returned raw (a day count) rather than bucketed because the model still consumes the
    continuous form; the bucketed form is what the tables show. Until 2026-08-11 this also
    returned one-hot change-type indicators, removed along with declared variables 9 and 10.
    """
    ordered = sorted(events)
    recency = np.zeros(len(dates), dtype=float)
    for index, date in enumerate(dates):
        prior = [day for day in ordered if day <= date]
        if prior:
            recency[index] = float((date - prior[-1]).days)
    return recency


def _ad_change_features(
    dates: pd.DatetimeIndex, *, spend_frame: pd.DataFrame | None = None,
    ad_set: str | None = None, ad_sets: tuple[str, ...] | None = None,
) -> dict[str, np.ndarray]:
    """Declared variable 6 (ad_change_recency).

    Confirmed change-log rows only -- the step-shift detector fallback (`_ad_change_events`)
    was found to produce wrong values and was removed 2026-08-06 (see Vault/Features/
    Ad-Decision-Engine.md). An ad set with no confirmed rows reports zero recency rather than
    an inferred event, until real dates are recorded via the "Ad change" tab of the Ad set
    change popover or a confirmed change-log upload.

    Variable 10 (ad_change_type) used to be produced here too; it was removed 2026-08-11.

    `ad_sets` narrows to a group (a campaign) while keeping the pooled-event encoding the
    whole-portfolio call already uses, so campaign and portfolio scopes stay comparable.
    """
    imported = _imported_recency_feature_values(
        spend_frame, dates, "ad_change_recency_imported", ad_set=ad_set,
    )
    if imported is not None:
        return {"ad_change_recency": imported}
    if ad_sets is not None:
        recorded: set[pd.Timestamp] = set()
        for set_id in ad_sets:
            recorded.update(_recorded_change_events("ad", set_id))
        events: Iterable[pd.Timestamp] = recorded
    else:
        events = _recorded_change_events("ad", ad_set)
    return {"ad_change_recency": _resolve_change_state(events, dates)}


def _rollup_ad_rows_to_ad_sets(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Collapse per-ad rows onto the ad-set grain the rest of the pipeline stores.

    Deduplicating instead of aggregating here would keep one arbitrary ad per ad-set-day and
    silently discard the rest of the spend, so every additive metric is summed. Reach is a
    deduplicated person count and cannot be summed across ads, so it (and the frequency ratio
    derived from it) is dropped rather than reported wrong.
    """
    report = {
        "ad_grain_input": False,
        "ad_rows_collapsed": 0,
        "ad_set_day_rows": int(len(frame)),
        "negative_source_rows": 0,
        "budget_conflict_groups": 0,
    }
    if not _is_ad_grain(frame):
        return frame, report

    negative = pd.Series(False, index=frame.index)
    for column in AD_NUMERIC_COLUMNS:
        negative = negative | frame[column].lt(0).fillna(False)
    report["negative_source_rows"] = int(negative.sum())
    working = frame.loc[~negative].copy()

    grouped = working.groupby(AD_ROLLUP_KEY, dropna=False, sort=False)
    report["budget_conflict_groups"] = int((grouped["Ad Set Budget"].nunique(dropna=True) > 1).sum())

    def _sum(series: pd.Series) -> float:
        return series.sum(min_count=1)

    def _first_text(series: pd.Series) -> str:
        return next((str(value).strip() for value in series if str(value).strip()), "")

    def _best_status(series: pd.Series) -> str:
        values = [str(value).strip() for value in series if str(value).strip()]
        return min(values, key=_delivery_status_rank) if values else ""

    specification: dict[str, object] = {column: _sum for column in AD_ADDITIVE_COLUMNS}
    specification["Ad Set Budget"] = "max"
    specification["Ad Set Budget Type"] = _first_text
    specification["days_since_adset_started"] = "max"
    specification["ad_set_change_recency"] = _first_text
    specification["ad_change_recency"] = _first_text
    specification["Delivery status"] = _best_status
    specification["Reporting starts"] = "min"
    specification["Reporting ends"] = "max"
    specification["_source_row"] = "min"
    aggregated = grouped.agg(specification).reset_index()

    # Reach/frequency are only unrecoverable when more than one ad actually shares an
    # ad-set-day slot (the sum would double-count reached people). Most ad-grain exports
    # have exactly one ad per ad-set per day, in which case the "rollup" is really just a
    # pass-through and the real per-day value should survive, not get nulled needlessly.
    group_sizes = grouped.size()
    single_ad_values = working.groupby(AD_ROLLUP_KEY, dropna=False, sort=False)[AD_NON_ADDITIVE_COLUMNS].first()
    single_ad_values = single_ad_values.where(group_sizes.eq(1), axis=0).reset_index()
    aggregated = aggregated.merge(single_ad_values, on=AD_ROLLUP_KEY, how="left")
    for column, (numerator, denominator) in AD_DERIVED_RATE_COLUMNS.items():
        denominators = aggregated[denominator]
        aggregated[column] = (aggregated[numerator] / denominators.where(denominators.fillna(0) > 0)).astype(float)
    aggregated["Delivery level"] = "adset"
    aggregated["Ad ID"] = ""

    report["ad_grain_input"] = True
    report["ad_rows_collapsed"] = int(len(working) - len(aggregated))
    report["ad_set_day_rows"] = int(len(aggregated))
    return aggregated.loc[:, [
        *AD_PERFORMANCE_COLUMNS, "_source_row", *AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS,
    ]].copy(), report


def read_ad_performance_tabular(path_or_buffer, extension: str) -> pd.DataFrame:
    frame, source_columns = _read_raw_frame(path_or_buffer, extension)
    renamed: dict[object, str] = {}
    for column in frame.columns:
        canonical = AD_KNOWN_HEADERS.get(_header_key(column))
        if canonical:
            renamed[column] = canonical
    frame = frame.rename(columns=renamed)
    frame.columns = [str(column).strip() for column in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("The file contains duplicate columns after header normalization.")
    # `Campaign ID` is satisfied by `Campaign name`: the column is created empty below and
    # _repair_ad_performance_attribution fills it in from the campaign name against campaigns
    # already in the database. Rows it cannot resolve are still rejected further down, so this
    # loosens what we accept, not what we store.
    missing = [
        column
        for column in AD_PERFORMANCE_REQUIRED
        if column not in frame.columns
        and not (column == "Campaign ID" and "Campaign name" in frame.columns)
    ]
    if missing:
        raise ValueError("Missing required ad performance columns: " + ", ".join(missing))

    source_rows = len(frame)
    frame["_source_row"] = range(2, source_rows + 2)
    frame = frame.dropna(how="all").copy()
    for column in AD_PERFORMANCE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan if column in [*AD_NUMERIC_COLUMNS, *AD_DATE_COLUMNS] else ""
    for column in AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan if column == "days_since_adset_started" else ""
    for column in [*AD_TEXT_COLUMNS, *AD_ID_COLUMNS]:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    if "Ad Set Budget" in frame.columns:
        combined_budget = frame["Ad Set Budget"].map(_split_ad_set_budget)
        budget_amount = combined_budget.map(lambda item: item[0])
        budget_type = combined_budget.map(lambda item: item[1])
        fill_type = budget_type.ne("") & frame["Ad Set Budget Type"].eq("")
        frame["Ad Set Budget"] = budget_amount
        frame.loc[fill_type, "Ad Set Budget Type"] = budget_type[fill_type]
    for column in AD_ID_COLUMNS:
        frame[column] = [
            _safe_id(value, column, int(row_number))
            for value, row_number in zip(frame[column].tolist(), frame["_source_row"].tolist())
        ]
    for column in AD_TEXT_COLUMNS:
        frame[column] = frame[column].fillna("").astype(str).map(lambda value: re.sub(r"\s+", " ", value.strip()))
    for column in ["Delivery status", "Delivery level"]:
        frame[column] = frame[column].map(_status_text)
    for column in AD_DATE_COLUMNS:
        frame[column] = pd.to_datetime(frame[column], format="mixed", errors="coerce")
    for column in AD_NUMERIC_COLUMNS:
        frame[column] = frame[column].map(_safe_number).astype(float)
    frame["days_since_adset_started"] = pd.to_numeric(frame["days_since_adset_started"], errors="coerce")
    for column in ("ad_set_change_recency", "ad_change_recency"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
        frame.loc[~frame[column].isin(RECENCY_BUCKETS), column] = ""
    frame["Ad ID"] = frame["Ad ID"].fillna("").astype(str).str.strip()
    # Snapshot the per-ad rows before the rollup collapses them away. Ad-level identity is the
    # only source for ad_added / ad_paused / ad_swapped, and it cannot be recovered afterwards.
    ad_level_rows = _extract_ad_level_rows(frame)
    # Roll up before repairing attribution: the repair matches rows to ad sets per campaign-day
    # and would see far more rows than there are candidate ad sets at ad grain.
    frame, rollup_report = _rollup_ad_rows_to_ad_sets(frame)
    recovery_report = _repair_ad_performance_attribution(frame)

    missing_spend = frame["Amount spent (USD)"].isna()
    missing_required = frame["Campaign ID"].eq("") | frame["Ad set ID"].eq("") | frame["Day"].isna() | missing_spend
    negative_metrics = pd.Series(False, index=frame.index)
    for column in AD_NUMERIC_COLUMNS:
        negative_metrics = negative_metrics | frame[column].lt(0).fillna(False)
    keep = ~(missing_required | negative_metrics)
    cleaned = frame.loc[keep, [
        *AD_PERFORMANCE_COLUMNS, "_source_row", *AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS,
    ]].copy().reset_index(drop=True)
    if cleaned.empty:
        raise ValueError("No valid ad performance rows remain after cleaning. Check Day, Campaign ID, Ad set ID, and Amount spent (USD).")

    grain_duplicates = int(cleaned.duplicated(["Day", "Campaign ID", "Ad set ID"], keep="last").sum())
    cleaned = cleaned.drop_duplicates(["Day", "Campaign ID", "Ad set ID"], keep="last").reset_index(drop=True)
    report = {
        "source_rows": int(source_rows),
        "clean_rows": int(len(cleaned)),
        "excluded_rows": int((~keep).sum()),
        "rejected_rows": int(missing_required.sum() + negative_metrics.sum()),
        "missing_required_rows": int(missing_required.sum()),
        "missing_spend_rows": int(missing_spend.sum()),
        "negative_metric_rows": int(negative_metrics.sum()),
        "duplicates_removed": grain_duplicates,
        **rollup_report,
        **recovery_report,
        "source_columns": source_columns,
        "recognized_columns": list(dict.fromkeys(
            AD_KNOWN_HEADERS[_header_key(column)] for column in source_columns if _header_key(column) in AD_KNOWN_HEADERS
        )),
        "ignored_columns": [
            column for column in source_columns
            if _header_key(column) not in AD_KNOWN_HEADERS
            and _header_key(column) not in {_header_key(name) for name in AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS}
        ],
        "unique_campaigns": int(cleaned["Campaign ID"].nunique()),
        "unique_ad_sets": int(cleaned["Ad set ID"].nunique()),
        "total_spend": float(cleaned["Amount spent (USD)"].fillna(0).sum()),
        "total_ad_leads": float(cleaned["Leads"].fillna(0).sum()),
        "total_impressions": float(cleaned["Impressions"].fillna(0).sum()),
        "total_link_clicks": float(cleaned["Link clicks"].fillna(0).sum()),
        "blank_metrics": {
            column: int(cleaned[column].isna().sum())
            for column in AD_NUMERIC_COLUMNS
            if int(cleaned[column].isna().sum())
        },
        "ad_level_rows_captured": int(len(ad_level_rows)),
    }
    cleaned.attrs["cleaning_report"] = report
    cleaned.attrs["ad_level_rows"] = ad_level_rows
    return cleaned


def _backfill_imported_ad_performance_derived_values(db: sqlite3.Connection) -> int:
    try:
        uploads = db.execute(
            "SELECT id, stored_path FROM raw_uploads WHERE file_type=?",
            (AD_PERFORMANCE_TYPE,),
        ).fetchall()
    except sqlite3.Error:
        return 0
    updated = 0
    for upload in uploads:
        path = Path(upload["stored_path"])
        if not path.exists():
            continue
        try:
            _, source_columns = _read_raw_frame(path, path.suffix)
        except Exception:
            continue
        if not is_leadlens_derived_columns(source_columns):
            continue
        try:
            frame = read_ad_performance_tabular(path, path.suffix)
        except Exception:
            continue
        for _, row in frame.iterrows():
            days = row.get("days_since_adset_started")
            ad_set_recency = str(row.get("ad_set_change_recency") or "").strip() or None
            ad_recency = str(row.get("ad_change_recency") or "").strip() or None
            if (days is None or pd.isna(days)) and ad_set_recency is None and ad_recency is None:
                continue
            cursor = db.execute(
                """UPDATE daily_ad_performance
                   SET days_since_adset_started_imported=?,
                       ad_set_change_recency_imported=?,
                       ad_change_recency_imported=?
                   WHERE upload_id=? AND day=? AND campaign_id=? AND ad_set_id=?
                     AND days_since_adset_started_imported IS NULL
                     AND ad_set_change_recency_imported IS NULL
                     AND ad_change_recency_imported IS NULL""",
                (
                    _float_ready(days),
                    ad_set_recency,
                    ad_recency,
                    int(upload["id"]),
                    _date_ready(row["Day"]),
                    str(row["Campaign ID"]).strip(),
                    str(row["Ad set ID"]).strip(),
                ),
            )
            updated += int(cursor.rowcount or 0)
    return updated


def preview_file(content: bytes, filename: str) -> dict:
    extension = Path(filename).suffix.lower()
    token = uuid.uuid4().hex
    target = PREVIEW_DIR / f"{token}{extension}"
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    try:
        # a model dataset outranks a change log: the template carries both, and the lead rows
        # are the part that cannot be recovered from anywhere else
        if is_model_dataset_workbook(target, extension):
            return _preview_model_dataset(target, token, filename)
        if is_change_log_workbook(target, extension):
            return _preview_change_log(target, token, filename)
        _, source_columns = _read_raw_frame(target, extension)
        file_type = detect_upload_type_from_columns(source_columns)
        if file_type == HOLIDAY_PROXIMITY_TYPE:
            return _preview_holiday_proximity(target, token, filename)
        if file_type == AD_PERFORMANCE_TYPE:
            frame = read_ad_performance_tabular(target, extension)
            report = frame.attrs.get("cleaning_report", {})
            derived = read_leadlens_derived_tabular(target, extension) if is_leadlens_derived_columns(source_columns) else None
            derived_report = derived["report"] if derived is not None else {}
            preview_columns = [
                "Campaign name", "Campaign ID", "Ad set ID", "Day", "Amount spent (USD)",
                "Leads", "Reach", "Impressions", "Link clicks", "Cost per lead",
            ]
            preview_rows = frame.loc[:, preview_columns].head(8).copy()
            for column in ["Day"]:
                preview_rows[column] = preview_rows[column].apply(lambda x: x.date().isoformat() if pd.notna(x) else None)
            for column in ["Amount spent (USD)", "Leads", "Reach", "Impressions", "Link clicks", "Cost per lead"]:
                preview_rows[column] = preview_rows[column].apply(lambda x: None if pd.isna(x) else float(x))
            if derived is not None:
                derived_rows = derived["rows"].loc[:, [
                    "days_since_adset_started", "ad_set_change_recency", "ad_change_recency",
                ]].head(len(preview_rows)).reset_index(drop=True)
                preview_rows = preview_rows.reset_index(drop=True)
                for column in derived_rows.columns:
                    preview_rows[column] = derived_rows[column]
                preview_columns = [*preview_columns, *list(derived_rows.columns)]
            preview_rows = preview_rows.astype(object).where(pd.notna(preview_rows), None)
            dates = frame["Day"].dropna()
            campaign_names = (
                frame.drop_duplicates("Ad set ID").set_index("Ad set ID")["Campaign name"].to_dict()
            )
            budget_periods = derive_budget_periods(frame)
            for period in budget_periods:
                period["campaign_name"] = str(campaign_names.get(period["ad_set_id"], "") or "")
            return {
                "token": token,
                "file_name": filename,
                "file_type": AD_PERFORMANCE_TYPE,
                "file_type_label": "Ad performance",
                "total_leads": int(report.get("total_ad_leads", 0)),
                "source_rows": report.get("source_rows", len(frame)),
                "clean_rows": report.get("clean_rows", len(frame)),
                "excluded_rows": report.get("excluded_rows", 0),
                "rejected_rows": report.get("rejected_rows", 0),
                "duplicates_removed": report.get("duplicates_removed", 0),
                "source_columns": report.get("source_columns", []),
                "recognized_columns": report.get("recognized_columns", []),
                "ignored_columns": report.get("ignored_columns", []),
                "unique_campaigns": report.get("unique_campaigns", 0),
                "unique_ad_sets": report.get("unique_ad_sets", 0),
                "total_spend": report.get("total_spend", 0),
                "total_ad_leads": report.get("total_ad_leads", 0),
                "total_impressions": report.get("total_impressions", 0),
                "total_link_clicks": report.get("total_link_clicks", 0),
                "recovered_campaign_ids": report.get("recovered_campaign_ids", 0),
                "recovered_ad_set_ids": report.get("recovered_ad_set_ids", 0),
                "inferred_multi_ad_sets": report.get("inferred_multi_ad_sets", 0),
                "unresolved_attribution_rows": report.get("unresolved_attribution_rows", 0),
                "missing_spend_rows": report.get("missing_spend_rows", 0),
                "blank_metrics": report.get("blank_metrics", {}),
                "ad_grain_input": report.get("ad_grain_input", False),
                "ad_rows_collapsed": report.get("ad_rows_collapsed", 0),
                "ad_set_day_rows": report.get("ad_set_day_rows", 0),
                "budget_conflict_groups": report.get("budget_conflict_groups", 0),
                "budget_periods": budget_periods,
                "budget_conflicts": sum(1 for period in budget_periods if period["spend_conflict"]),
                "start_dates": derived_report.get("start_dates", 0),
                "change_events": derived_report.get("change_events", 0),
                "change_events_by_scope": derived_report.get("change_events_by_scope", {}),
                "ambiguous_recency_runs": derived_report.get("ambiguous_recency_runs", 0),
                "date_min": dates.min().date().isoformat() if len(dates) else None,
                "date_max": dates.max().date().isoformat() if len(dates) else None,
                "missing_values": {},
                "columns": preview_columns,
                "rows": preview_rows.to_dict(orient="records"),
            }
        if file_type == LEADLENS_DERIVED_TYPE:
            return _preview_leadlens_derived(target, token, filename)
        frame = read_tabular(target, extension)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    report = frame.attrs.get("cleaning_report", {})
    missing_values = {c: int(frame[c].isna().sum() + (frame[c].astype(str).str.strip() == "").sum()) for c in REQUIRED_COLUMNS}
    missing_values = {k: v for k, v in missing_values.items() if v}
    preview_columns = [SOURCE_ID_COLUMN, "Created At", "Customer Name", "Status", "UTM Campaign ID", "UTM Ad Set ID", "UTM Ad ID"]
    preview_rows = frame.loc[:, preview_columns].head(8).copy()
    for column in ["Created At"]:
        preview_rows[column] = preview_rows[column].apply(lambda x: x.isoformat() if pd.notna(x) else None)
    preview_rows = preview_rows.astype(object).where(pd.notna(preview_rows), None)
    return {
        "token": token,
        "file_name": filename,
        "file_type": CUSTOMER_TRAFFIC_TYPE,
        "file_type_label": "Customer traffic",
        "total_leads": int(len(frame)),
        "source_rows": report.get("source_rows", len(frame)),
        "clean_rows": report.get("clean_rows", len(frame)),
        "excluded_rows": report.get("excluded_rows", 0),
        "recovered_rows": report.get("recovered_rows", 0),
        "duplicates_removed": report.get("duplicates_removed", 0),
        "scientific_id_values": report.get("scientific_id_values", 0),
        "invalid_dates": report.get("invalid_dates", 0),
        "unattributed_rows": report.get("unattributed_rows", 0),
        "ad_ids_filled": report.get("ad_ids_filled", 0),
        "source_columns": report.get("source_columns", []),
        "recognized_columns": report.get("recognized_columns", []),
        "ignored_columns": report.get("ignored_columns", []),
        "recovery_methods": report.get("recovery_methods", {}),
        "unique_ad_sets": int(frame["UTM Ad Set ID"].nunique()),
        "date_min": frame["Created At"].min().date().isoformat(),
        "date_max": frame["Created At"].max().date().isoformat(),
        "missing_values": missing_values,
        "columns": preview_columns,
        "rows": preview_rows.to_dict(orient="records"),
    }


def _preview_holiday_proximity(target: Path, token: str, filename: str) -> dict:
    frame = read_holiday_proximity_tabular(target, target.suffix)
    report = frame.attrs.get("cleaning_report", {})
    rows = frame.loc[:, HOLIDAY_PROXIMITY_COLUMNS].head(8).copy()
    rows["date"] = rows["date"].apply(lambda v: v.date().isoformat() if pd.notna(v) else None)
    rows = rows.astype(object).where(pd.notna(rows), None)
    return {
        "token": token,
        "file_name": filename,
        "file_type": HOLIDAY_PROXIMITY_TYPE,
        "file_type_label": "Holiday proximity",
        "total_leads": 0,
        "source_rows": report.get("source_rows", len(frame)),
        "clean_rows": report.get("clean_rows", len(frame)),
        "excluded_rows": report.get("excluded_rows", 0),
        "rejected_rows": report.get("excluded_rows", 0),
        "duplicates_removed": report.get("duplicate_dates", 0),
        "duplicate_dates": report.get("duplicate_dates", 0),
        "holiday_count": report.get("holiday_count", 0),
        "bucket_counts": report.get("bucket_counts", {}),
        "date_min": report.get("date_min"),
        "date_max": report.get("date_max"),
        "source_columns": report.get("source_columns", []),
        "recognized_columns": report.get("recognized_columns", []),
        "ignored_columns": report.get("ignored_columns", []),
        "missing_values": {},
        "columns": HOLIDAY_PROXIMITY_COLUMNS,
        "rows": rows.to_dict(orient="records"),
    }


def _preview_leadlens_derived(target: Path, token: str, filename: str) -> dict:
    parsed = read_leadlens_derived_tabular(target, target.suffix)
    report = parsed["report"]
    rows = parsed["rows"].loc[:, [
        "Day", "Campaign name", "Campaign ID", "Ad set ID",
        "days_since_adset_started", "ad_set_change_recency", "ad_change_recency",
    ]].head(8).copy()
    rows["Day"] = rows["Day"].apply(lambda v: v.date().isoformat() if pd.notna(v) else None)
    rows = rows.astype(object).where(pd.notna(rows), None)
    warnings = []
    if report.get("unresolved_attribution_rows") or report.get("ambiguous_attribution_rows"):
        warnings.append(
            "Some rows could not be mapped back to exact ad set IDs. Scientific-notation IDs "
            "are only accepted when campaign history can resolve them unambiguously."
        )
    if report.get("ambiguous_recency_runs"):
        warnings.append(
            f"{report['ambiguous_recency_runs']} recency runs did not identify an exact event date "
            "and will be skipped. Import the changelog workbook when exact dates are available."
        )
    return {
        "token": token,
        "file_name": filename,
        "file_type": LEADLENS_DERIVED_TYPE,
        "file_type_label": "LeadLens derived variables",
        "total_leads": 0,
        "source_rows": report["source_rows"],
        "clean_rows": report["clean_rows"],
        "excluded_rows": report["excluded_rows"],
        "rejected_rows": report["excluded_rows"],
        "duplicates_removed": 0,
        "unique_ad_sets": report["unique_ad_sets"],
        "date_min": report["date_min"],
        "date_max": report["date_max"],
        "start_dates": report["start_dates"],
        "change_events": report["change_events"],
        "change_events_by_scope": report["change_events_by_scope"],
        "recovered_ad_set_ids": report["recovered_ad_set_ids"],
        "unresolved_attribution_rows": report["unresolved_attribution_rows"],
        "ambiguous_attribution_rows": report["ambiguous_attribution_rows"],
        "ambiguous_recency_runs": report["ambiguous_recency_runs"],
        "source_columns": report["source_columns"],
        "recognized_columns": report["recognized_columns"],
        "ignored_columns": report["ignored_columns"],
        "warnings": warnings,
        "missing_values": {},
        "columns": list(rows.columns),
        "rows": rows.to_dict(orient="records"),
    }


def _preview_change_log(target: Path, token: str, filename: str) -> dict:
    frame = read_change_log_workbook(target, target.suffix)
    report = frame.attrs.get("cleaning_report", {})
    preview_columns = ["scope", "event_date", "ad_set_id", "ad_id", "source", "confirmed_by"]
    rows = frame.loc[:, preview_columns].head(8).copy()
    rows["event_date"] = rows["event_date"].apply(lambda v: v.date().isoformat() if pd.notna(v) else None)
    rows = rows.astype(object).where(pd.notna(rows), None)
    unconfirmed = int(report.get("unconfirmed_rows", 0))
    warnings = []
    if unconfirmed:
        warnings.append(
            f"{unconfirmed} of {report.get('clean_rows', 0)} rows are not marked "
            f"'{CONFIRMED_SOURCE}'. They will be stored but ignored by the model - a row still "
            "carrying the detector's own label is a guess, not a recorded change."
        )
    if not report.get("confirmed_rows"):
        warnings.append(
            "No confirmed rows in this file, so the forecast will keep using inferred changes. "
            "Set source to 'confirmed' on the events you have verified."
        )
    return {
        "token": token,
        "file_name": filename,
        "file_type": CHANGE_LOG_TYPE,
        "file_type_label": "Change log",
        "total_leads": 0,
        "source_rows": report.get("source_rows", len(frame)),
        "clean_rows": report.get("clean_rows", len(frame)),
        "excluded_rows": report.get("excluded_rows", 0),
        "rejected_rows": report.get("excluded_rows", 0),
        "duplicates_removed": report.get("duplicates_removed", 0),
        "confirmed_rows": report.get("confirmed_rows", 0),
        "unconfirmed_rows": unconfirmed,
        "sheets_read": report.get("sheets_read", []),
        "skipped_rows": report.get("skipped_rows", {}),
        "by_scope": report.get("by_scope", {}),
        "unique_ad_sets": report.get("unique_ad_sets", 0),
        "date_min": report.get("date_min"),
        "date_max": report.get("date_max"),
        "warnings": warnings,
        "missing_values": {},
        "columns": preview_columns,
        "rows": rows.to_dict(orient="records"),
    }


def _preview_model_dataset(target: Path, token: str, filename: str) -> dict:
    parsed = read_model_dataset_workbook(target, target.suffix)
    report = parsed["report"]
    traffic = parsed["traffic"]
    preview_columns = ["Created At", "Customer Name", "Status", "UTM Campaign ID",
                       "UTM Ad Set ID", "UTM Ad ID"]
    rows = traffic.loc[:, preview_columns].head(8).copy()
    rows["Created At"] = rows["Created At"].apply(lambda v: v.isoformat() if pd.notna(v) else None)
    rows = rows.astype(object).where(pd.notna(rows), None)

    warnings = []
    if not report["change_events"]:
        warnings.append(
            "No change dates were found, so variables 6 and 7 will "
            "keep using the system's inferred events. Fill those columns, or the changelog "
            "sheets, and re-upload to replace them."
        )
    if report["lead_rows_skipped"]:
        warnings.append(
            f"{report['lead_rows_skipped']} rows have no usable created_at or ad_set_id and "
            "will not be imported as leads."
        )
    # a lead-grain sheet alone can only describe days that produced at least one lead
    if report["ad_context_source"] != AD_SET_DAY_SHEET:
        warnings.append(
            "This file has no 'ad_set_days' sheet, so its ad set spend covers only days that "
            "produced a lead. Days that spent and got nothing are missing. Add that sheet, or "
            "keep uploading the Meta ad performance export alongside it."
        )
    return {
        "token": token,
        "file_name": filename,
        "file_type": MODEL_DATASET_TYPE,
        "file_type_label": "Model dataset",
        "total_leads": report["lead_rows"],
        "source_rows": report["source_rows"],
        "clean_rows": report["lead_rows"],
        "excluded_rows": report["lead_rows_skipped"],
        "rejected_rows": report["lead_rows_skipped"],
        "duplicates_removed": report["traffic_report"].get("duplicates_removed", 0),
        "lead_rows": report["lead_rows"],
        "ad_set_day_rows": report["ad_set_day_rows"],
        "context_rows_collapsed": report["context_rows_collapsed"],
        "ad_context_source": report["ad_context_source"],
        "zero_lead_days": report["zero_lead_days"],
        "change_events": report["change_events"],
        "change_events_by_scope": report["change_events_by_scope"],
        "unique_ad_sets": report["unique_ad_sets"],
        "total_spend": report["total_spend"],
        "recovered_rows": report["traffic_report"].get("recovered_rows", 0),
        "scientific_id_values": report["traffic_report"].get("scientific_id_values", 0),
        "date_min": report["date_min"],
        "date_max": report["date_max"],
        "warnings": warnings,
        "missing_values": {},
        "columns": preview_columns,
        "rows": rows.to_dict(orient="records"),
    }


def _import_model_dataset_preview(preview_path: Path, filename: str | None = None) -> dict:
    parsed = read_model_dataset_workbook(preview_path, preview_path.suffix)
    report = parsed["report"]
    traffic, ad, changes = parsed["traffic"], parsed["ad"], parsed["changes"]
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    now = utc_now()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at,
               row_count, cleaned_count, excluded_count, recovered_count, total_spend_usd,
               date_min, date_max) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                filename or stored_path.name, str(stored_path), file_hash, MODEL_DATASET_TYPE, now,
                report["source_rows"], report["lead_rows"], report["lead_rows_skipped"],
                report["traffic_report"].get("recovered_rows", 0), report["total_spend"],
                report["date_min"], report["date_max"],
            ),
        )
        upload_id = cur.lastrowid
        imported, duplicates = _write_lead_events(db, upload_id, traffic)
        ad_inserted = ad_updated = 0
        if ad is not None and len(ad):
            ad_inserted, ad_updated = _write_ad_performance(db, upload_id, ad, now)
        change_inserted = change_updated = 0
        for row in changes.itertuples():
            event_date = row.event_date.date().isoformat()
            existed = db.execute(
                "SELECT 1 FROM change_events WHERE scope=? AND event_date=? AND ad_set_id=? AND ad_id=?",
                (row.scope, event_date, row.ad_set_id, row.ad_id),
            ).fetchone()
            db.execute(
                """INSERT INTO change_events(upload_id, scope, event_date, ad_set_id, ad_id,
                   source, confirmed_by, notes, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(scope, event_date, ad_set_id, ad_id) DO UPDATE SET
                   upload_id=excluded.upload_id,
                   source=excluded.source, notes=excluded.notes, updated_at=excluded.updated_at""",
                (upload_id, row.scope, event_date, row.ad_set_id, row.ad_id,
                 row.source, None, row.notes, now, now),
            )
            change_inserted += int(existed is None)
            change_updated += int(existed is not None)
        db.execute(
            "UPDATE raw_uploads SET imported_count=?, duplicate_count=?, updated_count=? WHERE id=?",
            (imported, duplicates, ad_inserted + ad_updated, upload_id),
        )
    _clear_change_caches()
    budget_summary = {"written": 0, "skipped_manual": 0, "conflicts": 0}
    if ad is not None and len(ad):
        budget_summary = store_derived_budget_periods(derive_budget_periods(ad))
    rebuild_aggregates()
    run = train_models()
    return {
        "upload_id": upload_id,
        "file_type": MODEL_DATASET_TYPE,
        "imported": imported,
        "duplicates": duplicates,
        "cleaned": report["lead_rows"],
        "excluded": report["lead_rows_skipped"],
        "ad_set_days_inserted": ad_inserted,
        "ad_set_days_updated": ad_updated,
        "zero_lead_days": report["zero_lead_days"],
        "change_events_inserted": change_inserted,
        "change_events_updated": change_updated,
        "budget_periods_written": budget_summary["written"],
        "training_run": run,
    }


def _import_change_log_preview(preview_path: Path, filename: str | None = None) -> dict:
    frame = read_change_log_workbook(preview_path, preview_path.suffix)
    report = frame.attrs.get("cleaning_report", {})
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    now = utc_now()
    inserted = 0
    updated = 0
    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at,
               row_count, cleaned_count, excluded_count, duplicate_count, date_min, date_max)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                filename or stored_path.name,
                str(stored_path),
                file_hash,
                CHANGE_LOG_TYPE,
                now,
                report.get("source_rows", len(frame)),
                len(frame),
                report.get("excluded_rows", 0),
                report.get("duplicates_removed", 0),
                report.get("date_min"),
                report.get("date_max"),
            ),
        )
        upload_id = cur.lastrowid
        for row in frame.itertuples():
            event_date = row.event_date.date().isoformat()
            existed = db.execute(
                "SELECT 1 FROM change_events WHERE scope=? AND event_date=? AND ad_set_id=? AND ad_id=?",
                (row.scope, event_date, row.ad_set_id, row.ad_id),
            ).fetchone()
            db.execute(
                """INSERT INTO change_events(upload_id, scope, event_date, ad_set_id, ad_id,
                   source, confirmed_by, notes, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(scope, event_date, ad_set_id, ad_id) DO UPDATE SET
                   upload_id=excluded.upload_id,
                   source=excluded.source,
                   confirmed_by=excluded.confirmed_by,
                   notes=excluded.notes,
                   updated_at=excluded.updated_at""",
                (upload_id, row.scope, event_date, row.ad_set_id, row.ad_id,
                 row.source, row.confirmed_by or None, row.notes or None, now, now),
            )
            inserted += int(existed is None)
            updated += int(existed is not None)
        db.execute(
            "UPDATE raw_uploads SET imported_count=?, updated_count=? WHERE id=?",
            (inserted, updated, upload_id),
        )
    _clear_change_caches()
    run = train_models()
    return {
        "upload_id": upload_id,
        "file_type": CHANGE_LOG_TYPE,
        "inserted": inserted,
        "updated": updated,
        "cleaned": len(frame),
        "excluded": report.get("excluded_rows", 0),
        "duplicates": report.get("duplicates_removed", 0),
        "confirmed_rows": report.get("confirmed_rows", 0),
        "unconfirmed_rows": report.get("unconfirmed_rows", 0),
        "by_scope": report.get("by_scope", {}),
        "sheets_read": report.get("sheets_read", []),
        "training_run": run,
    }


def _write_derived_variable_facts(
    db: sqlite3.Connection, upload_id: int, starts: pd.DataFrame, changes: pd.DataFrame, now: str,
) -> dict[str, int]:
    start_inserted = start_updated = 0
    change_inserted = change_updated = 0
    for row in starts.itertuples():
        start_date = row.start_date.date().isoformat()
        existed = db.execute(
            "SELECT 1 FROM ad_set_start_dates WHERE ad_set_id=?",
            (row.ad_set_id,),
        ).fetchone()
        db.execute(
            """INSERT INTO ad_set_start_dates(ad_set_id, start_date, confirmed_by, notes, created_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(ad_set_id) DO UPDATE SET
               start_date=excluded.start_date,
               confirmed_by=excluded.confirmed_by,
               notes=excluded.notes,
               updated_at=excluded.updated_at""",
            (row.ad_set_id, start_date, row.confirmed_by or None, row.notes or None, now, now),
        )
        start_inserted += int(existed is None)
        start_updated += int(existed is not None)
    for row in changes.itertuples():
        event_date = row.event_date.date().isoformat()
        existed = db.execute(
            "SELECT 1 FROM change_events WHERE scope=? AND event_date=? AND ad_set_id=? AND ad_id=?",
            (row.scope, event_date, row.ad_set_id, row.ad_id),
        ).fetchone()
        db.execute(
            """INSERT INTO change_events(upload_id, scope, event_date, ad_set_id, ad_id,
               source, confirmed_by, notes, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(scope, event_date, ad_set_id, ad_id) DO UPDATE SET
               upload_id=excluded.upload_id,
               source=excluded.source,
               confirmed_by=excluded.confirmed_by,
               notes=excluded.notes,
               updated_at=excluded.updated_at""",
            (upload_id, row.scope, event_date, row.ad_set_id, row.ad_id,
             row.source, row.confirmed_by or None, row.notes or None, now, now),
        )
        change_inserted += int(existed is None)
        change_updated += int(existed is not None)
    return {
        "start_dates_inserted": start_inserted,
        "start_dates_updated": start_updated,
        "change_events_inserted": change_inserted,
        "change_events_updated": change_updated,
    }


def _import_leadlens_derived_preview(preview_path: Path, filename: str | None = None) -> dict:
    parsed = read_leadlens_derived_tabular(preview_path, preview_path.suffix)
    rows, starts, changes, report = parsed["rows"], parsed["starts"], parsed["changes"], parsed["report"]
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    now = utc_now()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at,
               row_count, cleaned_count, excluded_count, recovered_count, date_min, date_max)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                filename or stored_path.name,
                str(stored_path),
                file_hash,
                LEADLENS_DERIVED_TYPE,
                now,
                report["source_rows"],
                report["clean_rows"],
                report["excluded_rows"],
                report.get("recovered_ad_set_ids", 0),
                report["date_min"],
                report["date_max"],
            ),
        )
        upload_id = cur.lastrowid
        written = _write_derived_variable_facts(db, upload_id, starts, changes, now)
        db.execute(
            "UPDATE raw_uploads SET imported_count=?, updated_count=? WHERE id=?",
            (
                written["start_dates_inserted"] + written["change_events_inserted"],
                written["start_dates_updated"] + written["change_events_updated"],
                upload_id,
            ),
        )
    _clear_change_caches()
    run = train_models()
    return {
        "upload_id": upload_id,
        "file_type": LEADLENS_DERIVED_TYPE,
        "cleaned": len(rows),
        "excluded": report["excluded_rows"],
        **written,
        "ambiguous_recency_runs": report.get("ambiguous_recency_runs", 0),
        "training_run": run,
    }


def _import_holiday_proximity_preview(preview_path: Path, filename: str | None = None) -> dict:
    frame = read_holiday_proximity_tabular(preview_path, preview_path.suffix)
    report = frame.attrs.get("cleaning_report", {})
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    now = utc_now()

    out = frame.copy()
    out["date"] = out["date"].dt.date.astype(str)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(DATA_DIR / "holiday_proximity.csv", index=False)
    _holiday_proximity_map.cache_clear()

    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at,
               row_count, cleaned_count, excluded_count, duplicate_count, date_min, date_max)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                filename or stored_path.name,
                str(stored_path),
                file_hash,
                HOLIDAY_PROXIMITY_TYPE,
                now,
                report.get("source_rows", len(frame)),
                len(frame),
                report.get("excluded_rows", 0),
                report.get("duplicate_dates", 0),
                report.get("date_min"),
                report.get("date_max"),
            ),
        )
        upload_id = cur.lastrowid
        db.execute("UPDATE raw_uploads SET imported_count=?, updated_count=? WHERE id=?", (len(frame), 0, upload_id))

    run = train_models()
    return {
        "upload_id": upload_id,
        "file_type": HOLIDAY_PROXIMITY_TYPE,
        "imported": len(frame),
        "updated": 0,
        "cleaned": len(frame),
        "excluded": report.get("excluded_rows", 0),
        "duplicates": report.get("duplicate_dates", 0),
        "holiday_count": report.get("holiday_count", 0),
        "training_run": run,
    }


def _event_hash(row: pd.Series, row_number: int) -> str:
    return _lead_identity_hash(
        row["Created At"],
        row["Customer Name"],
        row["Status"],
        row["UTM Campaign ID"],
        row["UTM Ad Set ID"],
        row["UTM Ad ID"],
        row["FB Ad Title"],
    )


def normalize_existing_lead_events() -> dict:
    """Apply current lead identity cleaning rules to already-imported lead rows."""
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            """SELECT id, event_hash, status, created_at, customer_name, utm_campaign_id,
                      utm_ad_set_id, utm_ad_id, fb_ad_title
               FROM lead_events
               ORDER BY datetime(created_at), id"""
        ).fetchall()]
        seen_hashes: dict[str, int] = {}
        delete_ids: list[int] = []
        updates: list[tuple[str, str, int]] = []
        title_updates = 0
        hash_updates = 0

        for row in rows:
            cleaned_title = _clean_fb_ad_title(row.get("fb_ad_title"))
            new_hash = _lead_identity_hash(
                row.get("created_at"),
                row.get("customer_name"),
                row.get("status"),
                row.get("utm_campaign_id"),
                row.get("utm_ad_set_id"),
                row.get("utm_ad_id"),
                cleaned_title,
            )
            if new_hash in seen_hashes:
                delete_ids.append(int(row["id"]))
                continue
            seen_hashes[new_hash] = int(row["id"])

            current_title = str(row.get("fb_ad_title") or "").strip()
            if current_title != cleaned_title:
                title_updates += 1
            if str(row.get("event_hash") or "") != new_hash:
                hash_updates += 1
            if current_title != cleaned_title or str(row.get("event_hash") or "") != new_hash:
                updates.append((new_hash, cleaned_title, int(row["id"])))

        for lead_id in delete_ids:
            db.execute("DELETE FROM lead_events WHERE id=?", (lead_id,))
        for new_hash, cleaned_title, lead_id in updates:
            db.execute(
                "UPDATE lead_events SET event_hash=?, fb_ad_title=? WHERE id=?",
                (new_hash, cleaned_title, lead_id),
            )

    return {
        "scanned": len(rows),
        "titles_cleaned": title_updates,
        "duplicates_removed": len(delete_ids),
        "hashes_migrated": hash_updates,
    }


def _json_ready(value: object) -> object:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return float(value)
    return str(value)


def _date_ready(value: object) -> str | None:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return str(value)[:10]


def _float_ready(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _write_lead_events(db: sqlite3.Connection, upload_id: int, frame: pd.DataFrame) -> tuple[int, int]:
    """Insert cleaned traffic rows as lead_events, returning (new, duplicate) counts.

    Identity is the content hash, so the same lead arriving through a different file - a CRM
    export and a model dataset covering the same week - is stored once, not twice.
    """
    imported = 0
    duplicates = 0
    for row_index, row in frame.iterrows():
        event_hash = _event_hash(row, int(row_index) + 2)
        raw = {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}
        before = db.total_changes
        db.execute(
            """INSERT OR IGNORE INTO lead_events(event_hash, platform, status, created_at, updated_at,
               customer_name, utm_campaign, utm_campaign_id, utm_ad_set_id, utm_ad_id,
               fb_ad_title, amount_spent_usd, raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_hash, str(row["Platform"]).strip(), str(row["Status"]).strip(), row["Created At"].isoformat(),
             row["Updated At"].isoformat() if pd.notna(row["Updated At"]) else None,
             str(row["Customer Name"]).strip(), str(row["UTM Campaign"]).strip(), row["UTM Campaign ID"],
             row["UTM Ad Set ID"], row["UTM Ad ID"], str(row["FB Ad Title"]).strip(),
             None if pd.isna(row["Amount spent (USD)"]) else float(row["Amount spent (USD)"]), json.dumps(raw, ensure_ascii=False)),
        )
        was_new = db.total_changes > before
        lead_id = db.execute("SELECT id FROM lead_events WHERE event_hash=?", (event_hash,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO upload_lead_links(upload_id, lead_id) VALUES(?,?)", (upload_id, lead_id))
        imported += int(was_new)
        duplicates += int(not was_new)
    return imported, duplicates


def _import_customer_traffic_preview(preview_path: Path, filename: str | None = None) -> dict:
    frame = read_tabular(preview_path, preview_path.suffix)
    report = frame.attrs.get("cleaning_report", {})
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at, row_count,
               cleaned_count, excluded_count, recovered_count, date_min, date_max) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (filename or stored_path.name, str(stored_path), file_hash, CUSTOMER_TRAFFIC_TYPE, utc_now(), report.get("source_rows", len(frame)),
             len(frame), report.get("excluded_rows", 0), report.get("recovered_rows", 0),
             frame["Created At"].min().date().isoformat(), frame["Created At"].max().date().isoformat()),
        )
        upload_id = cur.lastrowid
        imported, duplicates = _write_lead_events(db, upload_id, frame)
        db.execute("UPDATE raw_uploads SET imported_count=?, duplicate_count=? WHERE id=?", (imported, duplicates, upload_id))
    rebuild_aggregates()
    run = train_models()
    return {"upload_id": upload_id, "imported": imported, "duplicates": duplicates,
            "file_type": CUSTOMER_TRAFFIC_TYPE,
            "cleaned": len(frame), "excluded": report.get("excluded_rows", 0),
            "recovered": report.get("recovered_rows", 0), "training_run": run}


def _remove_superseded_ad_rows(db: sqlite3.Connection, upload_id: int, report: dict) -> int:
    """Drop rows an authoritative export has just superseded for the same campaign-day.

    Exports whose IDs arrived as scientific notation have their ad set assigned by
    `_repair_ad_performance_attribution`, which *guesses* from the campaign name. When a later
    export carries real IDs, the upsert key `(day, campaign_id, ad_set_id)` no longer matches
    the guess, so the corrected row is inserted alongside it and the guess survives as an
    orphan that double-counts spend -- and, because CRM leads are keyed separately, can invert
    a boost/cut verdict between the phantom and the real ad set.

    Only an export that *reports* its own ad set IDs may supersede stored rows. A file whose
    IDs we had to infer is in no position to invalidate anything. Scope is limited to the
    campaign-days this upload actually covers, so untouched history is never at risk.

    Matching is by campaign *name*, not campaign ID: the repair guesses the campaign ID from
    the same lookup, so a superseded row's IDs are precisely the fields that cannot be trusted
    to join on. The name is what survived the round trip intact.
    """
    if report.get("recovered_ad_set_ids"):
        return 0
    cursor = db.execute(
        """DELETE FROM daily_ad_performance
           WHERE upload_id <> ?
             AND EXISTS (
               SELECT 1 FROM daily_ad_performance current
               WHERE current.upload_id = ?
                 AND current.campaign_name = daily_ad_performance.campaign_name
                 AND current.day = daily_ad_performance.day
             )""",
        (upload_id, upload_id),
    )
    return int(cursor.rowcount or 0)


def _import_ad_performance_preview(preview_path: Path, filename: str | None = None) -> dict:
    frame = read_ad_performance_tabular(preview_path, preview_path.suffix)
    report = frame.attrs.get("cleaning_report", {})
    derived = (
        read_leadlens_derived_tabular(preview_path, preview_path.suffix)
        if is_leadlens_derived_columns(report.get("source_columns", []))
        else None
    )
    stored_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{preview_path.suffix}"
    stored_path = UPLOAD_DIR / stored_name
    shutil.move(str(preview_path), stored_path)
    file_hash = hashlib.sha256(stored_path.read_bytes()).hexdigest()
    now = utc_now()
    inserted = 0
    updated = 0
    with connect() as db:
        cur = db.execute(
            """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at, row_count,
               cleaned_count, excluded_count, duplicate_count, rejected_count, total_spend_usd, date_min, date_max)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                filename or stored_path.name,
                str(stored_path),
                file_hash,
                AD_PERFORMANCE_TYPE,
                now,
                report.get("source_rows", len(frame)),
                len(frame),
                report.get("excluded_rows", 0),
                report.get("duplicates_removed", 0),
                report.get("rejected_rows", 0),
                report.get("total_spend", 0),
                frame["Day"].min().date().isoformat(),
                frame["Day"].max().date().isoformat(),
            ),
        )
        upload_id = cur.lastrowid
        inserted, updated = _write_ad_performance(db, upload_id, frame, now)
        superseded = _remove_superseded_ad_rows(db, upload_id, report)
        ad_level_written = _store_ad_level_rows(db, upload_id, frame.attrs.get("ad_level_rows"))
        derived_written = {
            "start_dates_inserted": 0,
            "start_dates_updated": 0,
            "change_events_inserted": 0,
            "change_events_updated": 0,
        }
        if derived is not None:
            derived_written = _write_derived_variable_facts(
                db, upload_id, derived["starts"], derived["changes"], now,
            )
        db.execute(
            "UPDATE raw_uploads SET imported_count=?, updated_count=? WHERE id=?",
            (
                inserted + derived_written["start_dates_inserted"] + derived_written["change_events_inserted"],
                updated + derived_written["start_dates_updated"] + derived_written["change_events_updated"],
                upload_id,
            ),
        )
    _clear_change_caches()
    budget_periods = derive_budget_periods(frame)
    budget_summary = store_derived_budget_periods(budget_periods)
    run = train_models()
    return {
        "upload_id": upload_id,
        "file_type": AD_PERFORMANCE_TYPE,
        "inserted": inserted,
        "updated": updated,
        "duplicates": report.get("duplicates_removed", 0),
        "cleaned": len(frame),
        "excluded": report.get("excluded_rows", 0),
        "rejected": report.get("rejected_rows", 0),
        "total_spend": report.get("total_spend", 0),
        "ad_rows_collapsed": report.get("ad_rows_collapsed", 0),
        "ad_level_rows_stored": ad_level_written,
        "superseded_rows_removed": superseded,
        **derived_written,
        "ambiguous_recency_runs": (
            derived["report"].get("ambiguous_recency_runs", 0) if derived is not None else 0
        ),
        "budget_periods_written": budget_summary["written"],
        "budget_periods_kept_manual": budget_summary["skipped_manual"],
        "budget_conflicts": budget_summary["conflicts"],
        "training_run": run,
    }


def _write_ad_performance(
    db: sqlite3.Connection, upload_id: int, frame: pd.DataFrame, now: str,
) -> tuple[int, int]:
    """Upsert cleaned ad-set-day rows, returning (inserted, updated) counts."""
    inserted = 0
    updated = 0
    for _, row in frame.iterrows():
        values = {target: row[source] for source, target in AD_INTERNAL_COLUMNS.items()}
        day = _date_ready(values["day"])
        campaign_id = str(values["campaign_id"]).strip()
        ad_set_id = str(values["ad_set_id"]).strip()
        existed = db.execute(
            "SELECT 1 FROM daily_ad_performance WHERE day=? AND campaign_id=? AND ad_set_id=?",
            (day, campaign_id, ad_set_id),
        ).fetchone()
        raw = {column: _json_ready(row[column]) for column in [
            *AD_PERFORMANCE_COLUMNS, *AD_PERFORMANCE_IMPORTED_DERIVED_COLUMNS,
        ]}
        imported_days = _float_ready(row.get("days_since_adset_started"))
        imported_ad_set_recency = str(row.get("ad_set_change_recency") or "").strip() or None
        imported_ad_recency = str(row.get("ad_change_recency") or "").strip() or None
        params = (
            upload_id,
            day,
            campaign_id,
            str(values["campaign_name"] or "").strip(),
            ad_set_id,
            str(values["delivery_status"] or "").strip(),
            str(values["delivery_level"] or "").strip(),
            _float_ready(values["amount_spent_usd"]),
            _float_ready(values["messaging_conversations_started"]),
            _float_ready(values["cost_per_messaging_conversation_started"]),
            _float_ready(values["reach"]),
            _float_ready(values["impressions"]),
            _float_ready(values["frequency"]),
            _float_ready(values["leads"]),
            _float_ready(values["cost_per_lead"]),
            _float_ready(values["link_clicks"]),
            _float_ready(values["cpc"]),
            _float_ready(values["unique_link_clicks"]),
            _float_ready(values["cost_per_unique_link_click"]),
            imported_days,
            imported_ad_set_recency,
            imported_ad_recency,
            _float_ready(values["ad_set_budget"]),
            str(values["ad_set_budget_type"] or "").strip() or None,
            _date_ready(values["reporting_starts"]),
            _date_ready(values["reporting_ends"]),
            json.dumps(raw, ensure_ascii=False),
            now,
            now,
        )
        db.execute(
            """INSERT INTO daily_ad_performance(
               upload_id, day, campaign_id, campaign_name, ad_set_id, delivery_status, delivery_level,
               amount_spent_usd, messaging_conversations_started, cost_per_messaging_conversation_started,
               reach, impressions, frequency, leads, cost_per_lead, link_clicks, cpc,
               unique_link_clicks, cost_per_unique_link_click,
               days_since_adset_started_imported, ad_set_change_recency_imported, ad_change_recency_imported,
               ad_set_budget, ad_set_budget_type,
               reporting_starts, reporting_ends,
               raw_json, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(day, campaign_id, ad_set_id) DO UPDATE SET
               upload_id=excluded.upload_id,
               campaign_name=excluded.campaign_name,
               delivery_status=excluded.delivery_status,
               delivery_level=excluded.delivery_level,
               amount_spent_usd=excluded.amount_spent_usd,
               messaging_conversations_started=excluded.messaging_conversations_started,
               cost_per_messaging_conversation_started=excluded.cost_per_messaging_conversation_started,
               reach=excluded.reach,
               impressions=excluded.impressions,
               frequency=excluded.frequency,
               leads=excluded.leads,
               cost_per_lead=excluded.cost_per_lead,
               link_clicks=excluded.link_clicks,
               cpc=excluded.cpc,
               unique_link_clicks=excluded.unique_link_clicks,
               cost_per_unique_link_click=excluded.cost_per_unique_link_click,
               days_since_adset_started_imported=excluded.days_since_adset_started_imported,
               ad_set_change_recency_imported=excluded.ad_set_change_recency_imported,
               ad_change_recency_imported=excluded.ad_change_recency_imported,
               ad_set_budget=excluded.ad_set_budget,
               ad_set_budget_type=excluded.ad_set_budget_type,
               reporting_starts=excluded.reporting_starts,
               reporting_ends=excluded.reporting_ends,
               raw_json=excluded.raw_json,
               updated_at=excluded.updated_at""",
            params,
        )
        inserted += int(existed is None)
        updated += int(existed is not None)
    return inserted, updated


def import_preview(token: str, filename: str | None = None) -> dict:
    # Validate the token shape (uuid4 hex) before it reaches glob(): an unchecked token could
    # carry glob metacharacters (`*`, `?`, `[`) and match previews other than the caller's own.
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("Preview token is invalid or has expired.")
    matches = list(PREVIEW_DIR.glob(f"{token}.*"))
    if not matches:
        raise ValueError("Preview token is invalid or has expired.")
    preview_path = matches[0]
    if is_model_dataset_workbook(preview_path, preview_path.suffix):
        return _import_model_dataset_preview(preview_path, filename)
    if is_change_log_workbook(preview_path, preview_path.suffix):
        return _import_change_log_preview(preview_path, filename)
    _, source_columns = _read_raw_frame(preview_path, preview_path.suffix)
    file_type = detect_upload_type_from_columns(source_columns)
    if file_type == HOLIDAY_PROXIMITY_TYPE:
        return _import_holiday_proximity_preview(preview_path, filename)
    if file_type == AD_PERFORMANCE_TYPE:
        return _import_ad_performance_preview(preview_path, filename)
    if file_type == LEADLENS_DERIVED_TYPE:
        return _import_leadlens_derived_preview(preview_path, filename)
    return _import_customer_traffic_preview(preview_path, filename)


# A day is treated as still in progress if its last lead lands before this hour while the
# rest of the history routinely runs to late evening. Exports are usually pulled mid-morning,
# which leaves a final day holding only a few hours of traffic.
PARTIAL_DAY_CUTOFF_HOUR = 21.0


def _trailing_partial_date(frame: pd.DataFrame) -> str | None:
    """The final calendar date, when it is only partly collected rather than genuinely quiet.

    A part-day counted as a whole one is not a small error: it enters every trailing average
    the models anchor their level on, so a morning export drags the entire forecast down and
    puts a cliff on the end of the actuals chart.
    """
    stamps = pd.to_datetime(frame["created_at"], errors="coerce").dropna()
    if stamps.empty:
        return None
    hours = stamps.dt.hour + stamps.dt.minute / 60.0
    last_hour_by_day = hours.groupby(stamps.dt.date).max()
    if len(last_hour_by_day) < 3:
        return None
    final_day = last_hour_by_day.index.max()
    final_hour = float(last_hour_by_day.loc[final_day])
    earlier = last_hour_by_day.drop(final_day)
    # Compare against how late this history normally runs. A genuinely quiet final day whose
    # last lead still arrives at 23:00 is complete and must be kept.
    if float(earlier.median()) - final_hour < 3.0 or final_hour >= PARTIAL_DAY_CUTOFF_HOUR:
        return None
    return str(final_day)


def rebuild_aggregates() -> None:
    """Recompute daily_ad_set_aggregates from scratch off lead_events.

    Deliberately three phases -- read, compute, write -- rather than one `with connect()`
    wrapping the whole thing. The pandas groupby below takes ~2s, and doing it inside the
    transaction meant SQLite's single write lock was held for that entire time: an
    interactive lead edit landing in that window blocked on `busy_timeout` (measured at
    ~2.1s) instead of returning immediately. Computing first and writing via one
    `executemany` shrinks the locked section to milliseconds. Correctness is unchanged --
    this always rebuilt the table wholesale, so it never depended on the read and the write
    sharing a transaction.
    """
    with connect() as db:
        rows = db.execute("SELECT * FROM lead_events").fetchall()
        # Newer traffic exports (since 2026-08-01) no longer carry a per-lead "Amount spent
        # (USD)" column -- that context used to arrive only via the model-dataset workbook,
        # which repeated the ad set's day spend onto every lead row. Ad-set-day spend is also
        # reported directly by the separate ad-performance export, so it's used as a fallback
        # here whenever no lead in the group carried its own spend value.
        spend_rows = db.execute(
            "SELECT day, ad_set_id, SUM(amount_spent_usd) AS spend FROM daily_ad_performance "
            "GROUP BY day, ad_set_id"
        ).fetchall()
    ad_performance_spend = {(r["day"], r["ad_set_id"]): r["spend"] for r in spend_rows}

    records: list[tuple] = []
    if rows:
        frame = pd.DataFrame([dict(r) for r in rows])
        frame["aggregate_date"] = pd.to_datetime(frame["created_at"]).dt.date.astype(str)
        partial = _trailing_partial_date(frame)
        if partial is not None:
            # Dropped from the modelled series only. lead_events keeps every row, so the
            # leads stay visible in Data History and land in the aggregate once the next
            # export completes the day.
            frame = frame[frame["aggregate_date"] != partial]
        for (date, ad_set), group in frame.groupby(["aggregate_date", "utm_ad_set_id"]):
            status_counts = group["status"].fillna("Unknown").value_counts().to_dict()
            campaign = group["utm_campaign_id"].dropna().astype(str).mode()
            # Spend is contextual only: repeated per-lead values are summarized with max, never sum.
            spend_context = group["amount_spent_usd"].max()
            if pd.isna(spend_context):
                spend_context = ad_performance_spend.get((date, ad_set))
            records.append((
                date, ad_set, campaign.iloc[0] if len(campaign) else "", len(group),
                group["utm_ad_id"].replace("", np.nan).nunique(),
                int(group["status"].str.casefold().eq("new").sum()),
                int(group["status"].str.casefold().eq("existing").sum()),
                json.dumps(status_counts), None if pd.isna(spend_context) else float(spend_context),
            ))

    with connect() as db:
        db.execute("DELETE FROM daily_ad_set_aggregates")
        if records:
            db.executemany("INSERT INTO daily_ad_set_aggregates VALUES(?,?,?,?,?,?,?,?,?)", records)
    refresh_forecast_realizations()


def get_dashboard_insights() -> dict:
    """Return reconciled portfolio-level mix metrics for the main dashboard."""
    with connect() as db:
        source_rows = db.execute(
            """SELECT status, created_at, utm_campaign, utm_campaign_id, utm_ad_set_id
               FROM lead_events ORDER BY created_at"""
        ).fetchall()

    total = len(source_rows)
    status_counts: Counter[str] = Counter()
    campaigns: dict[str, dict] = {}
    dates: list[str] = []
    invalid_names = {"", "nan", "none", "null", "n/a"}

    for row in source_rows:
        raw_status = str(row["status"] or "").strip()
        normalized = raw_status.casefold()
        if normalized == "new":
            status = "New"
        elif normalized == "existing":
            status = "Existing"
        else:
            status = raw_status.title() if raw_status else "Unspecified"
        status_counts[status] += 1

        created_date = str(row["created_at"] or "")[:10]
        if created_date:
            dates.append(created_date)

        campaign_id = str(row["utm_campaign_id"] or "").strip()
        campaign_name = str(row["utm_campaign"] or "").strip()
        has_valid_name = campaign_name.casefold() not in invalid_names
        bucket_key = f"name:{campaign_name.casefold()}" if has_valid_name else "unattributed"
        bucket = campaigns.setdefault(bucket_key, {
            "campaign_id": campaign_id or "Unattributed", "campaign_ids": set(),
            "leads": 0, "names": Counter(), "ad_set_ids": set(), "last_activity": None,
        })
        bucket["leads"] += 1
        if campaign_id:
            bucket["campaign_ids"].add(campaign_id)
            if bucket["campaign_id"] == "Unattributed":
                bucket["campaign_id"] = campaign_id
        if has_valid_name:
            bucket["names"][campaign_name] += 1
        ad_set_id = str(row["utm_ad_set_id"] or "").strip()
        if ad_set_id:
            bucket["ad_set_ids"].add(ad_set_id)
        if created_date and (bucket["last_activity"] is None or created_date > bucket["last_activity"]):
            bucket["last_activity"] = created_date

    status_order = {"New": 0, "Existing": 1}
    statuses = [
        {"status": status, "leads": count, "share": count / total if total else 0.0}
        for status, count in sorted(status_counts.items(), key=lambda item: (status_order.get(item[0], 2), -item[1]))
    ]
    campaign_rows = []
    for campaign_id, bucket in campaigns.items():
        if bucket["names"]:
            name = bucket["names"].most_common(1)[0][0]
        elif campaign_id == "Unattributed":
            name = "Unattributed"
        else:
            name = f"Campaign · {campaign_id[-6:]}"
        campaign_ids = sorted(bucket.get("campaign_ids", set()))
        campaign_id = campaign_ids[0] if len(campaign_ids) == 1 else bucket["campaign_id"]
        campaign_rows.append({
            "campaign_id": campaign_id,
            "campaign_ids": campaign_ids,
            "campaign": name,
            "leads": bucket["leads"],
            "share": bucket["leads"] / total if total else 0.0,
            "ad_set_count": len(bucket["ad_set_ids"]),
            "ad_set_ids": set(bucket["ad_set_ids"]),
            "last_activity": bucket["last_activity"],
        })
    campaign_rows.sort(key=lambda item: (-item["leads"], item["campaign"]))
    normalized_campaigns: dict[str, dict] = {}
    for row in campaign_rows:
        campaign_name = str(row["campaign"] or "").strip()
        if campaign_name.casefold() in invalid_names or campaign_name.startswith("Campaign "):
            campaign_name = "Unattributed"
        key = campaign_name.casefold()
        merged = normalized_campaigns.setdefault(key, {
            "campaign_id": row["campaign_id"],
            "campaign_ids": set(),
            "campaign": campaign_name,
            "leads": 0,
            "share": 0.0,
            "ad_set_count": 0,
            "ad_set_ids": set(),
            "last_activity": None,
        })
        raw_ids = row.get("campaign_ids") or [row["campaign_id"]]
        merged["campaign_ids"].update(str(value) for value in raw_ids if value and str(value) != "Unattributed")
        merged["leads"] += row["leads"]
        merged["ad_set_ids"].update(row.get("ad_set_ids", set()))
        if row["last_activity"] and (merged["last_activity"] is None or row["last_activity"] > merged["last_activity"]):
            merged["last_activity"] = row["last_activity"]
    campaign_rows = []
    for row in normalized_campaigns.values():
        campaign_ids = sorted(row.pop("campaign_ids"))
        row["campaign_ids"] = campaign_ids
        row["campaign_id"] = campaign_ids[0] if len(campaign_ids) == 1 else row["campaign_id"]
        row["ad_set_count"] = len(row.pop("ad_set_ids"))
        row["share"] = row["leads"] / total if total else 0.0
        campaign_rows.append(row)
    campaign_rows.sort(key=lambda item: (-item["leads"], item["campaign"]))

    return {
        "total_leads": total,
        "new_leads": status_counts.get("New", 0),
        "existing_leads": status_counts.get("Existing", 0),
        "new_share": status_counts.get("New", 0) / total if total else 0.0,
        "existing_share": status_counts.get("Existing", 0) / total if total else 0.0,
        "unique_campaigns": len([row for row in campaign_rows if row["campaign"] != "Unattributed"]),
        "unique_ad_sets": len({str(row["utm_ad_set_id"]) for row in source_rows if row["utm_ad_set_id"]}),
        "date_start": min(dates) if dates else None,
        "date_end": max(dates) if dates else None,
        "statuses": statuses,
        "campaigns": campaign_rows,
    }


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def get_ad_spend_analytics() -> dict:
    """Return dashboard-ready Meta ad spend analytics without changing lead forecasts."""
    with connect() as db:
        ad_rows = db.execute("SELECT * FROM daily_ad_performance ORDER BY day, campaign_name, ad_set_id").fetchall()
        actual_rows = db.execute(
            """SELECT date(created_at) day,
                      COALESCE(utm_campaign_id, '') campaign_id,
                      COALESCE(utm_campaign, '') campaign_name,
                      utm_ad_set_id ad_set_id,
                      COUNT(*) actual_leads
               FROM lead_events
               WHERE TRIM(COALESCE(utm_ad_set_id, '')) <> ''
               GROUP BY date(created_at), COALESCE(utm_campaign_id, ''), COALESCE(utm_campaign, ''), utm_ad_set_id
               ORDER BY day, campaign_name, ad_set_id"""
        ).fetchall()
    if not ad_rows:
        return {
            "available": False,
            "summary": {},
            "daily": [],
            "campaigns": [],
            "ad_sets": [],
            "best_cpl": [],
            "worst_cpl": [],
        }

    ad_frame = pd.DataFrame([dict(row) for row in ad_rows])
    actual_frame = pd.DataFrame([dict(row) for row in actual_rows]) if actual_rows else pd.DataFrame(
        columns=["day", "campaign_id", "campaign_name", "ad_set_id", "actual_leads"]
    )
    for column in [
        "amount_spent_usd", "leads", "reach", "impressions", "link_clicks",
        "unique_link_clicks", "messaging_conversations_started",
    ]:
        ad_frame[column] = pd.to_numeric(ad_frame[column], errors="coerce").fillna(0)
    actual_frame["actual_leads"] = pd.to_numeric(actual_frame.get("actual_leads", 0), errors="coerce").fillna(0)

    def summarize_frame(frame: pd.DataFrame) -> dict:
        spend = float(frame["amount_spent_usd"].sum())
        platform_leads = float(frame["leads"].sum())
        actual_leads = float(frame["actual_leads"].sum())
        impressions = float(frame["impressions"].sum())
        link_clicks = float(frame["link_clicks"].sum())
        unique_link_clicks = float(frame["unique_link_clicks"].sum())
        reach = float(frame["reach"].sum())
        return {
            "spend": spend,
            "platform_leads": platform_leads,
            "actual_leads": actual_leads,
            "lead_gap": actual_leads - platform_leads,
            "impressions": impressions,
            "reach": reach,
            "link_clicks": link_clicks,
            "unique_link_clicks": unique_link_clicks,
            "cpl": _safe_ratio(spend, actual_leads),
            "actual_cpl": _safe_ratio(spend, actual_leads),
            "meta_cpl": _safe_ratio(spend, platform_leads),
            "cpc": _safe_ratio(spend, link_clicks),
            "ctr": _safe_ratio(link_clicks, impressions),
            "frequency": _safe_ratio(impressions, reach),
        }

    linked_actual_frame = actual_frame.copy()
    if linked_actual_frame.empty:
        linked_actual_frame = pd.DataFrame(columns=actual_frame.columns)

    spend_metric_columns = [
        "amount_spent_usd", "leads", "reach", "impressions", "link_clicks",
        "unique_link_clicks", "messaging_conversations_started",
    ]

    def fill_spend_metrics(frame: pd.DataFrame) -> pd.DataFrame:
        for metric in spend_metric_columns:
            if metric not in frame.columns:
                frame[metric] = 0
            frame[metric] = pd.to_numeric(frame[metric], errors="coerce").fillna(0)
        if "actual_leads" not in frame.columns:
            frame["actual_leads"] = 0
        frame["actual_leads"] = pd.to_numeric(frame["actual_leads"], errors="coerce").fillna(0)
        return frame

    def coalesce_text_columns(frame: pd.DataFrame, column: str, fallback: str = "") -> pd.DataFrame:
        left = f"{column}_x"
        right = f"{column}_y"
        if left in frame.columns or right in frame.columns:
            left_values = frame[left] if left in frame.columns else pd.Series([None] * len(frame))
            right_values = frame[right] if right in frame.columns else pd.Series([None] * len(frame))
            frame[column] = left_values.where(left_values.notna() & (left_values.astype(str).str.strip() != ""), right_values)
            frame = frame.drop(columns=[name for name in [left, right] if name in frame.columns])
        if column not in frame.columns:
            frame[column] = fallback
        frame[column] = frame[column].fillna(fallback).astype(str)
        return frame

    daily_spend = ad_frame.groupby("day", as_index=False).agg({
        "amount_spent_usd": "sum",
        "leads": "sum",
        "reach": "sum",
        "impressions": "sum",
        "link_clicks": "sum",
        "unique_link_clicks": "sum",
        "messaging_conversations_started": "sum",
    })
    daily_actual = linked_actual_frame.groupby("day", as_index=False)["actual_leads"].sum()
    daily_merged = daily_spend.merge(daily_actual, on="day", how="outer").fillna(0).sort_values("day")

    campaign_spend = ad_frame.groupby(["campaign_id", "campaign_name"], as_index=False).agg({
        "amount_spent_usd": "sum",
        "leads": "sum",
        "reach": "sum",
        "impressions": "sum",
        "link_clicks": "sum",
        "unique_link_clicks": "sum",
        "messaging_conversations_started": "sum",
        "ad_set_id": "nunique",
    }).rename(columns={"ad_set_id": "ad_set_count"})
    campaign_actual = linked_actual_frame.groupby("campaign_id", as_index=False).agg({
        "campaign_name": "first",
        "actual_leads": "sum",
    })
    campaign_merged = campaign_spend.merge(campaign_actual, on="campaign_id", how="outer")
    campaign_merged = coalesce_text_columns(campaign_merged, "campaign_name")
    campaign_merged = fill_spend_metrics(campaign_merged).fillna({"ad_set_count": 0})

    ad_set_spend = ad_frame.groupby(["ad_set_id", "campaign_id", "campaign_name"], as_index=False).agg({
        "amount_spent_usd": "sum",
        "leads": "sum",
        "reach": "sum",
        "impressions": "sum",
        "link_clicks": "sum",
        "unique_link_clicks": "sum",
        "messaging_conversations_started": "sum",
        "day": "nunique",
    }).rename(columns={"day": "days"})
    ad_set_actual = linked_actual_frame.groupby("ad_set_id", as_index=False).agg({
        "campaign_id": "first",
        "campaign_name": "first",
        "actual_leads": "sum",
    })
    ad_set_merged = ad_set_spend.merge(ad_set_actual, on="ad_set_id", how="outer")
    ad_set_merged = coalesce_text_columns(ad_set_merged, "campaign_id")
    ad_set_merged = coalesce_text_columns(ad_set_merged, "campaign_name")
    ad_set_merged = fill_spend_metrics(ad_set_merged).fillna({"days": 0})

    summary_source = pd.DataFrame([{
        "amount_spent_usd": float(ad_frame["amount_spent_usd"].sum()),
        "leads": float(ad_frame["leads"].sum()),
        "reach": float(ad_frame["reach"].sum()),
        "impressions": float(ad_frame["impressions"].sum()),
        "link_clicks": float(ad_frame["link_clicks"].sum()),
        "unique_link_clicks": float(ad_frame["unique_link_clicks"].sum()),
        "actual_leads": float(linked_actual_frame["actual_leads"].sum()) if not linked_actual_frame.empty else 0,
    }])
    summary = summarize_frame(summary_source)
    spend_dates = pd.to_datetime(ad_frame["day"], errors="coerce").dropna()
    actual_dates = pd.to_datetime(linked_actual_frame["day"], errors="coerce").dropna() if not linked_actual_frame.empty else pd.Series(dtype="datetime64[ns]")
    all_dates = pd.concat([spend_dates, actual_dates], ignore_index=True).dropna()
    summary.update({
        "campaigns": int(ad_frame["campaign_id"].nunique()),
        "ad_sets": int(ad_frame["ad_set_id"].nunique()),
        "date_start": all_dates.min().date().isoformat() if len(all_dates) else None,
        "date_end": all_dates.max().date().isoformat() if len(all_dates) else None,
        "spend_date_start": str(ad_frame["day"].min()),
        "spend_date_end": str(ad_frame["day"].max()),
        "actual_date_start": actual_dates.min().date().isoformat() if len(actual_dates) else None,
        "actual_date_end": actual_dates.max().date().isoformat() if len(actual_dates) else None,
    })

    daily = []
    daily_renamed = daily_merged.rename(columns={"amount_spent_usd": "amount_spent_usd"})
    for _, row in daily_renamed.iterrows():
        item = summarize_frame(pd.DataFrame([row.to_dict()]))
        item["day"] = str(row["day"])
        daily.append(item)

    daily_campaigns = []
    daily_campaign_spend = ad_frame.groupby(["day", "campaign_id", "campaign_name"], as_index=False).agg({
        "amount_spent_usd": "sum",
        "leads": "sum",
        "reach": "sum",
        "impressions": "sum",
        "link_clicks": "sum",
        "unique_link_clicks": "sum",
        "messaging_conversations_started": "sum",
    })
    daily_campaign_actual = linked_actual_frame.groupby(["day", "campaign_id"], as_index=False).agg({
        "campaign_name": "first",
        "actual_leads": "sum",
    })
    daily_campaign_merged = daily_campaign_spend.merge(
        daily_campaign_actual, on=["day", "campaign_id"], how="outer"
    )
    daily_campaign_merged = coalesce_text_columns(daily_campaign_merged, "campaign_name")
    daily_campaign_merged = fill_spend_metrics(daily_campaign_merged).sort_values(["day", "campaign_name"])
    for _, row in daily_campaign_merged.iterrows():
        item = summarize_frame(pd.DataFrame([row.to_dict()]))
        item.update({
            "day": str(row["day"]),
            "campaign_id": str(row["campaign_id"]),
            "campaign_name": str(row["campaign_name"] or f"Campaign {str(row['campaign_id'])[-6:]}"),
        })
        daily_campaigns.append(item)

    daily_ad_sets = []
    daily_ad_set_spend = ad_frame.groupby(["day", "ad_set_id", "campaign_id", "campaign_name"], as_index=False).agg({
        "amount_spent_usd": "sum",
        "leads": "sum",
        "reach": "sum",
        "impressions": "sum",
        "link_clicks": "sum",
        "unique_link_clicks": "sum",
        "messaging_conversations_started": "sum",
    })
    daily_ad_set_actual = linked_actual_frame.groupby(["day", "ad_set_id"], as_index=False).agg({
        "campaign_id": "first",
        "campaign_name": "first",
        "actual_leads": "sum",
    })
    daily_ad_set_merged = daily_ad_set_spend.merge(
        daily_ad_set_actual, on=["day", "ad_set_id"], how="outer"
    )
    daily_ad_set_merged = coalesce_text_columns(daily_ad_set_merged, "campaign_id")
    daily_ad_set_merged = coalesce_text_columns(daily_ad_set_merged, "campaign_name")
    daily_ad_set_merged = fill_spend_metrics(daily_ad_set_merged).sort_values(["day", "campaign_name", "ad_set_id"])
    for _, row in daily_ad_set_merged.iterrows():
        item = summarize_frame(pd.DataFrame([row.to_dict()]))
        item.update({
            "day": str(row["day"]),
            "campaign_id": str(row["campaign_id"]),
            "campaign_name": str(row["campaign_name"] or f"Campaign {str(row['campaign_id'])[-6:]}"),
            "ad_set_id": str(row["ad_set_id"]),
        })
        daily_ad_sets.append(item)

    campaigns = []
    for _, row in campaign_merged.iterrows():
        item = summarize_frame(pd.DataFrame([row.to_dict()]))
        item.update({
            "campaign_id": str(row["campaign_id"]),
            "campaign_name": str(row["campaign_name"] or f"Campaign {str(row['campaign_id'])[-6:]}"),
            "ad_set_count": int(row["ad_set_count"] or 0),
        })
        campaigns.append(item)
    campaigns.sort(key=lambda item: (float("inf") if item["actual_cpl"] is None else float(item["actual_cpl"]), -item["actual_leads"], item["campaign_name"]))

    ad_sets = []
    for _, row in ad_set_merged.iterrows():
        item = summarize_frame(pd.DataFrame([row.to_dict()]))
        item.update({
            "ad_set_id": str(row["ad_set_id"]),
            "campaign_id": str(row["campaign_id"]),
            "campaign_name": str(row["campaign_name"] or f"Campaign {str(row['campaign_id'])[-6:]}"),
            "days": int(row["days"] or 0),
        })
        ad_sets.append(item)
    ad_sets.sort(key=lambda item: (float("inf") if item["actual_cpl"] is None else float(item["actual_cpl"]), -item["actual_leads"], item["ad_set_id"]))
    efficient = [item for item in ad_sets if item["actual_leads"] > 0 and _finite_or_none(item["actual_cpl"]) is not None]
    best_cpl = sorted(efficient, key=lambda item: (float(item["actual_cpl"]), -item["actual_leads"]))[:5]
    worst_cpl = sorted(efficient, key=lambda item: (-float(item["actual_cpl"]), -item["actual_leads"]))[:5]

    return {
        "available": True,
        "summary": summary,
        "daily": daily,
        "daily_campaigns": daily_campaigns,
        "daily_ad_sets": daily_ad_sets,
        "campaigns": campaigns[:25],
        "ad_sets": ad_sets[:25],
        "best_cpl": best_cpl,
        "worst_cpl": worst_cpl,
    }


# --- Ad decision engine -------------------------------------------------
# Turns spend + attributed leads into a per-ad-set verdict (scale / keep /
# watch / cut) plus a concrete reallocation plan. Verdicts are graded against
# a benchmark CPL: either the portfolio blended CPL or an explicit target.

DECISION_WINDOW_DAYS = 14
_SCALE_CEILING = 0.75    # <= 75% of benchmark CPL -> scale
_KEEP_CEILING = 1.10     # <= 110% -> keep
_WATCH_CEILING = 1.60    # <= 160% -> watch, above -> cut
_MIN_SPEND_ACTIVE = 1.0  # below this a set counts as paused, not failing
_SCALE_STEP = 0.30       # budget increase suggested for scale verdicts
_CUT_STEP = 0.50         # budget decrease suggested for cut verdicts


def _money(value: float | None) -> str:
    """Compact USD for headline copy: $1.28, $86, $1.2k."""
    if value is None:
        return "$0"
    amount = float(value)
    if abs(amount) >= 1000:
        return f"${amount / 1000:.1f}k"
    if abs(amount) >= 10:
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


_GENERIC_CAMPAIGN_WORDS = {"leads", "engagement", "traffic", "msr"}


def _shorten_campaign(name: str) -> str:
    """'Leads | VISA | AU | KHM' -> 'VISA | AU | KHM'. Drops generic objective words only."""
    parts = [part.strip() for part in str(name).split("|") if part.strip()]
    kept = [part for part in parts if part.strip().lower() not in _GENERIC_CAMPAIGN_WORDS]
    return " | ".join(kept) if kept else (" | ".join(parts) if parts else str(name))


def _distinguishing_id_fragment(ad_set_id: str, siblings: Iterable[str], width: int = 4) -> str:
    """Return the shortest window of `ad_set_id` that tells it apart from its siblings.

    Meta ad set IDs look like 120249276038040078: a shared leading run and an identical
    trailing account suffix, with the entropy in the middle. Slicing a fixed number of
    characters off either end prints the same fragment for every row.
    """
    identifier = str(ad_set_id)
    others = [str(value) for value in siblings if str(value) != identifier]
    if not others:
        return identifier[-width:]
    for start in range(len(identifier)):
        fragment = identifier[start:start + width]
        if all(other[start:start + width] != fragment for other in others):
            return fragment
    return identifier


def _trend_direction(delta: float | None) -> str:
    if delta is None:
        return "flat"
    if delta <= -0.10:
        return "improving"
    if delta >= 0.10:
        return "worsening"
    return "flat"


# One action per ad set, reconciling the two signals this page carries. They answer different
# questions -- "is this cheap against the portfolio" and "does it get cheaper when we feed it" --
# and they genuinely disagree: an ad set can sit well under benchmark while every budget rise
# buys leads that cost more than the ones already arriving. Showing both verdicts side by side
# put contradictory advice on one row (boost +$2.96/day next to "cost is climbing"), so the two
# collapse here into the single thing to actually do.
_ACTION_ORDER = {"cut": 0, "trim": 1, "scale": 2, "watch": 3, "keep": 4, "paused": 5}
_TRIM_MAX_SHARE = 0.6  # never suggest pulling more than this much of current daily spend


def _combine_action(verdict: str, budget: dict | None) -> str:
    """Fold the benchmark verdict and the budget-response curve into one action."""
    if verdict in {"paused", "cut"}:
        return verdict
    # Flat leads under a raised budget is the only case where money is provably buying nothing,
    # and it outranks a good benchmark score: being cheap does not make wasted spend worth keeping.
    if budget and budget.get("plateaued"):
        return "trim"
    return verdict


def _trim_delta(item: dict, budget: dict | None) -> float:
    """Daily budget to pull back from a plateaued ad set.

    Pulls back to the spend level it held before the leads stopped responding -- the plainest
    defensible number, and the one the row can explain in its own words.
    """
    ceiling = -round(item["daily_spend"] * _TRIM_MAX_SHARE, 2)
    if not budget:
        return ceiling
    before, now = budget.get("budget_from"), budget.get("budget_to")
    if before is None or now is None or now <= before:
        return ceiling
    return max(ceiling, -round(float(now) - float(before), 2))


def get_ad_decisions(window_days: int = DECISION_WINDOW_DAYS,
                     target_cpl: float | None = None) -> dict:
    """Per-ad-set boost/cut recommendations with a portfolio reallocation plan."""
    with connect() as db:
        anchor_row = db.execute("SELECT MAX(day) FROM daily_ad_performance").fetchone()
        anchor = anchor_row[0] if anchor_row else None
        if not anchor:
            return {"available": False, "ads": [], "summary": {}, "reallocation": {}}

        # Two adjacent windows ending at the last day with spend data.
        recent_start = db.execute("SELECT date(?, ?)", (anchor, f"-{window_days - 1} days")).fetchone()[0]
        prior_start = db.execute("SELECT date(?, ?)", (anchor, f"-{window_days * 2 - 1} days")).fetchone()[0]
        prior_end = db.execute("SELECT date(?, ?)", (recent_start, "-1 day")).fetchone()[0]

        spend_rows = db.execute(
            """SELECT ad_set_id, day,
                      SUM(COALESCE(amount_spent_usd, 0)) spend,
                      SUM(COALESCE(impressions, 0)) impressions,
                      SUM(COALESCE(link_clicks, 0)) link_clicks,
                      MAX(COALESCE(campaign_name, '')) campaign_name,
                      MAX(COALESCE(campaign_id, '')) campaign_id,
                      MAX(COALESCE(delivery_status, '')) delivery_status
               FROM daily_ad_performance
               WHERE day BETWEEN ? AND ?
               GROUP BY ad_set_id, day
               ORDER BY ad_set_id, day""",
            (prior_start, anchor),
        ).fetchall()
        lead_rows = db.execute(
            """SELECT utm_ad_set_id ad_set_id, date(created_at) day, COUNT(*) leads
               FROM lead_events
               WHERE TRIM(COALESCE(utm_ad_set_id, '')) <> ''
                 AND date(created_at) BETWEEN ? AND ?
               GROUP BY utm_ad_set_id, date(created_at)""",
            (prior_start, anchor),
        ).fetchall()

    ads: dict[str, dict] = {}

    def slot(ad_set_id: str) -> dict:
        return ads.setdefault(ad_set_id, {
            "ad_set_id": ad_set_id, "campaign_id": "", "campaign_name": "",
            "delivery_status": "",
            "spend_recent": 0.0, "spend_prior": 0.0,
            "leads_recent": 0.0, "leads_prior": 0.0,
            "impressions_recent": 0.0, "clicks_recent": 0.0,
            "series": {},
        })

    for row in spend_rows:
        item = slot(str(row["ad_set_id"]))
        day, spend = str(row["day"]), float(row["spend"] or 0)
        if row["campaign_name"]:
            item["campaign_name"] = str(row["campaign_name"])
        if row["campaign_id"]:
            item["campaign_id"] = str(row["campaign_id"])
        if row["delivery_status"]:
            item["delivery_status"] = str(row["delivery_status"])
        if day >= recent_start:
            item["spend_recent"] += spend
            item["impressions_recent"] += float(row["impressions"] or 0)
            item["clicks_recent"] += float(row["link_clicks"] or 0)
            point = item["series"].setdefault(day, {"day": day, "spend": 0.0, "leads": 0.0})
            point["spend"] += spend
        else:
            item["spend_prior"] += spend

    for row in lead_rows:
        item = slot(str(row["ad_set_id"]))
        day, leads = str(row["day"]), float(row["leads"] or 0)
        if day >= recent_start:
            item["leads_recent"] += leads
            point = item["series"].setdefault(day, {"day": day, "spend": 0.0, "leads": 0.0})
            point["leads"] += leads
        else:
            item["leads_prior"] += leads

    total_spend = sum(item["spend_recent"] for item in ads.values())
    total_leads = sum(item["leads_recent"] for item in ads.values())
    blended_cpl = _safe_ratio(total_spend, total_leads)
    benchmark = float(target_cpl) if target_cpl and float(target_cpl) > 0 else blended_cpl
    benchmark_source = "target" if target_cpl and float(target_cpl) > 0 else "blended"

    results = []
    for item in ads.values():
        cpl_recent = _safe_ratio(item["spend_recent"], item["leads_recent"])
        cpl_prior = _safe_ratio(item["spend_prior"], item["leads_prior"])
        cpl_delta = ((cpl_recent - cpl_prior) / cpl_prior) if (cpl_recent and cpl_prior) else None
        active = item["spend_recent"] >= _MIN_SPEND_ACTIVE
        daily_spend = item["spend_recent"] / window_days if window_days else 0.0

        # Grade against the benchmark, then let a strong trend move the verdict
        # one notch — an expensive ad that is rapidly improving is not a cut.
        if not active:
            cpl_recent = None  # "$0.00 per lead" would read as free, not as stopped
            count = int(item["leads_recent"])
            verdict, reason = "paused", (
                "No spend in this window." if count <= 0
                else f"{count} lead{'s' if count != 1 else ''} still arriving with no active spend.")
        elif item["leads_recent"] <= 0:
            verdict, reason = "cut", f"{_money(item['spend_recent'])} spent, zero attributed leads."
        elif benchmark is None or cpl_recent is None:
            verdict, reason = "keep", "Not enough data to grade."
        else:
            ratio = cpl_recent / benchmark
            if ratio <= _SCALE_CEILING:
                verdict = "scale"
                reason = f"{round((1 - ratio) * 100)}% cheaper per lead than benchmark."
            elif ratio <= _KEEP_CEILING:
                verdict, reason = "keep", "Performing in line with benchmark."
            elif ratio <= _WATCH_CEILING:
                verdict = "watch"
                reason = f"{round((ratio - 1) * 100)}% above benchmark cost per lead."
            else:
                verdict = "cut"
                reason = f"{round((ratio - 1) * 100)}% above benchmark cost per lead."
            if cpl_delta is not None:
                if cpl_delta <= -0.25 and verdict in {"watch", "cut"}:
                    verdict = "watch" if verdict == "cut" else "keep"
                    reason += f" Improving fast ({round(abs(cpl_delta) * 100)}% cheaper vs prior period)."
                elif cpl_delta >= 0.25 and verdict in {"scale", "keep"}:
                    verdict = "keep" if verdict == "scale" else "watch"
                    reason += f" But cost is climbing ({round(cpl_delta * 100)}% vs prior period)."

        if verdict == "scale":
            suggested_delta = daily_spend * _SCALE_STEP
        elif verdict == "cut":
            suggested_delta = -daily_spend * (1.0 if item["leads_recent"] <= 0 else _CUT_STEP)
        else:
            suggested_delta = 0.0

        campaign_name = item["campaign_name"] or f"Ad set {item['ad_set_id'][-6:]}"
        series = [item["series"][key] for key in sorted(item["series"])]
        for point in series:
            point["cpl"] = _safe_ratio(point["spend"], point["leads"])

        results.append({
            "ad_set_id": item["ad_set_id"],
            "campaign_id": item["campaign_id"],
            "campaign_name": campaign_name,
            "label": _shorten_campaign(campaign_name),
            "delivery_status": item["delivery_status"],
            "spend": round(item["spend_recent"], 2),
            "spend_prior": round(item["spend_prior"], 2),
            "daily_spend": round(daily_spend, 2),
            "leads": int(item["leads_recent"]),
            "leads_prior": int(item["leads_prior"]),
            "cpl": _finite_or_none(cpl_recent),
            "cpl_prior": _finite_or_none(cpl_prior),
            "cpl_delta_pct": _finite_or_none(cpl_delta),
            "trend": _trend_direction(cpl_delta),
            "spend_share": _safe_ratio(item["spend_recent"], total_spend) or 0.0,
            "lead_share": _safe_ratio(item["leads_recent"], total_leads) or 0.0,
            "ctr": _safe_ratio(item["clicks_recent"], item["impressions_recent"]),
            "verdict": verdict,
            "reason": reason,
            "suggested_daily_delta": round(suggested_delta, 2),
            "series": series,
        })

    # Several ad sets can share one campaign name; a bare label would make two
    # different rows look like the same ad. Suffix only the ones that collide, and suffix
    # them with the part of the ID that actually differs -- Meta ad set IDs share a long
    # common prefix and all end in the same account suffix, so a fixed slice of either end
    # would print the same characters for every row and disambiguate nothing.
    # Group before renaming: rewriting labels in the same pass that looks for collisions
    # would hide each already-renamed row from the rows still to be processed.
    label_groups: dict[str, list[dict]] = {}
    for item in results:
        label_groups.setdefault(item["label"], []).append(item)
    for label, group in label_groups.items():
        if len(group) <= 1:
            continue
        identifiers = [entry["ad_set_id"] for entry in group]
        for entry in group:
            siblings = [value for value in identifiers if value != entry["ad_set_id"]]
            entry["label"] = f"{label} · {_distinguishing_id_fragment(entry['ad_set_id'], siblings)}"

    # Join each ad set's own budget-response curve, then collapse the two signals into one action.
    try:
        budget_data = get_budget_optimization()
    except Exception:  # a missing/empty budget history must not take the whole page down
        budget_data = {"ad_sets": []}
    budget_by_id = {entry["ad_set_id"]: entry for entry in budget_data.get("ad_sets", [])}

    for item in results:
        budget = budget_by_id.get(item["ad_set_id"])
        item["budget"] = budget
        item["action"] = _combine_action(item["verdict"], budget)
        if item["action"] == "trim":
            item["suggested_daily_delta"] = _trim_delta(item, budget)
            item["reason"] = (budget or {}).get("reason") or item["reason"]
        elif item["action"] != item["verdict"]:
            item["suggested_daily_delta"] = 0.0

    # Declared variables 4/6/7 are shown on this table (see Vault/Features/
    # Ad-Decision-Engine.md) but deliberately left unpopulated -- the detector-inferred
    # values were wrong and are pending real data instead of a better detector. The two
    # change-TYPE columns that used to sit here were removed entirely on 2026-08-11.
    for item in results:
        item["days_since_adset_started"] = None
        item["ad_change_recency"] = None
        item["ad_set_change_recency"] = None

    results.sort(key=lambda item: (_ACTION_ORDER.get(item["action"], 9), -item["spend"]))

    scale_ads = [item for item in results if item["action"] == "scale"]
    cut_ads = [item for item in results if item["action"] == "cut"]
    trim_ads = [item for item in results if item["action"] == "trim"]
    watch_ads = [item for item in results if item["action"] == "watch"]
    freeing_ads = cut_ads + trim_ads

    # Freed budget flows to scale candidates weighted by lead efficiency
    # (1/CPL), so the cheapest performer absorbs the largest share.
    cut_freed = round(sum(-item["suggested_daily_delta"] for item in cut_ads), 2)
    trim_freed = round(sum(-item["suggested_daily_delta"] for item in trim_ads), 2)
    freed = round(cut_freed + trim_freed, 2)
    moves = []
    if freed > 0 and scale_ads:
        weights = [(1.0 / item["cpl"]) if item["cpl"] else 0.0 for item in scale_ads]
        weight_total = sum(weights) or 1.0
        for target, weight in zip(scale_ads, weights):
            amount = round(freed * (weight / weight_total), 2)
            if amount >= 0.01:
                moves.append({
                    "to_ad_set_id": target["ad_set_id"], "to_label": target["label"],
                    "amount": amount, "to_cpl": target["cpl"],
                    "expected_leads": round(amount / target["cpl"], 1) if target["cpl"] else None,
                })
    cut_cpl = _safe_ratio(sum(item["spend"] for item in cut_ads),
                          sum(item["leads"] for item in cut_ads))
    gained = sum(move["expected_leads"] or 0 for move in moves)
    # Only budget pulled from cuts costs leads. Trimmed budget is, by the definition that earned
    # the trim, money that stopped converting -- charging it the cut rate would understate the move.
    lost = (cut_freed / cut_cpl) if cut_cpl else 0.0
    net_leads = round(gained - lost, 1)

    if not results:
        headline = "No ad activity in this window."
    elif moves:
        if len(moves) == 1:
            destination = moves[0]["to_label"]
        elif len(moves) == 2:
            destination = f"{moves[0]['to_label']} and {moves[1]['to_label']}"
        else:
            destination = f"{moves[0]['to_label']} and {len(moves) - 1} other strong performers"
        source = f"{len(freeing_ads)} ad set{'s' if len(freeing_ads) != 1 else ''}"
        headline = (f"Move {_money(freed)}/day out of {source} into "
                    f"{destination} — worth about {net_leads:+.0f} leads a day.")
    elif freeing_ads:
        headline = (f"{len(freeing_ads)} ad set{'s' if len(freeing_ads) != 1 else ''} "
                    f"{'are' if len(freeing_ads) != 1 else 'is'} burning "
                    f"{_money(sum(item['spend'] for item in freeing_ads))} with no efficient "
                    f"ad set to absorb the budget — pull the money back and hold it.")
    elif scale_ads:
        headline = (f"Nothing needs cutting. {scale_ads[0]['label']} is your cheapest "
                    f"source at {_money(scale_ads[0]['cpl'])} per lead — push more budget there.")
    else:
        headline = "Every ad is performing near benchmark. No reallocation needed."

    return {
        "available": True,
        "window_days": window_days,
        "window_start": recent_start,
        "window_end": anchor,
        "prior_start": prior_start,
        "prior_end": prior_end,
        "summary": {
            "spend": round(total_spend, 2),
            "leads": int(total_leads),
            "blended_cpl": _finite_or_none(blended_cpl),
            "benchmark_cpl": _finite_or_none(benchmark),
            "benchmark_source": benchmark_source,
            "ad_count": len(results),
            "scale_count": len(scale_ads),
            "keep_count": len([item for item in results if item["action"] == "keep"]),
            "watch_count": len(watch_ads),
            "trim_count": len(trim_ads),
            "cut_count": len(cut_ads),
            "paused_count": len([item for item in results if item["action"] == "paused"]),
            "measured_count": len([item for item in results
                                   if (item.get("budget") or {}).get("verdict") not in (None, "unknown")]),
            "headline": headline,
        },
        "reallocation": {
            "freed_daily": freed,
            "trim_daily": trim_freed,
            "moves": moves,
            "net_daily_leads": net_leads,
            "cut_cpl": _finite_or_none(cut_cpl),
        },
        "ads": results,
    }


def get_portfolio_forecast_tracking(
    history_days: int = 90,
    future_days: int = 14,
    start_date: str | None = None,
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
) -> dict:
    """Return daily actuals and frozen forecast phases for each data upload.

    A phase uses the latest completed training run before the next upload. This keeps
    repeated same-data retrains from drawing overlapping forecasts while preserving a
    stable snapshot that can be evaluated as new actuals arrive.
    """
    refresh_forecast_realizations()
    history_days = max(1, min(int(history_days), 90))
    future_days = max(1, min(int(future_days), 14))
    campaign_id = str(campaign_id or "").strip() or None
    ad_set_id = str(ad_set_id or "").strip() or None
    actual_filters: list[str] = []
    actual_params: list[object] = []
    if campaign_id:
        actual_filters.append("utm_campaign_id=?")
        actual_params.append(campaign_id)
    if ad_set_id:
        actual_filters.append("utm_ad_set_id=?")
        actual_params.append(ad_set_id)
    actual_filter_sql = f"WHERE {' AND '.join(actual_filters)}" if actual_filters else ""

    with connect() as db:
        latest_actual_date = db.execute(
            "SELECT MAX(aggregate_date) FROM daily_ad_set_aggregates"
        ).fetchone()[0]
        actual_rows = db.execute(
            """SELECT aggregate_date forecast_date, SUM(lead_count) actual_leads,
                      COUNT(DISTINCT utm_ad_set_id) ad_set_count
               FROM daily_ad_set_aggregates
               {actual_filter_sql}
               GROUP BY aggregate_date""".format(actual_filter_sql=actual_filter_sql),
            actual_params,
        ).fetchall()
        uploads = [dict(row) for row in db.execute(
            """SELECT * FROM raw_uploads
               WHERE status='imported' ORDER BY datetime(uploaded_at), id"""
        ).fetchall()]
        runs = [dict(row) for row in db.execute(
            """SELECT * FROM model_training_runs
               WHERE status='completed' ORDER BY datetime(completed_at), id"""
        ).fetchall()]

        phase_sources: list[dict] = []
        for index, upload in enumerate(uploads):
            next_uploaded_at = uploads[index + 1]["uploaded_at"] if index + 1 < len(uploads) else None
            eligible_runs = [
                run for run in runs
                if str(run.get("completed_at") or run.get("started_at") or "") >= str(upload["uploaded_at"])
                and (not next_uploaded_at or str(run.get("completed_at") or "") < str(next_uploaded_at))
            ]
            if not eligible_runs:
                continue
            phase_sources.append({
                "phase_number": index + 1,
                "upload": upload,
                "run": eligible_runs[-1],
                "retrain_count": len(eligible_runs),
            })

        if not phase_sources and runs:
            phase_sources.append({
                "phase_number": 1,
                "upload": None,
                "run": runs[-1],
                "retrain_count": 1,
            })

        selected_run_ids = [int(source["run"]["id"]) for source in phase_sources]
        prediction_rows: list[dict] = []
        if selected_run_ids:
            params: list[object] = [*selected_run_ids]
            where = f"training_run_id IN ({','.join('?' for _ in selected_run_ids)})"
            if campaign_id:
                where += " AND utm_campaign_id=?"
                params.append(campaign_id)
            if ad_set_id:
                where += " AND utm_ad_set_id=?"
                params.append(ad_set_id)
            prediction_rows = [dict(row) for row in db.execute(
                f"""SELECT * FROM forecast_daily_predictions
                    WHERE {where}
                    ORDER BY training_run_id, date(forecast_date), utm_ad_set_id""",
                params,
            ).fetchall()]

    actual_by_date = {
        str(row["forecast_date"]): {
            "actual_leads": float(row["actual_leads"] or 0.0),
            "ad_set_count": int(row["ad_set_count"] or 0),
        }
        for row in actual_rows
    }
    actual_values = [float(row["actual_leads"] or 0.0) for row in actual_rows]
    historical_max = max(actual_values, default=0.0)
    historical_average = float(np.mean(actual_values)) if actual_values else 0.0
    anomaly_daily_limit = max(50.0, historical_max * 8.0, historical_average * 12.0)

    predictions_by_run: dict[int, list[dict]] = {}
    for row in prediction_rows:
        predictions_by_run.setdefault(int(row["training_run_id"]), []).append(row)

    def aggregate(rows: Iterable[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for row in rows:
            day = str(row["forecast_date"])
            bucket = grouped.setdefault(day, {
                "date": day, "forecast_leads": 0.0, "lower_estimate": 0.0,
                "upper_estimate": 0.0, "ad_set_ids": set(),
                "training_run_id": row.get("training_run_id"),
            })
            bucket["forecast_leads"] += float(row["predicted_leads"] or 0.0)
            bucket["lower_estimate"] += float(row["lower_estimate"] or 0.0)
            bucket["upper_estimate"] += float(row["upper_estimate"] or 0.0)
            bucket["ad_set_ids"].add(str(row["utm_ad_set_id"]))
            bucket["training_run_id"] = row.get("training_run_id")

        points = []
        for day in sorted(grouped):
            bucket = grouped[day]
            actual = float(actual_by_date.get(day, {"actual_leads": 0.0})["actual_leads"]) \
                if latest_actual_date and day <= latest_actual_date else None
            forecast = bucket["forecast_leads"]
            points.append({
                "date": day,
                "actual_leads": actual,
                "forecast_leads": round(forecast, 1),
                "lower_estimate": round(bucket["lower_estimate"], 1),
                "upper_estimate": round(bucket["upper_estimate"], 1),
                "difference": round(forecast - actual, 1) if actual is not None else None,
                "absolute_error": round(abs(forecast - actual), 1) if actual is not None else None,
                "ad_set_count": len(bucket["ad_set_ids"]),
                "training_run_id": bucket["training_run_id"],
                "state": "realized" if actual is not None else "future",
            })
        return points

    preliminary_phases: list[dict] = []
    for source in phase_sources:
        run = source["run"]
        points = aggregate(predictions_by_run.get(int(run["id"]), []))
        if not points:
            continue
        max_daily_forecast = max(float(point["forecast_leads"] or 0.0) for point in points)
        if max_daily_forecast > anomaly_daily_limit:
            continue
        upload = source["upload"]
        preliminary_phases.append({
            "phase_number": source["phase_number"],
            "phase_id": f"phase-{source['phase_number']}-run-{run['id']}",
            "label": f"Phase {source['phase_number']}",
            "upload_id": upload.get("id") if upload else None,
            "upload_file": upload.get("file_name") if upload else None,
            "uploaded_at": upload.get("uploaded_at") if upload else None,
            "data_start": upload.get("date_min") if upload else None,
            "data_end": upload.get("date_max") if upload else None,
            "imported_rows": int(upload.get("imported_count") or 0) if upload else None,
            "training_run_id": int(run["id"]),
            "trained_at": run.get("completed_at"),
            "training_rows": int(run.get("training_rows") or 0),
            "trained_ad_sets": int(run.get("ad_set_count") or 0),
            "backtest_accuracy": run.get("mean_backtest_accuracy"),
            "retrain_count": int(source["retrain_count"]),
            "forecast_start": points[0]["date"],
            "forecast_end": points[-1]["date"],
            "points": points,
        })

    phases: list[dict] = []
    phase_point_by_date: dict[str, dict] = {}
    previous_backtest_accuracy: float | None = None
    previous_actual_accuracy: float | None = None
    for index, phase in enumerate(preliminary_phases):
        next_start = preliminary_phases[index + 1]["forecast_start"] if index + 1 < len(preliminary_phases) else None
        active_end = phase["forecast_end"]
        if next_start:
            active_end = min(active_end, (pd.Timestamp(next_start) - pd.Timedelta(days=1)).date().isoformat())

        active_points = [point for point in phase.pop("points") if point["date"] <= active_end]
        # A run superseded before any of its horizon elapsed owns no days at all: clamping
        # against the next run's start pushes active_end behind active_start. Retraining
        # several times in a session produces a string of these, and drawing them stacks
        # phase markers on the same pixel. They are not phases, so they are not emitted.
        if active_end < phase["forecast_start"] or not active_points:
            continue
        if index == len(preliminary_phases) - 1:
            future_seen = 0
            limited_points = []
            for point in active_points:
                if point["state"] == "future":
                    future_seen += 1
                    if future_seen > future_days:
                        continue
                limited_points.append(point)
            active_points = limited_points
            if active_points:
                active_end = active_points[-1]["date"]

        comparable = [point for point in active_points if point["actual_leads"] is not None]
        errors = [float(point["absolute_error"]) for point in comparable]
        actual_total = sum(float(point["actual_leads"] or 0.0) for point in comparable)
        forecast_total = sum(float(point["forecast_leads"] or 0.0) for point in comparable)
        wape = sum(errors) / max(actual_total, 1.0) if errors else None
        actual_accuracy = max(0.0, 1.0 - wape) if wape is not None else None
        coverage = np.mean([
            float(point["lower_estimate"]) <= float(point["actual_leads"]) <= float(point["upper_estimate"])
            for point in comparable
        ]) if comparable else None
        backtest_accuracy = float(phase["backtest_accuracy"]) if phase["backtest_accuracy"] is not None else None

        # Numbered over the phases that survive, so the label matches the marker the chart
        # draws. Numbering off the run sequence instead would leave gaps wherever a
        # superseded run was dropped above. phase_id keeps the original run for tracing.
        display_number = len(phases) + 1
        phase.update({
            "phase_number": display_number,
            "label": f"Phase {display_number}",
            "active_start": phase["forecast_start"],
            "active_end": active_end,
            "realized_days": len(comparable),
            "forecast_days": len(active_points),
            "mae": float(np.mean(errors)) if errors else None,
            "wape": wape,
            "actual_accuracy": actual_accuracy,
            "interval_coverage": float(coverage) if coverage is not None else None,
            "realized_actual_total": round(actual_total, 1),
            "realized_forecast_total": round(forecast_total, 1),
            "backtest_delta_points": round((backtest_accuracy - previous_backtest_accuracy) * 100, 1)
                if backtest_accuracy is not None and previous_backtest_accuracy is not None else None,
            "actual_accuracy_delta_points": round((actual_accuracy - previous_actual_accuracy) * 100, 1)
                if actual_accuracy is not None and previous_actual_accuracy is not None else None,
            "status": "complete" if comparable and len(comparable) == len(active_points)
                else "monitoring" if comparable else "awaiting_actuals",
        })
        phases.append(phase)
        if backtest_accuracy is not None:
            previous_backtest_accuracy = backtest_accuracy
        if actual_accuracy is not None:
            previous_actual_accuracy = actual_accuracy

        for point in active_points:
            phase_point_by_date[point["date"]] = {
                **point,
                "phase_id": phase["phase_id"],
                "phase_number": phase["phase_number"],
                "phase_label": phase["label"],
                "phase_start": phase["active_start"],
                "phase_end": phase["active_end"],
            }

    earliest_actual_date = min(actual_by_date, default=None)
    history_start = earliest_actual_date
    if start_date:
        try:
            normalized_start = pd.Timestamp(start_date).date().isoformat()
            history_start = max(filter(None, [earliest_actual_date, normalized_start]), default=normalized_start)
        except (TypeError, ValueError):
            history_start = earliest_actual_date

    if not history_start and phases:
        history_start = phases[0]["active_start"]
    timeline_end = max(filter(None, [latest_actual_date, phases[-1]["active_end"] if phases else None]), default=None)
    timeline: list[dict] = []
    if history_start and timeline_end:
        for timestamp in pd.date_range(history_start, timeline_end, freq="D"):
            day = timestamp.date().isoformat()
            phase_point = phase_point_by_date.get(day)
            actual = actual_by_date.get(day, {"actual_leads": 0.0, "ad_set_count": 0})
            actual_leads = float(actual["actual_leads"]) if latest_actual_date and day <= latest_actual_date else None
            if phase_point:
                timeline.append({**phase_point, "actual_leads": actual_leads,
                    "difference": round(float(phase_point["forecast_leads"]) - actual_leads, 1) if actual_leads is not None else None,
                    "absolute_error": round(abs(float(phase_point["forecast_leads"]) - actual_leads), 1) if actual_leads is not None else None,
                    "state": "realized" if actual_leads is not None else "future"})
            else:
                timeline.append({
                    "date": day, "actual_leads": actual_leads, "forecast_leads": None,
                    "lower_estimate": None, "upper_estimate": None, "difference": None,
                    "absolute_error": None, "ad_set_count": int(actual["ad_set_count"]),
                    "training_run_id": None, "state": "historical" if actual_leads is not None else "future",
                    "phase_id": None, "phase_number": None, "phase_label": None,
                    "phase_start": None, "phase_end": None,
                })
    if not start_date:
        if latest_actual_date:
            history_floor = (
                pd.Timestamp(latest_actual_date) - pd.Timedelta(days=history_days - 1)
            ).date().isoformat()
            timeline = [point for point in timeline if point["date"] >= history_floor]
        else:
            timeline = timeline[-history_days:]

    comparable_realized = [point for point in timeline if point.get("absolute_error") is not None]
    errors = [float(point["absolute_error"]) for point in comparable_realized]
    actual_total = sum(float(point["actual_leads"] or 0.0) for point in comparable_realized)
    forecast_total = sum(float(point["forecast_leads"] or 0.0) for point in comparable_realized)
    interval_hits = [
        float(point["lower_estimate"]) <= float(point["actual_leads"]) <= float(point["upper_estimate"])
        for point in comparable_realized
    ]
    latest_run = phase_sources[-1]["run"] if phase_sources else None
    return {
        "summary": {
            "latest_actual_date": latest_actual_date,
            "history_start_date": timeline[0]["date"] if timeline else None,
            "timeline_end_date": timeline[-1]["date"] if timeline else None,
            "actual_days": len([point for point in timeline if point["actual_leads"] is not None]),
            "next_forecast_date": next((point["date"] for point in timeline if point["state"] == "future" and point["forecast_leads"] is not None), None),
            "realized_days": len(comparable_realized),
            "future_days": len([point for point in timeline if point["state"] == "future" and point["forecast_leads"] is not None]),
            "production_mae_daily": float(np.mean(errors)) if errors else None,
            "production_wape_daily": sum(errors) / max(actual_total, 1.0) if errors else None,
            "interval_coverage_daily": float(np.mean(interval_hits)) if interval_hits else None,
            "realized_actual_total": round(actual_total, 1),
            "realized_forecast_total": round(forecast_total, 1),
            "latest_training_run_id": latest_run["id"] if latest_run else None,
            "latest_training_completed_at": latest_run["completed_at"] if latest_run else None,
            "campaign_id": campaign_id,
            "ad_set_id": ad_set_id,
            "scope": "ad_set" if ad_set_id else "campaign" if campaign_id else "portfolio",
            "phase_count": len(phases),
        },
        "phases": phases,
        "timeline": timeline,
    }


def refresh_forecast_realizations() -> dict:
    """Attach actual lead counts to any stored daily forecasts whose dates have arrived.

    Split read / compute / write rather than doing everything in one transaction: this table
    runs ~90k rows, and holding SQLite's single write lock across the SELECT and the
    arithmetic blocked interactive lead edits for ~700ms. Only the final writes are inside a
    transaction now, and only rows whose `actual_leads` actually changed are rewritten -- a
    routine retrain usually changes a handful, so the locked section is milliseconds instead
    of a full-table rewrite.
    """
    realized_at = utc_now()
    with connect() as db:
        actual_range = db.execute(
            "SELECT MIN(aggregate_date), MAX(aggregate_date) FROM daily_ad_set_aggregates"
        ).fetchone()
        earliest_date, latest_date = actual_range[0], actual_range[1]
        if not latest_date:
            db.execute(
                """UPDATE forecast_daily_predictions
                   SET actual_leads=NULL, error=NULL, absolute_error=NULL, squared_error=NULL,
                       interval_hit=NULL, realized_at=NULL"""
            )
            return {"realized": 0, "latest_actual_date": None}
        # Read-only: `p.actual_leads` is the currently-stored value, kept alongside the freshly
        # joined count so the write below can skip rows that already agree.
        rows = db.execute(
            """SELECT p.id, p.predicted_leads, p.lower_estimate, p.upper_estimate,
                      p.actual_leads AS stored_actual,
                      COALESCE(a.lead_count, 0) actual_leads
               FROM forecast_daily_predictions p
               LEFT JOIN daily_ad_set_aggregates a
                 ON a.utm_ad_set_id=p.utm_ad_set_id
                AND a.aggregate_date=p.forecast_date
               WHERE date(p.forecast_date) BETWEEN date(?) AND date(?)""",
            (earliest_date, latest_date),
        ).fetchall()
        # Whether anything outside the actuals window still carries a realization to clear.
        # Probed here, as a read, so the clearing UPDATE below can be skipped entirely in the
        # normal case -- `date()` on every row makes it a full scan that costs ~80ms of write
        # lock even when it matches zero rows.
        stale_outside = db.execute(
            """SELECT EXISTS(SELECT 1 FROM forecast_daily_predictions
                             WHERE actual_leads IS NOT NULL
                               AND (date(forecast_date) < date(?)
                                    OR date(forecast_date) > date(?)))""",
            (earliest_date, latest_date),
        ).fetchone()[0]

    updates = []
    for row in rows:
        predicted = float(row["predicted_leads"])
        actual = float(row["actual_leads"])
        stored = row["stored_actual"]
        # Every other written column is a pure function of (predicted, bounds, actual), and
        # the first three are immutable once a forecast is stored -- so an unchanged actual
        # means an unchanged row. `realized_at` is informational (CSV export only, never read
        # by any logic or the UI), so leaving it at the run that established the value is
        # fine, and arguably truer than restamping it on every retrain.
        if stored is not None and float(stored) == actual:
            continue
        error = predicted - actual
        updates.append((
            actual, error, abs(error), error * error,
            int(float(row["lower_estimate"]) <= actual <= float(row["upper_estimate"])),
            realized_at, row["id"],
        ))

    if not stale_outside and not updates:
        # Nothing to write at all -- don't take the write lock just to prove it.
        return {"realized": len(rows), "earliest_actual_date": earliest_date,
                "latest_actual_date": latest_date}

    with connect() as db:
        if stale_outside:
            db.execute(
                """UPDATE forecast_daily_predictions
                   SET actual_leads=NULL, error=NULL, absolute_error=NULL, squared_error=NULL,
                       interval_hit=NULL, realized_at=NULL
                   WHERE date(forecast_date) < date(?) OR date(forecast_date) > date(?)""",
                (earliest_date, latest_date),
            )
        if updates:
            db.executemany(
                """UPDATE forecast_daily_predictions
                   SET actual_leads=?, error=?, absolute_error=?, squared_error=?,
                       interval_hit=?, realized_at=?
                   WHERE id=?""",
                updates,
            )
    return {"realized": len(rows), "earliest_actual_date": earliest_date,
            "latest_actual_date": latest_date}


def get_forecast_realizations(ad_set_id: str | None = None, limit: int = 250) -> dict:
    refresh_forecast_realizations()
    where = ["p.actual_leads IS NOT NULL"]
    params: list[object] = []
    if ad_set_id:
        where.append("p.utm_ad_set_id=?")
        params.append(str(ad_set_id))
    where_sql = " AND ".join(where)
    with connect() as db:
        all_rows = db.execute(
            f"""SELECT p.*, r.completed_at training_completed_at
                FROM forecast_daily_predictions p
                JOIN model_training_runs r ON r.id=p.training_run_id
                WHERE {where_sql}
                ORDER BY date(p.forecast_date), p.generated_at""",
            params,
        ).fetchall()
    ledger_rows = [dict(row) for row in all_rows]
    first_by_date: dict[tuple[str, str], dict] = {}
    comparison_by_date: dict[tuple[str, str], dict] = {}
    for row in ledger_rows:
        key = (str(row["utm_ad_set_id"]), str(row["forecast_date"]))
        if key not in first_by_date:
            first_by_date[key] = row
        # all_rows is oldest-to-newest, so overwriting keeps the final forecast
        # snapshot recorded before the actual was realized. This is the canonical
        # production comparison and prevents reruns from double-counting a date.
        comparison_by_date[key] = row
    first_forecast_rows = sorted(
        first_by_date.values(), key=lambda row: (row["forecast_date"], row["utm_ad_set_id"])
    )
    comparison_forecast_rows = sorted(
        comparison_by_date.values(), key=lambda row: (row["forecast_date"], row["utm_ad_set_id"])
    )
    rows = comparison_forecast_rows
    abs_errors = [float(row["absolute_error"]) for row in rows if row["absolute_error"] is not None]
    squared_errors = [float(row["squared_error"]) for row in rows if row["squared_error"] is not None]
    errors = [float(row["error"]) for row in rows if row["error"] is not None]
    actual_total = sum(float(row["actual_leads"] or 0.0) for row in rows)
    hits = [int(row["interval_hit"]) for row in rows if row["interval_hit"] is not None]
    def segmented_metrics(segment: list[dict]) -> tuple[float | None, float | None]:
        if not segment:
            return None, None
        segment_errors = [float(row["error"]) for row in segment]
        denominator = max(sum(abs(float(row["actual_leads"] or 0.0)) for row in segment), 1.0)
        return (sum(abs(error) for error in segment_errors) / denominator,
                float(np.mean(segment_errors)))

    weekdays = [row for row in rows if pd.Timestamp(row["forecast_date"]).weekday() < 5]
    weekends = [row for row in rows if pd.Timestamp(row["forecast_date"]).weekday() >= 5]
    weekday_wape, weekday_bias = segmented_metrics(weekdays)
    weekend_wape, weekend_bias = segmented_metrics(weekends)
    production_mae = float(np.mean(abs_errors)) if abs_errors else None
    production_rmse = float(math.sqrt(np.mean(squared_errors))) if squared_errors else None
    production_bias = float(np.mean(errors)) if errors else None
    production_wape = float(sum(abs_errors) / max(actual_total, 1.0)) if abs_errors else None
    interval_coverage = float(np.mean(hits)) if hits else None
    summary = {
        "realized_predictions": len(rows),
        "ad_set_count": len({row["utm_ad_set_id"] for row in rows}),
        "latest_realized_date": max((row["forecast_date"] for row in rows), default=None),
        "production_mae": production_mae,
        "production_rmse": production_rmse,
        "production_wape": production_wape,
        "production_bias": production_bias,
        "interval_coverage": interval_coverage,
        "weekday_wape": weekday_wape,
        "weekend_wape": weekend_wape,
        "weekday_bias": weekday_bias,
        "weekend_bias": weekend_bias,
        # Additive aliases keep existing internal consumers backward compatible.
        "mae": production_mae, "rmse": production_rmse,
        "wape": production_wape, "bias": production_bias,
    }
    visible_rows = sorted(
        rows,
        key=lambda row: (row["forecast_date"], row["generated_at"], row["utm_ad_set_id"]),
        reverse=True,
    )[:max(1, int(limit))]
    return {"summary": summary, "rows": visible_rows,
            "first_forecast_rows": first_forecast_rows,
            "comparison_forecast_rows": comparison_forecast_rows}


def _production_calibration(ad_set_id: str, limit: int = 28) -> dict:
    """Return conservative monitoring adjustments from recent realized forecasts."""
    with connect() as db:
        rows = db.execute(
            """WITH dated AS (
                   SELECT actual_leads, error, absolute_error, interval_hit,
                          forecast_date, generated_at,
                          ROW_NUMBER() OVER (
                              PARTITION BY utm_ad_set_id, forecast_date
                              ORDER BY datetime(generated_at) DESC, id DESC
                          ) AS date_rank
                     FROM forecast_daily_predictions
                    WHERE utm_ad_set_id=? AND actual_leads IS NOT NULL
               )
               SELECT actual_leads, error, absolute_error, interval_hit
                 FROM dated
                WHERE date_rank=1
                ORDER BY date(forecast_date) DESC, datetime(generated_at) DESC
                LIMIT ?""",
            (str(ad_set_id), int(limit)),
        ).fetchall()
    if len(rows) < 7:
        return {"eligible": False, "sample_size": len(rows), "prediction_multiplier": 1.0,
                "interval_multiplier": 1.0, "confidence_multiplier": 1.0,
                "bias": None, "wape": None, "coverage": None, "bias_adjustment": 0.0}
    errors = np.asarray([float(row["error"]) for row in rows], dtype=float)
    actuals = np.asarray([float(row["actual_leads"] or 0.0) for row in rows], dtype=float)
    bias = float(np.mean(errors))
    wape = float(np.sum(np.abs(errors)) / max(float(np.sum(np.abs(actuals))), 1.0))
    coverage = float(np.mean([int(row["interval_hit"]) for row in rows]))
    actual_total = float(np.sum(actuals))
    forecast_total = float(np.sum(actuals + errors))
    # error is predicted - actual. Negative total error means underprediction.
    bias_multiplier = 1.0 + float(np.clip((actual_total - forecast_total) / max(actual_total, 1.0), -0.20, 0.20))
    prediction_multiplier = float(np.clip(bias_multiplier, 0.80, 1.20))
    bias_adjustment = float(np.clip(-bias, -0.30 * (float(np.mean(actuals)) + 1.0), 0.30 * (float(np.mean(actuals)) + 1.0)))
    interval_multiplier = min(1.50, 1.0 + max(0.0, 0.80 - coverage))
    confidence_multiplier = max(0.65, 1.0 - min(0.35, max(0.0, wape - 0.30) * 0.25))
    return {"eligible": True, "sample_size": len(rows),
            "prediction_multiplier": prediction_multiplier,
            "interval_multiplier": interval_multiplier,
            "confidence_multiplier": confidence_multiplier,
            "bias": bias, "wape": wape, "coverage": coverage,
            "bias_adjustment": bias_adjustment}


def _apply_production_calibration(predictions: list[float], calibration: dict, model: str | None) -> list[float]:
    if not calibration.get("eligible"):
        return [max(0.0, float(value)) for value in predictions]
    multiplier = float(calibration.get("prediction_multiplier", 1.0))
    additive = (
        0.0 if _is_spend_adjusted_model(model) or model == ENSEMBLE_MODEL_NAME
        else float(calibration.get("bias_adjustment") or 0.0)
    )
    results = []
    for value in predictions:
        scaled = float(value) * multiplier
        adjusted = scaled + additive
        # The additive term is a flat daily correction learned from historical bias.
        # On a low-volume ad set the raw prediction can be smaller than that flat
        # correction, so a modest over-forecast bias would otherwise zero out an
        # already-thin but legitimate forecast for the whole horizon. Cap how much
        # of the scaled prediction the additive term is allowed to remove.
        if additive < 0 and scaled > 0:
            adjusted = max(adjusted, scaled * CALIBRATION_MIN_RETENTION)
        results.append(max(0.0, adjusted))
    return results


def _production_adjustment_label(calibration: dict, model: str | None) -> str:
    multiplier = (float(calibration.get("prediction_multiplier", 1.0)) - 1.0) * 100
    additive = (
        0.0 if _is_spend_adjusted_model(model) or model == ENSEMBLE_MODEL_NAME
        else float(calibration.get("bias_adjustment") or 0.0)
    )
    if abs(additive) >= 0.05:
        return f"Production calibration {multiplier:+.0f}% and {additive:+.1f}/day"
    return f"Production calibration {multiplier:+.0f}%"


def get_model_diagnostics(limit: int = 10) -> dict:
    """Explain which ad sets are helping or hurting forecast quality right now."""
    refresh_forecast_realizations()
    limit = max(3, min(25, int(limit)))
    with connect() as db:
        run = db.execute(
            "SELECT * FROM model_training_runs WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not run:
            return {
                "run": None,
                "summary": {},
                "worst_wape": [],
                "most_underpredicted": [],
                "most_overpredicted": [],
                "flat_forecasts": [],
                "best": [],
            }
        selected_rows = db.execute(
            """SELECT m.*,
                      f.confidence_score,
                      f.explanation,
                      COALESCE(
                        (SELECT p.campaign_name
                           FROM daily_ad_performance p
                          WHERE p.ad_set_id=m.utm_ad_set_id
                            AND TRIM(COALESCE(p.campaign_name, '')) <> ''
                          ORDER BY date(p.day) DESC
                          LIMIT 1),
                        (SELECT l.utm_campaign
                           FROM lead_events l
                          WHERE l.utm_ad_set_id=m.utm_ad_set_id
                            AND TRIM(COALESCE(l.utm_campaign, '')) <> ''
                          GROUP BY l.utm_campaign
                          ORDER BY COUNT(*) DESC
                          LIMIT 1),
                        ''
                      ) AS campaign_name,
                      CASE WHEN EXISTS (
                        SELECT 1 FROM daily_ad_performance p
                         WHERE p.ad_set_id=m.utm_ad_set_id
                      ) THEN 1 ELSE 0 END AS spend_active,
                      (SELECT COALESCE(SUM(amount_spent_usd), 0)
                         FROM daily_ad_performance p
                        WHERE p.ad_set_id=m.utm_ad_set_id) AS imported_spend
               FROM model_backtest_metrics m
               JOIN forecasts f
                 ON f.training_run_id=m.training_run_id
                AND f.utm_ad_set_id=m.utm_ad_set_id
                AND f.horizon_days=m.horizon_days
                AND f.model_used=m.model_used
              WHERE m.training_run_id=?
                AND m.horizon_days=14
              ORDER BY m.selection_score, m.utm_ad_set_id""",
            (run["id"],),
        ).fetchall()
    realized_rows = get_forecast_realizations(limit=10000).get("comparison_forecast_rows", [])
    grouped_realized: dict[str, list[dict]] = {}
    for row in realized_rows:
        grouped_realized.setdefault(str(row["utm_ad_set_id"]), []).append(row)

    production_by_ad_set = {}
    for ad_set_id, rows in grouped_realized.items():
        abs_errors = [float(row["absolute_error"]) for row in rows if row.get("absolute_error") is not None]
        errors = [float(row["error"]) for row in rows if row.get("error") is not None]
        actual_total = sum(float(row.get("actual_leads") or 0.0) for row in rows)
        hits = [int(row["interval_hit"]) for row in rows if row.get("interval_hit") is not None]
        production_by_ad_set[ad_set_id] = {
            "utm_ad_set_id": ad_set_id,
            "realized_days": len(rows),
            "production_mae": float(np.mean(abs_errors)) if abs_errors else None,
            "production_wape": float(sum(abs_errors) / max(actual_total, 1.0)) if abs_errors else None,
            "production_bias": float(np.mean(errors)) if errors else None,
            "production_coverage": float(np.mean(hits)) if hits else None,
            "latest_realized_date": max((row.get("forecast_date") for row in rows), default=None),
        }

    def clean_number(value: object) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    diagnostics = []
    for row in selected_rows:
        item = dict(row)
        ad_set_id = str(item["utm_ad_set_id"])
        production = production_by_ad_set.get(ad_set_id, {})
        calibration = _production_calibration(ad_set_id)
        merged = {
            "ad_set_id": ad_set_id,
            "campaign_name": item.get("campaign_name") or "Unknown campaign",
            "model_used": item.get("model_used"),
            "selection_score": clean_number(item.get("selection_score")),
            "mae": clean_number(item.get("mae")),
            "rmse": clean_number(item.get("rmse")),
            "wape": clean_number(item.get("wape")),
            "bias": clean_number(item.get("bias")),
            "interval_coverage": clean_number(item.get("interval_coverage")),
            "forecast_variance_ratio": clean_number(item.get("forecast_variance_ratio")),
            "flatness_penalty": clean_number(item.get("flatness_penalty")) or 0.0,
            "backtest_windows": int(item.get("backtest_windows") or 0),
            "spend_active": bool(item.get("spend_active")),
            "imported_spend": clean_number(item.get("imported_spend")) or 0.0,
            "confidence_score": int(item.get("confidence_score") or 0),
            "production_realized_days": int(production.get("realized_days") or 0),
            "production_mae": clean_number(production.get("production_mae")),
            "production_wape": clean_number(production.get("production_wape")),
            "production_bias": clean_number(production.get("production_bias")),
            "production_coverage": clean_number(production.get("production_coverage")),
            "latest_realized_date": production.get("latest_realized_date"),
            "bias_correction_multiplier": clean_number(calibration.get("prediction_multiplier")) or 1.0,
            "bias_adjustment": clean_number(calibration.get("bias_adjustment")) or 0.0,
            "calibration_sample_size": int(calibration.get("sample_size") or 0),
        }
        diagnostics.append(merged)

    with_production = [item for item in diagnostics if item["production_realized_days"] > 0]
    summary = {
        "ad_sets_evaluated": len(diagnostics),
        "spend_linked_ad_sets": sum(1 for item in diagnostics if item["spend_active"]),
        "realized_ad_sets": len(with_production),
        "flat_risk_ad_sets": sum(1 for item in diagnostics if float(item["flatness_penalty"] or 0) > 0),
        "median_wape": float(median([item["wape"] for item in diagnostics if item["wape"] is not None])) if any(item["wape"] is not None for item in diagnostics) else None,
        "median_production_wape": float(median([item["production_wape"] for item in with_production if item["production_wape"] is not None])) if any(item["production_wape"] is not None for item in with_production) else None,
    }

    def top_by(items: list[dict], key: str, reverse: bool = True) -> list[dict]:
        filtered = [item for item in items if item.get(key) is not None]
        return sorted(filtered, key=lambda item: float(item[key]), reverse=reverse)[:limit]

    return {
        "run": dict(run),
        "summary": summary,
        "worst_wape": top_by(with_production or diagnostics, "production_wape" if with_production else "wape", True),
        "most_underpredicted": top_by(
            [item for item in (with_production or diagnostics)
             if (item.get("production_bias") if with_production else item.get("bias")) is not None],
            "production_bias" if with_production else "bias",
            False,
        ),
        "most_overpredicted": top_by(
            [item for item in (with_production or diagnostics)
             if (item.get("production_bias") if with_production else item.get("bias")) is not None],
            "production_bias" if with_production else "bias",
            True,
        ),
        "flat_forecasts": top_by(diagnostics, "flatness_penalty", True),
        "best": top_by(with_production or diagnostics, "production_wape" if with_production else "wape", False),
    }


def _weekday_prediction(history: np.ndarray, dates: pd.DatetimeIndex, target: pd.Timestamp) -> float:
    same = history[np.array([d.weekday() == target.weekday() for d in dates])]
    if len(same):
        return float(np.mean(same[-6:]))
    return float(np.mean(history[-7:])) if len(history) else 0.0


def _cap_forecast_values(predictions: Iterable[float], history: np.ndarray) -> list[float]:
    clean_history = np.nan_to_num(np.asarray(history, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    clean_history = np.clip(clean_history, 0.0, None)
    recent = clean_history[-14:] if len(clean_history) else clean_history
    recent_mean = float(np.mean(recent)) if len(recent) else 0.0
    historical_max = float(np.max(clean_history)) if len(clean_history) else 0.0
    cap = max(25.0, historical_max * 4.0 + 10.0, recent_mean * 8.0 + 10.0)
    return [float(np.clip(float(value), 0.0, cap)) for value in predictions]


def _scale_daily_shape(values: list[float], target_total: float) -> list[float]:
    clean = [max(0.0, float(value)) for value in values]
    current_total = sum(clean)
    if current_total <= 0:
        daily = max(0.0, target_total / max(1, len(clean)))
        return [daily] * len(clean)
    scale = target_total / current_total
    return [value * scale for value in clean]


def _source_weekday_profile(values: np.ndarray | None, dates: pd.DatetimeIndex) -> dict:
    """Return learned weekday averages/factors without inventing missing weekdays."""
    if values is None or len(values) != len(dates) or not len(values):
        return {"averages": [None] * 7, "factors": [None] * 7, "sample_days": [0] * 7}
    clean = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    overall_average = float(np.mean(clean))
    averages: list[float | None] = []
    factors: list[float | None] = []
    sample_days: list[int] = []
    weekdays = np.asarray([date.weekday() for date in dates])
    for weekday in range(7):
        weekday_values = clean[weekdays == weekday]
        sample_days.append(int(len(weekday_values)))
        if not len(weekday_values):
            averages.append(None); factors.append(None)
            continue
        average = float(np.mean(weekday_values))
        averages.append(average)
        factors.append(average / overall_average if overall_average > 1e-12 else 1.0)
    return {"averages": averages, "factors": factors, "sample_days": sample_days}


def calculate_weekday_profile(
    values: Iterable[float], dates: Iterable[pd.Timestamp],
    campaign_values: Iterable[float] | None = None,
    portfolio_values: Iterable[float] | None = None, sparse: bool = False,
) -> dict:
    """Build a normalized hierarchical Mon-Sun profile for an ad set."""
    date_index = pd.DatetimeIndex(dates)
    ad_set = _source_weekday_profile(np.asarray(list(values), dtype=float), date_index)
    campaign = _source_weekday_profile(
        None if campaign_values is None else np.asarray(list(campaign_values), dtype=float), date_index,
    )
    portfolio = _source_weekday_profile(
        None if portfolio_values is None else np.asarray(list(portfolio_values), dtype=float), date_index,
    )
    weights = (0.20, 0.45, 0.35) if sparse else (0.60, 0.25, 0.15)
    smoothed: list[float] = []
    for weekday in range(7):
        candidates = [ad_set["factors"][weekday], campaign["factors"][weekday], portfolio["factors"][weekday]]
        available = [(weight, float(factor)) for weight, factor in zip(weights, candidates)
                     if factor is not None and math.isfinite(float(factor))]
        weight_total = sum(weight for weight, _ in available)
        smoothed.append(sum(weight * factor for weight, factor in available) / weight_total if weight_total else 1.0)
    # Keep the profile neutral across a complete week. Forecast shaping then preserves
    # the selected model's exact horizon total for both 7- and 14-day windows.
    profile_mean = float(np.mean(smoothed)) if smoothed else 1.0
    smoothed = [factor / profile_mean if profile_mean > 1e-12 else 1.0 for factor in smoothed]
    rows = [{
        "weekday_index": weekday,
        "weekday_name": WEEKDAY_NAMES[weekday],
        "ad_set_average": ad_set["averages"][weekday],
        "campaign_average": campaign["averages"][weekday],
        "portfolio_average": portfolio["averages"][weekday],
        "smoothed_factor": smoothed[weekday],
        "sample_days": ad_set["sample_days"][weekday],
    } for weekday in range(7)]
    weekday_factor = float(np.mean(smoothed[:5]))
    weekend_factor = float(np.mean(smoothed[5:]))
    return {
        "weekdays": rows,
        "factors": smoothed,
        "seasonality_strength": float(max(smoothed) - min(smoothed)),
        "weekday_factor": weekday_factor,
        "weekend_factor": weekend_factor,
        "sparse": bool(sparse),
        "smoothing_note": (
            "Sparse history: 20% ad set, 45% campaign, 35% portfolio; unavailable weekdays fall back safely."
            if sparse else
            "Smoothed from 60% ad set, 25% campaign, and 15% portfolio weekday patterns."
        ),
    }


def _shape_with_weekday_factors(base_predictions: Iterable[float], target_dates: pd.DatetimeIndex,
                                profile: dict) -> list[float]:
    base = [max(0.0, float(value)) for value in base_predictions]
    factors = profile.get("factors") or [1.0] * 7
    shaped = [value * float(factors[date.weekday()]) for value, date in zip(base, target_dates)]
    return _scale_daily_shape(shaped, sum(base))


def _apply_shape_profile(predictions: Iterable[float], values: np.ndarray, strength: float) -> list[float]:
    """Blend the recent week's day-to-day profile into a forecast, preserving its total.

    A conditional-mean forecast is close to flat, because for this data the day-to-day
    movement is mostly unpredictable and the error-minimising answer really is a level line.
    That is correct and it is also unreadable next to volatile actuals, so the delivered
    forecast carries the amplitude of the last observed week at FORECAST_SHAPE_STRENGTH.

    The horizon total is untouched - the blend only moves leads between days inside the
    window - so the 7- and 14-day figures stay exactly what the selected model predicted and
    only daily placement pays for the shape. Tiling starts at values[-7], which shares a
    weekday with the first forecast day, so the profile lands in weekday phase.
    """
    path = np.clip(np.asarray([float(value) for value in predictions], dtype=float), 0.0, None)
    total = float(path.sum())
    if strength <= 0.0 or total <= 0.0 or len(path) < 2 or len(values) < 7:
        return path.tolist()
    last_week = np.clip(np.asarray(values[-7:], dtype=float), 0.0, None)
    if float(last_week.sum()) <= 0.0:
        return path.tolist()
    tiled = np.asarray([last_week[index % 7] for index in range(len(path))], dtype=float)
    tiled *= total / float(tiled.sum())
    shaped = strength * tiled + (1.0 - strength) * path
    shaped_total = float(shaped.sum())
    return (shaped * (total / shaped_total)).tolist() if shaped_total > 0 else path.tolist()


def _recent_shape_forecast(values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, target_daily: float) -> list[float]:
    recent = values[-14:] if len(values) >= 14 else values
    recent_mean = float(np.mean(recent)) if len(recent) else 0.0
    shaped = []
    for step in range(1, horizon + 1):
        target = dates[-1] + pd.Timedelta(days=step)
        same_weekday = _weekday_prediction(values, dates, target)
        lag7 = float(values[-7 + ((step - 1) % 7)]) if len(values) >= 7 else recent_mean
        lag14 = float(values[-14 + ((step - 1) % 14)]) if len(values) >= 14 else lag7
        shaped.append(0.50 * same_weekday + 0.30 * lag7 + 0.20 * lag14)
    return _scale_daily_shape(shaped, target_daily * horizon)


def _poisson_forecast(values: list[float], dates: list[pd.Timestamp], horizon: int) -> list[float]:
    if len(values) < 15:
        return _recent_shape_forecast(np.asarray(values, dtype=float), pd.DatetimeIndex(dates), horizon, float(np.mean(values[-7:])))
    X, y = [], []
    for i in range(14, len(values)):
        dow = [1.0 if dates[i].weekday() == j else 0.0 for j in range(7)]
        recent3 = np.mean(values[max(0, i - 3):i])
        recent7 = np.mean(values[max(0, i - 7):i])
        recent14 = np.mean(values[max(0, i - 14):i])
        trend = recent3 - recent14
        X.append(dow + [recent3, recent7, recent14, trend, i / max(1, len(values))])
        y.append(values[i])
    # Dependency-light log-link count regression. Ridge regularization keeps
    # short ad-set series stable while expm1 guarantees non-negative counts.
    design = np.c_[np.ones(len(X)), np.asarray(X)]
    target = np.log1p(np.asarray(y, dtype=float))
    ridge = np.eye(design.shape[1]) * 0.4
    ridge[0, 0] = 0
    coefficients = np.linalg.pinv(design.T @ design + ridge) @ design.T @ target
    vals = list(map(float, values))
    output = []
    for step in range(horizon):
        date = dates[-1] + pd.Timedelta(days=step + 1)
        dow = [1.0 if date.weekday() == j else 0.0 for j in range(7)]
        r3, r7, r14 = np.mean(vals[-3:]), np.mean(vals[-7:]), np.mean(vals[-14:])
        row = dow + [r3, r7, r14, r3 - r14, (len(values) + step) / max(1, len(values))]
        cap = max(25.0, max(vals) * 4.0 + 10.0, float(np.mean(vals[-14:])) * 8.0 + 10.0)
        linear_prediction = float(np.clip(np.dot(np.r_[1.0, row], coefficients), -10.0, math.log1p(cap)))
        pred = max(0.0, min(cap, float(np.expm1(linear_prediction))))
        vals.append(pred)
        output.append(pred)
    return output


def _ols_adjusted_r2(y: np.ndarray, predicted: np.ndarray, feature_count: int) -> float:
    n = len(y)
    if n <= feature_count + 1:
        return float("-inf")
    total = float(np.sum((y - np.mean(y)) ** 2))
    if total <= 1e-12:
        return 0.0
    residual = float(np.sum((y - predicted) ** 2))
    r2 = 1.0 - residual / total
    return 1.0 - (1.0 - r2) * (n - 1) / max(1, n - feature_count - 1)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    max_iterations = 200
    epsilon = 3e-12
    fpmin = 1e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _beta_continued_fraction(a, b, x) / a
    return 1.0 - bt * _beta_continued_fraction(b, a, 1.0 - x) / b


def _student_t_two_tailed_p_value(t_value: float, df: int) -> float | None:
    if df <= 0:
        return None
    if math.isinf(t_value):
        return 0.0
    if not math.isfinite(t_value):
        return None
    x = df / (df + float(t_value) ** 2)
    return float(np.clip(_regularized_incomplete_beta(df / 2.0, 0.5, x), 0.0, 1.0))


def _f_survival_p_value(f_value: float, df_model: int, df_resid: int) -> float | None:
    if df_model <= 0 or df_resid <= 0 or not math.isfinite(f_value):
        return None
    x = (df_model * f_value) / (df_model * f_value + df_resid)
    cdf = _regularized_incomplete_beta(df_model / 2.0, df_resid / 2.0, x)
    return float(np.clip(1.0 - cdf, 0.0, 1.0))


def _feature_label(feature: str) -> str:
    labels = {
        "spend": "spent",
        "conversations": "conversations",
        "platform_leads": "platform leads",
        "link_clicks": "link clicks",
        "impressions": "impressions",
        "campaign_leads": "campaign leads",
        "portfolio_leads": "portfolio leads",
        "last3_leads": "last 3 avg",
        "last7_leads": "last 7 avg",
        "last14_leads": "last 14 avg",
        "trend_3_vs_14": "recent trend",
        "time_index": "time trend",
        "is_weekend": "weekend",
        "frequency": "frequency",
        "days_since_adset_started": "days_since_ad_set_started",
        "ad_set_change_recency": "ad set change recency",
        "ad_change_recency": "ad change recency",
        "holiday_during_holiday": "during holiday",
        "holiday_0_14_days": "holiday <15d",
        "holiday_15_30_days": "holiday 15-30d",
        "holiday_31_60_days": "holiday 31-60d",
        "weekday_0": "Monday",
        "weekday_1": "Tuesday",
        "weekday_2": "Wednesday",
        "weekday_3": "Thursday",
        "weekday_4": "Friday",
        "weekday_5": "Saturday",
        "weekday_6": "Sunday",
    }
    return labels.get(feature, feature.replace("_", " "))


def _ols_feature_frame(
    values: np.ndarray, dates: pd.DatetimeIndex, context: dict | None, horizon: int,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    context = context or {}
    clean_values = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)

    def context_array(name: str) -> np.ndarray:
        raw = np.clip(
            np.nan_to_num(np.asarray(context.get(name, np.zeros(len(clean_values))), dtype=float),
                          nan=0.0, posinf=0.0, neginf=0.0),
            0.0, None,
        )
        if len(raw) == len(clean_values):
            return raw
        if not len(clean_values):
            return np.asarray([], dtype=float)
        return np.zeros(len(clean_values), dtype=float) if not len(raw) else np.resize(raw, len(clean_values))

    spend = _lagged_signal(context_array("spend_values"), _spend_lag_for_model(context.get("model_used"), context))
    conversations = _lagged_signal(context_array("conversation_values"), _spend_lag_for_model(context.get("model_used"), context))
    platform_leads = context_array("platform_leads_values")
    link_clicks = context_array("link_click_values")
    impressions = context_array("impression_values")
    campaign_values = context_array("campaign_values")
    portfolio_values = context_array("overall_values")
    frequency = context_array("frequency_values")
    days_since_start = context_array("days_since_start_values")
    ad_set_change_recency = context_array("ad_set_change_recency_values")
    ad_change_recency = context_array("ad_change_recency_values")

    def recent_average(series: np.ndarray, window: int = 7) -> float:
        return _safe_recent_average(series, window)

    future_spend = context.get("future_spend_daily")
    try:
        future_spend_value = float(future_spend) if future_spend is not None else recent_average(spend)
    except (TypeError, ValueError):
        future_spend_value = recent_average(spend)
    if not math.isfinite(future_spend_value):
        future_spend_value = recent_average(spend)

    rows: list[dict[str, float]] = []
    for index, date in enumerate(dates):
        prior = clean_values[:index]
        last3 = float(np.mean(prior[-3:])) if len(prior) else 0.0
        last7 = float(np.mean(prior[-7:])) if len(prior) else last3
        last14 = float(np.mean(prior[-14:])) if len(prior) else last7
        row = {
            "spend": float(spend[index]) if index < len(spend) else 0.0,
            "conversations": float(conversations[index]) if index < len(conversations) else 0.0,
            "platform_leads": float(platform_leads[index]) if index < len(platform_leads) else 0.0,
            "link_clicks": float(link_clicks[index]) if index < len(link_clicks) else 0.0,
            "impressions": float(impressions[index]) if index < len(impressions) else 0.0,
            "campaign_leads": float(campaign_values[index]) if index < len(campaign_values) else 0.0,
            "portfolio_leads": float(portfolio_values[index]) if index < len(portfolio_values) else 0.0,
            "last3_leads": last3,
            "last7_leads": last7,
            "last14_leads": last14,
            "trend_3_vs_14": last3 - last14,
            "time_index": index / max(1, len(dates) - 1),
            "is_weekend": 1.0 if date.weekday() >= 5 else 0.0,
            "frequency": float(frequency[index]) if index < len(frequency) else 0.0,
            "days_since_adset_started": float(days_since_start[index]) if index < len(days_since_start) else 0.0,
            "ad_set_change_recency": float(ad_set_change_recency[index]) if index < len(ad_set_change_recency) else 0.0,
            "ad_change_recency": float(ad_change_recency[index]) if index < len(ad_change_recency) else 0.0,
        }
        row.update(_holiday_proximity_features(date))
        for weekday in range(7):
            row[f"weekday_{weekday}"] = 1.0 if date.weekday() == weekday else 0.0
        rows.append(row)

    future_rows: list[dict[str, float]] = []
    simulated = clean_values.astype(float).tolist()
    for step in range(1, horizon + 1):
        date = dates[-1] + pd.Timedelta(days=step)
        last3 = float(np.mean(simulated[-3:])) if simulated else 0.0
        last7 = float(np.mean(simulated[-7:])) if simulated else last3
        last14 = float(np.mean(simulated[-14:])) if simulated else last7
        row = {
            "spend": max(0.0, future_spend_value),
            "conversations": recent_average(conversations),
            "platform_leads": recent_average(platform_leads),
            "link_clicks": recent_average(link_clicks),
            "impressions": recent_average(impressions),
            "campaign_leads": recent_average(campaign_values),
            "portfolio_leads": recent_average(portfolio_values),
            "last3_leads": last3,
            "last7_leads": last7,
            "last14_leads": last14,
            "trend_3_vs_14": last3 - last14,
            "time_index": (len(dates) + step - 1) / max(1, len(dates) - 1),
            "is_weekend": 1.0 if date.weekday() >= 5 else 0.0,
            "frequency": recent_average(frequency),
            # these three advance deterministically one day at a time unless a new change
            # lands, which by definition is not knowable ahead of the forecast. There is no
            # longer a change TYPE to carry forward alongside them (removed 2026-08-11).
            "days_since_adset_started": (float(days_since_start[-1]) + step) if len(days_since_start) else 0.0,
            "ad_set_change_recency": (float(ad_set_change_recency[-1]) + step) if len(ad_set_change_recency) else 0.0,
            "ad_change_recency": (float(ad_change_recency[-1]) + step) if len(ad_change_recency) else 0.0,
        }
        row.update(_holiday_proximity_features(date))
        for weekday in range(7):
            row[f"weekday_{weekday}"] = 1.0 if date.weekday() == weekday else 0.0
        future_rows.append(row)
        simulated.append(last7)
    return rows, future_rows


def _fit_ols_predictions(
    values: np.ndarray, feature_rows: list[dict[str, float]], future_rows: list[dict[str, float]],
    features: list[str], *, ridge_penalty: float = 0.0,
) -> tuple[list[float], float]:
    """Fit and project. `ridge_penalty` shrinks the standardised slopes toward zero.

    Forecasting only. The diagnostic summary in _fit_ols_summary stays unpenalised, because
    a ridge fit has no honest standard errors or p-values to report.
    """
    y = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    if len(y) < max(12, len(features) + 6):
        raise ValueError("Not enough observations for OLS regression.")
    design = np.asarray([[row.get(feature, 0.0) for feature in features] for row in feature_rows], dtype=float)
    future_design = np.asarray([[row.get(feature, 0.0) for feature in features] for row in future_rows], dtype=float)
    if not np.any(np.std(design, axis=0) > 1e-9):
        raise ValueError("OLS regressors have no usable variation.")
    means = design.mean(axis=0)
    stds = design.std(axis=0)
    stds[stds <= 1e-9] = 1.0
    scaled = (design - means) / stds
    future_scaled = (future_design - means) / stds
    fitted_design = np.c_[np.ones(len(scaled)), scaled]
    penalty = np.eye(fitted_design.shape[1]) * max(0.0, float(ridge_penalty))
    penalty[0, 0] = 0.0  # never shrink the intercept - that would bias the level itself
    coefficients = np.linalg.pinv(fitted_design.T @ fitted_design + penalty) @ fitted_design.T @ y
    fitted = fitted_design @ coefficients
    adjusted_r2 = _ols_adjusted_r2(y, fitted, len(features))
    predictions = np.c_[np.ones(len(future_scaled)), future_scaled] @ coefficients
    return predictions.tolist(), adjusted_r2


def _fit_ols_summary(values: np.ndarray, feature_rows: list[dict[str, float]], features: list[str], model_name: str) -> dict | None:
    y = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    if len(y) < max(12, len(features) + 6):
        return None
    design = np.asarray([[row.get(feature, 0.0) for feature in features] for row in feature_rows], dtype=float)
    if design.shape[0] != len(y) or design.shape[1] != len(features):
        return None
    variable_mask = np.std(design, axis=0) > 1e-9
    kept_features = [feature for feature, keep in zip(features, variable_mask) if keep]
    if not kept_features:
        return None
    design = design[:, variable_mask]
    x = np.c_[np.ones(len(design)), design]
    rank = int(np.linalg.matrix_rank(x))
    df_model = max(0, rank - 1)
    df_resid = len(y) - rank
    if df_model <= 0 or df_resid <= 0:
        return None
    xtx_inv = np.linalg.pinv(x.T @ x)
    coefficients = xtx_inv @ x.T @ y
    fitted = x @ coefficients
    residuals = y - fitted
    sse = float(np.sum(residuals**2))
    mse_resid = sse / max(1, df_resid)
    total = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 0.0 if total <= 1e-12 else 1.0 - sse / total
    adjusted_r2 = _ols_adjusted_r2(y, fitted, df_model)
    rmse = math.sqrt(max(0.0, mse_resid))
    standard_errors = np.sqrt(np.clip(np.diag(xtx_inv) * mse_resid, 0.0, None))
    t_values = [
        float(coef / se) if se > 1e-12 else (0.0 if abs(coef) <= 1e-12 else math.copysign(float("inf"), coef))
        for coef, se in zip(coefficients, standard_errors)
    ]
    p_values = [_student_t_two_tailed_p_value(value, df_resid) for value in t_values]
    ci_multiplier = 1.96
    coefficient_rows = []
    for name, coef, se, t_value, p_value in zip(["Intercept", *kept_features], coefficients, standard_errors, t_values, p_values):
        coefficient_rows.append({
            "term": _feature_label(name) if name != "Intercept" else "Intercept",
            "feature": name,
            "coef": float(coef),
            "std_err": float(se),
            "t": float(t_value) if math.isfinite(t_value) else None,
            "p_value": p_value,
            "ci_low": float(coef - ci_multiplier * se),
            "ci_high": float(coef + ci_multiplier * se),
        })
    if total <= 1e-12:
        f_statistic, f_p_value = None, None
    else:
        explained = max(0.0, total - sse)
        f_statistic = (explained / df_model) / max(mse_resid, 1e-12)
        f_p_value = _f_survival_p_value(f_statistic, df_model, df_resid)
    sigma2_mle = max(sse / max(1, len(y)), 1e-12)
    log_likelihood = -0.5 * len(y) * (math.log(2.0 * math.pi * sigma2_mle) + 1.0)
    parameter_count = len(coefficients)
    # Residual diagnostics for the "Show Detail" expanded view -- Durbin-Watson (serial
    # correlation), skew/kurtosis (via raw central moments, no scipy dependency in this
    # codebase), and the Jarque-Bera normality test, whose statistic is chi-squared with
    # exactly 2 degrees of freedom under the null -- which has the closed form survival
    # function exp(-x/2), so no incomplete-gamma implementation is needed the way
    # _regularized_incomplete_beta was for the t/F tests above. Cond. No. is the design
    # matrix's condition number (large values flag near-collinear regressors, e.g. the
    # weekday block's intentional rank deficiency -- see Vault/Modeling/
    # OLS-Declared-Ten-Variables.md).
    durbin_watson = (
        float(np.sum(np.diff(residuals) ** 2) / sse) if sse > 1e-12 and len(residuals) > 1 else None
    )
    resid_mean = float(np.mean(residuals))
    resid_var = float(np.mean((residuals - resid_mean) ** 2))
    if resid_var > 1e-12:
        skew = float(np.mean((residuals - resid_mean) ** 3) / resid_var ** 1.5)
        kurtosis = float(np.mean((residuals - resid_mean) ** 4) / resid_var ** 2)
    else:
        skew, kurtosis = 0.0, 3.0
    jarque_bera = (len(y) / 6.0) * (skew**2 + ((kurtosis - 3.0) ** 2) / 4.0)
    jarque_bera_p_value = float(np.clip(math.exp(-jarque_bera / 2.0), 0.0, 1.0))
    cond_no = float(np.linalg.cond(x))
    return {
        "dep_variable": "leads",
        "model": model_name,
        "method": "Least Squares",
        "covariance_type": "nonrobust",
        "no_observations": int(len(y)),
        "df_residuals": int(df_resid),
        "df_model": int(df_model),
        "r_squared": float(r2),
        "adjusted_r_squared": float(adjusted_r2),
        "f_statistic": float(f_statistic) if f_statistic is not None else None,
        "f_p_value": f_p_value,
        "log_likelihood": float(log_likelihood),
        "aic": float(2 * parameter_count - 2 * log_likelihood),
        "bic": float(math.log(len(y)) * parameter_count - 2 * log_likelihood),
        "rmse": float(rmse),
        "features": [_feature_label(feature) for feature in kept_features],
        "coefficients": coefficient_rows,
        "durbin_watson": durbin_watson,
        "skew": skew,
        "kurtosis": kurtosis,
        "jarque_bera": float(jarque_bera),
        "jarque_bera_p_value": jarque_bera_p_value,
        "cond_no": cond_no,
    }


# The declared drivers (variables 2-8) as selection GROUPS: one entry per declared variable,
# carrying the encoded feature columns that variable is expressed through. Both the
# all-declared selector and the forward selector read this, so the two can never disagree
# about what the candidate pool is. Kept as a literal here rather than derived from
# DECLARED_VARIABLES (defined further down) because the two lists differ deliberately in one
# place: variable 8's spec also lists `is_weekend`, which is a redundant recode of the seven
# day indicators and has never been a fit candidate.
DECLARED_OLS_GROUPS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (2, "Spent", ("spend",)),
    (3, "Holiday_Proximity", ("holiday_during_holiday", "holiday_0_14_days",
                              "holiday_15_30_days", "holiday_31_60_days")),
    (4, "days_since_ad_set_started", ("days_since_adset_started",)),
    (5, "frequency", ("frequency",)),
    (6, "ad_change_recency", ("ad_change_recency",)),
    (7, "ad_set_change_recency", ("ad_set_change_recency",)),
    # All seven days, no reference day held out (see the note on DECLARED_VARIABLES #8 below).
    # Combined with the intercept this is rank-deficient by construction, which
    # _fit_ols_summary/_fit_ols_predictions already tolerate via np.linalg.pinv's minimum-norm
    # solution, and which the rank-prune below turns into a dropped redundant column.
    (8, "Days of the week", ("weekday_0", "weekday_1", "weekday_2", "weekday_3",
                             "weekday_4", "weekday_5", "weekday_6")),
)


def _select_multivariate_ols_features(values: np.ndarray, feature_rows: list[dict[str, float]]) -> list[str]:
    """Exactly the declared drivers (variables 2-8), with leads as the modelled outcome.

    Still what the forecast fits (`OLS_FORECAST_USES_FORWARD_SELECTION` is False): the
    forward-selected subset the OLS card shows was backtested against this in 2026-08-16 and
    forecast 7pp worse. The card and the forecast therefore describe different models on
    purpose, and that constant's comment is where the reason lives.

    Nothing else is admitted. Meta's other funnel metrics (conversations, impressions,
    link clicks, platform leads) and every autoregressive term are excluded even though
    several fit better, because the model is meant to express the declared causal
    framework rather than whatever maximises R2. last7_leads used to ride along as a
    momentum control and was by far the strongest regressor, which meant the declared
    variables were being read against a lagged copy of the target; it is gone, so each
    coefficient below is now that variable's own association with lead volume.

    Features with no variation over the window are dropped - they cannot be estimated,
    which is a different thing from being unhelpful, and _declared_variable_coverage
    reports them as such rather than letting them disappear silently. Varying features
    that do not add rank after the intercept and earlier declared terms are also dropped;
    otherwise OLS can only return arbitrary pseudoinverse coefficients for aliased
    columns such as age and a recency counter that differ by a constant.
    """
    candidates = [feature for _, _, features in DECLARED_OLS_GROUPS for feature in features]
    varying = [
        feature for feature in candidates
        if np.std([row.get(feature, 0.0) for row in feature_rows]) > 1e-9
    ]
    if not varying:
        return []
    return _prune_rank_dependent_features(feature_rows, varying)


def _ols_block_fit(
    y: np.ndarray, feature_rows: list[dict[str, float]], features: list[str],
) -> dict | None:
    """Least-squares fit of y on [intercept, features], reduced to what selection reads.

    The lightweight core of _fit_ols_summary, without the standard errors, per-coefficient
    t tests and residual diagnostics. Forward selection runs O(groups^2) fits per scope and
    only ever reads R2, adjusted R2 and the residual sum of squares off them, so it must not
    pay for the full summary each time.

    df_model is the design matrix rank minus the intercept, not len(features): the weekday
    block is rank-deficient by construction, and both adjusted R2 and the partial F test have
    to be charged for the degrees of freedom actually spent, otherwise a group that adds no
    rank looks free.

    An empty feature list is the intercept-only baseline -- a real model (predict the mean),
    and the one every first-round candidate is measured against.
    """
    n = len(y)
    if n <= 1:
        return None
    total = float(np.sum((y - np.mean(y)) ** 2))
    if not features:
        return {"sse": total, "r_squared": 0.0, "adjusted_r_squared": 0.0,
                "df_model": 0, "df_resid": n - 1}
    design = np.asarray([[row.get(feature, 0.0) for feature in features] for row in feature_rows], dtype=float)
    if design.shape[0] != n or design.shape[1] != len(features):
        return None
    x = np.c_[np.ones(n), design]
    rank = int(np.linalg.matrix_rank(x))
    df_model = max(0, rank - 1)
    df_resid = n - rank
    if df_model <= 0 or df_resid <= 0:
        return None
    coefficients = np.linalg.pinv(x.T @ x) @ x.T @ y
    fitted = x @ coefficients
    sse = float(np.sum((y - fitted) ** 2))
    return {
        "sse": sse,
        "r_squared": 0.0 if total <= 1e-12 else float(1.0 - sse / total),
        "adjusted_r_squared": _ols_adjusted_r2(y, fitted, df_model),
        "df_model": df_model,
        "df_resid": df_resid,
    }


def _partial_f_p_value(base: dict, candidate: dict) -> float | None:
    """p-value of the block F test for the terms `candidate` adds over `base`.

    Answers "does this whole variable belong in the model", which no per-column t test can:
    the weekday block carries seven t statistics and not one of them is that question, and
    Holiday_Proximity's four buckets have the same problem. Returns None when the block adds
    no degrees of freedom -- it is aliased with what is already in the design, so there is no
    hypothesis left to test.
    """
    delta_df = candidate["df_model"] - base["df_model"]
    if delta_df <= 0 or candidate["df_resid"] <= 0:
        return None
    mse_resid = candidate["sse"] / candidate["df_resid"]
    if mse_resid <= 1e-12:
        # Nothing left to test against: the block explains whatever the base model didn't.
        # Real in this data -- a synthetic-looking ad set whose leads are an exact multiple
        # of spend fits to machine precision, and F would be a division by zero.
        return 0.0 if candidate["sse"] < base["sse"] else None
    f_value = ((base["sse"] - candidate["sse"]) / delta_df) / mse_resid
    if f_value <= 0.0:
        return 1.0
    return _f_survival_p_value(f_value, delta_df, candidate["df_resid"])


# Adjusted R2 already charges for each degree of freedom spent, so any positive gain is in
# principle an improvement. This floor exists only to stop a term entering on floating-point
# noise; a genuine 1e-6 improvement in adjusted R2 is not a finding worth a coefficient row.
FORWARD_SELECTION_MIN_GAIN = 1e-6

# Second entry gate (2026-08-16): a candidate also has to clear this on its block F test.
# Adjusted R2 alone will happily admit a variable that lifts the fit by 0.0004 with p = 0.6 --
# noise that happened to lean the right way. Deliberately looser than the conventional 0.05:
# greedy search picks the best of up to seven candidates each round, so the winner's nominal
# p-value is optimistically biased and reading it at 0.05 would be false precision. It is a
# junk filter, not a certificate that what got in is real.
FORWARD_SELECTION_MAX_P = 0.10


def _forward_select_declared_features(
    values: np.ndarray, feature_rows: list[dict[str, float]],
) -> dict:
    """Greedy forward selection over the declared variables, scored by adjusted R2.

    Diagnostics only. This drives the Multivariate OLS card and the per-ad-set regression
    report; the forecast path deliberately still fits every declared variable that varies,
    because forward selection was measured against it on held-out WAPE in 2026-08-16 and lost
    -- see `OLS_FORECAST_USES_FORWARD_SELECTION` for the numbers and the survivorship trap.

    An empty return is a real answer, not a failure: it means nothing cleared both gates on
    this window. That happens on about a third of rolling-origin windows, which is exactly why
    the two paths disagree -- the card is willing to say "no declared variable is significant
    here" and a forecast still has to produce fourteen numbers.

    Whole variables enter or stay out together: Holiday_Proximity's four buckets and the seven
    weekday indicators are one candidate each, not eleven. Selecting individual dummies would
    fit leaner but leaves the declared-variable displays reporting partial credit ("Wednesday
    but not Thursday"), which is not a statement the declared causal framework can make.

    Candidates are evaluated against the observation budget `_fit_ols_summary` enforces
    (max(12, terms + 6) days), so on a thin ad set the search stops at a set that can actually
    be fitted rather than returning one that will be refused downstream.

    Two gates, not one (2026-08-16). A candidate enters only if it lifts adjusted R2 by more
    than FORWARD_SELECTION_MIN_GAIN *and* its block F test clears FORWARD_SELECTION_MAX_P.
    Adjusted R2 decides the ranking within a round; the p-value decides whether the round's
    winner is worth having at all.

    After every addition the search takes a backward glance at what is already in: a variable
    that earned its place early can become redundant once a correlated one joins, and pure
    greedy forward selection has no way to give the seat back. Both moves strictly increase
    adjusted R2, so the walk terminates.

    Returns the selected columns, a per-round trace of every candidate tried with its R2,
    adjusted R2, gain and p-value, plus, for every variable left out, the reason and the
    adjusted-R2 delta it would have produced against the FINAL model -- silently missing
    variables read as bugs on this project, so `_declared_variable_coverage` needs the number.
    """
    y = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    result: dict = {"features": [], "order": [], "rejected": {}, "adjusted_r_squared": None,
                    "r_squared": None, "steps": [], "alpha": FORWARD_SELECTION_MAX_P}
    if len(y) == 0 or not feature_rows or len(feature_rows) != len(y):
        return result

    groups: list[tuple[int, str, list[str]]] = []
    for number, label, features in DECLARED_OLS_GROUPS:
        varying = [
            feature for feature in features
            if np.std([row.get(feature, 0.0) for row in feature_rows]) > 1e-9
        ]
        if varying:
            groups.append((number, label, varying))
    baseline = _ols_block_fit(y, feature_rows, [])
    if not groups or baseline is None:
        return result

    def columns(selection: list[tuple[int, str, list[str]]]) -> list[str]:
        out: list[str] = []
        for _, _, features in selection:
            out.extend(feature for feature in features if feature not in out)
        return out

    def fit_of(selection: list[tuple[int, str, list[str]]]) -> tuple[dict | None, str]:
        features = columns(selection)
        if not features:
            return baseline, "scored"
        # The same budget _fit_ols_summary enforces, so the search stops at a set that can
        # actually be fitted rather than one that will be refused downstream.
        if len(y) < max(12, len(features) + 6):
            return None, "observations"
        fit = _ols_block_fit(y, feature_rows, features)
        return (fit, "scored") if fit is not None else (None, "rank")

    def measure(base: dict, fit: dict | None, status: str) -> dict:
        """One row of the trace: what this move would do to the fit, and whether it qualifies."""
        entry: dict = {"r_squared": None, "adjusted_r_squared": None, "gain": None,
                       "p_value": None, "df_added": None, "status": status}
        if fit is None:
            return entry
        gain = fit["adjusted_r_squared"] - base["adjusted_r_squared"]
        entry.update({
            "r_squared": float(fit["r_squared"]),
            "adjusted_r_squared": float(fit["adjusted_r_squared"]) if math.isfinite(fit["adjusted_r_squared"]) else None,
            "gain": float(gain) if math.isfinite(gain) else None,
            "p_value": _partial_f_p_value(base, fit),
            "df_added": int(fit["df_model"] - base["df_model"]),
        })
        if not math.isfinite(gain):
            entry["status"] = "rank"
        elif gain <= FORWARD_SELECTION_MIN_GAIN:
            entry["status"] = "no_gain"
        elif entry["p_value"] is None or entry["p_value"] >= FORWARD_SELECTION_MAX_P:
            entry["status"] = "not_significant"
        else:
            entry["status"] = "eligible"
        return entry

    selected: list[tuple[int, str, list[str]]] = []
    current = baseline

    def try_drop() -> bool:
        """Remove an already-selected variable if the model is better without it."""
        nonlocal selected, current
        if len(selected) < 2:
            return False
        rows, best = [], None
        for group in selected:
            trial = [item for item in selected if item is not group]
            fit, status = fit_of(trial)
            entry = measure(current, fit, status)
            entry.update({"number": group[0], "name": group[1]})
            rows.append(entry)
            if entry["status"] == "eligible" and (best is None or entry["gain"] > best[0]["gain"]):
                best = (entry, group, fit)
        if best is None:
            return False
        entry, group, fit = best
        selected = [item for item in selected if item is not group]
        current = fit  # type: ignore[assignment]
        result["steps"].append({
            "round": len(result["steps"]) + 1, "action": "drop", "winner": group[0],
            "winner_name": group[1], "candidates": rows,
            "r_squared": float(current["r_squared"]),
            "adjusted_r_squared": float(current["adjusted_r_squared"]),
        })
        return True

    def try_add() -> bool:
        """Add the best candidate that clears both gates, if there is one."""
        nonlocal selected, current
        remaining = [group for group in groups if group not in selected]
        if not remaining:
            return False
        rows, best = [], None
        for group in remaining:
            fit, status = fit_of(selected + [group])
            entry = measure(current, fit, status)
            entry.update({"number": group[0], "name": group[1]})
            rows.append(entry)
            if entry["status"] == "eligible" and (best is None or entry["gain"] > best[0]["gain"]):
                best = (entry, group, fit)
        if best is None:
            return False
        entry, group, fit = best
        selected = selected + [group]
        current = fit  # type: ignore[assignment]
        result["steps"].append({
            "round": len(result["steps"]) + 1, "action": "add", "winner": group[0],
            "winner_name": group[1], "candidates": rows,
            "r_squared": float(current["r_squared"]),
            "adjusted_r_squared": float(current["adjusted_r_squared"]),
        })
        return True

    # Intercept-only baseline: the mean predictor, whose adjusted R2 is 0 by construction. A
    # variable has to beat "predict the average day" before it earns a place.
    # The round cap is a backstop against a pathological add/drop oscillation; every accepted
    # move raises adjusted R2 by more than the floor and adjusted R2 is bounded above, so the
    # walk terminates on its own and this should never bind.
    max_rounds = 4 * len(groups) + 4
    while len(result["steps"]) < max_rounds:
        if try_add():
            try_drop()
            continue
        if try_drop():
            continue
        break

    # Report every rejection against the final model, not against whichever round it lost in --
    # a variable's marginal value depends on what else ended up in the fit.
    for group in groups:
        if group in selected:
            continue
        fit, status = fit_of(selected + [group])
        entry = measure(current, fit, status)
        result["rejected"][group[0]] = {
            "reason": entry["status"] if entry["status"] != "eligible" else "no_gain",
            "delta": entry["gain"], "p_value": entry["p_value"],
        }

    # Aliased columns can survive the greedy pass inside a winning group (the group earned its
    # place on the columns that did add rank). Prune them the same way the all-declared selector
    # does, so no coefficient row is a pseudoinverse artifact.
    result["features"] = _prune_rank_dependent_features(feature_rows, columns(selected))
    result["order"] = [number for number, _, _ in selected]
    if selected:
        result["adjusted_r_squared"] = float(current["adjusted_r_squared"])
        result["r_squared"] = float(current["r_squared"])
    return result


def _prune_rank_dependent_features(
    feature_rows: list[dict[str, float]], features: list[str],
) -> list[str]:
    """Keep only features that add rank once the intercept and earlier features are in place."""
    kept: list[str] = []
    design = np.ones((len(feature_rows), 1), dtype=float)
    current_rank = int(np.linalg.matrix_rank(design))
    for feature in features:
        column = np.asarray([row.get(feature, 0.0) for row in feature_rows], dtype=float).reshape(-1, 1)
        candidate_design = np.c_[design, column]
        candidate_rank = int(np.linalg.matrix_rank(candidate_design))
        if candidate_rank > current_rank:
            kept.append(feature)
            design = candidate_design
            current_rank = candidate_rank
    return kept


# Whether the forecast fits the forward-selected variables (what the Multivariate OLS card
# reports) or every declared variable that varies. FALSE, and measured, not assumed:
# backtest_forward_selection.py, rolling-origin over all 30 ad sets, 2026-08-16.
#
#   all-declared + ridge     14d pooled WAPE 51.0%   median 70.6%   9.1 terms   <- shipping
#   forward + ridge                          58.0%          69.8%   1.4 terms
#   forward, no ridge                        59.6%          69.7%
#   all-declared, no ridge                   56.5%          82.4%
#
# Forward selection makes the forecast worse here, and the reason is worth keeping: on 34% of
# backtest windows (55 of 164) nothing clears the entry gates at all. On those windows the
# all-declared model still extracts a weak signal from many shrunken coefficients -- which is
# precisely what the ridge is for -- while hard selection throws it away. Consistent with
# Vault/Modeling/Forecast-Flatness-Is-The-Data.md: regularised-everything beats select-then-fit
# on this data.
#
# Watch out for the trap that nearly sold the opposite conclusion. An empty selection used to
# raise, _rolling_origin_backtest swallows the exception, and the model was then scored only on
# the 66% of windows where it found signal -- which read as 46.3% pooled, a 4.5pp "win" that was
# pure survivorship. Any future re-test must score every window (hence the intercept fallback in
# _ols_forecast) or it will lie the same way.
#
# Flip to True only after re-running that harness on more data and seeing a real win.
OLS_FORECAST_USES_FORWARD_SELECTION = False


def _ols_forecast(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
    *, multivariate: bool = False,
) -> list[float]:
    feature_rows, future_rows = _ols_feature_frame(values, dates, context, horizon)
    if not multivariate:
        features = ["spend"]
    elif OLS_FORECAST_USES_FORWARD_SELECTION:
        # Refitted at every rolling-origin cutoff on that window's data only, so selection is
        # part of the forecast rather than something chosen with sight of the held-out days.
        features = _forward_select_declared_features(values, feature_rows)["features"]
        if not features:
            # Nothing cleared both gates, so the fitted model IS the intercept -- return it
            # rather than raising. Two reasons this is not a cop-out:
            #   * It is what the search actually concluded. Falling back to the spend line
            #     instead measured 5.8pp worse pooled WAPE, because the ad sets that select
            #     nothing are exactly the ones where a spend slope is noise.
            #   * _forecast_candidate must be total. _forecast_for_series calls it unguarded
            #     once a model wins selection, so a raise here would take down a whole ad
            #     set's training run in the narrow case where the backtest windows selected
            #     variables and the full series then selected none.
            mean_level = float(np.mean(values)) if len(values) else 0.0
            return _cap_forecast_values([mean_level] * horizon, values)
    else:
        features = _select_multivariate_ols_features(values, feature_rows)
    # Unpenalised, the declared set puts ~14 regressors against ~40 noisy daily counts and
    # backtests at R2 -0.84, i.e. worse than predicting the mean. The penalty scales with
    # feature count because that is what the overfitting scales with. Spend-only is a single
    # regressor with plenty of observations and needs no shrinkage.
    penalty = MULTIVARIATE_RIDGE_PER_FEATURE * len(features) if multivariate else 0.0
    predictions, _ = _fit_ols_predictions(values, feature_rows, future_rows, features,
                                          ridge_penalty=penalty)
    return _cap_forecast_values(predictions, values)


def _load_scope_feature_rows(
    ad_set_id: str | None = None, campaign_id: str | None = None,
) -> tuple[np.ndarray | None, list[dict] | None, dict]:
    """Build the daily lead-count series and OLS feature rows for one ad set, one
    campaign, or the whole portfolio.

    Scope narrows the population, never the encoding. An ad-set scope gets the ad-set-grain
    branches of the change/age helpers (0/1 indicators, true age); campaign and portfolio
    scopes get the pooled branches (shares of live ad sets), so the two campaign-vs-portfolio
    fits stay directly comparable to each other.

    Extracted from `get_ols_model_summaries` so the correlation matrix (which needs the same
    feature rows, not a fit) doesn't have to duplicate this. Returns `(None, None, scope)` when
    there is no data for the requested scope -- callers build their own "empty" response shape
    around that, since an OLS summary and a correlation matrix have different empty payloads.
    """
    ad_set_id = str(ad_set_id).strip() if ad_set_id not in (None, "") else None
    campaign_id = str(campaign_id).strip() if campaign_id not in (None, "") else None
    with connect() as db:
        lead_rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = _load_spend_frame(db)

    level = "ad_set" if ad_set_id else ("campaign" if campaign_id else "portfolio")
    scope: dict = {"level": level, "ad_set_id": ad_set_id, "campaign_id": campaign_id,
                   "ad_set_count": 0, "observations": 0, "lead_total": 0.0}
    if not lead_rows:
        return None, None, scope

    all_frame = pd.DataFrame([dict(row) for row in lead_rows])
    all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    if ad_set_id:
        all_frame = all_frame[all_frame["utm_ad_set_id"].astype(str) == ad_set_id]
    elif campaign_id:
        all_frame = all_frame[all_frame["utm_campaign_id"].astype(str) == campaign_id]
    if all_frame.empty:
        return None, None, scope

    # The window is the scope's own active span, not the portfolio's. Padding a short-lived
    # ad set out to the portfolio window would bury its real days under invented zeros.
    dates = pd.date_range(all_frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
    values = all_frame.groupby("aggregate_date")["lead_count"].sum().reindex(dates, fill_value=0).astype(float).to_numpy()
    scoped_sets = tuple(sorted({str(value) for value in all_frame["utm_ad_set_id"].dropna()}))
    scope.update({"ad_set_count": len(scoped_sets), "observations": int(len(dates)),
                  "lead_total": float(np.sum(values)),
                  "date_start": dates.min().strftime("%Y-%m-%d"),
                  "date_end": dates.max().strftime("%Y-%m-%d")})

    # Narrow the spend frame once, so every helper below sees only this scope's rows and the
    # pooled means/shares are taken over the scope rather than the portfolio.
    if spend_frame is not None and not spend_frame.empty:
        if ad_set_id:
            spend_frame = spend_frame[spend_frame["ad_set_id"].astype(str) == ad_set_id]
        elif campaign_id:
            spend_frame = spend_frame[spend_frame["campaign_id"].astype(str) == campaign_id]

    context = {
        "spend_values": _aligned_portfolio_spend_values(spend_frame, dates),
        "conversation_values": _aligned_performance_values(spend_frame, dates, "messaging_conversations_started"),
        "platform_leads_values": _aligned_performance_values(spend_frame, dates, "platform_leads"),
        "link_click_values": _aligned_performance_values(spend_frame, dates, "link_clicks"),
        "impression_values": _aligned_performance_values(spend_frame, dates, "impressions"),
        "campaign_values": np.zeros(len(values), dtype=float),
        "overall_values": np.zeros(len(values), dtype=float),
        "frequency_values": _aligned_mean_performance_values(spend_frame, dates, "frequency", ad_set=ad_set_id),
        "days_since_start_values": _days_since_start_values(spend_frame, dates, ad_set=ad_set_id),
    }
    ad_set_changes = _ad_set_change_features(spend_frame, dates, ad_set=ad_set_id)
    if ad_set_id:
        ad_changes = _ad_change_features(dates, spend_frame=spend_frame, ad_set=ad_set_id)
    elif campaign_id:
        ad_changes = _ad_change_features(dates, spend_frame=spend_frame, ad_sets=scoped_sets)
    else:
        ad_changes = _ad_change_features(dates, spend_frame=spend_frame)
    context["ad_set_change_recency_values"] = ad_set_changes["ad_set_change_recency"]
    context["ad_change_recency_values"] = ad_changes["ad_change_recency"]
    feature_rows, _ = _ols_feature_frame(values, dates, context, 14)
    return values, feature_rows, scope


def get_ols_model_summaries(
    ad_set_id: str | None = None, campaign_id: str | None = None,
) -> dict:
    """OLS diagnostics for one ad set, one campaign, or the whole portfolio.

    Narrow scopes have far fewer usable days than the portfolio, and the multivariate model
    asks for ~18 regressors. _fit_ols_summary refuses to fit below len(features) + 6
    observations, so a thin ad set returns None here rather than an impressive-looking fit
    with one degree of freedom. The returned `scope` block carries the counts the UI needs
    to explain that to the reader.
    """
    values, feature_rows, scope = _load_scope_feature_rows(ad_set_id, campaign_id)
    empty = {"univariate": None, "multivariate": None, "declared_variables": [], "scope": scope}
    if values is None or feature_rows is None:
        return empty
    univariate = _fit_ols_summary(values, feature_rows, ["spend"], "OLS")
    # Forward selection, not every declared variable that happens to vary -- see
    # _forward_select_declared_features. The forecast path deliberately does not (it fits all
    # of them under ridge, which backtests better -- OLS_FORECAST_USES_FORWARD_SELECTION).
    selection = _forward_select_declared_features(values, feature_rows)
    selected = selection["features"]
    multivariate = _fit_ols_summary(values, feature_rows, selected, "Multivariate OLS") if selected else None
    # Why a card is missing matters more at narrow scope than at portfolio scope, where it
    # only ever meant "no spend uploaded".
    scope["multivariate_terms_wanted"] = len(selected)
    scope["multivariate_days_needed"] = max(12, len(selected) + 6) if selected else 12
    scope["univariate_days_needed"] = 12
    scope["multivariate_selection"] = "forward"
    scope["multivariate_selection_order"] = selection["order"]
    return {
        "univariate": univariate,
        "multivariate": multivariate,
        "declared_variables": _declared_variable_coverage(
            feature_rows, selected, multivariate, selection=selection,
        ),
        # The search itself, round by round, for the "Selection path" panel. Everything the
        # panel shows was computed during selection anyway -- publishing it is what stops the
        # chosen variable list from reading as an unexplained verdict.
        "selection": {
            "method": "forward",
            "alpha": selection["alpha"],
            "min_gain": FORWARD_SELECTION_MIN_GAIN,
            "order": selection["order"],
            "steps": selection["steps"],
            "r_squared": selection["r_squared"],
            "adjusted_r_squared": selection["adjusted_r_squared"],
        },
        "scope": scope,
    }


# The eight variables declared as drivers of lead volume, mapped onto the model features that
# (was ten until 2026-08-11, when the two change-TYPE variables were removed entirely)
# carry them. Listed explicitly so a variable that is silently absent from the fit still
# appears in the UI with the reason why, rather than simply not being shown.
DECLARED_VARIABLES: tuple[dict[str, object], ...] = (
    {"number": 1, "name": "Leads", "features": (), "role": "target",
     "note": "Modelled outcome: CRM lead count per day."},
    {"number": 2, "name": "Spent", "features": ("spend",)},
    {"number": 3, "name": "Holiday_Proximity",
     "features": ("holiday_during_holiday", "holiday_0_14_days", "holiday_15_30_days", "holiday_31_60_days")},
    {"number": 4, "name": "days_since_ad_set_started", "features": ("days_since_adset_started",),
     "note": "Confirmed start dates only, recorded via the \"Start date\" tab of the Ad set "
             "change popover. The left-censored earliest-upload-day estimate was found wrong "
             "and was removed 2026-08-06 -- zero until a real start date is recorded."},
    {"number": 5, "name": "frequency", "features": ("frequency",)},
    {"number": 6, "name": "ad_change_recency", "features": ("ad_change_recency",),
     "note": "Confirmed change-log rows only. The per-ad activity detector was found wrong "
             "and was removed 2026-08-06 -- zero until a real change is recorded."},
    {"number": 7, "name": "ad_set_change_recency", "features": ("ad_set_change_recency",),
     "note": "Confirmed change-log rows only. The step-shift detector was found wrong and "
             "was removed 2026-08-06 -- zero until a real change is recorded."},
    # All seven days are included, no reference day held out -- per explicit request. This is rank-deficient by construction: the seven
    # indicators always sum to 1, exactly collinear with the intercept. The fit still runs
    # (np.linalg.pinv's minimum-norm solution in _fit_ols_summary/_fit_ols_predictions), but
    # the individual day coefficients split the intercept arbitrarily and aren't independently
    # meaningful on their own -- only their differences from each other are. Deliberately no
    # "note" here (contrast #4/#6/#7, which all have one): a note would replace the
    # dynamic in-model day list below with static text, and the whole point of this variable
    # is to show which of the seven days are actually in the fit.
    {"number": 8, "name": "Days of the week",
     "features": ("weekday_0", "weekday_1", "weekday_2", "weekday_3", "weekday_4", "weekday_5",
                  "weekday_6", "is_weekend")},
)


def _declared_variable_coverage(
    feature_rows: list[dict[str, float]], selected: list[str], multivariate: dict | None,
    *, selection: dict | None = None,
) -> list[dict]:
    """Per-variable status for Dataset and OLS diagnostics.

    Distinguishes four very different reasons a variable can be missing from the fit: it was
    never collected, it was collected but never varied, it varied but is not estimable
    (aliased with something already in the design), or it is estimable and forward selection
    weighed it and left it out. Pass `selection` (from `_forward_select_declared_features`) to
    tell the last two apart and to report the adjusted-R2 delta the variable would have added;
    without it, both collapse to the old "omitted from the estimable model" wording.
    """
    coefficients = {row["feature"]: row for row in (multivariate or {}).get("coefficients", [])}
    selected_set = set(selected)
    out: list[dict] = []
    for spec in DECLARED_VARIABLES:
        features: tuple[str, ...] = spec["features"]  # type: ignore[assignment]
        note = str(spec.get("note") or spec.get("partial") or "")
        entry: dict[str, object] = {
            "number": spec["number"], "name": spec["name"], "note": note,
        }
        if spec.get("role") == "target":
            entry.update({"status": "target", "detail": "Dependent variable"})
            out.append(entry)
            continue
        varying = [
            name for name in features
            if float(np.std([row.get(name, 0.0) for row in feature_rows] or [0.0])) > 1e-9
        ]
        in_model = [name for name in features if name in selected_set]
        if in_model:
            terms = [coefficients[name] for name in in_model if name in coefficients]
            entry.update({
                "status": "in_model",
                "detail": ", ".join(_feature_label(name) for name in in_model),
                "terms": [
                    {"term": term["term"], "coef": term["coef"], "p_value": term["p_value"]}
                    for term in terms
                ],
                "significant": any(
                    term["p_value"] is not None and term["p_value"] < 0.05 for term in terms
                ),
            })
        elif varying:
            rejection = (selection or {}).get("rejected", {}).get(spec["number"])
            reason = (rejection or {}).get("reason")
            if reason == "no_gain":
                delta = rejection.get("delta")
                moves = f"{delta:+.4f}" if delta is not None else "no better"
                entry.update({
                    "status": "not_selected",
                    "detail": f"Varies, but forward selection left it out -- adding it moves "
                              f"adjusted R2 by {moves}",
                })
            elif reason == "not_significant":
                # It does lift the fit, just not by enough to distinguish from noise. Report
                # both numbers: the gain on its own reads as "why was this left out?".
                delta = rejection.get("delta")
                p_value = rejection.get("p_value")
                moves = f"{delta:+.4f}" if delta is not None else "no better"
                significance = f"p = {p_value:.3f}" if p_value is not None else "not estimable"
                entry.update({
                    "status": "not_selected",
                    "detail": f"Varies and moves adjusted R2 by {moves}, but forward selection "
                              f"left it out -- {significance}, above the {FORWARD_SELECTION_MAX_P:.2f} "
                              f"entry threshold",
                })
            elif reason == "observations":
                entry.update({
                    "status": "not_selected",
                    "detail": "Varies, but this window has too few days to afford another term",
                })
            else:
                entry.update({"status": "available", "detail": "Varies, but omitted from the estimable model"})
        elif features:
            entry.update({"status": "flat", "detail": "Collected, but constant over this window"})
        else:
            entry.update({"status": "missing", "detail": "Not collected"})
        out.append(entry)
    return out


def get_dataset_correlation(ad_set_id: str | None = None, campaign_id: str | None = None) -> dict:
    """Correlation matrix over the declared-variable feature columns, for one ad set, one
    campaign, or the whole portfolio.

    Reuses the same feature rows the OLS fit uses at the same scope (see
    `get_ols_model_summaries`), so the correlation matrix and the "importance" regression on
    the Dataset page are always looking at exactly the same data. Constant columns are
    dropped -- a column with zero variance has an undefined correlation with everything, not
    a real 0 or 1. Narrow scopes have far fewer observations than the portfolio, so pairs can
    swing on a handful of days -- `sample_size` is returned so the UI can flag that.
    """
    values, feature_rows, scope = _load_scope_feature_rows(ad_set_id, campaign_id)
    if values is None or feature_rows is None:
        return {"variables": [], "matrix": [], "sample_size": 0, "date_start": None, "date_end": None, "scope": scope}

    feature_to_variable = {
        feature: spec for spec in DECLARED_VARIABLES for feature in spec["features"]  # type: ignore[union-attr]
    }
    frame = pd.DataFrame(feature_rows)
    frame.insert(0, "leads", pd.Series(values, index=frame.index))
    keep = ["leads"] + [name for name in feature_to_variable if name in frame.columns]
    frame = frame[keep].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    varying = [name for name in frame.columns if name == "leads" or float(frame[name].std()) > 1e-9]
    frame = frame[varying]

    correlation = frame.corr().fillna(0.0)
    variables = []
    for name in frame.columns:
        if name == "leads":
            variables.append({"key": name, "label": "Leads", "variable_number": 1, "variable_name": "Leads"})
        else:
            spec = feature_to_variable[name]
            variables.append({
                "key": name, "label": _feature_label(name),
                "variable_number": spec["number"], "variable_name": spec["name"],
            })
    return {
        "variables": variables,
        "matrix": [[round(float(value), 4) for value in row] for row in correlation.to_numpy()],
        "sample_size": int(len(frame)),
        "date_start": scope.get("date_start"),
        "date_end": scope.get("date_end"),
        "scope": scope,
    }


def get_dataset_overview() -> dict:
    """Row counts, date ranges, and totals for the tables that actually feed the app.

    Surfaces the confirmed-vs-inferred change-event gap up front: variables 6/7/9/10 in
    `DECLARED_VARIABLES` are entirely proxy-inferred while `change_events` is empty, and that
    caveat is easy to lose track of once it's buried in a per-variable note.
    """
    with connect() as db:
        lead_count, lead_min, lead_max = db.execute(
            "SELECT COUNT(*), MIN(date(created_at)), MAX(date(created_at)) FROM lead_events"
        ).fetchone()
        ad_count, ad_min, ad_max, ad_spend, ad_leads = db.execute(
            """SELECT COUNT(*), MIN(day), MAX(day),
                      COALESCE(SUM(amount_spent_usd), 0), COALESCE(SUM(leads), 0)
               FROM daily_ad_performance"""
        ).fetchone()
        change_total = db.execute("SELECT COUNT(*) FROM change_events").fetchone()[0]
        change_confirmed = db.execute(
            "SELECT COUNT(*) FROM change_events WHERE source='confirmed'"
        ).fetchone()[0]
        upload_count = db.execute("SELECT COUNT(*) FROM raw_uploads").fetchone()[0]

    tables = [
        {
            "key": "leads", "table": "lead_events", "label": "Leads (CRM traffic export)",
            "description": ("One row per lead: customer name, status, campaign/ad-set/ad IDs, "
                             "amount. Feeds the Leads list and the actual-leads counts used "
                             "everywhere else in the app."),
            "row_count": int(lead_count or 0), "date_start": lead_min, "date_end": lead_max,
        },
        {
            "key": "ad_performance", "table": "daily_ad_performance", "label": "Ad performance (Meta export)",
            "description": ("One row per ad set per day: spend, reach, impressions, frequency, "
                             "CPL, budget. Drives every spend/CPL chart and the forecast model's "
                             "spend signal."),
            "row_count": int(ad_count or 0), "date_start": ad_min, "date_end": ad_max,
            "total_spend": float(ad_spend or 0), "total_platform_leads": float(ad_leads or 0),
        },
    ]
    return {
        "tables": tables,
        "uploads_count": int(upload_count or 0),
        "change_events": {
            "total": int(change_total or 0),
            "confirmed": int(change_confirmed or 0),
            "note": ("No confirmed change events yet, so ad_change_recency and ad_set_change_recency "
                     "(declared variables 9 and 10) are currently 100% inferred proxies, not "
                     "confirmed facts -- see the Change Log Importer to upload confirmed rows."),
        },
    }


# A CRM pipeline stage, hand-recorded via the board -- there is no import source for it, so
# every lead starts at the first option ("Intake") until someone moves it. Order is the
# pipeline's natural progression, which is also the order the dropdown lists them in.
# Defined here (ahead of _LEADS_FILTER_FIELDS below, which reads it) rather than down near
# LEAD_UPDATE_FIELDS/_clean_lead_update_value where the rest of the lead-editing code lives --
# Python evaluates module-level statements top to bottom, and this file's editing helpers
# happen to sit thousands of lines below the dataset-rows table specs that also need this list.
LEAD_QUALITY_OPTIONS = [
    "Intake",
    "Not Qualified",
    "Qualified",
    "Converted",
    "Lost",
    "Awaiting Document and Payment",
]

# Ad-performance's filter fields are shared by "ad_performance" and "ad_performance_export"
# below -- both read the same `daily_ad_performance p` table under the same `p.` alias, just
# with a different column order/naming for display, so one field set covers both.
_AD_PERFORMANCE_FILTER_FIELDS: dict[str, dict[str, object]] = {
    "day": {"column": "p.day", "type": "date"},
    "campaign_name": {"column": "p.campaign_name", "type": "text"},
    "campaign_id": {"column": "p.campaign_id", "type": "text"},
    "ad_set_id": {"column": "p.ad_set_id", "type": "text"},
    "delivery_status": {"column": "p.delivery_status", "type": "text"},
    "amount_spent_usd": {"column": "p.amount_spent_usd", "type": "number"},
    "cost_per_lead": {"column": "p.cost_per_lead", "type": "number"},
    "reach": {"column": "p.reach", "type": "number"},
    "impressions": {"column": "p.impressions", "type": "number"},
    "frequency": {"column": "p.frequency", "type": "number"},
    "ad_set_budget": {"column": "p.ad_set_budget", "type": "number"},
}

_LEADS_FILTER_FIELDS: dict[str, dict[str, object]] = {
    "status": {"column": "status", "type": "enum", "options": ["New", "Existing"]},
    "lead_quality": {"column": "lead_quality", "type": "enum", "options": LEAD_QUALITY_OPTIONS},
    "customer_name": {"column": "customer_name", "type": "text"},
    "utm_campaign": {"column": "utm_campaign", "type": "text"},
    "utm_campaign_id": {"column": "utm_campaign_id", "type": "text"},
    "utm_ad_set_id": {"column": "utm_ad_set_id", "type": "text"},
    "utm_ad_id": {"column": "utm_ad_id", "type": "text"},
    "fb_ad_title": {"column": "fb_ad_title", "type": "text"},
    # Traffic exports since 2026-08-01 no longer carry a per-lead "Amount spent (USD)" column
    # (that context previously arrived only via the model-dataset workbook), so this falls back
    # to the ad set's day spend from the ad-performance export -- see the "leads" table's "join".
    "amount_spent_usd": {"column": "COALESCE(amount_spent_usd, p.spend)", "type": "number"},
    "created_at": {"column": "created_at", "type": "date"},
}


def _sort_fields_from(filter_fields: dict[str, dict[str, object]], **extra: str) -> dict[str, str]:
    """Sortable field key -> SQL expression, for the Dataset page's click-to-sort headers.

    Everything filterable is also sortable, so the filter allowlist is the base. `extra`
    carries the columns that are sortable but not filterable -- joined or computed values
    (`a.lead_count AS leads`, the COALESCE'd cost-per-message) which SQLite will happily
    ORDER BY via their SELECT alias, but which have no single base-table column for
    _build_filter_clause to bind against.
    """
    return {**{key: str(spec["column"]) for key, spec in filter_fields.items()}, **extra}


_AD_PERFORMANCE_SORT_FIELDS = _sort_fields_from(
    _AD_PERFORMANCE_FILTER_FIELDS,
    leads="leads",
    messaging_conversations_started="p.messaging_conversations_started",
    cost_per_messaging_conversation_started="cost_per_messaging_conversation_started",
    ad_set_budget_type="p.ad_set_budget_type",
)

# Columns a free-text board search sweeps, per table. Only text-ish columns -- LIKE against a
# REAL/INTEGER column matches on its string rendering, which produces results nobody asked for
# (searching "3" hitting every row whose spend merely contains a 3).
_LEADS_SEARCH_COLUMNS = ["customer_name", "utm_campaign", "utm_campaign_id",
                         "utm_ad_set_id", "utm_ad_id", "fb_ad_title", "status", "lead_quality"]
_AD_PERFORMANCE_SEARCH_COLUMNS = ["p.campaign_name", "p.campaign_id", "p.ad_set_id",
                                  "p.delivery_status", "p.ad_set_budget_type"]


DATASET_ROW_TABLES: dict[str, dict[str, object]] = {
    "leads": {
        "table": "lead_events",
        # Falls back to the ad set's day spend (from the ad-performance export) whenever the
        # lead itself carries no "Amount spent (USD)" -- see _LEADS_FILTER_FIELDS above.
        "join": ("LEFT JOIN (SELECT ad_set_id, day, SUM(amount_spent_usd) AS spend "
                 "FROM daily_ad_performance GROUP BY ad_set_id, day) p "
                 "ON p.ad_set_id = utm_ad_set_id AND p.day = date(created_at)"),
        "columns": ["id", "platform", "status", "lead_quality", "created_at", "updated_at",
                    "customer_name", "utm_campaign", "utm_campaign_id", "utm_ad_set_id",
                    "utm_ad_id", "fb_ad_title", "COALESCE(amount_spent_usd, p.spend) AS amount_spent_usd"],
        "order_by": "created_at ASC",
        "campaign_column": "utm_campaign_id",
        "ad_set_column": "utm_ad_set_id",
        # Field key -> {column, type}. Keys are the contract with the frontend's filter bar
        # (Dataset page, "Raw data" section) -- rename here only alongside the matching
        # DATASET_FILTER_FIELDS entry in App.tsx, or filters silently stop matching.
        "filter_fields": _LEADS_FILTER_FIELDS,
        "sort_fields": _sort_fields_from(_LEADS_FILTER_FIELDS, id="id"),
        "search_columns": _LEADS_SEARCH_COLUMNS,
    },
    "ad_performance": {
        "table": "daily_ad_performance p",
        # `p.leads` (Meta's own per-ad-set-day lead count) is essentially always NULL in
        # practice and, even populated, reads ~$11 CPL vs ~$1.28 actual (see
        # Vault/Features/Ad-Decision-Engine.md) -- it's attribution-broken, not just sparse.
        # The trustworthy lead count at this grain is the CRM-attributed one already computed
        # into `daily_ad_set_aggregates` (same (day, ad_set_id) key as this table), so it's
        # joined in here instead of read off `p.leads`. `cost_per_messaging_conversation_started`
        # is likewise usually NULL from the Meta export even though the two inputs needed to
        # derive it (spend, messages started) are always present, so it's computed rather than
        # read when the stored value is missing.
        "join": "LEFT JOIN daily_ad_set_aggregates a ON a.aggregate_date = p.day AND a.utm_ad_set_id = p.ad_set_id",
        "columns": ["p.id", "p.day", "p.campaign_id", "p.campaign_name", "p.ad_set_id",
                    "p.delivery_status", "p.amount_spent_usd", "a.lead_count AS leads",
                    "p.cost_per_lead", "p.reach", "p.impressions", "p.frequency",
                    "p.messaging_conversations_started",
                    "COALESCE(p.cost_per_messaging_conversation_started, "
                    "CASE WHEN p.messaging_conversations_started > 0 "
                    "THEN p.amount_spent_usd / p.messaging_conversations_started END) "
                    "AS cost_per_messaging_conversation_started",
                    "p.ad_set_budget", "p.ad_set_budget_type",
                    "p.days_since_adset_started_imported",
                    "p.ad_set_change_recency_imported",
                    "p.ad_change_recency_imported"],
        "order_by": "p.day ASC",
        "campaign_column": "p.campaign_id",
        "ad_set_column": "p.ad_set_id",
        "filter_fields": _AD_PERFORMANCE_FILTER_FIELDS,
        "sort_fields": _AD_PERFORMANCE_SORT_FIELDS,
        "search_columns": _AD_PERFORMANCE_SEARCH_COLUMNS,
    },
    # Same underlying table as "ad_performance", just the column set/order of the cleaned
    # Combined export. Placeholder-only ad identity columns and message-cost fields are left
    # out here so the UI/export does not surface empty or attribution-noisy columns.
    "ad_performance_export": {
        "table": "daily_ad_performance p",
        # Same leads reasoning as the "ad_performance" entry above.
        "join": "LEFT JOIN daily_ad_set_aggregates a ON a.aggregate_date = p.day AND a.utm_ad_set_id = p.ad_set_id",
        "columns": ["p.id", "p.day", "p.campaign_name", "p.campaign_id", "p.ad_set_id",
                    "p.reach", "p.impressions", "p.frequency",
                    "p.ad_set_budget", "p.ad_set_budget_type", "p.amount_spent_usd",
                    "a.lead_count AS leads", "p.cost_per_lead",
                    "p.days_since_adset_started_imported",
                    "p.ad_set_change_recency_imported",
                    "p.ad_change_recency_imported"],
        "order_by": "p.day ASC",
        "campaign_column": "p.campaign_id",
        "ad_set_column": "p.ad_set_id",
        "filter_fields": _AD_PERFORMANCE_FILTER_FIELDS,
        "sort_fields": _AD_PERFORMANCE_SORT_FIELDS,
        "search_columns": _AD_PERFORMANCE_SEARCH_COLUMNS,
    },
}


_DECLARED_VAR_TABLES = {"ad_performance", "ad_performance_export"}


# Operators each field type accepts. Enforced on top of (not instead of) the column allowlist
# in DATASET_ROW_TABLES[*]["filter_fields"] -- a caller can't smuggle in a numeric-only operator
# against a text column, or vice versa.
_FILTER_OPERATORS_BY_TYPE: dict[str, set[str]] = {
    "text": {"contains", "not_contains", "is", "is_not", "is_empty", "is_not_empty"},
    "number": {"eq", "neq", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"},
    "date": {"on", "before", "after", "between", "is_empty", "is_not_empty"},
    "enum": {"is", "is_not"},
}


def _escape_like(term: str) -> str:
    """Neutralise LIKE wildcards so a searched `%` or `_` matches itself, not everything.

    Paired with `ESCAPE '\\'` on the clause. The per-field filter operators above predate
    this and splice raw terms; only the board search (which users type freely into) routes
    through here.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_filter_clause(field_spec: dict, operator: str, value: object) -> tuple[str, list]:
    """One filter row (field/operator/value from the Dataset page's filter bar) -> (SQL, params).

    `field_spec` is always looked up from DATASET_ROW_TABLES[*]["filter_fields"] by the caller,
    so `column` is a hardcoded identifier from that allowlist, never user input -- safe to splice
    into the SQL string. Only `params` values (bound via `?`) carry caller-supplied data.
    """
    column = str(field_spec["column"])
    ftype = str(field_spec["type"])
    if operator not in _FILTER_OPERATORS_BY_TYPE.get(ftype, set()):
        raise ValueError(f"Operator {operator!r} is not valid for a {ftype!r} field")

    if operator == "is_empty":
        return f"({column} IS NULL OR {column} = '')", []
    if operator == "is_not_empty":
        return f"({column} IS NOT NULL AND {column} <> '')", []

    if ftype == "text":
        text_value = "" if value is None else str(value)
        if operator == "contains":
            return f"LOWER({column}) LIKE ?", [f"%{text_value.lower()}%"]
        if operator == "not_contains":
            return f"({column} IS NULL OR LOWER({column}) NOT LIKE ?)", [f"%{text_value.lower()}%"]
        if operator == "is":
            return f"LOWER({column}) = ?", [text_value.lower()]
        if operator == "is_not":
            return f"({column} IS NULL OR LOWER({column}) <> ?)", [text_value.lower()]

    if ftype == "enum":
        values = value if isinstance(value, list) else [value]
        values = [str(v) for v in values if v not in (None, "")]
        if not values:
            raise ValueError("Enum filters need at least one selected value")
        placeholders = ", ".join("?" for _ in values)
        if operator == "is":
            return f"{column} IN ({placeholders})", values
        if operator == "is_not":
            return f"({column} IS NULL OR {column} NOT IN ({placeholders}))", values

    if ftype == "number":
        try:
            number_value = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Filter value {value!r} is not a number") from exc
        op_sql = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
        return f"{column} {op_sql} ?", [number_value]

    if ftype == "date":
        if operator == "on":
            return f"date({column}) = date(?)", [value]
        if operator == "before":
            return f"date({column}) < date(?)", [value]
        if operator == "after":
            return f"date({column}) > date(?)", [value]
        if operator == "between":
            if not isinstance(value, dict) or not value.get("from") or not value.get("to"):
                raise ValueError("A 'between' date filter needs both a from and to value")
            return f"date({column}) BETWEEN date(?) AND date(?)", [value["from"], value["to"]]

    raise ValueError(f"Unsupported operator {operator!r} for field type {ftype!r}")


_DECLARED_VAR_KEYS = (
    "days_since_adset_started", "ad_change_recency", "ad_set_change_recency",
)


def _change_state_as_of(
    events: tuple[pd.Timestamp, ...], day: pd.Timestamp,
) -> str | None:
    """The recency BUCKET as of `day`, or None when this ad set has no recorded dates.

    Mirrors `_resolve_change_state`'s carry-forward, then buckets it, so the raw table and the
    model can never tell different stories about the same day.

    A day before this ad set's first recorded event has no prior event and so reads
    `no_recent_change`, the same category a long-ago change decays into. None is reserved for
    "nobody has recorded this ad set at all", which the page renders "-" -- collapsing that
    into `no_recent_change` would let an untouched ad set look like a fully audited one.

    Returned the change type as a second element until 2026-08-11.
    """
    if pd.isna(day) or not events:
        return None
    target = day.normalize()
    prior = [when for when in events if when <= target]
    return recency_bucket(int((target - prior[-1]).days) if prior else None)


def _attach_declared_variables(rows: list[dict]) -> None:
    """Mutates `rows` in place, adding declared variables 4/6/7 columns.

    Read from the same confirmed sources the model reads -- `_confirmed_ad_set_starts` and
    `_recorded_change_events` -- so a value shown here is the one the OLS fit and the
    correlation matrix are actually using for that ad-set-day, not a parallel derivation that
    could drift from them. Until 2026-08-06 this nulled all its columns unconditionally: a
    stub left behind when the wrong detectors were removed, which meant recorded data never
    reached the table even though it did reach the model. The two change-TYPE columns (9 and
    10) were dropped entirely on 2026-08-11.

    An ad set with NO recorded events of a scope stays None on that scope's column (the page
    renders "-"), rather than being folded into `no_recent_change`. The distinction is the
    point: `no_recent_change` is a claim the change log makes about a day, while a blank means
    nobody has recorded that ad set either way, and collapsing the two would let an untouched
    ad set look like a fully audited one. A genuine "changed today" reads `0_3_days`, so it
    stays distinguishable from both. Days before a confirmed start date are None for the same
    reason -- the model clips that age to 0, but a negative age on the page would read as a
    data error rather than as "not launched yet".
    """
    starts = dict(_confirmed_ad_set_starts())
    # One lookup per distinct ad set on the page, not one per row. `_recorded_change_events`
    # is lru_cached, but at maxsize=64 (and two scopes per ad set) a 500-row page spanning
    # more than ~32 ad sets would evict its own entries mid-loop, so they're hoisted here.
    ad_sets = {str(row.get("ad_set_id") or "") for row in rows}
    ad_sets.discard("")
    events_by_scope = {
        scope: {set_id: _recorded_change_events(scope, set_id) for set_id in ad_sets}
        for scope in CHANGE_SCOPES
    }

    for row in rows:
        ad_set = str(row.get("ad_set_id") or "")
        day = pd.to_datetime(row.get("day"), errors="coerce")
        start = starts.get(ad_set)
        imported_days = row.get("days_since_adset_started_imported")
        computed_days = (
            None if start is None or pd.isna(day) or day.normalize() < start
            else int((day.normalize() - start).days)
        )
        row["days_since_adset_started"] = (
            int(imported_days) if imported_days is not None and not pd.isna(imported_days)
            else computed_days
        )
        for scope, recency_key in (
            ("ad_set", "ad_set_change_recency"),
            ("ad", "ad_change_recency"),
        ):
            imported = str(row.get(f"{recency_key}_imported") or "").strip()
            computed = _change_state_as_of(events_by_scope[scope].get(ad_set, ()), day)
            row[recency_key] = imported or computed


def _dataset_where(
    table: str, spec: dict,
    campaign_id: str | None, ad_set_id: str | None,
    filters: list[dict] | None, search: str | None,
) -> tuple[str, list[object]]:
    """WHERE clause + bound params shared by `get_dataset_rows` and `get_dataset_row_ids`.

    `table` is resolved through the `DATASET_ROW_TABLES` allowlist -- the table name, column
    list, and sort order are always the hardcoded values from that dict, never the caller's
    input, so this can't become a SQL-injection vector no matter what `table` is. Same for
    `filters`: each row's `field` is looked up in that table's `filter_fields` allowlist, so
    the SQL column and type are always the hardcoded spec, never the caller's string -- only
    the filter's `value` (always bound via `?`) carries caller-supplied data.
    """
    where: list[str] = []
    params: list[object] = []
    if campaign_id:
        where.append(f"{spec['campaign_column']}=?")
        params.append(campaign_id)
    if ad_set_id:
        where.append(f"{spec['ad_set_column']}=?")
        params.append(ad_set_id)
    filter_fields = spec.get("filter_fields") or {}
    for row in filters or []:
        field_key = row.get("field")
        field_spec = filter_fields.get(field_key)  # type: ignore[union-attr]
        if field_spec is None:
            raise ValueError(f"Unknown filter field {field_key!r} for table {table!r}")
        clause_sql, clause_params = _build_filter_clause(field_spec, str(row.get("operator")), row.get("value"))
        where.append(clause_sql)
        params.extend(clause_params)
    search_term = (search or "").strip()
    if search_term:
        search_columns = list(spec.get("search_columns") or [])  # type: ignore[arg-type]
        if search_columns:
            # LOWER() on both sides, matching the text filters' idiom above -- SQLite's LIKE
            # only folds case for ASCII, and plenty of customer names here are Khmer script.
            like = f"%{_escape_like(search_term.lower())}%"
            where.append("(" + " OR ".join(f"LOWER({col}) LIKE ? ESCAPE '\\'" for col in search_columns) + ")")
            params.extend([like] * len(search_columns))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return clause, params


def get_dataset_rows(
    table: str, offset: int = 0, limit: int = 50,
    campaign_id: str | None = None, ad_set_id: str | None = None,
    filters: list[dict] | None = None,
    sort: str | None = None, direction: str = "asc", search: str | None = None,
) -> dict:
    """Paginated raw rows for one of the tables the Dataset page can browse.

    `sort` is a key into the table's `sort_fields` allowlist, and `direction` collapses to the
    literal "ASC"/"DESC" -- neither ever reaches the SQL as caller text. `search` is a free-text
    sweep across the table's `search_columns`, bound via `?` like any other value. See
    `_dataset_where` for how `filters`/`campaign_id`/`ad_set_id` are handled.
    """
    spec = DATASET_ROW_TABLES.get(table)
    if spec is None:
        raise ValueError(f"Unknown dataset table: {table!r}")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    clause, params = _dataset_where(table, spec, campaign_id, ad_set_id, filters, search)
    columns_sql = ", ".join(spec["columns"])  # type: ignore[arg-type]
    join_sql = str(spec.get("join") or "")

    # An unrecognised sort key falls back to the table's default order rather than raising --
    # a stale column key from a previously-open tab shouldn't blank the table.
    sort_fields: dict[str, str] = spec.get("sort_fields") or {}  # type: ignore[assignment]
    sort_column = sort_fields.get(sort or "")
    if sort_column:
        # `<col> IS NULL` first keeps blanks at the bottom in BOTH directions (SQLite ranks
        # NULL below everything, so a plain ASC would float every empty row to the top --
        # the opposite of what a spreadsheet user expects when sorting a sparse column).
        direction_sql = "DESC" if str(direction).lower() == "desc" else "ASC"
        order_sql = f"{sort_column} IS NULL, {sort_column} {direction_sql}"
    else:
        order_sql = str(spec["order_by"])

    with connect() as db:
        # The join is 1:1 (on daily_ad_set_aggregates' primary key, or on the leads table's
        # grouped-by-(ad_set_id, day) spend fallback), so it can't change the row count -- but
        # it's still included here since a filter/sort column may reference the joined alias.
        total = db.execute(f"SELECT COUNT(*) FROM {spec['table']} {join_sql} {clause}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT {columns_sql} FROM {spec['table']} {join_sql} {clause} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    result_rows = [dict(row) for row in rows]
    if table in _DECLARED_VAR_TABLES:
        _attach_declared_variables(result_rows)
    return {"rows": result_rows, "total": int(total), "offset": offset, "limit": limit}


# Hard ceiling on a "select all matching" bulk action -- protects the Delete/Export CSV bulk
# actions (each id then makes its own request or its own CSV row) from an unbounded scan if a
# filter is left too broad. The Dataset page surfaces `capped` so the user knows to narrow first.
SELECT_ALL_MATCHING_CAP = 20_000


def get_dataset_row_ids(
    table: str, campaign_id: str | None = None, ad_set_id: str | None = None,
    filters: list[dict] | None = None, search: str | None = None,
) -> dict:
    """Every row id matching the current filter/search, for the Dataset page's "select all N
    matching rows" action -- unlike `get_dataset_rows`, not capped to one page of 500."""
    spec = DATASET_ROW_TABLES.get(table)
    if spec is None:
        raise ValueError(f"Unknown dataset table: {table!r}")
    clause, params = _dataset_where(table, spec, campaign_id, ad_set_id, filters, search)
    join_sql = str(spec.get("join") or "")
    id_column = str(spec["columns"][0])  # every table's first declared column is its id
    with connect() as db:
        total = db.execute(f"SELECT COUNT(*) FROM {spec['table']} {join_sql} {clause}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT {id_column} FROM {spec['table']} {join_sql} {clause} "
            f"ORDER BY {spec['order_by']} LIMIT ?",
            [*params, SELECT_ALL_MATCHING_CAP],
        ).fetchall()
    ids = [str(row[0]) for row in rows]
    return {"ids": ids, "total": int(total), "capped": int(total) > len(ids)}


def calculate_forecast_metrics(
    actual: Iterable[float], predicted: Iterable[float], lower: Iterable[float] | None = None,
    upper: Iterable[float] | None = None, naive_scale: float = 1.0, backtest_windows: int = 1,
) -> dict:
    """Calculate out-of-sample forecast metrics. Bias is prediction minus actual."""
    actual_array = np.asarray(list(actual), dtype=float)
    predicted_array = np.nan_to_num(np.asarray(list(predicted), dtype=float), nan=0.0, posinf=1_000_000.0, neginf=0.0)
    predicted_array = np.clip(predicted_array, 0.0, 1_000_000.0)
    if len(actual_array) == 0 or len(actual_array) != len(predicted_array):
        return {"backtest_windows": int(backtest_windows), "mae": None, "rmse": None, "wape": None,
                "mase": None, "bias": None, "r2_out_of_sample": None, "interval_coverage": None,
                "average_interval_width": None, "selection_score": None}
    errors = predicted_array - actual_array
    absolute_errors = np.abs(errors)
    mae = float(np.mean(absolute_errors))
    rmse = float(np.sqrt(np.mean(errors**2)))
    wape = float(np.sum(absolute_errors) / max(float(np.sum(np.abs(actual_array))), 1.0))
    mase = float(mae / max(float(naive_scale), 1.0))
    bias = float(np.mean(errors))
    denominator = float(np.sum((actual_array - np.mean(actual_array)) ** 2))
    r2 = 1.0 if denominator <= 1e-12 and np.allclose(errors, 0) else (0.0 if denominator <= 1e-12 else 1.0 - float(np.sum(errors**2)) / denominator)
    lower_array = np.asarray(list(lower), dtype=float) if lower is not None else None
    upper_array = np.asarray(list(upper), dtype=float) if upper is not None else None
    if lower_array is not None and upper_array is not None and len(lower_array) == len(actual_array) and len(upper_array) == len(actual_array):
        coverage = float(np.mean((actual_array >= lower_array) & (actual_array <= upper_array)))
        average_width = float(np.mean(np.maximum(0.0, upper_array - lower_array)))
    else:
        coverage, average_width = None, None
    return {"backtest_windows": int(backtest_windows), "mae": mae, "rmse": rmse, "wape": wape,
            "mase": mase, "bias": bias, "r2_out_of_sample": r2, "interval_coverage": coverage,
            "average_interval_width": average_width, "selection_score": None}


def _peer_borrowing_weight(values: np.ndarray, context: dict) -> float:
    """How much of a sparse ad set's forecast may be borrowed from its campaign and portfolio.

    The fallback exists for an ad set that is running but has not accumulated its own track
    record yet, and for that case borrowing is right. It is wrong for an ad set that is not
    running: lending the campaign average to something with no delivery invents leads it has
    no mechanism to produce. An ad set carrying stale UTM tags can pick up a stray lead years
    after it stopped, and on a two-ad-set campaign with one busy sibling that turned 2
    lifetime leads into a 34-lead forecast.

    Spend is the test for whether it is running, so a live ad set still borrows freely while
    it ramps. Borrowing then tapers as the ad set builds its own history and needs the prior
    less. When no spend has been imported at all the test is meaningless, so the original
    behaviour stands rather than silently zeroing every sparse forecast.
    """
    portfolio_spend = np.asarray(context.get("portfolio_spend_values", []), dtype=float)
    if not portfolio_spend.size or not np.any(portfolio_spend > 0):
        return 1.0
    own_spend = np.asarray(context.get("spend_values", []), dtype=float)
    if float(np.sum(own_spend[-14:])) <= 0.0:
        return 0.0
    own_recent = float(np.sum(np.clip(values[-14:], 0.0, None))) if len(values) else 0.0
    return float(3.0 / (3.0 + own_recent))


def _fallback_forecast(values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None) -> list[float]:
    own_mean = float(np.mean(values[-7:])) if len(values) else 0.0
    if not context:
        return _recent_shape_forecast(values, dates, horizon, own_mean)
    campaign_values = np.asarray(context["campaign_values"], dtype=float)
    overall_values = np.asarray(context["overall_values"], dtype=float)
    campaign_sets = max(1, int(context["campaign_sets"]))
    all_sets = max(1, int(context["all_sets"]))
    campaign_mean = float(np.mean(campaign_values[-14:])) / campaign_sets
    overall_mean = float(np.mean(overall_values[-14:])) / all_sets
    borrowed = 0.55 * campaign_mean + 0.25 * overall_mean + 0.20 * own_mean
    borrow = _peer_borrowing_weight(values, context)
    fallback_daily = borrow * borrowed + (1.0 - borrow) * own_mean
    profile = calculate_weekday_profile(values, dates, campaign_values, overall_values, sparse=True)
    targets = pd.date_range(dates[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    return _shape_with_weekday_factors([max(0.0, fallback_daily)] * horizon, targets, profile)


def _clamped_parameter(parameters: dict | None, name: str, default: float, low: float = 0.0, high: float = 3.0) -> float:
    try:
        value = float((parameters or {}).get(name, default))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return float(np.clip(value, low, high))


def _forecast_parameter_set(parameters: dict | None = None) -> dict[str, float]:
    merged = dict(DEFAULT_FORECAST_PARAMETERS)
    if parameters:
        for name, default in DEFAULT_FORECAST_PARAMETERS.items():
            merged[name] = _clamped_parameter(parameters, name, default, 0.0, 1.0)
    signal_total = (
        merged["historical_signal_share"]
        + merged["spend_signal_share"]
        + merged["weekday_share"]
        + merged["error_share"]
    )
    if signal_total > 1e-12:
        for name in ("historical_signal_share", "spend_signal_share", "weekday_share", "error_share"):
            merged[name] = float(merged[name] / signal_total)
    merged["history_spend_share"] = float(
        merged["historical_signal_share"] + merged["spend_signal_share"]
    )
    merged["spend_elasticity"] = _clamped_parameter(parameters, "spend_elasticity", 0.65, 0.20, 1.0)
    return merged


def _parameters_for_formula_model(model: str, context: dict | None = None) -> dict[str, float]:
    for label, parameters in FORECAST_WEIGHT_CANDIDATES:
        if str(model).startswith(f"{SPEND_ADJUSTED_MODEL_PREFIX} {label}"):
            return _forecast_parameter_set(parameters)
    return _forecast_parameter_set((context or {}).get("parameters"))


def _is_spend_adjusted_model(model: str | None) -> bool:
    return bool(str(model or "").startswith(SPEND_ADJUSTED_MODEL_PREFIX))


def _formula_weight_label(model: str | None, parameters: dict | None = None) -> str:
    text = str(model or "")
    if text.startswith(f"{SPEND_ADJUSTED_MODEL_PREFIX} "):
        return text.removeprefix(f"{SPEND_ADJUSTED_MODEL_PREFIX} ").split(" lag", 1)[0]
    params = _forecast_parameter_set(parameters)
    return (
        f"{round(params['history_spend_share'] * 100):.0f}/"
        f"{round(params['weekday_share'] * 100):.0f}/"
        f"{round(params['error_share'] * 100):.0f}"
    )


def _spend_lag_for_model(model: str | None, context: dict | None = None) -> int:
    match = re.search(r"\blag([0-2])\b", str(model or ""))
    if match:
        return int(match.group(1))
    try:
        return int(np.clip(int((context or {}).get("signal_lag_days", 0)), 0, 2))
    except (TypeError, ValueError):
        return 0


def _future_target_dates(dates: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    return pd.date_range(dates[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")


def _aligned_performance_values(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, value_column: str,
    *, ad_set: str | None = None, campaign: str | None = None,
) -> np.ndarray:
    if spend_frame is None or spend_frame.empty:
        return np.zeros(len(dates), dtype=float)
    frame = spend_frame
    if ad_set is not None:
        frame = frame[frame["ad_set_id"].astype(str) == str(ad_set)]
    elif campaign is not None:
        if not str(campaign).strip():
            return np.zeros(len(dates), dtype=float)
        frame = frame[frame["campaign_id"].astype(str) == str(campaign)]
    if frame.empty:
        return np.zeros(len(dates), dtype=float)
    if value_column not in frame.columns:
        return np.zeros(len(dates), dtype=float)
    daily = frame.groupby("day")[value_column].sum()
    return daily.reindex(dates, fill_value=0).astype(float).to_numpy()


HOLIDAY_PROXIMITY_BUCKETS = ("during_holiday", "0_14_days", "15_30_days", "31_60_days")


@lru_cache(maxsize=1)
def _holiday_proximity_map() -> dict[str, str]:
    """date -> proximity bucket, from the Cambodia holiday calendar.

    Cambodia's calendar applies to every ad set: the audience is Khmer nationals and
    foreign residents physically in Cambodia, not the visa destination country.
    """
    path = DATA_DIR / "holiday_proximity.csv"
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except Exception:
        return {}
    if "date" not in frame.columns or "holiday_proximity" not in frame.columns:
        return {}
    dates = pd.to_datetime(frame["date"], errors="coerce")
    return {
        day.strftime("%Y-%m-%d"): str(bucket)
        for day, bucket in zip(dates, frame["holiday_proximity"])
        if pd.notna(day) and pd.notna(bucket)
    }


def _holiday_proximity_features(date: pd.Timestamp) -> dict[str, float]:
    """One indicator per proximity bucket. Known in advance for future dates, which is
    what makes the calendar useful for forecasting rather than only for backfitting."""
    bucket = _holiday_proximity_map().get(pd.Timestamp(date).strftime("%Y-%m-%d"), "")
    return {f"holiday_{name}": 1.0 if bucket == name else 0.0 for name in HOLIDAY_PROXIMITY_BUCKETS}


def _aligned_mean_performance_values(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, value_column: str,
    *, ad_set: str | None = None,
) -> np.ndarray:
    """Daily mean rather than sum - correct for ratio metrics such as frequency, where
    summing across ad sets would be meaningless."""
    if spend_frame is None or spend_frame.empty or value_column not in spend_frame.columns:
        return np.zeros(len(dates), dtype=float)
    frame = spend_frame
    if ad_set is not None:
        frame = frame[frame["ad_set_id"].astype(str) == str(ad_set)]
    if frame.empty:
        return np.zeros(len(dates), dtype=float)
    daily = frame.groupby("day")[value_column].mean()
    return daily.reindex(dates, fill_value=0).astype(float).to_numpy()


def _days_since_start_values(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, *, ad_set: str | None = None,
) -> np.ndarray:
    """Age of the ad set in days. Confirmed starts only (`ad_set_start_dates`) -- the
    left-censored earliest-upload-day fallback was found to produce wrong values and was
    removed 2026-08-06 (see Vault/Features/Ad-Decision-Engine.md). An ad set with no
    confirmed start date reports age 0 rather than an inferred one, until one is recorded
    via the "Start date" tab of the Ad set change popover."""
    if spend_frame is None or spend_frame.empty:
        return np.zeros(len(dates), dtype=float)
    frame = spend_frame
    if ad_set is not None:
        frame = frame[frame["ad_set_id"].astype(str) == str(ad_set)]
    if frame.empty:
        return np.zeros(len(dates), dtype=float)
    if "days_since_adset_started_imported" in frame.columns:
        imported = frame.dropna(subset=["days_since_adset_started_imported"]).copy()
        if not imported.empty:
            imported["_age"] = pd.to_numeric(imported["days_since_adset_started_imported"], errors="coerce")
            imported = imported.dropna(subset=["_age"])
            if not imported.empty:
                daily = imported.groupby("day")["_age"].mean()
                return daily.reindex(dates, fill_value=0).astype(float).to_numpy()
    confirmed = dict(_confirmed_ad_set_starts())
    if ad_set is None:
        # portfolio grain: mean age across the ad sets live that day with a confirmed start
        starts = frame["ad_set_id"].astype(str).map(confirmed)
        ages = frame.assign(_start=starts).dropna(subset=["_start"])
        if ages.empty:
            return np.zeros(len(dates), dtype=float)
        ages["_age"] = (pd.to_datetime(ages["day"]) - pd.to_datetime(ages["_start"])).dt.days
        daily = ages.groupby("day")["_age"].mean()
        return daily.reindex(dates, fill_value=0).astype(float).to_numpy()
    first_day = confirmed.get(str(ad_set))
    if first_day is None:
        return np.zeros(len(dates), dtype=float)
    return np.clip((dates - first_day).days.to_numpy().astype(float), 0.0, None)


def _imported_recency_feature_values(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, column: str, *, ad_set: str | None = None,
) -> np.ndarray | None:
    if spend_frame is None or spend_frame.empty or column not in spend_frame.columns:
        return None
    frame = spend_frame
    if ad_set is not None:
        frame = frame[frame["ad_set_id"].astype(str) == str(ad_set)]
    if frame.empty:
        return None
    imported = frame.loc[frame[column].fillna("").astype(str).str.strip().ne(""), ["day", column]].copy()
    if imported.empty:
        return None
    imported["_recency"] = imported[column].fillna("").astype(str).str.strip().map(RECENCY_BUCKET_FEATURE_VALUES)
    imported = imported.dropna(subset=["_recency"])
    if imported.empty:
        return None
    daily = imported.groupby("day")["_recency"].mean()
    return daily.reindex(dates, fill_value=0).astype(float).to_numpy()


def _ad_set_change_recency_values(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, *, ad_set: str | None = None,
) -> np.ndarray:
    """Superseded by _ad_set_change_features; kept as the recency-only view."""
    return _ad_set_change_features(spend_frame, dates, ad_set=ad_set)["ad_set_change_recency"]


def _ad_set_change_features(
    spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex, *, ad_set: str | None = None,
) -> dict[str, np.ndarray]:
    """Declared variable 7 (ad_set_change_recency).

    At ad-set grain this is that ad set's own day count. At portfolio grain it is the mean
    across the ad sets live that day, so campaign and portfolio scopes stay comparable.

    Confirmed change-log rows only -- the step-shift detector fallback (`_ad_set_change_
    events`) was found to produce wrong values and was removed 2026-08-06 (see Vault/
    Features/Ad-Decision-Engine.md). An ad set with no confirmed dates contributes no event
    (zero recency) rather than an inferred one, until real dates are recorded via the "Ad set
    change" tab of the popover or a confirmed change-log upload.

    Variable 9 (ad_set_change_type) was produced here too until 2026-08-11.
    """
    names = ["ad_set_change_recency"]
    empty = {name: np.zeros(len(dates), dtype=float) for name in names}
    if spend_frame is None or spend_frame.empty or "amount_spent_usd" not in spend_frame.columns:
        return empty
    imported = _imported_recency_feature_values(
        spend_frame, dates, "ad_set_change_recency_imported", ad_set=ad_set,
    )
    if imported is not None:
        return {"ad_set_change_recency": imported}
    frame = spend_frame
    if ad_set is not None:
        frame = frame[frame["ad_set_id"].astype(str) == str(ad_set)]
    if frame.empty:
        return empty
    totals = {name: np.zeros(len(dates), dtype=float) for name in names}
    live_counts = np.zeros(len(dates), dtype=float)
    for set_id, group in frame.groupby("ad_set_id"):
        events = _recorded_change_events("ad_set", str(set_id))
        if not events:
            continue
        live_days = set(pd.to_datetime(group["day"]).dt.normalize())
        live = np.asarray([1.0 if day.normalize() in live_days else 0.0 for day in dates], dtype=float)
        if not live.any():
            continue
        totals["ad_set_change_recency"] += _resolve_change_state(events, dates) * live
        live_counts += live
    divisor = np.where(live_counts > 0, live_counts, 1.0)
    return {name: values / divisor for name, values in totals.items()}


def _aligned_spend_values(spend_frame: pd.DataFrame | None, ad_set: str, dates: pd.DatetimeIndex) -> np.ndarray:
    return _aligned_performance_values(spend_frame, dates, "amount_spent_usd", ad_set=ad_set)


def _aligned_campaign_spend_values(spend_frame: pd.DataFrame | None, campaign: str, dates: pd.DatetimeIndex) -> np.ndarray:
    return _aligned_performance_values(spend_frame, dates, "amount_spent_usd", campaign=campaign)


def _aligned_portfolio_spend_values(spend_frame: pd.DataFrame | None, dates: pd.DatetimeIndex) -> np.ndarray:
    return _aligned_performance_values(spend_frame, dates, "amount_spent_usd")


def _lagged_signal(values: np.ndarray, lag_days: int) -> np.ndarray:
    clean = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    lag = max(0, min(int(lag_days), 2))
    if lag <= 0 or not len(clean):
        return clean
    return np.concatenate((np.zeros(lag, dtype=float), clean[:-lag]))


def _aggregate_spend_values(frame: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    if "spend_context_usd" not in frame.columns:
        return np.zeros(len(dates), dtype=float)
    daily = frame.groupby("aggregate_date")["spend_context_usd"].max()
    return daily.reindex(dates, fill_value=0).fillna(0).astype(float).to_numpy()


def _safe_recent_average(values: np.ndarray, window: int) -> float:
    clean = np.nan_to_num(np.asarray(values[-window:], dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    return float(np.mean(np.clip(clean, 0.0, None))) if len(clean) else 0.0


def _safe_ratio_or_none(numerator: float, denominator: float, min_denominator: float = 1.0) -> float | None:
    if numerator <= 0 or denominator < min_denominator:
        return None
    ratio = numerator / denominator
    return float(ratio) if math.isfinite(ratio) and ratio > 0 else None


def _weighted_available(values: list[tuple[float, float | None]]) -> float | None:
    available = [(weight, float(value)) for weight, value in values
                 if value is not None and math.isfinite(float(value)) and float(value) > 0]
    total_weight = sum(weight for weight, _ in available)
    if total_weight <= 0:
        return None
    return float(sum(weight * value for weight, value in available) / total_weight)


def _spend_formula_details(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
) -> dict:
    context = context or {}
    parameters = _forecast_parameter_set(context.get("parameters"))
    signal_lag_days = _spend_lag_for_model(context.get("model_used"), context)
    clean_values = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    raw_spend_values = np.clip(
        np.nan_to_num(np.asarray(context.get("spend_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    if len(raw_spend_values) != len(clean_values):
        raw_spend_values = (
            np.zeros(len(clean_values), dtype=float) if not len(raw_spend_values)
            else np.resize(raw_spend_values, len(clean_values))
        ) if len(clean_values) else np.asarray([], dtype=float)
    spend_values = _lagged_signal(raw_spend_values, signal_lag_days)
    raw_conversation_values = np.clip(
        np.nan_to_num(np.asarray(context.get("conversation_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    if len(raw_conversation_values) != len(clean_values):
        raw_conversation_values = (
            np.zeros(len(clean_values), dtype=float) if not len(raw_conversation_values)
            else np.resize(raw_conversation_values, len(clean_values))
        ) if len(clean_values) else np.asarray([], dtype=float)
    conversation_values = _lagged_signal(raw_conversation_values, signal_lag_days)
    campaign_values = np.clip(
        np.nan_to_num(np.asarray(context.get("campaign_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    overall_values = np.clip(
        np.nan_to_num(np.asarray(context.get("overall_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    campaign_spend_values = np.clip(
        np.nan_to_num(np.asarray(context.get("campaign_spend_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    portfolio_spend_values = np.clip(
        np.nan_to_num(np.asarray(context.get("portfolio_spend_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    campaign_conversation_values = np.clip(
        np.nan_to_num(np.asarray(context.get("campaign_conversation_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    portfolio_conversation_values = np.clip(
        np.nan_to_num(np.asarray(context.get("portfolio_conversation_values", np.zeros(len(clean_values))), dtype=float),
                      nan=0.0, posinf=0.0, neginf=0.0),
        0.0, None,
    )
    for name, array in (
        ("campaign_values", campaign_values),
        ("overall_values", overall_values),
        ("campaign_spend_values", campaign_spend_values),
        ("portfolio_spend_values", portfolio_spend_values),
        ("campaign_conversation_values", campaign_conversation_values),
        ("portfolio_conversation_values", portfolio_conversation_values),
    ):
        if len(array) != len(clean_values):
            resized = (
                np.zeros(len(clean_values), dtype=float) if not len(array)
                else np.resize(array, len(clean_values))
            ) if len(clean_values) else np.asarray([], dtype=float)
            if name == "campaign_values":
                campaign_values = resized
            elif name == "overall_values":
                overall_values = resized
            elif name == "campaign_spend_values":
                campaign_spend_values = resized
            elif name == "portfolio_spend_values":
                portfolio_spend_values = resized
            elif name == "campaign_conversation_values":
                campaign_conversation_values = resized
            else:
                portfolio_conversation_values = resized
    campaign_spend_values = _lagged_signal(campaign_spend_values, signal_lag_days)
    portfolio_spend_values = _lagged_signal(portfolio_spend_values, signal_lag_days)
    campaign_conversation_values = _lagged_signal(campaign_conversation_values, signal_lag_days)
    portfolio_conversation_values = _lagged_signal(portfolio_conversation_values, signal_lag_days)

    last3 = _safe_recent_average(clean_values, 3)
    last7 = _safe_recent_average(clean_values, 7)
    last14 = _safe_recent_average(clean_values, 14)
    all_history = float(np.mean(clean_values)) if len(clean_values) else 0.0
    previous7 = _safe_recent_average(clean_values[:-3], 7) if len(clean_values) > 3 else last7
    momentum = last3 / previous7 if previous7 > 0 else 1.0
    momentum = float(np.clip(momentum, 0.70, 1.35))
    baseline_daily = max(0.0, 0.50 * last7 + 0.30 * last14 + 0.20 * all_history)
    history_daily = max(0.0, baseline_daily * momentum)

    recent_window = 28 if len(clean_values) >= 28 else 14
    recent_spend = spend_values[-recent_window:] if len(spend_values) else np.asarray([], dtype=float)
    recent_leads = clean_values[-recent_window:] if len(clean_values) else np.asarray([], dtype=float)
    recent_campaign_spend = campaign_spend_values[-recent_window:] if len(campaign_spend_values) else np.asarray([], dtype=float)
    recent_campaign_leads = campaign_values[-recent_window:] if len(campaign_values) else np.asarray([], dtype=float)
    recent_portfolio_spend = portfolio_spend_values[-recent_window:] if len(portfolio_spend_values) else np.asarray([], dtype=float)
    recent_portfolio_leads = overall_values[-recent_window:] if len(overall_values) else np.asarray([], dtype=float)
    recent_conversations = conversation_values[-recent_window:] if len(conversation_values) else np.asarray([], dtype=float)
    recent_campaign_conversations = campaign_conversation_values[-recent_window:] if len(campaign_conversation_values) else np.asarray([], dtype=float)
    recent_portfolio_conversations = portfolio_conversation_values[-recent_window:] if len(portfolio_conversation_values) else np.asarray([], dtype=float)
    last7_spend_daily = _safe_recent_average(raw_spend_values, 7)
    last7_conversation_daily = _safe_recent_average(raw_conversation_values, 7)
    spend_total = float(np.sum(recent_spend))
    if last7_spend_daily <= 0 and spend_total > 0:
        last7_spend_daily = spend_total / max(1, min(recent_window, len(recent_spend)))
    # When the user has entered a dated budget covering the current date, anchor the
    # spend ratio to that stated budget instead of the last-7-day actual-spend average.
    spend_anchor = last7_spend_daily
    budget_override = context.get("current_budget_override")
    if budget_override is not None:
        try:
            override_val = float(budget_override)
            if math.isfinite(override_val) and override_val > 0:
                spend_anchor = override_val
        except (TypeError, ValueError):
            pass
    future_spend_daily = context.get("future_spend_daily")
    try:
        future_spend_daily = float(future_spend_daily) if future_spend_daily is not None else spend_anchor
    except (TypeError, ValueError):
        future_spend_daily = spend_anchor
    if not math.isfinite(future_spend_daily):
        future_spend_daily = spend_anchor
    future_spend_daily = max(0.0, future_spend_daily)

    lead_total = float(np.sum(recent_leads))
    campaign_lead_total = float(np.sum(recent_campaign_leads))
    portfolio_lead_total = float(np.sum(recent_portfolio_leads))
    ad_set_cpl = _safe_ratio_or_none(spend_total, lead_total, 3.0)
    campaign_cpl = _safe_ratio_or_none(float(np.sum(recent_campaign_spend)), campaign_lead_total, 8.0)
    portfolio_cpl = _safe_ratio_or_none(float(np.sum(recent_portfolio_spend)), portfolio_lead_total, 20.0)
    cpl_weights = [(0.60, ad_set_cpl), (0.25, campaign_cpl), (0.15, portfolio_cpl)] if lead_total >= 15 else [
        (0.35, ad_set_cpl), (0.45, campaign_cpl), (0.20, portfolio_cpl)
    ]
    smoothed_cpl = _weighted_available(cpl_weights)
    spend_available = bool(smoothed_cpl and last7_spend_daily > 0)
    spend_daily_raw = future_spend_daily / smoothed_cpl if spend_available else None
    if spend_available and spend_anchor > 0:
        spend_ratio = float(np.clip(future_spend_daily / spend_anchor, 0.0, 3.0))
        elastic_response = history_daily * spend_ratio ** parameters["spend_elasticity"]
        spend_daily = 0.60 * elastic_response + 0.40 * float(spend_daily_raw)
    else:
        spend_ratio = 1.0
        spend_daily = history_daily

    ad_set_leads_per_conversation = _safe_ratio_or_none(
        lead_total, float(np.sum(recent_conversations)), 3.0
    )
    campaign_leads_per_conversation = _safe_ratio_or_none(
        campaign_lead_total, float(np.sum(recent_campaign_conversations)), 8.0
    )
    portfolio_leads_per_conversation = _safe_ratio_or_none(
        portfolio_lead_total, float(np.sum(recent_portfolio_conversations)), 20.0
    )
    conversation_rate = _weighted_available([
        (0.60, ad_set_leads_per_conversation),
        (0.25, campaign_leads_per_conversation),
        (0.15, portfolio_leads_per_conversation),
    ])
    conversation_available = bool(conversation_rate and last7_conversation_daily > 0)
    conversation_daily = (
        max(0.0, last7_conversation_daily * float(conversation_rate))
        if conversation_available else history_daily
    )
    if spend_available and conversation_available:
        performance_daily = 0.45 * spend_daily + 0.55 * conversation_daily
    elif spend_available:
        performance_daily = spend_daily
    elif conversation_available:
        performance_daily = conversation_daily
    else:
        performance_daily = history_daily
    performance_signal_available = spend_available or conversation_available
    history_spend_component = (
        max(0.0, 0.60 * history_daily + 0.40 * performance_daily)
        if performance_signal_available else history_daily
    )
    targets = _future_target_dates(dates, horizon)
    profile = calculate_weekday_profile(
        clean_values, dates,
        campaign_values,
        overall_values,
        bool(context.get("sparse", False)),
    )
    factors = profile.get("factors") or [1.0] * 7
    weekday_factors = [float(np.clip(float(factors[date.weekday()]), 0.70, 1.30)) for date in targets]
    bias_adjustment = float(context.get("bias_adjustment") or 0.0)
    bias_adjustment = float(np.clip(
        bias_adjustment,
        -0.30 * max(history_spend_component, 1.0),
        0.30 * max(history_spend_component, 1.0),
    ))
    predictions = []
    for weekday_factor in weekday_factors:
        historical_signal = max(0.0, history_daily)
        spend_signal = max(0.0, performance_daily if performance_signal_available else history_daily)
        weekday_adjusted = max(0.0, historical_signal * weekday_factor)
        error_adjusted = max(0.0, historical_signal + bias_adjustment)
        prediction = (
            parameters["historical_signal_share"] * historical_signal
            + parameters["spend_signal_share"] * spend_signal
            + parameters["weekday_share"] * weekday_adjusted
            + parameters["error_share"] * error_adjusted
        )
        predictions.append(max(0.0, prediction))
    predictions = _cap_forecast_values(predictions, clean_values)

    return {
        "predictions": predictions,
        "parameters": parameters,
        "history_daily": history_daily,
        "baseline_daily": baseline_daily,
        "momentum": momentum,
        "spend_daily": spend_daily,
        "conversation_daily": conversation_daily,
        "performance_daily": performance_daily,
        "base_daily": history_spend_component,
        "history_spend_component": history_spend_component,
        "future_spend_daily": future_spend_daily,
        "last7_spend_daily": last7_spend_daily,
        "spend_ratio": spend_ratio,
        "spend_elasticity": parameters["spend_elasticity"],
        "recent_cpl": ad_set_cpl,
        "smoothed_cpl": smoothed_cpl,
        "campaign_cpl": campaign_cpl,
        "portfolio_cpl": portfolio_cpl,
        "spend_available": spend_available,
        "conversation_available": conversation_available,
        "performance_signal_available": performance_signal_available,
        "last7_conversation_daily": last7_conversation_daily,
        "leads_per_conversation": conversation_rate,
        "signal_lag_days": signal_lag_days,
        "last7_leads": float(np.sum(clean_values[-7:])) if len(clean_values) else 0.0,
        "last14_leads": float(np.sum(clean_values[-14:])) if len(clean_values) else 0.0,
        "last14_spend": spend_total,
        "bias_adjustment": bias_adjustment,
        "weekday_profile": profile,
        "formula_weights": {
            "history_spend": parameters["history_spend_share"],
            "historical_signal": parameters["historical_signal_share"],
            "spend_signal": parameters["spend_signal_share"],
            "weekday": parameters["weekday_share"],
            "error": parameters["error_share"],
        },
    }


def _spend_adjusted_formula_forecast(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
) -> list[float]:
    return _spend_formula_details(values, dates, horizon, context)["predictions"]


def _change_point_details(values: np.ndarray) -> dict:
    clean = np.clip(np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    recent3 = _safe_recent_average(clean, 3)
    recent7 = _safe_recent_average(clean, 7)
    recent14 = _safe_recent_average(clean, 14)
    prior = clean[-17:-3] if len(clean) >= 17 else clean[:-3]
    prior14 = float(np.mean(prior[-14:])) if len(prior) else recent14
    ratio = (recent3 + 0.5) / (prior14 + 0.5)
    high_days = int(np.sum(clean[-3:] >= max(2.0, prior14 * 1.45))) if len(clean) >= 3 else 0
    low_days = int(np.sum(clean[-3:] <= prior14 * 0.60)) if len(clean) >= 3 else 0
    upward = bool(len(clean) >= 14 and ratio >= 1.65 and high_days >= 2 and np.sum(clean[-3:]) >= 6)
    downward = bool(len(clean) >= 14 and prior14 >= 2.0 and ratio <= 0.58 and low_days >= 2)
    detected = upward or downward
    if detected:
        target_daily = 0.60 * recent3 + 0.25 * recent7 + 0.15 * recent14
    else:
        target_daily = 0.35 * recent3 + 0.40 * recent7 + 0.25 * recent14
    strength = min(1.0, abs(math.log(max(ratio, 1e-6))) / math.log(3.0))
    return {
        "detected": detected,
        "direction": "up" if upward else "down" if downward else "stable",
        "recent3": recent3,
        "recent7": recent7,
        "prior14": prior14,
        "ratio": ratio,
        "strength": strength,
        "target_daily": max(0.0, target_daily),
    }


def _change_point_forecast(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
) -> list[float]:
    details = _change_point_details(values)
    profile = calculate_weekday_profile(
        values, dates,
        (context or {}).get("campaign_values"),
        (context or {}).get("overall_values"),
        bool((context or {}).get("sparse", False)),
    )
    restrained_profile = {
        **profile,
        "factors": [float(np.clip(factor, 0.65, 1.40)) for factor in profile.get("factors", [1.0] * 7)],
    }
    targets = _future_target_dates(dates, horizon)
    base = [details["target_daily"]] * horizon
    return _cap_forecast_values(_shape_with_weekday_factors(base, targets, restrained_profile), values)


def _damped_trend_forecast(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
) -> list[float]:
    # Holt's linear method with damping: level-based candidates cannot follow a
    # sustained ramp, so this one carries a decaying share of the recent slope.
    if len(values) < 10:
        base = float(np.mean(values[-7:])) if len(values) else 0.0
        predictions = [max(0.0, base)] * horizon
    else:
        alpha, beta, phi = 0.35, 0.25, 0.85
        level, trend = float(values[0]), 0.0
        for value in map(float, values[1:]):
            previous = level
            level = alpha * value + (1 - alpha) * (level + phi * trend)
            trend = beta * (level - previous) + (1 - beta) * phi * trend
        predictions = []
        damp_sum = 0.0
        for step in range(1, horizon + 1):
            damp_sum += phi ** step
            predictions.append(max(0.0, level + damp_sum * trend))
    profile = calculate_weekday_profile(
        values, dates,
        (context or {}).get("campaign_values"),
        (context or {}).get("overall_values"),
        bool((context or {}).get("sparse", False)),
    )
    targets = _future_target_dates(dates, horizon)
    return _cap_forecast_values(_shape_with_weekday_factors(predictions, targets, profile), values)


def _ensemble_forecast(
    values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None,
) -> list[float]:
    context = context or {}
    components = context.get("ensemble_components")
    if not components:
        components = [
            {"model": BREAKOUT_MODEL_NAME, "weight": 0.40},
            {"model": f"{SPEND_ADJUSTED_MODEL_PREFIX} 65/20/15 lag0", "weight": 0.35},
            {"model": "weekday-shaped rolling forecast", "weight": 0.15},
            {"model": "Log-link count regression", "weight": 0.10},
        ]
    predictions: list[tuple[float, np.ndarray]] = []
    for component in components:
        model = str(component.get("model") or "")
        weight = max(0.0, float(component.get("weight") or 0.0))
        if not model or model == ENSEMBLE_MODEL_NAME or weight <= 0:
            continue
        try:
            output = np.asarray(_forecast_candidate(model, values, dates, horizon, context), dtype=float)
        except Exception:
            continue
        if len(output) == horizon:
            predictions.append((weight, output))
    total_weight = sum(weight for weight, _ in predictions)
    if total_weight <= 0:
        return _change_point_forecast(values, dates, horizon, context)
    blended = sum(weight * output for weight, output in predictions) / total_weight
    # Averaging components with different daily shapes cancels their variance and
    # produces a flat line even when every component is shaped. Restore the
    # weighted component variance so the ensemble keeps a realistic daily profile.
    target_std = sum(weight * float(np.std(output)) for weight, output in predictions) / total_weight
    blended_std = float(np.std(blended))
    if target_std > 1e-9:
        if blended_std <= 1e-9:
            profile = calculate_weekday_profile(
                values, dates, context.get("campaign_values"),
                context.get("overall_values"), bool(context.get("sparse", False)),
            )
            targets = _future_target_dates(dates, horizon)
            blended = np.asarray(_shape_with_weekday_factors(blended.tolist(), targets, profile), dtype=float)
            blended_std = float(np.std(blended))
        if 1e-9 < blended_std < target_std:
            center = float(np.mean(blended))
            blended = np.clip(center + (blended - center) * min(target_std / blended_std, 2.0), 0.0, None)
    return _cap_forecast_values(blended.tolist(), values)


def _forecast_candidate(model: str, values: np.ndarray, dates: pd.DatetimeIndex, horizon: int, context: dict | None = None) -> list[float]:
    if _is_spend_adjusted_model(model):
        formula_context = dict(context or {})
        formula_context["parameters"] = _parameters_for_formula_model(model, context)
        formula_context["model_used"] = model
        formula_context["signal_lag_days"] = _spend_lag_for_model(model, context)
        return _spend_adjusted_formula_forecast(values, dates, horizon, formula_context)
    if model == ENSEMBLE_MODEL_NAME:
        return _ensemble_forecast(values, dates, horizon, context)
    if model == BREAKOUT_MODEL_NAME:
        return _change_point_forecast(values, dates, horizon, context)
    if model == TREND_MODEL_NAME:
        return _damped_trend_forecast(values, dates, horizon, context)
    if model == "Log-link count regression":
        return _cap_forecast_values(_poisson_forecast(values.tolist(), list(dates), horizon), values)
    if model == OLS_SPEND_MODEL_NAME:
        return _ols_forecast(values, dates, horizon, context, multivariate=False)
    if model == OLS_MULTIVARIATE_MODEL_NAME:
        return _ols_forecast(values, dates, horizon, context, multivariate=True)
    if model == "weekday-aware average":
        return _cap_forecast_values(
            [_weekday_prediction(values, dates, dates[-1] + pd.Timedelta(days=step)) for step in range(1, horizon + 1)],
            values,
        )
    if model == "campaign + overall fallback":
        return _cap_forecast_values(_fallback_forecast(values, dates, horizon, context), values)
    rolling = float(np.mean(values[-7:])) if len(values) else 0.0
    if model == "weekday-shaped rolling forecast":
        profile = calculate_weekday_profile(
            values, dates,
            context.get("campaign_values") if context else None,
            context.get("overall_values") if context else None,
            bool(context.get("sparse")) if context else False,
        )
        targets = pd.date_range(dates[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
        return _cap_forecast_values(_shape_with_weekday_factors([max(0.0, rolling)] * horizon, targets, profile), values)
    return _cap_forecast_values([max(0.0, rolling)] * horizon, values)


def _base_intervals(predictions: list[float], training_values: np.ndarray, sparse: bool) -> tuple[list[float], list[float]]:
    recent = training_values[-14:] if len(training_values) else np.asarray([0.0])
    residual_std = max(0.5, float(np.std(recent - np.mean(recent))))
    multiplier = 1.8 if sparse else 1.28
    half_widths = [multiplier * math.sqrt(max(value, 0.0) + residual_std**2) for value in predictions]
    return ([max(0.0, value - width) for value, width in zip(predictions, half_widths)],
            [value + width for value, width in zip(predictions, half_widths)])


def _normalize(values: dict[str, float | None]) -> dict[str, float]:
    finite = [float(value) for value in values.values() if value is not None and math.isfinite(float(value))]
    if not finite:
        return {name: 1.0 for name in values}
    low, high = min(finite), max(finite)
    if high - low <= 1e-12:
        return {name: 0.0 if value is not None else 1.0 for name, value in values.items()}
    return {name: (float(value) - low) / (high - low) if value is not None else 1.0 for name, value in values.items()}


def _recency_bucket_weights(target_dates: list[pd.Timestamp]) -> np.ndarray:
    if not target_dates:
        return np.asarray([], dtype=float)
    parsed = pd.DatetimeIndex(target_dates)
    ages = np.asarray([(parsed.max() - date).days for date in parsed], dtype=int)
    buckets = ((ages <= 13, 0.50), ((ages >= 14) & (ages <= 27), 0.30), (ages >= 28, 0.20))
    weights = np.zeros(len(parsed), dtype=float)
    available_share = 0.0
    for mask, share in buckets:
        count = int(np.sum(mask))
        if count:
            weights[mask] = share / count
            available_share += share
    if available_share <= 0:
        return np.full(len(parsed), 1.0 / len(parsed), dtype=float)
    return weights / available_share


def _recency_weighted_metrics(
    actual: list[float], predicted: list[float], lower: list[float], upper: list[float],
    target_dates: list[pd.Timestamp],
) -> dict[str, float | None]:
    if not actual or len(actual) != len(predicted) or len(actual) != len(target_dates):
        return {
            "recency_weighted_mae": None, "recency_weighted_rmse": None,
            "recency_weighted_wape": None, "recency_weighted_bias": None,
            "recency_weighted_coverage": None, "recency_weighted_interval_width": None,
        }
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    errors = predicted_array - actual_array
    weights = _recency_bucket_weights(target_dates)
    weighted_actual = float(np.sum(weights * np.abs(actual_array)))
    result = {
        "recency_weighted_mae": float(np.sum(weights * np.abs(errors))),
        "recency_weighted_rmse": float(np.sqrt(np.sum(weights * errors**2))),
        "recency_weighted_wape": float(np.sum(weights * np.abs(errors)) / max(weighted_actual, 1.0)),
        "recency_weighted_bias": float(np.sum(weights * errors)),
        "recency_weighted_coverage": None,
        "recency_weighted_interval_width": None,
    }
    if len(lower) == len(actual) and len(upper) == len(actual):
        lower_array = np.asarray(lower, dtype=float)
        upper_array = np.asarray(upper, dtype=float)
        result["recency_weighted_coverage"] = float(
            np.sum(weights * ((actual_array >= lower_array) & (actual_array <= upper_array)))
        )
        result["recency_weighted_interval_width"] = float(
            np.sum(weights * np.maximum(0.0, upper_array - lower_array))
        )
    return result


def _model_family(model: str) -> str:
    if _is_spend_adjusted_model(model):
        return "spend"
    if model in (OLS_SPEND_MODEL_NAME, OLS_MULTIVARIATE_MODEL_NAME):
        return "ols"
    if model in (BREAKOUT_MODEL_NAME, TREND_MODEL_NAME):
        return "recent"
    if "weekday" in model:
        return "weekday"
    return "other"


def _learned_ensemble_components(
    models: list[str], records: dict[str, dict], training_values: np.ndarray,
) -> list[dict]:
    by_family: dict[str, list[str]] = {"recent": [], "spend": [], "weekday": [], "ols": [], "other": []}
    for model in models:
        by_family[_model_family(model)].append(model)
    preferred = {
        "recent": BREAKOUT_MODEL_NAME,
        "spend": f"{SPEND_ADJUSTED_MODEL_PREFIX} 65/20/15 lag0",
        "weekday": "weekday-shaped rolling forecast",
        "ols": OLS_MULTIVARIATE_MODEL_NAME,
        "other": "Log-link count regression",
    }
    selected: dict[str, str] = {}
    model_mae: dict[str, float | None] = {}
    for family, candidates in by_family.items():
        if not candidates:
            continue
        for model in candidates:
            errors = records.get(model, {}).get("daily_abs_errors", [])
            target_dates = records.get(model, {}).get("target_dates", [])
            if errors and len(errors) == len(target_dates):
                model_mae[model] = float(
                    np.sum(_recency_bucket_weights(target_dates) * np.asarray(errors, dtype=float))
                )
            else:
                model_mae[model] = float(np.mean(errors)) if errors else None
        measured = [model for model in candidates if model_mae[model] is not None]
        selected[family] = (
            min(measured, key=lambda model: float(model_mae[model]))
            if measured else preferred.get(family, candidates[0])
        )
        if selected[family] not in candidates:
            selected[family] = candidates[0]

    change = _change_point_details(training_values)
    priors = (
        {"recent": 0.50, "spend": 0.30, "weekday": 0.12, "other": 0.08}
        if change["detected"] else
        {"recent": 0.32, "spend": 0.28, "weekday": 0.18, "ols": 0.12, "other": 0.10}
    )
    reliabilities = {
        family: 1.0 / (float(model_mae[model]) + 1.0)
        for family, model in selected.items()
        if model_mae.get(model) is not None
    }
    max_reliability = max(reliabilities.values(), default=1.0)
    raw_weights = {}
    for family in selected:
        reliability_factor = (
            0.50 + 0.50 * reliabilities[family] / max_reliability
            if family in reliabilities else 0.75
        )
        raw_weights[family] = priors.get(family, 0.10) * reliability_factor
    total = sum(raw_weights.values()) or 1.0
    return [
        {"model": selected[family], "weight": raw_weights[family] / total}
        for family in ("recent", "spend", "weekday", "ols", "other")
        if family in selected
    ]


def _rolling_origin_backtest(values: np.ndarray, dates: pd.DatetimeIndex, sparse: bool, context: dict) -> dict[int, list[dict]]:
    spend_available = bool(np.any(np.asarray(context.get("spend_values", []), dtype=float)))
    # Gate on the declared variables the multivariate model actually fits. This used to test
    # Meta's funnel metrics (conversations, impressions, link clicks, platform leads), which
    # the model stopped using when it was cut back to the declared set - so it was being
    # offered and withheld on the strength of columns it no longer reads.
    multivariate_available = any(
        np.any(np.asarray(context.get(name, []), dtype=float))
        for name in ("frequency_values", "days_since_start_values",
                     "ad_set_change_recency_values", "ad_change_recency_values")
    )
    models = (["campaign + overall fallback", BREAKOUT_MODEL_NAME, *SPEND_ADJUSTED_MODEL_NAMES] if sparse else
              [BREAKOUT_MODEL_NAME, TREND_MODEL_NAME, "7-day rolling average", "weekday-aware average",
               "weekday-shaped rolling forecast", "Log-link count regression", *SPEND_ADJUSTED_MODEL_NAMES])
    if not sparse and spend_available:
        models.append(OLS_SPEND_MODEL_NAME)
    if not sparse and (spend_available or multivariate_available):
        models.append(OLS_MULTIVARIATE_MODEL_NAME)
    full_profile = calculate_weekday_profile(values, dates, context.get("campaign_values"),
                                             context.get("overall_values"), sparse)
    evaluated: dict[int, list[dict]] = {}
    for horizon in (7, 14):
        final_cutoff = len(values) - horizon
        cutoffs = list(range(14, final_cutoff + 1, 7)) if final_cutoff >= 14 else []
        if cutoffs and cutoffs[-1] != final_cutoff:
            cutoffs.append(final_cutoff)
        all_models = [*models, ENSEMBLE_MODEL_NAME]
        records: dict[str, dict] = {model: {
            "actual": [], "predicted": [], "lower": [], "upper": [],
            "naive_scales": [], "daily_abs_errors": [],
            "window_total_errors": [], "target_dates": [], "windows": 0,
            "window_variance_ratios": [],
        } for model in all_models}
        for cutoff in cutoffs:
            training_values, training_dates = values[:cutoff], dates[:cutoff]
            actual = values[cutoff:cutoff + horizon]
            cutoff_context = {**context, "campaign_values": context["campaign_values"][:cutoff],
                              "overall_values": context["overall_values"][:cutoff], "sparse": sparse}
            cutoff_context.pop("future_spend_daily", None)
            for spend_key in (
                "spend_values", "campaign_spend_values", "portfolio_spend_values",
                "conversation_values", "campaign_conversation_values", "portfolio_conversation_values",
                "platform_leads_values", "link_click_values", "impression_values",
                "frequency_values", "days_since_start_values", "ad_set_change_recency_values",
                "ad_change_recency_values",
            ):
                if spend_key in context:
                    cutoff_context[spend_key] = context[spend_key][:cutoff]
            naive_errors = np.abs(training_values[7:] - training_values[:-7]) if len(training_values) > 7 else np.abs(np.diff(training_values))
            naive_scale = float(np.mean(naive_errors)) if len(naive_errors) else 1.0
            window_predictions: dict[str, list[float]] = {}
            for model in models:
                try:
                    window_predictions[model] = _forecast_candidate(
                        model, training_values, training_dates, horizon, cutoff_context
                    )
                except Exception:
                    continue
            ensemble_components = _learned_ensemble_components(models, records, training_values)
            ensemble_context = {**cutoff_context, "ensemble_components": ensemble_components}
            window_predictions[ENSEMBLE_MODEL_NAME] = _ensemble_forecast(
                training_values, training_dates, horizon, ensemble_context
            )
            for model, predicted in window_predictions.items():
                lower, upper = _base_intervals(predicted, training_values, sparse)
                record = records[model]
                record["actual"].extend(actual.tolist()); record["predicted"].extend(predicted)
                record["lower"].extend(lower); record["upper"].extend(upper)
                record["naive_scales"].append(naive_scale)
                record["daily_abs_errors"].extend(np.abs(np.asarray(predicted) - actual).tolist())
                record["window_total_errors"].append(abs(float(sum(predicted)) - float(sum(actual))))
                record["target_dates"].extend(dates[cutoff:cutoff + horizon].tolist())
                record["windows"] += 1
                # Day-to-day shape inside this one horizon, which is what the chart draws.
                # Measured per window on purpose: pooling every window first lets the level
                # differences between windows stand in for shape, so a forecast that is dead
                # flat across all 14 days still scores as if it had structure.
                record["window_variance_ratios"].append(
                    float(np.std(np.asarray(predicted, dtype=float))) / max(float(np.std(actual)), 0.5)
                )
        metrics = []
        final_ensemble_components = _learned_ensemble_components(models, records, values)
        for model, record in records.items():
            metric = calculate_forecast_metrics(
                record["actual"], record["predicted"], record["lower"], record["upper"],
                float(np.mean(record["naive_scales"])) if record["naive_scales"] else 1.0, record["windows"],
            )
            target_weekdays = np.asarray([pd.Timestamp(date).weekday() for date in record["target_dates"]])
            actual_array = np.asarray(record["actual"], dtype=float)
            predicted_array = np.asarray(record["predicted"], dtype=float)
            def grouped_error(mask: np.ndarray) -> tuple[float | None, float | None]:
                if not len(actual_array) or not np.any(mask):
                    return None, None
                errors = predicted_array[mask] - actual_array[mask]
                wape = float(np.sum(np.abs(errors)) / max(float(np.sum(np.abs(actual_array[mask]))), 1.0))
                return wape, float(np.mean(errors))
            weekday_wape, weekday_bias = grouped_error(target_weekdays < 5)
            weekend_wape, weekend_bias = grouped_error(target_weekdays >= 5)
            metric.update({"weekday_wape": weekday_wape, "weekend_wape": weekend_wape,
                           "weekday_bias": weekday_bias, "weekend_bias": weekend_bias,
                           "weekday_seasonality_strength": full_profile["seasonality_strength"]})
            metric.update(_recency_weighted_metrics(
                record["actual"], record["predicted"], record["lower"], record["upper"],
                record["target_dates"],
            ))
            metric.update({"model_used": model, "horizon_days": horizon,
                           "_daily_abs_errors": record["daily_abs_errors"],
                           "_window_total_errors": record["window_total_errors"],
                           "_actual_mean": float(np.mean(record["actual"])) if record["actual"] else 0.0})
            if model == ENSEMBLE_MODEL_NAME:
                metric["_ensemble_components"] = final_ensemble_components
            actual_std = float(np.std(actual_array)) if len(actual_array) else 0.0
            window_ratios = record["window_variance_ratios"]
            variance_ratio = float(np.mean(window_ratios)) if window_ratios else 0.0
            flatness_penalty = float((0.60 - variance_ratio) / 0.60) if variance_ratio < 0.60 and actual_std >= 0.5 else 0.0
            metric.update({"forecast_variance_ratio": variance_ratio, "flatness_penalty": max(0.0, flatness_penalty)})
            metrics.append(metric)
        norm_mae = _normalize({item["model_used"]: item["recency_weighted_mae"] for item in metrics})
        norm_wape = _normalize({item["model_used"]: item["recency_weighted_wape"] for item in metrics})
        norm_rmse = _normalize({item["model_used"]: item["recency_weighted_rmse"] for item in metrics})
        for item in metrics:
            name = item["model_used"]
            if not item["backtest_windows"]:
                item["selection_score"] = 1.0
                continue
            mean_actual = item["_actual_mean"]
            bias_penalty = min(
                1.0, abs(float(item["recency_weighted_bias"] or 0.0)) / (mean_actual + 1.0)
            )
            coverage = float(item["recency_weighted_coverage"] or 0.0)
            width = float(item["recency_weighted_interval_width"] or 0.0)
            interval_penalty = min(1.0, abs(coverage - 0.80) / 0.80 + 0.25 * width / (mean_actual + 1.0))
            flatness_penalty = float(item.get("flatness_penalty") or 0.0)
            item["selection_score"] = float(0.35 * norm_mae[name] + 0.25 * norm_wape[name]
                                            + 0.15 * norm_rmse[name] + 0.15 * bias_penalty
                                            + 0.10 * interval_penalty
                                            + FLATNESS_SELECTION_WEIGHT * flatness_penalty)
        evaluated[horizon] = metrics
    return evaluated


def _selected_metric(metrics: list[dict]) -> dict:
    def score_key(item: dict) -> tuple[float, float]:
        return (float(item["selection_score"]) if item.get("selection_score") is not None else 1.0,
                float(item["mae"]) if item.get("mae") is not None else 1e12)

    best = min(metrics, key=score_key)
    ensemble = [
        item for item in metrics
        if item.get("model_used") == ENSEMBLE_MODEL_NAME and int(item.get("backtest_windows") or 0) > 0
    ]
    # Prefer the ensemble for stability, but only while it stays competitive with
    # the best individual model; a clearly better model must be allowed to win.
    if ensemble and score_key(ensemble[0])[0] <= score_key(best)[0] + ENSEMBLE_SCORE_TOLERANCE:
        return ensemble[0]
    return best


def _calibrated_half_width(predicted: float, residual_std: float, sparse: bool, errors: list[float], aggregate: bool = False) -> float:
    baseline = (1.8 if sparse else 1.28) * math.sqrt(max(predicted, 0.0) + residual_std**2)
    required = 3 if aggregate else 14
    if len(errors) < required:
        return baseline
    calibrated = max(0.5, float(np.quantile(np.asarray(errors, dtype=float), 0.80)))
    return 0.75 * calibrated + 0.25 * baseline


def _confidence_score(values: np.ndarray, sparse: bool, metric: dict, predicted: float, lower: float, upper: float) -> int:
    recent = values[-14:] if len(values) else np.asarray([0.0])
    cv = float(np.std(recent) / (np.mean(recent) + 0.5))
    volume_score = min(1.0, 0.55 * len(values) / 56.0 + 0.45 * float(values.sum()) / 80.0)
    volatility_score = 1.0 / (1.0 + cv)
    wape = metric.get("wape")
    error_score = 1.0 / (1.0 + max(0.0, float(wape))) if wape is not None else 0.25
    coverage = metric.get("interval_coverage")
    coverage_score = max(0.0, 1.0 - abs(float(coverage) - 0.80) / 0.80) if coverage is not None else 0.25
    width_score = max(0.0, 1.0 - (upper - lower) / (2.0 * (predicted + 1.0)))
    history_score = 0.0 if sparse else 1.0
    confidence = round(100 * (0.25 * volume_score + 0.15 * volatility_score + 0.25 * error_score
                              + 0.15 * coverage_score + 0.15 * width_score + 0.05 * history_score))
    return min(confidence, 40) if sparse else max(5, min(confidence, 95))


def _production_adjusted_confidence(score: int, calibration: dict, sparse: bool) -> int:
    adjusted = round(float(score) * float(calibration.get("confidence_multiplier", 1.0)))
    return min(adjusted, 40) if sparse else max(5, min(adjusted, 95))


def _forecast_for_series(
    frame: pd.DataFrame, all_frame: pd.DataFrame, ad_set: str, campaign: str,
    spend_frame: pd.DataFrame | None = None, parameters: dict | None = None,
    future_spend_daily: float | None = None, force_model: str | None = None,
    current_budget_override: float | None = None,
) -> dict:
    dates = pd.date_range(frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
    values = frame.set_index("aggregate_date")["lead_count"].reindex(dates, fill_value=0).astype(float).to_numpy()
    sparse = bool(len(values) < 21 or values.sum() < 8)
    campaign_daily = all_frame[all_frame["utm_campaign_id"] == campaign].groupby("aggregate_date")["lead_count"].sum()
    overall_daily = all_frame.groupby("aggregate_date")["lead_count"].sum()
    spend_values = _aligned_spend_values(spend_frame, ad_set, dates)
    if not np.any(spend_values):
        spend_values = _aggregate_spend_values(frame, dates)
    campaign_spend_values = _aligned_campaign_spend_values(spend_frame, campaign, dates)
    portfolio_spend_values = _aligned_portfolio_spend_values(spend_frame, dates)
    conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started", ad_set=ad_set
    )
    platform_leads_values = _aligned_performance_values(
        spend_frame, dates, "platform_leads", ad_set=ad_set
    )
    link_click_values = _aligned_performance_values(
        spend_frame, dates, "link_clicks", ad_set=ad_set
    )
    impression_values = _aligned_performance_values(
        spend_frame, dates, "impressions", ad_set=ad_set
    )
    campaign_conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started", campaign=campaign
    )
    portfolio_conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started"
    )
    context = {
        "campaign_values": campaign_daily.reindex(dates, fill_value=0).astype(float).to_numpy(),
        "overall_values": overall_daily.reindex(dates, fill_value=0).astype(float).to_numpy(),
        "campaign_sets": max(1, all_frame[all_frame["utm_campaign_id"] == campaign]["utm_ad_set_id"].nunique()),
        "all_sets": max(1, all_frame["utm_ad_set_id"].nunique()),
        "sparse": sparse,
        "spend_values": spend_values,
        "campaign_spend_values": campaign_spend_values,
        "portfolio_spend_values": portfolio_spend_values,
        "conversation_values": conversation_values,
        "platform_leads_values": platform_leads_values,
        "link_click_values": link_click_values,
        "impression_values": impression_values,
        "campaign_conversation_values": campaign_conversation_values,
        "portfolio_conversation_values": portfolio_conversation_values,
        "frequency_values": _aligned_mean_performance_values(spend_frame, dates, "frequency", ad_set=ad_set),
        "days_since_start_values": _days_since_start_values(spend_frame, dates, ad_set=ad_set),
        "parameters": parameters or DEFAULT_FORECAST_PARAMETERS,
    }
    _ad_set_changes = _ad_set_change_features(spend_frame, dates, ad_set=ad_set)
    _ad_changes = _ad_change_features(dates, spend_frame=spend_frame, ad_set=ad_set)
    context["ad_set_change_recency_values"] = _ad_set_changes["ad_set_change_recency"]
    context["ad_change_recency_values"] = _ad_changes["ad_change_recency"]
    if future_spend_daily is not None:
        context["future_spend_daily"] = future_spend_daily
    if current_budget_override is not None:
        context["current_budget_override"] = current_budget_override
    weekday_profile = calculate_weekday_profile(values, dates, context["campaign_values"],
                                                context["overall_values"], sparse)
    evaluated = _rolling_origin_backtest(values, dates, sparse, context)
    selected = {horizon: _selected_metric(evaluated[horizon]) for horizon in (7, 14)}
    if force_model:
        for horizon in (7, 14):
            forced_metrics = [
                metric for metric in evaluated[horizon]
                if metric["model_used"] == force_model
                or (force_model == SPEND_ADJUSTED_MODEL_PREFIX and _is_spend_adjusted_model(metric["model_used"]))
            ]
            if forced_metrics:
                selected[horizon] = _selected_metric(forced_metrics)
    production_calibration = _production_calibration(ad_set)
    if production_calibration["eligible"]:
        context["bias_adjustment"] = float(production_calibration.get("bias_adjustment") or 0.0)
    future = {}
    for horizon in (7, 14):
        final_context = dict(context)
        if selected[horizon]["model_used"] == ENSEMBLE_MODEL_NAME:
            final_context["ensemble_components"] = selected[horizon].get("_ensemble_components", [])
        future[horizon] = _forecast_candidate(
            selected[horizon]["model_used"], values, dates, horizon, final_context
        )
    # Every selected model is given the recent week's amplitude before delivery, not just the
    # ones that return a constant line. Weekday factors alone were not enough: they carry only
    # the ~11% of daily variance that weekday explains, which still draws as a flat line.
    for horizon in (7, 14):
        future[horizon] = _apply_shape_profile(future[horizon], values, FORECAST_SHAPE_STRENGTH)
    if production_calibration["eligible"]:
        future = {
            horizon: _apply_production_calibration(
                predictions, production_calibration, selected[horizon]["model_used"]
            )
            for horizon, predictions in future.items()
        }
    recent = values[-14:] if len(values) else np.asarray([0.0])
    residual_std = max(0.5, float(np.std(recent - np.mean(recent))))

    daily_metric = selected[14]
    daily_model = daily_metric["model_used"]
    change_point = _change_point_details(values)
    daily_results = []
    explanation_parts = [f"Rolling-origin WAPE: {(daily_metric.get('wape') or 0) * 100:.0f}%",
                         f"Interval coverage: {(daily_metric.get('interval_coverage') or 0) * 100:.0f}%",
                         "Confidence is a reliability score, not probability"]
    if _is_spend_adjusted_model(daily_model):
        explanation_parts.insert(0, "Uses historical pace, Meta spend, tuned weights, and weekday factors")
    if _is_spend_adjusted_model(daily_model):
        explanation_parts.insert(0, f"Formula weights {_formula_weight_label(daily_model)} selected by backtest")
        explanation_parts.insert(0, f"Spend/conversation lag {_spend_lag_for_model(daily_model)} day(s)")
    if daily_model == ENSEMBLE_MODEL_NAME:
        component_labels = [
            f"{component['model']} {float(component['weight']) * 100:.0f}%"
            for component in daily_metric.get("_ensemble_components", [])
        ]
        explanation_parts.insert(0, "Ensemble: " + ", ".join(component_labels))
    if daily_model in (OLS_SPEND_MODEL_NAME, OLS_MULTIVARIATE_MODEL_NAME):
        explanation_parts.insert(
            0,
            "OLS regression selected by rolling-origin backtest"
            if daily_model == OLS_SPEND_MODEL_NAME else
            "Multivariate OLS selected after adjusted R-squared feature screening",
        )
    if change_point["detected"]:
        explanation_parts.insert(
            0, f"Recent {change_point['direction']} change-point detected ({change_point['ratio']:.1f}x prior level)"
        )
    if production_calibration["eligible"]:
        explanation_parts.insert(
            0, f"{_production_adjustment_label(production_calibration, daily_model)} from {production_calibration['sample_size']} realized forecasts"
        )
    if sparse:
        explanation_parts.insert(0, "Sparse history: campaign and overall fallback used")
    explanation = " • ".join(explanation_parts)
    for index, value in enumerate(future[14], start=1):
        target_date = dates[-1] + pd.Timedelta(days=index)
        weekday_factor = weekday_profile["factors"][target_date.weekday()]
        half_width = _calibrated_half_width(
            value, residual_std, sparse, daily_metric["_daily_abs_errors"]
        ) * float(production_calibration["interval_multiplier"])
        if change_point["detected"]:
            half_width *= 1.0 + 0.35 * float(change_point["strength"])
        if _is_spend_adjusted_model(daily_model):
            half_width *= 1.0 + _parameters_for_formula_model(daily_model, context)["error_share"]
        lower, upper = max(0.0, value - half_width), value + half_width
        confidence = _production_adjusted_confidence(
            _confidence_score(values, sparse, daily_metric, value, lower, upper),
            production_calibration, sparse,
        )
        daily_results.append({"date": target_date.date().isoformat(), "day_index": index,
                              "weekday_name": WEEKDAY_NAMES[target_date.weekday()],
                              "weekday_factor": round(float(weekday_factor), 4),
                              "predicted": round(float(value), 1), "lower": round(lower, 1), "upper": round(upper, 1),
                              "confidence": confidence,
                              "model": daily_model, "sparse": sparse, "explanation": explanation})

    results, accuracies = [], []
    for horizon in (7, 14):
        metric = selected[horizon]
        predicted = float(sum(future[horizon]))
        half_width = _calibrated_half_width(predicted, math.sqrt(horizon) * residual_std, sparse,
                                            metric["_window_total_errors"], aggregate=True)
        half_width *= float(production_calibration["interval_multiplier"])
        if _is_spend_adjusted_model(metric["model_used"]):
            half_width *= 1.0 + _parameters_for_formula_model(metric["model_used"], context)["error_share"]
        lower, upper = max(0.0, predicted - half_width), predicted + half_width
        accuracy = max(0.0, min(1.0, 1.0 - float(metric.get("mae") or predicted) / (float(metric.get("_actual_mean") or 0.0) + 1.0)))
        if sparse:
            accuracy = min(accuracy, 0.45)
        accuracies.append(accuracy)
        result_explanation = [f"Selected by composite score {metric['selection_score']:.3f}",
                              f"MAE {float(metric.get('mae') or 0):.2f} leads",
                              f"WAPE {float(metric.get('wape') or 0) * 100:.0f}%",
                              f"Coverage {float(metric.get('interval_coverage') or 0) * 100:.0f}%",
                              "Confidence is not probability"]
        if _is_spend_adjusted_model(metric["model_used"]):
            result_explanation.insert(0, f"Formula weights {_formula_weight_label(metric['model_used'])}")
            result_explanation.insert(0, f"Spend/conversation lag {_spend_lag_for_model(metric['model_used'])} day(s)")
        if metric["model_used"] == ENSEMBLE_MODEL_NAME:
            result_explanation.insert(
                0, "Weighted ensemble of recent level, spend/conversation, weekday, and count models"
            )
        if metric["model_used"] in (OLS_SPEND_MODEL_NAME, OLS_MULTIVARIATE_MODEL_NAME):
            result_explanation.insert(
                0,
                "OLS spend-only regression"
                if metric["model_used"] == OLS_SPEND_MODEL_NAME else
                "Multivariate OLS with adjusted R-squared feature screening",
            )
        if sparse:
            result_explanation.insert(0, "Sparse history fallback")
        if production_calibration["eligible"]:
            result_explanation.insert(0, _production_adjustment_label(production_calibration, metric["model_used"]))
        confidence = _production_adjusted_confidence(
            _confidence_score(values, sparse, metric, predicted, lower, upper),
            production_calibration, sparse,
        )
        results.append({"horizon": horizon, "predicted": round(predicted, 1), "lower": round(lower, 1),
                        "upper": round(upper, 1), "confidence": confidence,
                        "model": metric["model_used"], "accuracy": accuracy, "sparse": sparse,
                        "explanation": " • ".join(result_explanation)})
    all_metrics = [item for horizon_metrics in evaluated.values() for item in horizon_metrics]
    return {"horizons": results, "daily": daily_results, "accuracy": float(np.mean(accuracies)),
            "metrics": all_metrics, "weekday_profile": weekday_profile,
            "production_calibration": production_calibration}


def _load_spend_frame(db: sqlite3.Connection | None = None) -> pd.DataFrame:
    owns_connection = db is None
    if owns_connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    else:
        conn = db
    try:
        rows = conn.execute(
            """SELECT day, campaign_id, campaign_name, ad_set_id,
                      COALESCE(amount_spent_usd, 0) amount_spent_usd,
                      COALESCE(messaging_conversations_started, 0) messaging_conversations_started,
                      COALESCE(leads, 0) platform_leads,
                      COALESCE(link_clicks, 0) link_clicks,
                      COALESCE(impressions, 0) impressions,
                      COALESCE(frequency, 0) frequency,
                      ad_set_budget,
                      days_since_adset_started_imported,
                      ad_set_change_recency_imported,
                      ad_change_recency_imported
               FROM daily_ad_performance
               ORDER BY date(day), campaign_id, ad_set_id"""
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    if not rows:
        return pd.DataFrame(columns=[
            "day", "campaign_id", "campaign_name", "ad_set_id", "amount_spent_usd",
            "messaging_conversations_started", "platform_leads", "link_clicks", "impressions",
            "frequency", "ad_set_budget", "days_since_adset_started_imported",
            "ad_set_change_recency_imported", "ad_change_recency_imported",
        ])
    frame = pd.DataFrame([dict(row) for row in rows])
    frame["day"] = pd.to_datetime(frame["day"])
    frame["amount_spent_usd"] = pd.to_numeric(frame["amount_spent_usd"], errors="coerce").fillna(0.0)
    frame["messaging_conversations_started"] = pd.to_numeric(
        frame["messaging_conversations_started"], errors="coerce"
    ).fillna(0.0)
    frame["platform_leads"] = pd.to_numeric(frame["platform_leads"], errors="coerce").fillna(0.0)
    frame["link_clicks"] = pd.to_numeric(frame["link_clicks"], errors="coerce").fillna(0.0)
    frame["impressions"] = pd.to_numeric(frame["impressions"], errors="coerce").fillna(0.0)
    frame["frequency"] = pd.to_numeric(frame["frequency"], errors="coerce").fillna(0.0)
    # left as NaN, not 0: a missing budget is unknown, and coercing it to 0 would fabricate
    # a budget change on either side of the gap
    frame["ad_set_budget"] = pd.to_numeric(frame["ad_set_budget"], errors="coerce")
    frame["days_since_adset_started_imported"] = pd.to_numeric(
        frame["days_since_adset_started_imported"], errors="coerce"
    )
    for column in ("ad_set_change_recency_imported", "ad_change_recency_imported"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    return frame


MANUAL_BUDGET_SOURCE = "manual"
DERIVED_BUDGET_SOURCE = "meta_export"
# Meta lets a daily budget overspend by ~25% on any single day, balanced across the week.
BUDGET_DAILY_OVERSPEND_ALLOWANCE = 1.25
# A period's *mean* spend cannot exceed its budget, so that is the test that actually matters.
# The margin keeps ordinary delivery noise from raising a flag.
BUDGET_CONFLICT_MEAN_MARGIN = 1.05
BUDGET_CONFLICT_DAY_SHARE = 0.25
BUDGET_CONFLICT_MIN_DAYS = 7
# The conflict test looks only at the tail of a period, not its whole span. A fresh or edited ad
# set routinely overspends while Meta calibrates delivery and then settles onto its budget, so a
# mean taken across many weeks grades that ramp-down as a permanent contradiction. Judging the
# trailing window instead answers the question that matters: is it over budget *now*.
BUDGET_CONFLICT_RECENT_DAYS = 14


def _budget_period_row(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": int(row["id"]),
        "ad_set_id": str(row["ad_set_id"]),
        "start_date": str(row["start_date"]),
        "end_date": str(row["end_date"]),
        "daily_budget": float(row["daily_budget"]),
        "source": str(row["source"]) if "source" in keys and row["source"] else MANUAL_BUDGET_SOURCE,
        "budget_type": (str(row["budget_type"]) if "budget_type" in keys and row["budget_type"] else None),
        "spend_conflict": bool(row["spend_conflict"]) if "spend_conflict" in keys else False,
        "mean_daily_spend": (
            float(row["mean_daily_spend"])
            if "mean_daily_spend" in keys and row["mean_daily_spend"] is not None else None
        ),
        "observed_days": (
            int(row["observed_days"])
            if "observed_days" in keys and row["observed_days"] is not None else None
        ),
        "recent_mean_daily_spend": (
            float(row["recent_mean_daily_spend"])
            if "recent_mean_daily_spend" in keys and row["recent_mean_daily_spend"] is not None else None
        ),
        "recent_days": (
            int(row["recent_days"])
            if "recent_days" in keys and row["recent_days"] is not None else None
        ),
    }


def derive_budget_periods(frame: pd.DataFrame) -> list[dict]:
    """Split each ad set's reported daily budget into dated periods.

    One period per contiguous run of the same budget value: when the reported budget changes
    between two days, the current period ends and a new one starts on the day it changed.

    Each period also carries whether observed spend contradicts the budget it claims. Spend can
    legitimately exceed a daily budget on any given day, so the flag needs the mean to exceed the
    budget as well -- that is the part a real daily budget cannot do. That mean is taken over the
    period's trailing window rather than its whole span, so an ad set that overspent early and has
    since settled onto its budget reads as compliant instead of permanently in conflict.
    """
    required = {"Ad set ID", "Day", "Ad Set Budget", "Amount spent (USD)"}
    if not required.issubset(frame.columns):
        return []
    columns = sorted(required)
    if "Ad Set Budget Type" in frame.columns:
        columns.append("Ad Set Budget Type")
    working = frame.loc[:, columns].copy()
    # Ad-grain imports attach a DataFrame to frame.attrs for later persistence. Pandas
    # propagates attrs into grouped Series and may compare them during groupby internals.
    # Budget period derivation only needs column values, so drop that metadata here.
    working.attrs.clear()
    if "Ad Set Budget Type" not in working.columns:
        working["Ad Set Budget Type"] = ""
    working = working[
        working["Ad Set Budget"].notna()
        & working["Day"].notna()
        & working["Ad set ID"].astype(str).str.strip().ne("")
    ]
    if working.empty:
        return []

    periods: list[dict] = []
    for ad_set_id, group in working.sort_values(["Ad set ID", "Day"]).groupby("Ad set ID", sort=True):
        budgets = group["Ad Set Budget"].round(4)
        budgets.attrs.clear()
        runs = (budgets != budgets.shift()).cumsum()
        runs.attrs.clear()
        for _, run in group.groupby(runs, sort=True):
            budget = float(run["Ad Set Budget"].iloc[0])
            spend = run["Amount spent (USD)"].fillna(0.0)
            days = int(len(run))
            mean_spend = float(spend.mean()) if days else 0.0
            over_days = int((spend > budget * BUDGET_DAILY_OVERSPEND_ALLOWANCE).sum()) if budget > 0 else 0
            budget_type = str(run["Ad Set Budget Type"].iloc[0] or "").strip()
            # The tail of the period decides the flag; the full-span figures stay for context.
            recent = spend.tail(BUDGET_CONFLICT_RECENT_DAYS)
            recent_days = int(len(recent))
            recent_mean = float(recent.mean()) if recent_days else 0.0
            recent_over_days = (
                int((recent > budget * BUDGET_DAILY_OVERSPEND_ALLOWANCE).sum()) if budget > 0 else 0
            )
            periods.append({
                "ad_set_id": str(ad_set_id).strip(),
                "start_date": run["Day"].min().strftime("%Y-%m-%d"),
                "end_date": run["Day"].max().strftime("%Y-%m-%d"),
                "daily_budget": budget,
                "budget_type": budget_type or None,
                "observed_days": days,
                "mean_daily_spend": round(mean_spend, 4),
                "over_budget_days": over_days,
                "recent_days": recent_days,
                "recent_mean_daily_spend": round(recent_mean, 4),
                "spend_conflict": bool(
                    budget > 0
                    and recent_days >= BUDGET_CONFLICT_MIN_DAYS
                    and recent_mean > budget * BUDGET_CONFLICT_MEAN_MARGIN
                    and recent_over_days >= recent_days * BUDGET_CONFLICT_DAY_SHARE
                ),
            })
    return periods


def store_derived_budget_periods(periods: list[dict]) -> dict:
    """Persist export-derived periods without ever clobbering hand-entered history."""
    summary = {"written": 0, "skipped_manual": 0, "conflicts": 0}
    if not periods:
        return summary
    by_ad_set: dict[str, list[dict]] = {}
    for period in periods:
        by_ad_set.setdefault(period["ad_set_id"], []).append(period)
    now = utc_now()
    with connect() as db:
        for ad_set_id, ad_periods in by_ad_set.items():
            window_start = min(period["start_date"] for period in ad_periods)
            window_end = max(period["end_date"] for period in ad_periods)
            # Clear only previously derived rows overlapping this window, so re-importing the
            # same export is idempotent while derived history outside it survives.
            db.execute(
                """DELETE FROM ad_set_budget_periods
                   WHERE ad_set_id=? AND source=? AND start_date<=? AND end_date>=?""",
                (ad_set_id, DERIVED_BUDGET_SOURCE, window_end, window_start),
            )
            manual = db.execute(
                "SELECT start_date, end_date FROM ad_set_budget_periods WHERE ad_set_id=? AND source<>?",
                (ad_set_id, DERIVED_BUDGET_SOURCE),
            ).fetchall()
            for period in ad_periods:
                if any(
                    period["start_date"] <= str(row["end_date"]) and str(row["start_date"]) <= period["end_date"]
                    for row in manual
                ):
                    summary["skipped_manual"] += 1
                    continue
                db.execute(
                    """INSERT INTO ad_set_budget_periods
                       (ad_set_id, start_date, end_date, daily_budget, created_at, updated_at,
                        source, budget_type, spend_conflict, mean_daily_spend, observed_days,
                        recent_mean_daily_spend, recent_days)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ad_set_id, period["start_date"], period["end_date"], period["daily_budget"],
                        now, now, DERIVED_BUDGET_SOURCE, period["budget_type"],
                        int(period["spend_conflict"]), period["mean_daily_spend"], period["observed_days"],
                        period["recent_mean_daily_spend"], period["recent_days"],
                    ),
                )
                summary["written"] += 1
                summary["conflicts"] += int(period["spend_conflict"])
    return summary


def list_budget_periods(ad_set_id: str | None = None) -> list[dict]:
    with connect() as db:
        if ad_set_id is None:
            rows = db.execute(
                "SELECT * FROM ad_set_budget_periods ORDER BY ad_set_id, start_date, id",
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ad_set_budget_periods WHERE ad_set_id=? ORDER BY start_date, id",
                (str(ad_set_id),),
            ).fetchall()
    return [_budget_period_row(row) for row in rows]


def _normalize_budget_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Both a start date and an end date are required.")
    try:
        return pd.to_datetime(text).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"'{text}' is not a valid date.") from exc


def save_budget_period(
    ad_set_id: str, start_date: object, end_date: object,
    daily_budget: object, period_id: int | None = None,
) -> dict:
    ad_set = str(ad_set_id or "").strip()
    if not ad_set:
        raise ValueError("An ad set is required.")
    start = _normalize_budget_date(start_date)
    end = _normalize_budget_date(end_date)
    if start > end:
        raise ValueError("The start date must be on or before the end date.")
    try:
        budget = float(daily_budget)
    except (TypeError, ValueError) as exc:
        raise ValueError("Daily budget must be a number.") from exc
    if not math.isfinite(budget) or budget < 0:
        raise ValueError("Daily budget must be zero or more.")
    now = utc_now()
    with connect() as db:
        if period_id is not None:
            existing = db.execute(
                "SELECT id FROM ad_set_budget_periods WHERE id=?", (int(period_id),)
            ).fetchone()
            if not existing:
                raise ValueError("Budget period not found.")
            # Editing an export-derived period makes it the analyst's own, so imports stop
            # overwriting it from here on.
            db.execute(
                """UPDATE ad_set_budget_periods
                   SET ad_set_id=?, start_date=?, end_date=?, daily_budget=?, updated_at=?,
                       source=?, spend_conflict=0,
                       recent_mean_daily_spend=NULL, recent_days=NULL
                   WHERE id=?""",
                (ad_set, start, end, budget, now, MANUAL_BUDGET_SOURCE, int(period_id)),
            )
            new_id = int(period_id)
        else:
            cursor = db.execute(
                """INSERT INTO ad_set_budget_periods
                   (ad_set_id, start_date, end_date, daily_budget, created_at, updated_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ad_set, start, end, budget, now, now, MANUAL_BUDGET_SOURCE),
            )
            new_id = int(cursor.lastrowid)
        row = db.execute(
            "SELECT * FROM ad_set_budget_periods WHERE id=?", (new_id,)
        ).fetchone()
    return _budget_period_row(row)


def delete_budget_period(period_id: int) -> dict:
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM ad_set_budget_periods WHERE id=?", (int(period_id),)
        ).fetchone()
        if not existing:
            raise ValueError("Budget period not found.")
        db.execute("DELETE FROM ad_set_budget_periods WHERE id=?", (int(period_id),))
    return {"deleted": int(period_id)}


def _change_event_row(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "scope": row["scope"],
        "ad_set_id": row["ad_set_id"],
        "ad_id": row["ad_id"] or "",
        "start_date": row["event_date"],
        "end_date": (row["end_date"] if "end_date" in row.keys() else None) or row["event_date"],
        "source": row["source"],
        "confirmed_by": row["confirmed_by"] or "",
        "notes": row["notes"] or "",
        "from_upload": row["upload_id"] is not None,
    }


def list_change_events(scope: str | None = None, ad_set_id: str | None = None) -> list[dict]:
    """Recorded change events, newest range first, optionally narrowed to one scope/ad set."""
    sql = "SELECT * FROM change_events"
    where: list[str] = []
    params: list[object] = []
    if scope:
        where.append("scope=?")
        params.append(str(scope))
    if ad_set_id:
        where.append("ad_set_id=?")
        params.append(str(ad_set_id))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY event_date DESC, id DESC"
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [_change_event_row(row) for row in rows]


def change_event_coverage(ad_set_id: str) -> dict:
    """How many of an ad set's live days carry a recorded change.

    Since changes became point events (2026-08-07) this is a straight count of days with an
    event, not a span: `covered_days` is "days something was recorded as changing" and
    `uncovered_days` is "days recorded as no change". Neither is a gap to be filled -- an
    uncovered day is a positive assertion once the ad set has any event at all, which is
    exactly what makes the type variable vary. The counts stay useful as a sanity check that
    what was written down matches what the analyst remembers doing.
    """
    ad_set = str(ad_set_id or "").strip()
    empty = {"ad_set_id": ad_set, "live_days": 0, "covered_days": 0, "uncovered_days": 0,
             "first_live_day": None, "last_live_day": None, "recorded_scopes": []}
    if not ad_set:
        return empty
    with connect() as db:
        live = db.execute(
            "SELECT DISTINCT day FROM daily_ad_performance WHERE ad_set_id=? ORDER BY day",
            (ad_set,),
        ).fetchall()
        rows = db.execute(
            "SELECT scope, event_date, end_date FROM change_events WHERE ad_set_id=? AND source=?",
            (ad_set, CONFIRMED_SOURCE),
        ).fetchall()
    live_days = [str(row["day"])[:10] for row in live]
    if not live_days:
        return empty
    covered: set[str] = set()
    scopes: set[str] = set()
    live_lookup = set(live_days)
    for row in rows:
        scopes.add(row["scope"])
        day = str(row["event_date"])[:10]
        if day in live_lookup:
            covered.add(day)
    return {
        "ad_set_id": ad_set,
        "live_days": len(live_days),
        "covered_days": len(covered),
        "uncovered_days": len(live_days) - len(covered),
        "first_live_day": live_days[0],
        "last_live_day": live_days[-1],
        "recorded_scopes": sorted(scopes),
    }


def save_change_event(
    scope: str, ad_set_id: str, start_date: object, end_date: object = None,
    ad_id: str = "", notes: str = "", confirmed_by: str = "", event_id: int | None = None,
) -> dict:
    """Record one human-confirmed change on ONE day.

    A change is a point event: it happened on a date, it is not a state that spans a range.
    `end_date` is retained only so the existing column (and older callers) keep working -- it
    is written equal to `event_date` and never read as a feature. Recency is derived by
    `_resolve_change_state` counting up from this day, so it is never written either.

    There is no change type any more (removed 2026-08-11): an event IS a date. Callers that
    still pass one will fail loudly on the changed signature rather than have it silently
    ignored, which is the intended outcome.
    """
    scope_key = str(scope or "").strip()
    if scope_key not in CHANGE_SCOPES:
        raise ValueError(f"Scope must be one of: {', '.join(CHANGE_SCOPES)}.")
    ad_set = str(ad_set_id or "").strip()
    if not ad_set:
        raise ValueError("An ad set is required.")
    start = _normalize_budget_date(start_date)
    # One date per event. An end date is accepted (and ignored) so an older payload that
    # still sends the range form doesn't 422; it is never allowed to widen the event.
    end = start
    ad = str(ad_id or "").strip()
    now = utc_now()
    with connect() as db:
        clash = db.execute(
            "SELECT id FROM change_events WHERE scope=? AND event_date=? AND ad_set_id=? AND ad_id=?",
            (scope_key, start, ad_set, ad),
        ).fetchone()
        if clash and (event_id is None or int(clash["id"]) != int(event_id)):
            label = "An ad-set" if scope_key == "ad_set" else "An ad"
            raise ValueError(
                f"{label} change is already recorded for this ad set on {start}. "
                "Edit or delete that one instead."
            )
        if event_id is not None:
            existing = db.execute(
                "SELECT id FROM change_events WHERE id=?", (int(event_id),)
            ).fetchone()
            if not existing:
                raise ValueError("Change event not found.")
            db.execute(
                """UPDATE change_events
                   SET scope=?, event_date=?, end_date=?, ad_set_id=?, ad_id=?,
                       source=?, confirmed_by=?, notes=?, updated_at=?
                   WHERE id=?""",
                (scope_key, start, end, ad_set, ad, CONFIRMED_SOURCE,
                 confirmed_by or None, notes or None, now, int(event_id)),
            )
            new_id = int(event_id)
        else:
            cursor = db.execute(
                """INSERT INTO change_events
                   (upload_id, scope, event_date, end_date, ad_set_id, ad_id,
                    source, confirmed_by, notes, created_at, updated_at)
                   VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (scope_key, start, end, ad_set, ad, CONFIRMED_SOURCE,
                 confirmed_by or None, notes or None, now, now),
            )
            new_id = int(cursor.lastrowid)
        row = db.execute("SELECT * FROM change_events WHERE id=?", (new_id,)).fetchone()
    _clear_change_caches()
    return _change_event_row(row)


def delete_change_event(event_id: int) -> dict:
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM change_events WHERE id=?", (int(event_id),)
        ).fetchone()
        if not existing:
            raise ValueError("Change event not found.")
        db.execute("DELETE FROM change_events WHERE id=?", (int(event_id),))
    _clear_change_caches()
    return {"deleted": int(event_id)}


@lru_cache(maxsize=1)
def _confirmed_ad_set_starts() -> tuple[tuple[str, pd.Timestamp], ...]:
    """(ad_set_id -> confirmed launch date), cached like `_recorded_change_events`.

    Read by `_days_since_start_values`, the only source of age for any ad set (no detector
    fallback since 2026-08-06). An ad set missing from this map reports age 0.
    """
    try:
        with connect() as db:
            rows = db.execute("SELECT ad_set_id, start_date FROM ad_set_start_dates").fetchall()
    except sqlite3.OperationalError:
        return ()
    out: dict[str, pd.Timestamp] = {}
    for row in rows:
        day = pd.to_datetime(row["start_date"], errors="coerce")
        if pd.isna(day):
            continue
        out[str(row["ad_set_id"])] = day.normalize()
    return tuple(sorted(out.items()))


def list_ad_set_start_dates(ad_set_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM ad_set_start_dates"
    params: list[object] = []
    if ad_set_id:
        sql += " WHERE ad_set_id=?"
        params.append(str(ad_set_id))
    sql += " ORDER BY start_date DESC"
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def save_ad_set_start_date(
    ad_set_id: str, start_date: object, confirmed_by: str = "", notes: str = "",
) -> dict:
    """Record (or correct) one ad set's true launch date -- a single fact, upserted by
    ad_set_id, not appended as a new row the way change_events are."""
    ad_set = str(ad_set_id or "").strip()
    if not ad_set:
        raise ValueError("An ad set is required.")
    text = str(start_date or "").strip()
    if not text:
        raise ValueError("A start date is required.")
    try:
        start = pd.to_datetime(text).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"'{text}' is not a valid date.") from exc
    if pd.to_datetime(start) > pd.Timestamp.now(tz="UTC").tz_localize(None).normalize():
        raise ValueError("The start date can't be in the future.")
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO ad_set_start_dates(ad_set_id, start_date, confirmed_by, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ad_set_id) DO UPDATE SET
               start_date=excluded.start_date, confirmed_by=excluded.confirmed_by,
               notes=excluded.notes, updated_at=excluded.updated_at""",
            (ad_set, start, confirmed_by or None, notes or None, now, now),
        )
        row = db.execute("SELECT * FROM ad_set_start_dates WHERE ad_set_id=?", (ad_set,)).fetchone()
    _clear_change_caches()
    return dict(row)


def delete_ad_set_start_date(ad_set_id: str) -> dict:
    ad_set = str(ad_set_id or "").strip()
    with connect() as db:
        existing = db.execute(
            "SELECT ad_set_id FROM ad_set_start_dates WHERE ad_set_id=?", (ad_set,)
        ).fetchone()
        if not existing:
            raise ValueError("No recorded start date for this ad set.")
        db.execute("DELETE FROM ad_set_start_dates WHERE ad_set_id=?", (ad_set,))
    _clear_change_caches()
    return {"deleted": ad_set}


def _fit_budget_elasticity(
    ad_set_id: str, frame: pd.DataFrame, spend_frame: pd.DataFrame | None,
    latest_date: pd.Timestamp | None = None,
) -> dict:
    """Fit an observed spend->leads elasticity from the user's dated budget periods.

    Returns the fitted elasticity (clamped, or None when <2 usable periods), the entered
    current budget covering the latest history date, and per-period observed leads/CPL.
    """
    return _fit_periods(ad_set_id, list_budget_periods(ad_set_id), frame, spend_frame, latest_date)


def _fit_periods(
    ad_set_id: str, periods: list[dict], frame: pd.DataFrame, spend_frame: pd.DataFrame | None,
    latest_date: pd.Timestamp | None = None,
) -> dict:
    """Core OLS fit shared by `_fit_budget_elasticity` and the spend-detected fallback in
    `get_budget_optimization` -- takes an explicit period list instead of reading the DB, so
    synthetic (spend-detected) periods can be fit the same way as recorded ones."""
    result: dict = {
        "fitted_elasticity": None,
        "usable_periods": 0,
        "leads_elasticity_raw": None,
        "cpl_elasticity": None,
        "cpl_usable_periods": 0,
        "current_budget": None,
        "periods": [],
    }
    if not periods:
        return result

    lead_dates = pd.to_datetime(frame["aggregate_date"]) if not frame.empty else pd.Series([], dtype="datetime64[ns]")
    lead_counts = frame["lead_count"].astype(float) if not frame.empty else pd.Series([], dtype=float)
    if spend_frame is not None and not spend_frame.empty:
        set_spend = spend_frame[spend_frame["ad_set_id"].astype(str) == str(ad_set_id)]
    else:
        set_spend = pd.DataFrame(columns=["day", "amount_spent_usd"])
    spend_days = pd.to_datetime(set_spend["day"]) if not set_spend.empty else pd.Series([], dtype="datetime64[ns]")
    spend_amounts = set_spend["amount_spent_usd"].astype(float) if not set_spend.empty else pd.Series([], dtype=float)

    fit_points: list[tuple[float, float, float]] = []  # (ln_budget, ln_leads_per_day, weight_days)
    cpl_fit_points: list[tuple[float, float, float]] = []  # (ln_budget, ln_cpl, weight_days)
    for period in periods:
        start = pd.to_datetime(period["start_date"])
        end = pd.to_datetime(period["end_date"])
        days = max(1, int((end - start).days) + 1)
        lead_mask = (lead_dates >= start) & (lead_dates <= end)
        leads_total = float(lead_counts[lead_mask].sum()) if len(lead_dates) else 0.0
        spend_mask = (spend_days >= start) & (spend_days <= end)
        spend_total = float(spend_amounts[spend_mask].sum()) if len(spend_days) else 0.0
        leads_per_day = leads_total / days
        observed_cpl = (spend_total / leads_total) if leads_total > 0 else None
        result["periods"].append({
            "id": period["id"],
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "daily_budget": round(float(period["daily_budget"]), 2),
            "observed_leads_per_day": round(leads_per_day, 2),
            "observed_cpl": round(observed_cpl, 2) if observed_cpl is not None else None,
            "days": days,
        })
        if period["daily_budget"] > 0 and leads_per_day > 0:
            fit_points.append((math.log(period["daily_budget"]), math.log(leads_per_day), float(days)))
        if period["daily_budget"] > 0 and observed_cpl is not None and observed_cpl > 0:
            cpl_fit_points.append((math.log(period["daily_budget"]), math.log(observed_cpl), float(days)))
        if latest_date is not None and start <= latest_date <= end:
            result["current_budget"] = round(float(period["daily_budget"]), 2)

    distinct_budgets = {round(math.exp(ln_b), 4) for ln_b, _, _ in fit_points}
    if len(fit_points) >= 2 and len(distinct_budgets) >= 2:
        weights = np.array([w for _, _, w in fit_points], dtype=float)
        xs = np.array([x for x, _, _ in fit_points], dtype=float)
        ys = np.array([y for _, y, _ in fit_points], dtype=float)
        x_mean = float(np.average(xs, weights=weights))
        y_mean = float(np.average(ys, weights=weights))
        cov = float(np.sum(weights * (xs - x_mean) * (ys - y_mean)))
        var = float(np.sum(weights * (xs - x_mean) ** 2))
        if var > 1e-9:
            slope = cov / var
            result["fitted_elasticity"] = float(np.clip(slope, 0.20, 1.0))
            result["leads_elasticity_raw"] = float(slope)
            result["usable_periods"] = len(fit_points)

    # Unclamped CPL-vs-budget slope, kept separate from the leads elasticity above: that one is
    # clamped to [0.20, 1.0] for forecast stability, which forbids the "budget increase" case
    # (would require slope > 1). This fit's whole purpose is the sign, so it stays unclamped.
    distinct_cpl_budgets = {round(math.exp(ln_b), 4) for ln_b, _, _ in cpl_fit_points}
    if len(cpl_fit_points) >= 2 and len(distinct_cpl_budgets) >= 2:
        weights = np.array([w for _, _, w in cpl_fit_points], dtype=float)
        xs = np.array([x for x, _, _ in cpl_fit_points], dtype=float)
        ys = np.array([y for _, y, _ in cpl_fit_points], dtype=float)
        x_mean = float(np.average(xs, weights=weights))
        y_mean = float(np.average(ys, weights=weights))
        cov = float(np.sum(weights * (xs - x_mean) * (ys - y_mean)))
        var = float(np.sum(weights * (xs - x_mean) ** 2))
        if var > 1e-9:
            result["cpl_elasticity"] = float(cov / var)
            result["cpl_usable_periods"] = len(cpl_fit_points)
    return result


def get_forecast_scenario(
    ad_set_id: str, horizon: int = 14, future_spend_daily: float | None = None,
    history_weight: float = 1.0, spend_weight: float = 1.0, momentum_weight: float = 1.0,
    weekday_weight: float = 1.0, error_multiplier: float = 1.0,
) -> dict:
    """Return a live spend-aware forecast scenario for one ad set without saving it."""
    horizon = max(7, min(14, int(horizon)))
    parameters = _forecast_parameter_set()
    with connect() as db:
        rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = _load_spend_frame(db)
    if not rows:
        raise ValueError("No lead history is available yet.")
    all_frame = pd.DataFrame([dict(row) for row in rows])
    all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    frame = all_frame[all_frame["utm_ad_set_id"].astype(str) == str(ad_set_id)]
    if frame.empty:
        raise ValueError("Ad set not found.")
    campaign_mode = frame["utm_campaign_id"].replace("", np.nan).dropna().mode()
    campaign = str(campaign_mode.iloc[0]) if len(campaign_mode) else ""
    latest_date = all_frame["aggregate_date"].max()
    budget_fit = _fit_budget_elasticity(str(ad_set_id), frame, spend_frame, latest_date)
    if budget_fit["fitted_elasticity"] is not None:
        parameters = dict(parameters)
        parameters["spend_elasticity"] = budget_fit["fitted_elasticity"]
    current_budget_override = budget_fit["current_budget"]
    forecast = _forecast_for_series(
        frame, all_frame, str(ad_set_id), campaign, spend_frame,
        parameters=parameters, future_spend_daily=future_spend_daily,
        force_model="spend-adjusted formula",
        current_budget_override=current_budget_override,
    )
    dates = pd.date_range(frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
    values = frame.set_index("aggregate_date")["lead_count"].reindex(dates, fill_value=0).astype(float).to_numpy()
    spend_values = _aligned_spend_values(spend_frame, str(ad_set_id), dates)
    if not np.any(spend_values):
        spend_values = _aggregate_spend_values(frame, dates)
    campaign_spend_values = _aligned_campaign_spend_values(spend_frame, campaign, dates)
    portfolio_spend_values = _aligned_portfolio_spend_values(spend_frame, dates)
    conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started", ad_set=str(ad_set_id)
    )
    campaign_conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started", campaign=campaign
    )
    portfolio_conversation_values = _aligned_performance_values(
        spend_frame, dates, "messaging_conversations_started"
    )
    context = {
        "campaign_values": all_frame[all_frame["utm_campaign_id"] == campaign].groupby("aggregate_date")["lead_count"].sum().reindex(dates, fill_value=0).astype(float).to_numpy(),
        "overall_values": all_frame.groupby("aggregate_date")["lead_count"].sum().reindex(dates, fill_value=0).astype(float).to_numpy(),
        "sparse": bool(len(values) < 21 or values.sum() < 8),
        "spend_values": spend_values,
        "campaign_spend_values": campaign_spend_values,
        "portfolio_spend_values": portfolio_spend_values,
        "conversation_values": conversation_values,
        "campaign_conversation_values": campaign_conversation_values,
        "portfolio_conversation_values": portfolio_conversation_values,
        "parameters": parameters,
    }
    if future_spend_daily is not None:
        context["future_spend_daily"] = future_spend_daily
    if current_budget_override is not None:
        context["current_budget_override"] = current_budget_override
    selected_model = forecast["daily"][0]["model"] if forecast.get("daily") else SPEND_ADJUSTED_MODEL_PREFIX
    context["parameters"] = _parameters_for_formula_model(selected_model, context)
    if budget_fit["fitted_elasticity"] is not None:
        # _parameters_for_formula_model rebuilds the param set from the weight-candidate
        # label and resets elasticity to the default, so re-apply the fitted value here.
        context["parameters"]["spend_elasticity"] = budget_fit["fitted_elasticity"]
    context["model_used"] = selected_model
    context["signal_lag_days"] = _spend_lag_for_model(selected_model, context)
    details = _spend_formula_details(values, dates, horizon, context)
    daily = forecast["daily"][:horizon]
    total = float(sum(day["predicted"] for day in daily))
    lower_total = float(sum(day["lower"] for day in daily))
    upper_total = float(sum(day["upper"] for day in daily))
    return {
        "ad_set_id": str(ad_set_id),
        "campaign_id": campaign,
        "horizon_days": horizon,
        "model": selected_model,
        "predicted_total": round(total, 1),
        "lower_total": round(lower_total, 1),
        "upper_total": round(upper_total, 1),
        "daily": daily,
        "parameters": details["parameters"],
        "components": {
            "history_daily": round(float(details["history_daily"]), 2),
            "spend_daily": round(float(details["spend_daily"]), 2),
            "conversation_daily": round(float(details["conversation_daily"]), 2),
            "performance_daily": round(float(details["performance_daily"]), 2),
            "base_daily": round(float(details["base_daily"]), 2),
            "auto_spend_daily": round(float(details["last7_spend_daily"]), 2),
            "future_spend_daily": round(float(details["future_spend_daily"]), 2),
            "spend_ratio": round(float(details["spend_ratio"]), 3),
            "spend_elasticity": round(float(details["spend_elasticity"]), 3),
            "recent_cpl": round(float(details["recent_cpl"]), 2) if details["recent_cpl"] is not None else None,
            "smoothed_cpl": round(float(details["smoothed_cpl"]), 2) if details["smoothed_cpl"] is not None else None,
            "campaign_cpl": round(float(details["campaign_cpl"]), 2) if details["campaign_cpl"] is not None else None,
            "portfolio_cpl": round(float(details["portfolio_cpl"]), 2) if details["portfolio_cpl"] is not None else None,
            "momentum": round(float(details["momentum"]), 3),
            "spend_available": bool(details["spend_available"]),
            "conversation_available": bool(details["conversation_available"]),
            "signal_lag_days": int(details["signal_lag_days"]),
            "last7_leads": round(float(details["last7_leads"]), 1),
            "last14_leads": round(float(details["last14_leads"]), 1),
            "last14_spend": round(float(details["last14_spend"]), 2),
            "weekday_strength": round(float(details["weekday_profile"].get("seasonality_strength", 0.0)), 3),
            "history_spend_share": details["formula_weights"]["history_spend"],
            "weekday_share": details["formula_weights"]["weekday"],
            "error_share": details["formula_weights"]["error"],
            "selected_weights": _formula_weight_label(selected_model, details["parameters"]),
        },
        "budget": {
            "fitted_elasticity": round(float(budget_fit["fitted_elasticity"]), 3) if budget_fit["fitted_elasticity"] is not None else None,
            "usable_periods": int(budget_fit["usable_periods"]),
            "current_budget": budget_fit["current_budget"],
            "periods": budget_fit["periods"],
        },
        "note": (
            "Imported spend and conversation signals are active."
            if details["spend_available"] and details["conversation_available"] else
            "Imported spend signal is active."
            if details["spend_available"] else
            "Imported conversation signal is active."
            if details["conversation_available"] else
            "No linked Meta spend or conversation rows were found, so the model uses lead history."
        ),
    }


# --- Budget optimization (CPL-vs-budget derivative) ----------------------
# For ad sets with recorded budget-change history, fits the slope of CPL
# against budget from their own periods: positive slope (CPL rises as budget
# rises) -> decrease budget; negative slope (CPL falls as budget rises) ->
# increase budget. Distinct from get_ad_decisions, which grades ad sets
# against a portfolio benchmark rather than against their own history.

# Cost per lead always drifts up a little as budget grows; that is ordinary auction pricing, not a
# problem to act on. At 0.5 the cost per lead rises with the square root of budget -- the classic
# diminishing-returns knee, and the point where more money stops being obviously worth spending.
# A tighter bar flags healthy scaling as failure: one ad set here nearly doubled its budget for a
# 15% cost rise while staying far under benchmark, which is exactly what good scaling looks like.
_CPL_SLOPE_DEAD_ZONE = 0.5
_CHANGEPOINT_MIN_SEGMENT_DAYS = 7
_CHANGEPOINT_MIN_SHIFT_RATIO = 1.35


def _detect_spend_changepoint(spend: np.ndarray) -> int | None:
    """Index of the strongest sustained spend-level shift, or None if none clears the bar.

    Meta's "Ad Set Budget" export field is a snapshot of the *current* budget, not a historical
    record -- it's flat across an ad set's entire history even when real spend clearly stepped up
    or down (verified: all recorded budgets are constant per ad set while actual spend is not).
    So real budget changes have to be inferred from the spend trajectory itself. This finds at
    most one split (two segments) -- deliberately conservative, matching the single clear regime
    change seen in practice, rather than risking noisy multi-way splits from ordinary day-to-day
    delivery variance.
    """
    n = len(spend)
    if n < _CHANGEPOINT_MIN_SEGMENT_DAYS * 2:
        return None
    best_idx, best_ratio = None, _CHANGEPOINT_MIN_SHIFT_RATIO
    for split in range(_CHANGEPOINT_MIN_SEGMENT_DAYS, n - _CHANGEPOINT_MIN_SEGMENT_DAYS + 1):
        left_mean = float(spend[:split].mean())
        right_mean = float(spend[split:].mean())
        lo, hi = min(left_mean, right_mean), max(left_mean, right_mean)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio > best_ratio:
            best_ratio, best_idx = ratio, split
    return best_idx


def _ad_set_daily_spend(spend_frame: pd.DataFrame | None, ad_set_id: str) -> tuple[pd.DatetimeIndex, np.ndarray]:
    if spend_frame is None or spend_frame.empty:
        return pd.DatetimeIndex([]), np.array([])
    set_spend = spend_frame[spend_frame["ad_set_id"].astype(str) == str(ad_set_id)]
    if set_spend.empty:
        return pd.DatetimeIndex([]), np.array([])
    daily = set_spend.groupby(pd.to_datetime(set_spend["day"]))["amount_spent_usd"].sum()
    dates = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    values = daily.reindex(dates, fill_value=0.0).astype(float).to_numpy()
    return dates, values


def _synthesize_spend_periods(dates: pd.DatetimeIndex, spend: np.ndarray, split_idx: int) -> list[dict]:
    """Two synthetic budget periods from a detected spend changepoint, shaped like DB rows.

    `daily_budget` is each segment's mean observed spend -- the real budget field is unusable
    (see `_detect_spend_changepoint`), so observed spend is the best available proxy. Never
    written to `ad_set_budget_periods`; computed in-memory per request only, so it can never
    collide with or overwrite an analyst's real manual entries.
    """
    left_dates, left_spend = dates[:split_idx], spend[:split_idx]
    right_dates, right_spend = dates[split_idx:], spend[split_idx:]
    return [
        {
            "id": "detected-0", "start_date": left_dates[0].strftime("%Y-%m-%d"),
            "end_date": left_dates[-1].strftime("%Y-%m-%d"), "daily_budget": round(float(left_spend.mean()), 2),
        },
        {
            "id": "detected-1", "start_date": right_dates[0].strftime("%Y-%m-%d"),
            "end_date": right_dates[-1].strftime("%Y-%m-%d"), "daily_budget": round(float(right_spend.mean()), 2),
        },
    ]


_LEADS_PLATEAU_SLOPE = 0.15


def _budget_response_verdict(
    cpl_elasticity: float | None, leads_elasticity_raw: float | None
) -> tuple[str, str, bool]:
    """Verdict, human reason, and whether leads have actually plateaued.

    The plateau flag is the part that carries money: a rising cost per lead because leads grow
    slower than budget still buys leads, but a rising cost per lead with *flat* leads means the
    extra budget bought nothing at all. Only the second one is waste worth pulling back.
    """
    if cpl_elasticity is None:
        return "unknown", "Not enough budget history to measure yet.", False
    if cpl_elasticity > _CPL_SLOPE_DEAD_ZONE:
        plateaued = leads_elasticity_raw is not None and leads_elasticity_raw < _LEADS_PLATEAU_SLOPE
        if plateaued:
            return "decrease", "Leads have plateaued — extra budget isn't converting.", True
        return "decrease", "Cost per lead is climbing faster than leads are growing.", False
    if cpl_elasticity < -_CPL_SLOPE_DEAD_ZONE:
        return "increase", "Cost per lead falls as budget rises — still scaling efficiently.", False
    return "hold", "No clear cost trend as budget changes.", False


def get_budget_optimization() -> dict:
    """Per-ad-set increase/decrease/hold verdicts from each ad set's own budget-change history."""
    with connect() as db:
        adset_rows = db.execute("SELECT DISTINCT ad_set_id FROM ad_set_budget_periods").fetchall()
        if not adset_rows:
            return {"available": False, "dead_zone": _CPL_SLOPE_DEAD_ZONE, "ad_sets": []}
        lead_rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = _load_spend_frame(db)
        campaign_rows = db.execute(
            """SELECT ad_set_id, campaign_name FROM (
                 SELECT ad_set_id, campaign_name,
                        ROW_NUMBER() OVER (PARTITION BY ad_set_id ORDER BY day DESC) rn
                 FROM daily_ad_performance WHERE COALESCE(campaign_name, '') <> ''
               ) WHERE rn = 1"""
        ).fetchall()

    campaign_names = {str(row["ad_set_id"]): str(row["campaign_name"]) for row in campaign_rows}
    all_frame = pd.DataFrame([dict(row) for row in lead_rows]) if lead_rows else pd.DataFrame(
        columns=["aggregate_date", "utm_ad_set_id", "lead_count"]
    )
    if not all_frame.empty:
        all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    latest_date = all_frame["aggregate_date"].max() if not all_frame.empty else None

    results = []
    for row in adset_rows:
        ad_set_id = str(row["ad_set_id"])
        frame = (
            all_frame[all_frame["utm_ad_set_id"].astype(str) == ad_set_id]
            if not all_frame.empty else all_frame
        )
        fit = _fit_budget_elasticity(ad_set_id, frame, spend_frame, latest_date)
        budget_basis = "recorded" if fit["cpl_usable_periods"] >= 2 else "unknown"

        if fit["cpl_usable_periods"] < 2:
            dates, spend_values = _ad_set_daily_spend(spend_frame, ad_set_id)
            split_idx = _detect_spend_changepoint(spend_values) if len(spend_values) else None
            if split_idx is not None:
                synthetic_periods = _synthesize_spend_periods(dates, spend_values, split_idx)
                detected_fit = _fit_periods(ad_set_id, synthetic_periods, frame, spend_frame, latest_date)
                if detected_fit["cpl_usable_periods"] >= 2:
                    fit = detected_fit
                    budget_basis = "detected"

        cpl_elasticity = fit["cpl_elasticity"]
        verdict, reason, plateaued = _budget_response_verdict(
            cpl_elasticity, fit["leads_elasticity_raw"]
        )

        campaign_name = campaign_names.get(ad_set_id) or f"Ad set {ad_set_id[-6:]}"
        periods = fit["periods"]
        results.append({
            "ad_set_id": ad_set_id,
            "campaign_name": campaign_name,
            "label": _shorten_campaign(campaign_name),
            "current_budget": fit["current_budget"],
            "cpl_elasticity": round(cpl_elasticity, 3) if cpl_elasticity is not None else None,
            "cpl_usable_periods": int(fit["cpl_usable_periods"]),
            # The raw slope, not `fitted_elasticity` -- that one is clamped to [0.20, 1.0] for
            # forecast stability, so it reports its own clamp bounds rather than what was measured.
            "leads_elasticity": (
                round(fit["leads_elasticity_raw"], 3)
                if fit["leads_elasticity_raw"] is not None else None
            ),
            "budget_basis": budget_basis,
            "verdict": verdict,
            "reason": reason,
            "plateaued": plateaued,
            # The two endpoints of the fitted curve, so the UI can state the observed change in
            # dollars and cost per lead instead of an elasticity nobody reads at a glance.
            "budget_from": periods[0]["daily_budget"] if len(periods) >= 2 else None,
            "budget_to": periods[-1]["daily_budget"] if len(periods) >= 2 else None,
            "cpl_from": periods[0]["observed_cpl"] if len(periods) >= 2 else None,
            "cpl_to": periods[-1]["observed_cpl"] if len(periods) >= 2 else None,
            "periods": periods,
        })

    # Same collision-safe disambiguation as get_ad_decisions: several ad sets can share
    # one shortened campaign label, so suffix only the ones that collide.
    label_groups: dict[str, list[dict]] = {}
    for item in results:
        label_groups.setdefault(item["label"], []).append(item)
    for label, group in label_groups.items():
        if len(group) <= 1:
            continue
        identifiers = [entry["ad_set_id"] for entry in group]
        for entry in group:
            siblings = [value for value in identifiers if value != entry["ad_set_id"]]
            entry["label"] = f"{label} · {_distinguishing_id_fragment(entry['ad_set_id'], siblings)}"

    order = {"decrease": 0, "increase": 0, "hold": 1, "unknown": 2}
    results.sort(key=lambda item: (
        order.get(item["verdict"], 9),
        -abs(item["cpl_elasticity"]) if item["cpl_elasticity"] is not None else 0,
    ))
    return {"available": True, "dead_zone": _CPL_SLOPE_DEAD_ZONE, "ad_sets": results}


def _portfolio_daily_forecast(all_frame: pd.DataFrame, spend_frame: pd.DataFrame | None, horizon: int = 14) -> dict | None:
    """Forecast the aggregate portfolio series directly for top-down reconciliation."""
    dates = pd.date_range(all_frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
    values = all_frame.groupby("aggregate_date")["lead_count"].sum().reindex(dates, fill_value=0).astype(float).to_numpy()
    if len(values) < PORTFOLIO_MIN_HISTORY_DAYS or values.sum() < 40:
        return None
    spend = _aligned_portfolio_spend_values(spend_frame, dates)
    conversations = _aligned_performance_values(spend_frame, dates, "messaging_conversations_started")
    context = {
        "campaign_values": values, "overall_values": values,
        "campaign_sets": 1, "all_sets": 1, "sparse": False,
        "spend_values": spend, "campaign_spend_values": spend, "portfolio_spend_values": spend,
        "conversation_values": conversations, "campaign_conversation_values": conversations,
        "portfolio_conversation_values": conversations,
        "parameters": DEFAULT_FORECAST_PARAMETERS,
    }
    evaluated = _rolling_origin_backtest(values, dates, False, context)
    metric = _selected_metric(evaluated[horizon])
    final_context = dict(context)
    if metric["model_used"] == ENSEMBLE_MODEL_NAME:
        final_context["ensemble_components"] = metric.get("_ensemble_components", [])
    predictions = _forecast_candidate(metric["model_used"], values, dates, horizon, final_context)
    return {"predictions": predictions, "model": metric["model_used"], "wape": metric.get("wape")}


def _reconcile_forecasts_to_portfolio(results: list[tuple[str, str, dict]], portfolio: dict | None) -> dict | None:
    """Blend bottom-up daily totals toward the aggregate forecast.

    Summing many independent ad-set forecasts cancels errors and variance, so the
    portfolio line goes flat even when the aggregate series has a clear trend. Each
    day's ad-set predictions are scaled by a clipped, partially-weighted ratio of
    the top-down aggregate forecast to the bottom-up sum.
    """
    if not portfolio or not results:
        return None
    horizon = len(portfolio["predictions"])
    bottom_up = 0.0
    for _, _, forecast in results:
        for day in forecast["daily"][:horizon]:
            bottom_up += float(day["predicted"])
    if bottom_up <= 1e-9:
        return None
    # A single level ratio over the whole horizon: reconciliation corrects the
    # aggregate level a summed forecast misses, but must never touch the daily
    # shape — a flat top-down model would otherwise flatten every ad set again.
    low, high = RECONCILIATION_RATIO_RANGE
    raw = float(sum(portfolio["predictions"])) / bottom_up
    ratio = float(np.clip(1.0 + RECONCILIATION_BLEND * (raw - 1.0), low, high))
    if abs(ratio - 1.0) >= 0.005:
        for _, _, forecast in results:
            for day in forecast["daily"]:
                day["predicted"] = round(float(day["predicted"]) * ratio, 1)
                day["lower"] = round(float(day["lower"]) * ratio, 1)
                day["upper"] = round(float(day["upper"]) * ratio, 1)
                day["explanation"] += f" • Portfolio level reconciliation ×{ratio:.2f}"
            for result in forecast["horizons"]:
                result["predicted"] = round(float(result["predicted"]) * ratio, 1)
                result["lower"] = round(float(result["lower"]) * ratio, 1)
                result["upper"] = round(float(result["upper"]) * ratio, 1)
                result["explanation"] += f" • Portfolio level reconciliation ×{ratio:.2f}"
    return {"model": portfolio["model"], "wape": portfolio.get("wape"),
            "mean_ratio": ratio, "raw_ratio": raw}


def train_models() -> dict:
    refresh_forecast_realizations()
    started = utc_now()
    with connect() as db:
        cur = db.execute("INSERT INTO model_training_runs(started_at, status) VALUES(?, 'running')", (started,))
        run_id = cur.lastrowid
        rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = _load_spend_frame(db)
    if not rows:
        with connect() as db:
            db.execute("UPDATE model_training_runs SET completed_at=?, status='completed', notes='No data' WHERE id=?", (utc_now(), run_id))
        return {"id": run_id, "status": "completed", "forecasts": 0}
    all_frame = pd.DataFrame([dict(r) for r in rows])
    all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    generated = utc_now()
    accuracies = []
    count = 0
    results: list[tuple[str, str, dict]] = []
    for ad_set, frame in all_frame.groupby("utm_ad_set_id"):
        campaign_mode = frame["utm_campaign_id"].replace("", np.nan).dropna().mode()
        campaign = str(campaign_mode.iloc[0]) if len(campaign_mode) else ""
        results.append((str(ad_set), campaign, _forecast_for_series(frame, all_frame, str(ad_set), campaign, spend_frame)))
    reconciliation = _reconcile_forecasts_to_portfolio(
        results, _portfolio_daily_forecast(all_frame, spend_frame)
    )
    with connect() as db:
        for ad_set, campaign, forecast in results:
            for metric in forecast["metrics"]:
                db.execute(
                    """INSERT INTO model_backtest_metrics(training_run_id, utm_ad_set_id, model_used,
                       horizon_days, backtest_windows, mae, rmse, wape, mase, bias, r2_out_of_sample,
                       interval_coverage, average_interval_width, selection_score, weekday_wape,
                       weekend_wape, weekday_bias, weekend_bias, weekday_seasonality_strength,
                       forecast_variance_ratio, flatness_penalty, recency_weighted_mae,
                       recency_weighted_rmse, recency_weighted_wape, recency_weighted_bias)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, str(ad_set), metric["model_used"], metric["horizon_days"],
                     metric["backtest_windows"], metric["mae"], metric["rmse"], metric["wape"],
                     metric["mase"], metric["bias"], metric["r2_out_of_sample"],
                     metric["interval_coverage"], metric["average_interval_width"], metric["selection_score"],
                     metric["weekday_wape"], metric["weekend_wape"], metric["weekday_bias"],
                     metric["weekend_bias"], metric["weekday_seasonality_strength"],
                     metric.get("forecast_variance_ratio"), metric.get("flatness_penalty"),
                     metric.get("recency_weighted_mae"), metric.get("recency_weighted_rmse"),
                     metric.get("recency_weighted_wape"), metric.get("recency_weighted_bias")),
                )
            for result in forecast["horizons"]:
                db.execute(
                    """INSERT INTO forecasts(training_run_id, generated_at, utm_ad_set_id, utm_campaign_id,
                       horizon_days, predicted_leads, lower_estimate, upper_estimate, confidence_score,
                       model_used, backtest_accuracy, sparse_warning, explanation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, generated, str(ad_set), campaign, result["horizon"], result["predicted"],
                     result["lower"], result["upper"], result["confidence"], result["model"],
                     result["accuracy"], int(result["sparse"]), result["explanation"]),
                )
                count += 1
            for day in forecast["daily"]:
                db.execute(
                    """INSERT INTO forecast_daily_predictions(training_run_id, generated_at, utm_ad_set_id,
                       utm_campaign_id, forecast_date, day_index, weekday_name, weekday_factor,
                       predicted_leads, lower_estimate,
                       upper_estimate, confidence_score, model_used, sparse_warning, explanation)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, generated, str(ad_set), campaign, day["date"], day["day_index"],
                     day["weekday_name"], day["weekday_factor"], day["predicted"], day["lower"], day["upper"], day["confidence"],
                     day["model"], int(day["sparse"]), day["explanation"]),
                )
            accuracies.append(forecast["accuracy"])
        notes = (
            "Recency-weighted rolling-origin evaluation trained an adaptive ensemble with change-point detection, "
            "tuned 0-2 day spend/conversation lags, and OLS regression candidates screened by adjusted R-squared."
        )
        if reconciliation:
            notes += (f" Top-down portfolio reconciliation applied via {reconciliation['model']}"
                      f" (mean daily ratio {reconciliation['mean_ratio']:.2f}).")
        db.execute(
            """UPDATE model_training_runs SET completed_at=?, status='completed', training_rows=?,
               ad_set_count=?, mean_backtest_accuracy=?, notes=? WHERE id=?""",
            (utc_now(), int(all_frame["lead_count"].sum()), all_frame["utm_ad_set_id"].nunique(),
             float(np.mean(accuracies)), notes, run_id),
        )
    return {"id": run_id, "status": "completed", "forecasts": count}


def rebuild_phase_forecasts(run_ids: list[int] | None = None) -> dict:
    """Re-forecast stored training runs with the current model, from their original vantage.

    The tracking chart stitches each phase from the run that produced it, so a phase keeps
    showing whatever model was current on the day it ran. After the model changes, history on
    the chart is a mix of old and new and cannot be read as one series.

    Each run is rebuilt from its own forecast origin with the lead and spend frames truncated
    to the day before, so the rebuilt phase is still a genuine out-of-sample forecast made
    without sight of what it is predicting. Rebuilding on full history would produce a fitted
    curve that hugs the actuals and would be a lie about what the model knew.
    """
    with connect() as db:
        targets = [dict(row) for row in db.execute(
            """SELECT r.id,
                      (SELECT MIN(forecast_date) FROM forecast_daily_predictions p
                       WHERE p.training_run_id = r.id) AS origin,
                      (SELECT COUNT(DISTINCT forecast_date) FROM forecast_daily_predictions p
                       WHERE p.training_run_id = r.id) AS horizon
               FROM model_training_runs r
               WHERE r.status = 'completed'
               ORDER BY datetime(r.completed_at), r.id"""
        ).fetchall()]
        rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = _load_spend_frame(db)
    targets = [t for t in targets if t["origin"] and t["horizon"]]
    if run_ids is not None:
        wanted = {int(x) for x in run_ids}
        targets = [t for t in targets if int(t["id"]) in wanted]
    if not targets or not rows:
        return {"rebuilt": [], "skipped": []}

    full_frame = pd.DataFrame([dict(row) for row in rows])
    full_frame["aggregate_date"] = pd.to_datetime(full_frame["aggregate_date"])
    spend_days = pd.to_datetime(spend_frame["day"]) if spend_frame is not None and not spend_frame.empty else None

    rebuilt: list[dict] = []
    skipped: list[dict] = []
    for target in targets:
        run_id = int(target["id"])
        origin = pd.Timestamp(str(target["origin"]))
        history = full_frame[full_frame["aggregate_date"] < origin]
        if history.empty or history["aggregate_date"].nunique() < 14:
            skipped.append({"run_id": run_id, "reason": "not enough history before origin"})
            continue
        past_spend = spend_frame if spend_days is None else spend_frame[spend_days < origin]
        results: list[tuple[str, str, dict]] = []
        for ad_set, frame in history.groupby("utm_ad_set_id"):
            campaign_mode = frame["utm_campaign_id"].replace("", np.nan).dropna().mode()
            campaign = str(campaign_mode.iloc[0]) if len(campaign_mode) else ""
            try:
                results.append((str(ad_set), campaign,
                                _forecast_for_series(frame, history, str(ad_set), campaign, past_spend)))
            except Exception:
                continue
        if not results:
            skipped.append({"run_id": run_id, "reason": "no ad set could be forecast"})
            continue
        _reconcile_forecasts_to_portfolio(results, _portfolio_daily_forecast(history, past_spend))
        generated = utc_now()
        written = 0
        with connect() as db:
            db.execute("DELETE FROM forecast_daily_predictions WHERE training_run_id=?", (run_id,))
            db.execute("DELETE FROM forecasts WHERE training_run_id=?", (run_id,))
            db.execute("DELETE FROM model_backtest_metrics WHERE training_run_id=?", (run_id,))
            for ad_set, campaign, forecast in results:
                for metric in forecast["metrics"]:
                    db.execute(
                        """INSERT INTO model_backtest_metrics(training_run_id, utm_ad_set_id, model_used,
                           horizon_days, backtest_windows, mae, rmse, wape, mase, bias, r2_out_of_sample,
                           interval_coverage, average_interval_width, selection_score, weekday_wape,
                           weekend_wape, weekday_bias, weekend_bias, weekday_seasonality_strength,
                           forecast_variance_ratio, flatness_penalty, recency_weighted_mae,
                           recency_weighted_rmse, recency_weighted_wape, recency_weighted_bias)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, str(ad_set), metric["model_used"], metric["horizon_days"],
                         metric["backtest_windows"], metric["mae"], metric["rmse"], metric["wape"],
                         metric["mase"], metric["bias"], metric["r2_out_of_sample"],
                         metric["interval_coverage"], metric["average_interval_width"], metric["selection_score"],
                         metric["weekday_wape"], metric["weekend_wape"], metric["weekday_bias"],
                         metric["weekend_bias"], metric["weekday_seasonality_strength"],
                         metric.get("forecast_variance_ratio"), metric.get("flatness_penalty"),
                         metric.get("recency_weighted_mae"), metric.get("recency_weighted_rmse"),
                         metric.get("recency_weighted_wape"), metric.get("recency_weighted_bias")),
                    )
                for result in forecast["horizons"]:
                    db.execute(
                        """INSERT INTO forecasts(training_run_id, generated_at, utm_ad_set_id, utm_campaign_id,
                           horizon_days, predicted_leads, lower_estimate, upper_estimate, confidence_score,
                           model_used, backtest_accuracy, sparse_warning, explanation)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, generated, str(ad_set), campaign, result["horizon"], result["predicted"],
                         result["lower"], result["upper"], result["confidence"], result["model"],
                         result["accuracy"], int(result["sparse"]), result["explanation"]),
                    )
                for day in forecast["daily"]:
                    db.execute(
                        """INSERT INTO forecast_daily_predictions(training_run_id, generated_at, utm_ad_set_id,
                           utm_campaign_id, forecast_date, day_index, weekday_name, weekday_factor,
                           predicted_leads, lower_estimate, upper_estimate, confidence_score, model_used,
                           sparse_warning, explanation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, generated, str(ad_set), campaign, day["date"], day["day_index"],
                         day["weekday_name"], day["weekday_factor"], day["predicted"], day["lower"],
                         day["upper"], day["confidence"], day["model"], int(day["sparse"]), day["explanation"]),
                    )
                    written += 1
            db.execute(
                "UPDATE model_training_runs SET notes=COALESCE(notes,'') || ? WHERE id=?",
                (f" Re-forecast {generated} with the current model from origin {origin.date()}.", run_id),
            )
        rebuilt.append({"run_id": run_id, "origin": str(origin.date()),
                        "ad_sets": len(results), "daily_rows": written})
    refresh_forecast_realizations()
    return {"rebuilt": rebuilt, "skipped": skipped}


def get_weekday_profile(ad_set_id: str) -> dict | None:
    """Return the current learned weekday hierarchy for one ad set."""
    with connect() as db:
        rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
    if not rows:
        return None
    all_frame = pd.DataFrame([dict(row) for row in rows])
    all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    frame = all_frame[all_frame["utm_ad_set_id"].astype(str) == str(ad_set_id)]
    if frame.empty:
        return None
    campaign_mode = frame["utm_campaign_id"].replace("", np.nan).dropna().mode()
    campaign_id = str(campaign_mode.iloc[0]) if len(campaign_mode) else ""
    dates = pd.date_range(frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
    values = frame.set_index("aggregate_date")["lead_count"].reindex(dates, fill_value=0).astype(float).to_numpy()
    campaign_frame = all_frame[all_frame["utm_campaign_id"] == campaign_id] if campaign_id else all_frame.iloc[0:0]
    campaign_daily = campaign_frame.groupby("aggregate_date")["lead_count"].sum() if not campaign_frame.empty else None
    portfolio_daily = all_frame.groupby("aggregate_date")["lead_count"].sum()
    sparse = bool(len(values) < 21 or values.sum() < 8)
    profile = calculate_weekday_profile(
        values, dates,
        campaign_daily.reindex(dates, fill_value=0).astype(float).to_numpy() if campaign_daily is not None else None,
        portfolio_daily.reindex(dates, fill_value=0).astype(float).to_numpy(), sparse,
    )
    return {"ad_set_id": str(ad_set_id), "campaign_id": campaign_id, **profile}


LEAD_UPDATE_FIELDS = {
    "status",
    "lead_quality",
    "created_at",
    "customer_name",
    "utm_campaign",
    "utm_campaign_id",
    "utm_ad_set_id",
    "utm_ad_id",
    "fb_ad_title",
    "amount_spent_usd",
}

LEAD_RAW_FIELD_MAP = {
    "status": "Status",
    "lead_quality": "Lead Quality",
    "created_at": "Created At",
    "customer_name": "Customer Name",
    "utm_campaign": "UTM Campaign",
    "utm_campaign_id": "UTM Campaign ID",
    "utm_ad_set_id": "UTM Ad Set ID",
    "utm_ad_id": "UTM Ad ID",
    "fb_ad_title": "FB Ad Title",
    "amount_spent_usd": "Amount spent (USD)",
}


def _clean_lead_update_value(field: str, value: object) -> object:
    if field == "amount_spent_usd":
        if value is None or str(value).strip() == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Amount spent must be a number.") from exc

    if field == "created_at":
        if value is None or str(value).strip() == "":
            raise ValueError("Created date is required.")
        parsed = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(parsed):
            raise ValueError("Created date could not be read.")
        return parsed.to_pydatetime().replace(tzinfo=None).isoformat(timespec="seconds")

    if field == "lead_quality":
        cleaned_quality = "" if value is None else str(value).strip()
        if cleaned_quality not in LEAD_QUALITY_OPTIONS:
            raise ValueError(f"Lead quality must be one of: {', '.join(LEAD_QUALITY_OPTIONS)}.")
        return cleaned_quality

    cleaned = "" if value is None else str(value).strip()
    if field == "utm_ad_set_id" and not cleaned:
        raise ValueError("Ad Set ID is required.")
    return cleaned


def update_lead_event(lead_id: int, changes: dict, retrain: bool = True) -> dict:
    """Apply one or more field edits to a lead.

    `retrain=False` writes the row and returns, leaving `rebuild_aggregates()` +
    `train_models()` to the caller. That pair costs ~31s here (2s + ~29s), which is far too
    much to spend inside an interactive request -- the Dataset board fires one PATCH per
    committed cell, so `app.py` passes False and schedules the work behind the background
    retrain guard instead. Defaults to True so non-interactive callers keep the old
    write-then-refresh-everything behaviour without having to know about the guard.
    """
    clean_changes = {
        field: _clean_lead_update_value(field, value)
        for field, value in changes.items()
        if field in LEAD_UPDATE_FIELDS
    }
    if not clean_changes:
        raise ValueError("No editable lead fields were provided.")

    with connect() as db:
        existing = db.execute("SELECT * FROM lead_events WHERE id=?", (lead_id,)).fetchone()
        if not existing:
            raise ValueError("Lead not found.")
        raw = {}
        try:
            raw = json.loads(existing["raw_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        for field, value in clean_changes.items():
            raw_key = LEAD_RAW_FIELD_MAP.get(field)
            if raw_key:
                raw[raw_key] = "" if value is None else str(value)
        clean_changes["updated_at"] = utc_now()
        clean_changes["raw_json"] = json.dumps(raw, ensure_ascii=False)
        assignments = ", ".join(f"{field}=?" for field in clean_changes)
        db.execute(
            f"UPDATE lead_events SET {assignments} WHERE id=?",
            [*clean_changes.values(), lead_id],
        )
        updated = dict(db.execute(
            """SELECT id, platform, status, lead_quality, created_at, updated_at, customer_name,
               utm_campaign, utm_campaign_id, utm_ad_set_id, utm_ad_id,
               fb_ad_title, amount_spent_usd
               FROM lead_events WHERE id=?""",
            (lead_id,),
        ).fetchone())

    if not retrain:
        return {"updated": lead_id, "lead": updated, "training_run": None}
    rebuild_aggregates()
    run = train_models()
    return {"updated": lead_id, "lead": updated, "training_run": run}


# Editable columns on daily_ad_performance, for the Dataset page's board. Deliberately a
# subset of the table:
#
#  * `leads` is excluded even though the column exists. The board's "Leads" value is the
#    CRM-attributed `daily_ad_set_aggregates.lead_count` joined in by get_dataset_rows(), not
#    `p.leads` (which is ~always NULL and attribution-broken -- see the DATASET_ROW_TABLES
#    comments). Writing `p.leads` would change nothing the user can see, which is worse than
#    refusing the edit.
#  * `cost_per_messaging_conversation_started` is excluded for a softer version of the same
#    reason: the board renders a COALESCE of it and spend/messages, so a hand-entered value is
#    silently overridden the moment the stored one is NULL.
#  * The declared-variable columns (#4/#6/#7/#9/#10) aren't columns at all -- they're attached
#    in Python after the query by _attach_declared_variables().
AD_PERFORMANCE_NUMERIC_FIELDS = {
    "amount_spent_usd", "cost_per_lead", "reach", "impressions", "frequency",
    "messaging_conversations_started", "ad_set_budget",
}
AD_PERFORMANCE_TEXT_FIELDS = {
    "day", "campaign_id", "campaign_name", "ad_set_id", "ad_set_budget_type", "delivery_status",
}
AD_PERFORMANCE_UPDATE_FIELDS = AD_PERFORMANCE_NUMERIC_FIELDS | AD_PERFORMANCE_TEXT_FIELDS
# The three columns UNIQUE(day, campaign_id, ad_set_id) is built on -- editing any of them can
# collide with another row, which surfaces as a clean message instead of a 500.
_AD_PERFORMANCE_KEY_FIELDS = ("day", "campaign_id", "ad_set_id")


def _clean_ad_performance_value(field: str, value: object) -> object:
    if field in AD_PERFORMANCE_NUMERIC_FIELDS:
        if value is None or str(value).strip() == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a number.") from exc
    cleaned = "" if value is None else str(value).strip()
    if field in _AD_PERFORMANCE_KEY_FIELDS and not cleaned:
        raise ValueError(f"{field.replace('_', ' ').capitalize()} is required.")
    return cleaned


def update_ad_performance_row(row_id: int, changes: dict) -> dict:
    """Patch one daily_ad_performance row from the Dataset page's board.

    Does NOT retrain -- spend and frequency are model inputs, so the caller schedules a
    background retrain (see _request_retrain in app.py). Inline cell editing fires one request
    per committed cell and train_models() takes ~18s, so retraining inline would freeze the
    board on every keystroke-commit.
    """
    clean_changes = {
        field: _clean_ad_performance_value(field, value)
        for field, value in changes.items()
        if field in AD_PERFORMANCE_UPDATE_FIELDS
    }
    if not clean_changes:
        raise ValueError("No editable ad performance fields were provided.")

    with connect() as db:
        existing = db.execute("SELECT * FROM daily_ad_performance WHERE id=?", (row_id,)).fetchone()
        if not existing:
            raise ValueError("Ad performance row not found.")
        clean_changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{field}=?" for field in clean_changes)
        try:
            db.execute(
                f"UPDATE daily_ad_performance SET {assignments} WHERE id=?",
                [*clean_changes.values(), row_id],
            )
        except sqlite3.IntegrityError as exc:
            key = {field: clean_changes.get(field, existing[field]) for field in _AD_PERFORMANCE_KEY_FIELDS}
            raise ValueError(
                "Another row already covers "
                f"{key['day']} / campaign {key['campaign_id']} / ad set {key['ad_set_id']}. "
                "Each ad set has one row per day."
            ) from exc
        updated = dict(db.execute("SELECT * FROM daily_ad_performance WHERE id=?", (row_id,)).fetchone())

    return {"updated": row_id, "row": updated}


def delete_ad_performance_row(row_id: int) -> dict:
    """Delete one daily_ad_performance row. Retraining is the caller's job, as above."""
    with connect() as db:
        existing = db.execute("SELECT id FROM daily_ad_performance WHERE id=?", (row_id,)).fetchone()
        if not existing:
            raise ValueError("Ad performance row not found.")
        db.execute("DELETE FROM daily_ad_performance WHERE id=?", (row_id,))
    return {"deleted": row_id}


def delete_lead_event(lead_id: int, retrain: bool = True) -> dict:
    """Delete one lead. See `update_lead_event` for what `retrain=False` defers and why."""
    with connect() as db:
        existing = db.execute("SELECT id FROM lead_events WHERE id=?", (lead_id,)).fetchone()
        if not existing:
            raise ValueError("Lead not found.")
        db.execute("DELETE FROM lead_events WHERE id=?", (lead_id,))

    if not retrain:
        return {"deleted": lead_id, "training_run": None}
    rebuild_aggregates()
    run = train_models()
    return {"deleted": lead_id, "training_run": run}


def delete_upload(upload_id: int) -> dict:
    file_type = None
    with connect() as db:
        upload = db.execute("SELECT * FROM raw_uploads WHERE id=?", (upload_id,)).fetchone()
        if not upload:
            raise ValueError("Upload not found")
        path = Path(upload["stored_path"])
        file_type = upload["file_type"]
        # daily_ad_performance and change_events cascade on the foreign key, so the upload row
        # going away withdraws exactly what that file brought in. lead_events do not: they are
        # shared across uploads by content hash, so only rows no upload still claims are cut.
        brought_leads = upload["file_type"] in (CUSTOMER_TRAFFIC_TYPE, MODEL_DATASET_TYPE)
        db.execute("DELETE FROM raw_uploads WHERE id=?", (upload_id,))
        if brought_leads:
            db.execute("DELETE FROM lead_events WHERE id NOT IN (SELECT lead_id FROM upload_lead_links)")
    path.unlink(missing_ok=True)
    if file_type in (CHANGE_LOG_TYPE, MODEL_DATASET_TYPE, LEADLENS_DERIVED_TYPE):
        _clear_change_caches()
    if file_type == HOLIDAY_PROXIMITY_TYPE:
        _holiday_proximity_map.cache_clear()
    if brought_leads:
        rebuild_aggregates()
    run = train_models()
    return {"deleted": upload_id, "file_type": file_type, "training_run": run}


init_db()
