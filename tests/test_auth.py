"""Accounts, sign-in, sessions, lockout, forged requests and the browser security policy."""
import hashlib
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, config
from tests.conftest import ADMIN_NAME, ADMIN_PASSWORD, CSRF_HEADER

GOOD = "another long password"


@pytest.fixture
def browser():
    """A factory for brand-new, signed-out browsers."""
    from app.main import app

    return lambda **kwargs: TestClient(app, headers=CSRF_HEADER, **kwargs)


def login(browser, username=ADMIN_NAME, password=ADMIN_PASSWORD):
    return browser.post("/auth/login", json={"username": username, "password": password})


# ------------------------------------------------------------------ passwords


def test_passwords_are_stored_as_salted_scrypt_hashes_never_plain():
    stored = auth.hash_password("a perfectly fine password")
    assert stored.startswith("scrypt$") and "a perfectly fine password" not in stored
    assert auth.hash_password("a perfectly fine password") != stored  # a fresh random salt each time


def test_verify_password_accepts_only_the_right_password():
    stored = auth.hash_password("correct horse battery")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("correct horse batter", stored)
    assert not auth.verify_password("", stored)


def test_verify_password_survives_garbage_in_the_database():
    for broken in ("", "plain text", "scrypt$1$2", "bcrypt$x$y$z$a$b", "scrypt$x$y$z$!!$??"):
        assert auth.verify_password("anything", broken) is False


def test_unicode_passwords_work():
    stored = auth.hash_password("pässwörd-密码-🔐-long")
    assert auth.verify_password("pässwörd-密码-🔐-long", stored)


@pytest.mark.parametrize("name", ["ab", "a" * 33, "has space", "semi;colon", "emoji🙂user", "", "a/b"])
def test_bad_usernames_are_rejected(name):
    with pytest.raises(ValueError, match="Usernames"):
        auth.validate_username(name)


@pytest.mark.parametrize("name", ["abc", "Anthony", "a.b-c_d", "user123", "x" * 32])
def test_good_usernames_are_accepted(name):
    auth.validate_username(name)


@pytest.mark.parametrize(
    ("password", "problem"),
    [("short", "at least 10"), ("x" * 201, "too long"), ("password123", "too common"),
     ("1234567890", "too common"), ("SameAsName1", "same as the username")],
)
def test_weak_passwords_are_rejected(password, problem):
    with pytest.raises(ValueError, match=problem):
        auth.validate_password(password, username="sameasname1")


def test_a_long_passphrase_is_fine():
    auth.validate_password("correct horse battery staple", "anthony")


# ------------------------------------------------------------------- set-up


def test_status_says_setup_is_needed_until_the_first_account_exists(anon_client):
    status = anon_client.get("/auth/status").json()
    assert status == {"authenticated": False, "setup_required": True, "user": None}


def test_setup_creates_an_administrator_and_signs_them_in(anon_client):
    r = anon_client.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin" and r.json()["setup_required"] is False
    assert anon_client.get("/auth/status").json()["authenticated"] is True


def test_setup_only_works_once(client, browser):
    again = browser().post("/auth/setup", json={"username": "intruder", "password": ADMIN_PASSWORD})
    assert again.status_code == 409
    assert auth.get_user_by_name("intruder") is None


def test_setup_rejects_a_weak_password(anon_client):
    r = anon_client.post("/auth/setup", json={"username": "boss", "password": "short"})
    assert r.status_code == 422 and "at least 10" in r.json()["detail"]
    assert auth.user_count() == 0


def test_the_session_cookie_is_httponly_and_samesite(anon_client):
    r = anon_client.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie  # plain http locally


def test_the_cookie_is_marked_secure_over_https(browser):
    r = browser(base_url="https://testserver").post(
        "/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_the_cookie_can_be_forced_secure_by_a_setting(monkeypatch, anon_client):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    config.get_settings.cache_clear()
    r = anon_client.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


# -------------------------------------------------------------------- sign-in


def test_login_works_and_username_is_not_case_sensitive(client, browser):
    other = browser()
    assert login(other, "ADMIN").status_code == 200
    assert other.get("/auth/status").json()["user"]["username"] == "admin"


def test_wrong_password_and_unknown_user_look_identical(client, browser):
    wrong = login(browser(), ADMIN_NAME, "not the password at all")
    unknown = login(browser(), "nobody", "not the password at all")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Incorrect username or password."}


def test_a_disabled_account_cannot_sign_in(client, new_user, browser):
    new_user("dora")
    dora = auth.get_user_by_name("dora")
    assert client.patch(f"/admin/users/{dora.id}", json={"disabled": True}).status_code == 200
    r = login(browser(), "dora", GOOD)
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect username or password."  # not "disabled"


def test_five_wrong_passwords_lock_the_account_out(client, browser):
    attacker = browser()
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert login(attacker, ADMIN_NAME, "guess guess guess").status_code == 401
    locked = login(attacker, ADMIN_NAME, ADMIN_PASSWORD)  # even the RIGHT password is refused now
    assert locked.status_code == 429
    assert "try again in" in locked.json()["detail"] and int(locked.headers["retry-after"]) > 0


def test_the_lockout_ends_after_a_while(client, browser, monkeypatch):
    attacker = browser()
    for _ in range(auth.MAX_FAILED_LOGINS):
        login(attacker, ADMIN_NAME, "guess guess guess")
    assert login(attacker, ADMIN_NAME, ADMIN_PASSWORD).status_code == 429
    later = time.time() + auth.LOCKOUT_SECONDS + 5
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert login(attacker, ADMIN_NAME, ADMIN_PASSWORD).status_code == 200


def test_locking_out_one_account_does_not_lock_out_another(client, new_user, browser):
    new_user("erin")
    attacker = browser()
    for _ in range(auth.MAX_FAILED_LOGINS):
        login(attacker, ADMIN_NAME, "guess guess guess")
    assert login(browser(), "erin", GOOD).status_code == 200


def test_a_successful_sign_in_resets_the_failure_count(client, browser):
    someone = browser()
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        login(someone, ADMIN_NAME, "guess guess guess")
    assert login(someone, ADMIN_NAME, ADMIN_PASSWORD).status_code == 200
    for _ in range(auth.MAX_FAILED_LOGINS - 1):  # the count started again from zero
        assert login(someone, ADMIN_NAME, "guess guess guess").status_code == 401


# ----------------------------------------------------------------- protection


PROTECTED = [
    ("get", "/documents"), ("get", "/jobs"), ("get", "/jobs/abc"), ("post", "/ask"), ("post", "/documents"),
    ("delete", "/documents?source=x.pdf"), ("delete", "/documents/all"), ("get", "/admin/users"),
    ("get", "/admin/audit"), ("post", "/admin/users"), ("patch", "/admin/users/1"),
    ("delete", "/admin/users/1"), ("post", "/auth/password"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_private_route_requires_signing_in(anon_client, method, path):
    r = getattr(anon_client, method)(path)
    assert r.status_code in (401, 422), (method, path, r.status_code)
    if r.status_code == 422:  # the body failed validation before auth could run: check with a valid body too
        r = getattr(anon_client, method)(path, json={"question": "hello"})
    assert r.status_code == 401 and r.json()["detail"] == "Please sign in."


def test_public_routes_need_no_sign_in(anon_client):
    assert anon_client.get("/").status_code == 200
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/auth/status").status_code == 200
    assert anon_client.get("/static/app.js").status_code == 200


def test_ordinary_users_cannot_reach_admin_routes(client, new_user):
    bob = new_user("bob")
    for method, path in [("get", "/admin/users"), ("get", "/admin/audit"), ("post", "/admin/users"),
                         ("patch", "/admin/users/1"), ("delete", "/admin/users/1")]:
        r = getattr(bob, method)(path, **({"json": {}} if method in ("post", "patch") else {}))
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"] == "Administrator access required."


def test_a_made_up_cookie_does_not_work(anon_client):
    anon_client.cookies.set("session", "not-a-real-token")
    assert anon_client.get("/documents").status_code == 401


# ------------------------------------------------------------------- sessions


def test_signing_out_ends_the_session_even_if_the_cookie_is_replayed(client, browser):
    stolen = client.cookies.get("session")
    assert client.get("/documents").status_code == 200
    client.post("/auth/logout")
    thief = browser()
    thief.cookies.set("session", stolen)
    assert thief.get("/documents").status_code == 401


def test_sessions_expire(client, monkeypatch):
    assert client.get("/documents").status_code == 200
    later = time.time() + 13 * 3600  # sessions last 12 hours
    monkeypatch.setattr(auth, "_now", lambda: later)
    r = client.get("/documents")
    assert r.status_code == 401


def test_session_length_is_a_setting(monkeypatch, anon_client):
    monkeypatch.setenv("SESSION_HOURS", "1")
    config.get_settings.cache_clear()
    anon_client.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    monkeypatch.setattr(auth, "_now", lambda: time.time() + 2 * 3600)
    assert anon_client.get("/documents").status_code == 401


def test_the_database_never_holds_the_session_token_itself(client):
    token = client.cookies.get("session")
    rows = sqlite3.connect(Path(config.get_settings().data_dir) / "app.db").execute(
        "SELECT token_hash FROM sessions").fetchall()
    stored = {r[0] for r in rows}
    assert token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored


def test_disabling_an_account_signs_it_out_immediately(client, new_user):
    bob = new_user("bob")
    assert bob.get("/documents").status_code == 200
    client.patch(f"/admin/users/{auth.get_user_by_name('bob').id}", json={"disabled": True})
    assert bob.get("/documents").status_code == 401


def test_changing_a_password_signs_out_other_devices_but_not_this_one(client, browser):
    laptop = client
    phone = browser()
    assert login(phone).status_code == 200
    r = laptop.post("/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": GOOD})
    assert r.status_code == 200
    assert laptop.get("/documents").status_code == 200  # this device got a fresh session
    assert phone.get("/documents").status_code == 401  # the old session elsewhere is gone
    assert login(browser(), ADMIN_NAME, GOOD).status_code == 200
    assert login(browser(), ADMIN_NAME, ADMIN_PASSWORD).status_code == 401


def test_changing_a_password_needs_the_current_one_and_a_good_new_one(client):
    wrong = client.post("/auth/password", json={"current_password": "nope nope nope", "new_password": GOOD})
    assert wrong.status_code == 403
    weak = client.post("/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"})
    assert weak.status_code == 422 and "at least 10" in weak.json()["detail"]


# ------------------------------------------------------- forged requests (CSRF)


def test_changes_without_the_security_header_are_refused():
    from app.main import app

    bare = TestClient(app)  # like a form posted from another website: no custom header
    r = bare.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    assert r.status_code == 403 and "security header" in r.json()["detail"]
    assert auth.user_count() == 0


def test_a_signed_in_user_cannot_be_tricked_by_a_header_less_request(client):
    from app.main import app

    victim = TestClient(app, cookies=client.cookies)  # has the real cookie but not the custom header
    assert victim.delete("/documents/all").status_code == 403
    assert victim.post("/ask", json={"question": "hi"}).status_code == 403
    assert victim.get("/documents").status_code == 200  # reading is unaffected


def test_requests_from_another_origin_are_refused_even_with_the_header(client):
    r = client.delete("/documents/all", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-site" in r.json()["detail"]


def test_requests_from_our_own_origin_are_fine(client):
    r = client.delete("/documents/all", headers={"Origin": "http://testserver"})
    assert r.status_code == 200


# ------------------------------------------------------- browser security policy


def test_the_page_carries_a_strict_security_policy(anon_client):
    r = anon_client.get("/")
    policy = r.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


def test_api_answers_are_never_cached(client):
    assert client.get("/documents").headers["cache-control"] == "no-store"


def test_static_files_are_revalidated_not_stored_forever(anon_client):
    assert anon_client.get("/static/app.css").headers["cache-control"] == "no-cache"


def test_the_generated_api_docs_still_load(anon_client):
    r = anon_client.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_the_page_contains_no_inline_scripts_styles_or_handlers():
    """A strict policy only works if the page really has none; this keeps it that way."""
    page = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page), "inline <script>"
    assert "<style" not in page.lower(), "inline <style>"
    assert not re.search(r'\sstyle\s*=', page, re.I), "inline style attribute"
    assert not re.search(r'\son[a-z]+\s*=', page, re.I), "inline event handler"


def test_the_script_never_builds_html_from_text():
    """Everything shown on the page is added as plain text, so text from a document or a user name can
    never become markup (the cross-site scripting hole)."""
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    forbidden = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function")
    for dangerous in forbidden:
        assert dangerous not in script, dangerous
    assert "alert(" not in script and "confirm(" not in script  # native popups are blocked in some browsers


def test_signing_out_wipes_the_previous_persons_data_from_the_page():
    """Regression: after sign-out the admin's user list and activity log stayed in the (hidden) page, so
    the next person to sign in on that browser still had it in their page."""
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    cleanup = script.split("function resetSessionState()")[1].split("\n}\n")[0]
    for must_clear in ('$("userList").replaceChildren()', '$("auditBody").replaceChildren()',
                       "pwCurrent", "nuPass"):
        assert must_clear in cleanup, must_clear
    assert '$("authUser").value = ""' in script.split("async function signOut()")[1].split("\n}\n")[0]


# ---------------------------------------------------------------- rate limits


def test_questions_are_rate_limited_per_user(client, new_user, loaded_store, monkeypatch):
    monkeypatch.setenv("ASK_PER_MINUTE", "2")
    config.get_settings.cache_clear()
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 200
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 200
    slow_down = client.post("/ask", json={"question": "getopt?"})
    assert slow_down.status_code == 429 and "very quickly" in slow_down.json()["detail"]
    assert int(slow_down.headers["retry-after"]) >= 1
    bob = new_user("bob")  # one person's speed doesn't slow anyone else down
    assert bob.post("/ask", json={"question": "getopt?"}).status_code == 409  # (no documents yet: not 429)


def test_the_question_limit_resets_after_a_minute(client, loaded_store, monkeypatch):
    from app import ratelimit

    monkeypatch.setenv("ASK_PER_MINUTE", "1")
    config.get_settings.cache_clear()
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 200
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 429
    now = time.monotonic()
    monkeypatch.setattr(ratelimit, "_now", lambda: now + 61)
    assert client.post("/ask", json={"question": "getopt?"}).status_code == 200


# ----------------------------------------------------------------------- audit


def actions(client):
    return [e["action"] for e in client.get("/admin/audit").json()]


def test_the_audit_log_records_sign_ins_failures_and_actions(client, browser, fake_gemini, loaded_store):
    login(browser(), ADMIN_NAME, "guess guess guess")
    client.post("/ask", json={"question": "getopt?"})
    client.delete("/documents", params={"source": "deck.pdf"})
    seen = actions(client)
    for expected in ("setup", "login_failed", "ask", "document_removed"):
        assert expected in seen


def test_the_audit_log_never_contains_passwords_or_questions(client, browser, loaded_store):
    login(browser(), ADMIN_NAME, "super secret guess")
    client.post("/ask", json={"question": "what is my confidential question"})
    dump = str(client.get("/admin/audit").json())
    assert "super secret guess" not in dump and "confidential question" not in dump
    assert ADMIN_PASSWORD not in dump


def test_the_audit_log_is_newest_first_and_limited(client):
    for _ in range(5):
        client.post("/auth/logout")
        login(client)
    entries = client.get("/admin/audit", params={"limit": 3}).json()
    assert len(entries) == 3 and entries[0]["time"] >= entries[-1]["time"]


def test_only_administrators_can_read_the_audit_log(client, new_user):
    assert new_user("bob").get("/admin/audit").status_code == 403


# ------------------------------------------------------ administering accounts


def test_the_administrator_can_add_list_and_remove_accounts(client):
    created = client.post("/admin/users", json={"username": "carol", "password": GOOD})
    assert created.status_code == 201 and created.json()["role"] == "user"
    assert {u["username"] for u in client.get("/admin/users").json()} == {"admin", "carol"}
    assert client.delete(f"/admin/users/{created.json()['id']}").status_code == 200
    assert {u["username"] for u in client.get("/admin/users").json()} == {"admin"}


def test_duplicate_and_invalid_accounts_are_refused(client):
    assert client.post("/admin/users", json={"username": "carol", "password": GOOD}).status_code == 201
    assert client.post("/admin/users", json={"username": "CAROL", "password": GOOD}).status_code == 422
    assert client.post("/admin/users", json={"username": "x", "password": GOOD}).status_code == 422
    assert client.post("/admin/users", json={"username": "dave", "password": "short"}).status_code == 422
    bad_role = {"username": "dave", "password": GOOD, "role": "root"}
    assert client.post("/admin/users", json=bad_role).status_code == 422


def test_an_administrator_can_reset_a_password_and_change_a_role(client, new_user, browser):
    new_user("frank")
    frank = auth.get_user_by_name("frank")
    r = client.patch(f"/admin/users/{frank.id}", json={"password": "a brand new password", "role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert login(browser(), "frank", GOOD).status_code == 401
    assert login(browser(), "frank", "a brand new password").status_code == 200


def test_administrators_cannot_lock_themselves_out(client):
    me = auth.get_user_by_name(ADMIN_NAME)
    assert client.patch(f"/admin/users/{me.id}", json={"disabled": True}).status_code == 400
    assert client.patch(f"/admin/users/{me.id}", json={"role": "user"}).status_code == 400
    assert client.delete(f"/admin/users/{me.id}").status_code == 400
    assert client.get("/documents").status_code == 200  # still signed in and still an administrator


def test_unknown_accounts_give_not_found(client):
    assert client.patch("/admin/users/999", json={"disabled": True}).status_code == 404
    assert client.delete("/admin/users/999").status_code == 404


def test_deleting_an_account_removes_its_documents_and_sessions(client, new_user, fake_gemini):
    from app import store
    from app.chunking import Chunk

    bob = new_user("bob")
    library = auth.get_user_by_name("bob").collection
    store.add_chunks([Chunk("secret.pdf", 1, 0, "getopt secret")], "secret.pdf", collection_name=library)
    assert bob.get("/documents").json()[0]["source"] == "secret.pdf"
    assert client.delete(f"/admin/users/{auth.get_user_by_name('bob').id}").status_code == 200
    assert bob.get("/documents").status_code == 401  # their session died with the account
    assert store.get_collection(library).count() == 0  # a fresh, empty library: the documents are gone


def test_the_first_account_keeps_documents_added_before_accounts_existed(anon_client, fake_gemini):
    from app import store
    from app.chunking import Chunk

    store.add_chunks([Chunk("old.pdf", 1, 0, "getopt old text")], "old.pdf")  # the pre-accounts library
    anon_client.post("/auth/setup", json={"username": "boss", "password": ADMIN_PASSWORD})
    assert [d["source"] for d in anon_client.get("/documents").json()] == ["old.pdf"]


def test_the_command_line_tool_manages_accounts(monkeypatch, capsys):
    from app import users

    monkeypatch.setenv("PW", "a command line password")
    assert users.main(["add", "ops", "--admin", "--password-env", "PW"]) == 0
    assert users.main(["add", "ops", "--password-env", "PW"]) == 1  # already exists
    assert users.main(["list"]) == 0
    assert "ops" in capsys.readouterr().out
    assert auth.authenticate("ops", "a command line password") is not None
    assert users.main(["disable", "ops"]) == 0
    assert auth.authenticate("ops", "a command line password") is None
    assert users.main(["enable", "ops"]) == 0
    monkeypatch.setenv("PW", "a different long password")
    assert users.main(["passwd", "ops", "--password-env", "PW"]) == 0
    assert auth.authenticate("ops", "a different long password") is not None


def test_the_command_line_tool_refuses_weak_passwords(monkeypatch, capsys):
    from app import users

    monkeypatch.setenv("PW", "short")
    assert users.main(["add", "ops", "--password-env", "PW"]) == 1
    assert "at least 10" in capsys.readouterr().err
    assert auth.get_user_by_name("ops") is None
