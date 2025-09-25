from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response, HTMLResponse
from datetime import datetime
import pytz, re, os, json, sqlite3, traceback
from xml.sax.saxutils import escape
from logging.handlers import RotatingFileHandler
import logging

from config import (
    TZ_REGION, OFFICE_START, OFFICE_END, PORT,
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER,
    CS_RECIPIENTS, AGENT_NUMBERS,
    SOP_DOC_URL, WARRANTY_CSV_URL,
    RAG_DIR, SOP_JSON_PATH, ADMIN_TOKEN
)

from lang_detect import is_malay
from deepseek_client import chat_completion
from rag.rag import RAGEngine
from rag.rebuild_index_combined import rebuild as rebuild_rag
from sop_doc_loader import fetch_sop_doc_text, parse_qas_from_text
from google_sheets import (
    fetch_warranty_all, warranty_lookup_by_dongle, warranty_text_from_row
)

# session state helpers (and optional init hook)
from session_state import get_session, set_lang, freeze, update_reply_state, log_qna
try:
    from session_state import init_db as _init_sessions_db
except Exception:
    _init_sessions_db = None

from web_scraper import scrape as scrape_site
from fastapi_utils.tasks import repeat_every
from twilio.rest import Client as TwilioClient


# ----------------- Logging -----------------
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

DEBUG_QA = os.getenv("DEBUG_QA", "1") == "1"
app = FastAPI(title="Kai - Kommu Chatbot")


# ----------------- Utilities -----------------
def is_office_hours(now=None):
    tz = pytz.timezone(TZ_REGION)
    now = now or datetime.now(tz)
    return now.weekday() < 5 and OFFICE_START <= now.hour < OFFICE_END

def after_hours_suffix(lang="EN"):
    return ("\n\nPS: Sekarang di luar waktu pejabat."
            if lang == "BM" else "\n\nPS: We’re currently after-hours.")

def now_myt_str():
    tz = pytz.timezone(TZ_REGION)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

def has_any(words, text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)

def looks_english(text: str) -> bool:
    t = f" { (text or '').lower() } "
    en_hits = sum(w in t for w in [
        " the ", " and ", " to ", " is ", " are ", " you ",
        " we ", " will ", " please ", " support "
    ])
    bm_hits = sum(w in t for w in [
        " dan ", " ialah ", " anda ", " kami ", " akan ",
        " sila ", " waktu ", " alamat ", " gantian ", " bahagian "
    ])
    return en_hits >= 2 and bm_hits == 0

def translate_to_bm(text: str) -> str:
    sys = "You are a professional Malay translator. Output only the translation in Malay. No extra commentary. No emojis."
    prompt = f"Terjemahkan ke Bahasa Melayu (Bahasa Malaysia) dengan nada profesional:\n\n{text}"
    try:
        out = chat_completion(sys, prompt)
        return out or ""
    except Exception:
        return ""

def twiml(message: str) -> Response:
    body = escape(message or "", {'"': "&quot;", "'": "&apos;"})
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{body}</Message></Response>'
    return Response(content=xml, media_type="text/xml; charset=utf-8")

def _log_and_twiml(wa_from, asked, answer, lang, intent, after_hours, frozen, status="ok"):
    try:
        log_qna(wa_from, asked, answer, lang, intent, after_hours, frozen, status)
    finally:
        return twiml(answer)


# ----------------- Ensure sessions DB exists (safety net) -----------------
def _ensure_sessions_db():
    """
    If session_state.init_db is available, call it.
    Otherwise create minimal tables used by log_qna/get_session.
    """
    if _init_sessions_db:
        try:
            _init_sessions_db()
            return
        except Exception as e:
            log.error(f"[sessions] init_db failed: {e}")

    # Minimal schema fallback (matches session_state.py expectations)
    try:
        conn = sqlite3.connect("sessions.db")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              user_id TEXT PRIMARY KEY,
              lang TEXT,
              reply_count INTEGER DEFAULT 0,
              frozen INTEGER DEFAULT 0,
              frozen_mode TEXT,
              taken_by TEXT,
              greeted INTEGER DEFAULT 0,
              updated_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qna (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id TEXT,
              last_question TEXT,
              last_answer TEXT,
              lang TEXT,
              intent TEXT,
              after_hours INTEGER,
              frozen INTEGER,
              status TEXT,
              ts TEXT
            )
        """)
        conn.commit()
        conn.close()
        log.info("[sessions] Tables ensured")
    except Exception as e:
        log.error(f"[sessions] ensure failed: {e}")


# ----------------- Escalation hint -----------------
def maybe_add_la_hint(user_id, msg, lang):
    update_reply_state(user_id)
    sess = get_session(user_id)
    if sess["reply_count"] >= 2:
        hint = " Jika perlu ejen manusia, taip LA." if lang == "BM" else " If you need a live agent, type LA."
        msg += hint
    return msg


# ----------------- CS forwarding -----------------
def summarize_for_agent(user_text: str, lang: str):
    sys = "You summarize customer WhatsApp issues for internal CS. Output 2-4 lines, no emojis."
    prompt = (
        f"Customer message ({'Malay' if lang=='BM' else 'English'}):\n{user_text}\n\n"
        "Summarize the request, including any car model/variant/year if present."
    )
    s = chat_completion(sys, prompt)
    return (s or user_text).strip()

def forward_to_cs(wa_from: str, summary_text: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER and CS_RECIPIENTS):
        print("[CS-FWD] Missing Twilio env or CS_RECIPIENTS; skipping forward.")
        return
    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    ts = now_myt_str()
    msg = f"[Kai] Live-agent request\nTime: {ts}\nFrom: {wa_from}\nSummary:\n{summary_text}"
    # CS_RECIPIENTS should be list; if string, wrap it
    recipients = CS_RECIPIENTS if isinstance(CS_RECIPIENTS, (list, tuple)) else [CS_RECIPIENTS]
    for to in recipients:
        try:
            client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=to, body=msg)
        except Exception as e:
            print(f"[CS-FWD] send fail to {to}: {e}")


# ----------------- RAG + LLM -----------------
def run_rag(user_text: str, lang_hint: str = "EN", intent_hint: str | None = None) -> str:
    if not rag:
        return ""

    context = rag.build_context(user_text, topk=4)

    intent_guidance = {
        "about":      "Briefly explain what Kommu/KommuAssist is and the main benefits. Include exactly one link.",
        "how":        "Explain briefly how KommuAssist works. One short paragraph, max one link.",
        "buy":        "Buying steps; include https://kommu.ai/products/ and https://kommu.ai/faq/",
        "hours":      "Office hours Mon–Fri 10:00–18:00 MYT, include address and Waze link.",
        "test_drive": "Offer test drive link: https://calendly.com/kommuassist/test-drive?month=2025-08",
    }
    guide = intent_guidance.get(intent_hint, "")

    sys = (
        "You are Kai, Kommu’s friendly assistant.\n"
        "- Always reply in the user's language (Malay users → BM).\n"
        "- No emojis. No tables. Max 2 links.\n"
        "- Use ONLY the provided context."
    )
    lang_instruction = (
        "Tulis jawapan 100% dalam Bahasa Melayu (Bahasa Malaysia)."
        if lang_hint == "BM" else
        "Write the final answer in English."
    )

    prompt = (
        f"User message: {user_text}\n\n"
        f"Context (top SOP matches):\n{context}\n\n"
        f"{lang_instruction}\n{guide}\n\n"
        "Write a concise, helpful answer."
    )

    try:
        llm = chat_completion(sys, prompt)
    except Exception as e:
        log.info(f"[Kai] ERR chat_completion: {e}")
        llm = ""

    if llm and llm.strip():
        out = llm.strip()
        if lang_hint == "BM" and looks_english(out):
            fixed = translate_to_bm(out) or out
            return fixed
        return out

    # Basic fallback if LLM returns nothing
    return ""


# ----------------- RAG load on startup -----------------
def load_rag():
    global rag
    try:
        rag = RAGEngine(k=4)
        log.info("[Kai] RAG loaded")
    except Exception as e:
        log.info(f"[Kai] RAG not available: {e}")
        rag = None


rag = None
try:
    _ensure_sessions_db()
    if SOP_DOC_URL:
        txt = fetch_sop_doc_text()
        qas = parse_qas_from_text(txt)
        if qas:
            os.makedirs(RAG_DIR, exist_ok=True)
            with open(SOP_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(qas, f, ensure_ascii=False, indent=2)
            rebuild_rag()
            load_rag()
            print(f"[SOP-DOC] Loaded {len(qas)} Q/A from Google Doc and rebuilt RAG.")
        else:
            print("[SOP-DOC] Parsed 0 Q/A.")
    else:
        load_rag()
    fetch_warranty_all()
except Exception as e:
    print("[Startup] Error:", e)


@app.on_event("startup")
@repeat_every(seconds=86400)   # every 24h
def auto_refresh():
    try:
        print("[AutoRefresh] Refreshing SOP + website…")
        if SOP_DOC_URL:
            txt = fetch_sop_doc_text()
            qas = parse_qas_from_text(txt)
            if qas:
                os.makedirs(RAG_DIR, exist_ok=True)
                with open(SOP_JSON_PATH, "w", encoding="utf-8") as f:
                    json.dump(qas, f, ensure_ascii=False, indent=2)
                rebuild_rag()
                load_rag()
        scrape_site()
        fetch_warranty_all()
        print("[AutoRefresh] Done")
    except Exception as e:
        print("[AutoRefresh] Error", e)


# ----------------- Routes -----------------
@app.get("/", response_class=PlainTextResponse)
@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "Kai alive"


@app.api_route("/status_callback", methods=["GET", "POST"])
async def status_callback(_: Request):
    return PlainTextResponse("OK")


@app.api_route("/admin/refresh_sheets", methods=["GET", "POST"])
async def refresh_sheets(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", status_code=403)
    try:
        if SOP_DOC_URL:
            txt = fetch_sop_doc_text()
            qas = parse_qas_from_text(txt)
            if qas:
                os.makedirs(RAG_DIR, exist_ok=True)
                with open(SOP_JSON_PATH, "w", encoding="utf-8") as f:
                    json.dump(qas, f, ensure_ascii=False, indent=2)
                rebuild_rag()
                load_rag()
        fetch_warranty_all()
        return PlainTextResponse("OK")
    except Exception as e:
        return PlainTextResponse(f"ERR: {e}", status_code=500)


# ---- Simple live dashboard (last 50 rows) ----
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    token = request.query_params.get("token", "")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", status_code=403)
    try:
        conn = sqlite3.connect("sessions.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT ts, user_id, lang, intent, after_hours, frozen, status, last_question, last_answer
            FROM qna ORDER BY ts DESC LIMIT 50
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return PlainTextResponse(f"DB error: {e}", status_code=500)

    style = """
    <style>
      body{font-family:system-ui,Arial,sans-serif;padding:18px;}
      table{border-collapse:collapse;width:100%;}
      th,td{border:1px solid #ccc;padding:8px;vertical-align:top;}
      th{background:#f5f5f5;}
      tr:nth-child(odd){background:#fafafa;}
      code{white-space:pre-wrap;}
    </style>
    """
    html = ["<html><head><title>Kai Dashboard</title>", style, "</head><body>"]
    html += ["<h2>Kai Live Dashboard (last 50)</h2>",
             "<table><tr><th>Time</th><th>User</th><th>Lang</th><th>Intent</th><th>AH</th><th>Frozen</th><th>Status</th><th>Q</th><th>A</th></tr>"]
    for ts, user, lang, intent, ah, frz, st, q, a in rows:
        html.append(f"<tr><td>{ts}</td><td>{user}</td><td>{lang}</td><td>{intent}</td>"
                    f"<td>{ah}</td><td>{frz}</td><td>{st}</td>"
                    f"<td><code>{(q or '')}</code></td><td><code>{(a or '')}</code></td></tr>")
    html += ["</table></body></html>"]
    return HTMLResponse("".join(html))


@app.get("/debug/state", response_class=PlainTextResponse)
async def debug_state():
    return "Sessions stored in SQLite (sessions.db). Use maintainer queries to inspect unanswered Qs."


# ----------------- Webhook -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        form = await request.form()
        body = (form.get("Body") or "").strip()
        wa_from = form.get("From") or ""
        log.info(f"[Kai] IN From={wa_from!r} Body={body!r}")

        if not body:
            return _log_and_twiml(wa_from, body, "", "EN", "empty", False, False)

        lower = norm(body)
        sess = get_session(wa_from)
        lang = sess["lang"] or ("BM" if is_malay(body) else "EN")
        set_lang(wa_from, lang)
        aft = not is_office_hours()

        # -------- Agent Commands --------
        if wa_from in AGENT_NUMBERS:
            if lower.startswith("take "):
                tgt = body.split(" ", 1)[1].strip()
                freeze(tgt, True, mode="agent", taken_by=wa_from)
                return _log_and_twiml(wa_from, body, f"Taken & frozen: {tgt}", "EN", "agent_cmd", aft, True)
            if lower.startswith("resume "):
                tgt = body.split(" ", 1)[1].strip()
                freeze(tgt, False, mode="user", taken_by=None)
                return _log_and_twiml(wa_from, body, f"Resumed bot for: {tgt}", "EN", "agent_cmd", aft, False)
            return _log_and_twiml(wa_from, body, "Agent commands: TAKE +6011xxxx, RESUME +6011xxxx", "EN", "agent_cmd", aft, False)

        # -------- Frozen Handling --------
        if sess["frozen"]:
            if sess["frozen_mode"] == "user" and lower in {"resume", "unfreeze", "sambung"}:
                freeze(wa_from, False, mode="user")
                msg = "Bot resumed. How can I help?" if lang != "BM" else "Bot disambung semula. Ada apa yang boleh saya bantu?"
                return _log_and_twiml(wa_from, body, msg, lang, "resume", aft, False)

            if lang == "BM":
                msg = ("Ejen akan menghubungi anda sekejap lagi."
                       "\n\nPautan berguna:\n- FAQ / Sokongan: https://kommu.ai/faq/\n- Kereta Disokong: https://kommu.ai/support/\n"
                       "- Komuniti FB: https://web.facebook.com/groups/kommu.official/\n- Discord: https://discord.gg/CP9ZpsXWqH")
            else:
                msg = ("A live agent will get back to you shortly."
                       "\n\nUseful links:\n- FAQ / Support: https://kommu.ai/faq/\n- Supported Cars: https://kommu.ai/support/\n"
                       "- FB Group: https://web.facebook.com/groups/kommu.official/\n- Discord: https://discord.gg/CP9ZpsXWqH")
            return _log_and_twiml(wa_from, body, msg, lang, "frozen_ack", aft, True)

        # -------- User Requests Live Agent --------
        if has_any(["la", "human", "request human"], lower):
            freeze(wa_from, True, mode="user")
            sm = summarize_for_agent(body, lang)
            forward_to_cs(wa_from, sm)
            if lang == "BM":
                msg = ("Seorang ejen manusia akan hubungi anda pada waktu pejabat. Chat dibekukan."
                       "\n\nPautan berguna:\n- FAQ / Sokongan: https://kommu.ai/faq/\n- Kereta Disokong: https://kommu.ai/support/\n"
                       "- Komuniti FB: https://web.facebook.com/groups/kommu.official/\n- Discord: https://discord.gg/CP9ZpsXWqH")
            else:
                msg = ("A live agent will reach out during office hours. Chat is now frozen."
                       "\n\nUseful links:\n- FAQ / Support: https://kommu.ai/faq/\n- Supported Cars: https://kommu.ai/support/\n"
                       "- FB Group: https://web.facebook.com/groups/kommu.official/\n- Discord: https://discord.gg/CP9ZpsXWqH")
            return _log_and_twiml(wa_from, body, msg, lang, "live_agent", aft, True)

        # -------- Greeting (only once) --------
        if has_any(["hi", "hello", "start", "mula", "hai", "helo", "menu"], lower) and not sess.get("greeted"):
            if lang == "BM":
                msg = ("Hai! Saya Kai - Chatbot Kommu\n"
                       "[Perbualan ini dikendalikan oleh chatbot dan sedang dalam ujian beta. "
                       "Ia diselia oleh manusia semasa waktu pejabat.]")
            else:
                msg = ("Hi! I'm Kai - Kommu Chatbot\n"
                       "[The conversation is handled by a chatbot and is under beta testing. "
                       "It is supervised by a human during working hours]")
            if aft:
                msg += after_hours_suffix(lang)
            # mark greeted in DB
            freeze(wa_from, False, mode="user")  # ensure row exists/updated
            sess2 = get_session(wa_from)
            sess2["greeted"] = 1
            # Persist greeted flag
            try:
                conn = sqlite3.connect("sessions.db"); cur = conn.cursor()
                cur.execute("UPDATE sessions SET greeted=1, updated_at=? WHERE user_id=?",
                            (now_myt_str(), wa_from))
                conn.commit(); conn.close()
            except Exception as e:
                log.error(f"[greeted] persist failed: {e}")
            return _log_and_twiml(wa_from, body, msg, lang, "greeting", aft, False)

        # -------- Warranty Direct Lookup --------
        if 6 <= len(lower) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg = (f"Status waranti: {warranty_text_from_row(row)}" if lang == "BM"
                       else f"Warranty status: {warranty_text_from_row(row)}")
                if aft:
                    msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # -------- Intent-based RAG --------
        if has_any(["buy", "beli", "order", "purchase", "tempah", "price", "harga"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="buy")
            if msg:
                if aft: msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "buy", aft, False)

        if has_any(["office", "waktu", "pejabat", "hour", "hours", "open", "close", "alamat", "address"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="hours")
            if msg:
                if aft: msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "hours", aft, False)

        if has_any(["test", "drive", "demo", "try", "pandu", "uji", "appointment", "book"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="test_drive")
            if msg:
                if aft: msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "test_drive", aft, False)

        # -------- Default RAG --------
        answer = run_rag(body, lang_hint=lang)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            answer = maybe_add_la_hint(wa_from, answer, lang)
            return _log_and_twiml(wa_from, body, answer, lang, "default", aft, False)

        # -------- Hard Fallback --------
        msg = ("Saya boleh bantu harga, pemasangan, waktu pejabat, waranti, dan pandu uji. "
               "Cuba: 'Beli Kommu', 'Apa itu Kommu', 'Bagaimana ia berfungsi', 'Waktu pejabat', 'Pandu uji'."
               if lang == "BM" else
               "I can help with price, installation, office hours, warranty, and test drives. "
               "Try: 'Buy Kommu', 'What is Kommu', 'How does it work', 'Office time', 'Test drive'.")
        if aft:
            msg += after_hours_suffix(lang)
        msg = maybe_add_la_hint(wa_from, msg, lang)
        return _log_and_twiml(wa_from, body, msg, lang, "fallback", aft, False, status="unanswered")

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[Kai] FATAL in webhook: {e}\n{tb}")
        return _log_and_twiml(
            wa_from if 'wa_from' in locals() else "",
            body if 'body' in locals() else "",
            "Sorry, internal error. Please try again or type LA.",
            "EN",
            "error",
            False,
            False,
            status="error"
        )
