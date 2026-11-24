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
- **Safe re-ingesting.** Sections are saved as they are embedded and unchanged ones are reused (see "Large
  documents" below), so a failure never wipes existing data, and adding a file again resumes where it stopped.

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

**Windows:** double-click **`Launch Study Assistant.bat`**. It starts the app and opens your browser at
<http://127.0.0.1:8000>. Keep its window open while you use the app, and close the window to stop it. If
something isn't set up yet (the `.venv` folder or the `.env` file), it says so in plain language. For a
desktop shortcut, right-click the file and choose Send to, then Desktop (create shortcut).

**Any system:** start the server yourself, then open <http://127.0.0.1:8000>:

```bash
uvicorn app.main:app
```

**First time:** the page asks you to create the administrator account (a username and a password of at
least 10 characters). That first account keeps any documents you added before accounts existed. After that
you sign in with it, and the administrator can add other people from the account menu, each with a
private, empty library of their own. Forgot the password? `python -m app.users passwd NAME` sets a new one.

Once signed in, the page has your documents on the left and the conversation on the right:

1. **Add documents.** Click "Add documents" (or drag PDFs onto the page). Each file is listed with its page
   count. Use the trash icon to remove one file, or "Remove all documents" to clear the library. Removing
   asks for confirmation inside the page.
2. **Ask a question.** Type it and press Enter, or click an example. The answer appears with the pages it
   came from, and you can ask follow-ups. "New chat" starts over.

You can also add PDFs from the command line (useful for many files at once), and manage accounts:

```bash
python -m app.ingest "path/to/lecture1.pdf" "path/to/lecture2.pdf" [--user NAME]
python -m app.users add alice [--admin]      # also: list, passwd, disable, enable
```

### Try it in five minutes with the sample documents

`samples/` holds three small documents written for this project (free to share): a **router manual** and
an **employee handbook** (the "work" use) and a set of **C pointer study notes** (the "study" use). Sign
in, then load them (about 17 sections, a minute or two):

```bash
python -m app.ingest samples/*.pdf        # add --user NAME to load them into someone else's library
```

`DEMO.md` has a step-by-step demo script with questions that are known to work, a checklist for before
you present, and what to do if something goes wrong. The PDFs are generated by
`python tools/make_sample_pdfs.py`, and a test keeps the files and the generator in sync.

### API (for developers)

Interactive documentation is at <http://127.0.0.1:8000/docs>. Everything below needs a signed-in session
(`POST /auth/login` sets an `HttpOnly` cookie), and every request that changes data must also send the
header `X-Requested-With: XMLHttpRequest` (see Security). Each user only ever reaches their own documents.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/auth/status` | Is anyone signed in, and is first-time setup still needed? (public) |
| `POST` | `/auth/setup`, `/auth/login`, `/auth/logout` | Create the first (administrator) account, sign in, sign out. Body `{"username", "password"}` |
| `POST` | `/auth/password` | Change your own password: `{"current_password", "new_password"}` |
| `GET`, `POST` | `/admin/users` | Administrators: list accounts, or add one `{"username", "password", "role"}` |
| `PATCH`, `DELETE` | `/admin/users/{id}` | Administrators: disable/enable, reset a password, change role, or delete an account and its documents |
| `GET` | `/admin/audit?limit=100` | Administrators: the activity log, newest first |
| `POST` | `/ask` | Body `{"question": "...", "history": [{"question": "...", "answer": "..."}]}` (`history` is optional, up to 20 earlier exchanges, oldest first). Returns `{"answer": "...", "sources": [{"source": "file.pdf", "page": 4}]}` |
| `GET` | `/documents` | Files currently stored, sorted by name: `[{"source": "file.pdf", "chunks": 20, "pages": 20, "complete": true, "expected_chunks": 20}]` (`pages` counts pages with readable text; `complete` is `false` if indexing stopped early) |
| `POST` | `/documents` | Upload a PDF (multipart field `file`). The file is read at once (bad files are rejected immediately), then indexed in the background. Returns `202` with a job: `{"id", "source", "status", "done", "total", "eta_seconds", "message", "empty_pages"}`. Same name replaces the old copy when indexing finishes |
| `GET` | `/jobs`, `/jobs/{id}` | Progress of background indexing (`status` is `queued`, `running`, `done` or `error`; `message` explains an error) |
| `DELETE` | `/documents?source=file.pdf` | Remove one file |
| `DELETE` | `/documents/all` | Remove every file |
| `GET` | `/health` | Is the server up? Public, and reveals nothing about anyone's documents |

Errors: `401` not signed in, `403` not allowed (or a missing security header), `409` nothing added yet, `422`
invalid input or a PDF with no readable text, `400` not a (readable) PDF, `413` file over `MAX_UPLOAD_MB`,
`429` too many wrong passwords, questions asked too fast, or Gemini's rate limit or quota reached, `503`
Gemini is overloaded (both already retried), `502` any other Gemini failure.

### Accounts and security

- **Passwords** are never stored, only salted `scrypt` hashes (the memory-hard function recommended for
  this), compared in constant time. Passwords need 10+ characters; common ones are refused.
- **Sign-in sessions** are random tokens in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` over HTTPS).
  Only the token's SHA-256 is stored, so a leaked database can't be used to impersonate anyone. Sessions
  expire (`SESSION_HOURS`), end on sign-out, and are revoked when a password changes or an account is
  disabled or deleted.
- **Lock-out.** Five wrong passwords lock that username (from that address) out for 15 minutes. Unknown
  usernames and wrong passwords get the identical answer, so nobody can probe which accounts exist.
- **Private libraries.** Every account has its own document library; one person's documents, indexing
  jobs and answers can never reach another's. Administrators manage accounts but cannot read documents.
- **Forged requests (CSRF).** Anything that changes data must carry a custom header and may not come from
  another origin, so another website can't act for a signed-in user.
- **Browser security policy.** The page serves only its own scripts and styles (no inline code, nothing
  from other sites), forbids framing, and builds everything on screen as plain text, never HTML. Tests
  enforce all three.
- **Audit log.** Sign-ins, failed attempts, uploads, removals, questions (that they were asked, never the
  text), and account changes are recorded and visible to administrators. Passwords, tokens and document
  text are never logged.
- **Rate limits.** `ASK_PER_MINUTE` questions per person, so nobody burns through the Gemini allowance.

What this does **not** include: HTTPS (put the app behind a reverse proxy such as Caddy or nginx, or a
hosting service, before using it across a network, and set `COOKIE_SECURE=true`), password-reset emails,
two-factor sign-in, or multiple server processes (lock-outs and rate limits are kept in memory). It is
meant for one computer or a small trusted group; by default it listens only on `127.0.0.1`.

### Data and privacy

- Your PDFs and their searchable text are stored **on the computer running the app** (in `chroma_db/`,
  one private library per account). Accounts, sessions and the audit log are in `data/app.db`. Both
  folders are git-ignored and must never be committed or shared.
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
| `EMBED_TEXTS_PER_MINUTE` | `90` | How fast document sections are sent to Gemini for indexing. Gemini's free plan allows 100 per minute, so 90 leaves room for questions. Set `0` to turn pacing off on a paid plan |
| `GEMINI_TIMEOUT_SECONDS` | `45` | How long to wait for any single Gemini request before giving up on it (a stalled model then triggers the fallback) |
| `EMBED_MODEL` | `gemini-embedding-001` | Embedding model |
| `CHAT_MODEL` | `gemini-3.5-flash` | Model that writes answers |
| `CHAT_FALLBACK_MODELS` | `gemini-3.8-flash,gemini-3.1-flash-lite` | Comma-separated backups tried in order when the main model is rate-limited or overloaded (empty = none) |
| `CHROMA_PATH` | `chroma_db` | Where ChromaDB stores the documents |
| `DATA_DIR` | `data` | Where the accounts, sessions and audit log are kept |
| `SESSION_HOURS` | `12` | How long a sign-in lasts |
| `COOKIE_SECURE` | off | Set `true` when serving over HTTPS (it is also switched on automatically for https requests) |
| `ASK_PER_MINUTE` | `20` | Questions one person may ask per minute |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1500` / `150` | Size and overlap when a long page is split |
| `PAGE_LIMIT` | `2400` | Pages up to this length stay one chunk (fewer, larger sections use less of Gemini's daily limit) |
| `PER_SOURCE_K` | `2` | Chunks taken from each document, so every document is heard |
| `TOP_K` | `8` | Most chunks sent to the model in total, after the relevance cutoff |
| `MAX_DISTANCE` | `0.38` | Relevance cutoff (cosine distance, lower = closer) |

**Usage limits (HTTP 429).** Gemini's free plan limits requests per minute and per day, and the limits are
counted per model. If the main chat model is limited or overloaded, the app tries each fallback model
before giving up, and if Google says to wait more than 30 seconds it stops waiting at once. If every model
is limited you'll see "The AI service's usage limit was reached". Wait a minute, or check your plan at
<https://aistudio.google.com>. Embeddings (used when adding documents and when searching) have no
fallback, because vectors from different embedding models can't be compared.

**Large documents and Gemini's limits.** To search a document, each of its sections must be sent to Gemini
once to be indexed, and Gemini's free plan counts every section as one request, with two limits: **100 per
minute** and about **1,000 per day**. A 370-page phone manual is 439 sections at the default section size
(1,015 at the old, smaller size, which by itself was more than a free day allows). What the app does about it:

- **Pacing.** It stays under the per-minute limit (`EMBED_TEXTS_PER_MINUTE`, default 90, with a few seconds'
  safety margin) and indexes in the background with a progress bar, so you can keep asking questions
  about other documents meanwhile. A 439-section manual takes about 5 minutes.
- **Larger sections by default** (`CHUNK_SIZE=1500`, `PAGE_LIMIT=2400`), so a typical manual page stays whole
  and needs one embedding instead of two or three.
- **Nothing is wasted.** Each batch is saved as soon as it is embedded, and sections already stored are
  reused. If indexing stops early (a daily limit, a network failure), the file shows as **Incomplete** in
  the library with how far it got, and adding the same file again continues where it stopped. Re-adding an
  unchanged file costs nothing.
- **A clear message.** A daily limit says so, says it resets about midnight Pacific time, and that your
  progress is saved.
- **A guard against bad models.** Some Gemini embedding models (for example `gemini-embedding-2`) merge a
  list of texts into a single embedding. The app checks that it gets one embedding per text and stops with
  an explanation instead of quietly storing a mostly empty index.

To go faster or past the daily limit, use a paid Gemini plan and set `EMBED_TEXTS_PER_MINUTE=0`. Changing
`EMBED_MODEL` means re-adding your documents (vectors from different models can't be compared).

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
  auth.py         accounts, password hashing, sessions, lock-out, audit log (SQLite)
  users.py        command-line account management
  ratelimit.py    per-user question limit
  pdf_loader.py   PDF -> text per page
  chunking.py     text -> chunks
  gemini.py       the only module that calls Gemini (client, pacing, retries, error handling)
  store.py        ChromaDB: add, search, list (one private library per account)
  rag.py          the question-answering pipeline
  main.py         FastAPI app: security headers, sign-in, admin, documents, questions
  ingest.py       read PDFs and add them (shared by the web page and the command line)
  jobs.py         background indexing queue with progress
  messages.py     user-facing error messages
  templates/      the page (index.html)
  static/         the page's script and styles (app.js, app.css)
tests/            pytest suite
Launch Study Assistant.bat   Windows double-click launcher
```

## Known limitations

- Text only: diagrams and images in slides are not read (a diagram's labels may extract as junk).
  Scanned or image-only PDFs have no text and are rejected at ingest.
- Short memory: follow-ups work because the page sends the last 6 exchanges, but nothing is saved
  between visits, and very old turns are forgotten. "New chat" wipes the memory.
- It is built for one computer or a small trusted group. Accounts have private libraries, but there are no
  shared team workspaces, no sign-in by company directory (SSO), no password-reset emails and no
  two-factor sign-in. Using it across an organisation would also need HTTPS, hosting and backups (see
  "Accounts and security").
- Retrieval is purely by meaning. It can miss questions that depend on exact terms or on combining
  many slides. Hybrid search and reranking are natural next steps.
