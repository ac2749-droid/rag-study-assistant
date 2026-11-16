"""Step 3: turn each chunk into an embedding (Gemini) and store it in ChromaDB."""
import os
import sys

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

from chunk import chunk_pdf

load_dotenv()

EMBED_MODEL = "gemini-embedding-001"
DB_PATH = "chroma_db"
COLLECTION = "lecture_notes"


def get_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY not found. Put it in a .env file in this folder.")
    return genai.Client(api_key=key)


def embed_texts(client: genai.Client, texts: list[str], task_type: str) -> list[list[float]]:
    """Return one embedding (list of floats) per text. Sends at most 100 texts per request."""
    vectors = []
    for i in range(0, len(texts), 100):
        resp = client.models.embed_content(
            model=EMBED_MODEL,
            contents=texts[i : i + 100],
            config=types.EmbedContentConfig(task_type=task_type),
        )
        vectors.extend(e.values for e in resp.embeddings)
    return vectors


def get_collection():
    db = chromadb.PersistentClient(path=DB_PATH)
    # cosine = compare by direction (meaning), not by vector length
    return db.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def store_pdf(pdf_path: str) -> int:
    chunks = chunk_pdf(pdf_path)
    client = get_client()
    vectors = embed_texts(client, [c["text"] for c in chunks], "RETRIEVAL_DOCUMENT")
    collection = get_collection()
    # Remove any chunks previously stored for this file, so a re-run with different
    # chunking doesn't leave stale chunks behind.
    collection.delete(where={"source": os.path.basename(pdf_path)})
    # upsert = insert, or overwrite if the id already exists, so re-running is safe
    collection.upsert(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        embeddings=vectors,
        metadatas=[{"page": c["page"], "source": os.path.basename(pdf_path)} for c in chunks],
    )
    print(f"Embedding size: {len(vectors[0])} numbers per chunk")
    return len(chunks)


if __name__ == "__main__":
    n = store_pdf(sys.argv[1])
    print(f"Stored {n} chunks. Collection now holds {get_collection().count()} items.")
