"""A stand-in for TypeSafe's System One API on any OpenAI-compatible LLM endpoint.

Each Choice, Score or Noul question becomes a prompt whose answers are single-token labels
(A, B, C… / 0–9 / Y, N). The model is asked for exactly one token; the probabilities it gives
those labels (`top_logprobs`) become the answer, in the same shape TypeSafe returns:

    choice: {"type", "choice", "probabilities", "confidence"}
    score:  {"type", "score", "legend", "probabilities", "confidence"}
    noul:   {"type", "noul"}

Works with llama.cpp / llama-swap, vLLM and hosted APIs that return logprobs. The endpoint must
support `logprobs` and `top_logprobs`; reasoning models that hide their logprobs will not work.
These probabilities are not calibrated like Jev's: re-tune confidence thresholds on your data.
Standard library only.
"""
from __future__ import annotations

import json
import math
import os
import string
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moth  # noqa: E402

DEFAULT_BASE_URL = "http://localhost:8080/v1"     # llama-swap's default listen address
TOP_LOGPROBS = 20                                  # OpenAI's maximum; plenty for ≤ 26 labels
CHOICE_LABELS = string.ascii_uppercase             # up to 26 options locally (TypeSafe: 255)
SCORE_LABELS = string.digits                       # up to 10 levels, as in TypeSafe
NOUL_LABELS = ("Y", "N")
SYSTEM = ("You are a precise judge. Read the state, then answer the question with exactly one "
          "label from the options, and nothing else.")


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def build_prompt(question: dict) -> tuple[str, list[str], list[str]]:
    """Return (user prompt, labels, option keys) for one TypeSafe-style question."""
    kind = question["type"]
    lines = [f"Question: {_text(question['instructions'])}", "", "Options:"]
    if kind == "choice":
        keys = list(question["criteria"])
        if len(keys) > len(CHOICE_LABELS):
            raise ValueError(f"local judge supports at most {len(CHOICE_LABELS)} choice options")
        labels = list(CHOICE_LABELS[:len(keys)])
        for label, key in zip(labels, keys):
            detail = question["criteria"][key]
            lines.append(f"{label}: {key}" + (f" ({_text(detail)})" if detail else ""))
    elif kind == "score":
        levels = question["criteria"]
        if not 2 <= len(levels) <= len(SCORE_LABELS):
            raise ValueError("a score needs 2 to 10 levels")
        labels = list(SCORE_LABELS[:len(levels)])
        keys = labels
        lines += [f"{label}: {_text(level)}" for label, level in zip(labels, levels)]
    elif kind == "noul":
        labels, keys = list(NOUL_LABELS), ["yes", "no"]
        criteria = question.get("criteria") or {}
        lines.append("Y: yes" + (f" ({_text(criteria['true'])})" if criteria.get("true") else ""))
        lines.append("N: no" + (f" ({_text(criteria['false'])})" if criteria.get("false") else ""))
    else:
        raise ValueError(f"unknown question type {kind!r}")
    lines += ["", f"Answer with one label: {', '.join(labels)}."]
    return "\n".join(lines), labels, keys


def label_distribution(top_logprobs: list[dict], labels: list[str]) -> tuple[list[float], float]:
    """Sum the probability of each label's token variants (' A', 'a'); renormalise.

    Returns (probabilities per label, coverage = total mass the model put on any label).
    """
    mass = dict.fromkeys(labels, 0.0)
    lookup = {label.lower(): label for label in labels}
    for entry in top_logprobs:
        label = lookup.get(entry["token"].strip().lower())
        if label:
            mass[label] += math.exp(entry["logprob"])
    coverage = sum(mass.values())
    if coverage == 0:
        raise RuntimeError("the model put no probability on any answer label; "
                           "check that thinking is off and the model follows the format")
    return [mass[label] / coverage for label in labels], min(coverage, 1.0)


def concentration(probs: list[float]) -> float:
    """1 − normalised entropy: 1 when one option takes everything, 0 when spread evenly."""
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    return 1 - entropy / math.log(len(probs))


def to_answer(question: dict, keys: list[str], probs: list[float], coverage: float) -> dict:
    """Shape a label distribution like TypeSafe's answer for that question type."""
    kind = question["type"]
    confidence = round(concentration(probs) * coverage, 4)
    if kind == "noul":
        return {"type": "noul", "noul": round(probs[0], 4)}
    distribution = {key: round(p, 4) for key, p in zip(keys, probs)}
    if kind == "choice":
        best = max(distribution, key=distribution.get)
        return {"type": "choice", "choice": best, "probabilities": distribution,
                "confidence": confidence}
    return {"type": "score", "score": round(sum(i * p for i, p in enumerate(probs)), 4),
            "legend": {str(i): level for i, level in enumerate(question["criteria"])},
            "probabilities": distribution, "confidence": confidence}


class LocalJudge:
    """Answers a TypeSafe-style questions map against an OpenAI-compatible endpoint."""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, concurrency: int | None = None):
        self.base_url = (base_url or os.environ.get("JUDGE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("JUDGE_API_KEY", "")
        self.model = model or os.environ.get("JUDGE_MODEL") or self._first_model()
        self.concurrency = concurrency or int(os.environ.get("JUDGE_CONCURRENCY", "4"))
        host = urlparse(self.base_url).hostname or ""
        # llama.cpp chat templates accept this; hosted APIs may reject unknown fields.
        self.local = host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _first_model(self) -> str:
        status, raw = moth._http("GET", f"{self.base_url}/models", headers=self._headers())
        if status >= 400:
            raise RuntimeError(f"cannot list models at {self.base_url}: HTTP {status}")
        models = [m["id"] for m in json.loads(raw)["data"]]
        if not models:
            raise RuntimeError(f"no models served at {self.base_url}; set JUDGE_MODEL")
        return models[0]

    def _ask_one(self, state_text: str, question: dict) -> dict:
        prompt, labels, keys = build_prompt(question)
        body = {"model": self.model, "max_tokens": 1, "temperature": 0,
                "logprobs": True, "top_logprobs": TOP_LOGPROBS,
                "messages": [{"role": "system", "content": f"{SYSTEM}\n\nState:\n{state_text}"},
                             {"role": "user", "content": prompt}]}
        if self.local:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        status, raw = moth._http("POST", f"{self.base_url}/chat/completions",
                                 body=json.dumps(body).encode(), headers=self._headers())
        if status >= 400:
            raise RuntimeError(f"judge HTTP {status}: {raw[:300].decode(errors='replace')}")
        logprobs = (json.loads(raw)["choices"][0].get("logprobs") or {}).get("content")
        if not logprobs:
            raise RuntimeError(f"{self.base_url} returned no logprobs; the judge needs an "
                               "endpoint and model that support top_logprobs")
        probs, coverage = label_distribution(logprobs[0]["top_logprobs"], labels)
        return to_answer(question, keys, probs, coverage)

    def system_one(self, state: object, questions: dict) -> dict:
        """Answer every question in parallel; returns {question id: answer}."""
        state_text = _text(state)   # identical system prompt → the server reuses its prefix cache
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {qid: pool.submit(self._ask_one, state_text, q) for qid, q in questions.items()}
            return {qid: future.result() for qid, future in futures.items()}
