"""Read a PDF and return its text page by page."""
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class Page:
    number: int  # 1-based, matches the page number you see in a PDF viewer
    text: str


def load_pdf(path: str | Path) -> list[Page]:
    """Extract text from every page. Pages with no extractable text get an empty string."""
    reader = PdfReader(str(path))
    return [
        Page(number=i, text=(page.extract_text() or "").strip())
        for i, page in enumerate(reader.pages, start=1)
    ]
