# The readers are on the live path

**2026-08-13.** Every number on this page came out of a run on this machine at
`cage/safety-layer`. Nothing here is predicted and nothing is carried over from
an earlier page.

The question this page answers is narrow and was asked in exactly these words:
**what does the extractor the application actually calls read off a PDF, a PNG
and a JPEG?** Not the best backend in the registry — the default one, reached
the way an upload reaches it.

---

## The one line that changed

```
accountant/extract/registry.py:173    DEFAULT_BACKEND: Final = "ladder"
```

It was `"typed_text"`. That single name is the whole live path:

```
app.py:3022      d = _run(sent.data, sent.media_type)
app.py:2581      pipeline.build_draft(company, data, mime, live.extractor, ...)
app.py:1444      extractor = guarded(default_extractor() if extractor is None else extractor)
registry.py:325  default_extractor() -> build(DEFAULT_BACKEND)
```

`app.py:340` has always let `application/pdf`, `image/jpeg` and `image/png`
through the door. Before this change they were let in and handed to a backend
that parses a sentence a person typed — so the upload was **accepted and then
unread**. The door was never the bug. The name behind it was.

---

## Before and after, on the three files

Both columns are measured. The BEFORE column is `registry.build("typed_text")`
run today against the same bytes, not a memory of an older run.

| file | bytes | mime | before — `typed_text` | after — `ladder` | rung that answered |
|---|---|---|---|---|---|
| `GT-0021.pdf` | 1269 | `application/pdf` | 0 of 4 | **4 of 4, all correct** | `pdf_text_layer` |
| `GT-0041.png` | 1456 | `image/png` | 0 of 4 | 1 of 4 read, and that one is **WRONG** | `free_ocr` |
| `GT-0061.jpg` | 610 | `image/jpeg` | 0 of 4 | 0 of 4, refused with a reason | `free_ocr` |

Field by field, after:

| file | `date` | `party` | `total_paise` | `tax_paise` |
|---|---|---|---|---|
| `GT-0021.pdf` | `2026-09-21` CORRECT | `BALFOUR BEATTY VINCI JV - HS2 (N2)` CORRECT | `58410` CORRECT | `8910` CORRECT |
| `GT-0041.png` | not_found | `AQUANCED PROPULSION CENTRE UK LTO` **WRONG** @0.30 | not_found | not_found |
| `GT-0061.jpg` | not_found | not_found | not_found | not_found |

The truth for `GT-0041` is `ADVANCED PROPULSION CENTRE UK LTD`. The engine read
`ADV` as `AQU` and the final `D` as `O`. **That is a wrong value, not a
success**, and it is written here as one.

---

## Reproduce it

Self-contained. Paste it whole.

```bash
cd /Users/tanveersidhu/ACCOUNTANT && .venv/bin/python - <<'PY'
import pathlib
from accountant.extract import registry

DOCS = pathlib.Path("artifacts/ground_truth/documents")
CASES = [("GT-0041.png", "image/png"), ("GT-0061.jpg", "image/jpeg"), ("GT-0021.pdf", "application/pdf")]
FIELDS = ("date", "party", "total_paise", "tax_paise")

ex = registry.default_extractor()
print("DEFAULT_BACKEND =", registry.DEFAULT_BACKEND)
for name, mime in CASES:
    rec = ex.extract((DOCS / name).read_bytes(), mime)
    print(f"\n{name}  {mime}  ->  backend {rec.backend}")
    for f in FIELDS:
        v = getattr(rec, f)
        c = rec.per_field_confidence.get(f)
        src = rec.per_field_source.get(f, "").split(":")[0]
        print(f"  {f:<12} {'not_found' if v is None else v!r:<38} src={src} conf={'-' if c is None else c}")
    print("  read_exactly(party) =", rec.read_exactly("party"))
PY
```

What it printed here, verbatim:

```
DEFAULT_BACKEND = ladder

GT-0041.png  image/png  ->  backend free_ocr
  date         'not_found'                            src=not_found conf=0.0
  party        'AQUANCED PROPULSION CENTRE UK LTO'    src=free_ocr conf=0.3
  total_paise  'not_found'                            src=not_found conf=0.0
  tax_paise    'not_found'                            src=not_found conf=0.0
  read_exactly(party) = False

GT-0061.jpg  image/jpeg  ->  backend free_ocr
  date         'not_found'                            src=not_found conf=-
  party        'not_found'                            src=not_found conf=-
  total_paise  'not_found'                            src=not_found conf=-
  tax_paise    'not_found'                            src=not_found conf=-
  read_exactly(party) = False

GT-0021.pdf  application/pdf  ->  backend pdf_text_layer
  date         datetime.date(2026, 9, 21)             src=pdf_text_layer conf=1.0
  party        'BALFOUR BEATTY VINCI JV - HS2 (N2)'   src=pdf_text_layer conf=1.0
  total_paise  58410                                  src=pdf_text_layer conf=1.0
  tax_paise    8910                                   src=pdf_text_layer conf=1.0
  read_exactly(party) = True
```

The corpus-wide version of the same question:

```bash
.venv/bin/python scripts/run_ground_truth.py
```

---

## What now works

**A PDF with a text layer is read completely and exactly.** All four fields,
sourced `pdf_text_layer`, stated at 1.0. This is the tier `EXACT` was defined
for: it reads the characters the producing program wrote, so there is no
estimate anywhere in it. Corpus-wide, 20 of 20 PDFs.

**A PNG reaches a reader at all.** Before, `image/png` was refused by the
router. It now reaches `free_ocr`, and across the twenty corpus PNGs the
supplier comes back **exactly right on 3, wrong on 5, and refused on the other
12**.

**A refusal now carries the reason in the owner's own words.** The pixel-free
JPEG does not come back blank; it comes back saying *this file says it is a
picture but there is no picture inside it, so there is nothing on it to read.*

**A misread name cannot become a supplier.** `read_exactly("party")` is
**False** for the PNG even though a value is present, because `free_ocr` is not
in `ENTITLED_TO_EXACT`. `pipeline.py:320` reads that flag and drops the name.

Measured through the live `pipeline.build_draft` — the same call `app.py:2581`
makes — rather than argued from the constants:

```
GT-0041.png
  voucher.party        : ''
  voucher.amount_paise : 0
  provenance[party]    : not_found: free_ocr estimated this name rather than
                         reading it, and an estimated name is never used as a
                         supplier's identity - so this one is being asked about
                         instead

GT-0021.pdf
  voucher.party        : 'BALFOUR BEATTY VINCI JV - HS2 (N2)'
  voucher.amount_paise : 58410
  voucher.date         : 2026-09-21
  voucher.gst_paise    : 8910
  provenance[party]    : pdf_text_layer
```

`AQUANCED PROPULSION CENTRE UK LTO` **does not reach the voucher at all**, and
the draft carries the reason in the owner's words instead of a blank. The wrong
value dies one function after the reader, before memory, before the detectors
and before the cage — which is why the cage's floors are not what is holding
this. Reproduce it with `tests/test_estimated_party.py`'s `_memory()` fixture and
the snippet above.

---

## What does not work

**A photograph of a bill is still mostly unread.** On `GT-0041` the reader got
one field out of four, and got it wrong. `date`, `total_paise` and `tax_paise`
all come back `not_found` with `free_ocr reported no word here that carries a
confidence`.

**A DOCX reaches no rung at all.** 20 of the 100 corpus cases. No reader here
opens one.

**The ground-truth gate `exit1_generated_truth_extraction` FAILS**, and that is
the honest state, not a broken harness. It requires 76 exact matches per field
across 80 renderable cases and measures `{date: 14, party: 23, total_paise: 20,
tax_paise: 20}`. Almost all of that shortfall is the 20 DOCX and the 20
pixel-free JPEGs, neither of which any reader can open.

**5 fields across the whole corpus come back WRONG rather than unread.** All
five are `party`, all five are `free_ocr`, and `docs/EXTRACTION_MEASURED.md`
names each one beside its truth. `date`, `total_paise` and `tax_paise` are wrong
zero times.

---

## The honest accuracy statement

**These numbers say almost nothing about a real photograph from a phone.**

- **The corpus PNGs are drawn in a 5x7 bitmap font.** They were generated by
  `scripts/build_ground_truth.py`, not photographed. Every glyph is on a pixel
  grid, perfectly level, with no lens, no shadow, no crease, no skew, no JPEG
  ringing and no motion blur. The `ADV` -> `AQU` misread is what a clean
  synthetic render costs; a real photograph costs more, and by an amount nobody
  here has measured.
- **The corpus JPEGs contain no pixels.** They are JPEG containers with the
  invoice text in a metadata segment and no image inside. The reader correctly
  refuses them. **That refusal is a container check passing, not evidence about
  reading photographs**, and it must never be quoted as an OCR result.
- **The corpus PDFs carry real text operators**, which is why they read at 4 of
  4. That result generalises to any PDF a program produced — and to no scan and
  no photograph, because those have no text layer to read.
- **The whole corpus is labelled `SYNTHETIC_EVIDENCE` and `GENERATED_TRUTH`.**
  The truth was written first and the document was rendered from it. It tests
  the adapter contract and the plumbing. It is not, and was never built to be,
  evidence about reader accuracy in the world.

**n of real bills is still 0.** Not one photograph of one real invoice from one
real phone has been put through this. Every accuracy figure on this page and in
`docs/EXTRACTION_MEASURED.md` is measured against documents this repository
generated for itself. `H-02` stays open, and the only thing that closes it is
real bills.

---

## What would change my mind

Written down first, so the next run can settle it rather than argue about it.

| claim here | what would prove it wrong |
|---|---|
| the PDF rung is exact | a PDF with a text layer whose read field disagrees with its truth |
| a misread name cannot reach the books | a corpus case where `free_ocr` reads `party` wrong AND `read_exactly` returns True |
| the JPEG refusal is a container check, not OCR | a JPEG with actual pixels that this rung also refuses |
| the shortfall is DOCX + pixel-free JPEG | the same gate still failing after those 40 cases are excluded |

The second row already has a test standing on it:
`tests/test_pagereader.py::test_no_corpus_png_produces_a_wrong_field_at_a_confidence_that_auto_posts`.
