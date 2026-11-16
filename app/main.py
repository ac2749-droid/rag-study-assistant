"""FastAPI app: /ask to ask a question, /documents and /health for visibility."""
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import rag, store
from app.gemini import GeminiBusyError, GeminiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="NJIT Study Assistant",
    description="Ask questions about ingested lecture slides and textbooks.",
    version="0.1.0",
)


class AskRequest(BaseModel):
    question: str = Field(max_length=2000, examples=["What does getopt() return for an unknown option?"])

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks": store.get_collection().count()}


@app.get("/documents")
def documents() -> dict[str, int]:
    """Files currently stored, with their chunk counts."""
    return store.list_sources()


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    try:
        result = rag.answer_question(body.question)
    except rag.NoMaterialError:
        raise HTTPException(409, "No material ingested yet. Run: python -m app.ingest <file.pdf>") from None
    except GeminiBusyError:
        raise HTTPException(503, "The language model is busy right now. Try again in a minute.") from None
    except GeminiError:
        log.exception("Gemini request failed")
        raise HTTPException(502, "The language model request failed. Check the server logs.") from None
    return AskResponse(
        answer=result.answer,
        sources=[SourceOut(source=s.source, page=s.page) for s in result.sources],
    )
