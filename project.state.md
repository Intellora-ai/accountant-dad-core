# project.state.md — Problem 1, the eight closed decisions

**Owner decisions. Closed 2026-08-15 13:01 IST. Do not reopen without a written
instruction in chat.**

This file exists because eight questions were asked and answered, and an answer
that lives only in a chat log is an answer that gets asked again. Every entry
records the question, the ruling, why, what it forces the code to do, the tests
that hold it, and what was measured afterwards.

Branch `cage/safety-layer`. Decisions recorded at `fac48ac`, before the work.
The post-implementation section at the bottom carries the commit that landed it.

---

## What Problem 1 is, in one paragraph

A photograph or PDF of a bill goes in. Five fields should come out — party,
invoice date, total, tax, invoice number — each with a value or an explicit
reason there is none. The engine already works: MEASURED over 60 real documents,
2,596 OCR word rows arrived, 2,367 carried characters, and **zero** field slots
died because the reader found no words. **287 of 300 died because no label
matched.** The bottleneck is the join between the words Tesseract returns and
the labels this reader knows — specifically, that a label and its value are
often on different lines.

**Baseline to beat: 1 of 300 field slots reach a candidate**, measured through
the real reading path. An earlier note said 3; that came from a label-match
proxy rather than from what a person actually receives, and it is withdrawn —
see the post-implementation section.
Command: `.venv/bin/python scripts/measure_field_slots.py`

---

## Decision 1 — OCR word position and geometry

**Question.** Tesseract's `image_to_data` returns `left/top/width/height`, and
`freeocr.Word` discards it. Half the requested relationships (value to the
right, value below, bounding-box radius) need it. May geometry be added?

**Decision.** **No.** `freeocr.Word` stays `text` + `confidence`. Ship
relationships 1–3 only: same-line, after-label-on-the-same-line, next-line.
Relationships 4–6 are deferred.

**Rationale.** The type's own docstring rejects geometry in terms: *"Not the
position, not the size... carrying the geometry would be the first line of
something that did [decide from position]."* That door was opened once already —
the positional party heuristic — and MEASURED harmful: ground-truth party-wrong
went 5 → 8, three answers added and three of them wrong. It was disabled the
same day. Reopening the same path to chase the same kind of win is the mistake
this repository has already paid for once.

**Implementation consequence.**
- `nearby.PageWord.box` stays `Box | None` and stays optional.
- With no geometry the module uses line distance and token order only.
- No fake box is constructed. No page coordinate is inferred.
- Where a geometry-only path is skipped, the reason is recorded rather than
  silently absent.

---

## Decision 2 — Pixel distance and its unit

**Question.** `Limits.max_pixel_distance = 400` is marked in the module itself
as *"NOT MEASURED AND THE UNIT IS THE PROBLEM"*. What number, in what unit?

**Decision.** **Pixel-distance search is disabled and deferred.** No pixel
constant is introduced.

**Rationale.** 400px is a third of an A4 width at 150 DPI and a sixth of it at
300 DPI, so one constant means two different search radii depending only on how
the caller rendered the page. A threshold that changes meaning with the input is
not a threshold. It follows from Decision 1 anyway — with no geometry there is
nothing to measure a pixel distance between.

**Before any future geometry work**, all of these must exist first: coordinate
source, page dimensions, rendering DPI, normalisation method, unit, a MEASURED
threshold, regression fixtures, and scale-invariance tests.

---

## Decision 3 — Subtotal versus total

**Question.** The request grouped `Subtotal` and `SUB TOTAL` under *Total*. The
repository deliberately separates them.

**Decision.** **Keep them completely separate.** `SUBTOTAL` and `SUB TOTAL` feed
**net/pre-tax only**. `TOTAL`, `GRAND TOTAL`, `AMOUNT PAYABLE` and their family
feed **gross total only**. Nearby-line search runs independently on each family.

**Rationale.** This is the exact defect fixed six commits earlier in `e783074`.
Line items are pre-tax; the gross is not. MEASURED on the repository's own bill:
rows summed to 1,046.24, which is the net exactly, while the gross is 1,234.56 —
so merging the families reports a correct bill short by exactly its tax, 18,832
paise. Worse, a subtotal read as the total would make
`net_plus_tax_equals_gross` compare a number against itself and pass for ever.

The two laws that must keep holding:

    line items            ==  net / subtotal
    net + tax + charges + round-off  ==  gross total

**Required behaviour.**

    SUB TOTAL                     ->  net = 1046.24
    1,046.24                          and NEVER total = 1046.24

    SUB TOTAL    1,046.24         ->  net   = 1046.24
    GRAND TOTAL  1,234.56             total = 1234.56

---

## Decision 4 — Next-line value policy

**Question.** Is a next-line value safe for every field?

**Decision.** **No — field-aware, label-aware, and conservative.** Next-line
extraction is allowed per field, each with its own validation:

| field | next-line allowed | the value must |
|---|---|---|
| supplier / vendor / seller | yes | look like a name; not numeric-only |
| buyer / customer | yes | look like a name; not numeric-only |
| invoice date | yes | parse as a real date; impossible dates refused |
| invoice number | yes | match reference-number shape; not a date, amount or phone |
| tax | yes | be a valid amount, under a label that identifies tax |
| final total | yes | be a valid amount, not a subtotal/tax/quantity/date, no stronger conflicting candidate |

**A general guard applies to every field**: if the next line is itself a
recognised label from any family, the candidate is refused. A line that is a
label is not a value.

**Rationale.** For `Supplier:` the following line is usually the name. For
`Total` the following line is very often the *next label*. One rule for both
would read a label as a total.

---

## Decision 5 — Multiple nearby values

**Question.** When two or more candidates survive, review or reject?

**Decision.** **Return all candidates, mark the field ambiguous, require
review.** Never guess. Not the first, not the largest, not the closest.

**Preserved on every ambiguous result:** candidate values, source text,
candidate order, field, extraction method, confidence, the rejection reason for
each discarded candidate, and the ambiguity reason.

**Effective posting behaviour today.** The cage has no surviving ASK path — every
draft blocks — and a next-line find is capped below `ASK_FLOOR` regardless. So an
ambiguous field is behaviourally a block. **No new ASK path is created in this
pass and the cage is not modified.**

---

## Decision 6 — Confidence for next-line values

**Question.** What confidence should a labelled-but-next-line value carry?

**Decision.** **Capped at `BY_POSITION` = 0.5.** Same-line labelled reads keep
their existing `EXACT` behaviour unchanged.

    same-line labelled       existing EXACT behaviour, untouched
    next-line labelled       min(engine score, 0.5)
    ambiguous next-line      AMBIGUOUS / REVIEW_REQUIRED
    invalid next-line        rejected

**Rationale.** A next-line value rests on a line relationship rather than on
label-and-value evidence in one place. 0.5 is below `ASK_FLOOR` (0.70) and far
below `AUTO_POST_FLOOR` (0.95), so **a next-line-only value cannot post and
cannot even spend one of the five daily questions.** That is intentional: the
value becomes visible for review without the system fabricating certainty.
Neither floor moves.

---

## Decision 7 — Multilingual labels and non-INR currency

**Question.** `Total des` is French and `Rp` is Indonesian, against a standing
"nobody in India writes euro or dollar".

**Decision.** **Labels may be multilingual — they only locate a figure.**
Non-INR currency is **detected and preserved but never converted and never
posted.**

    ₹ / Rs / Rs. / INR      existing INR behaviour
    $ / USD / € / EUR / Rp / IDR
                            marker preserved, marked non-INR,
                            status UNKNOWN or REVIEW_REQUIRED,
                            no exchange rate, no conversion, no auto-post

**Rationale.** A label is a pointer, not a promise — matching `Total des` costs
nothing and finds a figure. Converting a currency is an accounting act and needs
a policy that does not exist yet.

---

## Decision 8 — Untracked measurement scripts

**Question.** Eight untracked scripts produce ~150 ruff errors and ~10 format
failures. Both gates are `threshold = 0, required = true` in `ci/gates.toml`.
CI is green today only because untracked files do not exist in a fresh clone.

**Decision.** **Gitignore them**, following the precedent already recorded at
`.gitignore:71-92`, where six equivalent scripts were ignored on 2026-08-13 for
this exact reason.

**Before ignoring, each was confirmed to contain:** no production code, no test
fixture any test imports, no secret. Their measurements are preserved here and
in committed regression tests.

**No source file, test, configuration or workflow file is ignored.**

The scripts and their surviving measurements are listed in the post-implementation
section below.

---

## Post-implementation record — 2026-08-15, IST

### STATUS: PROBLEM_1_NOT_COMPLETE

The completion bar was a MATERIAL improvement on the real-corpus field-slot
measurement. It did not move. Everything else in the eight decisions is built,
tested and green, and none of that changes the status.

### The measurement, before and after

    command   .venv/bin/python scripts/measure_field_slots.py
    corpus    60 image documents, data/real_invoices_indian then data/real_invoices
    slots     300  (5 fields x 60 documents)

    SLOTS REACHING A CANDIDATE      before  1 of 300      after  1 of 300

Measured by reverting only `pagereader.py` and re-running, so the two numbers
differ in exactly one thing.

**THE EARLIER "3 of 300" WAS NOT THE SAME MEASUREMENT AND IS WITHDRAWN.** That
figure came from calling `labels.values_for` directly — a label-match proxy. The
script now runs `read_page` + `freeocr._scored`, which is what `page_reader`
calls on a real upload, so it includes `the_one`'s refusals, the artifact
ceiling and the confidence-above-zero rule. Through the real path the honest
baseline is **1**, and it was 1 before this work and 1 after.

### Why the next-line search did not move it

Next-line search needs a LABEL to anchor to. On this corpus:

    slots dying at "words present, NO LABEL MATCHED"     287 of 300
    slots dying at "no OCR words at all"                  10
    slots reaching a candidate                             1

287 slots never match a label at all, so there is nothing for a neighbouring-line
search to search *from*. The work fixes the case where a label matches and its
figure sits on the next line — which is real, and which these 60 documents
almost never present.

The 14 label-without-a-figure lines that motivated this work were counted on a
DIFFERENT 60 documents (the sample that reported 6,210 OCR rows). Both samples
are real; they are not the same sixty. That is the mistake to avoid repeating:
a measurement is only comparable to itself.

### What was built, and what it does on fixtures

MEASURED through `read_page` + `_scored`, nine cases, all correct:

    SUB TOTAL / 1,046.24            net=104624   total=None       D3 holds
    GRAND TOTAL / 1,234.56          total=123456 at 0.50          D6 holds
    Total Cost / 100.00             total=10000  at 0.50
    Total des / 100.00              total=10000  at 0.50          D7 holds
    SUB TOTAL + GRAND TOTAL both    net=104624 AND total=123456   D3 holds
    SUB TOTAL then GRAND TOTAL      total=123456, label refused   D4 holds
    TOTAL 1,020.70 (same line)      total=102070 at 0.90          unchanged
    GST / 188.32                    tax=18832
    TOTAL/100.00 TOTAL/200.00       all None, ambiguous           D5 holds

### Two defects found while building, both real

**A version number read as a confident date.** `Version 1.2.34` returned
2034-02-01 with `ambiguous=False` and `why=''`. Also `Clause 1.2.34`,
`Cheque 000123 dt 1.2.34`, `Ref 5.10.15`, `Challan 3.4.56`, `E-way 1.23.45.678`.
Fixed by requiring a four-digit year when the separator is a dot. Cost:
`DD.MM.YY`, a format never on the accepted list. Commit `647ea03`.

**A false ambiguity hid every clearly-stated total.** Searching the total family
found `GRAND TOTAL` and then two survivors — the figure below it and the figure
ABOVE it, which is the previous field's value. Two survivors is an ambiguity, so
a bill printing its total on its own line came back with no total. Fixed by
dropping `previous_line` (a value is printed after its label) and by running
every filter before the survivor count rather than after.

### Decisions as implemented

| # | decision | where it lives |
|---|---|---|
| 1 | no geometry on `freeocr.Word` | `pagereader._page_words` builds `box=None` |
| 2 | no pixel constant | `Limits(max_line_distance=1)`, no pixel argument |
| 3 | families separate | `_belongs_to_another_family`, separate searches |
| 4 | field-aware next-line guard | `_is_a_label`, `_EVERY_FAMILY` |
| 5 | ambiguity preserved | survivor count `!= 1` returns nothing |
| 6 | next-line capped at 0.5 | `BY_POSITION` via `Reading.at_most` |
| 7 | multilingual labels | `Total des` reads; no currency converted |
| 8 | scripts gitignored | `.gitignore`, 7 entries |

### Ignored measurement scripts (Decision 8)

    scripts/find_indian_documents.py      scripts/measure_corpus.py
    scripts/measure_indian_accuracy.py    scripts/measure_ocr_scanned.py
    scripts/corpus/                       --01.png    --1.png

Confirmed before ignoring: no production code, no test fixture any test imports,
no secret. `scripts/measure_field_slots.py` is deliberately NOT ignored — it is
the approved measurement command and it is tracked.

Repo-wide after: `ruff check .` **All checks passed**; `ruff format --check .`
**343 files already formatted**. Both were red before (150 errors, 10 files).

### The exact remaining blocker

**287 of 300 slots match no label at all.** Until that number moves, no
value-location work can move the field count, because there is nothing to locate
a value *from*. The next honest step is to find out WHY those labels do not
match on these documents — whether the pages carry no label, or carry one the
vocabulary does not know, or carry one the engine mangled past recognition. That
is a measurement, not a build, and it has not been done.

---

## Delivery record — 2026-08-15

    branch          cage/safety-layer
    target          main  (default branch, protected)
    local HEAD      0b2d229
    remote HEAD     0b2d229   verified with `git ls-remote origin`
    pull request    #63, OPEN, mergeable, base main, head 0b2d229
    main on GitHub  2e86a7e   UNCHANGED - nothing merged
    commits ahead   135 not on main

**32 commits existed only on this laptop before this push.** They are now on the
remote. That was the largest unmanaged risk in the project and it is closed.

### CI on the pushed commit

    pr-fast / lint          pass
    pr-fast / format        pass
    pr-fast / typecheck     PASS - 0 errors (was 1, fixed in 0b2d229)
    pr-fast / changed-tests FAIL
    ci-gate                 FAIL - because pr-fast failed

`changed-tests` fails on the SAME 173 failures as locally, and on the same one
cause: the cage narrows outcomes that pre-cage tests assert as VALID
(`assert <Outcome.NOT_VALID> is <Outcome.VALID>`, "nothing posted, so nothing to
undo"). No new failure and no failure in any file this work touched.

### NOT MERGED, and deliberately

This is REVIEW_REQUIRED, not NOT_GREEN-by-accident. Accounting behaviour changed:
954 drafts reach the gate and 954 block. Whether the 173 tests are stale or the
cage is too strict for a one-sentence typed entry is an owner decision that has
not been made, and editing 173 tests until they pass would bury it.

No force push. No branch protection bypassed. No admin merge.
