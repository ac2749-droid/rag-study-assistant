"""The retry/error logic in app.gemini, using fake API errors (no network)."""
import pytest
from google.genai import errors

from app import gemini


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(gemini, "_sleep", waits.append)
    return waits


def server_error(code=503):
    return errors.ServerError(code, {"error": {"message": "busy", "status": "UNAVAILABLE"}})


def test_retries_then_succeeds(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise server_error()
        return "ok"

    assert gemini._with_retries(call) == "ok"
    assert len(attempts) == 3
    assert no_sleep == [2, 4]  # growing pause


def test_gives_up_with_busy_error(no_sleep):
    def call():
        raise server_error()

    with pytest.raises(gemini.GeminiBusyError):
        gemini._with_retries(call)
    assert len(no_sleep) == gemini.MAX_ATTEMPTS - 1


def test_rate_limit_is_retried():
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise errors.ClientError(429, {"error": {"message": "slow down"}})
        return "ok"

    assert gemini._with_retries(call) == "ok"


def test_client_errors_fail_immediately(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        raise errors.ClientError(404, {"error": {"message": "model not found"}})

    with pytest.raises(gemini.GeminiError) as info:
        gemini._with_retries(call)
    assert not isinstance(info.value, gemini.GeminiBusyError)
    assert len(attempts) == 1 and no_sleep == []


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        gemini.get_client()
