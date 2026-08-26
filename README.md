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
scripts/gauntlet.sh    Tier A gates (Linux/Docker) in one invocation; --tier-b dispatches CI
```

## The gauntlet

```bash
./scripts/gauntlet.sh              # Tier A: build, tests, conformance, lint, secrets, determinism
./scripts/gauntlet.sh --tier-b     # dispatch + follow the macOS CI workflow
```

Tier A runs locally in pinned containers (`swift:6.3-noble`, matching the Xcode 26.6 toolchain
that `macos-26` CI ships). Every gate must be green before push; Tier B before any PR merge.
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
