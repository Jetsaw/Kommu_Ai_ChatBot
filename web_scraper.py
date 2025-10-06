import requests, json, os, logging
from bs4 import BeautifulSoup

log = logging.getLogger("kai")

CAR_SUPPORT_URL = "https://kommu.ai/support/"
OUTPUT_PATH = os.path.join("rag", "supported_cars.json")

def scrape():
    """Scrape supported car list from Kommu website and save structured JSON."""
    try:
        res = requests.get(CAR_SUPPORT_URL, timeout=20)
        if res.status_code != 200:
            raise Exception(f"Failed to fetch page, status={res.status_code}")

        soup = BeautifulSoup(res.text, "html.parser")
        sections = soup.find_all(["h3", "h4", "p", "li"])

        supported, current_brand = [], None
        for tag in sections:
            text = tag.get_text(strip=True)
            if not text:
                continue

            if text.lower() in ["perodua", "proton", "honda", "toyota", "byd", "lexus"]:
                current_brand = text
                continue

            if current_brand:
                if "(" in text and ")" in text:
                    model = text.split("(")[0].strip()
                    details = text.split("(", 1)[1].rstrip(")").strip()
                else:
                    model, details = text.strip(), ""
                supported.append({
                    "brand": current_brand,
                    "model": model,
                    "details": details
                })

        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump({"cars": supported}, f, ensure_ascii=False, indent=2)

        log.info(f"[AutoCar]  Scraped {len(supported)} supported car entries from Kommu.ai.")
        return supported

    except Exception as e:
        log.error(f"[AutoCar]  Error scraping: {e}")
        return []

if __name__ == "__main__":
    data = scrape()
    print(json.dumps(data, indent=2, ensure_ascii=False))
