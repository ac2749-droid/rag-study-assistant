"""FastAPI app: the web page at /, plus the JSON API behind it.

Everything except the page, /health and the sign-in routes needs a signed-in user, and every request only
ever touches that user's own private library of documents.
"""
import html
import logging
import math
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from pypdf.errors import PyPdfError

from app import auth, jobs, messages, rag, store
from app.config import get_settings
from app.gemini import GeminiBusyError, GeminiError, GeminiQuotaError
from app.ingest import prepare_pdf
from app.ratelimit import ask_limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).parent
INDEX_PATH = HERE / "templates" / "index.html"
UPLOAD_READ_SIZE = 1024 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# The page loads only its own scripts and styles (no inline code, no outside sites), so even if something
# malicious ever got into a page it could not run or send data anywhere else.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")  # the generated API docs load their own inline code


def csrf_guard(request: Request) -> None:
    """Block forged cross-site requests. Anything that changes data must carry a custom header, which
    a web page on another site cannot add without our permission, and must not come from another origin."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(403, "Missing security header.")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "Cross-site request blocked.")


app = FastAPI(
    title=get_settings().app_name,
    description=(
        "Ask questions about your own documents (lecture slides, textbooks, manuals, handbooks, "
        "policies) and get answers with page references. This is the JSON API behind the web page "
        "at `/`; most people should use that page. Sign in with `POST /auth/login` first."
    ),
    version="1.0.0",
    dependencies=[Depends(csrf_guard)],
)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    path = request.url.path
    if not path.startswith(DOCS_PATHS):
        headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    headers["Cache-Control"] = "no-cache" if path.startswith("/static") else "no-store"
    return response


# ------------------------------------------------------------------ who is asking


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def current_user(request: Request) -> auth.User:
    user = auth.session_user(request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "Please sign in.")
    return user


def admin_user(user: auth.User = Depends(current_user)) -> auth.User:
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required.")
    return user


def start_session(response: Response, request: Request, user: auth.User) -> None:
    settings = get_settings()
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session(user.id),
        max_age=settings.session_hours * 3600,
        httponly=True,  # page scripts can't read it
        samesite="lax",
        secure=settings.cookie_secure or request.url.scheme == "https",
        path="/",
    )


# ------------------------------------------------------------------------ models


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class NewUser(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)
    role: str = "user"


class UserUpdate(BaseModel):
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=200)
    role: str | None = None


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    disabled: bool
    created_at: float


class AuthStatus(BaseModel):
    authenticated: bool
    setup_required: bool  # true until the first (administrator) account has been created
    user: UserOut | None = None


def user_out(user: auth.User) -> UserOut:
    return UserOut(
        id=user.id, username=user.username, role=user.role, disabled=user.disabled, created_at=user.created_at
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


# ------------------------------------------------------------------- the page


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    # Read on each visit: the file is tiny, and edits to the page show up without a restart.
    page = INDEX_PATH.read_text(encoding="utf-8")
    return page.replace("{{APP_NAME}}", html.escape(get_settings().app_name))


@app.get("/health")
def health() -> dict:
    """Is the server up? (Reveals nothing about anyone's documents.)"""
    return {"status": "ok"}


# --------------------------------------------------------------------- sign-in


@app.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    user = auth.session_user(request.cookies.get(auth.SESSION_COOKIE))
    return AuthStatus(
        authenticated=user is not None,
        setup_required=auth.user_count() == 0,
        user=user_out(user) if user else None,
    )


@app.post("/auth/setup", response_model=AuthStatus)
def setup(body: Credentials, request: Request, response: Response) -> AuthStatus:
    """Create the first account (an administrator). Only works while no accounts exist."""
    if auth.user_count() > 0:
        raise HTTPException(409, "This app has already been set up.")
    try:
        user = auth.create_user(body.username.strip(), body.password, "admin")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    auth.record("setup", user=user, ip=client_ip(request), detail="administrator account created")
    start_session(response, request, user)
    return AuthStatus(authenticated=True, setup_required=False, user=user_out(user))


@app.post("/auth/login", response_model=AuthStatus)
def login(body: Credentials, request: Request, response: Response) -> AuthStatus:
    ip = client_ip(request)
    try:
        user = auth.authenticate(body.username.strip(), body.password, ip)
    except auth.LockedOut as exc:
        minutes = math.ceil(exc.seconds / 60)
        raise HTTPException(
            429,
            f"Too many wrong passwords. Please try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(exc.seconds)},
        ) from None
    if user is None:
        raise HTTPException(401, "Incorrect username or password.")
    auth.record("login", user=user, ip=ip)
    start_session(response, request, user)
    return AuthStatus(authenticated=True, setup_required=False, user=user_out(user))


@app.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE)
    user = auth.session_user(token)
    auth.end_session(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    if user:
        auth.record("logout", user=user, ip=client_ip(request))
    return {"ok": True}


@app.post("/auth/password")
def change_password(
    body: PasswordChange, request: Request, response: Response, user: auth.User = Depends(current_user)
) -> dict:
    ip = client_ip(request)
    try:
        if auth.authenticate(user.username, body.current_password, ip) is None:
            raise HTTPException(403, "Your current password is incorrect.")
    except auth.LockedOut as exc:
        raise HTTPException(429, "Too many wrong passwords. Please try again later.",
                            headers={"Retry-After": str(exc.seconds)}) from None
    try:
        auth.set_password(user.id, body.new_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    auth.record("password_changed", user=user, ip=ip)
    start_session(response, request, user)  # changing the password signed everyone out, this device included
    return {"ok": True}


# ----------------------------------------------------------- administration


@app.get("/admin/users", response_model=list[UserOut])
def admin_list_users(_: auth.User = Depends(admin_user)) -> list[UserOut]:
    return [user_out(u) for u in auth.list_users()]


@app.post("/admin/users", response_model=UserOut, status_code=201)
def admin_create_user(
    body: NewUser, request: Request, admin: auth.User = Depends(admin_user)
) -> UserOut:
    try:
        user = auth.create_user(body.username.strip(), body.password, body.role)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    auth.record("user_created", user=admin, detail=f"{user.username} ({user.role})", ip=client_ip(request))
    return user_out(user)


def _would_remove_last_admin(target: auth.User) -> bool:
    return target.is_admin and not target.disabled and auth.active_admin_count() <= 1


@app.patch("/admin/users/{user_id}", response_model=UserOut)
def admin_update_user(
    user_id: int, body: UserUpdate, request: Request, admin: auth.User = Depends(admin_user)
) -> UserOut:
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(404, "No such user.")
    demoting = body.role == "user" and target.is_admin
    disabling = body.disabled is True
    if target.id == admin.id and (demoting or disabling):
        raise HTTPException(400, "You can't disable or demote your own account.")
    if (demoting or disabling) and _would_remove_last_admin(target):
        raise HTTPException(400, "There must always be at least one active administrator.")
    changes = []
    try:
        if body.role is not None and body.role != target.role:
            auth.set_role(user_id, body.role)
            changes.append(f"role={body.role}")
        if body.password is not None:
            auth.set_password(user_id, body.password)
            changes.append("password reset")
        if body.disabled is not None and body.disabled != target.disabled:
            auth.set_disabled(user_id, body.disabled)
            changes.append("disabled" if body.disabled else "enabled")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    auth.record("user_updated", user=admin, detail=f"{target.username}: {', '.join(changes) or 'no change'}",
                ip=client_ip(request))
    return user_out(auth.get_user(user_id))


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, admin: auth.User = Depends(admin_user)) -> dict:
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(404, "No such user.")
    if target.id == admin.id:
        raise HTTPException(400, "You can't delete your own account.")
    if _would_remove_last_admin(target):
        raise HTTPException(400, "There must always be at least one active administrator.")
    collection = auth.delete_user(user_id)
    if collection:
        try:
            store.delete_library(collection)  # their documents go with them
        except Exception:  # the library may never have been created
            log.info("No library to delete for %s", target.username)
    auth.record("user_deleted", user=admin, detail=target.username, ip=client_ip(request))
    return {"ok": True}


@app.get("/admin/audit")
def admin_audit(limit: int = 100, _: auth.User = Depends(admin_user)) -> list[dict]:
    """The most recent audit-log entries, newest first."""
    return auth.recent_audit(limit)


# ---------------------------------------------------------------- documents


@app.get("/documents", response_model=list[DocumentOut])
def documents(user: auth.User = Depends(current_user)) -> list[DocumentOut]:
    """The signed-in user's files, sorted by name."""
    return [DocumentOut(**d) for d in store.list_documents(collection_name=user.collection)]


@app.post("/documents", response_model=JobOut, status_code=202)
def upload_document(
    file: UploadFile, request: Request, user: auth.User = Depends(current_user)
) -> JobOut:
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
    detail = f"{name} ({len(prepared.chunks)} sections)"
    auth.record("upload", user=user, detail=detail, ip=client_ip(request))
    job = jobs.index_queue.submit(
        prepared.chunks, prepared.name, prepared.empty_pages,
        owner_id=user.id, owner_name=user.username, collection_name=user.collection,
    )
    return job_out(job)


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(user: auth.User = Depends(current_user)) -> list[JobOut]:
    """The signed-in user's recent indexing jobs, newest first."""
    return [job_out(job) for job in jobs.index_queue.all(owner_id=user.id)]


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: auth.User = Depends(current_user)) -> JobOut:
    job = jobs.index_queue.get(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job_out(job)


@app.delete("/documents/all")
def delete_all_documents(request: Request, user: auth.User = Depends(current_user)) -> dict[str, int]:
    """Remove every one of the signed-in user's files."""
    removed = store.delete_all(collection_name=user.collection)
    auth.record("documents_removed_all", user=user, detail=f"{removed} sections", ip=client_ip(request))
    return {"removed_chunks": removed}


@app.delete("/documents")
def delete_document(
    source: str, request: Request, user: auth.User = Depends(current_user)
) -> dict[str, int]:
    """Remove one of the signed-in user's files (pass its name as ?source=...)."""
    removed = store.delete_source(source, collection_name=user.collection)
    if removed == 0:
        raise HTTPException(404, "No such document.")
    auth.record("document_removed", user=user, detail=source, ip=client_ip(request))
    return {"removed_chunks": removed}


# ---------------------------------------------------------------- questions


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, user: auth.User = Depends(current_user)) -> AskResponse:
    wait = ask_limiter.check(user.id, get_settings().ask_per_minute)
    if wait:
        raise HTTPException(
            429, f"You're asking questions very quickly. Please wait {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    try:
        history = [rag.Turn(t.question, t.answer) for t in body.history]
        result = rag.answer_question(body.question, history, collection_name=user.collection)
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
    auth.record("ask", user=user, detail=f"{len(body.question)} characters", ip=client_ip(request))
    return AskResponse(
        answer=result.answer,
        sources=[SourceOut(source=s.source, page=s.page) for s in result.sources],
    )
