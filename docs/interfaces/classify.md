# `accountant.cage.classify`

**Main job.** Say what a pile of bytes actually is. Nothing else.

## Inputs

| Field | Type | Required | Constraint |
|---|---|---|---|
| `data` | `bytes` | yes | a `str` raises — it has an encoding attached and the whole question is what the bytes are |
| `declared_mime` | `str` | no | recorded, **never believed** |

There is deliberately **no `filename`**. An extension decides nothing here, and a
parameter accepted and ignored invites a caller to believe it matters.

## Outputs

`Classified(kind, detected, reason, declared_disagreed)`.

`FileKind` is `TEXT` / `PDF` / `JPEG` / `PNG` / `UNSUPPORTED`.
`READABLE` names the four we extract from, **once** — a second copy drifts from the first.

| Invariant | |
|---|---|
| `reason` | empty exactly when readable, non-empty exactly when not — both directions tested |
| `detected` | never empty, even for a readable file — the audit line records what arrived |
| raises | **never**, on any content |

## Magic bytes beat the declared type, always

A `.pdf` that is really a JPEG is a Tuesday, not an attack. Browsers guess, phones
rename, mail clients relabel. The disagreement is still **counted** —
`declared_disagreed` — because how often uploads arrive mislabelled is a fact about
the product.

## Does NOT

Extract anything · execute · shell out · **unzip** · trust the declared MIME or the
extension · touch the network · persist anything.

DOCX and XLSX are zip archives. Refusing to extract from them removes the zip-bomb
surface as a side effect of a decision made for a different reason — D-23 excluded
DOCX on purpose. **Accepting a file is not the same as reading it.**

## Targets

| | |
|---|---|
| Correctness | 100% of uploads classified; **0 crashes** across 200 chaos inputs |
| Latency | measured; **threshold owner-set** |
| Throughput | ≥ 1 upload/sec, measured not assumed |

## Failure modes

| Trigger | Behaviour | Logging |
|---|---|---|
| empty file | `UNSUPPORTED("That file is empty…")` | INFO with hash |
| truncated / unknown header | `UNSUPPORTED` naming what it looks like | INFO |
| declared type disagrees with bytes | **the bytes win** | INFO with both |

Unreadable types are **named**, not lumped: *"a zip archive — Word, Excel and
OpenDocument files are zips"*, *"a HEIC photo — the format an iPhone uses by
default"*. That tells the person what to do next; "unsupported file" does not.

## Does not answer

Whether the file is a bill. A photo of a cat is a perfectly valid JPEG and is
classified as one. Stopping it is the reader's job, then the decision layer's.

## Dependencies

**None.** Stdlib only.

## Observability

Counter per detected kind; counter of declared-vs-actual mismatches. Every call
logged with byte length, declared MIME, detected kind, hash — **never contents**.
