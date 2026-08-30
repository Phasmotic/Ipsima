# Draft catalog offer

**Status: not filed.** A standing, declinable offer. It is independent of the documentation
corrections — declining this has no bearing on those.

We independently derived a machine-readable catalog of the Hermes TUI-gateway JSON-RPC request and
event identities and the REST method/path surface, from exact commit
`26350357d76e4508c8df9304a3374bdc5a6f6220`. The catalog records its source commit, the SHA-256 of
every input blob, source locations for each identity, separate request/event namespaces that
preserve the `session.title` and `session.usage` collisions, and transport/authentication
summaries. The generator reads Git objects rather than working-tree files, so it cannot be fooled
by uncommitted edits, and `--check` fails on any byte-level regeneration drift.

Both the catalog and `scripts/derive_protocol.py` are MIT-licensed and available if useful.

## What it would cost you to adopt

Being direct about the parts that do not fit, because they are the parts that matter:

**The exact-count ratchet is wrong for you as it stands.** The generator blocks when the derived
counts differ from an expected triple, and deriving against any revision other than its pin
*requires* passing counts explicitly (`derive_protocol.py:267,421`). That is the correct design for
a downstream consumer pinning a contract it must not silently drift from — it is what makes the
catalog trustworthy for us. It is the wrong default for the project that legitimately adds methods:
in your CI it would fail the first time someone adds an RPC. Adoption means making the counts
reported-and-diffed rather than blocking, or ratcheting only on unexpected *removal*. That is a
small change to one function, but it is a real change and it should be yours to make, not a
surprise you discover.

**Nobody is maintaining this on our side.** Client development has stopped. The tool works and is
tested, but treat it as code to take ownership of, not a dependency with an upstream.

**It reflects our reading of your source.** The derivation is mechanical, but the choice of what
counts as an identity is a judgement we made from outside.

## What it costs less than you might expect

No third-party dependencies — Python 3 standard library only, no install step, no lockfile. Reading
Git objects means it needs a checkout and nothing else. It is currently exercised by 32 tests.

The plausible value is regression detection: a protocol identity disappearing or changing shape
between releases shows up as a diff rather than as a broken client someone reports later. If that
is not a problem you have, this is not worth your time, and we would rather you said so.

We would adapt the location, output shape, or gate behaviour to fit the upstream tree.
