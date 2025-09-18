import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# 1) Load secrets from .env
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "changeme")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

print("🔑 Loaded configuration:")
print(f"SECRET_KEY: {SECRET_KEY[:8]}... (hidden)")
print(f"SMTP_HOST: {SMTP_HOST}")
print(f"SMTP_USER: {SMTP_USER}")
print("SMTP_PASSWORD: ******** (hidden)")

# 2) Init SQLite database
DB_PATH = "reminder_agent.db"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Create users table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at INTEGER
);
""")

# Create reminders table with snooze, recurrence, categories, tags
cursor.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    title TEXT,
    body TEXT,
    when_ts INTEGER,
    created_at INTEGER,
    status TEXT,
    snooze_until INTEGER,        -- unix ts for snooze
    recurrence TEXT,             -- e.g., "daily", "weekly", or cron-like JSON
    repeat_interval INTEGER,     -- seconds between repeats
    category TEXT,
    tags TEXT,                   -- comma-separated tags
    FOREIGN KEY (user_id) REFERENCES users(id)
);
""")

conn.commit()
conn.close()

print(f"✅ Database initialized at {DB_PATH} with users + reminders schema.")
