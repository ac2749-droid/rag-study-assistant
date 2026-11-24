"""Accounts, sign-in sessions and the audit log.

Everything lives in one SQLite file and uses only the Python standard library:
- passwords are never stored, only salted scrypt hashes, compared in constant time;
- a sign-in is a random token kept in an HttpOnly cookie; only its SHA-256 is stored, so a leaked
  database can't be used to impersonate anyone, and a session can be revoked at any time;
- repeated wrong passwords lock that username (from that address) out for a while;
- sensitive actions are written to an audit log.
"""
import base64
import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from app.config import get_settings

SESSION_COOKIE = "session"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
ROLES = ("admin", "user")
MAX_FAILED_LOGINS = 5  # wrong passwords allowed before a lock-out...
FAILURE_WINDOW_SECONDS = 15 * 60  # ...within this period
LOCKOUT_SECONDS = 15 * 60
COMMON_PASSWORDS = {
    "password12", "password123", "1234567890", "12345678910", "qwertyuiop", "iloveyou12",
    "letmein123", "admin12345", "welcome123", "passw0rd123",
}
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # about 16 MB and a tenth of a second per guess

_now = time.time  # indirection so tests can move the clock

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    disabled INTEGER NOT NULL DEFAULT 0,
    collection TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id INTEGER,
    username TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
"""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool
    collection: str  # the name of this user's private document library
    created_at: float

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class LockedOut(Exception):
    """Too many wrong passwords; try again after `seconds`."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"locked out for {seconds}s")
        self.seconds = seconds


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    parts = ["scrypt", str(_SCRYPT["n"]), str(_SCRYPT["r"]), str(_SCRYPT["p"])]
    parts += [base64.b64encode(salt).decode(), base64.b64encode(digest).decode()]
    return "$".join(parts)


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_bytes = base64.b64decode(expected)
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected_bytes),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected_bytes)


def validate_username(username: str) -> None:
    if not USERNAME_PATTERN.match(username):
        raise ValueError("Usernames are 3 to 32 characters: letters, numbers, dot, dash or underscore.")


def validate_password(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("That password is too long.")
    if password.lower() in COMMON_PASSWORDS:
        raise ValueError("That password is too common. Please choose another.")
    if username and password.lower() == username.lower():
        raise ValueError("The password can't be the same as the username.")


_dummy_hash: str | None = None


def _dummy() -> str:
    """A real hash to check against when the username doesn't exist, so timing doesn't reveal it."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(16))
    return _dummy_hash


# ----------------------------------------------------------------- database


@contextlib.contextmanager
def db() -> Iterator[sqlite3.Connection]:
    path = get_settings().data_dir / "app.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)  # safe to repeat: every statement is CREATE ... IF NOT EXISTS
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"], username=row["username"], role=row["role"], disabled=bool(row["disabled"]),
        collection=row["collection"], created_at=row["created_at"],
    )


def user_count() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def active_admin_count() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0]


def get_user(user_id: int) -> User | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user(row) if row else None


def get_user_by_name(username: str) -> User | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _user(row) if row else None


def list_users() -> list[User]:
    with db() as conn:
        return [_user(r) for r in conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")]


def create_user(username: str, password: str, role: str = "user") -> User:
    """Create an account. The very first account adopts the original document library (so documents added
    before accounts existed stay with it); every later account gets its own private, empty one."""
    validate_username(username)
    validate_password(password, username)
    if role not in ROLES:
        raise ValueError("Role must be 'admin' or 'user'.")
    base = get_settings().collection_name
    with db() as conn:
        first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, collection, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, hash_password(password), role, "pending-" + secrets.token_hex(8), _now()),
            )
        except sqlite3.IntegrityError:
            raise ValueError("That username is already taken.") from None
        user_id = cursor.lastrowid
        collection = base if first else f"{base}_u{user_id}"
        conn.execute("UPDATE users SET collection = ? WHERE id = ?", (collection, user_id))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user(row)


def set_password(user_id: int, new_password: str) -> None:
    user = get_user(user_id)
    if user is None:
        raise ValueError("No such user.")
    validate_password(new_password, user.username)
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user_id)
        )
    end_user_sessions(user_id)  # anyone signed in with the old password is signed out


def set_disabled(user_id: int, disabled: bool) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
    if disabled:
        end_user_sessions(user_id)


def set_role(user_id: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError("Role must be 'admin' or 'user'.")
    with db() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def delete_user(user_id: int) -> str | None:
    """Remove an account and its sessions. Returns the name of its document library so the caller can
    delete that too, or None if there was no such user."""
    user = get_user(user_id)
    if user is None:
        return None
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return user.collection


# ------------------------------------------------------------------ sign-in


class _Throttle:
    """Remembers recent wrong passwords per (username, address) and locks out repeat offenders."""

    def __init__(self) -> None:
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._locked_until: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def seconds_locked(self, key: tuple[str, str]) -> int:
        with self._lock:
            remaining = self._locked_until.get(key, 0) - _now()
            if remaining <= 0:
                self._locked_until.pop(key, None)
                return 0
            return int(remaining) + 1

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            now = _now()
            recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= MAX_FAILED_LOGINS:
                self._locked_until[key] = now + LOCKOUT_SECONDS
                self._failures.pop(key, None)

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


throttle = _Throttle()


def authenticate(username: str, password: str, ip: str = "") -> User | None:
    """Check a username and password. Returns the user, or None if they are wrong or the account is
    disabled (deliberately the same answer, so nobody can probe which usernames exist).
    Raises LockedOut after too many failures."""
    key = (username.lower(), ip)
    locked = throttle.seconds_locked(key)
    if locked:
        raise LockedOut(locked)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    stored = row["password_hash"] if row else _dummy()
    password_ok = verify_password(password, stored)
    if row is None or not password_ok or row["disabled"]:
        throttle.record_failure(key)
        reason = "account disabled" if row and row["disabled"] else ""
        record("login_failed", username=username, ip=ip, detail=reason)
        return None
    throttle.clear(key)
    return _user(row)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, now, now + get_settings().session_hours * 3600),
        )
    return token


def session_user(token: str | None) -> User | None:
    """The user a sign-in cookie belongs to, or None if it is unknown, expired or the account is disabled."""
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_hash_token(token), _now()),
        ).fetchone()
    if row is None or row["disabled"]:
        return None
    return _user(row)


def end_session(token: str | None) -> None:
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def end_user_sessions(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# -------------------------------------------------------------------- audit


def record(action: str, *, user: User | None = None, user_id: int | None = None, username: str = "",
           detail: str = "", ip: str = "") -> None:
    """Append an entry to the audit log. Never put passwords, tokens or document text in `detail`."""
    with db() as conn:
        conn.execute(
            "INSERT INTO audit (ts, user_id, username, action, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now(), user.id if user else user_id, user.username if user else username,
                action, detail[:300], ip,
            ),
        )


def recent_audit(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
    return [
        {"time": r["ts"], "user": r["username"], "action": r["action"], "detail": r["detail"], "ip": r["ip"]}
        for r in rows
    ]
