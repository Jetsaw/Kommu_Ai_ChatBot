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

# ----------------- Logging -----------------
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

# ----------------- App -----------------
app = FastAPI(title="Kai - Kommu Chatbot")

# Initialize databases
init_db()
init_media_log()

# Serve static dashboard + media for admin UI
app.mount("/media", StaticFiles(directory="media"), name="media")
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

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

# ----------------- RAG + Memory -----------------
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

    # SOP RAG
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

    # Website RAG
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
# ----------------- RAG Load -----------------
def load_rag():
    """Load both SOP and Website FAISS vector indices."""
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


# ----------------- Initialize RAG + SOP Loader -----------------
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
    """Refresh warranty data every 24 hours."""
    try:
        fetch_warranty_all()
        log.info("[AutoRefresh] Warranty refreshed successfully")
    except Exception as e:
        log.error(f"[AutoRefresh] Error: {e}")


# ----------------- Admin Endpoint -----------------
@app.api_route("/admin/reset_memory", methods=["GET", "POST"])
async def admin_reset_memory(request: Request):
    """
    Reset chatbot memory for a specific user or all users.
    Example:
      GET /admin/reset_memory?token=<ADMIN_TOKEN>&user_id=<wa_number>
    """
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")

    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)

    reset_memory(user_id)
    log.info(f"[ADMIN] Memory reset for {user_id or 'ALL USERS'}")
    return PlainTextResponse("Memory reset completed successfully")


# ----------------- Agent Dashboard API -----------------
AGENT_TOKENS = {}
for pair in os.getenv("AGENT_TOKENS", "").split(","):
    if ":" in pair:
        token, name = pair.split(":", 1)
        AGENT_TOKENS[token.strip()] = name.strip()


def verify_agent_token(token: str) -> str | None:
    """Return agent name if token is valid."""
    return AGENT_TOKENS.get(token)


def list_sessions():
    """List all active WhatsApp sessions with summary info."""
    conn = sqlite3.connect("sessions.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, data FROM sessions")
    rows = []
    for user_id, data in cur.fetchall():
        try:
            sess = json.loads(data)
            hist = sess.get("history", [])
            last = hist[-1]["text"] if hist else ""
            rows.append({
                "user_id": user_id,
                "lastMessage": last,
                "frozen": sess.get("frozen", False),
                "lang": sess.get("lang", "EN"),
            })
        except Exception:
            pass
    conn.close()
    return rows


def get_chat_history(user_id: str):
    """Return full chat history for a specific WhatsApp user."""
    conn = sqlite3.connect("sessions.db")
    cur = conn.cursor()
    cur.execute("SELECT data FROM sessions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return []
    sess = json.loads(row[0])
    hist = sess.get("history", [])
    return [{"sender": h.get("role", "bot"), "content": h.get("text", "")} for h in hist]


# ----------------- Agent Auth + Chat APIs -----------------
@app.get("/api/agents/me")
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
    """Allow authorized agent to reply to WhatsApp users."""
    token = authorization.replace("Bearer ", "").strip()
    agent = verify_agent_token(token)
    if not agent:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    user_id = data.get("user_id")
    content = data.get("content", "").strip()
    if not user_id or not content:
        return JSONResponse({"error": "missing fields"}, status_code=400)

    add_message_to_history(user_id, "agent", content)
    send_whatsapp_message(user_id, f"{agent}: {content}")
    log.info(f"[Agent:{agent}] → {user_id}: {content}")
    return {"status": "sent"}
# ----------------- Webhook (Main Entry Point) -----------------
@app.post("/webhook")
async def webhook(request: Request):
    """
    Main WhatsApp Cloud API webhook handler.
    Handles text, media, warranty lookup, car support, and fallback Q&A.
    """
    try:
        data = await request.json()
        value = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        msg = value.get("messages", [{}])[0]
        if not msg:
            return JSONResponse({"status": "no_message"})

        wa_from = msg.get("from")
        body = msg.get("text", {}).get("body", "").strip()
        msg_type = msg.get("type", "text")

        # --- Handle media messages first ---
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

        # -------- Greeting --------
        if not sess.get("greeted") and has_any(["hi", "hello", "hai", "helo", "mula", "start", "menu"], lower):
            msg_out = (
                "Hi! I'm Kai – Kommu Chatbot. This chat is handled by a chatbot (beta)."
                if lang == "EN"
                else "Hai! Saya Kai – Chatbot Kommu. Perbualan ini dikendalikan oleh chatbot (beta)."
            )
            if aft:
                msg_out += after_hours_suffix(lang)
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "greeted"})

        # -------- Live Agent Handling --------
        if sess.get("frozen"):
            if lower in {"resume", "unfreeze", "sambung"}:
                freeze(wa_from, False, mode="user")
                msg_out = (
                    "Bot resumed. How can I help?"
                    if lang == "EN"
                    else "Bot disambung semula. Ada apa saya boleh bantu?"
                )
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "resumed"})
            msg_out = (
                "A live agent will assist you soon. Type *resume* to continue with the bot."
                if lang == "EN"
                else "Ejen manusia akan membantu anda. Taip *resume* untuk teruskan."
            )
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "frozen"})

        # -------- Warranty Lookup --------
        if 6 <= len(body) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = (
                    f"Warranty status: {warranty_text_from_row(row)}"
                    if lang == "EN"
                    else f"Status waranti: {warranty_text_from_row(row)}"
                )
                if aft:
                    msg_out += after_hours_suffix(lang)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "warranty"})

        # -------- Car Support Logic --------
        if detect_car_support_query(body):
            answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
            lower_ans = answer.lower() if answer else ""
            year_in_text = extract_year(body)
            sop_years = parse_year_range(answer)

            if any(k in lower_ans for k in CAR_KEYWORDS):
                if sop_years != (None, None) and year_in_text:
                    start, end = sop_years
                    if year_in_text < start or year_in_text > end:
                        msg_out = (
                            f"Sorry, the {year_in_text} model is not supported. KommuAssist supports {start}–{end} variants only."
                            if lang == "EN"
                            else f"Maaf, model tahun {year_in_text} tidak disokong. KommuAssist hanya menyokong varian {start}–{end} sahaja."
                        )
                        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                        add_message_to_history(wa_from, "bot", msg_out)
                        return JSONResponse({"status": "car_year_not_supported"})

                send_whatsapp_message(wa_from, add_footer(answer, lang))
                add_message_to_history(wa_from, "bot", answer)
                return JSONResponse({"status": "car_supported_from_sop"})

            msg_out = (
                "I'm not sure about that car. Does it have Adaptive Cruise Control (ACC) and Lane Keep Assist (LKA)?"
                if lang == "EN"
                else "Saya tidak pasti tentang kereta itu. Adakah ia mempunyai sistem Adaptive Cruise Control (ACC) dan Lane Keep Assist (LKA)?"
            )
            set_last_intent(wa_from, "car_unknown")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "car_unknown"})

        # -------- Fallback (General Knowledge / SOP) --------
        answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
        if answer:
            if aft:
                answer += after_hours_suffix(lang)
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            add_message_to_history(wa_from, "bot", answer)
            return JSONResponse({"status": "answered"})

        # -------- Generic Fallback --------
        msg_out = (
            "I can help with pricing, installation, office hours, warranty, and test drives."
            if lang == "EN"
            else "Saya boleh bantu dengan harga, pemasangan, waktu pejabat, waranti, dan pandu uji."
        )
        if aft:
            msg_out += after_hours_suffix(lang)
        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
        add_message_to_history(wa_from, "bot", msg_out)
        return JSONResponse({"status": "fallback"})

    except Exception as e:
        log.error(f"[Kai] ERR webhook: {e}\n{traceback.format_exc()}")
        try:
            send_whatsapp_message(wa_from, "Sorry, I encountered an issue. Please try again later.")
        except Exception:
            pass
        return JSONResponse({"status": "error", "error": str(e)})


# ----------------- Root Health Check -----------------
@app.get("/health")
async def health_check():
    """Simple API health endpoint for UI and monitoring."""
    return {"status": "ok", "time": datetime.now().isoformat()}


# ----------------- Entry Point -----------------
if __name__ == "__main__":
    import uvicorn
    log.info("[Kai] Starting server...")
    uvicorn.run("app:app", host="0.0.0.0", port=int(PORT), reload=False)
