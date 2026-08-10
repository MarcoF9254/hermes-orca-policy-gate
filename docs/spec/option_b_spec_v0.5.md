# Option B — Hermes↔Orca Policy Gate Specification v0.5

**Status:** DRAFT — full review required. This artifact incorporates the Owner-approved consolidated profile from `option_b_v0.5_owner_decision_package_v0.2.md`. It is **not implementation-authorizing**, and implementation remains expressly unauthorized.

**Supersedes:** v0.4 as the current drafting candidate. v0.4 remains an immutable RED review artifact.

**Source basis:** Orca `403c60bbadb734d870a22c372aca8a988e348d21`.

**Decision-package basis:** `option_b_v0.5_owner_decision_package_v0.2.md`, SHA-256 `b2146726945e4e450b62a8dd027127f028473c4035c6c6eef02a8da206da2a06`.

**Owner authorization recorded:** draft v0.5 and perform fresh reviews only. No implementation, Orca-source modification, branch, commit, PR, deployment, or self-application is authorized.

---

## 0. Scope and authority boundary

### 0.1 In scope for v1

Policy-gated Orca operations issued by the supervised Hermes flow:

1. Orca mutations:
   - `orchestration dispatch` with `dry_run=false`;
   - `orchestration worker-start` without `--on`;
   - `orchestration worker-stop`;
   - `orchestration worker-abandon`.
2. Side-effecting observation:
   - `orchestration worker-show`, because both local restart reconciliation and federated paths can write Orca state.
3. Logged read-only or preview operations subject to deterministic adapter preconditions:
   - `orchestration dispatch --dry-run` under §3.6;
   - `orchestration run-current`;
   - `orchestration run-show`;
   - `orchestration task-list`;
   - `orchestration dispatch-show`;
   - `orchestration worker-read` for a proven local dispatch only;
   - `terminal read`;
   - `terminal wait`.
4. Command-specific immutable schemas, deterministic discovery/classification, policy decisions, approval binding, atomic single-use consumption, intent-stable Orca mutation receipts, local-dispatch provenance, observation, audit, and reconciliation.
5. Filesystem/Git effects performed by an approved local `worker-start` only within the exact bound repo and worker-worktree topology. This includes the worktree/branch effects of `new-child` or `new-top-level`; it is not general Git/filesystem authority.

### 0.2 Explicitly out of scope

- Federation in every form: no federated identity, observation, mutation, relay, or cross-runtime audit.
- Orca source changes.
- Orca GUI and governance of non-Hermes clients.
- Filesystem/Git mutation outside the exact approved worker-start topology.
- `worker-release`, `worker-retain`, and their unapproved lifecycle semantics.
- Time-based approval expiry; v1 instead uses single-use approval plus evidence freshness.
- LLM judgment in any authorization, classification, digest, retry, observation, or reconciliation decision.

### 0.3 Commands independently refused by Hermes

Hermes MUST refuse before Orca invocation, without relying on Orca upstream fences:

- `orchestration.run`, `orchestration.runStop`;
- `coordinator-start`, `coordinator-stop`;
- `orchestration.reset`;
- every `federation*` command;
- `orchestration worker-start --on ...` in every raw-argv representation;
- `orchestration worker-release`, `orchestration worker-retain`;
- every `orchestration check` variant, including `--wait`, `--peek`, `--ack`, `--inject`, `--unread`, and `--all`;
- any command, flag, duplicate flag, positional argument, or environment-dependent input not admitted by a schema in §3.

### 0.4 Allowlist scope

The allowlist governs only this supervised flow; it does not claim to prohibit Owner actions through Orca GUI/CLI outside the flow. Such actions are not represented as policy-gate successes.

If an actual call site does not fit this contract, implementation MUST stop and report. It MUST NOT widen a schema, infer permission, or normalize away a mismatch.

---

## 1. Architecture and invariants

```text
Hermes typed request construction
  → deterministic preflight and fresh discovery
  → command-specific adapter and total state classifier
  → deterministic policy gate (`default.action = block`)
  → durable decision event
  → ApprovedInvocation issuance
  → executor recomputation of effective invocation identity
  → atomic single-use operation consumption
  → durable exec-attempt marker
  → installed Orca CLI exec using structured argv and `--json`
  → immutable outcome / recovery / provenance events
  → bounded observation or reconciliation
```

1. No LLM judgment participates in this flow.
2. A gate result is not an executable boolean. The executor accepts only an exact, unconsumed `ApprovedInvocation` with `decision=allow`.
3. Authorization binds a versioned structured effective invocation, not raw argv alone.
4. The executor reconstructs argv from the typed request. It never executes arbitrary caller-supplied argv.
5. The executor recomputes the canonical invocation digest immediately before consumption and exec; mismatch blocks.
6. `operation_id` is unique and single-use per approval attempt.
7. `mutation_intent_id` identifies one intended Orca mutation. Its `orca_request_id` remains stable across recovery attempts for the same uncertain mutation.
8. A new approval attempt never reuses `operation_id`. A same-intent recovery may reuse `orca_request_id` only with the exact same method and effective payload.
9. Decision, consumption, exec-attempt, outcome, recovery, provenance, and reconciliation events are separate immutable event types.
10. Required pre-exec persistence failure means no exec. Post-exec evidence failure cannot undo or be reported as preventing the mutation.
11. Every normal dispatch-targeting command is fail-closed unless durable known-local provenance exists.
12. `reconciliation_required` has higher precedence than ordinary policy or observation approval.
13. `default.action` MUST equal `block`; missing/invalid policy fields fail load.

---

## 2. Identity, stores, and shared types

### 2.1 Attempt identity versus mutation intent

```yaml
OperationAttempt:
  operation_id:           # unique per approval presentation; never reused
  approval_kind:          # policy_allow | owner_approval | owner_reconciliation

MutationIntent:           # absent for worker-show and read-only/preview commands
  mutation_intent_id:     # stable for one intended Orca mutation
  orca_request_id:        # exact value passed through --retry-request
  normalized_method:      # exact Orca RPC/command identity
  effective_payload_digest:
  credential_binding_id:  # non-secret identifier/version for authenticated Orca credential
```

Rules:

1. Hermes creates `mutation_intent_id` and `orca_request_id`; callers cannot supply or rotate them.
2. A recovery attempt uses a new `operation_id`, new decision, and freshly recomputed digest.
3. It reuses the intent/request ID only when method, credential binding, target instance, and canonical effective payload are identical.
4. A deliberate second mutation after a confirmed outcome uses a new mutation intent and request ID.
5. Reusing a request ID with a method/payload mismatch blocks before exec.
6. Receipt recovery depends on Orca's `(caller_fingerprint, request_id)` identity. Hermes MUST use the same credential binding for same-intent recovery. If the credential is unavailable or rotated, receipt recovery is unavailable and reconciliation is required.
7. Raw credentials, auth tokens, and secret values are never stored in canonical payloads or audit events.

### 2.1A Immutable Owner authorization

A policy result and an Owner authorization are separate immutable records. `require_approval` never mutates into `allow` in place.

```yaml
OwnerAuthorization:
  authorization_id:
  basis_decision_event_id:
  authorization_kind:     # owner_approval | owner_reconciliation
  exact_action:
  args_digest:
  approved_state_class:
  mutation_intent_id:     # nullable
  reconciliation_scope:   # required for owner_reconciliation
  issued_at:
```

Rules:

1. A `policy_allow` needs no OwnerAuthorization but remains bound to its immutable allow decision.
2. `owner_approval` may resolve only the exact `require_approval` decision it references; it cannot override `block` or `reconciliation_required`.
3. `owner_reconciliation` must identify exact affected scope and recovery/containment action.
4. Human delay MUST NOT be hidden by extending stale evidence. After Owner authorization, the adapter performs fresh deterministic discovery and policy re-evaluation, then appends a new immutable approval-resolution decision event linked to the basis decision and authorization. It does not refresh or rewrite the basis decision.
5. The resolution decision may issue ApprovedInvocation only if action, canonical args/effective target, state class, mutation intent, credential/instance identity, and reconciliation scope still match the authorization and the new evidence is fresh. Otherwise a new ordinary policy decision and Owner authorization are required.
6. ApprovedInvocation references the immutable decision, OwnerAuthorization, and approval-resolution event. None is rewritten after execution or outcome.

### 2.2 Durable stores

The design requires independently addressed durable stores/interfaces for:

| Store | Required contents | Failure behavior |
|---|---|---|
| decision ledger | immutable policy decisions and evidence digests | pre-exec fail-closed |
| consumed-operation store | atomic unique `operation_id` consumption | pre-exec fail-closed |
| mutation-intent store | intent/request ID, exact method/payload/credential binding | fail-closed for mutation and recovery |
| local-provenance store | proven local dispatch records | dispatch-targeting commands fail-closed |
| primary event ledger | exec-attempt, outcome, recovery, observation events | §10 sequencing |
| reconciliation-state store | scopes requiring reconciliation and durable closure | highest-precedence fail-closed |
| recovery/error channel | durable alert independent of primary outcome append path | stop supervised flow if unavailable |

“Independent” means a separately invoked persistence path whose failure can be injected and detected independently of primary outcome append. The implementation MUST NOT claim physical failure-domain independence unless deployment evidence establishes it.

### 2.3 Known-local dispatch provenance

A valid provenance record is created only from a durably recorded successful outcome or durable worker-start acceptance receipt issued by this gate:

```yaml
LocalDispatchProvenance:
  dispatch_id:
  origin: local
  creating_command:       # orchestration.dispatch | orchestration.workerStart
  created_by_operation_id:
  mutation_intent_id:
  run_id:
  task_id:
  terminal_handle:        # where returned/known
  worktree_id:            # where returned/known
  recorded_at:
  source_contract_sha:
```

Requirements:

- `worker-start.on` was absent and explicitly rejected by both adapter and policy.
- Provenance is adapter-derived; a caller cannot assert `origin=local`.
- GUI-, external-client-, imported-, or unknown dispatch IDs are denied by the normal path.
- `worker-stop`, `worker-abandon`, `worker-show`, and `worker-read` verify provenance before Orca CLI invocation.
- Provenance read/write/correlation failure marks the affected scope `reconciliation_required` and blocks the normal path.

### 2.4 Provenance-loss break-glass boundary

On provenance loss, Hermes MUST:

1. stop automatic dispatch-targeting commands;
2. durably mark `reconciliation_required` where possible;
3. surface `CONTAINMENT_PATH_UNAVAILABLE`;
4. refuse to treat Owner assertion alone as evidence that a dispatch is local.

Containment may proceed only after independently sourced local evidence is explicitly accepted through a manual/break-glass reconciliation path. That event is labelled manual/break-glass, not normal policy-gate success. If locality cannot be established, the normal gate remains blocked. A direct Owner Orca action outside this flow remains outside this specification's authorization claim.

---

## 3. Closed command schemas

All supervised CLI calls use `--json`. Unknown, repeated single-valued, conflicting, or positional inputs are rejected from raw argv before policy. `--flag=value` and `--flag value` parse to the same typed field. Omitted and explicit default values remain distinguishable unless the schema below explicitly resolves them.

### 3.1 `orchestration dispatch` mutation

Required typed fields:

| Field | Contract |
|---|---|
| `run_id` | explicit `--run`; omission blocks |
| `task_id` | required `--task` |
| `to_handle` | required explicit `--to` |
| `from_handle` | required explicit `--from`; no env/cwd/focus fallback |
| `inject` | explicit boolean, default false |
| `return_preamble` | explicit boolean, default false |
| `dry_run` | fixed false |
| `dev_mode` | preflight-derived and bound, never caller asserted |
| mutation identity | §2.1 required; `--retry-request` emitted by executor |

`dispatch_id` is an exec outcome, never a gate input. A successful durable outcome creates local provenance.

### 3.2 `orchestration worker-start`

| Field | Contract |
|---|---|
| `run_id`, `task_id` | explicit `--run` and required `--task` |
| `from_handle` | required explicit `--from` |
| `worktree_mode` | exact existing worktree ID, `new-child`, or `new-top-level`; omitted/`current` is rejected |
| `parent_worktree_id` | required and bound for `new-child`; absent for `new-top-level` |
| `resolved_worktree_id` | required for existing-worktree mode; an exec outcome for new worktree modes |
| `repo_id` | exact resolved repo identity; required for new worktree modes and bound where applicable |
| `base_branch` | explicit and bound for new worktree modes |
| `name` | explicit and bound where worktree creation requires it |
| `display_name`, `comment` | optional, admitted and bound |
| `setup` | closed enum `run | skip | inherit`; effective value/source bound |
| `agent`, `model`, `effort` | optional admitted fields; presence/value bound |
| `terminal_handle` | optional exact existing terminal identity; bound; must match worktree |
| `startup_timeout_ms` | `--timeout-ms`; Orca agent-readiness budget only; default/explicit distinction bound |
| `retry_of` | optional exact prior dispatch ID; direct retry lineage |
| `on` | fixed absent; every representation rejected before policy and denied by policy |
| `dev_mode` | preflight-derived and bound |
| mutation identity | §2.1 required |

Outcomes include at minimum `runId`, `taskId`, `dispatchId`, `state`, stage/setup/launch fields where returned, `effects`, `residualResources`, warning/error fields, effective startup timeout, and new worktree/terminal identity where returned. `startup_timeout_ms` MUST NOT set the total observation ceiling.

### 3.3 `orchestration worker-stop`

Inputs:

- exact `dispatch_id`;
- matching durable known-local provenance and effective run/task target;
- mutation identity under §2.1.

Complete JSON outcome schema includes `dispatchId`, `state`, `alreadySettled`, `processAction`, and any `lastError`, `warning`, and close evidence returned. `worker-stop` has no `stale` field. `alreadySettled=true` is historical/no-new-effect evidence and MUST NOT alone be reported as fresh containment.

`stop_unknown` is a non-zero CLI result and maps to `RUNTIME_UNKNOWN`. It does not authorize automatic retry.

### 3.4 `orchestration worker-abandon`

Inputs are the same identity/provenance classes as worker-stop.

Complete JSON outcome schema includes `dispatchId`, `state`, `alreadySettled`, `stale`, `processAction`, `warning`, and `residualResources`.

- `stale=true` means the dispatch is no longer current and no state/process effect occurred; it MUST NOT be reported as successful containment.
- `alreadySettled=true` is no-new-effect evidence.
- Abandon retains possibly live resources and is not equivalent to process stop.

### 3.5 `orchestration worker-show` side-effecting observation

Inputs:

- exact `dispatch_id`;
- matching known-local provenance;
- a single-use `ApprovedInvocation` for normalized command `orchestration.workerShow`.

No `orca_request_id` exists because the CLI handler does not expose mutation-ledger retry for worker-show. Result ambiguity MUST NOT trigger automatic re-exec. Local restart handling can mark start/stop unknown, so worker-show is never represented as read-only.

During `reconciliation_required`, worker-show requires `approval_kind=owner_reconciliation`.

### 3.6 `orchestration dispatch --dry-run` preview

This is a separate logged preview route and does not use mutation approval or mutation intent.

Allowed fields:

- required explicit `run_id`, `task_id`, and `from_handle`;
- `dry_run=true`;
- optional exact `to_handle`;
- `return_preamble` admitted and bound/logged;
- preflight-derived `dev_mode` and target-instance identity.

`inject` and `retry-request` are rejected. Any returned preamble is preview output, not mutation evidence. `dispatch=null` is expected and creates no provenance.

### 3.7 Read-only observation schemas

| Command | Admitted inputs / conditions |
|---|---|
| `run-current` | explicit terminal context identity; logged |
| `run-show` | exact run ID; logged |
| `task-list` | exact run/filter fields admitted by adapter; logged |
| `dispatch-show` | exact task ID; explicit from/context fields bound where preamble requested |
| `worker-read` | exact known-local dispatch ID; cursor, limit, source all typed/logged |
| `terminal read` | exact terminal handle, cursor, limit |
| `terminal wait` | exact terminal handle, condition, timeout; §8 probe contract |

`terminal read` and `terminal wait` are admitted only when the terminal is the exact current preflight target or is bound by matching known-local dispatch provenance. Arbitrary local terminal handles are outside the supervised flow. No `orchestration check` variant is admitted in v1.

---

## 4. Effective invocation and canonical binding

### 4.1 Execution-context contract

The effective invocation identity includes:

```text
canonical_invocation_v1 = {
  normalization_version,
  normalized_command,
  cli_executable_identity,
  source_contract_sha,
  structured_args,
  effective_target_identity,
  fixed_or_bound_cwd,
  execution_relevant_env,
  target_instance_identity,
  dev_mode,
  credential_binding_id,
  mutation_intent_identity_or_null
}
```

Normative requirements:

1. The executor builds literal argv from `structured_args`; no raw caller argv reaches spawn.
2. The implementation chooses a fixed cwd or binds the exact canonical cwd. A changed cwd blocks.
3. Execution-relevant environment is a closed set and is bound. At minimum it includes `ORCA_USER_DATA_PATH`, `ORCA_DEV_CLI_INVOCATION`, and any pane/terminal variables not eliminated by explicit handles.
4. `ORCA_USER_DATA_PATH` is both target-instance identity and a `devMode` input. It MUST NOT be scrubbed to force production semantics.
5. `dev_mode` is deterministically derived at preflight and recomputed at exec. Mismatch blocks.
6. Transport/auth secrets are not included, but their immutable non-secret credential binding/version is bound.
7. Resolved executable path/version and source-contract identity are bound so an approval cannot move to an equivalent-looking different CLI.
8. Refs, handles, IDs, paths, and selectors are case-sensitive unless an Orca contract explicitly states otherwise. The adapter MUST NOT lowercase them.
9. Unexpected defaults are rejected rather than filled from coordinator focus/cwd/env.

### 4.2 Digest and normalization readiness

```text
args_digest = SHA256(canonical_json(canonical_invocation_v1))
```

v0.5 fixes the complete normative input domain above. The implementation PR, if later authorized, MUST provide a versioned deterministic canonical JSON/byte encoding specification and golden vectors covering:

- stable field and map ordering;
- UTF-8/quoting rules;
- omitted versus explicit null/default/false;
- path and case behavior;
- equivalent raw flag syntax;
- duplicate/unknown/conflicting flag rejection;
- environment, cwd, target-instance, executable, and credential-binding variance.

No implementation may claim a valid digest until those vectors pass. The implementation may define the byte algorithm; it may not change this input domain.

### 4.3 ApprovedInvocation

```yaml
ApprovedInvocation:
  operation_id:
  approval_kind:          # policy_allow | owner_approval | owner_reconciliation
  decision_event_id:
  owner_authorization_id: # nullable; required for owner_* kinds
  approval_resolution_event_id: # nullable; required for owner_* kinds
  normalized_command:
  args_digest:
  policy_sha256:
  source_contract_sha:
  evidence_digest:
  evidence_observed_at:
  evidence_process_epoch_id:
  cli_executable_identity:
  credential_binding_id:
  mutation_intent_id:     # nullable
  orca_request_id:        # nullable
  reconciliation_scope:   # required for owner_reconciliation
  decision: allow
  issued_at:
```

`require_approval` and `block` results are not ApprovedInvocations and cannot reach exec. Resolving `require_approval` requires the separate immutable OwnerAuthorization and post-authorization fresh approval-resolution event in §2.1A, followed by a newly issued ApprovedInvocation referencing all records.

### 4.4 Executor verification and atomic consumption

Immediately before exec the executor MUST:

1. reconstruct the complete effective invocation;
2. recompute `args_digest` and compare every bound identity;
3. verify policy/source/CLI/credential bindings;
4. verify evidence freshness under §5.3;
5. verify known-local provenance where required;
6. verify reconciliation precedence and approval kind;
7. require `decision=allow`;
8. atomically check-and-record `operation_id` in durable storage;
9. durably append an exec-attempt marker;
10. spawn only after all prior steps succeed.

Consumption is not rolled back after spawn attempt or non-zero CLI exit. Store read/write/atomicity failure is fail-closed. Time-based approval expiry remains v2 and MUST NOT be improvised.

### 4.5 Startup crash sweep

On startup, every consumed `operation_id` lacking a durable terminal outcome/recovery closure is marked `reconciliation_required`, regardless of whether spawn can be proven. Automatic mutation for the affected operation/intent/dispatch/task remains blocked.

---

## 5. Preflight discovery, freshness, and lineage

### 5.1 Explicit binding

`dispatch` and `worker-start` require explicit `--run` and `--from`. Omission blocks. `--run` is also cross-checked by Orca against the caller terminal's bound Run; `consumer_fenced` is a failed outcome, never permission to retarget.

### 5.2 Discovery requirements

Before a decision, deterministic discovery verifies and records at least:

- target Orca instance and source-contract identity;
- credential binding;
- explicit run exists and matches caller context;
- task existence/status and run membership;
- exact terminal/worktree/repo identities and relationships;
- prior dispatch context and command-specific lineage;
- current dispatch/worker state where required;
- known-local provenance for every dispatch-targeting command;
- absence of reconciliation state for normal approvals.

RPC/query/malformed-result failure is `RUNTIME_UNKNOWN`, not benign absence.

### 5.3 Evidence freshness

The exact policy artifact MUST define a positive `freshness_max_age_ms` no greater than 60,000 ms for each gated command and side-effecting observation. Individual commands may use shorter values. Missing, zero, over-limit, invalid, or unknown command entries are `PolicyLoadError` and fail closed.

- Age is measured using a monotonic source within a bound process epoch; wall clock is audit metadata only.
- ApprovedInvocation binds `evidence_process_epoch_id`. An executor in a different/restarted epoch cannot compare that monotonic age and MUST treat the evidence as stale, run fresh discovery, and obtain a new decision.
- Stale evidence is never refreshed under an existing decision. Adapter re-runs discovery and issues a new decision/operation ID. The post-Owner path in §2.1A likewise creates a new linked resolution-decision event; it does not mutate the basis decision.
- Executor checks freshness immediately before consumption.
- Discovery is not reusable across a source-contract, target-instance, credential, cwd/env, provenance, or reconciliation change.

### 5.4 Worker-start retry lineage

`is_retry := (retry_of is not None)`. `retry_of` must be the latest applicable dispatch for the same task/run and meet Orca retry preconditions. It is bound; inference is forbidden.

### 5.5 Dispatch lineage

Dispatch has no `retryOf` parameter. Adapter uses `dispatch-show --task <id>` and task/run evidence to classify prior context. Failure is `RUNTIME_UNKNOWN`.

### 5.6 Mutation recovery versus deliberate retry

Receipt recovery is not a new domain mutation:

- same uncertain intent → same `orca_request_id`, same method/payload, new operation/approval;
- confirmed first outcome followed by intended second effect → new intent/request ID;
- `stop_unknown` or any `RUNTIME_UNKNOWN` forbids automatic deliberate retry;
- replaying a completed receipt under the same intent may retrieve evidence but MUST NOT be represented as a new effect.

---

## 6. Total state classifiers and routes

Orca source enums:

```text
dispatch_status: pending | dispatched | completed | failed | circuit_broken
worker_state:    starting | ready | start_unknown | failed | succeeded |
                 stopping | stop_unknown | stopped | abandoned
```

### 6.1 Dispatch-only classifier

| Dispatch result | Classified state |
|---|---|
| query/RPC failure, absent/malformed status | `RUNTIME_UNKNOWN` |
| `circuit_broken` | `CIRCUIT_BROKEN` |
| `failed` | `FAILED` |
| `completed` | `OK` |
| `pending` or `dispatched` | `OBSERVATION_PENDING` |
| any otherwise-unmapped value | `RUNTIME_UNKNOWN` |

### 6.2 Worker-lifecycle classifier precedence

Apply top-to-bottom as adapter precedence, not YAML first-match ordering:

| Condition | Classified state |
|---|---|
| query/RPC failure or missing required worker row | `RUNTIME_UNKNOWN` |
| `dispatch_status == circuit_broken` | `CIRCUIT_BROKEN` |
| `worker_state ∈ {start_unknown, stop_unknown, abandoned}` | `RUNTIME_UNKNOWN` |
| `dispatch_status == failed` or `worker_state == failed` | `FAILED` |
| `worker_state == starting` | `STARTING` |
| `worker_state == stopping` | `STOPPING` |
| `worker_state == stopped` | `STOPPED` |
| `dispatch_status == completed` or `worker_state == succeeded` | `OK` |
| `worker_state == ready` | `OBSERVATION_PENDING` |
| any otherwise-unmapped pair | `RUNTIME_UNKNOWN` |

The dispatch classifier covers all five enum values plus default. The worker classifier covers all nine worker values through explicit rows plus a mandatory default and missing-worker rule.

### 6.3 Route semantics

| State | Deterministic route |
|---|---|
| `RUNTIME_UNKNOWN` | clear observation counter; stop automatic mutation; `require_approval`/reconciliation as applicable |
| `CIRCUIT_BROKEN` | no automatic retry; require Owner decision |
| `FAILED` | no automatic retry; fresh Owner/policy path only |
| `STARTING` | bounded observation only; no second start/dispatch |
| `STOPPING` | bounded stop observation only; no start/dispatch/second stop |
| `STOPPED` | containment state; no automatic restart |
| `OBSERVATION_PENDING` | §8 bounded observation; no concurrent mutation |
| `OK` | terminal successful state; no inferred new authority |

`stop_unknown` is `RUNTIME_UNKNOWN`. It MUST NOT cause automatic worker-stop, dispatch, or worker-start. `worker-abandon stale=true` is a no-op outcome and cannot establish containment.

---

## 7. Deterministic policy contract

1. Policy is a versioned artifact whose exact bytes are bound by `policy_sha256`.
2. `default.action` MUST be `block`.
3. Unknown command, field, enum, operator, state, policy version, duplicate rule ID, or malformed value causes `PolicyLoadError`; no partial policy loads.
4. `reconciliation_required` outranks every ordinary rule.
5. A normal `require_approval` cannot clear reconciliation.
6. Only a separate immutable OwnerAuthorization and ApprovedInvocation explicitly issued as `owner_reconciliation`, with exact scope/action, can authorize a reconciliation operation.
7. `RUNTIME_UNKNOWN`, `CIRCUIT_BROKEN`, and `FAILED` never route to automatic mutation.
8. `STARTING`, `STOPPING`, and `OBSERVATION_PENDING` route only to observation.
9. `STOPPED` does not imply permission to restart.
10. Policy never infers local provenance, retry lineage, effective target identity, or probe semantics; those are adapter facts.

Required policy fields include:

```yaml
version:
default:
  action: block
source_contract_sha:
freshness_max_age_ms:
  orchestration.dispatch:
  orchestration.workerStart:
  orchestration.workerStop:
  orchestration.workerAbandon:
  orchestration.workerShow:
observation:
  interval_ms: 60000
  qualifying_checkpoints: 3
  probe_timeout_ms: 60000
  default_ceiling_ms: 2700000
```

Per-invocation observation-ceiling override is absent in v1.

---

## 8. Observation lifecycle

### 8.1 Signals

A qualifying idle-silence checkpoint requires both:

1. no new terminal output since the previous completed checkpoint; and
2. a `terminal wait` result with `satisfied=true` for `tui-idle`.

Every probe uses the canonical equivalent of:

```text
orca terminal wait --terminal <known-local-worker-handle> \
  --for tui-idle --timeout-ms 60000 --json
```

Outcome taxonomy:

| Probe outcome | Required behavior |
|---|---|
| `satisfied=true`, no new output | increment consecutive counter |
| `satisfied=true`, new output | reset counter |
| RPC error code exactly `timeout` | terminal not observed idle; reset counter; not `RUNTIME_UNKNOWN` |
| `satisfied=false` with typed `blockedReason` | immediate `require_approval`; do not wait for N |
| any other RPC error, malformed response, stale identity, or execution failure | `RUNTIME_UNKNOWN`; exit observation and discard counter |

Counter state never survives `RUNTIME_UNKNOWN` or re-entry after fresh classification.

### 8.2 Scheduling

- Interval: 60 seconds.
- N: 3 consecutive qualifying checkpoints.
- At most one probe per worker is in flight; ticks while a probe is in flight are coalesced, never overlapped.
- Output cursor comparison spans from the prior completed checkpoint to the current completed checkpoint.
- Probe timing may exceed nominal interval due to RPC/process overhead; no claim of an exact three-minute trigger is made.
- N qualifying checkpoints route to `require_approval`, not `RUNTIME_UNKNOWN`.

### 8.3 Absolute ceiling

The exact policy sets `default_ceiling_ms=2700000` (45 minutes). It is independent of worker-start `startup_timeout_ms`.

- Measured from worker-start exec using monotonic elapsed time.
- Driven by a deadline scheduler independent of terminal read/wait completion.
- Fires despite output, counter resets, delayed/pending probe, or wall-clock change.
- Routes to `require_approval`.
- A post-ceiling “continue observing” decision requires a new explicit Owner-selected deadline and audit event. The expired deadline is never silently reused or extended.

### 8.4 Claude permission-prompt residual risk

Orca `tui-idle` excludes agent status `permission`, while the typed `blockedReason` set is Codex-specific. A Claude permission prompt can therefore appear as repeated probe `timeout` and may not escalate until the absolute ceiling. v1 explicitly accepts this bounded detection latency; it MUST be documented in deployment/operator material and tested.

### 8.5 Owner observation choices

| Choice | Contract |
|---|---|
| Continue | new explicit deadline, counter reset, immutable continuation event |
| Receipt recovery | same mutation intent/request ID only under §11.2; new operation/approval |
| Deliberate retry | new mutation intent/request ID, full fresh discovery; never automatic after unknown |
| Stop | new policy-gated known-local stop intent |
| Abandon | new policy-gated known-local abandon intent; does not claim process stop |

---

## 9. Read-only and side-effecting observation boundary

### 9.1 Logged read-only/preview operations

`run-current`, `run-show`, `task-list`, `dispatch-show`, safe dispatch dry-run, known-local `worker-read`, `terminal read`, and `terminal wait` do not require ApprovedInvocation. They still require schema validation, target-instance binding, known-local preconditions where applicable, `--json`, and immutable invocation/result logs with `observed_at`.

### 9.2 `worker-show`

`worker-show` requires ApprovedInvocation because:

- federated rows trigger remote observation/reconciliation;
- even local restart handling can mark start/stop unknown in Orca DB.

Known-local provenance prevents the federated branch. The side effect remains governed even when local.

### 9.3 Excluded mailbox check

`orchestration check` is excluded because it can acknowledge/create delivery rows, start federation relay, take an exclusive waiter, inject content, and mutate compatibility state. No flag subset is approved in v1.

---

## 10. Audit, crash, and reconciliation

Audit records are operational append logs, not tamper-proof/tamper-evident evidence.

### 10.1 Event types

At minimum:

1. decision event;
2. consumption event;
3. exec-attempt event;
4. command outcome event;
5. observation/checkpoint event;
6. mutation-intent creation/recovery event;
7. local-provenance creation event;
8. outcome-reconstruction event;
9. reconciliation-required/closure event;
10. manual/break-glass event.

No later event rewrites an earlier decision or consumption event.

### 10.2 Pre-exec versus post-exec failure

- Decision, required intent, consumption, and exec-attempt persistence occur before spawn. Failure means no exec.
- Outcome is post-exec. Outcome write failure cannot be represented as prevention.
- On outcome write failure, Hermes emits `OUTCOME_RECORDING_FAILED` through the independent recovery channel and marks operation/intent/known dispatch/task `reconciliation_required`.
- If both primary outcome path and recovery/error channel fail, Hermes stops the supervised flow and surfaces loss of auditability through the control plane; it does not continue silently.

### 10.3 Outcome schema truthfulness

Every actual exec records spawn result, exit code, RPC error, mutation receipt metadata, and complete command-specific JSON result.

- Missing/malformed required fields produce `OUTCOME_SCHEMA_MISMATCH` and reconciliation; fields are not synthesized.
- Stop `alreadySettled=true` and abandon `alreadySettled/stale=true` are no-new-effect evidence.
- Abandon residual resources are recorded and never represented as stopped/deleted.

### 10.4 Reconciliation precedence

1. `reconciliation_required` outranks normal allow/approval, observation, retry, and resume.
2. Normal approval cannot clear/bypass it.
3. Automatic later mutation remains blocked.
4. Only explicit Owner reconciliation may authorize an exact recovery or containment scope with a new operation ID.
5. Same-intent receipt recovery reuses `orca_request_id`; a deliberate new effect uses a new intent/request ID.
6. Reconciliation persists until durable closure evidence is appended.
7. Observation approval cannot clear reconciliation.
8. If local provenance is unavailable, §2.4 applies; normal stop remains blocked.

### 10.5 Startup reconciliation

Every consumed operation without terminal outcome/recovery closure is marked reconciliation-required on startup. This covers crash-before-spawn, crash-after-spawn, and unknown spawn boundary without claiming which occurred.

---

## 11. Trusted Orca receipt recovery

### 11.1 Source contract relied upon

Orca's durable mutation ledger binds caller fingerprint + request ID to exact method/payload hash, rejects mismatch, replays completed receipts, and can retain worker-start acceptance with dispatch ID before later setup completion.

### 11.2 Allowed reconstruction

Only these sources are trusted:

1. completed receipt replay using the same credential binding, request ID, method, and effective payload;
2. worker-start durable acceptance/`operation_unknown` metadata for acceptance and dispatch identity only;
3. fresh live discovery, explicitly labelled observation rather than reconstructed historical outcome.

Rules:

- Hermes durably maps each recovery operation ID to the stable mutation intent/request ID.
- Completed receipt replay may reconstruct the exact cached result.
- Worker-start acceptance may establish dispatch ID/local provenance, but not claim later worker success.
- Pending/unknown stop or abandon without a completed receipt cannot be labelled successful.
- Reconstruction appends a recovery event; it never rewrites original records.
- Credential rotation/unavailability prevents same-receipt recovery and triggers reconciliation.

---

## 12. Required deterministic tests

No test in this section is claimed executed until an authorized implementation exists.

| ID | Required assertion |
|---|---|
| A1 | Every gated command, including worker-show, is unreachable without exact unconsumed ApprovedInvocation; digest/context mismatch blocks. |
| A2 | Policy/source/CLI/credential binding mismatch blocks; `require_approval` and `block` cannot reach exec. |
| A3 | `default.action != block`, unknown operator/field/version, malformed freshness/observation value, or partial policy causes PolicyLoadError/no exec. |
| A4 | Explicit run/from are required; context default omission, run mismatch, and `consumer_fenced` never retarget. |
| A5 | Complete command × flag matrix rejects unknown, duplicate, conflicting, positional, `--on`, federation, release/retain, and every check variant. |
| A6 | Equivalent `--flag=value`/`--flag value` normalize identically; omitted/null/default/false remain correctly distinct. |
| A7 | Same structured args under different cwd/env/devMode/target instance/executable/credential do not share approval. |
| A8 | Exact repo/worktree/terminal relationships bind; `current` and omitted context defaults block. |
| A9 | Every `worker-start --on` representation and every `federation*` command is refused independently of Orca. |
| A10 | Unknown/federated/unproven dispatch ID is refused before stop/abandon/show/read CLI invocation. |
| A11 | Provenance store read/write/correlation failure blocks normal dispatch-targeting commands and surfaces G-9.1 without fail-open. |
| A12 | Local worker-show requires side-effecting observation approval; no automatic replay after unknown result. |
| A13 | Dispatch classifier exhaustively covers five statuses plus malformed/absent/default. |
| A14 | Worker classifier exhaustively covers every 5×9 pair, missing worker, malformed input, and default with fixed precedence. |
| A15 | `stop_unknown`, unknown states, and abandoned map to no automatic retry; stopped does not imply restart authority. |
| A16 | Probe `satisfied=true`/output combinations increment or reset exactly as §8.1. |
| A17 | Exact RPC code `timeout` resets as busy and never becomes RUNTIME_UNKNOWN. |
| A18 | Typed blockedReason immediately requires approval; other errors become RUNTIME_UNKNOWN and discard counter. |
| A19 | Probe is canonical `tui-idle/60000/json`, serial/non-overlapping; N=3 qualifying checkpoints escalates. |
| A20 | Independent monotonic ceiling fires while output flows or probe is pending; startup timeout cannot change it. |
| A21 | Post-ceiling continue requires a new Owner-selected deadline; expired deadline cannot be reused. |
| A22 | Claude permission status follows documented timeout/ceiling residual-risk path. |
| A23 | Atomic concurrent presentation of one operation ID yields one consumer; store read/write/atomicity failure means no exec. |
| A24 | Crash after consumption at each pre/post-spawn boundary results in consumed-without-outcome startup reconciliation. |
| A25 | Pre-exec event persistence failure prevents spawn; post-exec outcome failure never claims prevention and triggers independent recovery channel. |
| A26 | Recovery-channel failure stops supervised flow; reconciliation has precedence over normal approval and observation. |
| A27 | Same uncertain intent uses new operation ID but stable Orca request ID and identical method/payload/credential; mismatch blocks. |
| A28 | Deliberate second mutation uses new intent/request ID; completed receipt replay is recorded as replay, not new effect. |
| A29 | Worker-start acceptance reconstruction is limited to acceptance/dispatch ID; pending stop/abandon cannot be labelled successful. |
| A30 | Stop `alreadySettled` and abandon `alreadySettled/stale` cannot be reported as fresh containment; malformed JSON outcome reconciles. |
| A31 | Safe dispatch dry-run cannot inject or carry retry-request, creates no provenance, and cannot substitute for mutation approval. |
| A32 | Bounded worker-topology Git/filesystem effects are allowed only for exact approved repo/worktree mode; other effects remain refused. |
| A33 | Freshness is mandatory per command, must be `1..60000 ms`, and stale or process-epoch-mismatched evidence causes new discovery/decision rather than reuse. |
| A34 | Case-sensitive refs/IDs/handles are not normalized; case substitution blocks. |
| A35 | `require_approval` can reach exec only through separate immutable matching OwnerAuthorization plus post-authorization fresh resolution; `block`, reconciliation, action/digest/state/intent/scope mismatch cannot be overridden. |
| A36 | Terminal read/wait accepts only the exact current preflight or known-local-provenance terminal; arbitrary local terminal handles are refused. |

---

## 13. Upgrade and implementation boundaries

Before any later implementation is accepted, its preflight MUST verify:

- exact Orca source/CLI contract identity remains compatible;
- all cited command flags, RPC methods, return fields, wait semantics, federation branches, mutation-receipt behavior, and state enums remain unchanged or the spec is re-reviewed;
- source-contract drift fails closed and does not auto-update schemas.

Boundaries:

- Do not modify Orca source.
- Do not add LLM judgment.
- Do not change default block.
- Do not widen schema automatically.
- Do not self-apply.
- Any later implementation must be a separate, explicitly authorized PR with deterministic tests and golden canonicalization vectors.

---

## 14. Review gate

All Owner decisions approved for this draft are incorporated. Nevertheless:

1. v0.5 requires a fresh Hermes full-specification review;
2. v0.5 requires an independent Claude Code full source/contract review that does not rely on prior verdicts;
3. any blocker returns the artifact to RED;
4. any byte change to the reviewed spec or change to Orca source basis invalidates the review identity;
5. passing reviews do **not** themselves authorize implementation. Owner must issue a separate explicit implementation authorization.

Until those reviews pass and Owner separately acts, this document remains a DRAFT and implementation remains unauthorized.
