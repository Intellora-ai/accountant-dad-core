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
| Documents in `data/` that are not invoices | 333 of 413 | hand classification, recorded in `accountant/invoicelike.py` |
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

## 6a. Three things running it found, before any real document arrived

Not predictions. Each was caught by a test on the day the code was written.

### 1. `BILL NO` cannot be an invoice-number label — FIXED

`labels.values_for` anchors a label to the start of a line **or to a run of
spaces**. On the line

```
E-Way Bill No: 481920375566
```

the characters `Bill No` follow a space and match. With `BILL NO` in the
invoice-number vocabulary, `labels.the_one` then saw two different invoice
numbers, refused the disagreement, and **the bill's number read as nothing — on
every e-invoice**, which is every invoice above the e-invoicing turnover
threshold.

The matcher was right and the vocabulary was wrong, so the vocabulary changed.
`BILL NO` and `BILL NUMBER` are out. Cost: a bill printing only `Bill No:` has
its number unread and a person is asked. That is the cheaper mistake.

### 2. `invoicelike.py` reports a currency inside the word "hours" — NOT FIXED

The currency signal's pattern is `₹|Rs\.?|INR` with **no word boundary**, so the
`rs` inside `hours`, `boards`, `collectors` and every other such word matches.
A museum catalogue page was reported as *printing a currency*.

It did not change the verdict — one signal is not two — but the **signal list a
person is shown named a currency that is not on the page**, and the signal list
is the evidence behind a refusal.

`accountant/invoicelike.py` is not this package's file to edit. The fix
is a word boundary on that one alternative. Recorded here and in
`tests/invoice_documents.py`, where the fixture is worded around it.

### 3. `splitlines()` then `"\n".join()` silently dropped a trailing newline

`Reading.text` was reconstructed from the split lines, which loses a trailing
newline. That newline is part of what the reader returned, and the raw text is
the **only** useful evidence when somebody disputes a figure months later. A
batch test comparing a report against the reading it was built from caught it.
`Reading.text` is now a stored field carrying the characters verbatim.

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
- **No memory between runs.** The batch runner is idempotent **within one
  run**, by file hash and by supplier-plus-invoice-number. Across runs it
  remembers nothing, because nothing in this repository stores either.
  `accountant/memory/` indexes vendors and narration phrases and claims
  *operation ids* at the write boundary; there is no table keyed by supplier and
  invoice number, and this package did not invent one. Running the same folder
  twice produces two identical reports and neither knows about the other.

---

## 8. Known limitations, listed so nobody has to find them

| Limitation | Why it is there | What it costs |
| --- | --- | --- |
| A single GSTIN with no `Supplier:`/`Bill To:` heading is left **unassigned** | Almost always the supplier's is not a fact about *this* bill, and the wrong answer puts somebody else's input credit on a supplier ledger | one question on a screen |
| A day/month-ambiguous date (`09/08/2026`) is **refused** | Two readings, nothing on the page chooses, and choosing files a return in the wrong month | the person is asked for `YYYY-MM-DD` |
| Line items need a **table header** | Without one, the only way to know the third number is the rate is to assume a column order | a bill with no header yields no line items |
| A table row with a **blank column** is refused, not shifted | A shifted row still multiplies out — a wrong answer that passes its own arithmetic | that row is not read |
| A reading rebuilt from a word list has **no column gaps** | Any single-spaced line does; the real `extract/pagereader.py` has the same property | two fields on one line may merge |
| A party name printed under a bare heading is read **positionally** | `Bill To:` then the name is how most bills print it | a bill printing its address first reads an address as a name; the field says `BELOW_A_HEADING` so the record carries the warning |
| Amount in words is **recorded, never converted** | A words-to-number table is a second money parser nobody has verified | no cross-check against the figure |
| Only the **first** table on a page is read | Reading the last would make the answer depend on where somebody scrolled | a second table is read as rows of the first |
| `enough_characters=200` and `legible_share=0.5` are **unmeasured** | Shape arguments, gathered on `Thresholds` so a caller can replace them | see section 3 |
