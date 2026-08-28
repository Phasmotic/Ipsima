# Talaria project brief

## Authority and record

Talaria is maintained in the public `markschonfeld/Talaria` repository. Preserve that exact
capitalization in remotes, workflows, and documentation. The public audit ledger is issue #2.
Pull request #1 preserved and reviewed the recovered Phase 0 handoff, audit repair, and README
correction; it merged into `main` at `5a48deb`.

This file is the in-repository operating brief. Explicit owner or orchestrator decisions may
revise it. Expensive-to-reconstruct findings and evidence belong in the repository, pull request,
or issue rather than only in a conversation. Public evidence must contain repository-relative
paths only and must exclude local environment details and sensitive data.

Audit repair closed at `96df2d0`. The revised phase plan, pinned-source outbound-hook finding,
and pre-code push architecture are now recorded in this brief, `docs/PROTOCOL.md`, and
[ADR-0001](adr/0001-contentless-approval-push.md). P1 implementation has not begun and is outside
this run.

[ADR-0002](adr/0002-source-bound-pull-request-gauntlet.md) records the accepted two-stage PR
verification architecture. Only advisory Stage 1 Linux G1–G5 is implemented now. Stage 2 and P1
remain outside this run.

### Coordination record

GitHub is the durable project record. Expensive-to-reconstruct findings and any decision that
requires owner or orchestrator action must be persisted in the authorized issue or pull request
before work moves past them. Chat may point to that record but must not become its only copy.

On public repository surfaces—issue comments, pull request bodies, and pull request comments—
begin every automated contribution exactly with `Codex (automated contributor) —` so automated
authorship is unambiguous without disclosing an account relationship. Use these coordination
states exactly when they apply:

- `ACK (coordination)` — instructions received; coordination or a decision is next.
- `ACK (building)` — authorized implementation is in progress.
- `NEEDS DECISION: <the decision>` — an owner/orchestrator ruling is required before proceeding.
- `HANDOFF READY: <branch/SHA, tests, unverified>` — the work is safely checkpointed for review.

## Product and scope

Talaria is a native SwiftUI iPhone client for Hermes Agent, accompanied by an Apple Watch app and
WidgetKit extensions. Its goal is meaningful mobile parity with the Hermes desktop experience:
streaming chat, live tool activity, approvals, interruption and steering, session management, and
the mobile-native surfaces described below. It speaks Hermes' real gateway protocol; it is not an
SSH terminal wrapper or a thin chat-only client.

The product target is a Hermes mobile client with the onboarding, rendering, resilience, and
system integration quality expected of leading first-party agent clients. A credible adoption
path by Nous Research is a design goal. Talaria therefore remains MIT-licensed: the protocol
catalog stays mechanically derivable from pinned Hermes source, and the project must remain
straightforward to incorporate into an MIT upstream and distribute through the App Store.

The revised phases are:

| Phase | Deliverable |
| --- | --- |
| P0 | Repository scaffold, pinned protocol derivation, capture fixture, and trustworthy gates. |
| P1 | `HermesKit`: protocol and transport foundations, including a durable offline outbox and TLS TOFU trust store. |
| P2 | Connection and first run: fast pairing, discovery, authentication, trust acceptance, secure storage, and multi-gateway profiles. |
| P3 | Chat and approvals: streaming sessions, safe approvals, steering, high-quality rendering, attachments, and localization foundations. |
| P4 | Notifications, background execution, and system integration, based on a source-derived outbound-hook contract and a pre-code push ADR. |
| P5 | Parity surfaces and iPad: artifacts, files, terminal, scheduled work, skills and MCP, configuration, branching, subagents, and voice. |
| P6 | watchOS app, complications, relevance, and phone-independent networking fallback. |
| P7 | Signing, distribution, and the upstream-adoption package. Hard-gated; see below. |

### P1 — HermesKit

P1 retains the codec, WebSocket and REST/SSE transports, domain models, reconnect, reattach, and
best-effort gap recovery, approval state machine, and `MockGateway`. It also owns two mobile
transport requirements:

- A durable offline outbox queues user messages composed without connectivity and sends them on
  reconnect. Best-effort gap recovery is separate from preserving a dropped user intent and
  follows P1.4 below.
- A TLS trust store implements trust on first use for self-signed homelab gateways: pin the
  certificate, display its fingerprint, and require explicit acceptance. Plain HTTP is not an
  escape hatch.

#### P1.2 — Transport

P1.2 implements the primary WebSocket transport, including native-provider ticket acquisition
and the `gateway.ready` handshake, behind the raw `HermesTransport` boundary. The REST/SSE
fallback follows as the next P1 objective, before event routing and domain models. Its route,
authentication, listener, readiness, and framing semantics differ from WebSocket; it must gain an
honest capability or operation seam rather than fabricating JSON-RPC envelopes to look identical.

#### P1.4 — Reconnect, reattach, and best-effort gap recovery

These design rules are binding:

- Transcript reconciliation through `session.history`, or the corresponding
  `session.resume`/`session.activate` reconciliation path, is authoritative.
- The replay ring is only an optimisation for transient gaps on a corroborated, reused live
  session ID. It is never a source of truth.
- Any ambiguity forces full transcript reconciliation. In particular, `latest_seq: 0` with
  `truncated: false` is **UNKNOWN**, never proof of “no events”: an absent or evicted ring and a
  session with no sequenced events are indistinguishable.
- Sequence numbers alone do not establish continuity. `seq: 1` can recur after process restart
  or replay-ring eviction; corroborate continuity against authoritative session state before
  trusting replay.

### P2 — Connection and first run

P2 makes connection a product surface rather than a chat prerequisite. It includes QR pairing,
mDNS/Bonjour discovery, Nous Portal OAuth for Hermes Cloud, manual entry as fallback rather than
default, TOFU fingerprint review and acceptance, Keychain storage, a biometric gate on the
connection list, an explanatory path for a first-time user with no gateway, and a multi-gateway
registry with per-gateway profile rosters.

P2 acceptance is demonstrable: a new user connects in under 60 seconds without typing a URL. If
that cannot be shown, P2 is not complete.

### P3 — Chat and approvals

P3 includes the session list, streaming chat, collapsible tool cards, interruption and steering,
model selection, context and token meters, a YOLO toggle, and genuinely high-quality Markdown,
syntax-highlighted code, and diff rendering. Approval prompts are biometric-gated, show the full
untruncated command, default safely to deny, and expose their timeout.

Attachment support must be derived from the pinned WebSocket source before implementation; the
REST surface's inline-image limitation must not be assumed to apply to WebSocket. Localization
scaffolding begins here, with no hardcoded user-facing strings.

Each of G11, G14, and G12 streaming **arms at the first real streaming chat surface (currently
P3)**. The condition is authoritative; the phase number is only the current plan location.

### P4 — Notifications, background, and system integration

Pinned Hermes source establishes that stock `hooks.outbound` can subscribe to
`pre_approval_request`; no Hermes fork or protocol patch is needed. `docs/PROTOCOL.md` records the
exact body, HMAC signing, and best-effort delivery contract. Because the raw webhook contains the
command and sender working directory, the user points it at a user-controlled gateway-side
privacy bridge. The app registers its APNs token directly with the separate APNs delivery relay;
Hermes and the privacy bridge never handle that device token.

[ADR-0001](adr/0001-contentless-approval-push.md) fixes the trust boundary, closed relay schema,
and contentless APNs contract. The bridge verifies and minimizes the webhook before the hosted
relay trust boundary. APNs payloads contain only the required background member and an opaque
session alias; the app wakes and fetches details from the user's gateway over TLS.
The APNs relay stays outside Hermes because it holds the signing key bound to Talaria's bundle
identifier, and its protocol never receives command text. P4 implementation remains blocked until
its operational threat model fixes rate limits, registration expiry, abuse controls, credential
rotation and recovery, bridge packaging, deletion verification, bound-session coverage, and the
alias-linkability decision. No upstream issue, pull request, fork, or patch is required or
authorized; any later documentation or adoption proposal belongs to hard-gated P7 and requires
explicit owner authorization.

P4 also includes Live Activity and Dynamic Island status for an in-flight run, approval
notifications, background refresh and foreground reconciliation, home and lock-screen widgets,
App Intents, Shortcuts, Action Button quick prompt, and the Share extension.

### P5 — Parity surfaces and iPad

P5 includes the artifacts gallery, file browser, `/api/pty` terminal, cron jobs, skills and MCP
toggles, configuration editor, session search/fork/branch, subagent monitor, and voice. It adds
deliberate iPad layouts for the terminal, files, artifacts, and diff-heavy surfaces.

### P6 — watchOS

P6 includes the watch app for approve/deny, session status, and dictated prompts; WidgetKit
accessory widgets; Smart Stack relevance; WatchConnectivity; and an independent networking
fallback.

### P7 — Distribution and adoption

P7 remains hard-gated. In addition to signing and distribution, it includes a screenshot-led
README, `CONTRIBUTING.md`, an explicit welcome for upstream adoption, and a prominent explanation
that the protocol catalog tracks a pinned Hermes commit and can be re-derived mechanically.

Desktop-only worktree management, theme installation, HUD behavior, and bot-mode group rooms are
deliberately outside product scope.

## Fixed architecture

1. `Packages/HermesKit` is pure Swift and imports no Apple-only framework. Codec, transport,
   protocol models, reconnect/replay handling, and state machines belong there. It must build and
   test on Linux; SwiftUI and platform APIs stay in the thin app targets.
2. `project.yml` is the only source of truth for the Xcode project. XcodeGen output is generated,
   never hand-edited, and never tracked.
3. `MockGateway` is a first-class deliverable. Protocol behavior must be testable by replaying
   sanitized canonical JSON-RPC frames without a live gateway.
4. The primary transport is WebSocket JSON-RPC. REST with SSE is the fallback behind one
   `HermesTransport` abstraction. Gateway-only features must not be pretended onto the fallback.

The deployment targets are iOS 26.0 and watchOS 26.0. Tier B runs on `macos-26` with Xcode 26.6
and Swift 6.3.3. The generated project contains the iOS app, iOS widget extension, watch app,
watch widget extension, `HermesKit`, and their test bundles.

The watch app can run independently at runtime, but it is still distributed inside the iOS app
bundle. `TalariaWatch` therefore remains an embedded dependency of `Talaria`, and G13 must prove
that the unsigned archive contains the watch app and both widget extensions.

## Sources of truth

Use the following precedence rather than resolving conflicts by convenience:

1. Explicit current owner or orchestrator decisions revise earlier project instructions.
2. For Talaria behavior, repository source wins over narrative documentation unless the audit has
   identified that source as defective.
3. For Hermes protocol facts, the pinned upstream Git objects win. The derived
   `protocol/methods.json` catalog is the machine-readable projection of those objects; generators,
   generated tests, and fixtures follow it; prose documentation comes last.
4. For Xcode structure, `project.yml` wins over every generated project artifact.
5. For gate status, validated gate evidence linked from the pull request wins over prose claims.

Documentation intentionally lags source. When pinned Hermes source and this document disagree,
correct the catalog and tests from source first, then record the delta in `docs/PROTOCOL.md`.

## Quality gates

One gauntlet invocation evaluates all gates in its tier. Missing prerequisites, empty evidence,
and indeterminate results are `BLOCKED`, never passes or silent skips. See
`docs/GOVERNANCE.md` for exact tool pins and evidence rules.

### Local Tier A — fast Linux feedback

| Gate | Required evidence |
| --- | --- |
| G1 | `HermesKit` builds in debug and release with Swift 6.3.3, warnings as errors, and no warnings. |
| G2 | The Swift test suite passes and source-line coverage is at least 85% for codec, protocol, and state-machine code. |
| G3 | The pinned protocol catalog, generated request/event tests, and sanitized golden fixture are complete, deterministic, canonical, and round-trip stable. |
| G4 | SwiftFormat 0.62.1 and SwiftLint 0.65.0 report no violations. |
| G5 | Secret and source-hygiene scans report no findings and their canaries prove the scanners can fail. |
| G6 | XcodeGen 2.46.0 generates twice in isolated temporary directories with identical recursive output hashes. The generated project stays untracked. |

Local Tier A runs the Swift toolchain natively in Ubuntu entered only through PowerShell's
`scripts/gauntlet.ps1`. The original container requirement was a means of version pinning, not the
quality property itself. Native execution preserves exact version checks and the fast Linux loop,
but loses container hermeticity: system libraries, packages, caches, and environment state can
drift. Tier B remains authoritative.

No verified Linux executable is published for the pinned XcodeGen release. Local G6 therefore
fails closed as `BLOCKED`; its authoritative result comes from the digest-verified macOS asset in
Tier B. G6 tests the observable property—two identical recursive output hashes—not a Git diff
against a tracked generated project.

### Advisory pull-request Linux subset

Every pull request into `main` invokes a full-SHA-pinned reusable core on `ubuntu-24.04`. It proves
the GitHub-hosted Linux x64 runner, nominated merge SHA, non-shallow history, no persisted checkout
credential, and the exact Swift 6.3.3 toolchain before running G1–G5. No Swift, build-output,
coverage, profile, or test-binary cache is used. Its only green wording is `G1–G5 GREEN`.

This subset is advisory Stage 1 feedback. It is not Tier A, does not evaluate G6, cannot replace
the correlated Tier B run, cannot close a phase, and is not a required status check. The complete
two-stage design and its P1-or-outside-contribution arm point are in ADR-0002. Stage 2 must establish
trusted evaluator provenance and PASS/FAIL/BLOCKED attestation before checks become required;
BLOCKED can never be represented by a branch-protection-satisfying status. G12 cold launch remains
advisory after that transition until representative burn-in establishes stability.

### Tier B — authoritative Apple-platform CI

| Gate | Required evidence |
| --- | --- |
| G7 | iOS simulator build succeeds with zero warnings. |
| G8 | Unit and UI tests pass. |
| G9 | watchOS app, watch widget, and watch UI smoke build and pass with zero warnings. |
| G10 | Accessibility audits of every primary screen have no critical finding. |
| G11 | The required light/dark, device-size, and Dynamic Type screenshot matrix is uploaded. |
| G12 | Cold launch and live-stream responsiveness stay within the declared budgets. |
| G13 | An unsigned archive succeeds and proves the iOS widget, embedded watch app, and watch widget are present. |

G12 cold launch is armed during audit repair. Its first closure measurement was 3.062 seconds
against the strict `<3.0s` budget; the single permitted retry passed at 2.863 seconds over five
samples. The threshold and measurement contract remain unchanged.

By explicit orchestrator ruling, **G12 streaming is
`N/A (no live stream or streaming UI to measure)`**. The clause has no subject before a real
streaming chat surface exists. N/A is not PASS, a waiver, a deferral, or permission to build a
synthetic codec benchmark and describe it as UI responsiveness. Each of G12 streaming, G11, and
G14 **arms at the first real streaming chat surface (currently P3)**. The condition is authoritative;
the phase number records only its present plan location. The phase containing that surface cannot
close until all three gates are armed and genuinely green.

### Tier C — judgement

| Gate | Required evidence |
| --- | --- |
| G14 | Every G11 image receives a written visual-quality verdict. |
| G15 | The owner accepts the visual result. |

G11 is `N/A (no real streaming chat surface to capture)`, and G14 is
`N/A (no G11 images to review before that arm point)`. Each of G11, G14, and G12 streaming
**arms at the first real streaming chat surface (currently P3)**. The earlier scaffold removed
`Tests/TalariaUITests/ScreenshotMatrixUITests.swift`; rebuilding that test and its required
light/dark, device-size, and Dynamic Type matrix is an explicit prerequisite at that arm point,
not a matter of merely re-enabling a dormant test. The phase containing that surface cannot close
until G11, G14, and G12 streaming are all armed and green. Phase activation is not a deferral,
and an armed gate cannot later be called N/A to close a phase.

## The convergence loop

Every repair or phase objective uses one loop pass. A phase has a hard maximum of 12 iterations:

1. State one next objective.
2. Make only that change.
3. Run Tier A once.
4. If red, fix the work—not the gate—and return to step 3.
5. Preserve the checkpoint and push it. Normally this follows Tier A green; an honestly red
   checkpoint is allowed only under the named-failure rule in `docs/GOVERNANCE.md`.
6. Dispatch and follow Tier B.
7. If red, inspect the bound failing evidence, fix, and return to step 3.
8. At the first real streaming chat surface (currently P3), rebuild
   `Tests/TalariaUITests/ScreenshotMatrixUITests.swift`, arm G11/G14/G12-streaming, and obtain the
   G11 screenshot artifact.
9. Inspect every image and record the G14 verdict.
10. If visual review fails, repair the UI and return to step 3.
11. If every armed gate passes, update the pull request evidence table and stop.

The advisory pull-request G1–G5 result does not satisfy step 3 or step 6. The local and correlated
authoritative paths remain required until ADR-0002 Stage 2 is implemented and proven.

The audit itself and the deliberate proof that gates can fail are outside this convergence loop.
During the inverted gate proof, the intended red result is the evidence; the deliberate defect is
then reverted.

A checkpoint commit may preserve a named, honestly failing gate while repair progresses. It does
not certify green. A `GAUNTLET GREEN` sentinel is stricter: every required, armed gate must be
genuinely green, with nothing skipped, relaxed, deferred, quarantined, or allowed to continue on
error. An explicit orchestrator ruling of N/A is compatible with green because the gate is not
armed; the sentinel report must still list every N/A gate by name and reason so N/A cannot be
misread as PASS.

## Stop conditions and hard rules

- At 12 iterations in one phase, stop and report; do not start a thirteenth.
- When a required prerequisite or evidence source is unavailable or indeterminate, report
  `GAUNTLET BLOCKED — <reason>` and stop rather than substituting evidence.
- If a gate appears incorrect, report the proposed correction before changing it. Never lower a
  threshold, remove an assertion, skip a test, or mask a failure to obtain green.
- If an audit finds structural compromise, stop. Destructive history changes or subsystem
  rewrites require a separate decision.
- Run one phase at a time. A phase sentinel ends the run; it does not authorize starting the next
  phase.
- Keep zero warnings. Justify every new third-party dependency in the pull request.
- Retry a suspected flaky test exactly once. If it fails twice, preserve it, quarantine and
  report it in the pull request with the best available diagnosis; never delete it. A quarantine
  is not green evidence and cannot support a phase sentinel.
- Stay inside this repository and act only on processes and artifacts whose ownership is proven.
- Every pull request carries an evidence table for every gate, including explicit N/A entries for
  gates that are not yet armed.

The audit repair phase closes only with this exact sentinel on its own line:

`GAUNTLET GREEN — AUDIT REPAIR`

## Phase 0 continuation after audit repair

The audit-repair sentinel ends its run. In a later authorized run, complete the remaining Phase 0
work in this order; do not combine these steps with audit repair:

1. Prove the gauntlet can fail, outside the convergence loop. Commit a uniquely identifiable
   compiler warning and deliberately failing tests, observe Tier A and Tier B turn red for those
   exact reasons, preserve the evidence links, then revert the deliberate defects. Red is the
   passing result of this inverted proof; it is not permission to weaken a gate.
2. Regenerate `.gauntlet/` from scratch and run a clean full Tier A followed by Tier B.
3. Update draft pull request #1 with an evidence table for every gate. Mark G11
   `N/A (no real streaming chat surface to capture)`, G14
   `N/A (no G11 images to review before that arm point)`, and G12 streaming
   `N/A (no live stream or streaming UI to measure)` rather than PASS. State that each gate
   arms at the first real streaming chat surface (currently P3). Retain the sanitized historical
   disclosures about the first-session profile-scoping discrepancy and the earlier
   orphaned-backend reap.
4. Only after every armed Phase 0 gate is genuinely green, emit
   `GAUNTLET GREEN — PHASE 0` on its own line and stop. Do not begin P1 in that run.

P7 is hard-gated. Do not begin any P7 work—including signing, account configuration,
distribution, adoption artifacts, or upstream proposals—until the owner explicitly authorizes P7.
Distribution work additionally requires the necessary credential to be provisioned through
repository secrets. No earlier phase or general build authorization implies either permission.
