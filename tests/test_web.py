"""The web page, PDF upload and document removal."""
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import gemini, store
from app.main import app
from tests.conftest import make_pdf


@pytest.fixture
def client():
    return TestClient(app)


def upload(client, content: bytes, name: str = "notes.pdf"):
    return client.post("/documents", files={"file": (name, content, "application/pdf")})


def blank_pdf() -> bytes:
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    return buffer.getvalue()


def test_home_page_shows_default_name(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # The apostrophe is HTML-escaped (&#x27;); browsers display it as a normal '.
    assert "<title>Anthony&#x27;s Study Assistant</title>" in r.text
    assert "<h1>Anthony&#x27;s Study Assistant</h1>" in r.text
    assert "{{APP_NAME}}" not in r.text


def test_app_name_is_configurable_and_escaped(client, monkeypatch):
    monkeypatch.setenv("APP_NAME", "<script>alert(1)</script>")
    from app import config

    config.get_settings.cache_clear()
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_upload_valid_pdf_stores_it(client, fake_gemini):
    r = upload(client, make_pdf("getopt parses options"), "lecture 1.pdf")
    assert r.status_code == 200, r.text
    assert r.json() == {"source": "lecture 1.pdf", "chunks": 1, "empty_pages": []}
    assert client.get("/documents").json() == {"lecture 1.pdf": 1}
    assert fake_gemini["embedded"][0][0] == "RETRIEVAL_DOCUMENT"


def test_uploaded_file_can_be_asked_about(client, fake_gemini):
    upload(client, make_pdf("getopt parses options"))
    r = client.post("/ask", json={"question": "What does getopt do?"})
    assert r.status_code == 200
    assert r.json()["sources"] == [{"source": "notes.pdf", "page": 1}]


def test_upload_strips_directories_from_filename(client, fake_gemini):
    r = upload(client, make_pdf("getopt"), "../../evil/notes.pdf")
    assert r.status_code == 200
    assert r.json()["source"] == "notes.pdf"


def test_upload_rejects_non_pdf_extension(client, fake_gemini):
    assert upload(client, b"hello", "notes.txt").status_code == 400


def test_upload_rejects_fake_pdf(client, fake_gemini):
    r = upload(client, b"this is not a pdf at all", "fake.pdf")
    assert r.status_code == 400
    assert "doesn't look like a PDF" in r.json()["detail"]


def test_upload_rejects_oversized_file(client, fake_gemini, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from app import config

    config.get_settings.cache_clear()
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)
    assert upload(client, big).status_code == 413


def test_upload_pdf_without_text_is_422(client, fake_gemini):
    r = upload(client, blank_pdf(), "scan.pdf")
    assert r.status_code == 422
    assert "Scanned" in r.json()["detail"]
    assert store.get_collection().count() == 0


def test_upload_corrupt_pdf_is_400(client, fake_gemini):
    r = upload(client, b"%PDF-1.4\nthis is garbage", "broken.pdf")
    assert r.status_code == 400


def test_upload_when_gemini_busy_is_503(client, monkeypatch):
    def busy(texts, task_type):
        raise gemini.GeminiBusyError("overloaded")

    monkeypatch.setattr(gemini, "embed_texts", busy)
    assert upload(client, make_pdf("getopt")).status_code == 503


def test_delete_document(client, loaded_store):
    r = client.delete("/documents", params={"source": "deck.pdf"})
    assert r.status_code == 200 and r.json() == {"removed_chunks": 3}
    assert client.get("/documents").json() == {}


def test_delete_unknown_document_is_404(client, loaded_store):
    assert client.delete("/documents", params={"source": "nope.pdf"}).status_code == 404
    assert store.get_collection().count() == 3
