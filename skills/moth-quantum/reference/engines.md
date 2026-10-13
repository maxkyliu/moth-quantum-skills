# Engine map

Snapshot of the public catalog taken 2026-10-11 (moth-api v0.41.0). **Verify against the live
engine page or `GET /engines/<id>` before relying on a value**: names, ranges, defaults and
credit costs change.

Notation: `param = default` and what turning it up does. Slots are `input_files` names;
`→` lists output slots (no outputs = inline JSON `result`).

## Image

| Engine | Credits | Slots | Main knobs |
| --- | --- | --- | --- |
| `blur-v1` Quantum Blur | 1 | `image` (req), `mask` → `result` | `strength = 0.5` (0 unchanged, 1 maximum); `style = rx` (`rx`/`ry`, different interference texture); `reach = 0` (1 = non-local, any pixel can affect any other); `size = 1024` (pixels per pass, 8–1024); `downscale = true` (false tiles large regions) |
| `telablur-v1` Quantum Teleblur | 1 | `image1`, `image2` (req), `mask` → `result` | Morphs image1 toward image2. `strength = 0.5`; `direction = full` (`vertical`, `horizontal`) |
| `deep-fryer-v1` Deep Fryer | 1 | `image` (req), `mask` → `result` | `gates = [["rx", 0.5]]` (sequence of gate, intensity pairs); `tile_size = 4` (2–4) |
| `tessa-image-v1` Tessa Image | 1 | `image` (req) → `result` (PNG) | Colour → sphere round trip. `machine = aer` (`fake_<chip>` adds real noise); `distortion = 0`; `gate_pauli = XZ`; `fixed_palette = false` (for flat graphics); `shots = 4096` |
| `entanglement-shader-v1` | 1 | none → `result` (ZIP of OSL, GLSL, HLSL, MaterialX…) | `reflectance = 0.2`, `absorption = 0.95`, `layers = 2`, `incoming_rays = 8`, `interaction = 1` (sign changes colours), `style = peaked` |

Masks: black keeps the original, white takes the full effect, grey blends. Without a mask,
`blur-v1` uses the image's alpha channel.

## MIDI and audio

| Engine | Credits | Slots | Main knobs |
| --- | --- | --- | --- |
| `blur-midi-v1` Blur Jazz | 1 | `midi` (req) → `result` | `strength = 0.5`; `reach = 0`; `threshold = 0.1` (higher = fewer stray notes); `margin = 0.15`; `mask = [[track, start_s, end_s], ...]` |
| `qrc-midi-v1` QRC MIDI | 5 | `midi`, `model` → `result`, `state`, `model` | `length = 10` notes (≤ 500); `variation = 1` (low = faithful, high = surprising); `quality = moderate` (`instant`/`fast`/`complete`); `bpm = 120`; `velocity = 100`; `seed` |
| `qrc-audio-v1` QRC Audio | 5 | `audio`, `chunks` (zip), `model` → `result` (WAV), `state`, `vocabulary`, `model` | `chunk_seconds = 1`; `length = 10` chunks; `crossfade = 0` ms; `variation = 1`; `quality = moderate`; `loop = true`; `seed` |
| `retrocausal-echo-v1` Retrocausal Echo | 2 | `audio`, `ir` → `result`, `ir`, `taps` | `theta_x ≈ 0.94` ("how quantum": low = regular taps, high = inverted/erased); `theta_zz ≈ 1.10`; `depth = 8`; `n_sites = 8` (stereo positions); `master_ms = 640` or `bpm` + `division`; `mix = 0.6`; `feedback = 0`; `decay = 0.9`; `negative_mode = invert` (`reverse`, `phase` with `theta_z > 0`); `emit = audio` (`map` = tap map only) |

`retrocausal-echo-v1` sums stereo input to mono. Without `audio` it renders its own impulse
response (`ir_seconds = 3`). Feed its `ir` output back as `input_files.ir` to remix without
paying for the measurement again.

## Sequences

| Engine | Credits | Slots | Main knobs |
| --- | --- | --- | --- |
| `qrc-train-v2` QRC Train | 5 | none → `state` | `sequence` (tokens: ints, strings…); `num_qubits = 5` (2–12); `epochs = 50`; `sample_length = 15`; `washout = 5` (< sample_length); `mixing = 0.7`; `shots = 3000`; `vocabulary`; `seed` |
| `qrc-gen-v2` QRC Generate | 1 | `state` (req) → `state` | `length = 10`; `variation = 1` (temperature); `initial_events`; `random_seed`; `vocabulary` (same size swap) |

The trained `state` must go in `input_files.state` (`job:<train_job_id>/state`), not in `params`.

## Quantum tools

| Engine | Credits | Slots | Main knobs |
| --- | --- | --- | --- |
| `blur-core-v1` Quantum Blur Core | 1 | none | `values` (rectangular nested list of non-negative numbers); `strength = 0.5` (number or per-axis list); `axes`; `style = x` (letters x, y in order); `reach = 0`; `shots` (adds shot noise); `max_qubits = 20` |
| `otoc-echo-v1` Quantum Echo | 1 | none | `n_sites = 8`; `depth = 8`; `theta_x`, `theta_zz`, `theta_z`; `kick = Z`; `kick_site`; `disorder = 0` + `seed`; `lattice = chain` (`square` needs `width`, `height`); `machine = aer` |
| `qpixl-v1` Qpixl | 1 | none | `values`; `mode = emu`; `machine = aer` (`fake_<chip>`); `shots = 4096`; `dynamic_range = none` (`min_max`, `percentile`); `discretize = 0` |
| `graph-v1` Quantum Graph | 5 | none | `num_qubits = 4` (2–20); `coupling_map`; `operations` (`bloch`, `relationship`); `seed`; `shots = 1024`; `mode = emu` |
| `qdrive-api-v1` QDrive | 1 | `initial_circuit` → `circuit` (QASM3) | `n_qubits`; `targets` (desired expectation values); `update_method = spectral`; `tomography = 0` (1, 2); `machine = aer` |
| `tomography-api-v2` Tomography | 1 | none | `circuit_qasm` (OpenQASM 2, required); `qubit_list`; `qubit_pair_list`; `shots = 4096`; mutual-information toggles |

## Demos and games

| Engine | Credits | Main knobs |
| --- | --- | --- |
| `coin-toss-v1` Coin Toss | 2 | `shots = 10`; `mode = emu` (`qpu` = real IBM hardware) |
| `labyrinth-v1` Quantum Labyrinth | 5 | `level_data`; `steps = 3` (≤ 5); `fraction ≈ 0.33`; `k = 3`; `shots = 4096`; `top_n = 16`; `mode = emu` |
| `tamagotchi-v1` Tamagotchi | 0 | `actions` (`[gate, target]`, gates I X Z H S CX SE); `code = steane`; `n_logical = 1`; `noise`; `shots = 1024` |

`tamagotchi-v1` costs nothing: use it to smoke-test a new integration end to end.
