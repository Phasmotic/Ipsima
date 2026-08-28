# Talaria governance

## Phase plan boundaries

The phase plan is ordered and condition-gated. Renumbering a phase does not change a gate's arm
condition or authorize work from another phase.

| Phase | Governed boundary |
| --- | --- |
| P0 | Establish the repository scaffold, source-pinned protocol derivation, safe capture fixture, and trustworthy gates. |
| P1 | Build `HermesKit`, including the durable offline outbox and TLS TOFU trust store; no app-surface implementation is implied. |
| P2 | Deliver connection and first run; demonstrate a new user connecting in under 60 seconds without typing a URL. |
| P3 | Deliver chat and approvals; derive attachment capability from pinned WebSocket source before implementation; arm the streaming-surface gates at their condition. |
| P4 | Deliver notifications, background behavior, and system integrations only after the outbound-hook facts and push ADR are recorded. |
| P5 | Deliver parity surfaces and deliberate iPad layouts. |
| P6 | Deliver watchOS, including WatchConnectivity and an independent networking fallback. |
| P7 | Perform signing, distribution, and the adoption package only with explicit owner authorization; signing and distribution additionally require provisioned credentials. |

The P4 pre-code source gate is satisfied for the Hermes commit pinned by `protocol/methods.json`.
Stock `hooks.outbound` can subscribe to `pre_approval_request`; `docs/PROTOCOL.md` records the
source chain, body, HMAC signing, and best-effort delivery semantics. Revalidate those facts on
every Hermes pin change. No Hermes fork or protocol patch is authorized.

[ADR-0001](adr/0001-contentless-approval-push.md) precedes and governs P4 implementation. Talaria
sends APNs device tokens directly to the APNs delivery relay, never through Hermes or the
gateway-side privacy bridge. The bridge receives the sensitive raw webhook inside the user's
trust boundary, verifies and minimizes it, then sends the hosted relay only the closed ADR schema.
APNs payloads contain only the required background member and an opaque session alias, Talaria
fetches details from the gateway over TLS, and the hosted relay protocol never receives command
text.

P4 code remains blocked until its operational threat model fixes rate limits, registration
expiry, abuse controls, credential rotation and recovery, bridge packaging, and deletion
verification, plus bound-session coverage and the alias-linkability decision. No upstream issue,
pull request, fork, or patch is required or authorized. Any future documentation or adoption
proposal belongs to hard-gated P7 and requires explicit owner authorization.

## Gate integrity

A gate is never weakened to make it pass. Missing prerequisites and indeterminate results are
`BLOCKED`, never silent skips or passes. A phase can close only after its required Tier A and
Tier B gates are green, with the run evidence linked from the pull request.

The automatic pull-request G1–G5 workflow is advisory Stage 1 feedback. It is not local Tier A,
Tier B, phase evidence, or a green sentinel, and it is not a required branch-protection context.
Those limits remain visible until ADR-0002's trusted Stage 2 attestor is implemented and proven.

### Checkpoints are not green sentinels

A checkpoint commit may be made while a gate is honestly red when that failure is the work being
advanced rather than something being worked around. The gate must remain red and unmasked, and
the checkpoint commit message must explicitly name the failing gate and its known failure. This
exception exists so repair work can be preserved while it converges; it does not certify the
checkpoint as passing.

`GAUNTLET GREEN` has a stricter and absolute meaning: every required, armed gate is genuinely
green. It may not be emitted when any armed gate is failing, blocked, deferred, skipped, relaxed,
quarantined, or allowed to continue after failure. There are no exceptions to the green sentinel.
An explicit owner/orchestrator applicability ruling of N/A is compatible with green because that
gate is not armed; it is never represented as passing. Every green sentinel report must enumerate
every N/A gate by name and reason so a reader cannot mistake applicability for successful
execution.

Phase activation is not a deferral. G11 is `N/A (no real streaming chat surface to capture)`;
G14 is `N/A (no G11 images to review before that arm point)`; and, by explicit orchestrator
ruling, G12 streaming is `N/A (no live stream or streaming UI to measure)`. None is passing,
waived, deferred, or skipped. Each gate **arms at the first real streaming chat surface (currently
P3)**, and ordinary work cannot return an armed gate to N/A. The condition is authoritative; the
phase number records only its current plan location. The phase containing that
surface cannot close until all three are armed and genuinely green.

`Tests/TalariaUITests/ScreenshotMatrixUITests.swift` was removed with the pre-product scaffold.
Rebuilding that test and its light/dark, device-size, and Dynamic Type artifact matrix is an
explicit prerequisite at that arm point; G11 cannot be merely re-enabled because no dormant test
remains. A synthetic streaming harness is prohibited before a real streaming chat surface exists:
measuring codec work without a real streaming UI would not test G12's main-thread responsiveness
contract.

## Capture and publication safety

Talaria is a public repository. A value that enters a tracked fixture, commit, pull request,
issue, or other public artifact must therefore be safe for permanent disclosure; later deletion
does not undo publication.

### Golden-capture boundary

Golden fixtures come only from the source-bound capture harness. It requires a clean checkout at
the repository and commit pinned by `protocol/methods.json`, verifies the pinned source bytes and
locked runtime, starts only its own isolated gateway process, and proves ownership before driving
it. Ambient configuration, credentials, home state, caches, and working directories are not
forwarded. Existing gateway processes are observed only as a count and are never attached to,
signalled, or reaped.

The current capture contract is exactly three real frames: the gateway-ready event, one ping
request, and its response. Raw frames remain in memory only. Gateway output exists only inside a
private temporary directory and is removed on every handled exit. Neither may be copied into the
repository, terminal evidence, an issue, or a pull request.

The sanitizer is recursive and default-deny. It removes captured object names, replaces opaque
text, normalizes scalar values, deterministically aliases protocol identifiers, and preserves
only reviewed JSON-RPC control strings from the pinned catalog. Only its canonical JSONL output
may cross the capture boundary. The fixture is written atomically after validation. G3 repeats
the residual safety check so a manually placed or edited unsanitized fixture fails even when its
JSON is otherwise valid and canonical.

### Public-artifact redaction

All public references to repository content use repository-relative paths and, where useful,
line numbers. Never publish absolute paths or home-directory structure. Also never publish:

- local account, host, or machine names;
- network addresses or endpoint numbers;
- credential values, or infrastructure-specific credential names;
- names or operational details of unrelated projects, products, or clients;
- local session, request, correlation, job, transcript, or other runtime identifiers;
- raw snapshots, job logs, gateway output, captured free text, or tool payloads; or
- captured model or provider selections.

Public upstream URLs and commit SHAs, reviewed repository source excerpts, tool and dependency
versions, and repository-relative findings are publishable when they contain none of the
prohibited material above. A finding that contains protected detail is published with that
detail redacted and the redaction noted; the finding is not silently omitted. If redaction would
make it meaningless, stop and request direction. Approval to update one public artifact applies
only to that artifact and does not authorize a new issue, repository, or disclosure surface.

Capture and gate diagnostics follow the same rule. They emit bounded reason codes or reviewed
status markers, never input-derived values. Local raw evidence is used only for verification and
is not quoted into public audit prose.

## Local Tier A launcher and native-toolchain deviation

Local Tier A is supported only through PowerShell's `scripts/gauntlet.ps1` launcher. That launcher
enters the Ubuntu distribution with `wsl.exe`, translates the repository path without building
a shell command string, and marks the expected launcher namespace. Direct execution of
`scripts/gauntlet.sh`, entry through an Ubuntu shortcut, and entry through Git Bash are blocked
because launcher-specific WSL namespaces can disagree about installed tools and live services.
The only direct-Bash mode is `--github-pr` inside the SHA-pinned reusable workflow on a validated
GitHub-hosted runner; setting WSL or caller-chosen marker variables cannot select that mode.

The original brief specified a Linux container. This machine cannot provide a usable Docker
daemon, so Tier A instead runs the Swift toolchain natively in WSL. The deviation preserves the
fast Linux feedback loop and exact version checks, but loses container hermeticity: Ubuntu
packages, glibc, environment state, and caches can drift. Tier B on `macos-26` with Xcode 26.6
remains authoritative.

The required native compiler is Swift 6.3.3. SwiftFormat 0.62.1 and SwiftLint 0.65.0 match the
versions supplied to Tier B; their Linux release assets are pinned and SHA-256 verified before
execution. Tool version output and the non-sensitive launcher entry marker are retained in
`.gauntlet/` logs; runtime namespace identifiers and local filesystem paths are not evidence.

Swift 6.3.3 requires an explicit G2 coverage sequence. `swift test list` ignores its coverage
flag, while asking `swift test --skip-build` to enable coverage exits unsuccessfully without a
profile. Tier A therefore builds the tests once with `swift build --build-tests
--enable-code-coverage`, inventories that exact binary through SwiftPM and XCTest, and then runs
`swift test --skip-build` without the broken coverage flag while `LLVM_PROFILE_FILE` captures the
already-instrumented binary. Its SHA-256 is required to remain unchanged across discovery and
execution; the pinned `llvm-profdata` and `llvm-cov` from Swift 6.3.3 merge and report the profile.
This preserves the required `swift test` execution and 85% source-line floor without relying on
SwiftPM's faulty post-run coverage path.

## Pull-request verification architecture

[ADR-0002](adr/0002-source-bound-pull-request-gauntlet.md) governs two stages. Stage 1 runs the
reusable Linux G1–G5 core on every pull request into `main`. The caller pins that core by full
commit SHA; the core uses `ubuntu-24.04`, read-only contents permission, no secrets, no cache,
`fetch-depth: 0`, and `persist-credentials: false`. Before candidate code executes, it proves the
GitHub-hosted Linux x64 environment, Ubuntu version, nominated merge SHA, complete non-shallow
history, and exact Swift toolchain. The gauntlet rechecks those facts and can report only
`G1–G5 GREEN` for the exact five-gate PASS inventory. It never calls G6 or Tier B and never emits a
Tier A or full-gauntlet sentinel.

Stage 1 remains advisory because candidate code can modify the gauntlet evaluator. Branch
protection therefore has no required automated check yet. Stage 2 must provide trusted evaluator
provenance, a full-SHA-pinned macOS core, and a default-branch `workflow_run` attestor before any
aggregate becomes required. It arms when P1 lands, before later work, or immediately when the
first outside contribution appears. The attestor must distinguish typed FAIL from BLOCKED despite
first-failure job exits; missing or skipped evidence is BLOCKED, and BLOCKED must never map to a
branch-protection-satisfying state. G12 cold launch remains advisory after Stage 2 until
representative burn-in proves its retry contract stable.

## Toolchain parity and pins

Tier A and Tier B must prove the following exact versions before using a tool. A cached binary is
not trusted merely because it exists: its version is checked, and downloaded release assets are
checked against their published SHA-256 digest.

| Tool | Required version | Tier parity contract |
| --- | --- | --- |
| Swift | 6.3.3 | Native WSL and GitHub-hosted Ubuntu 24.04 prove Swift 6.3.3 exactly; Tier B receives it with Xcode 26.6. |
| SwiftFormat | 0.62.1 | The Linux release asset is verified for Tier A; `macos-26` supplies the same version. |
| SwiftLint | 0.65.0 | Digest-verified release assets provide the same version to Tier A and Tier B. |
| gitleaks | 8.30.1 | The release asset is explicitly installed, digest-verified, and version-checked. |
| XcodeGen | 2.46.0 | The release asset is explicitly installed, digest-verified, and version-checked. |
| GitHub CLI (run evidence) | 2.45.0 | Native WSL handles dispatch, run selection, watching, and the exact run-snapshot schema. |
| GitHub CLI (job logs) | 2.88.1 | The PowerShell launcher selects and version-checks the Windows CLI used only for per-job log retrieval. |

The macOS runner inventory supplies SwiftFormat at the required version. SwiftLint, gitleaks, and
XcodeGen are not assumed from runner state; the gauntlet installs and verifies their pinned
artifacts explicitly. Tier B remains authoritative for Apple-platform compilation and tests.

Linux G4 invokes the digest-verified SwiftFormat 0.62.1 binary in verbose lint mode. During the
Phase 0 closure PR, the same source first passed and then reproduced exit 139 in both permitted
quiet-mode attempts on GitHub-hosted Linux, while local Linux and authoritative macOS G4 stayed
green. This matches the failure shape of the open static-Linux-binary report
`swiftlang/swift#77841`. Verbose mode changes only captured diagnostics: the version, digest,
configuration, source inputs, formatting rules, and fail-closed exit classification remain the
same, and the verbose evidence remains private in `.gauntlet/`.

XcodeGen does not publish a verified Linux executable for the pinned release. Local G6 therefore
always reports `BLOCKED` instead of trusting an arbitrary executable found on `PATH` by its
self-reported version. An authoritative G6 result comes only from the digest-verified official
XcodeGen 2.46.0 macOS asset in a source-bound Tier B run. That run generates twice into isolated
temporary directories and compares recursive output hashes; the generated project remains
untracked.

## Tier B result evidence

The current Tier B path remains the manual dispatch and correlation-token design below. ADR-0002's
`workflow_run` attestor is accepted Stage 2 architecture, not an implemented alternative.

Tier B uses two explicitly versioned GitHub CLI clients because their observable interfaces differ.
Native WSL 2.45.0 performs dispatch, run selection, watching, and snapshot capture; the snapshot
validator requires that client's exact four-field job-step schema. Its per-job log endpoint can
return exit 0 with completely empty output, which is the project's explicit `BLOCKED` signature,
so it is never used to fetch job logs. For that operation alone, the PowerShell launcher resolves
Windows 2.88.1 without a hardcoded local path, verifies its exact version, translates its path for
WSL through path-aware `WSLENV`, and passes it to the Bash gate. Bash separately resolves and
verifies native 2.45.0, then revalidates the interop path and version of 2.88.1. Neither client is
a fallback for the other; missing, stderr-bearing, or empty CLI evidence remains `BLOCKED`.

Before entering WSL, the PowerShell launcher runs an executable transport self-test. It verifies
that process arguments remain distinct across space-bearing script paths and environment values,
and adversarially rejects wrong-version, stderr-bearing, empty, ambiguous, and non-interop
results. The launcher blocks before any gate or dispatch if this bridge check fails.

GitHub Actions reports every nonzero step exit as the same workflow conclusion, `failure`; that
summary cannot distinguish a deterministic gate failure from missing or indeterminate evidence.
Each of the three Tier B jobs therefore emits exactly one final status record bound to the
dispatch correlation token. At this repair checkpoint the workflow can legitimately emit only
`PASS` or `BLOCKED`; `FAIL` is unreachable and is rejected as malformed evidence. A job whose
earlier step failed emits `BLOCKED`, because its conclusion alone does not prove whether the
underlying process returned a findings status or an operational status.

The local gauntlet pins observation to attempt 1 of the unique source- and title-matched run. It
validates a fresh JSON snapshot containing the run ID, commit, title, canonical repository/run
URL, exact three-job inventory, canonical run/job URLs, job conclusions, and the successful final
reporter step. Only the validated job IDs are used with GitHub's per-job log endpoint; the CLI's
positional run argument is deliberately omitted there because it is not enforced when `--job` is
present. Each downloaded log must contain exactly its own correlation-bound record, so combined
logs and swapped job origins cannot certify the run.

After the three logs produce a decisive aggregate, the complete attempt-1 snapshot is fetched and
validated again and must yield the identical normalized job inventory. Any missing, duplicate,
malformed, mismatched, drifting, stderr-bearing, or contradictory evidence is `BLOCKED`. A run can
pass only when all three records say `PASS`, all three job conclusions are `success`, and the
workflow conclusion agrees. Raw snapshots, job IDs, job logs, and correlation tokens are local
evidence; they are never copied into public audit text.
