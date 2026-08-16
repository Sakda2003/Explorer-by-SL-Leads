"""Does forward selection actually forecast better, or only fit better?

Phase 3 gate. The Multivariate OLS *card* has used forward selection since 2026-08-13; the
forecast path still fits every declared variable that varies. Adjusted R2 is an in-sample
criterion and this project has already measured that maximising in-sample fit does not improve
forecasts here (Vault/Modeling/Forecast-Flatness-Is-The-Data.md), so the switch has to be
justified on held-out WAPE or not made at all.

Four configurations, each run through the real per-ad-set pipeline (`_forecast_for_series`, so
the same rolling-origin cutoffs, the same shape/calibration post-processing, the same model
competition) rather than a hand-rolled loop that might disagree with production:

    A  all-declared + ridge      current behaviour
    B  forward-selected + ridge  the candidate switch (ridge still scales per feature, so a
                                 smaller set is also a smaller penalty)
    C  forward-selected, no ridge
    D  all-declared, no ridge    control: separates "selection helped" from "less penalty helped"

Two questions, both answered per run:

  1. Direct  -- the multivariate OLS candidate's own rolling-origin WAPE.
  2. Downstream -- what each ad set's *selected* production model scores, since changing one
     candidate changes which candidate wins.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import core  # noqa: E402

MULTIVARIATE = core.OLS_MULTIVARIATE_MODEL_NAME
BASELINE_RIDGE = core.MULTIVARIATE_RIDGE_PER_FEATURE
ALL_DECLARED = core._select_multivariate_ols_features


def forward_selector(values: np.ndarray, feature_rows: list[dict]) -> list[str]:
    """Same selector the OLS card uses. Sees only the training window at each cutoff, so
    running it inside the backtest is not leakage -- selection is refitted per origin."""
    return core._forward_select_declared_features(values, feature_rows)["features"]


# Toggled through the real switch (`OLS_FORECAST_USES_FORWARD_SELECTION`) rather than by
# monkeypatching the selector, so what is measured is exactly the code that ships -- including
# the empty-selection fallback to spend, which a patched selector would bypass.
CONFIGS = [
    ("A all-declared + ridge", False, BASELINE_RIDGE),
    ("B forward + ridge", True, BASELINE_RIDGE),
    ("C forward, no ridge", True, 0.0),
    ("D all-declared, no ridge", False, 0.0),
]


def pooled_wape(rows: list[dict]) -> float | None:
    """Lead-weighted WAPE across ad sets: total absolute error over total actual leads.

    Weighted, not averaged: a 300%-WAPE ad set with four leads in it must not outvote the ad
    set carrying a fifth of the portfolio. The median across ad sets is reported alongside,
    since the two disagreeing is itself a finding.
    """
    error = sum(row["abs_error"] for row in rows)
    actual = sum(row["actual"] for row in rows)
    return float(error / actual) if actual > 0 else None


def backtested_actual_total(values: np.ndarray, horizon: int) -> float:
    """Total actual leads across the evaluated windows, mirroring _rolling_origin_backtest's
    cutoff schedule exactly. Recomputed here rather than derived from the metric dict: WAPE
    floors its denominator at 1.0, so inverting it would quietly misstate a near-dead ad set."""
    final_cutoff = len(values) - horizon
    cutoffs = list(range(14, final_cutoff + 1, 7)) if final_cutoff >= 14 else []
    if cutoffs and cutoffs[-1] != final_cutoff:
        cutoffs.append(final_cutoff)
    return float(sum(float(np.sum(values[cutoff:cutoff + horizon])) for cutoff in cutoffs))


def run_config(use_forward: bool, ridge: float, frames) -> dict:
    all_frame, spend_frame, ad_sets = frames
    selector = forward_selector if use_forward else ALL_DECLARED
    core.OLS_FORECAST_USES_FORWARD_SELECTION = use_forward
    core.MULTIVARIATE_RIDGE_PER_FEATURE = ridge
    direct: dict[int, list[dict]] = {7: [], 14: []}
    selected_rows: dict[int, list[dict]] = {7: [], 14: []}
    winners: dict[str, int] = {}
    term_counts: list[int] = []
    for ad_set, frame, campaign in ad_sets:
        forecast = core._forecast_for_series(frame, all_frame, ad_set, campaign, spend_frame)
        # Same series _forecast_for_series builds internally, for the actual-lead denominator.
        dates = pd.date_range(frame["aggregate_date"].min(), all_frame["aggregate_date"].max(), freq="D")
        values = frame.set_index("aggregate_date")["lead_count"].reindex(dates, fill_value=0).astype(float).to_numpy()
        by_model = {(m["model_used"], m["horizon_days"]): m for m in forecast["metrics"]}
        for horizon in (7, 14):
            actual_total = backtested_actual_total(values, horizon)

            def record(bucket: list[dict], metric: dict | None, model: str) -> None:
                if not metric or metric.get("wape") is None or metric.get("mae") is None:
                    return
                bucket.append({
                    "ad_set": ad_set, "model": model, "wape": float(metric["wape"]),
                    "abs_error": float(metric["mae"]) * int(metric["backtest_windows"]) * horizon,
                    "actual": actual_total, "r2": metric.get("r2_out_of_sample"),
                })

            record(direct[horizon], by_model.get((MULTIVARIATE, horizon)), MULTIVARIATE)
            chosen = next((h for h in forecast["horizons"] if h["horizon"] == horizon), None)
            if chosen:
                record(selected_rows[horizon], by_model.get((chosen["model"], horizon)), chosen["model"])
                if horizon == 14:
                    winners[chosen["model"]] = winners.get(chosen["model"], 0) + 1
        scope_values, feature_rows, _ = core._load_scope_feature_rows(ad_set_id=ad_set)
        if scope_values is not None and feature_rows is not None:
            term_counts.append(len(selector(scope_values, feature_rows)))
    return {
        "direct": {
            horizon: {
                "pooled_wape": pooled_wape(rows),
                "median_wape": float(np.median([r["wape"] for r in rows])) if rows else None,
                "ad_sets": len(rows),
            } for horizon, rows in direct.items()
        },
        "selected": {
            horizon: {
                "pooled_wape": pooled_wape(rows),
                "median_wape": float(np.median([r["wape"] for r in rows])) if rows else None,
                "ad_sets": len(rows),
            } for horizon, rows in selected_rows.items()
        },
        "multivariate_wins_14d": winners.get(MULTIVARIATE, 0),
        "winner_counts_14d": dict(sorted(winners.items(), key=lambda kv: -kv[1])),
        "mean_terms": float(np.mean(term_counts)) if term_counts else None,
        "ad_sets_with_no_terms": int(sum(1 for count in term_counts if count == 0)),
        # Full rows, not just WAPE: the configs do not score the same population (an empty
        # selected set means the multivariate candidate refuses to fit at all and the ad set
        # drops out), so any honest pooled comparison has to be recomputed on the intersection.
        "direct_rows_14": direct[14],
        "direct_rows_7": direct[7],
    }


def main() -> None:
    with core.connect() as db:
        rows = db.execute("SELECT * FROM daily_ad_set_aggregates ORDER BY aggregate_date").fetchall()
        spend_frame = core._load_spend_frame(db)
    all_frame = pd.DataFrame([dict(row) for row in rows])
    all_frame["aggregate_date"] = pd.to_datetime(all_frame["aggregate_date"])
    ad_sets = []
    for ad_set, frame in all_frame.groupby("utm_ad_set_id"):
        mode = frame["utm_campaign_id"].replace("", np.nan).dropna().mode()
        ad_sets.append((str(ad_set), frame, str(mode.iloc[0]) if len(mode) else ""))
    frames = (all_frame, spend_frame, ad_sets)
    print(f"{len(ad_sets)} ad sets, {len(all_frame)} aggregate rows\n")

    baseline_switch = core.OLS_FORECAST_USES_FORWARD_SELECTION
    results = {}
    try:
        for label, use_forward, ridge in CONFIGS:
            started = time.time()
            results[label] = run_config(use_forward, ridge, frames)
            print(f"{label:28s} done in {time.time() - started:5.1f}s")
    finally:
        core.OLS_FORECAST_USES_FORWARD_SELECTION = baseline_switch
        core.MULTIVARIATE_RIDGE_PER_FEATURE = BASELINE_RIDGE

    def cell(value) -> str:
        return "-" if value is None else f"{value * 100:6.1f}%"

    print("\nMultivariate OLS candidate, rolling-origin WAPE (lower is better)")
    print(f"{'config':28s} {'7d pooled':>10s} {'7d median':>10s} {'14d pooled':>11s} {'14d median':>11s} {'terms':>6s}")
    for label in results:
        r = results[label]
        print(f"{label:28s} {cell(r['direct'][7]['pooled_wape']):>10s} {cell(r['direct'][7]['median_wape']):>10s} "
              f"{cell(r['direct'][14]['pooled_wape']):>11s} {cell(r['direct'][14]['median_wape']):>11s} "
              f"{(r['mean_terms'] or 0):6.1f}")

    # Like-for-like: the configs disagree about which ad sets the multivariate model can be
    # fitted on at all, and an ad set that drops out of one column takes its errors with it.
    scored = [set(row["ad_set"] for row in results[label]["direct_rows_14"]) for label in results]
    common = set.intersection(*scored) if scored else set()
    print(f"\nSame {len(common)} ad sets in every column (14d), pooled WAPE")
    print(f"{'config':28s} {'14d pooled':>11s} {'14d median':>11s} {'scored':>7s} {'dropped out':>12s}")
    for label in results:
        rows = [row for row in results[label]["direct_rows_14"] if row["ad_set"] in common]
        allrows = results[label]["direct_rows_14"]
        print(f"{label:28s} {cell(pooled_wape(rows)):>11s} "
              f"{cell(float(np.median([r['wape'] for r in rows])) if rows else None):>11s} "
              f"{len(allrows):>7d} {len(ad_sets) - len(allrows):>12d}")

    print("\nProduction model actually selected per ad set (the number that ships)")
    print(f"{'config':28s} {'7d pooled':>10s} {'14d pooled':>11s} {'14d median':>11s} {'OLS-mv wins':>12s}")
    for label in results:
        r = results[label]
        print(f"{label:28s} {cell(r['selected'][7]['pooled_wape']):>10s} {cell(r['selected'][14]['pooled_wape']):>11s} "
              f"{cell(r['selected'][14]['median_wape']):>11s} {r['multivariate_wins_14d']:>12d}")

    out = Path(__file__).resolve().parent / "output" / "forward_selection_backtest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
