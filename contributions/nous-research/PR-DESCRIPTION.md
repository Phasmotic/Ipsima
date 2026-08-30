# Draft upstream PR body

Prepared text for a pull request against `NousResearch/hermes-agent` carrying
`documentation-corrections.patch`, laid out to match their
`.github/PULL_REQUEST_TEMPLATE.md`. Internal claim IDs and Ipsima bookkeeping are deliberately
absent; `HEAD-REVERIFICATION.md` is the evidence base if a maintainer asks for it.

**Boxes marked `← you` are attestations only the filer can honestly make.** Do not check them
without doing them.

---

**Title:** `docs: correct WebSocket framing, TUI event list, and webhook replay-protection guidance`

## What does this PR do?

Five documentation and comment corrections found while building
[Ipsima](https://github.com/Phasmotic/Ipsima), an unfinished native iOS and watchOS client for
Hermes, against the `tui_gateway` protocol (tracking issue #35966). The client is not shipping and
development has stopped, but the protocol work is what turned these up.

Every change is a comment, docstring, or `website/docs` edit — **no behavioural change, no API
change, no test change.** Each correction cites the source that establishes it, so it can be
checked without trusting this description. Verified against `4f22543509d1b91dc45bcb369447126c5eb14fb7`, the base of this branch; every
line reference below is relative to it.

**1. WebSocket framing is described as newline-delimited; it is not.**
`tui_gateway/ws.py:10-12` states the wire protocol is "Identical to stdio: newline-delimited
JSON-RPC in both directions... No framing differences", and `web/src/lib/gatewayClient.ts:4-7`
repeats it. In fact each WebSocket text message carries exactly one JSON-RPC object
(`tui_gateway/ws.py:141-145,236-260,420-443`); newline framing is the stdio path only
(`tui_gateway/entry.py:486-502`). A client that follows the comments gets no warning and two
failure modes: framing inbound by delimiter — buffering until a newline, the standard NDJSON
reader idiom — stalls forever, because the server appends no terminator
(`tui_gateway/ws.py:145,247,260`); and joining outbound requests with newlines into a single text
message returns `-32700`, because the server calls `json.loads` on the whole message
(`tui_gateway/ws.py:435-443`). The JSON-RPC *schema* is shared; the *framing* is not.

**2. The documented TUI event list names an event the TUI gateway never emits.**
`website/docs/developer-guide/programmatic-integration.md:75-77` lists `tool.progress` among
events streamed back. There is no `tool.progress` producer anywhere in `tui_gateway/`. The gateway
emits `tool.generating` (`tui_gateway/server.py:8106`) and `tool.output_risk`
(`tui_gateway/server.py:7878`), both conditional. The name is not fictional — the separate REST/SSE
API does emit `tool.progress` (`gateway/platforms/api_server.py:4826-4830,4961`) and
`hermes.tool.progress` (`gateway/platforms/api_server.py:5213,5449,5455`) — so the list appears to
have crossed the two surfaces. The patch corrects the list and states the distinction explicitly
rather than deleting the name. Consumers that accept `tool.progress` for compatibility are left
untouched — including `ui-tui/README.md:285`, which tables it with a `{ name, preview }` payload as
the Ink client's accept-list.

**3. Webhook docs promise replay protection that receivers must actually implement.**
`website/docs/user-guide/features/hooks.md:1913-1916` says that because `delivery_id` and
`timestamp` are inside the signed body, "a verified receiver also gets replay protection for free."
The HMAC covers the body only (`agent/outbound_webhooks.py:404-455`); there is no sender-side
freshness, nonce store, or replay rejection. A receiver gets *authenticated inputs* with which to
implement replay protection, which is not the same thing — and the surrounding text offers the
unsigned, attacker-controllable `X-Hermes-Delivery` header as an equivalent dedupe key. This is the
one correction with a security consequence, so the replacement prose is worth seeing without
opening the diff:

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
queue's `request_id` (`tools/approval.py:4606-4616`; `tui_gateway/methods_prompt.py:1659-1668`). A
consumer cannot answer *that* approval from the webhook payload alone: with no `request_id`,
`approval.respond` resolves the session's oldest pending approval
(`tools/approval.py:2865-2895`), which is the right one only when exactly one is queued. The patch
documents treating the webhook as a notification and calling `approval.pending` for the
authoritative record first.

**5. "Lossless" and "seamless" replay wording overstates a bounded ring.**
Comments in `apps/shared/src/json-rpc-gateway.ts:111`, `tui_gateway/event_replay.py:3-8`, and
`tui_gateway/server.py:2489-2495` describe reconnect replay as lossless or seamless. The ring is
bounded at 512 events per session across at most 64 remembered sessions, with FIFO session
eviction, and is reset by restart (`tui_gateway/event_replay.py:33-75`). `replay_epoch` correctly
identifies a process generation, but does not recover evicted or pre-restart events, and an unknown
or evicted ring is indistinguishable from a session that never emitted one. The patch qualifies the
wording as bounded best-effort recovery.

## Related Issue

No existing issue — these were found while building against the gateway. Happy to open one first
if you would prefer that order.

## Type of Change

- [x] 📝 Documentation update

## Changes Made

Comments and docstrings:

- `tui_gateway/ws.py` — corrects the "Wire protocol" header's framing claim
- `web/src/lib/gatewayClient.ts` — same correction on the browser client
- `tui_gateway/event_replay.py`, `tui_gateway/server.py`, `apps/shared/src/json-rpc-gateway.ts` —
  qualify "lossless"/"seamless" replay as bounded best-effort
- `agent/outbound_webhooks.py` — corrects the `_serialize_payload` docstring's replay-protection
  claim

Documentation:

- `website/docs/developer-guide/programmatic-integration.md` — adds one framing sentence; corrects
  the streamed-events list
- `website/docs/user-guide/features/hooks.md` — corrects the replay-protection guidance; adds the
  `pre_approval_request` reconciliation caveat

8 files, +31/−17, no executable line changed.

## How to Test

1. The branch is based on current `main` (`4f22543`) and needs no rebase.
2. Spot-check any citation above against your own tree; each names a file and line range.
3. `grep -rn "tool\.progress" tui_gateway/` returns nothing, which is correction 2 in one command.
4. Nothing to run: no executable line changes, so behaviour and tests are untouched.

## Checklist

### Code

- [ ] ← you: I've read the Contributing Guide
- [ ] ← you: commit messages follow Conventional Commits
- [ ] ← you: searched existing PRs for duplicates
- [x] My PR contains **only** changes related to this fix (no unrelated commits)
- [ ] ← you: `pytest tests/ -q` passes
- [x] N/A — tests: no executable line is changed, so there is no behaviour to cover
- [ ] ← you: platform tested

### Documentation & Housekeeping

- [x] This PR *is* the documentation update
- [x] N/A — no config keys added or changed
- [x] N/A — no architecture or workflow change
- [x] N/A — comment and prose only, no cross-platform surface
- [x] N/A — no tool behaviour, description, or schema changed

## Notes

The protocol surface was derived programmatically from Hermes source at a pinned commit, then
re-derived and re-verified against the SHA above — a commit on `main` as of 2026-08-30 — before
this was prepared; claims that had gone stale in between were dropped rather than filed. Two
findings were withdrawn as our own errors rather than upstream defects. The full claim-by-claim
record, including everything dropped, is in
[`HEAD-REVERIFICATION.md`](https://github.com/Phasmotic/Ipsima/blob/main/contributions/nous-research/HEAD-REVERIFICATION.md).

These corrections were prepared with AI assistance and reviewed against source line by line. Happy
to split this up — correction 3 stands alone if you would rather land the security wording first —
or to adjust any wording to house style. If you would rather treat correction 4 as a missing field
than a documented caveat, say so and I will drop that hunk and file it as an issue instead.

I am not developing the client further, but I will answer review comments on this PR, and I am
happy to keep going on any of it if it is useful to you. The client was paused on a judgement
about where the effort was best spent, not on losing interest — so if you would want the derived
protocol catalog and its generator upstream, a native Apple-platform client against
`tui_gateway`, or simply more corrections of this kind as we find them, say so and I will pick it
back up. `contributions/nous-research/catalog-offer.md` in the linked repository describes the
catalog and, more usefully, what adopting it would actually cost you.
