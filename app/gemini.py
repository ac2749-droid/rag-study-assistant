"""The only module that talks to the Gemini API: client, retries and error translation."""
import logging
import time
from functools import lru_cache

from google import genai
from google.genai import errors, types

from app.config import get_settings

log = logging.getLogger(__name__)

RETRYABLE_CODES = {429, 500, 502, 503, 504}  # rate limit / temporary server trouble
MAX_ATTEMPTS = 4
EMBED_BATCH_SIZE = 100  # the embeddings endpoint accepts at most 100 texts per request

_sleep = time.sleep  # indirection so tests can skip the waiting


class GeminiError(RuntimeError):
    """The request failed for a reason retrying won't fix (bad key, unknown model, ...)."""


class GeminiBusyError(GeminiError):
    """Gemini stayed overloaded or rate-limited through every retry."""


@lru_cache
def get_client() -> genai.Client:
    key = get_settings().gemini_api_key
    if not key:
        raise GeminiError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=key)


def _with_retries(call):
    """Run `call()`, retrying temporary failures with a growing pause."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call()
        except errors.APIError as exc:
            if exc.code not in RETRYABLE_CODES:
                raise GeminiError(f"Gemini API error {exc.code}: {exc.message}") from exc
            if attempt == MAX_ATTEMPTS:
                raise GeminiBusyError(
                    f"Gemini still unavailable after {MAX_ATTEMPTS} attempts ({exc.code})"
                ) from exc
            delay = 2 * attempt
            log.warning("Gemini returned %s; retry %d/%d in %ds", exc.code, attempt, MAX_ATTEMPTS - 1, delay)
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    """Return one embedding per text.

    task_type is "RETRIEVAL_DOCUMENT" for stored chunks and "RETRIEVAL_QUERY" for questions;
    Gemini tunes the vectors so the two match well.
    """
    client = get_client()
    model = get_settings().embed_model
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        response = _with_retries(
            lambda batch=batch: client.models.embed_content(
                model=model,
                contents=batch,
                config=types.EmbedContentConfig(task_type=task_type),
            )
        )
        vectors.extend(list(e.values) for e in response.embeddings)
    return vectors


def generate(prompt: str) -> str:
    """Ask the chat model and return its text answer."""
    client = get_client()
    model = get_settings().chat_model
    response = _with_retries(lambda: client.models.generate_content(model=model, contents=prompt))
    if not response.text:
        raise GeminiError("Gemini returned an empty response (it may have been blocked).")
    return response.text
