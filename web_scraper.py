import requests, os, json
from bs4 import BeautifulSoup
from config import RAG_DIR

URL = "https://kommu.ai/support/"
OUTPUT_FILE = os.path.join(RAG_DIR, "website_data.json")

def scrape():
    try:
        print("[Scraper] Fetching support page…")
        r = requests.get(URL, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cars = []
        # Example assumption: cars listed in <div class="car-model"> with sub-info
        # Adjust selectors based on real page structure
        for block in soup.select(".car-model, .supported-car, .car-item"):
            model = block.find("h3") or block.find("h2")
            if not model:
                continue
            model_name = model.get_text(strip=True)

            variants = []
            for li in block.find_all("li"):
                txt = li.get_text(" ", strip=True)
                if txt:
                    variants.append(txt)

            if variants:
                for v in variants:
                    # Try to split variant and year (example: "Myvi 2019 H Spec")
                    year = None
                    for word in v.split():
                        if word.isdigit() and len(word) == 4:
                            year = int(word)
                            break
                    cars.append({
                        "model": model_name,
                        "variant": v,
                        "year": year or ""
                    })
            else:
                cars.append({"model": model_name, "variant": "Unknown", "year": ""})

        data = {"cars": cars}
        os.makedirs(RAG_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[Scraper] Saved {len(cars)} cars → {OUTPUT_FILE}")
        return data

    except Exception as e:
        print("[Scraper] Error:", e)
        return {"cars": []}

if __name__ == "__main__":
    scrape()
