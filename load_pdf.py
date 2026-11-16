"""Step 1: open one PDF and pull out the text, page by page."""
import sys
from pypdf import PdfReader


def load_pdf(path: str) -> list[dict]:
    """Return a list of {"page": number, "text": text} for every page."""
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append({"page": i, "text": text})
    return pages


if __name__ == "__main__":
    pdf_path = sys.argv[1]
    pages = load_pdf(pdf_path)
    print(f"Pages: {len(pages)}")
    print(f"Total characters: {sum(len(p['text']) for p in pages)}")
    empty = [p["page"] for p in pages if not p["text"]]
    print(f"Pages with no text: {empty or 'none'}")
    for p in pages:
        print(f"\n===== PAGE {p['page']} ({len(p['text'])} chars) =====")
        print(p["text"])
