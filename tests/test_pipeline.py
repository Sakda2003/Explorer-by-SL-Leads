import csv
import io
import json
import os
import tempfile
import unittest
import math
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from backend import core


def frame_for(days=3, ad_set="120235942906970078", campaign="120235942262330078"):
    start = date(2026, 6, 1)
    rows = []
    for i in range(days):
        rows.append({"Platform": "messenger", "Status": "New" if i % 2 == 0 else "Existing",
            "Created At": f"{start + timedelta(days=i)} 10:30:00", "Updated At": f"{start + timedelta(days=i)} 10:30:00",
            "Customer Name": f"Customer {i}", "UTM Campaign": "Leads | VISA | JP | FOR",
            "UTM Campaign ID": campaign, "UTM Ad Set ID": ad_set, "UTM Ad ID": f"12023833890485{i:04d}",
            "FB Ad Title": "VF008E2", "Amount spent (USD)": "199.32"})
    return pd.DataFrame(rows, columns=core.REQUIRED_COLUMNS)


def shaped_frame_for(days=35, ad_set="120235942906970078", campaign="120235942262330078"):
    start = date(2026, 6, 1)
    rows = []
    index = 0
    for i in range(days):
        daily_leads = [4, 1, 0, 3, 5, 1, 2][i % 7]
        for lead in range(daily_leads):
            rows.append({"Platform": "messenger", "Status": "New" if index % 2 == 0 else "Existing",
                "Created At": f"{start + timedelta(days=i)} 10:{lead:02d}:00", "Updated At": f"{start + timedelta(days=i)} 10:{lead:02d}:00",
                "Customer Name": f"Customer {index}", "UTM Campaign": "Leads | VISA | JP | FOR",
                "UTM Campaign ID": campaign, "UTM Ad Set ID": ad_set, "UTM Ad ID": f"12023833890485{index:04d}",
                "FB Ad Title": "VF008E2", "Amount spent (USD)": "199.32"})
            index += 1
    return pd.DataFrame(rows, columns=core.REQUIRED_COLUMNS)


def actual_frame_for(date_counts, ad_set="120235942906970078", campaign="120235942262330078"):
    rows = []
    index = 0
    for forecast_date, count in date_counts:
        for lead in range(int(count)):
            rows.append({"Platform": "messenger", "Status": "New",
                "Created At": f"{forecast_date} 12:{lead:02d}:00", "Updated At": f"{forecast_date} 12:{lead:02d}:00",
                "Customer Name": f"Actual Customer {index}", "UTM Campaign": "Leads | VISA | JP | FOR",
                "UTM Campaign ID": campaign, "UTM Ad Set ID": ad_set, "UTM Ad ID": f"actual-{index:04d}",
                "FB Ad Title": "Actual", "Amount spent (USD)": "199.32"})
            index += 1
    return pd.DataFrame(rows, columns=core.REQUIRED_COLUMNS)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        core.DATA_DIR, core.DB_PATH = root, root / "test.db"
        core.UPLOAD_DIR, core.PREVIEW_DIR = root / "uploads", root / "previews"
        core.init_db()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def csv_bytes(frame):
        return frame.to_csv(index=False).encode()

    def import_frame(self, frame, name="test.csv"):
        preview = core.preview_file(self.csv_bytes(frame), name)
        return core.import_preview(preview["token"], name)

    def test_upload_validation_rejects_missing_columns(self):
        with self.assertRaisesRegex(ValueError, "Missing required columns"):
            core.read_tabular(io.BytesIO(b"Platform,Status\nmessenger,New\n"), ".csv")

    def test_holiday_proximity_workbook_import_updates_calendar_features(self):
        workbook = io.BytesIO()
        pd.DataFrame([
            {
                "date": "2026-01-01",
                "day": "Thursday",
                "is_holiday": 1,
                "holiday_name": "New Year's Day",
                "holiday_proximity": "during_holiday",
            },
            {
                "date": "2026-01-02",
                "day": "Friday",
                "is_holiday": 0,
                "holiday_name": "",
                "holiday_proximity": "60_plus_or_none",
            },
            {
                "date": "2026-01-10",
                "day": "Saturday",
                "is_holiday": 0,
                "holiday_name": "",
                "holiday_proximity": "0_14_days",
            },
        ]).to_excel(workbook, index=False)

        preview = core.preview_file(workbook.getvalue(), "holiday_proximity.xlsx")

        self.assertEqual(preview["file_type"], core.HOLIDAY_PROXIMITY_TYPE)
        self.assertEqual(preview["clean_rows"], 3)
        self.assertEqual(preview["holiday_count"], 1)
        self.assertEqual(preview["date_min"], "2026-01-01")
        self.assertIn("holiday_proximity", preview["columns"])
        with mock.patch.object(core, "train_models", return_value={"status": "skipped"}):
            result = core.import_preview(preview["token"], "holiday_proximity.xlsx")

        self.assertEqual(result["file_type"], core.HOLIDAY_PROXIMITY_TYPE)
        self.assertEqual(result["imported"], 3)
        self.assertEqual((core.DATA_DIR / "holiday_proximity.csv").exists(), True)
        self.assertEqual(core._holiday_proximity_map()["2026-01-01"], "during_holiday")
        features = core._holiday_proximity_features(pd.Timestamp("2026-01-10"))
        self.assertEqual(features["holiday_0_14_days"], 1.0)

    def test_imported_declared_variables_feed_correlation_matrix(self):
        dates = pd.date_range("2026-06-06", periods=8, freq="D")
        ad_rows = []
        with core.connect() as db:
            db.execute(
                """INSERT INTO raw_uploads(
                   file_name, stored_path, file_sha256, file_type, uploaded_at,
                   row_count, imported_count, duplicate_count, cleaned_count, excluded_count)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "ad_performance.csv", "ad_performance.csv", "hash",
                    core.AD_PERFORMANCE_TYPE, core.utc_now(), 8, 0, 0, 8, 0,
                ),
            )
            upload_id = db.execute("SELECT id FROM raw_uploads").fetchone()["id"]
            for offset, day in enumerate(dates):
                db.execute(
                    """INSERT INTO daily_ad_set_aggregates(
                       aggregate_date, utm_ad_set_id, utm_campaign_id, lead_count,
                       ad_id_count, new_count, existing_count, status_mix_json, spend_context_usd)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        day.date().isoformat(),
                        "adset-1",
                        "campaign-1",
                        [1, 3, 2, 5, 1, 4, 2, 6][offset],
                        1,
                        [1, 3, 2, 5, 1, 4, 2, 6][offset],
                        0,
                        "{}",
                        10 + offset,
                    ),
                )
                ad_rows.append({
                    "Campaign name": "Campaign",
                    "Campaign ID": "campaign-1",
                    "Ad set ID": "adset-1",
                    "Ad ID": "",
                    "Day": day,
                    "Delivery status": "active",
                    "Delivery level": "adset",
                    "Amount spent (USD)": 10 + offset,
                    "Messaging conversations started": 0,
                    "Cost per messaging conversation started": np.nan,
                    "Reach": 100 + offset,
                    "Impressions": 150 + offset,
                    "Frequency": 1 + offset / 10,
                    "Leads": 0,
                    "Cost per lead": np.nan,
                    "Link clicks": 0,
                    "CPC (cost per link click)": np.nan,
                    "Unique link clicks": 0,
                    "Cost per unique link click": np.nan,
                    "Ad Set Budget": np.nan,
                    "Ad Set Budget Type": "",
                    "Reporting starts": pd.NaT,
                    "Reporting ends": pd.NaT,
                    "days_since_adset_started": 200 + offset,
                    "ad_set_change_recency": "0_3_days" if offset < 3 else "4_7_days",
                    "ad_change_recency": "15_59_days" if offset < 4 else "8_14_days",
                })
            frame = pd.DataFrame(ad_rows)
            core._write_ad_performance(db, upload_id, frame, core.utc_now())

        correlation = core.get_dataset_correlation(ad_set_id="adset-1")
        numbers = {item["variable_number"] for item in correlation["variables"]}
        self.assertTrue({4, 6, 7}.issubset(numbers))

    def test_forecast_ledger_schema_has_realization_columns(self):
        with core.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(forecast_daily_predictions)")}
        self.assertTrue({"actual_leads", "error", "absolute_error", "squared_error",
                         "interval_hit", "realized_at"}.issubset(columns))

    def test_date_parsing_and_ids_remain_text(self):
        parsed = core.read_tabular(io.BytesIO(self.csv_bytes(frame_for(1))), ".csv")
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(parsed["Created At"]))
        self.assertEqual(parsed.loc[0, "UTM Ad Set ID"], "120235942906970078")
        self.assertIsInstance(parsed.loc[0, "UTM Ad Set ID"], str)

    def test_local_leadlens_export_headers_can_be_reimported(self):
        frame = pd.DataFrame([{
            "Created": "Jun 6, 2026",
            "Customer": "Malik King",
            "Status": "New",
            "Campaign": "Leads| VISA | TH | FOR",
            "Campaign ID": "120244603714820078",
            "Ad set ID": "120244603714800078",
            "Ad ID": "120244603714810078",
            "Ad title": "TH1|KFTH1",
            "Amount": "$1.93",
        }])
        parsed = core.read_tabular(io.BytesIO(self.csv_bytes(frame)), ".csv")
        self.assertEqual(parsed.loc[0, "Customer Name"], "Malik King")
        self.assertEqual(parsed.loc[0, "UTM Ad Set ID"], "120244603714800078")
        self.assertEqual(parsed.loc[0, "FB Ad Title"], "TH1|KFTH1")
        self.assertAlmostEqual(float(parsed.loc[0, "Amount spent (USD)"]), 1.93)

    def test_lead_management_export_matches_board_shape_and_date_filter(self):
        self.import_frame(frame_for(3), "lead-management-export.csv")
        from fastapi.testclient import TestClient
        from backend.app import app

        filters = json.dumps([{
            "field": "created_at",
            "operator": "between",
            "value": {"from": "2026-06-02", "to": "2026-06-03"},
        }])
        response = TestClient(app).get("/api/lead-management/leads.csv", params={"filters": filters})

        self.assertEqual(response.status_code, 200)
        self.assertIn("lead-management-2026-06-02-to-2026-06-03.csv", response.headers["content-disposition"])
        self.assertEqual(response.content[:3], b"\xef\xbb\xbf")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], [
            "Created", "Customer", "Status", "Lead Quality", "Campaign",
            "Campaign ID", "Ad set ID", "Ad ID", "Ad title",
        ])
        self.assertEqual(len(rows), 3)
        self.assertEqual([row[0] for row in rows[1:]], ["Jun 2, 2026", "Jun 3, 2026"])
        self.assertEqual([row[1] for row in rows[1:]], ["Customer 1", "Customer 2"])
        self.assertTrue(all(row[3] == "Pending Review" for row in rows[1:]))

    def test_imported_leads_start_pending_review_not_rated(self):
        self.import_frame(frame_for(3), "pending-review-default.csv")
        summary = core.get_lead_pipeline_summary()
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending_review"], 3)
        self.assertEqual(summary["intake"], 0)
        self.assertEqual(summary["rated"], 0)
        self.assertEqual(summary["rated_share"], 0.0)

    def test_dashboard_insights_reconcile_status_and_campaign_mix(self):
        self.import_frame(frame_for(4), "dashboard-mix.csv")
        insights = core.get_dashboard_insights()
        self.assertEqual(insights["total_leads"], 4)
        self.assertEqual(insights["new_leads"], 2)
        self.assertEqual(insights["existing_leads"], 2)
        self.assertAlmostEqual(insights["new_share"], 0.5)
        self.assertEqual(insights["unique_campaigns"], 1)
        self.assertEqual(insights["unique_ad_sets"], 1)
        self.assertEqual(sum(row["leads"] for row in insights["statuses"]), 4)
        self.assertEqual(sum(row["leads"] for row in insights["campaigns"]), 4)
        self.assertEqual(insights["campaigns"][0]["campaign"], "Leads | VISA | JP | FOR")

    def test_combined_khm_for_campaign_is_renamed_only_in_display_data(self):
        frame = frame_for(2)
        frame["UTM Campaign"] = "Leads | VISA | ALL | KHM & FOR"
        self.import_frame(frame, "combined-campaign.csv")

        insights = core.get_dashboard_insights()

        self.assertEqual(insights["campaigns"][0]["campaign"], "Leads | VISA | ALL | FOR")
        with core.connect() as db:
            stored = db.execute("SELECT DISTINCT utm_campaign FROM lead_events").fetchall()
        self.assertEqual([row[0] for row in stored], ["Leads | VISA | ALL | KHM & FOR"])

    def test_dashboard_campaign_mix_groups_by_name_and_avoids_placeholder_campaigns(self):
        rows = [
            {"Platform": "messenger", "Status": "New", "Created At": "2026-06-01 10:00:00", "Updated At": "2026-06-01 10:00:00",
             "Customer Name": "Lead 1", "UTM Campaign": "Engagement | VISA | ALL | KHM", "UTM Campaign ID": "120246730013800078",
             "UTM Ad Set ID": "120246730013810078", "UTM Ad ID": "120246730013810001", "FB Ad Title": "Creative A", "Amount spent (USD)": ""},
            {"Platform": "messenger", "Status": "New", "Created At": "2026-06-02 10:00:00", "Updated At": "2026-06-02 10:00:00",
             "Customer Name": "Lead 2", "UTM Campaign": "Engagement | VISA | ALL | KHM", "UTM Campaign ID": "120249276038010078",
             "UTM Ad Set ID": "120249276038040078", "UTM Ad ID": "120249276410630078", "FB Ad Title": "Creative B", "Amount spent (USD)": ""},
            {"Platform": "messenger", "Status": "New", "Created At": "2026-06-03 10:00:00", "Updated At": "2026-06-03 10:00:00",
             "Customer Name": "Lead 3", "UTM Campaign": "", "UTM Campaign ID": "120244916977850078",
             "UTM Ad Set ID": "120244916977830078", "UTM Ad ID": "120244918962030078", "FB Ad Title": "CN1|MSGENG|K", "Amount spent (USD)": ""},
            {"Platform": "messenger", "Status": "New", "Created At": "2026-06-04 10:00:00", "Updated At": "2026-06-04 10:00:00",
             "Customer Name": "Lead 4", "UTM Campaign": "", "UTM Campaign ID": "120236399148530078",
             "UTM Ad Set ID": "120236399148520078", "UTM Ad ID": "120240112007600078", "FB Ad Title": "POSC008 - TAPC01", "Amount spent (USD)": ""},
        ]
        self.import_frame(pd.DataFrame(rows, columns=core.REQUIRED_COLUMNS), "campaign-name-mix.csv")
        insights = core.get_dashboard_insights()
        campaigns = {row["campaign"]: row for row in insights["campaigns"]}
        self.assertEqual(insights["unique_campaigns"], 1)
        self.assertEqual(campaigns["Engagement | VISA | ALL | KHM"]["leads"], 2)
        self.assertEqual(campaigns["Engagement | VISA | ALL | KHM"]["ad_set_count"], 2)
        self.assertEqual(campaigns["Unattributed"]["leads"], 2)
        self.assertFalse(any(row["campaign"].startswith("Campaign ") for row in insights["campaigns"]))

    def test_scientific_notation_id_is_rejected(self):
        frame = frame_for(1); frame.loc[0, "UTM Ad Set ID"] = "1.2023594290697E+17"
        with self.assertRaisesRegex(ValueError, "scientific notation"):
            core.read_tabular(io.BytesIO(self.csv_bytes(frame)), ".csv")

    def test_new_customer_traffic_export_is_cleaned_and_attribution_is_recovered(self):
        rows = [
            {"ID": "lead-1", "Platform": "messenger", "Status": "New", "Created At": "7/18/2026, 10:50:20 AM",
             "Updated At": "7/18/2026, 10:50:20 AM", "Customer Name": " Customer One ", "UTM Campaign": "Campaign A",
             "UTM Campaign ID": "120200000000000001", "UTM Ad Set ID": "120200000000000002",
             "UTM Ad ID": "120200000000000003", "FB Ad ID": "120200000000000003", "FB Post ID": "post-1", "FB Ad Title": "Creative A"},
            {"ID": "lead-2", "Platform": "messenger", "Status": "New", "Created At": "7/18/2026, 11:20:00 AM",
             "Updated At": "7/18/2026, 11:20:00 AM", "Customer Name": "Customer Two", "UTM Campaign": "",
             "UTM Campaign ID": "", "UTM Ad Set ID": "", "UTM Ad ID": "", "FB Ad ID": "120200000000000003",
             "FB Post ID": "post-1", "FB Ad Title": "Creative A"},
            {"ID": "lead-3", "Platform": "messenger", "Status": "New", "Created At": "7/18/2026, 11:30:00 AM",
             "Updated At": "7/18/2026, 11:30:00 AM", "Customer Name": "Direct Lead", "UTM Campaign": "",
             "UTM Campaign ID": "", "UTM Ad Set ID": "", "UTM Ad ID": "", "FB Ad ID": "", "FB Post ID": "", "FB Ad Title": ""},
        ]
        parsed = core.read_tabular(io.BytesIO(self.csv_bytes(pd.DataFrame(rows))), ".csv")
        report = parsed.attrs["cleaning_report"]
        self.assertEqual(len(parsed), 2)
        self.assertEqual(report["recovered_rows"], 1)
        self.assertEqual(report["unattributed_rows"], 1)
        self.assertEqual(parsed.loc[1, "UTM Ad Set ID"], "120200000000000002")
        self.assertEqual(parsed.loc[0, "Customer Name"], "Customer One")

    def test_new_export_lead_id_deduplicates_across_reordered_exports(self):
        frame = frame_for(2)
        frame.insert(0, "ID", ["lead-a", "lead-b"])
        first = self.import_frame(frame, "first-export.csv")
        second = self.import_frame(frame.iloc[::-1].reset_index(drop=True), "second-export.csv")
        self.assertEqual(first["imported"], 2)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["duplicates"], 2)

    def test_deduplication_and_daily_aggregation(self):
        frame = frame_for(3); first = self.import_frame(frame, "first.csv"); second = self.import_frame(frame, "second.csv")
        self.assertEqual(first["imported"], 3); self.assertEqual(second["imported"], 0); self.assertEqual(second["duplicates"], 3)
        with core.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT SUM(lead_count) FROM daily_ad_set_aggregates").fetchone()[0], 3)

    def test_duplicate_looking_source_rows_are_cleaned_as_one_lead(self):
        frame = frame_for(1)
        frame = pd.concat([frame, frame], ignore_index=True)
        result = self.import_frame(frame, "same-looking-rows.csv")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 0)
        with core.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0], 1)

    def test_forecast_generation_produces_both_horizons(self):
        self.import_frame(frame_for(35), "history.csv")
        with core.connect() as db:
            rows = db.execute("SELECT horizon_days, predicted_leads, lower_estimate, upper_estimate FROM forecasts ORDER BY horizon_days").fetchall()
            daily = db.execute("SELECT forecast_date, day_index, weekday_name, weekday_factor, predicted_leads, lower_estimate, upper_estimate FROM forecast_daily_predictions ORDER BY day_index").fetchall()
        self.assertEqual([row["horizon_days"] for row in rows], [7, 14])
        self.assertTrue(all(row["lower_estimate"] <= row["predicted_leads"] <= row["upper_estimate"] for row in rows))
        self.assertEqual(len(daily), 14)
        self.assertEqual([row["day_index"] for row in daily], list(range(1, 15)))
        self.assertTrue(all(row["weekday_name"] in core.WEEKDAY_NAMES for row in daily))
        self.assertTrue(all(row["weekday_factor"] is not None and row["weekday_factor"] > 0 for row in daily))
        self.assertTrue(all(row["lower_estimate"] <= row["predicted_leads"] <= row["upper_estimate"] for row in daily))
        horizon_totals = {row["horizon_days"]: row["predicted_leads"] for row in rows}
        self.assertAlmostEqual(sum(row["predicted_leads"] for row in daily), horizon_totals[14], delta=0.8)
        self.assertGreater(sum(row["predicted_leads"] for row in daily[:7]), 0)
        self.assertGreater(sum(row["predicted_leads"] for row in daily[7:]), 0)

    def test_fourteen_day_daily_forecast_does_not_collapse_after_seven_days(self):
        dates = pd.date_range("2026-06-01", periods=35, freq="D")
        frame = pd.DataFrame({
            "aggregate_date": dates,
            "utm_ad_set_id": ["ad-cliff"] * len(dates),
            "utm_campaign_id": ["campaign-cliff"] * len(dates),
            "lead_count": [10] * len(dates),
        })
        metric_base = {
            "backtest_windows": 1,
            "mae": 1.0,
            "rmse": 1.0,
            "wape": 0.1,
            "mase": 1.0,
            "bias": 0.0,
            "r2_out_of_sample": 0.0,
            "interval_coverage": 0.8,
            "average_interval_width": 2.0,
            "selection_score": 0.1,
            "weekday_wape": 0.1,
            "weekend_wape": 0.1,
            "weekday_bias": 0.0,
            "weekend_bias": 0.0,
            "weekday_seasonality_strength": 0.0,
            "forecast_variance_ratio": 1.0,
            "flatness_penalty": 0.0,
            "_daily_abs_errors": [1.0] * 14,
            "_window_total_errors": [4.0],
            "_actual_mean": 10.0,
        }
        original_backtest = core._rolling_origin_backtest
        original_candidate = core._forecast_candidate
        original_calibration = core._production_calibration
        try:
            core._rolling_origin_backtest = lambda values, index, sparse, context: {
                7: [{**metric_base, "model_used": "short-horizon-high"}],
                14: [{**metric_base, "model_used": "long-horizon-even"}],
            }

            def fake_candidate(model, values, index, horizon, context=None):
                if model == "short-horizon-high":
                    return [18.0] * 7
                return [10.0] * 14

            core._forecast_candidate = fake_candidate
            core._production_calibration = lambda ad_set: {
                "eligible": False,
                "prediction_multiplier": 1.0,
                "interval_multiplier": 1.0,
                "confidence_multiplier": 1.0,
                "bias_adjustment": 0.0,
                "sample_size": 0,
            }
            forecast = core._forecast_for_series(frame, frame, "ad-cliff", "campaign-cliff")
        finally:
            core._rolling_origin_backtest = original_backtest
            core._forecast_candidate = original_candidate
            core._production_calibration = original_calibration

        daily = [row["predicted"] for row in forecast["daily"]]
        self.assertEqual(daily, [10.0] * 14)
        self.assertEqual(forecast["horizons"][0]["predicted"], 126.0)
        self.assertEqual(forecast["horizons"][1]["predicted"], 140.0)

    def test_metric_calculation_matches_known_errors(self):
        metrics = core.calculate_forecast_metrics(
            [1, 2, 3], [2, 2, 2], [1, 1, 1], [3, 3, 4], naive_scale=1.0, backtest_windows=2,
        )
        self.assertAlmostEqual(metrics["mae"], 2 / 3)
        self.assertAlmostEqual(metrics["rmse"], math.sqrt(2 / 3))
        self.assertAlmostEqual(metrics["wape"], 1 / 3)
        self.assertAlmostEqual(metrics["mase"], 2 / 3)
        self.assertAlmostEqual(metrics["bias"], 0.0)
        self.assertAlmostEqual(metrics["r2_out_of_sample"], 0.0)
        self.assertAlmostEqual(metrics["interval_coverage"], 1.0)
        self.assertAlmostEqual(metrics["average_interval_width"], 7 / 3)
        self.assertEqual(metrics["backtest_windows"], 2)

    def test_wape_handles_zero_actuals_safely(self):
        perfect = core.calculate_forecast_metrics([0, 0, 0], [0, 0, 0])
        missed = core.calculate_forecast_metrics([0, 0, 0], [1, 1, 1])
        self.assertEqual(perfect["wape"], 0.0)
        self.assertEqual(missed["wape"], 3.0)
        self.assertTrue(math.isfinite(missed["wape"]))

    def test_mase_handles_zero_naive_baseline_safely(self):
        metrics = core.calculate_forecast_metrics([2, 2], [3, 1], naive_scale=0.0)
        self.assertEqual(metrics["mase"], 1.0)
        self.assertTrue(math.isfinite(metrics["mase"]))

    def test_backtest_metrics_are_stored_and_selection_is_valid(self):
        self.import_frame(shaped_frame_for(), "metrics-history.csv")
        with core.connect() as db:
            metrics = db.execute("SELECT * FROM model_backtest_metrics ORDER BY horizon_days, selection_score").fetchall()
            forecasts = db.execute("SELECT horizon_days, model_used FROM forecasts ORDER BY horizon_days").fetchall()
        expected_per_horizon = 9 + len(core.SPEND_ADJUSTED_MODEL_NAMES)
        self.assertEqual(len(metrics), 2 * expected_per_horizon)
        self.assertIn("weekday-shaped rolling forecast", {row["model_used"] for row in metrics})
        self.assertIn(core.BREAKOUT_MODEL_NAME, {row["model_used"] for row in metrics})
        self.assertIn(core.TREND_MODEL_NAME, {row["model_used"] for row in metrics})
        self.assertIn(core.OLS_SPEND_MODEL_NAME, {row["model_used"] for row in metrics})
        self.assertIn(core.OLS_MULTIVARIATE_MODEL_NAME, {row["model_used"] for row in metrics})
        self.assertIn(core.ENSEMBLE_MODEL_NAME, {row["model_used"] for row in metrics})
        self.assertTrue(set(core.SPEND_ADJUSTED_MODEL_NAMES).issubset({row["model_used"] for row in metrics}))
        self.assertTrue(all(row["backtest_windows"] > 0 for row in metrics))
        self.assertTrue(all(row["selection_score"] is not None and math.isfinite(row["selection_score"]) for row in metrics))
        self.assertTrue(all(row["recency_weighted_mae"] is not None for row in metrics))
        self.assertTrue(all(row["recency_weighted_wape"] is not None for row in metrics))
        self.assertTrue(all(row["weekday_wape"] is not None and math.isfinite(row["weekday_wape"]) for row in metrics))
        self.assertTrue(all(row["weekend_wape"] is not None and math.isfinite(row["weekend_wape"]) for row in metrics))
        for forecast in forecasts:
            horizon_metrics = [row for row in metrics if row["horizon_days"] == forecast["horizon_days"]]
            best_score = min(row["selection_score"] for row in horizon_metrics)
            chosen = next(row for row in horizon_metrics if row["model_used"] == forecast["model_used"])
            self.assertLessEqual(chosen["selection_score"], best_score + core.ENSEMBLE_SCORE_TOLERANCE + 1e-9)

    def test_formula_weight_candidates_are_normalized_and_distinct(self):
        parameter_sets = [
            core._parameters_for_formula_model(model)
            for model in core.SPEND_ADJUSTED_MODEL_NAMES
        ]
        self.assertEqual(
            len(parameter_sets),
            len(core.FORECAST_WEIGHT_CANDIDATES) * len(core.SPEND_LAG_CANDIDATES),
        )
        self.assertEqual(
            {core._spend_lag_for_model(model) for model in core.SPEND_ADJUSTED_MODEL_NAMES},
            set(core.SPEND_LAG_CANDIDATES),
        )
        self.assertEqual(
            {core._formula_weight_label(model) for model in core.SPEND_ADJUSTED_MODEL_NAMES},
            {label for label, _ in core.FORECAST_WEIGHT_CANDIDATES},
        )
        for parameters in parameter_sets:
            signal_total = sum(parameters[name] for name in (
                "historical_signal_share", "spend_signal_share", "weekday_share", "error_share"
            ))
            self.assertAlmostEqual(signal_total, 1.0)
            self.assertGreaterEqual(parameters["spend_elasticity"], 0.2)
            self.assertLessEqual(parameters["spend_elasticity"], 1.0)

    def test_ols_spend_regression_responds_to_future_spend(self):
        dates = pd.date_range("2026-06-01", periods=35, freq="D")
        spend = pd.Series([10.0 + (i % 7) * 2.0 for i in range(35)], dtype=float).to_numpy()
        values = 1.0 + 0.5 * spend
        baseline = core._forecast_candidate(
            core.OLS_SPEND_MODEL_NAME, values, dates, 7,
            {"spend_values": spend, "future_spend_daily": 12.0},
        )
        increased = core._forecast_candidate(
            core.OLS_SPEND_MODEL_NAME, values, dates, 7,
            {"spend_values": spend, "future_spend_daily": 24.0},
        )
        self.assertEqual(len(baseline), 7)
        self.assertEqual(len(increased), 7)
        self.assertGreater(sum(increased), sum(baseline))

    def test_ols_summary_reports_coefficients_and_significance(self):
        dates = pd.date_range("2026-06-01", periods=35, freq="D")
        spend = pd.Series([10.0 + (i % 7) * 2.0 for i in range(35)], dtype=float).to_numpy()
        values = 1.0 + 0.5 * spend
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        summary = core._fit_ols_summary(values, feature_rows, ["spend"], "OLS")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["dep_variable"], "leads")
        self.assertGreater(summary["r_squared"], 0.99)
        self.assertGreater(summary["adjusted_r_squared"], 0.99)
        self.assertLess(summary["f_p_value"], 0.001)
        terms = {row["term"]: row for row in summary["coefficients"]}
        self.assertIn("Intercept", terms)
        self.assertIn("spent", terms)
        self.assertAlmostEqual(terms["spent"]["coef"], 0.5, places=6)
        self.assertLess(terms["spent"]["p_value"], 0.001)

    def test_univariate_spend_forms_fit_all_four_shapes(self):
        """Linear, quadratic, log and sqrt, all fitted on the same rows.

        Data is generated on a sqrt curve, so the sqrt form should not merely fit -- it should
        win on AIC over the straight line it was built to beat.
        """
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        spend = pd.Series([2.0 + (i % 17) * 0.75 for i in range(40)], dtype=float).to_numpy()
        values = 4.0 * np.sqrt(spend)
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        result = core._fit_univariate_spend_forms(values, feature_rows)
        self.assertEqual(sorted(result["forms"]), ["linear", "log", "quadratic", "sqrt"])
        for key in ("linear", "quadratic", "log", "sqrt"):
            self.assertIsNotNone(result["forms"][key], key)
        self.assertEqual(result["best"], "sqrt")
        self.assertGreater(result["forms"]["sqrt"]["r_squared"], 0.999)
        terms = {row["term"] for row in result["forms"]["quadratic"]["coefficients"]}
        self.assertEqual(terms, {"Intercept", "spent", "spent^2"})

    def test_univariate_spend_forms_share_one_row_set(self):
        """Every form sees the same positive-spend rows, so their AICs stay comparable.

        Zero-spend days are dropped -- log(0) has no value and an AIC computed on a different
        row count is not comparable to its siblings'. The count reported back is the fitted
        one, not the scope's day count.
        """
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        spend = np.array([0.0 if i % 4 == 0 else 5.0 + (i % 6) for i in range(40)], dtype=float)
        values = 2.0 + 0.8 * spend
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        result = core._fit_univariate_spend_forms(values, feature_rows)
        expected = int((spend > 0).sum())
        self.assertEqual(result["spend_days"], expected)
        for key, summary in result["forms"].items():
            self.assertIsNotNone(summary, key)
            self.assertEqual(summary["no_observations"], expected, key)
        self.assertAlmostEqual(result["spend_min"], float(spend[spend > 0].min()), places=6)
        self.assertAlmostEqual(result["spend_max"], float(spend.max()), places=6)

    def test_univariate_spend_forms_publish_residuals_against_a_shared_axis(self):
        """Each form returns its own residual vector, all against one shared spend axis.

        The residual plots pair `spend_values[i]` with `forms[key]["residuals"][i]`, so the two
        must stay the same length and the axis must be shared -- a per-form axis would let a
        length mismatch pass silently and pair each residual with the wrong day, which draws
        as a pattern rather than as an error.
        """
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        spend = np.array([0.0 if i % 5 == 0 else 3.0 + (i % 9) for i in range(40)], dtype=float)
        values = 1.0 + 0.6 * spend
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        result = core._fit_univariate_spend_forms(values, feature_rows)
        axis = result["spend_values"]
        self.assertEqual(len(axis), result["spend_days"])
        self.assertTrue(all(value > 0 for value in axis))
        for key, summary in result["forms"].items():
            self.assertEqual(len(summary["residuals"]), len(axis), key)
            # OLS residuals sum to zero whenever an intercept is in the design.
            self.assertAlmostEqual(sum(summary["residuals"]), 0.0, places=3, msg=key)

    def test_univariate_spend_forms_flag_an_unsupported_winner(self):
        """A form can take the lowest AIC while its own terms are insignificant.

        That is the quadratic trap the per-campaign notebooks kept hitting, so the winner
        carries a caveat rather than reading as a recommendation.
        """
        rng = np.random.default_rng(11)
        dates = pd.date_range("2026-06-01", periods=45, freq="D")
        spend = np.full(45, 6.0) + rng.normal(0.0, 1.2, 45)
        values = np.clip(rng.normal(5.0, 2.5, 45), 0.0, None)
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        result = core._fit_univariate_spend_forms(values, feature_rows)
        self.assertIsNotNone(result["best"])
        winner = result["forms"][result["best"]]
        weak = [row for row in winner["coefficients"]
                if row["feature"] != "Intercept" and row["p_value"] > 0.05]
        self.assertTrue(weak, "expected pure noise to leave the winner unsupported")
        self.assertIsNotNone(result["best_caveat"])
        self.assertIn("not significant", result["best_caveat"])

    def test_univariate_spend_forms_refuse_a_scope_with_no_spend(self):
        """An ad set with leads but no spend gets no form -- not even the linear one.

        Twelve of thirty real ad sets are in exactly this position. No functional form can
        rescue them, so the empty answer has to be explicit rather than a silent linear fit.
        """
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        spend = np.zeros(40, dtype=float)
        values = np.array([float(i % 3) for i in range(40)])
        feature_rows, _ = core._ols_feature_frame(values, dates, {"spend_values": spend}, 7)
        result = core._fit_univariate_spend_forms(values, feature_rows)
        self.assertEqual(result["spend_days"], 0)
        self.assertIsNone(result["best"])
        self.assertTrue(all(summary is None for summary in result["forms"].values()))

    def test_multivariate_ols_uses_available_performance_predictors(self):
        dates = pd.date_range("2026-06-01", periods=42, freq="D")
        spend = pd.Series([12.0 + (i % 5) for i in range(42)], dtype=float).to_numpy()
        conversations = pd.Series([3.0 + (i % 4) for i in range(42)], dtype=float).to_numpy()
        clicks = pd.Series([20.0 + (i % 6) * 2 for i in range(42)], dtype=float).to_numpy()
        impressions = clicks * 50.0
        values = 0.15 * spend + 0.9 * conversations + 0.03 * clicks
        context = {
            "spend_values": spend,
            "conversation_values": conversations,
            "link_click_values": clicks,
            "impression_values": impressions,
            "campaign_values": values * 1.8,
            "overall_values": values * 3.2,
            "future_spend_daily": 20.0,
        }
        forecast = core._forecast_candidate(core.OLS_MULTIVARIATE_MODEL_NAME, values, dates, 14, context)
        self.assertEqual(len(forecast), 14)
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in forecast))

    # --- forward selection over the declared variables (diagnostics path only) -------------
    # These build feature rows directly rather than through _ols_feature_frame: the selector
    # only ever reads row.get(name), and hand-built rows let each test isolate one property.

    @staticmethod
    def _forward_rows(count: int, **columns) -> list[dict]:
        return [{name: float(series[i]) for name, series in columns.items()} for i in range(count)]

    def test_forward_selection_takes_the_predictor_and_leaves_the_noise(self):
        rng = np.random.default_rng(7)
        n = 40
        spend = np.array([10.0 + (i % 9) * 3.0 for i in range(n)])
        noise = rng.normal(2.0, 0.5, n)
        values = 1.0 + 0.5 * spend
        rows = self._forward_rows(n, spend=spend, frequency=noise)
        selection = core._forward_select_declared_features(values, rows)
        self.assertEqual(selection["features"], ["spend"])
        self.assertEqual(selection["order"], [2])
        self.assertEqual(selection["rejected"][5]["reason"], "no_gain")
        self.assertLessEqual(selection["rejected"][5]["delta"], 0.0)

    def test_forward_selection_refuses_an_aliased_second_counter(self):
        # The real case from ad set 120238338920760078: ad_change_recency was always
        # days_since_ad_set_started - 137, so with an intercept the two are one signal.
        n = 40
        age = np.arange(n, dtype=float)
        values = 3.0 + 0.4 * age
        rows = self._forward_rows(n, days_since_adset_started=age, ad_change_recency=age - 137.0)
        selection = core._forward_select_declared_features(values, rows)
        self.assertEqual(selection["features"], ["days_since_adset_started"])
        self.assertEqual(selection["rejected"][6]["reason"], "no_gain")
        self.assertAlmostEqual(selection["rejected"][6]["delta"], 0.0, places=9)

    def test_forward_selection_enters_a_multi_column_variable_as_one_block(self):
        n = 56
        dates = pd.date_range("2026-06-01", periods=n, freq="D")
        weekdays = {f"weekday_{d}": np.array([1.0 if day.weekday() == d else 0.0 for day in dates])
                    for d in range(7)}
        values = np.array([9.0 if day.weekday() >= 5 else 2.0 for day in dates])
        rows = self._forward_rows(n, **weekdays)
        selection = core._forward_select_declared_features(values, rows)
        self.assertEqual(selection["order"], [8])
        # Six of the seven indicators survive: the seventh is the intercept's exact complement
        # and is dropped by the rank prune, not by selection.
        self.assertEqual(len([f for f in selection["features"] if f.startswith("weekday_")]), 6)

    def test_forward_selection_respects_the_observation_budget(self):
        # 11 days is below _fit_ols_summary's floor of 12, so nothing is affordable even
        # though spend is a perfect predictor here.
        n = 11
        spend = np.array([10.0 + i for i in range(n)])
        rows = self._forward_rows(n, spend=spend)
        selection = core._forward_select_declared_features(spend * 0.5, rows)
        self.assertEqual(selection["features"], [])
        self.assertEqual(selection["rejected"][2]["reason"], "observations")

    def test_coverage_reports_the_margin_of_a_rejected_variable(self):
        rng = np.random.default_rng(11)
        n = 40
        spend = np.array([10.0 + (i % 9) * 3.0 for i in range(n)])
        rows = self._forward_rows(n, spend=spend, frequency=rng.normal(2.0, 0.5, n))
        values = 1.0 + 0.5 * spend
        selection = core._forward_select_declared_features(values, rows)
        fit = core._fit_ols_summary(values, rows, selection["features"], "Multivariate OLS")
        coverage = core._declared_variable_coverage(
            rows, selection["features"], fit, selection=selection,
        )
        frequency = next(item for item in coverage if item["number"] == 5)
        self.assertEqual(frequency["status"], "not_selected")
        self.assertIn("adjusted R2", frequency["detail"])
        # Without the selection argument the old wording still applies, so existing callers
        # (the forecast path, any direct call) are unaffected.
        legacy = core._declared_variable_coverage(rows, selection["features"], fit)
        self.assertEqual(next(i for i in legacy if i["number"] == 5)["status"], "available")

    @staticmethod
    def _multivariate_context(n: int, rng) -> tuple[np.ndarray, pd.DatetimeIndex, dict]:
        dates = pd.date_range("2026-06-01", periods=n, freq="D")
        spend = np.array([40.0 + 20.0 * np.sin(i / 4) for i in range(n)])
        values = 2.0 + 0.1 * spend + rng.normal(0.0, 1.0, n)
        return values, dates, {
            "spend_values": spend,
            "frequency_values": rng.normal(1.6, 0.25, n),
            "days_since_start_values": np.arange(n, dtype=float),
            "campaign_values": values * 1.5,
            "overall_values": values * 3.0,
        }

    def _fitted_features(self, values, dates, context) -> list[str]:
        """The feature list _ols_forecast actually hands to the fit -- the returned numbers
        alone cannot tell the two selectors apart."""
        seen: list[list[str]] = []
        original = core._fit_ols_predictions

        def spy(values_, feature_rows, future_rows, features, **kwargs):
            seen.append(list(features))
            return original(values_, feature_rows, future_rows, features, **kwargs)

        core._fit_ols_predictions = spy
        try:
            forecast = core._ols_forecast(values, dates, 14, context, multivariate=True)
        finally:
            core._fit_ols_predictions = original
        self.assertEqual(len(forecast), 14)
        return seen[-1] if seen else []

    def test_multivariate_forecast_fits_every_declared_variable_by_default(self):
        # Phase 3 (2026-08-16) measured forward selection against this and it forecast worse,
        # so the forecast path keeps the all-declared set under ridge while the OLS card shows
        # the selected subset. The two disagreeing is deliberate -- see
        # OLS_FORECAST_USES_FORWARD_SELECTION.
        self.assertFalse(core.OLS_FORECAST_USES_FORWARD_SELECTION)
        values, dates, context = self._multivariate_context(45, np.random.default_rng(13))
        feature_rows, _ = core._ols_feature_frame(values, dates, context, 14)
        self.assertEqual(self._fitted_features(values, dates, context),
                         core._select_multivariate_ols_features(values, feature_rows))

    def test_multivariate_forecast_honours_the_forward_selection_switch(self):
        values, dates, context = self._multivariate_context(45, np.random.default_rng(13))
        feature_rows, _ = core._ols_feature_frame(values, dates, context, 14)
        core.OLS_FORECAST_USES_FORWARD_SELECTION = True
        try:
            fitted = self._fitted_features(values, dates, context)
        finally:
            core.OLS_FORECAST_USES_FORWARD_SELECTION = False
        self.assertIn("spend", fitted)
        self.assertEqual(fitted, core._forward_select_declared_features(values, feature_rows)["features"])
        self.assertLess(len(fitted), len(core._select_multivariate_ols_features(values, feature_rows)))

    def test_multivariate_forecast_returns_the_level_when_nothing_is_selected(self):
        # A third of rolling-origin windows select nothing. Returning the intercept keeps
        # _forecast_candidate total -- and keeps a re-test honest, since raising here would let
        # the model be scored only on the windows where it found signal.
        n = 40
        dates = pd.date_range("2026-06-01", periods=n, freq="D")
        rng = np.random.default_rng(2)
        values = rng.normal(6.0, 1.5, n)
        context = {
            "spend_values": rng.normal(30.0, 5.0, n),
            "campaign_values": values * 1.5,
            "overall_values": values * 3.0,
        }
        feature_rows, _ = core._ols_feature_frame(values, dates, context, 14)
        self.assertEqual(core._forward_select_declared_features(values, feature_rows)["features"], [])
        core.OLS_FORECAST_USES_FORWARD_SELECTION = True
        try:
            forecast = core._ols_forecast(values, dates, 14, context, multivariate=True)
        finally:
            core.OLS_FORECAST_USES_FORWARD_SELECTION = False
        self.assertEqual(len(forecast), 14)
        self.assertEqual(len(set(round(value, 6) for value in forecast)), 1)
        self.assertAlmostEqual(forecast[0], float(np.mean(values)), places=6)

    def test_forward_selection_reports_every_candidate_it_tried(self):
        # The selection path panel renders this trace directly, so it has to carry the losers
        # and their statistics, not just the winner.
        rng = np.random.default_rng(7)
        n = 40
        spend = np.array([10.0 + (i % 9) * 3.0 for i in range(n)])
        rows = self._forward_rows(n, spend=spend, frequency=rng.normal(2.0, 0.5, n))
        values = 1.0 + 0.5 * spend + rng.normal(0.0, 1.5, n)
        selection = core._forward_select_declared_features(values, rows)
        self.assertEqual([step["action"] for step in selection["steps"]], ["add"])
        step = selection["steps"][0]
        self.assertEqual(step["round"], 1)
        self.assertEqual(step["winner"], 2)
        self.assertEqual(sorted(row["number"] for row in step["candidates"]), [2, 5])
        winner = next(row for row in step["candidates"] if row["number"] == 2)
        self.assertEqual(winner["status"], "eligible")
        self.assertLess(winner["p_value"], 0.001)
        self.assertGreater(winner["r_squared"], winner["adjusted_r_squared"])
        self.assertAlmostEqual(step["adjusted_r_squared"], winner["adjusted_r_squared"])
        self.assertAlmostEqual(selection["adjusted_r_squared"], winner["adjusted_r_squared"])

    def test_forward_selection_refuses_a_gain_that_is_not_significant(self):
        # Adjusted R2 rises whenever the block F exceeds 1, which happens well before the term
        # is distinguishable from noise -- this frequency column lifts adjusted R2 by 0.0003 at
        # p = 0.30. Gain alone would admit it; the p-value gate is what keeps it out.
        rng = np.random.default_rng(1)
        n = 40
        spend = np.array([10.0 + (i % 9) * 3.0 for i in range(n)])
        rows = self._forward_rows(n, spend=spend, frequency=rng.normal(2.0, 0.5, n))
        values = 1.0 + 0.5 * spend + rng.normal(0.0, 2.0, n)
        selection = core._forward_select_declared_features(values, rows)
        self.assertEqual(selection["features"], ["spend"])
        rejection = selection["rejected"][5]
        self.assertEqual(rejection["reason"], "not_significant")
        self.assertGreater(rejection["delta"], 0.0)
        self.assertGreaterEqual(rejection["p_value"], core.FORWARD_SELECTION_MAX_P)
        coverage = core._declared_variable_coverage(
            rows, selection["features"],
            core._fit_ols_summary(values, rows, selection["features"], "Multivariate OLS"),
            selection=selection,
        )
        detail = next(item for item in coverage if item["number"] == 5)["detail"]
        self.assertIn("p = 0.300", detail)

    def test_forward_selection_tests_a_multi_column_block_with_one_p_value(self):
        # Seven weekday indicators produce seven t statistics and no single one of them asks
        # "does day-of-week belong here" -- the trace has to carry the block F instead.
        n = 56
        dates = pd.date_range("2026-06-01", periods=n, freq="D")
        weekdays = {f"weekday_{d}": np.array([1.0 if day.weekday() == d else 0.0 for day in dates])
                    for d in range(7)}
        rng = np.random.default_rng(4)
        values = np.array([9.0 if day.weekday() >= 5 else 2.0 for day in dates]) + rng.normal(0, 0.8, n)
        rows = self._forward_rows(n, **weekdays)
        selection = core._forward_select_declared_features(values, rows)
        candidate = selection["steps"][0]["candidates"][0]
        self.assertEqual(candidate["number"], 8)
        # Six degrees of freedom, not seven: the seventh indicator is the intercept's exact
        # complement and adds no rank.
        self.assertEqual(candidate["df_added"], 6)
        self.assertLess(candidate["p_value"], 0.001)

    def test_spend_signal_has_positive_diminishing_returns(self):
        dates = pd.date_range("2026-06-01", periods=35, freq="D")
        values = pd.Series([4, 5, 6, 5, 4, 2, 1] * 5, dtype=float).to_numpy()
        spend = pd.Series([20.0] * 35, dtype=float).to_numpy()
        base_context = {
            "campaign_values": values,
            "overall_values": values,
            "spend_values": spend,
            "campaign_spend_values": spend,
            "portfolio_spend_values": spend,
            "parameters": core._parameters_for_formula_model("spend-adjusted formula 70/20/10"),
        }
        current = core._spend_formula_details(
            values, dates, 14, {**base_context, "future_spend_daily": 20.0}
        )
        paused = core._spend_formula_details(
            values, dates, 14, {**base_context, "future_spend_daily": 0.0}
        )
        doubled = core._spend_formula_details(
            values, dates, 14, {**base_context, "future_spend_daily": 40.0}
        )
        self.assertLess(sum(paused["predictions"]), sum(current["predictions"]))
        self.assertGreater(sum(doubled["predictions"]), sum(current["predictions"]))
        self.assertLess(sum(doubled["predictions"]), 2 * sum(current["predictions"]))
        self.assertAlmostEqual(doubled["spend_ratio"], 2.0)

    def test_prior_daily_forecasts_are_realized_when_actuals_arrive(self):
        first = self.import_frame(shaped_frame_for(), "initial-history.csv")
        run_id = first["training_run"]["id"]
        with core.connect() as db:
            forecast_rows = db.execute(
                """SELECT * FROM forecast_daily_predictions
                   WHERE training_run_id=? ORDER BY day_index LIMIT 7""",
                (run_id,),
            ).fetchall()
        immutable_fields = ["training_run_id", "generated_at", "utm_ad_set_id", "utm_campaign_id",
            "forecast_date", "day_index", "weekday_name", "weekday_factor", "predicted_leads",
            "lower_estimate", "upper_estimate", "confidence_score", "model_used", "sparse_warning", "explanation"]
        originals = [{field: row[field] for field in immutable_fields} for row in forecast_rows]
        actual_counts = [(row["forecast_date"], 2 + (index % 3)) for index, row in enumerate(forecast_rows)]
        self.import_frame(actual_frame_for(actual_counts), "future-actuals.csv")
        with core.connect() as db:
            realized = db.execute(
                """SELECT *
                   FROM forecast_daily_predictions
                   WHERE training_run_id=? ORDER BY day_index""",
                (run_id,),
            ).fetchall()
        self.assertEqual(sum(row["actual_leads"] is not None for row in realized), 7)
        self.assertEqual(sum(row["actual_leads"] is None for row in realized), 7)
        first_realized = realized[0]
        self.assertAlmostEqual(first_realized["error"], first_realized["predicted_leads"] - first_realized["actual_leads"])
        self.assertAlmostEqual(first_realized["absolute_error"], abs(first_realized["error"]))
        self.assertAlmostEqual(first_realized["squared_error"], first_realized["error"] ** 2)
        self.assertIn(first_realized["interval_hit"], (0, 1))
        self.assertIsNotNone(first_realized["realized_at"])
        for before, after in zip(originals, realized[:7]):
            self.assertEqual(before, {field: after[field] for field in immutable_fields})

    def test_forecast_realization_summary_api_structure(self):
        first = self.import_frame(shaped_frame_for(), "ledger-history.csv")
        run_id = first["training_run"]["id"]
        with core.connect() as db:
            forecast_rows = db.execute(
                """SELECT forecast_date FROM forecast_daily_predictions
                   WHERE training_run_id=? ORDER BY day_index LIMIT 3""",
                (run_id,),
            ).fetchall()
        self.import_frame(actual_frame_for([(row["forecast_date"], 1) for row in forecast_rows]), "ledger-actuals.csv")
        from backend.app import forecast_realizations as forecast_realizations_endpoint
        result = forecast_realizations_endpoint("120235942906970078", 10)
        self.assertGreaterEqual(result["summary"]["realized_predictions"], 3)
        self.assertIn("production_mae", result["summary"])
        self.assertIn("production_wape", result["summary"])
        self.assertIn("weekday_wape", result["summary"])
        self.assertIn("weekend_bias", result["summary"])
        self.assertTrue(result["rows"])
        self.assertTrue(result["first_forecast_rows"])
        self.assertTrue(result["comparison_forecast_rows"])
        expected = {"forecast_date", "predicted_leads", "actual_leads", "error", "absolute_error", "interval_hit"}
        self.assertTrue(expected.issubset(result["rows"][0]))

    def test_portfolio_forecast_tracking_reconciles_daily_actuals_and_future_run(self):
        first = self.import_frame(shaped_frame_for(), "tracking-history.csv")
        run_id = first["training_run"]["id"]
        with core.connect() as db:
            forecast_rows = db.execute(
                """SELECT forecast_date FROM forecast_daily_predictions
                   WHERE training_run_id=? ORDER BY day_index LIMIT 3""",
                (run_id,),
            ).fetchall()
        actual_counts = [(row["forecast_date"], index + 2) for index, row in enumerate(forecast_rows)]
        self.import_frame(actual_frame_for(actual_counts), "tracking-actuals.csv")

        result = core.get_portfolio_forecast_tracking(history_days=14, future_days=14)
        realized = [point for point in result["timeline"] if point["state"] == "realized"]
        future = [point for point in result["timeline"] if point["state"] == "future"]
        self.assertEqual(len(realized), 3)
        self.assertEqual([point["actual_leads"] for point in realized], [2.0, 3.0, 4.0])
        self.assertTrue(all(point["forecast_leads"] >= 0 for point in realized))
        self.assertTrue(all(point["difference"] == round(point["forecast_leads"] - point["actual_leads"], 1) for point in realized))
        self.assertEqual(len(future), 14)
        self.assertTrue(all(point["actual_leads"] is None for point in future))
        self.assertEqual(result["summary"]["latest_actual_date"], realized[-1]["date"])
        self.assertEqual(result["summary"]["next_forecast_date"], future[0]["date"])
        self.assertAlmostEqual(result["summary"]["realized_actual_total"], 9.0)
        self.assertIsNotNone(result["summary"]["production_mae_daily"])

    def test_portfolio_tracking_includes_zero_filled_actual_history_from_requested_date(self):
        self.import_frame(shaped_frame_for(), "full-tracking-history.csv")
        result = core.get_portfolio_forecast_tracking(
            history_days=90, future_days=14, start_date="2026-06-06"
        )
        actual_points = [point for point in result["timeline"] if point["actual_leads"] is not None]
        future_points = [point for point in result["timeline"] if point["state"] == "future"]
        self.assertEqual(actual_points[0]["date"], "2026-06-06")
        self.assertEqual(actual_points[-1]["date"], "2026-07-05")
        self.assertEqual(len(actual_points), 30)
        self.assertTrue(any(point["actual_leads"] == 0 for point in actual_points))
        self.assertTrue(all(point["forecast_leads"] is None for point in actual_points))
        self.assertEqual(len(future_points), 14)
        self.assertEqual(result["summary"]["history_start_date"], "2026-06-06")

    def test_missing_ad_set_day_realizes_as_zero_inside_actual_range(self):
        first = self.import_frame(shaped_frame_for(), "zero-history.csv")
        run_id = first["training_run"]["id"]
        with core.connect() as db:
            forecast = db.execute(
                "SELECT * FROM forecast_daily_predictions WHERE training_run_id=? ORDER BY day_index LIMIT 1",
                (run_id,),
            ).fetchone()
        self.import_frame(
            actual_frame_for([(forecast["forecast_date"], 1)], ad_set="other-ad-set", campaign="other-campaign"),
            "other-adset-actual.csv",
        )
        with core.connect() as db:
            realized = db.execute(
                "SELECT * FROM forecast_daily_predictions WHERE id=?", (forecast["id"],)
            ).fetchone()
        self.assertEqual(realized["actual_leads"], 0.0)
        self.assertAlmostEqual(realized["error"], forecast["predicted_leads"])
        self.assertEqual(realized["interval_hit"],
                         int(forecast["lower_estimate"] <= 0 <= forecast["upper_estimate"]))

    def test_production_metrics_and_csv_match_known_realizations(self):
        ad_set = "ledger-ad-set"
        dates = pd.date_range("2026-06-01", periods=4, freq="D")
        predicted = [2.0, 2.0, 1.0, 1.0]
        with core.connect() as db:
            run_id = db.execute(
                "INSERT INTO model_training_runs(started_at, completed_at, status) VALUES(?,?,?)",
                (core.utc_now(), core.utc_now(), "completed"),
            ).lastrowid
            for index, (day, prediction) in enumerate(zip(dates, predicted), start=1):
                db.execute(
                    """INSERT INTO forecast_daily_predictions(training_run_id, generated_at, utm_ad_set_id,
                       utm_campaign_id, forecast_date, day_index, weekday_name, weekday_factor,
                       predicted_leads, lower_estimate, upper_estimate, confidence_score, model_used,
                       sparse_warning, explanation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, core.utc_now(), ad_set, "campaign", day.date().isoformat(), index,
                     core.WEEKDAY_NAMES[day.weekday()], 1.0, prediction, 0.0,
                     2.0 if index >= 3 else 3.0, 70, "test model", 0, "test"),
                )
            for day, actual in ((dates[0], 1), (dates[1], 2), (dates[3], 3)):
                db.execute(
                    "INSERT INTO daily_ad_set_aggregates VALUES(?,?,?,?,?,?,?,?,?)",
                    (day.date().isoformat(), ad_set, "campaign", actual, 1, actual, 0, "{}", None),
                )
            db.execute(
                "INSERT INTO daily_ad_set_aggregates VALUES(?,?,?,?,?,?,?,?,?)",
                (dates[2].date().isoformat(), "other", "other", 1, 1, 1, 0, "{}", None),
            )
        result = core.get_forecast_realizations(ad_set, 10)
        summary = result["summary"]
        self.assertEqual(summary["realized_predictions"], 4)
        self.assertAlmostEqual(summary["production_mae"], 1.0)
        self.assertAlmostEqual(summary["production_rmse"], math.sqrt(1.5))
        self.assertAlmostEqual(summary["production_wape"], 4 / 6)
        self.assertAlmostEqual(summary["production_bias"], 0.0)
        self.assertAlmostEqual(summary["interval_coverage"], 0.75)
        self.assertAlmostEqual(summary["weekday_wape"], 4 / 6)
        from backend.app import export_forecast_realizations
        response = export_forecast_realizations(ad_set)
        csv_text = response.body.decode("utf-8")
        self.assertIn("Training Run ID,Generated At,UTM Ad Set ID", csv_text)
        self.assertIn("ledger-ad-set", csv_text)

    def test_realized_errors_drive_conservative_calibration(self):
        with core.connect() as db:
            run_id = db.execute(
                "INSERT INTO model_training_runs(started_at, completed_at, status) VALUES(?,?,?)",
                (core.utc_now(), core.utc_now(), "completed"),
            ).lastrowid
            for index in range(7):
                day = (date(2026, 6, 1) + timedelta(days=index)).isoformat()
                db.execute(
                    """INSERT INTO forecast_daily_predictions(training_run_id, generated_at, utm_ad_set_id,
                       forecast_date, day_index, predicted_leads, lower_estimate, upper_estimate,
                       confidence_score, model_used, sparse_warning, explanation, actual_leads,
                       error, absolute_error, squared_error, interval_hit, realized_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, core.utc_now(), "biased-ad-set", day, index + 1, 3.0, 2.0, 4.0,
                     80, "test", 0, "test", 1.0, 2.0, 2.0, 4.0, 0, core.utc_now()),
                )
        calibration = core._production_calibration("biased-ad-set")
        self.assertTrue(calibration["eligible"])
        self.assertEqual(calibration["sample_size"], 7)
        self.assertAlmostEqual(calibration["prediction_multiplier"], 0.80)
        self.assertGreater(calibration["interval_multiplier"], 1.0)
        self.assertLess(calibration["confidence_multiplier"], 1.0)

    def test_production_calibration_deduplicates_each_forecast_date(self):
        with core.connect() as db:
            run_ids = [
                db.execute(
                    "INSERT INTO model_training_runs(started_at, completed_at, status) VALUES(?,?,?)",
                    (f"2026-06-01T0{hour}:00:00+00:00", f"2026-06-01T0{hour}:01:00+00:00", "completed"),
                ).lastrowid
                for hour in (1, 2)
            ]
            for index in range(7):
                day = (date(2026, 6, 1) + timedelta(days=index)).isoformat()
                for run_index, (run_id, error) in enumerate(zip(run_ids, (8.0, -2.0))):
                    actual = 10.0
                    predicted = actual + error
                    db.execute(
                        """INSERT INTO forecast_daily_predictions(
                           training_run_id, generated_at, utm_ad_set_id, forecast_date, day_index,
                           predicted_leads, lower_estimate, upper_estimate, confidence_score,
                           model_used, sparse_warning, explanation, actual_leads, error,
                           absolute_error, squared_error, interval_hit, realized_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, f"2026-06-01T0{run_index + 1}:00:00+00:00",
                         "dedup-ad-set", day, index + 1, predicted, 0.0, 20.0, 70,
                         "test", 0, "test", actual, error, abs(error), error * error, 1, core.utc_now()),
                    )
        calibration = core._production_calibration("dedup-ad-set")
        self.assertTrue(calibration["eligible"])
        self.assertEqual(calibration["sample_size"], 7)
        self.assertAlmostEqual(calibration["bias"], -2.0)

    def test_change_point_model_reacts_to_sustained_level_shift(self):
        dates = pd.date_range("2026-05-01", periods=31, freq="D")
        values = pd.Series([4.0] * 28 + [12.0, 13.0, 14.0], dtype=float).to_numpy()
        details = core._change_point_details(values)
        forecast = core._change_point_forecast(
            values, dates, 14,
            {"campaign_values": values, "overall_values": values, "sparse": False},
        )
        self.assertTrue(details["detected"])
        self.assertEqual(details["direction"], "up")
        self.assertGreater(details["ratio"], 2.0)
        self.assertGreater(sum(forecast) / len(forecast), 10.0)

    def test_recency_metrics_give_recent_dates_more_influence(self):
        dates = list(pd.date_range("2026-05-01", periods=42, freq="D"))
        actual = [10.0] * 42
        old_misses = [0.0] * 14 + [10.0] * 28
        recent_misses = [10.0] * 28 + [0.0] * 14
        old = core._recency_weighted_metrics(actual, old_misses, [], [], dates)
        recent = core._recency_weighted_metrics(actual, recent_misses, [], [], dates)
        self.assertLess(old["recency_weighted_mae"], recent["recency_weighted_mae"])

    def test_production_bias_adjustment_applies_to_non_formula_models(self):
        calibration = {
            "eligible": True,
            "prediction_multiplier": 1.1,
            "bias_adjustment": 3.0,
        }
        weekday = core._apply_production_calibration([10.0, 12.0], calibration, "weekday-aware average")
        formula = core._apply_production_calibration([10.0, 12.0], calibration, "spend-adjusted formula 80/15/5")
        self.assertEqual(len(weekday), 2)
        self.assertEqual(len(formula), 2)
        self.assertAlmostEqual(weekday[0], 14.0)
        self.assertAlmostEqual(weekday[1], 16.2)
        self.assertAlmostEqual(formula[0], 11.0)
        self.assertAlmostEqual(formula[1], 13.2)

    def test_calibration_does_not_zero_out_low_volume_forecast(self):
        calibration = {"eligible": True, "prediction_multiplier": 0.8, "bias_adjustment": -0.33}
        adjusted = core._apply_production_calibration([0.3, 0.4, 0.25], calibration, "Log-link count regression")
        self.assertTrue(all(value > 0.0 for value in adjusted))
        for value, raw in zip(adjusted, [0.3, 0.4, 0.25]):
            self.assertGreaterEqual(value, raw * 0.8 * core.CALIBRATION_MIN_RETENTION - 1e-9)

    def test_calibration_still_reduces_overpredicted_forecast(self):
        calibration = {"eligible": True, "prediction_multiplier": 0.8, "bias_adjustment": -0.33}
        adjusted = core._apply_production_calibration([5.0], calibration, "Log-link count regression")
        self.assertLess(adjusted[0], 5.0 * 0.8)

    def test_sparse_backtest_metrics_store_fallback_with_valid_score(self):
        self.import_frame(frame_for(3), "sparse-metrics.csv")
        with core.connect() as db:
            metrics = db.execute("SELECT model_used, horizon_days, backtest_windows, selection_score FROM model_backtest_metrics").fetchall()
        expected_models = {
            "campaign + overall fallback", core.BREAKOUT_MODEL_NAME,
            core.ENSEMBLE_MODEL_NAME, *core.SPEND_ADJUSTED_MODEL_NAMES,
        }
        self.assertEqual(len(metrics), 2 * len(expected_models))
        self.assertEqual({row["model_used"] for row in metrics}, expected_models)
        self.assertTrue(all(row["horizon_days"] in (7, 14) and row["selection_score"] == 1.0 for row in metrics))

    def test_daily_forecast_preserves_history_shape(self):
        self.import_frame(shaped_frame_for(), "shaped-history.csv")
        with core.connect() as db:
            daily = db.execute("SELECT predicted_leads FROM forecast_daily_predictions ORDER BY day_index").fetchall()
        values = [row["predicted_leads"] for row in daily]
        self.assertGreater(max(values) - min(values), 2.0)

    def test_selection_prefers_clearly_better_model_over_ensemble(self):
        shaped = {"model_used": "weekday-aware average", "backtest_windows": 3, "mae": 1.0, "selection_score": 0.10}
        weak_ensemble = {"model_used": core.ENSEMBLE_MODEL_NAME, "backtest_windows": 3, "mae": 2.0,
                         "selection_score": 0.10 + core.ENSEMBLE_SCORE_TOLERANCE + 0.10}
        close_ensemble = {**weak_ensemble, "selection_score": 0.10 + core.ENSEMBLE_SCORE_TOLERANCE - 0.01}
        self.assertEqual(core._selected_metric([weak_ensemble, shaped])["model_used"], "weekday-aware average")
        self.assertEqual(core._selected_metric([close_ensemble, shaped])["model_used"], core.ENSEMBLE_MODEL_NAME)

    def test_ensemble_restores_variance_when_components_cancel(self):
        import numpy as np
        dates = pd.date_range("2026-06-01", periods=28, freq="D")
        values = np.asarray([2, 2, 2, 2, 2, 12, 14] * 4, dtype=float)
        original = core._forecast_candidate
        try:
            def anti_phase(model, vals, index, horizon, context=None):
                pattern = [12.0, 0.0] if model == "model-a" else [0.0, 12.0]
                return [pattern[step % 2] for step in range(horizon)]
            core._forecast_candidate = anti_phase
            forecast = core._ensemble_forecast(values, dates, 14, {
                "ensemble_components": [{"model": "model-a", "weight": 0.5}, {"model": "model-b", "weight": 0.5}],
                "campaign_values": values, "overall_values": values, "sparse": False,
            })
        finally:
            core._forecast_candidate = original
        self.assertEqual(len(forecast), 14)
        self.assertGreater(max(forecast) - min(forecast), 1.0)
        self.assertAlmostEqual(sum(forecast) / len(forecast), 6.0, delta=2.5)

    def test_damped_trend_model_tracks_sustained_ramp(self):
        import numpy as np
        dates = pd.date_range("2026-06-01", periods=28, freq="D")
        values = np.asarray([2.0 + i for i in range(28)], dtype=float)
        context = {"campaign_values": values, "overall_values": values, "sparse": False}
        forecast = core._forecast_candidate(core.TREND_MODEL_NAME, values, dates, 7, context)
        self.assertEqual(len(forecast), 7)
        recent_mean = float(np.mean(values[-7:]))
        self.assertGreater(sum(forecast) / 7, recent_mean)
        # Damping keeps the projection below a straight-line extrapolation.
        self.assertLess(max(forecast), values[-1] + 7.0 + 5.0)

    def test_portfolio_reconciliation_blends_daily_totals_toward_portfolio(self):
        def build_results():
            daily = [{"day_index": i, "predicted": 10.0, "lower": 5.0, "upper": 15.0, "explanation": "base"}
                     for i in range(1, 15)]
            horizons = [{"horizon": 7, "predicted": 70.0, "lower": 40.0, "upper": 100.0, "explanation": "base"},
                        {"horizon": 14, "predicted": 140.0, "lower": 80.0, "upper": 200.0, "explanation": "base"}]
            return [("ad", "camp", {"daily": daily, "horizons": horizons})]

        results = build_results()
        outcome = core._reconcile_forecasts_to_portfolio(
            results, {"predictions": [14.0] * 14, "model": "test-model", "wape": 0.2},
        )
        daily = results[0][2]["daily"]
        horizons = results[0][2]["horizons"]
        # Raw ratio 1.4 blended at 0.5 -> 1.2 per day.
        self.assertAlmostEqual(daily[0]["predicted"], 12.0)
        self.assertAlmostEqual(daily[0]["lower"], 6.0)
        self.assertAlmostEqual(horizons[0]["predicted"], 84.0)
        self.assertAlmostEqual(horizons[1]["predicted"], 168.0)
        self.assertAlmostEqual(sum(day["predicted"] for day in daily), horizons[1]["predicted"], delta=0.8)
        self.assertIn("Portfolio level reconciliation", daily[0]["explanation"])
        # Level-only: shape (all-equal days) must stay uniform after scaling.
        self.assertEqual(len({day["predicted"] for day in daily}), 1)
        self.assertAlmostEqual(outcome["mean_ratio"], 1.2)

        clipped = build_results()
        core._reconcile_forecasts_to_portfolio(
            clipped, {"predictions": [60.0] * 14, "model": "test-model", "wape": 0.2},
        )
        # Raw ratio 6.0 blended -> 3.5, clipped to the 1.5 ceiling.
        self.assertAlmostEqual(clipped[0][2]["daily"][0]["predicted"], 15.0)
        self.assertIsNone(core._reconcile_forecasts_to_portfolio(build_results(), None))

    def test_flat_forecasts_receive_heavier_selection_penalty(self):
        import numpy as np
        dates = pd.date_range("2026-05-01", periods=42, freq="D")
        values = np.asarray([2, 2, 2, 2, 2, 12, 14] * 6, dtype=float)
        context = {"campaign_values": values, "overall_values": values, "sparse": False}
        evaluated = core._rolling_origin_backtest(values, dates, False, context)
        for horizon in (7, 14):
            selected = core._selected_metric(evaluated[horizon])
            self.assertGreater(float(selected["forecast_variance_ratio"]), 0.35)
            self.assertLess(float(selected.get("flatness_penalty") or 0.0), 0.42)

    def test_sparse_ad_set_uses_low_confidence_fallback(self):
        self.import_frame(frame_for(3), "sparse.csv")
        with core.connect() as db:
            rows = db.execute("SELECT model_used, confidence_score, sparse_warning FROM forecasts").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row["model_used"] == "campaign + overall fallback" for row in rows))
        self.assertTrue(all(row["confidence_score"] <= 40 and row["sparse_warning"] == 1 for row in rows))

    def test_weekday_profile_calculates_learned_daily_factors(self):
        dates = pd.date_range("2026-06-01", periods=28, freq="D")
        weekly = [10, 8, 6, 4, 2, 12, 14]
        values = weekly * 4
        profile = core.calculate_weekday_profile(values, dates, values, values)
        self.assertEqual([row["weekday_name"] for row in profile["weekdays"]], core.WEEKDAY_NAMES)
        self.assertEqual([row["sample_days"] for row in profile["weekdays"]], [4] * 7)
        self.assertAlmostEqual(profile["weekdays"][0]["ad_set_average"], 10.0)
        self.assertAlmostEqual(sum(profile["factors"]), 7.0)
        self.assertGreater(profile["weekend_factor"], profile["weekday_factor"])

    def test_sparse_profile_blends_more_campaign_and_portfolio_signal(self):
        dates = pd.date_range("2026-06-01", periods=7, freq="D")
        own = [7, 0, 0, 0, 0, 0, 0]
        campaign = [0, 0, 0, 0, 0, 0, 7]
        portfolio = [1] * 7
        dense_profile = core.calculate_weekday_profile(own, dates, campaign, portfolio, sparse=False)
        sparse_profile = core.calculate_weekday_profile(own, dates, campaign, portfolio, sparse=True)
        self.assertGreater(sparse_profile["factors"][6], dense_profile["factors"][6])
        self.assertTrue(sparse_profile["sparse"])
        self.assertIn("20% ad set", sparse_profile["smoothing_note"])

    def test_weekday_shaping_is_learned_and_preserves_total(self):
        dates = pd.date_range("2026-06-01", periods=28, freq="D")
        values = [2, 2, 2, 2, 2, 12, 14] * 4
        profile = core.calculate_weekday_profile(values, dates, values, values)
        targets = pd.date_range("2026-06-29", periods=14, freq="D")
        shaped = core._shape_with_weekday_factors([10] * 14, targets, profile)
        self.assertAlmostEqual(sum(shaped), 140.0)
        self.assertGreater(shaped[5], shaped[4])
        self.assertGreater(max(shaped) - min(shaped), 1.0)

    def test_weekday_profile_api_structure(self):
        self.import_frame(shaped_frame_for(), "weekday-api.csv")
        from backend.app import weekday_profile as weekday_profile_endpoint
        profile = weekday_profile_endpoint("120235942906970078")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["ad_set_id"], "120235942906970078")
        self.assertEqual(len(profile["weekdays"]), 7)
        expected = {"weekday_index", "weekday_name", "ad_set_average", "campaign_average",
                    "portfolio_average", "smoothed_factor", "sample_days"}
        self.assertTrue(expected.issubset(profile["weekdays"][0]))
        self.assertIn("seasonality_strength", profile)
        self.assertIn("weekend_factor", profile)
        self.assertIn("weekday_factor", profile)


class AdDecisionTests(unittest.TestCase):
    """Boost/cut grading and the reallocation plan built on top of it."""

    ANCHOR = date(2026, 7, 20)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        core.DATA_DIR, core.DB_PATH = root, root / "test.db"
        core.UPLOAD_DIR, core.PREVIEW_DIR = root / "uploads", root / "previews"
        core.init_db()
        with core.connect() as db:
            db.execute(
                """INSERT INTO raw_uploads (id, file_name, stored_path, file_sha256, uploaded_at,
                   row_count, imported_count, status) VALUES (1,'ads.csv','/tmp/ads.csv','x','2026-07-21',0,0,'imported')"""
            )

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, ad_set, campaign_name, daily_spend, daily_leads, days=28, campaign_id="c1"):
        """Write `days` of flat spend + leads ending at ANCHOR."""
        with core.connect() as db:
            for offset in range(days):
                day = self.ANCHOR - timedelta(days=offset)
                db.execute(
                    """INSERT INTO daily_ad_performance (upload_id, day, campaign_id, campaign_name,
                       ad_set_id, amount_spent_usd, impressions, link_clicks, raw_json, created_at, updated_at)
                       VALUES (1,?,?,?,?,?,1000,20,'{}','2026-07-21','2026-07-21')""",
                    (day.isoformat(), campaign_id, campaign_name, ad_set, daily_spend),
                )
                for index in range(daily_leads):
                    db.execute(
                        """INSERT INTO lead_events (event_hash, status, created_at, utm_ad_set_id,
                           utm_campaign_id, utm_campaign, raw_json)
                           VALUES (?,'New',?,?,?,?,'{}')""",
                        (f"{ad_set}-{day}-{index}", f"{day} 10:00:00", ad_set, campaign_id, campaign_name),
                    )

    def test_grades_cheap_ad_as_scale_and_expensive_as_cut(self):
        self.seed("adA", "Leads | VISA | AU | KHM", daily_spend=10.0, daily_leads=20)  # $0.50
        self.seed("adB", "Leads | VISA | JP | FOR", daily_spend=10.0, daily_leads=1)   # $10.00
        result = core.get_ad_decisions(window_days=14)
        verdicts = {ad["ad_set_id"]: ad["verdict"] for ad in result["ads"]}
        self.assertEqual(verdicts["adA"], "scale")
        self.assertEqual(verdicts["adB"], "cut")

    def test_spend_with_zero_leads_is_cut_and_frees_full_budget(self):
        self.seed("adA", "Leads | VISA | AU | KHM", daily_spend=10.0, daily_leads=20)
        self.seed("adDead", "Leads | VISA | EU | FOR", daily_spend=7.0, daily_leads=0)
        result = core.get_ad_decisions(window_days=14)
        dead = next(ad for ad in result["ads"] if ad["ad_set_id"] == "adDead")
        self.assertEqual(dead["verdict"], "cut")
        self.assertIsNone(dead["cpl"])
        # zero-lead ads surrender 100% of their daily budget, not the usual 50%
        self.assertAlmostEqual(dead["suggested_daily_delta"], -7.0, places=2)

    def test_reallocation_moves_budget_into_scale_candidates(self):
        self.seed("adA", "Leads | VISA | AU | KHM", daily_spend=10.0, daily_leads=20)
        self.seed("adB", "Leads | VISA | JP | FOR", daily_spend=10.0, daily_leads=1)
        result = core.get_ad_decisions(window_days=14)
        realloc = result["reallocation"]
        self.assertGreater(realloc["freed_daily"], 0)
        self.assertTrue(realloc["moves"])
        self.assertEqual(realloc["moves"][0]["to_ad_set_id"], "adA")
        self.assertIn("Move", result["summary"]["headline"])

    def test_target_cpl_overrides_blended_benchmark(self):
        self.seed("adA", "Leads | VISA | AU | KHM", daily_spend=10.0, daily_leads=20)  # $0.50
        loose = core.get_ad_decisions(window_days=14)
        self.assertEqual(loose["summary"]["benchmark_source"], "blended")
        strict = core.get_ad_decisions(window_days=14, target_cpl=0.10)
        self.assertEqual(strict["summary"]["benchmark_source"], "target")
        self.assertAlmostEqual(strict["summary"]["benchmark_cpl"], 0.10, places=4)
        # against a $0.10 target, a $0.50 ad is no longer a bargain
        self.assertEqual(strict["ads"][0]["verdict"], "cut")

    def test_ad_set_with_no_spend_is_paused_not_free(self):
        self.seed("adA", "Leads | VISA | AU | KHM", daily_spend=10.0, daily_leads=20)
        with core.connect() as db:
            db.execute(
                """INSERT INTO lead_events (event_hash, status, created_at, utm_ad_set_id,
                   utm_campaign_id, utm_campaign, raw_json)
                   VALUES ('orphan-1','New','2026-07-18 09:00:00','adGhost','c1','Leads | VISA | UK | KHM','{}')"""
            )
        result = core.get_ad_decisions(window_days=14)
        ghost = next(ad for ad in result["ads"] if ad["ad_set_id"] == "adGhost")
        self.assertEqual(ghost["verdict"], "paused")
        self.assertIsNone(ghost["cpl"])
        self.assertEqual(ghost["suggested_daily_delta"], 0.0)

    def test_shared_campaign_name_produces_distinct_labels(self):
        self.seed("adOne", "Leads | VISA | HK | FOR", daily_spend=5.0, daily_leads=4)
        self.seed("adTwo", "Leads | VISA | HK | FOR", daily_spend=5.0, daily_leads=4)
        result = core.get_ad_decisions(window_days=14)
        labels = [ad["label"] for ad in result["ads"]]
        self.assertEqual(len(labels), len(set(labels)), f"labels collided: {labels}")

    def test_realistic_meta_ids_still_produce_distinct_labels(self):
        # Real Meta ad set IDs share a long prefix and an identical trailing account suffix,
        # so a fixed slice off either end labels every colliding row the same.
        self.seed("120249276038040078", "Engagement | VISA | ALL | KHM", daily_spend=2.0, daily_leads=1)
        self.seed("120246731559360078", "Engagement | VISA | ALL | KHM", daily_spend=2.0, daily_leads=3)
        labels = [ad["label"] for ad in core.get_ad_decisions(window_days=14)["ads"]]
        self.assertEqual(len(labels), len(set(labels)), f"labels collided: {labels}")
        # Every colliding row must carry a fragment chosen against its siblings. The shared
        # trailing "0078" is what a naive slice produces and means the row fell through.
        suffixes = [label.rsplit(" · ", 1)[-1] for label in labels if " · " in label]
        self.assertEqual(len(suffixes), 2, f"expected both rows suffixed: {labels}")
        self.assertNotIn("0078", suffixes, f"a row fell back to the shared suffix: {labels}")

    def test_distinguishing_fragment_ignores_shared_prefix_and_suffix(self):
        self.assertNotEqual(
            core._distinguishing_id_fragment("120249276038040078", ["120246731559360078"]),
            core._distinguishing_id_fragment("120246731559360078", ["120249276038040078"]),
        )
        self.assertEqual(core._distinguishing_id_fragment("120249276038040078", []), "0078")

    def test_returns_unavailable_without_spend_data(self):
        self.assertFalse(core.get_ad_decisions()["available"])


def ad_export_csv(rows, columns=None):
    """Build an in-memory Meta ad export from dicts."""
    columns = columns or list(rows[0].keys())
    frame = pd.DataFrame(rows, columns=columns)
    return io.BytesIO(frame.to_csv(index=False).encode("utf-8"))


def ad_row(day, ad_set, ad_id, spend, *, campaign="Leads | VISA | JP | FOR", campaign_id="c1",
           leads="", impressions=1000, reach=800, budget="", level="ad", status="active"):
    return {
        "Campaign name": campaign, "Campaign ID": campaign_id, "Ad set ID": ad_set, "Ad ID": ad_id,
        "Day": day, "Delivery status": status, "Delivery level": level,
        "Amount spent (USD)": spend, "Messaging conversations started": "",
        "Cost per messaging conversation started": "", "Reach": reach, "Frequency": 1.25,
        "Impressions": impressions, "Leads": leads, "Cost per lead": "", "Link clicks": 10,
        "Ad Set Budget": budget, "Ad Set Budget Type": "Daily" if budget != "" else "",
    }


class IsolatedDbTestCase(unittest.TestCase):
    """Point core at a throwaway database so reads never touch the real one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        core.DATA_DIR, core.DB_PATH = root, root / "test.db"
        core.UPLOAD_DIR, core.PREVIEW_DIR = root / "uploads", root / "previews"
        core.init_db()

    def tearDown(self):
        self.temp.cleanup()


class ManualLeadEntryTests(IsolatedDbTestCase):
    def lead_payload(self, **overrides):
        payload = {
            "status": "New",
            "lead_quality": "Pending Review",
            "created_at": "2026-06-08T09:30:00",
            "customer_name": "Manual Customer",
            "utm_campaign": "Leads | VISA | JP | FOR",
            "utm_campaign_id": "120235942262330078",
            "utm_ad_set_id": "120235942906970078",
            "utm_ad_id": "120235942976420078",
            "fb_ad_title": "VF008C1 - TAFVJ01",
            "amount_spent_usd": 4.25,
            "platform": "manual",
        }
        payload.update(overrides)
        return payload

    def test_create_manual_lead_writes_the_board_fields_and_rejects_duplicates(self):
        created = core.create_lead_event(self.lead_payload(), retrain=False)
        self.assertEqual(created["lead"]["customer_name"], "Manual Customer")
        self.assertEqual(created["lead"]["lead_quality"], "Pending Review")
        self.assertEqual(created["lead"]["utm_ad_set_id"], "120235942906970078")
        self.assertAlmostEqual(created["lead"]["amount_spent_usd"], 4.25)

        with core.connect() as db:
            raw = json.loads(db.execute("SELECT raw_json FROM lead_events").fetchone()[0])
        self.assertTrue(raw["Manual Entry"])
        self.assertEqual(raw["Platform"], "manual")

        with self.assertRaisesRegex(ValueError, "already exists"):
            core.create_lead_event(self.lead_payload(), retrain=False)

    def test_upload_delete_does_not_remove_manual_leads(self):
        manual = core.create_lead_event(self.lead_payload(), retrain=False)
        upload_path = core.DATA_DIR / "traffic.csv"
        upload_path.write_text("placeholder", encoding="utf-8")

        with core.connect() as db:
            upload_id = db.execute(
                """INSERT INTO raw_uploads(file_name, stored_path, file_sha256, file_type, uploaded_at,
                   row_count, imported_count) VALUES(?,?,?,?,?,?,?)""",
                ("traffic.csv", str(upload_path), "hash", core.CUSTOMER_TRAFFIC_TYPE, core.utc_now(), 1, 1),
            ).lastrowid
            imported_id = db.execute(
                """INSERT INTO lead_events(event_hash, platform, status, created_at, updated_at,
                   customer_name, utm_campaign, utm_campaign_id, utm_ad_set_id, utm_ad_id,
                   fb_ad_title, amount_spent_usd, raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "imported-orphan", "messenger", "New", "2026-06-08T10:00:00", None,
                    "Imported Customer", "Leads | VISA | JP | FOR", "campaign", "adset", "ad",
                    "title", None, "{}",
                ),
            ).lastrowid
            db.execute("INSERT INTO upload_lead_links(upload_id, lead_id) VALUES(?,?)", (upload_id, imported_id))

        with mock.patch("backend.core.rebuild_aggregates"), mock.patch("backend.core.train_models", return_value={}):
            core.delete_upload(int(upload_id))

        with core.connect() as db:
            remaining = [dict(row) for row in db.execute("SELECT id, customer_name FROM lead_events ORDER BY id").fetchall()]
        self.assertEqual(remaining, [{"id": manual["created"], "customer_name": "Manual Customer"}])


class CurrencyParsingTests(IsolatedDbTestCase):
    """Accounting-formatted currency from exports that passed through Excel."""

    def test_parses_currency_symbols_and_separators(self):
        self.assertEqual(core._safe_number(" $0.05 "), 0.05)
        self.assertEqual(core._safe_number(" $1,234.50 "), 1234.5)
        self.assertEqual(core._safe_number(" $2.00 "), 2.0)

    def test_accounting_dash_is_zero_not_missing(self):
        # Excel writes a zero currency cell as "$-". Treating it as null would confuse
        # "spent nothing" with "no data" and drop the row as missing spend.
        self.assertEqual(core._safe_number(" $-   "), 0.0)

    def test_bare_dash_stays_ambiguous(self):
        self.assertIsNone(core._safe_number("-"))
        self.assertIsNone(core._safe_number("--"))
        self.assertIsNone(core._safe_number(""))

    def test_parenthesised_value_is_negative(self):
        self.assertEqual(core._safe_number("($5.00)"), -5.0)

    def test_currency_formatted_export_imports(self):
        rows = [
            ad_row("2026-06-01", "adA", "ad1", " $0.05 ", budget=" $2.00 "),
            ad_row("2026-06-01", "adA", "ad2", " $-   ", budget=" $2.00 "),
            ad_row("2026-06-02", "adA", "ad3", " $1.50 ", budget=" $2.00 "),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertEqual(frame.attrs["cleaning_report"]["missing_spend_rows"], 0)
        self.assertAlmostEqual(frame["Amount spent (USD)"].sum(), 1.55)


class AdGrainRollupTests(IsolatedDbTestCase):
    """Newer exports break spend out per ad and must aggregate, not deduplicate."""

    def test_additive_metrics_sum_across_ads(self):
        rows = [
            ad_row("2026-06-01", "adA", "ad1", 1.00, leads=2, impressions=100),
            ad_row("2026-06-01", "adA", "ad2", 2.50, leads=1, impressions=250),
            ad_row("2026-06-01", "adA", "ad3", 0.50, leads="", impressions=50),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertEqual(len(frame), 1)
        row = frame.iloc[0]
        self.assertAlmostEqual(row["Amount spent (USD)"], 4.00)
        self.assertAlmostEqual(row["Leads"], 3.0)
        self.assertAlmostEqual(row["Impressions"], 400.0)

    def test_no_spend_is_lost_to_deduplication(self):
        rows = [ad_row("2026-06-01", "adA", f"ad{i}", 1.25) for i in range(8)]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        report = frame.attrs["cleaning_report"]
        self.assertAlmostEqual(frame["Amount spent (USD)"].sum(), 10.0)
        self.assertEqual(report["ad_rows_collapsed"], 7)
        # The historical dedup must now be a no-op; if it fires, rows are being discarded.
        self.assertEqual(report["duplicates_removed"], 0)

    def test_reach_is_dropped_rather_than_summed(self):
        # Reach counts distinct people, so adding it across ads double-counts anyone they shared.
        rows = [
            ad_row("2026-06-01", "adA", "ad1", 1.0, reach=500),
            ad_row("2026-06-01", "adA", "ad2", 1.0, reach=500),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertTrue(pd.isna(frame.iloc[0]["Reach"]))
        self.assertTrue(pd.isna(frame.iloc[0]["Frequency"]))

    def test_cost_per_lead_is_recomputed_not_summed(self):
        rows = [
            ad_row("2026-06-01", "adA", "ad1", 6.0, leads=2),
            ad_row("2026-06-01", "adA", "ad2", 2.0, leads=2),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertAlmostEqual(frame.iloc[0]["Cost per lead"], 2.0)

    def test_zero_leads_leaves_cost_per_lead_blank(self):
        rows = [ad_row("2026-06-01", "adA", "ad1", 6.0, leads=0)]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertTrue(pd.isna(frame.iloc[0]["Cost per lead"]))

    def test_distinct_ad_sets_stay_separate(self):
        rows = [
            ad_row("2026-06-01", "adA", "ad1", 1.0),
            ad_row("2026-06-01", "adB", "ad2", 2.0),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertEqual(len(frame), 2)
        self.assertEqual(set(frame["Ad set ID"]), {"adA", "adB"})

    def test_delivery_status_takes_the_most_permissive(self):
        rows = [
            ad_row("2026-06-01", "adA", "ad1", 1.0, status="archived"),
            ad_row("2026-06-01", "adA", "ad2", 1.0, status="active"),
        ]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        self.assertEqual(frame.iloc[0]["Delivery status"], "active")
        self.assertEqual(frame.iloc[0]["Delivery level"], "adset")

    def test_ad_set_grain_export_is_left_alone(self):
        rows = [
            ad_row("2026-06-01", "120235942906970078", "", 1.0, level="adset", reach=500),
            ad_row("2026-06-02", "120235942906970078", "", 2.0, level="adset", reach=600),
        ]
        columns = [column for column in rows[0] if column not in {"Ad ID", "Ad Set Budget", "Ad Set Budget Type"}]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows, columns), ".csv")
        report = frame.attrs["cleaning_report"]
        self.assertFalse(report["ad_grain_input"])
        self.assertEqual(report["ad_rows_collapsed"], 0)
        self.assertEqual(sorted(frame["Reach"]), [500.0, 600.0])

    def test_colliding_grain_keys_alone_do_not_imply_ad_grain(self):
        # Ad-set exports whose IDs arrived as scientific notation get those columns blanked,
        # so their rows collide by grain key. Rolling them up would merge ad sets that
        # `_repair_ad_performance_attribution` is about to separate again.
        frame = pd.DataFrame([
            {"Delivery level": "adset", "Ad ID": "", "Day": pd.Timestamp("2026-06-01"),
             "Campaign ID": "", "Ad set ID": "", "Campaign name": "Leads | VISA | HK | FOR"},
            {"Delivery level": "adset", "Ad ID": "", "Day": pd.Timestamp("2026-06-01"),
             "Campaign ID": "", "Ad set ID": "", "Campaign name": "Leads | VISA | HK | FOR"},
        ])
        self.assertTrue(frame.duplicated(core.AD_ROLLUP_KEY).any())
        self.assertFalse(core._is_ad_grain(frame))


class CampaignNameOnlyExportTests(IsolatedDbTestCase):
    """Ad-set-level Meta exports that carry `Campaign name` but no `Campaign ID`.

    This is the shape of the Ad-Set-Performance-and-Traffic workbooks. Both the detector and
    read_ad_performance_tabular used to hard-require `Campaign ID`, which rejected the file
    before _repair_ad_performance_attribution -- whose whole job is reconstructing that ID from
    the campaign name -- ever ran. The repair was only reachable for files that had the column
    present but blank, so this shape was unimportable.
    """

    AD_SET = "120235942906970078"
    CAMPAIGN = "Leads | VISA | JP | FOR"

    def setUp(self):
        super().setUp()
        # _historical_campaign_ad_set_options reads lead_events -- not daily_ad_performance --
        # so this is what makes a campaign name resolvable to an ID.
        with core.connect() as db:
            db.execute(
                """INSERT INTO lead_events (event_hash, platform, status, created_at, updated_at,
                                            customer_name, utm_campaign, utm_campaign_id,
                                            utm_ad_set_id, raw_json)
                   VALUES ('h1', 'messenger', 'New', '2026-07-01 10:00:00', '2026-07-01 10:00:00',
                           'Someone', ?, 'c99', ?, '{}')""",
                (self.CAMPAIGN, self.AD_SET),
            )

    @staticmethod
    def _rows(day="2026-08-01", campaign="Leads | VISA | JP | FOR", spend=3.25):
        # Deliberately mirrors the real export: no Campaign ID, no Ad ID, no Leads column.
        return [{
            "Campaign name": campaign, "Ad set ID": "120235942906970078", "Day": day,
            "Delivery status": "active", "Delivery level": "adset",
            "Amount spent (USD)": spend, "Messaging conversations started": 4,
            "Cost per messaging conversation started": 0.8125, "Reach": 1249,
            "Frequency": 1.283427, "Impressions": 1603,
            "Ad Set Budget": 3.5, "Ad Set Budget Type": "Daily",
        }]

    def test_detected_as_ad_performance_without_campaign_id(self):
        self.assertEqual(
            core.detect_upload_type_from_columns(self._rows()[0].keys()),
            core.AD_PERFORMANCE_TYPE,
        )

    def test_campaign_id_is_recovered_from_a_known_campaign(self):
        frame = core.read_ad_performance_tabular(ad_export_csv(self._rows()), ".csv")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Campaign ID"], "c99")
        self.assertEqual(frame.attrs["cleaning_report"]["recovered_campaign_ids"], 1)
        self.assertEqual(frame.attrs["cleaning_report"]["unresolved_attribution_rows"], 0)

    def test_single_ad_set_row_keeps_reach_and_frequency(self):
        """No Ad ID column means nothing to collapse, so real Reach must survive."""
        frame = core.read_ad_performance_tabular(ad_export_csv(self._rows()), ".csv")
        self.assertAlmostEqual(frame.iloc[0]["Reach"], 1249.0)
        self.assertAlmostEqual(frame.iloc[0]["Frequency"], 1.283427, places=5)

    def test_local_leadlens_ad_export_headers_can_be_reimported(self):
        rows = [{
            "Day": "Jun 6, 2026",
            "Campaign": self.CAMPAIGN,
            "Campaign ID": "c99",
            "Ad set ID": self.AD_SET,
            "Spend": "$3.87",
            "Leads": 3,
            "CPL": "$1.29",
            "Reach": "1,292",
            "Impressions": "1,650",
            "Frequency": 1.2771,
            "Budget": "$3.50 / Daily",
            "days_since_adset_started": 202,
            "ad_set_change_recency": "15_59_days",
            "ad_change_recency": "15_59_days",
        }]
        self.assertEqual(core.detect_upload_type_from_columns(rows[0].keys()), core.AD_PERFORMANCE_TYPE)
        frame = core.read_ad_performance_tabular(ad_export_csv(rows), ".csv")
        row = frame.iloc[0]
        self.assertAlmostEqual(row["Amount spent (USD)"], 3.87)
        self.assertAlmostEqual(row["Cost per lead"], 1.29)
        self.assertAlmostEqual(row["Reach"], 1292.0)
        self.assertAlmostEqual(row["Impressions"], 1650.0)
        self.assertAlmostEqual(row["Ad Set Budget"], 3.5)
        self.assertEqual(row["Ad Set Budget Type"], "Daily")

    def test_local_leadlens_ad_export_imports_derived_columns_too(self):
        rows = []
        for offset in range(8):
            day = pd.Timestamp("2026-06-06") + pd.Timedelta(days=offset)
            rows.append({
                "Day": day.strftime("%Y-%m-%d"),
                "Campaign": self.CAMPAIGN,
                "Campaign ID": "c99",
                "Ad set ID": self.AD_SET,
                "Spend": "$3.87",
                "Leads": 3,
                "CPL": "$1.29",
                "Reach": "1,292",
                "Impressions": "1,650",
                "Frequency": 1.2771,
                "Budget": "$3.50 / Daily",
                "days_since_adset_started": 202 + offset,
                "ad_set_change_recency": "0_3_days" if offset < 4 else "4_7_days",
                "ad_change_recency": "no_recent_change",
            })
        content = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
        preview = core.preview_file(content, "ad_performance-2026-08-18.csv")

        self.assertEqual(preview["file_type"], core.AD_PERFORMANCE_TYPE)
        self.assertIn("days_since_adset_started", preview["columns"])
        self.assertEqual(preview["start_dates"], 1)
        self.assertEqual(preview["change_events_by_scope"], {"ad_set": 1})
        with mock.patch.object(core, "train_models", return_value={"status": "skipped"}):
            result = core.import_preview(preview["token"], "ad_performance-2026-08-18.csv")

        self.assertEqual(result["file_type"], core.AD_PERFORMANCE_TYPE)
        self.assertEqual(result["inserted"], 8)
        self.assertEqual(result["start_dates_inserted"], 1)
        self.assertEqual(result["change_events_inserted"], 1)
        self.assertEqual(core.list_ad_set_start_dates(self.AD_SET)[0]["start_date"], "2025-11-16")
        self.assertEqual(core.list_change_events(scope="ad_set", ad_set_id=self.AD_SET)[0]["start_date"], "2026-06-06")
        board = core.get_dataset_rows("ad_performance_export", limit=1)
        row = board["rows"][0]
        self.assertNotIn("ad_id", row)
        self.assertNotIn("fb_ad_title", row)
        self.assertNotIn("messaging_conversations_started", row)
        self.assertNotIn("cost_per_messaging_conversation_started", row)
        self.assertEqual(row["days_since_adset_started"], 202)
        self.assertEqual(row["ad_set_change_recency"], "0_3_days")
        self.assertEqual(row["ad_change_recency"], "no_recent_change")

    def test_existing_leadlens_ad_export_backfills_blank_derived_columns(self):
        rows = []
        for offset in range(8):
            day = pd.Timestamp("2026-06-06") + pd.Timedelta(days=offset)
            rows.append({
                "Day": day.strftime("%Y-%m-%d"),
                "Campaign": self.CAMPAIGN,
                "Campaign ID": "c99",
                "Ad set ID": self.AD_SET,
                "Spend": "$3.87",
                "Leads": 3,
                "CPL": "$1.29",
                "Reach": "1,292",
                "Impressions": "1,650",
                "Frequency": 1.2771,
                "Budget": "$3.50 / Daily",
                "days_since_adset_started": 202 + offset,
                "ad_set_change_recency": "0_3_days" if offset < 4 else "4_7_days",
                "ad_change_recency": "no_recent_change",
            })
        content = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
        preview = core.preview_file(content, "ad_performance-2026-08-18.csv")
        with mock.patch.object(core, "train_models", return_value={"status": "skipped"}):
            core.import_preview(preview["token"], "ad_performance-2026-08-18.csv")

        with core.connect() as db:
            db.execute(
                """UPDATE daily_ad_performance
                   SET days_since_adset_started_imported=NULL,
                       ad_set_change_recency_imported=NULL,
                       ad_change_recency_imported=NULL"""
            )
            blank = db.execute(
                """SELECT days_since_adset_started_imported,
                          ad_set_change_recency_imported,
                          ad_change_recency_imported
                   FROM daily_ad_performance
                   LIMIT 1"""
            ).fetchone()
            self.assertIsNone(blank["days_since_adset_started_imported"])
            self.assertIsNone(blank["ad_set_change_recency_imported"])
            self.assertIsNone(blank["ad_change_recency_imported"])
            updated = core._backfill_imported_ad_performance_derived_values(db)

        self.assertEqual(updated, 8)
        board = core.get_dataset_rows("ad_performance_export", limit=1)
        row = board["rows"][0]
        self.assertEqual(row["days_since_adset_started"], 202)
        self.assertEqual(row["ad_set_change_recency"], "0_3_days")
        self.assertEqual(row["ad_change_recency"], "no_recent_change")

    def test_local_leadlens_derived_export_without_spend_is_accepted(self):
        rows = []
        for offset in range(8):
            day = date(2026, 6, 6) + timedelta(days=offset)
            rows.append({
                "Day": day.strftime("%-d-%b-%y") if os.name != "nt" else day.strftime("%#d-%b-%y"),
                "Campaign": self.CAMPAIGN,
                "Campaign ID": "1.20236E+17",
                "Ad set ID": "1.20236E+17",
                "days_since_adset_started": 202 + offset,
                "ad_set_change_recency": "0_3_days" if offset < 4 else "4_7_days",
                "ad_change_recency": "no_recent_change",
            })
        content = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")

        preview = core.preview_file(content, "leadlens-ad_performance-2026-08-18.csv")

        self.assertEqual(preview["file_type"], core.LEADLENS_DERIVED_TYPE)
        self.assertEqual(preview["clean_rows"], 8)
        self.assertEqual(preview["unique_ad_sets"], 1)
        self.assertEqual(preview["start_dates"], 1)
        self.assertEqual(preview["change_events_by_scope"], {"ad_set": 1})

    def test_leadlens_derived_import_writes_starts_and_reconstructed_changes(self):
        rows = []
        for offset in range(8):
            day = pd.Timestamp("2026-06-06") + pd.Timedelta(days=offset)
            rows.append({
                "Day": day.strftime("%Y-%m-%d"),
                "Campaign": self.CAMPAIGN,
                "Campaign ID": "1.20236E+17",
                "Ad set ID": "1.20236E+17",
                "days_since_adset_started": 202 + offset,
                "ad_set_change_recency": "0_3_days" if offset < 4 else "4_7_days",
                "ad_change_recency": "no_recent_change",
            })
        content = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
        preview = core.preview_file(content, "leadlens-derived.csv")

        with mock.patch.object(core, "train_models", return_value={"status": "skipped"}):
            result = core.import_preview(preview["token"], "leadlens-derived.csv")

        self.assertEqual(result["file_type"], core.LEADLENS_DERIVED_TYPE)
        self.assertEqual(result["start_dates_inserted"], 1)
        self.assertEqual(result["change_events_inserted"], 1)
        starts = core.list_ad_set_start_dates(self.AD_SET)
        self.assertEqual(starts[0]["start_date"], "2025-11-16")
        events = core.list_change_events(scope="ad_set", ad_set_id=self.AD_SET)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-06-06")

    def test_unknown_campaign_is_rejected_not_guessed(self):
        """Loosening the detector must not let unattributable rows into the database.

        An unrecognised campaign has no ID to recover, so every row fails the
        `Campaign ID != ''` check and the read raises rather than importing rows with no
        attribution. Failing loudly is the point: silently dropping them would look like a
        successful import of a file whose spend went nowhere.
        """
        with self.assertRaises(ValueError):
            core.read_ad_performance_tabular(
                ad_export_csv(self._rows(campaign="Leads | VISA | NEVER SEEN | XX")), ".csv"
            )

    def test_a_file_with_no_campaign_column_at_all_still_fails_detection(self):
        columns = [key for key in self._rows()[0] if key != "Campaign name"]
        with self.assertRaises(ValueError):
            core.detect_upload_type_from_columns(columns)


class SupersededRowTests(IsolatedDbTestCase):
    """A true-ID export must retire the guesses an earlier export left behind."""

    def stored(self, upload_id, day, campaign_id, ad_set_id, spend,
               campaign_name="Engagement | VISA | ALL | KHM"):
        with core.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO raw_uploads (id, file_name, stored_path, file_sha256,
                   uploaded_at, row_count, imported_count, status)
                   VALUES (?,?,'/tmp/x','h','2026-07-21',0,0,'imported')""",
                (upload_id, f"upload{upload_id}.csv"),
            )
            db.execute(
                """INSERT INTO daily_ad_performance (upload_id, day, campaign_id, campaign_name,
                   ad_set_id, amount_spent_usd, raw_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,'{}','2026-07-21','2026-07-21')""",
                (upload_id, day, campaign_id, campaign_name, ad_set_id, spend),
            )

    def rows(self):
        with core.connect() as db:
            return [(r["ad_set_id"], r["day"]) for r in
                    db.execute("SELECT ad_set_id, day FROM daily_ad_performance ORDER BY ad_set_id, day")]

    def test_guessed_row_is_retired_by_the_authoritative_import(self):
        # The repair guesses the campaign ID from the same lookup as the ad set ID, so the
        # superseded row carries a different campaign ID than the authoritative one.
        self.stored(1, "2026-07-12", "120246730013800078", "guessedAdSet", 1.18)
        self.stored(2, "2026-07-12", "120249276038010078", "realAdSet", 1.18)
        with core.connect() as db:
            removed = core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 0})
        self.assertEqual(removed, 1)
        self.assertEqual(self.rows(), [("realAdSet", "2026-07-12")])

    def test_untouched_campaign_days_survive(self):
        self.stored(1, "2026-07-12", "c1", "guessedAdSet", 1.18)
        self.stored(1, "2026-06-01", "c9", "otherAdSet", 5.00, campaign_name="Leads | VISA | AU | KHM")
        self.stored(2, "2026-07-12", "c1", "realAdSet", 1.18)
        with core.connect() as db:
            core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 0})
        self.assertIn(("otherAdSet", "2026-06-01"), self.rows())

    def test_a_different_campaign_on_the_same_day_survives(self):
        self.stored(1, "2026-07-12", "c9", "otherAdSet", 5.00, campaign_name="Leads | VISA | AU | KHM")
        self.stored(2, "2026-07-12", "c1", "realAdSet", 1.18)
        with core.connect() as db:
            removed = core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 0})
        self.assertEqual(removed, 0)
        self.assertIn(("otherAdSet", "2026-07-12"), self.rows())

    def test_a_day_the_export_does_not_cover_survives(self):
        self.stored(1, "2026-05-01", "c1", "guessedAdSet", 1.18)
        self.stored(2, "2026-07-12", "c1", "realAdSet", 1.18)
        with core.connect() as db:
            removed = core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 0})
        self.assertEqual(removed, 0)
        self.assertIn(("guessedAdSet", "2026-05-01"), self.rows())

    def test_an_export_with_guessed_ids_supersedes_nothing(self):
        # A file whose own ad set IDs had to be inferred cannot invalidate stored data.
        self.stored(1, "2026-07-12", "c1", "guessedAdSet", 1.18)
        self.stored(2, "2026-07-12", "c1", "realAdSet", 1.18)
        with core.connect() as db:
            removed = core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 5})
        self.assertEqual(removed, 0)
        self.assertEqual(len(self.rows()), 2)

    def test_reimport_of_the_same_upload_removes_nothing(self):
        self.stored(2, "2026-07-12", "c1", "realAdSet", 1.18)
        with core.connect() as db:
            removed = core._remove_superseded_ad_rows(db, 2, {"recovered_ad_set_ids": 0})
        self.assertEqual(removed, 0)
        self.assertEqual(len(self.rows()), 1)


class BudgetPeriodTests(unittest.TestCase):
    """Dated budget periods derived from the export's Ad Set Budget column."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        core.DATA_DIR, core.DB_PATH = root, root / "test.db"
        core.UPLOAD_DIR, core.PREVIEW_DIR = root / "uploads", root / "previews"
        core.init_db()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def budget_frame(entries, ad_set="adA"):
        """entries: list of (day_offset_count, budget, daily_spend)."""
        rows = []
        day = date(2026, 6, 1)
        for count, budget, spend in entries:
            for _ in range(count):
                rows.append({"Ad set ID": ad_set, "Day": pd.Timestamp(day), "Ad Set Budget": budget,
                             "Ad Set Budget Type": "Daily", "Amount spent (USD)": spend})
                day += timedelta(days=1)
        return pd.DataFrame(rows)

    def test_constant_budget_produces_one_period(self):
        periods = core.derive_budget_periods(self.budget_frame([(20, 5.0, 4.8)]))
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["start_date"], "2026-06-01")
        self.assertEqual(periods[0]["end_date"], "2026-06-20")
        self.assertEqual(periods[0]["daily_budget"], 5.0)

    def test_budget_change_splits_on_the_day_it_changed(self):
        periods = core.derive_budget_periods(self.budget_frame([(15, 2.0, 1.9), (15, 5.0, 4.8)]))
        self.assertEqual(len(periods), 2)
        self.assertEqual((periods[0]["start_date"], periods[0]["end_date"]), ("2026-06-01", "2026-06-15"))
        self.assertEqual(periods[0]["daily_budget"], 2.0)
        self.assertEqual((periods[1]["start_date"], periods[1]["end_date"]), ("2026-06-16", "2026-06-30"))
        self.assertEqual(periods[1]["daily_budget"], 5.0)

    def test_periods_do_not_overlap(self):
        periods = core.derive_budget_periods(self.budget_frame([(10, 2.0, 1.9), (10, 5.0, 4.8), (10, 3.0, 2.9)]))
        self.assertEqual(len(periods), 3)
        for earlier, later in zip(periods, periods[1:]):
            self.assertLess(earlier["end_date"], later["start_date"])

    def test_sustained_overspend_is_flagged(self):
        # A daily budget can be exceeded on any single day but not on average.
        periods = core.derive_budget_periods(self.budget_frame([(20, 3.0, 5.0)]))
        self.assertTrue(periods[0]["spend_conflict"])
        self.assertAlmostEqual(periods[0]["mean_daily_spend"], 5.0)

    def test_ordinary_delivery_noise_is_not_flagged(self):
        periods = core.derive_budget_periods(self.budget_frame([(20, 3.0, 3.02)]))
        self.assertFalse(periods[0]["spend_conflict"])

    def test_single_expensive_day_is_not_flagged(self):
        frame = self.budget_frame([(19, 3.0, 2.9), (1, 3.0, 40.0)])
        self.assertFalse(core.derive_budget_periods(frame)[0]["spend_conflict"])

    def test_ad_set_that_settled_onto_its_budget_is_not_flagged(self):
        # Meta overspends while delivery calibrates, then converges. Judging the whole span would
        # grade that ramp as a permanent contradiction; only the recent days should decide.
        frame = self.budget_frame([(35, 3.0, 5.0), (14, 3.0, 2.9)])
        period = core.derive_budget_periods(frame)[0]
        self.assertFalse(period["spend_conflict"])
        self.assertAlmostEqual(period["recent_mean_daily_spend"], 2.9)
        self.assertEqual(period["recent_days"], 14)
        # The full-span mean stays available as context even though it no longer drives the flag.
        self.assertGreater(period["mean_daily_spend"], 3.0)
        self.assertEqual(period["observed_days"], 49)

    def test_overspend_only_in_recent_days_is_flagged(self):
        # The mirror image: compliant for weeks, over budget now. The full-span mean would hide it.
        frame = self.budget_frame([(120, 3.0, 2.9), (14, 3.0, 5.0)])
        period = core.derive_budget_periods(frame)[0]
        self.assertTrue(period["spend_conflict"])
        self.assertAlmostEqual(period["recent_mean_daily_spend"], 5.0)
        self.assertLess(period["mean_daily_spend"], 3.0 * core.BUDGET_CONFLICT_MEAN_MARGIN)

    def test_recent_window_never_exceeds_the_period(self):
        period = core.derive_budget_periods(self.budget_frame([(9, 3.0, 2.9)]))[0]
        self.assertEqual(period["recent_days"], 9)
        self.assertEqual(period["observed_days"], 9)

    def test_recent_window_is_scoped_to_its_own_period(self):
        # A later period must not borrow the tail of the one before it.
        periods = core.derive_budget_periods(self.budget_frame([(20, 3.0, 9.0), (8, 6.0, 5.5)]))
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[1]["recent_days"], 8)
        self.assertAlmostEqual(periods[1]["recent_mean_daily_spend"], 5.5)
        self.assertFalse(periods[1]["spend_conflict"])

    def test_frame_attrs_with_dataframe_do_not_break_grouping(self):
        frame = self.budget_frame([(20, 5.0, 4.8)])
        frame.attrs["ad_level_rows"] = pd.DataFrame({"ad_id": ["ad1"]})
        periods = core.derive_budget_periods(frame)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["daily_budget"], 5.0)

    def test_reimport_is_idempotent(self):
        periods = core.derive_budget_periods(self.budget_frame([(15, 2.0, 1.9), (15, 5.0, 4.8)]))
        core.store_derived_budget_periods(periods)
        core.store_derived_budget_periods(periods)
        self.assertEqual(len(core.list_budget_periods("adA")), 2)

    def test_manual_periods_are_never_overwritten(self):
        core.save_budget_period("adA", "2026-06-01", "2026-06-30", 9.0)
        summary = core.store_derived_budget_periods(
            core.derive_budget_periods(self.budget_frame([(20, 5.0, 4.8)]))
        )
        stored = core.list_budget_periods("adA")
        self.assertEqual(summary["written"], 0)
        self.assertEqual(summary["skipped_manual"], 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["daily_budget"], 9.0)
        self.assertEqual(stored[0]["source"], "manual")

    def test_editing_a_derived_period_makes_it_manual(self):
        core.store_derived_budget_periods(
            core.derive_budget_periods(self.budget_frame([(20, 5.0, 4.8)]))
        )
        derived = core.list_budget_periods("adA")[0]
        self.assertEqual(derived["source"], "meta_export")
        updated = core.save_budget_period(
            "adA", derived["start_date"], derived["end_date"], 7.0, period_id=derived["id"]
        )
        self.assertEqual(updated["source"], "manual")
        self.assertEqual(updated["daily_budget"], 7.0)

    def test_derived_history_outside_the_window_survives(self):
        core.store_derived_budget_periods(core.derive_budget_periods(self.budget_frame([(20, 5.0, 4.8)])))
        later = pd.DataFrame([
            {"Ad set ID": "adA", "Day": pd.Timestamp(date(2026, 8, 1) + timedelta(days=i)),
             "Ad Set Budget": 8.0, "Ad Set Budget Type": "Daily", "Amount spent (USD)": 7.9}
            for i in range(10)
        ])
        core.store_derived_budget_periods(core.derive_budget_periods(later))
        self.assertEqual(len(core.list_budget_periods("adA")), 2)

    def test_export_without_budget_column_yields_no_periods(self):
        rows = [ad_row("2026-06-01", "adA", "ad1", 1.0)]
        columns = [column for column in rows[0] if column not in {"Ad Set Budget", "Ad Set Budget Type"}]
        frame = core.read_ad_performance_tabular(ad_export_csv(rows, columns), ".csv")
        self.assertEqual(core.derive_budget_periods(frame), [])


def change_log_bytes(ad_set_rows=(), ad_rows=(), *, sheet_names=("changelog_ad_set", "changelog_ad")):
    """A minimal stand-in for MODEL_DATASET_TEMPLATE.xlsx carrying only the changelog sheets."""
    buffer = io.BytesIO()
    ad_set_frame = pd.DataFrame(list(ad_set_rows), columns=[
        "date", "ad_set_id", "campaign_name", "ad_set_change_type", "source", "confirmed_by", "notes"])
    ad_frame = pd.DataFrame(list(ad_rows), columns=[
        "date", "ad_set_id", "campaign_name", "ad_id", "ad_change_type", "source", "confirmed_by", "notes"])
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"": ["README"]}).to_excel(writer, sheet_name="README", index=False)
        ad_set_frame.to_excel(writer, sheet_name=sheet_names[0], index=False)
        ad_frame.to_excel(writer, sheet_name=sheet_names[1], index=False)
    return buffer.getvalue()


def adset_change_row(day, ad_set="adA", change_type="targeting_change", source="confirmed"):
    return {"date": day, "ad_set_id": ad_set, "campaign_name": "Leads | VISA | JP | FOR",
            "ad_set_change_type": change_type, "source": source, "confirmed_by": "SK", "notes": ""}


def ad_change_row(day, ad_set="adA", ad_id="ad1", change_type="ad_added", source="confirmed"):
    return {"date": day, "ad_set_id": ad_set, "campaign_name": "Leads | VISA | JP | FOR",
            "ad_id": ad_id, "ad_change_type": change_type, "source": source,
            "confirmed_by": "SK", "notes": ""}


class ChangeLogImportTests(IsolatedDbTestCase):
    def import_log(self, content, name="MODEL_DATASET_TEMPLATE.xlsx"):
        preview = core.preview_file(content, name)
        return preview, core.import_preview(preview["token"], name)

    def test_workbook_is_detected_by_sheet_name_not_columns(self):
        content = change_log_bytes([adset_change_row("2026-06-20")])
        path = Path(self.temp.name) / "probe.xlsx"
        path.write_bytes(content)
        self.assertTrue(core.is_change_log_workbook(path, ".xlsx"))
        # the first sheet is a README, so the column detector must not be what decides
        with self.assertRaises(ValueError):
            core.detect_upload_type_from_columns(["", "README"])

    def test_preview_reports_change_log_type_and_counts(self):
        content = change_log_bytes(
            [adset_change_row("2026-06-20"), adset_change_row("2026-07-02", change_type="bid_change")],
            [ad_change_row("2026-06-25")],
        )
        preview = core.preview_file(content, "template.xlsx")
        self.assertEqual(preview["file_type"], core.CHANGE_LOG_TYPE)
        self.assertEqual(preview["clean_rows"], 3)
        self.assertEqual(preview["confirmed_rows"], 3)
        self.assertEqual(preview["by_scope"]["ad_set"]["rows"], 2)
        self.assertEqual(preview["by_scope"]["ad"]["rows"], 1)

    def test_import_stores_events_and_reimport_updates_in_place(self):
        content = change_log_bytes([adset_change_row("2026-06-20")], [ad_change_row("2026-06-25")])
        _, result = self.import_log(content)
        self.assertEqual(result["file_type"], core.CHANGE_LOG_TYPE)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["updated"], 0)
        corrected = change_log_bytes(
            [adset_change_row("2026-06-20", change_type="budget_increase")], [ad_change_row("2026-06-25")])
        _, again = self.import_log(corrected)
        self.assertEqual(again["inserted"], 0)
        self.assertEqual(again["updated"], 2)
        # the sheet's change-type cell decides only whether the row IS an event; since the
        # type removal (2026-08-11) the value is discarded and the DATE is what is stored
        with core.connect() as db:
            rows = db.execute("SELECT event_date FROM change_events WHERE scope='ad_set'").fetchall()
        self.assertEqual([row["event_date"] for row in rows], ["2026-06-20"])

    def test_only_confirmed_rows_reach_the_features(self):
        content = change_log_bytes([
            adset_change_row("2026-06-20", change_type="targeting_change", source="confirmed"),
            adset_change_row("2026-07-02", change_type="bid_change", source="inferred"),
        ])
        self.import_log(content)
        self.assertEqual(core._recorded_change_events("ad_set", "adA"),
                         (pd.Timestamp("2026-06-20"),))

    def test_only_a_confirmed_log_produces_events_there_is_no_detector(self):
        spend = pd.DataFrame([
            {"ad_set_id": "adA", "day": pd.Timestamp("2026-06-01") + timedelta(days=i),
             "amount_spent_usd": 5.0 if i < 15 else 50.0, "impressions": 1000.0, "frequency": 1.2}
            for i in range(30)
        ] + [
            {"ad_set_id": "adB", "day": pd.Timestamp("2026-06-01") + timedelta(days=i),
             "amount_spent_usd": 5.0 if i < 15 else 50.0, "impressions": 1000.0, "frequency": 1.2}
            for i in range(30)
        ])
        dates = pd.date_range("2026-06-01", periods=30, freq="D")
        # the step-shift detector was removed 2026-08-06 for producing wrong values, so a
        # tenfold spend step on its own now infers nothing at all
        undetected = core._ad_set_change_features(spend, dates, ad_set="adA")
        self.assertEqual(undetected["ad_set_change_recency"].max(), 0.0)

        self.import_log(change_log_bytes([
            adset_change_row("2026-06-10", ad_set="adA", change_type="targeting_change")]))
        recorded = core._ad_set_change_features(spend, dates, ad_set="adA")
        # recency now counts up from Jun 10 to the Jun 30 end of the window
        self.assertEqual(recorded["ad_set_change_recency"].max(), 20.0)
        # adB was never recorded, so it stays at zero
        untouched = core._ad_set_change_features(spend, dates, ad_set="adB")
        self.assertEqual(untouched["ad_set_change_recency"].max(), 0.0)

    def test_recency_counts_from_the_recorded_event(self):
        self.import_log(change_log_bytes(ad_rows=[
            ad_change_row("2026-06-10", change_type="ad_paused")]))
        dates = pd.date_range("2026-06-08", periods=6, freq="D")
        features = core._ad_change_features(dates, ad_set="adA")
        # recency carries forward -- "how long since the last change". There is no longer a
        # type indicator alongside it (variables 9 and 10 removed 2026-08-11).
        self.assertEqual(list(features["ad_change_recency"]), [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
        self.assertEqual(list(features), ["ad_change_recency"])

    def test_a_no_recent_change_row_is_skipped_not_stored(self):
        content = change_log_bytes(ad_rows=[
            ad_change_row("2026-06-10", change_type="no_recent_change"),
            ad_change_row("2026-06-11", change_type="ad_paused"),
        ])
        preview, result = self.import_log(content)
        # under point-event semantics "nothing changed today" is the default for every
        # unrecorded day, so a row asserting it adds nothing and is counted under its own name
        self.assertEqual(preview["skipped_rows"]["changelog_ad:no_change_rows"], 1)
        self.assertEqual(preview["clean_rows"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(core._recorded_change_events("ad", "adA"), (pd.Timestamp("2026-06-11"),))

    def test_a_file_of_baseline_rows_only_holds_no_usable_events(self):
        content = change_log_bytes(ad_rows=[
            ad_change_row("2026-06-10", change_type="no_recent_change")])
        with self.assertRaisesRegex(ValueError, "no usable events"):
            core.preview_file(content, "template.xlsx")

    def test_unusable_rows_are_skipped_and_counted(self):
        content = change_log_bytes([
            adset_change_row("2026-06-20"),
            adset_change_row("", ad_set="adB"),
            adset_change_row("2026-06-22", ad_set=""),
            adset_change_row("2026-06-23", change_type="colour_change"),
        ])
        preview = core.preview_file(content, "template.xlsx")
        # "colour_change" is no longer rejected: with the type gone, any non-baseline cell
        # just marks that a change happened, and it is the DATE that gets stored. So three
        # usable rows survive and only the blank date / blank ad set are dropped.
        self.assertEqual(preview["clean_rows"], 2)
        self.assertEqual(preview["excluded_rows"], 2)
        self.assertEqual(preview["skipped_rows"]["changelog_ad_set:blank_date"], 1)
        self.assertEqual(preview["skipped_rows"]["changelog_ad_set:blank_ad_set_id"], 1)
        self.assertNotIn("changelog_ad_set:unknown_change_type", preview["skipped_rows"])

    def test_unconfirmed_only_file_warns_and_changes_nothing(self):
        content = change_log_bytes([adset_change_row("2026-06-20", source="inferred")])
        preview = core.preview_file(content, "template.xlsx")
        self.assertEqual(preview["confirmed_rows"], 0)
        self.assertTrue(any("keep using inferred" in warning for warning in preview["warnings"]))
        core.import_preview(preview["token"], "template.xlsx")
        self.assertEqual(core._recorded_change_events("ad_set", "adA"), ())

    def test_file_without_changelog_sheets_is_rejected(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="something_else", index=False)
        path = Path(self.temp.name) / "other.xlsx"
        path.write_bytes(buffer.getvalue())
        self.assertFalse(core.is_change_log_workbook(path, ".xlsx"))
        with self.assertRaisesRegex(ValueError, "No changelog sheets"):
            core.read_change_log_workbook(path, ".xlsx")

    def test_missing_required_column_names_the_sheet(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame({"date": ["2026-06-01"], "ad_set_id": ["adA"]}).to_excel(
                writer, sheet_name="changelog_ad_set", index=False)
        path = Path(self.temp.name) / "partial.xlsx"
        path.write_bytes(buffer.getvalue())
        with self.assertRaisesRegex(ValueError, "ad_set_change_type"):
            core.read_change_log_workbook(path, ".xlsx")

    def test_deleting_the_upload_withdraws_its_events(self):
        _, result = self.import_log(change_log_bytes([adset_change_row("2026-06-20")]))
        self.assertEqual(len(core._recorded_change_events("ad_set", "adA")), 1)
        core.delete_upload(result["upload_id"])
        self.assertEqual(core._recorded_change_events("ad_set", "adA"), ())

    def test_the_real_template_workbook_parses(self):
        template = Path(core.ROOT) / "Dataset" / "MODEL_DATASET_TEMPLATE.xlsx"
        if not template.exists():
            self.skipTest("template workbook not built")
        frame = core.read_change_log_workbook(template, ".xlsx")
        report = frame.attrs["cleaning_report"]
        self.assertEqual(set(report["by_scope"]), {"ad_set", "ad"})
        # every seeded row ships as a guess, so an untouched template must confirm nothing
        self.assertEqual(report["confirmed_rows"], 0)


MODEL_DATASET_COLUMNS = [
    "lead_id", "created_at", "customer_name", "lead_status", "is_new_customer", "platform",
    "campaign_id", "campaign_name", "ad_set_id", "ad_id", "fb_ad_title", "delivery_status",
    "spend", "holiday_proximity", "is_holiday", "days_since_adset_started", "frequency",
    "ad_change_recency", "ad_set_change_recency", "day_of_week", "day_of_week_num",
    "is_weekend", "ad_set_change_type", "ad_change_type", "reach", "impressions",
    "messaging_conversations", "ad_set_budget", "ad_set_budget_type", "cpl", "cpm",
    "adset_day_leads",
]


def model_dataset_row(day, index, ad_set="adA", campaign="c1", spend=10.0,
                      adset_change="", ad_change="", ad_id="ad1"):
    stamp = f"{day} 1{index % 10}:0{index % 6}:00"
    return {
        "lead_id": f"L{index:05d}", "created_at": stamp, "customer_name": f"Customer {index}",
        "lead_status": "New" if index % 2 == 0 else "Existing", "is_new_customer": int(index % 2 == 0),
        "platform": "messenger", "campaign_id": campaign, "campaign_name": "Leads | VISA | JP | FOR",
        "ad_set_id": ad_set, "ad_id": ad_id, "fb_ad_title": "VF008E2", "delivery_status": "active",
        "spend": spend, "holiday_proximity": "60_plus_or_none", "is_holiday": 0,
        "days_since_adset_started": 5, "frequency": 1.25,
        "ad_change_recency": "", "ad_set_change_recency": "",
        "day_of_week": "Monday", "day_of_week_num": 0, "is_weekend": 0,
        "ad_set_change_type": adset_change, "ad_change_type": ad_change,
        "reach": 800, "impressions": 1000, "messaging_conversations": 3,
        "ad_set_budget": 12.0, "ad_set_budget_type": "Daily", "cpl": 2.0, "cpm": 10.0,
        "adset_day_leads": 2,
    }


def model_dataset_bytes(rows, *, sheet="model_dataset", extra_sheets=None, columns=None):
    buffer = io.BytesIO()
    frame = pd.DataFrame(list(rows), columns=columns or MODEL_DATASET_COLUMNS)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"": ["README"]}).to_excel(writer, sheet_name="README", index=False)
        frame.to_excel(writer, sheet_name=sheet, index=False)
        for name, extra in (extra_sheets or {}).items():
            extra.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


class ModelDatasetImportTests(IsolatedDbTestCase):
    def import_dataset(self, content, name="Dataset 101.xlsx"):
        preview = core.preview_file(content, name)
        return preview, core.import_preview(preview["token"], name)

    def sample(self, **kwargs):
        return [model_dataset_row("2026-06-01", 0, **kwargs),
                model_dataset_row("2026-06-01", 1, **kwargs),
                model_dataset_row("2026-06-02", 2, **kwargs)]

    def test_detected_as_model_dataset_not_customer_traffic(self):
        content = model_dataset_bytes(self.sample())
        preview = core.preview_file(content, "Dataset 101.xlsx")
        self.assertEqual(preview["file_type"], core.MODEL_DATASET_TYPE)
        self.assertEqual(preview["file_type_label"], "Model dataset")

    def test_ad_set_day_grain_sheet_is_not_accepted(self):
        # the older shape has a `leads` count and no lead identity; importing it would
        # invent leads that have no names
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame([{"date": "2026-06-01", "ad_set_id": "adA", "leads": 4, "spend": 10.0}]
                         ).to_excel(writer, sheet_name="model_dataset", index=False)
        path = Path(self.temp.name) / "aggregated.xlsx"
        path.write_bytes(buffer.getvalue())
        self.assertFalse(core.is_model_dataset_workbook(path, ".xlsx"))

    def test_missing_required_column_is_named(self):
        columns = [c for c in MODEL_DATASET_COLUMNS if c != "customer_name"]
        content = model_dataset_bytes(
            [{k: v for k, v in row.items() if k != "customer_name"} for row in self.sample()],
            columns=columns)
        path = Path(self.temp.name) / "partial.xlsx"
        path.write_bytes(content)
        with self.assertRaisesRegex(ValueError, "customer_name"):
            core.read_model_dataset_workbook(path, ".xlsx")

    def test_import_writes_leads_and_ad_set_days_from_one_file(self):
        _, result = self.import_dataset(model_dataset_bytes(self.sample()))
        self.assertEqual(result["file_type"], core.MODEL_DATASET_TYPE)
        self.assertEqual(result["imported"], 3)
        # three leads across two days collapse to two ad-set-day context rows
        self.assertEqual(result["ad_set_days_inserted"], 2)
        with core.connect() as db:
            leads = db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0]
            days = db.execute("SELECT COUNT(*) FROM daily_ad_performance").fetchone()[0]
            spend = db.execute("SELECT SUM(amount_spent_usd) FROM daily_ad_performance").fetchone()[0]
        self.assertEqual(leads, 3)
        self.assertEqual(days, 2)
        # spend is the ad set's whole day, so it must not be multiplied by the lead count
        self.assertAlmostEqual(spend, 20.0)

    def test_leads_keep_their_names(self):
        self.import_dataset(model_dataset_bytes(self.sample()))
        with core.connect() as db:
            names = [r[0] for r in db.execute(
                "SELECT customer_name FROM lead_events ORDER BY created_at").fetchall()]
        self.assertEqual(names, ["Customer 0", "Customer 1", "Customer 2"])

    def test_reimport_of_the_same_file_adds_no_duplicate_leads(self):
        content = model_dataset_bytes(self.sample())
        self.import_dataset(content)
        _, again = self.import_dataset(content)
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["duplicates"], 3)
        with core.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0], 3)

    def test_blank_change_columns_record_nothing_and_warn(self):
        preview, result = self.import_dataset(model_dataset_bytes(self.sample()))
        self.assertEqual(preview["change_events"], 0)
        self.assertEqual(result["change_events_inserted"], 0)
        self.assertTrue(any("keep using the system's inferred events" in w
                            for w in preview["warnings"]))
        self.assertEqual(core._recorded_change_events("ad_set", "adA"), ())

    def test_filled_change_columns_become_confirmed_events(self):
        rows = [
            model_dataset_row("2026-06-01", 0, adset_change="budget_increase", ad_change="ad_added"),
            model_dataset_row("2026-06-02", 1, adset_change="budget_increase", ad_change=""),
            model_dataset_row("2026-06-03", 2, adset_change="no_change", ad_change=""),
        ]
        preview, result = self.import_dataset(model_dataset_bytes(rows))
        # each filled cell names what changed ON that day, so two budget changes on
        # consecutive days are two events -- the old state-dedup would have dropped the second
        self.assertEqual(preview["change_events"], 3)
        self.assertEqual(result["change_events_inserted"], 3)
        # the baseline cell on the 3rd records nothing: it is already every day's default.
        # The cell's value is only a marker that something changed -- the DATE is stored.
        self.assertEqual(core._recorded_change_events("ad_set", "adA"),
                         (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-02")))
        self.assertEqual(core._recorded_change_events("ad", "adA"),
                         (pd.Timestamp("2026-06-01"),))

    def test_filled_change_columns_drive_the_features(self):
        rows = [model_dataset_row(f"2026-06-0{d}", d, adset_change="targeting_change")
                for d in range(1, 5)]
        self.import_dataset(model_dataset_bytes(rows))
        dates = pd.date_range("2026-06-01", periods=4, freq="D")
        spend = pd.DataFrame([
            {"ad_set_id": "adA", "day": d, "amount_spent_usd": 10.0,
             "impressions": 1000.0, "frequency": 1.25} for d in dates])
        features = core._ad_set_change_features(spend, dates, ad_set="adA")
        # a change recorded on all four days keeps recency pinned at 0 throughout, and there
        # is no type indicator alongside it any more
        self.assertEqual(list(features), ["ad_set_change_recency"])
        self.assertEqual(features["ad_set_change_recency"].max(), 0.0)

    def test_rows_without_an_ad_set_are_skipped_not_guessed(self):
        rows = self.sample()
        rows.append(model_dataset_row("2026-06-02", 9, ad_set=""))
        preview, result = self.import_dataset(model_dataset_bytes(rows))
        self.assertEqual(preview["excluded_rows"], 1)
        self.assertEqual(result["imported"], 3)

    def test_preview_warns_that_zero_lead_days_are_absent(self):
        preview = core.preview_file(model_dataset_bytes(self.sample()), "Dataset 101.xlsx")
        self.assertTrue(any("days that produced a lead" in w for w in preview["warnings"]))

    def test_deleting_the_upload_removes_its_leads_and_context(self):
        _, result = self.import_dataset(model_dataset_bytes(self.sample()))
        core.delete_upload(result["upload_id"])
        with core.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM daily_ad_performance").fetchone()[0], 0)

    def test_model_dataset_outranks_the_change_log_when_both_sheets_exist(self):
        extra = {"changelog_ad_set": pd.DataFrame([{
            "date": "2026-06-05", "ad_set_id": "adA", "campaign_name": "x",
            "ad_set_change_type": "bid_change", "source": "confirmed",
            "confirmed_by": "SK", "notes": ""}])}
        content = model_dataset_bytes(self.sample(), extra_sheets=extra)
        preview = core.preview_file(content, "MODEL_DATASET_TEMPLATE.xlsx")
        self.assertEqual(preview["file_type"], core.MODEL_DATASET_TYPE)

    def ad_set_day_sheet(self, rows):
        return pd.DataFrame(rows, columns=[
            "date", "ad_set_id", "campaign_id", "campaign_name", "delivery_status", "leads",
            "spend", "frequency", "reach", "impressions", "messaging_conversations",
            "ad_set_budget", "ad_set_budget_type", "cpl", "zero_lead_day"])

    def ad_set_day_row(self, day, leads, spend, ad_set="adA"):
        return {"date": day, "ad_set_id": ad_set, "campaign_id": "c1",
                "campaign_name": "Leads | VISA | JP | FOR", "delivery_status": "active",
                "leads": leads, "spend": spend, "frequency": 1.25, "reach": 800,
                "impressions": 1000, "messaging_conversations": 3, "ad_set_budget": 12.0,
                "ad_set_budget_type": "Daily", "cpl": 2.0, "zero_lead_day": int(leads == 0)}

    def test_ad_set_days_sheet_carries_days_that_produced_no_leads(self):
        # two lead days plus a third day that spent and got nothing
        extra = {"ad_set_days": self.ad_set_day_sheet([
            self.ad_set_day_row("2026-06-01", 2, 10.0),
            self.ad_set_day_row("2026-06-02", 1, 10.0),
            self.ad_set_day_row("2026-06-03", 0, 7.5),
        ])}
        content = model_dataset_bytes(self.sample(), extra_sheets=extra)
        preview, result = self.import_dataset(content)
        self.assertEqual(preview["ad_context_source"], "ad_set_days")
        self.assertEqual(preview["ad_set_day_rows"], 3)
        self.assertEqual(preview["zero_lead_days"], 1)
        self.assertEqual(result["ad_set_days_inserted"], 3)
        with core.connect() as db:
            spend = db.execute("SELECT SUM(amount_spent_usd) FROM daily_ad_performance").fetchone()[0]
            zero_day = db.execute(
                "SELECT amount_spent_usd FROM daily_ad_performance WHERE day='2026-06-03'").fetchone()
        # the zero-lead day's spend is present, which collapsing the lead rows can never achieve
        self.assertAlmostEqual(spend, 27.5)
        self.assertAlmostEqual(zero_day[0], 7.5)

    def test_the_sheet_replaces_the_collapsed_lead_context_rather_than_adding_to_it(self):
        extra = {"ad_set_days": self.ad_set_day_sheet([
            self.ad_set_day_row("2026-06-01", 2, 10.0),
            self.ad_set_day_row("2026-06-02", 1, 10.0),
        ])}
        _, result = self.import_dataset(model_dataset_bytes(self.sample(), extra_sheets=extra))
        self.assertEqual(result["ad_set_days_inserted"], 2)
        with core.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM daily_ad_performance").fetchone()[0], 2)

    def test_no_missing_days_warning_once_the_sheet_is_present(self):
        extra = {"ad_set_days": self.ad_set_day_sheet([self.ad_set_day_row("2026-06-01", 2, 10.0)])}
        with_sheet = core.preview_file(
            model_dataset_bytes(self.sample(), extra_sheets=extra), "d.xlsx")
        without = core.preview_file(model_dataset_bytes(self.sample()), "d.xlsx")
        self.assertFalse(any("ad_set_days" in w and "missing" in w for w in with_sheet["warnings"]))
        self.assertTrue(any("Days that spent and got nothing are missing" in w
                            for w in without["warnings"]))

    def test_ad_set_days_missing_a_required_column_is_named(self):
        sheet = self.ad_set_day_sheet([self.ad_set_day_row("2026-06-01", 2, 10.0)]).drop(columns=["spend"])
        content = model_dataset_bytes(self.sample(), extra_sheets={"ad_set_days": sheet})
        path = Path(self.temp.name) / "bad.xlsx"
        path.write_bytes(content)
        with self.assertRaisesRegex(ValueError, "spend"):
            core.read_model_dataset_workbook(path, ".xlsx")

    def test_the_real_dataset_101_all_days_closes_the_spend_gap(self):
        folder = Path(core.ROOT) / "Dataset" / "Datsaa" / "Dataset Template"
        lead_only = folder / "Dataset 101.xlsx"
        all_days = folder / "Dataset 101 (all days).xlsx"
        if not (lead_only.exists() and all_days.exists()):
            self.skipTest("Dataset 101 files not present")
        without = core.read_model_dataset_workbook(lead_only, ".xlsx")["report"]
        with_sheet = core.read_model_dataset_workbook(all_days, ".xlsx")["report"]
        self.assertEqual(without["ad_set_day_rows"], 718)
        self.assertEqual(with_sheet["ad_set_day_rows"], 879)
        self.assertEqual(with_sheet["zero_lead_days"], 161)
        self.assertAlmostEqual(with_sheet["total_spend"], 4114.31, places=2)
        # the lead rows are identical either way; only the context changed
        self.assertEqual(without["lead_rows"], with_sheet["lead_rows"])

    def test_the_real_dataset_101_parses(self):
        path = Path(core.ROOT) / "Dataset" / "Datsaa" / "Dataset Template" / "Dataset 101.xlsx"
        if not path.exists():
            self.skipTest("Dataset 101 not present")
        parsed = core.read_model_dataset_workbook(path, ".xlsx")
        report = parsed["report"]
        self.assertEqual(report["source_rows"], 3038)
        self.assertEqual(report["lead_rows"], 3023)
        self.assertEqual(report["ad_set_day_rows"], 718)
        # ids must survive as text, never as scientific notation
        self.assertTrue(parsed["traffic"]["UTM Ad Set ID"].str.match(r"^\d{18}$").all())


class ChangeEventEditorTests(IsolatedDbTestCase):
    """The Forecast-page 'Ad set change' recorder: manual CRUD over change_events.

    Since 2026-08-11 an event is a scope and a DATE -- change type was removed from the model
    and from this table, so none of these pass or assert one.
    """

    AD_SET = "120236217374900078"

    def spend_frame(self, days=30, start="2026-06-06"):
        dates = pd.date_range(start, periods=days, freq="D")
        return pd.DataFrame({
            "ad_set_id": self.AD_SET, "day": dates,
            "amount_spent_usd": 10.0, "impressions": 1000.0, "frequency": 1.2,
        })

    def test_a_change_is_stored_as_a_point_event_on_its_own_day(self):
        saved = core.save_change_event("ad_set", self.AD_SET, "2026-06-20", "2026-06-30")
        self.assertEqual(saved["start_date"], "2026-06-20")
        # a change is a point event since 2026-08-07: an end date is still accepted so older
        # payloads don't 422, but it is collapsed onto the event day and never read
        self.assertEqual(saved["end_date"], "2026-06-20")
        # confirmed is what makes it reach the model at all
        self.assertEqual(saved["source"], core.CONFIRMED_SOURCE)
        # no change_type key survives on the serialized row
        self.assertNotIn("change_type", saved)
        self.assertEqual(core._recorded_change_events("ad_set", self.AD_SET),
                         (pd.Timestamp("2026-06-20"),))

    def test_recency_counts_up_daily_and_resets_on_each_recorded_change(self):
        core.save_change_event("ad_set", self.AD_SET, "2026-06-10", "2026-06-20")
        core.save_change_event("ad_set", self.AD_SET, "2026-06-25", "2026-06-30")
        dates = pd.date_range("2026-06-08", "2026-06-27", freq="D")
        features = core._ad_set_change_features(self.spend_frame(), dates, ad_set=self.AD_SET)
        recency = dict(zip(dates, features["ad_set_change_recency"]))
        self.assertEqual(recency[pd.Timestamp("2026-06-10")], 0.0)
        self.assertEqual(recency[pd.Timestamp("2026-06-14")], 4.0)
        # carries on counting past the range's end date, then resets at the next change
        self.assertEqual(recency[pd.Timestamp("2026-06-24")], 14.0)
        self.assertEqual(recency[pd.Timestamp("2026-06-25")], 0.0)
        self.assertEqual(recency[pd.Timestamp("2026-06-27")], 2.0)
        # recency is the only feature this produces now
        self.assertEqual(list(features), ["ad_set_change_recency"])

    def test_recency_buckets_split_at_the_declared_edges(self):
        """The bucketing the tables show, including the >=60-day decay back to baseline."""
        self.assertEqual(
            [core.recency_bucket(d) for d in (0, 3, 4, 7, 8, 14, 15, 59, 60, 200)],
            ["0_3_days", "0_3_days", "4_7_days", "4_7_days", "8_14_days", "8_14_days",
             "15_59_days", "15_59_days", "no_recent_change", "no_recent_change"])
        # no prior event reads as the baseline, which is what keeps "never changed"
        # distinguishable from "changed today" (0 -> 0_3_days)
        self.assertEqual(core.recency_bucket(None), "no_recent_change")

    def test_recording_one_ad_set_does_not_silence_detection_on_another(self):
        core.save_change_event("ad_set", self.AD_SET, "2026-06-20", "2026-06-25")
        self.assertTrue(core._recorded_change_events("ad_set", self.AD_SET))
        self.assertEqual(core._recorded_change_events("ad_set", "999999999999999999"), ())

    def test_an_unknown_scope_is_rejected(self):
        for scope in ("", "campaign", "adset"):
            with self.assertRaises(ValueError):
                core.save_change_event(scope, self.AD_SET, "2026-06-20")
        # both real scopes are accepted, and the same day at each is a separate event
        for scope in core.CHANGE_SCOPES:
            core.save_change_event(scope, self.AD_SET, "2026-06-20")
        self.assertEqual(len(core.list_change_events(ad_set_id=self.AD_SET)), 2)

    def test_a_second_change_on_the_same_day_is_refused_rather_than_overwritten(self):
        core.save_change_event("ad_set", self.AD_SET, "2026-06-20", "2026-06-30")
        with self.assertRaises(ValueError):
            core.save_change_event("ad_set", self.AD_SET, "2026-06-20", "2026-06-22")
        # the same day at the other scope is a different event and stays allowed
        core.save_change_event("ad", self.AD_SET, "2026-06-20", "2026-06-22")
        self.assertEqual(len(core.list_change_events(ad_set_id=self.AD_SET)), 2)

    def test_a_missing_or_unparseable_date_is_rejected(self):
        with self.assertRaises(ValueError):
            core.save_change_event("ad_set", self.AD_SET, "not-a-date")
        with self.assertRaises(ValueError):
            core.save_change_event("ad_set", self.AD_SET, "")
        with self.assertRaises(ValueError):
            core.save_change_event("ad_set", "", "2026-06-20")
        # a reversed range is no longer an error: there is no range to reverse, and the end
        # date is discarded rather than allowed to widen the event
        saved = core.save_change_event("ad_set", self.AD_SET, "2026-06-30", "2026-06-20")
        self.assertEqual((saved["start_date"], saved["end_date"]), ("2026-06-30", "2026-06-30"))

    def test_editing_updates_in_place_and_deleting_leaves_no_event_behind(self):
        saved = core.save_change_event("ad_set", self.AD_SET, "2026-06-20", "2026-06-30")
        core.save_change_event("ad_set", self.AD_SET, "2026-06-22", "2026-07-02",
                               event_id=saved["id"])
        rows = core.list_change_events(ad_set_id=self.AD_SET)
        self.assertEqual(len(rows), 1)
        # the edit moved the event's date, and its end date is collapsed onto it as on insert
        self.assertEqual(rows[0]["start_date"], "2026-06-22")
        self.assertEqual(rows[0]["end_date"], "2026-06-22")
        # nothing is restored on delete -- there is no detector to fall back to
        core.delete_change_event(saved["id"])
        self.assertEqual(core._recorded_change_events("ad_set", self.AD_SET), ())
        with self.assertRaises(ValueError):
            core.delete_change_event(saved["id"])

    def test_coverage_counts_the_live_days_that_carry_a_recorded_change(self):
        now = core.utc_now()
        with core.connect() as db:
            db.execute(
                """INSERT INTO raw_uploads (id, file_name, stored_path, file_sha256, uploaded_at,
                   row_count, imported_count, status) VALUES (1,'ads.csv','/tmp/ads.csv','x',?,0,0,'imported')""",
                (now,),
            )
            for day in pd.date_range("2026-06-06", periods=10, freq="D"):
                db.execute(
                    """INSERT INTO daily_ad_performance
                       (upload_id, campaign_id, ad_set_id, day, amount_spent_usd, raw_json,
                        created_at, updated_at)
                       VALUES (1, ?, ?, ?, ?, '{}', ?, ?)""",
                    ("120236217374890078", self.AD_SET, day.strftime("%Y-%m-%d"), 10.0, now, now),
                )
        core.save_change_event("ad_set", self.AD_SET, "2026-06-08", "2026-06-11")
        coverage = core.change_event_coverage(self.AD_SET)
        self.assertEqual(coverage["live_days"], 10)
        # a point event covers its own day only; the end date does not widen it, and the
        # nine uncovered days are a positive "nothing changed", not a gap to fill
        self.assertEqual(coverage["covered_days"], 1)
        self.assertEqual(coverage["uncovered_days"], 9)


if __name__ == "__main__":
    unittest.main()
