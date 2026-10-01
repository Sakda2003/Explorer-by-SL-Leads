"""Read-only, daily-scope diagnostics. CRM Leads is never Meta's lead counter.

This module intentionally does not feed train_models or budget forecasts. Same-day
Meta measurements are explanatory covariates, not known future inputs.
"""
from __future__ import annotations

from functools import lru_cache
import math

import numpy as np
import pandas as pd

from . import core


DECLARED_KEYS = ("spend", "holiday_proximity", "days_since_adset_started", "frequency",
                 "ad_change_recency", "ad_set_change_recency", "day_of_week")
DECLARED_LABELS = ("Spend", "Holiday proximity", "Days since ad set started", "Frequency",
                   "Ad change recency", "Ad set change recency", "Day of week")
DECLARED_DESCRIPTIONS = (
    "Total Meta spend per day, in USD.",
    "Holiday timing encoded as four proximity buckets. Switch off until this context is ready.",
    "Days since the confirmed ad-set launch date. Requires a recorded start date.",
    "Daily impressions / summed available ad-set reach. Never averages row-level frequency.",
    "Recency of confirmed ad changes. Requires recorded change history.",
    "Recency of confirmed ad-set changes. Requires recorded change history.",
    "Daily weekday indicators, evaluated as one declared group.",
)
META_METRICS = (
    ("messaging_conversations_started", "Messaging conversations started", "Messaging conversations started", "Sum of Meta messaging conversations started."),
    ("cost_per_messaging_conversation", "Cost per messaging conversation started", "Amount spent (USD) / Messaging conversations started", "Total spend / total conversations; undefined with a zero denominator."),
    ("reach", "Reach", "Reach", "Sum of available ad-set reach for correlation diagnostics; portfolio audiences may overlap."),
    ("impressions", "Impressions", "Impressions", "Total Meta impressions per day."),
    ("ctr_all", "CTR (all)", "Clicks (all) / Impressions × 100", "Total all-clicks / total impressions × 100. Never substitutes link clicks."),
    ("cpm", "CPM", "Amount spent (USD) / Impressions × 1,000", "Total spend / total impressions × 1,000."),
    ("link_clicks", "Link clicks", "Link clicks", "Total Meta link clicks per day."),
    ("clicks_all", "Clicks (all)", "Clicks (all)", "Total clicks of all types, not just link clicks."),
    ("source_leads", "Leads (Meta export)", "Meta export: Leads", "The ad-platform Leads field. Separate from CRM Leads, the model outcome."),
    ("cost_per_lead", "Cost Per Lead", "Meta export: Cost per lead", "Total spend / the ad-platform Leads field. Never uses CRM Leads."),
    ("meta_leads", "Meta Leads", "Meta export: Meta leads", "The explicit Meta leads field. Separate from both CRM Leads and the export's Leads field."),
)


def variable_catalog() -> list[dict]:
    catalog = [{"key": "leads", "label": "Leads (CRM outcome)", "features": ["leads"], "kind": "outcome",
                "source": "daily_ad_set_aggregates.lead_count", "description": "CRM lead count per day. Fixed regression outcome.",
                "default_enabled": True, "model_eligible": False}]
    for key, label, (_, _, features), description in zip(DECLARED_KEYS, DECLARED_LABELS, core.DECLARED_OLS_GROUPS, DECLARED_DESCRIPTIONS):
        catalog.append({"key": key, "label": label, "features": list(features), "kind": "declared",
                        "source": key, "description": description, "default_enabled": True, "model_eligible": True})
        if key == "day_of_week":
            catalog[-1]["features"].append("is_weekend")  # Visible redundant encoding, never a baseline fit candidate.
    for key, label, source, description in META_METRICS:
        catalog.append({"key": key, "label": label, "features": [key], "kind": "meta", "source": source,
                        "description": description, "default_enabled": False, "model_eligible": True})
    # Explicitly display the existing CRM-derived ratio, but never let it enter OLS.
    catalog.append({"key": "cost_per_crm_lead", "label": "Cost per Lead (CRM)", "features": ["cost_per_crm_lead"],
                    "kind": "descriptive", "source": "Spend / CRM Leads", "default_enabled": False,
                    "model_eligible": False, "description": "Descriptive only: contains the Leads outcome in its denominator (target leakage)."})
    return catalog


def aggregate_meta_daily(raw: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Aggregate available ad-set observations onto the CRM daily calendar.

    Older rows predate some imported metrics and therefore contain NULLs. Treating one
    legacy NULL as a veto made an otherwise populated portfolio day entirely unavailable.
    At this diagnostic grain, missing counters are zero contributions and rates are rebuilt
    from daily totals. Reach is the sum of available ad-set reach and is therefore a scoped
    diagnostic total, not deduplicated portfolio reach.
    """
    result = pd.DataFrame(index=dates)
    sources = {"spend": "amount_spent_usd", "messaging_conversations_started": "messaging_conversations_started",
               "impressions": "impressions", "link_clicks": "link_clicks", "clicks_all": "clicks_all",
               "source_leads": "leads", "meta_leads": "meta_leads"}
    frame = raw.copy()
    if not frame.empty:
        frame["day"] = pd.to_datetime(frame["day"], errors="coerce").dt.normalize()
    for key, source in sources.items():
        result[key] = np.nan
        if frame.empty or source not in frame:
            continue
        numbers = pd.to_numeric(frame[source], errors="coerce").replace([np.inf, -np.inf], np.nan)
        numbers = numbers.where(numbers >= 0)
        totals = numbers.groupby(frame["day"]).sum(min_count=1)
        result[key] = totals.reindex(dates)
    result["reach"] = np.nan
    if not frame.empty and "reach" in frame:
        reach = pd.to_numeric(frame["reach"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        result["reach"] = reach.where(reach >= 0).groupby(frame["day"]).sum(min_count=1).reindex(dates)
    ratios = {
        "frequency": ("impressions", "reach", 1),
        "ctr_all": ("clicks_all", "impressions", 100),
        "cpm": ("spend", "impressions", 1000),
        "cost_per_messaging_conversation": ("spend", "messaging_conversations_started", 1),
        "cost_per_lead": ("spend", "source_leads", 1),
    }
    for key, (num, den, scale) in ratios.items():
        result[key] = result[num] / result[den].where(result[den] > 0) * scale
        # A directly reported rate is valid at its original single-row grain when
        # totals are absent. Never average these rates, infer rounded counters, or
        # override a known zero denominator with a reported rate.
        source = {"frequency": "frequency", "ctr_all": "ctr_all", "cpm": "cpm",
                  "cost_per_messaging_conversation": "cost_per_messaging_conversation_started",
                  "cost_per_lead": "cost_per_lead"}[key]
        if not frame.empty and source in frame:
            reported = pd.to_numeric(frame[source], errors="coerce").replace([np.inf, -np.inf], np.nan)
            grouped = reported.where(reported >= 0).groupby(frame["day"])
            single = grouped.first().where(grouped.size().eq(1)).reindex(dates)
            missing_input = (result[num].isna() | result[den].isna()) & ~result[den].eq(0)
            result[key] = result[key].where(~missing_input, single)
    return result.replace([np.inf, -np.inf], np.nan)


def load_daily_frame(ad_set_id=None, campaign_id=None, start_date=None, end_date=None):
    values, rows, scope = core._load_scope_feature_rows(ad_set_id, campaign_id)
    if values is None:
        return pd.DataFrame(columns=["leads"]), scope
    dates = pd.date_range(scope["date_start"], periods=len(values))
    frame = pd.DataFrame(rows, index=dates)
    frame["leads"] = values  # CRM-only. Never populated from daily_ad_performance.leads.
    clauses, params = [], []
    if ad_set_id:
        clauses.append("ad_set_id=?")
        params.append(str(ad_set_id).strip())
    elif campaign_id:
        ids = core._scope_id_values(campaign_id)
        clauses.append("campaign_id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(ids)
    with core.connect() as db:
        raw = pd.read_sql_query("SELECT * FROM daily_ad_performance" + (" WHERE " + " AND ".join(clauses) if clauses else ""), db, params=params)
    metrics = aggregate_meta_daily(raw, dates)
    for name in metrics:
        frame[name] = metrics[name]
    frame["cost_per_crm_lead"] = frame["spend"] / frame["leads"].where(frame["leads"] > 0)
    # Slice AFTER building age/recency features: changing the window must not reset clocks.
    if start_date:
        frame = frame.loc[frame.index >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame.loc[frame.index <= pd.Timestamp(end_date)]
    scope = {**scope, "observations": len(frame), "lead_total": float(frame["leads"].sum()),
             "date_start": frame.index.min().strftime("%Y-%m-%d") if len(frame) else None,
             "date_end": frame.index.max().strftime("%Y-%m-%d") if len(frame) else None}
    return frame.replace([np.inf, -np.inf], np.nan), scope


def _complete(frame, features):
    return frame.reindex(columns=["leads", *features]).dropna()


def _availability(frame, features):
    if not len(frame):
        return "No observations in this scope and date range."
    usable = [pd.to_numeric(frame.get(f, pd.Series(dtype=float)), errors="coerce").dropna() for f in features]
    if not any(len(s) >= 3 for s in usable):
        return "Fewer than 3 observed daily values; source data is missing or insufficient."
    if not any(s.nunique() > 1 for s in usable):
        return "Constant over this date range; correlation and coefficients are undefined."
    return ""


@lru_cache(maxsize=64)
def _declared_baseline(columns: tuple, records: tuple) -> tuple[str, ...]:
    """Frozen per data/window, not per toggle. Toggle changes never re-search old groups."""
    frame = pd.DataFrame(records, columns=columns).astype(float)
    varying = [f for f in columns if f != "leads" and frame[f].count() >= 12 and frame[f].nunique() > 1]
    complete = _complete(frame, varying)
    if len(complete) < 12:
        return ()
    result = core._forward_select_declared_features(complete["leads"].to_numpy(), complete.drop(columns="leads").to_dict("records"))
    return tuple(result["features"])


def _block(frame, features):
    return core._ols_block_fit(frame["leads"].to_numpy(), frame.to_dict("records"), features)


def _candidate_vif(frame, features, candidate):
    """Candidate VIF against the accepted design, including multivariate dependencies."""
    y = frame[candidate].to_numpy(float)
    centered = y - y.mean()
    total = float(centered @ centered)
    if total <= 1e-12:
        return math.inf
    if not features:
        return 1.0
    x = frame[features].to_numpy(float)
    std = np.std(x, axis=0)
    x = (x - x.mean(axis=0)) / np.where(std > 1e-9, std, 1)
    x = np.c_[np.ones(len(x)), x]
    resid = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
    unexplained = float(resid @ resid) / total
    return 1 / unexplained if unexplained > 1e-12 else math.inf


def fit_active_model(frame, catalog, enabled):
    declared_features = [f for v in catalog if v["kind"] == "declared" for f in v["features"]]
    base_frame = frame.reindex(columns=["leads", *declared_features])
    # None is a stable cache key for missing values; NaN is not equal to itself.
    records = tuple(tuple(float(v) if pd.notna(v) else None for v in row) for row in base_frame.to_numpy())
    baseline = _declared_baseline(tuple(base_frame.columns), records)
    permitted = {f for v in catalog if v["key"] in enabled and v["model_eligible"] for f in v["features"]}
    selected = [f for f in baseline if f in permitted]
    accepted = frame.loc[_complete(frame, selected).index]
    # Prevent an empty/constant outcome from producing a misleading fit or search.
    if len(accepted) < 12 or accepted["leads"].nunique() < 2:
        selected = []
    pending = [v for v in catalog if v["kind"] == "meta" and v["key"] in enabled and v["model_eligible"]]
    steps, reasons = [], {}
    while pending:
        trials = []
        for variable in pending:
            key = variable["key"]
            trial_features = [*selected, key]
            # Use only rows retained by accepted additions, and a paired sample for comparison.
            sample = _complete(accepted, trial_features)
            entry = {"key": key, "label": variable["label"], "round": len({s["round"] for s in steps}) + 1,
                     "before": None, "after": None, "gain": None, "p_value": None, "vif": None,
                     "sample_size": len(sample), "result": "rejected", "reason": ""}
            minimum = max(12, len(trial_features) + 6)
            if len(sample) < minimum or sample["leads"].nunique() < 2:
                entry["reason"] = f"Insufficient complete daily observations ({len(sample)}; need {minimum}) or constant CRM Leads."
            elif sample[key].nunique() < 2:
                entry["reason"] = "Constant candidate on the shared daily sample."
            else:
                base, trial = _block(sample, selected), _block(sample, trial_features)
                vif = _candidate_vif(sample, selected, key)
                # Also reject a new metric that pushes an already accepted predictor
                # over the VIF limit. A pre-existing baseline violation is not a veto
                # unless this candidate worsens it (the baseline itself is preserved).
                worsened = []
                for feature in selected:
                    prior_vif = _candidate_vif(sample, [f for f in selected if f != feature], feature)
                    after_vif = _candidate_vif(sample, [f for f in trial_features if f != feature], feature)
                    if after_vif > 10 and after_vif > prior_vif + 1e-6:
                        worsened.append(feature)
                entry["vif"] = float(vif) if math.isfinite(vif) else None
                if base and trial:
                    entry.update(before=base["adjusted_r_squared"], after=trial["adjusted_r_squared"],
                                 gain=trial["adjusted_r_squared"] - base["adjusted_r_squared"],
                                 p_value=core._partial_f_p_value(base, trial))
                if vif > 10 or worsened:
                    entry["reason"] = "Multicollinearity: candidate VIF exceeds 10 or is rank-dependent." if vif > 10 else "Multicollinearity: worsens VIF above 10 for " + ", ".join(worsened) + "."
                elif not base or not trial or entry["p_value"] is None:
                    entry["reason"] = "Insufficient independent information to estimate the candidate."
                elif entry["gain"] <= core.FORWARD_SELECTION_MIN_GAIN:
                    entry["reason"] = "No adjusted R² improvement above 0.000001."
                elif entry["p_value"] >= core.FORWARD_SELECTION_MAX_P:
                    entry["reason"] = "Partial F-test p-value does not meet the 0.10 entry threshold."
                else:
                    entry["result"] = "eligible"
                    entry["reason"] = "Improves adjusted R² and passes the partial F-test and VIF gates."
            trials.append((entry, sample))
        eligible = [pair for pair in trials if pair[0]["result"] == "eligible"]
        best = max(eligible, key=lambda pair: pair[0]["gain"]) if eligible else None
        for entry, sample in trials:
            if best and entry is best[0]:
                entry["result"] = "accepted"
            elif entry["result"] == "eligible":
                entry["result"] = "deferred"
                entry["reason"] = "Another candidate improved adjusted R² more; reconsidered next round."
            entry["order"] = len(steps) + 1
            steps.append(entry)
            reasons[entry["key"]] = entry["reason"]
        if best is None:
            break
        selected.append(best[0]["key"])
        # A rejected candidate never changes either the accepted features or its sample.
        accepted = frame.loc[best[1].index]
        pending = [v for v in pending if v["key"] != best[0]["key"]]
    sample = _complete(accepted, selected)
    summary = core._fit_ols_summary(sample["leads"].to_numpy(), sample.to_dict("records"), selected, "Multivariate OLS") if selected else None
    if summary:
        selected = [row["feature"] for row in summary["coefficients"] if row["feature"] != "Intercept"]
        by_feature = {f: v for v in catalog for f in v["features"]}
        for coefficient in summary["coefficients"]:
            spec = by_feature.get(coefficient["feature"])
            coefficient["source"] = "Intercept" if not spec else ("Declared baseline" if spec["kind"] == "declared" else "Forward selected")
            if spec and len(spec["features"]) == 1:
                coefficient["term"] = spec["label"]
        summary["dep_variable"] = "Leads"
    return summary, selected if summary else [], baseline, steps, reasons


def correlation_matrix(frame, columns):
    numeric = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    correlations = numeric.corr(min_periods=3)
    counts = numeric.notna().astype(int).T @ numeric.notna().astype(int)
    matrix, explanations = [], []
    for a in columns:
        values, details = [], []
        for b in columns:
            value = correlations.loc[a, b]
            n = int(counts.loc[a, b])
            values.append(round(float(value), 4) if pd.notna(value) else None)
            details.append(f"Pearson r; {n} paired daily observations." if pd.notna(value) else
                           f"N/A: {n} paired days; at least 3 and variation in both variables are required.")
        matrix.append(values)
        explanations.append(details)
    return matrix, explanations


def _expanded_correlation_frame(frame: pd.DataFrame, catalog: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    expanded_frame = frame.copy()
    expanded = []
    holiday_scores = {
        "holiday_31_60_days": 1,
        "holiday_15_30_days": 2,
        "holiday_0_14_days": 3,
        "holiday_during_holiday": 4,
    }
    for variable in catalog:
        if variable["key"] == "holiday_proximity":
            columns = expanded_frame.reindex(columns=holiday_scores.keys()).apply(pd.to_numeric, errors="coerce")
            available = columns.notna().any(axis=1)
            expanded_frame["holiday_proximity"] = columns.fillna(0).mul(pd.Series(holiday_scores)).max(axis=1).where(available)
            expanded.append({"key": "holiday_proximity", "label": "Holiday proximity", "variable_key": variable["key"],
                             "status": variable["status"], "enabled": variable["enabled"], "group_summary": True})
        for feature in variable["features"]:
            expanded.append({"key": feature, "label": variable["label"] if len(variable["features"]) == 1 else core._feature_label(feature),
                             "variable_key": variable["key"], "status": variable["status"], "enabled": variable["enabled"],
                             "group_summary": False})
    return expanded_frame, expanded


def get_forecast_diagnostics(ad_set_id=None, campaign_id=None, start_date=None, end_date=None, enabled=None):
    catalog = variable_catalog()
    allowed = {v["key"] for v in catalog if v["model_eligible"]}
    enabled = set(allowed if enabled is None else enabled)
    unknown = enabled - allowed
    if unknown:
        raise ValueError("Not eligible as predictors: " + ", ".join(sorted(unknown)))
    frame, scope = load_daily_frame(ad_set_id, campaign_id, start_date, end_date)
    summary, selected, baseline, steps, reasons = fit_active_model(frame, catalog, enabled)
    for variable in catalog:
        key = variable["key"]
        warning = _availability(frame, variable["features"])
        observed = max((int(frame[f].count()) for f in variable["features"] if f in frame), default=0)
        status = ("Outcome" if key == "leads" else "Descriptive only" if not variable["model_eligible"] else
                  "Unavailable" if warning else "Excluded" if key not in enabled else
                  "Included" if any(f in selected for f in variable["features"]) else "Candidate" if variable["kind"] == "meta" else "Excluded")
        detail = reasons.get(key, "")
        if variable["kind"] == "declared" and key in enabled and status == "Excluded":
            detail = "Not selected by the existing declared baseline; toggles do not re-search that pool."
        variable.update(enabled=key == "leads" or key in enabled, status=status, warning=warning,
                        detail=detail, observed_days=observed,
                        missing_days=max(0, len(frame) - observed))
    expanded_frame, expanded = _expanded_correlation_frame(frame, catalog)
    matrix, explanations = correlation_matrix(expanded_frame, [v["key"] for v in expanded])
    declared = [v for v in catalog if v["kind"] in ("outcome", "declared", "meta")]
    indices = {v["key"]: [i for i, column in enumerate(expanded) if column["variable_key"] == v["key"] and not column.get("group_summary")] for v in declared}
    declared_matrix, declared_details = [], []
    for row in declared:
        cells, details = [], []
        for col in declared:
            pairs = [(matrix[i][j], i, j) for i in indices[row["key"]] for j in indices[col["key"]] if matrix[i][j] is not None]
            best = max(pairs, key=lambda p: abs(p[0])) if pairs else None
            cells.append(best[0] if best else None)
            details.append(("Strongest encoded-term pair: " + expanded[best[1]]["label"] + " × " + expanded[best[2]]["label"] + ". " + explanations[best[1]][best[2]]) if best else "N/A: missing, constant, or fewer than 3 paired daily values.")
        declared_matrix.append(cells)
        declared_details.append(details)
    empty_reason = "" if summary else ("Enable at least one predictor to estimate a multivariate model." if not enabled else "No estimable predictors passed selection, or there are too few complete daily observations (minimum 12; more for larger models).")
    return {"scope": scope, "variables": catalog, "expanded": {"variables": expanded, "matrix": matrix, "explanations": explanations},
            "declared": {"variables": declared, "matrix": declared_matrix, "explanations": declared_details},
            "multivariate": summary, "empty_reason": empty_reason,
            "selection": {"steps": steps, "baseline_features": list(baseline), "selected_features": selected,
                          "method": "Frozen declared baseline + forward selection of enabled Meta metrics",
                          "alpha": core.FORWARD_SELECTION_MAX_P, "min_gain": core.FORWARD_SELECTION_MIN_GAIN}}
