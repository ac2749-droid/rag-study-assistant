"""Background indexing. Big documents take minutes (Gemini limits how fast sections can be embedded), so
uploads are queued and one worker thread indexes them one after another while the page shows progress."""
import logging
import queue
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from app import auth, messages, store
from app.chunking import Chunk
from app.gemini import GeminiBusyError, GeminiError, GeminiQuotaError

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, ERROR = "queued", "running", "done", "error"
KEEP_JOBS = 50  # finished jobs remembered for the page to read


@dataclass
class Job:
    id: str
    source: str
    total: int  # sections to index
    empty_pages: list[int] = field(default_factory=list)
    done: int = 0  # sections indexed so far
    status: str = QUEUED
    message: str = ""  # a friendly error message when status is "error"
    owner_id: int | None = None  # the account that added the file; only they can see this job
    owner_name: str = ""
    collection_name: str | None = None  # the owner's private library (None = the original one)


class IndexQueue:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Job, list[Chunk]]] = queue.Queue()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def submit(
        self,
        chunks: list[Chunk],
        source: str,
        empty_pages: list[int],
        *,
        owner_id: int | None = None,
        owner_name: str = "",
        collection_name: str | None = None,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12], source=source, total=len(chunks), empty_pages=empty_pages,
            owner_id=owner_id, owner_name=owner_name, collection_name=collection_name,
        )
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > KEEP_JOBS:  # forget the oldest finished job, never an active one
                oldest = next((j for j in self._jobs.values() if j.status in (DONE, ERROR)), None)
                if oldest is None:
                    break
                del self._jobs[oldest.id]
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="indexer", daemon=True)
                self._worker.start()
        self._queue.put((job, chunks))
        return job

    def reset(self) -> None:
        """Forget every remembered job (used by tests so one test's jobs can't show up in the next)."""
        with self._lock:
            self._jobs.clear()

    def get(self, job_id: str, owner_id: int | None = None) -> Job | None:
        """A job by id. If `owner_id` is given, only that account's own jobs are visible."""
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job is not None and (owner_id is None or job.owner_id == owner_id) else None

    def all(self, owner_id: int | None = None) -> list[Job]:
        """Remembered jobs, newest first (just one account's when `owner_id` is given)."""
        with self._lock:
            jobs = list(reversed(self._jobs.values()))
        return [j for j in jobs if owner_id is None or j.owner_id == owner_id]

    def _run(self) -> None:
        while True:
            job, chunks = self._queue.get()
            try:
                self._execute(job, chunks)
            finally:
                self._queue.task_done()

    def _execute(self, job: Job, chunks: list[Chunk]) -> None:
        job.status = RUNNING
        try:
            store.add_chunks(
                chunks,
                job.source,
                on_progress=lambda done, total: setattr(job, "done", done),
                collection_name=job.collection_name,
            )
        except GeminiQuotaError as exc:
            log.warning("Indexing %s stopped by a Gemini limit: %s", job.source, exc)
            if exc.daily:
                self._fail(job, messages.daily_quota_message(job.done, job.total))
            else:
                self._fail(job, messages.QUOTA_MESSAGE + messages.saved_progress_note(job.done, job.total))
        except GeminiBusyError as exc:
            log.warning("Indexing %s stopped, Gemini overloaded: %s", job.source, exc)
            self._fail(job, messages.BUSY_MESSAGE + messages.saved_progress_note(job.done, job.total))
        except GeminiError:
            log.exception("Indexing %s failed", job.source)
            self._fail(job, messages.MODEL_FAILED_MESSAGE + messages.saved_progress_note(job.done, job.total))
        except Exception:  # a background thread must never die silently
            log.exception("Unexpected error while indexing %s", job.source)
            self._fail(job, "Something went wrong while indexing this file. Check the server logs.")
        else:
            job.done = job.total
            job.status = DONE
            self._audit(job, "indexing_done", f"{job.source} ({job.total} sections)")

    def _fail(self, job: Job, message: str) -> None:
        job.message = message
        job.status = ERROR
        self._audit(job, "indexing_failed", f"{job.source} at {job.done} of {job.total} sections")

    @staticmethod
    def _audit(job: Job, action: str, detail: str) -> None:
        try:
            auth.record(action, user_id=job.owner_id, username=job.owner_name, detail=detail)
        except Exception:  # the audit log must never break indexing
            log.exception("Could not write the audit log")


index_queue = IndexQueue()
