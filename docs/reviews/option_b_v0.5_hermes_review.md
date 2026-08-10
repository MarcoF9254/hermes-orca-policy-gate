# Option B v0.5 — Hermes Specification / Architecture Review

## 1. Review identity

| Item | Exact identity |
|---|---|
| Candidate | `option_b_spec_v0.5.md` |
| Candidate SHA-256 | `6f5d855f31909b7fb9836a9e788628107159daf767212d8bac096ff94ad7aa63` |
| Decision package | `option_b_v0.5_owner_decision_package_v0.2.md` |
| Decision-package SHA-256 | `b2146726945e4e450b62a8dd027127f028473c4035c6c6eef02a8da206da2a06` |
| Orca source basis | `403c60bbadb734d870a22c372aca8a988e348d21` |
| Review type | Fresh full specification / architecture review |

## 2. Verdict

**PASS — specification/contract level.**

Open blocking findings: **0**.

This verdict does not authorize implementation. It is valid only for the exact candidate bytes and Orca source identity above. Independent Claude Code review remains required.

## 3. Blocking findings found and corrected before final review identity

These corrections are already included in the reviewed candidate hash; they are not open findings.

| ID | Original gap | Corrected contract |
|---|---|---|
| H-01 | `require_approval` lacked an immutable resolution/Owner-authorization record model | Added separate immutable `OwnerAuthorization`, post-authorization fresh resolution-decision event, and exact ApprovedInvocation references; no decision rewriting |
| H-02 | read-only terminal schema could be read as authority to inspect arbitrary local terminals | Restricted terminal read/wait to exact current preflight target or terminal bound by known-local provenance |
| H-03 | “positive” freshness value did not enforce the approved short-window requirement | Bounded each command-specific value to `1..60000 ms`; invalid/missing/over-limit fails policy load |
| H-04 | monotonic freshness was ambiguous across process restart/handoff | Bound evidence to a process epoch; epoch mismatch forces fresh discovery and a new decision |
| H-05 | human approval delay could make short evidence freshness unusable or invite silent extension | Required fresh deterministic discovery and policy re-evaluation after Owner approval; only unchanged action/args/target/state/intent/scope may resolve |

## 4. Decision-package trace

| Approved gate | v0.5 disposition | Result |
|---|---|---|
| G-8 | exact terminal probe taxonomy: idle success, timeout=busy/reset, blockedReason=approval, other error=unknown | PASS |
| G-9 / G-9.1 | durable known-local provenance, fail-closed loss, evidence-based break-glass only | PASS |
| G-10 | policy-bound independent 45-minute observation ceiling | PASS |
| G-11 | typed request, explicit identities, bound cwd/env/devMode/target instance | PASS |
| G-12 | closed flag schemas, intent-stable retry request, separate dry-run preview | PASS |
| G-13 | check removed; worker-show side-effecting/gated; worker-read known-local | PASS |
| G-14 | separate total dispatch and worker classifiers with default unknown | PASS |
| G-15 | normative canonical input domain fixed; byte algorithm/golden vectors required in any later implementation | PASS |
| G-16.1 | bounded command-specific evidence freshness | PASS |
| G-16.2 | consumed-without-outcome startup reconciliation | PASS |
| G-16.3 | separately addressed durable recovery/error channel | PASS |
| G-16.4 | new explicit Owner deadline after observation ceiling | PASS |
| G-17 | complete JSON stop/abandon outcome truthfulness | PASS |
| G-18 | only exact worker-topology filesystem/Git effects authorized | PASS |
| G-19 | Claude permission-prompt detection latency documented as bounded residual risk | PASS |
| G-20 | enumerated receipt-bound reconstruction only | PASS |
| G-21 | reconciliation precedence and exact Owner reconciliation authority | PASS |

## 5. Source-contract cross-checks

Supported against Orca source basis:

1. `src/shared/orchestration-rpc-contract.ts:18–60`
   - dispatch, workerStart, workerStop, and workerAbandon are durable mutation methods;
   - dispatch `dryRun=true` is not a mutation.
2. `src/main/runtime/rpc/orchestration-mutation-executor.ts:28–105,128–175`
   - mutation identity uses caller fingerprint + request ID;
   - exact method/payload hash mismatch is refused;
   - completed receipt replay and pending worker-start recovery metadata are supported.
3. `src/main/runtime/rpc/methods/orchestration-worker-stop.ts:13–173`
   - dispatch ID alone can select federation;
   - stop result includes mandatory dispatch/state/alreadySettled/processAction plus variant close/warning/lastError;
   - no stop `stale` field exists.
4. `src/main/runtime/rpc/methods/orchestration-worker-control.ts:218–267`
   - abandon includes alreadySettled, stale, processAction, warning, and residualResources;
   - stale/no-current behavior is a no-op.
5. `src/main/runtime/rpc/methods/orchestration-workers.ts:57–117`
   - ambient worktree/repo defaults exist upstream;
   - timeout is worker-start readiness configuration;
   - setup accepts explicit/default semantics.
6. `src/main/runtime/rpc/methods/orchestration.ts:1200–1308`
   - dry-run returns preview without dispatch mutation;
   - normal dispatch creates local dispatch context.
7. Previously verified v0.2 evidence remains incorporated for terminal wait semantics, devMode/`ORCA_USER_DATA_PATH`, worker-show/read federation paths, state enums, and worktree creation effects.

## 6. Deterministic artifact checks

| Check | Result |
|---|---|
| UTF-8 without BOM | PASS |
| Markdown fences balanced | PASS |
| Heading text unique | PASS |
| Required tests sequential A1–A36 | PASS |
| Owner-decision markers present | PASS |
| Old timeout→ceiling rule absent | PASS |
| force-devMode rule absent | PASS |
| per-operation retry-request rule absent | PASS |
| v0.4 hash unchanged | PASS |
| decision-package hash unchanged | PASS |
| Orca source HEAD unchanged | PASS |

## 7. Non-blocking observations / residuals

1. No implementation exists, so A1–A36 and canonicalization golden vectors have not run.
2. Physical failure-domain independence of the recovery channel is deployment-specific and unverified. The spec correctly forbids claiming it without evidence.
3. The 45-minute observation ceiling and ≤60-second evidence-freshness bound are Owner/policy design parameters, not Orca-derived behavior.
4. Claude permission prompts can remain undetected until the absolute ceiling; v1 explicitly accepts and tests this residual risk.
5. Orca source drift invalidates this review identity and requires a fresh contract review.

## 8. Review-gate state

- Hermes review: **PASS**.
- Independent Claude Code review: **PENDING**.
- Implementation authorization: **NO**.
