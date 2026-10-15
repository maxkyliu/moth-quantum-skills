"""Offline tests for scripts/midi_io.py and scripts/yue2_harmony.py (needs a YuE checkout)."""
import sys
from fractions import Fraction
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"
sys.path.insert(0, str(SCRIPTS))
import midi_io  # noqa: E402

harmony = pytest.importorskip("yue2_harmony", reason="needs YuE2's abc_tools (set YUE2_DIR)")

SCORE = """X:1
T:
M:4/4
L:1/32
Q:1/4=88
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:F
% verse
V: Vocal
"F"A8c8A8F8|"Bb"B8d8"C7"c16|"Dm/A"A32|"Gm7"G16-"C"G16|
V: Ins
Z4|
"""


def test_midi_round_trip(tmp_path):
    notes = [midi_io.Note(0.0, 48, 4.0, 80), midi_io.Note(0.0, 64, 4.0, 80),
             midi_io.Note(4.5, 67, 0.25, 100)]
    path = tmp_path / "t.mid"
    midi_io.write_midi(path, notes, 96)

    assert midi_io.read_midi(path) == sorted(notes, key=lambda n: (n.onset, n.pitch))


def test_reader_handles_running_status_and_note_on_zero(tmp_path):
    track = (b"\x00\x90\x3c\x50"      # note on C4
             b"\x00\x40\x50"          # running status: note on E4
             b"\x83\x60\x3c\x00"      # +480 ticks, running status, C4 velocity 0 = off
             b"\x00\x40\x00"          # E4 off
             b"\x00\xff\x2f\x00")
    data = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0" + b"MTrk" + len(track).to_bytes(4, "big") + track
    path = tmp_path / "rs.mid"
    path.write_bytes(data)

    assert [(n.pitch, n.duration) for n in midi_io.read_midi(path)] == [(60, 1.0), (64, 1.0)]


@pytest.mark.parametrize("quality", list(harmony.INTERVALS))
def test_every_root_and_quality_names_itself(quality):
    for root in range(12):
        for flats, names in ((False, harmony.SHARPS), (True, harmony.FLATS)):
            symbol = names[root] + quality
            chord = harmony.Chord(Fraction(0), Fraction(4), symbol)
            notes = harmony.voice_chord(chord)
            weights, by_pitch = harmony.window_weights(notes, 0, 4)
            assert harmony.name_chord(weights, by_pitch, symbol, flats) == symbol


@pytest.mark.parametrize("symbol", ["C/E", "Am7/G", "F/C", "Bbmaj7/D"])
def test_slash_chords_name_themselves(symbol):
    flats = "b" in symbol
    notes = harmony.voice_chord(harmony.Chord(Fraction(0), Fraction(4), symbol))
    weights, by_pitch = harmony.window_weights(notes, 0, 4)
    assert harmony.name_chord(weights, by_pitch, symbol, flats) == symbol


def test_bass_leak_does_not_invent_a_slash():
    notes = harmony.voice_chord(harmony.Chord(Fraction(0), Fraction(4), "Amaj7"))
    notes.append(midi_io.Note(0.0, 44, 4.0, 90))           # blur leaks the A bass down to G#
    weights, by_pitch = harmony.window_weights(notes, 0, 4)

    assert harmony.name_chord(weights, by_pitch, "Amaj7", False) == "Amaj7"
    assert harmony.name_chord(weights, by_pitch, "Amaj7", False, new_slashes=True) == "Amaj7/G#"


def test_original_chord_survives_a_near_tie():
    notes = harmony.voice_chord(harmony.Chord(Fraction(0), Fraction(4), "C#m7"))
    notes.append(midi_io.Note(0.0, 69, 4.0, 40))           # a faint A: C#m7 and Amaj7 now close
    weights, by_pitch = harmony.window_weights(notes, 0, 4)

    assert harmony.name_chord(weights, by_pitch, "C#m7", False, harmony.key_scale("A")) == "C#m7"


def test_timeline_spans_until_next_chord_or_end():
    chords = harmony.chord_timeline(harmony.abc_tools.parse_abc(SCORE))
    assert [(float(c.onset), float(c.end), c.symbol) for c in chords] == [
        (0, 4, "F"), (4, 6, "Bb"), (6, 8, "C7"), (8, 12, "Dm/A"), (12, 14, "Gm7"), (14, 16, "C")]


def test_round_trip_score_is_identical():
    score = harmony.abc_tools.parse_abc(SCORE)
    notes = harmony.chords_to_notes(harmony.chord_timeline(score))
    edited, changes = harmony.edit_score(SCORE, notes)

    assert edited == SCORE
    assert all(old == new for old, new in changes)


def test_extra_tone_renames_chord_without_touching_melody():
    score = harmony.abc_tools.parse_abc(SCORE)
    notes = harmony.chords_to_notes(harmony.chord_timeline(score))
    notes.append(midi_io.Note(0.0, 64, 4.0, 80))          # add E over the first F chord
    edited, changes = harmony.edit_score(SCORE, notes)

    assert changes[0] == ("F", "Fmaj7")
    assert edited.replace('"Fmaj7"', '"F"', 1) == SCORE


def test_key_scale_matches_signature():
    assert harmony.key_scale("C") == {0, 2, 4, 5, 7, 9, 11}
    assert harmony.key_scale("F") == {5, 7, 9, 10, 0, 2, 4}
    assert harmony.key_scale("Am") == harmony.key_scale("C")


def test_key_palette_keeps_smeared_chords_in_key():
    # C major chord smeared onto its chromatic neighbours, as a strong blur does
    weights = [0.15, 0.11, 0.05, 0.11, 0.07, 0.04, 0.03, 0.10, 0.10, 0.09, 0.07, 0.20]
    by_pitch = {48 + pc: w for pc, w in enumerate(weights)}

    def tones(symbol):
        root, quality, _ = harmony.parse_symbol(symbol.split("/")[0])
        return {(root + i) % 12 for i in harmony.INTERVALS[quality]}

    chromatic = harmony.name_chord(weights, by_pitch, "C", False)
    in_key = harmony.name_chord(weights, by_pitch, "C", False, harmony.key_scale("C"))

    assert not tones(chromatic) <= harmony.key_scale("C")    # the smear names an out-of-key chord
    assert tones(in_key) <= harmony.key_scale("C")


def test_key_palette_always_allows_the_original_chord():
    notes = harmony.voice_chord(harmony.Chord(Fraction(0), Fraction(4), "E"))   # E major in C
    weights, by_pitch = harmony.window_weights(notes, 0, 4)
    assert harmony.name_chord(weights, by_pitch, "E", False, harmony.key_scale("C")) == "E"


def test_replace_chords_refuses_count_mismatch():
    score = harmony.abc_tools.parse_abc(SCORE)
    with pytest.raises(ValueError):
        harmony.replace_chords(SCORE, score, ["C"] * 7)


# --- change dial -------------------------------------------------------------------------

def _chords(*symbols):
    return [harmony.Chord(Fraction(4 * i), Fraction(4 * i + 4), s) for i, s in enumerate(symbols)]


def _blurred(chords, extra):
    """Clean voicings plus extra notes {chord index: [(pitch, velocity)]}."""
    notes = harmony.chords_to_notes(chords)
    for i, pitches in extra.items():
        notes += [midi_io.Note(4.0 * i, p, 4.0, v) for p, v in pitches]
    return notes


def test_zero_change_keeps_every_chord():
    chords = _chords("C", "F", "Am", "G")
    notes = _blurred(chords, {1: [(64, 90)], 2: [(65, 90)]})
    assert harmony.rename_by_budget(chords, notes, False, 0.0) == ["C", "F", "Am", "G"]


def test_clean_chords_are_not_eligible_even_at_full_budget():
    chords = _chords("C", "F", "Am", "G")
    notes = harmony.chords_to_notes(chords)
    assert harmony.rename_by_budget(chords, notes, False, 1.0) == ["C", "F", "Am", "G"]


def test_budget_adds_changes_on_top_of_smaller_budgets():
    chords = _chords("C", "F", "Am", "G", "C", "F", "Am", "G")
    notes = _blurred(chords, {1: [(64, 90)], 2: [(65, 110)], 4: [(71, 70)], 5: [(64, 40)],
                              6: [(65, 60)], 7: [(65, 90)]})
    small = harmony.rename_by_budget(chords, notes, False, 0.25)
    large = harmony.rename_by_budget(chords, notes, False, 0.5)
    changed = lambda out: {i for i, (a, b) in enumerate(zip(out, [c.symbol for c in chords])) if a != b}
    assert len(changed(small)) == 2 and len(changed(large)) == 4
    assert changed(small) <= changed(large)


def test_gentle_recolouring_outranks_a_root_change():
    chords = _chords("F", "Am")
    # F + E → Fmaj7 (same root, small pull); Am + strong F → F (new root, a much stronger pull)
    notes = _blurred(chords, {0: [(64, 90)], 1: [(65, 127), (53, 127)]})
    suggestions = [harmony.suggest(*harmony.window_weights(notes, float(c.onset), float(c.end)),
                                   c.symbol, False) for c in chords]
    assert 0 < suggestions[0].advantage < suggestions[1].advantage and suggestions[1].new_root
    assert harmony.rename_by_budget(chords, notes, False, 0.5) == ["Fmaj7", "Am"]


def test_suggestion_reports_advantage_and_root_cost():
    chord = harmony.Chord(Fraction(0), Fraction(4), "Am")
    notes = harmony.voice_chord(chord) + [midi_io.Note(0.0, 65, 4.0, 127), midi_io.Note(0.0, 53, 4.0, 127)]
    s = harmony.suggest(*harmony.window_weights(notes, 0, 4), "Am", False)
    assert s.symbol.startswith("F") and s.advantage > 0 and s.new_root


def test_parse_fraction_accepts_percent_and_decimal():
    assert harmony.parse_fraction("25%") == 0.25
    assert harmony.parse_fraction("0.1") == 0.1
    with pytest.raises(Exception):
        harmony.parse_fraction("150%")


def test_edit_score_with_budget_keeps_melody():
    score = harmony.abc_tools.parse_abc(SCORE)
    notes = harmony.chords_to_notes(harmony.chord_timeline(score))
    notes.append(midi_io.Note(0.0, 64, 4.0, 90))          # pull the first F toward Fmaj7
    edited, changes = harmony.edit_score(SCORE, notes, change=0.2)
    assert sum(a != b for a, b in changes) == 1 and changes[0] == ("F", "Fmaj7")


def test_intact_out_of_key_chord_is_not_a_target():
    chords = _chords("A", "Caug")                     # Caug is outside A major
    notes = harmony.chords_to_notes(chords)           # both chords intact, no blur
    assert harmony.rename_by_budget(chords, notes, False, 1.0, harmony.key_scale("A")) == ["A", "Caug"]
