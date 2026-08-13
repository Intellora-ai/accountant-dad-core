# Real-bill validation — the limitation, stated plainly

**Owner ruling, 2026-08-13, verbatim:**

> All current test images are synthetic and not representative of real bills.
> OCR accuracy on real bills is unmeasured (n = 0). The first real bill photo
> will be used in a future task to validate real-world OCR performance.

This is not a blocker and is not to be treated as one. The reader path is wired,
tested and shipped against the synthetic corpus. Real-bill validation is a
**future task**.

## Why the synthetic corpus says almost nothing about a real photograph

Measured, not asserted — see [`OCR_CORPUS_FINDING.md`](./OCR_CORPUS_FINDING.md)
for the full evidence:

| Corpus type | n | What the bytes actually are | What that means for OCR |
|---|---|---|---|
| PNG | 20 | a hand-built **5×7 bitmap font**, uppercase, ~10 px per line, 384×129 for a whole invoice | below the floor of every OCR engine. Real Tesseract returns `INVOICE NO: GT/0041` as `TWoIte Not eT/a081` |
| JPG | 20 | JFIF header plus **COM comment segments**. No `SOF0`, no `SOS`. **Zero pixels** | Pillow raises `UnidentifiedImageError`. There is nothing to read |
| PDF | 20 | a real, uncompressed text layer | reads exactly — but through `pypdf`, not through OCR |
| TXT | 20 | the same invoice layout as the PDFs, not typed sentences | exercises the text path, not the image path |

A phone photograph of a real bill differs from all four on every axis that
matters to an OCR engine: real typefaces, real anti-aliasing, real resolution,
skew, shadow, creases, thermal-print fade, and a layout no generator produced.

## What is therefore claimed, and what is not

**Claimed:** the *path* works. An uploaded PDF or image reaches a reader that can
read it, rather than a regex reader that cannot. That is demonstrable and
demonstrated.

**Not claimed:** any accuracy figure on a real supplier bill. There is no such
figure, because n = 0.

By the rule of three — 0 errors in n trials bounds the true rate at 3/n — even a
flawless run over the whole 100-case synthetic corpus would bound the real error
rate at **3%**, and that bound would be about synthetic documents, not real ones.
To claim **1%** needs 300 real bills read cleanly; to claim **0.1%** needs 3,000.

## The future task, when a real photo exists

1. Add the photograph to a corpus that is kept separate from
   `artifacts/ground_truth/` — real and synthetic must never be blended into one
   number, because a blended number is not a measurement of either.
2. Record the truth for it by hand: date, party, total, tax.
3. Run the shipped default reader and record per field: **exact**, **unread**, or
   **wrong**. The third column is the one that matters — an unread field is safe
   and a wrong field at high confidence is the failure the cage exists to stop.
4. Report the number as measured. Do not tune a threshold to reach it;
   `ARCHITECTURE.md` forbids it and the owner's standing rule is "never set a
   number I did not give you."

Until step 1 happens, every accuracy statement in this repository is about
generated documents and says so.
