# Extraction, measured

**2026-08-13. Every number here came out of a run on this machine against the
committed corpus. Nothing is estimated and nothing is carried over from a
prediction.**

Reproduce with:

```
.venv/bin/python scripts/run_ground_truth.py
```

---

## The number that matters most, first

**22 fields came back with a value and a source on them, and the value was not
the truth.**

It is not zero. Every one of the 22 is the same failure, in the same backend,
and that backend is the one the application runs today.

| field | wrong | where |
|---|---|---|
| `total_paise` | **20** | `typed_text`, on all 20 `text/plain` cases |
| `tax_paise` | **2** | `typed_text`, GT-0013 and GT-0019 |
| `date` | 0 | — |
| `party` | 0 | — |

What it does, on GT-0001:

```
document      INVOICE NO: GT/0001            (and TOTAL 147.50 eleven lines down)
returned      total_paise = 100              i.e. one rupee
source        "typed_text"                   not a refusal, not a blank
truth         total_paise = 14750
```

`adapter._AMOUNT.findall(text)[0]` takes **the first number in the document**.
On a typed sentence — *"paid Sharma Traders 4200 for cement"* — that is the
amount. On anything laid out like an invoice it is the invoice number. The
backend cannot tell the two apart, because it checks the **media type** and
never the **shape**: `text/plain` is `text/plain` whether a person typed one
line or pasted a whole bill.

This is a known number in one place already —
`tests/test_ground_truth_integrity.py` asserts `fabricated == 20` for
`total_amount` — and it was invisible in the harness, because
`exit1_exact_per_field` subtracts a refusal and a fabrication from the same
total. `exit1_wrong_per_field` now counts them apart.

**Nothing gates it.** `exit2_unrenderable_input_is_explicit` forbids a
fabricated value, but only on the 20 unrenderable JPEGs. All 22 fabrications are
on renderable cases, which no gate covers. Writing that gate means setting the
number a run may carry, and thresholds here are the owner's.

---

## The score

| field | exact of 80 renderable | required | wrong (all 100) |
|---|---|---|---|
| `date` | **14** | 76 | 0 |
| `party` | **20** | 76 | 0 |
| `total_paise` | **20** | 76 | 20 |
| `tax_paise` | **20** | 76 | 2 |

`s2_extraction` **FAILS**. Four other sections pass. The harness exits 1, which
is the benchmark working, not a broken run.

Wall clock for the whole harness: **0.42 s**. The extraction section is about
30 ms of that.

## Per input type

`exact / refused / wrong`, 20 cases of each type.

| type | mime | date | party | total | tax | rung that answered |
|---|---|---|---|---|---|---|
| text | `text/plain` | 0 / 20 / 0 | 0 / 20 / 0 | **0 / 0 / 20** | 0 / 18 / 2 | `typed_text` |
| PDF | `application/pdf` | 14 / 6 / 0 | 20 / 0 / 0 | 20 / 0 / 0 | 20 / 0 / 0 | `pdf_text_layer` |
| PNG | `image/png` | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | `ladder` refused |
| JPG | `image/jpeg` | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | `ladder` refused |
| DOCX | `…wordprocessingml.document` | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | 0 / 20 / 0 | `ladder` refused |

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

| tier | cases it answered | of which exact |
|---|---|---|
| `pdf_text_layer` (text layer) | 20 | 74 field-hits of 80 |
| `typed_text` (regex) | 20 | 0 field-hits of 80, 22 wrong |
| OCR | **0** | not wired — see below |
| `ladder` refused outright | 60 | — |

**The OCR tier handled zero cases, and not because the engine is missing.**
`tesseract` 5.5.3 is installed on this machine and was called for real. The tier
is not reachable from the product: `freeocr.FreeReader` takes an injected
`PageReader` — something that says which words on a page are the total, the tax,
the date and the supplier — and nothing in this repository does that. It is
field detection, it cannot be checked without `H-02`, and it sits in
`registry._NEEDS_WIRING` saying so rather than being quietly built.

---

## The engine, measured anyway

The tier is not wired; the engine is real and was run directly on the corpus, so
the ceiling is a number rather than a guess.

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

**No distribution can be drawn, and the reason is worse than the missing data.**

- **Correct fields, n = 74.** Every one is from `pdf_text_layer`, and every one
  carries `confidence.EXACT` = 1.0 by construction. One value, no spread.
- **Incorrect fields, n = 22.** Every one is from `typed_text`, which produces
  **no confidence at all**. `ExtractedRecord` has nowhere to put one, and
  `typed_text` has no `observe()` — only `textlayer` and `freeocr` build an
  `Observation`.

So the two populations do not share a scale, and a curve through them would be
drawing a line between a constant and an absence. **n is not too small; the
axis does not exist.**

The consequence is the finding, not the missing chart: **the 22 wrong fields
carry no confidence, so no decision band can see them.** A wrong value at 0.96
would at least be a number a threshold could argue with. A wrong value with no
score is outside the system that was built to catch it.

---

## Two things I was told to expect that did not hold

**1. "Roughly 40/80" — it is 20/80, and the 20 that are missing are not the
ones anyone thought.**

`docs/OCR_CORPUS_FINDING.md` puts the ceiling at `20 TXT exact + 20 PDF exact`.
The PDF half is right. **The TXT half scores zero and fabricates twenty.** The
corpus `.txt` files are not typed sentences — they are the same invoice layout
as the PDFs, in plain text — and `typed_text` was never built for that shape.

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

**The threshold was not moved.** 76 of 80 stands. `docs/ARCHITECTURE.md:671`
forbids *"tuning a threshold so a metric passes"*, and moving 76 to 20 would make the
harness green and tell the owner nothing true.

## What would move the number

Three, cheapest first. None is taken here; each is an owner decision.

1. **Route `text/plain` through the label parser instead of the regex.**
   Measured above as 20/20 on total, tax and date and 19/20 on party. It also
   deletes 22 fabrications outright. It needs the party defect fixed first,
   and it changes what a typed sentence means.
2. **Regenerate the image corpus with a real font, and the JPEGs with real
   pixels.** Turns `s2_extraction` into something that measures what it claims
   to. Costs a font dependency in the generator and a new set of expected
   values.
3. **Split the gate**, so a perfect text-layer reader is not marked failing by
   an OCR corpus that cannot be read. Needs two new thresholds, which are the
   owner's to set.
