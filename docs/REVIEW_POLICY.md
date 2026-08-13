# Review Policy v1 — Independent Dual Reviewer + Conditional Tier-3

**Effective:** this checkpoint onward. Does not retroactively invalidate
`docs/reviews/option_b_v0.5_review_verdict.md`.

**Supersedes:** the ad hoc review pattern used in that verdict, where one
of the two passing reviews was performed by Hermes itself.

## Decision

1. **Hermes MUST NOT act as a reviewer of its own proposed spec, code,
   or decision package**, regardless of framing (self-review,
   architecture review, sanity check). Hermes is the propose layer;
   it cannot also occupy the review layer. This closes a self-review
   gap present in the v0.5 review verdict and applies to every
   checkpoint from this point forward.

2. **Tier 1 + Tier 2 review is mandatory** for every Hermes-authored
   spec, decision package, or code change that reaches a reviewable
   checkpoint:
   - Reviewer 1: Claude Code CLI (Anthropic)
   - Reviewer 2: GLM5.2 (Zhipu)
   Both must independently reach PASS before a checkpoint is
   considered reviewed. Disagreement between them does not average
   out to PASS — see escalation rule below.

3. **Tier 3 review is conditional**, invoked only when a checkpoint
   meets any trigger below:
   - Reviewer: Gemini 3.1 Pro (Google DeepMind, via Nous Portal)
   - Triggers:
     - [ ] Tier 1 and Tier 2 disagree on verdict or on any blocking
           issue
     - [ ] Change touches `policy.rules.yaml`, gate logic, or
           fail-closed/break-glass behavior (e.g. provenance loss,
           reconciliation precedence)
     - [ ] Change is irreversible or has broad blast radius
     - [ ] Owner manually requests Tier-3

## Authority clarification

- All three reviewers are advisory. **None of them, individually or
  combined, has authority to approve implementation or merge.** Final
  sign-off remains with Owner.
- This document does not modify `policy.rules.yaml` or any gate
  decision (G-8–G-21). It governs the review workflow only, and lives
  outside the deterministic Policy Gate's enforcement scope.

## Prior verdict status

`option_b_v0.5_review_verdict.md` (PASS, reached under the prior
non-independent pattern) stands as-is and is not re-opened by this
policy. This policy governs checkpoints from this point forward only.

## Required artifact

This policy is recorded at `docs/REVIEW_POLICY.md`, separate from
`docs/spec/` and `policy.rules.yaml`, as source of truth for the
review-tier workflow.
