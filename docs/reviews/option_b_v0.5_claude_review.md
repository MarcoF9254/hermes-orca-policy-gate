# Independent Claude Code Review — Option B v0.5

## Review identity

- Candidate `option_b_spec_v0.5.md` SHA-256: `6f5d855f31909b7fb9836a9e788628107159daf767212d8bac096ff94ad7aa63` — **matches** expected.
- Decision package `option_b_v0.5_owner_decision_package_v0.2.md` SHA-256: `b2146726945e4e450b62a8dd027127f028473c4035c6c6eef02a8da206da2a06` — **matches** expected.
- Orca `git rev-parse HEAD`: `403c60bbadb734d870a22c372aca8a988e348d21` — **matches** expected basis.
- v0.5 self-declared source basis, decision-package basis, and SHA (spec lines 7–9) are internally consistent with the above. I did not read `option_b_v0.5_hermes_review.md` or any prior verdict; conclusions below are derived directly from the candidate, the approved decision package, and Orca source.

## Verdict

**PASS.**

v0.5 faithfully and completely incorporates every Owner-approved recommended decision G-8 through G-21 (including G-9.1 and G-16.1–G-16.4), and every load-bearing source-contract claim is true at the exact HEAD. I independently confirmed against source: the inverted-then-corrected terminal probe taxonomy (`Error('timeout')` reject at `orca-runtime.ts:17375/17474`, `satisfied = blockedReason === undefined` at `36998`, `timeout` as a stable passthrough code at `rpc/errors.ts:53`, six Codex-only `blockedReason` values at `runtime-types.ts:711–717`, `tui-idle` excludes `permission` at `17430–17432`); federation reachable by bare dispatch ID for stop/show/read (`worker-stop.ts:19`, `worker-control.ts:42,134`); worker-show side effects both federated and on local restart (`worker-control.ts:45–87,104–117`); exact enums (`db.ts:397–398,565`); worker-start startup-timeout-vs-ceiling (`orchestration-workers.ts:104,197`) and new-worktree Git effects (`orchestration-workers.ts:58–72`); ambient `--from`/worktree/repo/devMode context and `consumer_fenced` (`orchestration-workers.ts:30–37`, CLI `orchestration.ts:355–359`); the mutation ledger identity `(callerFingerprint, requestId)` with method/payload-mismatch rejection, completed-receipt replay, and worker-start `operation_unknown` acceptance recovery (`orchestration-mutation-executor.ts:36–86,128–134`, mutation set at `orchestration-rpc-contract.ts:18–61`); the exact stop/abandon outcome shapes including no-`stale`-on-stop and `alreadySettled/stale/processAction/residualResources` on abandon (`worker-stop.ts:117–123`, `worker-control.ts:226–251`); and side-effect-free dry-run (`orchestration.ts:1231–1244`). The contract is internally total (both classifiers are exhaustive with a mandatory `RUNTIME_UNKNOWN` default), fail-closed (`default.action = block`), and fixes the complete normative input/authority domain in §4.1, deferring to implementation only the byte-level canonical encoder plus golden vectors — which the review instruction expressly permits. No specification/source-contract defect that would permit fail-open behavior remains.

## Blocking issues

None.

## Non-blocking observations

1. **(Source-confirmed) Worker-start acceptance-record dispatch ID is a documented “may,” not a guarantee.** §11.1/§11.2.2 rely on Orca retaining a worker-start acceptance record “containing `dispatchId`.” Source confirms the *mechanism* exists — `getPendingWorkerStartRecovery` parses `accepted.dispatchId` from a pending receipt (`orchestration-mutation-executor.ts:160–175`) and emits `operation_unknown` with the dispatch ID — but whether the pending receipt is populated with `accepted.dispatchId` before setup completes is an Orca-internal timing detail. v0.5 correctly hedges (“may establish dispatch ID/local provenance,” §11.2.2; provenance from “durable worker-start acceptance receipt,” §2.3), so this is not an overclaim; noting only that the reconstruction path can legitimately fall through to the generic `operation_unknown` branch, and §2.4/§10.5 already govern that outcome.

2. **(Source-confirmed) Abandon idempotency is executor-layer, not handler-layer — spec treatment is correct.** The `orchestration.workerAbandon` RPC handler does not itself read `orchestrationMutation` (`worker-control.ts:220`), unlike worker-stop which forwards `orchestrationMutation.requestId` to the remote (`worker-stop.ts:38`). Idempotent receipt recovery for abandon is nonetheless provided because `orchestration.workerAbandon` is in `ORCHESTRATION_MUTATION_METHODS` (`orchestration-rpc-contract.ts:28`) and the CLI routes it through `callMutation` (`orchestration.ts:979`), so the executor keys and replays its receipt. §2.1/§3.4/§11 are therefore accurate; this is a clarification, not a defect.

3. **(Test-coverage, supported by current evidence) Credential-rotation → receipt-recovery-unavailable → reconciliation lacks a dedicated deterministic assertion.** The normative requirement is stated (§2.1 rule 6; §11 final rule) and is source-grounded — `callerFingerprint` derives from the auth/device token (`orchestration-mutation-executor.ts:128–134`), so a rotated credential changes the ledger key and makes same-receipt recovery impossible. A2 and A27 assert that credential-binding *mismatch blocks*, but no A-item asserts the specific transition “credential unavailable/rotated ⇒ receipt recovery unavailable ⇒ `reconciliation_required`.” Recommend an explicit test; specification remains implementable without it.

4. **(Residual/unverified, correctly disclosed by the spec) Claude permission-prompt detection latency.** §8.4 accepts that a Claude permission prompt surfaces only as repeated probe `timeout` (source-confirmed: `blockedReason` is Codex-only at `runtime-types.ts:711–717`; `tui-idle` excludes `permission` at `orca-runtime.ts:17430–17432`) and may not escalate until the 45-minute ceiling. This is an accepted bounded residual risk, required to be documented and tested (A22), not a contract defect.

## Decision trace

| Gate | Subject | Incorporated in v0.5 | Verdict |
|---|---|---|---|
| G-8 | Probe taxonomy: `timeout`=busy/reset, `blockedReason`→immediate approval, other→UNKNOWN+discard | §8.1, §6.2 note | PASS |
| G-9 | Durable known-local dispatch provenance; unproven/federated denied pre-CLI | §2.3, §3.3–3.5, §3.7 | PASS |
| G-9.1 | Provenance-loss fail-closed + evidence-based break-glass; no Owner-assertion bypass | §2.4 | PASS |
| G-10 | Independent policy-bound 45-min ceiling, decoupled from `startup_timeout_ms` | §7, §8.3, §3.2 | PASS |
| G-11 | Structured request; controlled cwd/env; devMode derive+bind; `ORCA_USER_DATA_PATH` as target identity | §4.1 | PASS |
| G-12 | Closed schemas: model/effort/display-name/comment/return-preamble allow+bind; retry-request intent-stable; dry-run preview; devMode derive; unknown/dup/conflict reject; setup enum | §3.1–3.7, §2.1 | PASS |
| G-13 | Remove `check`; gate `worker-show`; local-only `worker-read` | §3.5, §9 | PASS |
| G-14 | Separate total dispatch + worker classifiers with mandatory default | §6.1, §6.2 | PASS |
| G-15 | Fix normative input domain in spec; byte algorithm + golden vectors in implementation | §4.2 | PASS |
| G-16.1 | Evidence freshness ≤ 60,000 ms, per command, process-epoch bound | §5.3, §7 | PASS |
| G-16.2 | Consumed-without-outcome startup reconciliation | §4.5, §10.5 | PASS |
| G-16.3 | Identified durable recovery channel independent of outcome append | §2.2, §10.2 | PASS |
| G-16.4 | Post-ceiling continue requires new Owner-selected deadline | §8.3, §8.5 | PASS |
| G-17 | `--json` + complete stop/abandon outcome schemas; `alreadySettled/stale` not fresh containment | §3.3, §3.4, §10.3 | PASS |
| G-18 | Authorize only bounded worker-topology Git/filesystem effects | §0.1.5, §0.2 | PASS |
| G-19 | Document Claude permission-prompt latency as bounded residual risk | §8.4 | PASS |
| G-20 | Enumerated receipt-bound reconstruction only | §11 | PASS |
| G-21 | Reconciliation highest precedence; explicit scoped Owner recovery only | §10.4, §7 | PASS |

## Test-contract assessment

A1–A36 is **sufficient at specification level.** The suite covers every normative security/concurrency/crash domain: unreachability without unconsumed ApprovedInvocation and digest/context mismatch (A1, A7, A34); policy/binding/PolicyLoadError/fail-closed (A2, A3); explicit run/from and `consumer_fenced` non-retarget (A4); full flag matrix with `--on`/federation/release-retain/every-check refusal (A5, A9); equivalence and omitted-vs-explicit distinctions (A6); provenance and federated/unknown-ID refusal for stop/abandon/show/read, plus provenance-store failure/G-9.1 (A10, A11, A12); exhaustive classifiers (A13, A14, A15); the full probe taxonomy including exact `timeout`, `blockedReason`, serial N=3, independent ceiling, post-ceiling deadline, and Claude residual path (A16–A22); atomic single-use consumption, all crash boundaries, pre/post-exec persistence, and recovery-channel/reconciliation precedence (A23–A26); intent-stable vs deliberate-second-mutation, acceptance-only reconstruction, and outcome truthfulness (A27–A30); dry-run non-substitution and bounded Git/FS authority (A31, A32); mandatory freshness with epoch mismatch (A33); and OwnerAuthorization resolution boundaries (A35, A36).

Recommended additions (non-blocking, do not gate PASS):

1. an explicit assertion for credential-rotation/unavailability ⇒ receipt-recovery-unavailable ⇒ `reconciliation_required`;
2. a targeted assertion that the §2.1A post-authorization resolution appends a *new* linked decision event and never mutates the basis decision;
3. an assertion that a worker-start crash leaving a pending receipt *without* `accepted.dispatchId` degrades to the generic `operation_unknown`/startup-reconciliation path.

All are refinements of existing coverage, not gaps that permit fail-open.

## Scope statement

This review was strictly read-only. Claude Code created, edited, patched, formatted, renamed, or deleted no file; no Git state, branch, commit, worktree, remote, or GitHub object was modified. Only read/search tools and exact identity commands (`git rev-parse HEAD`, `sha256sum`, `wc -l`) were used. Implementation of Option B remains **unauthorized**; a PASS verdict does not itself authorize implementation, which per §14 requires a separate explicit Owner authorization.
