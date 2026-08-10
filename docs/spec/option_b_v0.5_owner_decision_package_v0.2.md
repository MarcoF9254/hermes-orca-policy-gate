# Hermes Policy Gate — v0.5 Owner Decision Package v0.2

**Purpose:** resolve the eight independently verified blockers B1–B8 against Option B specification v0.4, without modifying v0.4 or authorizing implementation.

**Status:** DRAFT DECISION PACKAGE v0.2. Every item marked `OWNER DECISION REQUIRED` remains open. Recommendations are advisory and MUST NOT be treated as decisions.

**v0.2 review note:** independently reviewed corrections address three defects in the first decision package (retry-request identity, devMode handling, and provenance-loss containment), five omitted concerns, and one overstatement about CLI outcome-field stripping. The first package remains unchanged as review history.

**Specification under review:**

- File: `option_b_spec_v0.4.md`
- SHA-256: `778496ca24c214e30eb053162a57c2b684d33eb5cb6896f4582a71892218c338`

**Orca source basis:**

- HEAD: `403c60bbadb734d870a22c372aca8a988e348d21`

**Review status:** v0.4 is RED. Previous Hermes/Claude PASS conclusions are withdrawn because their review scope did not detect the source-contract mismatches below.

**Authority:** this package does not modify Orca source, v0.4, policy files, runtime code, Git history, branches, or GitHub. Implementation remains unauthorized.

---

## 0. Findings disposition

| Finding | Independent reviewer | Hermes source re-verification | v0.5 impact |
|---|---|---|---|
| B1 — terminal wait semantics inverted | Critical | **Confirmed** | Correct probe outcome taxonomy |
| B2 — federation reachable by dispatch ID | Critical | **Confirmed** | Local-dispatch provenance boundary required |
| B3 — worker-start timeout misused as total ceiling | Critical | **Confirmed** | Independent observation ceiling required |
| B4 — argv digest omits effective context | High | **Confirmed in substance** | Fix digest input domain and command construction |
| B5 — command schemas omit accepted flags | High | **Confirmed in substance** | Closed command/flag schemas required |
| B6 — “read-only” allowlist contains mutations | High | **Confirmed** | Reclassify/remove side-effecting observations |
| B7 — state mapping is not total | High | **Confirmed** | Command-specific total classifiers required |
| B8 — normalization boundary deferred | High | **Confirmed as consequence of B4/B5** | Canonical input contract must be fixed before implementation |

---

# Gate G-8 — Terminal probe outcome taxonomy (B1)

## Source evidence

- `src/main/runtime/orca-runtime.ts:17352–17376` and `17445–17476`: an idle wait exceeding `timeoutMs` rejects with `Error('timeout')`.
- `src/main/runtime/orca-runtime.ts:36988–37002`: `satisfied` is false only when `blockedReason` exists.
- `src/main/runtime/rpc/errors.ts:40–59`: `timeout` is a stable runtime passthrough code.
- `src/main/runtime/rpc/methods/terminal.ts:1408–1417`: `terminal.wait` forwards rejection through RPC.

The v0.4 rule is inverted: a normal busy worker produces RPC code `timeout`, not `satisfied=false`.

## Source-forced correction

The adapter MUST distinguish these outcomes:

| Probe result | Meaning | Required classification/action |
|---|---|---|
| `satisfied=true` | terminal observed `tui-idle` | qualifies for idle-silence counter if no new output |
| RPC error code exactly `timeout` | terminal was not observed idle within 60s | worker treated as busy/not-idle; counter resets to zero; **not** `RUNTIME_UNKNOWN` |
| `satisfied=false` with `blockedReason` | known interactive/modal block | Owner policy choice below |
| any other RPC error, malformed result, stale terminal, or probe execution failure | observation unavailable | `RUNTIME_UNKNOWN`; exit observation route |

A `RUNTIME_UNKNOWN` transition MUST discard the current checkpoint counter. Counter state MUST NOT survive re-entry after fresh classification.

## OWNER DECISION REQUIRED — blockedReason route

### Option A — immediate `require_approval` **(recommended)**

Known modal blocks indicate the agent needs human action. Escalate immediately without waiting for N=3 or the absolute ceiling.

### Option B — count as idle-silence

Treat it as a qualifying checkpoint and escalate only at N=3. Simpler state model, slower response.

### Option C — reset as busy

Not recommended. This can hide a permanent trust/update/hooks prompt until the absolute ceiling.

**Recommendation:** G-8A.

**Tests affected:** rewrite A13–A15; add explicit cases for stable `timeout`, `blockedReason`, and non-timeout RPC errors.

---

# Gate G-9 — Local-dispatch provenance boundary (B2)

## Source evidence

- `orchestration-worker-stop.ts:19–39`: `worker-stop` detects a federated dispatch row and calls remote `orchestration.federationStop`.
- `orchestration-worker-control.ts:42–57`: `worker-show` detects federated dispatch, starts a relay, calls the remote runtime, and reconciles local DB state.
- `orchestration-worker-control.ts:134–153`: `worker-read` calls remote `orchestration.federationReadOutput` for a federated dispatch.

No `--on` or `federation*` argv marker is present. A dispatch ID alone selects the remote path. Therefore v0.4’s argv-level federation refusal is insufficient.

## OWNER DECISION REQUIRED

### Option A — Hermes known-local provenance registry **(recommended)**

Hermes records every dispatch ID returned by its own approved **local** `worker-start`/`dispatch` outcomes. Dispatch-targeting commands are allowed only when the target ID has durable provenance:

```text
origin = local
created_by_operation_id = <consumed approved invocation>
federated = false
recorded_at = <timestamp>
```

Requirements:

1. Only dispatch IDs returned from an approved local invocation with `worker-start.on = None` may enter the worker lifecycle/observation allowlist.
2. `worker-stop`, `worker-abandon`, `worker-show`, and `worker-read` MUST reject unknown/unproven dispatch IDs before Orca CLI invocation.
3. Provenance-store read/write failure is fail-closed.
4. Outcome-recording failure also means local provenance is unavailable; §9 reconciliation applies before any dispatch-targeting command.
5. A caller cannot self-assert `federated=false`; the adapter derives it from its durable outcome/provenance record.
6. Dispatch IDs created by Orca GUI, other clients, older Hermes versions, or an imported log are denied by default.

**Trade-offs:**

- local workers created outside this gate cannot be governed by the normal v1 path, even when genuinely local;
- if the provenance store is unavailable, corrupted, or lost, the normal automated path cannot prove that even a Hermes-created dispatch is local and therefore cannot safely call `worker-stop`;
- fail-open is not permitted: an unknown dispatch ID may select Orca's federated remote-stop path without an argv marker.

### G-9.1 OWNER DECISION REQUIRED — provenance-loss containment

#### Option A — stop supervised automation + explicit break-glass reconciliation **(recommended)**

Normal policy evaluation remains fail-closed. On provenance-store loss:

1. mark the affected scope `reconciliation_required`;
2. stop automatic dispatch-targeting commands;
3. surface `CONTAINMENT_PATH_UNAVAILABLE` to Owner;
4. permit containment only through an explicitly selected break-glass/manual reconciliation path after independent local verification;
5. record that operation as manual/break-glass evidence, not as a normal policy-gate success.

The break-glass path MUST NOT treat Owner assertion alone as proof of locality. It must use evidence outside the failed provenance store. If locality cannot be established, the gate remains blocked. A direct Orca GUI/CLI action outside the supervised flow remains outside this gate's authorization claim.

#### Option B — add an Orca local-only stop/discovery primitive

Strongest automated containment, but requires Orca source modification and an expanded scope decision.

#### Option C — Owner assertion bypass inside the normal gate

Not recommended. A mistaken assertion can silently invoke federated remote stop, reproducing B2 through an emergency bypass.

### Option B — add a local-only Orca discovery API

Would allow authoritative federation lookup without remote RPC, but requires Orca source modification and therefore violates current v1 boundary. Not available unless Owner expands scope.

### Option C — admit federation into v1

Requires federated identity, policy, observation, audit, and cross-runtime threat model. Largest scope; not recommended.

**Recommendation:** G-9A.

**Tests affected:** extend A5; add federated/unknown dispatch ID refusals for stop, abandon, show, and read; test provenance-store failure and spoofed provenance input.

---

# Gate G-10 — Independent observation ceiling (B3)

## Source evidence

- `orchestration-workers.ts:95–105`: worker-start records `timeoutMs`, default 60,000ms, in start options.
- `orchestration-workers.ts:196–200`: the value is passed to `waitForTerminal(... condition: 'tui-idle')` during `agent_readiness`.
- `orchestration-workers.ts:250–258`: returned `timeoutMs` is the startup readiness budget.

It is not a total worker execution budget.

## OWNER DECISION REQUIRED

### Option A — independent policy parameter **(recommended)**

Introduce a Hermes policy/observation parameter:

```yaml
observation:
  default_ceiling_ms: 2700000   # 45 minutes
```

Rules:

1. `worker-start --timeout-ms` remains an Orca startup-readiness argument and stays in `args_digest`.
2. It MUST NOT set or shorten the total observation ceiling.
3. The ceiling is taken from the exact policy artifact bound by `policy_sha256`.
4. A per-invocation override is absent in v1 unless separately approved.
5. Continue-observing after the ceiling cannot reuse an already-expired deadline; Owner must choose a new explicit observation deadline or a stop/retry action.

### Option B — allow per-invocation ceiling override

Adds `observation_ceiling_ms` to the Hermes request and ApprovedInvocation binding. More flexible but expands authorization surface.

### Option C — fixed 45-minute constant

Simpler but requires code/spec update for operational tuning.

**Recommendation:** G-10A, with a new explicit Owner-selected deadline on “continue observing after ceiling”.

**Tests affected:** A16/A17; add independence test proving worker-start `--timeout-ms` does not alter observation ceiling.

---

# Gate G-11 — Effective invocation identity and executor environment (B4/B8)

## Source evidence

- `orchestration.ts:205–235`, `322–359`: `from` can resolve from explicit flag, `ORCA_TERMINAL_HANDLE`, pane reminting, cwd/focus, and env-derived dev mode.
- `orchestration-workers.ts:57–77`, `95–105`: omitted worktree/repo values derive from coordinator worktree/repo.
- `orchestration-workers.ts:30–37`: caller terminal context determines the bound run and fences explicit mismatches.

Byte-identical argv can therefore act on different effective terminals, runs, worktrees, repos, and preamble modes.

## OWNER DECISION REQUIRED

### Option A — structured request + controlled execution context **(recommended)**

The executor MUST construct argv from a typed request. Arbitrary caller-supplied raw argv is not executable.

Normative requirements:

1. `--from` and `--run` are mandatory for `dispatch` and `worker-start`.
2. Worker-start MUST use an explicit worktree mode and effective identity:
   - existing worker: exact resolved worktree ID;
   - new worker: explicit `new-child`/`new-top-level` plus exact repo ID and base branch.
3. Omitted repo/worktree context defaults are rejected in the gated flow.
4. Executor uses a fixed documented cwd or binds canonical cwd into the approved input.
5. Executor defines a closed environment contract:
   - execution-relevant `ORCA_*` variables are included in the canonical digest input;
   - transport/auth variables needed to reach Orca are separately identified;
   - a variable that affects both target selection and command semantics cannot be scrubbed without changing the target.
6. `devMode` is deterministically derived during preflight from the exact execution environment and bound into the canonical payload. It is recomputed at exec and mismatch blocks.
7. `ORCA_USER_DATA_PATH` is bound as target-instance identity because it selects the Orca metadata/runtime instance and also contributes to `devMode`.
8. Adapter records both requested selector and resolved effective identity with `observed_at`.
9. Any change between preflight resolution and exec is mismatch/`RUNTIME_UNKNOWN`, not silently accepted.

**Verified constraint:** `isDevCliInvocation()` is true when either `ORCA_DEV_CLI_INVOCATION == '1'` or `ORCA_USER_DATA_PATH` contains `orca-dev` (`src/cli/handlers/orchestration.ts:355–359`). `ORCA_USER_DATA_PATH` also selects the target Orca instance (`src/cli/runtime/metadata.ts:42–52`). Therefore “force devMode=false” is not generally implementable without potentially connecting to a different instance.

Canonical approval input becomes a versioned structured payload, not “argv bytes only”:

```text
canonical_invocation_v1 = {
  command,
  structured_args,
  effective_target_identity,
  fixed_cwd_or_bound_cwd,
  execution_relevant_env,
  normalization_version
}
args_digest = SHA256(canonical_json(canonical_invocation_v1))
```

### Option B — bind ambient cwd/env without controlling it

Easier but fragile and harder to reproduce/audit.

### Option C — retain argv-only digest

Not acceptable while environment/context changes effective targets.

**Recommendation:** G-11A.

**Tests affected:** A1; add same-argv/different-env, same-argv/different-cwd, selector substitution, context-default omission, and TOCTOU tests.

---

# Gate G-12 — Closed command and flag schemas (B5)

## Source evidence

Worker-start accepts more execution-affecting flags than v0.4 lists:

- `model`, `effort`
- `display-name`, `comment`
- `retry-request`
- ambient `devMode`

Dispatch also accepts:

- `dry-run`
- `return-preamble`
- `retry-request`
- ambient `devMode`

`callMutation` consumes `--retry-request` for all four mutating handlers.

## OWNER DECISION REQUIRED

Choose an explicit v1 policy for every surface; absence from the table means `block`.

| Surface | Option A | Option B | Recommendation |
|---|---|---|---|
| `worker-start --model` | allow + bind | reject | **allow + bind** |
| `worker-start --effort` | allow + bind | reject | **allow + bind** |
| `--display-name` | allow + bind | reject | **allow + bind** |
| `--comment` | allow + bind | reject | **allow + bind** |
| `--retry-request` | intent-stable + bind | reject entirely | **intent-stable + bind** |
| `dispatch --dry-run` | separate logged preview route, no mutation approval | reject | **separate preview route** |
| `dispatch --return-preamble` | allow + bind | reject | **allow + bind** |
| `devMode` | derive at preflight + bind | restrict gated flow to production instance only | **derive + bind** |
| unknown / duplicate / conflicting flags | reject | normalize | **reject** |

Additional requirements:

1. `--flag=value` and `--flag value` MUST normalize to the same structured field if both are valid CLI syntax.
2. Repeated single-valued flags are rejected before policy.
3. Boolean presence/absence is explicit in the canonical payload.
4. `retry-request` identifies the **Orca mutation intent**, not a single Hermes approval attempt. Hermes generates it once and stores it durably as `orca_request_id` under a stable `mutation_intent_id`.
5. A recovery attempt uses a new `operation_id`, fresh approval, and freshly recomputed digest, but MUST reuse the same `orca_request_id` only when method and canonical effective payload are identical and the purpose is to recover the same uncertain mutation.
6. A deliberate second mutation after a confirmed first outcome uses a new `mutation_intent_id` and new `orca_request_id`.
7. The caller cannot supply or rotate `orca_request_id` independently. A method/payload mismatch against an existing intent is blocked.
8. `setup` is represented as closed enum `run | skip | inherit`, not a free string.

**Why:** Orca's durable mutation ledger keys receipts by `(caller_fingerprint, request_id)` and rejects method/payload changes. Reusing an intent-stable request ID can return a completed receipt or a worker-start acceptance record without repeating the mutation. Generating a new request ID for each new `operation_id` would disable this protection exactly during outcome ambiguity.

**Fallback Owner option:** reject `--retry-request` entirely in v1. This gives up Orca idempotent recovery but is safer than falsely claiming protection while rotating request IDs per approval attempt.

**Tests affected:** A1/A2/A11; add full command × flag allow/reject matrix, policy digest mismatch, same-intent recovery, payload-mismatch refusal, and deliberate-second-intent tests.

---

# Gate G-13 — Observation-command classification (B6)

## Source evidence

- `orchestration.ts:613–707`: CLI `orchestration check` uses `callMutation` and supports wait/peek/unread/all/ack/inject/retry-request.
- RPC `orchestration.ts:695–785`: it may start federation relay, acknowledge deliveries, create delivery rows, and take an exclusive mailbox waiter.
- `orchestration-worker-control.ts:42–87`: federated `worker-show` updates setup evidence and reconciles state.
- `orchestration-worker-control.ts:104–117`: local `worker-show` can also mark start/stop unknown after runtime restart.

## OWNER DECISION REQUIRED

### Option A — conservative observation surface **(recommended)**

1. Remove `orchestration check --wait` from v1.
2. Reclassify `worker-show` as a **gated side-effecting observation**, because it can update/reconcile Orca state.
3. Allow `worker-read` only for a G-9 known-local dispatch; log every invocation/result.
4. Keep `terminal read` and bounded `terminal wait` as logged read-only observations.
5. Keep `run-current`, `run-show`, `task-list`, and `dispatch-show` as read-only after source verification.
6. Completion/state observation uses gated `worker-show` plus known-local `worker-read`/terminal evidence, not mailbox `check`.

### Option B — retain restricted `check --peek`

Only if the adapter enforces a fixed safe flag subset and accepts relay/delivery side effects as v1 scope. Older-runtime compatibility behavior makes this harder to guarantee.

### Option C — gate all `check` operations

Preserves mailbox functionality but broadens the mutating policy surface substantially.

**Recommendation:** G-13A.

**Tests affected:** replace old check allowlist tests; add refusal of wait/ack/inject/unread/all; test worker-show cannot run without a matching observation approval and known-local provenance.

---

# Gate G-14 — Command-specific total state classifiers (B7)

## Source evidence

Orca defines:

```text
dispatch_status = pending | dispatched | completed | failed | circuit_broken
worker_state = starting | ready | start_unknown | failed | succeeded |
               stopping | stop_unknown | stopped | abandoned
```

v0.4 leaves reachable states unmapped, including `starting`, `stopping`, `stopped`, and dispatch-only contexts with no worker row.

## OWNER DECISION REQUIRED

### Option A — separate total classifiers **(recommended)**

Do not force dispatch-only and worker-lifecycle operations into one partial matrix.

#### Dispatch-only classifier

| Dispatch status | Classified state |
|---|---|
| `circuit_broken` | `CIRCUIT_BROKEN` |
| `failed` | `FAILED` |
| `completed` | `OK` |
| `pending` / `dispatched` | `OBSERVATION_PENDING` |
| missing/malformed/query failure | `RUNTIME_UNKNOWN` |

#### Worker-lifecycle classifier precedence

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

Owner must also decide route semantics:

| State | Recommended route |
|---|---|
| `STARTING` | continue bounded observation |
| `STOPPING` | continue bounded stop observation; no dispatch/start retry |
| `STOPPED` | terminal contained; no automatic restart |

### Option B — one exhaustive 5×(9+absent) matrix

More explicit but verbose and easier to make inconsistent.

**Recommendation:** G-14A, with deterministic tests enumerating every enum pair plus worker-absent cases.

**Tests affected:** rewrite A4/A8 as command-specific exhaustive tests and mandatory default `RUNTIME_UNKNOWN` assertion.

---

# Gate G-15 — Canonicalization readiness boundary (B8)

B8 is not a separate source mismatch; it is the consequence of B4/B5. A normalization algorithm can remain an implementation detail only after the architecture fixes the complete input domain.

## OWNER DECISION REQUIRED

### Option A — v0.5 fixes the normative input domain; implementation supplies algorithm **(recommended)**

v0.5 MUST define:

- complete structured fields per command;
- effective target identity;
- cwd/env contract;
- omitted/default behavior;
- unknown/duplicate flag rejection;
- canonical payload version;
- policy digest binding.

The implementation PR may then choose/implement the deterministic canonical JSON encoder and must provide golden vectors/tests before enforcement.

### Option B — v0.5 includes exact byte-level canonicalization algorithm

Stronger upfront contract, but may over-constrain implementation before the code repository and language are selected.

### Option C — retain current deferral

Not acceptable because implementation would invent the security boundary.

**Recommendation:** G-15A.

**Tests affected:** canonical golden vectors, equivalent syntax, omitted-vs-explicit defaults, path/case behavior, env/cwd variance, and policy digest mismatch.

---

# Gate G-16 — Additional review observations (non-blocking but consequential)

These are not required to acknowledge B1–B8, but resolving them now avoids another review cycle.

## G-16.1 Discovery freshness

Single-use prevents replay but not stale first use. Decide whether preflight evidence has a maximum age independent of approval expiry.

- **Option A (recommended):** short command-specific evidence freshness window; stale evidence requires reclassification and a new decision.
- Option B: no freshness bound until v2 expiry.

## G-16.2 Consumed-but-unresolved crash sweep

After restart, a consumed operation with no durable outcome may mean crash-after-spawn.

- **Option A (recommended):** startup reconciliation marks every consumed-without-outcome operation `reconciliation_required`.
- Option B: manual detection only.

## G-16.3 Recovery-channel identity

- **Option A (recommended):** implementation contract must identify a durable recovery channel with failure isolation from the primary outcome log.
- Option B: best-effort control-plane report only; accept possible silent audit-loss risk if both share failure domain.

## G-16.4 Post-ceiling continue behavior

Because the absolute deadline does not reset, “continue” after ceiling needs a new explicit deadline.

- **Option A (recommended):** Owner supplies/selects a new observation deadline; counter resets, original deadline remains in audit history.
- Option B: same expired ceiling repeatedly escalates, effectively making continue unusable.

---

# Gate G-17 — Stop/abandon outcome truthfulness

## Verified source contract

- `worker-stop` RPC outcomes include `alreadySettled`; they do **not** expose a `stale` field.
- `worker-abandon` outcomes include `alreadySettled`, `stale`, `processAction`, and `residualResources`.
- The CLI's TypeScript result annotations omit some fields, but this is compile-time only. `--json` serializes the complete RPC success object; it does not runtime-strip those fields.

## OWNER DECISION REQUIRED

### Option A — require JSON and bind complete command-specific outcome schemas **(recommended)**

1. Every supervised invocation uses `--json`.
2. Stop outcome schema includes `alreadySettled`, `state`, `processAction`, warning/error and close evidence where returned.
3. Abandon outcome schema includes `alreadySettled`, `stale`, `processAction`, `residualResources`, state, and warning.
4. `worker-abandon stale=true` is a no-op on a non-current dispatch and MUST NOT be reported as successful containment.
5. `alreadySettled=true` is historical/no-new-effect evidence; containment status must derive from the returned state plus fresh observation, not command exit alone.
6. Missing/malformed required outcome fields trigger `OUTCOME_SCHEMA_MISMATCH` and reconciliation; they are not synthesized.

### Option B — rely on text output

Not acceptable for deterministic outcome classification.

---

# Gate G-18 — Filesystem/Git authority wording

`worker-start --worktree new-child|new-top-level` invokes `createManagedWorktree`, creating worker topology and associated worktree/branch effects. v0.4's statement that Git/filesystem mutation authorization is globally out of scope is therefore false as written.

## OWNER DECISION REQUIRED

### Option A — narrow the exclusion **(recommended)**

State that v1 authorizes only the filesystem/Git effects explicitly performed by an approved local `worker-start` within its bound repo/worktree topology. Global filesystem/Git mutations outside those command-specific effects remain out of scope.

### Option B — prohibit new worktree modes

Allow only existing exact worktree IDs. Smaller authority surface, materially reduced worker-start capability.

---

# Gate G-19 — Claude permission-prompt observation gap

## Verified behavior

`tui-idle` is satisfied only by agent status `idle`, explicitly not `permission` (`src/main/runtime/orca-runtime.ts:17427–17432`). The typed `blockedReason` set contains six Codex prompt classes only (`src/shared/runtime-types.ts:711–717`). Under G-8A, a Claude permission prompt therefore normally produces probe code `timeout`, resets the checkpoint counter as busy, and is surfaced only by the absolute ceiling.

## OWNER DECISION REQUIRED

### Option A — accept and document bounded residual risk **(recommended for v1)**

Record that Claude permission prompts may take up to the observation ceiling to escalate. Keep the independent ceiling as the deterministic bound.

### Option B — add a separately verified agent-status observation signal

Potentially faster, but requires a new command/source contract and attack tests. It must distinguish authenticated worker identity and stale status before becoming policy input.

---

# Gate G-20 — Trusted outcome reconstruction

The earlier recommendation to delete all reconstruction is too broad. Orca already provides a durable mutation ledger:

- identity: caller fingerprint + stable request ID;
- method/payload hash mismatch refusal;
- completed receipt replay;
- worker-start pending acceptance record containing `dispatchId`;
- `operation_unknown` recovery metadata after restart.

## OWNER DECISION REQUIRED

### Option A — allow only enumerated receipt-bound reconstruction **(recommended)**

1. Hermes durably maps `mutation_intent_id` and `orca_request_id` to every fresh `operation_id` used to recover that same intent.
2. Reissue must use the same method and canonical effective payload; mismatch blocks.
3. A completed Orca receipt replay may reconstruct the exact outcome.
4. A worker-start durable acceptance receipt may reconstruct only acceptance/dispatch identity, not later worker success.
5. Pending/unknown stop or abandon without a completed receipt cannot be labelled successful; use fresh live discovery and Owner reconciliation.
6. Reconstructed evidence is appended as a recovery event and never rewrites the original decision/consumption record.

### Option B — delete reconstruction; fresh live discovery only

Simpler but discards Orca's native at-most-once receipt and may lose the safest way to recover a worker-start dispatch ID.

---

# Gate G-21 — Reconciliation precedence and containment authority

The test list required reconciliation precedence but the first package did not define it.

## OWNER DECISION REQUIRED

### Option A — explicit highest-precedence reconciliation state **(recommended)**

1. `reconciliation_required` outranks ordinary `allow`, `require_approval`, observation escalation, retry, and resume routes.
2. A normal approval cannot clear or bypass it.
3. Automatic later mutation remains blocked.
4. Only an explicit Owner reconciliation decision may authorize a specifically scoped recovery or containment action with a new `operation_id`.
5. That action uses the same intent-stable `orca_request_id` only when recovering the exact uncertain mutation; a deliberate new containment effect uses a new intent/request ID.
6. Reconciliation remains set until durable closure evidence is appended.
7. An observation approval cannot clear reconciliation.
8. If G-9 locality evidence is unavailable, the normal gate still cannot call stop; G-9.1 break-glass rules apply.

### Option B — ordinary approval may override reconciliation

Not recommended; it collapses audit-loss containment into the normal approval path.

---

# Consolidated recommended v0.5 profile (not yet decided)

| Gate | Recommended option |
|---|---|
| G-8 | A — timeout=busy, blockedReason immediate approval, other errors unknown |
| G-9 | A — durable known-local dispatch provenance |
| G-9.1 | A — fail-closed normal path + evidence-based break-glass reconciliation |
| G-10 | A — policy-bound independent 45-minute ceiling |
| G-11 | A — structured request + controlled cwd/env + explicit identities |
| G-12 | mixed closed-schema recommendations in table |
| G-13 | A — remove check; gate worker-show; local-only worker-read |
| G-14 | A — separate total dispatch/worker classifiers |
| G-15 | A — fix normative input domain in spec; algorithm in implementation |
| G-16.1 | A — evidence freshness |
| G-16.2 | A — consumed-without-outcome startup reconciliation |
| G-16.3 | A — identified durable recovery channel |
| G-16.4 | A — new Owner-selected deadline after ceiling |
| G-17 | A — JSON + complete stop/abandon outcome schemas |
| G-18 | A — authorize only bounded worker-topology Git/filesystem effects |
| G-19 | A — document Claude permission-prompt latency as bounded residual risk |
| G-20 | A — enumerated receipt-bound reconstruction only |
| G-21 | A — reconciliation has highest precedence; explicit scoped Owner recovery only |

---

# Test-suite consequences

The v0.4 A1–A20 suite is insufficient. If the recommended profile is approved, v0.5 must at minimum add or rewrite tests for:

1. exact `timeout` RPC code resets probe counter, not `RUNTIME_UNKNOWN`;
2. blockedReason immediate escalation;
3. other probe errors ⇒ `RUNTIME_UNKNOWN` and counter discard;
4. federated/unknown dispatch IDs refused on stop/abandon/show/read;
5. provenance-store failure and spoofed provenance refusal;
6. observation ceiling independent of worker-start timeout;
7. same argv under different cwd/env cannot share approval;
8. explicit from/run/effective worktree/repo binding;
9. complete command × flag allow/reject matrix;
10. `retry-request` bound to stable mutation intent, not rotated per operation;
11. `check` variants refused under conservative profile;
12. worker-show requires side-effecting observation approval;
13. total dispatch classifier and total worker matrix/default;
14. canonical payload golden vectors;
15. policy digest mismatch;
16. concurrent consumption and all crash boundaries;
17. consumed-without-outcome startup reconciliation;
18. recovery-channel failure;
19. stale evidence reclassification;
20. reconciliation precedence over ordinary approval;
21. post-ceiling continue with new explicit deadline;
22. intent-stable Orca request ID across uncertain-outcome recovery attempts;
23. new intent/request ID for a deliberate second mutation;
24. method/payload mismatch on reused request ID blocks;
25. worker-stop `alreadySettled` and worker-abandon `stale` cannot be reported as fresh containment;
26. full JSON outcome schema mismatch triggers reconciliation;
27. provenance-loss blocks normal stop and surfaces break-glass decision without federation fail-open;
28. completed-receipt and worker-start-acceptance reconstruction boundaries;
29. Claude permission prompt reaches the documented absolute ceiling path;
30. bounded worker-topology Git/filesystem authority; other mutations remain refused.

No test above has been run; no implementation exists.

---

# Owner response template

Copy/fill:

```text
G-8:  A / B / C
G-9:  A / B / C
G-9.1: A / B / C
G-10: A / B / C
G-11: A / B / C
G-12:
  model: allow+bind / reject
  effort: allow+bind / reject
  display-name: allow+bind / reject
  comment: allow+bind / reject
  retry-request: intent-stable+bind / reject
  dispatch dry-run: separate-preview / reject
  return-preamble: allow+bind / reject
  devMode: derive+bind / production-only
  unknown-duplicate-conflicting: reject / normalize
G-13: A / B / C
G-14: A / B
G-15: A / B / C
G-16.1: A / B
G-16.2: A / B
G-16.3: A / B
G-16.4: A / B
G-17: A / B
G-18: A / B
G-19: A / B
G-20: A / B
G-21: A / B
```

Until Owner responds, v0.4 remains RED and implementation remains unauthorized.
