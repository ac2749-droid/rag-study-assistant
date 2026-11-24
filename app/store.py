"""ChromaDB storage: save embedded chunks, search by meaning, list what's stored.

Every user has a private "library" (a ChromaDB collection of their own), so one person's documents can
never show up in another person's answers. Every function takes an optional `collection_name`; without
it the original library is used (the first account's, and what tests and the command line use).
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import chromadb

from app import gemini
from app.chunking import Chunk
from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    text: str
    source: str
    page: int
    distance: float  # cosine distance: 0 = identical meaning, larger = less related


@lru_cache(maxsize=256)
def get_collection(name: str | None = None) -> chromadb.Collection:
    settings = get_settings()
    db = chromadb.PersistentClient(path=str(settings.chroma_path))
    # cosine = compare by direction (meaning), not vector length
    return db.get_or_create_collection(name or settings.collection_name, metadata={"hnsw:space": "cosine"})


def delete_library(name: str) -> None:
    """Delete a whole library (used when an account is removed)."""
    chromadb.PersistentClient(path=str(get_settings().chroma_path)).delete_collection(name)
    get_collection.cache_clear()


def _stored_texts(source: str, collection_name: str | None) -> dict[str, str]:
    """{section id: text} for the sections of `source` that are already in the database."""
    found = get_collection(collection_name).get(where={"source": source}, include=["documents"])
    return dict(zip(found["ids"], found["documents"], strict=True))


def _in_batches(items: list, size: int = 1000):
    for start in range(0, len(items), size):  # stay well under ChromaDB's per-call limit
        yield items[start : start + size]


def add_chunks(
    chunks: list[Chunk],
    source: str,
    on_progress: Callable[[int, int], None] | None = None,
    *,
    collection_name: str | None = None,
) -> int:
    """Index the sections of one file, saving progress as it goes.

    Indexing a big document takes minutes and Gemini limits how many sections it will embed per minute
    and per day, so it can stop part-way. To make sure that never wastes work:
    - each batch is saved the moment it has been embedded;
    - sections already stored with identical text are reused, not embedded again, so adding the same
      file again after a failure continues where it stopped (and re-adding an unchanged file is free);
    - sections left over from an older version of the file are removed once everything is done.
    Every section records how many the whole file should have, so an unfinished file can be recognised.

    `on_progress(done, total)` counts reused sections as done.
    """
    if not chunks:
        raise ValueError(f"No text to store for {source!r} (is it a scanned/image-only PDF?)")
    collection = get_collection(collection_name)
    total = len(chunks)
    existing = _stored_texts(source, collection_name)
    todo = [c for c in chunks if existing.get(c.id) != c.text]
    reused = [c for c in chunks if existing.get(c.id) == c.text]
    if on_progress:
        on_progress(len(reused), total)

    def metadata(chunk: Chunk) -> dict:
        return {"source": chunk.source, "page": chunk.page, "total": total}

    for batch in _in_batches(reused):  # refresh the expected total on sections kept from an earlier run
        collection.update(ids=[c.id for c in batch], metadatas=[metadata(c) for c in batch])

    def save(start: int, vectors: list[list[float]]) -> None:
        batch = todo[start : start + len(vectors)]
        collection.upsert(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            embeddings=vectors,
            metadatas=[metadata(c) for c in batch],
        )

    if todo:
        gemini.embed_documents(
            [c.text for c in todo],
            on_progress=(lambda done, _: on_progress(len(reused) + done, total)) if on_progress else None,
            on_batch=save,
        )
    wanted = {c.id for c in chunks}
    for batch in _in_batches([stored_id for stored_id in existing if stored_id not in wanted]):
        collection.delete(ids=batch)
    log.info("Indexed %s: %d new sections, %d reused, %d total", source, len(todo), len(reused), total)
    return total


def search(vector: list[float], per_source_k: int, *, collection_name: str | None = None) -> list[Hit]:
    """Return the closest chunks to `vector` from EACH stored file, closest first overall.

    Searching every file separately (rather than taking the global top few) means a broad
    question like "what is this about?" hears from all documents instead of just whichever
    one happens to have the single closest passages.
    """
    collection = get_collection(collection_name)
    hits: list[Hit] = []
    for source, n_chunks in list_sources(collection_name=collection_name).items():
        result = collection.query(
            query_embeddings=[vector],
            n_results=min(per_source_k, n_chunks),
            where={"source": source},
        )
        hits.extend(
            Hit(text=doc, source=meta["source"], page=meta["page"], distance=dist)
            for doc, meta, dist in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
            )
        )
    return sorted(hits, key=lambda h: h.distance)


def delete_all(*, collection_name: str | None = None) -> int:
    """Remove everything that has been stored. Returns how many chunks were removed."""
    collection = get_collection(collection_name)
    ids = collection.get(include=[])["ids"]
    for batch in _in_batches(ids):
        collection.delete(ids=batch)
    log.info("Removed all %d chunks", len(ids))
    return len(ids)


def delete_source(source: str, *, collection_name: str | None = None) -> int:
    """Remove every chunk stored for one file. Returns how many were removed."""
    collection = get_collection(collection_name)
    before = collection.count()
    collection.delete(where={"source": source})
    return before - collection.count()


def list_documents(*, collection_name: str | None = None) -> list[dict]:
    """One entry per stored file, sorted by name: its chunk count and how many pages have text."""
    docs: dict[str, dict] = {}
    for meta in get_collection(collection_name).get(include=["metadatas"])["metadatas"]:
        doc = docs.setdefault(
            meta["source"], {"source": meta["source"], "chunks": 0, "pages": set(), "expected": 0}
        )
        doc["chunks"] += 1
        doc["pages"].add(meta["page"])
        doc["expected"] = max(doc["expected"], meta.get("total", 0))  # absent on files added long ago
    return sorted(
        (
            {
                "source": d["source"],
                "chunks": d["chunks"],
                "pages": len(d["pages"]),
                # A file whose indexing stopped early has fewer sections stored than it should.
                "complete": d["chunks"] >= d["expected"],
                "expected_chunks": max(d["expected"], d["chunks"]),
            }
            for d in docs.values()
        ),
        key=lambda d: d["source"].lower(),
    )


def list_sources(*, collection_name: str | None = None) -> dict[str, int]:
    """Map each stored file name to its number of chunks."""
    return {d["source"]: d["chunks"] for d in list_documents(collection_name=collection_name)}
