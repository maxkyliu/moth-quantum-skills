"""Offline tests for scripts/yue2_chip_noise.py (needs a YuE checkout for YuE2's ABC parser)."""
import sys
from fractions import Fraction
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"
sys.path.insert(0, str(SCRIPTS))
chip = pytest.importorskip("yue2_chip_noise", reason="needs YuE2's abc_tools (set YUE2_DIR)")
harmony = chip.h

SCORE = """X:1
T:
M:4/4
L:1/32
Q:1/4=88
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% verse
V: Vocal
"C"E8G8E8C8|"F"F8A8c16|"G"G32|"C"E16-"F"E16|
V: Ins
Z4|
"""


def chords():
    return harmony.chord_timeline(harmony.abc_tools.parse_abc(SCORE))


def test_profile_puts_root_at_one_and_tones_at_half():
    row = chip.profile(harmony.Chord(Fraction(0), Fraction(4), "Am"))

    assert row[9] == 1.0 and row[0] == 0.5 and row[4] == 0.5
    assert sum(row) == 2.0


def test_auto_encoding_sends_every_chord_when_it_fits():
    sent = chip.encode(chords())

    assert sent["chords"] == ["C", "F", "G", "C", "F"]
    assert len(sent["values"]) == 60


def test_auto_encoding_falls_back_to_distinct_chords_over_capacity():
    sent = chip.encode(chords(), capacity=48)

    assert sent["chords"] == ["C", "F", "G"]
    assert len(sent["values"]) == 36


@pytest.mark.parametrize("wrapped", [
    {"output": [1, 2]},
    {"$schema": "x", "result": {"output": [1, 2]}},
    {"job_id": "j", "result": {"$schema": "x", "result": {"output": [1, 2]}}},
])
def test_read_output_unwraps_saved_results(wrapped):
    assert chip.read_output(wrapped) == [1, 2]


def test_read_output_rejects_a_failed_job():
    with pytest.raises(ValueError):
        chip.read_output({"job_id": "j", "status": {"status": "failed"}, "result": None})


def test_noiseless_round_trip_changes_nothing():
    sent = chip.encode(chords())
    notes = chip.to_notes(chords(), chip.noisy_profiles(sent, sent["values"]))

    _, changes = harmony.edit_score(SCORE, notes, change=0.5)

    assert all(old == new for old, new in changes)


def test_noise_on_a_seventh_recolours_only_that_chord():
    sent = chip.encode(chords(), "distinct")
    output = list(sent["values"])
    output[12 * 1 + 4] = 0.6                     # F's row: noise lifts E, the major seventh
    notes = chip.to_notes(chords(), chip.noisy_profiles(sent, output))

    _, changes = harmony.edit_score(SCORE, notes, change=0.5)

    assert [new for _, new in changes] == ["C", "Fmaj7", "G", "C", "Fmaj7"]


def test_length_mismatch_is_an_error():
    sent = chip.encode(chords())
    with pytest.raises(ValueError):
        chip.noisy_profiles(sent, sent["values"][:-12])
