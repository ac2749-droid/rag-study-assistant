import pytest
from fastapi.testclient import TestClient

from app import gemini, store
from app.chunking import Chunk
from app.main import app
from app.rag import NOT_FOUND


@pytest.fixture
def client():
    return TestClient(app)


def test_health_reports_chunk_count(client, loaded_store):
    assert client.get("/health").json() == {"status": "ok", "chunks": 3}


def test_documents_lists_sources(client, loaded_store):
    assert client.get("/documents").json() == {"deck.pdf": 3}


def test_ask_returns_answer_with_deduplicated_sources(client, loaded_store, fake_gemini):
    r = client.post("/ask", json={"question": "What does getopt return?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "FAKE ANSWER"
    assert body["sources"] == [
        {"source": "deck.pdf", "page": 3},
        {"source": "deck.pdf", "page": 4},
    ]  # the environment-variables slide (page 6) is not relevant
    prompt = fake_gemini["prompts"][0]
    assert "getopt return values" in prompt and "What does getopt return?" in prompt


def test_every_document_is_searched_not_just_the_closest_one(client, fake_gemini, monkeypatch):
    """A broad question must hear from all documents, even if one has many closer passages."""
    from app import config

    monkeypatch.setenv("PER_SOURCE_K", "1")
    config.get_settings.cache_clear()
    store.add_chunks(
        [Chunk("big.pdf", p, 0, f"getopt details part {p}") for p in (1, 2, 3, 4)], "big.pdf"
    )
    store.add_chunks([Chunk("small.pdf", 1, 0, "getopt in one line")], "small.pdf")

    body = client.post("/ask", json={"question": "getopt overview"}).json()
    assert {s["source"] for s in body["sources"]} == {"big.pdf", "small.pdf"}
    # one passage per document, not four from big.pdf
    assert len(body["sources"]) == 2


def test_prompt_labels_each_excerpt_with_file_and_page(client, loaded_store, fake_gemini):
    client.post("/ask", json={"question": "getopt?"})
    assert "[deck.pdf, page 3]" in fake_gemini["prompts"][0]


def test_question_is_embedded_as_a_query(client, loaded_store, fake_gemini):
    client.post("/ask", json={"question": "getopt?"})
    assert fake_gemini["embedded"][-1][0] == "RETRIEVAL_QUERY"


def test_off_topic_question_skips_the_llm(client, loaded_store, fake_gemini):
    r = client.post("/ask", json={"question": "What is a B-tree?"})
    assert r.status_code == 200
    assert r.json() == {"answer": NOT_FOUND, "sources": []}
    assert fake_gemini["prompts"] == []  # no LLM call was made


def test_ask_with_empty_database_is_409(client, fake_gemini):
    assert client.post("/ask", json={"question": "anything"}).status_code == 409


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_bad_questions_are_rejected(client, loaded_store, question):
    assert client.post("/ask", json={"question": question}).status_code == 422


def test_missing_question_field_is_422(client, loaded_store):
    assert client.post("/ask", json={}).status_code == 422


def test_busy_gemini_becomes_503(client, loaded_store, monkeypatch):
    def busy(prompt):
        raise gemini.GeminiBusyError("overloaded")

    monkeypatch.setattr(gemini, "generate", busy)
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 503


def test_gemini_failure_becomes_502_without_leaking_details(client, loaded_store, monkeypatch):
    def broken(prompt):
        raise gemini.GeminiError("Gemini API error 404: secret internal detail")

    monkeypatch.setattr(gemini, "generate", broken)
    r = client.post("/ask", json={"question": "getopt?"})
    assert r.status_code == 502
    assert "secret" not in r.text


def test_reingesting_a_file_replaces_its_old_chunks(loaded_store):
    store.add_chunks([Chunk("deck.pdf", 1, 0, "strtok splits strings.")], "deck.pdf")
    assert store.get_collection().count() == 1
    assert store.list_sources() == {"deck.pdf": 1}


def test_ingest_failure_keeps_existing_data(loaded_store, monkeypatch):
    def fail(texts, task_type):
        raise gemini.GeminiError("boom")

    monkeypatch.setattr(gemini, "embed_texts", fail)
    with pytest.raises(gemini.GeminiError):
        store.add_chunks([Chunk("deck.pdf", 1, 0, "new text")], "deck.pdf")
    assert store.get_collection().count() == 3  # untouched


def test_adding_empty_chunk_list_is_an_error(fake_gemini):
    with pytest.raises(ValueError, match="No text"):
        store.add_chunks([], "scan.pdf")
