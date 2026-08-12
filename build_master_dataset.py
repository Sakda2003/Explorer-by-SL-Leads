import re
import numpy as np
import pandas as pd

PERF = r"Dataset/Datsaa/Ad-Set-Performance-Jun-6-2026-to-Jul-31-2026.xlsx"
TRAF = r"Dataset/Datsaa/Dataset 06-06 - Current.xlsx"
HOL = r"Dataset/Dataset/holiday_proximity.xlsx"
OUT = r"Dataset/master_dataset.xlsx"

# ---------- load ----------
perf = pd.read_excel(PERF, dtype={"Campaign ID": str, "Ad set ID": str})
perf["date"] = pd.to_datetime(perf["Day"]).dt.normalize()

traf = pd.read_excel(
    TRAF,
    sheet_name="traffic_2026-08-01",
    dtype={"UTM Campaign ID": str, "UTM Ad Set ID": str, "UTM Ad ID": str},
)
traf["ts"] = pd.to_datetime(traf["Created At"], format="%m/%d/%Y, %I:%M:%S %p")
traf["date"] = traf["ts"].dt.normalize()
traf = traf[traf["UTM Ad Set ID"].notna()]

hol = pd.read_excel(HOL)
hol["date"] = pd.to_datetime(hol["date"]).dt.normalize()

# ---------- skeleton: ad set x day, days the ad set was live ----------
m = perf[[
    "date", "Ad set ID", "Campaign ID", "Campaign name", "Delivery status",
    "Amount spent (USD)", "Reach", "Impressions", "Frequency",
    "Messaging conversations started", "Leads", "Ad Set Budget", "Ad Set Budget Type",
]].copy()
m.columns = [
    "date", "ad_set_id", "campaign_id", "campaign_name", "delivery_status",
    "spend", "reach", "impressions", "frequency",
    "messaging_conversations", "leads_meta_reported", "ad_set_budget", "ad_set_budget_type",
]
m = m.sort_values(["ad_set_id", "date"]).reset_index(drop=True)

# ---------- 1. leads (CRM ground truth, not Meta's column) ----------
leads = (traf.groupby(["UTM Ad Set ID", "date"]).size()
         .rename("leads").reset_index()
         .rename(columns={"UTM Ad Set ID": "ad_set_id"}))
m = m.merge(leads, on=["ad_set_id", "date"], how="left")
m["leads"] = m["leads"].fillna(0).astype(int)

leads_new = (traf[traf["Status"] == "New"].groupby(["UTM Ad Set ID", "date"]).size()
             .rename("leads_new").reset_index()
             .rename(columns={"UTM Ad Set ID": "ad_set_id"}))
m = m.merge(leads_new, on=["ad_set_id", "date"], how="left")
m["leads_new"] = m["leads_new"].fillna(0).astype(int)

# ---------- 3. holiday proximity + 8. day of week ----------
m = m.merge(hol[["date", "is_holiday", "holiday_name", "holiday_proximity"]], on="date", how="left")
m["day_of_week"] = m["date"].dt.day_name()
m["day_of_week_num"] = m["date"].dt.dayofweek
m["is_weekend"] = m["day_of_week_num"].isin([5, 6]).astype(int)

# ---------- 4. days since ad set started ----------
start = m.groupby("ad_set_id")["date"].min().rename("adset_first_seen")
m = m.merge(start, on="ad_set_id")
m["days_since_adset_started"] = (m["date"] - m["adset_first_seen"]).dt.days
PERIOD_START = m["date"].min()
m["adset_start_censored"] = (m["adset_first_seen"] == PERIOD_START).astype(int)

# ---------- ad-set change detection (step-shift on trailing/leading windows) ----------
W = 7
# Set True if a trailing "*" in an FB Ad Title means the creative was re-published
# with an edited message template. Left False: only repeated creative codes count.
STAR_MEANS_TEMPLATE_EDIT = False


def step_events(series, dates, rel_thr, abs_thr=0.0, min_gap=5):
    """Return dates where the level of `series` steps up/down beyond rel_thr."""
    v = pd.Series(series).astype(float).values
    n = len(v)
    score = np.full(n, np.nan)
    for i in range(n):
        pre = v[max(0, i - W):i]
        post = v[i:i + W]
        if len(pre) < 3 or len(post) < 3:
            continue
        a, b = np.nanmean(pre), np.nanmean(post)
        if not np.isfinite(a) or not np.isfinite(b) or a <= 0:
            continue
        if abs(b - a) < abs_thr:
            continue
        score[i] = abs(b - a) / a
    out, last = [], -99
    order = np.argsort(-np.nan_to_num(score))
    picked = []
    for i in order:
        if not np.isfinite(score[i]) or score[i] < rel_thr:
            break
        if all(abs(i - j) >= min_gap for j in picked):
            picked.append(i)
    for i in sorted(picked):
        out.append(pd.Timestamp(dates[i]))
    return out


def step_ratio(series, dates, at):
    """Relative level shift of `series` at date `at`, using the same windows."""
    v = pd.Series(series).astype(float).values
    i = list(pd.to_datetime(dates)).index(pd.Timestamp(at))
    pre, post = v[max(0, i - W):i], v[i:i + W]
    if len(pre) < 3 or len(post) < 3:
        return 0.0
    a, b = np.nanmean(pre), np.nanmean(post)
    return 0.0 if not np.isfinite(a) or a <= 0 else (b - a) / a


adset_events = []
for aid, g in m.groupby("ad_set_id"):
    g = g.sort_values("date")
    d = g["date"].values
    cpm = np.where(g["impressions"] > 0, g["spend"] / g["impressions"].replace(0, np.nan) * 1000, np.nan)
    budget = step_events(g["spend"], d, rel_thr=0.35, abs_thr=0.50, min_gap=10)
    freq = step_events(g["frequency"], d, rel_thr=0.12, min_gap=10)
    cost = step_events(cpm, d, rel_thr=0.25, min_gap=10)
    # placement change = delivery price AND delivery pattern move together while
    # spend holds -- the signature of adding/removing a placement
    freq_soft = step_events(g["frequency"], d, rel_thr=0.08, min_gap=5)
    place = [dt for dt in cost if any(abs((dt - f).days) <= 3 for f in freq_soft)]
    taken = {}
    for dt in budget:
        taken[dt] = "budget_change"
    for dt in place:
        if all(abs((dt - t).days) >= 4 for t in taken):
            taken[dt] = "placement_change"
    for dt in freq:
        if all(abs((dt - t).days) >= 4 for t in taken):
            taken[dt] = "targeting_change"
    for dt in cost:
        if all(abs((dt - t).days) >= 4 for t in taken) and abs(step_ratio(cpm, d, dt)) >= 0.35:
            taken[dt] = "bid_change"
    # No day-0 seed. Point encoding (see `attach()` below) no longer needs every ad set to
    # carry SOME type on every row -- a day without a detected shift is legitimately
    # "no_change", the same baseline the app now uses, not a gap that has to be papered
    # over. The old seed asserted a fictitious budget_change on every ad set's first
    # calendar day of the export purely so the carry-forward encoding always had a value
    # to carry; removed 2026-08-07 along with that encoding.
    for dt, typ in taken.items():
        adset_events.append((aid, dt, typ))
adset_events = pd.DataFrame(adset_events, columns=["ad_set_id", "date", "ad_set_change_type"])

# ---------- ad-level change detection (from CRM ad-id activity) ----------
SEP = r"\||\s-\s"


def creative_code(title):
    if not isinstance(title, str):
        return ""
    head = re.split(SEP, title)[0]
    return re.sub(r"[^A-Za-z0-9]", "", head).upper()


def template_key(title):
    if not isinstance(title, str):
        return ""
    parts = [p for p in re.split(SEP, title) if p.strip()]
    return re.sub(r"[^A-Za-z0-9]", "", parts[-1]).upper() if parts else ""


t2 = traf[traf["date"] >= PERIOD_START].copy()
t2["code"] = t2["FB Ad Title"].map(creative_code)
t2["tmpl"] = t2["FB Ad Title"].map(template_key)
t2["starred"] = t2["FB Ad Title"].fillna("").str.contains(r"\*").astype(int)

ad_span = (t2.groupby(["UTM Ad Set ID", "UTM Ad ID"])
           .agg(first=("date", "min"), last=("date", "max"), n=("date", "size"),
                code=("code", "first"), tmpl=("tmpl", "first"),
                starred=("starred", "max"), title=("FB Ad Title", "first"))
           .reset_index().rename(columns={"UTM Ad Set ID": "ad_set_id", "UTM Ad ID": "ad_id"}))

adset_last_day = m.groupby("ad_set_id")["date"].max()
PAUSE_SILENCE = 10

ad_events = []
for aid, g in ad_span.groupby("ad_set_id"):
    if aid not in adset_last_day.index:
        continue
    g = g.sort_values("first")
    seen_codes, seen_tmpl = set(), set()
    for _, r in g.iterrows():
        if r["first"] > PERIOD_START:            # not left-censored
            republished = r["code"] in seen_codes or (STAR_MEANS_TEMPLATE_EDIT and r["starred"])
            new_template = bool(seen_tmpl) and r["tmpl"] not in seen_tmpl
            typ = "msg_template_change" if (republished or new_template) else "ad_added"
            ad_events.append((aid, r["first"], typ, r["ad_id"]))
        seen_codes.add(r["code"])
        seen_tmpl.add(r["tmpl"])
        # pause: went silent while the ad set kept running
        if (adset_last_day[aid] - r["last"]).days >= PAUSE_SILENCE:
            ad_events.append((aid, r["last"] + pd.Timedelta(days=1), "ad_paused", r["ad_id"]))
ad_events = pd.DataFrame(ad_events, columns=["ad_set_id", "date", "ad_change_type", "ad_id"])

# swap = an add and a pause inside a 2-day window
swaps = []
for aid, g in ad_events.groupby("ad_set_id"):
    adds = g[g["ad_change_type"].isin(["ad_added", "msg_template_change"])]["date"].tolist()
    paus = g[g["ad_change_type"] == "ad_paused"]["date"].tolist()
    for a in adds:
        for p in paus:
            if abs((a - p).days) <= 2:
                swaps.append((aid, min(a, p)))
swaps = set(swaps)

PRIO = {"msg_template_change": 0, "ad_swapped": 1, "ad_added": 2, "ad_paused": 3}
ad_events["ad_change_type"] = [
    "ad_swapped" if (r.ad_change_type != "msg_template_change" and
                     any((r.ad_set_id, r.date - pd.Timedelta(days=k)) in swaps for k in range(0, 3)))
    else r.ad_change_type
    for r in ad_events.itertuples()
]
ad_events["_p"] = ad_events["ad_change_type"].map(PRIO)
ad_events = (ad_events.sort_values("_p").groupby(["ad_set_id", "date"], as_index=False).first()
             .drop(columns=["_p", "ad_id"]))

# ---------- recency (carried forward) + type (point-in-time) as-of each day ----------
# Matches `_resolve_change_state` / `_change_state_as_of` in backend/core.py (redesigned
# 2026-08-07). Recency still carries forward from the most recent detected event -- that
# half is unchanged. Type does NOT: `merge_asof`'s natural carry-forward used to pin the
# matched event's type onto every day from its date to the next event, which meant an ad
# set's first detected shift could end up constant across its entire history -- exactly the
# zero-variance-column problem the app fixed the same day. Now the type only holds on the
# event's own date; every other day, including days after a LATER change, reads `none_label`
# ("no_change" for both scopes here, matching the app's raw-table label).
def attach(base, ev, type_col, recency_col, none_label):
    ev = ev.sort_values(["ad_set_id", "date"]).rename(columns={"date": "_ev_date"})
    base = base.sort_values("date")
    out = pd.merge_asof(
        base.sort_values("date"), ev.sort_values("_ev_date"),
        left_on="date", right_on="_ev_date", by="ad_set_id", direction="backward",
    )
    out[recency_col] = (out["date"] - out["_ev_date"]).dt.days
    on_event_day = out["_ev_date"].notna() & (out["date"] == out["_ev_date"])
    out[type_col] = np.where(on_event_day, out[type_col], none_label)
    # An ad set with zero detected events anywhere in its history has no row in `ev` at all,
    # so `_ev_date` (and recency) is NaN for its every day -- not reachable before removing
    # the day-0 seed above, since that seed guaranteed at least one event per ad set. The
    # ad set's own age stands in for recency here; the app instead leaves recency blank for
    # an ad set with nothing ever recorded (`_change_state_as_of` returns `None, None`) --
    # a deliberate divergence, since this script has no "nothing recorded yet" concept, only
    # "the detector found nothing", and reporting 0 would misread as "changed on day one".
    out[recency_col] = out[recency_col].fillna(out["days_since_adset_started"]).astype(int)
    return out.drop(columns=["_ev_date"])


m = attach(m, adset_events, "ad_set_change_type", "ad_set_change_recency", "no_change")
m = attach(m, ad_events, "ad_change_type", "ad_change_recency", "no_change")

m["ad_set_change_today"] = (m["ad_set_change_type"] != "no_change").astype(int)
m["ad_change_today"] = (m["ad_change_type"] != "no_change").astype(int)

# ---------- derived helpers ----------
m["messaging_conversations"] = m["messaging_conversations"].fillna(0)
m["leads_meta_reported"] = m["leads_meta_reported"].fillna(0)
m["holiday_name"] = m["holiday_name"].fillna("")
m["is_holiday"] = m["is_holiday"].fillna(0).astype(int)
m["cpl"] = np.where(m["leads"] > 0, m["spend"] / m["leads"], np.nan)
m["cpm"] = np.where(m["impressions"] > 0, m["spend"] / m["impressions"] * 1000, np.nan)

COLS = [
    "date", "ad_set_id", "campaign_id", "campaign_name", "delivery_status",
    "leads", "spend",
    "holiday_proximity", "is_holiday", "holiday_name",
    "days_since_adset_started", "adset_start_censored",
    "frequency",
    "ad_change_recency", "ad_set_change_recency",
    "day_of_week", "day_of_week_num", "is_weekend",
    "ad_set_change_type", "ad_change_type",
    "ad_set_change_today", "ad_change_today",
    "reach", "impressions", "messaging_conversations", "ad_set_budget", "ad_set_budget_type",
    "leads_new", "leads_meta_reported", "cpl", "cpm",
]
m = m[COLS].sort_values(["ad_set_id", "date"]).reset_index(drop=True)

# ---------- daily rollup ----------
daily = (m.groupby("date").agg(
    leads=("leads", "sum"), spend=("spend", "sum"), reach=("reach", "sum"),
    impressions=("impressions", "sum"), active_ad_sets=("ad_set_id", "nunique"),
    ad_set_changes=("ad_set_change_today", "sum"), ad_changes=("ad_change_today", "sum"),
).reset_index())
daily["frequency"] = daily["impressions"] / daily["reach"]
daily = daily.merge(hol[["date", "is_holiday", "holiday_name", "holiday_proximity"]], on="date", how="left")
daily["day_of_week"] = daily["date"].dt.day_name()
daily["day_of_week_num"] = daily["date"].dt.dayofweek
daily["is_weekend"] = daily["day_of_week_num"].isin([5, 6]).astype(int)
daily["cpl"] = np.where(daily["leads"] > 0, daily["spend"] / daily["leads"], np.nan)

# ---------- dictionary ----------
DICT = [
    ("leads", "target", "Daily lead count from the CRM traffic export, counted by UTM Ad Set ID + Created At date. Meta's own Leads column is kept separately as leads_meta_reported."),
    ("spend", "driver", "Amount spent (USD) from the Meta ad-set export."),
    ("holiday_proximity", "calendar", "Bucketed distance to nearest Cambodian public holiday, joined from holiday_proximity.xlsx."),
    ("days_since_adset_started", "lifecycle", "Days since the ad set's first day in the export window. adset_start_censored=1 means the ad set was already running on 2026-06-06, so this is a lower bound."),
    ("frequency", "delivery", "Impressions / reach, as reported by Meta."),
    ("ad_change_recency", "change", "Days since the most recent ad-level change in this ad set (counts up on days with no change; 0 on a detected change's own day). Falls back to days_since_adset_started when the detector found nothing anywhere in this ad set's history."),
    ("ad_set_change_recency", "change", "Days since the most recent ad-set-level change. Same rule and fallback."),
    ("day_of_week", "calendar", "Weekday name; day_of_week_num is Monday=0."),
    ("ad_set_change_type", "change", "Point-in-time since 2026-08-07 (matches the app's own recorded-change encoding): budget_change | targeting_change | placement_change | bid_change on the exact day a step shift was detected, no_change every other day -- including days after a LATER detected change. Detected from 7-day trailing-vs-leading step shifts because Meta's Ad Set Budget export field is static. budget_change = spend level shift >35%; placement_change = CPM shift >25% moving together with a frequency shift >8% within 3 days while spend holds; targeting_change = frequency shift >12% alone; bid_change = CPM shift >35% alone. All four are inferred proxies, not fields reported by Meta -- see [[Change-Event-UI-Recorder]] for the app's confirmed-only alternative, which this offline script does not use."),
    ("ad_change_type", "change", "Point-in-time since 2026-08-07: ad_added | ad_paused | ad_swapped | msg_template_change on the exact day detected, no_change every other day. Inferred from UTM Ad ID activity in the CRM export. ad_added = ad's first lead day; msg_template_change = a new Ad ID whose creative code was already used in the ad set (creative republished with edited copy/template); ad_paused = day after an ad's last lead when the ad set kept running >=10 more days; ad_swapped = an add and a pause within 2 days."),
    ("leads_new", "support", "Subset of leads with CRM Status = New (first-time customers)."),
    ("cpl / cpm", "support", "spend/leads and spend/impressions*1000."),
]

with pd.ExcelWriter(OUT, engine="openpyxl", datetime_format="yyyy-mm-dd") as w:
    m.to_excel(w, sheet_name="master_adset_daily", index=False)
    daily.to_excel(w, sheet_name="master_daily", index=False)
    pd.DataFrame(DICT, columns=["variable", "group", "definition"]).to_excel(w, sheet_name="data_dictionary", index=False)

m.to_csv(r"Dataset/master_dataset.csv", index=False)

print("rows", m.shape, "| ad sets", m.ad_set_id.nunique(), "|", m.date.min().date(), "->", m.date.max().date())
print("leads CRM total", m.leads.sum(), "| Meta reported", m.leads_meta_reported.sum())
print("spend", round(m.spend.sum(), 2))
print(m.ad_set_change_type.value_counts().to_string())
print(m.ad_change_type.value_counts().to_string())
print("adset change events", len(adset_events), "| ad change events", len(ad_events))
print(m.isna().sum()[m.isna().sum() > 0].to_string())
