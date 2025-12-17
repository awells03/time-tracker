import streamlit as st
import sqlite3
from datetime import datetime, date, timedelta

# ----------------------------
# SETTINGS
# ----------------------------
DB_PATH = "time_tracker.db"

PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]
ADMIN = "Drew"

WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0  # Monday

REFRESH_SECONDS = 2  # keep smooth without annoying flicker

# ----------------------------
# DB
# ----------------------------
def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    con = db()
    cur = con.cursor()

    # Tables (create if missing)
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            person TEXT PRIMARY KEY,
            is_running INTEGER NOT NULL,
            started_at TEXT,
            accumulated_seconds INTEGER NOT NULL,
            active_date TEXT NOT NULL
        )
    """)

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

    # Ensure timer row exists for each person
    today = date.today().isoformat()
    for p in PEOPLE:
        cur.execute("""
            INSERT OR IGNORE INTO timers (person, is_running, started_at, accumulated_seconds, active_date)
            VALUES (?, 0, NULL, 0, ?)
        """, (p, today))

    con.commit()
    con.close()

def now_utc():
    return datetime.utcnow()

def now_utc_str():
    return now_utc().isoformat()

def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)

def month_start(d: date) -> date:
    return d.replace(day=1)

def fetch_timer(person: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT is_running, started_at, accumulated_seconds, active_date FROM timers WHERE person=?", (person,))
    row = cur.fetchone()
    con.close()
    return row  # (is_running, started_at, accumulated_seconds, active_date)

def update_timer(person: str, is_running: int, started_at, accumulated_seconds: int, active_date: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE timers
        SET is_running=?, started_at=?, accumulated_seconds=?, active_date=?
        WHERE person=?
    """, (is_running, started_at, accumulated_seconds, active_date, person))
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

def fetch_week_totals(target_week_start: date):
    """Return dict: person -> hours in that week."""
    start = target_week_start
    end = start + timedelta(days=7)
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours),0)
        FROM logs
        WHERE log_date >= ? AND log_date < ?
        GROUP BY person
    """, (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    con.close()
    totals = {p: 0.0 for p in PEOPLE}
    for person, total in rows:
        totals[person] = float(total or 0.0)
    return totals

def fetch_month_totals(target_month_start: date):
    """Return dict: person -> hours in that month."""
    start = target_month_start
    # next month start
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)

    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours),0)
        FROM logs
        WHERE log_date >= ? AND log_date < ?
        GROUP BY person
    """, (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    con.close()
    totals = {p: 0.0 for p in PEOPLE}
    for person, total in rows:
        totals[person] = float(total or 0.0)
    return totals

def fetch_notifications(limit=200):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT created_at, person, log_date, delta_hours, reason, kind
        FROM notifications
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows

def fetch_logs(limit=500):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT created_at, log_date, person, hours, notes, source
        FROM logs
        ORDER BY log_date DESC, created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows

def fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Equity Vesting Time Tracker", layout="wide")
init_db()

# simple styling
st.markdown("""
<style>
.block-container { padding-top: 1.1rem; }
.title-row { display:flex; align-items:center; gap:12px; }
.pill {
  display:inline-block; padding:6px 12px; border-radius:999px;
  border:1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.04);
  font-size:12px; opacity:0.85;
}
.timer {
  font-size: 54px;
  font-weight: 900;
  letter-spacing: 2px;
  margin-top: 6px;
}
.card {
  border:1px solid rgba(255,255,255,0.12);
  border-radius:16px;
  padding:16px;
  background: rgba(255,255,255,0.03);
}
.small { font-size:13px; opacity:0.8; }
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='title-row'><h1>⏱️ Equity Vesting Time Tracker</h1></div>", unsafe_allow_html=True)
st.caption("Clock in / Clock out • Weekly goal 10 hrs • Monthly vesting 40 hrs")

with st.sidebar:
    st.subheader("Controls")
    person = st.selectbox("Who are you?", PEOPLE)
    today = st.date_input("Today", value=date.today())
    st.markdown("---")
    st.write("Everyone can see everyone (leaderboard enabled).")

tabs = ["⏱️ Clock In", "🏆 Leaderboard", "✍️ Manual Time", "🔔 Notifications"]
if person == ADMIN:
    tabs.append("🧾 Logs (Admin)")

tabs = st.tabs(tabs)

# ----------------------------
# TAB 1: CLOCK IN
# ----------------------------
with tabs[0]:
    is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # auto-roll day (simple)
    if active_date != today.isoformat() and int(is_running) == 0:
        update_timer(person, 0, None, 0, today.isoformat())
        is_running, started_at, acc_sec, active_date = fetch_timer(person)

    # compute seconds
    seconds = int(acc_sec)
    if int(is_running) == 1 and started_at:
        seconds += int((now_utc() - datetime.fromisoformat(started_at)).total_seconds())

    # Auto-refresh while clocked in (every REFRESH_SECONDS)
    if int(is_running) == 1:
        st.meta_refresh(REFRESH_SECONDS)

    c1, c2, c3 = st.columns([2,1,1])

    with c1:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        if int(is_running) == 1:
            st.success("🟢 CLOCKED IN — Timer is running")
        else:
            st.info("⚪ CLOCKED OUT")
        st.markdown(f"<div class='small'>Date: <b>{today}</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='timer'>{fmt_hms(seconds)}</div>", unsafe_allow_html=True)
        st.markdown("<div class='small'>Tip: If you forget, use Manual Time (reason required).</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**Clock controls**")
        if int(is_running) == 0:
            if st.button("▶️ Clock In", use_container_width=True):
                update_timer(person, 1, now_utc_str(), int(acc_sec), today.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Clock Out (Save)", use_container_width=True):
                hours = seconds / 3600.0
                add_log(today, person, hours, "Clocked session", "timer")
                update_timer(person, 0, None, 0, today.isoformat())
                st.success(f"Saved {hours:.2f} hrs for {today}")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with c3:
        # Show quick status vs weekly target for THIS person
        this_week = week_start(today)
        wk_totals = fetch_week_totals(this_week)
        my_week = wk_totals.get(person, 0.0)

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("**This week**")
        st.write(f"**{my_week:.2f} hrs** / {WEEKLY_TARGET:.0f} hrs")
        st.progress(min(1.0, my_week / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)
        st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# TAB 2: LEADERBOARD
# ----------------------------
with tabs[1]:
    this_week = week_start(today)
    this_month = month_start(today)

    wk = fetch_week_totals(this_week)
    mo = fetch_month_totals(this_month)

    # Sort by week hours desc
    ordered = sorted(PEOPLE, key=lambda p: (wk.get(p, 0.0), mo.get(p, 0.0)), reverse=True)

    st.subheader("🏆 Weekly Leaderboard")
    st.caption(f"Week starting {this_week.isoformat()} • Goal: {WEEKLY_TARGET:.0f} hrs/person")

    for i, p in enumerate(ordered, start=1):
        hrs = wk.get(p, 0.0)
        st.markdown(f"**#{i} {p}** — {hrs:.2f} hrs")
        st.progress(min(1.0, hrs / WEEKLY_TARGET) if WEEKLY_TARGET else 0.0)

    st.divider()
    st.subheader("📅 Monthly Vesting Progress")
    st.caption(f"Month starting {this_month.isoformat()} • Vesting: {MONTHLY_TARGET:.0f} hrs/person")

    for p in PEOPLE:
        hrs = mo.get(p, 0.0)
        status = "✅ VESTED" if hrs >= MONTHLY_TARGET else "⏳ In progress"
        st.markdown(f"**{p}** — {hrs:.2f} hrs • {status}")
        st.progress(min(1.0, hrs / MONTHLY_TARGET) if MONTHLY_TARGET else 0.0)

# ----------------------------
# TAB 3: MANUAL TIME (REASON REQUIRED ALWAYS)
# ----------------------------
with tabs[2]:
    st.subheader("✍️ Manual Time (Reason Required)")

    st.markdown("Use this if you forgot to clock in, or need to correct time. **A reason is required and gets recorded.**")

    col1, col2 = st.columns([1,2])
    with col1:
        mode = st.radio("Type", ["Add time", "Adjust (+/- minutes)"], horizontal=False)
        entry_date = st.date_input("Date", value=today, key="manual_date")

    with col2:
        reason = st.text_input("Reason (required)", value="", key="manual_reason")

    if mode == "Add time":
        hours = st.number_input("Hours to add", min_value=0.0, max_value=24.0, value=0.0, step=0.25)
        if st.button("Add hours", use_container_width=True):
            if hours <= 0:
                st.warning("Enter > 0 hours.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                add_log(entry_date, person, float(hours), reason.strip(), "manual_add")
                add_notification(person, entry_date, float(hours), reason.strip(), "manual_add")
                st.success("Saved manual time + notified admin.")
                st.rerun()

    else:
        minutes = st.number_input("Minutes (+ add / - subtract)", min_value=-600, max_value=600, value=0, step=5)
        if st.button("Apply adjustment", use_container_width=True):
            if minutes == 0:
                st.warning("Minutes must not be 0.")
            elif reason.strip() == "":
                st.warning("Reason required.")
            else:
                delta_hours = float(minutes) / 60.0
                add_log(entry_date, person, delta_hours, reason.strip(), "adjustment")
                add_notification(person, entry_date, delta_hours, reason.strip(), "adjustment")
                st.success("Saved adjustment + notified admin.")
                st.rerun()

# ----------------------------
# TAB 4: NOTIFICATIONS (EVERYONE CAN VIEW, BUT IT'S YOUR INBOX)
# ----------------------------
with tabs[3]:
    st.subheader("🔔 Notifications (Manual entries & adjustments)")
    rows = fetch_notifications()

    if not rows:
        st.info("No notifications yet.")
    else:
        # Simple table output without pandas (no schema issues)
        st.write("Newest first:")
        for created_at, p, log_date, delta_hours, reason, kind in rows[:100]:
            st.markdown(
                f"- **{p}** • {log_date} • **{delta_hours:+.2f} hrs** • *{kind}*  \n"
                f"  Reason: {reason}  \n"
                f"  _(logged {created_at})_"
            )

# ----------------------------
# ADMIN LOGS TAB (ONLY DREW)
# ----------------------------
if person == ADMIN:
    with tabs[4]:
        st.subheader("🧾 Logs (Admin)")
        rows = fetch_logs()
        if not rows:
            st.info("No logs yet.")
        else:
            # show latest 200
            st.caption("Latest entries (newest first)")
            for created_at, log_date, p, hrs, notes, source in rows[:200]:
                st.markdown(
                    f"- **{p}** • {log_date} • **{hrs:+.2f} hrs** • *{source}*  \n"
                    f"  Notes: {notes}  \n"
                    f"  _(logged {created_at})_"
                )
