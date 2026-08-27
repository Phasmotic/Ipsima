# Talaria

A native **iOS + watchOS client for [Hermes Agent](https://github.com/NousResearch/hermes-agent)** —
streaming chat, live tool activity, remote approvals, session management. Speaks the real
`tui_gateway` WebSocket JSON-RPC protocol, which no existing third-party client does
(NousResearch/hermes-agent #35966 tracks the gap).

## Status

Phase 0 — scaffold, protocol derivation, gauntlet wiring. The project contract is recorded in
the [operating brief](docs/BRIEF.md), the source-derived wire contract is in the
[protocol guide](docs/PROTOCOL.md), and gate policy is in
[governance](docs/GOVERNANCE.md).

## Layout

```
project.yml            XcodeGen spec — the ONLY source of truth for the Xcode project
Packages/HermesKit/    Pure-Swift SwiftPM package: Phase-0 codec now; P1 transport, models,
                       state machines, and mock gateway will live here.
                       Zero Apple-only framework imports; builds and tests on Linux.
App/                   Thin SwiftUI shells: Talaria (iOS), TalariaWidgets, TalariaWatch,
                       TalariaWatchWidgets
protocol/methods.json  Machine-readable method/event catalog (derived from Hermes source)
Packages/HermesKit/Tests/HermesKitTests/Fixtures/
                       Sanitized golden JSON-RPC frame recordings (conformance substrate)
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
A gate is never weakened to pass — see [governance](docs/GOVERNANCE.md).

## Planned security requirements

- P2 will store gateway tokens only in the iOS Keychain with
  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`.
- Dangerous-command approvals will be biometric-gated, default to deny, and show the complete
  command without truncation.
- The transport layer will refuse plain HTTP to non-loopback hosts.
- Recommended remote access: [Tailscale](https://tailscale.com) to your home gateway.

These controls are requirements, not Phase 0 implementation claims.

## Deliberate omissions

Git worktree management, HUD mode, VS Code theme installation, bot-mode group rooms.
Out of scope by design; parity effort goes to surfaces a phone can meaningfully drive.

## License

MIT — see [LICENSE](LICENSE).
