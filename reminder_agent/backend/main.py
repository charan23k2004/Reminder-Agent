import os
import time
import json
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, status, Header
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
import sqlite3
import jwt
from passlib.context import CryptContext
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "changeme")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB_PATH = "backend/reminders_with_users.db"
os.makedirs("backend", exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER
                )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    title TEXT,
                    body TEXT,
                    when_ts INTEGER,
                    created_at INTEGER,
                    status TEXT,
                    snooze_until INTEGER,
                    recurrence TEXT,
                    repeat_interval INTEGER,
                    category TEXT,
                    tags TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )""")
    conn.commit()
    conn.close()


init_db()

app = FastAPI(title="Reminder Agent Backend")

jobstore_url = f"sqlite:///{DB_PATH}"
scheduler = BackgroundScheduler(jobstores={'default': SQLAlchemyJobStore(url=jobstore_url)})
scheduler.start()

# ---------- Utils ----------
def hash_password(password: str):
    return pwd_ctx.hash(password)


def verify_password(password, hashed):
    return pwd_ctx.verify(password, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        to_encode.update({"exp": time.time() + expires_delta.total_seconds()})
    return jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")


def decode_token(token: str):
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return data
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def get_user_by_email(email: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,email,password_hash,created_at FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "email": row[1], "password_hash": row[2], "created_at": row[3]}
    return None


def get_user_by_id(uid: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,email FROM users WHERE id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "email": row[1]}
    return None


def get_current_user(authorization: Optional[str] = Header(None)):
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid auth header (expected 'Bearer <token>')")
        token = authorization.split(" ", 1)[1]
        data = decode_token(token)
        uid = int(data.get("sub"))
        user = get_user_by_id(uid)
        if not user:
            raise HTTPException(401, "User from token not found")
        return user
    fallback = get_user_by_id(1)
    if fallback:
        return fallback
    raise HTTPException(401, "Authorization required.")


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/register")
def register(payload: RegisterIn):
    if get_user_by_email(payload.email):
        raise HTTPException(400, "User already exists")
    phash = hash_password(payload.password)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (email,password_hash,created_at) VALUES (?,?,?)",
                (payload.email, phash, int(time.time())))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user["id"])}, expires_delta=timedelta(days=7))
    return {"access_token": token, "token_type": "bearer"}


# ---------- Reminder Helpers ----------
def save_reminder_db(rid, user_id, title, body, when_ts, recurrence=None, repeat_interval=None, category=None, tags=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""INSERT INTO reminders (id,user_id,title,body,when_ts,created_at,status,recurrence,repeat_interval,category,tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, user_id, title, body, when_ts, int(time.time()), 'scheduled', recurrence, repeat_interval, category, tags))
    conn.commit()
    conn.close()


def update_reminder_status(rid, status):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET status=? WHERE id=?", (status, rid))
    conn.commit()
    conn.close()


def load_reminder(rid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,user_id,title,body,when_ts,created_at,status,recurrence,repeat_interval FROM reminders WHERE id=?", (rid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id", "user_id", "title", "body", "when_ts", "created_at", "status", "recurrence", "repeat_interval"]
    return dict(zip(keys, row))


# ---------- Notifier ----------
def send_email(to_email, subject, body):
    if not SMTP_HOST:
        print("SMTP not configured; skipping email.")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(msg["From"], [to_email], msg.as_string())
        return True
    except Exception as e:
        print("SMTP send failed", e)
        return False


def notifier_notify(reminder_id: str):
    rem = load_reminder(reminder_id)
    if not rem:
        print("Reminder not found", reminder_id)
        return
    update_reminder_status(reminder_id, "fired")
    user = get_user_by_id(rem["user_id"])
    if user:
        subject = f"Reminder: {rem['title']}"
        body = f"{rem['body']}\n\nScheduled for: {datetime.utcfromtimestamp(rem['when_ts']).isoformat()}"
        send_email(user["email"], subject, body)
    if rem.get("repeat_interval"):
        next_ts = rem["when_ts"] + rem["repeat_interval"]
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE reminders SET when_ts=?, status='scheduled' WHERE id=?", (next_ts, rem["id"]))
        conn.commit()
        conn.close()
        schedule_job(rem["id"], datetime.utcfromtimestamp(next_ts))


# ---------- Scheduler ----------
def schedule_job(reminder_id: str, when_dt: datetime):
    trigger = DateTrigger(run_date=when_dt)
    job_id = f"job_{reminder_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(func=notifier_notify, trigger=trigger, id=job_id, args=[reminder_id])
    print("Scheduled", job_id, when_dt)


# ---------- Endpoints ----------
class ReminderCreateIn(BaseModel):
    title: str
    body: Optional[str] = ""
    when: datetime
    recurrence: Optional[str] = None
    repeat_interval_seconds: Optional[int] = None
    category: Optional[str] = None
    tags: Optional[str] = None


@app.post("/reminders")
def create_reminder(payload: ReminderCreateIn, current_user: dict = Depends(get_current_user)):
    rid = str(uuid4())
    when_ts = int(payload.when.timestamp())
    save_reminder_db(rid, current_user["id"], payload.title, payload.body, when_ts,
                     payload.recurrence, payload.repeat_interval_seconds, payload.category, payload.tags)
    schedule_job(rid, payload.when)
    return {"id": rid, "scheduled_for": payload.when.isoformat()}


@app.post("/reminders/{rid}/snooze")
def snooze_reminder(rid: str, minutes: int = 5, current_user: dict = Depends(get_current_user)):
    rem = load_reminder(rid)
    if not rem or rem["user_id"] != current_user["id"]:
        raise HTTPException(404, "Not found")
    new_ts = int(time.time()) + minutes * 60
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET snooze_until=?, when_ts=?, status='scheduled' WHERE id=?",
                (new_ts, new_ts, rid))
    conn.commit()
    conn.close()
    schedule_job(rid, datetime.utcfromtimestamp(new_ts))
    return {"id": rid, "snoozed_until": datetime.utcfromtimestamp(new_ts).isoformat()}


@app.post("/reminders/{rid}/cancel")
def cancel_reminder(rid: str, current_user: dict = Depends(get_current_user)):
    rem = load_reminder(rid)
    if not rem or rem["user_id"] != current_user["id"]:
        raise HTTPException(404, "Reminder not found")
    update_reminder_status(rid, "cancelled")
    job_id = f"job_{rid}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    return {"id": rid, "status": "cancelled"}


@app.delete("/reminders/{rid}")
def delete_reminder(rid: str, current_user: dict = Depends(get_current_user)):
    rem = load_reminder(rid)
    if not rem or rem["user_id"] != current_user["id"]:
        raise HTTPException(404, "Reminder not found")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE id=?", (rid,))
    conn.commit()
    conn.close()

    job_id = f"job_{rid}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    return {"id": rid, "status": "deleted"}


@app.get("/reminders")
def get_reminders(current_user: dict = Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,title,body,when_ts,status,category,tags FROM reminders WHERE user_id=?",
                (current_user["id"],))
    rows = cur.fetchall()
    conn.close()
    reminders = []
    for r in rows:
        reminders.append({
            "id": r[0],
            "title": r[1],
            "body": r[2],
            "when": r[3],
            "status": r[4],
            "category": r[5],
            "tags": r[6]
        })
    return reminders


@app.get("/notifications/poll")
def poll_notifications(since: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if since:
        cur.execute("SELECT id,title,body,when_ts FROM reminders WHERE status='fired' AND when_ts> ? AND user_id=?",
                    (since, current_user["id"]))
    else:
        cur.execute("SELECT id,title,body,when_ts FROM reminders WHERE status='fired' AND user_id=?",
                    (current_user["id"],))
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        results.append({"id": r[0], "title": r[1], "body": r[2], "when": r[3]})
    return {"notifications": results}

