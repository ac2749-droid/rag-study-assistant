"""All tunable settings in one place, read from environment variables (or a .env file)."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str  # shown as the page title and in the API docs
    max_upload_mb: int  # largest PDF accepted through the web page
    gemini_api_key: str | None
    embed_model: str
    chat_model: str
    chroma_path: Path
    collection_name: str
    chunk_size: int  # max characters per chunk when a page has to be split
    chunk_overlap: int  # characters shared by neighbouring chunks
    page_limit: int  # pages up to this many characters stay a single chunk
    per_source_k: int  # chunks taken from EACH document, so every document gets a say
    top_k: int  # most chunks given to the LLM in total, after the relevance cutoff
    max_distance: float  # cosine distance cutoff; farther chunks are treated as irrelevant


@lru_cache
def get_settings() -> Settings:
    settings = Settings(
        app_name=os.getenv("APP_NAME", "Anthony's Study Assistant"),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        embed_model=os.getenv("EMBED_MODEL", "gemini-embedding-001"),
        chat_model=os.getenv("CHAT_MODEL", "gemini-3.5-flash"),
        chroma_path=Path(os.getenv("CHROMA_PATH", PROJECT_ROOT / "chroma_db")),
        collection_name=os.getenv("COLLECTION_NAME", "lecture_notes"),
        chunk_size=int(os.getenv("CHUNK_SIZE", "800")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "100")),
        page_limit=int(os.getenv("PAGE_LIMIT", "1200")),
        per_source_k=int(os.getenv("PER_SOURCE_K", "2")),
        top_k=int(os.getenv("TOP_K", "8")),
        max_distance=float(os.getenv("MAX_DISTANCE", "0.38")),
    )
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE")
    if settings.top_k < 1 or settings.per_source_k < 1:
        raise ValueError("TOP_K and PER_SOURCE_K must be at least 1")
    return settings
