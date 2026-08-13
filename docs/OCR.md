# Reading pixels: Tesseract, and what it is and is not good for

**Every number on this page was measured on 2026-08-13 against
`tesseract 5.5.3 / leptonica 1.87.0` on macOS arm64, not predicted.** Where a
number came from a run, the command that produced it is written beside it.

## Status: half landed, and the half that is missing is named

`accountant/extract/freeocr.py` exists and holds everything about reading that
is **not** the engine call: the confidence proxy, the `-1` marker rule, the
media-type gate, the refusal sentences, and the rule that a field with no
confidence carries no value.

The engine call itself is **not in this repository**. It cannot be:
`tests/test_no_reader.py` forbids a third-party import anywhere in
`accountant/extract/`, and forbids `pyproject.toml` declaring any runtime
dependency at all. So `freeocr.PageReader` is an **injected** transport, the
same shape `service.ServiceExtractor` already uses, and the ten lines that
satisfy it live in the deployment. They are written out below. See
"The blocker, measured".

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

## The reader the deployment supplies

`freeocr.PageReader` is `(bytes, media type) -> Reading`. The media type it
receives is always one of `freeocr.READABLE_MEDIA` — five constants written in
that module — never a string a caller chose. This is the whole of it:

```python
import io
import pytesseract
from PIL import Image
from pytesseract import Output
from accountant.extract.freeocr import Reading, Word

WORD_ROW = 5          # image_to_data levels 1-4 are page/block/para/line
DEADLINE_SECONDS = 8  # bounded; pytesseract kills the subprocess at the bound

def read_page(data: bytes, _media: str) -> Reading:
    page = Image.open(io.BytesIO(data))
    rows = pytesseract.image_to_data(
        page, output_type=Output.DICT, timeout=DEADLINE_SECONDS
    )
    words = [
        Word(text=rows["text"][i], confidence=rows["conf"][i])
        for i in range(len(rows["text"]))
        if rows["level"][i] == WORD_ROW
    ]
    return group_into_fields(words)   # the deployment's own field detection
```

Three things about that snippet are load-bearing:

- **`output_type=Output.DICT`, not the default text.** MEASURED: the text
  output prints `conf` as `92.372406`; the DICT output floors it to the integer
  `92`. `field_confidence` refuses anything that is not an `int`, so the
  conversion has to have already happened. Flooring is also the safe direction —
  rounding a confidence up is inventing certainty.
- **`timeout=`.** `pytesseract` kills the subprocess at the bound and raises
  `RuntimeError("Tesseract process timeout")`. Re-raise it as
  `freeocr.EngineTimedOut` so the person is told to try a smaller picture
  rather than to install software they already have.
- **Fixed argv.** `pytesseract` builds an argument *list*, never a shell
  string, and nothing a person uploads reaches it: the bytes go to a temporary
  file the wrapper owns and the flags are constants.

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

## The blocker, measured

Putting a real `pytesseract` call in `accountant/extract/` breaks **four**
assertions in `tests/test_no_reader.py`. Two were measured by planting the
module and running the suite; two by evaluating the guard's own helpers against
the `pyproject.toml` the approved dependencies would need.

| Assertion | Why it fires | How measured |
|---|---|---|
| `test_the_extraction_package_imports_nothing_a_reader_would_need` | `{'freeocr.py': ['PIL', 'pytesseract']}` — neither is stdlib nor `accountant.*` | planted, ran, FAILED |
| `test_no_module_in_the_extraction_package_names_the_work_of_reading` | `pytesseract` contains the reader word `tesseract` | planted, ran, FAILED |
| `test_the_project_declares_no_runtime_dependency_at_all` | needs `dependencies = ["pytesseract>=0.3", "Pillow>=11.0"]`; the guard asserts `== []` | evaluated, FAIL |
| `test_no_dependency_in_any_group_names_a_document_reader` | `pytesseract>=0.3` contains the reader word `tesseract` | evaluated, FAIL |

Two guards did **not** fire, and that is worth recording: the package starts no
subprocess of its own and opens no file, so `REACHES_OUTSIDE` and
`FORBIDDEN_CALLS` stay clean — and they stay clean under the injected design
permanently, not by luck.

**The last two are not fixable by moving the file.** They are repo-wide: no
location in this repository can host a declared `pytesseract` dependency while
`test_no_reader.py` stands. The guard says so itself, at
`tests/test_no_reader.py:430` — *"A new one is either a reader or the door a
reader walks through; either way it is an owner decision and not a test
change."* `dependencies = []` is load-bearing in two more places:
`tests/test_upload.py:107` and `tests/test_phase5b_readiness.py:744`, where the
`--no-deps` install is described as safe only because of it.

**This blocks `accountant/extract/textlayer.py` (`pypdf`) identically** on the
runtime-dependency guard. It is a phase question, not a Tesseract question:
`test_no_reader.py` was written for Phase 7, when the correct answer was that
no reader existed. Retiring or re-scoping it is the owner's call, and nothing
here was weakened to route around it.
