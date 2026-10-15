#!/usr/bin/env python3
"""Recolour a YuE2 score's chords with quantum-chip noise from qpixl-v1 (simulated or real IBM).

Each chord becomes 12 pitch-class strengths (root 1.0, other tones 0.5, absent 0). qpixl-v1
loads them into qubits and reads them back; the hardware's noise shifts the strengths a little.
The noisy profiles then go through yue2_harmony's change dial, so only the chords the noise
points to change and the melody stays exact.

    python yue2_chip_noise.py values score.abc values.json
    python yue2_chip_noise.py run values.json result.json --mode emu --machine fake_fez
    python yue2_chip_noise.py run values.json result.json --mode qpu --backend ibm_fez
    python yue2_chip_noise.py apply score.abc values.json result.json out.abc --change 25%

`run` costs 1 credit per job and needs MOTH_API_KEY; a real-chip job can queue for hours.
Uses YuE2's ABC tools via yue2_harmony (YUE2_DIR, default ~/projects/YuE). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yue2_harmony as h  # noqa: E402
from midi_io import Note  # noqa: E402

FAKE_FEZ_CAPACITY = 448       # values per qpixl-v1 job on fake_fez (larger jobs fail: insufficient_qubits)
WAIT_SECONDS = 5 * 3600       # the engine gives up on a queued real-chip job after 18000 s
PROFILE_PITCH = 60            # noisy profiles are voiced as one block of notes from middle C


def profile(chord: h.Chord) -> list[float]:
    """12 strengths from the chord's own voicing, normalised so the strongest (root) is 1."""
    weights, _ = h.window_weights(h.voice_chord(chord), float(chord.onset), float(chord.end))
    top = max(weights)
    return [round(w / top, 4) for w in weights]


def encode(chords: list[h.Chord], encoding: str = "auto",
           capacity: int = FAKE_FEZ_CAPACITY) -> dict:
    """{"chords": labels, "values": 12 per label}: one per occurrence, or one per distinct symbol.

    "auto" sends every occurrence when it fits in `capacity`, else each distinct chord once
    (all occurrences of a chord then share the same noise)."""
    if encoding == "auto":
        encoding = "occurrence" if 12 * len(chords) <= capacity else "distinct"
    if encoding == "distinct":
        firsts = list({c.symbol: c for c in chords}.values())
        return {"chords": [c.symbol for c in firsts], "values": [v for c in firsts for v in profile(c)]}
    return {"chords": [c.symbol for c in chords], "values": [v for c in chords for v in profile(c)]}


def read_output(data: dict) -> list[float]:
    """qpixl-v1's output list from a saved result, however deeply it is wrapped in "result"."""
    while isinstance(data, dict) and "output" not in data:
        data = data.get("result")
    if not isinstance(data, dict):
        raise ValueError("no qpixl-v1 output in the result file")
    return data["output"]


def noisy_profiles(sent: dict, output: list[float]) -> dict | list:
    """{symbol: 12 values} for a distinct encoding, else a list in chord order."""
    if len(output) != len(sent["values"]):
        raise ValueError(f"sent {len(sent['values'])} values but got {len(output)} back")
    rows = [output[12 * i:12 * i + 12] for i in range(len(sent["chords"]))]
    if len(set(sent["chords"])) == len(sent["chords"]):
        return dict(zip(sent["chords"], rows))
    return rows


def to_notes(chords: list[h.Chord], profiles: dict | list) -> list[Note]:
    """One note per pitch class with strength as velocity, spanning each chord."""
    notes = []
    for i, c in enumerate(chords):
        row = profiles[c.symbol] if isinstance(profiles, dict) else profiles[i]
        for pc, v in enumerate(row):
            if v > 0:
                notes.append(Note(float(c.onset), PROFILE_PITCH + pc, float(c.end - c.onset),
                                  max(1, round(127 * min(v, 1.0)))))
    return notes


def run_on_moth(values: list[float], params: dict) -> dict:
    import moth
    job = moth.submit("qpixl-v1", {**params, "values": values})
    print(f"job {job}", file=sys.stderr)
    status = moth.wait(job, timeout=WAIT_SECONDS)
    result = moth.result(job) if status["status"] == "completed" else None
    return {"job_id": job, "status": status, "result": result}


def main() -> None:
    p = argparse.ArgumentParser(description="Recolour a YuE2 score's chords with chip noise")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("values", help="write the chord profiles to send to qpixl-v1")
    v.add_argument("score")
    v.add_argument("out")
    v.add_argument("--encoding", choices=("auto", "occurrence", "distinct"), default="auto",
                   help=f"auto: every chord if it fits in {FAKE_FEZ_CAPACITY} values, else each "
                        "distinct chord once")
    r = sub.add_parser("run", help="send the profiles through qpixl-v1 on Moth (1 credit)")
    r.add_argument("values")
    r.add_argument("out")
    r.add_argument("--mode", choices=("emu", "qpu"), default="emu")
    r.add_argument("--machine", default="fake_fez", help="emu: aer or fake_<chip>")
    r.add_argument("--backend", default="ibm_fez", help="qpu: the IBM backend")
    a = sub.add_parser("apply", help="rename chords from the noisy profiles")
    a.add_argument("score")
    a.add_argument("values")
    a.add_argument("result")
    a.add_argument("out")
    a.add_argument("--change", type=h.parse_fraction, default=0.25, metavar="PERCENT",
                   help="change at most this share of the chords (default 25%%)")
    args = p.parse_args()

    if args.cmd == "values":
        chords = h.chord_timeline(h.abc_tools.parse_abc(Path(args.score).read_text()))
        sent = encode(chords, args.encoding)
        Path(args.out).write_text(json.dumps(sent))
        print(f"{len(sent['values'])} values for {len(sent['chords'])} chords", file=sys.stderr)
        return
    if args.cmd == "run":
        out = Path(args.out)
        if out.exists():
            sys.exit(f"{out} exists; choose a new file")
        params = ({"mode": "emu", "machine": args.machine} if args.mode == "emu"
                  else {"mode": "qpu", "backend_name": args.backend})
        saved = run_on_moth(json.loads(Path(args.values).read_text())["values"], params)
        out.write_text(json.dumps(saved, indent=1))
        print(f"{saved['status']['status']}; wrote {out}", file=sys.stderr)
        if saved["result"] is None:
            sys.exit(1)
        return

    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists; choose a new file")
    text = Path(args.score).read_text()
    chords = h.chord_timeline(h.abc_tools.parse_abc(text))
    sent = json.loads(Path(args.values).read_text())
    profiles = noisy_profiles(sent, read_output(json.loads(Path(args.result).read_text())))
    edited, changes = h.edit_score(text, to_notes(chords, profiles), change=args.change)
    out.write_text(edited)
    for old, new in changes:
        print(f"{old:>8} → {new}" + ("" if old != new else "   (unchanged)"))
    changed = sum(old != new for old, new in changes)
    print(f"{changed} of {len(changes)} chords changed", file=sys.stderr)
    print(f"wrote {out}; melody, bars and tempo verified unchanged", file=sys.stderr)


if __name__ == "__main__":
    main()
