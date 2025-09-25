import sqlite3
import os
from datetime import datetime

DB_PATH = "sessions.db"

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        user_id TEXT PRIMARY KEY,
        lang TEXT,
        frozen INTEGER DEFAULT 0,
        frozen_mode TEXT,
        taken_by TEXT,
        reply_count INTEGER DEFAULT 0,
        greeted INTEGER DEFAULT 0,
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS qna_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        asked TEXT,
        answer TEXT,
        lang TEXT,
        intent TEXT,
        after_hours INTEGER,
        frozen INTEGER,
        status TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def get_session(user_id):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO sessions (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        return {"lang": None, "frozen": 0, "frozen_mode": None, "taken_by": None,
                "reply_count": 0, "greeted": 0}
    return dict(row)

def set_lang(user_id, lang):
    conn = _get_conn()
    conn.execute("UPDATE sessions SET lang=?, updated=CURRENT_TIMESTAMP WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()

def freeze(user_id, state: bool, mode="user", taken_by=None):
    conn = _get_conn()
    conn.execute("UPDATE sessions SET frozen=?, frozen_mode=?, taken_by=?, updated=CURRENT_TIMESTAMP WHERE user_id=?",
                 (1 if state else 0, mode if state else None, taken_by, user_id))
    conn.commit()
    conn.close()

def update_reply_state(user_id):
    conn = _get_conn()
    conn.execute("UPDATE sessions SET reply_count=reply_count+1, updated=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def mark_greeted(user_id):
    conn = _get_conn()
    conn.execute("UPDATE sessions SET greeted=1, updated=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def log_qna(user_id, asked, answer, lang, intent, after_hours, frozen, status="ok"):
    conn = _get_conn()
    conn.execute("""
    INSERT INTO qna_log (user_id, asked, answer, lang, intent, after_hours, frozen, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, asked, answer, lang, intent, int(after_hours), int(frozen), status))
    conn.commit()
    conn.close()
