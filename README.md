# Talaria

A native **iOS + watchOS client for [Hermes Agent](https://github.com/NousResearch/hermes-agent)** —
streaming chat, live tool activity, remote approvals, session management. Speaks the real
`tui_gateway` WebSocket JSON-RPC protocol, which no existing third-party client does
(NousResearch/hermes-agent #35966 tracks the gap).

## Status

Phase 0 — scaffold, protocol derivation, gauntlet wiring. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
for the protocol catalog derived from Hermes source, and `qa/` for gate verdicts.

## Layout

```
project.yml            XcodeGen spec — the ONLY source of truth for the Xcode project
Packages/HermesKit/    Pure-Swift SwiftPM package: codec, transport, models, mock gateway.
                       Zero Apple-framework imports; builds and tests on Linux.
App/                   Thin SwiftUI shells: Talaria (iOS), TalariaWidgets, TalariaWatch,
                       TalariaWatchWidgets
protocol/methods.json  Machine-readable method/event catalog (derived from Hermes source)
Tests/Fixtures/        Sanitized golden JSON-RPC frame recordings (conformance substrate)
scripts/gauntlet.ps1   PowerShell → WSL entry for Tier A; -TierB dispatches macOS CI
scripts/gauntlet.sh    Internal native-Linux gate runner invoked by gauntlet.ps1
```

## The gauntlet

```powershell
pwsh -File scripts/gauntlet.ps1             # Tier A: build, tests, conformance, lint, secrets
pwsh -File scripts/gauntlet.ps1 -TierB      # dispatch + follow the macOS CI workflow
```

Tier A runs native Swift 6.3.3 in PowerShell-launched WSL Ubuntu, matching the compiler in the
Xcode 26.6 toolchain on `macos-26`. The compiler, SwiftFormat, and SwiftLint versions are checked
exactly; release assets are hash-verified. The native environment is version-pinned but not
container-hermetic, so Tier B remains authoritative. The final green sentinel requires every
armed gate genuinely green; repair checkpoints may preserve explicitly named honest-red gates.
A gate is never weakened to pass — see `docs/GOVERNANCE.md`.

## Security posture

- Gateway tokens live in the iOS Keychain only (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`).
- Approvals (dangerous-command Allow/Deny) are biometric-gated; deny is the safe default;
  full command text shown untruncated.
- Plain HTTP to non-loopback hosts is refused (or gated behind a red, explicit override).
- Recommended remote access: [Tailscale](https://tailscale.com) to your home gateway.

## Deliberate omissions

Git worktree management, HUD mode, VS Code theme installation, bot-mode group rooms.
Out of scope by design; parity effort goes to surfaces a phone can meaningfully drive.

## License

MIT — see [LICENSE](LICENSE).
