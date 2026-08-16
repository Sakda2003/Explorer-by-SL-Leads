"""Build the per-ad-set regression report (univariate + multivariate OLS, correlation matrix).

One HTML page covering every ad set in `daily_ad_set_aggregates`, ranked by multivariate
adjusted R2. Numbers come from the app's own fitting helpers at ad-set scope, so the report
and the Dataset / Forecast OLS panels can never disagree.

    python build_regression_report.py [output.html]

Default output: Dataset/regression_report.html. See Vault/Modeling/Per-Ad-Set-Regression-Report.md.
"""
import base64
import html
import json
import math
import sys
from pathlib import Path

import numpy as np

from backend import core

ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Dataset" / "regression_report.html"
FONT_DIR = ROOT / "frontend" / "node_modules" / "@fontsource-variable"

# This report names three variables by their model feature key rather than core.py's friendly
# label, so a coefficient here can be traced straight back to the declared spec. Patching
# core._feature_label rather than rewriting strings afterwards catches all four places a label
# reaches the page: the univariate rows built here, the coefficient terms _fit_ols_summary
# builds, the matrix axis labels get_dataset_correlation builds, and the coverage details
# _declared_variable_coverage builds. The app's own UI is untouched.
REPORT_LABELS = {
    "days_since_adset_started": "days_since_ad_set_started",
    "ad_change_recency": "ad_change_recency",
    "ad_set_change_recency": "ad_set_change_recency",
}
_core_feature_label = core._feature_label


def _report_feature_label(feature: str) -> str:
    return REPORT_LABELS.get(feature, _core_feature_label(feature))


core._feature_label = _report_feature_label

# The declared spec calls variable 4 `days_since_adset_started`; the report spells the same
# thing `days_since_ad_set_started` so the coverage card and the tables agree.
COVERAGE_NAMES = {"days_since_adset_started": "days_since_ad_set_started"}

# The multivariate presentation is intentionally at the declared-variable level. The coefficient
# table shows eight rows: the intercept plus the seven predictor groups requested for the report.
# The fitted equation still uses every varying encoded term inside each group.
MULTIVARIATE_DISPLAY_VARIABLES = (
    {"number": 1, "label": "Intercept", "features": (), "role": "intercept"},
    {"number": 2, "label": "Spent", "features": ("spend",)},
    {"number": 3, "label": "Holiday_proximity", "features": (
        "holiday_during_holiday", "holiday_0_14_days", "holiday_15_30_days", "holiday_31_60_days",
    )},
    {"number": 4, "label": "days_since_ad_set_started", "features": ("days_since_adset_started",)},
    {"number": 5, "label": "frequency", "features": ("frequency",)},
    {"number": 6, "label": "ad_change_recency", "features": ("ad_change_recency",)},
    {"number": 7, "label": "ad_set_change_recency", "features": ("ad_set_change_recency",)},
    {"number": 8, "label": "Days_of_the_week", "features": (
        "weekday_0", "weekday_1", "weekday_2", "weekday_3", "weekday_4", "weekday_5", "weekday_6",
    )},
)


def _ols_design_result(values, feature_rows, features: list[str]) -> dict:
    """Return rank and SSE for a nested-model partial F test."""
    y = np.clip(
        np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0),
        0.0,
        None,
    )
    if features:
        design = np.asarray(
            [[row.get(feature, 0.0) for feature in features] for row in feature_rows],
            dtype=float,
        )
        x = np.c_[np.ones(len(design)), design]
    else:
        x = np.ones((len(y), 1), dtype=float)
    coefficients = np.linalg.pinv(x) @ y
    residuals = y - x @ coefficients
    return {
        "rank": int(np.linalg.matrix_rank(x)),
        "sse": float(np.sum(residuals**2)),
        "coefficients": coefficients,
    }


def grouped_multivariate_rows(values, feature_rows, selected: list[str], fit: dict | None,
                              selection: dict | None = None) -> list[dict]:
    """Collapse encoded OLS terms into the eight displayed coefficient rows.

    Multi-term groups do not have a unique single coefficient. For the compact coefficient table
    we show the strongest fitted encoded term as the representative row, while keeping the group
    label visible. Exact aliases stay marked instead of being presented as independent effects.
    """
    all_coefficients = {
        coefficient["feature"]: coefficient
        for coefficient in (fit or {}).get("coefficients", [])
    }
    intercept = all_coefficients.get("Intercept")
    rows = [{
        "number": 1,
        "label": "Intercept",
        "status": "estimable" if intercept else "not_fitted",
        "term_count": 1 if intercept else 0,
        "df": None,
        "features": [],
        "f_statistic": None,
        "p_value": intercept["p_value"] if intercept else None,
        "partial_r_squared": None,
        "coefficient": intercept,
        "representative": None,
    }]
    selected_set = set(selected)
    coefficient_map = {
        coefficient["feature"]: coefficient
        for coefficient in (fit or {}).get("coefficients", [])
        if coefficient["feature"] != "Intercept"
    }
    full = _ols_design_result(values, feature_rows, selected) if fit is not None else None
    df_residuals = (len(values) - full["rank"]) if full is not None else None
    mse_full = (
        full["sse"] / df_residuals
        if full is not None and df_residuals is not None and df_residuals > 0
        else None
    )

    for spec in MULTIVARIATE_DISPLAY_VARIABLES[1:]:
        available = [
            feature for feature in spec["features"]
            if feature_rows is not None
            and float(np.std([row.get(feature, 0.0) for row in feature_rows] or [0.0])) > 1e-9
        ]
        active = [feature for feature in spec["features"] if feature in selected_set]
        base = {
            "number": spec["number"],
            "label": spec["label"],
            "term_count": len(active) if active else len(available),
            "features": active if active else available,
            "df": None,
            "f_statistic": None,
            "p_value": None,
            "partial_r_squared": None,
            "coefficient": None,
            "representative": None,
        }
        if values is None or feature_rows is None:
            rows.append({**base, "status": "no_data"})
            continue
        if not available:
            rows.append({**base, "status": "constant"})
            continue
        if not active:
            # Two different absences since forward selection drives this table: the variable is
            # aliased with something already in the design (collinear), or it is perfectly
            # estimable and simply did not improve adjusted R2 (not_selected).
            rejection = (selection or {}).get("rejected", {}).get(spec["number"])
            reason = (rejection or {}).get("reason")
            status = "not_selected" if reason in ("no_gain", "observations") else "collinear"
            rows.append({**base, "status": status,
                         "selection_delta": (rejection or {}).get("delta")})
            continue
        if fit is None or full is None or mse_full is None:
            rows.append({**base, "status": "not_fitted"})
            continue

        reduced_features = [feature for feature in selected if feature not in set(spec["features"])]
        reduced = _ols_design_result(values, feature_rows, reduced_features)
        group_df = full["rank"] - reduced["rank"]
        if group_df <= 0:
            rows.append({**base, "status": "aliased", "df": 0})
            continue

        group_ss = max(0.0, reduced["sse"] - full["sse"])
        f_statistic = (group_ss / group_df) / max(mse_full, 1e-12)
        p_value = core._f_survival_p_value(f_statistic, group_df, df_residuals)
        partial_denominator = group_ss + full["sse"]
        partial_r_squared = group_ss / partial_denominator if partial_denominator > 1e-12 else 0.0
        result = {
            **base,
            "status": "estimable",
            "df": group_df,
            "f_statistic": float(f_statistic),
            "p_value": p_value,
            "partial_r_squared": float(partial_r_squared),
        }
        group_coefficients = [
            coefficient_map[feature]
            for feature in active
            if feature in coefficient_map
        ]
        if group_coefficients:
            representative = max(
                group_coefficients,
                key=lambda coefficient: abs(coefficient["t"] or 0.0),
            )
            result["coefficient"] = representative
            result["representative"] = representative["term"] if len(group_coefficients) > 1 else None
        rows.append(result)
    return rows


def collect() -> list[dict]:
    """Fit both models and the correlation matrix for every ad set."""
    with core.connect() as db:
        ad_sets = [
            dict(row)
            for row in db.execute(
                """SELECT utm_ad_set_id AS ad_set_id,
                          COUNT(*) AS active_days,
                          SUM(lead_count) AS leads,
                          MIN(aggregate_date) AS first_day,
                          MAX(aggregate_date) AS last_day
                   FROM daily_ad_set_aggregates
                   GROUP BY utm_ad_set_id"""
            ).fetchall()
        ]
        names = {
            str(row[0]): row[1]
            for row in db.execute(
                "SELECT ad_set_id, campaign_name FROM daily_ad_performance "
                "WHERE campaign_name IS NOT NULL GROUP BY ad_set_id"
            ).fetchall()
        }
        # Ad sets with no Meta spend rows still have CRM leads; fall back to the campaign name
        # carried on the lead itself so they aren't nameless in the report.
        lead_names = {
            str(row[0]): row[1]
            for row in db.execute(
                "SELECT utm_ad_set_id, utm_campaign FROM lead_events "
                "WHERE utm_campaign IS NOT NULL AND utm_campaign != '' GROUP BY utm_ad_set_id"
            ).fetchall()
        }
        spend_by_set = {
            str(row[0]): float(row[1] or 0.0)
            for row in db.execute(
                "SELECT ad_set_id, SUM(amount_spent_usd) FROM daily_ad_performance GROUP BY ad_set_id"
            ).fetchall()
        }

    results = []
    for meta in ad_sets:
        ad_set_id = str(meta["ad_set_id"])
        values, feature_rows, scope = core._load_scope_feature_rows(ad_set_id=ad_set_id)
        entry = {
            "ad_set_id": ad_set_id,
            "campaign_name": names.get(ad_set_id) or lead_names.get(ad_set_id),
            "has_spend_rows": ad_set_id in names,
            "total_spend": spend_by_set.get(ad_set_id, 0.0),
            "active_days": int(meta["active_days"]),
            "leads": float(meta["leads"] or 0.0),
            "first_day": meta["first_day"],
            "last_day": meta["last_day"],
            "scope": scope,
            "univariate": [],
            "multivariate": None,
            "multivariate_groups": grouped_multivariate_rows(None, None, [], None),
            "multivariate_terms_wanted": 0,
            "correlation": None,
            "reason": None,
        }
        if values is None or feature_rows is None:
            entry["reason"] = "no_rows"
            results.append(entry)
            continue

        # Forward selection, matching the app's Multivariate OLS card. The forecast path still
        # fits every declared variable under ridge -- measured better on held-out WAPE, see
        # core.OLS_FORECAST_USES_FORWARD_SELECTION.
        selection = core._forward_select_declared_features(values, feature_rows)
        selected = selection["features"]
        entry["selection_order"] = [
            next(label for number, label, _ in core.DECLARED_OLS_GROUPS if number == group)
            for group in selection["order"]
        ]
        entry["multivariate_terms_wanted"] = len(selected)
        entry["multivariate_days_needed"] = max(12, len(selected) + 6) if selected else 12
        entry["multivariate"] = (
            core._fit_ols_summary(values, feature_rows, selected, "Multivariate OLS") if selected else None
        )
        entry["multivariate_groups"] = grouped_multivariate_rows(
            values, feature_rows, selected, entry["multivariate"], selection
        )

        # One simple regression per declared feature that actually varies over this window.
        for feature in selected:
            fit = core._fit_ols_summary(values, feature_rows, [feature], "OLS")
            if fit is None:
                continue
            spec = next(
                (s for s in core.DECLARED_VARIABLES if feature in s["features"]), None
            )
            slope = next((c for c in fit["coefficients"] if c["feature"] == feature), None)
            series = np.asarray([row.get(feature, 0.0) for row in feature_rows], dtype=float)
            y = np.asarray(values, dtype=float)
            corr = 0.0
            if series.std() > 1e-9 and y.std() > 1e-9:
                corr = float(np.corrcoef(series, y)[0, 1])
            entry["univariate"].append({
                "feature": feature,
                "label": core._feature_label(feature),
                "variable_number": spec["number"] if spec else None,
                "variable_name": spec["name"] if spec else None,
                "r_squared": fit["r_squared"],
                "adjusted_r_squared": fit["adjusted_r_squared"],
                "coef": slope["coef"] if slope else None,
                "std_err": slope["std_err"] if slope else None,
                "t": slope["t"] if slope else None,
                "p_value": slope["p_value"] if slope else None,
                "ci_low": slope["ci_low"] if slope else None,
                "ci_high": slope["ci_high"] if slope else None,
                "correlation": corr,
                "no_observations": fit["no_observations"],
                "rmse": fit["rmse"],
            })
        entry["univariate"].sort(key=lambda row: row["r_squared"], reverse=True)

        entry["correlation"] = core.get_dataset_correlation(ad_set_id=ad_set_id)
        entry["declared_variables"] = core._declared_variable_coverage(
            feature_rows, selected, entry["multivariate"], selection=selection
        )
        if entry["multivariate"] is None:
            if selected:
                entry["reason"] = "too_few_observations"
            elif selection["rejected"]:
                # Variables were available and measured; none of them beat an intercept-only
                # model. That is a result, not missing data, and must not read as the latter.
                entry["reason"] = "nothing_selected"
            else:
                entry["reason"] = "no_varying_features"
        results.append(entry)
    return results


data = collect()


def font_uri(path: Path) -> str:
    return "data:font/woff2;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


INTER = font_uri(FONT_DIR / "inter-tight" / "files" / "inter-tight-latin-wght-normal.woff2")
MONO = font_uri(FONT_DIR / "jetbrains-mono" / "files" / "jetbrains-mono-latin-wght-normal.woff2")

# ---------------------------------------------------------------- ordering
fitted = [row for row in data if row.get("multivariate")]
unfitted = [row for row in data if not row.get("multivariate")]
fitted.sort(key=lambda row: row["multivariate"]["adjusted_r_squared"], reverse=True)
unfitted.sort(key=lambda row: (row["scope"]["observations"], row["leads"]), reverse=True)
ordered = fitted + unfitted

ADJ_MIN, ADJ_MAX = -0.25, 0.60


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def num(value, digits=3, dash="--"):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return dash
    return f"{value:.{digits}f}"


def sci(value, digits=3):
    if value is None or not math.isfinite(value):
        return "--"
    if value != 0 and (abs(value) < 1e-3 or abs(value) >= 1e5):
        return f"{value:.{digits - 1}e}"
    return f"{value:.{digits}f}"


def pval(value):
    if value is None:
        return "--"
    if value < 0.0001:
        return "&lt;.0001"
    return f"{value:.4f}"


def sig_class(p):
    if p is None:
        return ""
    if p < 0.01:
        return " is-sig-strong"
    if p < 0.05:
        return " is-sig"
    return ""


def money(value):
    return f"${value:,.0f}"


def slug(ad_set_id):
    return "set-" + str(ad_set_id)[-8:]


def short_name(row):
    name = row.get("campaign_name") or "Unnamed ad set"
    return name.replace("Leads | ", "").replace("Leads| ", "").replace("Engagement | ", "ENG · ")


def cell_style(value):
    """Diverging fill for the compact declared-variable matrix."""
    weight = min(100, round(abs(value) * 100))
    pole = "--corr-hot" if value >= 0 else "--corr-cold"
    strong = " is-strong" if abs(value) >= 0.45 else ""
    return f' class="mono{strong}" style="background:color-mix(in oklab, var({pole}) {weight}%, var(--corr-zero))"'


DECLARED_MATRIX_LABELS = {
    1: "Leads",
    2: "Spend",
    3: "Holiday proximity",
    4: "Ad set age",
    5: "Frequency",
    6: "Ad change recency",
    7: "Ad set change recency",
    8: "Day of week",
}


def declared_correlation(correlation):
    """Collapse feature-level Pearson r to the app's eight declared variable groups.

    For a pair of multi-column groups, keep the underlying feature pair with the largest
    absolute correlation and preserve its sign. This matches DatasetPage's declared view.
    """
    variables = correlation.get("variables", [])
    matrix = correlation.get("matrix", [])
    groups: dict[int, dict] = {}
    for index, variable in enumerate(variables):
        number = int(variable["variable_number"])
        group = groups.setdefault(number, {
            "number": number,
            "name": DECLARED_MATRIX_LABELS.get(number, variable["variable_name"]),
            "indices": [],
        })
        group["indices"].append(index)
    ordered = [groups[number] for number in sorted(groups)]
    collapsed = []
    for row_group in ordered:
        row = []
        for col_group in ordered:
            if row_group["number"] == col_group["number"]:
                row.append(1.0)
                continue
            strongest = 0.0
            for row_index in row_group["indices"]:
                for col_index in col_group["indices"]:
                    value = float(matrix[row_index][col_index])
                    if abs(value) > abs(strongest):
                        strongest = value
            row.append(round(strongest, 2))
        collapsed.append(row)
    return {"variables": ordered, "matrix": collapsed}


def collinear_pairs(correlation):
    """Off-diagonal predictor pairs at |r| >= 0.95 -- these make individual coefficients unreadable."""
    variables = correlation.get("variables", [])
    matrix = correlation.get("matrix", [])
    out = []
    for i in range(len(variables)):
        for j in range(i + 1, len(variables)):
            if variables[i]["key"] == "leads" or variables[j]["key"] == "leads":
                continue
            value = matrix[i][j]
            if abs(value) >= 0.95:
                out.append((variables[i]["label"], variables[j]["label"], value))
    return out


# ---------------------------------------------------------------- portfolio numbers
window_start = min(row["scope"].get("date_start") or "9999" for row in data)
window_end = max(row["scope"].get("date_end") or "0000" for row in data)
total_leads = sum(row["leads"] for row in data)
total_spend = sum(row["total_spend"] for row in data)
adj_values = sorted(row["multivariate"]["adjusted_r_squared"] for row in fitted)
median_adj = adj_values[len(adj_values) // 2]
spend_sig = sum(
    1
    for row in fitted
    for u in row["univariate"]
    if u["feature"] == "spend" and u["p_value"] is not None and u["p_value"] < 0.05
)
spend_present = sum(1 for row in fitted for u in row["univariate"] if u["feature"] == "spend")

# ---------------------------------------------------------------- leaderboard
board_rows = []
for index, row in enumerate(ordered, start=1):
    mv = row.get("multivariate")
    spend_uni = next((u for u in row["univariate"] if u["feature"] == "spend"), None)
    if mv:
        adj = mv["adjusted_r_squared"]
        span = ADJ_MAX - ADJ_MIN
        zero = (0 - ADJ_MIN) / span * 100
        point = (adj - ADJ_MIN) / span * 100
        left, width = min(zero, point), abs(point - zero)
        tone = "pos" if adj >= 0 else "neg"
        bar = (
            f'<span class="bar"><span class="bar-zero" style="left:{zero:.2f}%"></span>'
            f'<span class="bar-fill is-{tone}" style="left:{left:.2f}%;width:{max(width, 0.6):.2f}%"></span></span>'
        )
        adj_cell = f'<span class="mono">{num(adj)}</span>{bar}'
        r2_cell = f'<span class="mono">{num(mv["r_squared"])}</span>'
        f_cell = f'<span class="mono{sig_class(mv["f_p_value"])}">{pval(mv["f_p_value"])}</span>'
    else:
        adj_cell = '<span class="thin-note">no fit</span>'
        r2_cell = '<span class="mono dim">--</span>'
        f_cell = '<span class="mono dim">--</span>'
    uni_cell = (
        f'<span class="mono">{num(spend_uni["r_squared"])}</span>'
        if spend_uni
        else '<span class="mono dim">--</span>'
    )
    board_rows.append(
        f"""<tr>
 <td class="rank mono">{index if mv else '<span class="dim">&middot;</span>'}</td>
 <td class="name"><a href="#{slug(row['ad_set_id'])}">{esc(short_name(row))}</a>
   <span class="id mono">{esc(row['ad_set_id'])}</span></td>
 <td class="mono">{row['scope']['observations']}</td>
 <td class="mono">{row['leads']:,.0f}</td>
 <td class="mono">{money(row['total_spend'])}</td>
 <td class="uni">{uni_cell}</td>
 <td class="mono">{r2_cell}</td>
 <td class="adj">{adj_cell}</td>
 <td>{f_cell}</td>
</tr>"""
    )

# ---------------------------------------------------------------- sections
sections = []
for index, row in enumerate(ordered, start=1):
    mv = row.get("multivariate")
    scope = row["scope"]
    correlation = row.get("correlation") or {}
    rank_label = f'{index:02d}' if mv else '<span class="unranked">unranked</span>'

    chips = [
        f'<span class="chip"><span class="chip-k">days</span><span class="chip-v mono">{scope["observations"]}</span></span>',
        f'<span class="chip"><span class="chip-k">leads</span><span class="chip-v mono">{row["leads"]:,.0f}</span></span>',
        f'<span class="chip"><span class="chip-k">spend</span><span class="chip-v mono">{money(row["total_spend"])}</span></span>',
        f'<span class="chip"><span class="chip-k">window</span><span class="chip-v mono">{esc(scope.get("date_start"))} &rarr; {esc(scope.get("date_end"))}</span></span>',
    ]
    if not row.get("has_spend_rows"):
        chips.append('<span class="chip is-warn"><span class="chip-v">no Meta spend rows</span></span>')

    if mv:
        stats = [
            ("R&sup2;", num(mv["r_squared"]), ""),
            ("Adj. R&sup2;", num(mv["adjusted_r_squared"]), " is-lead"),
            ("F", num(mv["f_statistic"], 2), ""),
            ("Prob (F)", pval(mv["f_p_value"]), sig_class(mv["f_p_value"])),
            ("RMSE", num(mv["rmse"], 2), ""),
            ("AIC", num(mv["aic"], 1), ""),
            ("BIC", num(mv["bic"], 1), ""),
            ("Durbin-Watson", num(mv["durbin_watson"], 2), ""),
            ("Df model / resid", f'{mv["df_model"]} / {mv["df_residuals"]}', ""),
        ]
        stat_html = "".join(
            f'<div class="stat{tone}"><span class="stat-k">{label}</span>'
            f'<span class="stat-v mono">{value}</span></div>'
            for label, value, tone in stats
        )
        fit_block = f'<div class="stats">{stat_html}</div>'
    else:
        wanted = row.get("multivariate_terms_wanted", 0)
        needed = row.get("multivariate_days_needed", 12)
        if row.get("reason") == "nothing_selected":
            # Not a data problem: predictors varied, forward selection measured each one, and
            # none of them beat predicting this ad set's average day.
            tag, reason = "No variable selected", (
                f"Forward selection kept none of the declared variables across this ad set's "
                f"{scope['observations']}-day window - no candidate improved adjusted R&sup2; "
                f"over an intercept-only model. Per-variable margins are in the table below."
            )
        elif wanted == 0:
            tag, reason = "Insufficient data", (
                f"Every candidate predictor is constant across this ad set's "
                f"{scope['observations']}-day window, so nothing can be estimated."
            )
        else:
            tag, reason = "Insufficient data", (
                f"Forward selection kept {wanted} terms, which needs {needed} days. "
                f"This ad set has {scope['observations']}."
            )
        fit_block = f'<div class="no-fit"><span class="no-fit-tag">{tag}</span><p>{reason}</p></div>'

    # ---- univariate table: only the spend-only simple regression is shown.
    spend_uni = next((u for u in row["univariate"] if u["feature"] == "spend"), None)
    if spend_uni:
        u = spend_uni
        uni_body = f"""<tr>
 <td class="term coef-name">spent</td>
 <td class="mono">{sci(u['coef'])}</td>
 <td class="mono">{sci(u['std_err'])}</td>
 <td class="mono">{num(u['t'], 2)}</td>
 <td class="mono{sig_class(u['p_value'])}">{pval(u['p_value'])}</td>
 <td class="mono muted-num">{sci(u['ci_low'])}</td>
 <td class="mono muted-num">{sci(u['ci_high'])}</td>
 <td class="mono">{num(u['r_squared'], 4)}</td>
</tr>"""
        uni_table = f"""<div class="table-wrap">
<table class="grid coef-grid uni-grid">
 <thead><tr><th class="term">Variable</th><th>Coef</th><th>Std err</th>
 <th>t</th><th>P&gt;|t|</th><th>0.025</th><th>0.975</th><th>R&sup2;</th></tr></thead>
 <tbody>{uni_body}</tbody>
</table></div>"""
    else:
        uni_table = '<p class="empty">Spend has no usable variation in this ad set window.</p>'

    # ---- multivariate table. Always show the eight requested coefficient rows, not the expanded
    # holiday/weekday design columns. Multi-term variables display their strongest encoded term.
    mv_body = []
    for group in row["multivariate_groups"]:
        status = group["status"]
        coefficient = group.get("coefficient")
        if status == "constant":
            label_note = '<span class="coef-sub">no variation</span>'
        elif status == "not_fitted":
            label_note = '<span class="coef-sub">fit unavailable</span>'
        elif status == "aliased":
            label_note = '<span class="coef-sub">aliased</span>'
        elif status == "collinear":
            label_note = '<span class="coef-sub">omitted</span>'
        elif status == "not_selected":
            delta = group.get("selection_delta")
            margin = f"not selected ({delta:+.4f} adj R&sup2;)" if delta is not None else "not selected"
            label_note = f'<span class="coef-sub">{margin}</span>'
        elif status == "no_data":
            label_note = '<span class="coef-sub">no observations</span>'
        else:
            label_note = ""

        if coefficient:
            coef_cell = f'<td class="mono">{sci(coefficient["coef"])}</td>'
            se_cell = f'<td class="mono">{sci(coefficient["std_err"])}</td>'
            t_cell = f'<td class="mono">{num(coefficient["t"], 2)}</td>'
            p_cell = f'<td class="mono{sig_class(coefficient["p_value"])}">{pval(coefficient["p_value"])}</td>'
            low_cell = f'<td class="mono muted-num">{sci(coefficient["ci_low"])}</td>'
            high_cell = f'<td class="mono muted-num">{sci(coefficient["ci_high"])}</td>'
        else:
            coef_cell = se_cell = t_cell = p_cell = low_cell = high_cell = '<td class="mono dim">--</td>'

        mv_body.append(
            f"""<tr class="is-{status}">
 <td class="term coef-name">{esc(group['label'])}{label_note}</td>
 {coef_cell}{se_cell}{t_cell}{p_cell}{low_cell}{high_cell}
</tr>"""
        )
    order = row.get("selection_order") or []
    mv_note = (
        "Holiday_proximity and Days_of_the_week are compact display rows; the fitted model still "
        "uses every selected encoded term inside each group. Variables enter by forward "
        "selection - whole variables, in the order that maximises adjusted R&sup2;, stopping when "
        "nothing improves it"
        + (f": {' &rarr; '.join(esc(name) for name in order)}." if order else ".")
    )
    mv_table = f"""<div class="table-wrap">
<table class="grid coef-grid mv-grid">
 <thead><tr><th class="term">Variable</th><th>Coef</th><th>Std err</th>
 <th>t</th><th>P&gt;|t|</th><th>0.025</th><th>0.975</th></tr></thead>
 <tbody>{''.join(mv_body)}</tbody>
</table></div><p class="note mv-note">{mv_note}</p>"""

    # ---- correlation matrix. Match the Dataset page's compact declared-variable view:
    # multi-feature groups collapse to their strongest absolute underlying correlation.
    compact_correlation = declared_correlation(correlation)
    variables = compact_correlation.get("variables", [])
    matrix = compact_correlation.get("matrix", [])
    if len(variables) >= 2:
        head = "".join(
            f'<th class="col" scope="col" title="Declared variable #{v["number"]}">'
            f'<span>{esc(v["name"])}</span></th>'
            for v in variables
        )
        body = []
        for i, rv in enumerate(variables):
            cells = []
            for j, cv in enumerate(variables):
                value = matrix[i][j]
                text = f"{value:.2f}"
                cells.append(
                    f'<td{cell_style(value)} data-r="{esc(rv["name"])}" data-c="{esc(cv["name"])}" '
                    f'data-v="{value:.2f}">{text}</td>'
                )
            body.append(
                f'<tr><th class="row" scope="row" title="Declared variable #{rv["number"]}">{esc(rv["name"])}</th>'
                + "".join(cells)
                + "</tr>"
            )
        pairs = collinear_pairs(correlation)
        warn = ""
        if pairs:
            listed = "; ".join(f"{a} &harr; {b} at r = {v:.2f}" for a, b, v in pairs)
            warn = (
                f'<p class="warn"><strong>Collinear predictors.</strong> {listed}. '
                "The fit cannot assign both a unique standalone effect; redundant groups are omitted "
                "from the multivariate table.</p>"
            )
        matrix_block = f"""<div class="matrix-wrap">
<table class="matrix"><thead><tr><th class="corner"></th>{head}</tr></thead>
<tbody>{''.join(body)}</tbody></table></div>{warn}
<p class="note">Sample size {correlation.get('sample_size', 0)} days. Holiday proximity and day of week
use the strongest absolute correlation among their underlying indicators, with the sign retained.
Constant variable groups are omitted.</p>"""
    else:
        matrix_block = '<p class="empty">Only the lead series varies here, so there is no pair to correlate.</p>'

    # ---- declared coverage
    coverage = []
    for spec in row.get("declared_variables", []):
        status = spec["status"]
        name = COVERAGE_NAMES.get(spec["name"], spec["name"])
        # Three variables now carry the same string as name and detail; printing it twice
        # in one card reads as a rendering bug.
        detail = "" if spec["detail"] == name else spec["detail"]
        # The report is now grouped at the declared-variable level. Keep the coverage row, but
        # do not list underlying holiday buckets or weekdays in small print.
        if spec["number"] in (3, 8):
            detail = "" if status == "in_model" else detail
        coverage.append(
            f'<li class="cov is-{status}"><span class="cov-n mono">{spec["number"]}</span>'
            f'<span class="cov-name">{esc(name)}</span>'
            f'<span class="cov-d">{esc(detail)}</span></li>'
        )
    coverage_block = f'<ul class="coverage">{"".join(coverage)}</ul>' if coverage else ""

    sections.append(
        f"""<section class="adset" id="{slug(row['ad_set_id'])}">
 <header class="adset-head">
  <span class="rank-badge mono">{rank_label}</span>
  <div class="adset-title">
   <h2>{esc(short_name(row))}</h2>
   <span class="id mono">{esc(row['ad_set_id'])}</span>
  </div>
  <div class="chips">{''.join(chips)}</div>
 </header>
 {fit_block}
 <div class="pair">
  <div class="panel">
   <h3>Univariate <span class="h3-sub">leads ~ spent</span></h3>
   {uni_table}
  </div>
  <div class="panel multivariate-panel">
   <h3>Multivariate <span class="h3-sub">intercept + seven declared predictor groups</span></h3>
   {mv_table}
  </div>
 </div>
 <div class="panel matrix-panel">
  <h3>Correlation matrix</h3>
  {matrix_block}
 </div>
 <div class="panel">
  <h3>Declared variable coverage</h3>
  {coverage_block}
 </div>
 <a class="to-top" href="#board">Back to ranking</a>
</section>"""
    )

jump_options = "".join(
    f'<option value="#{slug(row["ad_set_id"])}">'
    f'{(f"{i:02d}" if row.get("multivariate") else "--")} &middot; {esc(short_name(row))}</option>'
    for i, row in enumerate(ordered, start=1)
)

page = f"""<!doctype html>
<html lang="en" data-theme="light">
<title>Ad Set Regression Report &middot; LeadLens</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@font-face {{ font-family:"Inter Tight"; font-style:normal; font-display:swap;
  font-weight:100 900; src:url({INTER}) format("woff2"); }}
@font-face {{ font-family:"JetBrains Mono"; font-style:normal; font-display:swap;
  font-weight:100 800; src:url({MONO}) format("woff2"); }}

:root {{
  color-scheme: light;
  --canvas:#F4F5F7; --surface:#FFFFFF; --surface-2:#F8F9FB; --tint:rgba(16,20,28,.04);
  --text:#14181F; --muted:#5A6270; --dim:#676F7C;
  --gold:#866B28; --gold-strong:#7C6021; --gold-dim:#B49A5E; --ink-on-gold:#FFFFFF;
  --cyan:#0E7894; --cyan-strong:#0A6076;
  --success:#17805A; --danger:#B3352C; --warn:#9A6B10;
  --line:rgba(16,20,28,.10); --line-accent:rgba(154,123,46,.34);
  --corr-cold:#386987; --corr-hot:#E2685C; --corr-zero:#F2F3F5; --corr-ink:#FFFFFF;
  --shadow:0 1px 2px rgba(16,20,28,.06), 0 8px 24px rgba(16,20,28,.06);
  --font:"Inter Tight", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono:"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --canvas:#0C0D0F; --surface:#16181C; --surface-2:#1D2025; --tint:rgba(255,255,255,.045);
    --text:#F2F1EE; --muted:#9BA0A8; --dim:#7C828C;
    --gold:#C9A86A; --gold-strong:#E0C285; --gold-dim:#8A7442; --ink-on-gold:#16130B;
    --cyan:#4FC3D9; --cyan-strong:#7FDCEC;
    --success:#5BC08C; --danger:#E2685C; --warn:#E0A93C;
    --line:rgba(255,255,255,.09); --line-accent:rgba(201,168,106,.34);
    --corr-cold:#4A90C9; --corr-hot:#E2685C; --corr-zero:#1A1D22; --corr-ink:#0C0D0F;
    --shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 10px 30px rgba(0,0,0,.45);
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --canvas:#0C0D0F; --surface:#16181C; --surface-2:#1D2025; --tint:rgba(255,255,255,.045);
  --text:#F2F1EE; --muted:#9BA0A8; --dim:#7C828C;
  --gold:#C9A86A; --gold-strong:#E0C285; --gold-dim:#8A7442; --ink-on-gold:#16130B;
  --cyan:#4FC3D9; --cyan-strong:#7FDCEC;
  --success:#5BC08C; --danger:#E2685C; --warn:#E0A93C;
  --line:rgba(255,255,255,.09); --line-accent:rgba(201,168,106,.34);
  --corr-cold:#4A90C9; --corr-hot:#E2685C; --corr-zero:#1A1D22; --corr-ink:#0C0D0F;
  --shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 10px 30px rgba(0,0,0,.45);
}}

* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--canvas); color:var(--text); font-family:var(--font);
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased; }}
.mono {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}
.dim {{ color:var(--dim); }}
a {{ color:var(--gold); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
:focus-visible {{ outline:2px solid var(--gold); outline-offset:2px; border-radius:4px; }}

.shell {{ max-width:1180px; margin:0 auto; padding:0 24px 96px; }}

/* ---------- topbar ---------- */
.topbar {{ position:sticky; top:0; z-index:20; background:color-mix(in srgb, var(--canvas) 88%, transparent);
  backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }}
.topbar-in {{ max-width:1180px; margin:0 auto; padding:10px 24px; display:flex; align-items:center;
  gap:16px; flex-wrap:wrap; }}
.mark {{ font-family:var(--mono); font-size:10px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--gold); }}
.topbar select {{ margin-left:auto; font-family:var(--mono); font-size:11px; color:var(--text);
  background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:5px 8px;
  max-width:min(340px, 60vw); }}

/* ---------- masthead ---------- */
.masthead {{ padding:64px 0 40px; border-bottom:1px solid var(--line); }}
.eyebrow {{ font-family:var(--mono); font-size:10px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--gold); margin:0 0 14px; }}
.masthead h1 {{ margin:0 0 16px; font-size:clamp(30px,4.2vw,50px); line-height:1.04;
  letter-spacing:-.032em; font-weight:640; text-wrap:balance; max-width:16ch; }}
.lede {{ margin:0; max-width:62ch; font-size:16px; color:var(--muted); }}
.facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
  margin-top:36px; background:var(--line); border:1px solid var(--line); border-radius:10px;
  overflow:hidden; }}
.fact {{ background:var(--surface); padding:14px 16px; }}
.fact-k {{ display:block; font-family:var(--mono); font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--dim); margin-bottom:6px; }}
.fact-v {{ font-family:var(--mono); font-size:20px; font-weight:600; letter-spacing:-.02em; }}
.fact-v small {{ font-size:12px; font-weight:400; color:var(--muted); }}

/* ---------- method ---------- */
.method {{ margin:40px 0 0; display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:20px; }}
.method div {{ padding:18px 20px; background:var(--surface); border:1px solid var(--line);
  border-radius:10px; box-shadow:var(--shadow); }}
.method h4 {{ margin:0 0 8px; font-size:12px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--gold); font-weight:600; }}
.method p {{ margin:0; font-size:13.5px; color:var(--muted); }}
.method p + p {{ margin-top:10px; }}

/* ---------- leaderboard ---------- */
h2.sec {{ margin:64px 0 6px; font-size:24px; letter-spacing:-.02em; font-weight:620; }}
.sec-sub {{ margin:0 0 20px; color:var(--muted); max-width:62ch; }}
.board-wrap, .table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px;
  background:var(--surface); box-shadow:var(--shadow); }}
table {{ border-collapse:collapse; width:100%; }}
thead th {{ position:sticky; top:0; background:var(--surface-2); font-family:var(--mono);
  font-size:9.5px; letter-spacing:.13em; text-transform:uppercase; color:var(--dim);
  font-weight:500; text-align:right; padding:10px 12px; white-space:nowrap;
  border-bottom:1px solid var(--line); }}
thead th.term, thead th.name, thead th.v {{ text-align:left; }}
tbody td {{ padding:9px 12px; text-align:right; border-bottom:1px solid var(--line);
  white-space:nowrap; }}
tbody tr:last-child td {{ border-bottom:0; }}
tbody tr:hover td {{ background:var(--tint); }}
td.term, td.name, td.uni {{ text-align:left; }}
.board td.rank {{ width:44px; color:var(--gold); font-weight:600; }}
/* A few legacy campaign names carry a long creative-code tail; let them wrap instead of
   stretching the table past its container and clipping the score columns. */
.board td.name {{ white-space:normal; max-width:300px; }}
.board td.name a {{ color:var(--text); font-weight:520; }}
.board td.name a:hover {{ color:var(--gold); }}
.board td.name .id {{ display:block; font-size:10px; color:var(--dim); letter-spacing:.01em; }}
.board td.uni .dim {{ font-size:11.5px; }}
.board td.adj {{ min-width:190px; }}
.bar {{ position:relative; display:block; height:5px; margin-top:5px; border-radius:3px;
  background:var(--tint); }}
.bar-zero {{ position:absolute; top:-2px; bottom:-2px; width:1px; background:var(--line-accent); }}
.bar-fill {{ position:absolute; top:0; bottom:0; border-radius:3px; }}
.bar-fill.is-pos {{ background:var(--cyan); }}
.bar-fill.is-neg {{ background:var(--danger); }}
.thin-note {{ font-family:var(--mono); font-size:10px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--warn); }}
.is-sig {{ color:var(--success); }}
.is-sig-strong {{ color:var(--success); font-weight:600; }}

/* ---------- ad set section ---------- */
.adset {{ margin-top:56px; padding-top:28px; border-top:1px solid var(--line); scroll-margin-top:64px; }}
.adset-head {{ display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; margin-bottom:18px; }}
.rank-badge {{ font-size:26px; font-weight:700; color:var(--gold); letter-spacing:-.03em; }}
.unranked {{ font-size:9.5px; font-weight:500; letter-spacing:.13em; text-transform:uppercase;
  color:var(--dim); }}
.adset-title h2 {{ margin:0; font-size:21px; letter-spacing:-.02em; font-weight:600; }}
.adset-title .id {{ font-size:11px; color:var(--dim); }}
.chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-left:auto; }}
.chip {{ display:inline-flex; gap:6px; align-items:baseline; padding:4px 9px; border-radius:999px;
  border:1px solid var(--line); background:var(--surface); font-size:11px; }}
.chip-k {{ font-family:var(--mono); font-size:9px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--dim); }}
.chip-v {{ font-size:11.5px; }}
.chip.is-warn {{ border-color:var(--warn); color:var(--warn); }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(112px,1fr)); gap:1px;
  background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden;
  margin-bottom:20px; }}
.stat {{ background:var(--surface); padding:11px 13px; }}
.stat.is-lead {{ background:var(--surface-2); box-shadow:inset 2px 0 0 var(--gold); }}
.stat-k {{ display:block; font-family:var(--mono); font-size:9px; letter-spacing:.12em;
  text-transform:uppercase; color:var(--dim); margin-bottom:4px; }}
.stat-v {{ font-size:15px; font-weight:600; letter-spacing:-.01em; }}

.no-fit {{ display:flex; gap:14px; align-items:flex-start; padding:14px 16px; margin-bottom:20px;
  border:1px solid var(--warn); border-radius:10px; background:color-mix(in srgb, var(--warn) 8%, var(--surface)); }}
.no-fit-tag {{ font-family:var(--mono); font-size:9.5px; letter-spacing:.13em; text-transform:uppercase;
  color:var(--warn); white-space:nowrap; padding-top:3px; }}
.no-fit p {{ margin:0; color:var(--muted); }}

/* Both regression tables run full width. Side by side, the R2 column and the confidence
   interval -- the two things the ranking is actually built on -- fell off the right edge. */
.pair {{ display:grid; grid-template-columns:1fr; gap:22px; }}
.pair > .panel {{ min-width:0; }}
.panel {{ margin-top:22px; min-width:0; }}
.pair .panel {{ margin-top:0; }}
.panel h3 {{ margin:0 0 10px; font-size:12px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--text); font-weight:600; }}
.h3-sub {{ margin-left:8px; font-family:var(--font); font-size:12px; letter-spacing:0;
  text-transform:none; color:var(--dim); font-weight:400; }}
td.v {{ color:var(--gold); width:28px; }}
td.r2 {{ min-width:118px; }}
.minibar {{ display:block; height:4px; margin-top:4px; border-radius:2px; background:var(--tint); }}
.minibar span {{ display:block; height:100%; border-radius:2px; background:var(--cyan); }}
.empty {{ margin:0; padding:14px 16px; border:1px dashed var(--line); border-radius:10px;
  color:var(--dim); }}
.multivariate-panel {{ break-inside:avoid-page; page-break-inside:avoid; }}
.multivariate-panel h3 {{ break-after:avoid-page; page-break-after:avoid; }}
.coef-grid {{ table-layout:fixed; }}
.coef-grid th:first-child {{ width:34%; }}
.coef-grid td {{ padding-top:10px; padding-bottom:10px; }}
.coef-grid tbody tr:nth-child(2n) td {{ background:var(--surface-2); }}
.coef-grid tbody tr:hover td {{ background:color-mix(in srgb, var(--gold) 7%, var(--surface)); }}
.coef-name {{ font-weight:520; white-space:normal; overflow-wrap:anywhere; }}
.coef-sub {{ display:block; margin-top:2px; font-family:var(--mono); font-size:10px; line-height:1.25;
  color:var(--dim); font-weight:400; }}
.muted-num {{ color:#697386; }}
.mv-grid tr.is-constant .coef-name, .mv-grid tr.is-no_data .coef-name,
.mv-grid tr.is-not_fitted .coef-name, .mv-grid tr.is-aliased .coef-name,
.mv-grid tr.is-collinear .coef-name, .mv-grid tr.is-not_selected .coef-name {{ color:var(--muted); }}
.mv-note {{ max-width:96ch; }}
@media print {{
  /* Chromium may fragment a break-avoiding grid item. Switch the single-column pair back to
     normal flow and give every compact multivariate panel a clean page start. */
  .pair {{ display:block; }}
  .pair > .panel + .panel {{ margin-top:22px; }}
  .multivariate-panel {{ break-before:page; page-break-before:always;
    break-inside:avoid-page; page-break-inside:avoid; }}
  .multivariate-panel .table-wrap, .multivariate-panel .mv-note {{
    break-inside:avoid-page; page-break-inside:avoid;
  }}
}}

/* ---------- matrix ---------- */
.matrix-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px;
  background:var(--surface); box-shadow:var(--shadow); padding:0; }}
.matrix {{ table-layout:fixed; border-collapse:separate; border-spacing:0; width:100%;
  min-width:880px; margin:0; font-variant-numeric:tabular-nums; }}
.matrix th, .matrix td {{ border-right:1px solid var(--line); border-bottom:1px solid var(--line); }}
.matrix tr > :last-child {{ border-right:0; }}
.matrix tbody tr:last-child > * {{ border-bottom:0; }}
.matrix th.col {{ position:static; height:auto; padding:10px 8px; text-align:center;
  vertical-align:middle; background:var(--surface-2); color:var(--text); }}
.matrix th.col span {{ display:block; font-family:var(--font); font-size:11px; font-weight:650;
  line-height:1.2; letter-spacing:0; text-transform:none; white-space:normal; }}
.matrix th.row {{ width:138px; padding:9px 10px; text-align:left; font-family:var(--font);
  font-size:11px; line-height:1.18; font-weight:650; color:var(--text); white-space:normal;
  background:var(--surface-2); letter-spacing:0; }}
.matrix th.corner {{ width:138px; background:var(--surface-2); }}
.matrix td {{ height:35px; padding:7px 5px; text-align:center; font-size:10.5px;
  line-height:1; color:var(--text); font-weight:550; }}
.matrix td.is-strong {{ color:var(--corr-ink); font-weight:700; }}
.matrix tbody tr:hover td {{ filter:saturate(1.08) brightness(.98); }}
.matrix-panel {{ break-inside:avoid-page; page-break-inside:avoid; }}
.matrix-panel h3 {{ break-after:avoid-page; page-break-after:avoid; }}
.warn {{ margin:12px 0 0; padding:12px 14px; border-left:2px solid var(--warn);
  background:color-mix(in srgb, var(--warn) 7%, var(--surface)); color:var(--muted);
  border-radius:0 8px 8px 0; }}
.warn strong {{ color:var(--text); }}
.note {{ margin:10px 0 0; font-size:12.5px; color:var(--dim); max-width:70ch; }}

/* ---------- coverage ---------- */
.coverage {{ list-style:none; margin:0; padding:0; display:grid;
  grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:1px; background:var(--line);
  border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
.cov {{ background:var(--surface); padding:10px 13px; display:flex; gap:9px; align-items:baseline; }}
.cov-n {{ font-size:10px; color:var(--dim); }}
.cov-name {{ font-size:12.5px; font-weight:520; }}
.cov-d {{ font-size:11.5px; color:var(--dim); margin-left:auto; text-align:right; }}
.cov.is-in_model .cov-name {{ color:var(--cyan); }}
.cov.is-target .cov-name {{ color:var(--gold); }}
.cov.is-flat .cov-name, .cov.is-missing .cov-name {{ color:var(--muted); }}
.cov.is-not_selected .cov-name {{ color:var(--dim); }}

.to-top {{ display:inline-block; margin-top:20px; font-family:var(--mono); font-size:10px;
  letter-spacing:.13em; text-transform:uppercase; color:var(--dim); }}
.to-top:hover {{ color:var(--gold); }}

.tip {{ position:fixed; z-index:50; pointer-events:none; opacity:0; transform:translateY(3px);
  transition:opacity .12s ease; background:var(--surface-2); color:var(--text);
  border:1px solid var(--line-accent); border-radius:7px; padding:7px 10px; font-size:11.5px;
  box-shadow:var(--shadow); max-width:250px; }}
.tip.on {{ opacity:1; transform:translateY(0); }}
.tip b {{ font-family:var(--mono); font-weight:600; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}

footer {{ margin-top:72px; padding-top:24px; border-top:1px solid var(--line); color:var(--dim);
  font-size:12.5px; }}
</style>

<div class="topbar"><div class="topbar-in">
 <span class="mark">LeadLens &middot; Regression Report</span>
 <select id="jump" aria-label="Jump to an ad set">
  <option value="">Jump to ad set...</option>{jump_options}
 </select>
</div></div>

<div class="shell">
<header class="masthead">
 <p class="eyebrow">{esc(window_start)} &rarr; {esc(window_end)} &middot; {len(data)} ad sets</p>
 <h1>How much of each ad set's daily leads the declared model explains</h1>
 <p class="lede">Every ad set gets two fits against its own daily CRM lead count: one simple regression
 per declared predictor, and one multivariate regression carrying all of them together. Ad sets are
 ranked by multivariate adjusted R&sup2;, which charges the model for each term it spends.</p>
 <div class="facts">
  <div class="fact"><span class="fact-k">Ad sets</span><span class="fact-v">{len(data)}</span></div>
  <div class="fact"><span class="fact-k">Fitted</span><span class="fact-v">{len(fitted)}<small> / {len(data)}</small></span></div>
  <div class="fact"><span class="fact-k">Leads</span><span class="fact-v">{total_leads:,.0f}</span></div>
  <div class="fact"><span class="fact-k">Spend</span><span class="fact-v">{money(total_spend)}</span></div>
  <div class="fact"><span class="fact-k">Median adj. R&sup2;</span><span class="fact-v">{num(median_adj)}</span></div>
 </div>
</header>

<div class="method">
 <div>
  <h4>What the two fits are</h4>
  <p>The univariate table shows only the spend-only fit: daily CRM leads against daily spend.
  The multivariate table runs a single regression carrying the declared predictors that forward
  selection kept: starting from an intercept-only model, whole variables enter one at a time,
  each round taking whichever candidate raises adjusted R&sup2; most, until none of the remaining
  ones raises it at all.</p>
  <p>Both use the same feature rows the app builds for its own OLS panels, at ad-set scope, so
  these numbers match what the Dataset and Forecast pages report.</p>
 </div>
 <div>
  <h4>Reading the coefficient table</h4>
  <p>The multivariate table has the same eight rows for every ad set: intercept, then the seven
  declared predictor groups. Holiday_proximity and Days_of_the_week use the strongest encoded
  fitted term as their displayed row, while the regression itself keeps all selected encoded
  terms inside those groups. A row marked <em>not selected</em> carries the adjusted-R&sup2;
  change it would have caused, so a variable never disappears without its margin.</p>
 </div>
 <div>
 <h4>What the ranking measured</h4>
  <p>Spend clears p &lt; 0.05 on its own in {spend_sig} of the {spend_present} ad sets where spend moves at all.
  {len(unfitted)} ad sets carry too few days to fit and appear at the bottom with their correlation
  matrices only.</p>
 </div>
</div>

<h2 class="sec" id="board">Ranking</h2>
<p class="sec-sub">Sorted by multivariate adjusted R&sup2;, high to low. A tick marks zero on each bar.
Bars that run left of the tick belong to ad sets where the declared model does worse than
predicting the mean.</p>
<div class="board-wrap">
<table class="board">
 <thead><tr>
  <th class="v">#</th><th class="name">Ad set</th><th>Days</th><th>Leads</th><th>Spend</th>
  <th class="name">Spend-only R&sup2;</th><th>R&sup2;</th><th class="name">Adj. R&sup2;</th><th>Prob (F)</th>
 </tr></thead>
 <tbody>{''.join(board_rows)}</tbody>
</table>
</div>

{''.join(sections)}

<footer>
 <p>Generated from <span class="mono">leadlens.db</span> using the app's own fitting helpers
 (<span class="mono">_load_scope_feature_rows</span>, <span class="mono">_fit_ols_summary</span>,
 <span class="mono">get_dataset_correlation</span>) at ad-set scope. Window
 {esc(window_start)} to {esc(window_end)}.</p>
</footer>
</div>

<div class="tip" id="tip" role="status"></div>
<script>
document.getElementById("jump").addEventListener("change", function (event) {{
  if (event.target.value) {{ location.hash = event.target.value; event.target.value = ""; }}
}});

var tip = document.getElementById("tip");
document.addEventListener("mouseover", function (event) {{
  var cell = event.target.closest(".matrix td[data-v]");
  if (!cell) {{ tip.classList.remove("on"); return; }}
  tip.innerHTML = cell.dataset.r + " &times; " + cell.dataset.c + "<br><b>r = " + cell.dataset.v + "</b>";
  tip.classList.add("on");
}});
document.addEventListener("mousemove", function (event) {{
  if (!tip.classList.contains("on")) return;
  var x = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 10);
  var y = Math.max(event.clientY - tip.offsetHeight - 12, 8);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}});
</script>
</html>
"""


OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page, encoding="utf-8")
print(f"{OUT} -- {OUT.stat().st_size / 1024:.0f} KB, {len(fitted)} fitted / {len(data)} ad sets")
