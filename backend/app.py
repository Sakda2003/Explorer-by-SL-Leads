from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
from datetime import datetime
from statistics import median
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from . import security

from .core import (
                   ROOT, bulk_delete_lead_events, change_event_coverage, connect, create_lead_event, delete_ad_performance_row, delete_ad_set_start_date, delete_budget_period, delete_change_event, delete_lead_event, delete_newer_duplicate_leads, delete_upload, get_ad_decisions, get_ad_spend_analytics, get_budget_optimization, get_dashboard_insights, get_dataset_correlation, get_dataset_overview, get_dataset_row_ids, get_dataset_rows, get_duplicate_leads, get_forecast_realizations,
                   get_forecast_scenario,
                   get_model_diagnostics, get_ols_model_summaries, get_portfolio_forecast_tracking, get_weekday_profile, import_preview, init_db,
                   bulk_update_lead_quality, get_lead_filter_options, get_lead_pipeline_summary,
                   get_followup_lead, get_followup_leads, save_followup,
                   list_ad_set_start_dates, list_budget_periods, list_change_events, preview_file, rebuild_aggregates, save_ad_set_start_date, save_budget_period, save_change_event, train_models, update_ad_performance_row, update_lead_event)

# Refuse to boot open on a deployment that declares itself public (Render, or an explicit
# LEADLENS_REQUIRE_AUTH). This runs before anything else so a misconfigured public deploy fails
# loudly at startup instead of silently serving every route -- including the DELETE routes.
auth.require_gate_or_die()

# Hide the interactive API surface (/docs, /redoc, /openapi.json) whenever a gate is configured:
# it enumerates every route, including the DELETE ones, and there is no reason to publish that on
# a real deployment. Left on when inert, so it stays available for local development.
_docs_enabled = not auth.config.mode
app = FastAPI(
    title="LeadLens Forecasting",
    version="1.0.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

auth.log_startup_state()

# Resolve the Content-Security-Policy once, hashing the served shell's inline theme script so a
# strict script-src can allow it without opening 'unsafe-inline'. See backend/security.py.
security.configure_csp(ROOT / "frontend" / "dist" / "index.html")


# Runs ahead of every route and the static mount, so an unauthenticated request cannot reach
# the dashboard shell either. See backend/auth.py (who may call) and backend/security.py (how
# hard they may lean on it: body-size cap, rate limits, brute-force throttle, response headers).
@app.middleware("http")
async def require_access(request: Request, call_next):
    ip = security.client_ip(request)
    is_https = security.is_https_request(request)
    exempt = auth.is_exempt_request(request)

    def _sealed(response):
        security.apply_headers(response, is_https)
        return response

    # Reject oversized bodies from the declared length before reading anything into memory.
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > security.MAX_BODY_BYTES:
        return _sealed(JSONResponse({"detail": "Request body too large."}, status_code=413))

    if exempt:
        return _sealed(await call_next(request))

    # Both count as "a human is proving who they are", which is what clears the lockout.
    is_login_check = request.url.path in ("/api/auth/me", "/api/auth/login")

    # A caller who has burned through the failed-sign-in budget is refused before re-checking the
    # credential. The login check itself is allowed through so a real user with the correct
    # password can recover immediately instead of waiting for the whole window to expire.
    if security.auth_blocked(ip):
        if is_login_check:
            ok, status, message, headers = await auth.verify(request)
            if ok:
                security.clear_auth_failures(ip)
                return _sealed(await call_next(request))
            if status in (401, 403):
                security.record_auth_failure(ip)
            return _sealed(JSONResponse(
                {"detail": "Too many failed sign-in attempts. Try again later."},
                status_code=429, headers={"Retry-After": str(security.AUTH_FAIL_WINDOW)},
            ))
        return _sealed(JSONResponse(
            {"detail": "Too many failed sign-in attempts. Try again later."},
            status_code=429, headers={"Retry-After": str(security.AUTH_FAIL_WINDOW)},
        ))

    if not security.allow_general(ip):
        return _sealed(JSONResponse(
            {"detail": "Too many requests."},
            status_code=429, headers={"Retry-After": str(security.GENERAL_WINDOW)},
        ))

    ok, status, message, headers = await auth.verify(request)
    if not ok:
        if status in (401, 403):
            security.record_auth_failure(ip)
        return _sealed(JSONResponse({"detail": message}, status_code=status, headers=headers or None))

    if is_login_check:
        security.clear_auth_failures(ip)

    # Retrain is CPU-bound (~29s) and holds the GIL; throttle it independently of the general cap.
    if request.url.path == "/api/models/retrain" and request.method == "POST":
        if not security.allow_retrain(ip):
            return _sealed(JSONResponse(
                {"detail": "Retrain was requested too frequently. Try again shortly."},
                status_code=429, headers={"Retry-After": str(security.RETRAIN_WINDOW)},
            ))

    return _sealed(await call_next(request))


# In production the frontend is served from this same origin, so no CORS is needed at all --
# hence the empty default once LEADLENS_CORS_ORIGINS is set to "". The localhost fallback
# exists only for `pnpm dev` on port 5173.
_cors_origins = [
    origin.strip()
    for origin in os.getenv("LEADLENS_CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"])


class ConfirmUpload(BaseModel):
    token: str
    file_name: str | None = None


# The Lead Management board's bulk bar: one pipeline stage applied to a whole selection.
# A body (not query params) because the id list is unbounded in principle -- a "select all
# matching" of a few thousand leads would blow past URL length limits as a query string.
class LeadQualityBulkUpdate(BaseModel):
    lead_ids: list[int]
    lead_quality: str


class LeadBulkDelete(BaseModel):
    lead_ids: list[int]


class LeadUpdate(BaseModel):
    status: str | None = None
    lead_quality: str | None = None
    created_at: str | None = None
    customer_name: str | None = None
    utm_campaign: str | None = None
    utm_campaign_id: str | None = None
    utm_ad_set_id: str | None = None
    utm_ad_id: str | None = None
    fb_ad_title: str | None = None
    amount_spent_usd: float | None = None


class LeadCreate(BaseModel):
    status: str
    lead_quality: str
    created_at: str
    customer_name: str
    utm_campaign: str
    utm_campaign_id: str
    utm_ad_set_id: str
    utm_ad_id: str
    fb_ad_title: str
    amount_spent_usd: float | None = None
    platform: str | None = "manual"


class FollowupUpdate(BaseModel):
    outcome: str
    note: str | None = None
    next_follow_up_at: str | None = None
    last_contacted_at: str | None = None
    contact_method: str | None = None
    assigned_to: str | None = None
    lost_reason: str | None = None
    required_documents: str | None = None
    expected_payment_date: str | None = None
    converted_at: str | None = None
    selected_service: str | None = None
    payment_status: str | None = None
    conversion_remarks: str | None = None


# Mirrors AD_PERFORMANCE_UPDATE_FIELDS in core.py. `leads` and
# `cost_per_messaging_conversation_started` are deliberately absent -- see the allowlist's
# comment there for why writing them would be invisible to the user.
class AdPerformanceUpdate(BaseModel):
    day: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    ad_set_id: str | None = None
    delivery_status: str | None = None
    amount_spent_usd: float | None = None
    cost_per_lead: float | None = None
    reach: float | None = None
    impressions: float | None = None
    frequency: float | None = None
    messaging_conversations_started: float | None = None
    ad_set_budget: float | None = None
    ad_set_budget_type: str | None = None


class BudgetPeriod(BaseModel):
    ad_set_id: str
    start_date: str
    end_date: str
    daily_budget: float
    id: int | None = None


class ChangeEvent(BaseModel):
    scope: str
    ad_set_id: str
    start_date: str
    # A change is a point event, so only `start_date` is read. Kept optional so a client
    # still sending the old range form is accepted rather than 422'd; the value is ignored.
    end_date: str | None = None
    ad_id: str | None = None
    notes: str | None = None
    confirmed_by: str | None = None
    id: int | None = None


class AdSetStartDate(BaseModel):
    ad_set_id: str
    start_date: str
    confirmed_by: str | None = None
    notes: str | None = None


class AdminUserPayload(BaseModel):
    email: str
    full_name: str | None = ""
    role: str = "staff"
    status: str = "active"
    password: str | None = None


# Excel, LibreOffice and Sheets treat a leading =, +, -, @ (or a leading tab/CR, which they
# strip before deciding) as the start of a formula, not text. Every CSV this app exports is
# built from values it did not author -- campaign and ad-set names come straight out of a Meta
# workbook, full_name comes from the admin screen -- so a cell reaching the sheet as
# `=HYPERLINK(...)` or a DDE payload is a live code path on the machine of whoever opens the
# download. Prefixing an apostrophe is the standard neutraliser: the spreadsheet shows the
# original text and refuses to evaluate it.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", chr(9), chr(13))


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in one exported cell."""
    if not isinstance(value, str):
        return value
    if value.startswith(_CSV_FORMULA_LEAD):
        return "'" + value
    return value


def _write_csv_row(writer, values) -> None:
    """csv.writer.writerow, with every string cell run through _csv_safe first."""
    writer.writerow([_csv_safe(value) for value in values])


def _current_user(request: Request) -> str:
    return str(getattr(request.state, "user_email", "") or "")


def _require_admin(request: Request) -> None:
    # Local development can run with no access gate; keep the admin screen usable there.
    if not auth.config.mode:
        return
    if getattr(request.state, "user_role", "") != "admin":
        raise HTTPException(403, "Admin access is required.")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status():
    return {"required": bool(auth.config.mode), "mode": auth.config.mode or "open"}


@app.get("/api/auth/me")
def auth_me(request: Request):
    return {
        "user": getattr(request.state, "user_email", ""),
        "role": getattr(request.state, "user_role", ""),
        "name": getattr(request.state, "user_name", ""),
    }


# Exchange a credential for a session. The middleware has already authenticated this request
# (it reaches the route only if `auth.verify` passed), so this endpoint's job is just to mint
# the token -- it never sees or re-checks a password itself. The raw token is returned exactly
# once and only its SHA-256 is stored, so it cannot be recovered from the database afterwards.
@app.post("/api/auth/login")
def auth_login(request: Request):
    email = _current_user(request)
    if not email:
        raise HTTPException(401, "Not signed in.")
    try:
        session = auth.create_session(email)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "token": session["token"],
        "expires_at": session["expires_at"],
        "user": email,
        "role": getattr(request.state, "user_role", ""),
        "name": getattr(request.state, "user_name", ""),
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    # Signing out has to destroy the session server-side. Clearing localStorage alone would
    # leave a token that still works for anyone who captured it.
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        auth.revoke_session(header[7:].strip())
    return {"signed_out": True}


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request)
    return {"users": auth.list_users(), "activity": auth.list_user_audit(25)}


@app.post("/api/admin/users")
def admin_create_user(payload: AdminUserPayload, request: Request):
    _require_admin(request)
    try:
        return auth.save_user(payload.dict(exclude_unset=True), _current_user(request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, payload: AdminUserPayload, request: Request):
    _require_admin(request)
    try:
        return auth.save_user(payload.dict(exclude_unset=True), _current_user(request), user_id=user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request):
    _require_admin(request)
    try:
        return auth.delete_user(user_id, _current_user(request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/admin/activity")
def admin_activity(request: Request, limit: int = Query(80, ge=1, le=250)):
    _require_admin(request)
    return {"activity": auth.list_user_audit(limit)}


@app.get("/api/admin/users.csv")
def admin_export_users(request: Request):
    _require_admin(request)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Email", "Full Name", "Role", "Status", "Created", "Last Login", "Password Set"])
    for user in auth.list_users():
        _write_csv_row(writer, [
            user["email"], user["full_name"], user["role"], user["status"],
            user["created_at"], user["last_login_at"], "yes" if user["has_password"] else "no",
        ])
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=admin-users.csv"})


def _reject_in_demo() -> None:
    # The public demo carries no real customer data by design; disabling imports there makes
    # that a technical control rather than a promise. Set LEADLENS_DEMO_MODE=1 on that deploy.
    if security.DEMO_MODE:
        raise HTTPException(403, "Data import is disabled on the demo deployment.")


@app.post("/api/uploads/preview")
async def upload_preview(file: UploadFile = File(...)):
    _reject_in_demo()
    # Read at most MAX_UPLOAD_BYTES+1 so a file larger than the cap is rejected without ever
    # buffering the whole thing -- the one worker on Render free has ~512 MB to lose.
    content = await file.read(security.MAX_UPLOAD_BYTES + 1)
    if len(content) > security.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {security.MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
    try:
        return preview_file(content, file.filename or "upload.csv")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/uploads/confirm")
def upload_confirm(payload: ConfirmUpload):
    _reject_in_demo()
    try:
        return import_preview(payload.token, payload.file_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/uploads")
def uploads():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM raw_uploads ORDER BY id DESC").fetchall()]


@app.delete("/api/uploads/{upload_id}")
def remove_upload(upload_id: int):
    try:
        return delete_upload(upload_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/models/retrain")
def retrain():
    return train_models()


# Every mutation routed through this guard moves a model input -- a change event or start date
# moves declared variables 4/6/7, a lead edit moves the aggregates, an ad-performance edit moves
# spend/frequency -- so every stored forecast goes stale the instant one is saved. Retraining
# inline would hang the caller: train_models() runs a rolling-origin backtest over every ad set
# and measures ~29s here, so it always runs on a background thread behind this guard.
#
# The guard is single-flight with a one-slot queue rather than a plain "skip if busy": a run
# already in flight may have read the aggregates before this save landed, so its results
# would not reflect the edit. One follow-up pass is queued instead, and further saves during
# that window collapse into the same pass rather than stacking N full retrains.
#
# In front of that sits a debounce (see RETRAIN_DEBOUNCE_SECONDS) so a burst of edits schedules
# one run after the user stops, rather than one run per edit.
_retrain_lock = threading.Lock()
_retrain_running = False
_retrain_queued = False
_retrain_error: str | None = None
# Set when a queued pass must run rebuild_aggregates() before train_models(). Lead edits move
# what daily_ad_set_aggregates holds (the model reads that table, not lead_events directly), so
# retraining without rebuilding first would train on pre-edit counts. Ad-performance edits do
# not -- rebuild_aggregates() reads only lead_events -- so they leave this false and skip the
# extra ~2s. Tracked as a flag rather than a second guard so a burst of mixed edits still
# collapses into one pass that rebuilds if *any* of them needed it.
_retrain_needs_aggregates = False
# Debounce before a requested retrain actually starts. The Dataset board fires one request per
# committed cell, so firing a full train_models() per edit meant that during any editing session
# a ~30s retrain was permanently in flight -- and because numpy/pandas hold the GIL for long
# stretches, it taxed every subsequent edit in the same process by ~200-400ms. Waiting for the
# user to stop editing costs nothing (the retrain is already asynchronous and the UI shows a
# chip until it lands) and collapses a burst of edits into one run instead of N.
RETRAIN_DEBOUNCE_SECONDS = 4.0
_retrain_timer: threading.Timer | None = None
_retrain_pending = False


def _run_retrain() -> None:
    global _retrain_running, _retrain_queued, _retrain_error, _retrain_needs_aggregates
    while True:
        with _retrain_lock:
            rebuild_first = _retrain_needs_aggregates
            _retrain_needs_aggregates = False
        try:
            if rebuild_first:
                rebuild_aggregates()
            train_models()
        except Exception as exc:  # a failed retrain must not wedge the guard permanently
            with _retrain_lock:
                _retrain_error = str(exc)
                # Put the rebuild back: it either didn't run or didn't finish, and dropping it
                # would leave the aggregates stale until some *later* lead edit happens to ask
                # for one -- a silent wrong-numbers state, not a visible failure.
                if rebuild_first:
                    _retrain_needs_aggregates = True
                    _retrain_queued = True
        with _retrain_lock:
            if not _retrain_queued:
                _retrain_running = False
                return
            _retrain_queued = False


def _start_retrain() -> None:
    """Debounce timer callback: promote the pending request into a real run."""
    global _retrain_running, _retrain_queued, _retrain_pending, _retrain_timer
    with _retrain_lock:
        _retrain_pending = False
        _retrain_timer = None
        if _retrain_running:
            # A previous run is still going and may have read the data before these edits
            # landed. Queue exactly one follow-up rather than starting a second run.
            _retrain_queued = True
            return
        _retrain_running = True
    threading.Thread(target=_run_retrain, daemon=True).start()


def _request_retrain(rebuild_aggregates_first: bool = False) -> None:
    """Schedule a retrain once the caller stops editing, collapsing a burst into one run.

    Returns immediately -- the request never waits on model work. Each call restarts the
    debounce window, so N rapid edits produce one retrain `RETRAIN_DEBOUNCE_SECONDS` after the
    last of them, not N retrains competing with the edits that triggered them.

    `rebuild_aggregates_first` is sticky: it stays set until a run actually consumes it, so an
    edit landing mid-retrain still gets its aggregates rebuilt by the queued follow-up pass.
    """
    global _retrain_timer, _retrain_pending, _retrain_needs_aggregates, _retrain_error
    with _retrain_lock:
        if rebuild_aggregates_first:
            _retrain_needs_aggregates = True
        _retrain_error = None
        _retrain_pending = True
        if _retrain_timer is not None:
            _retrain_timer.cancel()
        _retrain_timer = threading.Timer(RETRAIN_DEBOUNCE_SECONDS, _start_retrain)
        _retrain_timer.daemon = True
        _retrain_timer.start()


@app.get("/api/models/retrain-status")
def retrain_status():
    # "Pending" (waiting out the debounce) counts as running for the UI's purposes: the model
    # is out of date either way, and reporting false in that window would make the retrain chip
    # blink off and back on between the user's edit and the run actually starting.
    with _retrain_lock:
        running, error = (_retrain_running or _retrain_pending), _retrain_error
    with connect() as db:
        row = db.execute(
            "SELECT id, status, completed_at FROM model_training_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "running": running,
        "last_error": error,
        "latest_run_id": int(row["id"]) if row else None,
        "latest_status": row["status"] if row else None,
        "latest_completed_at": row["completed_at"] if row else None,
    }


@app.get("/api/model-runs")
def model_runs():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM model_training_runs ORDER BY id DESC LIMIT 30").fetchall()]


@app.get("/api/model-metrics")
def model_metrics(ad_set_id: str | None = None, horizon: int | None = Query(None, ge=7, le=14)):
    with connect() as db:
        run = db.execute(
            "SELECT * FROM model_training_runs WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not run:
            return {"run": None, "overall": None, "horizons": [], "metrics": [], "ols": {"univariate": None, "multivariate": None}}
        where, params = ["m.training_run_id=?"], [run["id"]]
        if ad_set_id:
            where.append("m.utm_ad_set_id=?"); params.append(ad_set_id)
        if horizon:
            where.append("m.horizon_days=?"); params.append(horizon)
        rows = db.execute(
            f"""SELECT m.*,
              CASE WHEN EXISTS (
                SELECT 1 FROM forecasts f WHERE f.training_run_id=m.training_run_id
                AND f.utm_ad_set_id=m.utm_ad_set_id AND f.horizon_days=m.horizon_days
                AND f.model_used=m.model_used
              ) THEN 1 ELSE 0 END AS is_selected
              FROM model_backtest_metrics m WHERE {' AND '.join(where)}
              ORDER BY m.horizon_days, is_selected DESC, m.selection_score, m.utm_ad_set_id""",
            params,
        ).fetchall()
    metrics = []
    numeric_fields = ["mae", "rmse", "wape", "mase", "bias", "r2_out_of_sample",
                      "interval_coverage", "average_interval_width", "selection_score",
                      "weekday_wape", "weekend_wape", "weekday_bias", "weekend_bias",
                      "weekday_seasonality_strength", "forecast_variance_ratio", "flatness_penalty"]
    for row in rows:
        item = dict(row)
        for field in numeric_fields:
            if item[field] is not None and not math.isfinite(float(item[field])):
                item[field] = None
        metrics.append(item)
    selected = [row for row in metrics if row["is_selected"]]
    fields = numeric_fields

    def summarize(items: list[dict]) -> dict | None:
        if not items:
            return None
        summary = {}
        for field in fields:
            values = [float(item[field]) for item in items if item[field] is not None]
            summary[field] = median(values) if values else None
        summary["backtest_windows"] = sum(int(item["backtest_windows"]) for item in items)
        summary["ad_set_count"] = len({item["utm_ad_set_id"] for item in items})
        return summary

    horizons = []
    for days in sorted({int(row["horizon_days"]) for row in selected}):
        items = [row for row in selected if int(row["horizon_days"]) == days]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["model_used"]] = counts.get(item["model_used"], 0) + 1
        horizon_summary = summarize(items) or {}
        horizon_summary.update({"horizon_days": days,
                                "most_selected_model": max(counts, key=counts.get) if counts else None,
                                "model_selection_counts": counts})
        horizons.append(horizon_summary)
    return {"run": dict(run), "overall": summarize(selected), "horizons": horizons, "metrics": metrics,
            "ols": get_ols_model_summaries()}


@app.get("/api/ols-summary")
def ols_summary(ad_set_id: str | None = None, campaign_id: str | None = None):
    # Same payload as the "ols" key of /api/model-metrics, without the ~1 MB of per-ad-set
    # backtest rows that endpoint also carries. The Forecast page only wants the regression.
    # ad_set_id wins over campaign_id, matching the Forecast page, where picking an ad set
    # is the more specific act. Omit both for the whole portfolio.
    return get_ols_model_summaries(ad_set_id=ad_set_id, campaign_id=campaign_id)


@app.get("/api/model-diagnostics")
def model_diagnostics(limit: int = Query(10, ge=3, le=25)):
    return get_model_diagnostics(limit)


@app.get("/api/dataset/overview")
def dataset_overview():
    return get_dataset_overview()


@app.get("/api/dataset/correlation")
def dataset_correlation(ad_set_id: str | None = None, campaign_id: str | None = None):
    return get_dataset_correlation(ad_set_id=ad_set_id, campaign_id=campaign_id)


def _parse_filters_param(filters: str | None) -> list | None:
    """Shared JSON-decode for the `filters` query param used by the Dataset and Lead
    Management boards. Both send the same [{field, operator, value}, ...] shape, and both
    need the same 400 (not a 500) when it is not valid JSON."""
    if not filters:
        return None
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "filters must be a valid JSON array.") from exc
    if not isinstance(parsed, list):
        raise HTTPException(400, "filters must be a JSON array")
    return parsed


@app.get("/api/dataset/rows")
def dataset_rows(
    table: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
    # JSON-encoded [{"field": ..., "operator": ..., "value": ...}, ...] from the Dataset
    # page's filter bar. Kept as one query param (rather than repeated ?filters=) since each
    # row carries three related values that need to stay grouped.
    filters: str | None = None,
    # Click-to-sort column header, and the board's free-text search box. `sort` is a field key
    # resolved against the table's sort_fields allowlist in core (an unknown key falls back to
    # the table's default order rather than erroring).
    sort: str | None = None,
    direction: str = "asc",
    search: str | None = None,
):
    parsed_filters = _parse_filters_param(filters)
    try:
        return get_dataset_rows(
            table, offset, limit, campaign_id=campaign_id, ad_set_id=ad_set_id,
            filters=parsed_filters, sort=sort, direction=direction, search=search,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/dataset/row-ids")
def dataset_row_ids(
    table: str,
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
    filters: str | None = None,
    search: str | None = None,
):
    """Every row id matching the current filter/search -- backs the Dataset page's "select all
    N matching rows" action, which needs ids beyond whatever page happens to be loaded."""
    parsed_filters = _parse_filters_param(filters)
    try:
        return get_dataset_row_ids(
            table, campaign_id=campaign_id, ad_set_id=ad_set_id,
            filters=parsed_filters, search=search,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/dashboard/summary")
def summary():
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0]
        sets = db.execute("SELECT COUNT(DISTINCT utm_ad_set_id) FROM lead_events").fetchone()[0]
        latest_upload = db.execute("SELECT uploaded_at FROM raw_uploads ORDER BY id DESC LIMIT 1").fetchone()
        run = db.execute("SELECT * FROM model_training_runs WHERE status='completed' ORDER BY id DESC LIMIT 1").fetchone()
        latest_date = db.execute("SELECT MAX(date(created_at)) FROM lead_events").fetchone()[0]
        recent7 = db.execute("SELECT COUNT(*) FROM lead_events WHERE date(created_at) >= date(?, '-6 days')", (latest_date,)).fetchone()[0] if latest_date else 0
        previous7 = db.execute("SELECT COUNT(*) FROM lead_events WHERE date(created_at) BETWEEN date(?, '-13 days') AND date(?, '-7 days')", (latest_date, latest_date)).fetchone()[0] if latest_date else 0
        return {"total_leads": total, "unique_ad_sets": sets, "last_upload": latest_upload[0] if latest_upload else None,
                "last_data_date": latest_date, "recent_7_leads": recent7, "previous_7_leads": previous7,
                "backtest_accuracy": run["mean_backtest_accuracy"] if run else None, "last_trained": run["completed_at"] if run else None}


@app.get("/api/dashboard/insights")
def dashboard_insights():
    return get_dashboard_insights()


@app.get("/api/dashboard/ad-spend")
def dashboard_ad_spend():
    return get_ad_spend_analytics()


@app.get("/api/dashboard/ad-decisions")
def dashboard_ad_decisions(
    window_days: int = Query(14, ge=7, le=60),
    target_cpl: float | None = Query(None, gt=0),
):
    return get_ad_decisions(window_days, target_cpl)


@app.get("/api/dashboard/ad-decisions.csv")
def export_ad_decisions(
    window_days: int = Query(14, ge=7, le=60),
    target_cpl: float | None = Query(None, gt=0),
):
    result = get_ad_decisions(window_days, target_cpl)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Ad Set ID", "Campaign", "Verdict", "Reason", "Spend (USD)", "Leads",
                     "Cost Per Lead", "Prior Cost Per Lead", "CPL Change %", "Daily Spend",
                     "Suggested Daily Change", "Spend Share %"])
    for row in result.get("ads", []):
        _write_csv_row(writer, [row["ad_set_id"], row["campaign_name"], row["verdict"], row["reason"],
                         row["spend"], row["leads"], row["cpl"], row["cpl_prior"],
                         None if row["cpl_delta_pct"] is None else round(row["cpl_delta_pct"] * 100, 1),
                         row["daily_spend"], row["suggested_daily_delta"],
                         round(row["spend_share"] * 100, 1)])
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ad-decisions.csv"})


@app.get("/api/dashboard/forecast-tracking")
def dashboard_forecast_tracking(
    history_days: int = Query(90, ge=1, le=365),
    future_days: int = Query(14, ge=1, le=14),
    start_date: str | None = Query("2026-06-06", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
):
    return get_portfolio_forecast_tracking(history_days, future_days, start_date, campaign_id, ad_set_id)


@app.get("/api/forecast-scenario")
def forecast_scenario(
    ad_set_id: str,
    horizon: int = Query(14, ge=7, le=14),
    future_spend_daily: float | None = Query(None, ge=0),
):
    try:
        return get_forecast_scenario(ad_set_id, horizon, future_spend_daily)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/dashboard/budget-optimization")
def dashboard_budget_optimization():
    return get_budget_optimization()


@app.get("/api/budget-periods")
def budget_periods(ad_set_id: str | None = None):
    return list_budget_periods(ad_set_id)


@app.post("/api/budget-periods")
def create_budget_period(payload: BudgetPeriod):
    try:
        return save_budget_period(
            payload.ad_set_id, payload.start_date, payload.end_date,
            payload.daily_budget, payload.id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/budget-periods/{period_id}")
def remove_budget_period(period_id: int):
    try:
        return delete_budget_period(period_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/change-events")
def change_events(scope: str | None = None, ad_set_id: str | None = None):
    events = list_change_events(scope, ad_set_id)
    return {
        "events": events,
        "coverage": change_event_coverage(ad_set_id) if ad_set_id else None,
    }


@app.post("/api/change-events")
def create_change_event(payload: ChangeEvent):
    try:
        saved = save_change_event(
            payload.scope, payload.ad_set_id,
            payload.start_date, payload.end_date,
            payload.ad_id or "", payload.notes or "", payload.confirmed_by or "", payload.id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _request_retrain()
    return saved


@app.delete("/api/change-events/{event_id}")
def remove_change_event(event_id: int):
    try:
        deleted = delete_change_event(event_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    _request_retrain()
    return deleted


@app.get("/api/ad-set-start-dates")
def ad_set_start_dates(ad_set_id: str | None = None):
    return {"dates": list_ad_set_start_dates(ad_set_id)}


@app.post("/api/ad-set-start-dates")
def create_ad_set_start_date(payload: AdSetStartDate):
    try:
        saved = save_ad_set_start_date(
            payload.ad_set_id, payload.start_date, payload.confirmed_by or "", payload.notes or "",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _request_retrain()
    return saved


@app.delete("/api/ad-set-start-dates/{ad_set_id}")
def remove_ad_set_start_date(ad_set_id: str):
    try:
        deleted = delete_ad_set_start_date(ad_set_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    _request_retrain()
    return deleted


@app.get("/api/ad-sets")
def ad_sets(q: str = ""):
    with connect() as db:
        rows = db.execute("""SELECT source.utm_ad_set_id,
             COALESCE((
               SELECT mapped.utm_campaign_id
               FROM lead_events mapped
               WHERE mapped.utm_ad_set_id = source.utm_ad_set_id
                 AND TRIM(COALESCE(mapped.utm_campaign_id, '')) <> ''
               GROUP BY mapped.utm_campaign_id
               ORDER BY COUNT(*) DESC, MAX(datetime(mapped.created_at)) DESC
               LIMIT 1
             ), '') utm_campaign_id,
             COUNT(*) total_leads, MAX(date(source.created_at)) last_seen
             FROM lead_events source
             WHERE source.utm_ad_set_id LIKE ? OR source.utm_campaign_id LIKE ?
             GROUP BY source.utm_ad_set_id ORDER BY last_seen DESC LIMIT 250""", (f"%{q}%", f"%{q}%")).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/leads")
def leads(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
    limit: int = Query(1000, ge=1, le=5000),
):
    where = ["date(created_at)=date(?)"]
    params: list[object] = [date]
    if campaign_id:
        where.append("utm_campaign_id=?")
        params.append(campaign_id)
    if ad_set_id:
        where.append("utm_ad_set_id=?")
        params.append(ad_set_id)
    params.append(limit)
    with connect() as db:
        rows = db.execute(
            f"""SELECT id, platform, status, lead_quality, created_at, updated_at, customer_name,
               utm_campaign, utm_campaign_id, utm_ad_set_id, utm_ad_id,
               fb_ad_title, amount_spent_usd
               FROM lead_events
               WHERE {' AND '.join(where)}
               ORDER BY datetime(created_at), id
               LIMIT ?""",
            params,
        ).fetchall()
    return {
        "date": date,
        "campaign_id": campaign_id,
        "ad_set_id": ad_set_id,
        "count": len(rows),
        "rows": [dict(row) for row in rows],
    }


@app.get("/api/lead-management/options")
def lead_management_options():
    """Campaign / ad set / date-bound choices for the Lead Management filter bar."""
    return get_lead_filter_options()


@app.get("/api/lead-management/summary")
def lead_management_summary(
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
    filters: str | None = None,
    search: str | None = None,
):
    """Pipeline-stage counts for the same filter set the board is paging through -- takes the
    identical params as /api/dataset/rows?table=leads so the funnel and the table can never
    describe different row sets."""
    try:
        return get_lead_pipeline_summary(
            campaign_id=campaign_id, ad_set_id=ad_set_id,
            filters=_parse_filters_param(filters), search=search,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/lead-management/duplicates")
def lead_management_duplicates():
    """Duplicate customer names across the full dataset, independent of board filters."""
    return get_duplicate_leads()


def _lead_management_export_filename(filters: list | None) -> str:
    def clean_date_label(value: object) -> str:
        text = str(value or "")
        if len(text) == 10 and text[4] == "-" and text[7] == "-" and text.replace("-", "").isdigit():
            return text
        return "custom"

    for row in filters or []:
        if row.get("field") != "created_at" or row.get("operator") != "between":
            continue
        value = row.get("value")
        if isinstance(value, dict) and value.get("from") and value.get("to"):
            return f"lead-management-{clean_date_label(value['from'])}-to-{clean_date_label(value['to'])}.csv"
    return "lead-management-leads.csv"


def _lead_management_created_label(value: object) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return text
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


@app.get("/api/lead-management/leads.csv")
def lead_management_export(
    campaign_id: str | None = None,
    ad_set_id: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    direction: str = "asc",
    search: str | None = None,
):
    parsed_filters = _parse_filters_param(filters)
    try:
        rows: list[dict] = []
        offset = 0
        page_size = 500
        while True:
            page = get_dataset_rows(
                "leads", offset, page_size, campaign_id=campaign_id, ad_set_id=ad_set_id,
                filters=parsed_filters, sort=sort, direction=direction, search=search,
            )
            page_rows = page.get("rows") or []
            rows.extend(page_rows)
            if not page_rows or len(rows) >= int(page.get("total") or 0):
                break
            offset += page_size
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    output = io.StringIO()
    writer = csv.writer(output)
    _write_csv_row(writer, [
        "Created", "Customer", "Status", "Lead Quality", "Campaign",
        "Campaign ID", "Ad set ID", "Ad ID", "Ad title",
    ])
    for row in rows:
        _write_csv_row(writer, [
            _lead_management_created_label(row.get("created_at")),
            row.get("customer_name"),
            row.get("status"),
            row.get("lead_quality"),
            row.get("utm_campaign"),
            row.get("utm_campaign_id"),
            row.get("utm_ad_set_id"),
            row.get("utm_ad_id"),
            row.get("fb_ad_title"),
        ])
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={_lead_management_export_filename(parsed_filters)}"},
    )


# Rating a batch is one judgement about many rows, so it is one request rather than N.
#
# And deliberately NO retrain, unlike the single-lead PATCH below. `lead_quality` is a pure
# CRM annotation: nothing in rebuild_aggregates() or train_models() reads it (grep it in
# core.py -- outside the schema, the update allowlist, and this page's own summary query, it
# appears nowhere). The generic PATCH has to retrain because it can also move `created_at`,
# `utm_ad_set_id`, or `amount_spent_usd`, all of which really do feed the aggregates; this
# endpoint can only ever write the one column that doesn't. Scheduling ~31s of
# rebuild+train per rating batch would burn the CPU to recompute an identical model.
@app.get("/api/follow-up/leads")
def followup_leads(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    search: str = "", statuses: str = "", due: str = "", assigned_to: str = "",
    platform: str = "", service: str = "", sort: str = "next_follow_up_at",
    direction: str = "asc",
):
    return get_followup_leads(
        limit=limit, offset=offset, search=search,
        statuses=[value for value in statuses.split(",") if value], due=due,
        assigned_to=assigned_to, platform=platform, service=service,
        sort=sort, direction=direction,
    )


@app.get("/api/follow-up/leads/{lead_id}")
def followup_lead(lead_id: int):
    try:
        return get_followup_lead(lead_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/follow-up/leads/{lead_id}")
def update_followup_lead(lead_id: int, payload: FollowupUpdate, request: Request):
    try:
        return save_followup(lead_id, payload.dict(exclude_unset=True), _current_user(request))
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(404 if message == "Lead not found." else 422, message) from exc


@app.post("/api/leads/bulk-quality")
def bulk_lead_quality(payload: LeadQualityBulkUpdate):
    try:
        return bulk_update_lead_quality(payload.lead_ids, payload.lead_quality, retrain=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/leads/bulk-delete")
def bulk_lead_delete(payload: LeadBulkDelete):
    try:
        result = bulk_delete_lead_events(payload.lead_ids, retrain=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result["deleted"]:
        _request_retrain(rebuild_aggregates_first=True)
    return result


@app.post("/api/leads/delete-newer-duplicates")
def delete_newer_duplicate_leads_endpoint():
    result = delete_newer_duplicate_leads(retrain=False)
    if result["deleted"]:
        _request_retrain(rebuild_aggregates_first=True)
    return result


@app.post("/api/leads")
def create_lead(payload: LeadCreate):
    try:
        result = create_lead_event(payload.dict(exclude_unset=True), retrain=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _request_retrain(rebuild_aggregates_first=True)
    return result


# Lead edits schedule their model refresh in the background, for the same reason the
# ad-performance endpoints below do: rebuild_aggregates() + train_models() measure ~31s
# together here, and the Dataset board fires one request per committed cell. Running that
# inline froze the whole board (`.board-scroll.is-busy` disables pointer events) for half a
# minute per keystroke-commit. `rebuild_aggregates_first=True` because a lead edit changes
# what those aggregates hold -- see the guard's comment above.
@app.patch("/api/leads/{lead_id}")
def patch_lead(lead_id: int, payload: LeadUpdate, request: Request):
    if getattr(request.state, "user_role", "") == "staff" and payload.status is not None:
        raise HTTPException(403, "Staff can only change lead quality.")
    try:
        result = update_lead_event(lead_id, payload.dict(exclude_unset=True), retrain=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _request_retrain(rebuild_aggregates_first=True)
    return result


@app.delete("/api/leads/{lead_id}")
def remove_lead(lead_id: int):
    try:
        result = delete_lead_event(lead_id, retrain=False)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    _request_retrain(rebuild_aggregates_first=True)
    return result


# Row-level edits for the Dataset page's "Ad performance" / "Combined export" board tabs.
# Both tabs are two views over the same daily_ad_performance rows, so one pair of endpoints
# serves both. These schedule a background retrain too, but without the aggregate rebuild:
# spend and frequency are model inputs, yet rebuild_aggregates() reads only lead_events.
@app.patch("/api/dataset/ad-performance/{row_id}")
def patch_ad_performance(row_id: int, payload: AdPerformanceUpdate):
    try:
        result = update_ad_performance_row(row_id, payload.dict(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _request_retrain()
    return result


@app.delete("/api/dataset/ad-performance/{row_id}")
def remove_ad_performance(row_id: int):
    try:
        result = delete_ad_performance_row(row_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    _request_retrain()
    return result


def _latest_forecast_rows(ad_set_ids: list[str] | None, active_only: bool):
    with connect() as db:
        run = db.execute("SELECT id, completed_at FROM model_training_runs WHERE status='completed' ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return []
        params: list[object] = [run["id"]]
        where = "f.training_run_id=?"
        if ad_set_ids:
            where += f" AND f.utm_ad_set_id IN ({','.join('?' for _ in ad_set_ids)})"
            params.extend(ad_set_ids)
        if active_only:
            where += " AND EXISTS (SELECT 1 FROM lead_events l WHERE l.utm_ad_set_id=f.utm_ad_set_id AND date(l.created_at) >= date((SELECT MAX(date(created_at)) FROM lead_events), '-13 days'))"
        rows = db.execute(f"""SELECT f.*, r.completed_at last_trained,
          (SELECT COUNT(*) FROM lead_events l WHERE l.utm_ad_set_id=f.utm_ad_set_id AND date(l.created_at) >= date((SELECT MAX(date(created_at)) FROM lead_events), '-6 days')) historical_7,
          (SELECT COUNT(*) FROM lead_events l WHERE l.utm_ad_set_id=f.utm_ad_set_id AND date(l.created_at) >= date((SELECT MAX(date(created_at)) FROM lead_events), '-13 days')) historical_14
          FROM forecasts f JOIN model_training_runs r ON r.id=f.training_run_id WHERE {where}
          ORDER BY f.utm_ad_set_id, f.horizon_days""", params).fetchall()
        grouped = {}
        for row in rows:
            item = dict(row); key = item["utm_ad_set_id"]
            base = grouped.setdefault(key, {"utm_ad_set_id": key, "utm_campaign_id": item["utm_campaign_id"],
                "historical_7": item["historical_7"], "historical_14": item["historical_14"], "last_trained": item["last_trained"]})
            base[f"forecast_{item['horizon_days']}"] = item
        if grouped:
            daily_params: list[object] = [run["id"], *grouped.keys()]
            daily_rows = db.execute(f"""SELECT forecast_date date, day_index, weekday_name,
              weekday_factor, utm_ad_set_id,
              predicted_leads, lower_estimate, upper_estimate, confidence_score,
              model_used, sparse_warning, explanation
              FROM forecast_daily_predictions
              WHERE training_run_id=? AND utm_ad_set_id IN ({','.join('?' for _ in grouped)})
              ORDER BY utm_ad_set_id, day_index""", daily_params).fetchall()
            for daily in daily_rows:
                item = dict(daily)
                grouped[item["utm_ad_set_id"]].setdefault("daily_forecast", []).append(item)
        return list(grouped.values())


@app.get("/api/forecasts")
def forecasts(ad_set_ids: str | None = None, scope: str = Query("active", pattern="^(active|all|selected)$")):
    ids = [x.strip() for x in ad_set_ids.split(",") if x.strip()] if ad_set_ids else None
    if scope == "selected" and not ids:
        return []
    return _latest_forecast_rows(ids, scope == "active")


@app.get("/api/history")
def history(ad_set_id: str):
    with connect() as db:
        rows = db.execute("SELECT aggregate_date date, lead_count leads FROM daily_ad_set_aggregates WHERE utm_ad_set_id=? ORDER BY aggregate_date", (ad_set_id,)).fetchall()
        if not rows:
            return []
        start, end = rows[0]["date"], rows[-1]["date"]
        filled = db.execute("""WITH RECURSIVE dates(d) AS (SELECT date(?) UNION ALL SELECT date(d, '+1 day') FROM dates WHERE d < date(?))
            SELECT dates.d date, COALESCE(a.lead_count,0) leads FROM dates LEFT JOIN daily_ad_set_aggregates a
            ON a.aggregate_date=dates.d AND a.utm_ad_set_id=?""", (start, end, ad_set_id)).fetchall()
        return [dict(r) for r in filled]


@app.get("/api/weekday-profile")
def weekday_profile(ad_set_id: str):
    profile = get_weekday_profile(ad_set_id)
    if profile is None:
        raise HTTPException(404, "Ad set not found")
    return profile


@app.get("/api/forecast-realizations")
def forecast_realizations(ad_set_id: str | None = None, limit: int = Query(250, ge=1, le=1000)):
    return get_forecast_realizations(ad_set_id, limit)


@app.get("/api/forecasts.csv")
def export_forecasts(scope: str = "active"):
    rows = _latest_forecast_rows(None, scope == "active")
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["UTM Ad Set ID", "UTM Campaign ID", "Historical Leads Last 7 Days", "Historical Leads Last 14 Days",
        "Forecast Leads Next 7 Days", "Forecast Leads Next 14 Days", "Lower Estimate (14d)", "Upper Estimate (14d)",
        "Confidence Score", "Model Used", "Last Trained Date"])
    for row in rows:
        f7, f14 = row.get("forecast_7", {}), row.get("forecast_14", {})
        _write_csv_row(writer, [row["utm_ad_set_id"], row["utm_campaign_id"], row["historical_7"], row["historical_14"],
            f7.get("predicted_leads"), f14.get("predicted_leads"), f14.get("lower_estimate"), f14.get("upper_estimate"),
            f14.get("confidence_score"), f14.get("model_used"), row["last_trained"]])
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=lead-forecasts.csv"})


@app.get("/api/forecasts/daily.csv")
def export_daily_forecasts(scope: str = "active"):
    rows = _latest_forecast_rows(None, scope == "active")
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["UTM Ad Set ID", "UTM Campaign ID", "Forecast Date", "Weekday", "Weekday Factor", "Day Index",
        "Predicted Leads", "Lower Estimate", "Upper Estimate", "Confidence Score",
        "Model Used", "Last Trained Date"])
    for row in rows:
        for day in row.get("daily_forecast", []):
            _write_csv_row(writer, [row["utm_ad_set_id"], row["utm_campaign_id"], day["date"], day.get("weekday_name"),
                day.get("weekday_factor"), day["day_index"],
                day["predicted_leads"], day["lower_estimate"], day["upper_estimate"],
                day["confidence_score"], day["model_used"], row["last_trained"]])
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=lead-daily-forecasts.csv"})


@app.get("/api/forecast-realizations.csv")
def export_forecast_realizations(ad_set_id: str | None = None):
    result = get_forecast_realizations(ad_set_id, 1000)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Training Run ID", "Generated At", "UTM Ad Set ID", "UTM Campaign ID",
        "Forecast Date", "Weekday", "Day Index", "Predicted Leads", "Actual Leads",
        "Error (Predicted - Actual)", "Absolute Error", "Lower Estimate", "Upper Estimate",
        "Interval Hit", "Model Used", "Realized At"])
    for row in result["rows"]:
        _write_csv_row(writer, [row["training_run_id"], row["generated_at"], row["utm_ad_set_id"],
            row["utm_campaign_id"], row["forecast_date"], row.get("weekday_name"), row["day_index"],
            row["predicted_leads"], row["actual_leads"], row["error"], row["absolute_error"],
            row["lower_estimate"], row["upper_estimate"], row["interval_hit"], row["model_used"],
            row["realized_at"]])
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=lead-forecast-realizations.csv"})


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
