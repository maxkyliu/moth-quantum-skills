#!/usr/bin/env python3
"""Refine the last Moth run from plain-language feedback.

Reads the last run from <out>/session.json (written by the routers' --run), asks the judge
which settings the feedback wants changed and by how much, and plans the next job. Unnamed
settings keep their current values. For retrocausal-echo-v1, a change that only touches
playback settings reuses the previous run's measured IR instead of measuring again.

    python typesafe_refine.py "less backwards, more of the dry voice"
    python typesafe_refine.py "make the echoes stranger" --run

Judge: TypeSafe (TYPESAFE_API_KEY) or a local LLM (JUDGE=local). MOTH_API_KEY for --run.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from typesafe_core import SESSION_FILE, Plan, Reader, ask, finish, judge, load_session  # noqa: E402

SIZE_LEVELS = ["a little", "a lot"]
PHASE_THETA_Z = 0.79     # "phase" echoes need an IR measured with theta_z > 0


@dataclass(frozen=True)
class Dial:
    """One setting feedback can move. Numbers move up or down 1-2 steps; choices pick an option.

    Direction is asked as a choice between `lower` and `higher`, each in plain words, rather
    than as "more / less": "more of the dry voice" otherwise reads as "more echo"."""
    name: str
    param: str
    about: str                       # what the setting is, in plain words
    default: object
    step: float = 0.0                # change for "a little"; "a lot" moves two steps
    low: float = 0.0
    high: float = 1.0
    lower: str = ""                  # what moving it down sounds or looks like
    higher: str = ""                 # what moving it up sounds or looks like
    options: dict = field(default_factory=dict)
    measured: bool = False           # retrocausal-echo-v1: changing it needs a new measurement
    says: str = ""                   # casual ways people ask for it, for the yes/no question


ECHO = (
    Dial("mix", "mix", "how loud the echo effect is against the original, dry sound", 0.6, 0.15,
         lower="drier: less echo, more of the original sound",
         higher="wetter: more echo, less of the original sound",
         says="more or less echo, wetter, drier, more of the original voice, subtler"),
    Dial("character", "theta_x", "how regular or chaotic the echo pattern is (not whether echoes "
         "play backwards or swirl)", 0.94, 0.35, high=3.14, measured=True,
         lower="cleaner, more regular, evenly repeating echoes",
         higher="stranger: more chaotic, erratic, inverted and vanishing echoes",
         says="cleaner, more regular, more chaotic, weirder pattern, more unpredictable"),
    Dial("repeats", "feedback", "how many times the echoes keep coming back", 0.0, 0.15,
         high=0.9, lower="fewer repeats", higher="more repeats, echoes keep coming back",
         says="more repeats, keep repeating, fewer repeats, single echo"),
    Dial("tail", "decay", "how slowly the echoes fade away, so how long the tail rings out",
         0.9, 0.1, lower="shorter tail, echoes fade faster",
         higher="longer tail, echoes ring out longer",
         says="longer tail, ring out longer, shorter tail, fade faster"),
    Dial("width", "stereo_width", "how wide the stereo image is, from mono to fully wide",
         1.0, 0.25, lower="narrower, closer to mono", higher="wider stereo",
         says="wider, narrower, more mono, more stereo, more centred"),
    Dial("negative_mode", "negative_mode", "the direction and kind of the echoes: forward but "
         "flipped, played backwards, or swirling in phase", "invert",
         says="less or more backwards, reversed, swirly, phasey, forward echoes",
         options={"invert": "forward echoes, flipped so they partly cancel the sound",
                  "reverse": "echoes played backwards",
                  "phase": "echoes that swirl or shift in phase"}),
)
VARIATION = Dial("variation", "variation", "how freely the result departs from the original",
                 1.0, 0.4, low=0.1, high=3.0,
                 lower="closer to the original, more predictable",
                 higher="looser, more surprising")
DIALS = {
    "retrocausal-echo-v1": ECHO,
    "blur-midi-v1": (
        Dial("strength", "strength", "how strongly the notes are blurred", 0.5, 0.15,
             lower="gentler, closer to the original notes", higher="stronger blur"),
        Dial("reach", "reach", "how far apart notes can be and still blend", 0.0, 0.25,
             lower="only neighbouring notes blend", higher="notes far apart blend too"),
        Dial("cleanup", "threshold", "how many faint, stray notes are removed", 0.1, 0.05,
             lower="keep more faint notes", higher="remove more faint, stray notes"),
    ),
    "qrc-midi-v1": (VARIATION,),
    "qrc-audio-v1": (
        VARIATION,
        Dial("crossfade", "crossfade", "how smoothly pieces blend into each other", 0, 100,
             high=1000, lower="harder cuts between pieces", higher="smoother blends"),
    ),
    "blur-v1": (
        Dial("strength", "strength", "how strong the blur is", 0.5, 0.15,
             lower="subtler blur", higher="stronger blur"),
        Dial("reach", "reach", "how far across the picture the blur scatters", 0.0, 0.25,
             lower="a soft, local blur", higher="scattered across the whole picture"),
    ),
    "telablur-v1": (
        Dial("strength", "strength", "how far the first image morphs into the second", 0.5, 0.15,
             lower="closer to the first image", higher="closer to the second image"),
    ),
}


def build_questions(engine: str) -> dict:
    """Per dial: did the feedback ask to change it; then which way and how much, or to what."""
    questions = {}
    for dial in DIALS[engine]:
        said = f" People may say it casually, e.g. {dial.says}." if dial.says else ""
        # Naming both concrete ends ("narrower, closer to mono" / "wider stereo") made the
        # local model much surer than the description alone (16/16 vs 13/16 on test phrasings).
        ends = (f", either making it {dial.lower} or making it {dial.higher}" if dial.lower
                else "")
        questions[f"{dial.name}?"] = {"type": "noul", "instructions": ask(
            f"Does the feedback ask to change {dial.about}{ends}?{said} "
            "Answer no if it asks for neither.", "feedback")}
        if dial.options:
            questions[dial.name] = {"type": "choice", "instructions": ask(
                f"After this feedback, which should it be: {dial.about}?", "feedback"),
                "criteria": dial.options}
            continue
        questions[f"{dial.name}.way"] = {"type": "choice", "instructions": ask(
            f"Which way does the feedback want this to go: {dial.about}?", "feedback"),
            "criteria": {"lower": dial.lower, "higher": dial.higher}}
        questions[f"{dial.name}.size"] = {"type": "score", "instructions": ask(
            f"How big a change does the feedback ask for in {dial.about}?", "feedback"),
            "criteria": SIZE_LEVELS}
    return questions


def _size(answer: dict) -> float:
    """0 = a little, 1 = a lot (probability-weighted)."""
    return answer["score"] / (len(answer["legend"]) - 1)


def _moved(dial: Dial, now: float, way: str, size: float) -> float:
    """One step for "a little", two for "a lot", clamped to the schema's range."""
    steps = (1 + size) * (1 if way == "higher" else -1)
    value = min(max(now + steps * dial.step, dial.low), dial.high)
    return round(value) if isinstance(dial.default, int) else round(value, 2)


def plan_refinement(answers: dict, last: dict) -> tuple[Plan, dict, dict]:
    """Return (plan, input files, changes {param: [old, new]}) for the next job."""
    engine, params = last["engine"], dict(last["params"])
    read = Reader(answers)
    changes: dict = {}
    for dial in DIALS[engine]:
        if not read.stated(dial.name):
            continue
        now = params.get(dial.param, dial.default)
        # Size ("a little" or "a lot") does not count toward confidence: a wrong guess is one
        # step off, and the next refine fixes it.
        new = read.choice(dial.name) if dial.options else _moved(
            dial, now, read.choice(f"{dial.name}.way"), _size(answers[f"{dial.name}.size"]))
        if new != now:
            changes[dial.param] = [now, new]
            params[dial.param] = new
    if not changes:
        names = ", ".join(d.name for d in DIALS[engine])
        return (Plan(None, confidence=read.confidence(), reasons=(
            f"The feedback did not ask to change anything adjustable ({names}).",)), {}, {})

    reasons: list[str] = []
    files = dict(last["inputs"])
    if engine == "retrocausal-echo-v1":
        if params.get("negative_mode") == "phase" and params.get("theta_z", 0) <= 0:
            changes["theta_z"] = [params.get("theta_z", 0), PHASE_THETA_Z]
            params["theta_z"] = PHASE_THETA_Z
        measured = {d.param for d in ECHO if d.measured} | {"theta_z"}
        if not measured & set(changes) and "ir" in last["outputs"]:
            files["ir"] = last["outputs"]["ir"]
        else:
            files.pop("ir", None)
            reasons.append("This change needs a new quantum measurement; the previous IR "
                           "cannot be reused.")
    return Plan(engine, params, (), read.confidence(), tuple(reasons)), files, changes


def main() -> None:
    p = argparse.ArgumentParser(description="Refine the last Moth run from feedback")
    p.add_argument("feedback")
    p.add_argument("--out", default="outputs", help="folder holding session.json")
    p.add_argument("--run", action="store_true", help="submit the job (spends Moth credits)")
    p.add_argument("--yes", action="store_true", help="run even if the plan needs confirmation")
    args = p.parse_args()

    session = Path(args.out) / SESSION_FILE
    if not session.exists():
        sys.exit(f"no {session}: run a router with --run first")
    last = load_session(session)["history"][-1]
    if last["engine"] not in DIALS:
        sys.exit(f"refining {last['engine']} is not supported yet")
    # Only the feedback: changes are relative, and code applies them to the current values.
    # Listing every setting's value in the state made the local model pick wrong directions.
    state = {"feedback": args.feedback}
    plan, files, changes = plan_refinement(judge(state, build_questions(last["engine"])), last)
    finish(plan, files, run=args.run, yes=args.yes, out=args.out, request=args.feedback,
           extra={"changes": changes, "previous_job": last["job_id"],
                  "reuses_measurement": "ir" in files})


if __name__ == "__main__":
    main()
