# Prepared contributions to Nous Research

**Nothing here has been filed. No issue, no pull request.** These are materials prepared for the
repository owner to submit, and an unsolicited standing offer that Nous Research is free to
ignore. Nothing here claims Nous endorsement.

## What is being offered

Five documentation and comment corrections to
[Hermes Agent](https://github.com/NousResearch/hermes-agent), found while building an external
client against the `tui_gateway` WebSocket JSON-RPC protocol. Eight files, +31/−17, entirely
comments, docstrings, and `website/docs` prose — **no behaviour, API, or test change.** Every
claim cites a file and line in the Hermes tree so it can be checked without trusting us.

The claims were re-derived and re-verified against Hermes HEAD
`26350357d76e4508c8df9304a3374bdc5a6f6220` on 2026-08-30, not against the older commit this
repository builds against. That pass **dropped 8 claims as stale**, **withdrew 2 as our own
errors**, and **killed a prepared bug report** because upstream had already fixed it in
`beb794123618c997e82791316df643fc61347665`.

## The files, in reading order

| File | What it is |
| --- | --- |
| [`PR-DESCRIPTION.md`](PR-DESCRIPTION.md) | **Start here.** The pitch: each correction with the source that establishes it. The text below the `---` is the proposed PR body. |
| [`documentation-corrections.patch`](documentation-corrections.patch) | The deliverable. Applies clean to the HEAD above under `git apply --check --whitespace=error`. |
| [`HEAD-REVERIFICATION.md`](HEAD-REVERIFICATION.md) | A working record, not a pitch. Every claim we held, with a STILL VALID / STALE / NEVER VALID verdict and its source. Consult it to audit a specific claim; there is no need to read it end to end. Its ID numbers are internal bookkeeping and resolve to no public document — each row stands alone. |
| [`catalog-offer.md`](catalog-offer.md) | A **separate, optional** ask: the MIT-licensed derived protocol catalog and its generator, if they are useful. Declining it has no bearing on the corrections. |

## Provenance

This repository was named **Talaria** until 2026-08-30; the commit history, Xcode targets, and
several internal paths still carry that name. Any earlier correspondence would have been under it.
