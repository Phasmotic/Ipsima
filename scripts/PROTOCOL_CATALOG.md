# The Hermes protocol catalog

`derive_protocol.py` mechanically derives a machine-readable catalog of the Hermes TUI-gateway
JSON-RPC surface by reading Git objects from a Hermes checkout. It is MIT-licensed and has **no
third-party dependencies** — Python standard library only, no install step, no lockfile.
Developed and tested on CPython 3.12.

## What it is, and what it is not

It is an **identity and provenance** catalog: which request and event names exist, where in the
source each one is established, and which exact blobs the answer was derived from. Every input is
recorded with its SHA-256, and regeneration must reproduce the output byte for byte.

It is **not** a payload schema. It does not describe the shape of any request's parameters or any
event's body. A name appearing here means the source establishes that identity, nothing more.

Two fields are worth calling out because they are not derived: `framing` and `auth` are maintained
prose. They are held true by a set of source guards that fail the derivation if the behaviour they
describe stops matching the source, but the sentences themselves are written, not extracted.

## Running it

```bash
# Report what a revision contains. Writes nothing.
python3 scripts/derive_protocol.py /path/to/hermes-agent --revision HEAD

# Write a catalog for a specific revision.
python3 scripts/derive_protocol.py /path/to/hermes-agent \
    --revision v1.2.3 --output protocol/methods-v1.2.3.json

# Verify a committed catalog still regenerates byte-identically.
python3 scripts/derive_protocol.py /path/to/hermes-agent \
    --revision <sha> --output protocol/methods-<sha>.json --check
```

Exit status is a contract: **0** the catalog was written or is byte-identical, **1** `--check`
found drift, **2** the source or output evidence could not be evaluated reliably. A `2` always
means "could not decide", never "decided against".

The checkout is only an object database. Source is read with `git show <commit>:<path>`, so a
dirty worktree, a different checked-out branch, or a moving branch cannot change the result.

## If you are adopting this

Two behaviours exist for a downstream consumer pinning a contract, and are **not** defaults:

- **`--expected-counts`** fails unless the derived counts match exactly. That is right for a
  consumer that must not drift silently, and wrong for the project that legitimately adds methods.
  It is opt-in; omitting it reports the counts instead.
- **`--expect-origin`** defaults to the canonical Hermes repository. Point it at a fork to derive
  from one. Scp-style, SSH, and HTTPS spellings all compare equal; unauthenticated transports
  deliberately do not.

The useful signal for an upstream owner is regression detection: derive per release, commit the
output, and let review show identities appearing and disappearing. An identity vanishing is the
event worth a gate.

## Known coverage limits

The extractor scans call sites for literal names. That is auditable and cheap, and it has edges.
These are the ones we know about:

- **Events dispatched through a variable are invisible.** `tui_gateway/server.py` has five such
  sites, including `_broadcast_global_event(event, payload_fn())` and `_emit(event, sid, payload)`,
  where the name arrives from a caller. No call-site scan can resolve these.
- **`subagent.*` events are absent.** They originate as literals in `tools/delegate_tool.py`
  (`_relay("subagent.start", ...)`) and reach clients through a variable, and that file is outside
  the declared input set.
- **Nine `.expire` events are synthesised**, not observed. They are derived from the nine
  timeout-backed `_block` request families rather than found at an emit site, so they carry no
  source location.
- **REST coverage is the routes the derivation can attribute**, not every route registered in
  `api_server.py`.

A concrete demonstration of why this list matters: until this catalog was corrected, five real
events — `skin.changed`, `session.reclaimed`, `voice.status`, `voice.transcript`,
`voice.interrupted` — were missing, because the original pattern matched only `_emit(` and could
not see `_voice_emit(` or `_broadcast_global_event(`. Thirty-two green tests never noticed, because
none of them ran the extractor against real Hermes source. `RealSourceExtractionTests` now does;
set `HERMES_CHECKOUT` to a Hermes clone to run it.

Treat the catalog as a floor on the protocol surface, not a ceiling.
