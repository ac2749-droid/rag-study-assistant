"""FastAPI app: a simple web page at /, plus the JSON API behind it."""
import html
import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from pypdf.errors import PyPdfError

from app import rag, store
from app.config import get_settings
from app.gemini import GeminiBusyError, GeminiError, GeminiQuotaError
from app.ingest import ingest_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).parent / "static" / "index.html"
UPLOAD_READ_SIZE = 1024 * 1024
BUSY_MESSAGE = "The AI service is overloaded right now. Please try again in a minute."
QUOTA_MESSAGE = (
    "The AI service's usage limit was reached (too many requests too quickly, or today's free quota "
    "is used up). Wait a minute and try again. If it keeps happening, check your Gemini plan limits."
)

app = FastAPI(
    title=get_settings().app_name,
    description=(
        "Ask questions about your own documents (lecture slides, textbooks, manuals, handbooks, "
        "policies) and get answers with page references. This is the JSON API behind the web page "
        "at `/`; most people should use that page."
    ),
    version="0.3.0",
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


class UploadResponse(BaseModel):
    source: str
    chunks: int
    empty_pages: list[int]


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


@app.post("/documents", response_model=UploadResponse)
def upload_document(file: UploadFile) -> UploadResponse:
    """Add a PDF. Uploading a file with the same name replaces the earlier copy."""
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
            result = ingest_pdf(path, name)
        except PyPdfError:
            raise HTTPException(
                400, "That PDF couldn't be read. It may be damaged or password-protected."
            ) from None
        except ValueError:
            raise HTTPException(
                422, "No text could be read from that PDF. Scanned pages (pictures of text) aren't supported."
            ) from None
        except GeminiQuotaError:
            raise HTTPException(429, QUOTA_MESSAGE) from None
        except GeminiBusyError:
            raise HTTPException(503, BUSY_MESSAGE) from None
        except GeminiError:
            log.exception("Gemini request failed during upload")
            raise HTTPException(502, "The language model request failed. Check the server logs.") from None
    return UploadResponse(source=name, chunks=result.chunks, empty_pages=result.empty_pages)


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
    except GeminiQuotaError:
        raise HTTPException(429, QUOTA_MESSAGE) from None
    except GeminiBusyError:
        raise HTTPException(503, BUSY_MESSAGE) from None
    except GeminiError:
        log.exception("Gemini request failed")
        raise HTTPException(502, "The language model request failed. Check the server logs.") from None
    return AskResponse(
        answer=result.answer,
        sources=[SourceOut(source=s.source, page=s.page) for s in result.sources],
    )
