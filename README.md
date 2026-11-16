# NJIT Study Assistant

A small retrieval-augmented generation (RAG) app. Feed it lecture slides or textbook PDFs, then ask
questions in plain English and get answers grounded in that material, with the page numbers they came from.

Built with **Python 3.12**, **FastAPI**, **ChromaDB** and the **Gemini API** (embeddings and chat).

## How it works

```
 INGEST (once per PDF)                         ASK (every question)
 ---------------------                         --------------------
 PDF --> text per page                         question
     --> chunks (keep page number)                 --> embed with Gemini
     --> embed each chunk with Gemini              --> ChromaDB: find the closest chunks
     --> store text + vector in ChromaDB           --> drop chunks that aren't close enough
                                                   --> Gemini writes an answer from ONLY those chunks
                                                   --> return answer + source pages
```

Design choices worth knowing:

- **Answers come only from your material.** The prompt tells the model to use the excerpts and to say so
  when the answer isn't there. The excerpts are also marked as data, not instructions, so text inside a
  slide can't redirect the model.
- **Relevance cutoff.** Chunks farther than `MAX_DISTANCE` from the question are discarded. If nothing is
  close enough, the app replies "I can't find that in the uploaded material" without calling the LLM.
- **One slide, one chunk.** A short slide is usually one idea (or one code sample), so pages up to
  `PAGE_LIMIT` characters stay whole. Longer pages are split with overlap, at line breaks where possible.
- **Safe re-ingesting.** Embeddings are computed before anything is deleted, so a Gemini failure never
  wipes existing data. Re-ingesting a file replaces its old chunks.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Get a free API key from [Google AI Studio](https://aistudio.google.com), then:

```bash
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

and put your key in `.env` (`GEMINI_API_KEY=...`). Make sure the file is really named `.env`, not
`.env.txt` (Windows hides extensions by default).

## Usage

Add one or more PDFs:

```bash
python -m app.ingest "path/to/lecture1.pdf" "path/to/lecture2.pdf"
```

Start the server:

```bash
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/docs> for an interactive page, or call it directly:

```bash
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
     -d '{"question": "What does getopt() return for an unknown option?"}'
```

### API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ask` | Body `{"question": "..."}`. Returns `{"answer": "...", "sources": [{"source": "file.pdf", "page": 4}]}` |
| `GET` | `/documents` | Files currently stored, with chunk counts |
| `GET` | `/health` | Liveness check and total chunk count |

Errors: `409` nothing ingested yet, `422` invalid question (blank or over 2000 chars), `503` Gemini is
busy (already retried), `502` any other Gemini failure.

## Configuration

Set in `.env` or the environment. All optional except the key.

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none (required) | Your Gemini API key |
| `EMBED_MODEL` | `gemini-embedding-001` | Embedding model |
| `CHAT_MODEL` | `gemini-3.5-flash` | Model that writes answers |
| `CHROMA_PATH` | `chroma_db` | Where ChromaDB stores data |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `100` | Size and overlap when a long page is split |
| `PAGE_LIMIT` | `1200` | Pages up to this length stay one chunk |
| `TOP_K` | `4` | Chunks retrieved per question |
| `MAX_DISTANCE` | `0.38` | Relevance cutoff (cosine distance, lower = closer) |

**Tuning `MAX_DISTANCE`.** The default was measured on one 11-slide C deck: on-topic questions had their
best chunk at 0.20 to 0.25 and off-topic ones at 0.43 or more. A larger or more uniform corpus may need
a different value. Ask a few questions you know are covered and a few that aren't, compare, and adjust.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # no network or API key needed; Gemini is faked
ruff check .
```

Layout:

```
app/
  config.py       settings from environment variables
  pdf_loader.py   PDF -> text per page
  chunking.py     text -> chunks
  gemini.py       the only module that calls Gemini (client, retries, error handling)
  store.py        ChromaDB: add, search, list
  rag.py          the question-answering pipeline
  main.py         FastAPI app
  ingest.py       command line tool: python -m app.ingest
tests/            pytest suite
```

## Known limitations

- Text only: diagrams and images in slides are not read (a diagram's labels may extract as junk).
  Scanned or image-only PDFs have no text and are rejected at ingest.
- Single-turn: each question is answered independently; there is no conversation memory.
- There is no upload endpoint or authentication. It is meant to run locally for one user, not to be
  exposed to the internet as is.
- Retrieval is purely by meaning. It can miss questions that depend on exact terms or on combining
  many slides. Hybrid search and reranking are natural next steps.
