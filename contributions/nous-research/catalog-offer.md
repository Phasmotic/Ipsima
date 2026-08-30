# Draft catalog offer

We independently derived a machine-readable catalog of the Hermes TUI-gateway JSON-RPC request
and event identities and the REST method/path surface from exact commit
`26350357d76e4508c8df9304a3374bdc5a6f6220`.

The catalog records its immutable source commit, the SHA-256 of every input blob, source locations
for each identity, separate request/event namespaces that preserve collisions, and
transport/authentication summaries. The generator reads Git objects rather than mutable checkout
files and blocks on origin, revision, input-manifest, or exact-count mismatch; in `--check` mode it
also fails on any byte-level regeneration drift. Both the catalog and `scripts/derive_protocol.py`
are available under the MIT license if they would be useful to Hermes maintainers. We would be
happy to adapt their location or output shape to fit the upstream tree.
