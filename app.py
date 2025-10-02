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
from session_state import get_session, set_lang, freeze, update_reply_state, log_qna, init_db
from web_scraper import scrape as scrape_site
from fastapi_utils.tasks import repeat_every

# ----------------- Logging -----------------
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/kai.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("kai")

DEBUG_QA = os.getenv("DEBUG_QA", "1") == "1"

# Meta API (Cloud API)
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "Kommu_Bot")
WHATSAPP_TOKEN = os.getenv("META_PERMANENT_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("META_PHONE_NUMBER_ID", "")

app = FastAPI(title="Kai - Kommu Chatbot")

FOOTER_EN = "\n\nI am Kai, Kommu’s support chatbot (beta). Please send your questions one by one. If you’d like to chat with a live agent, type LA."
FOOTER_BM = "\n\nSaya Kai, chatbot sokongan Kommu (beta). Sila hantar soalan anda satu demi satu. Jika anda mahu bercakap dengan ejen manusia, taip LA."

# ----------------- Allowed Links (Whitelist) -----------------
ALLOWED_LINKS = [
    "https://kommu.ai/",
    "https://kommu.ai/faq/",
    "https://kommu.ai/products/",
    "https://kommu.ai/support/",
    "https://kommu.ai/store/",
    "https://calendly.com/kommuassist/test-drive",
    "https://discord.gg/",
    "https://facebook.com/groups/"
]

# ----------------- Utilities -----------------
def is_office_hours(now=None):
    tz = pytz.timezone(TZ_REGION)
    now = now or datetime.now(tz)
    return now.weekday() < 5 and OFFICE_START <= now.hour < OFFICE_END

def after_hours_suffix(lang="EN"):
    return ("\n\nPS: Sekarang di luar waktu pejabat." if lang=="BM" else "\n\nPS: At the moment we’re outside office hours. A live agent will follow up later.")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

def has_any(words, text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)

def add_footer(answer: str, lang: str) -> str:
    footer = FOOTER_BM if lang == "BM" else FOOTER_EN
    return (answer or "").rstrip() + footer

def filter_hallucinated_links(answer: str, context: str) -> str:
    """Keep only links that are whitelisted or present in context"""
    context_links = set(re.findall(r"(https?://\S+)", context))
    answer_links = set(re.findall(r"(https?://\S+)", answer))
    valid_links = context_links.union(set(ALLOWED_LINKS))
    for u in answer_links:
        if not any(u.startswith(v) for v in valid_links):
            answer = answer.replace(u, "")
    return answer.strip()

def enforce_link_intents(user_text: str, answer: str) -> str:
    """Force specific links for common intents"""
    lower = user_text.lower()
    # Test drive → Calendly
    if "test drive" in lower or "pandu uji" in lower:
        if "https://calendly.com/kommuassist/test-drive" not in answer:
            answer += "\n\n👉 You can book a test drive here: https://calendly.com/kommuassist/test-drive"
    # Price / buy → Store
    if "price" in lower or "harga" in lower or "buy" in lower or "beli" in lower:
        if "https://kommu.ai/store/" not in answer:
            answer += "\n\n👉 You can view pricing at our store: https://kommu.ai/store/"
    # Community → Discord/Facebook
    if "community" in lower or "komuniti" in lower or "discord" in lower or "facebook" in lower:
        if "https://discord.gg/" not in answer and "https://facebook.com/groups/" not in answer:
            answer += "\n\n👉 Join our community here: https://discord.gg/ / https://facebook.com/groups/"
    return answer

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

# ----------------- Car Support Helpers -----------------
def detect_car_support_query(text: str) -> bool:
    car_keywords = ["myvi", "perodua", "honda", "toyota", "proton", "kereta", "car", "support", "pasang", "compatible"]
    return any(k in text.lower() for k in car_keywords)

def extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None

# ----------------- RAG + LLM -----------------
def run_rag_dual(user_text: str, lang_hint: str = "EN") -> str:
    sys_prompt = (
        "You are Kai, Kommu’s polite and professional support assistant.\n"
        "- Always answer in a friendly and respectful tone.\n"
        "- Reply ONLY using the provided context.\n"
        "- Do NOT invent or make up links.\n"
        "- Only include links that are explicitly in the context or from the known official sources.\n"
        "- If the info is not in the context, politely say you don’t know.\n"
        "- No emojis. Maximum 2 links."
    )
    lang_instruction = "Jawab dalam BM dengan nada mesra." if lang_hint == "BM" else "Answer politely in English."

    # Step 1: SOP RAG
    context = rag_sop.build_context(user_text, topk=4) if rag_sop else ""
    if context.strip():
        prompt = f"User: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        llm = filter_hallucinated_links(llm, context)
        llm = enforce_link_intents(user_text, llm)
        if llm:
            return llm

    # Step 2: Website RAG
    context = rag_web.build_context(user_text, topk=4) if rag_web else ""
    if context.strip():
        prompt = f"User: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        llm = filter_hallucinated_links(llm, context)
        llm = enforce_link_intents(user_text, llm)
        if llm:
            return llm

    return ""

def maybe_add_la_hint(user_id, msg, lang):
    update_reply_state(user_id)
    sess = get_session(user_id)
    if sess["reply_count"] >= 2:
        hint = " Jika anda perlukan ejen manusia, taip LA." if lang=="BM" else " If you need a live agent, type LA."
        msg += "\n" + hint
    return msg

# ----------------- RAG load on startup -----------------
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
            print(f"[SOP-DOC] Loaded {len(qas)} Q/A from Google Doc and rebuilt SOP RAG.")
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

# Webhook verification
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        log.info("[Kai] Webhook verified successfully")
        return PlainTextResponse(hub_challenge)
    log.warning(f"[Kai] Webhook verification failed: {hub_verify_token}")
    return PlainTextResponse("Forbidden", status_code=403)

# ----------------- Admin Endpoints -----------------
@app.api_route("/admin/freeze", methods=["POST", "GET"])
async def admin_freeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN: 
        return PlainTextResponse("Forbidden", 403)
    freeze(user_id, True, mode="admin")
    log.info(f"[ADMIN] Freeze user {user_id}")
    return PlainTextResponse("Frozen")

@app.api_route("/admin/unfreeze", methods=["POST", "GET"])
async def admin_unfreeze(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN: 
        return PlainTextResponse("Forbidden", 403)
    freeze(user_id, False, mode="user")
    log.info(f"[ADMIN] Unfrozen {user_id}")
    return PlainTextResponse("Unfrozen")

@app.api_route("/admin/reset", methods=["POST", "GET"])
async def admin_reset(request: Request):
    token = (request.query_params.get("token") or (await request.form()).get("token") or "")
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    import sqlite3
    conn = sqlite3.connect("sessions.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM qna_log WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    log.info(f"[ADMIN] Reset session for {user_id}")
    return PlainTextResponse(f"Reset session for {user_id}")

# ----------------- Webhook POST -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        log.info(f"[Kai] IN: {json.dumps(data)}")

        entry = data.get("entry", [])
        if not entry:
            return JSONResponse({"status": "no_entry"})
        changes = entry[0].get("changes", [])
        if not changes:
            return JSONResponse({"status": "no_changes"})
        value = changes[0].get("value", {})
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
            send_whatsapp_message(
                wa_from,
                add_footer(
                    "We received a media message that I cannot process at the moment. "
                    "A live agent will reach out to assist you." if not is_malay(body) else
                    "Kami menerima mesej media yang tidak dapat diproses buat masa ini. "
                    "Seorang ejen manusia akan menghubungi anda.",
                    "BM" if is_malay(body) else "EN"
                )
            )
            return JSONResponse({"status": "unsupported"})

        if not body:
            return JSONResponse({"status": "empty"})

        # -------- Session + Language --------
        lower = norm(body)
        sess = get_session(wa_from)
        lang = sess["lang"] or ("BM" if is_malay(body) else "EN")
        set_lang(wa_from, lang)
        aft = not is_office_hours()

        # -------- Resume --------
        if sess["frozen"]:
            if lower in {"resume", "unfreeze", "sambung"}:
                freeze(wa_from, False, mode="user")
                msg_out = "Bot resumed. How can I assist you?" if lang == "EN" else \
                          "Bot disambung semula. Apa yang boleh saya bantu?"
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "resumed"})
            msg_out = (
                "A live agent will assist you soon. Type *resume* if you’d like me to continue."
                if lang == "EN"
                else "Seorang ejen manusia akan membantu anda tidak lama lagi. Taip *resume* untuk sambung berbual dengan saya."
            )
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "frozen"})

        # -------- Live Agent Request --------
        if has_any(["la", "human", "request human"], lower):
            freeze(wa_from, True, mode="user")
            msg_out = (
                "Sure, I’ll connect you with a live agent during office hours. This chat is now paused."
                if lang == "EN"
                else "Baik, saya akan hubungkan anda dengan ejen manusia pada waktu pejabat. Chat kini dijeda."
            )
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "agent"})

        # -------- Greeting --------
        if not sess["greeted"] and has_any(["hi", "hello", "hai", "helo", "mula", "start", "menu"], lower) and len(lower.split()) <= 3:
            msg_out = (
                "Hello! I’m Kai, Kommu’s support chatbot.\nThis chat is supervised by humans during office hours."
                if lang == "EN"
                else "Hai! Saya Kai, chatbot sokongan Kommu.\nPerbualan ini dipantau oleh ejen manusia pada waktu pejabat."
            )
            if aft:
                msg_out += after_hours_suffix(lang)
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            return JSONResponse({"status": "greeted"})

        # -------- Warranty Lookup --------
        if 6 <= len(lower) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = (
                    f"Here is the warranty status: {warranty_text_from_row(row)}"
                    if lang == "EN"
                    else f"Inilah status waranti: {warranty_text_from_row(row)}"
                )
                if aft:
                    msg_out += after_hours_suffix(lang)
                msg_out = maybe_add_la_hint(wa_from, msg_out, lang)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "warranty"})

        # -------- Car Support Flow --------
        if detect_car_support_query(body):
            year = extract_year(body)
            if year and year < MIN_SUPPORTED_YEAR:
                msg_out = (
                    f"Apologies, KommuAssist only supports cars from {MIN_SUPPORTED_YEAR} onwards."
                    if lang == "EN" else
                    f"Maaf, KommuAssist hanya menyokong kereta dari tahun {MIN_SUPPORTED_YEAR} dan ke atas."
                )
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "car_not_supported"})
            if not year:
                msg_out = (
                    "Could you please tell me your car variant and year? (e.g., Myvi 2019 H Spec)"
                    if lang == "EN" else
                    "Boleh beritahu saya varian dan tahun kereta anda? (cth: Myvi 2019 H Spec)"
                )
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                return JSONResponse({"status": "car_need_details"})
            # if year ok, fallthrough to RAG

        # -------- RAG Default --------
        answer = run_rag_dual(body, lang_hint=lang)
        if answer:
            if aft:
                answer += after_hours_suffix(lang)
            answer = maybe_add_la_hint(wa_from, answer, lang)
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            return JSONResponse({"status": "answered"})

        # -------- Fallback --------
        msg_out = (
            "I can help with pricing, installation, office hours, warranty, test drives, and general product support."
            if lang == "EN"
            else "Saya boleh bantu dengan harga, pemasangan, waktu pejabat, waranti, pandu uji, dan sokongan produk umum."
        )
        if aft:
            msg_out += after_hours_suffix(lang)
        msg_out = maybe_add_la_hint(wa_from, msg_out, lang)
        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
        return JSONResponse({"status": "fallback"})

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[Kai] ERR webhook: {e}\n{tb}")
        return JSONResponse({"status": "error", "error": str(e)})
