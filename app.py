import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date, timedelta
from streamlit_autorefresh import st_autorefresh

# ----------------------------
# Settings
# ----------------------------
DB_PATH = "time_tracker.db"
PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]  # edit
WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0  # 0=Mon, 6=Sun

ACCESS_CODES = {
    "Drew": "1111",
    "Carson": "2222",
    "Kaden": "3333",
    "Chandler": "4444",
}

# ----------------------------
# DB helpers
# ----------------------------
def conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    c = conn()
    cur = c.cursor()

    # Log entries (includes timer stops + manual adjustments)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            hours REAL NOT NULL,
            notes TEXT,
            source TEXT NOT NULL
        )
    """)

    # One row per person for timer state
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at TEXT,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT
        )
    """)

    # Ensure timer row exists for each person
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, date.today().isoformat()))

    c.commit()
    c.close()

def fetch_logs():
    c = conn()
    df = pd.read_sql_query("SELECT log_date, person, hours, notes, source FROM logs", c)
    c.close()
    if df.empty:
        return df
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df

def fetch_timer(person: str):
    c = conn()
    cur = c.cursor()
    cur.execute("SELECT is_running, started_at, accumulated_seconds, active_date FROM timers WHERE person=?", (person,))
    row = cur.fetchone()
    c.close()
    return row  # (is_running, started_at, accumulated_seconds, active_date)

def update_timer(person: str, is_running: int, started_at: str, accumulated_seconds: int, active_date: str):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        UPDATE timers
        SET is_running=?, started_at=?, accumulated_seconds=?, active_date=?
        WHERE person=?
    """, (is_running, started_at, accumulated_seconds, active_date, person))
    c.commit()
    c.close()

def add_log(log_date: date, person: str, hours: float, notes: str, source: str):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO logs (log_date, person, hours, notes, source)
        VALUES (?, ?, ?, ?, ?)
    """, (log_date.isoformat(), person, float(hours), notes, source))
    c.commit()
    c.close()

# ----------------------------
# Time helpers
# ----------------------------
def week_start(d: date) -> date:
    weekday = d.weekday()
    delta = (weekday - WEEK_START) % 7
    return d - timedelta(days=delta)

def month_start(d: date) -> date:
    return d.replace(day=1)

def month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)

def fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def now_utc_str():
    return datetime.utcnow().isoformat()

def parse_dt(s: str):
    return datetime.fromisoformat(s)

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Equity Time Tracker", layout="wide")
init_db()

st.markdown("""
<style>
.block-container { padding-top: 1.25rem; }
.cool-card {
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 16px;
  padding: 16px;
  background: rgba(255,255,255,0.03);
}
.big-timer {
  font-size: 44px;
  font-weight: 800;
  letter-spacing: 2px;
}
.small-muted { opacity: 0.8; font-size: 13px; }
</style>
""", unsafe_allow_html=True)

st.title("⏱️ Equity Vesting Time Tracker")
st.caption("Goal: 10 hrs/week • Vesting: 40 hrs/month")

with st.sidebar:
    st.subheader("Login")
    person = st.selectbox("Who are you?", PEOPLE)
    code = st.text_input("Access code", type="password")
    logged_in = (ACCESS_CODES.get(person) == code)
    today = st.date_input("Today", value=date.today())
    if not logged_in:
        st.warning("Enter your code to start/stop timer and log time.")

tabs = st.tabs(["🚀 Timer", "📈 Dashboard", "✍️ Adjust Time", "🧾 Logs"])

# ----------------------------
# Timer tab
# ----------------------------
with tabs[0]:
    st.subheader("Timer")
    if not logged_in:
        st.info("Log in (sidebar) to use the timer.")
    else:
        is_running, started_at, acc_sec, active_date = fetch_timer(person)

        # If day changed, we "roll" timer to today but keep accumulated (optional).
        # For simplicity, we reset accumulated when day changes IF not running.
        if active_date != today.isoformat() and int(is_running) == 0:
            update_timer(person, 0, None, 0, today.isoformat())
            is_running, started_at, acc_sec, active_date = fetch_timer(person)

        # Compute displayed seconds
        current_seconds = int(acc_sec)
        if int(is_running) == 1 and started_at:
            delta = datetime.utcnow() - parse_dt(started_at)
            current_seconds += int(delta.total_seconds())

        # Auto refresh when running (makes it tick)
        if int(is_running) == 1:
            st_autorefresh(interval=1000, key=f"tick_{person}")

        colA, colB, colC = st.columns([2,1,1])

        with colA:
            st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
            st.markdown(f"<div class='small-muted'>Tracking for <b>{person}</b> • Date: <b>{today}</b></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='big-timer'>{fmt_hms(current_seconds)}</div>", unsafe_allow_html=True)
            st.markdown("<div class='small-muted'>Start → it counts. Stop → it saves to today.</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        with colB:
            st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
            st.write("Controls")
            if int(is_running) == 0:
                if st.button("▶️ Start", use_container_width=True):
                    update_timer(person, 1, now_utc_str(), int(acc_sec), today.isoformat())
                    st.rerun()
            else:
                if st.button("⏸️ Stop & Save", use_container_width=True):
                    # finalize seconds -> hours -> log
                    final_seconds = int(acc_sec) + int((datetime.utcnow() - parse_dt(started_at)).total_seconds())
                    hours = final_seconds / 3600.0
                    add_log(today, person, hours, notes="Timer session", source="timer")
                    # reset timer
                    update_timer(person, 0, None, 0, today.isoformat())
                    st.success(f"Saved {hours:.2f} hours for {today}.")
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        with colC:
            st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
            st.write("Quick add")
            quick = st.number_input("Add hours (manual)", 0.0, 24.0, 0.0, 0.25)
            if st.button("➕ Add", use_container_width=True):
                if quick > 0:
                    add_log(today, person, quick, notes="Manual add", source="manual")
                    st.success(f"Added {quick:.2f} hours for {today}.")
                    st.rerun()
                else:
                    st.warning("Enter > 0 hours.")
            st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# Dashboard tab
# ----------------------------
with tabs[1]:
    st.subheader("Dashboard")

    df = fetch_logs()
    if df.empty:
        st.info("No logs yet.")
    else:
        df["week_start"] = df["log_date"].apply(week_start)
        df["month_start"] = df["log_date"].apply(month_start)

        # totals for selected person (cleaner UX)
        mine = df[df["person"] == person].copy()

        this_week = week_start(today)
        this_month = month_start(today)

        week_hours = float(mine.loc[mine["week_start"] == this_week, "hours"].sum())
        month_hours = float(mine.loc[mine["month_start"] == this_month, "hours"].sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("This week", f"{week_hours:.2f} hrs", f"{week_hours - WEEKLY_TARGET:+.2f} vs 10")
        c2.progress(min(1.0, week_hours / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)
        c3.metric("This month", f"{month_hours:.2f} hrs", f"{month_hours - MONTHLY_TARGET:+.2f} vs 40")
        c4.progress(min(1.0, month_hours / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

        st.divider()
        st.write("Week-by-week (you)")
        wk = (
            mine.groupby("week_start", as_index=False)["hours"]
            .sum()
            .sort_values("week_start", ascending=False)
        )
        wk["status"] = wk["hours"].apply(lambda x: "✅ On track" if x >= WEEKLY_TARGET else "⚠️ Behind")
        st.dataframe(wk.rename(columns={"week_start": "week", "hours": "hours"}), use_container_width=True)

        st.write("Month-by-month (you)")
        mo = (
            mine.groupby("month_start", as_index=False)["hours"]
            .sum()
            .sort_values("month_start", ascending=False)
        )
        mo["vested"] = mo["hours"].apply(lambda x: "✅ Vested" if x >= MONTHLY_TARGET else "❌ Not vested")
        st.dataframe(mo.rename(columns={"month_start": "month", "hours": "hours"}), use_container_width=True)

# ----------------------------
# Adjust tab
# ----------------------------
with tabs[2]:
    st.subheader("Adjust time (fix mistakes)")
    if not logged_in:
        st.info("Log in to adjust time.")
    else:
        col1, col2 = st.columns([1,2])
        with col1:
            adj_date = st.date_input("Date to adjust", value=today, key="adj_date")
            minutes = st.number_input("Minutes (+ add / - subtract)", min_value=-600, max_value=600, value=0, step=5)
        with col2:
            reason = st.text_input("Reason (ex: forgot to clock in, left running)", value="Adjustment")

        if st.button("Apply adjustment", use_container_width=True):
            if minutes == 0:
                st.warning("Enter non-zero minutes.")
            else:
                hours = minutes / 60.0
                add_log(adj_date, person, hours, notes=reason, source="adjustment")
                st.success(f"Applied {hours:+.2f} hours on {adj_date}.")
                st.rerun()

# ----------------------------
# Logs tab
# ----------------------------
with tabs[3]:
    st.subheader("Logs")
    df = fetch_logs()
    if df.empty:
        st.info("No logs yet.")
    else:
        # show mine by default; you can change this later to admin view
        mine = df[df["person"] == person].sort_values("log_date", ascending=False)
        st.dataframe(mine, use_container_width=True)
