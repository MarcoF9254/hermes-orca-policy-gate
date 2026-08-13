# Read-only smoke runbook

How to drive the gate against a live Orca and prove the path end to end without any
mutation. Written 2026-08-13 from a run that actually completed; every command here was
executed and its output observed, not derived from the operator guide.

Read `docs/operator-guide.md` first for the safety model. This file covers only what that
guide does not: the environment the CLI actually requires, and the traps that cost time.

## What this proves

`preflight` on a read-only command evaluates policy, durably records the decision, invokes
the real Orca CLI, and returns Orca's own result as discovery evidence. A green run means
request → policy → decision → adapter → Orca → evidence is wired correctly.

It does not exercise mutation. `execute`, `owner-authorize`, `resolve-approval`, non-dry-run
`dispatch`, and every `worker*` command stay behind the separate live-mutation Owner gate.

## Preconditions

| | |
|---|---|
| Orca | installed and **running**; `orca status --json` must show `runtime.reachable: true` |
| Verified against | Orca 1.4.175 |
| Python | the repo `.venv` (3.12); `python -m hermes_orca_gate` works from the repo root |

Launching Orca is an Owner action. `orca open` blocks until the runtime is reachable, which
makes it a poor fit for an agent session — have the Owner open the app and confirm status
instead.

## Environment

`preflight` builds its request context from the environment and fails with
`controlled Orca identity environment is incomplete` if any of these is missing or malformed.
They must be set in the same shell session as the command.

```powershell
$env:ORCA_USER_DATA_PATH = "$env:APPDATA\Orca"
$env:ORCA_DEV_CLI_INVOCATION = "0"                      # must be exactly "0" or "1"
$env:HERMES_ORCA_GATE_CREDENTIAL_BINDING_ID = "<label>"
$env:HERMES_ORCA_GATE_PROCESS_EPOCH_ID = "<label>"
```

`orca` must also be on `PATH` — the adapter resolves it with `shutil.which` and binds the
executable's SHA-256 at request time.

`validate-policy` and `init-state` need none of this; they read no runtime context.

## Steps

```powershell
# 1. state directory - the gate does NOT create parents
New-Item -ItemType Directory C:\gate-state -Force | Out-Null
New-Item -ItemType Directory C:\gate-input -Force | Out-Null

# 2. policy and state
.\.venv\Scripts\python.exe -m hermes_orca_gate validate-policy --policy config\policy.example.json
.\.venv\Scripts\python.exe -m hermes_orca_gate init-state --state C:\gate-state\gate.db

# 3. a Run to read - created OUTSIDE the gate, as test setup only
orca orchestration run-create --objective "read-only smoke" --json

# 4. request file - exactly two keys, no BOM
# 5. preflight
.\.venv\Scripts\python.exe -m hermes_orca_gate preflight `
  --policy config\policy.example.json `
  --state C:\gate-state\gate.db `
  --request C:\gate-input\run-show.json
```

Step 3 is an out-of-flow Orca operation. It is test setup and must never be described as a
gate success.

Expected shape on success: `{"code":"PREFLIGHT_RESULT","result":{"decision":"allow",
"decision_event_id":...,"discovery":{"result":{...}}}}`, exit 0. Cross-check that
`decision_event_id` appears in `decision_events` in the state db — the decision is written
before Orca is invoked, so a failed invocation still leaves a record.

## Traps

Each of these was hit in a real run.

**The gate does not create parent directories.** `init-state --state C:\gate-state\gate.db`
returns `PERSISTENCE_ERROR: unable to open database file` when `C:\gate-state\` is absent.
The message does not name the directory.

**Request files must not carry a UTF-8 BOM.** Windows PowerShell 5.1 `Out-File -Encoding utf8`
writes one, and the gate rejects it with
`Unexpected UTF-8 BOM (decode using utf-8-sig)`. Write with:

```powershell
[System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding $false))
```

**A request file holds exactly `command` and `args`.** Context is assembled by the CLI from
the environment; supplying it yourself is rejected.

**Per-subcommand flag spellings.** Orca spells the same logical argument differently
depending on the subcommand. `COMMAND_FLAG_OVERRIDES` in `src/hermes_orca_gate/orca_cli.py`
carries the exceptions; the flat table carries the rule. When adding a command, check its
real `--help` rather than assuming.

| Command | Argument | Flag |
|---|---|---|
| `orchestration run-show` | `run_id` | `--id` |
| `orchestration task-list` | `run_id` | `--run` |
| `orchestration run-current` | `terminal_handle` | `--from` |
| `orchestration dispatch-show` | `return_preamble` | `--preamble` |
| `orchestration dispatch` | `return_preamble` | `--return-preamble` |

**Orca's stdout is UTF-8.** `subprocess_runner` pins the decoding. Without the pin, a
non-ASCII byte in Orca's output raises `UnicodeDecodeError` on a cp950 host and surfaces
misleadingly as `malformed Orca JSON envelope`. Decoding is deliberately strict — replacing
undecodable bytes would corrupt the payload the gate reasons about.

## Verified state at time of writing

All four read-only commands admitted by `config/policy.example.json` complete with exit 0:
`run-show`, `task-list`, `run-current`, `dispatch-show`.

`policy_sha256` `001b3d7ff5b769e6f0eec234134f6aa959f03410eaa3086c7ecef4471eb9a229`,
`source_contract_sha` `403c60bbadb734d870a22c372aca8a988e348d21`.

## Known open item

`SOURCE_CONTRACT_SHA` in `src/hermes_orca_gate/cli.py` is pinned to Orca source commit
`403c60bbad`. Both the policy and the generated request context use that same constant, so
the binding check compares a value against itself and cannot detect drift in the CLI surface
it names. The three flag mismatches above were real drift against that pin and were found by
running the commands, not by the check. Re-deriving the pin against the Orca revision the
installed CLI was built from is unresolved.
