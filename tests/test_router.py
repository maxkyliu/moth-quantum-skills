"""Offline tests for scripts/typesafe_router.py, using answers in TypeSafe's documented shape."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import typesafe_core as core  # noqa: E402
import typesafe_router as router  # noqa: E402


def choice(option, confidence=0.9):
    return {"type": "choice", "choice": option, "probabilities": {option: confidence},
            "confidence": confidence}


def score(value, levels, confidence=0.9):
    return {"type": "score", "score": value,
            "legend": {str(i): name for i, name in enumerate(levels)}, "confidence": confidence}


def noul(p):
    return {"type": "noul", "noul": p}


def answers(**overrides):
    base = {
        "engine": choice("blur-v1"),
        "strength": score(1.0, router.STRENGTH_LEVELS), "strength?": noul(0.05),
        "reach": score(0.0, router.REACH_LEVELS), "reach?": noul(0.05),
        "direction": choice("full"), "direction?": noul(0.05),
        "partial": noul(0.05), "noisy": noul(0.05), "real_hardware": noul(0.02),
    }
    return {**base, **overrides}


def test_unstated_arguments_keep_engine_defaults():
    plan = router.plan_from_answers(answers(), image_count=1, has_mask=False)

    assert plan.engine == "blur-v1"
    assert plan.params == {}
    assert not plan.missing_slots and not plan.needs_confirmation


def test_stated_strength_and_reach_map_levels_to_unit_range():
    plan = router.plan_from_answers(answers(**{
        "strength?": noul(0.95), "strength": score(3.0, router.STRENGTH_LEVELS),
        "reach?": noul(0.9), "reach": score(2.0, router.REACH_LEVELS),
    }), image_count=1, has_mask=False)

    assert plan.params == {"strength": 0.75, "reach": 1.0}


def test_partial_edit_without_mask_asks_for_one():
    plan = router.plan_from_answers(answers(partial=noul(0.92)), image_count=1, has_mask=False)

    assert plan.missing_slots == ("mask",)
    assert plan.needs_confirmation


def test_partial_edit_with_mask_is_ready():
    plan = router.plan_from_answers(answers(partial=noul(0.92)), image_count=1, has_mask=True)

    assert plan.missing_slots == ()


def test_morph_needs_two_images_and_reads_direction():
    plan = router.plan_from_answers(answers(**{
        "engine": choice("telablur-v1"), "direction?": noul(0.9),
        "direction": choice("vertical"),
    }), image_count=1, has_mask=False)

    assert plan.params == {"direction": "vertical"}
    assert plan.missing_slots == ("image2",)


def test_deep_fryer_strength_becomes_gate_intensity():
    plan = router.plan_from_answers(answers(**{
        "engine": choice("deep-fryer-v1"), "strength?": noul(0.9),
        "strength": score(4.0, router.STRENGTH_LEVELS),
    }), image_count=1, has_mask=False)

    assert plan.params == {"gates": [["rx", 1.0]]}


def test_real_hardware_always_needs_confirmation():
    plan = router.plan_from_answers(answers(**{
        "engine": choice("tessa-image-v1"), "real_hardware": noul(0.97),
    }), image_count=1, has_mask=False)

    assert plan.params == {"machine": "least_busy"}
    assert plan.needs_confirmation


def test_noise_request_uses_noisy_simulator():
    plan = router.plan_from_answers(answers(**{
        "engine": choice("tessa-image-v1"), "noisy": noul(0.9),
    }), image_count=1, has_mask=False)

    assert plan.params == {"machine": "fake_fez"}


def test_low_confidence_route_needs_confirmation():
    plan = router.plan_from_answers(answers(engine=choice("blur-v1", confidence=0.4)),
                                    image_count=1, has_mask=False)

    assert plan.confidence == 0.4 and plan.needs_confirmation


def test_undecided_stated_noul_lowers_confidence():
    plan = router.plan_from_answers(answers(**{"strength?": noul(0.55)}),
                                    image_count=1, has_mask=False)

    assert plan.confidence == pytest.approx(0.1)
    assert plan.needs_confirmation


def test_no_matching_engine_returns_empty_plan():
    plan = router.plan_from_answers(answers(engine=choice("none")), image_count=1, has_mask=False)

    assert plan.engine is None and plan.needs_confirmation


def test_slot_files_follow_chosen_engine():
    plan = router.Plan("telablur-v1")

    assert router.slot_files(plan, ["a.png", "b.png"], "m.png") == {
        "image1": "a.png", "image2": "b.png", "mask": "m.png"}


def test_request_shape_matches_typesafe_api(monkeypatch):
    sent = {}

    def fake_http(method, url, *, body=None, headers=None):
        sent.update(method=method, url=url, body=json.loads(body), headers=headers)
        return 200, json.dumps({"answers": answers()}).encode()

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    monkeypatch.setattr(core.moth, "_http", fake_http)

    core.ask_typesafe({"request": "make it dreamy", "attached_images": 1}, router.build_questions())

    assert sent["url"] == core.TYPESAFE_URL
    assert sent["body"]["model"] == "jev-latest"
    assert sent["body"]["state"] == {"request": "make it dreamy", "attached_images": 1}
    assert set(sent["body"]["questions"]) == set(router.build_questions())
    assert all(q["type"] in {"choice", "score", "noul"} for q in sent["body"]["questions"].values())
    assert sent["headers"]["Authorization"] == "Bearer ts_test"
