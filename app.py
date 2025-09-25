from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from datetime import datetime, timedelta
import pytz, re, os, json
from collections import defaultdict
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
from twilio.rest import Client as TwilioClient

 # logger
from qna_logger import log_qna

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

def contains_any(text: str, pieces):
    return any(p in text for p in pieces)

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
        log_qna(
            wa_from=wa_from,
            asked=asked,
            answer=answer,
            lang=lang,
            intent=intent,
            after_hours=after_hours,
            frozen=frozen,
            status=status,
        )
    finally:
        return twiml(answer)

def norm_dongle(s: str) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())

def _looks_like_dongle_id(s: str) -> bool:
    s2 = norm_dongle(s)
    return 6 <= len(s2) <= 20

# ----------------- Per-user language pinning -----------------
USER_LANG = {}  

def detect_lang(text: str) -> str:
    return "BM" if is_malay(text) else "EN"

def set_user_lang(wa_from: str, lang_code: str):
    if lang_code in ("BM", "EN"):
        USER_LANG[wa_from] = lang_code

def try_language_command(wa_from: str, lower_text: str):
    """
    Returns a response string if the user issued a language command; otherwise None.
    Supported:
      - 'lang en', 'language english', 'english'
      - 'lang bm', 'language malay', 'bahasa', 'malay', 'bm'
    """
    en_cmds = ("lang en", "language english", "english")
    bm_cmds = ("lang bm", "language malay", "bahasa", "malay", "bm")

    if any(cmd == lower_text for cmd in en_cmds):
        set_user_lang(wa_from, "EN")
        return "Language set to English."
    if any(cmd == lower_text for cmd in bm_cmds):
        set_user_lang(wa_from, "BM")
        return "Bahasa ditetapkan (Malay)."
    return None

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

    intent_guidance = {
        "about":  ("Briefly explain what Kommu/KommuAssist is and the main benefits. Include exactly one link."),
        "how":    ("Explain briefly how KommuAssist works (plug-and-play hardware tuned for Malaysian roads). One short paragraph, max one link."),
        "buy":    ("Buying steps; include https://kommu.ai/products/ and https://kommu.ai/support/ . Ask for car make/model/year/trim."),
        "hours":  ("Office hours Mon–Fri 10:00–18:00 MYT, address: C/105B Block C, Jalan PJU 10/2a, Damansara Damai, 47830 PJ, Selangor. Include Waze link."),
        "test_drive": ("Offer test drive link: https://calendly.com/kommuassist/test-drive?month=2025-08"),
        "no_led": ("Quick troubleshooting steps for LED not blinking."),
        "parts":  ("How to request part replacement; info to provide (part name, issue, photo/video, order email/phone).")
    }
    guide = intent_guidance.get(intent_hint, "")

    sys = (
        "You are Kai, Kommu’s friendly, professional assistant.\n"
        "- Always reply in the user's language. For Malay users, reply purely in Bahasa Malaysia.\n"
        "- No emojis. No markdown tables. 1–2 relevant links max.\n"
        "- Prefer: https://kommu.ai/products/ https://kommu.ai/support/ https://kommu.ai/faq/\n"
        "- Use ONLY the provided context."
    )

    lang_instruction = (
        "Tulis jawapan 100% dalam Bahasa Melayu (Bahasa Malaysia)."
        if lang_hint == "BM" else
        "Write the final answer in English."
    )

    prompt = (
        f"User message: {user_text}\n\n"
        "Context (top SOP matches with similarity scores):\n"
        f"{context}\n\n"
        f"{lang_instruction}\n"
        f"Extra guidance for this query: {guide}\n\n"
        "Write a concise, helpful answer in the user's language."
    )

    try:
        llm = chat_completion(sys, prompt)
    except Exception as e:
        log.info(f"[Kai] ERR chat_completion: {e}")
        llm = ""

    if llm and llm.strip():
        out = llm.strip()
        if DEBUG_QA:
            print(f"[Kai][LLM] Q: {user_text}\n[A]: {out}\n")
        if lang_hint == "BM" and looks_english(out):
            fixed = translate_to_bm(out) or out
            if DEBUG_QA and fixed != out:
                print(f"[Kai][LLM→BM] Q: {user_text}\n[A]: {fixed}\n")
            return fixed
        return out

    #  fallback from SOP block
    top = context.split("\n\n---\n\n")[0].strip() if context else ""
    a_text = ""
    for line in top.splitlines():
        if line.lstrip().lower().startswith("a:"):
            a_text = line.split(":", 1)[1].strip()
    common_links = "Useful links: https://kommu.ai/products/  https://kommu.ai/support/  https://kommu.ai/faq/"
    if lang_hint == "BM":
        return ("Baik, ini yang berkaitan:\n- " + (a_text or "Rujukan SOP ditemui.") + "\n\n" + common_links).strip()
    return ("Here’s the relevant summary:\n- " + (a_text or "I found a related SOP entry.") + "\n\n" + common_links).strip()

# ----------------- Session State -----------------
REPLY_STATE = defaultdict(lambda: {"count": 0, "offered": False, "offers_count": 0, "last_offer_at": None, "fail_count": 0})
FROZEN_USERS = set()
TAKEN_BY = {}
LA_COOLDOWN_HOURS = 24
PENDING = defaultdict(lambda: {"expect": None, "tries": 0})  # e.g., waiting for dongle

def track_and_maybe_offer(wa_from: str, msg: str, lang: str, was_greeting: bool = False) -> str:
    st = REPLY_STATE[wa_from]
    if not was_greeting:
        st["count"] += 1
    def can_offer():
        if st["offered"]:
            return False
        if st["last_offer_at"] is None:
            return True
        return (datetime.now() - st["last_offer_at"]) >= timedelta(hours=LA_COOLDOWN_HOURS)
    should = (st["fail_count"] >= 2 or st["count"] >= 2) and can_offer()
    if should:
        st["offered"] = True
        st["offers_count"] += 1
        st["last_offer_at"] = datetime.now()
        offer = "Jika perlu ejen manusia, taip LA" if lang=="BM" else "If you need a live agent, type LA"
        msg = f"{msg}\n\n{offer}"
    return msg

def is_agent_number(num: str) -> bool:
    return num in AGENT_NUMBERS

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

# ---- Startup load SOP Doc,build RAG; load warranty sheets ----
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
            print("[SOP-DOC] Parsed 0 Q/A. Ensure the Doc uses Q:/A: pairs or question lines ending with '?'.")
    else:
        load_rag()
    fetch_warranty_all()
except Exception as e:
    print("[Startup] Error:", e)

# ----------------- Routes -----------------
@app.get("/", response_class=PlainTextResponse)
async def health():
    return "Kai alive"

# Twilio 404s
@app.post("/status_callback")
async def status_callback(_: Request):
    return PlainTextResponse("OK")

@app.post("/admin/refresh_sheets")
async def refresh_sheets(request: Request):
    token = (request.query_params.get("token") or "")
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

@app.get("/debug/state", response_class=PlainTextResponse)
async def debug_state():
    # memory state 
    lines = []
    lines.append(f"USER_LANG: {len(USER_LANG)} entries")
    lines.append(f"FROZEN_USERS: {len(FROZEN_USERS)} users")
    lines.append(f"PENDING: {sum(1 for v in PENDING.values() if v.get('expect'))} waiting")
    lines.append(f"REPLY_STATE: {len(REPLY_STATE)} tracked")
    return "\n".join(lines)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        form = await request.form()
        body = (form.get("Body") or "").strip()
        wa_from = form.get("From") or ""
        log.info(f"[Kai] IN From={wa_from!r} Body={body!r}")
        if not body:
            return _log_and_twiml(wa_from, body, "", "EN", "empty", False, (wa_from in FROZEN_USERS))

        # ---- Agent control surface ----
        if is_agent_number(wa_from):
            lower = norm(body)
            if lower.startswith("ping"):
                return _log_and_twiml(wa_from, body, "OK", "EN", "agent", False, False)
            if lower.startswith("take "):
                tgt = body.split(" ", 1)[1].strip()
                FROZEN_USERS.add(tgt); TAKEN_BY[tgt] = wa_from
                return _log_and_twiml(wa_from, body, f"Taken & frozen: {tgt}", "EN", "agent", False, False)
            if lower.startswith("resume "):
                tgt = body.split(" ", 1)[1].strip()
                FROZEN_USERS.discard(tgt); TAKEN_BY.pop(tgt, None)
                return _log_and_twiml(wa_from, body, f"Resumed: {tgt}", "EN", "agent", False, False)
            if lower.startswith("note "):
                try:
                    rest = body.split(" ", 1)[1]
                    phone, note = [x.strip() for x in rest.split("|", 1)]
                    forward_to_cs(wa_from, f"NOTE for {phone}\n{note}")
                    return _log_and_twiml(wa_from, body, "Noted and forwarded.", "EN", "agent", False, False)
                except:
                    return _log_and_twiml(wa_from, body, "Usage: NOTE +6011xxxx | message", "EN", "agent", False, False)
            if lower.startswith("lang "):
                try:
                    rest = body.split(" ", 1)[1].strip()
                    phone, code = rest.split(" ", 1)
                    code = code.strip().lower()
                    target_lang = "EN" if code in ("en","eng","english") else ("BM" if code in ("bm","ms","my","malay","bahasa") else None)
                    if target_lang:
                        set_user_lang(phone, target_lang)
                        return _log_and_twiml(wa_from, body, f"Language pinned for {phone}: {target_lang}", "EN", "agent", False, False)
                    else:
                        return _log_and_twiml(wa_from, body, "Usage: LANG +6011xxxx en|bm", "EN", "agent", False, False)
                except:
                    return _log_and_twiml(wa_from, body, "Usage: LANG +6011xxxx en|bm", "EN", "agent", False, False)
            if lower.startswith("unlang "):
                try:
                    phone = body.split(" ", 1)[1].strip()
                    USER_LANG.pop(phone, None)
                    return _log_and_twiml(wa_from, body, f"Language pin removed for {phone}", "EN", "agent", False, False)
                except:
                    return _log_and_twiml(wa_from, body, "Usage: UNLANG +6011xxxx", "EN", "agent", False, False)
            return _log_and_twiml(
                wa_from, body,
                "Commands: PING, TAKE +6011xxxx, RESUME +6011xxxx, NOTE +6011xxxx | text, LANG +6011xxxx en|bm, UNLANG +6011xxxx",
                "EN","agent",False,False
            )

        lower = norm(body)

        
        lang_cmd_reply = try_language_command(wa_from, lower)
        if lang_cmd_reply:
            return _log_and_twiml(wa_from, body, lang_cmd_reply, "EN", "lang_cmd", False, False)

        # pinned or detect
        if wa_from in USER_LANG:
            lang = USER_LANG[wa_from]
        else:
            lang = detect_lang(body)
            set_user_lang(wa_from, lang)

        aft = not is_office_hours()

        # unfreeze
        if lower in {"resume","unfreeze","sambung"}:
            if wa_from in FROZEN_USERS:
                FROZEN_USERS.remove(wa_from); TAKEN_BY.pop(wa_from, None)
                answer = "Bot resumed. How can I help?" if lang!="BM" else "Bot disambung semula. Ada apa yang boleh saya bantu?"
                return _log_and_twiml(wa_from, body, answer, lang, "resume", aft, (wa_from in FROZEN_USERS))

        # frozen - ack only
        if wa_from in FROZEN_USERS:
            answer = ("A live agent will get back to you shortly. (Type RESUME to continue with the bot.)"
                      if lang!="BM" else
                      "Ejen akan menghubungi anda sekejap lagi. (Taip SAMBUNG untuk teruskan dengan bot.)")
            return _log_and_twiml(wa_from, body, answer, lang, "frozen_ack", aft, True)

        # greeting
        if has_any(["hi","hello","start","mula","hai","helo","menu"], lower):
            msg = ("Hai! Saya Kai - Chatbot Kommu\nPerlu ejen manusia? Taip LA"
                   if lang=="BM" else
                   "Hi ! i'm Kai - Kommu Chatbot\nNeed a live agent ? Type LA")
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang, was_greeting=True)
            return _log_and_twiml(wa_from, body, msg, lang, "greeting", aft, False)

        # live agent
        if has_any(["la","human","request human"], lower):
            FROZEN_USERS.add(wa_from); TAKEN_BY[wa_from] = "user-initiated"
            sm = summarize_for_agent(body, lang)
            forward_to_cs(wa_from, sm)
            msg = "Seorang ejen manusia akan hubungi anda pada waktu pejabat. Chat dibekukan." if lang=="BM" else \
                  "A live agent will reach out during office hours. Chat is now frozen."
            return _log_and_twiml(wa_from, body, msg, lang, "live_agent", aft, True)

        # warranty — direct dongle
        if _looks_like_dongle_id(body):
            row = warranty_lookup_by_dongle(body)
            if row:
                msg = (f"Status waranti: {warranty_text_from_row(row)}" if lang=="BM"
                       else f"Warranty status: {warranty_text_from_row(row)}")
                if aft: msg += after_hours_suffix(lang)
                msg = track_and_maybe_offer(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # warranty intent - ask dongle
        if contains_any(lower, ["warranty","waranti","jaminan","waranty","warrant"]):
            PENDING[wa_from] = {"expect": "warranty_dongle", "tries": 0}
            msg = ("Untuk semakan waranti, sila beri Dongle ID (contoh: KM12345)."
                   if lang=="BM" else
                   "To check your warranty, please provide your Dongle ID (e.g., KM12345).")
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # wrong dongle
        if PENDING[wa_from]["expect"] == "warranty_dongle":
            did = norm_dongle(body)
            if not did or len(did) < 4:
                PENDING[wa_from]["tries"] += 1
                msg = ("Nombor Dongle tidak sah. Sila beri Dongle ID (contoh: KM12345)."
                       if lang == "BM" else
                       "That Dongle ID looks invalid. Please provide the Dongle ID (e.g., KM12345).")
                if PENDING[wa_from]["tries"] >= 2:
                    REPLY_STATE[wa_from]["fail_count"] = 2
                    msg = track_and_maybe_offer(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

            row = warranty_lookup_by_dongle(did)
            if row:
                msg = (f"Status waranti: {warranty_text_from_row(row)}" if lang=="BM"
                       else f"Warranty status: {warranty_text_from_row(row)}")
                if aft: msg += after_hours_suffix(lang)
                PENDING[wa_from] = {"expect": None, "tries": 0}
                msg = track_and_maybe_offer(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)
            else:
                PENDING[wa_from]["tries"] += 1
                msg = ("Maaf, Dongle ID tidak dijumpai. Sila semak dan cuba lagi, atau taip LA untuk bantuan manusia."
                       if lang=="BM" else
                       "Sorry, I couldn’t find that Dongle ID. Please double-check and try again, or type LA for a live agent.")
                if PENDING[wa_from]["tries"] >= 2:
                    REPLY_STATE[wa_from]["fail_count"] = 2
                    msg = track_and_maybe_offer(wa_from, msg, lang)
                return _log_and_twiml(wa_from, body, msg, lang, "warranty", aft, False)

        # intents via RAG
        brand_mentioned = mentions_brand(norm(body))
        asks_about = contains_any(norm(body), ["what is","apa itu","about","explain","how does","how do","bagaimana"]) and brand_mentioned
        if asks_about:
            intent = "how" if contains_any(norm(body), ["how ","bagaimana","how does","how do"]) else "about"
            msg = run_rag(body, lang_hint=lang, intent_hint=intent)
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, intent, aft, False)

        if has_any(["buy","beli","order","purchase","tempah","price","harga","want to buy","nak beli"], norm(body)) or (brand_mentioned and has_any(["buy","beli","order","purchase","tempah","price","harga","want to buy","nak beli"], norm(body))):
            msg = run_rag(body, lang_hint=lang, intent_hint="buy")
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "buy", aft, False)

        if has_any(["test","drive","testdrive","demo","try","pandu","uji","appointment","book","booking"], norm(body)):
            msg = run_rag(body, lang_hint=lang, intent_hint="test_drive")
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "test_drive", aft, False)

        if has_any(["office","waktu","pejabat","hour","hours","open","close","alamat","address","location"], norm(body)):
            msg = run_rag(body, lang_hint=lang, intent_hint="hours")
            if aft: msg += after_hours_suffix(lang)
            msg = track_and_maybe_offer(wa_from, msg, lang)
            return _log_and_twiml(wa_from, body, msg, lang, "hours", aft, False)

        #  RAG
        answer = run_rag(body, lang_hint=lang, intent_hint=None)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            answer = track_and_maybe_offer(wa_from, answer, lang)
            return _log_and_twiml(wa_from, body, answer, lang, "default", aft, False)

        # hard fallback
        msg = ("Saya boleh bantu harga, pemasangan, waktu pejabat, penggantian bahagian, waranti dan pandu uji. "
               "Cuba: 'Beli Kommu', 'Apa itu Kommu', 'Bagaimana ia berfungsi', 'Waktu pejabat', 'Pandu uji'. Perlu ejen manusia? Taip LA."
               if lang=="BM" else
               "I can help with price, installation, office hours, parts, warranty and test drives. "
               "Try: 'Buy Kommu', 'What is Kommu', 'How does it work', 'Office time', 'Test drive'. Need a live agent? Type LA.")
        if aft: msg += after_hours_suffix(lang)
        msg = track_and_maybe_offer(wa_from, msg, lang)
        return _log_and_twiml(wa_from, body, msg, lang, "fallback", aft, False)

    except Exception as e:
        log.info(f"[Kai] FATAL in webhook: {e}")
        err = "Sorry, I hit an internal error. Please try again. If urgent, type LA."
        return _log_and_twiml(wa_from, (body if 'body' in locals() else ""), err, "EN", "error", False, (wa_from in FROZEN_USERS), status="error")
