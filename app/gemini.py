"""The only module that talks to the Gemini API: client, pacing, retries, fallback, error translation."""
import importlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
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
MAX_RATE_LIMIT_WAIT = 30  # seconds. For a user waiting on an answer, longer waits aren't worth it.
WINDOW_SECONDS = 65  # Google's limit is per minute; the extra 5s absorbs network delay and clock skew
PATIENT_ATTEMPTS = 10  # background indexing can afford to wait out rate limits
PATIENT_RATE_LIMIT_WAIT = 120
EMBED_BATCH_SIZE = 100  # the embeddings endpoint accepts at most 100 texts per request
DOC_BATCH_SIZE = 20  # indexing sends small batches: smooth progress, and a refusal wastes little

_sleep = time.sleep  # indirection so tests can skip the waiting
_clock = time.monotonic


class GeminiError(RuntimeError):
    """The request failed for a reason retrying won't fix (bad key, unknown model, ...)."""


class GeminiBusyError(GeminiError):
    """Gemini stayed overloaded (5xx) through every retry."""


class GeminiQuotaError(GeminiBusyError):
    """Gemini's rate limit or usage quota (HTTP 429) was hit and didn't clear in time."""

    def __init__(self, message: str, daily: bool = False) -> None:
        super().__init__(message)
        self.daily = daily  # True when it is a per-DAY quota: waiting a minute won't help


class _RateWindow:
    """Counts the texts embedded in the last WINDOW_SECONDS.

    Google counts EVERY text in a batch as one request (free plan: 100 per minute), so a big document
    has to be fed in slowly. Questions record their usage here too, so indexing leaves them room.
    The window is a little longer than a minute because Google times a request when it ARRIVES, a second
    or two after we record sending it; with an exact minute the two counts disagree and Google refuses.
    """

    def __init__(self) -> None:
        self._sent: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._sent.clear()

    def _trim(self, now: float) -> None:
        while self._sent and now - self._sent[0][0] >= WINDOW_SECONDS:
            self._sent.popleft()

    def record(self, count: int) -> None:
        with self._lock:
            self._sent.append((_clock(), count))

    def reserve(self, count: int, limit: int) -> float:
        """Record `count` texts if they fit under `limit` in the last minute and return 0.
        Otherwise return how many seconds to wait before trying again."""
        with self._lock:
            now = _clock()
            self._trim(now)
            if sum(n for _, n in self._sent) + count <= limit:
                self._sent.append((now, count))
                return 0.0
            return max(0.5, WINDOW_SECONDS - (now - self._sent[0][0]) + 0.5)


_WINDOW = _RateWindow()


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


def _quota_info(exc: errors.APIError) -> str:
    """Name of the quota Google says was exceeded (e.g. 'EmbedContentRequestsPerMinute...'), if given."""
    names: list[str] = []
    try:
        for item in exc.details["error"]["details"]:
            for violation in item.get("violations", []):
                names.append(str(violation.get("quotaId") or violation.get("quotaMetric") or ""))
    except (KeyError, TypeError, AttributeError):
        return ""
    return ", ".join(dict.fromkeys(name for name in names if name))


def _with_retries(call, attempts: int = MAX_ATTEMPTS, max_rate_limit_wait: float = MAX_RATE_LIMIT_WAIT):
    """Run `call()`, retrying temporary failures.

    For a rate limit (429) we wait as long as Google asks, unless that is longer than
    `max_rate_limit_wait` (or the limit is a DAILY one), in which case waiting is pointless and we stop
    at once. Other temporary errors get a growing pause.
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
            log.warning(
                "Network problem talking to Gemini (%s); retry %d/%d in %ds",
                type(exc).__name__, attempt, attempts - 1, delay,
            )
            _sleep(delay)
        except errors.APIError as exc:
            if exc.code not in RETRYABLE_CODES:
                raise GeminiError(f"Gemini API error {exc.code}: {exc.message}") from exc
            is_rate_limit = exc.code == 429
            asked_to_wait = _retry_after(exc) if is_rate_limit else None
            quota = _quota_info(exc) if is_rate_limit else ""
            quota_note = f" [{quota}]" if quota else ""
            if is_rate_limit and "PerDay" in quota:
                raise GeminiQuotaError(f"Gemini daily quota reached{quota_note}", daily=True) from exc
            if asked_to_wait is not None and asked_to_wait > max_rate_limit_wait:
                raise GeminiQuotaError(
                    f"Gemini rate limit reached; it asked us to wait {asked_to_wait:.0f}s{quota_note}"
                ) from exc
            if attempt == attempts:
                error_class = GeminiQuotaError if is_rate_limit else GeminiBusyError
                raise error_class(
                    f"Gemini still unavailable after {attempts} attempts ({exc.code}){quota_note}"
                ) from exc
            delay = asked_to_wait + 1 if asked_to_wait is not None else 2 * attempt
            log.warning(
                "Gemini returned %s%s; retry %d/%d in %ds", exc.code, quota_note, attempt, attempts - 1, delay
            )
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _embed_batch(batch: list[str], task_type: str, *, patient: bool) -> list[list[float]]:
    client = get_client()
    model = get_settings().embed_model
    options = {}
    if patient:  # indexing runs in the background, so it can afford to wait out a rate limit
        options = {"attempts": PATIENT_ATTEMPTS, "max_rate_limit_wait": PATIENT_RATE_LIMIT_WAIT}
    response = _with_retries(
        lambda: client.models.embed_content(
            model=model, contents=batch, config=types.EmbedContentConfig(task_type=task_type)
        ),
        **options,
    )
    vectors = [list(e.values) for e in response.embeddings]
    if len(vectors) != len(batch):
        # Some models (e.g. gemini-embedding-2) merge a list of texts into ONE embedding. Storing that
        # would silently lose most of a document, so refuse loudly instead.
        raise GeminiError(
            f"{model} returned {len(vectors)} embeddings for {len(batch)} texts, so it can't be used here. "
            "Use a model that returns one embedding per text (such as gemini-embedding-001)."
        )
    return vectors


def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    """Return one embedding per text, quickly. Used for questions.

    task_type is "RETRIEVAL_QUERY" for questions; Gemini tunes the vectors so they match the
    "RETRIEVAL_DOCUMENT" ones well. There is no fallback model here: vectors from different embedding
    models can't be compared with each other. Usage is recorded so indexing leaves room for questions.
    """
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        vectors.extend(_embed_batch(batch, task_type, patient=False))
        _WINDOW.record(len(batch))
    return vectors


def embed_documents(
    texts: list[str],
    on_progress: Callable[[int, int], None] | None = None,
    on_batch: Callable[[int, list[list[float]]], None] | None = None,
) -> list[list[float]]:
    """Return one embedding per document section, paced to stay under Gemini's per-minute limit.

    Used when indexing a PDF. A 370-page manual is ~1,000 sections, which at the free plan's 100/minute
    takes over ten minutes, so this reports progress as it goes and waits patiently if Google pushes back.
    """
    per_minute = get_settings().embed_texts_per_minute
    step = min(DOC_BATCH_SIZE, per_minute) if per_minute > 0 else EMBED_BATCH_SIZE
    vectors: list[list[float]] = []
    for i in range(0, len(texts), step):
        batch = texts[i : i + step]
        if per_minute > 0:
            while (wait := _WINDOW.reserve(len(batch), per_minute)) > 0:
                log.info("Pacing: waiting %.0fs to stay under %d texts/minute", wait, per_minute)
                _sleep(wait)
        else:
            _WINDOW.record(len(batch))
        batch_vectors = _embed_batch(batch, "RETRIEVAL_DOCUMENT", patient=True)
        if on_batch:  # lets the caller save each batch the moment it exists, so a later failure loses nothing
            on_batch(i, batch_vectors)
        vectors.extend(batch_vectors)
        if on_progress:
            on_progress(len(vectors), len(texts))
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
