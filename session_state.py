import sqlite3, json, os
from datetime import datetime
from config import MEMORY_DEPTH

# ----------------- Database Path Setup -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)  # ensure writable folder inside container
DB_PATH = os.getenv("SESSION_DB_PATH", os.path.join(DATA_DIR, "sessions.db"))


# ----------------- Database Init -----------------
def init_db():
    try:
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
        print(f"[DB] sessions.db initialized successfully at {DB_PATH}")
    except Exception as e:
        print(f"[DB ERROR] Failed to init database: {e}")
        raise


# ----------------- Core Session Ops -----------------
def get_session(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT data FROM sessions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
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


# ----------------- State Updates -----------------
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
    sess["logs"] = logs[-50:]  # Keep last 50 pairs
    save_session(user_id, sess)


def set_last_intent(user_id: str, intent: str | None):
    sess = get_session(user_id)
    sess["last_intent"] = intent
    save_session(user_id, sess)


def get_last_intent(user_id: str):
    sess = get_session(user_id)
    return sess.get("last_intent")


# ----------------- Multi-Turn Memory -----------------
def add_message_to_history(user_id: str, role: str, text: str):
    """Append message to session history, keeping only MEMORY_DEPTH turns."""
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


# ----------------- Memory Reset Helpers -----------------
def reset_memory(user_id: str | None = None):
    """Reset memory for a specific user or all users."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user_id:
        default = {
            "lang": None,
            "frozen": False,
            "reply_count": 0,
            "greeted": False,
            "last_intent": None,
            "history": []
        }
        c.execute("REPLACE INTO sessions (user_id, data) VALUES (?,?)", (user_id, json.dumps(default)))
    else:
        c.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()


# ----------------- Utility -----------------
def get_all_user_ids():
    """Return list of all active session user_ids (for admin view/logging)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM sessions")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

# ----------------- Backward Compatibility Shim -----------------
def set_session(user_id: str, data: dict):
    """Legacy alias for save_session (for backward compatibility)."""
    save_session(user_id, data)
