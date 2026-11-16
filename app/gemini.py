"""The only module that talks to the Gemini API: client, retries, fallback and error translation."""
import importlib
import logging
import time
from functools import lru_cache

from google import genai
from google.genai import errors, types

from app.config import get_settings

log = logging.getLogger(__name__)


def _httpx_classes(name: str) -> tuple[type[Exception], ...]:
    """The named exception class from every installed httpx flavour (google-genai may use either)."""
    found = []
    for module_name in ("httpx", "httpx2"):
        try:
            found.append(getattr(importlib.import_module(module_name), name))
        except (ImportError, AttributeError):
            continue
    return tuple(found)


TIMEOUT_ERRORS = _httpx_classes("TimeoutException")  # Gemini didn't answer in time
NETWORK_ERRORS = _httpx_classes("TransportError")  # connection dropped, DNS trouble, ... (includes timeouts)

RETRYABLE_CODES = {429, 500, 502, 503, 504}  # rate limit / temporary server trouble
MAX_ATTEMPTS = 4
MAX_RATE_LIMIT_WAIT = 30  # seconds. If Google asks us to wait longer, don't hold the user's request.
EMBED_BATCH_SIZE = 100  # the embeddings endpoint accepts at most 100 texts per request

_sleep = time.sleep  # indirection so tests can skip the waiting


class GeminiError(RuntimeError):
    """The request failed for a reason retrying won't fix (bad key, unknown model, ...)."""


class GeminiBusyError(GeminiError):
    """Gemini stayed overloaded (5xx) through every retry."""


class GeminiQuotaError(GeminiBusyError):
    """Gemini's rate limit or usage quota (HTTP 429) was hit and didn't clear in time."""


@lru_cache
def get_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    # The library's default is NO timeout, so a stalled request would hang the user forever.
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout * 1000)),  # milliseconds
    )


def _retry_after(exc: errors.APIError) -> float | None:
    """Seconds Google says to wait (the RetryInfo part of a 429 response), if it said."""
    try:
        for item in exc.details["error"]["details"]:
            delay = item.get("retryDelay")
            if delay:
                return float(str(delay).rstrip("s"))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def _with_retries(call, attempts: int = MAX_ATTEMPTS):
    """Run `call()`, retrying temporary failures.

    For a rate limit (429) we wait as long as Google asks, unless that is longer than
    MAX_RATE_LIMIT_WAIT, in which case waiting is pointless and we stop at once. Other temporary
    errors get a growing pause.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except TIMEOUT_ERRORS as exc:
            # We already waited a full timeout; retrying the same stuck model would just double that.
            # Report it as "busy" so generate() can move on to a fallback model.
            seconds = get_settings().gemini_timeout
            raise GeminiBusyError(f"Gemini did not answer within {seconds:.0f}s") from exc
        except NETWORK_ERRORS as exc:  # a dropped connection is usually brief: retry like a server error
            if attempt == attempts:
                raise GeminiBusyError(f"Could not reach Gemini after {attempts} attempts") from exc
            delay = 2 * attempt
            log.warning("Network problem talking to Gemini (%s); retry %d/%d in %ds",
                        type(exc).__name__, attempt, attempts - 1, delay)
            _sleep(delay)
        except errors.APIError as exc:
            if exc.code not in RETRYABLE_CODES:
                raise GeminiError(f"Gemini API error {exc.code}: {exc.message}") from exc
            is_rate_limit = exc.code == 429
            asked_to_wait = _retry_after(exc) if is_rate_limit else None
            if asked_to_wait is not None and asked_to_wait > MAX_RATE_LIMIT_WAIT:
                raise GeminiQuotaError(
                    f"Gemini rate limit reached; it asked us to wait {asked_to_wait:.0f}s"
                ) from exc
            if attempt == attempts:
                error_class = GeminiQuotaError if is_rate_limit else GeminiBusyError
                raise error_class(f"Gemini still unavailable after {attempts} attempts ({exc.code})") from exc
            delay = asked_to_wait + 1 if asked_to_wait is not None else 2 * attempt
            log.warning("Gemini returned %s; retry %d/%d in %ds", exc.code, attempt, attempts - 1, delay)
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    """Return one embedding per text.

    task_type is "RETRIEVAL_DOCUMENT" for stored chunks and "RETRIEVAL_QUERY" for questions;
    Gemini tunes the vectors so the two match well. There is no fallback model here: vectors from
    different embedding models can't be compared with each other.
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
    """Ask the chat model and return its text answer.

    Rate limits are counted per model, so if the main model stays limited or overloaded we move on to
    each fallback model (CHAT_FALLBACK_MODELS) before giving up. Models before the last one get only
    one quick retry so the user isn't kept waiting.
    """
    client = get_client()
    settings = get_settings()
    models = [settings.chat_model, *settings.chat_fallback_models]
    for index, model in enumerate(models):
        is_last = index == len(models) - 1
        try:
            response = _with_retries(
                lambda model=model: client.models.generate_content(model=model, contents=prompt),
                attempts=MAX_ATTEMPTS if is_last else 2,
            )
        except GeminiBusyError:  # includes GeminiQuotaError
            if is_last:
                raise
            log.warning("%s is unavailable; trying %s", model, models[index + 1])
            continue
        if not response.text:
            raise GeminiError("Gemini returned an empty response (it may have been blocked).")
        if index:
            log.info("Answered with fallback model %s", model)
        return response.text
    raise AssertionError("unreachable")  # pragma: no cover
