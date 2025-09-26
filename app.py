from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from datetime import datetime
import pytz, re, os, json
from xml.sax.saxutils import escape
from logging.handlers import RotatingFileHandler
import logging, traceback

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

def mentions_brand(text: str) -> bool:
    return bool(re.search(r"\bkommu(?:assist)?\b", text))

def looks_english(text: str) -> bool:
    t = f" { (text or '').lower() } "
    en_hits = sum(w in t for w in [" the ", " and ", " to ", " is ", " are ", " you ", " we ", " will ", " please ", " support "])
    bm_hits = sum(w in t for w in [" dan ", " ialah ", " anda ", " kami ", " akan ", " sila ", " waktu ", " alamat ", " gantian ", " bahagian "])
    return en_hits >= 2 and bm_hits == 0

def translate_to_bm(text: str) -> str:
    sys = "You are a professional Malay translator. Output only the translation in Malay. No extra commentary. No emojis."
    prompt = f"Terjemahkan ke Bahasa Melayu (Bahasa Malaysia) dengan nada profesional:\n\n{text}"
    try:
        out = chat_completion(sys, prompt)
        return out or ""
    except:
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

# ----------------- Escalation Hint -----------------
def maybe_add_la_hint(user_id, msg, lang):
    update_reply_state(user_id)
    sess = get_session(user_id)
    if sess["reply_count"] >= 2:
        hint = " Jika perlu ejen manusia, taip LA." if lang=="BM" else " If you need a live agent, type LA."
        msg += hint
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
        "You are Kai, Kommu’s friendly assistant.\n"
        "- Always reply in the user's language (Malay users → BM).\n"
        "- No emojis. No tables. Max 2 links.\n"
        "- Use ONLY the provided context."
    )
    lang_instruction = "Tulis jawapan 100% dalam Bahasa Melayu." if lang_hint == "BM" else "Write the final answer in English."
    prompt = f"User message: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}\nWrite a concise, helpful answer."
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
                os.makedirs(RAG_DIR, exist_ok=True)
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
                os.makedirs(RAG_DIR, exist_ok=True)
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
        freeze(user_id, False, mode="manual")  # bot stays silent
        log.info(f"[ADMIN] Unfrozen {user_id} (manual takeover)")
    else:
        freeze(user_id, False, mode="user")    # bot resumes
        log.info(f"[ADMIN] Unfrozen {user_id} (chatbot takeover)")

    return PlainTextResponse(f"Unfrozen ({takeover})")


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

        # -------- Unsupported message types --------
        msg_type = form.get("MessageType") or ""
        media_url = form.get("MediaUrl0") or ""
        if msg_type in {"voice", "audio", "image", "video", "document"} or media_url:
            freeze(wa_from, True, mode="user")
            forward_to_cs(wa_from, f"⚠️ Unsupported message type: {msg_type or 'media'}")
            msg = (
                "Kami terima mesej media (gambar/audio/video) yang tidak disokong. "
                "Seorang ejen manusia akan hubungi anda."
                if is_malay(body)
                else "We received a media message (image/audio/video) that is not supported. "
                     "A live agent will contact you."
            )
            return _log_and_twiml(
                wa_from, body, msg,
                "BM" if is_malay(body) else "EN",
                "unsupported_media", not is_office_hours(), True
            )

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
                tgt = body.split(" ",1)[1].strip()
                freeze(tgt, True, mode="agent", taken_by=wa_from)
                return _log_and_twiml(wa_from, body, f"Taken & frozen: {tgt}", "EN", "agent_cmd", aft, True)
            if lower.startswith("resume "):
                tgt = body.split(" ",1)[1].strip()
                freeze(tgt, False, mode="user", taken_by=None)
                return _log_and_twiml(wa_from, body, f"Resumed bot for: {tgt}", "EN", "agent_cmd", aft, False)
            return _log_and_twiml(
                wa_from, body,
                "Agent commands: TAKE +6011xxxx, RESUME +6011xxxx",
                "EN", "agent_cmd", aft, False
            )

        # -------- Frozen Handling --------
        if sess["frozen"]:
            if sess["frozen_mode"] == "user" and lower in {"resume", "unfreeze", "sambung"}:
                freeze(wa_from, False, mode="user")
                msg = "Bot resumed. How can I help?" if lang != "BM" else "Bot disambung semula. Ada apa yang boleh saya bantu?"
                return _log_and_twiml(wa_from, body, msg, lang, "resume", aft, False)

            if lang == "BM":
                msg = (
                    "Seorang ejen manusia akan menghubungi anda sekejap lagi."
                    "\n\nSementara menunggu, anda boleh sertai komuniti kami:"
                    "\n- FB Group: https://web.facebook.com/groups/kommu.official/"
                    "\n- Discord: https://discord.gg/CP9ZpsXWqH"
                    "\n\n👉 Jika anda mahu terus berbual dengan chatbot, taip *resume*."
                )
            else:
                msg = (
                    "A live agent will get back to you shortly."
                    "\n\nWhile waiting, you can join our community:"
                    "\n- FB Group: https://web.facebook.com/groups/kommu.official/"
                    "\n- Discord: https://discord.gg/CP9ZpsXWqH"
                    "\n\n👉 If you want to continue chatting with the bot, type *resume*."
                )
            return _log_and_twiml(wa_from, body, msg, lang, "frozen_ack", aft, True)

        # -------- User Requests Live Agent --------
        if has_any(["la", "human", "request human"], lower):
            freeze(wa_from, True, mode="user")
            sm = summarize_for_agent(body, lang)
            forward_to_cs(wa_from, sm)
            if lang == "BM":
                msg = (
                    "Seorang ejen manusia akan hubungi anda pada waktu pejabat. Chat dibekukan."
                    "\n\nSementara menunggu, anda boleh sertai komuniti kami:"
                    "\n- FB Group: https://web.facebook.com/groups/kommu.official/"
                    "\n- Discord: https://discord.gg/CP9ZpsXWqH"
                    "\n\n👉 Jika anda mahu terus berbual dengan chatbot, taip *resume*."
                )
            else:
                msg = (
                    "A live agent will reach out during office hours. Chat is now frozen."
                    "\n\nWhile waiting, you can join our community:"
                    "\n- FB Group: https://web.facebook.com/groups/kommu.official/"
                    "\n- Discord: https://discord.gg/CP9ZpsXWqH"
                    "\n\n👉 If you want to continue chatting with the bot, type *resume*."
                )
            return _log_and_twiml(wa_from, body, msg, lang, "live_agent", aft, True)

        # -------- Greeting (short messages only) --------
        if not sess["greeted"] and has_any(["hi","hello","start","mula","hai","helo","menu"], lower) and len(lower.split()) <= 3:
            msg = "Hai! Saya Kai - Chatbot Kommu\n[Perbualan ini dikendalikan oleh chatbot dan dalam ujian beta.]" if lang=="BM" else \
                  "Hi! I'm Kai - Kommu Chatbot\n[The conversation is handled by a chatbot and is under beta testing.]"
            if aft: msg += after_hours_suffix(lang)
            sess["greeted"] = True
            return _log_and_twiml(wa_from, body, msg, lang, "greeting", aft, False)

        # -------- Warranty Direct Lookup --------
        if 6 <= len(lower) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg = f"Status waranti: {warranty_text_from_row(row)}" if lang=="BM" else f"Warranty status: {warranty_text_from_row(row)}"
                if aft: msg += after_hours_suffix(lang)
                msg = maybe_add_la_hint(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # -------- Intent-based RAG --------
        if has_any(["buy","beli","order","purchase","tempah","price","harga"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="buy")
            if aft: msg += after_hours_suffix(lang)
            msg = maybe_add_la_hint(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "buy", aft, False)

        if has_any(["office","waktu","pejabat","hour","hours","open","close","alamat","address"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="hours")
            if aft: msg += after_hours_suffix(lang)
            msg = maybe_add_la_hint(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "hours", aft, False)

        if has_any(["test","drive","demo","try","pandu","uji","appointment","book"], lower):
            msg = run_rag(body, lang_hint=lang, intent_hint="test_drive")
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
        msg = "Saya boleh bantu harga, pemasangan, waktu pejabat, waranti, dan pandu uji." if lang=="BM" else \
              "I can help with price, installation, office hours, warranty, and test drives."
        if aft: msg += after_hours_suffix(lang)
        msg = maybe_add_la_hint(wa_from, msg, lang)
        return _log_and_twiml(wa_from, body, msg, lang, "fallback", aft, False, status="unanswered")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.error(f"[Kai] FATAL in webhook: {e}\n{tb}")
        return _log_and_twiml(
            wa_from if 'wa_from' in locals() else "",
            body if 'body' in locals() else "",
            "Sorry, internal error. Please try again or type LA.",
            "EN", "error", False, False, status="error"
        )
