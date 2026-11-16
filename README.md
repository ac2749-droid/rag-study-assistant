# Anthony's Study Assistant

Ask questions about your own documents in plain English and get answers with the pages they came from.
It runs on your own computer, for two kinds of use:

| | Studying | Work |
|---|---|---|
| **Add** | Lecture slides, textbooks, notes | Product manuals, employee handbooks, policies, procedures |
| **Ask** | "Summarize the key ideas." "What does `**ptr` mean?" | "How do I clear a paper jam?" "How much notice do I need for vacation?" |
| **Get** | An answer grounded in your slides, with page numbers | An answer from the right manual or policy, with page numbers |

You can load several documents at once, and one question can draw on all of them (each point in the answer
names its source file). Follow-up questions work. The name shown on the page is the `APP_NAME` setting, so
it can be renamed for your team or organisation.

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
- **Follow-up questions.** The page sends the last few exchanges with each question. A vague follow-up
  ("explain that more") is first rewritten into a standalone question by the model, and that rewrite is
  what gets searched; if the rewrite fails, the original text is used. The first question of a chat skips
  this step, so it costs nothing extra.
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

Start the server:

```bash
uvicorn app.main:app
```

Open <http://127.0.0.1:8000>. The page has your documents on the left and the conversation on the right:

1. **Add documents.** Click "Add documents" (or drag PDFs onto the page). Each file is listed with its page
   count. Use the trash icon to remove one file, or "Remove all documents" to clear the library. Removing
   asks for confirmation inside the page.
2. **Ask a question.** Type it and press Enter, or click an example. The answer appears with the pages it
   came from, and you can ask follow-ups. "New chat" starts over.

You can also add PDFs from the command line (useful for many files at once):

```bash
python -m app.ingest "path/to/lecture1.pdf" "path/to/lecture2.pdf"
```

### API (for developers)

Interactive documentation is at <http://127.0.0.1:8000/docs>.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ask` | Body `{"question": "...", "history": [{"question": "...", "answer": "..."}]}` (`history` is optional, up to 20 earlier exchanges, oldest first). Returns `{"answer": "...", "sources": [{"source": "file.pdf", "page": 4}]}` |
| `GET` | `/documents` | Files currently stored, sorted by name: `[{"source": "file.pdf", "chunks": 20, "pages": 20}]` (`pages` counts pages that contain readable text) |
| `POST` | `/documents` | Upload a PDF (multipart field `file`). Same name replaces the old copy |
| `DELETE` | `/documents?source=file.pdf` | Remove one file |
| `DELETE` | `/documents/all` | Remove every file |
| `GET` | `/health` | Liveness check and total chunk count |

Errors: `409` nothing added yet, `422` invalid question or a PDF with no readable text, `400` not a
(readable) PDF, `413` file over `MAX_UPLOAD_MB`, `503` Gemini is busy (already retried), `502` any other
Gemini failure.

### Security

There is no login. The server listens on `127.0.0.1` (this computer only) by default, which is the
intended use. Anyone who can reach the server can read your material, upload files and spend your Gemini
quota, so don't start it with `--host 0.0.0.0` or put it on the internet without adding authentication.
Uploads are limited to PDFs under `MAX_UPLOAD_MB`, and their file names are reduced to a plain name.

### Data and privacy

- Your PDFs and their searchable text are stored **on the computer running the app** (in `chroma_db/`).
- To work, the app sends text to **Google's Gemini API**: each section of a document when it is added (to
  create its search embedding), and the relevant excerpts plus your question whenever you ask something.
  Check that your organisation's policies allow this before adding confidential documents, and read Google's
  terms for the Gemini API tier you use (free and paid tiers handle data differently).
- Removing a document deletes its stored text and embeddings from this computer. It cannot recall text
  that was already sent to Gemini.

## Configuration

Set in `.env` or the environment. All optional except the key.

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none (required) | Your Gemini API key |
| `APP_NAME` | `Anthony's Study Assistant` | Name shown on the page and in the API docs |
| `MAX_UPLOAD_MB` | `50` | Largest PDF accepted through the web page |
| `EMBED_MODEL` | `gemini-embedding-001` | Embedding model |
| `CHAT_MODEL` | `gemini-3.5-flash` | Model that writes answers |
| `CHROMA_PATH` | `chroma_db` | Where ChromaDB stores data |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `100` | Size and overlap when a long page is split |
| `PAGE_LIMIT` | `1200` | Pages up to this length stay one chunk |
| `PER_SOURCE_K` | `2` | Chunks taken from each document, so every document is heard |
| `TOP_K` | `8` | Most chunks sent to the model in total, after the relevance cutoff |
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
  main.py         FastAPI app: web page, upload, ask
  ingest.py       add PDFs (shared by the web page and the command line)
  static/         the web page (index.html)
tests/            pytest suite
```

## Known limitations

- Text only: diagrams and images in slides are not read (a diagram's labels may extract as junk).
  Scanned or image-only PDFs have no text and are rejected at ingest.
- Short memory: follow-ups work because the page sends the last 6 exchanges, but nothing is saved
  between visits, and very old turns are forgotten. "New chat" wipes the memory.
- It is built for one person or a small trusted group on one machine. There is no login, no separate
  workspaces per team, and no audit trail: everyone who can open the page sees every document. Shared use
  across an organisation would need those added (plus hosting, HTTPS and backups) before it is exposed
  beyond a trusted network.
- Retrieval is purely by meaning. It can miss questions that depend on exact terms or on combining
  many slides. Hybrid search and reranking are natural next steps.
