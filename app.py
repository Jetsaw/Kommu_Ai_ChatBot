from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from datetime import datetime
import pytz, re, os, json, traceback, logging
from logging.handlers import RotatingFileHandler
import requests
from deep_translator import GoogleTranslator

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
from fastapi_utils.tasks import repeat_every

# ----------------- Logging -----------------
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

app = FastAPI(title="Kai - Kommu Chatbot")

FOOTER_EN = "\n\nI am Kai, Kommu’s support chatbot (beta). Please send your questions one by one. If you’d like a live agent, type LA."
FOOTER_BM = "\n\nSaya Kai, chatbot sokongan Kommu (beta). Sila hantar soalan anda satu demi satu. Jika anda mahu bercakap dengan ejen manusia, taip LA."

# ----------------- Tuning Constants -----------------
MEMORY_LAYERS = 5   # number of last conversation turns kept in context

# ----------------- WhatsApp Cloud API -----------------
def send_whatsapp_message(to: str, text: str):
    url = f"https://graph.facebook.com/v17.0/{os.getenv('META_PHONE_NUMBER_ID')}/messages"
    headers = {
        "Authorization": f"Bearer {os.getenv('META_PERMANENT_TOKEN','')}",
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

# ----------------- Helpers -----------------
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
# ----------------- Car Support Keywords -----------------
def detect_car_support_query(text: str) -> bool:
    car_keywords = [
        "myvi", "alza", "perodua", "honda", "toyota", "proton", "nissan", "mazda",
        "chery", "tiggo", "omoda", "byd", "geely", "kereta", "car", "support", "compatible"
    ]
    return any(k in text.lower() for k in car_keywords)

def extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None

# ----------------- RAG + Memory -----------------
def run_rag_dual(user_text: str, lang_hint: str = "EN", user_id: str | None = None) -> str:
    sys_prompt = (
        "You are Kai, Kommu’s polite and professional support assistant.\n"
        "- Always answer in a friendly and respectful tone.\n"
        "- Reply ONLY using the provided context.\n"
        "- Do NOT invent or make up links.\n"
        "- If user asks in Malay, reply in Malay.\n"
        "- Only include links from the context or known sources.\n"
        "- If info not found, politely admit it.\n"
        "- No emojis. Max 3 links."
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
            if lang_hint == "BM":
                llm = GoogleTranslator(source="auto", target="ms").translate(llm)
            return llm.strip()

    # Website RAG
    context = rag_web.build_context(user_text, topk=4) if rag_web else ""
    if context.strip():
        prompt = f"{history_text}\nUser: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        if llm:
            if lang_hint == "BM":
                llm = GoogleTranslator(source="auto", target="ms").translate(llm)
            return llm.strip()
    return ""

# ----------------- RAG Load on Startup -----------------
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

@app.on_event("startup")
def startup_event():
    init_db()
    log.info("[Kai] sessions.db initialized")

@repeat_every(seconds=86400)
def auto_refresh():
    try:
        log.info("[AutoRefresh] Refreshing SOP + warranty data…")
        fetch_warranty_all()
        log.info("[AutoRefresh] Done")
    except Exception as e:
        log.error(f"[AutoRefresh] {e}")

# ----------------- Admin -----------------
@app.api_route("/admin/refresh", methods=["GET","POST"])
async def admin_refresh(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    fetch_warranty_all()
    return PlainTextResponse("Manual refresh completed")

@app.api_route("/admin/reset_memory", methods=["GET","POST"])
async def admin_reset_memory(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    reset_memory(user_id)
    log.info(f"[ADMIN] Memory reset for {user_id}")
    return PlainTextResponse("Memory reset completed")
# ----------------- Webhook POST -----------------
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
        if not sess.get("greeted") and has_any(["hi","hello","hai","helo","mula","start","menu"], lower):
            msg_out = ("Hi! I'm Kai – Kommu Chatbot. This chat is handled by a chatbot (beta)."
                       if lang=="EN" else
                       "Hai! Saya Kai – Chatbot Kommu. Perbualan ini dikendalikan oleh chatbot (beta).")
            if aft: msg_out += after_hours_suffix(lang)
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "greeted"})

        # -------- Live Agent Handling --------
        if sess.get("frozen"):
            if lower in {"resume","unfreeze","sambung"}:
                freeze(wa_from, False, mode="user")
                send_whatsapp_message(wa_from, add_footer(
                    "Bot resumed. How can I help?" if lang=="EN" else
                    "Bot disambung semula. Ada apa saya boleh bantu?", lang))
                return JSONResponse({"status": "resumed"})
            send_whatsapp_message(wa_from, add_footer(
                "A live agent will assist you soon. Type *resume* to continue with the bot."
                if lang=="EN" else
                "Ejen manusia akan membantu anda. Taip *resume* untuk teruskan.", lang))
            return JSONResponse({"status": "frozen"})

        if has_any(["la","human"], lower):
            freeze(wa_from, True, mode="user")
            send_whatsapp_message(wa_from, add_footer(
                "A live agent will reach out during office hours."
                if lang=="EN" else
                "Ejen manusia akan menghubungi anda pada waktu pejabat.", lang))
            return JSONResponse({"status": "agent"})

        # -------- Warranty Lookup --------
        if 6 <= len(body) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = (f"Warranty status: {warranty_text_from_row(row)}"
                           if lang=="EN" else
                           f"Status waranti: {warranty_text_from_row(row)}")
                if aft: msg_out += after_hours_suffix(lang)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "warranty"})

        # -------- Car Support via SOP --------
        if detect_car_support_query(body):
            answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
            if answer and any(x in answer.lower() for x in [
                "supported", "myvi", "proton", "honda", "toyota", "byd", "perodua"
            ]):
                msg_out = answer
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_supported_from_sop"})
            msg_out = ("I'm not sure about that car. Does it have ACC & LKA?"
                       if lang=="EN" else
                       "Saya tidak pasti tentang kereta itu. Adakah ia mempunyai ACC & LKA?")
            set_last_intent(wa_from, "car_unknown")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "car_unknown"})

        # -------- ACC/LKA Follow-up --------
        last_intent = get_last_intent(wa_from)
        if last_intent == "car_unknown":
            if has_any(["yes","ya","ok","baik"], lower):
                msg_out = (
                    "Great! Since your car has ACC & LKA, it might be compatible soon.\n\n"
                    "Register your interest here so our team can notify you when ready:\n"
                    "https://forms.gle/9XZ5VoswiX6RiDY88\n\n"
                    "You can also book a test drive here:\n"
                    "https://calendly.com/kommuassist/test-drive\n\n"
                    "Please send a picture of your steering wheel so our CS team can confirm."
                    if lang=="EN" else
                    "Bagus! Oleh kerana kereta anda ada ACC dan LKA, ia mungkin serasi tidak lama lagi.\n\n"
                    "Daftar minat anda di sini:\n"
                    "https://forms.gle/9XZ5VoswiX6RiDY88\n\n"
                    "Tempah pandu uji di sini:\n"
                    "https://calendly.com/kommuassist/test-drive\n\n"
                    "Hantar gambar stereng anda untuk pengesahan.")
                freeze(wa_from, True, mode="user")
                set_last_intent(wa_from, None)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_unknown_acc_lka"})
            elif has_any(["no","tak","tidak"], lower):
                msg_out = ("Thanks! Without ACC & LKA, your car may not be supported."
                           if lang=="EN" else
                           "Terima kasih! Tanpa ACC & LKA, kereta anda mungkin tidak disokong.")
                set_last_intent(wa_from, None)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_unknown_no_acc_lka"})

        # -------- General SOP RAG --------
        answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            add_message_to_history(wa_from, "bot", answer)
            return JSONResponse({"status": "answered"})

        # -------- Fallback --------
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
