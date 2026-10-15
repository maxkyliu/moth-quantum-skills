#!/usr/bin/env python3
"""Quantum-blur the harmony of a YuE2 score with blur-midi-v1, keeping the melody exact.

YuE2 scores are lead sheets: a Vocal and an Ins melody plus one chord symbol per harmonic
change. This tool turns the chord symbols into a block-chord MIDI track, blurs it on Moth,
reads the blurred notes back into the closest chord YuE2 accepts, and swaps only the text
inside the chord quotes, so every note, onset and bar stays identical (checked with YuE2's
own compare). Render the edited score with YuE2 (`--abc-file edited.abc --cot full`).

    python yue2_harmony.py chords score.abc
    python yue2_harmony.py blur score.abc edited.abc --params '{"strength": 0.5}'

Uses YuE2's ABC tools from a YuE checkout (YUE2_DIR, default ~/projects/YuE).
MOTH_API_KEY is needed for `blur` (1 credit per run). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from midi_io import Note, read_midi, write_midi  # noqa: E402

YUE2_DIR = Path(os.environ.get("YUE2_DIR", Path.home() / "projects" / "YuE"))
sys.path.insert(0, str(YUE2_DIR / "skills" / "yue2-music" / "scripts"))
try:
    import abc_tools  # noqa: E402  (YuE2's own parser: the authority on its dialect)
except ImportError:
    sys.exit(f"YuE2's abc_tools.py not found under {YUE2_DIR}; set YUE2_DIR to a YuE checkout")

# Intervals for every chord quality YuE2's dialect accepts (abc_tools.QUALITIES).
INTERVALS = {"": (0, 4, 7), "m": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
             "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
             "dim7": (0, 3, 6, 9), "m7b5": (0, 3, 6, 10), "sus4": (0, 5, 7),
             "sus2": (0, 2, 7), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9),
             "7sus4": (0, 5, 7, 10), "m(maj7)": (0, 3, 7, 11)}
assert set(INTERVALS) == set(abc_tools.QUALITIES)
SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
SYMBOL = re.compile(r"(?P<root>[A-G](?:bb|##|b|#)?)(?P<quality>.*?)(?:/(?P<bass>[A-G](?:bb|##|b|#)?))?")
BASS_OCTAVE, CHORD_OCTAVE, VELOCITY = 48, 60, 80
KEEP_ROOT_BONUS = 0.02        # prefer the original root when two names fit almost equally
KEEP_MARGIN = 0.05            # the original chord stays unless another fits clearly better:
                              # seventh chords share three tones, so a light blur tipped them
EXTRA_TONE_COST = 0.01        # prefer triads unless the extra tone is really there
OUT_OF_KEY = 0.2              # palette "key": weight kept by notes outside the key's scale
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
BASS_FLOOR = 0.5              # fallback without a bass register: lowest pitch at least half
                              # as strong as the strongest


@dataclass(frozen=True)
class Chord:
    onset: Fraction
    end: Fraction
    symbol: str


def pitch_class(name: str) -> int:
    pc = "C D EF G A B".index(name[0])
    return (pc + name.count("#") - name.count("b")) % 12


def parse_symbol(symbol: str) -> tuple[int, str, int | None]:
    match = SYMBOL.fullmatch(symbol)
    if not match or match["quality"] not in INTERVALS:
        raise ValueError(f"unsupported chord symbol {symbol!r}")
    bass = pitch_class(match["bass"]) if match["bass"] else None
    return pitch_class(match["root"]), match["quality"], bass


def chord_timeline(score) -> list[Chord]:
    """Chord symbols with the span each one governs, until the next or the score's end."""
    vocal = score.voices["Vocal"]
    start, length, _ = vocal.bars[-1]
    total = start + length
    events = vocal.chords
    return [Chord(onset, events[i + 1][0] if i + 1 < len(events) else total, symbol)
            for i, (onset, symbol) in enumerate(events)]


def voice_chord(chord: Chord) -> list[Note]:
    """Bass note in octave 3 plus close-position chord tones above middle C."""
    root, quality, bass = parse_symbol(chord.symbol)
    onset, duration = float(chord.onset), float(chord.end - chord.onset)
    pitches = [BASS_OCTAVE + (root if bass is None else bass)]
    pitches += sorted(CHORD_OCTAVE + (root + i) % 12 for i in INTERVALS[quality])
    return [Note(onset, p, duration, VELOCITY) for p in pitches]


def chords_to_notes(chords: list[Chord]) -> list[Note]:
    return [note for chord in chords for note in voice_chord(chord)]


def window_weights(notes: list[Note], start: float, end: float) -> tuple[list[float], dict]:
    """Pitch-class weights (overlap × velocity) and per-pitch weights inside [start, end)."""
    weights, by_pitch = [0.0] * 12, {}
    for n in notes:
        overlap = min(n.onset + n.duration, end) - max(n.onset, start)
        if overlap > 0:
            w = overlap * n.velocity / 127
            weights[n.pitch % 12] += w
            by_pitch[n.pitch] = by_pitch.get(n.pitch, 0.0) + w
    return weights, by_pitch


def _cosine(weights: list[float], tones: set[int]) -> float:
    norm = math.sqrt(sum(w * w for w in weights)) * math.sqrt(len(tones))
    return sum(weights[pc] for pc in tones) / norm if norm else 0.0


def key_scale(key: str) -> set[int]:
    """Pitch classes of a key's scale (natural minor shares its relative major's notes)."""
    tonic = (7 * abc_tools.KEYS[key]) % 12
    return {(tonic + i) % 12 for i in MAJOR_SCALE}


def bass_pitch_class(by_pitch: dict) -> int:
    """The strongest pitch class in the bass register (below the chord octave).

    Blur leaks the bass note to its semitone neighbours; the lowest-pitch rule turned that
    leak into a slash on nearly every chord (Amaj7 → Amaj7/G#)."""
    register = [0.0] * 12
    for pitch, w in by_pitch.items():
        if pitch < CHORD_OCTAVE:
            register[pitch % 12] += w
    if any(register):
        return max(range(12), key=lambda pc: register[pc])
    strongest = max(by_pitch.values())
    return min(p for p, w in by_pitch.items() if w >= BASS_FLOOR * strongest) % 12


def name_chord(weights: list[float], by_pitch: dict, original: str, flats: bool,
               scale: set[int] | None = None, new_slashes: bool = False) -> str:
    """The YuE2 chord whose tones best match the weights; slash bass if the bass is strong.

    With a `scale`, notes outside it count for less and new chords must fit the scale
    (the original chord is always allowed), so blur moves harmony between chords of the key.
    """
    if not any(weights):
        return original
    old_root, old_quality, old_bass = parse_symbol(original)
    if scale is not None:
        weights = [w if pc in scale else w * OUT_OF_KEY for pc, w in enumerate(weights)]

    def allowed(r: int, q: str) -> bool:
        tones = {(r + i) % 12 for i in INTERVALS[q]}
        return scale is None or tones <= scale or (r, q) == (old_root, old_quality)

    best = max(((_cosine(weights, {(r + i) % 12 for i in iv})
                 + (KEEP_ROOT_BONUS if r == old_root else 0)
                 + (KEEP_MARGIN if (r, q) == (old_root, old_quality) else 0)
                 - EXTRA_TONE_COST * (len(iv) - 3), r, q)
                for r in range(12) for q, iv in INTERVALS.items() if allowed(r, q)))
    _, root, quality = best
    names = FLATS if flats else SHARPS
    symbol = names[root] + quality
    bass = bass_pitch_class(by_pitch)
    tones = {(root + i) % 12 for i in INTERVALS[quality]}
    # YuE2 mostly ignores slash basses (tested 2026-10-14), and blur leaks the bass to its
    # neighbours, so a slash is kept only where the original had the same bass, unless asked.
    if bass != root and bass in tones and (scale is None or bass in scale) and (
            new_slashes or bass == old_bass):
        symbol += "/" + names[bass]
    return symbol


def rename(chords: list[Chord], notes: list[Note], flats: bool,
           scale: set[int] | None = None, new_slashes: bool = False) -> list[str]:
    return [name_chord(*window_weights(notes, float(c.onset), float(c.end)), c.symbol, flats,
                       scale, new_slashes) for c in chords]


def replace_chords(text: str, score, symbols: list[str]) -> str:
    """Swap the quoted chord symbols in Vocal music lines, in order; nothing else changes."""
    new = iter(symbols)
    lines = text.splitlines()
    for index, voice in score.music_lines.items():
        if voice == "Vocal":
            lines[index] = re.sub(r'"[^"\n]*"', lambda m: f'"{next(new)}"', lines[index])
    if next(new, None) is not None:
        raise ValueError("more new chord symbols than chords in the score")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def edit_score(text: str, notes: list[Note], palette: str = "key",
               new_slashes: bool = False) -> tuple[str, list]:
    """Rename every chord from `notes`; verify melody, bars and tempo are untouched.

    palette "key" keeps new chords inside the score's key; "chromatic" allows any chord."""
    score = abc_tools.parse_abc(text)
    chords = chord_timeline(score)
    key = score.voices["Vocal"].keys[0][1]
    flats = abc_tools.KEYS[key] < 0
    scale = key_scale(key) if palette == "key" else None
    symbols = rename(chords, notes, flats, scale, new_slashes)
    edited = replace_chords(text, score, symbols)
    check = abc_tools.compare(score, abc_tools.parse_abc(edited))
    if not check["match"]:
        raise RuntimeError(f"edit changed the melody: {check['differences']}")
    return edited, [(c.symbol, s) for c, s in zip(chords, symbols)]


def blur_on_moth(midi: Path, params: dict, out_dir: Path) -> Path:
    import moth
    asset = moth.upload(midi, "audio/midi")
    job = moth.submit("blur-midi-v1", params, {"midi": asset})
    status = moth.wait(job)
    if status["status"] != "completed":
        raise RuntimeError(json.dumps(status))
    result = moth.result(job, out_dir)
    name = next(o.get("filename") or o["slot"] for o in result["outputs"] if o["slot"] == "result")
    return out_dir / name


def main() -> None:
    p = argparse.ArgumentParser(description="Quantum-blur a YuE2 score's harmony")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chords", help="print the chord timeline")
    c.add_argument("score")
    m = sub.add_parser("to-midi", help="write the chords as a block-chord MIDI file")
    m.add_argument("score")
    m.add_argument("midi")
    a = sub.add_parser("apply", help="rename the chords from a (blurred) MIDI file")
    a.add_argument("score")
    a.add_argument("midi")
    a.add_argument("out")
    b = sub.add_parser("blur", help="to-midi, blur-midi-v1 on Moth (1 credit), apply")
    b.add_argument("score")
    b.add_argument("out")
    b.add_argument("--params", default="{}", help="blur-midi-v1 params as JSON")
    b.add_argument("--work", default="harmony-work", help="folder for the MIDI files")
    for sp in (a, b):
        sp.add_argument("--palette", choices=("key", "chromatic"), default="key",
                        help="key: new chords stay in the score's key (default); chromatic: any")
        sp.add_argument("--slashes", action="store_true",
                        help="allow new slash basses (default: keep only the original ones)")
    args = p.parse_args()

    text = Path(args.score).read_text()
    score = abc_tools.parse_abc(text)
    chords = chord_timeline(score)
    if args.cmd == "chords":
        for ch in chords:
            print(f"{float(ch.onset):7.2f}–{float(ch.end):7.2f} q  {ch.symbol}")
        return
    if args.cmd == "to-midi":
        write_midi(Path(args.midi), chords_to_notes(chords), score.bpm)
        return
    if args.cmd == "apply":
        midi = Path(args.midi)
    else:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        source = work / "harmony.mid"
        write_midi(source, chords_to_notes(chords), score.bpm)
        midi = blur_on_moth(source, json.loads(args.params), work)
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists; choose a new file")
    edited, changes = edit_score(text, read_midi(midi), args.palette, args.slashes)
    out.write_text(edited)
    for old, new in changes:
        print(f"{old:>8} → {new}" + ("" if old != new else "   (unchanged)"))
    print(f"wrote {out}; melody, bars and tempo verified unchanged", file=sys.stderr)


if __name__ == "__main__":
    main()
