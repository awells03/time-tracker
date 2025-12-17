import streamlit as st
import sqlite3
from datetime import datetime, date, timedelta

# Auto-refresh (safe + compatible)
try:
    from streamlit_autorefresh import st_autorefresh
    HAVE_AUTOREFRESH = True
except Exception:
    HAVE_AUTOREFRESH = False

# ----------------------------
# SETTINGS
# ----------------------------
DB_PATH = "time_tracker.db"

PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]
ADMIN = "Drew"

WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0  # Monday

REFRESH_MS = 1500  # refresh while clocked in (lower flicker than 1000ms)

# ----------------------------
# DB helpers (robust)
# ----------------------------
def db():
    # timeout helps with "database is locked"
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    cur = con.cursor()
    # WAL helps reliability on Streamlit Cloud
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    return con

def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def columns(cur, table: str) -> set:
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}

def ensure_column(cur, table: str, col: str, ddl_type: str):
    cols = columns(cur, table)
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl_type}")

def init_db():
    con = db()
    cur = con.cursor()

    # --- logs (migratable)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            hours REAL NOT NULL,
            notes TEXT,
            source TEXT
        )
    """)
    # Add missing cols if old DB exists
    ensure_column(cur, "logs", "created_at", "TEXT")
    ensure_column(cur, "logs", "notes", "TEXT")
    ensure_column(cur, "logs", "source", "TEXT")

    # --- timers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at TEXT,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT NOT NULL
        )
    """)

    # --- notifications (migratable)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            person TEXT NOT NULL,
            log_date TEXT NOT NULL,
            delta_hours REAL NOT NULL,
            reason TEXT NOT NULL,
            kind TEXT NOT NULL
        )
    """)
    ensure_column(cur, "notifications", "created_at", "TEXT")

    # Ensure timer rows
    today_iso = date.today().isoformat()
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, today_iso))

    con.commit()
    con.close()

def now_utc():
    return datetime.utcnow()

def now_utc_str():
    return now_utc().isoformat()

# ----------------------------
# Time helpers
# ----------------------------
def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)

def month_start(d: date) -> date:
    return d.replace(day=1)

def fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ----------------------------
# Queries (no pandas)
# ----------------------------
def fetch_timer(person: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT is_running, started_at, accumulated_seconds, active_date FROM timers WHERE person=?", (person,))
    row = cur.fetchone()
    con.close()
    # row always exists due to init_db, but guard anyway
    if not row:
        return (0, None, 0, date.today().isoformat())
    return row

def update_timer(person: str, is_running: int, started_at, accumulated_seconds: int, active_date: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE timers
        SET is_running=?, started_at=?, accumulated_seconds=?, active_date=?
        WHERE person=?
    """, (int(is_running), started_at, int(accumulated_seconds), active_date, person))
    con.commit()
    con.close()

def add_log(log_date: date, person: str, hours: float, notes: str, source: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO logs (created_at, log_date, person, hours, notes, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (now_utc_str(), log_date.isoformat(), person, float(hours), notes, source))
    con.commit()
    con.close()

def add_notification(person: str, log_date: date, delta_hours: float, reason: str, kind: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO notifications (created_at, person, log_date, delta_hours, reason, kind)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (now_utc_str(), person, log_date.isoformat(), float(delta_hours), reason, kind))
    con.commit()
    con.close()

def week_totals(d: date):
    start = week_start(d)
    end = start + timedelta(days=7)
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours), 0)
        FROM logs
        WHERE log_date >= ? AND log_date < ?
        GROUP BY person
    """, (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    con.close()
    out = {p: 0.0 for p in PEOPLE}
    for p, v in rows:
        out[p] = float(v or 0.0)
    return out

def month_totals(d: date):
    start = month_start(d)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours), 0)
        FROM logs
        WHERE log_date >= ? AND log_date < ?
        GROUP BY person
    """, (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    con.close()
    out = {p: 0.0 for p in PEOPLE}
    for p, v in rows:
        out[p] = float(v or 0.0)
    return out

def fetch_notifications(limit=200):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT created_at, person, log_date, delta_hours, reason, kind
        FROM notifications
        ORDER BY created_at DESC
        LIMIT ?
    """, (int(limit),))
    rows = cur.fetchall()
    con.close()
    return rows

def fetch_logs(limit=300):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT created_at, log_date, person, hours, notes, source
        FROM logs
        ORDER BY log_date DESC, created_at DESC
        LIMIT ?
    """, (int(limit),))
    rows = cur.fetchall()
    con.close()
    return rows

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Equity Vesting Time Tracker", layout="wide")
init_db()

st.title("⏱️ Equity Vesting Time Tracker")
st.caption("Clock in / Clock out • Weekly goal 10 hrs • Monthly vesting 40 hrs")

with st.sidebar:
    st.subheader("Controls")
    person = st.selectbox("Who are you?", PEOPLE)
    today = st.date_input("Today", value=date.today())
    st.divider()
    st.write("Everyone can see everyone (leaderboard enabled).")

tab_names = ["⏱️ Clock In", "🏆 Leaderboard", "✍️ Manual Time", "🔔 Notifications"]
if person == ADMIN:
    tab_names.append("🧾 Logs (Admin)")
tabs = st.tabs(tab_names)

# ----------------------------
# TAB 1: CLOCK IN
# ----------------------------
with tabs[0]:
    st.subheader("Clock In / Clock Out")

    is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # If day changed and not running, reset accumulator
    if active_date != today.isoformat() and int(is_running) == 0:
        update_timer(person, 0, None, 0, today.isoformat())
        is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # Auto-refresh ONLY while running
    if int(is_running) == 1 and HAVE_AUTOREFRESH:
        st_autorefresh(interval=REFRESH_MS, key=f"tick_{person}")

    # Compute display seconds
    seconds = int(acc_sec)
    if int(is_running) == 1 and started_at:
        seconds += int((now_utc() - datetime.fromisoformat(started_at)).total_seconds())

    if int(is_running) == 1:
        st.success("🟢 CLOCKED IN — Timer running")
        st.caption("Time is actively counting…")
    else:
        st.info("⚪ CLOCKED OUT")

    st.markdown(f"## {fmt_hms(seconds)}")

    c1, c2, c3 = st.columns([1,1,1])

    with c1:
        if int(is_running) == 0:
            if st.button("▶️ Clock In", use_container_width=True):
                update_timer(person, 1, now_utc_str(), int(acc_sec), today.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Clock Out (Save)", use_container_width=True):
                hours = seconds / 3600.0
                add_log(today, person, hours, "Clocked session", "timer")
                update_timer(person, 0, None, 0, today.isoformat())
                st.success(f"Saved {hours:.2f} hrs")
                st.rerun()

    with c2:
        wk = week_totals(today).get(person, 0.0)
        st.metric("This week", f"{wk:.2f} hrs")
        st.progress(min(1.0, wk / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    with c3:
        mo = month_totals(today).get(person, 0.0)
        st.metric("This month", f"{mo:.2f} hrs")
        st.progress(min(1.0, mo / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

    if not HAVE_AUTOREFRESH and int(is_running) == 1:
        st.warning("Auto-refresh package not installed. Add it to requirements.txt (see below) to make the timer tick visually.")

# ----------------------------
# TAB 2: LEADERBOARD
# ----------------------------
with tabs[1]:
    st.subheader("Leaderboard")

    wk = week_totals(today)
    mo = month_totals(today)

    ordered = sorted(PEOPLE, key=lambda p: (wk.get(p, 0.0), mo.get(p, 0.0)), reverse=True)

    st.caption(f"Week starting {week_start(today)} • Goal {WEEKLY_TARGET:.0f} hrs")
    for i, p in enumerate(ordered, start=1):
        hrs = wk.get(p, 0.0)
        st.write(f"**#{i} {p}** — {hrs:.2f} hrs")
        st.progress(min(1.0, hrs / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    st.divider()
    st.caption(f"Month starting {month_start(today)} • Vesting {MONTHLY_TARGET:.0f} hrs")
    for p in PEOPLE:
        hrs = mo.get(p, 0.0)
        status = "✅ VESTED" if hrs >= MONTHLY_TARGET else "⏳ In progress"
        st.write(f"**{p}** — {hrs:.2f} hrs • {status}")
        st.progress(min(1.0, hrs / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

# ----------------------------
# TAB 3: MANUAL TIME (reason required)
# ----------------------------
with tabs[2]:
    st.subheader("Manual Time (Reason Required)")

    entry_date = st.date_input("Date", value=today, key="m_date")
    mode = st.radio("Type", ["Add hours", "Adjust (+/- minutes)"], horizontal=True)

    reason = st.text_input("Reason (required)", value="", key="m_reason")

    if mode == "Add hours":
        hours = st.number_input("Hours", min_value=0.0, max_value=24.0, value=0.0, step=0.25)
        if st.button("Save manual hours", use_container_width=True):
            if hours <= 0:
                st.warning("Enter > 0 hours.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                add_log(entry_date, person, float(hours), reason.strip(), "manual_add")
                add_notification(person, entry_date, float(hours), reason.strip(), "manual_add")
                st.success("Saved + recorded reason.")
                st.rerun()
    else:
        minutes = st.number_input("Minutes (+ add / - subtract)", min_value=-600, max_value=600, value=0, step=5)
        if st.button("Save adjustment", use_container_width=True):
            if minutes == 0:
                st.warning("Minutes cannot be 0.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                delta_hours = float(minutes) / 60.0
                add_log(entry_date, person, delta_hours, reason.strip(), "adjustment")
                add_notification(person, entry_date, delta_hours, reason.strip(), "adjustment")
                st.success("Saved + recorded reason.")
                st.rerun()

# ----------------------------
# TAB 4: NOTIFICATIONS
# ----------------------------
with tabs[3]:
    st.subheader("Notifications (Manual entries & adjustments)")

    rows = fetch_notifications()
    if not rows:
        st.info("No notifications yet.")
    else:
        for created_at, p, log_date, delta_hours, reason, kind in rows[:150]:
            st.markdown(
                f"- **{p}** • {log_date} • **{delta_hours:+.2f} hrs** • *{kind}*\n"
                f"  \n  Reason: {reason}\n"
                f"  \n  _{created_at}_"
            )

# ----------------------------
# ADMIN LOGS TAB (ONLY DREW)
# ----------------------------
if person == ADMIN:
    with tabs[4]:
        st.subheader("Logs (Admin only)")
        rows = fetch_logs()
        if not rows:
            st.info("No logs yet.")
        else:
            for created_at, log_date, p, hrs, notes, source in rows[:300]:
                st.markdown(
                    f"- **{p}** • {log_date} • **{hrs:+.2f} hrs** • *{source}*\n"
                    f"  \n  Notes: {notes}\n"
                    f"  \n  _{created_at}_"
                )
