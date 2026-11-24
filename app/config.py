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
    gemini_timeout: float  # seconds to wait for any single Gemini request before giving up on it
    embed_texts_per_minute: int  # indexing pace; Gemini's free plan allows 100/min. 0 = no pacing (paid)
    embed_model: str
    chat_model: str
    chat_fallback_models: tuple[str, ...]  # tried in order if chat_model is rate-limited or overloaded
    chroma_path: Path
    collection_name: str
    chunk_size: int  # max characters per chunk when a page has to be split
    chunk_overlap: int  # characters shared by neighbouring chunks
    page_limit: int  # pages up to this many characters stay a single chunk
    per_source_k: int  # chunks taken from EACH document, so every document gets a say
    top_k: int  # most chunks given to the LLM in total, after the relevance cutoff
    max_distance: float  # cosine distance cutoff; farther chunks are treated as irrelevant
    data_dir: Path  # where the accounts/sessions/audit database lives
    session_hours: int  # how long a sign-in lasts
    cookie_secure: bool  # mark the sign-in cookie "Secure" (HTTPS only); also done automatically on https
    ask_per_minute: int  # questions one user may ask per minute (protects the free Gemini allowance)


@lru_cache
def get_settings() -> Settings:
    settings = Settings(
        app_name=os.getenv("APP_NAME", "Anthony's Study Assistant"),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_timeout=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")),
        embed_texts_per_minute=int(os.getenv("EMBED_TEXTS_PER_MINUTE", "90")),
        embed_model=os.getenv("EMBED_MODEL", "gemini-embedding-001"),
        chat_model=os.getenv("CHAT_MODEL", "gemini-3.5-flash"),
        chat_fallback_models=tuple(
            name.strip()
            for name in os.getenv("CHAT_FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.1-flash-lite").split(",")
            if name.strip()
        ),
        chroma_path=Path(os.getenv("CHROMA_PATH", PROJECT_ROOT / "chroma_db")),
        collection_name=os.getenv("COLLECTION_NAME", "lecture_notes"),
        # Larger sections mean fewer embeddings per document, which matters because Gemini's free plan
        # allows only ~1,000 per day (a 370-page manual needs 439 at these sizes, 1,015 at 800/1200).
        chunk_size=int(os.getenv("CHUNK_SIZE", "1500")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "150")),
        page_limit=int(os.getenv("PAGE_LIMIT", "2400")),
        per_source_k=int(os.getenv("PER_SOURCE_K", "2")),
        top_k=int(os.getenv("TOP_K", "8")),
        max_distance=float(os.getenv("MAX_DISTANCE", "0.38")),
        data_dir=Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")),
        session_hours=int(os.getenv("SESSION_HOURS", "12")),
        cookie_secure=os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
        ask_per_minute=int(os.getenv("ASK_PER_MINUTE", "20")),
    )
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE")
    if settings.gemini_timeout <= 0:
        raise ValueError("GEMINI_TIMEOUT_SECONDS must be positive")
    if settings.embed_texts_per_minute < 0:
        raise ValueError("EMBED_TEXTS_PER_MINUTE must be 0 (no pacing) or a positive number")
    if settings.top_k < 1 or settings.per_source_k < 1:
        raise ValueError("TOP_K and PER_SOURCE_K must be at least 1")
    if settings.session_hours < 1 or settings.ask_per_minute < 1:
        raise ValueError("SESSION_HOURS and ASK_PER_MINUTE must be at least 1")
    return settings
