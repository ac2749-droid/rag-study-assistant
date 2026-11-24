"""The retry/error logic in app.gemini, using fake API errors (no network)."""
from types import SimpleNamespace

import httpx
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


def quota_error(delay: int | None = None):
    """A 429 like Google's, optionally carrying a RetryInfo hint ("wait N seconds")."""
    retry_info = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"}
    details = [retry_info] if delay else []
    body = {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED", "details": details}}
    return errors.ClientError(429, body)


def test_the_exceeded_quota_is_named_in_the_error():
    body = {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "600s"},
    ]}}

    def call():
        raise errors.ClientError(429, body)

    with pytest.raises(gemini.GeminiQuotaError, match="EmbedContentRequestsPerMinutePerProjectPerModel"):
        gemini._with_retries(call)


def test_waits_as_long_as_google_asks_on_a_rate_limit(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise quota_error(delay=7)
        return "ok"

    assert gemini._with_retries(call) == "ok"
    assert no_sleep == [8]  # Google's 7 seconds plus one second of margin


def test_a_long_rate_limit_wait_fails_immediately(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        raise quota_error(delay=600)

    with pytest.raises(gemini.GeminiQuotaError):
        gemini._with_retries(call)
    assert len(attempts) == 1 and no_sleep == []  # didn't make the user wait 10 minutes


def test_persistent_rate_limit_is_a_quota_error_which_is_also_busy():
    def call():
        raise quota_error()

    with pytest.raises(gemini.GeminiQuotaError) as info:
        gemini._with_retries(call)
    assert isinstance(info.value, gemini.GeminiBusyError)


def test_persistent_server_errors_are_busy_but_not_quota():
    def call():
        raise server_error()

    with pytest.raises(gemini.GeminiBusyError) as info:
        gemini._with_retries(call)
    assert not isinstance(info.value, gemini.GeminiQuotaError)


class _Models:
    """A stand-in for client.models that records which model was called."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, model, contents):
        self.calls.append(model)
        return self.behaviour(model)


def use_fake_client(monkeypatch, behaviour):
    models = _Models(behaviour)
    monkeypatch.setattr(gemini, "get_client", lambda: SimpleNamespace(models=models))
    return models


def set_fallbacks(monkeypatch, names: str):
    from app import config

    monkeypatch.setenv("CHAT_FALLBACK_MODELS", names)
    config.get_settings.cache_clear()


def test_falls_back_to_another_model_when_the_main_one_is_rate_limited(monkeypatch):
    set_fallbacks(monkeypatch, "backup-model")

    def behaviour(model):
        if model == "gemini-3.5-flash":
            raise quota_error()
        return SimpleNamespace(text="from the backup")

    models = use_fake_client(monkeypatch, behaviour)
    assert gemini.generate("hi") == "from the backup"
    # the main model gets one quick retry, then we move on
    assert models.calls == ["gemini-3.5-flash", "gemini-3.5-flash", "backup-model"]


def test_tries_each_backup_in_order(monkeypatch):
    set_fallbacks(monkeypatch, "backup-1,backup-2")

    def behaviour(model):
        if model != "backup-2":
            raise server_error()
        return SimpleNamespace(text="second backup answered")

    models = use_fake_client(monkeypatch, behaviour)
    assert gemini.generate("hi") == "second backup answered"
    assert [m for i, m in enumerate(models.calls) if i == 0 or m != models.calls[i - 1]] == [
        "gemini-3.5-flash", "backup-1", "backup-2",
    ]


def test_default_backups_are_other_models(monkeypatch):
    from app import config

    config.get_settings.cache_clear()
    names = config.get_settings().chat_fallback_models
    assert names and "gemini-3.5-flash" not in names


def test_main_model_is_used_when_it_works(monkeypatch):
    models = use_fake_client(monkeypatch, lambda model: SimpleNamespace(text="main answer"))
    assert gemini.generate("hi") == "main answer"
    assert models.calls == ["gemini-3.5-flash"]


def test_error_when_every_model_is_limited(monkeypatch):
    def behaviour(model):
        raise quota_error()

    use_fake_client(monkeypatch, behaviour)
    with pytest.raises(gemini.GeminiQuotaError):
        gemini.generate("hi")


def test_without_fallback_models_the_main_model_gets_every_attempt(monkeypatch):
    set_fallbacks(monkeypatch, "")

    def behaviour(model):
        raise server_error()

    models = use_fake_client(monkeypatch, behaviour)
    with pytest.raises(gemini.GeminiBusyError):
        gemini.generate("hi")
    assert models.calls == ["gemini-3.5-flash"] * gemini.MAX_ATTEMPTS


def test_other_errors_do_not_trigger_the_fallback(monkeypatch):
    def behaviour(model):
        raise errors.ClientError(404, {"error": {"message": "model not found"}})

    models = use_fake_client(monkeypatch, behaviour)
    with pytest.raises(gemini.GeminiError):
        gemini.generate("hi")
    assert models.calls == ["gemini-3.5-flash"]  # a wrong model name isn't a capacity problem


def test_fallback_models_come_from_a_comma_separated_setting(monkeypatch):
    from app import config

    monkeypatch.setenv("CHAT_FALLBACK_MODELS", "model-a, model-b ,")
    config.get_settings.cache_clear()
    assert config.get_settings().chat_fallback_models == ("model-a", "model-b")


def test_a_timeout_is_not_retried_on_the_same_model(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        raise httpx.ReadTimeout("no answer")

    with pytest.raises(gemini.GeminiBusyError):
        gemini._with_retries(call)
    assert len(attempts) == 1 and no_sleep == []  # one long wait is enough


def test_a_dropped_connection_is_retried(no_sleep):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection dropped")
        return "ok"

    assert gemini._with_retries(call) == "ok"
    assert no_sleep == [2]


def test_a_connection_that_never_recovers_is_busy_not_quota():
    def call():
        raise httpx.ConnectError("down")

    with pytest.raises(gemini.GeminiBusyError) as info:
        gemini._with_retries(call)
    assert not isinstance(info.value, gemini.GeminiQuotaError)


def test_a_timeout_on_the_main_model_moves_to_the_backup(monkeypatch):
    set_fallbacks(monkeypatch, "backup-model")

    def behaviour(model):
        if model == "gemini-3.5-flash":
            raise httpx.ReadTimeout("stuck")
        return SimpleNamespace(text="backup answered")

    models = use_fake_client(monkeypatch, behaviour)
    assert gemini.generate("hi") == "backup answered"
    assert models.calls == ["gemini-3.5-flash", "backup-model"]  # no second try on the stuck model


def test_the_timeout_setting_reaches_the_gemini_client(monkeypatch):
    from app import config

    seen = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(gemini.genai, "Client", fake_client)
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "12")
    config.get_settings.cache_clear()
    gemini.get_client.cache_clear()
    gemini.get_client()
    assert seen["http_options"].timeout == 12000  # the library wants milliseconds


def test_default_timeout_is_not_unlimited():
    from app import config

    assert 0 < config.get_settings().gemini_timeout <= 120


def test_a_non_positive_timeout_is_rejected(monkeypatch):
    from app import config

    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "0")
    config.get_settings.cache_clear()
    with pytest.raises(ValueError, match="GEMINI_TIMEOUT_SECONDS"):
        config.get_settings()


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        gemini.get_client()
