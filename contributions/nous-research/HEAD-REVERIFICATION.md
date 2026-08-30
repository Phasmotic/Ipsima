# Hermes HEAD protocol re-verification

This is the final source audit used to decide which Ipsima findings are safe to offer upstream.
It compares the original pinned Hermes commit
`e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246` with exact upstream HEAD
`26350357d76e4508c8df9304a3374bdc5a6f6220` on 2026-08-30. Source wins over
documentation throughout.

## Reproducible catalog delta

The original catalog remains `protocol/methods.json`. The second, independently derived catalog
is `protocol/methods-26350357.json`, whose SHA-256 is
`8863412ba36e4b518da9aff635312e937d97a7a44c92ebf52c99c1694a15255b`.

| Surface | Original pin | HEAD | Semantic delta |
| --- | ---: | ---: | --- |
| JSON-RPC requests | 168 | 170 | Added `mcp.servers.oauth.callback`, `prompt.btw` |
| Server events | 56 | 58 | Added `btw.complete`, `todo.updated` |
| REST method/path routes | 42 | 42 | None |
| Request/event collisions | 2 | 2 | Still `session.title`, `session.usage` |
| Source inputs | 33 | 33 | Same manifest; 11 blobs changed |

No request, event, or REST route was removed. The changed input blobs are
`gateway/platforms/api_server.py`, `hermes_cli/web_server.py`, and
`tui_gateway/{entry.py,event_replay.py,mcp_oauth_sessions.py,methods_profiles.py,methods_prompt.py,methods_session.py,methods_tools.py,server.py,ws.py}`.

Reproduction from a canonical Hermes Git object database:

```bash
python3 -B scripts/derive_protocol.py <Hermes-checkout> \
  --revision 26350357d76e4508c8df9304a3374bdc5a6f6220 \
  --derived-at 2026-08-30 \
  --expected-counts 170 58 42 \
  --output protocol/methods-26350357.json \
  --check
```

## Claim-by-claim classification

`STILL VALID` means the asserted upstream behavior remains true at HEAD. It is an upstream
contribution only when the evidence also identifies inaccurate or materially incomplete upstream
documentation. `STALE` means the assertion was true at the old pin but changed before HEAD.
`NEVER VALID` means Ipsima stated the claim too broadly or incorrectly even for the old pin.
Compound prose from `docs/PROTOCOL.md` is split below so every individual claim receives exactly
one verdict.

| ID | Verdict | HEAD verification |
| --- | --- | --- |
| 01 | STILL VALID | WebSocket carries one JSON object per text message, while stdio is newline-delimited (`tui_gateway/ws.py:141-145,236-260,420-443`; `tui_gateway/entry.py:486-502`; `tui_gateway/transport.py:100-142`). Upstream comments at `tui_gateway/ws.py:10-12` and `web/src/lib/gatewayClient.ts:4-7` remain wrong and are contributable. |
| 02 | STILL VALID | Request, event-notification, success-response, and error-response envelopes remain distinct (`tui_gateway/server.py:2511-2519,2888-2897`; `tui_gateway/entry.py:499-503`; `tui_gateway/ws.py:441-457`; `apps/shared/src/json-rpc-gateway.ts:450-495`). |
| 03 | STILL VALID | Only message/reasoning/thinking deltas use the scheduling buffer; control frames drain it and each object remains a distinct `send_text` (`tui_gateway/ws.py:46-62,133-180,212-260`). |
| 04 | STILL VALID | Inline `gateway.ping` returns `{ok:true}`; registered `ping` returns `{pong:true}` (`tui_gateway/ws.py:474-487`; `tui_gateway/server.py:16755-16767`). |
| 05 | STILL VALID | Ticket minting remains bodyless, authenticated, process-memory, identity-bound, single-use, and 30 seconds (`hermes_cli/dashboard_auth/routes.py:932-961`; `hermes_cli/dashboard_auth/ws_tickets.py:39-78`). |
| 06 | STILL VALID | `/api/ws` consumes authentication before later admission checks; acceptance and `gateway.ready` follow, so a later rejection can burn the ticket (`hermes_cli/web_server.py:17519-17543`; `tui_gateway/ws.py:350-390`). |
| 07 | STILL VALID | Official clients mint a fresh ticket per connect/reconnect and distinguish reauthentication failures from transient transport failures (`web/src/lib/gatewayClient.ts:40-61`; `apps/shared/src/websocket-url.ts:11-15,39-79`; `apps/desktop/electron/connection-config.ts:118-188,951-961`). |
| 08 | STILL VALID | Consumption atomically removes a ticket, then applies `expires_at < now`; equality is accepted, expired use burns the entry, restart loses entries, and no revocation recheck exists (`hermes_cli/dashboard_auth/ws_tickets.py:44-50,81-107`). |
| 09 | STILL VALID | Ready is post-admission with no server deadline, retry, or acknowledgement; the shared client still marks socket-open before handling ready (`apps/shared/src/json-rpc-gateway.ts:215-240,465-480`; `tui_gateway/ws.py:369-418`). |
| 10 | STILL VALID | Gated/public mode accepts ticket or internal credentials and rejects legacy token; loopback compatibility, paired subprotocol parsing, and internal-child credential behavior remain (`hermes_cli/web_server.py:16277-16412,19511-19521`; `hermes_cli/dashboard_auth/ws_tickets.py:110-153`). |
| 11 | STILL VALID | REST/SSE remains a separate bearer-key API-server listener with no `/api/ws` contract (`gateway/platforms/api_server.py:1511,1920-1973,2247-2277`; `website/docs/user-guide/tui.md:284-290`). |
| 12a | STILL VALID | Requests and events remain separate namespaces, with only `session.title` and `session.usage` colliding (`tui_gateway/methods_session.py:1310,1710`; `tui_gateway/server.py:12539,12896-12897`). |
| 12b | STALE | The 168-request, 56-event, and 224-test live-HEAD counts are obsolete. HEAD derives 170 requests and 58 events, hence 228 kind-aware cases. The pinned catalog's old counts remain correct for its own immutable commit. |
| 13 | STILL VALID | Session transport, request-context transport, then stdio precedence and separate sessionless broadcast remain (`tui_gateway/server.py:2478-2508,2522-2565`). |
| 14 | STILL VALID | Browser-controller notifications still bypass central routing, sequence stamping, and replay while using a standard sessionful event envelope (`tui_gateway/methods_browser_control.py:92-114`). |
| 15a | STILL VALID | The TUI gateway emits `tool.start`, optional `tool.generating`, optional `tool.output_risk`, and `tool.complete`; it has no `tool.progress` producer (`tui_gateway/server.py:7735-7854,8051-8082`). The TUI event guide's `tool.progress` producer claim is contributably wrong (`website/docs/developer-guide/programmatic-integration.md:75-77`). |
| 15b | NEVER VALID | The strict universal arrow `tool.start -> tool.generating -> tool.output_risk -> tool.complete` was overbroad: the middle events are conditional. |
| 15c | NEVER VALID | “No `tool.progress` event in the pinned source” was repo-wide overreach. REST/SSE emits `tool.progress`/`hermes.tool.progress` (`gateway/platforms/api_server.py:4826-4830,5212-5217,5446-5455`). The accurate surviving claim is only that the TUI gateway has no producer. Compatibility consumers and accepted-type declarations are not defects. |
| 16 | STILL VALID | The `_block` allowlist remains exactly nine request/expire families; separate `approval.request` makes ten request names and nine expire names (`tui_gateway/server.py:4603-4625`). |
| 17 | STILL VALID | `gateway.ready` remains the initial notification and its payload remains source-authoritative; HEAD adds `replay_epoch` to skin, change-event, and heartbeat data (`tui_gateway/ws.py:369-385`; `tui_gateway/entry.py:454-466`). |
| 18 | STALE | Absence of a replay generation was true at the old pin only. HEAD defines a process replay epoch and emits it in both ready transports and replay responses (`tui_gateway/event_replay.py:25-31,47-49`; `tui_gateway/ws.py:369-385`; `tui_gateway/entry.py:454-466`; `tui_gateway/methods_session.py:3686-3697`). |
| 19 | STILL VALID | The 512-event/64-session in-memory rings, locked per-session stamping, FIFO session eviction, counter deletion, sessionless exclusion, and restart reset remain. Sequence 1 can recur, but the epoch now identifies the generation (`tui_gateway/event_replay.py:33-75`). |
| 20a | STILL VALID | Replay still returns ordered events plus `latest_seq`, `truncated`, and `count`; an unknown/evicted ring remains `[]`, zero, and false (`tui_gateway/event_replay.py:78-108`; `tui_gateway/methods_session.py:3669-3697`). |
| 20b | STALE | The old exhaustive replay-result field list is obsolete because HEAD adds `epoch`. |
| 20c | STALE | Replayed elements are no longer complete JSON-RPC envelopes. HEAD stores and returns bare event `params` objects (`tui_gateway/event_replay.py:40-42,75,78-91`). |
| 21 | STALE | The TypeScript replay producer/consumer defect was fixed in `beb794123618c997e82791316df643fc61347665`. Server and client now agree on bare events; tests cover the real shape, reject the obsolete envelope, handle the live/replay race, and reset on epoch change (`tui_gateway/event_replay.py:40-42,78-90`; `apps/shared/src/json-rpc-gateway.ts:552-648`; `apps/shared/src/json-rpc-gateway-replay.test.ts:111-150,198-335`). No bug report survives. |
| 22a | STILL VALID | Fresh authentication, reattach operations, independently initiated replay/resume, absence of a server handoff barrier, and bounded/non-lossless replay remain (`apps/shared/src/json-rpc-gateway.ts:228-240,529-592`; `apps/desktop/src/app/session/hooks/use-route-resume.ts:112-189`; `tui_gateway/methods_session.py:374-1094,1235-1257`). |
| 22b | STALE | Client guidance to decode complete replay envelopes is obsolete; replay now returns bare events. |
| 22c | STALE | The claim that restart generations cannot be distinguished is obsolete; `replay_epoch` now identifies a process generation. |
| 22d | STILL VALID | Sequence alone still cannot prove continuity, and replay cannot recover already evicted or pre-restart events. Transcript reconciliation remains necessary under ambiguity. |
| 23 | STILL VALID | No server-level normative choice spans activate/resume/history/REST for every gap; there is no universal live/replay handoff, sessionless refresh, or cross-session ordering contract (`tui_gateway/methods_session.py:374-1094,1235-1257,2805-2834,3669-3697`; `apps/shared/src/json-rpc-gateway.ts:529-648`). |
| 24 | STILL VALID | Per-session counters/deques, locked insertion, no cross-session sequence, FIFO eviction, and thread-pool RPC completion remain (`tui_gateway/event_replay.py:33-108`; `tui_gateway/server.py:220-379`). |
| 25 | STILL VALID | The 33 ms delta buffer, control-frame drain, single send lock, and one-object-per-`send_text` behavior remain (`tui_gateway/ws.py:46-62,133-180,212-260`). |
| 26a | STILL VALID | No strict total order exists across concurrent producers because replay stamping and transport enqueue use distinct locks; no cross-session sequence exists (`tui_gateway/event_replay.py:64-75`; `tui_gateway/ws.py:152-184,251-260`). |
| 26b | STALE | Replay-returned events and live events parked while replay is in flight no longer dispatch indiscriminately. They are gated against non-increasing sequences (`apps/shared/src/json-rpc-gateway.ts:529-648`). |
| 26c | STILL VALID | Ordinary live frames outside a replay window still call `recordSeq()` and then dispatch even when duplicate, late, or decreasing; only their watermark advancement is suppressed (`apps/shared/src/json-rpc-gateway.ts:475-494,496-515`). |
| 27 | STILL VALID | Replay fields are not one atomic snapshot; independently locked reads allow `latest_seq` to exceed the maximum returned event (`tui_gateway/methods_session.py:3686-3696`; `tui_gateway/event_replay.py:78-108`). |
| 28 | STILL VALID | Common `_block` ID, registration, emit/wait, timeout, expire, and response lookup behavior remains; response identity is still only the short request ID (`tui_gateway/server.py:4547-4626,13810-13837`; `tui_gateway/methods_prompt.py:1586-1656`). |
| 29 | STILL VALID | Clarify single/batch shapes, timeout rules, locked-answer preservation, and `timed_out` behavior remain (`tui_gateway/server.py:4629-4683`; `tools/clarify_gateway.py:539-574`; `tools/clarify_tool.py:255-425`). |
| 30 | STILL VALID | `mcp.setup` payload, response, 600-second bound, and unanswered result remain (`tui_gateway/server.py:8164-8168`; `tools/setup_mcp_tool.py:25-64`; `apps/desktop/src/components/assistant-ui/mcp-setup-tool.tsx:213-234`). |
| 31 | STILL VALID | `preview.act` payload, 45-second wait, and non-cancellation of already-started client work remain (`tui_gateway/server.py:8134-8146`; `tools/drive_preview_tool.py:54-124`; `tools/annotate_preview_tool.py:40-86`). |
| 32 | STILL VALID | `preview.read` normalization, 45-second wait, serialized response, and timeout behavior remain (`tui_gateway/server.py:8124-8132`; `tools/read_preview_tool.py:21-66`). |
| 33 | STILL VALID | Secret request/response, skip-on-empty/timeout, validation flag, and secure save behavior remain (`tools/skills_tool.py:448-484`; `tui_gateway/server.py:8256-8277`). |
| 34 | STILL VALID | Sudo request, 120-second wait, NOPASSWD probing, backend differences, and scoped caching remain (`tui_gateway/server.py:8248-8254`; `tools/terminal_tool.py:247-342,834-858,1025-1115`; `tools/environments/base.py:1493-1550`). |
| 35 | STILL VALID | `terminal.read` normalization, 30-second wait, text reply, and failure result remain (`tui_gateway/server.py:8116-8123`; `tools/read_terminal_tool.py:20-61`). |
| 36 | STILL VALID | Tour's first 10-second probe, later 45-second wait, unavailable latch, and non-cancellation remain (`tui_gateway/server.py:4686-4751`; `tools/tour_tool.py:35-107`). |
| 37 | STILL VALID | `window.read` shape, text-not-pixels result, 30-second wait, and failure result remain (`tui_gateway/server.py:8148-8156`; `tools/read_window_tool.py:18-56`). |
| 38 | STILL VALID | Batch clarify's per-question lock, remaining IDs, final release, cancel-all omission, and timeout preservation remain (`tui_gateway/server.py:4554-4601,4642-4668,13810-13837`). |
| 39 | STILL VALID | Only clarify has a reconnectable `_block` snapshot; the other eight lack a durable pending snapshot, and timeout still does not cancel renderer work (`tui_gateway/server.py:2796-2832,10857-10860`). |
| 40 | STILL VALID | Bundled expiry-consumer asymmetry and the detached-tour comment/code conflict remain (`apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.ts:160-188`; `ui-tui/src/app/createGatewayEventHandler.ts:1284-1292`; `tui_gateway/server.py:4729-4744`). |
| 41 | STILL VALID | Pending response lookup still verifies only the short 32-bit ID, not family/session/transport; no insertion collision check exists. Secret capture remains a process-global callback rewired per turn (`tui_gateway/server.py:4547-4559,13810-13837`; `tools/skills_tool.py:191,262-264,448-466`). |
| 42 | STILL VALID | Process-local live IDs and durable session keys still coexist and can diverge (`tui_gateway/methods_session.py:14-17,77-113,130-161,454-490,606-652,1079-1094`; `tui_gateway/event_replay.py:52-75`). |
| 43 | STILL VALID | `session.create` still allocates both identities, registers memory state, schedules agent construction, and defers database persistence until the first prompt (`tui_gateway/methods_session.py:14-162`). |
| 44 | STILL VALID | `session.activate` still requires a live ID, rebinds it, returns a snapshot, and does not establish global focus or close another session (`tui_gateway/methods_session.py:1197-1257`; `tui_gateway/server.py:10767-10861`). |
| 45 | STILL VALID | `session.resume` still resolves/follows durable identity, reuses or creates live state, varies history fields, and retains the `stored_session_id`/`session_key` schema split (`tui_gateway/methods_session.py:374-1094`). |
| 46 | STILL VALID | `session.close` still atomically removes live state, best-effort tears down resources, returns false when repeated, and does not delete stored history (`tui_gateway/methods_session.py:3128-3137`; `tui_gateway/server.py:882-1051,1087-1178`). |
| 47 | STILL VALID | `session.interrupt` still stops process-global TTS, clears/increments the queue, interrupts the turn, releases `_block`, denies pending approvals, and retains the live record (`tui_gateway/methods_session.py:3356-3375`; `tui_gateway/server.py:1233-1280`). |
| 48 | STILL VALID | `session.branch` still copies eligible visible history into a durable child/new live ID, preserves lineage, and leaves the parent intact (`tui_gateway/methods_session.py:3140-3353`). |
| 49 | STILL VALID | Compression's busy handling, continuation rotation with stable live ID, re-anchoring, queue invalidation, held-lock result, and variable response shapes remain (`tui_gateway/methods_session.py:2884-3053`; `tui_gateway/server.py:6884-7115`). |
| 50 | STILL VALID | Disconnect teardown/viewer rebind/orphan reap, 20-second default grace, zero-grace parking, fresh-auth separation, durable recovery, and loss of never-persisted drafts remain (`tui_gateway/server.py:175-207,1367-1565`; `tui_gateway/methods_session.py:428-490,548-652`). |
| 51 | STILL VALID | Close still lacks interrupt-equivalent `_clear_pending` and approval denial, leaving waiter/approval cleanup unestablished (`tui_gateway/methods_session.py:3128-3137`; `tui_gateway/server.py:1087-1178,1273-1278`). |
| 52 | STILL VALID | Caller close still removes shared live state without an explicit other-viewer invalidation event and remains excluded from `session.reclaimed` (`tui_gateway/server.py:1054-1084`; `tui_gateway/methods_session.py:3128-3137`). |
| 53 | STILL VALID | `session.history` still prefers persisted lineage where available; no universal cache-invalidation transaction spans resume/replay/REST/live events (`tui_gateway/methods_session.py:2805-2834`). |
| 54 | STILL VALID | Stock Hermes still supports observer-only `pre_approval_request` through outbound webhooks; the ordinary gateway path fires it before native notification (`hermes_cli/plugins.py:236-251`; `agent/outbound_webhooks.py:268-340`; `gateway/run.py:13233-13247`; `tools/approval.py:4518-4545`). |
| 55 | STILL VALID | Outbound delivery remains asynchronous, advisory, nondurable, and disabled by safe mode; hook return values cannot decide approval (`agent/outbound_webhooks.py:156-173,380-401,458-501`). |
| 56 | STILL VALID | The exact UTF-8 JSON serialization remains unsorted, default-spaced, `default=str`, and newline-free (`agent/outbound_webhooks.py:404-431`). |
| 57 | STILL VALID | Approval payload promotion/fallback, hook context injection, and `hermes.observer.v1` metadata remain (`agent/outbound_webhooks.py:98-100,404-431`; `tools/approval.py:108-138`; `hermes_cli/plugins.py:5598-5604`; `hermes_cli/middleware.py:17`). |
| 58 | STILL VALID | Approval inputs are redacted before observer dispatch but receive no further outbound sanitization; working directory and hook-supplied strings remain sensitive (`tools/approval.py:3841-3850,5073-5092`; `agent/outbound_webhooks.py:404-431`). |
| 59 | STILL VALID | The ordinary gateway hook still omits the approval queue's `request_id`; other transport paths may add it, so `approval.pending` remains the authoritative reconciliation step (`tools/approval.py:2784-2801,4294-4304,4383-4400,4518-4540`; `tui_gateway/methods_prompt.py:1659-1668,1736-1764`). |
| 60 | STILL VALID | Outbound `session_id` still falls back through parent to empty; nonempty identity is not guaranteed (`agent/outbound_webhooks.py:419-430`). |
| 61 | STILL VALID | Header shapes, raw-body HMAC-SHA-256, UTF-8 secret, and `secret_env` precedence without inline fallback remain (`agent/outbound_webhooks.py:358-373,434-455`). |
| 62 | STILL VALID | HMAC still covers only the body; sender-side freshness, nonce storage, replay rejection, key ID, and rotation metadata remain absent (`agent/outbound_webhooks.py:404-455,520-569`). Upstream's “replay protection for free” wording is a contributable security-documentation defect (`website/docs/user-guide/features/hooks.md:1913-1916`). |
| 63 | STILL VALID | Queue capacity, drop behavior, timeout clamp, two-attempt retry policy, redirect/client-error handling, ignored response, exit flush, and retry identity remain (`agent/outbound_webhooks.py:89-106,221-231,331-340,458-569`). |
| 64 | STILL VALID | Stock Hermes still accepts plain HTTP with a warning (`agent/outbound_webhooks.py:278-293`). |
| 65 | STILL VALID | Generic upstream docs cover the approval hook and outbound wrapper but still omit the ordinary gateway hook's approval-specific outbound shape and missing queue `request_id` (`website/docs/user-guide/features/hooks.md:1271-1302,1843-1927`). A concise augmentation is included in the prepared patch. |

Ipsima-owned transport policy, generator/conformance behavior, capture/sanitizer behavior, and
the original catalog's local provenance in `docs/PROTOCOL.md` are repository implementation facts,
not claims about live upstream HEAD. They were rechecked through the project tests and both exact
catalog regenerations, but are not relabeled as upstream protocol claims here.

## Contribution disposition

The findings that survive as upstream documentation work are:

1. Correct WebSocket framing comments in `tui_gateway/ws.py` and
   `web/src/lib/gatewayClient.ts`.
2. Correct the TUI-gateway emitted-event list: replace `tool.progress` with the actual optional
   `tool.generating` and `tool.output_risk` events. Do not remove compatibility consumers that
   genuinely accept `tool.progress`.
3. Replace automatic replay-protection wording with the accurate requirement for receiver-side
   signed-body/header matching, delivery-ID deduplication, and timestamp freshness enforcement.
4. Add the ordinary gateway approval webhook's missing-`request_id` caveat and the required
   `approval.pending` reconciliation step.
5. Qualify “lossless”/“seamless” replay comments as bounded best-effort recovery; the epoch fixes
   process-generation detection but not ring eviction, non-atomic snapshots, or ignored
   truncation.

The TypeScript replay defect does not survive. No bug report is prepared. The claims Ipsima owns
as errors are the universal tool-lifecycle arrow and the repo-wide absence of `tool.progress`;
both are corrected in `docs/PROTOCOL.md`.
