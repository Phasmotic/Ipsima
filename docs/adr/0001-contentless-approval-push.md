# ADR-0001: Contentless approval push through a privacy bridge

- Status: Accepted architecture boundary; P4 implementation is not authorized
- Date: 2026-08-27
- Source baseline: Hermes `e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246`
- Implementation gate: accept the P4 operational threat model before code

## Context

Talaria must alert a user to a pending approval while the app is suspended, without requiring a
Hermes fork and without disclosing command text to the operator of Talaria's APNs relay. The app
must fetch all approval details from the user's gateway over TLS. Hermes must not store or handle
APNs device tokens, while the bundle-bound APNs signing key must remain outside Hermes.

Pinned source establishes that stock Hermes can send `pre_approval_request` through
`hooks.outbound`. It also establishes a privacy constraint: the raw webhook body includes the
entire hook-supplied command and description after surface/configuration redaction, internal
session context, and the sender process's working directory. The command can still contain
sensitive raw text. HMAC authenticates that plaintext; it does not encrypt it. A direct
stock-Hermes webhook to a hosted APNs relay would therefore expose command text to that relay and
cannot satisfy the privacy claim merely by making the later APNs payload contentless.

The exact source-derived webhook contract and its delivery limits are recorded in
`docs/PROTOCOL.md`. In particular, ordinary gateway approval hooks do not carry the queue's
authoritative `request_id`, and delivery is best-effort through a bounded nondurable queue. Push
must remain a wakeup hint followed by gateway reconciliation, never the source of approval state.

## Decision

Use stock Hermes with two distinct relay trust zones:

1. A **user-controlled gateway-side privacy bridge** receives the raw Hermes webhook.
2. A **Talaria APNs delivery relay** receives only a minimal opaque wake request and holds the
   bundle-bound APNs signing key.

These are separate components and separate trust boundaries. The hosted APNs relay does not
accept raw Hermes lifecycle webhooks.

### Registration

Talaria registers its APNs device token directly with the APNs delivery relay over TLS. The relay
stores the token against a subscription identifier generated from 32 CSPRNG bytes and encoded as
43 unpadded base64url characters. It returns that identifier plus a scoped 32-byte CSPRNG bridge
authentication key. Talaria independently creates a 32-byte CSPRNG session-alias key. Both keys
are unique per subscription and gateway profile, are never reused across profiles, and are stored
in the Keychain. Hermes never receives the device token, either key, or the APNs signing key.

The user configures stock Hermes `hooks.outbound` to send `pre_approval_request` to the
gateway-side privacy bridge over HTTPS. The Hermes target must use an HMAC secret; unsigned
delivery is unsupported. During P4 setup, Talaria transfers the subscription identifier, scoped
bridge authentication key, and session-alias key to the user-controlled bridge through the
approved pairing boundary. The bridge never receives the APNs device token.

### Gateway-side verification and minimization

The privacy bridge performs this sequence before forwarding anything:

1. Require exactly one non-coalesced instance of each security-relevant header: `Content-Type`,
   `X-Hermes-Event`, `X-Hermes-Delivery`, and `X-Hermes-Signature-256`. Reject a missing or
   non-JSON content type, a comma-coalesced security header, and any raw body larger than one
   mebibyte. Read accepted body bytes without normalizing JSON and never place them in access,
   debug, or error logs.
2. Require `X-Hermes-Signature-256` in `sha256=<64 lowercase hexadecimal characters>` form and
   verify HMAC-SHA-256 over the raw bytes using a constant-time comparison.
3. Parse the authenticated JSON with duplicate-key rejection and require exactly the pinned
   top-level field set documented in `docs/PROTOCOL.md`.
4. Require the authenticated body field `hook_event_name` and the unsigned `X-Hermes-Event`
   header both to equal `pre_approval_request`. Require `extra.surface == "gateway"` and
   `extra.telemetry_schema_version == "hermes.observer.v1"`. The header alone is never trusted;
   CLI, smart, and approval-transport surfaces are not mobile gateway wakeups.
5. Require `delivery_id` in the authenticated body and `X-Hermes-Delivery` header to be the same
   32-character lowercase hexadecimal value.
6. Parse `timestamp` as strict UTC ISO-8601 ending in `Z`. Accept it only from five minutes in the
   past through 30 seconds in the future according to a trusted clock.
7. Atomically claim the pair of Hermes target and delivery ID in a durable replay store before
   forwarding, with `pending` and `delivered` states retained for at least ten minutes. Store only
   the minimized relay request described below, never the raw Hermes body. A duplicate of a
   delivered request gets idempotent success; a duplicate while pending re-drives that same
   minimized request. Unavailable clock or replay state fails closed.
8. Require a nonempty Hermes `session_id`; source does not guarantee one, and `extra.session_key`
   is forbidden as a fallback. Compute the domain-separated alias
   `base64url_no_padding(HMAC-SHA-256(session_alias_key, "talaria.session-alias.v1" || 0x00 ||
   UTF8(subscription_id) || 0x00 || UTF8(session_id)))`, which is exactly 43 characters. The raw
   Hermes identifier never leaves the bridge.
9. Discard the command, description, working directory, pattern data, raw session identifier, and
   all remaining webhook bytes. They are never forwarded, persisted, logged, included in metrics,
   or attached to an error report.

If authentication, freshness, identity, or minimization is indeterminate, the bridge rejects the
request. Permanent authentication, schema, surface, identity, or staleness failures return 4xx;
unavailable trusted time, replay storage, or hosted relay returns 5xx. The bridge returns 2xx only
after the hosted relay accepts the minimized request, except that an authenticated duplicate
already marked delivered returns 2xx without forwarding. Hermes ignores response bodies, retries
network and 5xx failures once, and does not retry 3xx or 4xx. The bridge never forwards a degraded
or unsigned wakeup.

### Closed bridge-to-relay request

The privacy bridge sends one UTF-8 JSON object serialized with exactly these sorted keys, compact
separators, and no trailing newline:

```json
{"delivery_id":"<32 lowercase hex>","sent_at":"<validated UTC timestamp>","session_alias":"<43 base64url characters>","subscription_id":"<43 base64url characters>"}
```

The body is limited to 512 bytes and unknown, missing, duplicate, or wrongly typed fields are
rejected. The ingress also requires exactly one non-coalesced occurrence of each security header;
duplicates and comma-coalesced values are rejected before authentication. Its only application
headers are `Content-Type: application/json`,
`X-Talaria-Delivery: <delivery_id>`, and
`X-Talaria-Signature-256: sha256=<64 lowercase hexadecimal characters>`. The signature is
HMAC-SHA-256 over the exact raw body using the subscription's scoped bridge authentication key.
TLS is mandatory.

The hosted relay verifies the signature in constant time, cross-checks the header delivery ID,
applies the same timestamp bounds, and atomically claims `(subscription_id, delivery_id)` with
durable `pending` and `sent` states retained for at least ten minutes. A delivered duplicate gets
idempotent success; a pending duplicate re-drives the same APNs wake. A crash after APNs accepts a
request but before the relay marks it sent can still produce a duplicate, so the app reconciliation
path must remain idempotent. Unknown subscriptions, unavailable replay state or trusted time,
oversized bodies, schema violations, and authentication failures are rejected. Permanent
authentication, schema, identity, or staleness failures return 4xx; unavailable trusted time,
replay state, relay storage, or APNs returns 5xx. The entire ingress disables request-body logging;
bounded diagnostics contain reason codes only.

The subscription and session alias formats are closed and fixed-size. The hosted relay therefore
rejects accidental command, approval-description, tool-input, working-directory, model-selection,
Hermes-credential, raw-session, and extension fields. The relay cannot prove that an opaque
43-character value was derived correctly because it does not possess the alias key. The
user-controlled bridge is therefore a trusted minimizer; a buggy or compromised bridge could use
an opaque field as a covert channel even though the schema prevents ordinary free-text leakage.

### Exact APNs wake

After resolving the encrypted-at-rest APNs device token, the relay sends exactly this JSON payload:

```json
{"aps":{"content-available":1},"session_alias":"<43 base64url characters>"}
```

Beyond APNs-required provider authentication and transport metadata, the allowed
application-controlled APNs headers are `apns-push-type: background`,
`apns-priority: 5`, the registered Talaria bundle identifier as `apns-topic`, and an
`apns-expiration` no later than five minutes after the validated `sent_at`. The relay does not send
an alert, title, subtitle, sound, badge, category, thread identifier, mutable-content flag, or any
other custom payload key. If the wake is already expired, it is not sent.

The deterministic alias lets the hosted relay and APNs correlate multiple wakeups for the same
session within one subscription, although the unique per-profile key prevents cross-subscription
alias equality. P4's threat model must explicitly accept that metadata leakage or amend this ADR
with an app-decryptable randomized alias that preserves the same closed payload shape.

Raw bridge or APNs bodies are never persisted or logged. Delivery replay records contain only a
subscription reference, delivery ID, and expiry and are deleted after ten minutes. The session
alias is discarded after APNs accepts or rejects the request. APNs device tokens and provider
credentials are encrypted separately at rest, as are scoped bridge authentication keys; tokens
and scoped keys are deleted on app deregistration or provider invalidation.

Before P4 code, a project threat model must fix rate limits, registration expiry, abuse controls,
credential rotation and recovery, bridge packaging, deletion verification, bound-session coverage,
and whether deterministic alias linkability is acceptable. That gate may tighten these contracts
but cannot add payload fields or move sensitive data across the hosted relay boundary without an
explicit ADR revision.

### App wake and reconciliation

On a background wake, Talaria recomputes aliases for its local sessions to map `session_alias` to
the registered gateway profile, connects to that gateway over its established TLS/TOFU trust
boundary, and calls `approval.pending`. The returned pending record supplies the authoritative
full command,
description, and `request_id`. Only that fetched state may populate the approval UI or be
submitted through `approval.respond`.

Zero alias matches, multiple alias matches, or an absent bound Hermes session ID fail closed and
produce no actionable notification. Background and foreground reconciliation still query the
gateway, so a source event without a usable session ID becomes a missed hint rather than a false
approval.

The app displays no actionable approval when reconciliation fails or the request is no longer
pending. Deny remains the safe default. Background refresh and every foreground transition also
reconcile pending approvals because a webhook can be dropped, delayed, duplicated, disabled by
safe mode, or lost during process shutdown.

## Security properties

- Stock Hermes is unchanged; `hooks.outbound` is the only Hermes integration.
- Hermes handles neither APNs device tokens nor the APNs signing key.
- With the user-controlled bridge operating as the trusted minimizer, the APNs delivery relay
  receives no command field or raw command bytes. A compromised bridge is outside this guarantee.
- APNs receives only the required background `aps` member and one opaque session alias, not
  approval content.
- HMAC protects the Hermes-to-bridge hop; separate scoped authentication protects the
  bridge-to-relay hop. TLS is mandatory on both hops.
- A push cannot approve or deny anything. Gateway state fetched over TLS remains authoritative.
- Duplicate, late, or missing pushes are safe because reconciliation is idempotent and approvals
  use the gateway-issued `request_id`.

## Consequences

The design works against stock Hermes and needs no upstream patch. It adds a small user-controlled
bridge beside the gateway; that is the cost of making the “relay operator never sees command
text” property architectural rather than policy-based. The bridge must be packaged and configured
as part of P4. P2 pairing may define an extension point for later bridge material, but registration
and transfer are implemented only in P4 after its pre-code gates.

Push remains advisory because Hermes delivery is nondurable and can drop events. Live Activity,
notifications, and widgets must tolerate stale or absent wakeups and refresh from the gateway.

Upstream already documents generic approval hooks and outbound webhooks, but no single section
combines the approval-specific body with the absent ordinary-gateway queue `request_id`. No
upstream issue, pull request, fork, or patch is required or authorized. Any later documentation or
adoption proposal belongs to hard-gated P7 and requires explicit owner authorization.

## Rejected alternatives

- **Send stock Hermes directly to the hosted APNs relay.** Rejected because the raw webhook
  contains the hook-supplied command—which can include sensitive unredacted text—and
  working-directory data; contentless APNs does not remove that ingress exposure.
- **Put APNs device tokens or signing logic in Hermes.** Rejected because device registration and
  the bundle-bound signing key are Talaria infrastructure, not a generic Hermes responsibility.
- **Patch Hermes to emit a reduced approval webhook.** Rejected because stock Hermes already
  exposes the event and a separate bridge provides minimization without an upstream dependency.
- **Treat webhook delivery as durable approval state.** Rejected because the sender queue is
  bounded and nondurable, ordinary approval webhooks lack the authoritative `request_id`, and
  retries are best-effort.

## Revalidation

Any change to the pinned Hermes commit requires re-deriving the following before relying on this
ADR: valid-hook membership, approval call sites, body and header serialization, secret resolution,
retry semantics, and `approval.pending`/`approval.respond`. A change that removes the command from
the raw webhook may simplify the bridge, but it does not silently authorize a direct hosted-relay
path; this ADR must be revised explicitly.
