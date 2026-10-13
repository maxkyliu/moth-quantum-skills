"""Shared pieces for routing plain-language requests to Moth engines with TypeSafe.

A router builds every question for every engine, sends them in one TypeSafe request,
then reads only the chosen engine's answers through a `Reader`, which remembers how
certain each judgment it used was. Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moth  # noqa: E402

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
# Example threshold: evaluate it on your own requests before relying on it.
CONFIRM_BELOW = 0.6
STATED = 0.5          # a "did the user say anything about X?" Noul above this uses X's answer
MAX_RETRIES = 5
BACKOFF_SECONDS = 2


def ask(question: str) -> str:
    """Point a question at the user's words, held in state.request."""
    return f"{question} Judge from the user's request in `request`."


def ask_typesafe(state: dict, questions: dict) -> dict:
    """POST one System One request; return the answers map. Retries 429/529 with backoff."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        sys.exit("TYPESAFE_API_KEY is not set. Create one at https://console.typesafe.ai/.")
    body = json.dumps({"model": TYPESAFE_MODEL, "state": state, "questions": questions}).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        status, raw = moth._http("POST", TYPESAFE_URL, body=body, headers=headers)
        if status in (429, 529) and attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_SECONDS * 2 ** attempt)
            continue
        if status >= 400:
            raise RuntimeError(f"TypeSafe HTTP {status}: {raw[:500].decode(errors='replace')}")
        return json.loads(raw)["answers"]
    raise RuntimeError("TypeSafe retries exhausted")


@dataclass(frozen=True)
class Plan:
    engine: str | None
    params: dict = field(default_factory=dict)
    missing_slots: tuple = ()
    confidence: float = 0.0
    reasons: tuple = ()

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.reasons) or self.confidence < CONFIRM_BELOW

    def report(self) -> dict:
        return {"engine": self.engine, "params": self.params, "confidence": self.confidence,
                "missing_slots": list(self.missing_slots), "reasons": list(self.reasons),
                "needs_confirmation": self.needs_confirmation}


def certainty(noul: float) -> float:
    """How decided a yes/no is: 0.5 → 0, 0 or 1 → 1."""
    return abs(noul - 0.5) * 2


class Reader:
    """Reads answers and records the certainty of each one the plan actually uses."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.used: list[float] = []

    def choice(self, qid: str) -> str:
        self.used.append(self.answers[qid]["confidence"])
        return self.answers[qid]["choice"]

    def yes(self, qid: str) -> bool:
        p = self.answers[qid]["noul"]
        self.used.append(certainty(p))
        return p > STATED

    def peek(self, qid: str) -> bool:
        """A yes/no that only matters if true; its certainty counts only then."""
        p = self.answers[qid]["noul"]
        if p > STATED:
            self.used.append(certainty(p))
        return p > STATED

    def stated(self, qid: str) -> bool:
        return self.yes(f"{qid}?")

    def unit(self, qid: str) -> float:
        """A Score's probability-weighted level mapped to 0..1."""
        answer = self.answers[qid]
        self.used.append(answer["confidence"])
        return round(answer["score"] / (len(answer["legend"]) - 1), 2)

    def along(self, qid: str, anchors: tuple[float, ...]) -> float:
        """A Score's level interpolated between one anchor value per level."""
        answer = self.answers[qid]
        self.used.append(answer["confidence"])
        position = min(max(answer["score"], 0), len(anchors) - 1)
        low = int(position)
        if low == len(anchors) - 1:
            return round(anchors[-1], 2)
        frac = position - low
        return round(anchors[low] + frac * (anchors[low + 1] - anchors[low]), 2)

    def confidence(self) -> float:
        return round(min(self.used), 2) if self.used else 0.0


def run_plan(plan: Plan, files: dict[str, str], out: Path) -> dict:
    """Upload files, show the live cost, submit, wait and download."""
    definition = moth.engine(plan.engine)
    print(f"{plan.engine}: {definition.get('credits_per_run')} credit(s) per run", file=sys.stderr)
    uploaded = {slot: moth.upload(Path(path)) for slot, path in files.items()}
    job_id = moth.submit(plan.engine, plan.params, uploaded or None)
    status = moth.wait(job_id)
    if status["status"] != "completed":
        raise RuntimeError(json.dumps(status))
    return moth.result(job_id, out)


def finish(plan: Plan, files: dict[str, str], *, run: bool, yes: bool, out: str) -> None:
    """Print the plan; run it only when asked, complete, and confirmed if needed."""
    print(json.dumps(plan.report(), indent=2))
    if not run or plan.engine is None:
        return
    if plan.missing_slots:
        sys.exit(f"missing input file(s): {', '.join(plan.missing_slots)}")
    if plan.needs_confirmation and not yes:
        sys.exit("plan needs confirmation: review it, then rerun with --yes")
    print(json.dumps(run_plan(plan, files, Path(out)), indent=2, ensure_ascii=False))
