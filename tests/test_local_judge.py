"""Offline tests for scripts/local_judge.py and the judge() dispatcher."""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import local_judge as lj  # noqa: E402
import typesafe_core as core  # noqa: E402

CHOICE = {"type": "choice", "instructions": "Which team?",
          "criteria": {"billing": "payments", "technical": None, "sales": {"covers": "pricing"}}}
SCORE = {"type": "score", "instructions": "How urgent?", "criteria": ["can wait", "this week", "today"]}
NOUL = {"type": "noul", "instructions": "Is it urgent?", "criteria": {"true": "time-sensitive"}}


def top(**probs):
    return [{"token": token, "logprob": math.log(p)} for token, p in probs.items()]


def test_build_prompt_labels_each_type():
    prompt, labels, keys = lj.build_prompt(CHOICE)
    assert labels == ["A", "B", "C"] and keys == ["billing", "technical", "sales"]
    assert "A: billing (payments)" in prompt and "B: technical\n" in prompt
    assert 'C: sales ({"covers": "pricing"})' in prompt

    _, labels, keys = lj.build_prompt(SCORE)
    assert labels == keys == ["0", "1", "2"]

    prompt, labels, keys = lj.build_prompt(NOUL)
    assert labels == ["Y", "N"] and keys == ["yes", "no"]
    assert "Y: yes (time-sensitive)" in prompt and "N: no\n" in prompt


def test_build_prompt_rejects_oversized_questions():
    with pytest.raises(ValueError):
        lj.build_prompt({"type": "choice", "instructions": "?",
                         "criteria": {str(i): None for i in range(27)}})
    with pytest.raises(ValueError):
        lj.build_prompt({"type": "score", "instructions": "?", "criteria": ["only one"]})


def test_label_distribution_merges_variants_and_reports_coverage():
    probs, coverage = lj.label_distribution(
        top(**{"A": 0.5, " A": 0.1, "b": 0.2, "<think>": 0.2}), ["A", "B"])

    assert probs == pytest.approx([0.75, 0.25])
    assert coverage == pytest.approx(0.8)


def test_label_distribution_without_any_label_fails_loudly():
    with pytest.raises(RuntimeError, match="no probability"):
        lj.label_distribution(top(**{"<think>": 0.9}), ["Y", "N"])


def test_concentration_ranges_from_spread_to_certain():
    assert lj.concentration([1 / 3] * 3) == pytest.approx(0)
    assert lj.concentration([1.0, 0.0, 0.0]) == pytest.approx(1)


def test_to_answer_matches_typesafe_shapes():
    choice = lj.to_answer(CHOICE, ["billing", "technical", "sales"], [0.8, 0.2, 0.0], 1.0)
    assert choice["type"] == "choice" and choice["choice"] == "billing"
    assert set(choice["probabilities"]) == {"billing", "technical", "sales"}
    assert 0 < choice["confidence"] < 1

    score = lj.to_answer(SCORE, ["0", "1", "2"], [0.0, 0.5, 0.5], 1.0)
    assert score["score"] == pytest.approx(1.5)
    assert score["legend"] == {"0": "can wait", "1": "this week", "2": "today"}

    assert lj.to_answer(NOUL, ["yes", "no"], [0.9, 0.1], 1.0) == {"type": "noul", "noul": 0.9}


def test_low_coverage_lowers_confidence():
    full = lj.to_answer(CHOICE, ["billing", "technical", "sales"], [1.0, 0.0, 0.0], 1.0)
    half = lj.to_answer(CHOICE, ["billing", "technical", "sales"], [1.0, 0.0, 0.0], 0.5)
    assert half["confidence"] == pytest.approx(full["confidence"] / 2)


def fake_server(calls, tops):
    """A fake OpenAI-compatible endpoint answering each question with the given top_logprobs."""
    def _http(method, url, *, body=None, headers=None):
        if url.endswith("/models"):
            return 200, json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        payload = json.loads(body)
        calls.append({"url": url, "body": payload, "headers": headers})
        prompt = payload["messages"][1]["content"]
        logprobs = tops["noul"] if "Y: yes" in prompt else tops["choice"]
        return 200, json.dumps({"choices": [{"logprobs": {
            "content": [{"top_logprobs": logprobs}]}}]}).encode()
    return _http


def test_system_one_answers_all_questions_against_local_server(monkeypatch):
    calls = []
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.setattr(lj.moth, "_http", fake_server(calls, {
        "choice": top(A=0.9, B=0.1), "noul": top(Y=0.2, N=0.8)}))
    judge = lj.LocalJudge(base_url="http://localhost:8080/v1")

    answers = judge.system_one({"request": "refund please"}, {"team": CHOICE, "urgent": NOUL})

    assert judge.model == "model-a"
    assert answers["team"]["choice"] == "billing"
    assert answers["urgent"]["noul"] == pytest.approx(0.2)
    body = calls[0]["body"]
    assert body["max_tokens"] == 1 and body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert '"refund please"' in body["messages"][0]["content"]
    assert len({c["body"]["messages"][0]["content"] for c in calls}) == 1   # shared prefix


def test_cloud_endpoint_sends_key_and_no_template_kwargs(monkeypatch):
    calls = []
    monkeypatch.setattr(lj.moth, "_http", fake_server(calls, {
        "choice": top(A=1.0), "noul": top(Y=1.0)}))
    judge = lj.LocalJudge(base_url="https://api.example.com/v1", model="m", api_key="sk-test")

    judge.system_one("state", {"q": NOUL})

    assert "chat_template_kwargs" not in calls[0]["body"]
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_endpoint_without_logprobs_fails_with_clear_message(monkeypatch):
    monkeypatch.setattr(lj.moth, "_http", lambda *a, **k: (200, json.dumps(
        {"choices": [{"message": {"content": "Y"}}]}).encode()))
    judge = lj.LocalJudge(base_url="http://localhost:8080/v1", model="m")

    with pytest.raises(RuntimeError, match="no logprobs"):
        judge.system_one("state", {"q": NOUL})


def test_judge_dispatches_by_env(monkeypatch):
    seen = []
    monkeypatch.setattr(core, "ask_typesafe", lambda s, q: seen.append("typesafe") or {})
    monkeypatch.setattr(lj.LocalJudge, "__init__", lambda self: setattr(self, "model", "m")
                        or setattr(self, "base_url", "u"))
    monkeypatch.setattr(lj.LocalJudge, "system_one", lambda self, s, q: seen.append("local") or {})

    monkeypatch.delenv("JUDGE", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts")
    core.judge({}, {})
    monkeypatch.delenv("TYPESAFE_API_KEY")
    core.judge({}, {})
    monkeypatch.setenv("JUDGE", "local")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts")
    core.judge({}, {})

    assert seen == ["typesafe", "local", "local"]
