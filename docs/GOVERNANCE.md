# Talaria governance

## Gate integrity

A gate is never weakened to make it pass. Missing prerequisites and indeterminate results are
`BLOCKED`, never silent skips or passes. A phase can close only after its required Tier A and
Tier B gates are green, with the run evidence linked from the pull request.

### Checkpoints are not green sentinels

A checkpoint commit may be made while a gate is honestly red when that failure is the work being
advanced rather than something being worked around. The gate must remain red and unmasked, and
the checkpoint commit message must explicitly name the failing gate and its known failure. This
exception exists so repair work can be preserved while it converges; it does not certify the
checkpoint as passing.

`GAUNTLET GREEN` has a stricter and absolute meaning: every required, armed gate is genuinely
green. It may not be emitted when any armed gate is failing, blocked, deferred, skipped, relaxed,
quarantined, or allowed to continue after failure. There are no exceptions to the green sentinel.

Phase activation is not a deferral. The binding brief explicitly leaves G11 and G14 unarmed
through P1 because no product UI surface exists. They are recorded as `N/A (no UI surface yet)`,
never as passing, and they arm in P2. Once a gate is armed, ordinary work cannot return it to N/A.

## Tier A launcher and native-toolchain deviation

Tier A is supported only through PowerShell's `scripts/gauntlet.ps1` launcher. That launcher
enters the Ubuntu distribution with `wsl.exe`, translates the repository path without building
a shell command string, and marks the expected launcher namespace. Direct execution of
`scripts/gauntlet.sh`, entry through an Ubuntu shortcut, and entry through Git Bash are blocked
because launcher-specific WSL namespaces can disagree about installed tools and live services.

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

## Toolchain parity and pins

Tier A and Tier B must prove the following exact versions before using a tool. A cached binary is
not trusted merely because it exists: its version is checked, and downloaded release assets are
checked against their published SHA-256 digest.

| Tool | Required version | Tier parity contract |
| --- | --- | --- |
| Swift | 6.3.3 | Native WSL uses Swift 6.3.3; Tier B receives Swift 6.3.3 with Xcode 26.6. |
| SwiftFormat | 0.62.1 | The Linux release asset is verified for Tier A; `macos-26` supplies the same version. |
| SwiftLint | 0.65.0 | The Linux release asset is verified for Tier A; `macos-26` supplies the same version. |
| gitleaks | 8.30.1 | The release asset is explicitly installed, digest-verified, and version-checked. |
| XcodeGen | 2.46.0 | The release asset is explicitly installed, digest-verified, and version-checked. |

The macOS runner inventory supplies SwiftFormat and SwiftLint at the required versions. Gitleaks
and XcodeGen are not assumed from runner state; the gauntlet installs and verifies their pinned
artifacts explicitly. Tier B remains authoritative for Apple-platform compilation and tests.

XcodeGen does not publish a verified Linux executable for the pinned release. Local G6 therefore
always reports `BLOCKED` instead of trusting an arbitrary executable found on `PATH` by its
self-reported version. An authoritative G6 result comes only from the digest-verified official
XcodeGen 2.46.0 macOS asset in a source-bound Tier B run. That run generates twice into isolated
temporary directories and compares recursive output hashes; the generated project remains
untracked.

## Tier B result evidence

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
