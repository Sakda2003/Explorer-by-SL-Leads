import json
import io
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from backend import core
from backend import forecast_diagnostics as fd


@pytest.fixture
def db(tmp_path, monkeypatch):
    for name, value in {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "test.db",
                        "UPLOAD_DIR": tmp_path / "uploads", "PREVIEW_DIR": tmp_path / "previews"}.items():
        monkeypatch.setattr(core, name, value)
    core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO raw_uploads(id, file_name, stored_path, file_sha256, uploaded_at, row_count) VALUES(1,'test','test','test','2026-01-01',0)")
    yield


def synthetic_frame():
    rng = np.random.default_rng(17)
    n = 150
    spend = rng.uniform(2, 20, n)
    meta = rng.uniform(2, 10, n)
    frame = pd.DataFrame({"leads": 10 + spend * 2 + meta * 4 + rng.normal(0, .3, n),
                          "spend": spend, "meta_leads": meta,
                          "impressions": spend * 100, "link_clicks": rng.uniform(0, 10, n)},
                         index=pd.date_range("2026-01-01", periods=n))
    frame["holiday_during_holiday"] = np.arange(n) % 13 == 0
    return frame


def test_catalog_keeps_crm_and_meta_separate_and_blocks_target_ratio():
    catalog = {v["key"]: v for v in fd.variable_catalog()}
    assert catalog["leads"]["label"] == "Leads (CRM outcome)"
    assert catalog["source_leads"]["label"] == "Leads (Meta export)"
    assert catalog["meta_leads"]["label"] == "Meta Leads"
    assert catalog["leads"]["kind"] == "outcome"
    assert not catalog["cost_per_crm_lead"]["model_eligible"]
    assert catalog["cost_per_lead"]["model_eligible"]
    assert len([v for v in catalog.values() if v["key"] == "frequency"]) == 1
    for key in ("leads", "cost_per_crm_lead", "unknown"):
        with pytest.raises(ValueError, match="Not eligible"):
            fd.get_forecast_diagnostics(enabled=[key])


def test_daily_aggregation_ratios_use_correct_totals_and_preserve_missing():
    raw = pd.DataFrame([
        dict(day="2026-01-01", amount_spent_usd=10, leads=2, meta_leads=7, messaging_conversations_started=2,
             impressions=1000, reach=500, clicks_all=20, link_clicks=5, frequency=99, ctr_all=99),
        dict(day="2026-01-01", amount_spent_usd=30, leads=3, meta_leads=8, messaging_conversations_started=6,
             impressions=3000, reach=700, clicks_all=100, link_clicks=None, frequency=99, ctr_all=99),
        dict(day="2026-01-02", amount_spent_usd=20, leads=0, meta_leads=0, messaging_conversations_started=0,
             impressions=2000, reach=800, clicks_all=60, link_clicks=0),
    ])
    daily = fd.aggregate_meta_daily(raw, pd.date_range("2026-01-01", periods=3))
    first, second, absent = [daily.iloc[i] for i in range(3)]
    assert first["source_leads"] == 5
    assert first["meta_leads"] == 15
    assert first["cost_per_lead"] == 8
    assert first["cost_per_messaging_conversation"] == 5
    assert first["ctr_all"] == 3
    assert first["cpm"] == 10
    assert first["reach"] == 1200
    assert first["frequency"] == pytest.approx(4000 / 1200)
    assert first["link_clicks"] == 5
    assert second["frequency"] == 2.5
    assert pd.isna(second["cost_per_lead"])
    assert second["link_clicks"] == 0  # A reported zero is not a missing value.
    assert absent.isna().all()


def test_missing_older_columns_nonnumeric_constant_and_pairwise_na():
    dates = pd.date_range("2026-01-01", periods=5)
    daily = fd.aggregate_meta_daily(pd.DataFrame({"day": dates, "amount_spent_usd": [1, 2, "bad", 4, 5]}), dates)
    assert daily["clicks_all"].isna().all()
    assert pd.isna(daily.iloc[2]["spend"])
    frame = pd.DataFrame({"a": [1, 2, 3, None], "b": [2, 4, 6, None], "flat": [1, 1, 1, 1], "short": [1, 2, None, None]})
    matrix, details = fd.correlation_matrix(frame, ["a", "b", "flat", "short", "missing"])
    assert matrix[0][1] == 1
    assert matrix[2][2] is None
    assert matrix[0][3] is None and "2 paired days" in details[0][3]
    assert matrix[4][4] is None
    json.dumps(matrix, allow_nan=False)


def test_mixed_legacy_and_new_rows_keep_new_metrics_available_for_matrix():
    dates = pd.date_range("2026-01-01", periods=10)
    rows = []
    for index, day in enumerate(dates, 1):
        rows.append({"day": day, "amount_spent_usd": 1})  # legacy ad set: new fields are NULL
        rows.append({
            "day": day, "amount_spent_usd": index + 2,
            "messaging_conversations_started": index,
            "reach": 100 + index * 3, "impressions": 180 + index * 8,
            "clicks_all": 10 + index * 2, "link_clicks": index + 1,
            "leads": index % 4, "meta_leads": index % 5,
        })
    daily = fd.aggregate_meta_daily(pd.DataFrame(rows), dates)
    columns = [
        "messaging_conversations_started", "cost_per_messaging_conversation", "reach",
        "frequency", "impressions", "ctr_all", "cpm", "link_clicks", "clicks_all",
        "source_leads", "cost_per_lead", "meta_leads",
    ]
    assert all(daily[column].count() >= 7 for column in columns)
    matrix, _ = fd.correlation_matrix(daily, columns)
    assert all(matrix[index][index] == 1 for index in range(len(columns)))


def test_reported_rates_only_fallback_at_single_source_grain():
    dates = pd.date_range("2026-01-01", periods=3)
    raw = pd.DataFrame([
        {"day": "2026-01-01", "ctr_all": 2.5, "cpm": 7, "frequency": 1.8, "cost_per_lead": 4},
        {"day": "2026-01-02", "ctr_all": 2, "frequency": 1},
        {"day": "2026-01-02", "ctr_all": 4, "frequency": 2},
        {"day": "2026-01-03", "amount_spent_usd": 8, "leads": 0, "cost_per_lead": 4},
    ])
    daily = fd.aggregate_meta_daily(raw, dates)
    assert daily.iloc[0]["ctr_all"] == 2.5
    assert daily.iloc[0]["cpm"] == 7
    assert daily.iloc[0]["frequency"] == 1.8
    assert daily.iloc[0]["cost_per_lead"] == 4
    assert pd.isna(daily.iloc[0]["source_leads"])  # Do not invert a rounded rate into a count.
    assert pd.isna(daily.iloc[1]["ctr_all"])
    assert pd.isna(daily.iloc[1]["frequency"])
    assert pd.isna(daily.iloc[2]["cost_per_lead"])


def test_enabled_new_candidate_is_selected_and_disabled_never_evaluated():
    frame = synthetic_frame()
    with mock.patch.object(fd, "_declared_baseline", return_value=("spend",)):
        summary, selected, _, steps, _ = fd.fit_active_model(frame, fd.variable_catalog(), {"spend", "meta_leads"})
        assert selected == ["spend", "meta_leads"]
        assert all(step["key"] == "meta_leads" for step in steps)
        assert steps[-1]["result"] == "accepted"
        assert summary["dep_variable"] == "Leads"
        assert next(c for c in summary["coefficients"] if c["feature"] == "meta_leads")["term"] == "Meta Leads"
        summary_off, selected_off, _, steps_off, _ = fd.fit_active_model(frame, fd.variable_catalog(), {"spend"})
        assert selected_off == ["spend"] and steps_off == []
        assert summary_off["no_observations"] == 150


def test_dataset_multivariate_selection_pool_includes_imported_meta_metrics():
    rng = np.random.default_rng(31)
    meta_leads = rng.uniform(0, 12, 120)
    values = 4 + meta_leads * 3 + rng.normal(0, 0.05, 120)
    rows = [{"meta_leads": float(value)} for value in meta_leads]
    selection = core._forward_select_declared_features(values, rows)
    assert selection["features"] == ["meta_leads"]
    assert selection["order"] == [19]


def test_shared_ols_selector_includes_every_varying_requested_variable():
    rng = np.random.default_rng(44)
    n = 80
    rows = []
    for index in range(n):
        rows.append({
            "spend": float(index + 1),
            "frequency": float(1 + (index % 11) / 10),
            "conversations": float((index * 3) % 17),
            "meta_leads": float((index * 5) % 19),
            "impressions": float(100 + index * index + rng.normal(0, 1)),
        })
    selected, status = core._select_all_diagnostic_ols_features(rows)
    assert {"spend", "frequency", "conversations", "meta_leads", "impressions"} <= set(selected)
    by_name = {item["name"]: item for item in status}
    assert by_name["Meta Leads"]["status"] == "included"
    assert by_name["Reach"]["status"] == "unavailable"
    assert len(status) == len(core.DIAGNOSTIC_OLS_GROUPS)


def test_shared_dataset_and_forecast_ols_uses_all_available_selector():
    rng = np.random.default_rng(45)
    n = 80
    rows = [{
        "spend": float(index + 2),
        "frequency": float(1 + (index % 9) / 10),
        "conversations": float((index * 3) % 17),
        "meta_leads": float((index * 5) % 19),
        "impressions": float(100 + index * index + rng.normal(0, 1)),
    } for index in range(n)]
    values = np.asarray([
        2 + row["spend"] * .2 + row["meta_leads"] * .5 + rng.normal(0, .2)
        for row in rows
    ])
    scope = {"date_start": "2026-01-01", "date_end": "2026-03-21", "observations": n}
    with mock.patch.object(core, "_load_scope_feature_rows", return_value=(values, rows, scope)):
        result = core.get_ols_model_summaries()
    summary = result["multivariate"]
    assert result["selection"]["method"] == "all_available"
    assert result["selection"]["steps"] == []
    assert summary["dep_variable"] == "Leads (CRM outcome)"
    assert summary["variable_count"] == 5
    assert "Meta Leads" in summary["variables"]
    assert any(row["feature"] == "meta_leads" for row in summary["coefficients"])


def test_rejection_preserves_model_and_sample_and_records_collinearity():
    frame = synthetic_frame()
    frame.loc[frame.index[:2], "impressions"] = np.nan
    with mock.patch.object(fd, "_declared_baseline", return_value=("spend",)):
        initial = fd.fit_active_model(frame, fd.variable_catalog(), {"spend"})
        trial = fd.fit_active_model(frame, fd.variable_catalog(), {"spend", "impressions"})
    assert trial[0] == initial[0]
    assert trial[1] == ["spend"]
    assert trial[3][-1]["result"] == "rejected"
    assert "Multicollinearity" in trial[3][-1]["reason"]
    assert trial[3][-1]["sample_size"] == 148


def test_declared_toggles_do_not_repeat_existing_forward_search():
    fd._declared_baseline.cache_clear()
    frame = synthetic_frame()
    with mock.patch.object(core, "_forward_select_declared_features", return_value={"features": ["spend", "holiday_during_holiday"]}) as search:
        first = fd.fit_active_model(frame, fd.variable_catalog(), {"spend", "holiday_proximity"})
        second = fd.fit_active_model(frame, fd.variable_catalog(), {"spend"})
        third = fd.fit_active_model(frame, fd.variable_catalog(), {"spend", "holiday_proximity"})
    assert search.call_count == 1
    assert "holiday_during_holiday" in first[1]
    assert "holiday_during_holiday" not in second[1]
    assert third[1] == first[1]
    fd._declared_baseline.cache_clear()


def test_unavailable_and_empty_models_do_not_crash():
    with mock.patch.object(fd, "load_daily_frame", return_value=(pd.DataFrame(columns=["leads"]), {"observations": 0})):
        result = fd.get_forecast_diagnostics(enabled=[])
    assert result["multivariate"] is None
    assert "Enable at least one" in result["empty_reason"]
    assert len(result["expanded"]["variables"]) > 18
    assert all(cell is None for row in result["expanded"]["matrix"] for cell in row)
    json.dumps(result, allow_nan=False)
    with mock.patch.object(fd, "load_daily_frame", return_value=(pd.DataFrame(columns=["leads"]), {"observations": 0})):
        default_result = fd.get_forecast_diagnostics()
    model_keys = {variable["key"] for variable in default_result["declared"]["variables"]}
    assert {"source_leads", "cost_per_lead", "meta_leads", "clicks_all", "ctr_all", "cpm"} <= model_keys


def test_candidate_missing_data_reason_and_target_constant():
    frame = synthetic_frame()
    frame["meta_leads"] = np.nan
    with mock.patch.object(fd, "_declared_baseline", return_value=("spend",)):
        result = fd.fit_active_model(frame, fd.variable_catalog(), {"spend", "meta_leads"})
    assert "Insufficient" in result[3][0]["reason"]
    assert result[1] == ["spend"]


def test_import_new_columns_persist_and_rollup(db):
    rows = []
    for index in range(2):
        rows.append({"Campaign ID": "campaign-1", "Campaign name": "Test", "Ad set ID": "adset-1", "Ad ID": f"ad-{index}",
                     "Day": "2026-01-01", "Amount spent (USD)": 10, "Impressions": 1000,
                     "Leads": 2, "Meta leads": 3, "Clicks (all)": 20, "CTR (all)": 2, "CPM (cost per 1,000 impressions)": 10})
    cleaned = core.read_ad_performance_tabular(io.BytesIO(pd.DataFrame(rows).to_csv(index=False).encode()), ".csv")
    assert cleaned.iloc[0]["Clicks (all)"] == 40
    assert cleaned.iloc[0]["CTR (all)"] == 2
    assert cleaned.iloc[0]["CPM (cost per 1,000 impressions)"] == 10
    with core.connect() as conn:
        core._write_ad_performance(conn, 1, cleaned, "2026-01-01")
        stored = dict(conn.execute("SELECT * FROM daily_ad_performance").fetchone())
    assert stored["clicks_all"] == 40 and stored["ctr_all"] == 2 and stored["cpm"] == 10
    assert stored["leads"] == 4 and stored["meta_leads"] == 6
    # A blank is not an explicit zero. Do not present partial totals as complete.
    rows[1]["Leads"] = ""
    rows[1]["Clicks (all)"] = ""
    partial = core.read_ad_performance_tabular(io.BytesIO(pd.DataFrame(rows).to_csv(index=False).encode()), ".csv")
    assert pd.isna(partial.iloc[0]["Leads"])
    assert pd.isna(partial.iloc[0]["Clicks (all)"])
    assert pd.isna(partial.iloc[0]["CTR (all)"])
    assert pd.isna(partial.iloc[0]["Cost per lead"])


def test_demographic_export_rolls_up_requested_metrics_before_import(db):
    rows = [
        {"Campaign ID": "campaign-1", "Campaign name": "Test", "Ad set ID": "adset-1",
         "Day": "2026-01-01", "Age": "18-24", "Gender": "female",
         "Amount spent (USD)": 10, "Messaging conversations started": 2,
         "Reach": 100, "Impressions": 150, "Link clicks": 3, "Clicks (all)": 6,
         "Leads": 1, "Meta leads": 1},
        {"Campaign ID": "campaign-1", "Campaign name": "Test", "Ad set ID": "adset-1",
         "Day": "2026-01-01", "Age": "25-34", "Gender": "male",
         "Amount spent (USD)": 20, "Messaging conversations started": "",
         "Reach": 200, "Impressions": 450, "Link clicks": "", "Clicks (all)": 9,
         "Leads": 2, "Meta leads": 2},
    ]
    cleaned = core.read_ad_performance_tabular(
        io.BytesIO(pd.DataFrame(rows).to_csv(index=False).encode()), ".csv",
    )
    assert len(cleaned) == 1
    row = cleaned.iloc[0]
    assert row["Amount spent (USD)"] == 30
    assert row["Messaging conversations started"] == 2
    assert row["Reach"] == 300 and row["Impressions"] == 600
    assert row["Frequency"] == 2
    assert row["Link clicks"] == 3 and row["Clicks (all)"] == 15
    assert row["CTR (all)"] == 2.5 and row["CPM (cost per 1,000 impressions)"] == 50
    assert row["Leads"] == 3 and row["Cost per lead"] == 10
    assert row["Meta leads"] == 3
    assert cleaned.attrs["cleaning_report"]["demographic_rows_collapsed"] == 1


def test_loader_campaign_union_adset_precedence_and_inclusive_dates(db):
    with core.connect() as conn:
        for campaign, adset, spend, leads, meta_leads in [("c1", "a1", 10, 2, 4), ("c2", "a2", 20, 3, 5), ("c3", "a3", 500, 900, 901)]:
            for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
                conn.execute("""INSERT INTO daily_ad_performance(upload_id, day, campaign_id, ad_set_id, amount_spent_usd,
                    leads, meta_leads, raw_json, created_at, updated_at) VALUES(1,?,?,?,?,?,?,'{}','now','now')""", (day, campaign, adset, spend, leads, meta_leads))
    rows = [{"spend": 0, "days_since_adset_started": i + 10} for i in range(3)]
    scope = {"date_start": "2026-01-01", "date_end": "2026-01-03"}
    with mock.patch.object(core, "_load_scope_feature_rows", return_value=(np.array([7, 8, 9]), rows, scope)):
        frame, scope = fd.load_daily_frame(campaign_id="c1,c2", start_date=date(2026, 1, 2), end_date=date(2026, 1, 3))
        assert len(frame) == 2 and scope["lead_total"] == 17
        assert frame["spend"].tolist() == [30, 30]
        assert frame["source_leads"].tolist() == [5, 5]
        assert frame["meta_leads"].tolist() == [9, 9]
        assert frame["leads"].tolist() == [8, 9]
        assert frame.iloc[0]["cost_per_lead"] == 6
        assert frame.iloc[0]["cost_per_crm_lead"] == 30 / 8
        assert frame.iloc[0]["days_since_adset_started"] == 11
        adset_frame, _ = fd.load_daily_frame(ad_set_id="a1", campaign_id="c3")
        assert adset_frame["spend"].tolist() == [10, 10, 10]


def test_toggled_holiday_stays_visible_and_response_is_json_safe():
    frame = synthetic_frame()
    with mock.patch.object(fd, "load_daily_frame", return_value=(frame, {"observations": len(frame)})), mock.patch.object(fd, "_declared_baseline", return_value=("spend", "holiday_during_holiday")):
        result = fd.get_forecast_diagnostics(enabled=["spend", "meta_leads"])
    assert next(v for v in result["variables"] if v["key"] == "holiday_proximity")["status"] == "Excluded"
    expanded_keys = [v["key"] for v in result["expanded"]["variables"]]
    assert "holiday_proximity" in expanded_keys
    assert any(v["key"] == "holiday_during_holiday" for v in result["expanded"]["variables"])
    holiday_index = expanded_keys.index("holiday_proximity")
    assert result["expanded"]["matrix"][holiday_index][holiday_index] == 1
    assert all(c["feature"] != "holiday_during_holiday" for c in result["multivariate"]["coefficients"])
    json.dumps(result, allow_nan=False)


def test_api_validation_and_empty_enabled_contract():
    from fastapi import HTTPException
    from backend.app import forecast_diagnostics
    with pytest.raises(HTTPException) as error:
        forecast_diagnostics(start_date=date(2026, 2, 1), end_date=date(2026, 1, 1), enabled="")
    assert error.value.status_code == 400
    with mock.patch("backend.app.get_forecast_diagnostics", return_value={}) as call:
        forecast_diagnostics(campaign_id="c1,c2", enabled="")
        assert call.call_args.kwargs["enabled"] == []


def test_http_contract_validates_dates_and_protects_target():
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)
    assert client.get("/api/forecast/diagnostics?start_date=not-a-date").status_code == 422
    assert client.get("/api/forecast/diagnostics?start_date=2026-07-01&end_date=2026-06-01").status_code == 400
    assert client.get("/api/forecast/diagnostics?enabled=leads").status_code == 400
    assert client.get("/api/forecast/diagnostics?enabled=cost_per_crm_lead").status_code == 400
