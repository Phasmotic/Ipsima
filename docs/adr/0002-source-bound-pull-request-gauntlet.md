# ADR-0002: Source-bound pull-request gauntlet

- Status: Accepted; Stage 1 authorized now, Stage 2 accepted but not yet implemented
- Date: 2026-08-27
- Decision baseline: `main` at `5a48deb`
- Stage 2 arm point: P1 landing, or the first outside contribution if earlier

## Context

Talaria's authoritative Apple workflow is deliberately `workflow_dispatch`-only. The local
launcher creates a fresh correlation token, dispatches one run for an exact published commit, and
accepts evidence only from the unique run whose source and dispatch identity both match. Accepting
merely “some run on the same SHA” would weaken that security property.

That workflow shape leaves an ordinary pull request with no automatic verification. A naive
`pull_request` workflow cannot immediately become authoritative: candidate code can change the
scripts that evaluate it. A green run produced by a candidate-controlled evaluator is useful
feedback, but it is not trusted phase evidence. Current contributions originate from trusted
same-repository branches, so Talaria can add that feedback now without misrepresenting its
authority and can add the attestation boundary when the trust model changes or P1 makes further
delay expensive.

The existing manual Tier B dispatch and correlation-token binding remain unchanged. This ADR
defines a separate two-stage pull-request architecture; it does not relax or replace that path.

## Decision

### Stage 1: advisory Linux G1–G5

Add a reusable Linux workflow core at `.github/workflows/linux-g1-g5-core.yml`. A small
`pull_request` caller references that core by its full 40-character commit SHA. The core runs on
`ubuntu-24.04` with read-only contents permission and no secrets, OIDC permission, self-hosted
runner, or cache. Checkout uses the full-SHA-pinned checkout action, `fetch-depth: 0`, and
`persist-credentials: false`.

Before running any file from the candidate checkout, the pinned workflow proves all of these:

- the event is `pull_request` in `Phasmotic/Talaria` on a GitHub-hosted Linux x64 runner;
- the environment is Ubuntu 24.04 and is not WSL;
- the checked-out commit exactly equals GitHub's nominated pull-request merge SHA;
- the repository is not shallow; and
- Swift reports exactly 6.3.3, the target is `x86_64-unknown-linux-gnu`, and the matching
  `llvm-cov`, `llvm-profdata`, and SourceKit components exist.

The candidate gauntlet has a distinct GitHub entry mode. It runs exactly G1, G2, G3, G4, and G5,
rechecks the hosted environment and complete history, and emits `G1–G5 GREEN` only after validating
one ordered PASS record for every gate. It never runs G6, dispatches Tier B, or emits `TIER A
GREEN` or `GAUNTLET GREEN`. G6 remains unresolved on Linux and authoritative only in the existing
source-bound macOS workflow.

This check is advisory. It is not local Tier A, Tier B, phase evidence, a green sentinel, or a
required branch-protection context. The full-SHA workflow pin protects the wrapper and its
pre-candidate checks, but the wrapper still invokes `scripts/gauntlet.sh` and related evaluator
code from the pull request. A candidate can modify that evaluator. Stage 1 therefore provides fast
feedback without making a security claim it cannot support.

The core and caller land as two history-preserving commits. The first commit creates the reusable
core; the second can then name that already-existing commit by literal SHA without an impossible
self-reference. Future core changes use the same deliberate core-then-pin sequence.

### Stage 2: trusted producer and default-branch attestor

Stage 2 adds a full-SHA-pinned macOS core and a default-branch `workflow_run` attestor. It also
turns the Linux producer into a trusted evaluation path: the immutable core must run a
source-pinned evaluator against the candidate checkout, or equivalently bind and verify every
evaluator byte it executes. Merely attesting a run that used candidate-modified gate logic is not
sufficient.

For pull-request runs, the GitHub-created `workflow_run` identity replaces the locally generated
correlation nonce. GitHub nominates one exact completed producer run; the attestor does not search
for any run sharing a commit. The attestor runs only trusted default-branch code, never checks out
or executes pull-request code, treats producer output as bounded untrusted data, and validates an
exact identity and evidence tuple including:

- repository, event, pull request, head repository and SHA, base repository and SHA, tested merge
  reference and SHA;
- producer workflow identity, path and workflow SHA, reusable-core path and full-SHA pin;
- run ID and attempt, canonical run and job URLs, and the exact expected job and step inventory;
  and
- one typed result for every applicable gate, followed by a second snapshot whose normalized
  identity and evidence digest are unchanged after log retrieval.

The largest Stage 2 implementation risk is first-failure collapse. Several current jobs stop at
their first failed step, so later gates are skipped. That cannot distinguish a deterministic FAIL
from missing evidence. Stage 2 must split independent gates or collect bounded typed results while
accounting for every expected gate. A workflow conclusion alone never proves FAIL; missing,
skipped, malformed, cancelled, stale, or contradictory evidence is BLOCKED.

The attestor publishes stable aggregate contexts with these semantics:

- complete applicable PASS evidence produces `success`;
- a determinate gate finding produces `failure`; and
- BLOCKED produces commit-status `error`, or another explicitly verified non-success check
  conclusion, never `neutral` or `skipped`.

After deliberate inversion canaries prove those mappings and representative runs establish
stability, the Linux G1–G5 aggregate and deterministic Tier B aggregate become required checks on
`main`. Individual shards remain diagnostic. G12 cold launch is stood down pending its P2
re-specification; after re-arm it stays advisory until representative burn-in proves the
statistical contract stable. G11, G12 streaming, and G14 retain their
governed N/A state until their real streaming-surface arm point; applicability is named and never
reported as PASS.

Stage 2 arms when P1 lands, before work proceeds beyond that landing. P1 materially expands the
HermesKit, test, and gate surface, so retrofitting trusted evaluation becomes more expensive after
that boundary. An outside contribution before P1 immediately preempts this schedule and arms Stage
2 because candidate-controlled evaluation then crosses the trust boundary the attestor exists to
defend.

### Fork posture

Candidate execution uses `pull_request`, a read-only token, and no secrets. Talaria will not use
`pull_request_target` to check out or execute a pull-request head. The Stage 2 attestor may receive
the completed-run event with trusted permissions, but it never executes candidate code.

If a future platform constraint requires reduced fork verification, that reduction must have a
distinct visible context and `REDUCED` result. It cannot silently reuse a green context or satisfy
branch protection.

## Security properties

- The manual Tier B path keeps its exact source-plus-correlation binding.
- Stage 1 never claims authority its candidate-controlled evaluator does not possess.
- GitHub-hosted execution is selected and validated directly; WSL markers cannot select it.
- A shallow clone cannot pass G5's history claim.
- No pull-request candidate receives repository secrets or a persisted checkout credential.
- Stage 2 accepts exactly one GitHub-nominated producer identity and fails closed on incomplete or
  drifting evidence.
- BLOCKED can never satisfy a required status check.

## Consequences

Stage 1 gives every pull request fast Linux feedback with the same exact compiler and tool pins as
the local loop, without installing Swift or caching compiler state. The cost is that its result is
explicitly advisory and branch protection has no required automated context until Stage 2.

Stage 2 is real restructuring work, not a trigger edit. It must separate trusted evaluator logic
from candidate source, preserve evidence after early failures, implement typed aggregation, and
prove branch-protection semantics. Full-SHA core pins also require deliberate two-commit updates;
that friction is the mechanism that makes evaluator changes reviewable.

Superseded Stage 1 runs may be cancelled by pull-request-number concurrency. Cancellation is
advisory feedback, never PASS. Stage 2 must classify cancellation as BLOCKED.

## Rejected alternatives

- **Make the direct Stage 1 result required.** Rejected because candidate code can modify the
  evaluator that produces it.
- **Accept any successful run on the same SHA.** Rejected because a shared source hash does not
  bind the workflow identity, attempt, evaluator, or evidence inventory.
- **Use a branch or tag for the reusable core.** Rejected because the referenced evaluator could
  move after review.
- **Use `pull_request_target` and check out the pull-request head.** Rejected as privileged
  execution of untrusted code.
- **Map BLOCKED to `neutral` or `skipped`.** Rejected because branch protection can treat that
  state as satisfied.
- **Infer FAIL from a failed workflow job.** Rejected because operational errors and skipped
  downstream gates share that conclusion.
- **Keep pull-request CI manual-only.** Rejected because it gives contributors no automatic
  feedback and allows the two execution paths to drift.
- **Build Stage 2 immediately.** Rejected while all contributors remain trusted and Stage 1 can
  provide honest advisory feedback; the deterministic P1/external-contributor arm point prevents
  indefinite delay.

## Revalidation

Revalidate this ADR when GitHub changes pull-request merge-ref behavior, reusable-workflow SHA
resolution, `workflow_run` identity fields, fork token permissions, status-check satisfaction, or
hosted runner images. A Swift, Ubuntu, action, gate-inventory, or reusable-core change must update
its exact pins and negative tests deliberately.

Before Stage 2 is enabled or any context becomes required, amend the implementation record with
the exact workflow paths, permissions, evaluator-provenance mechanism, status context names, and
observed PASS/FAIL/BLOCKED inversion evidence. Do not move the arm point merely because the work is
unfinished.
