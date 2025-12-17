import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date, timedelta

# ----------------------------
# Settings
# ----------------------------
DB_PATH = "time_tracker.db"

PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]  # edit names
WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0  # 0=Mon, 6=Sun

ADMIN_NAME = "Drew"  # who you are (for admin-style views)

# ----------------------------
# DB helpers
# ----------------------------
def conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def col_exists(cur, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols

def init_db():
    c = conn()
    cur = c.cursor()

    # logs table (schema may evolve)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            hours REAL NOT NULL,
            notes TEXT
        )
    """)

    # MIGRATIONS: add missing columns safely
    if not col_exists(cur, "logs", "source"):
        cur.execute("ALTER TABLE logs ADD COLUMN source TEXT")
    if not col_exists(cur, "logs", "created_at"):
        cur.execute("ALTER TABLE logs ADD COLUMN created_at TEXT")

    # timers table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at TEXT,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT
        )
    """)

    # notifications for manual adjustments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            person TEXT NOT NULL,
            log_date TEXT NOT NULL,
            delta_hours REAL NOT NULL,
            reason TEXT NOT NULL
        )
    """)

    # Ensure a timer row exists for each person
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, date.today().isoformat()))

    c.commit()
    c.close()

def now_utc_str():
    return datetime.utcnow().isoformat()

def parse_dt(s: str):
    return datetime.fromisoformat(s)

def add_log(log_date: date, person: str, hours: float, notes: str, source: str):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO logs (log_date, person, hours, notes, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (log_date.isoformat(), person, float(hours), notes, source, now_utc_str()))
    c.commit()
    c.close()

def add_notification(person: str, log_date: date, delta_hours: float, reason: str):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO notifications (created_at, person, log_date, delta_hours, reason)
        VALUES (?, ?, ?, ?, ?)
    """, (now_utc_str(), person, log_date.isoformat(), float(delta_hours), reason))
    c.commit()
    c.close()

def fetch_logs():
    c = conn()
    # explicitly select columns that exist (after migration they should)
    df = pd.read_sql_query(
        "SELECT log_date, person, hours, notes, source, created_at FROM logs",
        c
    )
    c.close()
    if df.empty:
        return df
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df

def fetch_notifications():
    c = conn()
    df = pd.read_sql_query(
        "SELECT created_at, person, log_date, delta_hours, reason FROM notifications ORDER BY created_at DESC",
        c
    )
    c.close()
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"])
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

# ----------------------------
# Time helpers
# ----------------------------
def week_start(d: date) -> date:
    weekday = d.weekday()
    delta = (weekday - WEEK_START) % 7
    return d - timedelta(days=delta)

def month_start(d: date) -> date:
    return d.replace(day=1)

def fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

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
.big { font-size: 40px; font-weight: 800; letter-spacing: 1px; }
.muted { opacity: 0.75; font-size: 13px; }
</style>
""", unsafe_allow_html=True)

st.title("⏱️ Equity Vesting Time Tracker")
st.caption("Start/Stop timer • Weekly goal 10 hrs • Monthly vesting 40 hrs")

with st.sidebar:
    st.subheader("Controls")
    person = st.selectbox("Who are you?", PEOPLE)
    today = st.date_input("Today", value=date.today())
    st.divider()
    st.write("Everyone can see everyone (leaderboard enabled).")

tabs = st.tabs(["🚀 Timer", "🏆 Leaderboard", "✍️ Adjust Time", "🔔 Notifications", "🧾 Logs"])

# ----------------------------
# Timer
# ----------------------------
with tabs[0]:
    st.subheader(f"Timer — {person}")

    is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # If date changed and not running, reset daily accumulated to 0 (simple rule)
    if active_date != today.isoformat() and int(is_running) == 0:
        update_timer(person, 0, None, 0, today.isoformat())
        is_running, started_at, acc_sec, active_date = fetch_timer(person)

    current_seconds = int(acc_sec)
    if int(is_running) == 1 and started_at:
        current_seconds += int((datetime.utcnow() - parse_dt(started_at)).total_seconds())

    c1, c2, c3 = st.columns([2,1,1])
    with c1:
        st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
        st.markdown(f"<div class='muted'>Date: <b>{today}</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='big'>{fmt_hms(current_seconds)}</div>", unsafe_allow_html=True)
        st.markdown("<div class='muted'>Start → counts • Stop → saves to today</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
        st.write("Timer")
        if int(is_running) == 0:
            if st.button("▶️ Start", use_container_width=True):
                update_timer(person, 1, now_utc_str(), int(acc_sec), today.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Stop & Save", use_container_width=True):
                final_seconds = int(acc_sec) + int((datetime.utcnow() - parse_dt(started_at)).total_seconds())
                hours = final_seconds / 3600.0
                add_log(today, person, hours, notes="Timer session", source="timer")
                update_timer(person, 0, None, 0, today.isoformat())
                st.success(f"Saved {hours:.2f} hours for {today}.")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with c3:
        st.markdown("<div class='cool-card'>", unsafe_allow_html=True)
        st.write("Quick add (manual)")
        quick = st.number_input("Hours", 0.0, 24.0, 0.0, 0.25)
        if st.button("➕ Add", use_container_width=True):
            if quick <= 0:
                st.warning("Enter > 0 hours.")
            else:
                add_log(today, person, quick, notes="Manual add", source="manual")
                st.success(f"Added {quick:.2f} hours.")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# Leaderboard
# ----------------------------
with tabs[1]:
    st.subheader("Leaderboard")

    df = fetch_logs()
    if df.empty:
        st.info("No logs yet.")
    else:
        df["week_start"] = df["log_date"].apply(week_start)
        df["month_start"] = df["log_date"].apply(month_start)

        wk = df[df["week_start"] == week_start(today)].groupby("person")["hours"].sum().reindex(PEOPLE).fillna(0.0)
        mo = df[df["month_start"] == month_start(today)].groupby("person")["hours"].sum().reindex(PEOPLE).fillna(0.0)

        board = pd.DataFrame({
            "person": PEOPLE,
            "week_hours": [float(wk[p]) for p in PEOPLE],
            "month_hours": [float(mo[p]) for p in PEOPLE],
        })
        board["week_rank"] = board["week_hours"].rank(ascending=False, method="min").astype(int)
        board = board.sort_values(["week_hours", "month_hours"], ascending=False)

        st.write(f"**This week (goal {WEEKLY_TARGET} hrs/person)**")
        for _, r in board.iterrows():
            st.write(f"**#{r['week_rank']} {r['person']}** — {r['week_hours']:.2f} hrs")
            st.progress(min(1.0, r["week_hours"] / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

        st.divider()
        st.write(f"**This month (vesting at {MONTHLY_TARGET} hrs/person)**")
        st.dataframe(
            board[["person", "week_hours", "month_hours"]].rename(columns={
                "week_hours": "Week hours",
                "month_hours": "Month hours"
            }),
            use_container_width=True
        )

# ----------------------------
# Adjustments (require comment + notify)
# ----------------------------
with tabs[2]:
    st.subheader("Adjust time (requires a reason)")

    adj_date = st.date_input("Date to adjust", value=today, key="adj_date")
    minutes = st.number_input("Minutes (+ add / - subtract)", min_value=-600, max_value=600, value=0, step=5)
    reason = st.text_input("Reason (required)", value="")

    if st.button("Apply adjustment", use_container_width=True):
        if minutes == 0:
            st.warning("Enter non-zero minutes.")
        elif reason.strip() == "":
            st.warning("Please enter a reason (required).")
        else:
            delta_hours = minutes / 60.0
            add_log(adj_date, person, delta_hours, notes=reason.strip(), source="adjustment")
            add_notification(person, adj_date, delta_hours, reason.strip())
            st.success(f"Applied {delta_hours:+.2f} hrs on {adj_date} and notified admin.")
            st.rerun()

# ----------------------------
# Notifications (your inbox)
# ----------------------------
with tabs[3]:
    st.subheader("Notifications (Adjustment inbox)")

    ndf = fetch_notifications()
    if ndf.empty:
        st.info("No adjustment notifications yet.")
    else:
        st.dataframe(ndf, use_container_width=True)
        csv = ndf.to_csv(index=False).encode("utf-8")
        st.download_button("Download notifications CSV", csv, file_name="adjustment_notifications.csv", mime="text/csv")

        st.caption("Next upgrade: auto-email these to Drew (requires adding an email secret).")

# ----------------------------
# Logs
# ----------------------------
with tabs[4]:
    st.subheader("All logs (everyone)")
    df = fetch_logs()
    if df.empty:
        st.info("No logs yet.")
    else:
        st.dataframe(df.sort_values(["log_date", "person"], ascending=[False, True]), use_container_width=True)
