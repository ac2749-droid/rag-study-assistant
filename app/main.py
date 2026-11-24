"""FastAPI app: a simple web page at /, plus the JSON API behind it."""
import html
import logging
import math
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from pypdf.errors import PyPdfError

from app import jobs, messages, rag, store
from app.config import get_settings
from app.gemini import GeminiBusyError, GeminiError, GeminiQuotaError
from app.ingest import prepare_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).parent / "static" / "index.html"
UPLOAD_READ_SIZE = 1024 * 1024

app = FastAPI(
    title=get_settings().app_name,
    description=(
        "Ask questions about your own documents (lecture slides, textbooks, manuals, handbooks, "
        "policies) and get answers with page references. This is the JSON API behind the web page "
        "at `/`; most people should use that page."
    ),
    version="0.4.0",
)


class TurnIn(BaseModel):
    """One earlier exchange, sent back by the page so follow-up questions make sense."""

    question: str = Field(max_length=2000)
    answer: str = Field(max_length=20000)


class AskRequest(BaseModel):
    question: str = Field(max_length=2000, examples=["What does getopt() return for an unknown option?"])
    history: list[TurnIn] = Field(
        default_factory=list,
        max_length=20,
        description="Earlier exchanges, oldest first. Only the most recent few are used.",
    )

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class SourceOut(BaseModel):
    source: str
    page: int


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceOut]


class DocumentOut(BaseModel):
    source: str
    chunks: int  # searchable sections the file was split into
    pages: int  # pages that contain readable text
    complete: bool  # False if indexing stopped early: only part of the file is searchable
    expected_chunks: int  # how many sections the whole file should have


class JobOut(BaseModel):
    """A document being indexed in the background."""

    id: str
    source: str
    status: str  # queued | running | done | error
    done: int  # sections indexed so far
    total: int  # sections to index
    eta_seconds: int | None  # rough time left, or None when unknown / finished
    message: str  # a friendly explanation when status is "error"
    empty_pages: list[int]  # pages with no readable text, which were skipped


def job_out(job: jobs.Job) -> JobOut:
    per_minute = get_settings().embed_texts_per_minute
    active = job.status in (jobs.QUEUED, jobs.RUNNING)
    eta = math.ceil((job.total - job.done) / per_minute * 60) if active and per_minute > 0 else None
    return JobOut(
        id=job.id, source=job.source, status=job.status, done=job.done, total=job.total,
        eta_seconds=eta, message=job.message, empty_pages=job.empty_pages,
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    # Read on each visit: the file is tiny, and edits to the page show up without a restart.
    page = INDEX_PATH.read_text(encoding="utf-8")
    return page.replace("{{APP_NAME}}", html.escape(get_settings().app_name))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks": store.get_collection().count()}


@app.get("/documents", response_model=list[DocumentOut])
def documents() -> list[DocumentOut]:
    """Files currently stored, sorted by name."""
    return [DocumentOut(**d) for d in store.list_documents()]


@app.post("/documents", response_model=JobOut, status_code=202)
def upload_document(file: UploadFile) -> JobOut:
    """Add a PDF. The file is read right away (so a bad file is rejected at once); indexing it can take
    minutes for a big document, so it continues in the background: follow it at GET /jobs/{id}.
    Uploading a file with the same name replaces the earlier copy when indexing finishes, and adding a
    file whose indexing stopped early continues where it stopped."""
    name = Path(file.filename or "").name[:200]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Please choose a PDF file.")

    limit_mb = get_settings().max_upload_mb
    max_bytes = limit_mb * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.pdf"
        size = 0
        with open(path, "wb") as out:
            while block := file.file.read(UPLOAD_READ_SIZE):
                size += len(block)
                if size > max_bytes:
                    raise HTTPException(413, f"That file is too large (limit {limit_mb} MB).")
                out.write(block)
        with open(path, "rb") as check:
            if check.read(5) != b"%PDF-":
                raise HTTPException(400, "That doesn't look like a PDF file.")
        try:
            prepared = prepare_pdf(path, name)
        except PyPdfError:
            raise HTTPException(400, messages.UNREADABLE_PDF_MESSAGE) from None
        except ValueError:
            raise HTTPException(422, messages.NO_TEXT_MESSAGE) from None
    return job_out(jobs.index_queue.submit(prepared.chunks, prepared.name, prepared.empty_pages))


@app.get("/jobs", response_model=list[JobOut])
def list_jobs() -> list[JobOut]:
    """Recent indexing jobs, newest first."""
    return [job_out(job) for job in jobs.index_queue.all()]


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    job = jobs.index_queue.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job_out(job)


@app.delete("/documents/all")
def delete_all_documents() -> dict[str, int]:
    """Remove every stored file."""
    return {"removed_chunks": store.delete_all()}


@app.delete("/documents")
def delete_document(source: str) -> dict[str, int]:
    """Remove a stored file (pass its name as ?source=...)."""
    removed = store.delete_source(source)
    if removed == 0:
        raise HTTPException(404, "No such document.")
    return {"removed_chunks": removed}


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    try:
        history = [rag.Turn(t.question, t.answer) for t in body.history]
        result = rag.answer_question(body.question, history)
    except rag.NoMaterialError:
        raise HTTPException(409, "No material yet. Add a PDF first.") from None
    except GeminiQuotaError as exc:
        log.warning("Question stopped by a Gemini limit: %s", exc)
        raise HTTPException(429, messages.QUOTA_MESSAGE) from None
    except GeminiBusyError:
        raise HTTPException(503, messages.BUSY_MESSAGE) from None
    except GeminiError:
        log.exception("Gemini request failed")
        raise HTTPException(502, messages.MODEL_FAILED_MESSAGE) from None
    return AskResponse(
        answer=result.answer,
        sources=[SourceOut(source=s.source, page=s.page) for s in result.sources],
    )
