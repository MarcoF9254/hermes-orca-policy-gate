# Hermes–Orca Policy Gate v1 operator guide

## Safety model

This program is an external deterministic gate for the supervised Hermes → Orca flow. It does not modify Orca and cannot prevent an operator or another client from calling Orca directly. Direct calls are outside the gate and are never recorded as gate successes.

The v1 adapter rejects federation, `worker-start --on`, release/retain, every `orchestration check` variant, unknown fields, caller argv, caller cwd/environment overrides, and context defaults. Actual mutations and `worker-show` require an exact single-use `ApprovedInvocation`. The example policy requires Owner authorization for every mutation and defaults to block.

## Install and initialize

Python 3.11 or 3.12 and `uv` are required.

```powershell
uv sync --locked --group test
uv run --locked python -m hermes_orca_gate validate-policy --policy config/policy.example.json
uv run --locked python -m hermes_orca_gate init-state --state C:\gate-state\gate.db
```

The supervising process, not an individual request, controls these values:

```powershell
$env:ORCA_USER_DATA_PATH = 'C:\Users\operator\AppData\Roaming\Orca'
$env:ORCA_DEV_CLI_INVOCATION = '0' # exact 0 or 1
$env:HERMES_ORCA_GATE_CREDENTIAL_BINDING_ID = 'orca-credential-v3'
$env:HERMES_ORCA_GATE_PROCESS_EPOCH_ID = 'supervisor-epoch-2026-08-10T09:00Z'
```

`HERMES_ORCA_GATE_PROCESS_EPOCH_ID` must be a new unpredictable, case-sensitive value for each long-lived supervised gate process epoch. All lifecycle commands delegated by that same supervisor use the same value. A restart/handoff rotates it; stale invocations then fail and require fresh discovery and a new decision. Do not copy an old value to bypass freshness.

The gate resolves the installed `orca` executable, canonical cwd, `ORCA_USER_DATA_PATH`, dev-mode input, target-instance digest, and credential binding itself. Request JSON contains only `command` and `args`.

## Safe read-only and preview pilot

A read-only request example:

```json
{"command":"orchestration.runShow","args":{"run_id":"Run-A"}}
```

```powershell
uv run --locked python -m hermes_orca_gate preflight `
  --policy config/policy.example.json `
  --state C:\gate-state\gate.db `
  --request C:\gate-input\run-show.json
```

For read-only commands, preflight executes the admitted read and returns its discovery/result evidence. A dispatch preview is a separate route:

```json
{"command":"orchestration.dispatchDryRun","args":{"run_id":"Run-A","task_id":"Task-A","from_handle":"Hermes-A","dry_run":true,"return_preamble":false}}
```

The adapter first validates run/task/dispatch lineage, durably appends the allow decision, and only then invokes `dispatch --dry-run --json`. Blocking policy or decision-persistence failure prevents the requested read/preview. Preview rejects inject and retry-request, creates no provenance, and cannot substitute for later mutation approval.

No real Orca mutation was performed during implementation validation.

## Mutation approval lifecycle

Example typed dispatch request:

```json
{"command":"orchestration.dispatch","args":{"run_id":"Run-A","task_id":"Task-A","to_handle":"Worker-A","from_handle":"Hermes-A","inject":false,"return_preamble":false,"dry_run":false}}
```

1. Run `preflight`. The gate performs admitted read-only discovery, appends the immutable decision, creates the mutation intent/request ID internally, and returns `decision_event_id`.
2. If the result is `require_approval`, record exact Owner authorization:

   ```powershell
   uv run --locked python -m hermes_orca_gate owner-authorize `
     --state C:\gate-state\gate.db `
     --decision <decision_event_id> `
     --kind owner_approval
   ```

3. Use the returned `authorization_id` for fresh resolution:

   ```powershell
   uv run --locked python -m hermes_orca_gate resolve-approval `
     --policy config/policy.example.json `
     --state C:\gate-state\gate.db `
     --authorization <authorization_id> `
     --request C:\gate-input\dispatch.json
   ```

   Resolution repeats discovery and appends a new linked immutable decision. It never rewrites the basis decision.

4. `execute` accepts only the returned unconsumed operation ID and the same typed request:

   ```powershell
   uv run --locked python -m hermes_orca_gate execute `
     --policy config/policy.example.json `
     --state C:\gate-state\gate.db `
     --operation <operation_id> `
     --request C:\gate-input\dispatch.json
   ```

This command performs a real Orca mutation when all checks pass. Do not run it without the separate live-mutation authority required by the Owner gate.

## Observation

Observation accepts a proven-local dispatch ID, derives its terminal from durable provenance, holds a SQLite serial-probe lease, and mechanically invokes terminal read plus canonical `terminal wait --for tui-idle --timeout-ms 60000 --json`.

```powershell
uv run --locked python -m hermes_orca_gate observe `
  --policy config/policy.example.json `
  --state C:\gate-state\gate.db `
  --dispatch <known-local-dispatch-id>
```

The command remains running across checkpoints until it reaches approval-required or `RUNTIME_UNKNOWN`. Each checkpoint waits first and then reads from the prior durable cursor, so output arriving during the wait resets the idle counter. Three consecutive idle/no-output checkpoints require approval. Exact Orca error code `timeout` means still busy and resets the counter. A typed `blockedReason` immediately requires approval. Other errors/malformed data become `RUNTIME_UNKNOWN`. The absolute policy ceiling is independent of worker-start timeout.

Completed checkpoints are separated by the policy's exact 60-second interval. The Orca wait argument remains exactly 60000 ms; a separate process deadline enforces the absolute ceiling and the clock is rechecked after external calls.

A crash-held observation lease is cleared on the next gate startup, resets the counter, and appends a `RUNTIME_UNKNOWN` recovery event. When observation requires approval it returns a continuation decision ID. To continue, the Owner selects and binds a new absolute monotonic deadline:

```powershell
uv run --locked python -m hermes_orca_gate owner-authorize `
  --state C:\gate-state\gate.db `
  --decision <observation-decision-id> `
  --kind owner_approval `
  --observation-deadline-ms <future-monotonic-ms>

uv run --locked python -m hermes_orca_gate observe `
  --policy config/policy.example.json `
  --state C:\gate-state\gate.db `
  --dispatch <known-local-dispatch-id> `
  --continue-authorization <authorization-id>
```

The continuation resets the counter, updates mutable observation state, and appends an immutable continuation event; an expired deadline is never silently reused.

## Reconciliation

Startup always sweeps consumed operations without a terminal outcome/recovery closure. It opens reconciliation for every known affected operation, intent, dispatch, and task before ordinary policy paths.

Normal approval cannot clear reconciliation. Create an `owner_reconciliation` authorization only from an exact `reconciliation_required` decision and open scope. Bind exactly one action: `receipt_recovery` for the normal fresh-resolution/execute path, or `manual_break_glass` for an explicitly manual closure.

```powershell
uv run --locked python -m hermes_orca_gate owner-authorize `
  --state C:\gate-state\gate.db `
  --decision <reconciliation-decision-id> `
  --kind owner_reconciliation `
  --scope operation:<operation-id> `
  --reconciliation-action manual_break_glass
```

The manual evidence file is a closed object whose fields are exactly `evidence_kind`, `scope`, `finding`, and `source_contract_sha`. `evidence_kind` is `independently_sourced_owner_evidence`; scope must match; finding is one of `locality_established`, `no_effect_confirmed`, or `contained`; and the source pin must be `403c60bbadb734d870a22c372aca8a988e348d21`.

```powershell
uv run --locked python -m hermes_orca_gate reconcile `
  --state C:\gate-state\gate.db `
  --scope operation:<operation-id> `
  --authorization <owner-reconciliation-authorization-id> `
  --evidence C:\gate-input\owner-reviewed-evidence.json
```

Same-intent receipt recovery starts with `preflight --recover-intent <mutation-intent-id>`, uses a new decision/operation, and reuses the exact Orca request ID only with identical method, effective payload, target instance, and credential binding. If reconciliation is open, authorize `--reconciliation-action receipt_recovery`, then run fresh `resolve-approval` and `execute`. Credential rotation/unavailability makes receipt recovery unavailable and requires reconciliation. A completed replay is labeled replay rather than a new effect. A pending worker-start receipt may establish only accepted dispatch identity/provenance; it never proves worker success. Missing `accepted.dispatchId` remains generic `operation_unknown`.

## Exit codes and output

Every command prints one compact JSON object.

- `0`: successful gate/read/preview/result operation
- `1`: deterministic denial/failure or no mechanical success
- `2`: invalid invocation/input/policy
- `3`: outcome unknown or reconciliation required

## Backup and upgrade

Stop the supervised flow before backup. Copy the SQLite database together with its `-wal` and `-shm` files, or use SQLite's online backup API from a controlled maintenance tool. Test restoration before relying on it.

Before upgrading, verify the Orca CLI/source contract remains compatible with `403c60bbadb734d870a22c372aca8a988e348d21`. Drift fails closed; do not edit policy/source identities to force compatibility. Back up state, validate the new policy, run the full locked test suite, and use a fresh process epoch.

## Explicit bypasses and residual risks

- Direct Orca GUI/CLI/API use bypasses this gate; OS account/ACL isolation is deployment work, not a v1 claim.
- SQLite logs are operational append-only records, not tamper-proof evidence.
- The recovery channel is separately invoked and independently failure-injectable, but currently shares the same SQLite database and physical failure domain. Physical independence is not claimed.
- Federation is refused; remote dispatch identities are not reconstructed.
- Claude permission prompts can appear as repeated terminal-wait timeouts because Orca's typed `blockedReason` is Codex-specific. Detection can therefore wait until the absolute ceiling.
- Manual/break-glass reconciliation depends on Owner-reviewed independent evidence and is labeled manual; an assertion alone never creates normal local provenance.
- Source-contract or JSON-envelope drift becomes `RUNTIME_UNKNOWN`/reconciliation, not automatic schema widening.
- Executable bytes and the reviewed source-contract pin are bound separately. Their compatibility requires controlled operator verification and is not inferred from a matching-looking CLI name.
- Existing-worktree worker-start is deliberately narrowed to require an exact terminal/worktree relationship. New worktree modes reject existing/resolved terminal fields and require the requested repo (plus new-child parent) to match the from-terminal discovery evidence.
