"""Pacing for large documents: Gemini's free plan allows 100 embedded texts per minute, so indexing a
big PDF must feed texts in slowly (and wait patiently), while questions must stay quick."""
from types import SimpleNamespace

import pytest
from google.genai import errors

from app import config, gemini


class FakeTime:
    """A clock the test controls: sleeping just moves time forward."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(gemini, "_clock", fake.clock)
    monkeypatch.setattr(gemini, "_sleep", fake.sleep)
    return fake


class EmbedClient:
    """Stands in for client.models: records every embed request with the time it was made."""

    def __init__(self, fake_time, behaviour=None):
        self.fake_time = fake_time
        self.behaviour = behaviour
        self.requests: list[tuple[float, int]] = []  # (time, number of texts)

    def embed_content(self, model, contents, config):
        self.requests.append((self.fake_time.now, len(contents)))
        if self.behaviour:
            self.behaviour(len(self.requests))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2]) for _ in contents])


def install(monkeypatch, fake_time, behaviour=None) -> EmbedClient:
    client = EmbedClient(fake_time, behaviour)
    monkeypatch.setattr(gemini, "get_client", lambda: SimpleNamespace(models=client))
    return client


def set_pace(monkeypatch, per_minute: int):
    monkeypatch.setenv("EMBED_TEXTS_PER_MINUTE", str(per_minute))
    config.get_settings.cache_clear()


PER_MINUTE_QUOTA = "EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier"


def quota(delay: int | None = None, quota_id: str = PER_MINUTE_QUOTA):
    """A 429 shaped like Google's, naming the exceeded quota and optionally saying how long to wait."""
    failure = {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}
    details = [failure]
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"})
    body = {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED", "details": details}}
    return errors.ClientError(429, body)


def test_a_model_that_merges_texts_into_one_embedding_is_rejected(monkeypatch, fake_time):
    """Regression: gemini-embedding-2 returned ONE embedding for a list of 6 texts. Indexing would have
    silently kept only the first few sections of a document and reported success."""
    set_pace(monkeypatch, 1000)

    class MergingClient:
        def embed_content(self, model, contents, config):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])  # always just one

    monkeypatch.setattr(gemini, "get_client", lambda: SimpleNamespace(models=MergingClient()))
    with pytest.raises(gemini.GeminiError, match="returned 1 embeddings for 6 texts"):
        gemini.embed_documents([f"t{i}" for i in range(6)])
    with pytest.raises(gemini.GeminiError, match="one embedding per text"):
        gemini.embed_texts(["a", "b"], "RETRIEVAL_QUERY")


def test_documents_are_sent_in_small_batches_with_progress(monkeypatch, fake_time):
    set_pace(monkeypatch, 1000)
    client = install(monkeypatch, fake_time)
    seen = []
    texts = [f"text {i}" for i in range(45)]
    vectors = gemini.embed_documents(texts, lambda done, total: seen.append((done, total)))
    assert len(vectors) == 45
    assert [n for _, n in client.requests] == [20, 20, 5]  # DOC_BATCH_SIZE at a time
    assert seen == [(20, 45), (40, 45), (45, 45)]


def test_documents_never_exceed_the_per_minute_budget(monkeypatch, fake_time):
    set_pace(monkeypatch, 40)
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(200)])
    assert sum(n for _, n in client.requests) == 200
    # In ANY 60-second window, no more than 40 texts were sent.
    for start, _ in client.requests:
        in_window = sum(n for when, n in client.requests if start <= when < start + 60)
        assert in_window <= 40
    assert fake_time.now - 1000 >= 4 * 60 - 60  # 200 texts at 40/min takes about four minutes


def test_the_budget_window_is_longer_than_a_minute(monkeypatch, fake_time):
    """Regression: Google times a request on ARRIVAL, a moment after we record it. With an exact
    60-second window our count and Google's disagreed and Google answered 429 mid-document."""
    assert gemini.WINDOW_SECONDS > 60
    set_pace(monkeypatch, 90)
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(100)])
    first_burst = [when for when, _ in client.requests[:4]]  # 80 texts go out at once
    last = client.requests[-1][0]
    assert last - first_burst[0] >= gemini.WINDOW_SECONDS  # the 5th batch waited out the whole window


def test_the_first_budget_worth_goes_out_immediately(monkeypatch, fake_time):
    set_pace(monkeypatch, 40)
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(40)])
    assert fake_time.sleeps == []  # no pointless waiting when under the limit
    assert len(client.requests) == 2


def test_pacing_can_be_switched_off_for_paid_plans(monkeypatch, fake_time):
    set_pace(monkeypatch, 0)
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(250)])
    assert [n for _, n in client.requests] == [100, 100, 50]
    assert fake_time.sleeps == []


def test_a_batch_is_never_bigger_than_the_budget(monkeypatch, fake_time):
    set_pace(monkeypatch, 7)  # smaller than DOC_BATCH_SIZE: must not wait forever for an impossible batch
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(15)])
    assert max(n for _, n in client.requests) <= 7


def test_questions_are_never_paced_but_they_use_up_budget(monkeypatch, fake_time):
    set_pace(monkeypatch, 40)
    install(monkeypatch, fake_time)
    gemini.embed_texts(["a question"], "RETRIEVAL_QUERY")
    assert fake_time.sleeps == []  # answered at once
    client = install(monkeypatch, fake_time)
    gemini.embed_documents([f"t{i}" for i in range(40)])
    # 39 left in this minute after the question, so the last batch of 20 had to wait for the window
    assert fake_time.sleeps, "indexing should leave room for the question already sent"
    assert [n for _, n in client.requests] == [20, 20]


def test_indexing_waits_out_a_rate_limit_that_a_question_would_not(monkeypatch, fake_time):
    set_pace(monkeypatch, 1000)

    def refuse_once(request_number):
        if request_number == 1:
            raise quota(delay=59)

    client = install(monkeypatch, fake_time, refuse_once)
    assert len(gemini.embed_documents(["only one"])) == 1  # waited ~60s and carried on
    assert len(client.requests) == 2 and 59 < fake_time.sleeps[0] <= 61

    # The same 59s request for a QUESTION fails fast: nobody should stare at a spinner that long.
    client2 = install(monkeypatch, fake_time, refuse_once)
    with pytest.raises(gemini.GeminiQuotaError):
        gemini.embed_texts(["a question"], "RETRIEVAL_QUERY")
    assert len(client2.requests) == 1


def test_a_daily_quota_fails_fast_even_when_indexing(monkeypatch, fake_time):
    set_pace(monkeypatch, 1000)

    def daily(request_number):
        raise quota(delay=5, quota_id="EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier")

    client = install(monkeypatch, fake_time, daily)
    with pytest.raises(gemini.GeminiQuotaError, match="daily"):
        gemini.embed_documents(["x"])
    assert len(client.requests) == 1 and fake_time.sleeps == []  # waiting 5s wouldn't help: it's per day


def test_indexing_gives_up_after_a_very_long_wait_request(monkeypatch, fake_time):
    set_pace(monkeypatch, 1000)

    def very_long(request_number):
        raise quota(delay=900)

    client = install(monkeypatch, fake_time, very_long)
    with pytest.raises(gemini.GeminiQuotaError):
        gemini.embed_documents(["x"])
    assert len(client.requests) == 1


def test_pace_setting_is_validated(monkeypatch):
    monkeypatch.setenv("EMBED_TEXTS_PER_MINUTE", "-5")
    config.get_settings.cache_clear()
    with pytest.raises(ValueError, match="EMBED_TEXTS_PER_MINUTE"):
        config.get_settings()


def test_default_pace_leaves_headroom_under_the_free_limit():
    assert 0 < config.get_settings().embed_texts_per_minute < 100
