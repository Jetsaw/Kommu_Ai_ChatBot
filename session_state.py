import sqlite3, json, os
from datetime import datetime
from config import MEMORY_DEPTH

DB_PATH = os.getenv("SESSION_DB_PATH", "sessions.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        user_id TEXT PRIMARY KEY,
        data TEXT
    )
    """)
    conn.commit()
    conn.close()

def get_session(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT data FROM sessions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except:
            return {}
    return {
        "lang": None,
        "frozen": False,
        "reply_count": 0,
        "greeted": False,
        "last_intent": None,
        "history": []   
    }

def save_session(user_id: str, data: dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO sessions (user_id, data) VALUES (?,?)", (user_id, json.dumps(data)))
    conn.commit()
    conn.close()

def set_lang(user_id: str, lang: str):
    sess = get_session(user_id)
    sess["lang"] = lang
    save_session(user_id, sess)

def freeze(user_id: str, frozen: bool, mode="user"):
    sess = get_session(user_id)
    sess["frozen"] = frozen
    save_session(user_id, sess)

def update_reply_state(user_id: str):
    sess = get_session(user_id)
    sess["reply_count"] = sess.get("reply_count", 0) + 1
    save_session(user_id, sess)

def log_qna(user_id: str, q: str, a: str):
    sess = get_session(user_id)
    logs = sess.get("logs", [])
    logs.append({"q": q, "a": a, "t": datetime.utcnow().isoformat()})
    sess["logs"] = logs[-50:]  # keep last 50
    save_session(user_id, sess)

def set_last_intent(user_id: str, intent: str | None):
    sess = get_session(user_id)
    sess["last_intent"] = intent
    save_session(user_id, sess)

def get_last_intent(user_id: str):
    sess = get_session(user_id)
    return sess.get("last_intent")

# ----------------- Multi-turn Memory -----------------
def add_message_to_history(user_id: str, role: str, text: str):
    """Append a message to session history, keeping only MEMORY_DEPTH turns."""
    sess = get_session(user_id)
    history = sess.get("history", [])
    history.append({"role": role, "text": text})
    if len(history) > MEMORY_DEPTH:
        history = history[-MEMORY_DEPTH:]
    sess["history"] = history
    save_session(user_id, sess)

def get_history(user_id: str):
    """Retrieve the recent conversation history."""
    sess = get_session(user_id)
    return sess.get("history", [])
