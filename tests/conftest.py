"""Shared test setup: an isolated temp database and a fake Gemini, so tests never hit the network."""
import pytest

from app import config, gemini, store
from app.chunking import Chunk

KEYWORDS = ["getopt", "environment", "strtok"]
REWRITTEN_QUESTION = "Explain the getopt return values in more detail"


def fake_vector(text: str) -> list[float]:
    """A tiny stand-in for an embedding: one dimension per topic keyword, plus a constant.

    Texts about the same keyword get identical vectors (distance 0); texts about no known
    keyword are far from every keyword text (distance ~0.9).
    """
    lowered = text.lower()
    return [1.0 if k in lowered else 0.0 for k in KEYWORDS] + [0.1]


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing `text` (so upload tests need no fixtures on disk)."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def _clear_caches() -> None:
    # A test may have replaced one of these with a plain stand-in function (no cache to clear).
    for function in (config.get_settings, store.get_collection, gemini.get_client):
        getattr(function, "cache_clear", lambda: None)()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    for name in (
        "CHUNK_SIZE", "CHUNK_OVERLAP", "PAGE_LIMIT", "TOP_K", "PER_SOURCE_K", "MAX_DISTANCE", "APP_NAME",
        "MAX_UPLOAD_MB", "CHAT_MODEL", "CHAT_FALLBACK_MODELS", "GEMINI_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
def fake_gemini(monkeypatch):
    """Replace the Gemini calls; the returned dict records the prompts sent to the LLM."""
    calls: dict[str, list] = {"prompts": [], "embedded": []}

    def embed(texts, task_type):
        calls["embedded"].append((task_type, list(texts)))
        return [fake_vector(t) for t in texts]

    def generate(prompt):
        calls["prompts"].append(prompt)
        if prompt.startswith("Rewrite the user's latest message"):
            return REWRITTEN_QUESTION  # what the "follow-up rewriter" would produce
        return "FAKE ANSWER"

    monkeypatch.setattr(gemini, "embed_texts", embed)
    monkeypatch.setattr(gemini, "generate", generate)
    return calls


@pytest.fixture
def loaded_store(fake_gemini):
    """A database holding two slides about getopt and one about environment variables."""
    chunks = [
        Chunk("deck.pdf", 3, 0, "getopt parses command line options."),
        Chunk("deck.pdf", 4, 0, "getopt return values: -1, '?' and ':'."),
        Chunk("deck.pdf", 6, 0, "environment variables are passed in envp."),
    ]
    store.add_chunks(chunks, "deck.pdf")
    return chunks
