#!/usr/bin/env python3
"""Labelled evaluation of both routers against a live judge (no Moth credits spent).

Not collected by pytest: it calls TypeSafe or an LLM. Run, for example:

    JUDGE=local JUDGE_MODEL=Qwen3.6-35B-A3B-MTP python tests/eval_routers.py
    JUDGE=typesafe python tests/eval_routers.py

Each case states the engine the request should reach and a few checks on the plan.
Use it to compare judges and models, and to re-tune CONFIRM_BELOW.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import typesafe_music_router as music  # noqa: E402
import typesafe_refine as refine  # noqa: E402
import typesafe_router as image  # noqa: E402
from typesafe_core import judge  # noqa: E402

# (request, inputs, expected engine, checks). Checks: param -> ("<=", x) | (">=", x) | ("==", x)
# | ("absent", None); "mask" -> missing slot expected; "reason" -> substring in a reason.
IMAGE_CASES = [
    ("make this photo dreamy but keep the face sharp", 1, "blur-v1",
     {"reach": ("absent", None), "mask": True}),
    ("give it a really heavy quantum blur that scatters detail all over the picture", 1, "blur-v1",
     {"strength": (">=", 0.6), "reach": (">=", 0.6)}),
    ("just a subtle blur please", 1, "blur-v1", {"strength": ("<=", 0.4)}),
    ("melt my cat photo into the dog photo, top to bottom", 2, "telablur-v1",
     {"direction": ("==", "vertical")}),
    ("deep fry this meme to the max", 1, "deep-fryer-v1", {"gates": (">=", 0.75)}),
    ("deep fry it, but only a little", 1, "deep-fryer-v1", {"gates": ("<=", 0.4)}),
    ("show me what real quantum hardware noise does to the colours of my picture", 1,
     "tessa-image-v1", {"machine": ("==", "fake_fez")}),
    ("run my picture through an actual IBM quantum computer", 1, "tessa-image-v1",
     {"machine": ("==", "least_busy"), "reason": "real IBM"}),
    ("what's the weather tomorrow?", 0, None, {}),
]
MUSIC_CASES = [
    ("make my melody jazzier, just a little", {"midi": True}, "blur-midi-v1",
     {"strength": ("<=", 0.4)}),
    ("blur only the piano track between 8 and 16 seconds", {"midi": True}, "blur-midi-v1",
     {"reason": "--section"}),
    ("write a new tune in the style of this MIDI, staying close to the original",
     {"midi": True}, "qrc-midi-v1", {"variation": ("<=", 0.7)}),
    ("chop this loop up and rearrange it into something wild", {"audio": True}, "qrc-audio-v1",
     {"variation": (">=", 1.3)}),
    ("eerie backwards echoes on the beat", {"audio": True, "bpm": 96}, "retrocausal-echo-v1",
     {"negative_mode": ("==", "reverse"), "bpm": ("==", 96)}),
    ("add a subtle echo, mostly the dry vocal", {"audio": True}, "retrocausal-echo-v1",
     {"mix": ("<=", 0.4)}),
    ("swirling, phasey echoes on my guitar", {"audio": True}, "retrocausal-echo-v1",
     {"negative_mode": ("==", "phase")}),
    ("just give me the echo timing map for my DAW", {}, "retrocausal-echo-v1",
     {"emit": ("==", "map")}),
    ("tell me a joke", {}, None, {}),
]


# Refine: feedback on the echo run → expected changes ("up" / "down" / a value) and whether
# the measured IR is reused. Any other changed setting fails the case.
LAST_ECHO = {"engine": "retrocausal-echo-v1",
             "params": {"theta_x": 1.98, "negative_mode": "reverse", "bpm": 96.0},
             "inputs": {"audio": "audio-asset"}, "job_id": "job-1",
             "outputs": {"result": "wav", "ir": "ir-asset", "taps": "taps"}}
REFINE_CASES = [
    ("less backwards, more of the dry voice", {"mix": "down", "negative_mode": "invert"}, True),
    ("make the echoes stranger", {"theta_x": "up"}, False),
    ("longer tails and let them keep repeating", {"feedback": "up", "decay": "up"}, True),
    ("narrower, almost mono", {"stereo_width": "down"}, True),
    ("swirly phasey echoes instead", {"negative_mode": "phase", "theta_z": "up"}, False),
    ("sounds great, thanks", {}, None),
    # phrasings not used while wording the questions
    ("can you pull the echoes back a bit", {"mix": "down"}, True),
    ("collapse it to the centre", {"stereo_width": "down"}, True),
    ("tighter, the echoes die out too slowly", {"decay": "down"}, True),
    ("tame the chaos, make it more predictable", {"theta_x": "down"}, False),
    ("more repeats please", {"feedback": "up"}, True),
    ("play the echoes forwards again", {"negative_mode": "invert"}, True),
]


def run_refine() -> tuple[int, float]:
    passed, seconds = 0, 0.0
    for feedback, want, reuse in REFINE_CASES:
        started = time.monotonic()
        answers = judge({"feedback": feedback}, refine.build_questions(LAST_ECHO["engine"]))
        seconds += time.monotonic() - started
        plan, files, changes = refine.plan_refinement(answers, LAST_ECHO)
        problems = [f"changed {sorted(set(changes) - set(want))}"] if set(changes) - set(want) else []
        for param, rule in want.items():
            if param not in changes:
                problems.append(f"{param} unchanged")
                continue
            old, new = changes[param]
            ok = {"up": new > old, "down": new < old}.get(rule, new == rule)
            if not ok:
                problems.append(f"{param} {old}->{new}, wanted {rule}")
        if reuse is not None and ("ir" in files) != reuse:
            problems.append(f"reuses IR: {'ir' in files}, wanted {reuse}")
        passed += not problems
        mark = "PASS" if not problems else "FAIL"
        print(f"  {mark}  conf {plan.confidence:.2f}  {feedback[:58]:<58}  {'; '.join(problems)}")
    return passed, seconds


def _value(plan, key):
    value = plan.params.get(key)
    return value[0][1] if key == "gates" and value else value


def check(plan, engine, checks) -> list[str]:
    problems = [] if plan.engine == engine else [f"engine {plan.engine} != {engine}"]
    if plan.engine != engine:
        return problems
    for key, rule in checks.items():
        if key == "mask":
            if "mask" not in plan.missing_slots:
                problems.append("mask not requested")
        elif key == "reason":
            if not any(rule in r for r in plan.reasons):
                problems.append(f"no reason mentioning {rule!r}")
        else:
            op, want = rule
            got = _value(plan, key)
            ok = {"absent": got is None, "==": got == want,
                  "<=": got is not None and got <= want, ">=": got is not None and got >= want}[op]
            if not ok:
                problems.append(f"{key}={got!r}, wanted {op} {want!r}")
    return problems


def run(cases, build, plan_fn, label: str) -> tuple[int, float, list[float]]:
    passed, seconds, confidences = 0, 0.0, []
    for request, inputs, engine, checks in cases:
        state = ({"request": request, "attached_images": inputs} if label == "image" else
                 {"request": request, "attached": {"midi": bool(inputs.get("midi")),
                                                    "audio": bool(inputs.get("audio"))}})
        started = time.monotonic()
        answers = judge(state, build())
        seconds += time.monotonic() - started
        plan = plan_fn(answers, inputs)
        problems = check(plan, engine, checks)
        passed += not problems
        confidences.append(plan.confidence)
        mark = "PASS" if not problems else "FAIL"
        print(f"  {mark}  conf {plan.confidence:.2f}  {request[:58]:<58}  {'; '.join(problems)}")
    return passed, seconds, confidences


def main() -> None:
    print("image router")
    ip, its, _ = run(IMAGE_CASES, image.build_questions,
                     lambda a, n: image.plan_from_answers(a, n, False), "image")
    print("music router")
    mp, mts, _ = run(MUSIC_CASES, music.build_questions,
                     lambda a, i: music.plan_from_answers(
                         a, has_midi=bool(i.get("midi")), has_audio=bool(i.get("audio")),
                         bpm=i.get("bpm")), "music")
    print("refine (echo feedback)")
    rp, rts = run_refine()
    total = len(IMAGE_CASES) + len(MUSIC_CASES) + len(REFINE_CASES)
    print(f"\n{ip + mp + rp}/{total} passed · image {ip}/{len(IMAGE_CASES)} · music "
          f"{mp}/{len(MUSIC_CASES)} · refine {rp}/{len(REFINE_CASES)} · "
          f"{(its + mts + rts) / total:.1f} s per request")


if __name__ == "__main__":
    main()
