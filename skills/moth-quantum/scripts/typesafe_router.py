#!/usr/bin/env python3
"""Route a plain-language image request to a Moth image engine with TypeSafe judgments.

One TypeSafe request asks which engine fits and, speculatively, every argument any
engine could need. Code reads only the chosen engine's answers, keeps engine defaults
for arguments the user never mentioned, and asks for confirmation when the least
certain judgment it used is below CONFIRM_BELOW. Standard library only.

    python typesafe_router.py "make it dreamy but keep the face sharp" --image photo.png
    python typesafe_router.py "..." --image photo.png --mask face.png --run

Judge: TypeSafe (TYPESAFE_API_KEY) or a local LLM (JUDGE=local, see local_judge.py).
MOTH_API_KEY is needed for --run. Music: typesafe_music_router.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from typesafe_core import Plan, Reader, ask, finish, judge  # noqa: E402

ENGINES = {
    "blur-v1": "Blur one image: a soft, dreamy, hazy or ghostly quantum blur, possibly only "
               "in part of it",
    "telablur-v1": "Morph, blend or melt one image into a second image",
    "deep-fryer-v1": "Deep fry an image: a loud, over-saturated, blown-out meme look, "
                     "at any intensity",
    "tessa-image-v1": "Send the colours through a quantum encode-and-measure round trip, "
                      "for glitchy colour shifts or to see quantum noise",
    "none": "The request is not about changing an image at all, for example an unrelated "
            "question",
}
STRENGTH_LEVELS = ["barely noticeable", "subtle", "clearly visible", "strong",
                   "as strong as possible"]
REACH_LEVELS = ["stays local: only nearby detail mixes",
                "spreads part of the way across the picture",
                "scattered across the whole picture"]
IMAGE_SLOTS = {"blur-v1": ["image"], "telablur-v1": ["image1", "image2"],
               "deep-fryer-v1": ["image"], "tessa-image-v1": ["image"]}
MASKABLE = {"blur-v1", "telablur-v1", "deep-fryer-v1"}


def build_questions() -> dict:
    """Every question, for every engine, in one request (speculative fan-out)."""
    return {
        "engine": {"type": "choice", "instructions": ask(
            "Which image effect is the user asking for?"), "criteria": ENGINES},
        "strength": {"type": "score", "instructions": ask(
            "How strong should the effect be?"), "criteria": STRENGTH_LEVELS},
        "strength?": {"type": "noul", "instructions": ask(
            "Does the user say anything about how strong, subtle or heavy the effect should be?")},
        "reach": {"type": "score", "instructions": ask(
            "How far across the picture should the effect spread?"), "criteria": REACH_LEVELS},
        "reach?": {"type": "noul", "instructions": ask(
            "Setting aside which region of the image changes, does the user describe how far "
            "the smearing itself should travel, such as a gentle local haze versus detail "
            "scattered across the entire picture?")},
        "direction": {"type": "choice", "instructions": ask(
            "In which direction should one image melt into the other?"),
            "criteria": {"full": "in every direction", "vertical": "up and down only",
                         "horizontal": "side to side only"}},
        "direction?": {"type": "noul", "instructions": ask(
            "Does the user mention a direction, such as vertical or horizontal, for the morph?")},
        "partial": {"type": "noul", "instructions": ask(
            "Does the user want only part of the image changed while another part stays "
            "untouched, for example keeping a face or the background sharp?")},
        "noisy": {"type": "noul", "instructions": ask(
            "Does the user want to see the noise or imperfections of real quantum hardware?")},
        "real_hardware": {"type": "noul", "instructions": ask(
            "Does the user explicitly ask for the job itself to run on a real quantum computer? "
            "Wanting to see or imitate hardware noise is not enough.")},
    }


def plan_from_answers(answers: dict, image_count: int, has_mask: bool) -> Plan:
    """Turn TypeSafe answers into Moth params, reading only the chosen engine's branch."""
    read = Reader(answers)
    engine = read.choice("engine")
    if engine == "none":
        return Plan(None, confidence=read.confidence(),
                    reasons=("No image effect fits this request.",))

    params: dict = {}
    reasons: list[str] = []
    if engine != "tessa-image-v1" and read.stated("strength"):
        level = read.unit("strength")
        if engine == "deep-fryer-v1":
            params["gates"] = [["rx", level]]   # assumes intensity 0..1; schema gives no range
        else:
            params["strength"] = level
    if engine == "blur-v1" and read.stated("reach"):
        params["reach"] = read.unit("reach")
    if engine == "telablur-v1" and read.stated("direction"):
        params["direction"] = read.choice("direction")

    missing = IMAGE_SLOTS[engine][image_count:]
    if engine in MASKABLE and read.peek("partial") and not has_mask:
        missing.append("mask")
        reasons.append("Only part of the image should change: supply a mask "
                       "(white = apply the effect, black = keep).")
    if engine == "tessa-image-v1":
        if read.peek("real_hardware"):
            params["machine"] = "least_busy"
            reasons.append("Runs on real IBM hardware: it queues and costs more. Confirm first.")
        elif read.peek("noisy"):
            params["machine"] = "fake_fez"     # real-chip noise, simulated, no queue
    return Plan(engine, params, tuple(missing), read.confidence(), tuple(reasons))


def slot_files(plan: Plan, images: list[str], mask: str | None) -> dict[str, str]:
    """Assign the user's images to the chosen engine's slots, in order."""
    files = dict(zip(IMAGE_SLOTS[plan.engine], images))
    if mask and plan.engine in MASKABLE:
        files["mask"] = mask
    return files


def main() -> None:
    p = argparse.ArgumentParser(description="Route an image request to a Moth engine via TypeSafe")
    p.add_argument("request")
    p.add_argument("--image", action="append", default=[], help="image path (repeat for a morph)")
    p.add_argument("--mask", help="mask image: white = apply the effect, black = keep")
    p.add_argument("--run", action="store_true", help="submit the job (spends Moth credits)")
    p.add_argument("--yes", action="store_true", help="run even if the plan needs confirmation")
    p.add_argument("--out", default="outputs")
    args = p.parse_args()

    state = {"request": args.request, "attached_images": len(args.image)}
    plan = plan_from_answers(judge(state, build_questions()),
                             len(args.image), args.mask is not None)
    files = slot_files(plan, args.image, args.mask) if plan.engine else {}
    finish(plan, files, run=args.run, yes=args.yes, out=args.out)


if __name__ == "__main__":
    main()
