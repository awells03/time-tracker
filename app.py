import os
import sqlite3
from datetime import datetime, date, timedelta, timezone

import streamlit as st

# Optional live refresh (nice-to-have; app works without it)
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None


# =========================
# Config
# =========================
APP_TITLE = "⏱️ Equity Vesting Time Tracker"
SUBTITLE = "Clock in / Clock out • Weekly goal 12 hrs • Monthly vesting 48 hrs"

ADMIN_NAME = "Drew"
PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]

WEEKLY_GOAL_HRS = 12.0
MONTHLY_GOAL_HRS = 48.0

DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "time_tracker.db")

# Manual adjustment safety rules
ENFORCE_MONTH_FLOOR = True      # prevents month total going below 0
ENFORCE_DAY_FLOOR = True        # also prevents day total going below 0
HISTORY_MONTHS_BACK = 12        # how many months show in history


# =========================
# Helpers
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

def parse_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)

def clamp_nonneg(x: float) -> float:
    try:
        x = float(x)
    except Exception:
        return 0.0
    return max(0.0, x)

def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday start

def month_start(d: date) -> date:
    return date(d.year, d.month, 1)

def month_end_exclusive(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)

def add_months(d: date, months: int) -> date:
    # move to the 1st of month, then offset
    y = d.year
    m = d.month + months
    while m <= 0:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return date(y, m, 1)

def ym_label(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# =========================
# Database
# =========================
def db() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con

def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None

def migrate_logs(cur: sqlite3.Cursor):
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

    cur.execute("PRAGMA table_info(logs)")
    info = cur.fetchall()
    existing_cols = {r[1] for r in info}
    col_types = {r[1]: (r[2] or "").upper() for r in info}
    log_date_type = col_types.get("log_date", "")
    needs_rebuild = (not required.issubset(existing_cols)) or ("TEXT" not in log_date_type)

    if not needs_rebuild:
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

    created_at_expr = "created_at" if "created_at" in existing_cols else "datetime('now')"
    person_expr     = "person"     if "person" in existing_cols else "''"
    hours_expr      = "hours"      if "hours" in existing_cols else "0.0"
    notes_expr      = "notes"      if "notes" in existing_cols else "''"
    source_expr     = "source"     if "source" in existing_cols else "'legacy'"

    log_date_expr = "COALESCE(date(log_date), date('now'))" if "log_date" in existing_cols else "date('now')"

    cur.execute(f"""
        INSERT INTO logs_new (created_at, log_date, person, hours, notes, source)
        SELECT {created_at_expr}, {log_date_expr}, {person_expr}, {hours_expr}, {notes_expr}, {source_expr}
        FROM logs
    """)

    cur.execute("DROP TABLE logs")
    cur.execute("ALTER TABLE logs_new RENAME TO logs")


def migrate_active_sessions(cur: sqlite3.Cursor):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            person TEXT PRIMARY KEY,
            start_at TEXT NOT NULL,
            log_date TEXT NOT NULL
        )
    """)

def migrate_notifications(cur: sqlite3.Cursor):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            person TEXT NOT NULL,
            log_date TEXT NOT NULL,
            delta_hours REAL NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            seen INTEGER NOT NULL DEFAULT 0
        )
    """)

def ensure_schema():
    con = db()
    cur = con.cursor()
    migrate_logs(cur)
    migrate_active_sessions(cur)
    migrate_notifications(cur)
    con.commit()
    con.close()


# =========================
# DB Ops
# =========================
def log_event(person: str, log_date_str: str, hours: float, notes: str, source: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO logs (created_at, log_date, person, hours, notes, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (iso_utc(now_utc()), log_date_str, person, float(hours), notes or "", source))
    con.commit()
    con.close()

def add_notification(person: str, log_date_str: str, delta_hours: float, reason: str, source: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO notifications (created_at, person, log_date, delta_hours, reason, source, seen)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (iso_utc(now_utc()), person, log_date_str, float(delta_hours), reason or "", source))
    con.commit()
    con.close()

def get_active_session(person: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT start_at, log_date FROM active_sessions WHERE person=?;", (person,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return {"start_at": row[0], "log_date": row[1]}

def start_session(person: str, log_date_str: str):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO active_sessions (person, start_at, log_date)
        VALUES (?, ?, ?)
        ON CONFLICT(person) DO UPDATE SET start_at=excluded.start_at, log_date=excluded.log_date
    """, (person, iso_utc(now_utc()), log_date_str))
    con.commit()
    con.close()

def stop_session(person: str):
    active = get_active_session(person)
    if not active:
        return None, 0.0

    start_at = parse_iso(active["start_at"])
    log_date_str = active["log_date"]

    elapsed = (now_utc() - start_at).total_seconds()
    elapsed_hours = max(0.0, elapsed / 3600.0)

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM active_sessions WHERE person=?;", (person,))
    con.commit()
    con.close()

    return log_date_str, elapsed_hours


def sum_hours_raw(person: str, start_d: date, end_exclusive: date) -> float:
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(hours), 0)
        FROM logs
        WHERE person=?
          AND date(log_date) >= date(?)
          AND date(log_date) <  date(?)
    """, (person, start_d.isoformat(), end_exclusive.isoformat()))
    v = float(cur.fetchone()[0] or 0.0)
    con.close()
    return v

def sum_hours(person: str, start_d: date, end_exclusive: date) -> float:
    return clamp_nonneg(sum_hours_raw(person, start_d, end_exclusive))

def sum_hours_all(start_d: date, end_exclusive: date) -> dict:
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT person, COALESCE(SUM(hours), 0)
        FROM logs
        WHERE date(log_date) >= date(?)
          AND date(log_date) <  date(?)
        GROUP BY person
    """, (start_d.isoformat(), end_exclusive.isoformat()))
    rows = cur.fetchall()
    con.close()
    out = {p: 0.0 for p in PEOPLE}
    for p, v in rows:
        out[p] = clamp_nonneg(v)
    return out

def fetch_recent_logs(limit: int = 50, person: str | None = None):
    con = db()
    cur = con.cursor()
    if person:
        cur.execute("""
            SELECT created_at, log_date, person, hours, source, notes
            FROM logs
            WHERE person=?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
        """, (person, int(limit)))
    else:
        cur.execute("""
            SELECT created_at, log_date, person, hours, source, notes
            FROM logs
            ORDER BY datetime(created_at) DESC
            LIMIT ?
        """, (int(limit),))
    rows = cur.fetchall()
    con.close()
    return rows

def fetch_notifications(limit: int = 50):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, created_at, person, log_date, delta_hours, reason, source, seen
        FROM notifications
        ORDER BY datetime(created_at) DESC
        LIMIT ?
    """, (int(limit),))
    rows = cur.fetchall()
    con.close()
    return rows

def mark_notifications_seen(ids):
    if not ids:
        return
    con = db()
    cur = con.cursor()
    cur.executemany("UPDATE notifications SET seen=1 WHERE id=?;", [(int(i),) for i in ids])
    con.commit()
    con.close()


def monthly_totals_for_month(m0: date) -> dict:
    m1 = month_end_exclusive(m0)
    return sum_hours_all(m0, m1)

def month_history_rows(months_back: int = 12):
    """
    Returns list of month starts descending from current month: [m0, m-1, m-2...]
    """
    base = month_start(date.today())
    months = []
    for i in range(0, months_back):
        months.append(add_months(base, -i))
    return months


# =========================
# UI
# =========================
st.set_page_config(page_title="Equity Vesting Tracker", layout="wide")
ensure_schema()

st.sidebar.markdown("### Controls")
person = st.sidebar.selectbox("Who are you?", PEOPLE, index=0)

today_default = date.today()
selected_date = st.sidebar.date_input("Today", value=today_default)

st.sidebar.markdown("---")
st.sidebar.caption("Everyone can see everyone (leaderboard enabled).")

live_timer = False
if st_autorefresh is not None:
    live_timer = st.sidebar.toggle(
        "Live timer (optional)",
        value=False,
        help="Auto-refresh while clocked in."
    )

st.title(APP_TITLE)
st.caption(SUBTITLE)

is_admin = (person == ADMIN_NAME)

tabs = ["🕒 Clock In", "🏆 Leaderboard", "✍️ Manual Time", "📅 History"]
if is_admin:
    tabs += ["📊 Vesting Report (Admin)", "🔔 Notifications (Admin)", "🧾 Logs (Admin only)"]

t_clock, t_leader, t_manual, t_history, *admin_tabs = st.tabs(tabs)


# -------------------------
# CLOCK IN TAB
# -------------------------
with t_clock:
    st.subheader("Clock In / Clock Out")

    active = get_active_session(person)
    running = active is not None

    if running and live_timer and st_autorefresh is not None:
        st_autorefresh(interval=2000, key=f"autorefresh_{person}")

    if running:
        st.success(f"🟢 CLOCKED IN — Timer running (saving to {active['log_date']})")
    else:
        st.info("⚪ CLOCKED OUT")

    timer_col, wk_col, mo_col = st.columns([1.2, 1, 1])

    with timer_col:
        if running:
            start_at = parse_iso(active["start_at"])
            elapsed_sec = max(0.0, (now_utc() - start_at).total_seconds())
        else:
            elapsed_sec = 0.0

        hh = int(elapsed_sec // 3600)
        mm = int((elapsed_sec % 3600) // 60)
        ss = int(elapsed_sec % 60)
        st.markdown(
            f"<div style='font-size:56px; font-weight:700; line-height:1.0'>{hh:02d}:{mm:02d}:{ss:02d}</div>",
            unsafe_allow_html=True
        )

        if not running:
            if st.button("▶️ Clock In", use_container_width=True):
                start_session(person, selected_date.isoformat())
                st.rerun()
        else:
            if st.button("⏸️ Clock Out (Save)", use_container_width=True):
                log_date_str, elapsed_hours = stop_session(person)
                elapsed_hours = max(0.0, elapsed_hours)

                if log_date_str and elapsed_hours > 0:
                    log_event(
                        person=person,
                        log_date_str=log_date_str,
                        hours=elapsed_hours,
                        notes="Clocked session",
                        source="timer"
                    )
                st.rerun()

        if running and (not live_timer):
            st.caption("Tip: toggle **Live timer** in the sidebar (optional), or refresh anytime.")

    # Totals
    w0 = week_start(selected_date)
    w1 = w0 + timedelta(days=7)
    m0 = month_start(selected_date)
    m1 = month_end_exclusive(selected_date)

    week_total = sum_hours(person, w0, w1)
    month_total = sum_hours(person, m0, m1)

    with wk_col:
        st.markdown("**This week**")
        st.markdown(f"<div style='font-size:44px; font-weight:700'>{week_total:.2f} hrs</div>", unsafe_allow_html=True)
        st.progress(min(1.0, week_total / WEEKLY_GOAL_HRS) if WEEKLY_GOAL_HRS > 0 else 0.0)

    with mo_col:
        st.markdown("**This month**")
        st.markdown(f"<div style='font-size:44px; font-weight:700'>{month_total:.2f} hrs</div>", unsafe_allow_html=True)
        st.progress(min(1.0, month_total / MONTHLY_GOAL_HRS) if MONTHLY_GOAL_HRS > 0 else 0.0)

    st.divider()

    recent = fetch_recent_logs(limit=12, person=person)
    st.caption(f"Saved sessions for **{person}** (recent):")
    if not recent:
        st.write("No saved sessions yet.")
    else:
        for created_at, log_date, p, hrs, src, notes in recent:
            st.write(f"• {created_at} • {log_date} • **{hrs:+.4f} hrs** • `{src}` • {notes}")


# -------------------------
# LEADERBOARD TAB
# -------------------------
with t_leader:
    st.subheader("Leaderboard")

    w0 = week_start(selected_date)
    w1 = w0 + timedelta(days=7)
    totals_week = sum_hours_all(w0, w1)

    st.caption(f"Week starting {w0.isoformat()} • Goal {WEEKLY_GOAL_HRS:.0f} hrs")
    ranked_week = sorted(totals_week.items(), key=lambda kv: kv[1], reverse=True)
    for i, (p, hrs) in enumerate(ranked_week, start=1):
        st.markdown(f"**#{i} {p} — {hrs:.2f} hrs**")
        st.progress(min(1.0, hrs / WEEKLY_GOAL_HRS) if WEEKLY_GOAL_HRS > 0 else 0.0)

    st.divider()

    m0 = month_start(selected_date)
    m1 = month_end_exclusive(selected_date)
    totals_month = sum_hours_all(m0, m1)

    st.caption(f"Month {ym_label(m0)} • Vesting {MONTHLY_GOAL_HRS:.0f} hrs")
    ranked_month = sorted(totals_month.items(), key=lambda kv: kv[1], reverse=True)
    for p, hrs in ranked_month:
        status = "✅ Vested" if hrs >= MONTHLY_GOAL_HRS else "⏳ In progress"
        st.markdown(f"**{p} — {hrs:.2f} hrs** • {status}")
        st.progress(min(1.0, hrs / MONTHLY_GOAL_HRS) if MONTHLY_GOAL_HRS > 0 else 0.0)


# -------------------------
# MANUAL TIME TAB
# -------------------------
with t_manual:
    st.subheader("Manual Time")
    st.caption("Manual time requires a reason and notifies Drew. Manual changes cannot push totals below 0.")

    with st.form("manual_form", clear_on_submit=True):
        colA, colB = st.columns([1, 1])
        with colA:
            m_date = st.date_input("Date to apply manual time", value=selected_date, key="m_date")
            m_hours = st.number_input(
                "Hours (use negative to subtract)",
                value=0.50,
                step=0.25,
                format="%.2f",
                key="m_hours"
            )
        with colB:
            m_reason = st.text_input("Reason (required)", value="", key="m_reason", placeholder="e.g., Forgot to clock in")

        submit = st.form_submit_button("Save manual hours", use_container_width=True)

    if submit:
        if not str(m_reason).strip():
            st.error("Reason is required for manual time.")
        else:
            # -------- SAFETY FLOOR LOGIC --------
            applied_hours = float(m_hours)

            # Month floor: prevent month total going below 0
            m0 = month_start(m_date)
            m1 = month_end_exclusive(m_date)
            month_before = sum_hours_raw(person, m0, m1)
            month_after = month_before + applied_hours

            if ENFORCE_MONTH_FLOOR and month_after < 0:
                # Clamp so it lands exactly at 0
                applied_hours = -month_before
                month_after = 0.0

            # Day floor: prevent day total going below 0
            if ENFORCE_DAY_FLOOR:
                d0 = m_date
                d1 = m_date + timedelta(days=1)
                day_before = sum_hours_raw(person, d0, d1)
                day_after = day_before + applied_hours
                if day_after < 0:
                    # clamp again at day level
                    applied_hours = -day_before

            # If we clamped to ~0, block pointless submission
            if abs(applied_hours) < 1e-9:
                st.warning("That adjustment would push totals below 0, so nothing was applied.")
            else:
                log_event(
                    person=person,
                    log_date_str=m_date.isoformat(),
                    hours=applied_hours,
                    notes=m_reason.strip(),
                    source="manual_add"
                )
                add_notification(
                    person=person,
                    log_date_str=m_date.isoformat(),
                    delta_hours=applied_hours,
                    reason=m_reason.strip(),
                    source="manual_add"
                )
                st.success(f"Saved manual time ✅ ({applied_hours:+.2f} hrs) and notified Drew.")
                st.rerun()


# -------------------------
# HISTORY TAB (everyone)
# -------------------------
with t_history:
    st.subheader("Month History")

    months = month_history_rows(HISTORY_MONTHS_BACK)
    month_labels = [ym_label(m) for m in months]
    pick = st.selectbox("Pick a month", month_labels, index=0)
    idx = month_labels.index(pick)
    m0 = months[idx]
    m1 = month_end_exclusive(m0)

    totals = sum_hours_all(m0, m1)
    st.caption(f"Showing {pick} • Vesting threshold {MONTHLY_GOAL_HRS:.0f} hrs")

    # Everyone sees everyone’s vesting history here (you asked for leaderboard transparency)
    for p in PEOPLE:
        hrs = totals.get(p, 0.0)
        vested = hrs >= MONTHLY_GOAL_HRS
        badge = "✅ Vested" if vested else "❌ Not vested"
        st.markdown(f"**{p}: {hrs:.2f} hrs** — {badge}")
        st.progress(min(1.0, hrs / MONTHLY_GOAL_HRS) if MONTHLY_GOAL_HRS > 0 else 0.0)

    st.divider()

    # Personal history quick list
    st.markdown(f"### {person}'s last {HISTORY_MONTHS_BACK} months")
    for m in months:
        t = sum_hours(person, m, month_end_exclusive(m))
        badge = "✅" if t >= MONTHLY_GOAL_HRS else "❌"
        st.write(f"{badge} {ym_label(m)} — {t:.2f} hrs")


# -------------------------
# ADMIN: VESTING REPORT
# -------------------------
if is_admin and len(admin_tabs) >= 1:
    with admin_tabs[0]:
        st.subheader("Vesting Report (Admin)")

        months = month_history_rows(HISTORY_MONTHS_BACK)
        month_labels = [ym_label(m) for m in months]
        pick = st.selectbox("Report month", month_labels, index=0, key="admin_report_month")
        idx = month_labels.index(pick)
        m0 = months[idx]
        m1 = month_end_exclusive(m0)

        totals = sum_hours_all(m0, m1)
        vested = {p: (totals.get(p, 0.0) >= MONTHLY_GOAL_HRS) for p in PEOPLE}

        st.caption(f"Month {pick} • Vesting threshold {MONTHLY_GOAL_HRS:.0f} hrs")

        vested_list = [p for p in PEOPLE if vested[p]]
        not_vested_list = [p for p in PEOPLE if not vested[p]]

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### ✅ Vested")
            if not vested_list:
                st.write("Nobody vested yet.")
            for p in vested_list:
                st.write(f"• {p} — {totals[p]:.2f} hrs")

        with c2:
            st.markdown("### ❌ Not vested")
            if not not_vested_list:
                st.write("Everyone vested 🎉")
            for p in not_vested_list:
                remaining = max(0.0, MONTHLY_GOAL_HRS - totals[p])
                st.write(f"• {p} — {totals[p]:.2f} hrs (needs {remaining:.2f} more)")

        st.divider()
        st.markdown("### Snapshot (all)")
        for p in PEOPLE:
            hrs = totals.get(p, 0.0)
            status = "✅ Vested" if hrs >= MONTHLY_GOAL_HRS else "⏳ In progress"
            st.write(f"**{p}** — {hrs:.2f} hrs • {status}")


# -------------------------
# ADMIN: NOTIFICATIONS
# -------------------------
if is_admin and len(admin_tabs) >= 2:
    with admin_tabs[1]:
        st.subheader("Notifications (Admin)")

        notes = fetch_notifications(limit=80)
        if not notes:
            st.write("No notifications yet.")
        else:
            unseen_ids = [n[0] for n in notes if n[7] == 0]
            if unseen_ids:
                if st.button("Mark all as seen"):
                    mark_notifications_seen(unseen_ids)
                    st.rerun()

            for nid, created_at, p, log_date, delta_hours, reason, source, seen in notes:
                badge = "🟡 NEW" if seen == 0 else "⚪ Seen"
                st.write(
                    f"{badge} • {created_at} • **{p}** • {log_date} • "
                    f"**{delta_hours:+.2f} hrs** • `{source}` • {reason}"
                )


# -------------------------
# ADMIN: LOGS
# -------------------------
if is_admin and len(admin_tabs) >= 3:
    with admin_tabs[2]:
        st.subheader("Logs (Admin only)")

        filt_person = st.selectbox("Filter by person", ["(All)"] + PEOPLE, index=0, key="admin_logs_filter")
        rows = fetch_recent_logs(limit=300, person=None if filt_person == "(All)" else filt_person)

        if not rows:
            st.write("No logs yet.")
        else:
            st.caption("Most recent first:")
            for created_at, log_date, p, hrs, src, notes in rows:
                st.write(f"• {created_at} • {log_date} • **{p}** • {hrs:+.4f} • `{src}` • {notes}")
