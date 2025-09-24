import os, re, requests, json
from bs4 import BeautifulSoup
from config import RAG_DIR

PAGES = {
    "faq": "https://kommu.ai/faq/",
    "cars": "https://kommu.ai/support/",
    "home": "https://kommu.ai/",
}

OUT_JSON = os.path.join(RAG_DIR, "website_data.json")

def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup(["script","style"]): 
        script.decompose()
    text = soup.get_text(" ")
    text = re.sub(r"\s+"," ",text)
    return text.strip()

def scrape():
    """Scrape kommu.ai pages and save content into rag/website_data.json"""
    data = []
    for name,url in PAGES.items():
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent":"KaiBot/1.0"})
            r.raise_for_status()
            txt = clean_text(r.text)
            data.append({"page":name,"url":url,"content":txt})
            print(f"[scraper] {name} ok, {len(txt)} chars")
        except Exception as e:
            print(f"[scraper] fail {url}: {e}")
    os.makedirs(RAG_DIR, exist_ok=True)
    with open(OUT_JSON,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    return data
