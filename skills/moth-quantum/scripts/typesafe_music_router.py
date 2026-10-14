#!/usr/bin/env python3
"""Route a plain-language music request to a Moth MIDI or audio engine with TypeSafe.

Same design as typesafe_router.py: one TypeSafe request with every question, code
reads the chosen engine's answers, unmentioned settings keep engine defaults.
Free numbers (BPM, length, a time range) are not closed sets, so they come from
flags rather than from the model.

    python typesafe_music_router.py "make my melody jazzier, just a little" --midi tune.mid
    python typesafe_music_router.py "eerie backwards echoes on the beat" --audio vox.wav --bpm 96
    python typesafe_music_router.py "remix this loop into 32 loose pieces" --audio loop.wav \
        --length 32 --run

Judge: TypeSafe (TYPESAFE_API_KEY) or a local LLM (JUDGE=local, see local_judge.py).
MOTH_API_KEY is needed for --run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from typesafe_core import Plan, Reader, ask, finish, judge  # noqa: E402

ENGINES = {
    "blur-midi-v1": "Blur the notes of a MIDI file or one of its tracks: notes bleed into "
                    "neighbouring pitches and moments, adding jazzy passing notes",
    "qrc-midi-v1": "Learn a MIDI melody and generate a new melody or variation in its style",
    "retrocausal-echo-v1": "Add an echo, delay or reverb-like repeats to an audio recording",
    "qrc-audio-v1": "Cut an audio recording into pieces and rearrange or remix them "
                    "into a new arrangement",
    "none": "The request is not about changing or generating music at all, for example an "
            "unrelated question",
}
STRENGTH_LEVELS = ["barely noticeable", "subtle", "clearly audible", "strong",
                   "as strong as possible"]
REACH_LEVELS = ["only neighbouring notes mix", "notes spread part of the way across the range",
                "any pitch can mix with any other"]
VARIATION_LEVELS = ["stick very close to the original order", "some variation",
                    "loose and surprising"]
ECHO_CHARACTER_LEVELS = ["clean, regular repeats", "some repeats flip and fade",
                         "strange: inverted, fading and vanishing repeats"]
MIX_LEVELS = ["a faint touch of effect", "balanced with the original", "mostly effect"]
# Example anchors, one value per level; listen and retune them for your material.
VARIATION_ANCHORS = (0.3, 1.0, 2.0)          # schema default 1, no maximum
THETA_X_ANCHORS = (0.3, 0.94, 2.0)           # radians, 0..π; default ≈ 0.94
MIX_ANCHORS = (0.2, 0.6, 0.9)                # 0..1; default 0.6
PHASE_THETA_Z = 0.79                         # negative_mode "phase" needs theta_z > 0
DIVISIONS = {"0.25": "sixteenth notes", "0.5": "eighth notes", "0.75": "dotted eighth notes",
             "0.3333": "eighth-note triplets", "1.0": "quarter notes"}
NEEDS = {"blur-midi-v1": "midi", "qrc-midi-v1": "midi", "qrc-audio-v1": "audio"}


def _scale(qid: str, question: str, levels: list[str], said: str) -> dict:
    """A Score plus its "did the user say anything about it?" Noul."""
    return {qid: {"type": "score", "instructions": ask(question), "criteria": levels},
            f"{qid}?": {"type": "noul", "instructions": ask(said)}}


def build_questions() -> dict:
    """Every question, for every music engine, in one request."""
    return {
        "engine": {"type": "choice", "instructions": ask(
            "Which music effect or generator is the user asking for?"), "criteria": ENGINES},
        **_scale("strength", "How strong should the blur of the notes be?", STRENGTH_LEVELS,
                 "Does the user say how strong, subtle or heavy the change should be?"),
        **_scale("reach", "How far apart can notes be and still blend into each other?",
                 REACH_LEVELS,
                 "Does the user say whether only nearby notes should blend or notes far apart too?"),
        **_scale("variation", "How freely should the result depart from the original?",
                 VARIATION_LEVELS,
                 "Does the user say how faithful, repetitive, loose or surprising it should be?"),
        "quality": {"type": "choice", "instructions": ask(
            "How carefully should the model learn the material before generating?"),
            "criteria": {"instant": "no learning at all, a quick random shuffle",
                         "fast": "quick and rough", "moderate": "a reasonable balance",
                         "complete": "slow and careful, the most faithful"}},
        "quality?": {"type": "noul", "instructions": ask(
            "Does the user say anything about speed versus care, such as quick draft or best quality?")},
        **_scale("character", "What character should the echoes have?", ECHO_CHARACTER_LEVELS,
                 "Does the user describe the character of the echoes, such as clean or strange?"),
        **_scale("mix", "How much of the effect versus the original sound should be heard?",
                 MIX_LEVELS, "Does the user say how loud the effect should be against the original?"),
        "negative_mode": {"type": "choice", "instructions": ask(
            "What should the unusual, negative echoes sound like?"),
            "criteria": {"invert": "flipped echoes that partly cancel the sound",
                         "reverse": "echoes played backwards",
                         "phase": "echoes that swirl or shift in phase"}},
        "negative_mode?": {"type": "noul", "instructions": ask(
            "Does the user ask for backwards, swirling, phasey or cancelling echoes?")},
        "division": {"type": "choice", "instructions": ask(
            "Which note length should the echoes follow?"), "criteria": DIVISIONS},
        "division?": {"type": "noul", "instructions": ask(
            "Does the user name a note length for the echoes, such as eighths or triplets?")},
        "on_beat": {"type": "noul", "instructions": ask(
            "Does the user want the echoes to land in time with the beat or tempo?")},
        "map_only": {"type": "noul", "instructions": ask(
            "Does the user want only the echo timing data for a DAW or plug-in, not rendered audio?")},
        "partial": {"type": "noul", "instructions": ask(
            "Does the user want only part of the piece changed, such as one track or section?")},
        "real_hardware": {"type": "noul", "instructions": ask(
            "Does the user explicitly ask for the job itself to run on a real quantum computer? "
            "Wanting to see or imitate hardware noise is not enough.")},
    }


def _blur_midi(read: Reader, mask: list | None, reasons: list[str]) -> dict:
    params: dict = {}
    if read.stated("strength"):
        params["strength"] = read.unit("strength")
    if read.stated("reach"):
        params["reach"] = read.unit("reach")
    if mask:
        params["mask"] = mask
    elif read.peek("partial"):
        reasons.append("Only part of the piece should change: pass --section "
                       "TRACK:START:END (seconds), e.g. --section 1:8:16.")
    return params


def _qrc(read: Reader, engine: str, length: int | None, bpm: float | None) -> dict:
    params: dict = {}
    if read.stated("variation"):
        params["variation"] = read.along("variation", VARIATION_ANCHORS)
    if read.stated("quality"):
        params["quality"] = read.choice("quality")
    if length:
        params["length"] = length
    if bpm and engine == "qrc-midi-v1":
        params["bpm"] = bpm
    if bpm and engine == "qrc-audio-v1":
        params["chunk_seconds"] = round(240 / bpm, 3)   # one 4/4 bar per chunk
    return params


def _echo(read: Reader, bpm: float | None, reasons: list[str]) -> dict:
    params: dict = {}
    if read.stated("character"):
        params["theta_x"] = read.along("character", THETA_X_ANCHORS)
    if read.stated("mix"):
        params["mix"] = read.along("mix", MIX_ANCHORS)
    if read.stated("negative_mode"):
        params["negative_mode"] = read.choice("negative_mode")
        if params["negative_mode"] == "phase":
            params["theta_z"] = PHASE_THETA_Z
    if read.peek("on_beat"):
        if bpm:
            params["bpm"] = bpm
            if read.stated("division"):
                params["division"] = float(read.choice("division"))
        else:
            reasons.append("Echoes should follow the beat: pass --bpm with the song's tempo.")
    if read.peek("map_only"):
        params["emit"] = "map"
    return params


def plan_from_answers(answers: dict, *, has_midi: bool, has_audio: bool,
                      length: int | None = None, bpm: float | None = None,
                      mask: list | None = None) -> Plan:
    """Turn TypeSafe answers into Moth params for the chosen music engine."""
    read = Reader(answers)
    engine = read.choice("engine")
    if engine == "none":
        return Plan(None, confidence=read.confidence(),
                    reasons=("No music effect fits this request.",))

    reasons: list[str] = []
    if engine == "blur-midi-v1":
        params = _blur_midi(read, mask, reasons)
    elif engine == "retrocausal-echo-v1":
        params = _echo(read, bpm, reasons)
        if not has_audio and params.get("emit") != "map":
            reasons.append("No audio given: the engine will render its own impulse response.")
    else:
        params = _qrc(read, engine, length, bpm)

    have = {"midi": has_midi, "audio": has_audio}
    missing = [NEEDS[engine]] if engine in NEEDS and not have[NEEDS[engine]] else []
    if read.peek("real_hardware"):
        reasons.append("Real quantum hardware was requested: confirm the cost and pick an "
                       "IBM backend; this plan stays on the simulator.")
    return Plan(engine, params, tuple(missing), read.confidence(), tuple(reasons))


def slot_files(plan: Plan, midi: str | None, audio: str | None) -> dict[str, str]:
    slot = NEEDS.get(plan.engine, "audio")
    path = midi if slot == "midi" else audio
    return {slot: path} if path else {}


def parse_section(spec: str) -> list:
    """TRACK:START:END → [track, start_s, end_s], blur-midi-v1's mask entry."""
    try:
        track, start, end = spec.split(":")
        return [int(track), float(start), float(end)]
    except ValueError:
        sys.exit(f"--section expects TRACK:START:END in seconds, got {spec!r}")


def main() -> None:
    p = argparse.ArgumentParser(description="Route a music request to a Moth engine via TypeSafe")
    p.add_argument("request")
    p.add_argument("--midi", help="MIDI file")
    p.add_argument("--audio", help="audio file (WAV, MP3, OGG, FLAC)")
    p.add_argument("--bpm", type=float, help="song tempo, for beat-synced echoes or MIDI output")
    p.add_argument("--length", type=int, help="notes or chunks to generate (≤ 500)")
    p.add_argument("--section", action="append", default=[],
                   help="TRACK:START:END seconds to blur (repeatable)")
    p.add_argument("--run", action="store_true", help="submit the job (spends Moth credits)")
    p.add_argument("--yes", action="store_true", help="run even if the plan needs confirmation")
    p.add_argument("--out", default="outputs")
    args = p.parse_args()

    state = {"request": args.request,
             "attached": {"midi": bool(args.midi), "audio": bool(args.audio)}}
    mask = [parse_section(s) for s in args.section] or None
    plan = plan_from_answers(judge(state, build_questions()),
                             has_midi=bool(args.midi), has_audio=bool(args.audio),
                             length=args.length, bpm=args.bpm, mask=mask)
    files = slot_files(plan, args.midi, args.audio) if plan.engine else {}
    finish(plan, files, run=args.run, yes=args.yes, out=args.out, request=args.request)


if __name__ == "__main__":
    main()
