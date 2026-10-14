# Recipes

Each recipe uses `scripts/moth.py` from the skill directory. In Python, import its functions
(`upload`, `submit`, `wait`, `result`) after adding the `scripts/` folder to `sys.path`.
Check each engine's live page for current parameter names first.

## Smoke test (0 credits)

```bash
python scripts/moth.py me
python scripts/moth.py run tamagotchi-v1 --params '{}'
```

## Image: blur only inside a mask

```bash
python scripts/moth.py run blur-v1 \
  --params '{"strength": 0.7, "style": "ry", "reach": 0}' \
  --file image=photo.png --file mask=mask.png --out out/
```

The mask must match the image size. White = full effect, black = untouched.

## Parameter sweep, upload once

```python
from pathlib import Path
from moth import upload, submit, wait, result

image = upload(Path("photo.png"))
for strength in (0.2, 0.5, 0.9):
    job = submit("blur-v1", {"strength": strength}, {"image": image})
    if wait(job)["status"] == "completed":
        result(job, Path(f"out/strength-{strength}"))
```

Tell the user the total cost first (3 runs × `credits_per_run`).

## Sequence: train once, generate many

```python
train = submit("qrc-train-v2", {"sequence": ["A", "B", "C", "A", "B", "D"] * 10, "seed": 42})
assert wait(train)["status"] == "completed"
for variation in (0.3, 1.0, 2.0):
    gen = submit("qrc-gen-v2", {"length": 30, "variation": variation},
                 {"state": f"job:{train}/state"})
    wait(gen)
    print(variation, result(gen))
```

Read the generated tokens from the result or the output `state` file as the engine page
describes; save inline results immediately, since they expire.

## Audio: measure an echo once, mix it many ways

```python
from pathlib import Path
audio = upload(Path("vocal.wav"))
first = submit("retrocausal-echo-v1", {"bpm": 96, "division": 0.25, "theta_x": 1.4},
               {"audio": audio})
wait(first)
for mode in ("invert", "reverse"):
    job = submit("retrocausal-echo-v1", {"negative_mode": mode, "mix": 0.5},
                 {"audio": audio, "ir": f"job:{first}/ir"})
    wait(job)
    result(job, Path(f"out/{mode}"))
```

`submit()` resolves `job:{first}/ir` to the IR's asset id (the API itself accepts only asset
ids). With `ir` supplied, playback settings (`mix`, `negative_mode`, `bpm`, `division`…) apply
but measurement settings (`theta_x`, `theta_z`, `depth`…) are ignored, and no new measurement is
paid for. See the table in [engines.md](engines.md#midi-and-audio).
Use `emit: "map"` to get only the tap map for a DAW or plug-in.

## Simulator, noisy simulator, real hardware

Run the same small job three ways to show what hardware noise does:

| Engine | Simulator | Noisy simulator | Real hardware |
| --- | --- | --- | --- |
| `qpixl-v1` | `mode: emu, machine: aer` | `mode: emu, machine: fake_fez` | `mode: qpu, backend_name: ibm_fez` |
| `tessa-image-v1` | `machine: aer` | `machine: fake_torino` | `machine: ibm_<chip>` |
| `coin-toss-v1` | `mode: emu` | — | `mode: qpu` |

Ask before the real-hardware run: it queues and costs more.

## Compose with other skills

- **Natural-language requests → engine params.** TypeSafe Choice and Score judgments pick
  the engine and map words like "subtle" or "heavy" to `strength`; ask the user when
  confidence is low instead of spending credits. See [typesafe-routing.md](typesafe-routing.md)
  and `scripts/typesafe_router.py`.
- **Music generators that expose a score** (for example YuE2's ABC plan): convert the score
  to MIDI, run `blur-midi-v1` or `qrc-midi-v1`, convert back, and render. Keep the vocal
  track out of the blur mask so lyrics stay aligned.
