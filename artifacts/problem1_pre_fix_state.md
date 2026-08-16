# Step 0 — state before any Problem 1 edit

Captured 2026-08-15, read-only. No file was modified during this step.

## Branch and commit

```
branch   cage/safety-layer
commit   cfcdf1d  docs: the word is READING, not reach - owner's correction
remote   origin/cage/safety-layer at c1b28e2, PR #63 open
main     2e86a7e, untouched
```

## Working tree

Clean. No modified files, no untracked files, nothing staged. 9 worktrees
registered; none holding Problem 1 work.

Three stashes existed before this step and were **not touched**:

```
5066ad2  extract: a line that says TAX INVOICE is a bill, and HSN 998311 is not money
be4fe08  textlayer: name both readings of an ambiguous date, and flag a rebuilt xref
```

## The live reader path — identified, which is the gate on continuing

`looks_like_a_date` has three callers:

| file:line | role |
|---|---|
| `accountant/extract/freeocr.py:815` | **the live photo path** — `_read_date` |
| `accountant/extract/nearby.py:581` | candidate filter |
| `accountant/cage/lying.py:211` | the cage. Not a reader. Not touched. |

The PDF path has its own date reader, `textlayer.py:566` `_date_from`, which
already reads named-part and arithmetic-settled forms.

**`accountant/extract/dates.py` had exactly one importer: `tests/test_dates.py`.**
Built, tested at 58 green, wired to nothing.

## Baseline corpus measurement

`.venv/bin/python scripts/measure_problem1_corpus.py`, 62 documents, 310 slots:

| | count | share |
|---|---|---|
| correct | 50 | 16.1% |
| incorrect | 0 | 0.0% |
| false positive | 0 | 0.0% |
| correctly unresolved | 34 | 11.0% |
| incorrectly unresolved | 226 | 72.9% |

| field | correct | incorrect | missed | correctly-unresolved |
|---|---|---|---|---|
| party | 0 | 0 | 59 | 3 |
| invoice_date | 0 | 0 | 55 | 7 |
| total | 30 | 0 | 25 | 7 |
| tax | 20 | 0 | 32 | 10 |
| invoice_number | 0 | 0 | 55 | 7 |

## Baseline tests and gates

```
pytest              174 failed, 5058 passed, 10 skipped, 4 xfailed
ruff check          All checks passed
ruff format --check 354 files already formatted
pyright             0 errors, 0 warnings, 0 informations
```

**174, not the 173 carried in earlier notes.** That figure was stale by one
and was corrected by running both arms of an A/B rather than trusting it.

The 174 are the known set: drafts the cage blocks that tests written before the
cage assert are VALID. Classified previously as 155 B_CAGE_FALSE_BLOCK,
10 A_CAGE_CORRECT, 6 D_TEST_INVALID, 2 C_REQUIREMENT_CHANGED. Step 6 revisits
them against the seven-category contract.

## A method note, paid for once

`git checkout <file>` and `git stash` both cost real work in this session.
`git checkout` reverted an entire change when only a mutation was meant to go.
A `git stash` used to A/B the suite was killed by a command timeout **before its
`git stash pop` ran**, leaving the fix sitting in `stash@{0}` and the tree
looking clean. It was recovered by popping that one stash by name.

Restore by rewriting the exact text. If a stash is unavoidable, verify
`git stash list` and grep the file for the change before believing the tree.
