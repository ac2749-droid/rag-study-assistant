"""The web page, PDF upload and document removal."""
import io

from pypdf import PdfWriter

from app import gemini, store
from app.chunking import Chunk
from tests.conftest import make_pdf, wait_for_job


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
    assert '<h1 class="brand-name">Anthony&#x27;s Study Assistant</h1>' in r.text
    assert "{{APP_NAME}}" not in r.text


def test_app_name_is_configurable_and_escaped(client, monkeypatch):
    monkeypatch.setenv("APP_NAME", "<script>alert(1)</script>")
    from app import config

    config.get_settings.cache_clear()
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_upload_returns_a_job_and_indexes_in_the_background(client, fake_gemini):
    r = upload(client, make_pdf("getopt parses options"), "lecture 1.pdf")
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["source"] == "lecture 1.pdf" and job["total"] == 1 and job["empty_pages"] == []
    final = wait_for_job(client, job["id"])
    assert final["status"] == "done" and final["done"] == 1 and final["eta_seconds"] is None
    assert client.get("/documents").json() == [
        {"source": "lecture 1.pdf", "chunks": 1, "pages": 1, "complete": True, "expected_chunks": 1}
    ]
    assert fake_gemini["embedded"][0][0] == "RETRIEVAL_DOCUMENT"


def test_uploaded_file_can_be_asked_about(client, fake_gemini):
    wait_for_job(client, upload(client, make_pdf("getopt parses options")).json()["id"])
    r = client.post("/ask", json={"question": "What does getopt do?"})
    assert r.status_code == 200
    assert r.json()["sources"] == [{"source": "notes.pdf", "page": 1}]


def test_upload_strips_directories_from_filename(client, fake_gemini):
    r = upload(client, make_pdf("getopt"), "../../evil/notes.pdf")
    assert r.status_code == 202
    assert r.json()["source"] == "notes.pdf"
    wait_for_job(client, r.json()["id"])


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


def test_job_fails_with_a_friendly_message_when_gemini_is_busy(client, monkeypatch):
    def busy(texts, on_progress=None, on_batch=None):
        raise gemini.GeminiBusyError("overloaded")

    monkeypatch.setattr(gemini, "embed_documents", busy)
    r = upload(client, make_pdf("getopt"))
    assert r.status_code == 202  # accepted; the failure shows up on the job
    final = wait_for_job(client, r.json()["id"])
    assert final["status"] == "error" and "overloaded" in final["message"]
    assert store.get_collection().count() == 0


def test_job_fails_with_a_friendly_message_when_gemini_is_rate_limited(client, monkeypatch):
    def limited(texts, on_progress=None, on_batch=None):
        raise gemini.GeminiQuotaError("limit")

    monkeypatch.setattr(gemini, "embed_documents", limited)
    final = wait_for_job(client, upload(client, make_pdf("getopt")).json()["id"])
    assert final["status"] == "error" and "usage limit" in final["message"]
    assert store.get_collection().count() == 0  # nothing half-stored


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404


def test_delete_document(client, loaded_store):
    r = client.delete("/documents", params={"source": "deck.pdf"})
    assert r.status_code == 200 and r.json() == {"removed_chunks": 3}
    assert client.get("/documents").json() == []


def test_delete_one_document_leaves_the_others(client, loaded_store):
    store.add_chunks([Chunk("other.pdf", 1, 0, "strtok splits strings")], "other.pdf")
    client.delete("/documents", params={"source": "deck.pdf"})
    assert [d["source"] for d in client.get("/documents").json()] == ["other.pdf"]


def test_delete_all_documents(client, loaded_store):
    store.add_chunks([Chunk("other.pdf", 1, 0, "strtok splits strings")], "other.pdf")
    r = client.delete("/documents/all")
    assert r.status_code == 200 and r.json() == {"removed_chunks": 4}
    assert client.get("/documents").json() == []


def test_delete_all_on_empty_database_is_fine(client, fake_gemini):
    r = client.delete("/documents/all")
    assert r.status_code == 200 and r.json() == {"removed_chunks": 0}


def test_asking_after_removing_everything_is_409(client, loaded_store, fake_gemini):
    client.delete("/documents/all")
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 409


def test_delete_all_handles_more_chunks_than_one_batch(client, fake_gemini):
    chunks = [Chunk("big.pdf", i, 0, f"getopt chunk {i}") for i in range(1, 1201)]
    store.add_chunks(chunks, "big.pdf")
    assert client.delete("/documents/all").json() == {"removed_chunks": 1200}


def test_delete_unknown_document_is_404(client, loaded_store):
    assert client.delete("/documents", params={"source": "nope.pdf"}).status_code == 404
    assert store.get_collection().count() == 3
