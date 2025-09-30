from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from datetime import datetime
import pytz, re, os, json, traceback
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
from session_state import get_session, set_lang, freeze, update_reply_state, log_qna
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

def twiml(message: str) -> Response:
    body = escape(message or "", {'"': "&quot;", "'": "&apos;"})
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{body}</Message></Response>'
    return Response(content=xml, media_type="text/xml; charset=utf-8")

def _log_and_twiml(wa_from, asked, answer, lang, intent, after_hours, frozen, status="ok"):
    footer = FOOTER_BM if lang == "BM" else FOOTER_EN
    answer = (answer or "").rstrip() + footer
    try:
        log_qna(wa_from, asked, answer, lang, intent, after_hours, frozen, status)
    finally:
        return twiml(answer)

# ----------------- Escalation Hint -----------------
def maybe_add_la_hint(user_id, msg, lang):
    update_reply_state(user_id)
    sess = get_session(user_id)
    if sess["reply_count"] >= 2:
        hint = " Jika perlu ejen manusia, taip LA." if lang=="BM" else " If you need a live agent, type LA."
        msg += "\n" + hint
    return msg

# ----------------- CS forwarding -----------------
def summarize_for_agent(user_text: str, lang: str):
    sys = "You summarize customer WhatsApp issues for internal CS. Output 2-4 lines, no emojis."
    prompt = f"Customer message ({'Malay' if lang=='BM' else 'English'}):\n{user_text}\n\nSummarize the request."
    s = chat_completion(sys, prompt)
    return (s or user_text).strip()

def forward_to_cs(wa_from: str, summary_text: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER and CS_RECIPIENTS):
        print("[CS-FWD] Missing Twilio env or CS_RECIPIENTS; skipping forward.")
        return
    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    ts = now_myt_str()
    msg = f"[Kai] Live-agent request\nTime: {ts}\nFrom: {wa_from}\nSummary:\n{summary_text}"
    for to in CS_RECIPIENTS:
        try:
            client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=to, body=msg)
        except Exception as e:
            print(f"[CS-FWD] send fail to {to}: {e}")

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

@app.api_route("/status_callback", methods=["GET","POST"])
async def status_callback(_: Request):
    return PlainTextResponse("OK")

@app.api_route("/admin/refresh_sheets", methods=["GET","POST"])
async def refresh_sheets(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", status_code=403)
    try:
        if SOP_DOC_URL:
            txt = fetch_sop_doc_text()
            qas = parse_qas_from_text(txt)
            if qas:
                with open(SOP_JSON_PATH, "w", encoding="utf-8") as f:
                    json.dump(qas, f, ensure_ascii=False, indent=2)
                rebuild_rag()
                load_rag()
        fetch_warranty_all()
        return PlainTextResponse("OK")
    except Exception as e:
        return PlainTextResponse(f"ERR: {e}", status_code=500)

# ----------------- Admin Endpoints for CS -----------------
@app.api_route("/admin/freeze", methods=["POST", "GET"])
async def admin_freeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    mode = request.query_params.get("mode") or (await request.form()).get("mode") or "user"

    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", status_code=403)
    if not user_id:
        return PlainTextResponse("Missing user_id", status_code=400)

    freeze(user_id, True, mode=mode, taken_by="ADMIN")
    log.info(f"[ADMIN] Freeze user {user_id} with mode={mode}")
    return PlainTextResponse("Frozen")

@app.api_route("/admin/unfreeze", methods=["POST", "GET"])
async def admin_unfreeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    takeover = request.query_params.get("takeover") or (await request.form()).get("takeover") or "bot"

    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", status_code=403)
    if not user_id:
        return PlainTextResponse("Missing user_id", status_code=400)

    if takeover == "manual":
        freeze(user_id, False, mode="manual")
        log.info(f"[ADMIN] Unfrozen {user_id} (manual takeover)")
    else:
        freeze(user_id, False, mode="user")
        log.info(f"[ADMIN] Unfrozen {user_id} (chatbot takeover)")

    return PlainTextResponse(f"Unfrozen ({takeover})")

# ----------------- Webhook -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        form = await request.form()
        body = (form.get("Body") or "").strip()
        wa_from = form.get("From") or ""
        log.info(f"[Kai] IN From={wa_from!r} Body={body!r}")

        # Unsupported message types
        msg_type = form.get("MessageType") or ""
        media_url = form.get("MediaUrl0") or ""
        if msg_type in {"voice","audio","image","video","document"} or media_url:
            freeze(wa_from, True, mode="user")
            forward_to_cs(wa_from, f"Unsupported: {msg_type or 'media'}")
            msg = "Kami terima mesej media yang tidak disokong. Ejen akan hubungi anda." if is_malay(body) else \
                  "We received a media message that is not supported. A live agent will contact you."
            return _log_and_twiml(wa_from, body, msg, "BM" if is_malay(body) else "EN",
                                  "unsupported_media", not is_office_hours(), True)

        if not body:
            return _log_and_twiml(wa_from, body, "", "EN", "empty", False, False)

        lower = norm(body)
        sess = get_session(wa_from)
        lang = sess["lang"] or ("BM" if is_malay(body) else "EN")
        set_lang(wa_from, lang)
        aft = not is_office_hours()

        # Frozen Handling
        if sess["frozen"]:
            if sess["frozen_mode"] == "user" and lower in {"resume","unfreeze","sambung"}:
                freeze(wa_from, False, mode="user")
                msg = "Bot resumed. How can I help?" if lang != "BM" else \
                      "Bot disambung semula. Ada apa yang boleh saya bantu?"
                return _log_and_twiml(wa_from, body, msg, lang, "resume", aft, False)

            msg = ("Seorang ejen manusia akan menghubungi anda.\n\n👉 Taip *resume* untuk terus berbual dengan bot."
                   if lang=="BM" else
                   "A live agent will get back to you.\n\n👉 Type *resume* if you want to continue with the bot.")
            return _log_and_twiml(wa_from, body, msg, lang, "frozen_ack", aft, True)

        # Live Agent request
        if has_any(["la","human","request human"], lower):
            freeze(wa_from, True, mode="user")
            sm = summarize_for_agent(body, lang)
            forward_to_cs(wa_from, sm)
            msg = "Seorang ejen manusia akan hubungi anda." if lang=="BM" else "A live agent will reach you."
            return _log_and_twiml(wa_from, body, msg, lang, "live_agent", aft, True)

        # Greeting (only short)
        if not sess["greeted"] and has_any(["hi","hello","hai","helo","mula","start","menu"], lower) and len(lower.split()) <= 3:
            msg = ("Hai! Saya Kai - Chatbot Kommu\nPerbualan ini dikendalikan oleh chatbot beta."
                   if lang=="BM" else
                   "Hi! I'm Kai - Kommu Chatbot\nThis conversation is handled by a chatbot (beta).")
            if aft: msg += after_hours_suffix(lang)
            sess["greeted"] = True
            return _log_and_twiml(wa_from, body, msg, lang, "greeting", aft, False)

        # Warranty lookup
        if 6 <= len(lower) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg = f"Status waranti: {warranty_text_from_row(row)}" if lang=="BM" else \
                      f"Warranty status: {warranty_text_from_row(row)}"
                if aft: msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # RAG default
        answer = run_rag(body, lang_hint=lang)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            answer = maybe_add_la_hint(wa_from, answer, lang)
            return _log_and_twiml(wa_from, body, answer, lang, "default", aft, False)

        # Hard fallback
        msg = ("Saya boleh bantu harga, pemasangan, waktu pejabat, waranti, dan pandu uji."
               if lang=="BM" else
               "I can help with price, installation, office hours, warranty, and test drives.")
        if aft: msg += after_hours_suffix(lang)
        msg = maybe_add_la_hint(wa_from, msg, lang)
        return _log_and_twiml(wa_from, body, msg, lang, "fallback", aft, False, status="unanswered")

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[Kai] FATAL in webhook: {e}\n{tb}")
        return _log_and_twiml(
            wa_from if 'wa_from' in locals() else "",
            body if 'body' in locals() else "",
            "Sorry, internal error. Please try again or type LA.",
            "EN", "error", False, False, status="error"
        )
