"""ChromaDB storage: save embedded chunks, search by meaning, list what's stored."""
import logging
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


@lru_cache
def get_collection() -> chromadb.Collection:
    settings = get_settings()
    db = chromadb.PersistentClient(path=str(settings.chroma_path))
    # cosine = compare by direction (meaning), not vector length
    return db.get_or_create_collection(settings.collection_name, metadata={"hnsw:space": "cosine"})


def add_chunks(chunks: list[Chunk], source: str) -> int:
    """Embed and store the chunks of one file, replacing anything stored for it before."""
    if not chunks:
        raise ValueError(f"No text to store for {source!r} (is it a scanned/image-only PDF?)")
    # Embed first: if Gemini fails we haven't touched the existing data yet.
    vectors = gemini.embed_texts([c.text for c in chunks], "RETRIEVAL_DOCUMENT")
    collection = get_collection()
    collection.delete(where={"source": source})
    collection.upsert(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        embeddings=vectors,
        metadatas=[{"source": c.source, "page": c.page} for c in chunks],
    )
    log.info("Stored %d chunks for %s", len(chunks), source)
    return len(chunks)


def search(vector: list[float], k: int) -> list[Hit]:
    """Return the k stored chunks closest in meaning to `vector`, closest first."""
    result = get_collection().query(query_embeddings=[vector], n_results=k)
    return [
        Hit(text=doc, source=meta["source"], page=meta["page"], distance=dist)
        for doc, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        )
    ]


def delete_source(source: str) -> int:
    """Remove every chunk stored for one file. Returns how many were removed."""
    collection = get_collection()
    before = collection.count()
    collection.delete(where={"source": source})
    return before - collection.count()


def list_sources() -> dict[str, int]:
    """Map each stored file name to its number of chunks."""
    counts: dict[str, int] = {}
    for meta in get_collection().get(include=["metadatas"])["metadatas"]:
        counts[meta["source"]] = counts.get(meta["source"], 0) + 1
    return counts
