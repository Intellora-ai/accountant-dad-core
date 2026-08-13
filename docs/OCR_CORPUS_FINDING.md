# The `s2_extraction` gate cannot be met, and the reader is not the reason

**Dated 2026-08-13. Measured with the real Tesseract binary against the real
corpus. Not predicted, not estimated.**

## The one sentence

`s2_extraction` requires **76 exact field matches out of 80 renderable cases**,
and **40 of those 80 contain no readable image** — so the highest score any
reader can honestly reach is about **40/80**, and no choice of OCR engine
changes that.

> **CORRECTED 2026-08-13, after the readers were wired and the harness actually
> scored them. Two things below are wrong, and `docs/EXTRACTION_MEASURED.md`
> carries the measured version.**
>
> 1. **The scored 80 is every type except JPG, not every type except DOCX.**
>    DOCX *is* in the denominator and contributes 0/20; the 20 JPEGs are the
>    ones held out — `scripts/run_ground_truth.py` says so beside
>    `EXIT1_RENDERABLE_CASES`. The ceiling table at the bottom is right about
>    the number and wrong about which four types make it up.
> 2. **"20 TXT exact — existing regex path" is false.** The TXT tier scores
>    **0/20** and returns **20 fabricated totals**. The corpus `.txt` files are
>    the same invoice layout as the PDFs, not typed sentences, and `typed_text`
>    reads `INVOICE NO: GT/0001` as a total of one rupee. Measured, not argued.
>
> The headline — about 40 of 80 achievable against a required 76 — survives
> both corrections by coincidence rather than by the reasoning given here. What
> is actually achievable today is **20**.

## What is actually in the corpus

100 documents, 20 of each type. The harness scores the 80 that are
"renderable" — every type except JPG.

| Type | n | What the bytes contain | Readable? |
|---|---|---|---|
| TXT | 20 | plain text | **yes, exactly** |
| PDF | 20 | a real text layer, **uncompressed** — `BT /F1 9 Tf … (TAX INVOICE) Tj` | **yes, exactly** |
| PNG | 20 | a hand-built **5×7 bitmap font**, uppercase only, ~10 px line pitch, 384×129 for a whole invoice | **no** — see below |
| JPG | 20 | JFIF header plus **COM comment segments**. No `SOF0`, no `SOS`. **Zero pixels.** | **no — there is nothing to read** |

The JPEG finding is not an inference. Parsing the marker stream of
`GT-0061.jpg` gives `APP0/JFIF` then thirteen `COM` segments and nothing else.
`render_jpg_container` in `scripts/build_ground_truth.py:906` says so in its own
docstring: *"A JFIF-framed container with the text in COM segments and NO image
data."*

## What Tesseract actually read

`brew install tesseract`, then the binary against two real corpus PNGs. Left is
the truth, right is what came back:

```
INVOICE NO: GT/0041                    ->  TWoIte Not eT/a081
DATE: 2038-05-15                       ->  Dares g038-05-15.
SUPPLIER: ADVANCED PROPULSION CENTRE
          UK LTD                       ->  AQUANCED PROPULSION CENTRE UK LTO
HSN/SAC: …                             ->  Forshee oes)
PLACE OF SUPPLY: 27                    ->  PEACE oF SUFPL'

SUPPLIER: SHARMA TRADERS               ->  SHARMA, TRADERS
```

`TAX INVOICE` is the only line that survives intact. On exact-match scoring —
which is what the harness does — **party, date and amount score zero** on this
path. `SHARMA, TRADERS` is the instructive one: a single invented comma, and the
field is wrong.

## Why this is not a Tesseract problem

Tesseract is an LSTM trained on real typefaces at roughly 300 DPI. The corpus
draws a **5×7 bitmap font at one pixel per stroke**, about 10 pixels per line.
That is below the floor of every OCR engine in existence, free or paid. Swapping
in PaddleOCR, EasyOCR, docTR, Azure Document Intelligence or AWS Textract would
not move this number, because the input is not degraded text — it is a different
kind of object that happens to look like text to a human.

And no engine reads the JPEGs at all, because they contain no image.

**So this is not risk R-2 ("Tesseract is too inaccurate").** R-2 was about a
weak reader. This is a corpus that was never an OCR benchmark: it was built to
test the **plumbing** — does the pipeline accept a PNG, a JPEG, a DOCX, and
route each correctly? For that job a 5×7 font and a pixel-free JFIF container
are perfectly good, cheap, and dependency-free, which is very likely why they
were chosen. The `s2_extraction` threshold of 76/80 was written for documents
that can be read.

## The honest ceiling

```
20 TXT   exact          existing regex path
20 PDF   exact          pypdf, text layer verified uncompressed
20 PNG   ~0 exact       bitmap font, measured above
20 JPG   0              no pixels exist
----
~40 / 80 achievable     76 required
```

A reader hitting 40/80 here is a reader **working correctly**. The two tiers it
can read, it reads perfectly.

## What I did not do, and why

**I did not move the threshold.** `docs/ARCHITECTURE.md:671` forbids *"tuning a
threshold so a metric passes"* (the line was cited as 616 here until
2026-08-13; the line numbers had drifted), blocker B-C is still standing, and
the owner's
standing rule 10 is "never set a number I did not give you." Moving 76 to 40
would make the harness green and would tell the owner nothing true.

**I did not read the JPEG COM segments as if they were extraction.** The text is
sitting right there in the comment segments and parsing it would score 20/20 on
that path in about six lines of code. It would also be reading a label we wrote
ourselves and calling it document extraction — precisely the self-deception this
whole safety cage exists to prevent. If that path is ever added it must be
labelled metadata, never `source="ocr"`.

**I did not regenerate the corpus.** Rendering the images with a real TrueType
font would turn them into a genuine OCR benchmark and is probably the right
answer — but it changes what a passing harness means, and that is the owner's
call, not mine.

## The three options, for the owner

1. **Regenerate the image corpus with a real font.** Makes `s2_extraction`
   measure what it claims to. Costs a font dependency in the generator and a new
   set of expected values. The JPEGs would need real image data too.
2. **Split the gate.** Score the text-layer path and the OCR path separately,
   with their own numbers, so a perfect text-layer reader is not marked failing
   by an OCR corpus that cannot be read. Needs two new thresholds, which are
   the owner's to set.
3. **Leave it failing and say why.** `s2_extraction` stays red, this page is the
   explanation, and nothing in the product pretends otherwise.

Until one is chosen, the harness stays at four sections PASS and
`s2_extraction` FAIL, and the reason is written down here rather than left as a
red mark somebody later "fixes".

## How I would have caught this sooner

The plan assumed the corpus was an extraction benchmark because the harness
scored extraction against it. Nobody had opened an image. The cheap check —
`tesseract GT-0041.png -` — takes ten seconds and settles it, and it should have
run before the reader was designed, not after. That is the same lesson as the
stale `.coverage` artefact earlier in this build: **a number that describes an
artefact is not a measurement of the artefact.**
