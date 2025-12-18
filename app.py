# app.py
# Equity Vesting Time Tracker (Streamlit)
# NOTE: Only change requested: Weekly goal = 12 hrs, Monthly vesting = 48 hrs

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date, timedelta
import time

import pandas as pd
import streamlit as st


# =========================
# CONFIG (ONLY CHANGES HERE)
# =========================
WEEKLY_GOAL_HRS = 12.0
MONTHLY_VESTING_HRS = 48.0
# =========================

APP_TITLE = "⏱️ Equity Vesting Time Tracker"
DB_PATH = "time_tracker.db"

# Team list (edit names as needed)
PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]

# Admin (who can see admin-only tabs)
ADMIN_NAME = "Drew"


# -------------------------
# Utilities
# -------------------------
def today_local() -> date:
    # Streamlit Cloud runs in UTC; for “good enough” behavior you were using a manual Today picker anyway.
    # We still default to user's selected "Today" in the sidebar.
    return date.today()


def week_start(d: date) -> date:
    # Monday start
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month_start(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def clamp_floor_zero(x: float) -> float:
    return max(0.0, float(x))


def fmt_hrs(x: float) -> str:
    return f"{clamp_floor_zero(x):.2f} hrs"


def fmt_hms(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                person TEXT NOT NULL,
                log_date TEXT NOT NULL,
                kind TEXT NOT NULL,              -- 'timer' or 'manual'
                delta_hours REAL NOT NULL,
                reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                person TEXT NOT NULL,
                log_date TEXT NOT NULL,
                delta_hours REAL NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )


def add_event(person: str, log_date: date, kind: str, delta_hours: float, reason: str | None = None):
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (created_at, person, log_date, kind, delta_hours, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.utcnow().isoformat(), person, log_date.isoformat(), kind, float(delta_hours), reason),
        )


def add_notification(person: str, log_date: date, delta_hours: float, reason: str):
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO notifications (created_at, person, log_date, delta_hours, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (datetime.utcnow().isoformat(), person, log_date.isoformat(), float(delta_hours), reason),
        )


def fetch_events() -> pd.DataFrame:
    with db_conn() as conn:
        df = pd.read_sql_query(
            "SELECT id, created_at, person, log_date, kind, delta_hours, reason FROM events ORDER BY created_at DESC",
            conn,
        )
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df


def fetch_notifications(limit: int = 200) -> pd.DataFrame:
    with db_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT id, created_at, person, log_date, delta_hours, reason
            FROM notifications
            ORDER BY created_at DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df


def sum_hours(df: pd.DataFrame, person: str, start: date, end_exclusive: date) -> float:
    if df.empty:
        return 0.0
    m = (df["person"] == person) & (df["log_date"] >= start) & (df["log_date"] < end_exclusive)
    return float(df.loc[m, "delta_hours"].sum())


def all_people_hours(df: pd.DataFrame, start: date, end_exclusive: date) -> pd.DataFrame:
    rows = []
    for p in PEOPLE:
        rows.append({"person": p, "hours": sum_hours(df, p, start, end_exclusive)})
    out = pd.DataFrame(rows).sort_values("hours", ascending=False).reset_index(drop=True)
    out["hours"] = out["hours"].apply(clamp_floor_zero)
    return out


def month_vesting_status(df: pd.DataFrame, month: date) -> pd.DataFrame:
    ms = month_start(month)
    me = next_month_start(month)
    rows = []
    for p in PEOPLE:
        hrs = clamp_floor_zero(sum_hours(df, p, ms, me))
        vested = hrs >= MONTHLY_VESTING_HRS
        rows.append({"person": p, "month": ms.isoformat(), "hours": hrs, "vested": vested})
    out = pd.DataFrame(rows).sort_values(["vested", "hours"], ascending=[False, False]).reset_index(drop=True)
    return out


def safe_manual_delta(df: pd.DataFrame, person: str, log_date: date, requested_delta: float) -> float:
    """
    Prevent totals for that person on that day from going below 0.
    """
    if df.empty:
        current_day = 0.0
    else:
        m = (df["person"] == person) & (df["log_date"] == log_date)
        current_day = float(df.loc[m, "delta_hours"].sum())

    # If requested is negative, clamp so current_day + delta >= 0
    if requested_delta < 0:
        return max(requested_delta, -current_day)
    return requested_delta


# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="Equity Vesting Time Tracker", layout="wide")
init_db()

st.markdown(f"# {APP_TITLE}")
st.caption(f"Clock in / Clock out • Weekly goal **{WEEKLY_GOAL_HRS:g} hrs** • Monthly vesting **{MONTHLY_VESTING_HRS:g} hrs**")

# Sidebar controls
with st.sidebar:
    st.subheader("Controls")
    who = st.selectbox("Who are you?", PEOPLE, index=0)
    selected_day = st.date_input("Today", value=today_local())
    st.markdown("---")
    st.write("Everyone can see everyone (leaderboard enabled).")

# Load data
events_df = fetch_events()

# Session state for timer
if "running" not in st.session_state:
    st.session_state.running = False
if "start_ts" not in st.session_state:
    st.session_state.start_ts = None
if "elapsed_before" not in st.session_state:
    st.session_state.elapsed_before = 0.0
if "manual_reason" not in st.session_state:
    st.session_state.manual_reason = ""
if "manual_hours" not in st.session_state:
    st.session_state.manual_hours = 0.0

# Tabs
tabs = ["⏱️ Clock In", "🏆 Leaderboard", "🧾 Manual Time"]
if who == ADMIN_NAME:
    tabs += ["🔔 Notifications (Admin)", "📋 Logs (Admin)", "✅ Vesting Report (Admin)"]

tab_objs = st.tabs(tabs)

# -------------------------
# Clock In / Out
# -------------------------
with tab_objs[0]:
    st.subheader("Clock In / Clock Out")

    ws = week_start(selected_day)
    we = ws + timedelta(days=7)
    ms = month_start(selected_day)
    me = next_month_start(selected_day)

    week_hrs = clamp_floor_zero(sum_hours(events_df, who, ws, we))
    month_hrs = clamp_floor_zero(sum_hours(events_df, who, ms, me))

    colA, colB, colC = st.columns([2, 1, 1])

    with colA:
        status_box = st.empty()
        timer_box = st.empty()
        btn_box = st.empty()

        def render_status():
            if st.session_state.running:
                status_box.success(f"🟢 CLOCKED IN — Timer running (saving to {selected_day.isoformat()})")
            else:
                status_box.info("⚪ CLOCKED OUT")

        def current_elapsed_seconds() -> float:
            if not st.session_state.running or st.session_state.start_ts is None:
                return st.session_state.elapsed_before
            return st.session_state.elapsed_before + (time.time() - st.session_state.start_ts)

        render_status()
        timer_box.markdown(f"## {fmt_hms(current_elapsed_seconds())}")

        if st.session_state.running:
            if btn_box.button("⏸️ Clock Out (Save)", use_container_width=True):
                # compute elapsed hours since last clock-in and save
                elapsed_sec = current_elapsed_seconds()
                delta_hours = max(0.0, elapsed_sec / 3600.0)

                # Save event
                add_event(who, selected_day, "timer", delta_hours, reason="Clocked session")

                # Reset timer state
                st.session_state.running = False
                st.session_state.start_ts = None
                st.session_state.elapsed_before = 0.0

                st.success(f"Saved {delta_hours:.4f} hrs")
                st.rerun()
        else:
            if btn_box.button("▶️ Clock In", use_container_width=True):
                st.session_state.running = True
                st.session_state.start_ts = time.time()
                st.session_state.elapsed_before = 0.0
                st.rerun()

        # Lightweight “live” feeling without spamming the header: only rerun while running.
        if st.session_state.running:
            time.sleep(0.5)
            st.rerun()

    with colB:
        st.markdown("**This week**")
        st.markdown(f"### {fmt_hrs(week_hrs)}")
        st.progress(min(week_hrs / WEEKLY_GOAL_HRS, 1.0) if WEEKLY_GOAL_HRS > 0 else 0.0)
        if week_hrs >= WEEKLY_GOAL_HRS:
            st.success("✅ On track this week")
        else:
            st.warning(f"Goal: {WEEKLY_GOAL_HRS:g} hrs")

    with colC:
        st.markdown("**This month**")
        st.markdown(f"### {fmt_hrs(month_hrs)}")
        st.progress(min(month_hrs / MONTHLY_VESTING_HRS, 1.0) if MONTHLY_VESTING_HRS > 0 else 0.0)
        if month_hrs >= MONTHLY_VESTING_HRS:
            st.success("🎉 Vested (month)")
        else:
            st.info(f"Target: {MONTHLY_VESTING_HRS:g} hrs")

# -------------------------
# Leaderboard
# -------------------------
with tab_objs[1]:
    st.subheader("Leaderboard")

    ws = week_start(selected_day)
    we = ws + timedelta(days=7)
    ms = month_start(selected_day)
    me = next_month_start(selected_day)

    st.caption(f"Week starting {ws.isoformat()} • Goal {WEEKLY_GOAL_HRS:g} hrs")
    weekly = all_people_hours(events_df, ws, we)

    for i, row in weekly.iterrows():
        p = row["person"]
        hrs = float(row["hours"])
        st.markdown(f"**#{i+1} {p}** — {hrs:.2f} hrs")
        st.progress(min(hrs / WEEKLY_GOAL_HRS, 1.0) if WEEKLY_GOAL_HRS > 0 else 0.0)

    st.markdown("---")
    st.caption(f"Month starting {ms.isoformat()} • Vesting {MONTHLY_VESTING_HRS:g} hrs")
    monthly = all_people_hours(events_df, ms, me)
    for _, row in monthly.iterrows():
        p = row["person"]
        hrs = float(row["hours"])
        vested = hrs >= MONTHLY_VESTING_HRS
        st.markdown(f"**{p}** — {hrs:.2f} hrs • {'🎉 Vested' if vested else '⏳ In progress'}")
        st.progress(min(hrs / MONTHLY_VESTING_HRS, 1.0) if MONTHLY_VESTING_HRS > 0 else 0.0)

# -------------------------
# Manual Time
# -------------------------
with tab_objs[2]:
    st.subheader("Manual Time (reason required)")
    st.caption("Use this if you forgot to clock in, or to correct an overrun. Totals will never be allowed to go below 0 for a given day.")

    c1, c2 = st.columns([2, 1])

    with c1:
        reason = st.text_input("Reason (required)", key="manual_reason", placeholder="e.g., Forgot to clock in for meeting")
    with c2:
        hrs = st.number_input("Hours (can be negative)", value=float(st.session_state.manual_hours), step=0.25, format="%.2f", key="manual_hours")

    if st.button("Save manual hours", use_container_width=True):
        if not reason.strip():
            st.error("Reason is required.")
        else:
            # clamp to avoid going below 0 on that date
            safe_delta = safe_manual_delta(events_df, who, selected_day, float(hrs))

            # If user tried to go below zero, safe_delta will be less negative than requested.
            if safe_delta != float(hrs):
                st.warning("Adjusted your entry to prevent total hours for that day from going below 0.")

            # Save event + notification (for admin)
            add_event(who, selected_day, "manual", safe_delta, reason=reason.strip())
            add_notification(who, selected_day, safe_delta, reason.strip())

            # Clear inputs to prevent double-submit
            st.session_state.manual_reason = ""
            st.session_state.manual_hours = 0.0

            st.success("Saved.")
            st.rerun()

# -------------------------
# Admin: Notifications
# -------------------------
idx = 3
if who == ADMIN_NAME:
    with tab_objs[idx]:
        st.subheader("Notifications (Admin)")
        st.caption("Manual adjustments (always requires a reason).")

        notif = fetch_notifications(limit=500)
        if notif.empty:
            st.info("No notifications yet.")
        else:
            st.dataframe(
                notif[["created_at", "person", "log_date", "delta_hours", "reason"]],
                use_container_width=True,
                hide_index=True,
            )

    idx += 1
    # -------------------------
    # Admin: Logs
    # -------------------------
    with tab_objs[idx]:
        st.subheader("Logs (Admin)")
        st.caption("All events (timer + manual).")
        if events_df.empty:
            st.info("No events yet.")
        else:
            st.dataframe(
                events_df[["created_at", "person", "log_date", "kind", "delta_hours", "reason"]],
                use_container_width=True,
                hide_index=True,
            )

    idx += 1
    # -------------------------
    # Admin: Vesting Report
    # -------------------------
    with tab_objs[idx]:
        st.subheader("Vesting Report (Admin)")
        st.caption(f"Monthly vesting threshold: {MONTHLY_VESTING_HRS:g} hrs")

        report_month = st.date_input("Report month", value=month_start(selected_day))
        rep = month_vesting_status(events_df, report_month)

        # Pretty summary
        for _, r in rep.iterrows():
            if r["vested"]:
                st.success(f"✅ {r['person']} — {r['hours']:.2f} hrs (VESTED)")
            else:
                st.warning(f"⏳ {r['person']} — {r['hours']:.2f} hrs (NOT vested)")
        st.markdown("---")
        st.dataframe(rep, use_container_width=True, hide_index=True)
