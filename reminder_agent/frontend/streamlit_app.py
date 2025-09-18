from streamlit_autorefresh import st_autorefresh
import streamlit as st
import requests
from datetime import datetime, timedelta

API_BASE = "http://127.0.0.1:8000"

st.set_page_config("Reminder Agent", layout="centered")
st.title("🤖 Intelligent Reminder Agent")

# --- Session state defaults ---
if "token" not in st.session_state:
    st.session_state["token"] = None
if "user_email" not in st.session_state:
    st.session_state["user_email"] = None
if "last_notifications_ts" not in st.session_state:
    st.session_state["last_notifications_ts"] = 0

def auth_headers():
    if st.session_state["token"]:
        return {"Authorization": f"Bearer {st.session_state['token']}"}
    return {}

# --- Format backend 'when' (unix ts) to readable string ---
def when_to_str(when_val):
    try:
        return datetime.utcfromtimestamp(int(when_val)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(when_val)

# ----------------- SIMPLE AI AGENT HELPERS -----------------
def suggest_category(title, body):
    txt = f"{title} {body}".lower()
    if "meeting" in txt or "call" in txt:
        return "Work"
    if "medicine" in txt or "pill" in txt or "doctor" in txt:
        return "Health"
    if "birthday" in txt or "party" in txt:
        return "Personal"
    if "exam" in txt or "assignment" in txt or "study" in txt:
        return "Education"
    return "General"

def classify_priority(title, body):
    txt = f"{title} {body}".lower()
    if any(k in txt for k in ["urgent", "asap", "deadline", "exam", "doctor"]):
        return "High"
    if any(k in txt for k in ["meeting", "project", "call"]):
        return "Medium"
    return "Low"

def suggest_snooze(title):
    txt = title.lower()
    if "meeting" in txt:
        return 10  # minutes
    if "medicine" in txt:
        return 60
    if "birthday" in txt:
        return 1440  # 1 day
    return 5

def summarize_reminders(reminders):
    total = len(reminders)
    cats = {}
    for r in reminders:
        cat = r.get("category", "General") or "General"
        cats[cat] = cats.get(cat, 0) + 1
    summary = f"You have {total} reminders.\n"
    for k, v in cats.items():
        summary += f"- {v} in {k}\n"
    return summary

# ----------------- Auth -----------------
with st.expander("🔐 Login / Register", expanded=True):
    cols = st.columns(2)
    with cols[0]:
        st.write("Create account")
        reg_email = st.text_input("Register email", key="reg_email")
        reg_pass = st.text_input("Register password", type="password", key="reg_pass")
        if st.button("Register"):
            if not reg_email or not reg_pass:
                st.warning("Email and password required.")
            else:
                try:
                    r = requests.post(f"{API_BASE}/auth/register", json={"email": reg_email, "password": reg_pass}, timeout=10)
                    if r.ok:
                        st.success("Registered. Now log in below.")
                    else:
                        st.error(f"Register failed: {r.status_code} {r.text}")
                except Exception as e:
                    st.error(f"Register error: {e}")
    with cols[1]:
        st.write("Login")
        login_email = st.text_input("Login email", key="login_email")
        login_pass = st.text_input("Login password", type="password", key="login_pass")
        if st.button("Login"):
            if not login_email or not login_pass:
                st.warning("Email and password required.")
            else:
                try:
                    r = requests.post(f"{API_BASE}/auth/login", data={"username": login_email, "password": login_pass}, timeout=10)
                    if r.ok:
                        j = r.json()
                        token = j.get("access_token")
                        if token:
                            st.session_state["token"] = token
                            st.session_state["user_email"] = login_email
                            st.success("Logged in ✅")
                        else:
                            st.error("No token in response.")
                    else:
                        st.error(f"Login failed: {r.status_code} {r.text}")
                except Exception as e:
                    st.error(f"Login error: {e}")

if st.session_state["token"]:
    st.info(f"Authenticated as: {st.session_state.get('user_email', 'unknown')}")

st.markdown("---")

# ----------------- Create reminder form -----------------
st.subheader("➕ Create Reminder (AI-assisted)")
with st.form("create_form", clear_on_submit=True):
    title = st.text_input("Title")
    body = st.text_area("Body (optional)")
    when_date = st.date_input("Date (UTC)", value=datetime.utcnow().date())

    col1, col2 = st.columns(2)
    hour = col1.selectbox("Hour (UTC)", list(range(0, 24)), index=datetime.utcnow().hour)
    minute = col2.selectbox("Minute", list(range(0, 60)), index=datetime.utcnow().minute)
    when_time = datetime.strptime(f"{hour}:{minute}", "%H:%M").time()

    recurrence = st.selectbox("Repeat", ["none", "daily", "weekly", "monthly"], index=0)
    # AI category suggestion
    suggested_cat = suggest_category(title, body) if title else ""
    category = st.text_input("Category (Work, Personal, ...)", value=suggested_cat)
    tags = st.text_input("Tags (comma separated)")
    submitted = st.form_submit_button("Create reminder")

    if submitted:
        if not st.session_state["token"]:
            st.error("You must log in before creating reminders.")
        elif not title:
            st.error("Please provide a title for the reminder.")
        else:
            when_dt = datetime.combine(when_date, when_time)
            payload = {
                "title": title,
                "body": body,
                "when": when_dt.isoformat(),
                "recurrence": (None if recurrence == "none" else recurrence),
                "repeat_interval_seconds": (
                    86400 if recurrence == "daily" else 604800 if recurrence == "weekly" else None
                ),
                "category": category,
                "tags": tags,
            }
            try:
                r = requests.post(f"{API_BASE}/reminders", json=payload, headers=auth_headers(), timeout=10)
                if r.ok:
                    st.success("✅ Reminder created")
                else:
                    st.error(f"Failed to create reminder: {r.status_code} {r.text}")
            except Exception as e:
                st.error(f"Error creating reminder: {e}")

st.markdown("---")

# ----------------- Fetch & display reminders -----------------
st.subheader("📋 My Reminders (AI-enhanced)")
search_q = st.text_input("Search by title", key="search_q")
status_filter = st.selectbox("Filter", ["all", "scheduled", "fired", "cancelled"], index=0)

def fetch_and_show_reminders():
    if not st.session_state["token"]:
        st.warning("Log in to fetch your reminders.")
        return
    try:
        r = requests.get(f"{API_BASE}/reminders", headers=auth_headers(), timeout=10)
    except Exception as e:
        st.error(f"Error fetching reminders: {e}")
        return
    if not r.ok:
        st.error(f"Failed fetching reminders: {r.status_code} {r.text}")
        return
    items = r.json()

    # AI summary of reminders
    st.info(summarize_reminders(items))

    filtered = []
    for it in items:
        if search_q and search_q.lower() not in it.get("title", "").lower():
            continue
        if status_filter != "all" and it.get("status") != status_filter:
            continue
        filtered.append(it)

    if not filtered:
        st.info("No reminders match your filters.")
        return

    for idx, it in enumerate(filtered):
        when_str = when_to_str(it.get("when"))
        priority = classify_priority(it.get("title", ""), it.get("body", ""))
        header = f"{it.get('title')} — {it.get('status')} — Priority: {priority}"
        with st.expander(header, expanded=False):
            st.write(f"**When:** {when_str}")
            st.write(f"**Category:** {it.get('category','-')}  **Tags:** {it.get('tags','-')}")
            st.write(it.get("body") or "—")
            cols = st.columns([1,1,1,3])

            if it.get("status") == "scheduled":
                smart_minutes = suggest_snooze(it.get("title",""))
                if cols[0].button(f"Snooze {smart_minutes}m", key=f"snooze_{it['id']}_{idx}"):
                    try:
                        rr = requests.post(f"{API_BASE}/reminders/{it['id']}/snooze?minutes={smart_minutes}", headers=auth_headers(), timeout=10)
                        if rr.ok:
                            st.success(f"⏰ Snoozed {smart_minutes}m")
                        else:
                            st.error(f"Snooze failed: {rr.status_code} {rr.text}")
                    except Exception as e:
                        st.error(f"Snooze error: {e}")

                if cols[1].button("Cancel", key=f"cancel_{it['id']}_{idx}"):
                    try:
                        rr = requests.post(f"{API_BASE}/reminders/{it['id']}/cancel", headers=auth_headers(), timeout=10)
                        if rr.ok:
                            st.success("🛑 Cancelled")
                        else:
                            st.error(f"Cancel failed: {rr.status_code} {rr.text}")
                    except Exception as e:
                        st.error(f"Cancel error: {e}")

            if cols[2].button("Delete", key=f"delete_{it['id']}_{idx}"):
                try:
                    rr = requests.delete(f"{API_BASE}/reminders/{it['id']}", headers=auth_headers(), timeout=10)
                    if rr.ok:
                        st.success("🗑️ Deleted")
                    else:
                        st.error(f"Delete failed: {rr.status_code} {rr.text}")
                except Exception as e:
                    st.error(f"Delete error: {e}")

if st.button("Refresh"):
    fetch_and_show_reminders()

if st.session_state["token"]:
    fetch_and_show_reminders()

st.markdown("---")

# ----------------- Polling for notifications -----------------
st.subheader("🔔 Notifications")
st.write("App polls every 5 seconds for fired reminders.")
_ = st_autorefresh(interval=5000, limit=None, key="notif_refresh")

def poll_notifications():
    if not st.session_state["token"]:
        return
    since = st.session_state.get("last_notifications_ts", 0)
    try:
        r = requests.get(f"{API_BASE}/notifications/poll", params={"since": since}, headers=auth_headers(), timeout=10)
    except Exception:
        return
    if not r.ok:
        return
    data = r.json()
    notifs = data.get("notifications", [])
    if notifs:
        for n in notifs:
            st.success(f"🔔 Reminder: {n.get('title')}")
            try:
                with open("ding.mp3", "rb") as f:
                    st.audio(f.read())
            except Exception:
                pass
            try:
                max_when = max([int(x.get("when", 0)) for x in notifs])
            except Exception:
                max_when = since
            st.session_state["last_notifications_ts"] = max(st.session_state.get("last_notifications_ts", 0), max_when)

poll_notifications()
