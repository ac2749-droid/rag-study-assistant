"""Conversation memory: follow-up questions are rewritten so they can be searched."""
import pytest
from fastapi.testclient import TestClient

from app import gemini
from app.main import app
from tests.conftest import REWRITTEN_QUESTION

HISTORY = [{"question": "What does getopt return?", "answer": "It returns -1, '?' or ':'."}]


@pytest.fixture
def client():
    return TestClient(app)


def is_rewrite(prompt: str) -> bool:
    return prompt.startswith("Rewrite the user's latest message")


def test_first_question_costs_no_extra_llm_call(client, loaded_store, fake_gemini):
    client.post("/ask", json={"question": "getopt?"})
    assert len(fake_gemini["prompts"]) == 1
    assert not is_rewrite(fake_gemini["prompts"][0])


def test_follow_up_is_rewritten_and_the_rewrite_is_what_gets_searched(client, loaded_store, fake_gemini):
    r = client.post("/ask", json={"question": "explain that more", "history": HISTORY})
    assert r.status_code == 200
    rewrite, answer = fake_gemini["prompts"]
    assert is_rewrite(rewrite) and "explain that more" in rewrite and "What does getopt return?" in rewrite
    # the SEARCH used the standalone question, not the vague original
    assert fake_gemini["embedded"][-1] == ("RETRIEVAL_QUERY", [REWRITTEN_QUESTION])
    # and it found the getopt slides, which "explain that more" alone could never match
    assert {s["page"] for s in r.json()["sources"]} == {3, 4}
    # the final answer prompt carries the conversation and the user's own words
    assert "What does getopt return?" in answer and "QUESTION: explain that more" in answer
    assert "<conversation>" in answer


def test_without_history_the_conversation_block_is_absent(client, loaded_store, fake_gemini):
    client.post("/ask", json={"question": "getopt?"})
    assert "<conversation>" not in fake_gemini["prompts"][0]


def test_only_the_most_recent_turns_are_used(client, loaded_store, fake_gemini):
    history = [{"question": f"old question {i}", "answer": f"old answer {i}"} for i in range(12)]
    client.post("/ask", json={"question": "and that?", "history": history})
    answer_prompt = fake_gemini["prompts"][-1]
    assert "old question 11" in answer_prompt and "old question 6" in answer_prompt
    assert "old question 5" not in answer_prompt  # older than the last 6 turns


def test_long_earlier_answers_are_shortened(client, loaded_store, fake_gemini):
    history = [{"question": "q", "answer": "x" * 5000}]
    client.post("/ask", json={"question": "more?", "history": history})
    assert "x" * 2000 not in fake_gemini["prompts"][-1]


def test_failed_rewrite_falls_back_to_the_original_question(client, loaded_store, fake_gemini, monkeypatch):
    original_generate = gemini.generate

    def flaky(prompt):
        if is_rewrite(prompt):
            raise gemini.GeminiBusyError("busy")
        return original_generate(prompt)

    monkeypatch.setattr(gemini, "generate", flaky)
    r = client.post("/ask", json={"question": "getopt follow up", "history": HISTORY})
    assert r.status_code == 200 and r.json()["answer"] == "FAKE ANSWER"
    assert fake_gemini["embedded"][-1] == ("RETRIEVAL_QUERY", ["getopt follow up"])


def test_empty_rewrite_falls_back_to_the_original_question(client, loaded_store, fake_gemini, monkeypatch):
    monkeypatch.setattr(gemini, "generate", lambda prompt: "   " if is_rewrite(prompt) else "FAKE ANSWER")
    client.post("/ask", json={"question": "getopt again", "history": HISTORY})
    assert fake_gemini["embedded"][-1] == ("RETRIEVAL_QUERY", ["getopt again"])


def test_multiline_rewrite_keeps_only_the_first_line(client, loaded_store, fake_gemini, monkeypatch):
    chatty = '"getopt details?"\nSure! Here is some extra chatter'
    monkeypatch.setattr(gemini, "generate", lambda p: chatty if is_rewrite(p) else "FAKE ANSWER")
    client.post("/ask", json={"question": "more", "history": HISTORY})
    assert fake_gemini["embedded"][-1] == ("RETRIEVAL_QUERY", ["getopt details?"])


def test_off_topic_follow_up_is_still_refused(client, loaded_store, fake_gemini, monkeypatch):
    monkeypatch.setattr(gemini, "generate", lambda p: "What is a B-tree?" if is_rewrite(p) else "FAKE ANSWER")
    r = client.post("/ask", json={"question": "and a B-tree?", "history": HISTORY})
    assert r.json()["sources"] == []


@pytest.mark.parametrize(
    "history",
    [
        [{"question": "q", "answer": "a"}] * 21,  # too many turns
        [{"question": "q" * 2001, "answer": "a"}],  # question too long
        [{"question": "q", "answer": "a" * 20001}],  # answer too long
        [{"question": "missing answer"}],  # malformed turn
    ],
)
def test_bad_history_is_rejected(client, loaded_store, history):
    assert client.post("/ask", json={"question": "more?", "history": history}).status_code == 422
