# Extraction, measured

**2026-08-13. Every number here came out of a run on this machine against the
committed corpus. Nothing is estimated and nothing is carried over from a
prediction.**

Reproduce with:

```
.venv/bin/python scripts/run_ground_truth.py
```

**RE-MEASURED 2026-08-13, after PHASE 8 DECISION 1 and after the picture rung
was wired.** Two changes landed between the first run on this page and this one,
and both moved numbers here: invoice-shaped `text/plain` is now refused rather
than read for its first number, and `image/png` now reaches `free_ocr` instead
of being refused by the router. Every figure below is from the later run. Where
a number moved, the old one is stated beside it, because a page that quietly
restates itself cannot be checked.

---

## The number that matters most, first

**5 fields came back with a value and a source on them, and the value was not
the truth.** It was **22** before the two changes above, **2** before the
separator tolerance landed later the same day.

All five are `party`, all five are from `free_ocr`, and every one is an engine
misreading letters off a 5x7 bitmap font:

| field | wrong | was | where |
|---|---|---|---|
| `party` | **5** | 2 | `free_ocr`, GT-0041, GT-0046, GT-0050, GT-0055, GT-0056 |
| `total_paise` | **0** | 0 | — |
| `tax_paise` | **0** | 0 | — |
| `date` | **0** | 0 | — |

```
GT-0041  truth 'ADVANCED PROPULSION CENTRE UK LTD'
                 ->  'AQUANCED PROPULSION CENTRE UK LTO'    @0.30  free_ocr
GT-0046  truth 'GUPTA HARDWARE STORES'
                 ->  '“GUPTA HARONARE STORES'                @0.16  free_ocr
GT-0050  truth 'DECCAN LOGISTICS PVT LTD'
                 ->  'GECCAN LOGISTICS PUT LTO'             @0.10  free_ocr
GT-0055  truth 'UK HEALTH SECURITY AGENCY (UKHSA)'
                 ->  'UK HEALTH SECURITY AGENCY <UKHSAD'    @0.48  free_ocr
GT-0056  truth 'NARMADA PACKAGING CO'
                 ->  '"NARHAGR PACKAGING CO'                @0.08  free_ocr
```

**The rise from 2 to 5 is the tolerance working, not a reader getting worse.**
`labels.Printing` let the picture rung accept a mangled SEPARATOR - the engine
returns `SUPPLIER:` as `SUPPLIER?`, `SUPPLIER!`, `SUPPLIER®` or `SUPPLIER'` on
six of the twenty PNGs - so four more pages are now READ. One of the four is
read exactly and three are misread. Nothing about the engine changed and no
value is corrected anywhere.

**GT-0058 LEFT this list**, which is the safe direction. It prints its supplier
twice and the engine read the two printings as `IVER. ELECTRICALS` and `IVER
ELECTRICALS`. Exact matching saw only the one with a surviving colon and
answered it; both are visible now, they disagree, and `labels.the_one` refuses
rather than picking. A wrong value at 0.37 became a question.

**The 20 that went away, and why that is not a reader getting better.**
`adapter._AMOUNT.findall(text)[0]` took **the first number in the document**. On
a typed sentence — *"paid Sharma Traders 4200 for cement"* — that is the amount.
On anything laid out like an invoice it is the invoice number, and the backend
could not tell the two apart because it checked the **media type** and never the
**shape**: `text/plain` is `text/plain` whether a person typed one line or
pasted a whole bill. On GT-0001 it answered `total_paise = 100` — one rupee,
read off `GT/0001` — sourced `typed_text`, against a truth of `14750`.

The owner closed that. `adapter` now decides from the **shape** of the text, and
invoice-shaped text is refused in the owner's own words:

```
GT-0001  total_paise = None
source   'not_found: This document looks like an invoice, but the amount could
          not be reliably read. Please upload a clearer image or a proper PDF.'
```

**Nothing reads better than it did. Twenty wrong totals stopped reaching the
ledger.** The exact-match scores did not move at all — see the next table.

**The 5 that remain are a different animal, and the difference decides how
worried to be.** The 20 were a backend stating a source for a number it had no
business reading. These five are an engine reading a 5×7 bitmap font, getting
letters wrong, and **saying how unsure it is**: the worst of them is **0.48**
and the rest are 0.30, 0.16, 0.10 and 0.08, nowhere near the `0.95` that would
auto-post.
A value that is wrong and scored is a value a threshold can argue with; a value
that is wrong and unscored is outside the system built to catch it.

**Nothing gates the count.** `exit2_unrenderable_input_is_explicit` forbids a
fabricated value, but only on the 20 unrenderable JPEGs; both survivors are on
renderable cases, which no gate covers. Writing that gate means setting the
number a run may carry, and thresholds here are the owner's.
`tests/test_gst_ground_truth_runner.py` pins all four counts exactly, so a
rise fails a test rather than waiting to be noticed.

---

## The score

| field | exact of 80 renderable | required | wrong (all 100) |
|---|---|---|---|
| `date` | **14** | 76 | 0 |
| `party` | **23** | 76 | 5 |
| `total_paise` | **20** | 76 | 0 |
| `tax_paise` | **20** | 76 | 0 |

`party` was 20 exact / 0 wrong before the picture rung was wired and 22 / 2
before the separator tolerance. **Both halves of that move are the same
documents**: three of the twenty corpus PNGs have their supplier read exactly,
five more come back misread. A rung that answers gets both, and a rung that
refuses gets neither.

**The PDF row of the table below did not move by one field across either
change, and that is the control.** The tolerance is scoped by a `Printing` the
caller states; the text-layer rung states `EXACT_CHARACTERS` and matches a
colon and nothing else.

`s2_extraction` **FAILS**. Four other sections pass. The harness exits 1, which
is the benchmark working, not a broken run.

Wall clock for `scripts/run_ground_truth.py`, `/usr/bin/time -p`, two runs:
**2.43 s and 2.32 s** end to end, interpreter start included. It was 0.42 s when
no rung read a picture; the extra two seconds are twenty PNGs going through a
real engine and twenty JPEGs being refused before one is reached.

## Per input type

`exact / refused / wrong`, 20 cases of each type.

| type | mime | date | party | total | tax | rung that answered |
|---|---|---|---|---|---|---|
| text | `text/plain` | 0 / 20 / 0 | 0 / 20 / 0 | **0 / 20 / 0** | 0 / 20 / 0 | `typed_text` |
| PDF | `application/pdf` | 14 / 6 / 0 | 20 / 0 / 0 | 20 / 0 / 0 | 20 / 0 / 0 | `pdf_text_layer` |
| PNG | `image/png` | 0 / 20 / 0 | **3 / 12 / 5** | 0 / 20 / 0 | 0 / 20 / 0 | `free_ocr` |
| JPG | `image/jpeg` | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | `free_ocr` refused |
| DOCX | `…wordprocessingml.document` | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | `ladder` refused |

**The text row is the whole of PHASE 8 DECISION 1.** It read `0 / 0 / 20` on
`total` and `0 / 18 / 2` on `tax`; it now reads `0 / 20 / 0` on both. Nothing in
that row got read. Everything in it that used to be invented is now refused with
a sentence a person can act on.

**The PDF tier is perfect on everything it does not refuse.** 20/20 party,
20/20 total, 20/20 tax, and 14/20 date.

The six missing dates are GT-0030, 0031, 0034, 0035, 0037 and 0038, and every
one is refused for the same stated reason — an ambiguous numeric form:

```
GT-0034  06/10/2026  the 6th of month 10, or the 10th of month 6?
```

The document does not say which convention it used. `textlayer.py` refuses
rather than picks, and that is the correct answer, not a gap: a guessed date at
confidence 1.0 files a GST return in the wrong month with nothing on screen to
notice. Whether an Indian supplier's bill may be assumed `DD/MM` is an owner
decision about locale, not a fact a reader can read.

## The tier split

Which **rung answered**, not which backend was asked for. The router is
`ladder`; a case counted against `ladder` is one no rung below it could open.

| rung | cases it answered | of which exact |
|---|---|---|
| `pdf_text_layer` (text layer) | 20 | 74 field-hits of 80 |
| `free_ocr` (picture) | **40** | 2 field-hits of 160, 2 wrong |
| `typed_text` (regex) | 20 | 0 field-hits of 80, **0 wrong** |
| `ladder` refused outright | 20 | — the DOCX, which no reader here opens |

**The OCR tier answered nothing until 2026-08-13, and the engine was never the
reason.** `tesseract` 5.5.3 was installed and was being called for real; what
did not exist was a `PageReader` — something that says which words on a page are
the total, the tax, the date and the supplier — so `freeocr.FreeReader` had
nothing to inject and sat in `registry._NEEDS_WIRING` saying so.

`accountant/extract/pagereader.py` is that function now. It is not a heuristic:
it runs the **same label vocabulary the PDF rung uses**
(`accountant/extract/labels.py`) over the lines the engine reports. The 40
`free_ocr` cases are the 20 PNGs and the 20 JPEGs; the JPEGs are refused inside
the rung, because there are no pixels in them to read.

**Both halves of what wiring it bought are below**, and the section that follows
is now a measurement of a live path rather than of an engine called on the side.

---

## The engine, on its own

Measured by calling the engine directly on the corpus, before the rung was
wired, so the ceiling is a number rather than a guess. Kept because it bounds
what any field detector on top of this engine could reach — the wired rung
scores 2 exact against this bound of 6.

### PNG — 20 of 20 produced a reading

| | |
|---|---|
| engine | tesseract 5.5.3, `image_to_data`, no config |
| latency | min 42 ms, median 97 ms, max 132 ms |
| failures | 0 |

**Upper bound on exact matches, generous:** counting a field as *possible* if
the exact truth string appears **anywhere** in what the engine emitted — a bound
no real field detector would reach, because it does not have to find the string,
only not have lost it.

| date | party | total | tax |
|---|---|---|---|
| **0 / 20** | **6 / 20** | **0 / 20** | **0 / 20** |

Party at 6 is **better than predicted**. `docs/OCR_CORPUS_FINDING.md` expected
roughly zero on this path; six supplier names survive intact. Date, total and
tax are zero, as predicted.

What it actually reads. Truth on the left, taken from the case files, not from
another document:

```
GT-0041  party 'ADVANCED PROPULSION CENTRE UK LTD'  ->  AQUANCED PROPULSION CENTRE UK LTO
GT-0041  date  '2026-05-13'                         ->  Dares g038-05-15.
GT-0041  invoice number GT/0041                     ->  TWoIte Not eT/a081
GT-0042  party 'SHARMA TRADERS'                     ->  SHARMA, TRADERS
```

`SHARMA, TRADERS` is the instructive one: one invented comma, and on exact-match
scoring the field is wrong. The GT-0041 date is the frightening one — the engine
did not blur it, it returned a **different, plausible, well-formed date**, three
years and two days out.

### JPG — 0 of 20 produced a reading

All 20 raise `PIL.UnidentifiedImageError: cannot identify image file`. Not a
weak reading — **no image at all**. The files are a JFIF header plus COM comment
segments, with no `SOF0` and no `SOS`, which
`scripts/build_ground_truth.py:906` states in its own docstring. Pillow cannot
identify them, so no engine reaches a pixel, because there are none.

`FreeReader` turns that into a refusal rather than a traceback:
`UnidentifiedImageError` falls through `_REFUSAL_FOR` to *"the text reading
program could not read this file (UnidentifiedImageError)"*.

**The COM segments contain the answer.** Parsing them would score 20/20 on this
path in about six lines. It would be reading a label we wrote ourselves and
calling it document extraction. Not done, and if it is ever done it must be
labelled metadata and never `source="ocr"`.

---

## Confidence, correct against incorrect

**Still no distribution, and n is still not the reason. The axis is now real for
part of the data and flat for the rest.**

- **Correct fields, n = 76.** 74 from `pdf_text_layer`, every one
  `confidence.EXACT` = 1.0 by construction; 2 from `free_ocr`, and those two do
  carry a measured score. One constant plus two points.
- **Incorrect fields, n = 2.** Both from `free_ocr`, at **0.08** and **0.37**.

**What changed here is the finding, not the chart.** This section used to read
*"the 22 wrong fields carry no confidence, so no decision band can see them"* —
a wrong value with no score is outside the system built to catch it. Those 22
are gone, and the two that replaced them are the opposite case: both are scored,
both score low, and both land far below the `0.95` auto-post floor and below the
`0.70` ask floor in `accountant/cage/decision.py`. **A reader that is wrong and
says so is a reader the cage can stop.**

The remaining gap is `typed_text`, which produces no confidence at all —
`ExtractedRecord` has nowhere to put one and it has no `observe()`, only
`textlayer` and `freeocr` build an `Observation`. That no longer matters for
these numbers, because `typed_text` now returns **0 wrong fields**: it refuses
instead. It would matter again the moment it reads anything.

---

## Two things I was told to expect that did not hold

**1. "Roughly 40/80" — it is 20/80 on the fields that matter, and the 20 that
are missing are not the ones anyone thought.**

`docs/OCR_CORPUS_FINDING.md` puts the ceiling at `20 TXT exact + 20 PDF exact`.
The PDF half is right. **The TXT half scores zero.** The corpus `.txt` files are
not typed sentences — they are the same invoice layout as the PDFs, in plain
text — and `typed_text` was never built for that shape.

It also **fabricated twenty** when this page was first written. It does not any
more: the same shape test that stops it reading an invoice number as a total
makes it refuse the whole file. Scoring zero and inventing twenty look identical
in an exact-match column and are not the same event, which is why
`exit1_wrong_per_field` counts them apart.

**2. That document also says the 80 renderable cases are "every type except
DOCX". They are every type except JPG.** DOCX is *in* the scored 80 and
contributes 0/20; JPG is the 20 held out, which
`scripts/run_ground_truth.py:318` states directly. The ceiling table is
therefore right about the number and wrong about the composition. Corrected in
that file.

### And a defect the measurement found

Running `textlayer`'s parser over the plain-text corpus — a **counterfactual**,
nothing ships this route — scores date 20/20, total 20/20, tax 20/20, party
**19/20**. The one miss is real and reproduces through the **shipped**
`pdf_text_layer` backend on a real PDF:

```
SUPPLIER: NORTHERN TRAINS LIMITED HSN/SAC: 998311
party   -> 'NORTHERN TRAINS LIMITED HSN/SAC: 998311'
source  -> 'pdf_text_layer'                    (confidence EXACT, 1.0)
```

`_NEXT_LABEL` requires **two or more** spaces before the next label, so a
single-space separator is not seen and the party value runs on. A wrong supplier
name reaches `propose_account`, where a name that does not match history is a
**new vendor**.

It does not fire on the 20 corpus PDFs, because those print one label per line —
which is exactly why a corpus is not a guarantee. Not fixed here: the obvious
one-character fix (`\s{2,}` → `\s+`) was tried and is **worse**, because
`[A-Z0-9 /]*` then matches greedily from the first word of the supplier name and
truncates the party to empty. It needs its own change and its own tests.

---

## What these numbers do NOT establish

**They say nothing about real supplier bills.** Every document in this corpus
was generated by `scripts/build_ground_truth.py`. It is labelled
`SYNTHETIC_EVIDENCE` and `GENERATED_TRUTH` in the run output, and the labels are
load-bearing. A generated PDF has a clean uncompressed text layer, one label per
line, one supplier format and one date format. Real bills have none of that.
`S2 = NOT_MEASURED` against real documents is **unchanged by everything above**.
`H-02` — a pile of real bills whose answers somebody already knows — does not
exist here, and nothing on this page is a substitute for it.

**A perfect score on 20 documents bounds almost nothing.** By the rule of three:
if `n` trials produce zero failures, the 95% upper bound on the true failure
rate is about `3/n`.

| clean runs | 95% upper bound on the error rate | what that means |
|---|---|---|
| 20 | 15% | about 1 in 7 bills could be wrong |
| 100 | 3% | 1 in 33 |
| 300 | 1% | 1 in 100 |
| 3,000 | 0.1% | 1 in 1,000 |
| 30,000 | 0.01% | 1 in 10,000 |

**The 20 exact PDF reads bound the real error rate at 15%, at best.** And not
even that, because the bound assumes the trials are independent draws from the
population you care about — 20 documents from one generator are one draw
repeated twenty times, so the true bound is worse than the table says.

**Required n for the claims people will want to make:**

| the claim | needs |
|---|---|
| "it reads bills" | **≈ 100 real bills**, zero wrong → ≤ 3% |
| "safe to post without review" | **≈ 3,000 real bills**, zero wrong → ≤ 0.1% |
| "as good as a bookkeeper" | **≈ 30,000**, and a measured bookkeeper error rate to compare against |

Nobody would accept 3% on their books. The gap between what this corpus can
prove and what the product needs is **two orders of magnitude of real
documents**, and it is a data-collection problem, not a code problem.

**The threshold was not moved.** 76 of 80 stands. `docs/ARCHITECTURE.md` §4.10,
the **Forbidden** row of the scoring-harness table, forbids *"tuning a threshold
so a metric passes"* — line 712 as of 2026-08-13, and search the section rather
than the number, because it has drifted twice. Moving 76 to 20 would make the
harness green and tell the owner nothing true.

## What would move the number

Three, cheapest first. None is taken here.

1. **Route `text/plain` through the label parser instead of the regex.**
   Measured above as 20/20 on total, tax and date and 19/20 on party. It needs
   the party defect fixed first, and it changes what a typed sentence means.
   **Still an owner decision.** The 22 fabrications it would also have deleted
   are already gone by the cheaper route — refusing invoice-shaped text — so
   this item now buys exact matches only, and none of the safety it used to.
2. **Regenerate the image corpus with a real font, and the JPEGs with real
   pixels.** Turns `s2_extraction` into something that measures what it claims
   to. Costs a font dependency in the generator and a new set of expected
   values. **RULED 2026-08-13 — this is the named future task, and it is not
   authorised now.**
3. **Split the gate**, so a perfect text-layer reader is not marked failing by
   an OCR corpus that cannot be read. Needs two new thresholds, which are the
   owner's to set. **RULED 2026-08-13 — not now. Revisit only after item 2, when
   real data can set real numbers.**

**The owner closed the gate question on 2026-08-13, in these words:**

> "The OCR corpus is intentionally unreadable; s2_extraction is red by design
> for this MVP. A future task will regenerate a realistic corpus and revisit
> this gate."

So the `FAIL` above is the design, not a defect to be chased. **No threshold
moves and the gate is not split.** Full note:
[`OCR_CORPUS_FINDING.md`](./OCR_CORPUS_FINDING.md); index entry:
[`PROJECT_STATE.md`](./PROJECT_STATE.md) §51.1.
