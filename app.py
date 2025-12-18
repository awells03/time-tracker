import streamlit as st
import sqlite3
import time
from datetime import datetime, date, timedelta

# Optional autorefresh (safe if installed)
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

REFRESH_MS = 1200  # only while clocked in


# ----------------------------
# Helpers
# ----------------------------
def clamp_nonneg(x: float) -> float:
    try:
        return max(0.0, float(x or 0.0))
    except Exception:
        return 0.0


def now_utc_str() -> str:
    return datetime.utcnow().isoformat()


def fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end_exclusive(d: date) -> date:
    start = month_start(d)
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


# ----------------------------
# DB layer
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


def migrate_logs(cur):
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


def migrate_notifications(cur):
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


def init_db():
    con = db()
    cur = con.cursor()

    # Timers table: started_at stored as UNIX epoch seconds (REAL)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at REAL,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT NOT NULL
        )
    """)

    migrate_logs(cur)
    migrate_notifications(cur)

    today_iso = date.today().isoformat()
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, today_iso))

    con.commit()
    con.close()


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


def sum_hours(person: str, start: date, end_exclusive: date) -> float:
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(hours), 0)
        FROM logs
        WHERE person=? AND log_date >= ? AND log_date < ?
    """, (person, start.isoformat(), end_exclusive.isoformat()))
    v = cur.fetchone()[0]
    con.close()
    return clamp_nonneg(v)


def sum_hours_all(start: date, end_exclusive: date):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours), 0)
        FROM logs
        WHERE log_date >= ? AND log_date < ?
        GROUP BY person
    """, (start.isoformat(), end_exclusive.isoformat()))
    rows = cur.fetchall()
    con.close()
    out = {p: 0.0 for p in PEOPLE}
    for p, v in rows:
        out[p] = clamp_nonneg(v)
    return out


def fetch_logs_for_person_date(person: str, d: date, limit=50):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT created_at, hours, notes, source
        FROM logs
        WHERE person=? AND log_date=?
        ORDER BY created_at DESC
        LIMIT ?
    """, (person, d.isoformat(), int(limit)))
    rows = cur.fetchall()
    con.close()
    return rows


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
# APP
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

tabs = ["⏱️ Clock In", "🏆 Leaderboard", "✍️ Manual Time"]
if person == ADMIN:
    tabs += ["🔔 Notifications (Admin)", "🧾 Logs (Admin only)"]
t = st.tabs(tabs)

# ----------------------------
# TAB: CLOCK IN
# ----------------------------
with t[0]:
    st.subheader("Clock In / Clock Out")

    is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # Resolve the "save date" for the running session (locked in)
    try:
        run_date = date.fromisoformat(active_date)
    except Exception:
        run_date = sidebar_today

    # If not running, the effective date is sidebar date
    effective_date = run_date if int(is_running) == 1 else sidebar_today

    # If not running and user changed the sidebar date, reset internal day timer state
    if int(is_running) == 0 and active_date != effective_date.isoformat():
        update_timer(person, 0, None, 0, effective_date.isoformat())
        is_running, started_at, acc_sec, active_date = fetch_timer(person)
        run_date = date.fromisoformat(active_date)
        effective_date = sidebar_today

    # Auto refresh only while running
    if int(is_running) == 1 and HAVE_AUTOREFRESH:
        st_autorefresh(interval=REFRESH_MS, key=f"tick_{person}")

    # Compute current seconds (epoch math)
    seconds = int(acc_sec or 0)
    if int(is_running) == 1 and started_at is not None:
        try:
            started_epoch = float(started_at)
        except Exception:
            started_epoch = time.time()
        seconds += int(time.time() - started_epoch)

    seconds = max(0, seconds)
    session_hours = seconds / 3600.0

    if int(is_running) == 1:
        st.success(f"🟢 CLOCKED IN — Timer running (saving to {run_date.isoformat()})")
        if sidebar_today != run_date:
            st.caption("Sidebar date is ignored while clocked in (prevents lost time).")
    else:
        st.info("⚪ CLOCKED OUT")

    st.markdown(f"## {fmt_hms(seconds)}")

    c1, c2, c3 = st.columns([1, 1, 1])

    # Logged totals (real saved DB totals)
    wk_start = week_start(effective_date)
    wk_end = wk_start + timedelta(days=7)
    mo_start = month_start(effective_date)
    mo_end = month_end_exclusive(effective_date)

    wk_logged = sum_hours(person, wk_start, wk_end)
    mo_logged = sum_hours(person, mo_start, mo_end)

    # Dynamic "projected" totals if running
    wk_projected = wk_logged + (session_hours if int(is_running) == 1 else 0.0)
    mo_projected = mo_logged + (session_hours if int(is_running) == 1 else 0.0)

    with c1:
        if int(is_running) == 0:
            if st.button("▶️ Clock In", use_container_width=True):
                # Lock-in date and store start epoch
                update_timer(person, 1, time.time(), 0, sidebar_today.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Clock Out (Save)", use_container_width=True):
                # Re-read timer to avoid race/rerun issues
                is_running2, started_at2, acc_sec2, active_date2 = fetch_timer(person)
                try:
                    save_date = date.fromisoformat(active_date2)
                except Exception:
                    save_date = date.today()

                sec2 = int(acc_sec2 or 0)
                if int(is_running2) == 1 and started_at2 is not None:
                    try:
                        sec2 += int(time.time() - float(started_at2))
                    except Exception:
                        pass

                sec2 = max(0, sec2)
                hrs2 = sec2 / 3600.0

                # Save only if at least 1 second
                if sec2 >= 1:
                    add_log(save_date, person, hrs2, "Clocked session", "timer")

                # Reset timer state
                update_timer(person, 0, None, 0, save_date.isoformat())

                st.success(f"Saved {hrs2:.4f} hrs to {save_date.isoformat()}")
                st.rerun()

    with c2:
        label = "This week (if you clock out now)" if int(is_running) == 1 else "This week"
        st.metric(label, f"{wk_projected:.2f} hrs")
        st.progress(min(1.0, wk_projected / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    with c3:
        label = "This month (if you clock out now)" if int(is_running) == 1 else "This month"
        st.metric(label, f"{mo_projected:.2f} hrs")
        st.progress(min(1.0, mo_projected / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

    st.divider()
    st.caption(f"Saved sessions for **{person}** on **{effective_date.isoformat()}** (proof it saved):")
    rows = fetch_logs_for_person_date(person, effective_date)
    if not rows:
        st.write("No sessions saved yet for this date.")
    else:
        for created_at, hrs, notes, source in rows:
            st.write(f"- {created_at} • **{float(hrs):.4f} hrs** • {source} • {notes}")

# ----------------------------
# TAB: LEADERBOARD
# ----------------------------
with t[1]:
    st.subheader("Leaderboard")

    wk_s = week_start(sidebar_today)
    wk_e = wk_s + timedelta(days=7)
    mo_s = month_start(sidebar_today)
    mo_e = month_end_exclusive(sidebar_today)

    wk_all = sum_hours_all(wk_s, wk_e)
    mo_all = sum_hours_all(mo_s, mo_e)

    ordered = sorted(PEOPLE, key=lambda p: wk_all.get(p, 0.0), reverse=True)

    st.caption(f"Week starting {wk_s.isoformat()} • Goal {WEEKLY_TARGET:.0f} hrs")
    for i, p in enumerate(ordered, start=1):
        hrs = clamp_nonneg(wk_all.get(p, 0.0))
        st.write(f"**#{i} {p}** — {hrs:.2f} hrs")
        st.progress(min(1.0, hrs / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    st.divider()
    st.caption(f"Month starting {mo_s.isoformat()} • Vesting {MONTHLY_TARGET:.0f} hrs")
    for p in PEOPLE:
        hrs = clamp_nonneg(mo_all.get(p, 0.0))
        status = "✅ VESTED" if hrs >= MONTHLY_TARGET else "⏳ In progress"
        st.write(f"**{p}** — {hrs:.2f} hrs • {status}")
        st.progress(min(1.0, hrs / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

# ----------------------------
# TAB: MANUAL TIME (FIXED)
# ----------------------------
with t[2]:
    st.subheader("Manual Time (Reason Required)")
    st.caption("This uses a form with clear-on-submit so it never crashes and it clears inputs automatically.")

    with st.form("manual_time_form", clear_on_submit=True):
        entry_date = st.date_input("Date", value=sidebar_today)
        mode = st.radio("Type", ["Add hours", "Adjust (+/- minutes)"], horizontal=True)

        reason = st.text_input("Reason (required)")

        if mode == "Add hours":
            hours = st.number_input("Hours", min_value=0.0, max_value=24.0, value=0.0, step=0.25)
            submitted = st.form_submit_button("Save manual hours", use_container_width=True)

            if submitted:
                if hours <= 0:
                    st.warning("Enter > 0 hours.")
                elif reason.strip() == "":
                    st.warning("Reason required.")
                else:
                    add_log(entry_date, person, float(hours), reason.strip(), "manual_add")
                    add_notification(person, entry_date, float(hours), reason.strip(), "manual_add")
                    st.success("Saved.")
                    st.rerun()

        else:
            minutes = st.number_input("Minutes (+ add / - subtract)", min_value=-600, max_value=600, value=0, step=5)
            submitted = st.form_submit_button("Save adjustment", use_container_width=True)

            if submitted:
                if minutes == 0:
                    st.warning("Minutes cannot be 0.")
                elif reason.strip() == "":
                    st.warning("Reason required.")
                else:
                    delta_hours = float(minutes) / 60.0
                    # Floor protection is handled in totals via clamp_nonneg
                    add_log(entry_date, person, delta_hours, reason.strip(), "adjustment")
                    add_notification(person, entry_date, delta_hours, reason.strip(), "adjustment")
                    st.success("Saved.")
                    st.rerun()

# ----------------------------
# ADMIN: Notifications + Logs
# ----------------------------
if person == ADMIN:
    with t[3]:
        st.subheader("Notifications (Admin)")
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

    with t[4]:
        st.subheader("Logs (Admin only)")
        rows = fetch_logs()
        if not rows:
            st.info("No logs yet.")
        else:
            for created_at, log_date, p, hrs, notes, source in rows[:300]:
                st.markdown(
                    f"- **{p}** • {log_date} • **{float(hrs):+.4f} hrs** • *{source}*\n"
                    f"\n  Notes: {notes}\n"
                    f"\n  _{created_at}_"
                )
