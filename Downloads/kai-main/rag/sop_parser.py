import json, re
from docx import Document
from config import SOP_DOCX_PATH, SOP_JSON_PATH

def parse_docx_to_qa():
    doc = Document(SOP_DOCX_PATH)
    text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    qas = []
    for block in re.split(r"(?=Q\s*:)", text):
        block = block.strip()
        if not block.lower().startswith("q"):
            continue
        q = re.search(r"Q\s*:\s*(.+?)\s*A\s*:", block, flags=re.S|re.I)
        a = re.search(r"A\s*:\s*(.+)$", block, flags=re.S|re.I)
        if q and a:
            qas.append({"question": " ".join(q.group(1).split()),
                        "answer":   " ".join(a.group(1).split())})
    with open(SOP_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(qas, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(qas)} Q/A → {SOP_JSON_PATH}")

if __name__ == "__main__":
    parse_docx_to_qa()
