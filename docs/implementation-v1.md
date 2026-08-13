# Hermes–Orca Policy Gate v1 implementation record

## Identity and scope

- Normative specification: `docs/spec/option_b_spec_v0.5.md`
- Specification SHA-256: `6f5d855f31909b7fb9836a9e788628107159daf767212d8bac096ff94ad7aa63`
- Orca source-contract pin: `403c60bbadb734d870a22c372aca8a988e348d21`
- Implementation branch/baseline: `feat/v1-policy-gate` at `519cbc3e3b777e5787ab1ae915196bb9abc53a76`
- Runtime dependencies: Python standard library only
- Supported Python: `>=3.11,<3.13`

## Architecture mapping

| Contract area | Implementation |
|---|---|
| Closed typed domain | `models.py` exact command/field schemas, topology validation, case preservation, closed controlled context |
| Canonical binding | `canonical.py` sorted compact UTF-8 JSON, float/non-string-key rejection, SHA-256 golden vectors |
| Policy | `policy.py`, example policy, and JSON Schema; exact fields/version/enums; mandatory default block and 1..60000 ms freshness |
| Total state | separate dispatch/worker classifiers with mandatory unknown default and completion-evidence check |
| Orca adapter/discovery | `orca_cli.py`; argv lists, `shell=False`, `--json` once, independent refusal set, run-current/run/task/dispatch discovery, total failure classification, correlated command outcome schemas |
| Durable records | `stores.py`; separate immutable decision, Owner authorization, resolution, invocation, consumption, exec, outcome, recovery, provenance, reconciliation, and observation tables/events |
| Approval/execution | `service.py`; fresh preflight, separate Owner resolution, exact recomputation, atomic consumption, pre-exec marker, post-exec recovery/reconciliation |
| Observation | persistent provenance-bound wait/read loop, SQLite one-probe lease and 60-second interval, startup lease recovery, durable Owner continuation, exact timeout taxonomy, process deadline and absolute ceiling |
| CLI | `python -m hermes_orca_gate` / `hermes-orca-gate`; compact JSON and stable exit status mapping |

Immutable event tables have SQLite triggers refusing update/delete. Connections set WAL, `synchronous=FULL`, foreign keys, and busy timeout. Consumption uses `BEGIN IMMEDIATE` plus a unique operation ID. Startup sweep runs before every CLI state path and expands unknown operations to known intent/dispatch/task scopes.

The primary outcome append and recovery alert use separately invoked methods with independent failure injection. They currently share one database; no physical failure-domain independence is claimed.

## Canonical JSON v1

`canonical-json-v1` admits only JSON null, booleans, integers, strings, lists, and string-keyed maps. Floats and non-string map keys are rejected. Encoding uses `json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` followed by UTF-8. No Unicode, case, ref, ID, handle, or path normalization occurs. Omitted fields remain absent and therefore differ from explicit null/false/default fields.

`args_digest = SHA256(canonical_bytes(canonical_invocation_v1))`. Tests bind UTF-8 bytes and show context/case/presence variance changes the digest.

## Recovery TDD log

The recovery run first completed `uv sync --locked --group test` (`Checked 7 packages`). The abandoned workspace-write process was later found still writing this repository and was stopped by exact PID after its command line confirmed it was the failed run identified by the Owner. Useful tests/packaging were preserved; unjustified production files were deleted/recreated. A test-harness-only eager store import was made lazy and its collection error was not counted as feature RED.

Actual vertical slices:

| Slice | RED command and observed cause | GREEN command and result |
|---|---|---|
| Canonical JSON | `uv run --locked python -m pytest tests/test_canonical.py -q` → missing `hermes_orca_gate.canonical` | same → `2 passed`; growing canonical/classifier → `4 passed` |
| Total classifiers | growing run → missing `hermes_orca_gate.classifiers` | focused → `2 passed` |
| Policy loader | `... pytest tests/test_policy.py -q` → missing errors/policy behavior | focused → `10 passed`; growing → `14 passed` |
| Typed requests/adapter/observation | `... pytest tests/test_service.py tests/test_orca_cli.py -q` → missing service observation behavior | focused → `13 passed`; growing → `27 passed` |
| Durable stores | `... pytest tests/test_stores.py -q` → seven persistence contract failures against abandoned partial store | focused → `7 passed`; growing → `34 passed` |
| Service/executor | `... pytest tests/test_service.py -q` → missing `GateExecutor`/`GateService` | focused → `16 passed`; growing → `40 passed` |
| A01–A36 trace | `... pytest tests/test_contract_a01_a36.py -q` | `36 passed` (behaviors were already driven by focused REDs) |
| Policy artifacts | focused policy → two missing-file failures | focused → `12 passed` |
| CLI baseline | full suite → `80 passed, 1 failed` (`INVALID_INPUT` vs stable `INVALID_INVOCATION`) | CLI → `3 passed`; full → `81 passed` |
| Strict policy identity | focused policy → non-hex 40-character source identity incorrectly loaded | focused → `13 passed`; full → `82 passed` |
| Closed context/topology | focused service → five failures for partial intent and empty topology IDs | focused → `21 passed`; full → `87 passed` |
| Receipt/outcome truth | focused adapter → four failures for operation-unknown routing, incomplete start result, read-only malformed result | focused → `7 passed`; full → `91 passed` |
| Startup affected scopes | focused stores → intent scope not reconciled | focused → `8 passed`; full → `92 passed` |
| Executor precedence/acceptance | focused service → two failures for late reconciliation and accepted provenance | focused → `24 passed`; full → `95 passed` |
| Cross-process CLI intent | focused CLI → fresh resolution mismatch because intent was not reconstructed | focused → `4 passed`; full → `96 passed` |
| Adapter-owned discovery | focused adapter → missing `OrcaDiscovery`; focused service → discovery injection unsupported | adapter → `9 passed`; service → `25 passed`; full → `99 passed` |
| Gate-owned observation | focused service → missing `ObservationController`; focused CLI → caller-probe interface/missing clock | service → `27 passed`; CLI → `5 passed`; full → `102 passed` |
| Startup CLI precedence | focused CLI → status did not sweep consumed-without-outcome | CLI/full → `103 passed` |
| Reconciliation override | focused CLI → `owner_reconciliation` incorrectly accepted a block basis | focused corrected; full remained green |
| Manual reconciliation label | focused CLI → returned generic closure code | focused corrected to explicit manual/break-glass closure |
| Worker state discovery | focused adapter → lifecycle discovery lacked a provenance-bound dispatch-state route | focused → `10 passed`; full → `106 passed` |

Additional recovery slices observed real REDs for strict null rejection, observation-only routing, atomic outcome/provenance, affected startup scopes, same-intent recovery, replay labeling, no-op containment truth, CLI recovery selection, task-scope precedence, checkpoint interval/deadline handling, production evidence authority, policy-drift resolution, discovery/provenance correlation, outcome/request correlation, subprocess deadlines, exact reconciliation actions, from-terminal lineage, spawn/unknown-outcome audit, and completion-evidence default deny. Their focused GREEN runs ranged from 1 to 8 passing tests; the final frozen-tree count below supersedes intermediate counts.

The first frozen-tree review returned RED. Corrective slices then observed focused failures for mode-conflicting worker topology (`7 failed`), new-child/new-top-level repo/parent correlation, existing-terminal/worktree correlation, stale observation leases, output arriving during wait, durable continuation decisions, scheduler deadline clamping, read/dry-run execution before durable allow, missing Owner issuance metadata, and shallow A-trace assertions. Focused GREEN runs passed before the growing suite reached `165 passed`.

The second frozen-tree review found that startup recovery of an abandoned observation lease recorded `RUNTIME_UNKNOWN` but did not prevent immediate re-entry. The focused RED was `uv run pytest -q tests/test_stores.py::test_startup_sweep_recovers_abandoned_observation_lease` (`1 failed`: missing dispatch reconciliation barrier). Startup now records `OBSERVATION_EXECUTION_INTERRUPTED` reconciliation and `begin_observation_probe` refuses that scope until exact authorized closure; the focused observation GREEN was `10 passed`. The review also required a more substantive A01–A36 trace. The trace was upgraded to exercise real service/executor/discovery/store paths for the cited items; its GREEN was `37 passed`, followed by `165 passed` for the complete suite.

Counts above are the literal outcomes observed at each slice, not the final count. No environment/setup failure is claimed as a feature RED.

## Source-contract and authority details

The public request file contains only normalized command plus structured args. The CLI derives executable/cwd/environment/target/dev-mode/credential identity, discovery facts, monotonic timestamps, process epoch, decision IDs, mutation intent/request IDs, authorization IDs, resolution IDs, and operation IDs. Executor argv is reconstructed mechanically; caller raw argv is never accepted.

Discovery uses admitted read routes and exact JSON shapes. Dispatch/worker-start preflight cross-checks the explicit from-terminal with run-current, then performs run-show, task-list, and dispatch-show lineage checks. Dispatch-targeting discovery requires provenance correlated to the current source contract and target instance. Any query, identity, retry-lineage, completion-evidence, or result-shape mismatch is `RUNTIME_UNKNOWN`; schemas do not auto-widen.

Worker-start schemas are mode-exclusive. Existing-worktree mode refuses creation fields and requires an exact terminal that run-current correlates to the approved run/worktree. New modes refuse resolved/existing-terminal fields, bind the repo to the from-terminal, and bind new-child parent worktree exactly. Requested read-only operations, worker-read, and dry-run preview are deferred until the allow decision has been durably appended; prerequisite discovery remains read-only and is recorded as decision evidence.

Worker-start `operation_unknown` with trusted `accepted.dispatchId` atomically records the unknown outcome plus acceptance-only local provenance and opens reconciliation. It does not claim readiness/success. Without that ID it remains generic operation unknown. Credential rotation/unavailability refuses same-receipt recovery and opens intent reconciliation. Completed receipt replay is explicitly recorded as replay and never as a fresh effect.

## Exact limitations and residuals

1. No real Orca mutation, Orca startup, remote access, deployment, or source modification was performed in this implementation run.
2. Physical recovery-channel independence is unverified; primary and recovery records use separate methods/failpoints but one SQLite database.
3. Audit records are operational append logs, not tamper-proof or tamper-evident storage.
4. Direct Orca clients bypass the gate. OS account/ACL enforcement is outside v1.
5. Claude permission prompts can remain indistinguishable from busy timeout until the absolute ceiling.
6. The CLI requires a supervisor-controlled process-epoch environment value shared only within one supervised epoch. Rotation is mandatory on restart/handoff.
7. Manual reconciliation requires a separately bound `manual_break_glass` action and closed evidence schema; it remains Owner-reviewed/manual and never becomes normal gate provenance.
8. Exact Orca discovery/result shapes remain pinned to the reviewed source identity. The installed executable bytes are bound independently, but compatibility between those bytes and the reviewed source pin is an operator attestation, not a cryptographic proof; it remains unverified until a safe live read-only/dry-run smoke is performed.

## Verification commands

```text
uv lock --check
uv sync --locked --group test
uv run --locked python -m compileall -q src tests
uv run --locked python -m pytest -q
uv run --locked python -m hermes_orca_gate --help
uv run --locked python -m hermes_orca_gate validate-policy --policy config/policy.example.json
git diff --check
git status --short
```

Exact changed-path verification compares `git status --porcelain` paths with section 2 of `docs/owner-authorization-v0.1.md`; generated `.venv` and ignored `__pycache__` are excluded from source changes.

## Final local verification evidence

- `uv lock --check`: pass (`Resolved 7 packages`)
- `uv sync --locked --group test`: pass (`Checked 7 packages`)
- `uv run --locked python -m compileall -q src tests`: pass
- locked runtime import check: `imports ok`
- default locked suite: `165 passed in 8.13s`
- isolated Python 3.11 compile/tests: `165 passed in 7.36s`
- isolated Python 3.12 compile/tests: `165 passed in 15.79s`
- module help: all nine lifecycle subcommands listed
- example policy: `POLICY_VALID`, SHA-256 `001b3d7ff5b769e6f0eec234134f6aa959f03410eaa3086c7ecef4471eb9a229`
- exact changed-path comparison: `30/30`, no path outside the Owner allowlist
- `git diff --check`: pass

CI execution, safe live Orca read-only/dry-run smoke, and physical durability testing were not performed locally and are not claimed. An independent Claude read-only implementation review returned PASS with no blockers; its restricted review session inspected the full tree but did not independently rerun pytest or recompute the changed-tree manifest hash.
