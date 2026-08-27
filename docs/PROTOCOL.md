# Hermes protocol contract

## Authority and provenance

Hermes source wins over documentation. Talaria's current protocol catalog is derived from the
public `NousResearch/hermes-agent` repository at this immutable commit:

`e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246`

The compatible capture runtime reports Hermes version `0.20.5`. `scripts/derive_protocol.py`
reads Git objects at the pinned commit rather than mutable working-tree files. It records SHA-256
digests for all 33 source inputs, rejects a different upstream origin or unresolved commit, and
ratchets the catalog to the expected counts. The committed catalog's pinned SHA-256 is:

`c51404eb76d93a37f36155dc2df9688821aab8a9d694135d1407bfe7de96928b`

Current exact inventory:

| Surface | Count |
| --- | ---: |
| Registered JSON-RPC requests | 168 |
| Server event types | 56 |
| REST method/path routes | 42 |
| Pinned upstream source inputs | 33 |
| Generated kind-aware conformance tests | 224 |
| Sanitized frames in the golden fixture | 3 |

`protocol/methods.json` is an identity and provenance catalog, not a complete schema for every
payload. Do not infer an unrecorded payload shape from a method name. Consult the pinned source
and extend the catalog or a real sanitized fixture when stronger schema knowledge is required.

## Transport and framing

The primary transport is WebSocket JSON-RPC 2.0. Each WebSocket text message contains exactly
one JSON-RPC object. The stdio form is newline-delimited. REST with SSE is the fallback surface;
its 42 entries are identified by the method-and-path pair, not by path alone.

Client requests use the ordinary JSON-RPC request shape with an identifier, method, and optional
parameters. Server events do **not** use their event type as the JSON-RPC method. Every pushed
event uses this notification envelope, without an identifier:

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"<event-name>","payload":{}}}
```

Canonical fixture form is UTF-8 JSON with sorted object keys, no insignificant whitespace, and
one envelope per line. Stream delta events can be coalesced by the server, so clients must not
depend on one event per token or on exact inter-event timing.

There are two intentionally distinct liveness calls:

- `gateway.ping` is an inline transport heartbeat and returns `{"ok":true}`. It is not one of the
  168 registered request methods.
- `ping` is a registered request and returns `{"pong":true}`. The golden capture uses this method
  because it exercises normal request dispatch and response correlation.

## Authentication boundary

In gated deployments, the client first asks the authenticated dashboard to mint a single-use
ticket. The ticket expires after 30 seconds and is consumed when the WebSocket opens. The legacy
long-lived query-credential path is rejected in gated mode.

Local or explicitly insecure deployments have a separate dashboard-session compatibility path;
it must not be mistaken for gated authentication. Server-internal process authentication is for
server-spawned children only and is never a client mechanism. The REST fallback requires its own
bearer authorization. Client credentials remain outside source, logs, fixtures, documentation,
and public evidence.

## Request and event namespaces

Requests and events are separate namespaces even when their strings match. Two names currently
collide across kinds:

- `session.title`
- `session.usage`

Each collision therefore has two distinct generated tests: a request-shaped JSON-RPC round trip
and a real event-envelope round trip. Across the whole catalog, generation produces exactly 168
request tests plus 56 event tests, for 224 kind-aware tests. Collapsing the namespaces would hide
two cases and is a G3 failure.

## Lifecycle facts established from source

The tool-event lifecycle is:

`tool.start` → `tool.generating` → `tool.output_risk` → `tool.complete`

There is no `tool.progress` event in the pinned source.

The timeout-backed `_block` derivation contains exactly nine request/expire families:
`clarify`, `mcp.setup`, `preview.act`, `preview.read`, `secret`, `sudo`, `terminal.read`, `tour`,
and `window.read`. The catalog therefore has nine corresponding `.expire` event names.
`approval.request` is emitted separately and has no catalogued `approval.expire`. Consequently,
the complete event catalog contains ten names ending in `.request`, but only nine ending in
`.expire`; it does not contain ten request/expire pairs.

`gateway.ready` is the initial server notification captured by the real gateway harness.
Reconnect and replay code must respect the gateway's replay epoch rather than assuming that a
new connection continues an old event stream. Because the catalog inventories identities rather
than every payload member, the pinned source and sanitized captures remain authoritative for the
readiness payload.

## Catalog and conformance regeneration

Catalog regeneration requires a Git object database that contains the pinned Hermes commit and
has the canonical upstream origin. A safe identity check is:

```bash
python3 -B scripts/derive_protocol.py <clean-pinned-Hermes-checkout> --check
```

The derivation ignores the checked-out branch and mutable files. It reads the pinned objects,
validates the 33-input manifest, verifies the 168/56/42 count ratchet, and compares exact catalog
bytes. A new upstream revision is a contract change: update the source commit, reviewed input
set, expected counts, and pinned catalog digest together; regenerate; inspect the semantic diff;
then run G3. Never point the existing derivation at a moving branch and call the result equivalent.

The generated Swift suite is produced by:

```bash
python3 -B scripts/gen_conformance_tests.py
```

The six generated source files are committed, but they are not hand-edited. G3 generates them
twice into separate temporary directories, requires byte-identical inventories, compares them
with the committed files, and binds each generated body to the reviewed request or event helper.
G3 also validates every nonblank golden-fixture line as strict canonical JSON-RPC and requires a
decode/re-encode fixed point.

## Golden capture scope and safety

`scripts/capture_golden.py` records a deliberately narrow real transcript from an isolated Hermes
0.20.5 gateway at the pinned source commit. The tracked fixture is
`Packages/HermesKit/Tests/HermesKitTests/Fixtures/golden.jsonl` and contains exactly:

1. the inbound `gateway.ready` event;
2. one outbound registered `ping` request; and
3. its correlated successful response.

The harness deliberately does not submit a model-backed prompt. The fixture proves real
WebSocket framing, event wrapping, request dispatch, response correlation, and codec stability;
catalog-wide behavior is covered by the 224 generated tests. A deterministic three-frame capture
must not be described as a full chat, streaming, or approval transcript.

Capture is foreground-only and fail-closed. Before launch it verifies a clean pinned checkout,
every recorded source digest, the locked runtime, an offline frozen environment check with
`uv 0.11.7`, and that imported runtime modules resolve inside that checkout. It launches the exact
checkout-local Python in isolated mode, gives the gateway private temporary state, enumerates
pre-existing gateway processes without touching them, proves ownership of the new endpoint, and
requires cleanup of the owned process group before publication.

No raw frame wrapper or raw gateway log is published or tracked. Captured envelopes are held only
long enough to validate their three-frame flow. Before any fixture write, the sanitizer removes
captured field names, redacts strings, normalizes scalar values, aliases JSON-RPC identifiers,
preserves only catalog-approved protocol control strings, and validates canonical form. The
fixture replacement is atomic and occurs only after owned-process cleanup succeeds. Any missing,
ambiguous, dirty, redirected, noncanonical, unsanitized, or incomplete evidence is `BLOCKED` and
leaves the existing fixture unchanged.

Never hand-edit the fixture and never substitute fabricated frames. Regenerate it only through
the foreground harness, review only the sanitized diff, and run G3 plus the focused capture tests
before treating it as protocol evidence.
