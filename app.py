from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from datetime import datetime
import pytz, re, os, json, traceback, logging
from logging.handlers import RotatingFileHandler
import requests

from config import (
    TZ_REGION, OFFICE_START, OFFICE_END, PORT,
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
from session_state import get_session, set_lang, freeze, update_reply_state, log_qna
from web_scraper import scrape as scrape_site
from fastapi_utils.tasks import repeat_every
from session_state import init_db
# ----------------- Logging -----------------
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

DEBUG_QA = os.getenv("DEBUG_QA", "1") == "1"
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Kommu_Bot")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")

app = FastAPI(title="Kai - Kommu Chatbot")

FOOTER_EN = "\n\nNote: I am a chatbot, please send your questions one by one. If you need a live agent, type LA."
FOOTER_BM = "\n\nNota: Saya chatbot, sila hantar soalan satu demi satu. Jika anda perlukan ejen manusia, taip LA."

# ----------------- Utilities -----------------
def is_office_hours(now=None):
    tz = pytz.timezone(TZ_REGION)
    now = now or datetime.now(tz)
    return now.weekday() < 5 and OFFICE_START <= now.hour < OFFICE_END

def after_hours_suffix(lang="EN"):
    return ("\n\nPS: Sekarang di luar waktu pejabat." if lang=="BM" else "\n\nPS: We’re currently after-hours.")

def now_myt_str():
    tz = pytz.timezone(TZ_REGION)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

def has_any(words, text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)

def add_footer(answer: str, lang: str) -> str:
    footer = FOOTER_BM if lang == "BM" else FOOTER_EN
    return (answer or "").rstrip() + footer

# ----------------- Cloud API Send -----------------
def send_whatsapp_message(to: str, text: str):
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code >= 400:
            log.error(f"[Kai] Send fail {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"[Kai] Send error: {e}")
# ----------------- RAG + LLM -----------------
def run_rag(user_text: str, lang_hint: str = "EN", intent_hint: str | None = None) -> str:
    if not rag:
        return ""
    context = rag.build_context(user_text, topk=4)
    sys = (
        "You are Kai, Kommu’s assistant.\n"
        "- Reply only from context.\n- No emojis. Max 2 links."
    )
    lang_instruction = "Jawab dalam BM." if lang_hint == "BM" else "Answer in English."
    prompt = f"User: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
    try:
        llm = chat_completion(sys, prompt)
    except Exception as e:
        log.info(f"[Kai] ERR chat_completion: {e}")
        llm = ""
    return llm.strip() if llm else ""

def maybe_add_la_hint(user_id, msg, lang):
    update_reply_state(user_id)
    sess = get_session(user_id)
    if sess["reply_count"] >= 2:
        hint = " Jika perlu ejen manusia, taip LA." if lang=="BM" else " If you need a live agent, type LA."
        msg += "\n" + hint
    return msg

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
        load_rag()
    fetch_warranty_all()
except Exception as e:
    print("[Startup] Error:", e)

@app.on_event("startup")
def startup_event():
    init_db()
    log.info("[Kai] sessions.db initialized")
@repeat_every(seconds=86400)
def auto_refresh():
    try:
        print("[AutoRefresh] Refreshing SOP + website…")
        if SOP_DOC_URL:
            txt = fetch_sop_doc_text()
            qas = parse_qas_from_text(txt)
            if qas:
                with open(SOP_JSON_PATH,"w",encoding="utf-8") as f:
                    json.dump(qas,f,ensure_ascii=False,indent=2)
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

# Webhook verification (GET)
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)

# Admin freeze/unfreeze
@app.api_route("/admin/freeze", methods=["POST", "GET"])
async def admin_freeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN: return PlainTextResponse("Forbidden", 403)
    freeze(user_id, True, mode="admin")
    return PlainTextResponse("Frozen")

@app.api_route("/admin/unfreeze", methods=["POST", "GET"])
async def admin_unfreeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN: return PlainTextResponse("Forbidden", 403)
    freeze(user_id, False, mode="user")
    return PlainTextResponse("Unfrozen")
# ----------------- Webhook POST -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        log.info(f"[Kai] IN: {json.dumps(data)}")

        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return JSONResponse({"status": "no_messages"})

        msg = messages[0]
        wa_from = msg.get("from")
        msg_type = msg.get("type")
        body = ""
        if msg_type == "text":
            body = msg["text"]["body"].strip()
        else:
            freeze(wa_from, True, mode="user")
            send_whatsapp_message(wa_from, "Unsupported media. A live agent will contact you.")
            return JSONResponse({"status": "unsupported"})

        lower = norm(body)
        sess = get_session(wa_from)
        lang = sess["lang"] or ("BM" if is_malay(body) else "EN")
        set_lang(wa_from, lang)
        aft = not is_office_hours()

        # Resume
        if sess["frozen"]:
            if lower in {"resume","unfreeze","sambung"}:
                freeze(wa_from, False, mode="user")
                msg_out = "Bot resumed. How can I help?" if lang=="EN" else "Bot disambung semula. Ada apa yang boleh saya bantu?"
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "resumed"})
            send_whatsapp_message(wa_from, add_footer("A live agent will assist you soon.", lang))
            return JSONResponse({"status": "frozen"})

        # Request agent
        if has_any(["la","human"], lower):
            freeze(wa_from, True, mode="user")
            send_whatsapp_message(wa_from, add_footer("A live agent will reach you.", lang))
            return JSONResponse({"status": "agent"})

        # Greeting
        if not sess["greeted"] and has_any(["hi","hello","hai","helo","mula","start","menu"], lower):
            msg_out = "Hai! Saya Kai - Chatbot Kommu" if lang=="BM" else "Hi! I'm Kai - Kommu Chatbot"
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "greeted"})

        # Warranty check
        if 6 <= len(lower) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = f"Status waranti: {warranty_text_from_row(row)}" if lang=="BM" else f"Warranty status: {warranty_text_from_row(row)}"
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "warranty"})

        # RAG
        answer = run_rag(body, lang_hint=lang)
        if answer:
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            return JSONResponse({"status": "answered"})

        # Fallback
        msg_out = "Saya boleh bantu harga, pemasangan, waktu pejabat, waranti, dan pandu uji." if lang=="BM" else "I can help with price, installation, office hours, warranty, and test drives."
        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
        return JSONResponse({"status": "fallback"})

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[Kai] ERR webhook: {e}\n{tb}")
        return JSONResponse({"status": "error", "error": str(e)})
