# Quantum harmony for YuE2 songs

[YuE2](https://github.com/multimodal-art-projection/YuE) writes a song as an editable ABC
score before rendering audio. [scripts/yue2_harmony.py](../scripts/yue2_harmony.py) runs that
score's chord progression through `blur-midi-v1` and writes back new chord symbols. The
melody, lyrics alignment, bars and tempo stay byte-for-byte the same.

```bash
export YUE2_DIR=~/projects/YuE                       # a YuE checkout (for its ABC parser)
python scripts/yue2_harmony.py chords score.abc      # show the progression
python scripts/yue2_harmony.py blur score.abc score-quantum.abc --params '{"strength": 0.3}'
# then render in the YuE checkout:
python examples/generate.py --request examples/song.json --abc-file score-quantum.abc \
    --cot full --output outputs/quantum
```

`blur` costs 1 credit. `to-midi` and `apply` split it into steps: export the chords, blur the
MIDI any way you like, rename from the result.

## How it works

1. **Read the lead sheet.** YuE2 scores carry a Vocal and an Ins melody plus quoted chord
   symbols in the Vocal part; YuE2 invents the accompaniment from those chords. The tool uses
   YuE2's own `abc_tools.parse_abc` for exact chord onsets.
2. **Chords → MIDI.** Each chord becomes a bass note (octave 3) plus close chord tones above
   middle C, held for the chord's span. Standard-library MIDI I/O lives in `midi_io.py`.
3. **Blur on Moth.** `blur-midi-v1` spreads note energy to neighbouring pitches and times.
4. **MIDI → chords.** For each original chord's span, weight every pitch class by
   overlap × velocity and pick the best match among the chord qualities YuE2 accepts (major,
   `m`, `dim`, `aug`, `7`, `maj7`, `m7`, `dim7`, `m7b5`, `sus4`, `sus2`, `6`, `m6`, `7sus4`,
   `m(maj7)`), by cosine similarity with a small bonus for keeping the root and a small cost
   per extra tone. The original chord stays unless another fits clearly better (similarity
   margin 0.05): seventh chords share three tones and otherwise tip between neighbours.
   The bass is the strongest pitch class below middle C. New slash chords are opt-in
   (`--slashes`): blur leaks the bass to its semitone neighbour, which otherwise put a slash
   on nearly every chord, and YuE2 mostly ignores slash basses anyway.
5. **Swap only the quoted text** and verify with YuE2's `compare` that every note, bar and the
   tempo are unchanged.

**Palette.** The blur works on a chromatic piano roll, so at higher strengths it pushes energy
onto semitone neighbours: named freely, C became `C#maj7/C`. The default `--palette key`
weights out-of-key notes at 0.2 and only accepts new chords that fit the score's key (the
original chord is always allowed), so blur moves harmony between related chords of the key.
`--palette chromatic` keeps the raw result.

## The change dial (`--change`)

`strength` does not mean the same amount on every song: at 0.5 it changed 57% of City Lights'
simple triads but 81% of Sukie's dense seventh chords. `--change 25%` instead fixes the share:

```bash
python scripts/yue2_harmony.py blur score.abc out.abc --params '{"strength": 0.5}' --change 25%
python scripts/yue2_harmony.py apply score.abc blurred.mid out-10.abc --change 10%   # reuse a blur
```

1. Blur once at a high strength (0.5).
2. For each chord, take the best alternative the blur suggests and its advantage: its fit minus
   the original chord's fit, both measured on the unweighted blurred notes (the key palette only
   decides which alternatives are allowed; down-weighted notes made intact out-of-key chords such
   as Caug in A major look wrong).
3. Eligible: chords whose alternative fits better (advantage > 0). Rank all recolourings that keep
   the root (F → Fmaj7) before any substitution with a new root (Am → Fmaj7); within each group, the
   largest advantage first. A mild penalty for new roots was not enough on dense harmony.
4. Apply the top `round(share × chords)`; fewer if fewer are eligible.

The ranking does not depend on the share, so 25% contains every 10% change plus more. One blur
(1 credit) gives any number of settings.

| Song | 10% | 25% | 50% |
| --- | --- | --- | --- |
| City Lights (21 chords) | 2: C → Cmaj7 | 5: + F → Fmaj7 | 10: + Csus4, first root change Fmaj7 → Em |
| Sukie (128 chords) | 13, all same-root | 32, all same-root | – |

## Results (City Lights, YuE2's generated score, C major, 21 chords, 2026-10-14)

| `strength` | Chords changed | What changed |
| --- | --- | --- |
| 0.15 | 0/21 | nothing: the blur stays inside each chord |
| 0.3 | 7/21 | colour: `C → Csus4`, `F ↔ Fmaj7`, one `Am/E` |
| 0.5 | 13/21 | also substitutions: `Am → Fmaj7`, `G → Em`, an `Fmaj7/E` bass line |

| Song | `strength` 0.3 | `strength` 0.5 |
| --- | --- | --- |
| City Lights (21 chords, C major, triads) | 5/21 | 12/21 |
| Sukie (128 chords, A major, dense sevenths; SheetSage2 transcription) | 63/128 | 104/128 |

The first table predates the keep-margin and slash rules; the second uses them (City Lights
then changes 0, 5 and 12 chords at 0.15, 0.3 and 0.5). Dense seventh-chord harmony moves much
more at the same strength.

**Does YuE2 play the new chords?** Measured as the strongest bass pitch class per chord span
after aligning score to audio: on Sukie at 0.5, of 61 chords whose bass changed, the render's
bass matched the new chord 32 times and the old one 3 times (0.3: 15 vs 6). On City Lights root
changes came through but slash basses mostly did not. Rough measure: drums, melody and timing
drift add noise.

Round trips without blur return all three YuE2 example scores byte-identical. These results
are symbolic; whether a progression sounds better needs a YuE2 render and listening.

## Running YuE2 on an NVIDIA GB10 (aarch64)

Tested 2026-10-14: install `torch==2.10.0` from `https://download.pytorch.org/whl/cu130`
before `pip install .`. PyTorch warns that it supports compute capability up to 12.0 and the
GB10 is 12.1; BF16 matmul and attention run correctly (about 40 TFLOPS). The first run
downloads 7.3 GB (model) + 0.5 GB (decoder); a 58 s song then took 160 s including about
100 s of model loading.

YuE2's weights are CC BY-NC 4.0 with a creator permission: free for personal use and for
monetising your own songs; a company offering this as a service needs YuE2's commercial
license.

## Covering a recording (instrumental)

Tested on a 5.4-minute song: SheetSage2 transcription (`--task full`, keeps chords) in its own
Python 3.11 venv with `torch==2.8.0` from the `cu129` index (no aarch64 2.8.0 wheel on cu128/cu126),
then YuE2's instrumental tool (`instrumental.py cover --abc … --keep-harmony --prepare-only`) moves
the vocal melody to Ins, then `yue2_harmony.py blur` on the prepared score, then render each
version with the same request and seed. Transcription took 27 s; each full render about 4–6 min.
Renders can differ a lot in loudness and arrangement density: loudness-match before comparing.
