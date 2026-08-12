# `accountant.cage.wall`

**Main job.** Keep "what we think the bill says" a different type from "what we will
write", with one gate between them.

## The two types

| | `Observation` | `LedgerEntry` |
|---|---|---|
| means | what we think it says | what we will write |
| built by | any reader | **only `accountant.cage.decision`** |
| postable | **never** | yes |

## Inputs

`Field(value, confidence, source)` — invariants enforced on construction, not trusted:

- a `value` of `None` **must** carry confidence `0.0`. "We did not read it" and "we
  are unsure" are the same fact; letting them disagree allows a post on nothing.
- confidence is inside `0.0`–`1.0`. Above 1.0 would clear the auto-post band by accident.
- `source` is never empty. A field with no provenance cannot be explained to the
  person whose books it changes.

## Outputs

`Observation.lowest_confidence` returns the **minimum**, not the mean. A bill is not
uniformly legible — a clean printed total beside a smudged letterhead averages to a
number describing neither. One misread digit ruins an amount.

## Does NOT

`Observation` has no `post`, `write`, `save`, `commit`, `send` or `to_ledger_entry`,
and a test asserts it never grows one · the wall does not decide anything · it does
not touch Tally.

## The guard, both halves

| Half | What it catches |
|---|---|
| **runtime** — `LedgerEntry.decided(caller, ...)` refuses any caller ≠ `DECIDING_MODULE` | a reader trying to build a write |
| **static** — AST scan asserts only `wall.py` constructs `LedgerEntry` | a module that walks around the constructor by never calling it |

**Neither covers the other's failure.** Defect J1: *a unit test of a guard proves the
guard works and says nothing about whether the guard is installed.*

`caller` is **passed, not inspected from the stack**. Stack inspection is fragile
across wrappers, decorators and threads, and fails **open** when it cannot tell —
the wrong direction for a guard. Passing it means a bypass has to write
`DECIDING_MODULE` in its own source, where a reviewer sees it in the diff.

## Failure modes

| Trigger | Behaviour |
|---|---|
| a non-decision module calls `decided` | `NotYourEntryError` naming who asked and who is allowed |
| amount ≤ 0 | `ValueError` — corrections are done by reversal, never by sign |
| both sides name the same ledger | `ValueError` — money moving to itself is a typo |
| unnamed party or account | `ValueError` |

## Dependencies

**None.** Stdlib only.

## Observability

No side effects. Frozen dataclasses — mutation raises.
