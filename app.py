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
    add_message_to_history, get_history
)
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

FOOTER_EN = "\n\nI am Kai, Kommu’s support chatbot (beta). Please send your questions one by one. If you’d like a live agent, type LA."
FOOTER_BM = "\n\nSaya Kai, chatbot sokongan Kommu (beta). Sila hantar soalan anda satu demi satu. Jika anda mahu bercakap dengan ejen manusia, taip LA."

# ----------------- Allowed Links -----------------
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
    return ("\n\nPS: Sekarang di luar waktu pejabat."
            if lang == "BM"
            else "\n\nPS: At the moment we’re outside office hours. A live agent will follow up later.")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

def has_any(words, text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)

def add_footer(answer: str, lang: str) -> str:
    footer = FOOTER_BM if lang == "BM" else FOOTER_EN
    return (answer or "").rstrip() + footer

def filter_hallucinated_links(answer: str, context: str) -> str:
    context_links = set(re.findall(r"(https?://\S+)", context))
    answer_links = set(re.findall(r"(https?://\S+)", answer))
    valid_links = context_links.union(set(ALLOWED_LINKS))
    for u in answer_links:
        if not any(u.startswith(v) for v in valid_links):
            answer = answer.replace(u, "")
    return answer.strip()

def enforce_link_intents(user_text: str, answer: str) -> str:
    lower = user_text.lower()
    if "test drive" in lower or "pandu uji" in lower:
        if "https://calendly.com/kommuassist/test-drive" not in answer:
            answer += "\n\n Book a test drive here: https://calendly.com/kommuassist/test-drive"
    if "price" in lower or "harga" in lower or "buy" in lower or "beli" in lower:
        if "https://kommu.ai/store/" not in answer:
            answer += "\n\n You can view pricing here: https://kommu.ai/store/"
    if "community" in lower or "komuniti" in lower or "discord" in lower or "facebook" in lower:
        if "https://discord.gg/" not in answer and "https://facebook.com/groups/" not in answer:
            answer += "\n\n Join our community: https://discord.gg/ / https://facebook.com/groups/"
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
def load_supported_cars():
    try:
        with open(os.path.join(RAG_DIR, "website_data.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
            # Normalize: wrap list into dict
            if isinstance(data, list):
                return {"cars": data}
            return data
    except Exception:
        return {"cars": []}

SUPPORTED_CARS = load_supported_cars()

def detect_car_support_query(text: str) -> bool:
    car_keywords = [
        "myvi", "perodua", "honda", "toyota", "proton", "nissan", "mazda",
        "chery", "tiggo", "omoda", "byd", "geely", "kereta", "car", "support", "compatible"
    ]
    return any(k in text.lower() for k in car_keywords)

def extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None

def get_supported_variants(model: str):
    results = []
    cars_data = SUPPORTED_CARS["cars"] if isinstance(SUPPORTED_CARS, dict) else SUPPORTED_CARS
    for entry in cars_data:
        if model.lower() in entry["model"].lower():
            results.append(entry)
    return results

# ----------------- RAG + LLM -----------------
def run_rag_dual(user_text: str, lang_hint: str = "EN", user_id: str = None) -> str:
    sys_prompt = (
        "You are Kai, Kommu’s polite and professional support assistant.\n"
        "- Always answer in a friendly and respectful tone.\n"
        "- Reply ONLY using the provided context.\n"
        "- Do NOT invent or make up links.\n"
        "- If the user ask in Malay reply in malay language.\n"
        "- Only include links from context or official sources.\n"
        "- If info is not found, politely say you don’t know.\n"
        "- No emojis. Max 3 links."
    )
    lang_instruction = "Jawab dalam BM dengan nada mesra." if lang_hint == "BM" else "Answer politely in English."

    # Conversation history
    history_text = ""
    if user_id:
        history = get_history(user_id)
        if history:
            history_text = "\n".join([f"{h['role']}: {h['text']}" for h in history])

    # Step 1: SOP RAG
    context = rag_sop.build_context(user_text, topk=4) if rag_sop else ""
    if context.strip():
        prompt = f"{history_text}\nUser: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        llm = filter_hallucinated_links(llm, context)
        llm = enforce_link_intents(user_text, llm)
        if llm:
            if lang_hint == "BM":
                llm = GoogleTranslator(source="auto", target="ms").translate(llm)
            return llm

    # Step 2: Website RAG
    context = rag_web.build_context(user_text, topk=4) if rag_web else ""
    if context.strip():
        prompt = f"{history_text}\nUser: {user_text}\n\nContext:\n{context}\n\n{lang_instruction}"
        llm = chat_completion(sys_prompt, prompt)
        llm = filter_hallucinated_links(llm, context)
        llm = enforce_link_intents(user_text, llm)
        if llm:
            if lang_hint == "BM":
                llm = GoogleTranslator(source="auto", target="ms").translate(llm)
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

# ----------------- Admin Endpoints -----------------
@app.api_route("/admin/freeze", methods=["POST", "GET"])
async def admin_freeze(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    freeze(user_id, True, mode="admin")
    log.info(f"[ADMIN] Freeze user {user_id}")
    return PlainTextResponse("Frozen")

@app.api_route("/admin/unfreeze", methods=["POST", "GET"])
async def admin_unfreeze(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    user_id = request.query_params.get("user_id") or (await request.form()).get("user_id")
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    freeze(user_id, False, mode="user")
    log.info(f"[ADMIN] Unfrozen {user_id}")
    return PlainTextResponse("Unfrozen")

@app.api_route("/admin/refresh", methods=["POST", "GET"])
async def admin_refresh(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    try:
        txt = fetch_sop_doc_text()
        qas = parse_qas_from_text(txt)
        if qas:
            with open(SOP_JSON_PATH,"w",encoding="utf-8") as f:
                json.dump(qas,f,ensure_ascii=False,indent=2)
            rebuild_rag()
            load_rag()
        scrape_site()
        fetch_warranty_all()
        return PlainTextResponse("Manual refresh completed")
    except Exception as e:
        return PlainTextResponse(f"Error during refresh: {e}", 500)

@app.api_route("/admin/scrape", methods=["POST", "GET"])
async def admin_scrape(request: Request):
    token = request.query_params.get("token") or (await request.form()).get("token") or ""
    if token != ADMIN_TOKEN:
        return PlainTextResponse("Forbidden", 403)
    try:
        data = scrape_site()
        global SUPPORTED_CARS
        SUPPORTED_CARS = load_supported_cars()
        log.info(f"[ADMIN] Scraped {len(SUPPORTED_CARS.get('cars', []))} cars")
        return PlainTextResponse("Scrape completed")
    except Exception as e:
        return PlainTextResponse(f"Error during scrape: {e}", 500)
# ----------------- Webhook POST -----------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
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

        log.info("[Kai] IN: from=%s type=%s text=%s",
                 wa_from, msg_type, msg.get("text", {}).get("body"))

        # -------- Unsupported Message Types --------
        if msg_type == "text":
            body = msg["text"]["body"].strip()
        else:
            freeze(wa_from, True, mode="user")
            send_whatsapp_message(
                wa_from,
                add_footer("We received a media message that isn’t supported. A live agent will assist you.", "EN")
            )
            return JSONResponse({"status": "unsupported"})

        if not body:
            return JSONResponse({"status": "empty"})

        # -------- Session + Language --------
        lower = norm(body)
        sess = get_session(wa_from)
        lang = "BM" if is_malay(body) else "EN"
        set_lang(wa_from, lang)
        aft = not is_office_hours()

        add_message_to_history(wa_from, "user", body)
        log.info(f"[Kai] Detected language={lang} for user={wa_from}")

        # -------- Greetings --------
        if not sess.get("greeted") and has_any(["hi","hello","hai","helo","mula","start","menu"], lower) and len(lower.split()) <= 3:
            msg_out = ("Hi! I'm Kai - Kommu Chatbot. This chat is handled by a chatbot (beta)."
                       if lang=="EN" else
                       "Hai! Saya Kai - Chatbot Kommu. Perbualan ini dikendalikan oleh chatbot (beta).")
            if aft: msg_out += after_hours_suffix(lang)
            sess["greeted"] = True
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "greeted"})

        # -------- Frozen / LA --------
        if sess.get("frozen"):
            if lower in {"resume","unfreeze","sambung"}:
                freeze(wa_from, False, mode="user")
                msg_out = "Bot resumed. How can I help?" if lang=="EN" else "Bot disambung semula. Ada apa saya boleh bantu?"
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "resumed"})
            msg_out = ("A live agent will assist you soon. Type *resume* to continue with the bot."
                       if lang=="EN" else
                       "Seorang ejen manusia akan membantu anda. Taip *resume* untuk teruskan berbual dengan bot.")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "frozen"})

        if has_any(["la","human","request human"], lower):
            freeze(wa_from, True, mode="user")
            msg_out = ("A live agent will reach out during office hours. Chat is frozen."
                       if lang=="EN" else
                       "Ejen manusia akan hubungi anda pada waktu pejabat. Chat dibekukan.")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "agent"})

        # -------- Warranty Lookup --------
        if 6 <= len(body) <= 20:
            row = warranty_lookup_by_dongle(body)
            if row:
                msg_out = (f"Warranty status: {warranty_text_from_row(row)}"
                           if lang=="EN" else
                           f"Status waranti: {warranty_text_from_row(row)}")
                if aft: msg_out += after_hours_suffix(lang)
                msg_out = maybe_add_la_hint(wa_from, msg_out, lang)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "warranty"})

        # -------- Car Support Flow --------
        if detect_car_support_query(body):
            model = None
            cars_data = SUPPORTED_CARS["cars"] if isinstance(SUPPORTED_CARS, dict) else SUPPORTED_CARS

            for entry in cars_data:
                model_name = entry.get("model")
                if model_name and model_name.lower() in lower:
                    model = model_name
                    break

            #  Unknown model
            if not model:
                msg_out = ("I’m not sure about that car. Does it have ACC & LKA?"
                           if lang=="EN" else
                           "Saya tidak pasti tentang kereta itu. Adakah ia ada ACC & LKA?")
                set_last_intent(wa_from, "car_unknown")
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_unknown"})

            #  Known supported model
            variants = get_supported_variants(model)
            year = extract_year(body)

            if not year:
                msg_out = (f"Yes, {model} is one of our supported models \n"
                           f"Could you tell me which variant and year (e.g., {model} 2021 AV Spec)?\n\n"
                           "Our Kommu KA2 pairs perfectly with it — it enhances your highway experience by making adaptive cruise smoother and lane-keeping steadier."
                           if lang=="EN" else
                           f"Ya, {model} adalah antara model yang disokong \n"
                           f"Boleh beritahu varian dan tahun (cth: {model} 2021 AV Spec)?\n\n"
                           "Kommu KA2 sangat sesuai — meningkatkan pengalaman pemanduan di lebuh raya dengan cruise control yang lebih lancar dan LKA yang lebih stabil.")
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_supported_variant_request"})

            if year and year < MIN_SUPPORTED_YEAR:
                msg_out = (f"Sorry, KommuAssist supports cars from {MIN_SUPPORTED_YEAR} onwards."
                           if lang=="EN" else
                           f"Maaf, KommuAssist hanya menyokong kereta dari tahun {MIN_SUPPORTED_YEAR} ke atas.")
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_not_supported"})

            msg_out = (f"Perfect — the {model} is fully supported 🎉\n"
                       "With Kommu KA2, you’ll enjoy smoother adaptive cruise and steadier lane assistance, "
                       "especially on long highway drives."
                       if lang=="EN" else
                       f"Hebat — {model} anda disokong sepenuhnya 🎉\n"
                       "Dengan Kommu KA2, anda akan alami cruise control lebih lancar dan bantuan lorong lebih stabil, "
                       "terutama semasa memandu jauh di lebuh raya.")
            send_whatsapp_message(wa_from, add_footer(msg_out, lang))
            add_message_to_history(wa_from, "bot", msg_out)
            return JSONResponse({"status": "car_supported_confirmed"})

        # -------- Car Unknown ACC/LKA Follow-up --------
        last_intent = get_last_intent(wa_from)
        if last_intent == "car_unknown":
            if has_any(["yes","ya","ok","baik"], lower):
                msg_out = ("Great! Since your car has ACC & LKA, you might be interested in a Kommu test drive.\n"
                           " Book here: https://calendly.com/kommuassist/test-drive\n\n"
                           "Please send a picture of your steering wheel so our CS team can confirm. "
                           "Your chat will be handed over to a live agent, but you can type *resume* to continue with the bot."
                           if lang=="EN" else
                           "Bagus! Oleh kerana kereta anda ada ACC & LKA, anda mungkin berminat untuk pandu uji Kommu.\n"
                           " Tempah di sini: https://calendly.com/kommuassist/test-drive\n\n"
                           "Sila hantar gambar stereng untuk pengesahan CS. "
                           "Perbualan anda akan dihantar ke ejen manusia, tetapi anda boleh taip *resume* untuk sambung dengan bot.")
                freeze(wa_from, True, mode="user")
                set_last_intent(wa_from, None)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_unknown_acc_lka"})
            elif has_any(["no","tak","tidak"], lower):
                msg_out = ("Thank you. Without ACC & LKA, your car may not be supported."
                           if lang=="EN" else
                           "Terima kasih. Tanpa ACC & LKA, kereta anda mungkin tidak disokong.")
                set_last_intent(wa_from, None)
                send_whatsapp_message(wa_from, add_footer(msg_out, lang))
                add_message_to_history(wa_from, "bot", msg_out)
                return JSONResponse({"status": "car_unknown_no_acc_lka"})

        # -------- RAG Default --------
        answer = run_rag_dual(body, lang_hint=lang, user_id=wa_from)
        if answer:
            if aft: answer += after_hours_suffix(lang)
            answer = maybe_add_la_hint(wa_from, answer, lang)
            send_whatsapp_message(wa_from, add_footer(answer, lang))
            add_message_to_history(wa_from, "bot", answer)
            return JSONResponse({"status": "answered"})

        # -------- Fallback --------
        msg_out = ("I can help with pricing, installation, office hours, warranty, test drives, and support."
                   if lang=="EN" else
                   "Saya boleh bantu dengan harga, pemasangan, waktu pejabat, waranti, pandu uji, dan sokongan produk.")
        if aft: msg_out += after_hours_suffix(lang)
        msg_out = maybe_add_la_hint(wa_from, msg_out, lang)
        send_whatsapp_message(wa_from, add_footer(msg_out, lang))
        add_message_to_history(wa_from, "bot", msg_out)
        return JSONResponse({"status": "fallback"})

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[Kai] ERR webhook: {e}\n{tb}")
        try:
            send_whatsapp_message(wa_from, " Sorry, I encountered an issue. Please try again.")
        except:
            pass
        return JSONResponse({"status": "error", "error": str(e)})
