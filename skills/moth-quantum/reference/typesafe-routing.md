# Natural-language routing with TypeSafe

Turn a sentence like *"make this photo dreamy but keep the face sharp"* into a Moth engine
call: which engine, which params, which files are still missing, and whether to ask the
user before spending credits. [TypeSafe](https://docs.typesafe.ai) System One models answer
typed questions (Choice, Score, Noul) with probabilities, so every value that reaches Moth
is one the engine's schema accepts.

Two runnable routers share [scripts/typesafe_core.py](../scripts/typesafe_core.py) (the
TypeSafe call, the `Plan`, and a `Reader` that tracks how certain each used answer was).
Both use the standard library only. The judge is TypeSafe (`TYPESAFE_API_KEY`) or a local or
cloud LLM (see [Without TypeSafe](#without-typesafe-a-local-or-cloud-llm-judge)); `--run` also
needs `MOTH_API_KEY`.

| Router | Engines |
| --- | --- |
| [scripts/typesafe_router.py](../scripts/typesafe_router.py) | `blur-v1`, `telablur-v1`, `deep-fryer-v1`, `tessa-image-v1` |
| [scripts/typesafe_music_router.py](../scripts/typesafe_music_router.py) | `blur-midi-v1`, `qrc-midi-v1`, `retrocausal-echo-v1`, `qrc-audio-v1` |

```bash
python scripts/typesafe_router.py "make it dreamy but keep the face sharp" --image photo.png
python scripts/typesafe_router.py "melt my cat into the dog, top to bottom" \
    --image cat.png --image dog.png --run
python scripts/typesafe_music_router.py "make my melody jazzier, just a little" --midi tune.mid
python scripts/typesafe_music_router.py "eerie backwards echoes on the beat" \
    --audio vox.wav --bpm 96
```

Without `--run` it prints the plan and spends no Moth credits (the TypeSafe call still uses
tokens). With `--run` it uploads, submits, waits and downloads.

Before changing it, read TypeSafe's live docs: the [HTTP API](https://docs.typesafe.ai/api.md),
[Confidence](https://docs.typesafe.ai/confidence.md) and the
[function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling.md), which
this design follows.

## The design

**One request, every question.** The engine choice and every argument any engine might need
go out together; TypeSafe answers them in parallel. Code then reads only the chosen engine's
answers. This costs extra tokens but saves a round trip.

**Closed sets only.** Each question maps to a value the Moth schema already allows: an enum
becomes a Choice whose option keys are the enum values; a 0–1 number becomes a Score whose
levels are described in plain words and mapped to `score / (levels - 1)`.

**Keep defaults unless the user said something.** Each optional argument gets a second Noul,
"does the user say anything about X?". Below 0.5, the argument is left out and the engine's
default applies. "Make it dreamy" therefore does not invent a `reach`.

**Confidence = the least certain judgment used.** Choice and Score answers carry
`confidence`. A Noul has none, so its decisiveness is `|p - 0.5| × 2`. Only answers the plan
actually used count; uncertainty on unused branches is ignored.

| Question | Type | Becomes | Used when |
| --- | --- | --- | --- |
| `engine` | Choice over engine ids + `none` | the engine to call | always |
| `strength`, `strength?` | Score (5 levels), Noul | `strength` 0–1 (deep-fryer: `gates: [["rx", x]]`) | blur, teleblur, deep-fryer, and stated |
| `reach`, `reach?` | Score (3 levels), Noul | `reach` 0–1 | blur-v1, and stated |
| `direction`, `direction?` | Choice (`full`, `vertical`, `horizontal`), Noul | `direction` | telablur-v1, and stated |
| `partial` | Noul | a `mask` is required | maskable engines |
| `noisy` | Noul | `machine: fake_fez` (real-chip noise, simulated) | tessa-image-v1 |
| `real_hardware` | Noul | `machine: least_busy` (real IBM hardware) | tessa-image-v1 |

## Policy: when code stops and asks

| Situation | Behaviour |
| --- | --- |
| `engine` is `none` | No call. Tell the user no image effect fits. |
| Plan confidence below `CONFIRM_BELOW` (0.6) | Show the plan; run only after the user confirms (`--yes`). |
| User wants part of the image changed but gave no mask | Ask for a mask: white = apply, black = keep. Never guess one. |
| Morph chosen with one image | Ask for the second image. |
| Real hardware requested | Always confirm: it queues and costs more. |

`CONFIRM_BELOW = 0.6` is an example. Measure it on real requests from your users: log the
plan, what the user actually wanted, and adjust. TypeSafe's typed output guarantees the
interface, not the judgment.

## The music router

The design is the same; two things differ.

**Scales that are not 0–1.** `variation` has no maximum, `theta_x` runs 0–π and means "how
strange the echoes are", and `mix` is best heard in three steps. Each Score level gets an
**anchor value**, and the probability-weighted level is interpolated between anchors. The
anchors are examples to tune by ear on your material:

| Score | Levels (plain words) | Anchors | Param |
| --- | --- | --- | --- |
| `variation` | close to the original · some variation · loose and surprising | 0.3 · 1.0 · 2.0 | `variation` (qrc-midi, qrc-audio) |
| `character` | clean, regular repeats · some flip and fade · inverted, fading, vanishing | 0.3 · 0.94 · 2.0 rad | `theta_x` (retrocausal-echo) |
| `mix` | a faint touch · balanced · mostly effect | 0.2 · 0.6 · 0.9 | `mix` (retrocausal-echo) |

`strength` and `reach` for `blur-midi-v1` map straight to 0–1, as for images.

**Free numbers come from flags, not from the model.** A tempo, a length or a time range is
not a closed set, so TypeSafe only judges whether the user *wants* it; code supplies the
number:

| Flag | Becomes | When TypeSafe says the user wants it but the flag is missing |
| --- | --- | --- |
| `--bpm` | `bpm` (echo, qrc-midi); one-bar `chunk_seconds = 240 / bpm` (qrc-audio) | "Echoes should follow the beat: pass --bpm" |
| `--length` | `length` (qrc-midi, qrc-audio) | engine default (10) |
| `--section TRACK:START:END` | `mask` entries for `blur-midi-v1` | "Only part of the piece should change: pass --section" |

Other music rules:

| Question | Becomes |
| --- | --- |
| `negative_mode` Choice (`invert`, `reverse`, `phase`) | `negative_mode`; `phase` also sets `theta_z = 0.79`, since phase mode needs `theta_z > 0` |
| `division` Choice (0.25 sixteenths … 1.0 quarters) | `division`, only with a BPM |
| `quality` Choice (`instant` … `complete`) | `quality` for the qrc engines |
| `map_only` Noul | `emit: "map"`: tap timings for a DAW, no rendered audio |
| `real_hardware` Noul | confirmation only: the plan stays on the simulator until a backend is chosen |

`retrocausal-echo-v1` without audio is valid (it renders its own impulse response), so the
router asks for confirmation rather than refusing. The `qrc-*` engines cost 5 credits; `--run`
prints the live cost before submitting.

## Without TypeSafe: a local or cloud LLM judge

[scripts/local_judge.py](../scripts/local_judge.py) answers the same questions with any
OpenAI-compatible endpoint that returns `top_logprobs` (llama.cpp / llama-swap, vLLM, hosted
APIs), and returns answers in TypeSafe's shape, so the routers do not change.

How it imitates a System One judgment:

1. Each question becomes a prompt whose answers are one-token labels: `A`, `B`, `C`… for a
   Choice, `0`–`9` for Score levels, `Y` / `N` for a Noul. The state goes in the system message,
   identical for every question, so the server reuses its prompt cache.
2. The model is asked for exactly one token (`max_tokens: 1`, thinking off). The probabilities
   it gives the labels become the distribution; a Score's value is the probability-weighted level.
3. `confidence` = 1 − normalised entropy of that distribution, multiplied by *coverage* (the
   share of probability the model put on valid labels at all). TypeSafe computes its own
   confidence differently, so thresholds do not transfer.
4. Questions run in parallel (`JUDGE_CONCURRENCY`, default 4). llama.cpp batches parallel
   requests, which shifts probabilities slightly between runs; set `JUDGE_CONCURRENCY=1` for
   identical answers every time (about 3× slower).

```bash
export JUDGE=local                              # default when TYPESAFE_API_KEY is unset
export JUDGE_BASE_URL=http://localhost:8080/v1  # default: llama-swap on this machine
export JUDGE_MODEL=Qwen3.6-35B-A3B-MTP          # default: the first model the server lists
export CONFIRM_BELOW=0.4                        # see "Tuning" below
python scripts/typesafe_router.py "make it dreamy but keep the face sharp" --image photo.png
```

For a hosted API, set `JUDGE_BASE_URL`, `JUDGE_API_KEY` and `JUDGE_MODEL` to a model that
returns logprobs. Reasoning models that hide logprobs cannot be used. llama.cpp's
`chat_template_kwargs` (to turn thinking off) is only sent to localhost endpoints.

### Results on this machine (2026-10-13)

`tests/eval_routers.py` runs 18 labelled requests (9 image, 9 music) through both routers:

| Judge | Passed | Time per request | Notes |
| --- | --- | --- | --- |
| Qwen3.6-35B-A3B-MTP (MoE), llama-swap | 18/18 | 2.9 s | 15/18 before three questions were reworded |
| Qwen3.8-27B (dense), llama-swap | 15/18 | 7.5 s | Too cautious on "did the user mention X?" Nouls, so stated settings were dropped |

The wording was tuned on this same set, so expect lower accuracy on new requests: add your own
cases to `eval_routers.py` before relying on it. The three rewordings (a narrower `none`
option, the effect's everyday name in each engine description, and "wants real hardware" kept
apart from "wants to see hardware noise") make the questions clearer for any judge, Jev included.

### Tuning the threshold

Local probabilities are flatter than Jev's: correct plans scored 0.15–1.00 (median about 0.6)
with the MoE model. At the default `CONFIRM_BELOW=0.6`, about half of the correct plans would
stop for confirmation. Lower it through the environment once your own evaluation shows where
wrong plans start; the 18 cases above contain no wrong plans, so they cannot set it.

## Refining a result from feedback

[scripts/typesafe_refine.py](../scripts/typesafe_refine.py) changes the last run instead of
starting over: "less backwards, more of the dry voice", "narrower, almost mono".

```bash
python scripts/typesafe_music_router.py "eerie backwards echoes on the beat" --audio vox.wav --bpm 96 --run
python scripts/typesafe_refine.py "narrower, almost mono"          # plan only
python scripts/typesafe_refine.py "narrower, almost mono" --run    # submit
```

Every `--run` (routers and refine) appends the run to `<out>/session.json`: engine, params,
input asset ids, job id and output asset ids. Job records from the API do not include params or
inputs, so the session file is the only memory. Refine reads its last entry.

**Dials.** Each supported engine lists the settings feedback can move (`DIALS` in the script):
echo `mix`, character (`theta_x`), repeats (`feedback`), tail (`decay`), `stereo_width`,
`negative_mode`; `blur-midi-v1` strength, reach, cleanup (`threshold`); `qrc-*` variation;
`qrc-audio-v1` crossfade; `blur-v1` and `telablur-v1` strength. For each dial the judge answers:

| Question | Type | Notes |
| --- | --- | --- |
| Does the feedback ask to change it? | Noul | names both concrete ends ("narrower, closer to mono" or "wider stereo") plus casual phrasings |
| Which way? | Choice `lower` / `higher` | each end in plain words, never "more/less of X" |
| How much? | Score: a little / a lot | one or two steps from the current value, clamped to the schema |
| Which option? (choice dials) | Choice | e.g. `invert` / `reverse` / `phase` |

The judge sees **only the feedback**. Changes are relative, and code applies them to the current
values; when the state also listed every setting's current value, the local model picked wrong
directions ("almost mono" → wider).

**Measurement reuse.** For `retrocausal-echo-v1`, a change that touches only playback settings
passes the previous run's IR asset, so the echo pattern stays identical and no new quantum
measurement runs. Changing `character`, or switching to `phase` (which adds `theta_z = 0.79`),
measures again, and the plan asks for confirmation. The credit price per job is the same either
way; reuse saves measurement time and keeps everything you didn't mention unchanged.

**Confidence** counts the yes/no and direction answers of the dials it changes plus every
dial's yes/no. "A little versus a lot" is left out: a wrong guess is one step off and the next
refine corrects it.

Results on this machine (Qwen3.6-35B-A3B-MTP, `JUDGE_CONCURRENCY=1`, 2026-10-14): 11/12 refine
cases pass, 5/6 on phrasings not used for wording. The miss: "can you pull the echoes back a bit"
lowered `mix` as expected but also shortened the tail. A real refine run ("narrower, almost
mono") changed only `stereo_width` 1.0 → 0.64 and reused the measured IR (`ir_source: supplied`).

## Adapting it to other engine families

Keep the structure; swap the closed sets. For sequences (`qrc-train-v2` / `qrc-gen-v2`), a
Score for `variation` with anchors and `length` from a flag covers most requests.

When you add an engine, read its live schema (`python scripts/moth.py engine <id>`) and use
its exact enum values as Choice option keys.

## Limits

- TypeSafe judges the words of the request, not the image or the sound itself.
- `deep-fryer-v1`'s schema gives no range for gate intensity; the router assumes 0–1, like
  the default of 0.5. Check results before trusting high values.
- `least_busy` on `tessa-image-v1` sends the job to whichever real IBM device is least busy.
