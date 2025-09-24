import sqlite3, os
from datetime import datetime

DB_FILE = os.getenv("SESSION_DB", "sessions.db")

DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id TEXT PRIMARY KEY,
    lang TEXT DEFAULT 'EN',
    frozen INTEGER DEFAULT 0,
    frozen_mode TEXT DEFAULT 'user',   -- 'user' or 'agent'
    taken_by TEXT DEFAULT NULL,        -- which agent took over
    reply_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_QNA = """
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

def _connect():
    con = sqlite3.connect(DB_FILE)
    con.execute(DDL_SESSIONS)
    con.execute(DDL_QNA)
    return con

# ----------------- Session State -----------------
def get_session(user_id: str):
    with _connect() as con:
        cur = con.execute("SELECT lang,frozen,frozen_mode,taken_by,reply_count,fail_count FROM sessions WHERE user_id=?",(user_id,))
        row = cur.fetchone()
        if not row:
            return {"lang":"EN","frozen":0,"frozen_mode":"user","taken_by":None,"reply_count":0,"fail_count":0}
        return {"lang":row[0],"frozen":row[1],"frozen_mode":row[2],"taken_by":row[3],"reply_count":row[4],"fail_count":row[5]}

def set_lang(user_id: str, lang: str):
    with _connect() as con:
        con.execute("INSERT INTO sessions(user_id,lang) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET lang=?",
                    (user_id, lang, lang))
        con.commit()

def freeze(user_id: str, state=True, mode="user", taken_by=None):
    with _connect() as con:
        con.execute("""
            INSERT INTO sessions(user_id,frozen,frozen_mode,taken_by)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET frozen=?, frozen_mode=?, taken_by=?
        """,(user_id, 1 if state else 0, mode, taken_by, 1 if state else 0, mode, taken_by))
        con.commit()

def update_reply_state(user_id: str, fail_inc=False):
    with _connect() as con:
        sess = get_session(user_id)
        reply_count = sess["reply_count"]+1
        fail_count = sess["fail_count"] + (1 if fail_inc else 0)
        con.execute("INSERT INTO sessions(user_id,reply_count,fail_count,last_updated) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET reply_count=?, fail_count=?, last_updated=?",
                    (user_id, reply_count, fail_count, datetime.utcnow(),
                     reply_count, fail_count, datetime.utcnow()))
        con.commit()

# ----------------- Logging -----------------
def log_qna(user_id, asked, answer, lang, intent, after_hours, frozen, status="ok"):
    """Logs each Q/A interaction for tracking & gap analysis"""
    with _connect() as con:
        con.execute("""
            INSERT INTO qna_log (user_id, asked, answer, lang, intent, after_hours, frozen, status)
            VALUES (?,?,?,?,?,?,?,?)
        """, (user_id, asked, answer, lang, intent, 1 if after_hours else 0, 1 if frozen else 0, status))
        con.commit()

# ----------------- Maintenance Queries -----------------
def get_unanswered(limit=50):
    """Return the most recent unanswered questions (status='unanswered')"""
    with _connect() as con:
        cur = con.execute("SELECT id,user_id,asked,created_at FROM qna_log WHERE status='unanswered' ORDER BY created_at DESC LIMIT ?",(limit,))
        return cur.fetchall()

def export_all_logs():
    """Export full logs for auditing (for CS team to analyze)"""
    with _connect() as con:
        cur = con.execute("SELECT * FROM qna_log ORDER BY created_at DESC")
        return cur.fetchall()
