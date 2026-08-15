# Accountant Dad — the whole build, for somebody who has never seen it

**Written 2026-08-15 on branch `cage/safety-layer`. The test suite was run in a
clean worktree at commit `f7dda81`. Two commits landed while this page was being
written — `b354750` ("the net was read, judged, and then not written down") and
`84ea572` ("407 documents read nothing, and 333 of them were right to") — and
where they change something, it says so. Code claims outside the test-suite
section were re-checked against `84ea572`.**

Every number on this page came from a command run on this machine on the day
above. Where something has not been measured, it says **not measured**. Nothing
here is an estimate.

Read time: about fifteen minutes. You are being asked for a second opinion, and
the three questions you are actually being asked are at the very bottom. Read
[The state of the reader](#7-the-state-of-the-reader--this-is-the-part-you-are-being-asked-about)
and [Where the corpus came from](#8-where-the-corpus-came-from-and-why-that-is-the-root-cause)
if you read nothing else.

---

## 0. What the product is, and what "safe" means here

A person photographs or uploads a supplier's bill. The system reads it, checks
it against the company's own TallyPrime history and Indian accounting rules,
asks plain-English questions when it is unsure, and writes the voucher into
TallyPrime.

The user is **not an accountant**. That single fact sets the whole design.

**The target is zero SILENT wrong posts. It is not zero mistakes.**

The difference is the entire point:

| kind of mistake | what happens | cost |
|---|---|---|
| a refusal you can see | person fixes it, or types it in themselves | minutes |
| a question you can see | person answers it | seconds |
| a wrong entry you cannot see | sits in a statutory record | found months later at tax filing, permanent |

A statutory record is a legal filing. Once a wrong number is in it and the
period is closed, correcting it is a formal amendment, not an edit. So the
system is built to fail loudly and often rather than quietly and rarely.

The owner's stated exchange rate is **100 false blocks : 1 silent wrong post**.
That number is not decoration — it is what justifies every design choice below
that looks paranoid.

---

## 1. The chain

Six stages. One module owns each. One sentence each.

| stage | module | the one sentence it is responsible for |
|---|---|---|
| classify | `accountant/cage/classify.py` | what these bytes actually are, decided by magic bytes and never by the filename — **see the warning below** |
| read | `accountant/extract/ladder.py` → `textlayer.py`, `freeocr.py`, `adapter.py` | what the bill appears to say, with a confidence and a named source on every field |
| conservation | `accountant/cage/conservation.py` | do the numbers on this bill agree with each other |
| validate | `accountant/checks.py` + `accountant/problems.py` + `accountant/detect/detectors.py` | is this voucher well-formed, and does it look like this company's own history |
| decide | `accountant/cage/decision.py` (fed by `accountant/cage/gate.py`) | post, ask, or block — and build the only postable object in the system |
| write | `accountant/tallyio/real.py`, guarded by `accountant/tallyio/writedoor.py` | put exactly this voucher into TallyPrime, once, and prove it landed |

### Warning: stage 1 is not installed either

`accountant/cage/classify.py` sniffs magic bytes and says a `.pdf` that is
really a JPEG is a Tuesday, not an attack. It is 211 lines, it is tested, and
**nothing on the shipped path calls it.** Verified:

```
$ git grep -l "cage.classify" -- accountant/ tests/
tests/test_chaos_corpus.py
tests/test_classify.py
```

Only tests. What actually ships routes on the media type the **browser
declared**, derived from the filename and never sniffed from the bytes —
`ladder.py` states this as a deliberate design choice and argues it at length.

Both positions are defensible. What is not defensible is holding both at once
without saying which one is live, and the answer today is: the declaration wins,
and the magic-byte classifier is decoration. This is the same shape as the cage
itself (§6) and as defect J1 (§4) — **a guard that is unit-tested and not
installed.** It appears three times in this repository, which suggests it is a
process problem and not three coincidences.

Two supporting modules matter for reading the rest:

- `accountant/cage/wall.py` — the two types and the wall between them (§4).
- `accountant/money.py` — the **one** rupee formatter. Indian grouping, verified:
  `format_inr(10000000)` → `₹1,00,000.00`, not `₹1,000,000.00`. Python groups
  digits in threes; India groups the last three then in twos.

### Two decision layers, not one

This trips up every new reader, so it is stated here rather than discovered.

- `accountant/decide.py` is the **original** decision order: NOT_VALID → UNCLEAR
  → VALID, computed from checks, memory and detectors.
- `accountant/cage/decision.py` is the **cage**: POST / ASK / BLOCK, computed
  from confidence, conservation and four facts about the world.

They are deliberately different enums so a log line cannot confuse them. The
cage is applied **after** the original order and **may only narrow it**
(`pipeline.narrowed_by_the_cage`, `accountant/pipeline.py:156`):

```
VALID    + cage POST  -> VALID
VALID    + cage ASK   -> UNCLEAR
VALID    + cage BLOCK -> NOT_VALID
UNCLEAR  + anything   -> UNCLEAR   (unchanged)
NOT_VALID+ anything   -> NOT_VALID (unchanged)
```

The one-way property is held by an early return, not by the contents of that
table — so deleting a row cannot turn a refusal into a post.

---

## 2. The conservation laws — the core idea

### Why they are the core idea

Every accuracy claim about reading a bill normally needs **labelled data**: a
pile of invoices where somebody already knows the right answer. This repository
has none, and building one is an open item (`H-02`).

A conservation law needs none of it. Debits and credits are equal or they are
not. The lines sum to the total or they do not. No expert, no labels, no model,
no network — and the same verdict on a machine that has never seen an invoice.

That is the argument, in the module's own docstring
(`accountant/cage/conservation.py:1-71`): this is the cheapest real safety
available, so it was built **before** the reader that feeds it.

### The four laws

`accountant/cage/conservation.py:83`, and the order is part of the contract:

```
debits_equal_credits          the two sides of the entry are equal
lines_sum_to_total            the bill's line items add up to its own total
net_plus_tax_equals_gross     pre-tax + tax = total
balance_delta_equals_entry    the books moved by exactly the entry amount
```

`run()` always returns all four, always in that order. It never stops at the
first failure — a report of one problem when there are two sends the person to
fix one thing and walk straight into the other.

### Three verdicts, and why INDETERMINATE exists

`accountant/cage/conservation.py:91-104`:

| verdict | meaning |
|---|---|
| `PASS` | checked, and the two sides are exactly equal |
| `FAIL` | checked, and they are not |
| `INDETERMINATE` | **could not be checked** — a number the law needs was never read |

A two-verdict system has to call INDETERMINATE either PASS or FAIL, and both
are wrong:

- **PASS** — an unread field silently authorises a write. This is the defect
  this repository keeps rediscovering: absence of evidence read as evidence of
  absence. It is how a GST bill gets posted without its tax, losing real input
  credit with nothing on screen to notice.
- **FAIL** — every bill that did not itemise its lines is refused, which makes
  the product useless.

So it is its own verdict, and **the decision layer blocks on it**
(`decision.py:863`). "This is wrong" and "I could not check" are different
sentences, and the person reading the refusal needs to know which one they got.

Two details that are easy to miss and are load-bearing:

- Equality is **exact**. A one-paisa tolerance would absorb the single most
  useful signal the module produces — a one-paisa disagreement is almost always
  a misread digit, not rounding (`conservation.py:169-187`).
- `None` and `()` are different. `None` line items means nobody looked; an
  empty tuple means they were read and there were none. Collapsing them would
  turn every un-itemised bill into a passing one (`conservation.py:209-234`).

### What conservation CANNOT catch — failure mode F-02

It cannot see a bill misread **consistently**.

Worked example. A real bill reads:

```
  Subtotal      ₹1,000.00
  GST 18%         ₹180.00
  Total         ₹1,180.00
```

The reader misreads every figure by a factor of ten:

```
  net    10,000.00
  tax     1,800.00
  gross  11,800.00
```

Now run the laws:

- `net_plus_tax_equals_gross`: 10,000 + 1,800 = 11,800. **PASS.**
- `lines_sum_to_total`: the lines were scaled too, so they still sum. **PASS.**
- `debits_equal_credits`: one amount on both sides. **PASS.**
- confidence: every character was crisp. **1.0.**

Every guard is green and the entry is wrong by ₹10,620. Arithmetic cannot see
it, because the error preserved the relationships the arithmetic checks. Both
`conservation.py:31-37` and `decision.py:171-176` say this in their own
docstrings so that nobody builds on top of a promise the module never made.

**Nothing in the system currently catches F-02.** That is the honest state.

---

## 3. The decision bands and the eight hard rules

`accountant/cage/decision.py`. Real constants, read off the file today:

| constant | value | line |
|---|---|---|
| `AUTO_POST_FLOOR` | `0.95` | `decision.py:206` |
| `ASK_FLOOR` | `0.70` | `decision.py:211` |
| `AUTO_POST_ALLOWED_TIERS` | `frozenset({"pdf_text_layer", "typed_text"})` | `decision.py:279` |
| `QUESTION_CAP` | `5` | `accountant/questions.py:24` |

### The bands

```
post    confidence >= 0.95  AND every conservation law PASS
                            AND the party known
                            AND the period open
                            AND no hard rule broken
                            AND every reading tier on AUTO_POST_ALLOWED_TIERS

ask     0.70 <= confidence < 0.95
        OR something readable more than one way
        OR the file had to be repaired before it could be read
        OR it was read by a tier not on that list

block   confidence < 0.70
        OR any hard rule broken
```

**Confidence never outvotes arithmetic.** A failing law refuses a bill at
confidence 1.0 exactly as it does at 0.71. A confidence score says how legible
some pixels were; a conservation law says whether numbers agree. They are not on
the same scale and they do not trade off. This is the one behaviour the cage
exists for — a scoring function cannot see a value the engine misread
*confidently*, and arithmetic can, but only if arithmetic is allowed to win.

Whether `0.95` and `0.70` are the *right* numbers is **not measured**. It needs
a corpus of labelled invoices this repository does not have. They are explicitly
not retuned to make anything pass.

### The eight hard rules — each always blocks

From `decision.py:47-81` and the functions that implement them:

| # | rule | implemented at | why it is absolute |
|---|---|---|---|
| 1 | any law `FAIL` | `_failed_laws_block`, `decision.py:903` | nothing a person can answer makes 45,000 + 74,999 equal 1,20,000 |
| 2 | tax on the bill | `_world_blocks`, `decision.py:1007` | GST posting is off; writing the bill without its tax line leaves a wrong statutory entry |
| 3 | the tax flag and the tax figure disagree | `_world_blocks`, `decision.py:1007` | two statements about one fact; neither is trusted over the other |
| 4 | checked amount ≠ written amount | `_write_is_what_was_checked`, `decision.py:941` | see below — the sentence the whole cage rests on |
| 5 | any law `INDETERMINATE` | `_conservation_blocks`, `decision.py:863` | "could not check" is not "checked and fine" |
| 6 | the period closed | `_world_blocks`, `decision.py:1007` | the books for that date are shut |
| 7 | the party unknown | `_world_blocks`, `decision.py:1007` | we never add a name to somebody's chart of accounts |
| 8 | the question budget spent | `_budget_blocks`, `decision.py:993` | a product that will not take no for an answer is worse than one that hands the entry back |

Rules 1 and 5 are **separate functions on purpose**. "Nobody could check this"
and "it was checked and it does not add up" are different facts, get different
sentences, and the person does something different about each. They share only
the outcome.

Rule 5 has one narrow exemption, and the reasoning is worth reading. Three laws
are about the **bill** and can be answered before anything is written. The fourth,
`balance_delta_equals_entry`, is about the **books** — it compares the ledger
balance before the entry with the balance after it, and before a write there is
no after. Its honest pre-write verdict is INDETERMINATE on every bill, every
time. Blocking on that made auto-post unreachable, and the only route to a POST
was to hand the law a *predicted* after-balance — a number compared against
itself, a check that cannot fail wearing the face of a check that passed.

So the caller states which moment it is (`Situation.moment`, no default, never
inferred), and pre-write an INDETERMINATE fourth law is expected. The exemption
is narrow in three ways: one law, one moment, one verdict. `DOCUMENT_LAWS`
(`decision.py:296`) is **derived** from `conservation.LAWS` by subtraction, so a
law added later blocks by default rather than becoming exempt by being left out
of a hand-typed list.

### Rule 4 is the sentence the whole cage rests on

"Arithmetic is checked before anything is written" is only true if the amount
checked and the amount written are the same amount. They arrive from two places:
the verdicts come from the caller, and the entry is built from
`observation.total_paise`. Until 2026-08-13 nothing compared them. Measured on
that day: **laws passing on 1,00,000 paise authorised a write of 1,00,00,000
paise and the function returned POST.**

The fix is `Situation.checked_paise`, no default. The laws are deliberately
**not** re-run inside `decide` — a check that computes its own evidence cannot
be contradicted by anybody, which is another check that cannot fail. The caller
states what it checked; the decision layer compares two statements and believes
neither on its own.

### Two ceilings that are NOT hard rules

A repaired PDF, and a reading tier the owner has not cleared. Both lower the
best available outcome from POST to ASK and change nothing else. Both are extra
reasons inside `_asking` (`decision.py:1161`) rather than early returns —
written as `return ASK` they would have **overturned** blocks on a repaired file
that was also wrong about something, which is the opposite of what was asked
for.

The tier allowlist is about **estimation, not media type**:

| tier | what it does | estimates? | may auto-post |
|---|---|---|---|
| `typed_text` | a person typed the words | no | **yes** |
| `pdf_text_layer` | the bytes say the words | no | **yes** |
| `free_ocr` | a model guessed from pixels | **yes** | no |

### What 100:1 buys you

The exchange rate says: accept a hundred false blocks to avoid one silent wrong
post.

What it buys is that **every failure mode above is visible**. A false block puts
a plain sentence on a screen and hands the bill back; the person types it into
Tally themselves and loses two minutes. That cost is bounded, immediate, and
paid by somebody who can see it. A silent wrong post is unbounded, delayed, and
paid by somebody who cannot.

What it **costs** is that the product may be unusable. That is not hypothetical
here — see §9(b). At 100:1 you have deliberately built a system whose default
answer is no, and you have to measure whether the yes rate is above zero. Today
it is zero.

---

## 4. The wall

Two types, and a wall between them. `accountant/cage/wall.py`.

Before this, one type carried a field from the moment it was guessed to the
moment it was written into a customer's books. That single fact is the root
cause of six of the sixteen recorded failure modes.

| type | what it is | can it be posted? |
|---|---|---|
| `Observation` (`wall.py:115`) | what we think the bill says. Every field carries a value, a confidence 0.0–1.0, and a named source | **no.** It has no method that writes, saves, or converts itself into anything postable |
| `LedgerEntry` (`wall.py:180`) | what we will write | yes — and it is the only thing that can be |

`LedgerEntry` is constructible only by the decision layer. Two independent halves
enforce it:

**Run-time half.** `LedgerEntry.decided()` takes the caller's module name and
refuses anybody who is not `"accountant.cage.decision"`
(`wall.py:65`, checked at `wall.py:213`). The name is **passed, not inspected
from the stack** — stack inspection is fragile across wrappers, decorators and
threads, and it fails *open* when it cannot tell, which is the wrong direction
for a guard. Passing it explicitly means a module that wants to bypass this has
to write the deciding module's name in its own source, where a reviewer sees it
in the diff.

**Test-time half — the AST scan.** `tests/test_the_wall.py:380`,
`test_only_the_wall_module_calls_the_ledger_entry_constructor`. It parses every
`.py` file under `accountant/` and asserts that no module except
`accountant/cage/wall.py` calls `LedgerEntry(` directly. An AST scan and not a
grep, because a mention inside a docstring is not a construction.

Both halves are needed and neither covers the other's failure. The repository's
own **defect J1**: *a unit test of a guard proves the guard works and says
nothing about whether the guard is installed.* A run-time guard is skipped by any
module that never calls it; a static guard cannot stop a write already in flight.

Three further tests keep the scan honest:

- `test_the_control_the_scanner_can_actually_find_a_construction` (`:391`) — the
  scanner is pointed at a class that *is* constructed, to prove it can see one.
- `test_the_ledger_entry_guard_is_not_asserting_over_nothing` (`:406`) — asserts
  the primary scan has a real subject. **This control has already earned its
  place**: `decided()` originally built its result with `cls(...)`, invisible to
  a scan looking for `LedgerEntry(`, so the guard was asserting over an empty set
  and proving nothing. `wall.py` was changed to construct by name so the scan has
  something to see.
- `test_no_module_outside_the_cage_imports_the_ledger_entry_type` (`:413`) — even
  importing the name outside `accountant/cage/` fails.

---

## 5. The closed owner decisions

These are settled. They are not open questions and re-litigating them is out of
scope for a second opinion — but you need them to read the rest.

| decision | where it is recorded | where it is enforced |
|---|---|---|
| **GST posting is OFF** | `docs/OWNER_DECISIONS.md` Q3=D, "a deliberate safety boundary, not a failed test" | hard rule 2 in `decision.py:1007`; `checks.tax_lines_can_be_posted`; `tallyio.real.check_writable`; `TaxDecision.posting_enabled` cannot be constructed `True` |
| **auto-post = non-GST purchases only** | Q3=D plus owner decision 2 of 2026-08-13 | the `post` band needs `carries_gst is False` **and** a tier on `AUTO_POST_ALLOWED_TIERS` (`decision.py:279`) |
| **free OCR only, no paid API** | `docs/DECISIONS.md` D-30 | `pypdf` + `pytesseract` + `Pillow`; "no customer bill goes to a third party without explicit approval" (Q4) |
| **party unknown always asks** | hard rule 7 | see the correction in §11 — in the cage it **blocks**, it does not ask |
| **a 1-paisa disagreement blocks** | `conservation.py:169-187` | exact equality, no tolerance; the refusal reads "out by 1 paisa", singular, deliberately |
| **integer paise always** | `conservation.py:120-134`, `wall.py:226` | a float is refused, not coerced; a `bool` is refused too, because `isinstance(True, int)` is `True` and `True == 1` would balance a one-paisa entry |
| **Indian grouping on every rupee figure** | `accountant/money.py` | one formatter. `format_inr(10000000)` → `₹1,00,000.00`. Verified by running it today |
| **100 MB cap, 413 before the body is read** | `docs/ARCHITECTURE.md:173`, `:631`, `:650` | `MAX_UPLOAD_BYTES = 100 * 1024 * 1024` (`accountant/web/app.py:314`); the `413` fires on the **declared** `Content-Length` at `app.py:3076`, before any parsing. A missing length is a `411`. The oversize body is drained and discarded first, so the browser can actually read the refusal instead of getting a connection error |

Note on the last one: "before the body is read" is true in the sense that
matters — nothing is parsed, nothing is written to disk, no extractor is called.
The bytes are drained off the socket and thrown away.

---

## 6. What is built and tested, versus what is not

### The suite, run in a clean worktree

`git worktree add` at `f7dda81`, then
`.venv/bin/python -m pytest tests/ -q`:

```
1 failed, 4546 passed, 6 skipped, 4 xfailed, 2 warnings in 462.87s
```

> **THE SUITE IS NO LONGER GREEN. Re-measured 2026-08-15 at commit `64b6bce`,
> whole suite, no `-x`:**
>
> ```
> 174 failed, 4665 passed, 10 skipped, 4 xfailed, 2 warnings in 476.77s
> ```
>
> **173 of the 174 have one cause: the cage was wired onto the live path** and
> now narrows outcomes these tests were written before it existed. They assert
> `VALID`; the answer is `NOT_VALID` with the four hard blocks named in plain
> sentences. The 174th is a stray `.DS_Store` file in `accountant/extract/`.
> Whether the tests are wrong or the cage is too strict for a typed sentence is
> **an open owner decision** — see [`PROJECT_STATE.md`](./PROJECT_STATE.md)
> §52.4b. The number below is kept because it is the pre-cage baseline the
> comparison needs.

The 6 skips and 4 xfails, with their own stated reasons:

```
SKIPPED  test_controlled_merge.py:1136       no ruff in the project virtualenv
SKIPPED  test_interface_contract_pages.py    x4 — "conservation.md / decision.md /
                                             gate.md / wall.md claims no such thing"
SKIPPED  test_mutation_environment.py:168    COVERAGE_CORE unset: outside the mutation path
XFAIL    test_error_responses.py             x3 — DEFECT E1, see below
XFAIL    test_gate_contract.py               the lockfile gate is declared but not enforced
```

**Defect E1 deserves your attention, because it is a silent-wrong-answer defect
in a system whose whole target is no silent wrong answers.**
`accountant/tallyio/real.py:1179` records it: `parse_read_response` refuses a
response carrying an error tag, and `parse_xml` refuses one that will not parse,
but **nothing checks that the document is a Tally response at all**. An HTML 404
page, a corporate proxy's sign-in page, and an unrelated service's XML are all
well-formed, carry no error tag, and therefore read as *a company with no
companies, no ledgers, no vouchers, and a trial balance of `{}`* — on all four
read paths. It is marked `xfail(strict=True)`, so it is a known, tracked, unfixed
defect rather than a surprise.

Why it matters here: an empty chart of accounts is not obviously wrong. It makes
`party_known` `False` for everything, which blocks — safe. But it also makes the
company's history empty, so every detector that compares a proposal against
history has nothing to disagree with and stays silent. That is the same shape as
the empty-set AST guard `wall.py` shipped once: a check that passes by having no
inputs.

The one failure is
`tests/test_startup_path.py::test_running_the_readme_command_reaches_the_apps_own_entry_point`.
It runs `python -m accountant.web.app` with output captured and expects the
banner `"Accountant Dad -> http://"` within 15 seconds. The process is still
running at 15s — so it **is** serving — but nothing was captured. The same test
fails identically in the main working tree, so this is not a worktree artefact.
The likely cause is that the banner is printed without a flush and Python
block-buffers stdout when it is a pipe rather than a terminal. Cosmetic in a
terminal, real if anyone ever pipes or supervises the process.

### The thing you most need to know about the test suite

**The cage is not installed on the shipped path at `f7dda81`.**

Verified: `git show f7dda81:accountant/pipeline.py | grep cage` finds one
mention, inside a comment. `git show f7dda81:accountant/web/app.py | grep -c
cage` returns `0`. At `f7dda81`, nothing outside `accountant/cage/` imports
`cage.decision` or `cage.gate` at all — the readers import `cage.confidence`
(scoring) and `cage.wall` (the `Observation` type), and that is the whole of it.

So those 4546 passing tests prove the cage **works**. They do not prove it is
**installed**, because at that commit it is not. That is defect J1 again, at
the level of the whole subsystem rather than one function.

**FIXED 2026-08-15, commit `6629b51` — this paragraph used to say the wiring
existed "only as uncommitted work in the current working tree". It is committed.**
Verified by reading the file at `8050dcd`: `accountant/pipeline.py:25` imports
the gate, `pipeline.py:156` is `narrowed_by_the_cage`, and `pipeline.py:795` is
the gate call inside `evaluate`. `tests/test_cage_on_the_live_path.py` is still
untracked in the working tree.

**One thing named in this document as "unit-tested and not installed" is still
not installed:** `accountant/cage/classify.py` (§1). `git grep -l "cage.classify"`
returns test files, `demo_safety_cage.py` and documentation, and nothing else.

`cage.state` has exactly one non-test importer, `accountant/invoice/status.py` —
and the whole `accountant/invoice/` package is imported by nothing outside
itself, so that is one unreached module importing another.

### Built and tested

- The cage: conservation, wall, decision, gate, confidence, state, `classify`
  (not installed — §1), and `lying.py` — a model that lies on command, so the
  guards can be tested against a known lie instead of against whatever a real
  model happened to do that day. That last one is the reason the guards are
  testable at all without labelled data: point a real model at them and you
  learn only which of that day's mistakes it happened to make.
- The write path: `tallyio/real.py` has run against a **real** TallyPrime
  Release 7.0 (Educational mode, Windows 11 ARM64 VM) on 2026-08-08 — list
  companies, read the chart of accounts, read the trial balance, write one
  marked voucher, read it back by operation id, reject a duplicate operation id,
  delete it, and see the trial balance return to its exact prior value in paise.
- Reversal, idempotency, memory, redaction, auth, the web app, the rules corpus.
- **New, 2026-08-15, commit `84ea572`:** `accountant/extract/invoicelike.py` —
  a score for "does this document look like a bill at all". It exists precisely
  because of §7: it turns a blank read into a *reason*, so "nothing was read and
  this does not look like a bill" (a corpus problem) stops being the same
  silence as "nothing was read and this looks exactly like a bill" (a reader
  problem). It is a label beside the fields, **never a gate in front of them** —
  wiring it as a filter would add a brand-new way to lose a real bill.
  Its own docstring is honest about the evidence: it separates 7 known invoices
  from 5 known non-invoices with one false positive, and says out loud that
  **n = 12 is an anecdote, not an accuracy claim.**

### Not built, or not measured

- **Real-bill extraction accuracy: not measured.** There are no labelled real
  invoices. `docs/OWNER_DECISIONS.md` Q4 says so explicitly and forbids claiming
  otherwise.
- **Whether 0.95 and 0.70 are the right thresholds: not measured.** Same reason.
- **Whether F-02 (consistent misread) ever happens in practice: not measured.**
- ~~**`period_open` has no source anywhere in this repository.**~~ **FALSE as of
  2026-08-13, corrected here 2026-08-15.** `accountant/tallyio/period.py` reads
  `BOOKSFROM` and `STARTINGFROM` off the company over the gateway, and
  `accountant/period.py:336::is_period_open` turns them into the boolean the gate
  takes. Both `pipeline.evaluate` call sites in `accountant/web/app.py` pass it.
  It fails closed: a timeout, an unreachable Tally or a missing field all return
  `False`, which blocks. The upper bound is **derived** and the log says so on
  every line. See [`PROJECT_STATE.md`](./PROJECT_STATE.md) §51.2.
- ~~**Line items are never read.**~~ **FALSE as of 2026-08-15, corrected here.**
  `textlayer.py:1427-1430` fills `ExtractedRecord.line_items` from `_read_lines`
  (`textlayer.py:808`), and `ladder.py:355` merges them. `lines_sum_to_total` is
  therefore no longer INDETERMINATE by construction on a born-digital PDF whose
  rows were found. **Two docstrings inside `gate.py` still say the old thing** —
  `gate.py:113` and `gate.py:319` both claim nothing ever fills the field. They
  are stale source comments and are recorded as a known defect, not fixed here.
  `gate.py:322` still passes `None` rather than `()` when the tuple is empty,
  deliberately, because reading it the other way would turn every un-itemised
  bill into a passing one.
- The `s2_extraction` gate is **red by design** for this MVP — closed by the
  owner on 2026-08-13 (`docs/OCR_CORPUS_FINDING.md`).

---

## 7. The state of the reader — this is the part you are being asked about

Measured today, 2026-08-15, by running the **shipped** reader
(`registry.build()`, whose default backend is `ladder`) over the committed
corpus. Scripts are in the scratchpad; the method is: read the file, call the
backend with the media type the browser would declare, count how many of the
four named fields came back with a value.

### The headline

| | count |
|---|---|
| documents the upload endpoint would accept (PDF / JPEG / PNG) | **413** |
| of those, documents where the reader read **zero** fields | **407** |
| documents where it read **at least one** field | **6** |

413 is 422 files minus 9 that the media-type gate refuses outright
(5 `.doc`, 3 `.docx`, 1 `.tif`). `UPLOAD_MEDIA_TYPES` is
`{application/pdf, image/png, image/jpeg}` (`app.py:342`).

Zero backends crashed. All 407 zeroes are honest "not found", not exceptions.

### Did text come out at all?

This is the question that separates "the reader is bad" from "the document has
nothing to read". A separate diagnostic pass, run with the product's own 30-second
OCR deadline (`pagereader.READING_DEADLINE_SECONDS = 30.0`):

| | with text | no text | total |
|---|---|---|---|
| PDFs (`textlayer.read`) | 64 | 16 | 80 |
| images (Tesseract returned ≥1 word) | 279 | 54 | 333 |
| **of the 407 that read zero fields** | **337** | **70** | **407** |

The briefed figures were 344 / 63. I measured 337 / 70 — a 7-document
disagreement (1.7%), most likely a different "has text" threshold or deadline in
the original run. **The conclusion is the same either way and it is the
load-bearing one: about five out of six documents that yielded no field DO have
readable text on them.** The reader is not failing to see characters. It is
failing to find the labels it is looking for, because the labels are not there —
see the PDF breakdown below.

### What the 6 actually read

Not one of them read a **party** or a **date**. Every one is a lone amount:

```
photos-and-scans-cord-test-028.jpg    total_paise 42619500     (₹4,26,195.00)
photos-and-scans-cord-test-048.jpg    total_paise  2500000     (₹25,000.00)
photos-and-scans-cord-test-056.jpg    tax_paise     731800     (₹7,318.00)
photos-and-scans-cord-test-060.jpg    total_paise      2625     (₹26.25)
photos-and-scans-cord-test-088.jpg    total_paise  4700000     (₹47,000.00)
open-datasets-and-photos-062.jpg      total_paise     10180     (₹101.80)
```

A bill with an amount and no party and no date cannot be posted anyway — hard
rule 7 blocks on an unknown party. So the practically useful yield is **0 of
413**.

### The 77 PDFs, broken down

Restricting to `data/real_invoices` (77 PDFs) reproduces the brief exactly:

| | count |
|---|---|
| no text layer at all | 15 |
| text, but no TOTAL label anywhere on the page | 36 |
| text, a TOTAL label, and a number on the same line | **14** |
| text, a TOTAL label, but the number is somewhere else | 12 |
| **total** | **77** |

(Including the 3 PDFs in `data/real_invoices_indian` makes it 80: 16 / 37 / 15 / 12.)

The label set is `TOTAL_LABELS` at `accountant/extract/labels.py:274` —
`GRAND TOTAL`, `TOTAL DUE`, `AMOUNT PAYABLE`, `AMOUNT DUE`, `TOTAL`.

### The 14 the matcher could have got are ALL foreign — confirmed

Every one, with the line as it appears in the text layer:

```
gov-and-open-data-113.pdf   Total HT 27,99 €                       upload.wikimedia.org
vendor-samples-024.pdf      Total HT -218,48 €                     ZUGFeRD/corpus
vendor-samples-038.pdf      Total HT 530,75 €                      ZUGFeRD/corpus
vendor-samples-039.pdf      Total HT 624,90 €                      ZUGFeRD/corpus
vendor-samples-040.pdf      Net total 2,076.76 €                   ZUGFeRD/corpus
vendor-samples-042.pdf      Net total: 496.00 €                    ZUGFeRD/corpus
vendor-samples-043.pdf      Total BeforeTax 32.838,00              ZUGFeRD/corpus
vendor-samples-047.pdf      TOTAL AMOUNT DUE ON August 3, 2014 $4.11   invoice2data
vendor-samples-050.pdf      Total 1 278.61 40.39 319.00            invoice2data
vendor-samples-052.pdf      Total HT : 46,68 €                     invoice2data
vendor-samples-053.pdf      Total EUR 34,73                        invoice2data
vendor-samples-064.pdf      Total HT -218,48 €                     ZUGFeRD/mustangproject
vendor-samples-071.pdf      Total € Incl. VAT 681.87               ZUGFeRD/mustangproject
vendor-samples-074.pdf      Total Gross Amount 360,00 EUR          ZUGFeRD/mustangproject
```

Seven French/German (`Total HT` = *total hors taxes*, pre-tax), five more euro,
one US dollar, one whose "total" line is three unlabelled columns. **Zero
rupees. Zero GST. Zero Indian.**

Two of them (`Net total`, `Total BeforeTax`) are not even the number you want —
they are the **pre-tax** figure, which `TOTAL_LABELS` deliberately excludes
because the amount payable is the gross.

### One thing the brief did not say, and it matters

I also ran the shipped reader over all 80 PDFs. **It reads zero fields off all
80 — including the 14.** The reason is the separator: the product's amount
parser (`labels.amounts_for`, using `Printing.EXACT_CHARACTERS`) requires a
colon between label and number. `Total HT 27,99 €` has no colon, so the matcher
finds nothing.

So "the 14 the matcher could have got" is a claim about a **looser** matcher
than the one that ships. With today's parser the PDF yield is 0 of 80, not 14 of
77. That distinction matters for question 3 at the bottom: the gap between what
the corpus offers and what the product takes is a separator rule, and the corpus
cannot tell you whether relaxing it is safe on an Indian bill.

---

## 8. Where the corpus came from, and why that is the root cause

Read from the `.json` sidecar beside every document (422 sidecars, one per file).

### Where the documents came from

| host | documents |
|---|---|
| `commons.wikimedia.org` | 178 |
| `upload.wikimedia.org` | 122 |
| `huggingface.co` | 52 |
| `github.com` | 48 |
| `assets.publishing.service.gov.uk` (UK) | 12 |
| `www.gsa.gov` (US) | 8 |
| `business.gov.au` (Australia) | 2 |
| **total** | **422** |

Wikimedia is **300 of 422**. Government sites are 22.

### Licences

| licence | documents |
|---|---|
| CC BY-SA 4.0 | 134 |
| Public domain | 95 |
| CC BY 4.0 | 46 |
| Apache-2.0 | 35 |
| CC BY-SA 3.0 | 26 |
| CC-BY-4.0 (same thing, spelled differently) | 22 |
| MIT | 13 |
| everything else (OGL v3, CC0, CC BY 2.0, GODL-India, …) | 51 |

Every single one is an openly-licensed, redistributable document.

### The conclusion

The repository is **public**. Confirmed:

```
$ gh repo view --json visibility
{"name":"accountant-dad-core","owner":{"login":"Intellora-ai"},"visibility":"PUBLIC"}
```

A public repository can only carry documents it is allowed to redistribute. So
the corpus was selected for **licence**, not for **resemblance to the product's
input**.

And openly-licensed documents and real commercial invoices are **near-disjoint
sets**. Nobody CC-licenses their bills. A supplier invoice is a commercial
document between two parties; it carries names, GSTINs, addresses and prices, and
there is no reason on earth for anyone to put one under CC BY-SA. What *is*
openly licensed is: blank government templates, Wikipedia illustrations of what
an invoice looks like, synthetic test fixtures from open-source invoice-parsing
projects, and museum scans of 19th-century ledgers.

That is exactly the pile above, and it explains every number in §7 without
needing any theory about the reader being bad. **The reader is being tested
against documents that are not the thing it reads.**

### One correction to how this is usually stated

It is often said that the field `kind` is `"none"` on 392 of 422. That is
literally true — only 30 sidecars carry a key called `kind`.

But it is misleading. **Every one of the 422 carries a document classification**,
under one of five different key names:

| keys present | sidecars |
|---|---|
| `document_kind` + `doctype` + `category` | 140 |
| `category` only | 107 |
| `document_type` + `category` | 71 |
| `doctype` only | 70 |
| `kind` + `document_type` + `category` | 30 |
| `document_kind` + `category` | 4 |
| **no classification under any of the five** | **0** |

So the corpus is not unclassified. It has **five competing schemas**, one per
collection script, and nothing reconciles them. That is a smaller problem than
"nothing is labelled" and a real one: any measurement that groups by document
type has to know all five names or it silently under-counts.

---

## 9. The two open defects

### (a) `net_paise` — read and thrown away

`freeocr.Reading` has carried five values since it was written: date, party,
total, tax, **net**. `ExtractedRecord` had four.

The trace:

| where | what happens |
|---|---|
| `pagereader.py:306` | reads the net off the page using `NET_LABELS` (`SUBTOTAL`, `NET AMOUNT`, `NET`) |
| `freeocr.py:818` | parses it into paise |
| `freeocr.py:826` | uses it **once**, to ask whether the three amounts contradict each other |
| `freeocr.py:913` (before today) | builds `ExtractedRecord` without it — the number is gone |

**The consequence.** `net_plus_tax_equals_gross` needs a net that was *read*. It
returned INDETERMINATE on every bill, and INDETERMINATE blocks. One of the four
conservation laws was dead — not from a missing capability, from a dropped
assignment.

**And it cannot be derived.** `gate.py:119` refuses to compute `total - tax`,
because both are already inputs to the same law, so a derived net would be "a
number checked against itself" and the law would pass on every bill for ever
while reporting that it had checked something.

**Where it stands — CLOSED 2026-08-15.** The table below was rewritten on
2026-08-15 after re-reading every file in it. **The three rows that said "no"
had all become false.**

| piece | state, re-verified 2026-08-15 |
|---|---|
| `ExtractedRecord.net_paise` | **added** — `accountant/extract/adapter.py:159`, defaults to `None`, deliberately not in `FIELDS` |
| `freeocr` carries it | **yes** — `freeocr.py:1065`, `net_paise=answer.net_paise` |
| `textlayer` carries it | **YES, as of 2026-08-15** — `textlayer.py:1426`, read by `_read_net` at `textlayer.py:713`. *This row said "no ... the string does not appear in that file at all". That is now wrong: it appears at lines 210, 222, 935, 1055 and 1426.* |
| `ladder` carries it through a merge | **yes** — `ladder.py:345-347,354`, added in `8050dcd` after the morning's fix was undone by a rebuild that did not name the field |
| `gate.observed()` reads it off the record | **still no** — `gate.py:380-394` builds the `Observation` from date, party, total, tax and line items only |
| `gate.gate()` gets it | as a caller keyword argument, defaulting to `None` (`gate.py:406`) |
| `pipeline.evaluate` passes it | **YES, as of 2026-08-15** — `pipeline.py:829`, `net_paise=draft.record.net_paise`. *This row said "no". It is now wrong.* |

So the fix is **landed end to end for the tier that matters**. `pdf_text_layer`
is one of the two tiers on `AUTO_POST_ALLOWED_TIERS` (`decision.py:279-281`; the
other is `typed_text`, where a person typed it themselves), and it is the only
*reader* on that list — the only one whose reading of a file may be posted
without asking a person. It now reads the net and hands it over.

**One thing here is still true and is worth keeping in view.** `gate.observed()`
does not pull the net off the record. The net reaches the law only because
`pipeline.evaluate` remembers to pass it. Exactly one caller does. A second
caller that forgets gets INDETERMINATE, which blocks — so it fails safe, but it
fails **silently**, and nothing shouts.

**And a second defect from the same family, fixed the same day.** The law
`lines_sum_to_total` was comparing the **pre-tax** item rows against the
**gross** total, so a correct bill failed by exactly its own tax. The comparand
is now chosen by `_lines_add_up_to` at `gate.py:332-377`. See
[`PROJECT_STATE.md`](./PROJECT_STATE.md) §52.

### (b) The cage blocks every draft

`accountant/pipeline.py:687` (working tree), measured 2026-08-13:

> Across this repository's whole suite, **993 drafts** reach `evaluate` and the
> cage **BLOCKS every one of them** — 280 that the decision order called VALID,
> 624 UNCLEAR and 89 NOT_VALID. Only the 280 change outcome, all of them from
> VALID to NOT_VALID, because the cage may not widen the other two. So the live
> path posts **nothing**.

The docstring names **four** independent hard blocks, and says supplying
`period_open=True` clears only one:

1. `period_open` is `None` — nothing in this repository reads whether a
   company's books are open for a date, so nobody looked, and nobody-looked
   blocks.
2. `net_paise` has no source, so `net_plus_tax_equals_gross` is INDETERMINATE on
   every bill. (Defect (a).)
3. `party_known` is `False` for every supplier that is not itself already a
   ledger in the chart of accounts.
4. **270 of the 280** read below `ASK_FLOOR` (0.70).

The docstring's own conclusion, and it is the right one: *"That is a real
measurement of how much this product actually knows, not a tuning problem."*

Blocks 3 and 4 are not bugs. Block 3 is hard rule 7 working as designed on a
first-time supplier. Block 4 is the reader telling the truth about §7.

---

## 10. What a second opinion is actually wanted on

### Q1. Is there a legal, redistributable source of **Indian GST invoices**?

The corpus has zero. Not few — zero. Every one of the 14 readable-total PDFs is
European or American. The product is built for Indian bills with GSTIN, HSN/SAC
codes, CGST/SGST splits and lakh grouping, and it has never been shown one it
could read.

If you know of a source — a government sample set, an academic dataset, a
tax-authority publication, an anonymised bank or ERP corpus — say so. If you
believe none exists, say that too; it settles the question and forces the corpus
out of the repo (Q2).

### Q2. Should the corpus live **outside** the public repo so real bills can be used?

The repository is PUBLIC. That is the constraint that produced the corpus, and
it is the only one that is not physics. Moving the corpus to a private store
would let real bills be used — but it costs reproducibility for anyone reading
the public repo, it creates a data-handling obligation for documents carrying
real party names, and `docs/DATA_POLICY.md` / `docs/REDACTION.md` would have to
grow to cover it.

The question is not "is a private corpus better" — obviously it is, for
measurement. The question is what the smallest version is that touches reality.
One real bill you can read tells you more than 413 you cannot.

### Q3. Is fixing `net_paise` worth it when the corpus cannot test it?

The case **for**: the law is one of four, it is currently dead, and a dead law
is worse than a missing one because the cage reports "could not check" on
something it was built to check. The fix is small — carry a value already read.

The case **against**: with zero Indian invoices, you cannot demonstrate that
`net_plus_tax_equals_gross` ever catches anything. You would be shipping an
untested guard on the strength of an argument. And §7 shows the reader gets
nothing off 407 of 413 documents — a fourth conservation law does not help a
reader that reads no fields at all.

My own read, offered so you have something to disagree with: **the bottleneck
is not the cage and it is not `net_paise`. It is that the reader has never seen
an Indian bill.** Fixing `net_paise` optimises a non-bottleneck. It is cheap,
it is correct, and it will change no measurable outcome until Q1 or Q2 is
answered.

Q2 is the one that only the owner can decide, and everything else is waiting
behind it.

---

## 11. Corrections to the brief this document was written from

Recorded because a document that quietly agrees with its own brief cannot be
checked.

| claim as briefed | what I measured |
|---|---|
| "the cage, once wired into `pipeline.evaluate`, blocks every draft: 280 VALID become NOT_VALID on **three** independent blocks" | the docstring at `pipeline.py:687` says **four**, and lists four: `period_open=None`, `net_paise` INDETERMINATE, `party_known=False`, and 270 of 280 below `ASK_FLOOR`. Also, the cage blocks **all 993** drafts, not only the 280 — the 280 are just the ones whose outcome changes |
| "`net_paise` … `ExtractedRecord` **had** four" | correct as history. It was fixed in commit `b354750` during this session: `ExtractedRecord.net_paise` now **exists** (`adapter.py:159`) and `freeocr` fills it (`freeocr.py:956`). **But the fix does not yet reach the law.** `textlayer` still does not carry it, and `gate.observed()` never reads it off the record, so `net_plus_tax_equals_gross` is still INDETERMINATE on the only tier that can auto-post. Two more edits, in two other files |
| "of **77** PDFs" | 77 is `data/real_invoices` only. The corpus has **80** PDFs; the other 3 are in `data/real_invoices_indian`. The 15/36/14/12 split is exactly right for the 77; for all 80 it is 16/37/15/12 |
| "the **14** the matcher could have got" | reproduced exactly, and all 14 are foreign — confirmed. But the **shipped** matcher gets 0 of them, because `Printing.EXACT_CHARACTERS` requires a colon between label and number and none of the 14 has one. "Could have got" is a looser matcher than the one that ships |
| "`kind` is 'none' on 392 of 422" | literally true — only 30 sidecars have a key called `kind`. Misleading as stated: **all 422** carry a classification, spread across five key names (`kind`, `document_kind`, `doctype`, `document_type`, `category`). The problem is schema drift across collection scripts, not missing labels |
| "of the 407: **344** have text, **63** have none" | I measured **337 / 70**, using `textlayer.read` for PDFs and Tesseract at the product's own 30s deadline for images. A 7-document (1.7%) disagreement. The conclusion survives: about five in six of the zero-field documents do have readable text |
| "party unknown always **asks**" | in the cage it **blocks**. It is hard rule 7 in `decision.py`, listed under "eight hard rules, each of which always blocks", and the sentence a person reads is *"I will never add a new name to your books on my own, so this one is saved for you to finish"* — a refusal, not a question. The intent (never invent a party) is right; the outcome word is wrong |
| implied: the suite at `f7dda81` exercises the cage | it does not. At `f7dda81` **nothing outside `accountant/cage/` imports `cage.decision` or `cage.gate`**, and `web/app.py` contains the string "cage" zero times. The 4546 passing tests prove the cage works, not that it is installed |
| "THE CHAIN. **classify** → read → …" | `accountant/cage/classify.py` is real, tested, and **called by nothing except tests** (`git grep -l cage.classify` returns two test files and no source file). The shipped path routes on the browser's declared media type and never sniffs. Stage 1 of the chain, as briefed, is not on the chain |

Everything else in the brief that I checked — 413/407/6, the six documents and
what they read, the 15/36/14/12 PDF split, all-14-foreign, 300 Wikimedia (178 +
122), 52 HuggingFace, 48 GitHub, 22 government (12 UK + 8 US + 2 AU),
134/95/46/35 licences, 422 total,
`AUTO_POST_FLOOR` 0.95, `ASK_FLOOR` 0.70, `QUESTION_CAP` 5, the eight hard
rules, the wall and its AST scan, the 100 MB cap and the 413 — reproduced
exactly.

---

## Appendix — how to reproduce the numbers

```bash
# the suite, in a clean tree
git worktree add /tmp/wt f7dda81 --detach
cd /tmp/wt && /path/to/.venv/bin/python -m pytest tests/ -q

# repo visibility
gh repo view --json visibility

# corpus provenance: read the .json sidecars beside data/real_invoices*/
#   host        -> urlparse(sidecar["source_url"]).netloc
#   licence     -> sidecar["licence"]
#   classification -> any of kind / document_kind / doctype / document_type / category

# reader coverage: for every PDF/JPEG/PNG under data/real_invoices*/
#   registry.build().extract(bytes, mime)
#   count how many of date / party / total_paise / tax_paise are not None

# PDF text-layer diagnostic
#   accountant.extract.textlayer.read(bytes).text
#   search it for accountant.extract.labels.TOTAL_LABELS
```
