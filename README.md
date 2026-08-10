# Hermes–Orca Policy Gate

Deterministic, standalone policy gate for the supervised Hermes → Orca orchestration flow.

## Status

**Bounded v1 implementation authorized; implementation not yet complete.**

The gate is external to Orca. It does not modify Orca and does not claim to prevent direct Orca GUI/CLI use outside the supervised flow. Out-of-flow operations are never represented as gate successes.

## Source contract

The implementation is governed by the reviewed Option B v0.5 specification under `docs/spec/` and the exact Orca source basis recorded there.

## Safety boundary

- deterministic authorization only; no LLM judgment in gate decisions;
- default deny and fail-closed policy loading;
- no federation in v1;
- no arbitrary caller argv execution;
- immutable Owner authorization records remain separate from executor operation records;
- local dispatch provenance is required for dispatch-targeting lifecycle operations;
- reconciliation has precedence over ordinary approval;
- no direct Orca mutation is permitted during bootstrap validation without a separately recorded live-run authorization.

See `AGENTS.md` and `docs/owner-authorization-v0.1.md`.
