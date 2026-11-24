"""Indexing must never waste work: progress is saved as it goes, and adding the same file again resumes."""
import time

import pytest

from app import config, gemini, jobs, messages, store
from app.chunking import Chunk, chunk_pages
from app.pdf_loader import Page
from tests.conftest import fake_vector


def sections(source: str, n: int, text: str = "getopt section") -> list[Chunk]:
    return [Chunk(source, page, 0, f"{text} {page}") for page in range(1, n + 1)]


class Embedder:
    """A fake embed_documents that works in batches of 2 and can stop (like a daily limit) part-way."""

    def __init__(self, stop_after_batches: int | None = None, daily: bool = True):
        self.stop_after_batches = stop_after_batches
        self.daily = daily
        self.texts_embedded: list[str] = []

    def __call__(self, texts, on_progress=None, on_batch=None):
        out = []
        for i in range(0, len(texts), 2):
            if self.stop_after_batches is not None and i // 2 >= self.stop_after_batches:
                raise gemini.GeminiQuotaError("limit reached", daily=self.daily)
            batch = texts[i : i + 2]
            self.texts_embedded.extend(batch)
            vectors = [fake_vector(t) for t in batch]
            if on_batch:
                on_batch(i, vectors)
            out.extend(vectors)
            if on_progress:
                on_progress(len(out), len(texts))
        return out


@pytest.fixture
def embedder(monkeypatch):
    def install(**kwargs) -> Embedder:
        fake = Embedder(**kwargs)
        monkeypatch.setattr(gemini, "embed_documents", fake)
        return fake

    return install


def document(name: str = "manual.pdf") -> dict:
    return next(d for d in store.list_documents() if d["source"] == name)


def test_a_failure_part_way_keeps_what_was_already_saved(embedder):
    embedder(stop_after_batches=2)  # 4 of 6 sections get embedded, then the limit hits
    with pytest.raises(gemini.GeminiQuotaError):
        store.add_chunks(sections("manual.pdf", 6), "manual.pdf")
    assert store.get_collection().count() == 4  # nothing wasted
    assert document() == {
        "source": "manual.pdf", "chunks": 4, "pages": 4, "complete": False, "expected_chunks": 6,
    }


def test_adding_the_same_file_again_continues_where_it_stopped(embedder):
    embedder(stop_after_batches=2)
    with pytest.raises(gemini.GeminiQuotaError):
        store.add_chunks(sections("manual.pdf", 6), "manual.pdf")
    resumed = embedder()  # the limit has reset
    store.add_chunks(sections("manual.pdf", 6), "manual.pdf")
    assert resumed.texts_embedded == ["getopt section 5", "getopt section 6"]  # only the missing ones
    assert document()["complete"] is True and document()["chunks"] == 6


def test_adding_an_unchanged_complete_file_costs_nothing(embedder):
    embedder()
    store.add_chunks(sections("manual.pdf", 4), "manual.pdf")
    again = embedder()
    store.add_chunks(sections("manual.pdf", 4), "manual.pdf")
    assert again.texts_embedded == []  # no Gemini requests at all
    assert store.get_collection().count() == 4


def test_a_changed_section_is_embedded_again_and_the_rest_reused(embedder):
    embedder()
    store.add_chunks(sections("manual.pdf", 3), "manual.pdf")
    edited = sections("manual.pdf", 3)
    edited[1] = Chunk("manual.pdf", 2, 0, "this page was rewritten")
    again = embedder()
    store.add_chunks(edited, "manual.pdf")
    assert again.texts_embedded == ["this page was rewritten"]


def test_sections_from_an_older_version_of_the_file_are_removed(embedder):
    embedder()
    store.add_chunks(sections("manual.pdf", 5), "manual.pdf")
    store.add_chunks(sections("manual.pdf", 2), "manual.pdf")  # the new version is shorter
    assert store.get_collection().count() == 2
    assert document()["complete"] is True and document()["expected_chunks"] == 2


def test_a_file_that_grows_is_incomplete_until_the_new_sections_are_in(embedder):
    embedder()
    store.add_chunks(sections("manual.pdf", 2), "manual.pdf")
    embedder(stop_after_batches=0)  # nothing more can be embedded
    with pytest.raises(gemini.GeminiQuotaError):
        store.add_chunks(sections("manual.pdf", 6), "manual.pdf")  # a longer version of the same file
    assert document()["complete"] is False and document()["expected_chunks"] == 6


def test_progress_counts_reused_sections_as_already_done(embedder):
    embedder()
    store.add_chunks(sections("manual.pdf", 4), "manual.pdf")
    embedder()
    seen = []
    store.add_chunks(
        sections("manual.pdf", 6), "manual.pdf", on_progress=lambda done, total: seen.append((done, total))
    )
    assert seen[0] == (4, 6)  # starts from what is already stored
    assert seen[-1] == (6, 6)


def test_files_added_before_completeness_tracking_count_as_complete(fake_gemini):
    # Older databases have no "total" on their sections.
    store.get_collection().upsert(
        ids=["old.pdf:p1-c0"], documents=["getopt"], embeddings=[fake_vector("getopt")],
        metadatas=[{"source": "old.pdf", "page": 1}],
    )
    assert document("old.pdf")["complete"] is True


# ----- what the user is told -----


@pytest.fixture
def queue():
    return jobs.IndexQueue()


def run_job(queue, monkeypatch, error, done_before_error: int, total: int = 10):
    def add(chunks, source, on_progress=None):
        on_progress(done_before_error, total)
        raise error

    monkeypatch.setattr(store, "add_chunks", add)
    job = queue.submit(sections("m.pdf", total), "m.pdf", [])
    deadline = time.time() + 5
    while job.status not in (jobs.DONE, jobs.ERROR) and time.time() < deadline:
        time.sleep(0.01)
    return job


def test_a_daily_limit_message_says_when_it_resets_and_that_progress_is_saved(queue, monkeypatch):
    daily_limit = gemini.GeminiQuotaError("daily", daily=True)
    job = run_job(queue, monkeypatch, daily_limit, done_before_error=540, total=1015)
    assert job.status == jobs.ERROR
    assert "resets daily" in job.message and "midnight Pacific" in job.message
    assert "540 of 1,015" in job.message and "add the same file again" in job.message


def test_a_per_minute_limit_does_not_claim_it_resets_tomorrow(queue, monkeypatch):
    job = run_job(queue, monkeypatch, gemini.GeminiQuotaError("minute", daily=False), done_before_error=3)
    assert "resets daily" not in job.message and "3 of 10" in job.message


def test_other_failures_also_mention_saved_progress(queue, monkeypatch):
    job = run_job(queue, monkeypatch, gemini.GeminiBusyError("busy"), done_before_error=7)
    assert "overloaded" in job.message and "7 of 10" in job.message


def test_no_saved_progress_note_when_nothing_was_saved(queue, monkeypatch):
    job = run_job(queue, monkeypatch, gemini.GeminiBusyError("busy"), done_before_error=0)
    assert "progress is saved" not in job.message


@pytest.mark.parametrize(("done", "total", "mentioned"), [(0, 10, False), (10, 10, False), (5, 10, True)])
def test_saved_progress_note_only_when_the_file_is_partly_done(done, total, mentioned):
    assert bool(messages.saved_progress_note(done, total)) is mentioned


def test_the_api_reports_an_incomplete_document(embedder):
    from fastapi.testclient import TestClient

    from app.main import app

    embedder(stop_after_batches=1)
    with pytest.raises(gemini.GeminiQuotaError):
        store.add_chunks(sections("manual.pdf", 6), "manual.pdf")
    [doc] = TestClient(app).get("/documents").json()
    assert doc["complete"] is False and doc["chunks"] == 2 and doc["expected_chunks"] == 6


# ----- larger default sections -----


def test_default_sections_keep_a_typical_manual_page_whole():
    settings = config.get_settings()
    one_page = Page(1, "word " * 340)  # about 1,700 characters, like a page of a phone manual
    long_page = Page(2, "word " * 780)  # about 3,900 characters
    chunks = chunk_pages(
        [one_page, long_page], "m.pdf", size=settings.chunk_size, overlap=settings.chunk_overlap,
        page_limit=settings.page_limit,
    )
    per_page = {p: sum(1 for c in chunks if c.page == p) for p in (1, 2)}
    assert per_page[1] == 1  # not split, so fewer embeddings to pay for
    assert per_page[2] <= 3  # a very long page becomes a few large sections, not six small ones
