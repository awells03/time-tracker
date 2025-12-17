import streamlit as st
import pandas as pd
import sqlite3
from datetime import date, timedelta

DB_PATH = "time_tracker.db"

PEOPLE = ["Drew", "Carson", "Kaden", "Chandler"]
WEEKLY_TARGET = 10.0
MONTHLY_TARGET = 40.0
WEEK_START = 0

ACCESS_CODES = {
    "Drew": "1111",
    "Carson": "2222",
    "Kaden": "3333",
    "Chandler": "4444",
}

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            hours REAL NOT NULL,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_log(log_date, person, hours, notes):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (log_date, person, hours, notes) VALUES (?, ?, ?, ?)",
        (log_date.isoformat(), person, float(hours), notes)
    )
    conn.commit()
    conn.close()

def fetch_logs():
    conn = get_conn()
    df = pd.read_sql_query("SELECT log_date, person, hours, notes FROM logs", conn)
    conn.close()
    if df.empty:
        return df
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
    return df

def week_start(d):
    weekday = d.weekday()
    delta = (weekday - WEEK_START) % 7
    return d - timedelta(days=delta)

def month_start(d):
    return d.replace(day=1)

def month_end(d):
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)

def status_color_week(total, wk_start, today):
    if total >= WEEKLY_TARGET:
        return "background-color: #d4f7d4"
    if today > wk_start + timedelta(days=6):
        return "background-color: #ffd6d6"
    return "background-color: #fff3cd"

def status_color_month(total, mo_start, today):
    if total >= MONTHLY_TARGET:
        return "background-color: #d4f7d4"
    if today > month_end(mo_start):
        return "background-color: #ffd6d6"
    return "background-color: #fff3cd"

st.set_page_config(page_title="Equity Vesting Time Tracker", layout="wide")
init_db()

st.title("Equity Vesting Time Tracker")
st.caption("10 hrs/week • 40 hrs/month to vest")

with st.sidebar:
    person = st.selectbox("Who are you?", PEOPLE)
    code = st.text_input("Access code", type="password")
    logged_in = ACCESS_CODES.get(person) == code
    today = st.date_input("Today", value=date.today())

tabs = st.tabs(["Log Time", "Weekly", "Monthly"])

df = fetch_logs()

with tabs[0]:
    if logged_in:
        d = st.date_input("Date", value=today)
        h = st.number_input("Hours", 0.0, 24.0, 1.0, 0.25)
        n = st.text_input("Notes")
        if st.button("Log"):
            add_log(d, person, h, n)
            st.success("Logged!")
            st.rerun()
    else:
        st.info("Enter access code to log time")

if not df.empty:
    df["week"] = df["log_date"].apply(week_start)
    df["month"] = df["log_date"].apply(month_start)

    w = df.groupby(["person", "week"])["hours"].sum().reset_index()
    m = df.groupby(["person", "month"])["hours"].sum().reset_index()

    with tabs[1]:
        for p in PEOPLE:
            st.subheader(p)
            for _, r in w[w.person == p].iterrows():
                st.markdown(
                    f"<div style='{status_color_week(r.hours, r.week, today)}; padding:8px'>"
                    f"Week of {r.week}: {r.hours:.2f} hrs</div>",
                    unsafe_allow_html=True
                )

    with tabs[2]:
        for p in PEOPLE:
            st.subheader(p)
            for _, r in m[m.person == p].iterrows():
                st.markdown(
                    f"<div style='{status_color_month(r.hours, r.month, today)}; padding:8px'>"
                    f"{r.month.strftime('%B %Y')}: {r.hours:.2f} hrs</div>",
                    unsafe_allow_html=True
                )
