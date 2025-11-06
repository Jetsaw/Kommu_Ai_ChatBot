from fastapi import FastAPI, Request, Query, Header
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import pytz, re, os, json, traceback, logging, sqlite3
from logging.handlers import RotatingFileHandler
import requests
from deep_translator import GoogleTranslator
from fastapi_utils.tasks import repeat_every

from config import (
    TZ_REGION, OFFICE_START, OFFICE_END, PORT,
    SOP_DOC_URL, WARRANTY_CSV_URL,
    RAG_DIR, SOP_JSON_PATH, ADMIN_TOKEN,
    MIN_SUPPORTED_YEAR
)
from lang_detect import is_malay
from deepseek_client import chat_completion
from rag.rag import RAGEngine
from rag.rebuild_index_combined import rebuild as rebuild_rag
from sop_doc_loader import fetch_sop_doc_text, parse_qas_from_text
from google_sheets import (
    fetch_warranty_all, warranty_lookup_by_dongle, warranty_text_from_row
)
from session_state import (
    get_session, set_lang, freeze, update_reply_state,
    log_qna, init_db, set_last_intent, get_last_intent,
    add_message_to_history, get_history, reset_memory
)
from media_handler import handle_incoming_media, init_media_log


os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

app = FastAPI(title="Kai - Kommu Chatbot")

# Initialize databases
init_db()
init_media_log()

# Serve media and dashboard UI
app.mount("/media", StaticFiles(directory="media"), name="media")
app.mount("/ui", StaticFiles(directory="kommu-ui/dist", html=True), name="ui")
app.mount("/ui/assets", StaticFiles(directory="kommu-ui/dist/assets"), name="ui-assets")

FOOTER_EN = "\n\nI am Kai, Kommu’s support chatbot (beta). Please send your questions one by one. If you’d like a live agent, type LA."
FOOTER_BM = "\n\nSaya Kai, chatbot sokongan Kommu (beta). Sila hantar soalan anda satu demi satu. Jika anda mahu bercakap dengan ejen manusia, taip LA."

# ----------------- Constants -----------------
MEMORY_LAYERS = 5
CAR_KEYWORDS = [
    "myvi", "alza", "ativa", "perodua", "proton", "s70", "x50", "x70",
    "honda", "city", "accord", "hrv", "crv", "toyota", "vios", "cross",
    "byd", "lexus", "kereta", "support", "compatible"
]

# ----------------- Utility Functions -----------------
def is_office_hours(now=None):
    tz = pytz.timezone(TZ_REGION)
    now = now or datetime.now(tz)
    return now.weekday() < 5 and OFFICE_START <= now.hour < OFFICE_END

def after_hours_suffix(lang="EN"):
    return ("\n\nPS: Sekarang di luar waktu pejabat."
            if lang == "BM"
            else "\n\nPS: We’re currently outside office hours. A live agent will follow up later.")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

def has_any(words, text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)

def add_footer(answer: str, lang: str) -> str:
    footer = FOOTER_BM if lang == "BM" else FOOTER_EN
    return (answer or "").rstrip() + footer

def detect_car_support_query(text: str) -> bool:
    return any(k in text.lower() for k in CAR_KEYWORDS)

def extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None

def parse_year_range(text: str):
    match = re.search(r"(\d{4})[–-](\d{4})", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None
# ----------------- WhatsApp Send -----------------
def send_whatsapp_message(to: str, text: str):
    url = f"https://graph.facebook.com/v17.0/{os.getenv('META_PHONE_NUMBER_ID')}/messages"
    headers = {
        "Authorization": f"Bearer {os.getenv('META_PERMANENT_TOKEN','')}",
        "Content-Type": "application/json"
    }
    payload = {"messaging_product": "whatsapp","to": to,"type": "text","text": {"body": text}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code >= 400:
            log.error(f"[Kai] Send fail {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"[Kai] Send error: {e}")

# ----------------- RAG Dual Engine -----------------
def run_rag_dual(user_text: str, lang_hint: str = "EN", user_id: str | None = None) -> str:
    sys_prompt = (
        "You are Kai, Kommu’s polite and professional support assistant.\n"
        "- Always answer in a friendly and respectful tone.\n"
        "- Reply ONLY using the provided context.\n"
        "- Do NOT invent or make up links.\n"
        "- If user asks in Malay, reply in Malay.\n"
        "- Only include links from context or known sources.\n"
        "- If info not found, politely admit it.\n"
        "- No emojis. Max 5 links."
    )
    lang_instruction = "Jawab dalam BM dengan nada mesra." if lang_hint == "BM" else "Answer politely in English."

    history_text = ""
    if user_id:
        history = get_history(user_id)
        if history:
            limited = history[-MEMORY_LAYERS:]
            history_text = "\n".join([f"{h['role']}: {h['text']}" for h in limited])

    context = rag_sop.build_context(user_text, topk=4) if rag_sop else ""
    if context.strip():
        prompt = f"{history_text}\nUser: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        if llm:
            try:
                if lang_hint == "BM":
                    llm = GoogleTranslator(source="auto", target="ms").translate(llm)
            except Exception as e:
                log.warning(f"[Translate] BM translation failed: {e}")
            return llm.strip()

    context = rag_web.build_context(user_text, topk=4) if rag_web else ""
    if context.strip():
        prompt = f"{history_text}\nUser: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        if llm:
            try:
                if lang_hint == "BM":
                    llm = GoogleTranslator(source="auto", target="ms").translate(llm)
            except Exception as e:
                log.warning(f"[Translate] BM translation failed: {e}")
            return llm.strip()
    return ""


# ----------------- RAG Loader -----------------
def load_rag():
    global rag_sop, rag_web
    try:
        rag_sop = RAGEngine(k=4, base_dir=os.path.join(RAG_DIR, "faiss_index"))
        log.info("[Kai] SOP RAG loaded")
    except Exception as e:
        log.info(f"[Kai] SOP RAG not available: {e}")
        rag_sop = None
    try:
        rag_web = RAGEngine(k=4, base_dir=os.path.join(RAG_DIR, "faiss_index_web"))
        log.info("[Kai] Website RAG loaded")
    except Exception as e:
        log.info(f"[Kai] Website RAG not available: {e}")
        rag_web = None


rag_sop, rag_web = None, None
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
            log.info(f"[Startup] Loaded {len(qas)} SOP QAs")
    else:
        load_rag()
    fetch_warranty_all()
except Exception as e:
    log.error(f"[Startup] Error: {e}")
# ----------------- Scheduler -----------------
@app.on_event("startup")
def startup_event():
    log.info("[Kai] sessions.db initialized")

@repeat_every(seconds=86400)
def auto_refresh():
    try:
        fetch_warranty_all()
        log.info("[AutoRefresh] Warranty refreshed")
    except Exception as e:
        log.error(f"[AutoRefresh] {e}")


# ----------------- Admin Endpoint -----------------
@app.api_route("/admin/reset_memory", methods=["GET", "POST"])
async def admin_reset_memory(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    reset_memory(user_id)
    log.info(f"[ADMIN] Memory reset for {user_id or 'ALL'}")
    return PlainTextResponse("Memory reset completed")


# ----------------- Agent Dashboard API -----------------
AGENT_TOKENS = {}
for pair in os.getenv("AGENT_TOKENS", "").split(","):
    if ":" in pair:
        token, name = pair.split(":", 1)
        AGENT_TOKENS[token.strip()] = name.strip()

def verify_agent_token(token: str) -> str | None:
    return AGENT_TOKENS.get(token)

def list_sessions():
    db_path = "/app/data/sessions.db"
    rows = []
    try:
        if not os.path.exists(db_path):
            print(f"[WARN] Database not found: {db_path}", flush=True)
            return []
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT user_id, data FROM sessions")
        for user_id, data in cur.fetchall():
            try:
                sess = json.loads(data)
                hist = sess.get("history", [])
                last = hist[-1]["text"] if hist else ""
                rows.append({
                    "user_id": user_id,
                    "name": sess.get("name", user_id),
                    "profile_pic": sess.get("profile_pic", ""),
                    "lastMessage": last,
                    "frozen": sess.get("frozen", False),
                    "lang": sess.get("lang", "EN")
                })
            except Exception as e:
                print(f"[WARN] Skipped bad session {user_id}: {e}", flush=True)
        conn.close()
    except Exception as e:
        print(f"[ERROR] list_sessions failed: {e}", flush=True)
    return rows

def get_chat_history(user_id: str):
    db_path = "/app/data/sessions.db"
    if not os.path.exists(db_path):
        log.error(f"[get_chat_history] DB not found at {db_path}")
        return []

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT data FROM sessions WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return []
        sess = json.loads(row[0])
        hist = sess.get("history", [])
        return [
            {"sender": h.get("role", "bot"), "content": h.get("text", "")}
            for h in hist
        ]
    except Exception as e:
        log.error(f"[get_chat_history] Error for {user_id}: {e}")
        return []

@app.get("/api/agent/me")
async def get_agent_me(authorization: str = Header("")):
    token = authorization.replace("Bearer ", "").strip()
    name = verify_agent_token(token)
    if not name:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"name": name}

@app.get("/api/chats")
async def get_chats(authorization: str = Header("")):
    token = authorization.replace("Bearer ", "").strip()
    if not verify_agent_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return list_sessions()

@app.get("/api/chat/{user_id}")
async def get_chat(user_id: str, authorization: str = Header("")):
    token = authorization.replace("Bearer ", "").strip()
    if not verify_agent_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return get_chat_history(user_id)

@app.post("/api/send_message")
async def send_agent_message(request: Request, authorization: str = Header("")):
    token = authorization.replace("Bearer ", "").strip()
    agent = verify_agent_token(token)
    if not agent:
        log.warning(f"[AgentAPI] Unauthorized token: {token}")
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        data = await request.json()
        user_id = data.get("user_id")
        content = data.get("content", "").strip()
        if not user_id or not content:
            return JSONResponse({"error": "missing fields"}, status_code=400)
        log.info(f"[AgentAPI] Agent={agent} sending to {user_id}: {content}")
        from datetime import datetime
        from media_handler import send_whatsapp_message
        add_message_to_history(user_id, "agent", content, time=datetime.now().isoformat())
        send_whatsapp_message(user_id, f"{agent}: {content}")
        log.info(f"[Agent:{agent}] → {user_id}: {content}")
        return {"status": "sent"}
    except Exception as e:
        log.error(f"[AgentAPI] send_message failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ----------------- Webhook -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        value = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        msg = value.get("messages", [{}])[0]
        if not msg:
            return JSONResponse({"status": "no_message"})

        wa_from = msg.get("from")
        body = msg.get("text", {}).get("body", "").strip()
        msg_type = msg.get("type", "text")

        # --- Safe extraction of profile info ---
        contacts = value.get("contacts", [])
        if contacts and isinstance(contacts[0], dict):
            profile = contacts[0].get("profile", {}) or {}
            user_name = profile.get("name", "Unknown")
            profile_pic = contacts[0].get("profile_pic", "") if "profile_pic" in contacts[0] else ""
        else:
            user_name, profile_pic = "Unknown", ""

        # --- Update session safely ---
        try:
            sess = get_session(wa_from)
            sess["name"] = user_name
            sess["profile_pic"] = profile_pic
            from session_state import set_session
            set_session(wa_from, sess)
        except Exception as e:
            log.warning(f"[Kai] Could not save profile info for {wa_from}: {e}")

        # --- Handle media ---
        if handle_incoming_media(msg, wa_from, add_message_to_history):
            return JSONResponse({"status": "media_received"})
        if not body:
            return JSONResponse({"status": "empty"})

        log.info(f"[Kai] IN from={wa_from} type={msg_type} text={body}")
        lower = norm(body)

        sess = get_session(wa_from)
        lang = "BM" if is_malay(body) else "EN"
        set_lang(wa_from, lang)
        aft = not is_office_hours()
        add_message_to_history(wa_from, "user", body)

        # --- Auto-freeze trigger when user types "LA" or "live agent" ---
        if lower in {"la", "live agent", "agent", "human"}:
            try:
                freeze(wa_from, True, mode="user")
                msg_out = (
                    "A live agent will take over shortly."
                    if lang == "EN"
                    else "Ejen manusia akan mengambil alih perbualan sebentar lagi."
                )
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "frozen_by_user"})
            except Exception as e:
                log.error(f"[Kai] Failed to auto-freeze on LA: {e}")

        # --- Greeting ---
        if not sess.get("greeted") and has_any(["hi","hello","hai","helo","start","menu"], lower):
            msg_out = ("Hi! I'm Kai – Kommu Chatbot. This chat is handled by a chatbot (beta)."
                       if lang=="EN" else
                       "Hai! Saya Kai – Chatbot Kommu. Perbualan ini dikendalikan oleh chatbot (beta).")
            if aft: msg_out += after_hours_suffix(lang)
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "greeted"})

        # --- Live Agent Handling ---
        if sess.get("frozen"):
            if lower in {"resume","unfreeze","sambung"}:
                freeze(wa_from, False, mode="user")
                msg_out = "Bot resumed. How can I help?" if lang=="EN" else "Bot disambung semula. Ada apa saya boleh bantu?"
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "resumed"})
            msg_out = ("A live agent will assist you soon. Type *resume* to continue with the bot."
                       if lang=="EN" else
                       "Ejen manusia akan membantu anda. Taip *resume* untuk teruskan.")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "frozen"})

        # --- Warranty Lookup ---
        if 6 <= len(body) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = (f"Warranty status: {warranty_text_from_row(row)}"
                           if lang=="EN" else
                           f"Status waranti: {warranty_text_from_row(row)}")
                if aft: msg_out += after_hours_suffix(lang)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "warranty"})

        # --- Car Support Logic ---
        if detect_car_support_query(body):
            answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
            lower_ans = answer.lower() if answer else ""
            year_in_text = extract_year(body)
            sop_years = parse_year_range(answer)
            if any(k in lower_ans for k in CAR_KEYWORDS):
                if sop_years != (None, None) and year_in_text:
                    start, end = sop_years
                    if year_in_text < start or year_in_text > end:
                        msg_out = (f"Sorry, the {year_in_text} model is not supported. KommuAssist supports {start}–{end} variants only."
                                   if lang=="EN" else
                                   f"Maaf, model tahun {year_in_text} tidak disokong. KommuAssist hanya menyokong varian {start}–{end} sahaja.")
                        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                        add_message_to_history(wa_from, "bot", msg_out)
                        return JSONResponse({"status": "car_year_not_supported"})
                send_whatsapp_message(wa_from, add_footer(answer, lang))
                add_message_to_history(wa_from, "bot", answer)
                return JSONResponse({"status": "car_supported_from_sop"})
            msg_out = ("I'm not sure about that car. Does it have Adaptive Cruise Control (ACC) and Lane Keep Assist (LKA)?"
                       if lang=="EN" else
                       "Saya tidak pasti tentang kereta itu. Adakah ia mempunyai sistem Adaptive Cruise Control (ACC) dan Lane Keep Assist (LKA)?")
            set_last_intent(wa_from, "car_unknown")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "car_unknown"})

        # --- Fallback RAG ---
        answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            add_message_to_history(wa_from, "bot", answer)
            return JSONResponse({"status": "answered"})

        # --- Default fallback ---
        msg_out = ("I can help with pricing, installation, office hours, warranty, and test drives."
                   if lang=="EN" else
                   "Saya boleh bantu dengan harga, pemasangan, waktu pejabat, waranti, dan pandu uji.")
        if aft: msg_out += after_hours_suffix(lang)
        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
        add_message_to_history(wa_from, "bot", msg_out)
        return JSONResponse({"status": "fallback"})

    except Exception as e:
        log.error(f"[Kai] ERR webhook: {e}\n{traceback.format_exc()}")
        try:
            send_whatsapp_message(wa_from, "Sorry, I encountered an issue. Please try again.")
        except Exception:
            pass
        return JSONResponse({"status": "error", "error": str(e)})
