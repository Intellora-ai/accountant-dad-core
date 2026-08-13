# The ground-truth harness, measured

**Run fresh on 2026-08-13 at commit `0e27360`, branch `cage/safety-layer`, on
the main checkout. Python 3.14.6, pypdf 6.15.0, tesseract 5.5.3 installed and
working. Every number below was measured. None was estimated, and none was
carried over from a previous report.**

> **The tree moved while this was being measured.** Partway through, another
> agent landed `accountant/extract/ladder.py` and registered both it and
> `pdf_text_layer`. Every number below was re-measured afterwards and reflects
> the tree at that point. The headline did not change, and the reason it did not
> is the most useful finding here: **the readers landed, and the harness score
> stayed at 0**, because the harness never asks which backend exists.

Nothing in this document changes a threshold, and nothing in it asks for one to
be changed. `docs/ARCHITECTURE.md` §4.10, the **Forbidden** row of the
scoring-harness table, lists **"tuning a threshold so a metric passes"** as
forbidden — line 712 as of 2026-08-13, and the section is the reliable handle
because the line number has drifted twice. `tests/test_gst_ground_truth_runner.py:230` pins the
two numbers with the comment *"80 and 76 are owner-set, 2026-08-10. Neither is
derived, tuned or rounded."* The measurement is reported as it came out.

---

## The one sentence

The harness runs, measures honestly, and reports **FAIL (exit 1)** because
`s2_extraction` scores **0 exact matches on every field against a required 76 of
80** — a shortfall of **76 on each of the four fields** — and the direct cause is
that the harness is wired to a stub and never asks which backend exists.

---

## How to run it

```
/Users/tanveersidhu/ACCOUNTANT/.venv/bin/python scripts/run_ground_truth.py
```

One command. It writes `artifacts/ground_truth/results.json` and
`artifacts/ground_truth/results.md`, and exits:

| exit | meaning |
|---|---|
| 0 | every gate passed |
| 1 | a gate failed — the harness worked and the number was not good enough |
| 2 | the harness broke — nothing was measured, and no number may be quoted |

**Measured exit code: 1.** `harness_error` is empty and no gate is `BLOCKED`, so
every number in this document is a real measurement rather than a wiring
failure.

### It reproduces exactly

The committed baseline was produced on 2026-08-10, on a different branch
(`phase8/ground-truth-harness`), at a different commit, in a different worktree.
Comparing that file to the fresh run, ignoring only `generated_at` and
`provenance`:

```
committed 2026-08-10 run == fresh 2026-08-13 run: True
```

Same gates, same statuses, same numbers, byte for byte. The harness is not
flaky and it is not path-dependent.

---

## The five sections, as measured

26 gates in total: **24 PASS, 2 FAIL, 0 BLOCKED.**

| # | Section | What it measures | Now |
|---|---|---|---|
| 1 | `manifest` | the corpus is what it claims — every manifest entry present, every sha256 recomputed and matched, the GST rule-case file readable | **3/3 PASS** |
| 2 | `s2_extraction` | how well a reader turns a document into four fields, and whether an unreadable document fails safely | **1/3 PASS, 2 FAIL** |
| 3 | `gst_rules` | every production tax rule carries a notification number, a retrieval date, an effective date, a version and a citation, and no tax rule is fetched over the network at runtime | **7/7 PASS** |
| 4 | `gst_cases` | worked GST cases split correctly — intra-state into CGST + SGST/UTGST, inter-state into IGST — and refuse when evidence is missing or a rule is stale | **7/7 PASS** |
| 5 | `safety` | GST posting stays switched off, and a GST bill is refused by the application, by the decision layer and again at the connector | **6/6 PASS** |

### Section 1 — `manifest`

All three PASS. GST rule-case file sha256
`5b2e44fe065b6bb58f81dda423f671783215ad3cef0f6dbfae4628afe6fbdb2c`.

### Section 3 — `gst_rules`

All seven PASS. **15 rules loaded, 0 rejected.** HSN codes `2523`, `4820`; SAC
codes `9972`, `9987`. TDS sections 0, Schedule III heads 0.

One number here is a fact about the world rather than a pass: **8 sources could
not be verified**, all with the same error — `unable to verify the first
certificate` against `taxinformation.cbic.gov.in`. The corpus responds by
refusing every supply dated later than 2017-08-17 rather than returning a rate
that may have moved. The gate passes because refusing is the correct behaviour;
it does not mean the rates are current.

### Section 4 — `gst_cases`

All seven PASS: intra-state 20/20, inter-state 20/20, missing-evidence refusals
10/10, stale-or-conflicting-rule refusals 10/10, false-valid 0, guessed rates 0.

### Section 5 — `safety`

All six PASS. This is the section that keeps the product honest while extraction
is unbuilt, and it is green.

---

## Section 2 — `s2_extraction`, in full

Three gates, and they are three different claims.

| Gate | Status | Measured | Needed |
|---|---|---|---|
| `exit1_generated_truth_extraction` | **FAIL** | `{date: 0, party: 0, total_paise: 0, tax_paise: 0}` | **≥ 76 of 80 on every field** |
| `exit2_unrenderable_input_is_explicit` | PASS | 0 unsafe | every field of the 20 unreadable cases refused *with a reason* |
| `s2_extraction_scored` | **FAIL** | `{date: 0, party: 0, total_paise: 0, tax_paise: 0}` | every field of all 100 cases sourced |

Backend used by the harness: **`stub`**. Corpus label `SYNTHETIC_EVIDENCE`,
truth label `GENERATED_TRUTH`.

### The shortfall, stated plainly

**Required 76. Measured 0. Short by 76 on each of the four fields.** Not close,
not marginal — the harness is scoring a backend that reads nothing at all.

### The zeros are real zeros

A comparator that always answered "no match" would produce exactly these zeros,
so the zeros mean nothing until something that *should* score full marks does.
Running an oracle — a fake backend handed the answer key, which never looks at
the document — through the harness's own `field_matches`:

```
oracle answering from the truth : {date: 80, party: 80, total_paise: 80, tax_paise: 80}
oracle wrong on party           : party=0, others all 80
oracle wrong on total_paise     : total_paise=0, others all 80
```

The comparator works, and it is independent per field. **The measured zeros are
measurements, not a broken scorer.** This also settles a second question: the
76 threshold is reachable in principle — a perfect reader scores 80/80.

---

## The corpus

100 cases. The harness scores 80 of them for EXIT 1; the flag is
`renderable = input_type != "JPG"` (`scripts/build_ground_truth.py:1432`).

| Type | n | mime | fidelity | In the 80? |
|---|---|---|---|---|
| text | 20 | `text/plain` | `native_text` | **yes** |
| PDF | 20 | `application/pdf` | `text_operators` | **yes** |
| PNG | 20 | `image/png` | `rendered_raster` | **yes** |
| DOCX | 20 | `…wordprocessingml.document` | `native_text` | **yes** |
| JPG | 20 | `image/jpeg` | `container_only` | no — these are the 20 EXIT 2 cases |

> **Correction to `docs/OCR_CORPUS_FINDING.md`.** That file states the 80
> renderable cases are *"every type except DOCX"*. Measured, it is the opposite:
> **DOCX is inside the 80 and JPG is outside it.** The flag keys on `input_type
> != "JPG"`. That file is owned elsewhere and is not edited here, but the
> ceiling it derives is built on the wrong 80.

---

## Does a text-layer or OCR reader exist, and can it move `s2_extraction` off zero?

**Yes, two readers exist. No, neither is wired to anything.** Both live in
`accountant/extract/` and neither appears in `registry._READY`, so neither is
reachable by name and the harness cannot select one.

| File | Class | Registered? | Buildable from a name? |
|---|---|---|---|
| `accountant/extract/textlayer.py` | `TextLayerReader` (`pdf_text_layer`) | **yes**, as of 2026-08-13 | yes |
| `accountant/extract/ladder.py` | `Ladder` (`ladder`) — routes by media type | **yes**, as of 2026-08-13 | yes |
| `accountant/extract/freeocr.py` | `FreeReader` (`free_ocr`) | **no** | **no** — needs an injected `PageReader` |

`registry.available()` is now
`('ladder', 'no_reader', 'pdf_text_layer', 'stub', 'typed_text', 'unavailable')`,
and `DEFAULT_BACKEND` is still `typed_text` — deliberately, with the reason
written under it: routing PDFs to `pypdf` in the web process is an exposure
decision the owner has not made.

### Every backend, scored against the 80 renderable cases

Scored with the harness's own `field_matches`, so this is the EXIT 1 comparison
and not a second one that could disagree with it.

| Backend | date | party | total_paise | tax_paise | worst field vs 76 |
|---|---|---|---|---|---|
| `stub` *(what the harness uses)* | 0 | 0 | 0 | 0 | **−76** |
| `typed_text` *(DEFAULT_BACKEND)* | 0 | 0 | 0 | 0 | **−76** |
| `no_reader` | 0 | 0 | 0 | 0 | **−76** |
| `unavailable` | 0 | 0 | 0 | 0 | **−76** |
| `pdf_text_layer` | **14** | **20** | **20** | **20** | **−62** |
| **`ladder`** *(best available)* | **14** | **20** | **20** | **20** | **−62** |
| `free_ocr` *(unregistered, cannot be built)* | 0 | 0 | 0 | 0 | **−76** |

**So yes — `s2_extraction` can move off zero today, and by exactly 20 points on
three fields.** `TextLayerReader` reads all 20 PDF cases and gets party, total
and tax right on **20 of 20**, and the date right on 14 of 20.

`ladder` scores identically to `pdf_text_layer` rather than better, and that is
the measurement worth reading twice. It has two rungs — `text/plain` to
`TypedTextExtractor` and `application/pdf` to `TextLayerReader` — so it *should*
have added the 20 text cases. It adds nothing, because the text rung is the
backend that scores 0 and invents totals (see below). PNG and DOCX have no rung
at all.

**Best score available from any registered backend today: 14 of 80 on `date`,
20 of 80 on the other three. Required 76. Short by 62 on the worst field.**

And the harness reports none of it: re-running `scripts/run_ground_truth.py`
after the readers landed still prints `stub backend … {date: 0, party: 0,
total_paise: 0, tax_paise: 0}`. Two readers were registered and **the measured
number did not move by one point**, because of reason 1 below.

---

## Four measured reasons 76 is not reachable today

### 1. The harness never asks which backend exists

`scripts/run_ground_truth.py:380` is:

```python
extractor = StubExtractor()
```

The name is spelled into the runner. `accountant/extract/registry.py` exists
precisely so that the choice of backend lives in one place, and the harness does
not call it. **This is the first thing that would have to change, and on its own
it is one line.**

There is a second half to this. EXIT 1 scores **one** backend against all 80
cases, and the 80 span four media types, while every backend refuses the types
it does not handle. That needed a router, and as of 2026-08-13 there is one —
`Ladder`. **It is registered, it works, and the harness still cannot see it**,
because line 380 names a class instead of calling `registry.default_extractor()`
or taking a backend name as an argument.

This is the cheapest real movement available in the whole document: one line,
worth 20 points on three fields immediately.

### 2. Three of the four renderable tiers have no reader

Measured coverage of the 80:

| Tier | n | Reader that handles it | Best measured score |
|---|---|---|---|
| PDF | 20 | `TextLayerReader`, and `ladder` routes to it | party/total/tax **20/20**, date 14/20 |
| text | 20 | `TypedTextExtractor` — a rung exists, but it is wrong | **0/20** on all four, and it invents a total on 20/20 |
| DOCX | 20 | **none at all** — no rung, no reader | **0/20** |
| PNG | 20 | `FreeReader`, which cannot be built | **0/20** |

`grep` over `accountant/` for `docx`, `openxmlformats` or `zipfile` returns
**zero files**. There is no DOCX reader in the package.

The DOCX cases are not hard, though — the text is sitting in the zip in clean
runs:

```
'TAX INVOICE', 'INVOICE NO: GT/0081', 'DATE: 2026-09-25',
'SUPPLIER: CORNWALL COUNCIL', … 'TOTAL   1,893.90'
```

Same for `text/plain`. Both are the same line-and-label layout the PDF reader
already parses correctly.

### 3. The PNG tier cannot be read by the OCR that is installed

`FreeReader` needs a `PageReader` returning a `Reading` whose words are
**already grouped per field** (`date`, `party`, `total`, `tax`, `net`). Grouping
words into fields is field detection, which `freeocr.py` deliberately refuses to
do — its docstring calls it *"the third-party's job by this package's oldest
rule"*. `read_words` returns a flat list. **Nothing in the repository closes that
gap**, which is why `FreeReader` cannot be constructed by name.

Rather than write the missing grouper and measure a guess, the question was
bounded from above: **does the correct answer appear verbatim anywhere in the
engine's own word stream?** If the engine never emitted the string, no grouping
of its words can produce an exact match.

| Field | Present verbatim in the OCR output | of |
|---|---|---|
| date | **0** | 20 |
| party | **6** | 20 |
| total_paise | **0** | 20 |
| tax_paise | **0** | 20 |

The engine does run — 28 words came back from `GT-0041.png` — but the corpus
PNGs are drawn in a hand-built 5×7 bitmap font, and what comes back is garbled:

```
truth : INVOICE NO: GT/0041   ->  read: TWoIte Not eT/a081
truth : DATE: 2026-05-13      ->  read: Dares g038-05-15.
truth : ADVANCED … UK LTD     ->  read: AQUANCED PROPULSION CENTRE UK LTO
```

`LTD` read as `LTO` is enough to fail an exact party match on its own.

**20 PNG cases cannot be read. EXIT 1 allows 4 misses. This alone puts 76 out of
reach.**

*Caveat, stated because it is a real limit on this measurement:* this is
tesseract 5.5.3 with `ENGINE_ARGUMENTS = ""` — no page segmentation mode forced,
which `freeocr.py` chose deliberately. A different engine or a forced mode was
not measured. What is measured is that the reader **as configured in this
repository** reads none of the money and none of the dates.

### 4. Eighteen dates are ambiguous, and an honest reader must refuse them

All 6 PDF date misses are the same refusal, and it is a correct one:

```
not_found: 02/06/2026 is either the 2 of month 6 or the 6 of month 2,
and the document does not say which.
```

Not one of the 6 is a wrong answer — every one is a refusal. Counting the whole
renderable set for printed numeric dates where both readings are valid calendar
dates (`dmy_slash` or `dmy_dash`, day ≤ 12):

| Tier | ambiguous |
|---|---|
| PDF | 6 |
| DOCX | 6 |
| text | 4 |
| PNG | 2 |
| **total** | **18 of 80** |

The PDF figure of 6 matches the 6 refusals exactly, which is what makes this
count trustworthy rather than theoretical.

**A reader that refuses a date it cannot resolve caps at 62 of 80 on `date`,
even if every tier were perfectly readable. EXIT 1 needs 76.** No reader can
close this gap by reading better, because the information is not on the page.
(62 is the ambiguity limit alone. The ceiling table below shows 44, which is 62
minus the 18 PNG cases whose dates are unambiguous but which reason 3 shows
cannot be read at all: 20 PNGs, 2 of them already ambiguous, leaves 18.) This is an owner decision, not an engineering
one, and **the owner has not given a ruling on it** — so no convention is
assumed here.

---

## The ceiling, if every buildable reader were built

`TextLayerReader` and `Ladder` are now registered, so suppose the two remaining
gaps closed too: a correct text rung and a DOCX rung, both wired into `Ladder`,
and the harness pointed at it. Measured ceiling:

| Field | text | PDF | DOCX | PNG | ceiling | required | short by |
|---|---|---|---|---|---|---|---|
| party | 20 | 20 | 20 | **6** | **66** | 76 | **10** |
| total_paise | 20 | 20 | 20 | **0** | **60** | 76 | **16** |
| tax_paise | 20 | 20 | 20 | **0** | **60** | 76 | **16** |
| date | 16 | 14 | 14 | 0 | **44** | 76 | **32** |

The PNG column is the measured verbatim-presence ceiling; the date row also
carries the 18 ambiguous cases. **Even with perfect new readers for two whole
tiers, EXIT 1 still fails on all four fields.**

---

## The two gates that cannot both pass

`s2_extraction_scored` and `exit2_unrenderable_input_is_explicit` are in direct
contradiction, and this was proved by running the harness's own `run_s2` against
an oracle rather than by reading the code:

| Oracle behaviour | exit1 | exit2 | s2_extraction_scored |
|---|---|---|---|
| refuses the 20 unreadable JPGs | PASS | **PASS** | **FAIL** |
| sources every one of the 100 cases | PASS | **FAIL** (80 unsafe) | **PASS** |

- `s2_extraction_scored` requires every field of **all 100** cases to be sourced.
- `exit2` requires every field of the **20 unreadable** cases to be refused.

The same 80 field-instances are required to be both sourced and refused.
**No backend — not even one handed the answer key — can turn both green.** The
20 JPG cases carry no pixel data at all, so sourcing them would mean inventing
values, which is the exact failure `exit2` exists to catch.

`s2_extraction_scored` is therefore unpassable by any honest backend, and its
FAIL is a property of the gate rather than of any reader.

---

## A finding that is not about the harness

`registry.DEFAULT_BACKEND` is `typed_text`, and `accountant/web/app.py:1444`
resolves its reader through `default_extractor()`.

**Which route this affects, checked rather than assumed.** `UPLOAD_MEDIA_TYPES`
(`app.py:329`) is `{application/pdf, image/jpeg, image/png}` — `text/plain` is
deliberately absent, and `typed_text` refuses all three of those. So this does
**not** reach `/upload`. It reaches the **typed-sentence** route at
`app.py:3192`, which calls `_run(text.encode(), "text/plain")`.

Measured against the 80 renderable cases, split by whether a miss was a safe
refusal or an invented value:

| Field | exact | refused | **wrong, but sourced as read** |
|---|---|---|---|
| date | 0 | 80 | 0 |
| party | 0 | 80 | 0 |
| total_paise | 0 | 60 | **20** |
| tax_paise | 0 | 78 | **2** |

On **all 20** `text/plain` cases it returns a wrong total and labels the source
`typed_text`, meaning "I read this":

```
GT-0001  got total_paise 100     truth 14750   (₹1.00 against ₹147.50)
GT-0002  got total_paise 200     truth 16072
GT-0003  got total_paise 300     truth 17010
```

The pattern is the invoice number — `GT/0001` read as `1.00`. It takes the first
number it sees, and it does the same thing to a sentence a person would actually
type:

```
"bill no 7 from ACME, total 1,605.00"     -> total_paise 700      (₹7.00, truth ₹1,605.00)
"Invoice 0002 from Gupta Hardware,
 total 730.80"                            -> total_paise 200      (₹2.00, truth ₹730.80)
"paid 1450 to Sharma Traders …"           -> total_paise 145000   (correct)
```

Both wrong answers are sourced `typed_text`, so nothing downstream can tell them
from the correct one. This is reachable on the typed-sentence route today.

**`ladder` inherits this.** Its `text/plain` rung is the same
`TypedTextExtractor`, so it produces the identical `WRONG_BUT_SOURCED` 20 of 20
on the text tier, and the identical `700` for `"bill no 7 from ACME, total
1,605.00"`. The router did not introduce the bug and it does not fix it — it
carries it. That is why adding a text rung moved the score by zero.

EXIT 1 scores an
invented value and a safe refusal identically at zero, so the harness's zero
hides this completely, and `exit2` does not cover it because `exit2` only looks
at the 20 unreadable cases. `pdf_text_layer`, by contrast, produced **zero**
wrong-but-sourced values across all 20 PDFs — it refuses instead.

This is reported, not fixed. It is outside this measurement task's files.

---

## Exactly what would have to change for `s2_extraction` to pass

Ordered by what blocks what. **None of these is a change to 76 or to 80.**

1. **Wire the harness to a real backend.** `scripts/run_ground_truth.py:380`
   says `extractor = StubExtractor()`. One line. Until it changes, no reader in
   this repository can ever be measured, however good it gets — which is
   precisely what happened on 2026-08-13, when two readers landed and the
   reported score stayed at 0. Pointing it at `ladder` is worth **+20 on party,
   total and tax, and +14 on date, on the day it is done.**
2. ~~Register `TextLayerReader` and write a media-type router.~~ **Done
   2026-08-13.** `pdf_text_layer` and `ladder` are both in `registry._READY`.
   Nothing further is needed here.
3. **Fix or replace `typed_text`.** It is the production default and it invents
   a total on 20 of 20 text cases, and on typed sentences that carry a bill
   number. This is a correctness bug on a live route, independent of the
   harness, and it does not block 76 — it is simply the most serious thing this
   measurement turned up.
4. **Write a DOCX reader.** None exists. The text is in clean runs inside the
   zip, in the same layout `textlayer.py` already parses. Worth up to 20 cases.
5. **Owner decision on ambiguous dates.** 18 of 80 print a numeric date that does
   not say whether it is day-first or month-first. Either a locale convention is
   ruled on by the owner, or the corpus is regenerated without the ambiguity.
   **Until one of those happens the `date` field is capped at 62 of 80 and
   cannot reach 76, whatever the reader does.** No convention is assumed here,
   because the owner has not given one.
6. **A PNG tier that can actually read the PNGs.** The installed engine returns
   the correct money string 0 times in 20 and the correct date 0 times in 20.
   This needs a better engine, a re-rendered corpus in a real font, or an owner
   decision that the PNG tier is out of scope. **20 unreadable cases against a
   4-miss budget means this blocks 76 on its own.**
7. **Resolve the `s2_extraction_scored` / `exit2` contradiction.** As written the
   two cannot both pass. Somebody who owns the harness has to decide which claim
   `s2_extraction_scored` is making.

Items 5, 6 and 7 are decisions, not code. Items 1 and 2 are one line each and
are the cheapest real movement available.

---

## What this document does not prove

- **That any backend reads a real bill.** The corpus is `SYNTHETIC_EVIDENCE`
  and the answers are `GENERATED_TRUTH`, projected from canonical JSON. Real
  customer accuracy stays `NOT_MEASURED`.
- **That `pdf_text_layer` is good.** It is good *on 20 generated PDFs built by
  this repository's own generator*. That is not evidence about supplier PDFs.
- **That the OCR ceiling holds for other engines or settings.** It was measured
  with tesseract 5.5.3 and `ENGINE_ARGUMENTS = ""`. Nothing else was tried.
- **That the DOCX and text ceilings of 20/20 are achievable.** Those readers do
  not exist; 20 is what is *available* to be read, not what a written reader
  would score.
- **That the GST rates are current.** Section 3 passes because the corpus
  refuses to answer past 2017-08-17, not because the rates were verified — 8
  sources failed TLS verification.

---

## Reproducing every number here

```bash
cd /Users/tanveersidhu/ACCOUNTANT
.venv/bin/python scripts/run_ground_truth.py          # exit 1, 24 PASS / 2 FAIL
.venv/bin/python -m pytest -q -n auto
```

The suite was **4095 passed, 6 skipped, 4 xfailed, 0 failed** at the start of
this measurement, and **4144 passed, 4 failed** at the end. The 4 failures are
not from this work and not from the harness: `accountant/extract/registry.py` is
mid-refactor by another agent and is momentarily broken —

```
registry.py:196  "pdf_text_layer": TextLayerReader,
E  NameError: name 'TextLayerReader' is not defined
```

the imports having been moved into lazy factories at lines 163–172 while
`_READY` still names them at module level. Verified as not mine by removing
`docs/HARNESS_MEASUREMENT.md` and re-running: the same failure. **That file is
owned elsewhere and was not touched here.** The reader measurements above were
taken while the registry was working, and they come from the reader classes,
which the refactor does not change.

The per-backend scores, the refused-versus-invented split, the OCR ceiling, the
oracle control and the two-gate contradiction were each measured by a read-only
script that imports `run_ground_truth` unmodified and reuses its own
`field_matches`, so the comparison is EXIT 1's and not a second one that could
drift from it. No source file, test file or threshold was modified to produce
this document.
