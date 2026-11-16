"""Add PDFs to the database. Used by the web upload and by the command line:

    python -m app.ingest lecture1.pdf [lecture2.pdf ...]
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from pypdf.errors import PyPdfError

from app import store
from app.chunking import chunk_pages
from app.config import get_settings
from app.gemini import GeminiError
from app.pdf_loader import load_pdf


@dataclass(frozen=True)
class IngestResult:
    chunks: int  # how many chunks were stored
    empty_pages: list[int]  # pages with no extractable text (images, diagrams, scans)


def ingest_pdf(path: Path, name: str | None = None) -> IngestResult:
    """Load, chunk, embed and store one PDF under `name` (defaults to the file name).

    Raises ValueError if the PDF has no extractable text at all.
    """
    settings = get_settings()
    name = name or path.name
    pages = load_pdf(path)
    chunks = chunk_pages(
        pages,
        name,
        size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        page_limit=settings.page_limit,
    )
    stored = store.add_chunks(chunks, name)
    return IngestResult(chunks=stored, empty_pages=[p.number for p in pages if not p.text])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add PDFs to the study assistant's database.")
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to ingest")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    failures = 0
    for path in args.pdfs:
        print(f"Ingesting {path.name} ...")
        try:
            result = ingest_pdf(path)
            if result.empty_pages:
                print(f"  note: no text on page(s) {result.empty_pages} (images/diagrams are skipped)")
            print(f"  stored {result.chunks} chunks")
        except (OSError, PyPdfError, ValueError, GeminiError) as exc:
            failures += 1
            print(f"  FAILED: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
