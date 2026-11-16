"""Step 2: split page text into overlapping chunks, remembering the page number."""
import sys
from load_pdf import load_pdf

CHUNK_SIZE = 800   # max characters per chunk
OVERLAP = 100      # characters shared between neighbouring chunks
PAGE_LIMIT = 1200  # pages up to this many characters stay as a single chunk


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """Cut text into pieces of at most `size` chars, preferring to cut at a line break."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Not at the end yet: try to back up to the last line break (or space)
            # in the second half of the window so we don't cut mid-line.
            cut = text.rfind("\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + size // 2, end)
            if cut != -1:
                end = cut
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)  # step back for overlap, always move forward
    return chunks


def chunk_pdf(path: str) -> list[dict]:
    """Return [{"id": ..., "page": ..., "text": ...}] for the whole PDF."""
    chunks = []
    for page in load_pdf(path):
        text = page["text"]
        # A slide is usually one idea (or one code sample), so keep it whole when it
        # is short enough; only fall back to splitting for genuinely long pages.
        pieces = [text] if 0 < len(text) <= PAGE_LIMIT else split_text(text)
        for n, piece in enumerate(pieces):
            chunks.append({"id": f"p{page['page']}-c{n}", "page": page["page"], "text": piece})
    return chunks


if __name__ == "__main__":
    chunks = chunk_pdf(sys.argv[1])
    print(f"{len(chunks)} chunks, sizes: {[len(c['text']) for c in chunks]}")
    for c in chunks:
        print(f"\n----- {c['id']} (page {c['page']}, {len(c['text'])} chars) -----")
        print(c["text"])
