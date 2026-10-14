"""Offline tests for scripts/typesafe_refine.py and session saving in typesafe_core."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import typesafe_core as core  # noqa: E402
import typesafe_refine as refine  # noqa: E402

LAST_ECHO = {"engine": "retrocausal-echo-v1",
             "params": {"theta_x": 1.98, "negative_mode": "reverse", "bpm": 96.0},
             "inputs": {"audio": "audio-asset"}, "job_id": "job-1",
             "outputs": {"result": "wav-asset", "ir": "ir-asset", "taps": "taps-asset"}}


def size(value, confidence=0.9):
    """0 = a little, 1 = a lot."""
    return {"type": "score", "score": value, "confidence": confidence,
            "legend": {str(i): lvl for i, lvl in enumerate(refine.SIZE_LEVELS)}}


def down(amount=0.0):
    return {"way": "lower", "size": amount}


def up(amount=0.0):
    return {"way": "higher", "size": amount}


def choice(option, confidence=0.9):
    return {"type": "choice", "choice": option, "confidence": confidence,
            "probabilities": {option: confidence}}


def answers(engine, change=None, **values):
    """Every dial unchanged unless named in `change`. Numeric dials take up()/down();
    choice dials take choice(option)."""
    change = change or {}
    out = {}
    for dial in refine.DIALS[engine]:
        out[f"{dial.name}?"] = {"type": "noul", "noul": 0.95 if dial.name in change else 0.03}
        value = values.get(dial.name)
        if dial.options:
            out[dial.name] = value or choice("invert")
        else:
            move = value or up()
            out[f"{dial.name}.way"] = choice(move["way"])
            out[f"{dial.name}.size"] = size(move["size"])
    return out


def test_questions_cover_every_dial():
    questions = refine.build_questions("retrocausal-echo-v1")
    for dial in refine.ECHO:
        assert f"{dial.name}?" in questions
        if dial.options:
            assert dial.name in questions
        else:
            assert set(questions[f"{dial.name}.way"]["criteria"]) == {"lower", "higher"}
            assert questions[f"{dial.name}.size"]["criteria"] == refine.SIZE_LEVELS
    assert "drier" in questions["mix.way"]["criteria"]["lower"]
    assert "`feedback`" in questions["mix?"]["instructions"]
    assert set(questions["negative_mode"]["criteria"]) == {"invert", "reverse", "phase"}


def test_playback_change_reuses_measured_ir():
    plan, files, changes = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"mix", "negative_mode"}, mix=down(1.0),
                negative_mode=choice("invert")), LAST_ECHO)

    assert changes == {"mix": [0.6, 0.3], "negative_mode": ["reverse", "invert"]}
    assert plan.params == {"theta_x": 1.98, "negative_mode": "invert", "bpm": 96.0, "mix": 0.3}
    assert files == {"audio": "audio-asset", "ir": "ir-asset"}
    assert not plan.needs_confirmation


def test_character_change_needs_new_measurement():
    plan, files, changes = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"character"}, character=down(1.0)), LAST_ECHO)

    assert changes == {"theta_x": [1.98, 1.28]}
    assert "ir" not in files
    assert plan.needs_confirmation and "new quantum measurement" in plan.reasons[0]


def test_phase_mode_adds_theta_z_and_measures_again():
    plan, files, changes = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"negative_mode"}, negative_mode=choice("phase")), LAST_ECHO)

    assert plan.params["theta_z"] == refine.PHASE_THETA_Z
    assert changes["theta_z"] == [0, refine.PHASE_THETA_Z]
    assert "ir" not in files


def test_values_stay_inside_schema_limits():
    last = {**LAST_ECHO, "params": {"mix": 0.95, "theta_x": 3.1}}
    plan, _, changes = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"mix", "character"}, mix=up(1.0),
                character=up(1.0)), last)

    assert plan.params["mix"] == 1.0 and plan.params["theta_x"] == 3.14


def test_a_little_moves_one_step_and_a_lot_two():
    little, _, _ = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"width"}, width=down(0.0)), LAST_ECHO)
    lot, _, _ = refine.plan_refinement(
        answers("retrocausal-echo-v1", {"width"}, width=down(1.0)), LAST_ECHO)

    assert little.params["stereo_width"] == 0.75 and lot.params["stereo_width"] == 0.5


def test_integer_dial_stays_integer():
    last = {"engine": "qrc-audio-v1", "params": {}, "inputs": {"audio": "a"}, "job_id": "j",
            "outputs": {"result": "r"}}
    plan, _, _ = refine.plan_refinement(
        answers("qrc-audio-v1", {"crossfade"}, crossfade=up(0.0)), last)

    assert plan.params["crossfade"] == 100 and isinstance(plan.params["crossfade"], int)


def test_feedback_that_changes_nothing_returns_no_plan():
    plan, files, changes = refine.plan_refinement(answers("retrocausal-echo-v1"), LAST_ECHO)

    assert plan.engine is None and changes == {} and plan.needs_confirmation


def test_uncertain_change_lowers_confidence():
    a = answers("retrocausal-echo-v1", {"mix"}, mix=down(1.0))
    a["tail?"] = {"type": "noul", "noul": 0.55}
    plan, _, _ = refine.plan_refinement(a, LAST_ECHO)

    assert plan.confidence == pytest.approx(0.1) and plan.needs_confirmation


def test_session_appends_runs(tmp_path):
    record = {"engine": "retrocausal-echo-v1", "params": {"mix": 0.3}, "inputs": {"audio": "a"},
              "job_id": "j1", "outputs": {"ir": "i"}, "result": {"big": "blob"}}
    core.save_session(tmp_path, record, "first")
    core.save_session(tmp_path, {**record, "job_id": "j2"}, "less echo")

    history = core.load_session(tmp_path / core.SESSION_FILE)["history"]
    assert [h["job_id"] for h in history] == ["j1", "j2"]
    assert history[-1]["request"] == "less echo" and "result" not in history[-1]


def test_run_plan_reuses_assets_and_records_outputs(monkeypatch, tmp_path):
    sent = {}
    monkeypatch.setattr(core.moth, "engine", lambda e: {"credits_per_run": 2})
    monkeypatch.setattr(core.moth, "upload", lambda p: pytest.fail("should not upload"))
    monkeypatch.setattr(core.moth, "submit", lambda e, p, f: sent.update(files=f) or "job-2")
    monkeypatch.setattr(core.moth, "wait", lambda j: {"status": "completed"})
    monkeypatch.setattr(core.moth, "result", lambda j, out: {"outputs": [
        {"slot": "ir", "output_asset_id": "ir-2"}, {"slot": "result", "output_asset_id": "wav-2"}]})
    asset = "45e2f7da-2ce1-45cd-a651-64828e832715"

    record = core.run_plan(core.Plan("retrocausal-echo-v1", {"mix": 0.3}),
                           {"audio": asset, "ir": "64ef3280-6a03-4f56-85c3-ea30ff021292"}, tmp_path)

    assert sent["files"] == {"audio": asset, "ir": "64ef3280-6a03-4f56-85c3-ea30ff021292"}
    assert record["outputs"] == {"ir": "ir-2", "result": "wav-2"} and record["job_id"] == "job-2"
