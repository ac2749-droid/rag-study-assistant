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
from app.chunking import Chunk, chunk_pages
from app.config import get_settings
from app.gemini import GeminiError
from app.pdf_loader import load_pdf


@dataclass(frozen=True)
class PreparedPdf:
    """A PDF that has been read and split, but not yet indexed (indexing is the slow part)."""

    name: str
    chunks: list[Chunk]
    empty_pages: list[int]  # pages with no extractable text (images, diagrams, scans)


@dataclass(frozen=True)
class IngestResult:
    chunks: int  # how many chunks were stored
    empty_pages: list[int]


def prepare_pdf(path: Path, name: str | None = None) -> PreparedPdf:
    """Read and chunk one PDF quickly. Raises PyPdfError if it can't be read, and ValueError if it
    has no extractable text at all."""
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
    if not chunks:
        raise ValueError(f"No text to store for {name!r} (is it a scanned/image-only PDF?)")
    return PreparedPdf(name=name, chunks=chunks, empty_pages=[p.number for p in pages if not p.text])


def ingest_pdf(path: Path, name: str | None = None, on_progress=None) -> IngestResult:
    """Read, chunk, embed and store one PDF under `name` (defaults to the file name), start to finish."""
    prepared = prepare_pdf(path, name)
    stored = store.add_chunks(prepared.chunks, prepared.name, on_progress=on_progress)
    return IngestResult(chunks=stored, empty_pages=prepared.empty_pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add PDFs to the study assistant's database.")
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to ingest")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    def show_progress(done: int, total: int) -> None:
        print(f"\r  indexed {done} of {total} sections", end="", flush=True)

    failures = 0
    for path in args.pdfs:
        print(f"Ingesting {path.name} ...")
        try:
            result = ingest_pdf(path, on_progress=show_progress)
            print()
            if result.empty_pages:
                print(f"  note: no text on page(s) {result.empty_pages} (images/diagrams are skipped)")
            print(f"  stored {result.chunks} chunks")
        except (OSError, PyPdfError, ValueError, GeminiError) as exc:
            failures += 1
            print(f"\n  FAILED: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
