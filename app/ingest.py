"""Command line tool: python -m app.ingest lecture1.pdf [lecture2.pdf ...]"""
import argparse
import logging
import sys
from pathlib import Path

from pypdf.errors import PyPdfError

from app import store
from app.chunking import chunk_pages
from app.config import get_settings
from app.gemini import GeminiError
from app.pdf_loader import load_pdf


def ingest_pdf(path: Path) -> int:
    """Load, chunk, embed and store one PDF. Returns the number of chunks stored."""
    settings = get_settings()
    pages = load_pdf(path)
    empty = [p.number for p in pages if not p.text]
    if empty:
        print(f"  note: no extractable text on page(s) {empty} (images/diagrams are skipped)")
    chunks = chunk_pages(
        pages,
        path.name,
        size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        page_limit=settings.page_limit,
    )
    return store.add_chunks(chunks, path.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add PDFs to the study assistant's database.")
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to ingest")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    failures = 0
    for path in args.pdfs:
        print(f"Ingesting {path.name} ...")
        try:
            n = ingest_pdf(path)
            print(f"  stored {n} chunks")
        except (OSError, PyPdfError, ValueError, GeminiError) as exc:
            failures += 1
            print(f"  FAILED: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
