import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date, timedelta

# ----------------------------
# Settings
# ----------------------------
DB_PATH = "time_tracker.db"
PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]
WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0  # Monday

# ----------------------------
# Database helpers
# ----------------------------
def conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def safe_table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

def init_db():
    c = conn()
    cur = c.cursor()

    # Logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT,
            person TEXT,
            hours REAL,
            notes TEXT,
            source TEXT,
            created_at TEXT
        )
    """)

    # Timers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER,
            started_at TEXT,
            accumulated_seconds INTEGER,
            active_date TEXT
        )
    """)

    # Notifications (safe create)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            person TEXT,
            log_date TEXT,
            delta_hours REAL,
            reason TEXT,
            kind TEXT
        )
    """)

    # Ensure timer rows
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers
            VALUES (?, 0, NULL, 0, ?)
        """, (p, date.today().isoformat()))

    c.commit()
    c.close()

def add_log(d, p, h, notes, source):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO logs VALUES (NULL,?,?,?,?,?,?)
    """, (d.isoformat(), p, h, notes, source, datetime.utcnow().isoformat()))
    c.commit()
    c.close()

def add_notification(p, d, dh, r, k):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO notifications VALUES (NULL,?,?,?,?,?,?)
    """, (datetime.utcnow().isoformat(), p, d.isoformat(), dh, r, k))
    c.commit()
    c.close()

def fetch_logs():
    c = conn()
    df = pd.read_sql_query("SELECT * FROM logs", c)
    c.close()
    if not df.empty:
        df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df

def fetch_notifications():
    c = conn()
    cur = c.cursor()
    if not safe_table_exists(cur, "notifications"):
        c.close()
        return pd.DataFrame()
    df = pd.read_sql_query("SELECT * FROM notifications ORDER BY created_at DESC", c)
    c.close()
    return df

def fetch_timer(p):
    c = conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM timers WHERE person=?", (p,))
    row = cur.fetchone()
    c.close()
    return row

def update_timer(p, r, s, a, d):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        UPDATE timers SET is_running=?, started_at=?, accumulated_seconds=?, active_date=?
        WHERE person=?
    """, (r, s, a, d, p))
    c.commit()
    c.close()

# ----------------------------
# Time helpers
# ----------------------------
def week_start(d):
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)

def fmt(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ----------------------------
# App
# ----------------------------
st.set_page_config("Equity Time Tracker", layout="wide")
init_db()

st.title("⏱️ Equity Vesting Time Tracker")
st.caption("Start / Stop timer • Weekly goal 10 hrs • Monthly vesting 40 hrs")

with st.sidebar:
    person = st.selectbox("Who are you?", PEOPLE)
    today = st.date_input("Today", date.today())
    st.divider()
    st.write("Everyone can see everyone")

tabs = st.tabs(["🚀 Timer", "🏆 Leaderboard", "✍️ Adjust", "🔔 Notifications", "🧾 Logs"])

# ----------------------------
# Timer
# ----------------------------
with tabs[0]:
    _, is_running, started_at, acc, active_date = fetch_timer(person)

    if active_date != today.isoformat() and not is_running:
        update_timer(person, 0, None, 0, today.isoformat())
        acc = 0

    seconds = acc
    if is_running:
        seconds += int((datetime.utcnow() - datetime.fromisoformat(started_at)).total_seconds())

    if is_running:
        st.success("🟢 CLOCKED IN — TIMER RUNNING")
        st.markdown("⏳ **Time is actively counting…**")
    else:
        st.info("⚪ CLOCKED OUT")

    st.markdown(f"## {fmt(seconds)}")

    c1, c2 = st.columns(2)
    with c1:
        if not is_running and st.button("▶️ Start"):
            update_timer(person, 1, datetime.utcnow().isoformat(), acc, today.isoformat())
            st.experimental_rerun()
    with c2:
        if is_running and st.button("⏸️ Stop & Save"):
            hours = seconds / 3600
            add_log(today, person, hours, "Timer session", "timer")
            update_timer(person, 0, None, 0, today.isoformat())
            st.success(f"Saved {hours:.2f} hrs")
            st.experimental_rerun()

# ----------------------------
# Leaderboard
# ----------------------------
with tabs[1]:
    df = fetch_logs()
    if df.empty:
        st.info("No data yet")
    else:
        df["week"] = df["log_date"].apply(week_start)
        wk = df[df["week"] == week_start(today)].groupby("person")["hours"].sum()
        board = pd.DataFrame({"Hours": wk}).reindex(PEOPLE).fillna(0)
        st.dataframe(board)

# ----------------------------
# Adjust
# ----------------------------
with tabs[2]:
    mins = st.number_input("Minutes (+ / -)", -600, 600, 0, 5)
    reason = st.text_input("Reason (required)")
    if st.button("Apply"):
        if mins == 0 or not reason:
            st.warning("Minutes ≠ 0 and reason required")
        else:
            hrs = mins / 60
            add_log(today, person, hrs, reason, "adjustment")
            add_notification(person, today, hrs, reason, "adjustment")
            st.success("Adjustment saved")

# ----------------------------
# Notifications
# ----------------------------
with tabs[3]:
    n = fetch_notifications()
    if n.empty:
        st.info("No notifications")
    else:
        st.dataframe(n)

# ----------------------------
# Logs
# ----------------------------
with tabs[4]:
    st.dataframe(fetch_logs())
