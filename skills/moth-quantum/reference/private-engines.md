# Private engines

A private engine has two halves: a **catalog definition** registered through the API, and a
**worker** that runs the program, deployed separately (see the engine authoring guides in the
moth-quantum GitHub organisation). Until a worker is running, jobs for the engine queue and
then time out.

Sources: https://docs.mothquantum.com/docs/working-with-engines and the `CreateEngineInputBody`
schema in https://api.mothquantum.com/openapi.json. Re-check both before registering.

## Prerequisites

- The account needs the `publish_engines` feature and an active state. Check first:
  `python scripts/moth.py me` lists the account's `features`. If `publish_engines` is absent,
  creation will be refused; the user should ask Moth to enable it.
- Know the program's params, input files and output files before writing the definition.

## Definition fields

| Field | Required | Notes |
| --- | --- | --- |
| `engine_id` | yes | `^[a-z0-9][a-z0-9-]{1,62}$`, e.g. `my-blur-v1`. Fixed after creation. |
| `name` | yes | 1–200 characters |
| `description`, `description_md` | no | ≤ 2000 / ≤ 20000 characters |
| `params_schema` | no | JSON Schema for `params`. Set `additionalProperties: false`; give every field a type, default, range and description. |
| `input_files` | no | `[{name, mime_types, required, description}]` |
| `output_files` | no | `[{name, content_types, required, description}]`; the worker reports files under these names. Empty = inline JSON result. |
| `run_policy` | no | `{timeout, max_retries, heartbeat}` in seconds; all three together |
| `error_codes` | no | `[{type, description}]` |
| `credits_per_run` | no | 0–100000, default 1 |
| `visibility` | no | `private` (default) or `public` |
| `version` | no | informational |
| `organization_id` | no | create as an organisation engine, if policy allows |
| `enabled`, `execution_mode` | no | defaults `true`, `handler` |

## Register

```python
from moth import call

definition = {
    "engine_id": "my-multi-coin-v1",
    "name": "My Multi Coin",
    "description": "Toss n quantum coins and return the counts of each outcome.",
    "version": "0.1.0",
    "params_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "coins": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2},
            "shots": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 100},
        },
    },
    "input_files": [],
    "output_files": [],
    "run_policy": {"timeout": 300, "max_retries": 0, "heartbeat": 30},  # example values
    "error_codes": [{"type": "invalid_params", "description": "params failed validation"}],
}
print(call("POST", "/engines", definition))   # 201 on success
```

Responses: `201` created; `409` usually means the `engine_id` is taken; `422` lists definition
errors; `403` no permission to publish.

Confirm the registration before deploying anything:

```bash
python scripts/moth.py engine my-multi-coin-v1   # visibility should be private
```

## Test once the worker runs

1. Submit a valid job and wait for `completed`.
2. Submit a deliberately invalid one (`{"coins": 99}`) and confirm a `422` that names the field.
3. Check the output shape: inline `result` if `output_files` is empty, otherwise one
   `outputs[]` entry per declared slot.

## Update, share, publish

- `PATCH /engines/{engine_id}` with only the fields to change. `engine_id` and `owner` cannot
  change; built-in `moth` engines cannot be modified.
- Sharing with specific people (`PUT /engines/{id}/permissions`) and transferring to an
  organisation (`POST /engines/{id}/transfer`) need a dashboard session, not an API key.
  Point the user to the dashboard.
- Make it public with `PATCH {"visibility": "public"}`, only when the user asks and after testing.

## Checklist

- [ ] `engine_id` ends in a version suffix (`-v1`), so a breaking change can ship as `-v2`
- [ ] `params_schema` is strict, with defaults, ranges and descriptions
- [ ] Slot names match the worker code exactly
- [ ] `run_policy.timeout` exceeds the slowest expected run, including any QPU queue
- [ ] `error_codes` covers the failures users can trigger
- [ ] The engine stays private until tested
