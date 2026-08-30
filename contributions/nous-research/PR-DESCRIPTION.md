# Draft upstream PR body

Prepared text for a pull request against `NousResearch/hermes-agent` carrying
`documentation-corrections.patch`. Internal claim IDs and Ipsima bookkeeping are deliberately
absent; `HEAD-REVERIFICATION.md` is the evidence base if a maintainer asks for it.

---

**Title:** `docs: correct WebSocket framing, TUI event list, and webhook replay-protection guidance`

## Summary

Five documentation and comment corrections found while building
[Ipsima](https://github.com/Phasmotic/Ipsima), an unfinished native iOS and watchOS client for
Hermes, against the `tui_gateway` protocol (tracking your issue #35966). The client is not
shipping and development has stopped, but the protocol work is what turned these up. Every change is a comment, docstring, or `website/docs` edit — **no
behavioural change, no API change, no test change.**

Each correction cites the source that establishes it, so it can be checked without trusting this
description. Verified against `26350357d76e4508c8df9304a3374bdc5a6f6220`.

## The corrections

**1. WebSocket framing is described as newline-delimited; it is not.**
`tui_gateway/ws.py:10-12` states the wire protocol is "Identical to stdio: newline-delimited
JSON-RPC in both directions... No framing differences", and `web/src/lib/gatewayClient.ts:4-7`
repeats it. In fact each WebSocket text message carries exactly one JSON-RPC object
(`tui_gateway/ws.py:141-145,236-260,420-443`); newline framing is the stdio path only
(`tui_gateway/entry.py:486-502`). A client that follows the comments gets no warning and two
failure modes: framing inbound by delimiter — buffering until a newline, the standard NDJSON reader
idiom — stalls forever, because the server appends no terminator (`tui_gateway/ws.py:145,247,260`);
and joining outbound requests with newlines into a single text message returns `-32700`, because
the server calls `json.loads` on the whole message (`tui_gateway/ws.py:435-443`). The JSON-RPC
*schema* is shared; the *framing* is not. Also
adds one clarifying line to `website/docs/developer-guide/programmatic-integration.md`, which
mentions both transports without distinguishing their framing.

**2. The documented TUI event list names an event the TUI gateway never emits.**
`programmatic-integration.md:75-77` lists `tool.progress` among events streamed back. There is no
`tool.progress` producer anywhere in `tui_gateway/`. The gateway emits `tool.generating`
(`tui_gateway/server.py:8082`) and `tool.output_risk` (`tui_gateway/server.py:7854`), both
conditional. The name is not fictional — the separate REST/SSE API does emit
`tool.progress` (`gateway/platforms/api_server.py:4826-4830,4961`) and `hermes.tool.progress`
(`gateway/platforms/api_server.py:5213,5449,5455`) — so the list appears to have crossed the two
surfaces. The patch corrects the list and states the distinction
explicitly rather than deleting the name. Consumers that accept `tool.progress` for compatibility
are left untouched — including `ui-tui/README.md:285`, which tables it with a `{ name, preview }`
payload as the Ink client's accept-list.

**3. Webhook docs promise replay protection that receivers must actually implement.**
`hooks.md:1913-1916` says that because `delivery_id` and `timestamp` are inside the signed body, "a
verified receiver also gets replay protection for free." The HMAC covers the body only
(`agent/outbound_webhooks.py:404-455`); there is no sender-side freshness, nonce store, or replay
rejection. A receiver gets *authenticated inputs* with which to implement replay protection, which
is not the same thing — and the surrounding text offers the unsigned, attacker-controllable
`X-Hermes-Delivery` header as an equivalent dedupe key. The patch keeps the existing dedupe and
freshness advice, moves it from "free" to "required", and adds the header-to-signed-body
comparison step. This is the one correction with a security consequence, so the replacement prose
is worth seeing without opening the diff:

> **Before —** Because `delivery_id` and `timestamp` live **inside the signed body**, a verified
> receiver also gets replay protection for free:
>
> **After —** Because `delivery_id` and `timestamp` live **inside the signed body**, a verified
> receiver has authenticated inputs with which to implement replay protection. Replay protection
> is not automatic; the receiver must:
>
> - **Match the headers to the signed body** — require `X-Hermes-Event` to equal the authenticated
>   body's `hook_event_name` and `X-Hermes-Delivery` to equal its `delivery_id`; the HMAC covers
>   the body, not the headers.

**4. The `pre_approval_request` webhook lacks the field a responder needs.**
The ordinary gateway approval path puts approval context under `extra` but omits the approval
queue's `request_id` (`tools/approval.py:4518-4540`; `tui_gateway/methods_prompt.py:1659-1668`).
A consumer cannot answer *that* approval from the webhook payload alone: with no `request_id`,
`approval.respond` resolves the session's oldest pending approval
(`tools/approval.py:2846-2859`), which is the right one only when exactly one is queued. The
patch documents treating the webhook as a notification and calling `approval.pending` for the
authoritative record first.

**5. "Lossless" and "seamless" replay wording overstates a bounded ring.**
Comments in `apps/shared/src/json-rpc-gateway.ts:111`, `tui_gateway/event_replay.py:3-8`, and
`tui_gateway/server.py:2489-2495` describe reconnect replay as lossless or seamless. The ring is
bounded at 512 events per session across at most 64 remembered sessions, with FIFO session
eviction, and is reset by restart
(`tui_gateway/event_replay.py:33-75`). `replay_epoch` correctly identifies a process generation,
but does not recover evicted or pre-restart events, and an unknown or evicted ring is
indistinguishable from a session that never emitted one. The patch qualifies the wording as
bounded best-effort recovery and notes that clients must reconcile authoritative history when the
ring cannot prove a complete gap recovery.

## Provenance

The protocol surface was derived programmatically from Hermes source at a pinned commit, then
re-derived and re-verified against the HEAD above before this was prepared; claims that had gone
stale in between were dropped rather than filed. Two findings were withdrawn as our own errors
rather than upstream defects.

The SHA above is a commit on `main` as of 2026-08-30. The full claim-by-claim record, including everything we
dropped, is in
[`HEAD-REVERIFICATION.md`](https://github.com/Phasmotic/Ipsima/blob/main/contributions/nous-research/HEAD-REVERIFICATION.md).

These corrections were prepared with AI assistance and reviewed against source line by line. Happy
to split this up — correction 3 stands alone if you would rather land the security wording first —
or to adjust any wording to house style. If you would rather treat correction 4 as a missing field
than a documented caveat, say so and I will drop that hunk and file it as an issue instead.

I am not developing the client further, but I will answer review comments on this PR.
