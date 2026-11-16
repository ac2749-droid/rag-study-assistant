"""Split page text into chunks small enough to embed and retrieve precisely."""
from dataclasses import dataclass

from app.pdf_loader import Page


@dataclass(frozen=True)
class Chunk:
    source: str  # file name the chunk came from
    page: int
    index: int  # position within the page
    text: str

    @property
    def id(self) -> str:
        return f"{self.source}:p{self.page}-c{self.index}"


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Cut text into pieces of at most `size` chars, preferring line breaks, then spaces.

    Neighbouring pieces share about `overlap` characters so an idea that straddles a cut
    still appears whole in one of them.
    """
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than size")
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Back up to a natural break in the second half of the window.
            cut = text.rfind("\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + size // 2, end)
            if cut != -1:
                end = cut
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        # Step back by `overlap` for the next piece, but always move forward, and nudge the
        # start forward to a word boundary so a piece never begins with half a word.
        next_start = max(end - overlap, start + 1)
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start
    return pieces


def chunk_pages(
    pages: list[Page], source: str, *, size: int, overlap: int, page_limit: int
) -> list[Chunk]:
    """Turn pages into chunks. A short page is one idea (or one code sample), so it stays whole."""
    chunks: list[Chunk] = []
    for page in pages:
        if not page.text:
            continue
        pieces = [page.text] if len(page.text) <= page_limit else split_text(page.text, size, overlap)
        chunks.extend(
            Chunk(source=source, page=page.number, index=i, text=piece)
            for i, piece in enumerate(pieces)
        )
    return chunks
