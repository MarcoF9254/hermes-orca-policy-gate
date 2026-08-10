# Project execution rules

## Authority

This repository implements the standalone deterministic gate defined by `docs/spec/option_b_spec_v0.5.md`.

- Owner decides architecture and consequential scope changes.
- Hermes orchestrates, verifies, and reports.
- Codex may mutate only exact authorized paths.
- Claude performs independent read-only review.
- Orca remains an external execution surface and MUST NOT be modified by this repository.

## Mandatory boundaries

1. No LLM judgment in authorization, state classification, canonicalization, retry, provenance, observation, or reconciliation.
2. Default deny. Invalid or missing policy is `PolicyLoadError`; never fall back to allow.
3. Caller-supplied raw argv, cwd, environment, target identity, provenance claims, and approval booleans are not authority.
4. Immutable Owner authorization records are separate from mutable operation records and are never rewritten to consumed/rejected states.
5. Git refs, dispatch IDs, terminal handles, operation IDs, mutation request IDs, and target identities are case-sensitive and are never normalized.
6. Federation is refused in v1.
7. No source change outside the exact changed-path allowlist in `docs/owner-authorization-v0.1.md`.
8. If implementation requires another path or contradicts the reviewed contract, STOP and report before modification.
9. TDD is mandatory: failing test first, observe RED, minimal GREEN, then refactor.
10. Runtime code uses Python standard library only. Test/development dependencies may be locked through `uv`.

## Git and GitHub

- `main` is the accepted baseline.
- Implementation occurs on `feat/v1-policy-gate`.
- Commit and push are authorized after deterministic validation.
- Open a Draft PR only. Do not mark Ready, request reviewers, merge, release, publish a package, or deploy without a new Owner gate.

## Validation minimum

- targeted tests during every TDD slice;
- complete pytest suite under Python 3.11 and, where available, 3.12;
- `uv lock --check`;
- `git diff --check`;
- compile/import/static syntax checks;
- exact changed-path verification;
- read-only and dry-run Orca smoke tests only;
- independent read-only review before Draft PR closure report.
