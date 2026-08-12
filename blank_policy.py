"""Blank policy for the exported datasets.

Rule: a column shows a value only when that value was READ from a source. Anything
reconstructed from delivery signatures is moved to a parallel `<col>_inferred`
column and the primary column is left blank, so a blank honestly means "nobody has
recorded this yet" rather than "the detector had nothing to go on".

The inferred values are kept, not deleted -- they are what the model currently runs
on, and they are the starting point for confirming events by hand.
"""

import pandas as pd

# columns whose values are reconstructions, not reported fields
INFERRED_COLS = [
    "ad_set_change_type", "ad_set_change_recency", "ad_set_change_today",
    "ad_change_type", "ad_change_recency", "ad_change_today",
]

# blank category for every column that can be empty
#   MISSING    -> real gap, someone must go and find it
#   STRUCTURAL -> blank is the correct answer, nothing to fill
#   INFERRED   -> blanked by this policy; the guess sits in <col>_inferred
BLANK_CATEGORY = {
    "holiday_name": ("STRUCTURAL", "Blank on every ordinary day. Only holidays carry a name."),
    "cpl": ("STRUCTURAL", "spend / leads is undefined when the ad set got 0 leads that day."),
    "cpm": ("STRUCTURAL", "spend / impressions is undefined when the ad set had 0 impressions."),
    "notes": ("MISSING", "Free text. Fill in when something unusual happened that day."),
    "ad_id": ("MISSING", "Ad ID for the change event. Read it off Meta Ads Manager."),
    "confirmed_by": ("MISSING", "Who verified this change event."),
    "minutes_to_update": ("STRUCTURAL", "Blank when the CRM record was never updated after creation."),
    "updated_at": ("STRUCTURAL", "Blank when the CRM record was never updated after creation."),

    "ad_set_change_type": ("INFERRED", "No change log exists. Record real ad-set edits in changelog_ad_set."),
    "ad_set_change_recency": ("INFERRED", "Derived from ad_set_change_type; fills in once events are recorded."),
    "ad_set_change_today": ("INFERRED", "Derived from ad_set_change_type."),
    "ad_change_type": ("INFERRED", "No change log exists. Record real ad edits in changelog_ad."),
    "ad_change_recency": ("INFERRED", "Derived from ad_change_type; fills in once events are recorded."),
    "ad_change_today": ("INFERRED", "Derived from ad_change_type."),
}

# columns that are blank only because a row failed to join to an ad set x day
JOIN_DEPENDENT = [
    "campaign_name", "delivery_status", "adset_day_leads", "spend",
    "days_since_adset_started", "adset_start_censored", "frequency",
    "reach", "impressions", "messaging_conversations",
    "ad_set_budget", "ad_set_budget_type", "cpl", "cpm",
]


def apply_blank_policy(df, source_cols=("ad_set_change_source", "ad_change_source")):
    """Move inferred values into <col>_inferred and blank the primary column.

    Rows already marked source == 'confirmed' keep their value in the primary
    column -- those came from a human-maintained change log.
    """
    df = df.copy()
    pairs = [
        (("ad_set_change_type", "ad_set_change_recency", "ad_set_change_today"),
         "ad_set_change_source"),
        (("ad_change_type", "ad_change_recency", "ad_change_today"),
         "ad_change_source"),
    ]
    for cols, src in pairs:
        if src not in df.columns:
            continue
        inferred = df[src].eq("inferred")
        for c in cols:
            if c not in df.columns:
                continue
            df[c + "_inferred"] = df[c]
            df.loc[inferred, c] = pd.NA
        # the source column describes a value that is now blank; say so plainly
        df.loc[inferred, src] = "not_recorded"
    return df


def order_with_inferred(cols):
    """Insert each <col>_inferred immediately after its primary column."""
    out = []
    for c in cols:
        out.append(c)
        if c in INFERRED_COLS:
            out.append(c + "_inferred")
    return out
