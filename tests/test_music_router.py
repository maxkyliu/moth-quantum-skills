"""Offline tests for scripts/typesafe_music_router.py."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import typesafe_music_router as music  # noqa: E402


def choice(option, confidence=0.9):
    return {"type": "choice", "choice": option, "probabilities": {option: confidence},
            "confidence": confidence}


def score(value, levels, confidence=0.9):
    return {"type": "score", "score": value,
            "legend": {str(i): name for i, name in enumerate(levels)}, "confidence": confidence}


def noul(p):
    return {"type": "noul", "noul": p}


def answers(engine, **overrides):
    base = {"engine": choice(engine)}
    for qid, levels in (("strength", music.STRENGTH_LEVELS), ("reach", music.REACH_LEVELS),
                        ("variation", music.VARIATION_LEVELS),
                        ("character", music.ECHO_CHARACTER_LEVELS), ("mix", music.MIX_LEVELS)):
        base[qid] = score(1.0, levels)
        base[f"{qid}?"] = noul(0.05)
    for qid, option in (("quality", "moderate"), ("negative_mode", "invert"), ("division", "0.25")):
        base[qid] = choice(option)
        base[f"{qid}?"] = noul(0.05)
    for qid in ("on_beat", "map_only", "partial", "real_hardware"):
        base[qid] = noul(0.05)
    return {**base, **overrides}


def plan(engine, *, has_midi=False, has_audio=False, **kwargs):
    overrides = {k: v for k, v in kwargs.items() if k not in ("length", "bpm", "mask")}
    extra = {k: v for k, v in kwargs.items() if k in ("length", "bpm", "mask")}
    return music.plan_from_answers(answers(engine, **overrides),
                                   has_midi=has_midi, has_audio=has_audio, **extra)


def test_questions_cover_every_branch():
    questions = music.build_questions()

    assert set(questions["engine"]["criteria"]) == set(music.ENGINES)
    for qid in ("strength", "reach", "variation", "character", "mix",
                "quality", "negative_mode", "division"):
        assert f"{qid}?" in questions
    assert set(questions["division"]["criteria"]) == {"0.25", "0.5", "0.75", "0.3333", "1.0"}


def test_blur_midi_maps_strength_and_reach():
    result = plan("blur-midi-v1", has_midi=True, **{
        "strength?": noul(0.9), "strength": score(1.0, music.STRENGTH_LEVELS),
        "reach?": noul(0.9), "reach": score(2.0, music.REACH_LEVELS)})

    assert result.params == {"strength": 0.25, "reach": 1.0}
    assert not result.needs_confirmation


def test_blur_midi_partial_without_section_asks_for_one():
    result = plan("blur-midi-v1", has_midi=True, partial=noul(0.9))

    assert "mask" not in result.params
    assert any("--section" in r for r in result.reasons)


def test_blur_midi_section_becomes_mask():
    result = plan("blur-midi-v1", has_midi=True, partial=noul(0.9), mask=[[1, 8.0, 16.0]])

    assert result.params == {"mask": [[1, 8.0, 16.0]]}
    assert result.reasons == ()


def test_midi_engine_without_midi_is_missing_it():
    assert plan("blur-midi-v1", has_audio=True).missing_slots == ("midi",)


def test_qrc_midi_interpolates_variation_and_takes_flags():
    result = plan("qrc-midi-v1", has_midi=True, length=64, bpm=90, **{
        "variation?": noul(0.9), "variation": score(1.5, music.VARIATION_LEVELS),
        "quality?": noul(0.9), "quality": choice("complete")})

    assert result.params == {"variation": 1.5, "quality": "complete", "length": 64, "bpm": 90}


def test_qrc_audio_cuts_one_bar_chunks_from_bpm():
    result = plan("qrc-audio-v1", has_audio=True, bpm=120)

    assert result.params == {"chunk_seconds": 2.0}


def test_qrc_audio_without_audio_is_missing_it():
    assert plan("qrc-audio-v1", has_midi=True).missing_slots == ("audio",)


def test_echo_character_mix_and_reverse():
    result = plan("retrocausal-echo-v1", has_audio=True, **{
        "character?": noul(0.9), "character": score(2.0, music.ECHO_CHARACTER_LEVELS),
        "mix?": noul(0.9), "mix": score(0.0, music.MIX_LEVELS),
        "negative_mode?": noul(0.9), "negative_mode": choice("reverse")})

    assert result.params == {"theta_x": 2.0, "mix": 0.2, "negative_mode": "reverse"}


def test_echo_phase_mode_turns_on_theta_z():
    result = plan("retrocausal-echo-v1", has_audio=True, **{
        "negative_mode?": noul(0.9), "negative_mode": choice("phase")})

    assert result.params == {"negative_mode": "phase", "theta_z": music.PHASE_THETA_Z}


def test_echo_on_beat_uses_bpm_and_division():
    result = plan("retrocausal-echo-v1", has_audio=True, bpm=96, on_beat=noul(0.9), **{
        "division?": noul(0.9), "division": choice("0.3333")})

    assert result.params == {"bpm": 96, "division": 0.3333}


def test_echo_on_beat_without_bpm_asks_for_it():
    result = plan("retrocausal-echo-v1", has_audio=True, on_beat=noul(0.9))

    assert "bpm" not in result.params
    assert any("--bpm" in r for r in result.reasons)


def test_echo_without_audio_warns_impulse_response():
    result = plan("retrocausal-echo-v1")

    assert result.missing_slots == ()
    assert any("impulse response" in r for r in result.reasons)


def test_echo_map_only_needs_no_audio_warning():
    result = plan("retrocausal-echo-v1", map_only=noul(0.9))

    assert result.params == {"emit": "map"}
    assert result.reasons == ()


def test_real_hardware_needs_confirmation_and_stays_simulated():
    result = plan("retrocausal-echo-v1", has_audio=True, real_hardware=noul(0.95))

    assert "machine" not in result.params
    assert result.needs_confirmation


def test_slot_files_pick_the_engine_input():
    assert music.slot_files(music.Plan("blur-midi-v1"), "a.mid", "b.wav") == {"midi": "a.mid"}
    assert music.slot_files(music.Plan("retrocausal-echo-v1"), "a.mid", "b.wav") == {"audio": "b.wav"}
    assert music.slot_files(music.Plan("retrocausal-echo-v1"), None, None) == {}


def test_parse_section():
    assert music.parse_section("1:8:16.5") == [1, 8.0, 16.5]
    with pytest.raises(SystemExit):
        music.parse_section("1-8-16")
