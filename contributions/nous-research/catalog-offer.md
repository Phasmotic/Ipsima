# Draft catalog offer

**Status: not filed.** A standing, declinable offer, independent of the documentation
corrections — declining this has no bearing on those.

We independently derived a machine-readable catalog of the Hermes TUI-gateway JSON-RPC request and
event identities and the REST method/path surface, from exact commit
`26350357d76e4508c8df9304a3374bdc5a6f6220`. The catalog records its source commit, the SHA-256 of
every input blob, source locations for each identity, separate request/event namespaces that
preserve the `session.title` and `session.usage` collisions, and transport/authentication
summaries. The generator reads Git objects rather than working-tree files, so it cannot be fooled
by uncommitted edits, and `--check` fails on any byte-level regeneration drift.

Both the catalog and `scripts/derive_protocol.py` are MIT-licensed and available if useful.
`scripts/PROTOCOL_CATALOG.md` documents what it does and does not cover.

## What it costs to adopt

**Nobody is maintaining this on our side.** Client development has stopped. The tool works and is
tested, but treat it as code to take ownership of, not a dependency with an upstream.

**A call-site extractor can only ever miss.** It scans for literal event and method names, so its
counts are a floor on your protocol surface, not a ceiling. We know of three shapes it cannot
reach, all listed in `PROTOCOL_CATALOG.md`: names dispatched through a variable rather than a
literal, the `subagent.*` family that originates outside the declared input set, and nine `.expire`
events that are synthesised from the `_block` families rather than observed.

We know that concretely because it bit us. Until 2026-08-30 the extractor matched only `_emit(`
call sites and could not see `_voice_emit(` or `_broadcast_global_event(`, so five real events —
`skin.changed`, `session.reclaimed`, `voice.status`, `voice.transcript`, `voice.interrupted` —
were missing from every catalog we had produced. It is fixed, the catalogs are regenerated, and
there is now a test that runs the extractor against a real Hermes checkout rather than against
fixtures. If you adopt this, keep that test: it is the only thing that would have caught it.

**It reflects our reading of your source.** The derivation is mechanical, but the choice of what
constitutes an identity is a judgement made from outside your team.

## What it does not cost

No third-party dependencies — Python 3 standard library only, no install step, no lockfile.
Reading Git objects means it needs a checkout and nothing else. 33 tests.

The parts that were built for a downstream consumer are now opt-in rather than default, so it
should behave sensibly in your hands out of the box:

- **The exact-count ratchet is off unless you ask for it.** It exists for a consumer pinning a
  contract that must not drift; upstream adding a method is normal, so `--expected-counts` is
  opt-in and omitting it reports the counts instead of failing.
- **Any revision expression works** — a tag, a branch, `HEAD`, an abbreviated SHA — resolved to an
  immutable commit before anything is read.
- **Any clone works.** Scp-style, SSH and HTTPS spellings of the same repository compare equal, and
  `--expect-origin` points it at a fork.
- **One command reports a revision** without writing anything:
  `derive_protocol.py <checkout> --revision HEAD`.

The plausible value is regression detection: an identity disappearing or changing shape between
releases shows up as a diff rather than as a broken client someone reports later. If that is not a
problem you have, this is not worth your time, and we would rather you said so.

We would adapt the location, output shape, or gate behaviour to fit the upstream tree.
