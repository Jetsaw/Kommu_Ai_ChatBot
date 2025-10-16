import os
import requests
import logging
import sqlite3
from datetime import datetime
from typing import Optional

# ----------------- Setup -----------------
log = logging.getLogger(__name__)

META_TOKEN = os.getenv("META_PERMANENT_TOKEN", "")

# Safe writable directories inside container
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MEDIA_CACHE_DIR = os.path.join(BASE_DIR, "media")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# Use dedicated DB file inside /app/data/
DB_PATH = os.path.join(DATA_DIR, "media_log.db")


# ----------------- Database Helpers -----------------
def _db():
    """Open a connection to the local media_log SQLite DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_media_log():
    """Ensure media_log table exists"""
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS media_log (
                id TEXT PRIMARY KEY,
                sender TEXT,
                type TEXT,
                caption TEXT,
                mime_type TEXT,
                path TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()
        print(f"[MediaLog] media_log.db initialized successfully at {DB_PATH}")
    except Exception as e:
        print(f"[MediaLog ERROR] Failed to initialize DB: {e}")
        raise


def insert_media_record(media_id, sender, mtype, caption, mime, path):
    """Insert or update a media record."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO media_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        (media_id, sender, mtype, caption, mime, path, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


# ----------------- Meta Media Handling -----------------
def get_media_url(media_id: str) -> Optional[str]:
    """Request temporary download URL from Meta"""
    if not META_TOKEN:
        log.error("META_PERMANENT_TOKEN missing")
        return None
    try:
        r = requests.get(
            f"https://graph.facebook.com/v17.0/{media_id}",
            headers={"Authorization": f"Bearer {META_TOKEN}"},
            timeout=15,
        )
        if r.ok:
            return r.json().get("url")
        log.warning(f"Failed to get media URL: {r.text}")
    except Exception as e:
        log.error(f"get_media_url error: {e}")
    return None


def guess_extension_from_type(mime_type: str) -> str:
    if "image" in mime_type:
        return ".jpg"
    if "audio" in mime_type:
        return ".ogg"
    if "video" in mime_type:
        return ".mp4"
    if "pdf" in mime_type:
        return ".pdf"
    return ".bin"


def download_media(media_url: str, media_id: str, ext: str) -> Optional[str]:
    """Download file and return saved path"""
    try:
        headers = {"Authorization": f"Bearer {META_TOKEN}"}
        r = requests.get(media_url, headers=headers, timeout=30)
        if not r.ok:
            log.warning(f"Download failed {r.status_code}")
            return None
        filename = f"{media_id}{ext}"
        path = os.path.join(MEDIA_CACHE_DIR, filename)
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    except Exception as e:
        log.error(f"download_media error: {e}")
        return None


def handle_incoming_media(msg: dict, sender_id: str, add_message_to_history):
    """
    Detect and process WhatsApp media messages.
    Returns True if handled.
    """
    msg_type = msg.get("type", "text")
    if msg_type == "text":
        return False

    media = msg.get(msg_type, {})
    media_id = media.get("id")
    caption = media.get("caption", "")
    mime = media.get("mime_type", "")
    ext = guess_extension_from_type(mime)

    media_url = get_media_url(media_id)
    if not media_url:
        add_message_to_history(sender_id, "user", f"[{msg_type.upper()}] (could not get URL)")
        return True

    path = download_media(media_url, media_id, ext)
    if not path:
        add_message_to_history(sender_id, "user", f"[{msg_type.upper()}] (download failed)")
        return True

    # Log in database
    insert_media_record(media_id, sender_id, msg_type, caption, mime, path)

    note = f"[{msg_type.upper()}] {caption or mime}\nSaved at: {path}"
    add_message_to_history(sender_id, "user", note)
    log.info(f"Media saved: {path}")
    return True
