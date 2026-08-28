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

## Outbound approval webhook

### Stock-Hermes capability verdict

At the pinned commit, stock Hermes **can** send `pre_approval_request` through `hooks.outbound`;
Talaria does not need a Hermes fork or protocol patch for approval wakeups.

The source chain is explicit. Every upstream path below is evaluated at the immutable commit
named in this document's provenance section:

1. `hermes_cli/plugins.py:234-249` registers `pre_approval_request` as a valid observer-only
   lifecycle hook.
2. `agent/outbound_webhooks.py:268-329` accepts every configured event in that valid-hook set. A
   target `matcher` is ignored for approval events; it applies only to `pre_tool_call` and
   `post_tool_call`.
3. `agent/outbound_webhooks.py:156-207` registers the event callback with the plugin manager, and
   `gateway/run.py:12793-12806` loads that configuration during ordinary gateway startup.
4. `tools/approval.py:4382-4463` fires `pre_approval_request` after queueing the approval and
   before the normal gateway notification callback. The hook return value cannot approve, deny,
   delay, or replace the native approval flow.

The HTTP worker is asynchronous, so enqueue order does not guarantee that a remote webhook
arrives before the gateway's native notification.

`HERMES_SAFE_MODE=1` disables outbound registration. Delivery is an advisory wakeup, not an
approval decision or a durable queue.

### Exact HTTP request

The callback in `agent/outbound_webhooks.py:380-455` sends an HTTP `POST` with a UTF-8 JSON body.
The body is produced by Python
`json.dumps(payload, ensure_ascii=False, default=str)`: keys are not sorted, ordinary Python JSON
separators are retained, unsupported values are stringified, and no trailing newline is added.
Receivers must verify the signature over the raw bytes; re-serializing parsed JSON changes the
signed representation.

A normal gateway approval has this shape. Placeholder strings describe source fields; they are
not captured values:

```json
{
  "hook_event_name": "pre_approval_request",
  "tool_name": null,
  "tool_input": null,
  "session_id": "<bound Hermes session identifier or empty>",
  "cwd": "<sensitive sender working directory or empty>",
  "extra": {
    "command": "<entire hook command after surface/configuration redaction>",
    "description": "<approval reason>",
    "pattern_key": "<primary approval pattern>",
    "pattern_keys": ["<approval pattern>"],
    "session_key": "<Hermes routing session key>",
    "surface": "gateway",
    "turn_id": "<turn identifier or empty>",
    "tool_call_id": "<tool-call identifier or empty>",
    "telemetry_schema_version": "hermes.observer.v1"
  },
  "delivery_id": "<UUIDv4 as 32 lowercase hexadecimal characters>",
  "timestamp": "<UTC ISO-8601 timestamp ending in Z>"
}
```

`session_id` falls back from `session_id` to `parent_session_id` and then to the empty string.
`cwd` comes from the sender process and is sensitive. Every hook argument other than
`tool_name`, `args`, `session_id`, and `parent_session_id` is placed under `extra`.
`tools/approval.py:108-138` adds the current turn, tool call, and bound session context.
`hermes_cli/plugins.py:5238-5244` injects `telemetry_schema_version`; its value is defined at
`hermes_cli/middleware.py:17`.

For ordinary gateway approvals, the `command` and `description` values are the entire strings
supplied to the hook after surface- and configuration-dependent redaction
(`tools/approval.py:3765-3777,4965-4997`). They can still contain sensitive text if redaction does
not cover it. Smart and other approval surfaces have different redaction paths. The outbound
serializer performs no additional sanitization, so every surface must be treated as sensitive.

The ordinary gateway path at `tools/approval.py:4451-4459` does **not** include the approval
queue's `request_id`. The queue entry creates that identifier at `tools/approval.py:2779-2787`.
Some approval-transport paths add `request_id` and `request_digest`
(`tools/approval.py:4213-4223`), and a coalesced follower can add `coalesced`
(`tools/approval.py:4310-4319`), so receivers must tolerate those extra members without depending
on them. A Talaria wakeup reconnects to the gateway, calls `approval.pending` to obtain the
authoritative pending record and `request_id`, and only then uses `approval.respond`; those
handlers are source-defined at `tui_gateway/methods_prompt.py:1588-1690`.

Pinned source does not guarantee a nonempty top-level `session_id`; the serializer explicitly
falls back to an empty string. `extra.session_key` is internal structured routing state and is not
a privacy-safe substitute. ADR-0001 therefore permits a push only when the bridge can alias a
nonempty bound session ID and otherwise relies on foreground/background reconciliation.

The request headers are:

```text
Content-Type: application/json
User-Agent: Hermes-Agent-Outbound-Webhook
X-Hermes-Event: pre_approval_request
X-Hermes-Delivery: <the body's delivery_id>
X-Hermes-Signature-256: sha256=<lowercase hexadecimal HMAC>
```

The signature is HMAC-SHA-256 over the exact raw body bytes, keyed by the UTF-8 bytes of the
resolved secret. Secret resolution is defined at `agent/outbound_webhooks.py:358-373`: a configured
`secret_env` takes precedence over an inline `secret`. If that environment lookup is absent or
empty, Hermes does not fall back to the inline value and omits the signature; with no usable
secret it also sends unsigned. Talaria's push ingress must require a configured secret and reject
an absent, malformed, or invalid signature.

The HMAC authenticates only the body; it does not cover the HTTP headers. After verifying the raw
body, a receiver must require the authenticated `hook_event_name` to match `X-Hermes-Event` and
the authenticated `delivery_id` to match `X-Hermes-Delivery`. A header value alone is not event or
delivery identity.

There is no key identifier, rotation metadata, timestamp header, sender-enforced freshness
window, nonce store, or replay rejection. The body timestamp and delivery ID are authenticated
only when the signature is present. Receivers must enforce freshness and deduplicate delivery
IDs themselves.

### Delivery semantics and privacy boundary

`agent/outbound_webhooks.py:89-99,221-231,331-340,458-569` implements best-effort delivery through
one daemon worker and a nondurable in-memory queue of 256 items. Enqueue is nonblocking and drops
a new event when full. The default per-attempt timeout is 10 seconds and is clamped to 1–60
seconds. Hermes makes at most two attempts, waiting one second before the retry; it retries server
errors and connection failures, but not redirects or client errors. Redirects are not followed,
response bodies are ignored, and process-exit flushing is best-effort for at most five seconds. A
retry reuses the same body, timestamp, delivery ID, and signature.

Stock Hermes accepts plain HTTP with a warning. Talaria's architecture requires HTTPS and treats
the webhook only as a lossy wakeup hint; foreground and background reconciliation remain
authoritative.

The raw approval webhook contains the entire hook-supplied command and description—which can
include unredacted sensitive text—and the sender's working directory. Making the eventual APNs
payload contentless does not erase that ingress disclosure. Consequently, a hosted APNs relay
must not receive the raw Hermes webhook directly. The gateway-side privacy bridge and central
relay boundary are fixed by
[ADR-0001](adr/0001-contentless-approval-push.md).

The pinned upstream repository already documents generic approval hooks and outbound webhooks at
`website/docs/user-guide/features/hooks.md:1268-1297,1840-1924`. What it does not present in one
place is the approval-specific outbound body together with the ordinary gateway path's absent
queue `request_id`. This section records that cross-source contract; it is not evidence that the
generic upstream feature is undocumented.

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
