# Three things the safety cage found, and none of them is a bug in the cage

**Dated 2026-08-13. Every number here was measured. Where something was not
measured, it says so.**

The cage was built to stop a wrong entry reaching a customer's books. Building it
surfaced three facts about the product that were true before it existed and that
nothing had reported. All three need an owner decision. None of them blocks the
rest of the work, and the rest of the work is done.

---

## Finding 1 — the extraction corpus cannot be read by any OCR engine

Full evidence in [`OCR_CORPUS_FINDING.md`](./OCR_CORPUS_FINDING.md).

`s2_extraction` requires **76 exact field matches out of 80** renderable cases.
Forty of those eighty contain nothing readable:

- the **20 JPEGs contain zero pixels** — `APP0/JFIF` then thirteen `COM` comment
  segments, no `SOF0`, no `SOS`. `render_jpg_container` says "NO image data" in
  its own docstring.
- the **20 PNGs use a hand-built 5×7 bitmap font**, uppercase, ~10 px per line.
  Real Tesseract on a real corpus file returned `INVOICE NO: GT/0041` as
  `TWoIte Not eT/a081`.

**Honest ceiling ≈ 40/80**, and no engine choice changes it. The threshold was
**not** moved. Three options are written out for the owner.

---

## Finding 2 — an existing guard made every reader impossible to land

`tests/test_no_reader.py` asserts, repo-wide, that no document reader exists:
nothing in `accountant/extract/` may import a reader library, no module there may
contain the word "tesseract", and `pyproject.toml` must declare
`dependencies == []`.

Two agents hit it independently — one building `freeocr.py`, one building
`textlayer.py`. **Neither could land, and no location in the repository could
host them**, because two of the four assertions are repo-wide rather than
path-scoped.

The guard's own failure message names the way out: *"either way it is an owner
decision and not a test change."* **That decision was already made**, twice — the
approved plan records `B-A … CLEARED`, and the owner's words were "Final
authorization: you may add the free OCR + PDF reader dependencies now." The guard
was written in Phase 7 when "no reader exists" was the true answer. It stopped
being true when the owner ruled.

**Resolution taken:** the ban becomes an **allow-list**, not a deletion. Every
assertion is kept and re-aimed at *"everything outside the approved set is
exactly as forbidden as before"*, plus new controls proving an unapproved module,
an unapproved dependency, and a reader importing the wrong library are all still
caught. Net test count goes **up**. A ban that must be deleted the moment reality
changes protects nothing afterwards; an allow-list keeps protecting.

---

## Finding 3 — the cage cannot honestly post anything, and this is the deep one

Measured twice, independently, and the second measurement is the better one: a
one-line experiment (`if False and …` at the `VALID` branch) against a
3,820-pass green baseline produced **50 failures**. Wiring the gate to the live
pipeline was not "the gate is too strict". It was an off switch with a 50-test
receipt.

The reason is worse than the one I first identified, and it is worth stating
exactly. **Three of the four conservation laws have no inputs on that path:**

| Law | Pre-write, on the typed-text path | Why |
|---|---|---|
| `debits_equal_credits` | **computable** | the voucher has both legs |
| `lines_sum_to_total` | INDETERMINATE | nothing fills `line_items` — the reader extracts one amount, not a table |
| `net_plus_tax_equals_gross` | INDETERMINATE | no reader produces a net amount |
| `balance_delta_equals_entry` | INDETERMINATE | there is no after-balance before a write |

Every INDETERMINATE is a hard block. And on the typed-text path `date` is always
`not_found`, so `lowest_confidence` is 0.0 **before conservation even runs**.

### What this actually means

**The cage is not broken. It is ahead of its input.** It asks four questions
about a document; the current reader supplies enough to answer one. That is
precisely why Steps 13 and 14 exist, and it is the strongest possible argument
that the readers are not optional polish.

But it also exposes a real distinction the design does not yet make:

> **"not applicable" is not the same as "could not check".**

A bill that lists no line items is not a bill whose line items went unread. The
first is a document without that structure; the second is a failure to read one
that exists. Both currently return `INDETERMINATE`, and `INDETERMINATE` blocks.
Collapsing them means the cage refuses a perfectly ordinary one-line bill for a
reason that is not true of it.

`conservation.py` already distinguishes `None` (not read) from `()` (read, none
exist), which is the right instinct — but `()` currently passes only when the
total is zero, and a one-line bill for ₹1,200 with no itemisation is neither
"unread" nor "sums to zero".

### What was done, and what was not

**Done:** `balance_delta_equals_entry` is being exempted pre-write, because it is
knowable only after — with a control proving the exemption is exactly one law and
not the other three, and with a pre-write **FAIL** still refused. Not-yet-knowable
earns an exemption; known-to-be-wrong never does.

**Not done, and it is the owner's call:** whether a bill that genuinely has no
line items and no separate net should PASS those two laws as *not applicable*, or
keep blocking. Both are defensible. Passing them risks a real itemised bill whose
lines were missed being treated as a bill that had none — which is failure mode
F-02 wearing a different hat. Blocking them means the product asks about every
un-itemised bill, which at 100 false blocks : 1 silent wrong post is the
**correct** direction and may simply be the right answer.

I did not choose. Choosing it by writing code would be setting a number the owner
did not give.

---

## What is true regardless of all three

- The suite is green: **3,874 passed, 0 failed** at last clean measurement.
- Coverage measured fresh: **93% whole repo** against a gate of 90;
  **97% across `accountant/cage/`**; `state.py`, `conservation.py` and
  `confidence.py` at 100%.
- **No threshold was moved. No gate was removed. The gate count is 20, as it
  was.** `.github/**` was not edited — the exact twelve-line diff sits in
  [`CI_OCR_INSTALL.md`](./CI_OCR_INSTALL.md), stated line by line before the
  fact, as the standing rules require.
- The demo runs all twenty inputs and every outcome matches what was written
  down first, with the guard that fired asserted per row.

## The one-line version

The cage works. What it found is that **the product cannot yet answer the
questions the cage asks** — the corpus cannot be read, the readers were
structurally forbidden from landing, and three of four arithmetic checks have no
inputs. Those are not reasons to loosen the cage. They are the work list.
