# Hermes protocol contract

## Authority and provenance

Hermes source wins over documentation. Ipsima's current protocol catalog is derived from the
public `NousResearch/hermes-agent` repository at this immutable commit:

`e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246`

The compatible capture runtime reports Hermes version `0.20.5`. `scripts/derive_protocol.py`
reads Git objects at the pinned commit rather than mutable working-tree files. It records SHA-256
digests for all 33 source inputs, rejects a different upstream origin or unresolved commit, and
ratchets the catalog to the expected counts. The committed catalog's pinned SHA-256 is:

`0d87c36ee37f34d90e989259e0fa25fcfb24fce4e8a3c0f0f1c04a2bb735dda4`

Current exact inventory:

| Surface | Count |
| --- | ---: |
| Registered JSON-RPC requests | 168 |
| Server event types | 61 |
| REST method/path routes | 42 |
| Pinned upstream source inputs | 33 |
| Generated kind-aware conformance tests | 229 |
| Sanitized frames in the golden fixture | 3 |

`protocol/methods.json` is an identity and provenance catalog, not a complete schema for every
payload. Do not infer an unrecorded payload shape from a method name. Consult the pinned source
and extend the catalog or a real sanitized fixture when stronger schema knowledge is required.
The P1.1 semantic derivation adds no catalog field because the current generator supports
identity and provenance only; runtime facts remain source-cited here, and the catalog must still
regenerate byte-identically.

## Final live-HEAD re-verification

Before preparing any upstream contribution, every upstream-facing claim in this document was
rechecked against exact Hermes commit
`26350357d76e4508c8df9304a3374bdc5a6f6220` on 2026-08-30. The independently derived second
catalog is `protocol/methods-26350357.json`; its SHA-256 is
`15f4544c8c8350bc4a47d4195d9a2b45ad6c32fc5b6cf35d610af4dae205a5a2`.

HEAD has 170 requests, 63 events, and the same 42 REST routes. It adds requests
`mcp.servers.oauth.callback` and `prompt.btw`, plus events `btw.complete` and `todo.updated`, with
no removals. The exhaustive classification—including stale replay findings and Ipsima's own
overbroad `tool.progress` statement—is recorded in
[`contributions/nous-research/HEAD-REVERIFICATION.md`](../contributions/nous-research/HEAD-REVERIFICATION.md).
The original catalog and the rest of this document remain the contract for the immutable pinned
commit unless a paragraph explicitly gives a live-HEAD status.

## Transport and framing

The primary transport is WebSocket JSON-RPC 2.0. Each WebSocket text message contains exactly
one JSON-RPC object. The stdio form is newline-delimited. REST with SSE is the fallback surface;
its 42 entries are identified by the method-and-path pair, not by path alone.

Client requests use the ordinary JSON-RPC request shape with an identifier, method, and optional
parameters. Server events do **not** use their event type as the JSON-RPC method. Every pushed
gateway event uses this notification envelope, without an identifier:

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"<event-name>","session_id":"<live-session-id>","payload":{}}}
```

`params.session_id` and `params.payload` are conditional. `gateway.ready` is a sessionless event
sent to one newly accepted connection; other sessionless events can be global broadcasts. An event
with no body can omit `payload`. A successful response instead has `id` plus `result`; an error
response has `id` plus `error`. Responses do not use `method: "event"`, and their `id` correlates
them with one outstanding request. A parse error can use `id: null`. These shapes are constructed
at `tui_gateway/server.py:2023-2027,2400-2408`, with parse-error cases at
`tui_gateway/entry.py:486` and `tui_gateway/ws.py:438-443`, and dispatched at
`apps/shared/src/json-rpc-gateway.ts:425-459` in the pinned source.

Canonical fixture form is UTF-8 JSON with sorted object keys, no insignificant whitespace, and
one envelope per line. Stream delta scheduling can be coalesced by the server, but each event
remains its own WebSocket text message. Coalescing does not change event cardinality; the protocol
independently defines no one-model-token-to-one-event contract. Clients must not depend on that
cardinality or on exact inter-event timing.

There are two intentionally distinct liveness calls:

- `gateway.ping` is an inline transport heartbeat and returns `{"ok":true}`. It is not one of the
  168 registered request methods.
- `ping` is a registered request and returns `{"pong":true}`. The golden capture uses this method
  because it exercises normal request dispatch and response correlation.

## Authentication boundary

In gated deployments, an authenticated client sends `POST /api/auth/ws-ticket`; no request body is
needed. The dashboard accepts either its authenticated browser session or the native app's provider
bearer token and returns `{"ticket":"…","ttl_seconds":30}`. The ticket is process-local, in
memory, bound to the authenticated `user_id` and provider, and not bound to a path or socket.
Minting and storage are defined at `hermes_cli/dashboard_auth/routes.py:932-961` and
`hermes_cli/dashboard_auth/ws_tickets.py:39-77`; authentication and session attachment are at
`hermes_cli/dashboard_auth/middleware.py:323-373,375-520`.

Consumption is the authentication boundary, not `gateway.ready`:

1. `/api/ws` checks the feature flag and consumes the ticket.
2. It then checks the request's host, origin, and peer policy.
3. `tui_gateway.ws.handle_ws` accepts the WebSocket, builds the transport, resolves the initial
   skin payload, sends `gateway.ready`, and only then reads requests.

That order is source-defined at `hermes_cli/web_server.py:17535-17559` and
`tui_gateway/ws.py:342-406`. A valid ticket can therefore be burned by a later admission failure.
Once consumed, its expiry is never checked again for that connection. If it is expired when
consumed, the store removes it and the handler requests a pre-accept close with code 4401. If it
expires after successful consumption but before acceptance or readiness, the connection proceeds.
Expired, replayed, and never-minted ticket values share that server rejection branch, while the
server audit retains their internal reason (`hermes_cli/dashboard_auth/ws_tickets.py:81-107`,
`hermes_cli/web_server.py:16389-16416,17541-17543`). The pinned source and tests do not establish
the exact HTTP/WebSocket status or close code a remote peer observes when rejection occurs before
`ws.accept()`.

A public client must mint a fresh ticket before **every** connection attempt, including reconnect.
It cannot safely retry a ticket after a failed attempt because it cannot know whether consumption
occurred. Replaying a consumed ticket reaches the unknown-ticket branch and the same pre-accept
rejection path; it does not revoke the authenticated REST session or sibling tickets. An explicit
401/403 or structured reauthentication result from ticket minting is the official client's
reauthentication signal; transport and server failures remain connectivity failures. The official
browser and desktop paths follow this remint-before-redial rule at
`web/src/lib/gatewayClient.ts:40-61`, `apps/desktop/electron/main.ts:7724-7751`,
`apps/desktop/electron/connection-config.ts:118-188,218-221`, and
`apps/shared/src/websocket-url.ts:39-79`.

The integer-second expiry comparison is `expires_at < now`, so equality at the recorded expiry
second is accepted. Consumption atomically removes the entry before that check; an expired
consumption attempt burns the ticket too. Process restart loses every outstanding ticket. The
pinned source defines no logout or session-revocation recheck at consumption time.

`gateway.ready` is a separate protocol-ready milestone after successful transport admission. The
pinned server defines no readiness deadline, retry count, or client acknowledgement. Those are
Ipsima-owned policy questions for P1.2, not facts to infer from socket `open`. The pinned shared
client itself marks the transport open before it later processes `gateway.ready`
(`apps/shared/src/json-rpc-gateway.ts:198-265,448-458`); waiting for readiness is Ipsima's planned
handshake policy, not an upstream client requirement.

P1.2 fixes that Ipsima-owned policy as follows:

- `connect()` completes only after one text frame decodes as JSON-RPC 2.0 method `event`, with no
  identifier, result, error, or session ID, and with `params.type: "gateway.ready"` plus an object
  payload. Socket opening alone is never ready; a binary, malformed, bare, sessionful, or different
  first frame closes the attempt.
- The readiness deadline is a positive configurable duration, defaulting to ten seconds. Expiry or
  cancellation closes the underlying socket and leaves the transport disconnected. Every later
  `connect()` remints a ticket; no failed attempt reuses one.
- The primary client uses the official `?ticket=` WebSocket form and never sends the legacy
  `token` or server-child `internal` credential. Errors are mapped to bounded categories before
  leaving the transport so a bearer value, ticket-bearing URL, or response body cannot escape.
- After readiness, the transport preserves one-message-per-text-frame ordering and permits one
  pending receive. It does not correlate request IDs or unwrap events; those are P1.3 routing
  responsibilities.

The Apple implementation uses `URLSessionWebSocketTask`. Swift 6.3.3 exposes the same API through
FoundationNetworking on the pinned Linux environment, but that environment's libcurl was built
without WebSocket support and fails at runtime. Linux therefore exercises the production ticket,
handshake, framing, and lifecycle actor through the injected package `MockGateway`; it does not
claim live URLSession WebSocket I/O. Adding a second networking stack solely for Linux tests is
outside this objective.

Current loopback mode uses the legacy process-scoped `?token=` compatibility credential. On a
public bind, gated mode rejects that token; the legacy `--insecure` flag no longer disables
public-bind authentication (`hermes_cli/web_server.py:529-551,703-741,16313-16423,19395-19416`).
The process-lifetime, multi-use `?internal=` credential is exclusively for server-spawned children
and is never a client mechanism (`hermes_cli/dashboard_auth/ws_tickets.py:110-149`,
`hermes_cli/web_server.py:16326-16336`). The ticket can also be carried in the precisely paired
`hermes-gateway-v1` and `hermes-gateway-ticket.<ticket>` WebSocket subprotocols; only the stable
public protocol is reflected after admission (`hermes_cli/web_server.py:16293-16310,16402-16407`).
Official browser and desktop clients currently use `?ticket=`.

The REST/SSE fallback is the separate `gateway/platforms/api_server.py` listener, authenticated
with `Authorization: Bearer <API_SERVER_KEY>`, not the dashboard provider bearer
(`gateway/platforms/api_server.py:1920-1973,2241-2272`). It has no `/api/ws`, WebSocket ticket, or
`gateway.ready` contract (`website/docs/user-guide/tui.md:284-290`). Client credentials remain
outside source, logs, fixtures, documentation, and public evidence.

## Request and event namespaces

Requests and events are separate namespaces even when their strings match. Two names currently
collide across kinds:

- `session.title`
- `session.usage`

Each collision therefore has two distinct generated tests: a request-shaped JSON-RPC round trip
and a real event-envelope round trip. Across the whole catalog, generation produces exactly 168
request tests plus 61 event tests, for 229 kind-aware tests. Collapsing the namespaces would hide
two cases and is a G3 failure.

For events flowing through `server.write_json` and carrying a live `session_id`, the server routes
first to that session's stored transport. Otherwise it uses the transport bound to the current
request and finally stdio. A session-less global event uses a separate connected-transport
broadcast path. This precedence is defined at `tui_gateway/server.py:1990-2020,2034-2077`.

That ladder is not universal. Browser-controller broker notifications construct the standard
sessionful event envelope but write directly to an explicit transport, bypassing `write_json`,
sequence stamping, and replay (`tui_gateway/methods_browser_control.py:92-114`). It means an event
type is not a routing key, and a nonempty `session_id` alone does not prove replay support:
envelope shape, producer path, live session identity, and request correlation remain separate
concerns.

## Lifecycle facts established from source

The TUI gateway's tool-event identities are:

`tool.start` → `tool.generating` → `tool.output_risk` → `tool.complete`

This is not a universal strict sequence: `tool.generating` and `tool.output_risk` are conditional.
The TUI gateway has no `tool.progress` producer at either the pin or re-verified HEAD. The earlier
repo-wide statement that `tool.progress` was absent from the pinned source was never valid: the
pinned shared client accepted that identifier, and at HEAD the separate REST/SSE API emits
`hermes.tool.progress`. Upstream's TUI-gateway event guide still presents `tool.progress` as a TUI
producer event, which is the narrower surviving documentation defect.

The timeout-backed `_block` derivation contains exactly nine request/expire families:
`clarify`, `mcp.setup`, `preview.act`, `preview.read`, `secret`, `sudo`, `terminal.read`, `tour`,
and `window.read`. The catalog therefore has nine corresponding `.expire` event names.
`approval.request` is emitted separately and has no catalogued `approval.expire`. Consequently,
the complete event catalog contains ten names ending in `.request`, but only nine ending in
`.expire`; it does not contain ten request/expire pairs.

`gateway.ready` is the initial server notification captured by the real gateway harness. Because
the catalog inventories identities rather than every payload member, the pinned source and
sanitized captures remain authoritative for its payload.

## Replay and reconnect at the pinned commit

There is **no** `replay_epoch`, `replayEpoch`, replay-generation field, or equivalent wire identity
anywhere in the pinned source. `gateway.ready` advertises skin, change-event, and heartbeat
capabilities, but no replay generation (`tui_gateway/ws.py:368-382`). The earlier claim that a
client could compare a gateway replay epoch was incorrect.

**Live-HEAD status: STALE.** HEAD defines a process replay epoch, emits `replay_epoch` in both
`gateway.ready` transports, returns `epoch` from `session.events.since`, and uses epoch changes to
clear stale client watermarks (`tui_gateway/event_replay.py:25-31,47-49`,
`tui_gateway/ws.py:369-385`, `tui_gateway/entry.py:454-466`,
`tui_gateway/methods_session.py:3686-3697`,
`apps/shared/src/json-rpc-gateway.ts:465-479,565-578,617-632` at HEAD).

Hermes instead implements a process-memory replay ring in `tui_gateway/event_replay.py:24-74`:

- Each event flowing through `server.write_json` and carrying a nonempty live `session_id` receives
  a per-session integer `params.seq`. Assignment and ring insertion share one module lock.
- Each session retains at most 512 event frames; at most 64 sessions are retained. Session rings
  are evicted FIFO, and eviction deletes that session's sequence counter.
- Events without a nonempty `session_id`, including `gateway.ready` and global broadcasts, receive
  no sequence and have no replay contract.
- A process restart loses all rings and counters. An event after restart or ring eviction can
  therefore reuse sequence 1 for the same textual session ID.

`session.events.since {session_id,last_seen}` returns `events`, `latest_seq`, `truncated`, and
`count` (`tui_gateway/methods_session.py:3640-3670`). `events` is an ordered array of **complete
JSON-RPC event envelopes**, not bare `params` objects. `truncated` is true only when the oldest
retained sequence is greater than `last_seen + 1`. An unknown or evicted ring returns an empty
array, `latest_seq: 0`, and `truncated: false`, so it is indistinguishable from a known session
that has never emitted a sequenced event.

**Live-HEAD status: PARTLY STALE.** HEAD adds `epoch` to the result and stores/returns bare event
`params` objects rather than complete JSON-RPC envelopes. The bounded-ring, truncation, and
unknown-ring behavior remains.

### Resolved upstream after the pin: TypeScript replay envelope mismatch

The pinned source contains a real producer/consumer disagreement. The server stores and returns
complete envelopes (`tui_gateway/event_replay.py:36-68`), but the shared TypeScript client expects
bare `{type,session_id,seq,payload}` values and skips a real envelope because it has no top-level
`type` (`apps/shared/src/json-rpc-gateway.ts:503-526`). Its test fabricates the bare shape at
`apps/shared/src/json-rpc-gateway-replay.test.ts:131-146`. Server output is authoritative for
Ipsima; a later decoder may deliberately accept both shapes for compatibility, but must not copy
the bare-only assumption.

That mismatch is fixed at HEAD by commit
`beb794123618c997e82791316df643fc61347665`. The server now deliberately returns bare events, the
client consumes that shape, and tests cover the real producer shape, reject the obsolete envelope,
hold live frames during replay, and reset watermarks on epoch change. No replay bug report is a
valid upstream contribution.

Reconnect requires several distinct operations, but the pinned source establishes no lossless
ordering or barrier among them:

- Mint a fresh ticket and open a new socket. Treat that socket's `gateway.ready` as Ipsima's
  protocol-ready boundary.
- Reattach server state. A still-live session can be rebound by `session.activate`; a durable
  session can be reopened by `session.resume`. The latter may reuse the old live ID or return a new
  one, and it follows compression-continuation IDs to their current tip
  (`tui_gateway/methods_session.py:372-1078,1219-1240`).
- Treat replay only as transient event-gap recovery for a reused live ID. At the pinned commit,
  decode each returned full envelope and correlate by `params.session_id` and `params.seq`; at
  live HEAD, decode each returned bare event and correlate by `session_id` and `seq`.
- Reconcile from a transcript snapshot through resume/activate, `session.history`, or the
  authenticated durable transcript whenever replay is truncated, malformed, refers to a replaced
  live ID, reports a lower `latest_seq` than a retained watermark, or otherwise cannot prove
  continuity. Never blindly resubmit an ambiguously acknowledged prompt.

The shared client starts replay from socket `open`, while the desktop's session reactivation runs
independently after a closed-to-open transition (`apps/shared/src/json-rpc-gateway.ts:211-223`,
`apps/desktop/src/app/session/hooks/use-route-resume.ts:112-166`). P1.4 must define how Ipsima
buffers or sequences reattach, snapshot, replay, and newly arriving live events; no order shown
above is an upstream guarantee. At the pinned commit, a lower counter detects some resets but
cannot identify a generation reliably. HEAD's epoch now identifies process-generation changes,
but it does not recover evicted or pre-restart history. Neither revision can promise lossless
replay across restart or eviction.

Source-undetermined replay questions remain open: there is no normative choice among
`session.activate`, `session.resume`, `session.history`, and the durable REST transcript for every
gap; no replay/live handoff barrier; no prescribed snapshot refresh for every session-less global
event; and no cross-session ordering contract. These unknowns constrain P1.4 rather than being
filled with assumed behavior.

## Event ordering and coalescing

Within one retained session ring, `seq` assignment is monotonic in the current in-memory
generation and `session.events.since` preserves deque order. There is no cross-session sequence.
Long-running RPC handlers execute on a thread pool, so responses can complete in a different order
from requests and must be matched by JSON-RPC `id` (`tui_gateway/server.py:218-366,2475-2513`).

On WebSocket transport, only `message.delta`, `reasoning.delta`, and `thinking.delta` are eligible
for the 33 ms scheduling buffer (`tui_gateway/ws.py:45-61,140-164`). A non-streaming event or RPC
response drains already-buffered deltas ahead of itself. One async send lock serializes each batch,
and every serialized event is still sent through a separate `send_text` call
(`tui_gateway/ws.py:166-189,211-259`). Thus an already-buffered delta cannot be overtaken by a
later control frame on that transport, and coalescing does not merge payloads or alter framing.

The pinned source does **not** establish a strict total order across concurrent producer threads.
Sequence assignment and transport enqueue use different locks, and replay can arrive while live
events continue. At the pin, the shared client advanced its maximum watermark but still dispatched
duplicate, late, or decreasing-sequence events. At HEAD, replay-returned events and live events
parked while replay is in flight are gated against non-increasing sequences, but ordinary live
frames still dispatch after watermark recording (`apps/shared/src/json-rpc-gateway.ts:475-494,
529-648` at HEAD). The lack of a strict producer total order remains; the old replay-race behavior
is stale while the ordinary-live limitation survives.

Replay response fields are not one atomic snapshot. `events_since` releases the replay lock before
the handler separately reads truncation state and `latest_seq`; a new event can land between those
reads (`tui_gateway/event_replay.py:62-74`, `tui_gateway/methods_session.py:3657-3669`).
`latest_seq` can therefore exceed the highest event returned and cannot mean that every event
through that value was included. A later client must advance its accepted watermark only from
events it actually validates and applies.

## The nine timeout-backed blocking families

All nine share one mechanism at `tui_gateway/server.py:4031-4110,12617-12644` and map their
response value through `tui_gateway/methods_prompt.py:1515-1585`:

- `_block` generates an eight-hex-character `request_id`, registers one waiter, adds that ID to
  the event payload, emits `<family>.request`, and waits.
- The client answers with a JSON-RPC `<family>.respond` request whose params contain that
  `request_id` and the family value shown below. A live answer normally returns
  `{"status":"ok"}`. The server does not validate the value's presence or type; a missing value
  becomes an empty string.
- When a bounded wait returns false, the server normally retires the waiter, emits
  `<family>.expire` with payload exactly `{"request_id":"…"}`, and gives the tool its
  family-specific timeout result. A non-batch response or batch cancel-all racing the deadline can
  acquire the prompt lock first and suppress expiry. A per-question batch clarify differs: if the
  wait already returned false, even the final lock can be retained while the result is marked
  `timed_out:true` and `clarify.expire` is emitted. `.expire` means **the server's timed wait
  returned false and it retired that request**. It is not a cancellation request and is not
  emitted for an explicit empty answer or `session.interrupt`.
- A late reply, a duplicate after cleanup, and any never-known nonempty request ID all return
  `{"status":"expired"}`. That result does not prove the request once existed. A missing or empty
  ID is error 4009.

| Family | Trigger and request payload before the added `request_id` | Response params | Deadline and no-answer result |
| --- | --- | --- | --- |
| `clarify` | The model invokes `clarify`. Single: `{question,choices,multi_select?}`. Batch: `{questions:[{qid,question,choices,multi_select}]}`. | `{request_id,answer}`; batch requires `question_id` to lock one answer, and omission cancels the whole batch. The last lock releases the waiter. | Configured timeout; canonical default 3600 s, unexpected bridge-exception fallback 300 s, nonpositive means unlimited. Bounded single returns an empty user response. Batch preserves locked answers and marks `timed_out:true`. (`tools/clarify_gateway.py:531-574`, `tools/clarify_tool.py:255-276,416-425`, `tui_gateway/server.py:4113-4167`) |
| `mcp.setup` | The model invokes `setup_mcp`; `{server,action,reason}`, where action is install, enable, or authorize. | `{request_id,result}`; intended `result` is a JSON string describing installed, enabled, authorized, declined, or error state. | 600 s. The tool returns `status:"unanswered"`. Setup or OAuth work already started by the client is not rolled back. (`tools/setup_mcp_tool.py:25-87`, `tui_gateway/server.py:6983-6994`, `apps/desktop/src/components/assistant-ui/mcp-setup-tool.tsx:213-234`) |
| `preview.act` | `drive_preview` or `annotate_preview`; action plus its nonnull target/input fields. | `{request_id,text}`; intended `text` is a JSON action outcome; drive responses can include refreshed or delta inventory. | 45 s. The tool returns a timeout/no-GUI error. A click, typing action, or annotation already running client-side is not canceled. (`tools/drive_preview_tool.py:35-124`, `tools/annotate_preview_tool.py:33-86`, `tui_gateway/server.py:6959-6972`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts:87-125`) |
| `preview.read` | The model invokes `read_preview`; optional normalized `start` and `count`. | `{request_id,text}`; intended `text` is serialized preview content and bounds. | 45 s. The tool reports no preview or timeout; an in-flight client extraction is not canceled. (`tools/read_preview_tool.py:21-66`, `tui_gateway/server.py:6949-6958`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts:67-81`) |
| `secret` | An interactive skill load finds a missing required environment value; `{env_var,prompt,metadata?}`. | `{request_id,value}`. A nonempty value is persisted through the secure environment-value path. | 300 s. The callback reports a successful skip with `validated:false`; no value is saved. (`tools/skills_tool.py:409-484,1698-1720`, `tui_gateway/server.py:7081-7102`) |
| `sudo` | A sudo-containing command in a supported terminal execution environment has no configured/cached password and an interactive prompt is available; only local execution pre-probes NOPASSWD. Request payload: `{}`. | `{request_id,password}`. A nonempty value is session-scoped when an active session key exists, otherwise callback- or thread-scoped. It is supplied backend-specifically: piped where supported and embedded by Modal, Daytona, or Vercel Sandbox. | 120 s. No password is supplied, but the original command can still run; its outcome depends on backend sudo policy. (`tools/environments/base.py:1529-1533`, `tools/terminal_tool.py:311-342,525-547,1015-1107`, `tui_gateway/server.py:7073-7079`) |
| `terminal.read` | The model invokes `read_terminal`; optional normalized `start` and `count`. | `{request_id,text}`; intended `text` is serialized terminal-buffer metadata and content. | 30 s. The tool reports no in-app terminal or timeout. (`tools/read_terminal_tool.py:20-61`, `tui_gateway/server.py:6941-6948`) |
| `tour` | The model invokes `tour`; `{action,surface}` plus nonnull selector, title, text, side, steps, or index fields. | `{request_id,text}`; intended `text` is a JSON action outcome. | First request in a live session probes for 10 s; after any nonempty answer, later requests get 45 s. An unanswered probe marks the bridge unavailable and later calls in that live session return immediately. An action already underway is not canceled. (`tools/tour_tool.py:30-107`, `tui_gateway/server.py:4170-4235`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts:170-209`) |
| `window.read` | The model invokes `read_window_below`; `{}`. | `{request_id,text}`; intended `text` is JSON window metadata or an error, never pixels. | 30 s. The tool reports that the window could not be determined; an in-flight main-process read is not canceled. (`tools/read_window_tool.py:25-56`, `tui_gateway/server.py:6973-6982`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts:131-150`) |

Batch clarify is the one special response path: `{request_id,question_id,answer}` updates an
editable answer and returns the remaining question IDs; all IDs locked releases the waiter.
Omitting `question_id` performs cancel-all. At a deadline, locked answers survive and the response
to the tool carries `timed_out:true` (`tui_gateway/server.py:4069-4085,12617-12644`).

Only clarify has an explicit pending-state snapshot for reconnect, including already locked batch
answers (`tui_gateway/server.py:2308-2332,9665-9668`). The other eight rely at most on the bounded
event ring; source defines no durable pending snapshot when their request has fallen out of it.
Timeout does not cancel client-side work, so Ipsima must never auto-retry side-effecting
`mcp.setup`, `preview.act`, or `tour` requests.

Bundled-client expiry cleanup is not complete enough to infer a universal UI contract. The desktop
has an explicit `clarify.expire` path, and the TUI has `sudo.expire` and `secret.expire`; the pinned
tree has no literal consumer for `mcp.setup.expire`, `preview.act.expire`, `preview.read.expire`,
`terminal.read.expire`, `tour.expire`, or `window.read.expire`
(`apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.ts:160-188`,
`ui-tui/src/app/createGatewayEventHandler.ts:1281-1288`). Card teardown for those families is
source-undetermined. For a detached tour caller, the source comment says unprobed but the code
still applies a nonpersistent 10 s probe; that code/comment conflict is also undetermined
(`tui_gateway/server.py:4213-4228`).

The server registry binds a pending waiter only to the short request ID and owning session, while
`_respond` looks up only the request ID. It does not verify response family, session, or transport,
and it accepts another live reply before cleanup. The source therefore does not establish
at-most-once resolution or cross-family protection. The ID contains only 32 random bits and
insertion performs no collision check. Ipsima's later state machine must retain and match family,
session, and request identity itself; it must not replay passwords or secrets. The secret-capture
callback is also process-global and is rewired to a session-capturing closure for each turn
(`tools/skills_tool.py:177,248-250`, `tui_gateway/server.py:4038-4043,7081-7102,11464-11470`), so
concurrent-session destination safety is source-undetermined.

## Session lifecycle and client invalidation

Hermes uses two session identities: a short process-local live `session_id`, which owns transport
and replay state, and a durable stored/session key used by persistence and resume. They can diverge
and can change independently.

| RPC | Server-side effect | Client-held state consequence |
| --- | --- | --- |
| `session.create` | Allocates a new live ID and durable key, registers an in-memory session, returns its seed transcript and lightweight info, and builds the agent asynchronously. It deliberately does not create a database row until the first prompt. (`tui_gateway/methods_session.py:14-160`) | Initialize a distinct session from the response. An untouched draft is only process-memory state and is not restart-durable. No existing session is closed or invalidated. |
| `session.activate` | Looks up an already-live ID, rebinds it to the requesting transport, cancels a pending orphan reap, and returns a current live snapshot. It does not close the previously focused session or establish a process-global “active” session. (`tui_gateway/methods_session.py:1181-1240`, `tui_gateway/server.py:9597-9669`) | Replace cached state for the selected live session with the returned snapshot. Other sessions remain valid. A missing live ID requires durable resume, not repeated activate. |
| `session.resume` | Resolves a durable key or title, follows a compression-continuation chain, reuses and rebinds a matching live session when possible, or creates a new live ID and hydrates durable history. It can return full, omitted, or deferred messages according to explicit request flags. A reused unpersisted draft reports its durable identity as `stored_session_id`; ordinary live/cold paths use `session_key`, so the response schema is not uniform. (`tui_gateway/methods_session.py:372-1078`, `tui_gateway/server.py:9649-9659`) | Normalize both durable-ID field spellings and treat the returned identities as authoritative. If the live ID changed, discard old live-only state: replay watermark, pending stream fragments, and transport ownership. Replace or hydrate the transcript as indicated; do not infer continuity from the socket. |
| `session.close` | Atomically removes the live record, then best-effort flushes unpersisted messages, fires lifecycle cleanup, ends TUI-owned durable rows, interrupts owned background delegations, and closes agent/worker resources. Repeated close returns `closed:false`. It does not delete stored history. (`tui_gateway/methods_session.py:3099-3108`, `tui_gateway/server.py:773-1047`) | Invalidate the live ID and all replay, streaming, pending-input, and queued client state. The durable transcript may still be listed or resumed; gateway-owned rows have a different lifecycle and are not ended by this viewer close. |
| `session.interrupt` | Requests a hard turn interrupt, stops process-global streaming TTS, clears queued prompts, advances queue generation, releases `_block` waiters with empty answers, and best-effort denies every pending gateway approval for the durable session. It retains the live session. (`tui_gateway/methods_session.py:3327-3346`, `tui_gateway/server.py:1102-1149`) | Keep both session IDs, but clear local streaming, queued-send, blocking-card, and approval-pending state. Reconcile history before sending again because partial turn output can remain. No `.expire` event is implied by the interrupt. In a multi-session process, the global TTS stop can also silence another session. |
| `session.branch` | Copies a prefix (or all) of the nonempty visible user/assistant display transcript into a new durable child and new live session, preserving lineage and leaving the parent live. Tool-only rows are not branch seeds. (`tui_gateway/methods_session.py:3111-3324`) | Treat the response as a new independent session with new replay and persistence identities. Do not mutate or invalidate the parent. The returned transcript is the child's authoritative initial state. |
| `session.compress` | Refuses an in-process busy session until it is interrupted. Compression replaces working history with a summary/protected tail and can rotate the durable key to a continuation while retaining the live ID. It updates the gateway `session_key`, invalidates queued-prompt generation, and attempts to re-anchor lease, approval, and worker state; several re-anchors are best-effort. Local and fallback compute-host paths can return replacement messages/info; a held lock returns a success result with `compressed:false` and no replacement state, while a structured compute-host result is returned verbatim with no gateway-enforced shape. (`tui_gateway/methods_session.py:2855-3024`, `tui_gateway/server.py:5857-6081`) | Keep the live ID and its replay namespace. Branch on outcome and field presence: a held lock leaves cached state unchanged; when replacement messages/info are present, replace the transcript and adopt `info.stored_session_id`. Only `session.resume` is proven to follow a stale pre-compression durable key to its continuation; other operations must use the new key. Queued-send assumptions made before rotation are stale. |

On WebSocket loss, sessions with `close_on_disconnect` are torn down immediately. A session with
another live viewer is rebound to the most recent one. Otherwise it is detached and, when the
configured grace is positive, enters an orphan-reap window (20 s by default); a quick resume or
transport rebind cancels that reap. A zero grace disables timer scheduling and leaves the detached
record parked (`tui_gateway/server.py:1240-1430`). This grace is not an auth credential: reconnect
still needs a newly minted ticket. If the old live record survives, resume may preserve its live ID
and replay ring. After reaping, resume can reconstruct a new live session only if a durable row
exists; an untouched, never-persisted draft is lost (`tui_gateway/methods_session.py:426-485,540-541`).

Unlike `session.interrupt`, `session.close` does not explicitly call `_clear_pending` or deny
pending approvals before teardown. The source therefore does not establish interrupt-equivalent
cleanup for an `_block` waiter already running when a session closes; its eventual response,
timeout, or teardown interaction is source-undetermined.

A live session can have multiple viewers, but a caller-initiated `session.close` removes their
shared record and is deliberately excluded from `session.reclaimed` broadcasts. The calling peer
learns closure from its response; source defines no explicit invalidation event for another viewer,
which may discover the missing live ID only on a later request
(`tui_gateway/server.py:923-951,9611-9617`). Multi-window clients therefore need their own closure
coordination policy.

`session.history` returns a live-session transcript snapshot, preferring persisted lineage when
available (`tui_gateway/methods_session.py:2776-2805`). The source does not define one universal
client cache-invalidation transaction spanning resume, replay, REST history, and live events. P1
state-machine design must make that transaction explicit after this semantic review; P1.1 does
not implement it.

## Outbound approval webhook

### Stock-Hermes capability verdict

At the pinned commit, stock Hermes **can** send `pre_approval_request` through `hooks.outbound`;
Ipsima does not need a Hermes fork or protocol patch for approval wakeups.

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
on them. A Ipsima wakeup reconnects to the gateway, calls `approval.pending` to obtain the
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
secret it also sends unsigned. Ipsima's push ingress must require a configured secret and reject
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

Stock Hermes accepts plain HTTP with a warning. Ipsima's architecture requires HTTPS and treats
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
validates the 33-input manifest, verifies the 168/61/42 count ratchet, and compares exact catalog
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
catalog-wide behavior is covered by the 229 generated tests. A deterministic three-frame capture
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
