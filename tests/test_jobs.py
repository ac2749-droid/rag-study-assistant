"""The background indexing queue and the /jobs endpoints."""
import threading
import time

import pytest

from app import auth, gemini, jobs, store
from app.chunking import Chunk
from tests.conftest import make_pdf, wait_for_job


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def chunks_for(source: str, n: int = 3) -> list[Chunk]:
    return [Chunk(source, page, 0, f"getopt text {page}") for page in range(1, n + 1)]


@pytest.fixture
def queue_under_test():
    return jobs.IndexQueue()  # a fresh queue and worker per test


def test_a_job_moves_from_queued_to_running_to_done(queue_under_test, monkeypatch):
    release = threading.Event()

    def slow_add(chunks, source, on_progress=None, **kwargs):
        on_progress(1, len(chunks))
        release.wait(5)
        on_progress(len(chunks), len(chunks))
        return len(chunks)

    monkeypatch.setattr(store, "add_chunks", slow_add)
    job = queue_under_test.submit(chunks_for("a.pdf"), "a.pdf", [])
    wait_until(lambda: job.status == jobs.RUNNING and job.done == 1)  # progress is visible mid-way
    release.set()
    wait_until(lambda: job.status == jobs.DONE)
    assert job.done == job.total == 3


def test_files_are_indexed_one_at_a_time_in_order(queue_under_test, monkeypatch):
    order, release = [], threading.Event()

    def add(chunks, source, on_progress=None, **kwargs):
        order.append(("start", source))
        release.wait(5)
        order.append(("end", source))
        return len(chunks)

    monkeypatch.setattr(store, "add_chunks", add)
    first = queue_under_test.submit(chunks_for("first.pdf"), "first.pdf", [])
    second = queue_under_test.submit(chunks_for("second.pdf"), "second.pdf", [])
    wait_until(lambda: first.status == jobs.RUNNING)
    assert second.status == jobs.QUEUED  # waits its turn: they share Gemini's per-minute limit
    release.set()
    wait_until(lambda: second.status == jobs.DONE)
    assert order == [
        ("start", "first.pdf"), ("end", "first.pdf"), ("start", "second.pdf"), ("end", "second.pdf"),
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (gemini.GeminiQuotaError("limit"), "usage limit"),
        (gemini.GeminiBusyError("busy"), "overloaded"),
        (gemini.GeminiError("other"), "request failed"),
        (RuntimeError("bug"), "Something went wrong"),
    ],
)
def test_failures_become_friendly_job_errors(queue_under_test, monkeypatch, error, expected):
    def explode(chunks, source, on_progress=None, **kwargs):
        raise error

    monkeypatch.setattr(store, "add_chunks", explode)
    job = queue_under_test.submit(chunks_for("a.pdf"), "a.pdf", [])
    wait_until(lambda: job.status == jobs.ERROR)
    assert expected in job.message
    assert "bug" not in job.message and "limit'" not in job.message  # no internals leaked


def test_a_failed_job_does_not_stop_the_next_one(queue_under_test, monkeypatch):
    def add(chunks, source, on_progress=None, **kwargs):
        if source == "bad.pdf":
            raise gemini.GeminiBusyError("busy")
        return len(chunks)

    monkeypatch.setattr(store, "add_chunks", add)
    bad = queue_under_test.submit(chunks_for("bad.pdf"), "bad.pdf", [])
    good = queue_under_test.submit(chunks_for("good.pdf"), "good.pdf", [])
    wait_until(lambda: good.status == jobs.DONE)
    assert bad.status == jobs.ERROR


def test_only_recent_finished_jobs_are_remembered(queue_under_test, monkeypatch):
    monkeypatch.setattr(store, "add_chunks", lambda chunks, source, on_progress=None, **kwargs: len(chunks))
    monkeypatch.setattr(jobs, "KEEP_JOBS", 3)
    made = [queue_under_test.submit(chunks_for(f"{i}.pdf", 1), f"{i}.pdf", []) for i in range(6)]
    wait_until(lambda: all(j.status == jobs.DONE for j in made))
    queue_under_test.submit(chunks_for("last.pdf", 1), "last.pdf", [])  # trims on submit
    assert len(queue_under_test.all()) <= 4
    assert queue_under_test.get(made[0].id) is None  # the oldest was forgotten


# ----- through the HTTP API -----


def upload(client, content, name="notes.pdf"):
    return client.post("/documents", files={"file": (name, content, "application/pdf")})


def test_jobs_endpoint_lists_newest_first(client, fake_gemini):
    first = upload(client, make_pdf("getopt one"), "one.pdf").json()["id"]
    wait_for_job(client, first)
    second = upload(client, make_pdf("getopt two"), "two.pdf").json()["id"]
    wait_for_job(client, second)
    ids = [j["id"] for j in client.get("/jobs").json()]
    assert ids.index(second) < ids.index(first)


def test_job_reports_progress_and_an_eta_while_active(client, fake_gemini, monkeypatch):
    release = threading.Event()

    def slow_add(chunks, source, on_progress=None, **kwargs):
        on_progress(1, len(chunks))
        release.wait(5)
        on_progress(len(chunks), len(chunks))
        return len(chunks)

    monkeypatch.setattr(store, "add_chunks", slow_add)
    job_id = upload(client, make_pdf("getopt slow"), "slow.pdf").json()["id"]
    wait_until(lambda: client.get(f"/jobs/{job_id}").json()["done"] == 1)
    mid = client.get(f"/jobs/{job_id}").json()
    assert mid["status"] == "running" and mid["total"] == 1
    release.set()
    final = wait_for_job(client, job_id)
    assert final["status"] == "done" and final["eta_seconds"] is None


def test_eta_follows_the_pace_setting(client, monkeypatch):
    from app import config

    release = threading.Event()

    def hold(chunks, source, on_progress=None, **kwargs):
        release.wait(5)
        return len(chunks)

    monkeypatch.setattr(store, "add_chunks", hold)
    monkeypatch.setenv("EMBED_TEXTS_PER_MINUTE", "60")
    config.get_settings.cache_clear()
    owner = auth.get_user_by_name("admin")  # jobs are private to the account that added them
    job = jobs.index_queue.submit(chunks_for("big.pdf", 120), "big.pdf", [], owner_id=owner.id)
    wait_until(lambda: job.status == jobs.RUNNING)
    assert client.get(f"/jobs/{job.id}").json()["eta_seconds"] == 120  # 120 sections at 60/min = 2 minutes
    monkeypatch.setenv("EMBED_TEXTS_PER_MINUTE", "0")
    config.get_settings.cache_clear()
    assert client.get(f"/jobs/{job.id}").json()["eta_seconds"] is None  # no pacing -> no estimate
    release.set()
    wait_until(lambda: job.status == jobs.DONE)


def test_skipped_pages_are_reported_on_the_job(client, fake_gemini, monkeypatch):
    from app import ingest
    from app.pdf_loader import Page

    # Page 2 has no readable text (say, a diagram or a scan), page 1 does.
    monkeypatch.setattr(ingest, "load_pdf", lambda path: [Page(1, "getopt text"), Page(2, "")])
    job = upload(client, make_pdf("x"), "partly-scanned.pdf").json()
    assert job["empty_pages"] == [2] and job["total"] == 1
    wait_for_job(client, job["id"])


def test_reading_a_bad_file_still_fails_immediately_not_as_a_job(client, fake_gemini):
    before = len(client.get("/jobs").json())
    assert upload(client, b"%PDF-1.4\ngarbage", "broken.pdf").status_code == 400
    assert len(client.get("/jobs").json()) == before  # nothing was queued
