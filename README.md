# Moth Quantum Agent Skills

An agent skill for building with the [Moth Quantum](https://docs.mothquantum.com) engine API:
run quantum engines on images, MIDI, audio and number sequences, chain jobs, and register
private engines.

| Skill | Purpose |
| --- | --- |
| [moth-quantum](skills/moth-quantum/SKILL.md) | Read live docs first, call engines correctly, control credit and hardware use, publish private engines |

## Contents

```
.claude-plugin/plugin.json         plugin manifest
.claude-plugin/marketplace.json    marketplace manifest
skills/moth-quantum/
  SKILL.md                         workflow, rules, engine choice, common mistakes
  scripts/moth.py                  dependency-free client and CLI (Python 3.9+)
  scripts/typesafe_core.py         shared TypeSafe call, Plan and Reader for the routers
  scripts/typesafe_router.py       plain-language request → image engine call, via TypeSafe
  scripts/typesafe_music_router.py plain-language request → MIDI / audio engine call
  scripts/local_judge.py           TypeSafe-compatible judge on a local or cloud LLM (logprobs)
  reference/engines.md             catalog snapshot with main parameters
  reference/recipes.md             multi-step flows (masks, sweeps, train/generate, echo remix)
  reference/private-engines.md     registering and testing your own engine
  reference/typesafe-routing.md    routing design, confidence policy, adapting to other engines
tests/test_moth.py                 offline unit tests for the client
tests/test_router.py               offline unit tests for the image router
tests/test_music_router.py         offline unit tests for the music router
tests/test_local_judge.py          offline unit tests for the local judge
tests/eval_routers.py              labelled evaluation against a live judge (manual)
```

## Install

### Claude Code plugin

```bash
claude plugin marketplace add maxkyliu/moth-quantum-skills
claude plugin install moth-quantum@moth-quantum-skills
```

### As a plain skill

Copy `skills/moth-quantum/` into `~/.claude/skills/` (global) or `.claude/skills/` (project).

## Use

Set the key, then ask the agent, for example:

```bash
export MOTH_API_KEY="moth_…"
```

> Use the moth-quantum skill to quantum-blur every PNG in `photos/` at strengths 0.2, 0.5 and 0.9, and tell me the credit cost first.

The client also works on its own:

```bash
python skills/moth-quantum/scripts/moth.py run tamagotchi-v1 --params '{}'
```

## Tests

```bash
python -m pytest tests/
```

## License

[MIT](LICENSE).
