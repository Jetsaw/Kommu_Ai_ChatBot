# session_state.py
import sqlite3
import os
import datetime

DB_PATH = "sessions.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        user_id TEXT PRIMARY KEY,
        lang TEXT,
        frozen INTEGER DEFAULT 0,
        frozen_mode TEXT,
        reply_count INTEGER DEFAULT 0,
        greeted INTEGER DEFAULT 0,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS qna_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        question TEXT,
        answer TEXT,
        lang TEXT,
        intent TEXT,
        after_hours INTEGER,
        frozen INTEGER,
        status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

# ----------------- Session helpers -----------------
def get_session(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,))
    row = cur.fetchone()

    # Check expiry (1 hour)
    if row:
        try:
            last_seen = datetime.datetime.strptime(row["last_seen"], "%Y-%m-%d %H:%M:%S")
            if (datetime.datetime.now() - last_seen).total_seconds() > 3600:  # 1 hour
                cur.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                conn.commit()
                row = None
        except Exception:
            # if parsing error, reset session
            cur.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
            row = None

    # Create new session if missing or expired
    if not row:
        cur.execute("""
        INSERT INTO sessions (user_id, lang, frozen, frozen_mode, reply_count, greeted, last_seen)
        VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """, (user_id, None, 0, None, 0, 0))
        conn.commit()
        cur.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,))
        row = cur.fetchone()

    # Always update last_seen
    cur.execute("UPDATE sessions SET last_seen=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return dict(row)

def set_lang(user_id: str, lang: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET lang=?, last_seen=CURRENT_TIMESTAMP WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()

def freeze(user_id: str, frozen: bool, mode: str = "user", taken_by: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET frozen=?, frozen_mode=?, last_seen=CURRENT_TIMESTAMP WHERE user_id=?",
                (1 if frozen else 0, mode if frozen else None, user_id))
    conn.commit()
    conn.close()

def update_reply_state(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET reply_count=reply_count+1, last_seen=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def log_qna(user_id: str, question: str, answer: str, lang: str, intent: str, after_hours: bool, frozen: bool, status: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO qna_log (user_id, question, answer, lang, intent, after_hours, frozen, status)
    VALUES (?,?,?,?,?,?,?,?)
    """, (user_id, question, answer, lang, intent, 1 if after_hours else 0, 1 if frozen else 0, status))
    conn.commit()
    conn.close()

# Initialize DB automatically
init_db()
