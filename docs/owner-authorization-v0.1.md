# Owner Authorization — Standalone Hermes–Orca Policy Gate v1

**Status:** AUTHORIZED FOR BOUNDED IMPLEMENTATION  
**Recorded at:** 2026-08-10T16:22:23+08:00  
**Authority source:** Owner instruction in the active Hermes conversation: create a new standalone policy-gate repository, operate it autonomously, and prompt the Owner only at major gates.  
**Target repository:** `MarcoF9254/hermes-orca-policy-gate`  
**Visibility:** PRIVATE  
**Local target:** `C:\AI-Projects\hermes-orca-policy-gate`  
**Architecture basis:** `docs/spec/option_b_spec_v0.5.md`, SHA-256 `6f5d855f31909b7fb9836a9e788628107159daf767212d8bac096ff94ad7aa63`  
**Orca source-contract basis:** `403c60bbadb734d870a22c372aca8a988e348d21`

## 1. Authorized outcome

Build and exercise a standalone deterministic Python policy gate implementing the reviewed Option B v0.5 supervised Hermes → Orca flow without modifying Orca.

Authorized activities:

1. create and configure this private GitHub repository;
2. establish a clean `main` baseline;
3. implement on `feat/v1-policy-gate` using strict TDD;
4. run deterministic unit/integration tests and safe read-only/dry-run Orca smoke tests;
5. start the local Orca application/runtime when required for a safe smoke test;
6. commit and push validated bounded changes;
7. open a Draft PR and observe CI;
8. conduct Hermes and independent Claude read-only reviews;
9. correct review blockers within the exact allowlist and revalidate.

## 2. Exact implementation changed-path allowlist

Only these paths may be created or modified on the implementation branch:

```text
.github/workflows/ci.yml
README.md
pyproject.toml
uv.lock
config/policy.example.json
schemas/policy.schema.json
src/hermes_orca_gate/__init__.py
src/hermes_orca_gate/__main__.py
src/hermes_orca_gate/canonical.py
src/hermes_orca_gate/classifiers.py
src/hermes_orca_gate/cli.py
src/hermes_orca_gate/errors.py
src/hermes_orca_gate/models.py
src/hermes_orca_gate/orca_cli.py
src/hermes_orca_gate/policy.py
src/hermes_orca_gate/service.py
src/hermes_orca_gate/stores.py
tests/conftest.py
tests/fixtures/policy.invalid.json
tests/fixtures/policy.valid.json
tests/test_canonical.py
tests/test_classifiers.py
tests/test_cli.py
tests/test_contract_a01_a36.py
tests/test_orca_cli.py
tests/test_policy.py
tests/test_service.py
tests/test_stores.py
docs/implementation-v1.md
docs/operator-guide.md
```

A path not listed above is not implicitly authorized. If another path becomes necessary, stop before modifying it and report the exact blocker and smallest scope expansion.

## 3. Implementation constraints

- Python `>=3.11,<3.13`.
- Runtime dependencies: Python standard library only.
- JSON is the v1 policy format; YAML parsing is not authorized.
- Canonical JSON and golden vectors must be deterministic and versioned.
- The executor constructs structured argv and invokes the installed Orca CLI; it never executes caller-provided argv or a shell command string.
- Runtime stores use durable SQLite transactions or an equally deterministic stdlib mechanism; write success must be checked.
- All command/state/flag/federation/provenance/receipt rules in the reviewed specification remain normative.
- The implementation may narrow behavior to fail closed; it may not widen the specification.

## 4. Safe autonomous run boundary

Authorized without another prompt:

- local tests and CI;
- syntax/static checks;
- read-only Orca discovery (`status`, admitted `run-*`, `task-list`, `dispatch-show`, proven-local `worker-read`, bound terminal read/wait);
- `orchestration dispatch --dry-run` only when all preconditions are satisfied;
- simulated/fake-executor integration tests;
- starting/stopping the policy-gate process itself;
- starting Orca to make the local runtime reachable for the above safe checks.

Not authorized without a new major gate:

- a real Orca mutation (`dispatch dry_run=false`, worker-start, worker-stop, worker-abandon);
- modifying Orca source or installed binaries;
- federation, SSH remote execution, or remote environment access;
- secrets, credential migration, or OS-account/ACL isolation changes;
- Ready transition, merge, release, package publication, deployment, or self-enforcing replacement of normal Orca entrypoints;
- public visibility;
- any architecture/scope contradiction or path expansion.

## 5. Git/GitHub authority

Authorized:

- initial baseline commit and push to `main` for this new empty repository;
- implementation commits on `feat/v1-policy-gate`;
- branch push;
- Draft PR creation and CI observation.

Not authorized:

- force-push;
- deleting branches or repository;
- changing visibility;
- branch-protection changes;
- marking Ready;
- requesting reviewers;
- merge or release.

## 6. Evidence standard

Completion requires real tool output proving:

- exact changed paths are within §2;
- tests pass in the supported local interpreter and CI matrix;
- policy load fails closed;
- command schemas reject unknown, duplicate, conflicting, and federated inputs;
- single-use consumption, stable mutation intent recovery, crash boundaries, provenance, freshness, and reconciliation behavior are tested;
- safe Orca smoke output is captured without claiming mutation success;
- both reviews bind the exact commit SHA.

Generated code or one passing command alone is not completion evidence.
