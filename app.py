"""Streamlit dashboard for Garmin health metrics.

Fetches data from a private GitHub repo using a read-only token.
"""

import json
import streamlit as st
import plotly.graph_objects as go
import requests
from datetime import date, timedelta


# --- Config ---
REPO = "jrjrjr4/garmin-health"
DATA_PATH = "data"

TARGETS = {
    "hrv": {"green": 50, "yellow": 30},
    "resting_hr": {"green": 60, "yellow": 70},
    "sleep_score": {"green": 80, "yellow": 60},
    "deep_sleep_pct": {"green": 20, "yellow": 15},
    "rem_sleep_pct": {"green": 20, "yellow": 15},
    "sleep_hours": {"green": 7.0, "yellow": 6.0},
    "vo2_max": {"green": 42, "yellow": 35},
    "zone2_weekly_min": {"green": 150, "yellow": 90},
}


# --- Auth ---
def check_password():
    if st.session_state.get("authenticated"):
        return True
    password = st.text_input("Password", type="password")
    if password:
        if password == st.secrets.get("dashboard_password", ""):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False


# --- Data fetching from private repo ---
@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_data_files(days_back: int) -> list[dict]:
    """Fetch JSON data files from the private GitHub repo.

    Lists all files in data/ first (single API call), then fetches
    only the ones in our date range. Uses GitHub's raw content API
    for faster downloads.
    """
    token = st.secrets.get("github_token", "")
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    session = requests.Session()
    session.headers.update(headers)
    session.timeout = 15

    today = date.today()
    cutoff = today - timedelta(days=days_back)

    # List all files in data/ (single API call)
    url = f"https://api.github.com/repos/{REPO}/contents/{DATA_PATH}"
    resp = session.get(url)
    if resp.status_code != 200:
        return []

    files = resp.json()
    if not isinstance(files, list):
        return []

    # Filter to date range and sort
    target_files = []
    for f in files:
        name = f.get("name", "")
        if not name.endswith(".json"):
            continue
        file_date_str = name.replace(".json", "")
        try:
            file_date = date.fromisoformat(file_date_str)
            if cutoff <= file_date <= today:
                target_files.append((file_date, f.get("download_url", "")))
        except ValueError:
            continue

    target_files.sort(key=lambda x: x[0])

    # Fetch each file's content (raw URL is faster than contents API)
    results = []
    raw_headers = {"Authorization": f"token {token}"}
    for _, download_url in target_files:
        try:
            resp = session.get(download_url, headers=raw_headers, timeout=10)
            if resp.status_code == 200:
                results.append(resp.json())
        except (requests.RequestException, json.JSONDecodeError):
            continue

    return results


# --- Metrics extraction (self-contained, no imports from private repo) ---
def extract_metrics(data: dict) -> dict:
    sleep = data.get("sleep", {})
    dto = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
    scores = dto.get("sleepScores", {}) if isinstance(dto, dict) else {}

    # HRV
    hrv = None
    hrv_data = data.get("hrv", {})
    if isinstance(hrv_data, dict) and "error" not in hrv_data:
        summary = hrv_data.get("hrvSummary", hrv_data)
        for k in ("lastNightAvg", "weeklyAvg", "lastNight5MinHigh"):
            if summary.get(k):
                hrv = float(summary[k])
                break
    if hrv is None and isinstance(sleep, dict) and sleep.get("avgOvernightHrv"):
        hrv = float(sleep["avgOvernightHrv"])

    # Resting HR
    resting_hr = None
    rhr = data.get("resting_hr", {})
    if isinstance(rhr, dict) and "error" not in rhr:
        for k in ("restingHeartRate", "value"):
            if rhr.get(k):
                resting_hr = int(rhr[k])
                break
        if resting_hr is None:
            metrics_map = rhr.get("allMetrics", {}).get("metricsMap", {})
            rhr_list = metrics_map.get("WELLNESS_RESTING_HEART_RATE", [{}])
            if rhr_list and isinstance(rhr_list, list) and rhr_list[0].get("value"):
                resting_hr = int(rhr_list[0]["value"])

    # Sleep
    sleep_score = None
    overall = scores.get("overall", {})
    if isinstance(overall, dict) and overall.get("value"):
        sleep_score = int(overall["value"])

    deep_pct = None
    deep_data = scores.get("deepPercentage", {})
    if isinstance(deep_data, dict) and deep_data.get("value") is not None:
        deep_pct = float(deep_data["value"])
    elif dto.get("sleepTimeSeconds") and dto.get("deepSleepSeconds"):
        deep_pct = round(dto["deepSleepSeconds"] / dto["sleepTimeSeconds"] * 100, 1)

    rem_pct = None
    rem_data = scores.get("remPercentage", {})
    if isinstance(rem_data, dict) and rem_data.get("value") is not None:
        rem_pct = float(rem_data["value"])
    elif dto.get("sleepTimeSeconds") and dto.get("remSleepSeconds"):
        rem_pct = round(dto["remSleepSeconds"] / dto["sleepTimeSeconds"] * 100, 1)

    sleep_hours = None
    secs = dto.get("sleepTimeSeconds")
    if secs:
        sleep_hours = round(secs / 3600, 1)

    # VO2 Max
    vo2 = None
    mm = data.get("max_metrics")
    if isinstance(mm, dict) and "error" not in mm:
        vo2 = mm.get("generic", {}).get("vo2MaxValue")
        if vo2:
            vo2 = float(vo2)
    elif isinstance(mm, list):
        for entry in mm:
            v = entry.get("generic", {}).get("vo2MaxValue")
            if v:
                vo2 = float(v)
                break

    # Zone 2
    zone2 = 0.0
    activities = data.get("activities", [])
    if isinstance(activities, list):
        for act in activities:
            if isinstance(act, dict) and "error" not in act:
                hr_zones = act.get("heartRateZones") or []
                for zone in hr_zones if isinstance(hr_zones, list) else []:
                    zn = zone.get("zoneNumber") or zone.get("zone")
                    if zn == 2:
                        zone2 += zone.get("secsInZone", 0) / 60
                if not hr_zones:
                    act_type = act.get("activityType", {}).get("typeKey", "")
                    dur = act.get("duration", 0) or 0
                    if act_type in ("running", "cycling", "walking") and dur > 0:
                        zone2 += dur / 60

    return {
        "date": data.get("date"),
        "hrv": hrv,
        "resting_hr": resting_hr,
        "sleep_score": sleep_score,
        "deep_sleep_pct": deep_pct,
        "rem_sleep_pct": rem_pct,
        "sleep_hours": sleep_hours,
        "vo2_max": vo2,
        "zone2_min": round(zone2, 1) if zone2 > 0 else 0.0,
    }


def score_metric(name, value):
    if value is None:
        return "grey"
    target = TARGETS.get(name)
    if not target:
        return "grey"
    if name == "resting_hr":
        if value <= target["green"]:
            return "green"
        return "yellow" if value <= target["yellow"] else "red"
    if value >= target["green"]:
        return "green"
    return "yellow" if value >= target["yellow"] else "red"


def rolling_average(values, window=7):
    result = []
    for i in range(len(values)):
        w = [v for v in values[max(0, i - window + 1):i + 1] if v is not None]
        result.append(sum(w) / len(w) if w else None)
    return result


# --- Page ---
st.set_page_config(page_title="Health Dashboard", page_icon="\U0001f4aa", layout="wide")

if not check_password():
    st.stop()

# --- Sidebar ---
st.sidebar.title("\U0001f4aa Health Dashboard")
days_back = st.sidebar.slider("Days to show", 7, 90, 30)

# --- Load data ---
with st.spinner("Loading data from Garmin..."):
    raw_data = fetch_data_files(days_back)

if not raw_data:
    st.warning("No data found. The sync may not have run yet!")
    st.stop()

all_metrics = [extract_metrics(d) for d in raw_data]
dates = [m["date"] for m in all_metrics]

# --- Today's summary ---
st.header("Today's Snapshot")

latest = all_metrics[-1]
yesterday = all_metrics[-2] if len(all_metrics) > 1 else None

cols = st.columns(5)
metric_display = [
    ("hrv", "HRV", "ms", True),
    ("resting_hr", "Resting HR", "bpm", False),
    ("sleep_score", "Sleep", "/100", True),
    ("vo2_max", "VO2 Max", "", True),
]

for i, (key, label, unit, higher_better) in enumerate(metric_display):
    val = latest.get(key)
    delta = None
    if yesterday and val is not None and yesterday.get(key) is not None:
        delta = val - yesterday[key]
    with cols[i]:
        val_str = f"{val}{unit}" if val is not None else "\u2014"
        delta_str = f"{delta:+.1f}" if delta is not None else None
        st.metric(label=label, value=val_str, delta=delta_str,
                  delta_color="normal" if higher_better else "inverse")

# Zone 2 weekly
with cols[4]:
    week_z2 = sum(m.get("zone2_min", 0) or 0 for m in all_metrics[-7:])
    st.metric(label="Zone 2 (Week)", value=f"{week_z2:.0f}/150 min")


# --- Charts ---
st.header("Trends")


def make_chart(title, key, color, target=None, invert=False):
    vals = [m.get(key) for m in all_metrics]
    smoothed = rolling_average(vals)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=vals, mode="markers",
                             marker=dict(color=color, size=5, opacity=0.4), name="Daily"))
    fig.add_trace(go.Scatter(x=dates, y=smoothed, mode="lines",
                             line=dict(color=color, width=3), name="7-day avg"))
    if target:
        fig.add_hline(y=target, line_dash="dot", line_color=color, opacity=0.3,
                      annotation_text=f"Target: {target}")
    fig.update_layout(title=title, template="plotly_dark", height=300,
                      margin=dict(l=20, r=20, t=40, b=20), showlegend=False,
                      yaxis=dict(autorange="reversed" if invert else True))
    return fig


col1, col2 = st.columns(2)
with col1:
    st.plotly_chart(make_chart("HRV (ms)", "hrv", "#00d4ff", TARGETS["hrv"]["green"]), use_container_width=True)
    st.plotly_chart(make_chart("Sleep Score", "sleep_score", "#a78bfa", TARGETS["sleep_score"]["green"]), use_container_width=True)
with col2:
    st.plotly_chart(make_chart("Resting HR (bpm)", "resting_hr", "#ff6b6b", TARGETS["resting_hr"]["green"], invert=True), use_container_width=True)
    st.plotly_chart(make_chart("VO2 Max", "vo2_max", "#4ade80", TARGETS["vo2_max"]["green"]), use_container_width=True)

# Sleep breakdown
st.header("Sleep Breakdown")
sc1, sc2, sc3 = st.columns(3)
with sc1:
    st.plotly_chart(make_chart("Sleep Duration (h)", "sleep_hours", "#818cf8", TARGETS["sleep_hours"]["green"]), use_container_width=True)
with sc2:
    st.plotly_chart(make_chart("Deep Sleep %", "deep_sleep_pct", "#6366f1", TARGETS["deep_sleep_pct"]["green"]), use_container_width=True)
with sc3:
    st.plotly_chart(make_chart("REM Sleep %", "rem_sleep_pct", "#8b5cf6", TARGETS["rem_sleep_pct"]["green"]), use_container_width=True)

# Zone 2
st.header("Zone 2 Training (Weekly)")
z2_weekly, z2_dates = [], []
for i in range(0, len(all_metrics), 7):
    end = min(i + 6, len(all_metrics) - 1)
    total = sum(m.get("zone2_min", 0) or 0 for m in all_metrics[i:end + 1])
    z2_weekly.append(total)
    z2_dates.append(all_metrics[end]["date"])

if z2_weekly:
    fig = go.Figure()
    colors = ["#4ade80" if v >= 150 else "#facc15" if v >= 90 else "#f87171" for v in z2_weekly]
    fig.add_trace(go.Bar(x=z2_dates, y=z2_weekly, marker_color=colors))
    fig.add_hline(y=150, line_dash="dot", line_color="#4ade80", opacity=0.5, annotation_text="Target: 150 min")
    fig.update_layout(title="Weekly Zone 2 Minutes", template="plotly_dark", height=300, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

# Raw data
with st.expander("Raw data"):
    import pandas as pd
    st.dataframe(pd.DataFrame(all_metrics), use_container_width=True)
