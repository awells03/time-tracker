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

REFRESH_MS = 1500  # only while clocked in

def clamp_nonneg(x: float) -> float:
    return max(0.0, float(x or 0.0))

# ----------------------------
# DB helpers
# ----------------------------
def db():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    return con

def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def get_cols(cur, table: str) -> set:
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}

def now_utc_str():
    return datetime.utcnow().isoformat()

def migrate_notifications_if_needed(cur):
    required = {"created_at", "person", "log_date", "delta_hours", "reason", "kind"}

    if not table_exists(cur, "notifications"):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                person TEXT NOT NULL,
                log_date TEXT NOT NULL,
                delta_hours REAL NOT NULL,
                reason TEXT NOT NULL,
                kind TEXT NOT NULL
            )
        """)
        return

    existing = get_cols(cur, "notifications")
    if required.issubset(existing):
        return

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            person TEXT NOT NULL,
            log_date TEXT NOT NULL,
            delta_hours REAL NOT NULL,
            reason TEXT NOT NULL,
            kind TEXT NOT NULL
        )
    """)

    created_at_expr = "created_at" if "created_at" in existing else "datetime('now')"
    person_expr     = "person"     if "person" in existing else "''"
    log_date_expr   = "log_date"   if "log_date" in existing else "date('now')"
    delta_expr      = "delta_hours" if "delta_hours" in existing else "0.0"
    reason_expr     = "reason"     if "reason" in existing else "''"
    kind_expr       = "kind"       if "kind" in existing else "'legacy'"

    cur.execute(f"""
        INSERT INTO notifications_new (created_at, person, log_date, delta_hours, reason, kind)
        SELECT {created_at_expr}, {person_expr}, {log_date_expr}, {delta_expr}, {reason_expr}, {kind_expr}
        FROM notifications
    """)

    cur.execute("DROP TABLE notifications")
    cur.execute("ALTER TABLE notifications_new RENAME TO notifications")

def migrate_logs_if_needed(cur):
    required = {"created_at", "log_date", "person", "hours", "notes", "source"}

    if not table_exists(cur, "logs"):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                log_date TEXT NOT NULL,
                person TEXT NOT NULL,
                hours REAL NOT NULL,
                notes TEXT NOT NULL,
                source TEXT NOT NULL
            )
        """)
        return

    existing = get_cols(cur, "logs")
    if required.issubset(existing):
        return

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            hours REAL NOT NULL,
            notes TEXT NOT NULL,
            source TEXT NOT NULL
        )
    """)

    created_at_expr = "created_at" if "created_at" in existing else "datetime('now')"
    log_date_expr   = "log_date"   if "log_date" in existing else "date('now')"
    person_expr     = "person"     if "person" in existing else "''"
    hours_expr      = "hours"      if "hours" in existing else "0.0"
    notes_expr      = "notes"      if "notes" in existing else "''"
    source_expr     = "source"     if "source" in existing else "'legacy'"

    cur.execute(f"""
        INSERT INTO logs_new (created_at, log_date, person, hours, notes, source)
        SELECT {created_at_expr}, {log_date_expr}, {person_expr}, {hours_expr}, {notes_expr}, {source_expr}
        FROM logs
    """)

    cur.execute("DROP TABLE logs")
    cur.execute("ALTER TABLE logs_new RENAME TO logs")

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at TEXT,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT NOT NULL
        )
    """)

    migrate_logs_if_needed(cur)
    migrate_notifications_if_needed(cur)

    today_iso = date.today().isoformat()
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, today_iso))

    con.commit()
    con.close()

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

def safe_parse_iso(ts: str) -> datetime:
    # very defensive: never break timer math
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.utcnow()

# ----------------------------
# Data access
# ----------------------------
def fetch_timer(person: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT is_running, started_at, accumulated_seconds, active_date FROM timers WHERE person=?", (person,))
    row = cur.fetchone()
    con.close()
    return row if row else (0, None, 0, date.today().isoformat())

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
        out[p] = clamp_nonneg(v)
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
        out[p] = clamp_nonneg(v)
    return out

def fetch_notifications(limit=200):
    try:
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
    except sqlite3.Error:
        return []

def fetch_logs(limit=300):
    try:
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
    except sqlite3.Error:
        return []

# ----------------------------
# APP UI
# ----------------------------
st.set_page_config(page_title="Equity Vesting Time Tracker", layout="wide")
init_db()

st.title("⏱️ Equity Vesting Time Tracker")
st.caption("Clock in / Clock out • Weekly goal 10 hrs • Monthly vesting 40 hrs")

with st.sidebar:
    st.subheader("Controls")
    person = st.selectbox("Who are you?", PEOPLE)
    sidebar_today = st.date_input("Today", value=date.today())
    st.divider()
    st.write("Everyone can see everyone (leaderboard enabled).")

# Tabs: Admin-only tabs only for Drew
tab_names = ["⏱️ Clock In", "🏆 Leaderboard", "✍️ Manual Time"]
if person == ADMIN:
    tab_names.append("🔔 Notifications (Admin)")
    tab_names.append("🧾 Logs (Admin only)")
tabs = st.tabs(tab_names)

# ----------------------------
# TAB 1: CLOCK IN
# ----------------------------
with tabs[0]:
    st.subheader("Clock In / Clock Out")

    is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # If running, the "truth" date is active_date (NOT whatever the sidebar says)
    try:
        run_date = date.fromisoformat(active_date)
    except Exception:
        run_date = date.today()

    today_effective = run_date if int(is_running) == 1 else sidebar_today

    # If not running and date changed, reset day state (safe)
    if int(is_running) == 0 and active_date != today_effective.isoformat():
        update_timer(person, 0, None, 0, today_effective.isoformat())
        is_running, started_at, acc_sec, active_date = fetch_timer(person)
        run_date = date.fromisoformat(active_date)
        today_effective = run_date

    # Auto-refresh only while running
    if int(is_running) == 1 and HAVE_AUTOREFRESH:
        st_autorefresh(interval=REFRESH_MS, key=f"tick_{person}")

    # Compute seconds
    seconds = int(acc_sec)
    if int(is_running) == 1 and started_at:
        started_dt = safe_parse_iso(started_at)
        seconds += int((datetime.utcnow() - started_dt).total_seconds())

    seconds = max(0, seconds)

    if int(is_running) == 1:
        st.success(f"🟢 CLOCKED IN — Timer running (saving to {run_date.isoformat()})")
        if sidebar_today != run_date:
            st.caption("Note: Sidebar date changes are ignored while clocked in (prevents lost time).")
    else:
        st.info("⚪ CLOCKED OUT")

    st.markdown(f"## {fmt_hms(seconds)}")

    c1, c2, c3 = st.columns([1, 1, 1])

    with c1:
        if int(is_running) == 0:
            if st.button("▶️ Clock In", use_container_width=True):
                # Start a fresh session for the selected date
                update_timer(person, 1, now_utc_str(), int(acc_sec), sidebar_today.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Clock Out (Save)", use_container_width=True):
                # Save to the run_date (active_date), not the sidebar
                hours = seconds / 3600.0
                if hours > 0:
                    add_log(run_date, person, hours, "Clocked session", "timer")
                update_timer(person, 0, None, 0, run_date.isoformat())
                st.success(f"Saved {hours:.2f} hrs to {run_date.isoformat()}")
                st.rerun()

    with c2:
        wk = clamp_nonneg(week_totals(today_effective).get(person, 0.0))
        st.metric("This week", f"{wk:.2f} hrs")
        st.progress(min(1.0, wk / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    with c3:
        mo = clamp_nonneg(month_totals(today_effective).get(person, 0.0))
        st.metric("This month", f"{mo:.2f} hrs")
        st.progress(min(1.0, mo / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

    if int(is_running) == 1 and not HAVE_AUTOREFRESH:
        st.warning("Timer is running but the page can't auto-tick. Add `streamlit-autorefresh` to requirements.txt.")

# ----------------------------
# TAB 2: LEADERBOARD
# ----------------------------
with tabs[1]:
    st.subheader("Leaderboard")

    wk = week_totals(sidebar_today)
    mo = month_totals(sidebar_today)

    ordered = sorted(PEOPLE, key=lambda p: (wk.get(p, 0.0), mo.get(p, 0.0)), reverse=True)

    st.caption(f"Week starting {week_start(sidebar_today)} • Goal {WEEKLY_TARGET:.0f} hrs")
    for i, p in enumerate(ordered, start=1):
        hrs = clamp_nonneg(wk.get(p, 0.0))
        st.write(f"**#{i} {p}** — {hrs:.2f} hrs")
        st.progress(min(1.0, hrs / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    st.divider()
    st.caption(f"Month starting {month_start(sidebar_today)} • Vesting {MONTHLY_TARGET:.0f} hrs")
    for p in PEOPLE:
        hrs = clamp_nonneg(mo.get(p, 0.0))
        status = "✅ VESTED" if hrs >= MONTHLY_TARGET else "⏳ In progress"
        st.write(f"**{p}** — {hrs:.2f} hrs • {status}")
        st.progress(min(1.0, hrs / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

# ----------------------------
# TAB 3: MANUAL TIME (REASON REQUIRED)
# ----------------------------
with tabs[2]:
    st.subheader("Manual Time (Reason Required)")

    entry_date = st.date_input("Date", value=sidebar_today, key="m_date")
    mode = st.radio("Type", ["Add hours", "Adjust (+/- minutes)"], horizontal=True, key="m_mode")
    reason = st.text_input("Reason (required)", value=st.session_state.get("m_reason", ""), key="m_reason")

    if mode == "Add hours":
        hours = st.number_input("Hours", min_value=0.0, max_value=24.0, value=float(st.session_state.get("m_hours", 0.0)),
                                step=0.25, key="m_hours")
        if st.button("Save manual hours", use_container_width=True):
            if hours <= 0:
                st.warning("Enter > 0 hours.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                add_log(entry_date, person, float(hours), reason.strip(), "manual_add")
                add_notification(person, entry_date, float(hours), reason.strip(), "manual_add")

                # CLEAR form so they can’t double-submit accidentally
                st.session_state["m_reason"] = ""
                st.session_state["m_hours"] = 0.0

                st.success("Saved + recorded reason.")
                st.rerun()
    else:
        minutes = st.number_input(
            "Minutes (+ add / - subtract)",
            min_value=-600, max_value=600,
            value=int(st.session_state.get("m_minutes", 0)),
            step=5, key="m_minutes"
        )
        if st.button("Save adjustment", use_container_width=True):
            if minutes == 0:
                st.warning("Minutes cannot be 0.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                delta_hours = float(minutes) / 60.0
                add_log(entry_date, person, delta_hours, reason.strip(), "adjustment")
                add_notification(person, entry_date, delta_hours, reason.strip(), "adjustment")

                # CLEAR form so they can’t double-submit accidentally
                st.session_state["m_reason"] = ""
                st.session_state["m_minutes"] = 0

                st.success("Saved + recorded reason.")
                st.rerun()

# ----------------------------
# ADMIN TABS (ONLY DREW)
# ----------------------------
if person == ADMIN:
    with tabs[3]:
        st.subheader("Notifications (Manual entries & adjustments)")
        rows = fetch_notifications()
        if not rows:
            st.info("No notifications yet.")
        else:
            for created_at, p, log_date, delta_hours, reason, kind in rows[:200]:
                st.markdown(
                    f"- **{p}** • {log_date} • **{float(delta_hours):+.2f} hrs** • *{kind}*\n"
                    f"\n  Reason: {reason}\n"
                    f"\n  _{created_at}_"
                )

    with tabs[4]:
        st.subheader("Logs (Admin only)")
        rows = fetch_logs()
        if not rows:
            st.info("No logs yet.")
        else:
            for created_at, log_date, p, hrs, notes, source in rows[:300]:
                st.markdown(
                    f"- **{p}** • {log_date} • **{float(hrs):+.2f} hrs** • *{source}*\n"
                    f"\n  Notes: {notes}\n"
                    f"\n  _{created_at}_"
                )
