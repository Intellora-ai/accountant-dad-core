# Reading an invoice: what is built, and what nobody has measured yet

**Written 2026-08-15, before a line of the code it describes.** That order is
deliberate. A note written afterwards describes what was built; a note written
first states what the build is allowed to claim, and this repository has already
been burned once by a corpus that measured its own generator
(`artifacts/ground_truth`, and `docs/EXTRACTION_MEASURED.md` records the
correction).

---

## 1. The one sentence

**Raw reading works. Field detection and document classification do not, and
the folder we would use to prove otherwise is not a folder of invoices.**

---

## 2. The corrected diagnosis

The original diagnosis was "the reader returns nothing". A repository
inspection on 2026-08-15 corrected it. Four facts, all measured, none
predicted:

| Fact | Number | How it was measured |
| --- | --- | --- |
| JPGs that return non-empty text | 82 of 106 | `tesseract` 5.5.3, by raw binary and again through `accountant/extract/freeocr.py` |
| Median characters returned per JPG | 107.5 | same run |
| Documents in `data/` that are not invoices | 333 of 413 | hand classification, recorded in `accountant/extract/invoicelike.py` |
| Files anywhere in `data/` carrying a GSTIN | **1** | and it is a GST appeal tribunal order, not a bill |

The reader reaches the pixels. What happens after that is where the loss is:
nothing downstream turns those 107 median characters into a supplier, an
invoice number, a tax split or a line item.

`docs/OCR_CORPUS_FINDING.md` already carries the reason the corpus looks like
this, and it is not an accident of collection. 300 of 422 documents came from
Wikimedia Commons and every licence recorded is a redistributable one. **The
corpus was selected for LICENCE.** Nobody licenses their own bills for
redistribution, so openly-licensed documents and real commercial invoices are
very nearly disjoint sets.

---

## 3. What this means for accuracy claims

**GST-specific extraction cannot be accuracy-validated against `data/`.**

There is one GSTIN in the whole folder. A GSTIN extractor scored against one
document is not measured; it is anecdote with a percentage sign on it. The same
argument applies, with the same force, to every field below:

| Field | Documents in `data/` that carry one | Can accuracy be claimed today |
| --- | --- | --- |
| GSTIN | 1 | **no** |
| IRN | 0 | **no** |
| e-way bill number | 0 | **no** |
| HSN / SAC code | 0 confirmed | **no** |
| CGST / SGST split | 0 confirmed | **no** |
| IGST | 0 confirmed | **no** |
| Line items with qty x rate | 0 confirmed | **no** |

**These fields REMAIN UNVERIFIED.** Not "approximately correct", not "expected
to work", not "validated on synthetic data". Unverified. Any sentence in any
document, docstring, dashboard or report that implies otherwise is the exact
defect this page exists to prevent.

### What CAN be claimed, and it is a different claim

The parser and the validator are **deterministic pure functions**. Given a
string, they return the same fields every time, on any machine, with no clock,
no network and no filesystem. That property is provable without a corpus, and
the tests prove it: same bytes twice, byte-identical output.

The accounting validation is stronger still. `qty x rate - discount = taxable
value` and `taxable + tax + round-off = grand total` are **conservation laws**.
They need no labelled data, no expert and no corpus - they hold or they do not.
That is why `accountant/cage/conservation.py` is called rather than re-written:
one comparison, one place, one answer.

So the honest split is:

- **PROVEN:** parsing behaviour, validation arithmetic, determinism, status
  mapping, batch idempotency, that no Tally write is reachable.
- **UNPROVEN:** that the parser finds the right fields on a real Indian GST
  invoice, because there is no such invoice here to try it on.

---

## 4. Why the implementation is still worth completing

Three reasons, and none of them is "it will probably work".

1. **The structure is what is missing, not the accuracy.** Today a document
   that returns 107 characters and a document that returns 0 characters produce
   the same downstream silence. That is a *design* defect, and it is fixable
   with no corpus at all: `OCR_FAILED` and `INVOICE_MISSING_FIELDS` become two
   different sentences on a person's screen.

2. **The validation is corpus-free by construction.** See above. It is the
   cheapest real safety available and it does not wait on fixtures.

3. **The fixture contract below means the day real documents arrive, nothing
   has to be redesigned** - the fixtures drop into a shape that already exists
   and the unverified column above starts filling in.

---

## 5. THE FIXTURE CONTRACT

This is the part somebody will need six months from now. It is written so a
person who has never seen this code can hand over usable evidence.

### What is needed

Redacted **captured reading output** - not photographs, not scans, not PDFs.
The reading has already happened by the time this framework sees anything, so
what the framework needs is what the reading produced:

1. **The text**, exactly as the engine returned it, newlines and all. Do not
   tidy it. A mangled colon is data.
2. **The word list**, if available: for each word, the characters and the
   engine's confidence, `0` to `100`. `-1` is tesseract's "no text here"
   marker; keep it as `-1`, never as a score.
3. **The expected answer**, field by field, written by a person who read the
   original document: supplier GSTIN, buyer GSTIN, invoice number, invoice
   date, each line's HSN/SAC, quantity, rate, discount and taxable value, the
   CGST/SGST/IGST/cess figures, round-off, grand total.
4. **What the document actually is** - a real invoice, or not one.

### What must be removed before it lands here

Real party names, real addresses, real phone numbers, real bank details, real
PANs, and any GSTIN belonging to a real registered person. Replace them with
values of the **same shape** - a fake GSTIN must still be 15 characters in the
GSTIN pattern, or it tests nothing.

### Where it goes

`tests/invoice_documents.py`, alongside the six that are already there, in the
same frozen-tuple shape. Add the expected answer to the same record. Nothing
else has to change.

### What lands with it

The table in section 3 gets a new column: **documents tested** and **exact
matches**. Until that column exists, the "no" in the last column stands.

---

## 6. What the six shipped fixtures prove, and what they do not

The six fixtures in `tests/invoice_documents.py` are **hand-written text in the
shape a reading engine produces**. They were not read off a photograph and no
photograph of them exists.

> **THEY PROVE PARSER AND VALIDATION BEHAVIOUR.**
> **THEY PROVE NOTHING ABOUT PRODUCTION OCR ACCURACY.**

They answer questions like "given this text, does the parser find the IGST
figure and refuse the CGST/SGST contradiction". They cannot answer "does
tesseract produce this text from a real bill", because nobody has ever handed
this repository a real bill to try.

A synthetic render is not a photograph. `artifacts/ground_truth` already
recorded what happens when that distinction is lost - a corpus measuring its
own generator, scoring 20/20 on a tier that was inventing every total. The
fixtures here carry the same warning in their own docstring, so it travels with
them.

---

## 7. What is NOT built, and why

- **No GSTIN checksum.** The shape is checked - 15 characters, 2 digits of
  state code, the 10-character PAN, an entity character, a literal `Z`, a check
  character. The checksum algorithm is **NOT IMPLEMENTED**, deliberately:
  `accountant/rules/place_of_supply.py` already states that the algorithm is not
  in any document this repository retrieved, and writing an unverified one would
  mean rejecting real registrations on arithmetic nobody has checked. A shape
  check can only ever reject, so being conservative there costs nothing.
- **No LLM, anywhere on the production path.** Not for parsing, not for
  classification, not for repair.
- **No new dependency.** `pypdf`, `pytesseract`, `Pillow`. Nothing else.
- **No Tally write.** The batch runner posts nothing. The write door and its
  approval are untouched.
- **No value is ever silently repaired.** A validation failure is *recorded*.
  A parser that mends a figure to make a law hold is a parser that hides the
  one thing the law was for.
