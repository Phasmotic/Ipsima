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
