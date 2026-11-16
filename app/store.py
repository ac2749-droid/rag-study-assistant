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


def search(vector: list[float], per_source_k: int) -> list[Hit]:
    """Return the closest chunks to `vector` from EACH stored file, closest first overall.

    Searching every file separately (rather than taking the global top few) means a broad
    question like "what is this about?" hears from all documents instead of just whichever
    one happens to have the single closest passages.
    """
    collection = get_collection()
    hits: list[Hit] = []
    for source, n_chunks in list_sources().items():
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


def delete_all() -> int:
    """Remove everything that has been stored. Returns how many chunks were removed."""
    collection = get_collection()
    ids = collection.get(include=[])["ids"]
    for start in range(0, len(ids), 1000):  # stay well under ChromaDB's per-call limit
        collection.delete(ids=ids[start : start + 1000])
    log.info("Removed all %d chunks", len(ids))
    return len(ids)


def delete_source(source: str) -> int:
    """Remove every chunk stored for one file. Returns how many were removed."""
    collection = get_collection()
    before = collection.count()
    collection.delete(where={"source": source})
    return before - collection.count()


def list_documents() -> list[dict]:
    """One entry per stored file, sorted by name: its chunk count and how many pages have text."""
    docs: dict[str, dict] = {}
    for meta in get_collection().get(include=["metadatas"])["metadatas"]:
        doc = docs.setdefault(meta["source"], {"source": meta["source"], "chunks": 0, "pages": set()})
        doc["chunks"] += 1
        doc["pages"].add(meta["page"])
    return sorted(
        ({"source": d["source"], "chunks": d["chunks"], "pages": len(d["pages"])} for d in docs.values()),
        key=lambda d: d["source"].lower(),
    )


def list_sources() -> dict[str, int]:
    """Map each stored file name to its number of chunks."""
    return {d["source"]: d["chunks"] for d in list_documents()}
