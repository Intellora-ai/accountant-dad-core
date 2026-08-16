# Reading pixels: Tesseract, and what it is and is not good for

**Every number on this page was measured on 2026-08-13 against
`tesseract 5.5.3 / leptonica 1.87.0` on macOS arm64, not predicted.** Where a
number came from a run, the command that produced it is written beside it.

## Status: the engine call is real; one piece above it is deliberately absent

`accountant/extract/freeocr.py` holds the engine call — `read_words` — and
everything the safety of a reading depends on: the confidence proxy, the `-1`
marker rule, the media-type gate, the bounded wait, the refusal sentences, and
the rule that a field with no confidence carries no value.

It could not have held any of that before **2026-08-13**. `tests/test_no_reader.py`
forbade every third-party import in `accountant/extract/` and required
`pyproject.toml` to declare `dependencies = []`. **`D-30` in
[`DECISIONS.md`](./DECISIONS.md) lifted it by name**: `freeocr.py` may import
`pytesseract` and `PIL`, `textlayer.py` may import `pypdf`, and nothing else may
import anything. Widening that list is still an owner decision.

**What is NOT built, on purpose: nothing turns a list of words into "this one is
the total".** See "The gap that is left" at the bottom. It is not an oversight
and it is not a small piece of work; it is the piece that cannot be checked
without labelled data.

## Which tool, and why this one

**Tesseract, driven through `pytesseract`.** Three reasons, in the order they
matter:

| Property | Tesseract | EasyOCR / PaddleOCR |
|---|---|---|
| **Deterministic** — same bytes, byte-identical output | **yes, measured below** | not guaranteed; the neural engines sample |
| **Per-word confidence** | **yes** — `image_to_data` returns a `conf` column, 0–100 | per-line or per-box, engine-dependent |
| **Install size** | `pytesseract` is a ~30 KB subprocess wrapper over a system binary | a PyTorch install, ~2 GB |

Determinism is the load-bearing one. This system's claim is that a refusal is
reproducible: the same bill read twice produces the same verdict, or an audit
row means nothing. An engine that samples cannot promise that.

Determinism is **not** accuracy. An engine that misreads the same digit the
same way ten times running is exactly as wrong and perfectly reproducible.
Accuracy needs the labelled corpus (`H-02`) this repository does not have.

### Determinism, measured

Four images, **10 consecutive runs** each, SHA-256 over the engine's output:

| Input | Result |
|---|---|
| `artifacts/ground_truth/documents/GT-0041.png`, `--psm 6 tsv` | **1 distinct output** (`fed6fe77902c21c5…`) |
| clean synthetic invoice, 1000×620, `image_to_data` | **1 distinct output** (`f671bfa9994b6b33…`) |
| the same page scaled to a third — a bad phone photo proxy | **1 distinct output** (`beed9515f4012c65…`) |
| the same page under 40 000 speckle marks | **1 distinct output** (`036622ea3a401d21…`) |

**Byte-identical, 10 out of 10, in every condition including degraded ones.
There is no variation to document.** If this stops being true,
`tests/test_freeocr.py::test_the_engine_itself_answers_identically_ten_times_on_the_same_bytes`
is what says so — and it **skips loudly** on a machine with no engine rather
than passing on nothing.

Timing, same runs: 0.065 s to 0.131 s per page. A bounded wait of a few seconds
is two orders of magnitude of headroom, not a tight fit.

## Installing the binary

`pytesseract` is a wrapper. The engine is a system package and must be
installed separately, or every read refuses.

```sh
# Linux (Debian/Ubuntu)
apt-get install -y tesseract-ocr

# macOS
brew install tesseract

# Windows
choco install tesseract
```

## The engine call, and the three things about it that matter

```python
from accountant.extract.freeocr import FreeReader, Reading, read_words

words = read_words(image_bytes, deadline_seconds=8)  # -> tuple[Word, ...]
reader = FreeReader(my_page_reader)  # -> Extractor
record = reader.extract(image_bytes, "image/png")  # -> ExtractedRecord
observed = reader.observe(image_bytes, "image/png")  # -> Observation, with scores
```

- **`image_to_data`, `output_type=DICT`.** MEASURED: the engine's text output
  prints the confidence as `92.372406`; the DICT output floors it to the integer
  `92`. `field_confidence` refuses anything that is not an `int`, so the
  conversion has to have already happened. Flooring is also the safe direction —
  rounding a confidence up is inventing certainty. `read_words` **does not**
  coerce the column itself; `_complaint` refuses a float by name, so a change in
  the wrapper shows up as a refusal rather than as a plausible number.

- **Only level 5 rows survive.** `image_to_data` reports a hierarchy: 1 page,
  2 block, 3 paragraph, 4 line, 5 word. Levels 1–4 always carry the marker.
  Filtering at the source means a caller cannot forget to.

- **`deadline_seconds` has no default, and that is the one number this
  page does not set.** A bounded wait is required; the bound belongs to the
  deployment, which knows its own page sizes. Measured headroom: a page takes
  0.065–0.131 s, so any bound above a second or two is generous. **The
  production value is an owner setting and nothing here invents one.**

Every argument is a **fixed argument list, never a shell string** — read off
`pytesseract.run_tesseract`, which builds a python `list` and hands it to
`subprocess.Popen` with no shell. This module passes an **empty** config, so it
contributes no argument at all: there is no string for anything to be
interpolated into, and no page segmentation mode is forced. The media type is
matched against `READABLE_MEDIA` before anything runs, so what crosses the
boundary is one of five constants, never a caller's header.

## The confidence proxy, exactly

Computed by `accountant/cage/confidence.py::field_confidence`. `freeocr.py`
feeds it and never re-implements it.

```
field_confidence = min(word_conf)/100  ×  format_valid  ×  consistent
```

- **`min`, not mean.** One misread digit ruins an amount. A mean of 0.99 and
  0.40 is 0.70, which reads as "worth asking about" rather than "certainly
  wrong". A field is exactly as trustworthy as its least legible part.
- **`format_valid` is a hard multiplier**, not a penalty. The amount goes
  through `adapter._to_paise`, which is exact `Decimal` and refuses anything it
  cannot hold in whole paise; the date goes through
  `confidence.looks_like_a_date`. Tesseract can be entirely certain it read
  `2026-13-45`, and that certainty is about pixels, not about whether the thing
  is a date.
- **`consistent` is a hard multiplier too**, and it is the conservation law
  `net_plus_tax_equals_gross`, run over the three amounts the page printed. A
  `FAIL` zeroes **both** money fields. An `INDETERMINATE` — the bill printed no
  net — does **not**: "could not check" is not "checked and wrong", and
  blocking on it belongs to the decision layer, which already treats it as
  blocking.
- **No usable word score → `0.0`.** Not a low score. No score.
- **`0.0` means no value.** One rule, so the record and the observation cannot
  disagree: a field is carried only when a confidence above zero can be stated
  for it. Everything else is `None`, `0.0`, and a sentence.

### `-1` is a marker, never a score

Tesseract writes `conf = -1` on every row that carries no score — every
structural row, and every word row it found no text in.

- passed through, `min()` returns `-1` and `field_confidence` **raises**. Loud,
  and correct, but a raised exception in the reading path is an HTTP 503 page.
- coerced to `0`, a perfectly-read field silently scores `0.0`.
- dropped and the rest averaged, a word the engine actually reported is
  discarded and the score describes the words that are left.

So `freeocr.py` does none of the three: a field with a marker among its words
**has no score**, and says so in the sentence the person reads.

MEASURED: `GT-0041.png` gives **31 scored words and 21 marker rows**; the
synthetic invoice gives 24 scored and 19 markers. The markers are roughly
40% of what a naive reader would treat as scores.

## Known limitations — stated so nobody relies on them

**Handwriting is not supported and is not claimed.** Tesseract is trained on
typefaces. A handwritten amount is not a low-confidence reading; it is outside
what the engine does. This product does not read handwritten bills and does not
pretend to.

**Low-quality photographs degrade, and the proxy is what catches it.** Skew,
shadow, phone-camera blur and low resolution all lower per-word confidence.
That is the mechanism working: a degraded photo produces a low score, the score
fails the band, and the person is asked instead of being told a number.

**Small or non-standard type falls off a cliff, not a slope.** Measured on this
repository's own corpus PNGs, drawn in a 5×7 bitmap font at roughly 10 px per
line: `SUPPLIER: SHARMA TRADERS` came back as `SHARMA, TRADERS` — one invented
comma, and on exact-match scoring the field is simply wrong. The lowest word
confidence on that page was **0**, so `field_confidence` returns **0.0** and
the document is refused. The proxy fails closed on this input with nothing
tuned. Full evidence: `docs/OCR_CORPUS_FINDING.md`.

**A confident misread is not detectable here.** Tesseract reporting 96 on a
digit it got wrong is failure mode F-02, and no score computed from the
engine's own opinion of itself catches it. This is why confidence alone never
authorises a post: the decision layer also requires every conservation law to
pass, the party to be known and the period to be open.

**PDF is refused, not split.** `READABLE_MEDIA` is five image types. The engine
does not take a PDF, and a page-splitter is a second component nobody has
chosen.

## How it fails, and why each failure is a sentence and not a crash

| What happened | What the person gets |
|---|---|
| binary not installed | every field `not_found`, confidence `0.0`, "the text reading program is not installed on this machine" |
| binary present but not executable | "this machine will not let us run the text reading program" |
| read exceeded the bounded wait | the subprocess is killed at the bound; "the text reading program did not finish in time" |
| the engine crashed some other way | "the text reading program could not read this file (`ValueError`)" |
| the file is not a picture we read | refused **before** the engine is called, naming the type that arrived |

The application **starts and refuses** on a machine with no engine. That is
structural rather than handled: `accountant/extract/freeocr.py` imports only
the standard library and `accountant.*`, so there is no import in it that can
fail. `extract` never raises, because `pipeline.build_draft` has no `try`
around it and an exception there is a 503 telling somebody the application
broke when the truth is that a program is not installed.

`KeyboardInterrupt` and `SystemExit` are deliberately **not** caught. That is
somebody stopping the process, and answering it with a tidy record would fight
them.

## The blocker, and how it was lifted

Before `D-30`, putting a `pytesseract` call in `accountant/extract/` broke
**four** assertions in `tests/test_no_reader.py`. Two were measured by planting
the module and running the suite; two by evaluating the guard's own helpers
against the `pyproject.toml` the dependencies would need.

| Assertion | Why it fired |
|---|---|
| `test_the_extraction_package_imports_nothing_a_reader_would_need` | `{'freeocr.py': ['PIL', 'pytesseract']}` — neither is stdlib nor `accountant.*` |
| `test_no_module_in_the_extraction_package_names_the_work_of_reading` | `pytesseract` contains the reader word `tesseract` |
| `test_the_project_declares_no_runtime_dependency_at_all` | the guard asserted `project.dependencies == []` |
| `test_no_dependency_in_any_group_names_a_document_reader` | `pytesseract>=0.3` contains `tesseract` |

`D-30` replaced the blanket ban with an **allow-list**: two reader modules by
name, three runtime dependencies by name, one `subprocess` exception for
`freeocr.py`, and everything else exactly as forbidden as before. No assertion
was deleted. Two guards never fired and still do not: this module opens no file
and evaluates no code.

## The gap that is left — CLOSED 2026-08-13

**Something decides which words are the total now.**
`accountant/extract/pagereader.py`, and it is not a heuristic.

`read_words` returns every word on the page and `read_lines` returns the same
words grouped into the lines the engine reported them on. `Reading` says which
words make up each field. The function between them runs the **same label logic
the PDF rung already uses** — `TOTAL`, `GRAND TOTAL`, `AMOUNT PAYABLE`, the
vocabulary now shared in `accountant/labels.py` — over those lines. A
number with no label on it is not a total on a photograph any more than it is
in a PDF, which is the defect `adapter.TYPED_TEXT_MIME` records twenty times.

**The confidence difference survives the join, by construction.** A text-layer
field is `confidence.EXACT`, which is 1.0, because there is nothing to be unsure
about. The page reader computes no confidence at all: it reports WORDS, each
carrying the engine's own 0-100 score, and `freeocr._judge` runs them through
`field_confidence` — the worst word, times format validity, times the
conservation law.

### What was said to be needed first, and what actually happened

1. **`H-02`, the labelled corpus.** It existed, in a narrow form nobody here had
   used for this: `artifacts/ground_truth/` carries 80 cases with expected
   fields, 20 of them PNGs. So the reader is **measured** rather than asserted
   about. `H-02` remains open for REAL customer bills, and no number below is a
   claim about those.
2. **The deadline.** `accountant/extract/pagereader.py::READING_DEADLINE_SECONDS`
   is 30 seconds. It is the number this repository already uses everywhere it
   waits on something outside the process, and it is a BOUND rather than a
   target: the slowest of the twenty corpus PNGs reads in 0.151s.
3. **Registration.** `registry._READY` carries `free_ocr`, and
   `tests/test_adapter_contract.py` asserts `registry.available()` is exactly
   seven names. `ladder.py` routes all five of `freeocr.READABLE_MEDIA` to it.

### What it measures, on the corpus, through the wired path

| | |
|---|---|
| fields with a value, of 80 | 8, all of them the supplier |
| exactly right | 3, at confidence 0.48, 0.61 and 0.74 |
| wrong | 5, at 0.48, 0.30, 0.16, 0.10 and 0.08 |
| refused | 72 |

Nothing comes back wrong at a confidence that would auto-post, and that is the
number that matters: the band is 0.95. A reader that reads nothing is never
wrong and is also never useful.

The corpus PNGs are rendered in a **5x7 bitmap font** and the engine mostly
cannot read it. It was **4 with a value, 2 right, 2 wrong, 76 refused** until
2026-08-13, when `labels.Printing` let this rung tolerate a mangled SEPARATOR.
`GT-0041.png` read nothing at all before that, over one character: its
`SUPPLIER:` comes back as `SUPPLIER?`. It now reads `AQUANCED PROPULSION CENTRE
UK LTO` against a truth of `ADVANCED PROPULSION CENTRE UK LTD` — **read and
wrong, at 0.30**, which is the intended outcome and not a regression. A
misreading with a score on it is one the cage can block or ask about; the same
misreading unread is invisible.

Only the separator is tolerated, never the label word and never the value. The
engine reads `SUPPLIER:` as `SUPPLIERS` on eight of the twenty PNGs and those
eight stay **unread**: a plural and a mangled colon are the same character, and
guessing between them would read `SUPPLIERS OF FINE GOODS` as a supplier. The
amount and date labels are destroyed as WORDS on these pages — `TOTAL` comes
back as `For.` and `DATE:` as `Dares` — so those fields did not move by one.

**No image processing is done, and that is a decision with evidence behind it.**
Scaling the pictures up was tried: at 2x the engine reads GT-0041's supplier
perfectly, at 3x it returns `ADUANCED`, at 4x it returns `pec`. A factor chosen
by which one flatters the corpus is fitted to the corpus, and interpolation
invents ink that was never on the page. Page segmentation mode was tried too —
`--psm 4`, `6` and `11` give byte-identical field results to the default.

### What is wired — CORRECTED 2026-08-13

This section read "What is still not wired, and it is not a code change", and
said two things that had both stopped being true:

- `registry.DEFAULT_BACKEND` is **`ladder`**, not `typed_text`. Uploading a
  document to the running application does reach this code, and every image
  goes to it. The reason is written under that constant.
- **The container image installs the engine.** `tesseract-ocr` and
  `tesseract-ocr-eng`, owner decision 2026-08-13, recorded in the `Dockerfile`
  and asserted by `tests/test_deploy_artefacts.py`. The old sentence — "installs
  no `tesseract` binary, on purpose and by a test that says so" — was true until
  that day and is now the opposite of what the test asserts.

On a machine without the binary this backend still answers
`freeocr.ENGINE_MISSING` — a refusal in plain words, not a crash. That property
is unchanged and is what makes the engine a requirement of the *deployment*
rather than of the *code*.

What is still not settled is not wiring: it is accuracy. The corpus numbers
above are poor, `H-02` is open, and no real photographed bill has been through
this.
