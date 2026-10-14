---
name: moth-quantum
description: >
  Build with the Moth Quantum API: run quantum engines (image and MIDI blur,
  audio echo and remixing, quantum reservoir sequence generation, graph states,
  tomography, coin toss) from code, upload files as assets, chain jobs, and
  register private engines. Use when the user mentions Moth, mothquantum,
  a Moth engine id (blur-v1, qrc-train-v2, retrocausal-echo-v1, ...), MOTH_API_KEY,
  or wants a quantum effect on an image, audio, MIDI, or a number sequence.
  Read the live docs and the engine's schema before writing a call.
---

# Build with Moth Quantum

Moth Quantum wraps quantum programs as **engines**. Every engine is called the same way:
upload files if it needs any → submit a **job** → poll its status → fetch the result.
The platform validates params against each engine's JSON Schema, so a correct call
comes from reading that schema, not from guessing.

## Read the live docs first

**The live docs and the engine definition are the source of truth.** Engines are
added and updated often; parameter names, ranges, slots and costs change.

| Need | Read |
| --- | --- |
| Index of every page and engine | https://docs.mothquantum.com/llms.txt |
| One engine: params, slots, defaults, error codes, example script | `https://docs.mothquantum.com/docs/engines/<engine_id>` |
| The same, machine-readable, for the user's own key | `GET /engines/<engine_id>` (`params_schema`, `input_files`, `output_files`, `credits_per_run`, `run_policy.timeout`, `error_codes`) |
| Jobs, assets, errors, auth | `/docs/submitting-jobs`, `/docs/job-status-and-results`, `/docs/assets`, `/docs/errors`, `/docs/authentication` |
| Full API contract | https://api.mothquantum.com/openapi.json |

Before writing an integration, fetch the engine's page or `GET /engines/<id>` and use
its exact field names and slot names. If live access fails, say so and fall back to
[reference/engines.md](reference/engines.md), marking values as possibly stale.

## Platform facts

- Base URL: `https://api.mothquantum.com/api/v1`. Not `/v1`, not `/generation/...`.
- Auth: `Authorization: Bearer $MOTH_API_KEY` on every call. Keys start with `moth_`.
  Read the key from the environment; never hard-code, print or commit it.
- Rate limit: 300 requests/minute per key; `429` has no reset header, so back off a few seconds.
- Errors are `application/problem+json`: `title`, `status`, `detail`, `errors[]`
  (each with `location`, `message`, `value`). One `422` lists every problem: fix them all at once.
- `404`, never `403`, for anything you do not own. A 404 on your own resource means a wrong id.
- There is no webhook. Poll `GET /jobs/{id}/status` every 2–5 seconds.

## The call shape

```
POST /engines/{engine_id}/process
{"params": {...}, "input_files": {"<slot>": "<asset_id | job:<job_id>/<slot>>"}}
```

- Everything the schema defines goes **inside `params`**. Schemas set
  `additionalProperties: false`: a top-level or misspelled field is rejected.
- Files go in **`input_files`**, keyed by the engine's slot name, never in `params`.
  A previous job's output chains in as `job:<job_id>/<slot>` (e.g. `qrc-gen-v2`'s
  `state`, `retrocausal-echo-v1`'s `ir`, `qdrive-api-v1`'s `circuit`).
- Submit returns `202 {"job_id", "status": "queued"}`. Nothing has run yet.
- Status moves `queued → processing → completed`, or ends `failed` / `cancelled`.
  Those three are terminal. Treat unknown states (e.g. `fetching`) as processing.
  On failure read `error: {type, message, retryable}` before resubmitting.
- `GET /jobs/{id}/result` returns one of two shapes:
  - `result`: inline JSON for engines without file outputs. It expires: save it immediately.
  - `outputs[]`: one entry per output slot with a presigned `url` (fetch with **no** auth
    header, before `expires_at`). Each output is also an asset you own.
  - `409` = not finished or failed, `410` = inline result aged out.

## Uploading files (three calls)

1. `POST /assets` with `filename`, `content_type`, `size_bytes`.
2. `PUT` the bytes to `upload.url` with **exactly** `upload.headers`; type and length must match.
3. `POST /assets/{asset_id}/complete`. Until then the asset is `pending`, unusable, and still counts toward quota.

Images are scanned at completion (PNG or JPEG for scanned uploads); the limit is 100 MiB.
Reuse an `asset_id` across jobs: upload once, submit many. Delete downloaded outputs
(`DELETE /assets/{id}`) to free quota; check usage with `GET /me/storage`.

## Use the helper

`scripts/moth.py` is a dependency-free client (Python 3.9+, standard library only)
that implements all of the above, including upload, polling with backoff and output download.
Prefer it over hand-written HTTP code for scripts and one-off runs; copy its functions into
the user's codebase when they need an integration.

```bash
python scripts/moth.py me                               # check the key
python scripts/moth.py engines                          # list visible engines
python scripts/moth.py engine blur-v1                   # schema, slots, cost
python scripts/moth.py run coin-toss-v1 --params '{"shots": 100}'
python scripts/moth.py run blur-v1 --params '{"strength": 0.5}' \
    --file image=photo.png --out outputs/
python scripts/moth.py run qrc-gen-v2 --params '{"length": 30}' \
    --file state=job:<train_job_id>/state
```

`run` prints the credit cost before submitting. Pass `--dry-run` to validate the plan
without spending credits.

## Cost and hardware rules

- Show the user `credits_per_run` before running anything that costs 5 credits or more,
  or before a batch of jobs.
- Default to the simulator (`mode: "emu"`, `machine: "aer"`). Switch to real IBM hardware
  (`mode: "qpu"`, `machine: "ibm_<chip>"`, `backend_name`) only when the user asks for it.
  Real hardware can queue for minutes and costs more.
- `fake_<chip>` machines simulate a real chip's noise without the queue: use them to
  preview hardware-like results.
- Never put the user's IBM token (`qpu_token`) in code or logs. Leave it blank so Moth's
  own IBM account is used, unless the user explicitly provides their own.
- Keep `shots` at the default unless there is a reason. Above 8192 usually needs
  `allow_high_shots: true` and multiplies hardware time.

## Choosing an engine

Match the user's goal to an engine, then read that engine's live page. A summary with
the main knobs and what they do is in [reference/engines.md](reference/engines.md).

| Goal | Start with |
| --- | --- |
| Quantum effect on an image | `blur-v1`, `telablur-v1` (morph A into B), `deep-fryer-v1`, `tessa-image-v1` |
| 3D material / shader | `entanglement-shader-v1` |
| MIDI | `blur-midi-v1` (blur the piano roll), `qrc-midi-v1` (learn and regenerate) |
| Audio | `retrocausal-echo-v1` (quantum delay), `qrc-audio-v1` (learn and rearrange chunks) |
| Learn a token sequence, generate more | `qrc-train-v2` once, then `qrc-gen-v2` many times |
| Blur any numeric grid | `blur-core-v1` |
| Quantum physics tools | `otoc-echo-v1`, `qpixl-v1`, `graph-v1`, `qdrive-api-v1`, `tomography-api-v2` |
| Demos, games, teaching | `coin-toss-v1`, `labyrinth-v1`, `tamagotchi-v1` (0 credits) |

For worked multi-step flows (blur with a mask, train then generate, measure an echo once
and remix it many times), see [reference/recipes.md](reference/recipes.md).

## Plain-language requests

When an app must turn a user's sentence ("make it dreamy but keep the face sharp") into an
engine call, don't let free text choose params. Ask typed questions whose answers are the
schema's own values, keep engine defaults for anything the user didn't mention, and confirm
before spending credits when the least certain judgment is low.
[reference/typesafe-routing.md](reference/typesafe-routing.md) shows this with TypeSafe and
`scripts/typesafe_router.py` (image engines) and `scripts/typesafe_music_router.py`
(MIDI and audio engines) implement it. Without a TypeSafe key they run on a local or cloud
LLM through `scripts/local_judge.py` (`JUDGE=local`, default endpoint: llama-swap on
`localhost:8080`).

## Private engines

To publish the user's own quantum program, register a definition with `POST /engines`
and deploy its worker separately. Until a worker runs, jobs queue and time out. Follow
[reference/private-engines.md](reference/private-engines.md).

## Common mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| 404 on every call | Old or guessed base URL (`/v1/...`, `/generation/...`, `/status/{id}`) | Use `https://api.mothquantum.com/api/v1` and the paths above |
| 422 "additional properties are not allowed" | Params sent at the top level, or a field the schema lacks | Wrap them in `"params"`; read the schema for exact names |
| 422 or 415 about files | A file or `job:` reference put in `params`, or a wrong slot name | Put it in `input_files` under the slot name from `input_files[]` |
| Asset rejected in a job | `complete` never called, or the PUT headers differed | Run all three upload steps; send `upload.headers` unchanged |
| 410 on result | Inline result fetched too late | Fetch and save `result` right after completion |
| Job stuck in `queued`, then fails | Private engine has no running worker, or QPU queue | Check the worker; use `emu` for development |
| 429 | Polling too fast or a tight batch loop | Poll every 2–5 s; add backoff |
| 403 "Error 1010: Access denied" (Cloudflare) | Python's `urllib` sends its default `Python-urllib` user agent, which the API edge blocks | Send your own `User-Agent` header (the helper does); `requests` and `fetch` are not affected |

## Verify

After any integration: run the cheapest representative call, confirm the status reaches
`completed`, and check that the output has the expected shape (inline `result` vs
`outputs[]`). When reporting a problem, include the `job_id` or `asset_id`, the time
and the full error body, never the API key.
