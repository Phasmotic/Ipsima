# Golden fixture resources

`golden.jsonl` is a real capture from an isolated Hermes gateway, not a synthetic protocol
example. The harness binds the runtime to the public Hermes repository and commit recorded in
`protocol/methods.json`; the current fixture was captured from commit
`e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246`.
It also verifies every pinned source-input digest and the locked Hermes `0.20.5` environment
before starting the gateway.

## Scope

The fixture deliberately contains three canonical JSON-RPC frames:

1. the gateway-ready event;
2. one client ping request; and
3. its successful server response.

This narrow transcript proves real transport framing and codec compatibility. Catalog-wide
request and event coverage comes from the generated conformance tests; the fixture does not
pretend to be a complete session or streaming transcript.

## Capture contract

The harness runs only on Linux. On this machine, enter Ubuntu through the supported
PowerShell-launched WSL path and run from the Ipsima repository root. Prerequisites are:

- a clean Hermes checkout at `.gauntlet/hermes-capture-src` whose origin, commit, and pinned
  source bytes match `protocol/methods.json`;
- exactly one checkout-local virtual environment containing the Hermes launcher and Python;
- `uv 0.11.7` on `PATH`, with the locked `web` environment already available for an offline,
  frozen verification; and
- no checkout-local `.env` file or ambient credential forwarding.

With the checkout-local environment in `.venv`, regenerate in the foreground with:

```bash
.gauntlet/hermes-capture-src/.venv/bin/python -B scripts/capture_golden.py \
  --hermes-root .gauntlet/hermes-capture-src
```

The gateway receives a private home, configuration, cache, state, and working directory. It
uses an ephemeral loopback endpoint, proves process ownership before capture, and leaves every
pre-existing gateway process untouched. Raw frames remain in memory only. Gateway output stays
in a permission-restricted temporary directory, is never written into the repository, and is
removed on every handled exit.

Before the tracked file is replaced, the harness recursively removes captured field names,
redacts text, normalizes scalar values, aliases identifiers, preserves only catalog-approved
protocol control strings, and emits sorted compact UTF-8 JSON with one frame per line. The write
is atomic and occurs only after the same residual safety and canonical-form checks enforced by
G3 pass.

## Maintenance

Never hand-edit `golden.jsonl` and never substitute a fabricated frame. Regenerate it with the
capture command, review only the sanitized diff, and run the gauntlet. G3 rejects missing,
malformed, noncanonical, manually unsanitized, or catalog-drifting fixtures; SwiftPM then loads
this tracked resource through `Bundle.module` and proves decode/re-encode byte identity.
