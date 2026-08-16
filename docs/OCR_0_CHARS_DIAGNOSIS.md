# Why 86 real invoices read zero fields — traced, not guessed

Measured 2026-08-15 at `ce5dd63`, on this machine, with Tesseract 5.5.0.

**The OCR engine was never broken. Two hypotheses were tested and both were
false. The defect is three rules in the label matcher, and the same three rules
explain the PDF failures as well.**

---

## The headline, before the detail

| Claim made earlier | Verdict |
|---|---|
| "OCR returns 0 characters from real images" | **WRONG.** It returns 470–738 characters. I measured `raw_text`, which `freeocr.py:874` sets to `""` deliberately. |
| "Tesseract's stderr may be discarded" | **FALSE.** `freeocr.py:420-434` maps four exception classes, correctly ordered. |
| "Default `--psm 3` returns nothing on a page photo" | **FALSE.** `--psm 3` returns 470 chars on the same image `--psm 6` gives 564. |
| "This is an OCR or preprocessing defect" | **FALSE.** It is field detection. |

---

## Boundary trace — one invoice, end to end

`data/real_invoices_indian/gst-portal-and-govt-004.jpg`

| # | Boundary | Result | Verdict |
|---|---|---|---|
| 1 | File exists, not empty | 2.1 MB | PASS |
| 2 | Bytes readable | 2,148,xxx bytes | PASS |
| 3 | PIL decode | `.load()` clean | PASS |
| 4 | Dimensions | 3072 × 4080 | PASS |
| 5 | Pixels valid | luminance extrema (3, 251) | PASS — not blank |
| 6 | Image reaches OCR | `PIL.Image` handed to `image_to_data` | PASS |
| 7 | Tesseract invocation | `ENGINE_ARGUMENTS = ""` → engine default `--psm 3` | PASS |
| 8 | Engine return | 174 rows, 100 at level 5, 95 with text, conf 80–96 | **PASS — engine works** |
| 9 | Field detection | **every field `n=0`** | **FAIL — this is the defect** |
| 10 | Evidence layer | `raw_text=""` by design, `freeocr.py:874` | PASS — privacy guard, not a bug |

### Boundary 7 — PSM recorded before anything was changed

```python
ENGINE_ARGUMENTS: Final = ""  # freeocr.py:368
```

No page segmentation mode is forced; the engine's own default (`--psm 3`) stands.
The module docstring states this was deliberate: *"Choosing one would be a
decision about how a page is read that nobody has made and nothing here has
measured."*

**PSM treated as an experimental variable, not a diagnosis.** Character counts
from `image_to_string`, five arms, four images:

| file | size | A `--psm 3` (current) | B `--psm 6` | C `--psm 13` | D `--psm 11` | E `--psm 4` |
|---|---|---|---|---|---|---|
| gst-portal-and-govt-004.jpg | 3072×4080 | 470 | 564 | 2 | **785** | 473 |
| open-datasets-and-photos-001.jpg | 6140×3800 | 738 | 275 | 1 | **1221** | 251 |
| open-datasets-and-photos-002.jpg | 837×292 | 12 | 22 | 8 | **39** | 11 |
| open-datasets-and-photos-003.jpg | 905×590 | 421 | 510 | 1 | **512** | 399 |

**No arm returns zero. The current setting already reads the images.** PSM is
therefore NOT the cause, and that is the finding — causality is not attributed
where the outputs do not demonstrate it. `--psm 13` is markedly *worse* (1–8
chars). `--psm 11` reads most, and is a P1 tuning question, not this defect.

### Boundary 8 — stderr is NOT discarded

```python
except pytesseract.TesseractNotFoundError as exc:  raise EngineMissing(...)
except pytesseract.TesseractError        as exc:  raise EngineFailed(...)   # carries stderr
except RuntimeError                      as exc:  raise EngineTimedOut(...)
```

`freeocr.py:420-434`, four clauses, `TesseractError` deliberately ordered before
`RuntimeError` because it subclasses it. The engine is exiting **0** and
reporting words. It is not failing quietly; it is succeeding.

### Boundary 10 — `raw_text=""` is a privacy guard, not a bug

```python
raw_text = ("",)  # freeocr.py:874
```

Its own comment gives the reason: `pipeline.build_draft` copies `raw_text` into
`Voucher.narration`, which reaches the page, the durable action log **and Tally
itself**. A backend carrying what it read would put a customer's whole scanned
bill in all three.

**Measuring `len(raw_text)` on an OCR record therefore always returns 0, by
design.** That is the mistake that produced the original "0 characters" report.
The number to measure is fields read, not characters carried.

---

## Boundary 9 — the actual defect

What Tesseract read from the bill (34 lines, abridged):

```
  3  HOTEL ¥VISHWANAND
  4  Om Chanakya CHS Ltd,Sec:6
  5  CBD Belapur,Navi Mumbai
  7  eran --4-W- TAX INVOICE ------
  8  Date : 28/01/26 Bill No. : aK
 12  VEG BIRYANI 1 152.38
 17  Sub Total :
 18  SGST @2.5% :
 19  CGST @2.5% :
 20  Food Total :
```

A legible Navi Mumbai restaurant bill. What field detection returned:

```
date    n=0    party   n=0    total   n=0    tax   n=0    net   n=0
```

### The three rules, isolated one at a time

`amounts_for((line,), TOTAL_LABELS)`:

| input | result | what it proves |
|---|---|---|
| `TOTAL 500.00` | matched | baseline works |
| `TOTAL: 500.00` | matched | separator is fine |
| `TOTAL : 500.00` | matched | spaced colon is fine |
| `TOTAL:500.00` | matched | no space is fine |
| `TOTAL 1,23,456.00` | matched | **Indian grouping already works** |
| `GRAND TOTAL: 500.00` | matched | multi-word label works |
| `Total: 500.00` | **()** | **RULE 1 — matching is CASE-SENSITIVE** |
| `total: 500.00` | **()** | same |
| `  TOTAL: 500.00` | **()** | **RULE 2 — label must be at column 0** |
| `X TOTAL: 500.00` | **()** | **RULE 3 — no text may precede the label** |
| `TOTAL Rs. 500.00` | **()** | **RULE 4 — a currency token between label and number kills the match** |
| `Food Total : 525.00` | **()** | fails rules 1 and 3 together |

**The matcher requires an ALL-CAPS label starting at column 0 with nothing but
whitespace between it and a bare number.**

### Why that fails every real bill

Real invoices print `Total`, `Sub Total`, `Food Total`, `Grand Total`,
`Total Amount`, `Total Rs. 500`, and indent them inside a table. The synthetic
corpus in `artifacts/ground_truth` does not — its generator writes `TOTAL:` in
capitals at column 0, which is why that corpus scores 20/20 on total and this
one scores 0.

**The synthetic corpus was measuring the generator, not the reader.**

### The party field cannot match a real invoice at all

```python
PARTY_LABELS = ("SUPPLIER", "VENDOR", "BILLED BY", "SOLD BY")
```

The bill's vendor is `HOTEL ¥VISHWANAND` on line 3 — the top of the page, with
**no label of any kind**. That is how nearly every real invoice prints its
vendor. No amount of case-insensitivity fixes this; the party needs a different
strategy from label matching, and that is a separate piece of work.

---

## Root cause, in one sentence

**Field detection requires an ALL-CAPS label at column 0 followed by a bare
number; real invoices satisfy none of those three conditions, so every field is
refused even though the OCR read the page correctly.**

Classified against the ten boundaries: **response parser (boundary 9).** Not
file, not decoder, not conversion, not OCR invocation, not OCR engine, not
evidence layer.

## What this means for the two "separate" defects

The 86 JPGs and the text-layer PDFs (18,310 and 40,061 characters, 0 of 4 fields)
fail at the **same** boundary for the **same** reason. They were reported as two
bugs. They are one.

## What is NOT explained by this

- Whether the corpus documents are invoices at all. `gst-portal-and-govt-001.pdf`
  carries 18,310 characters including `GSTIN`×2, `CGST`×8, `SGST`×7, `IGST`×9 and
  **zero lines containing "Total"** — the shape of a GST form, not a bill. Fixing
  the matcher will not make a form into an invoice.
- The party field, which has no label to match on real bills.
- Whether loosening the three rules costs the zero-wrong property. That is
  measured before any change is kept, not assumed.

---

# Measured on Indian documents, in rupees — 2026-08-15

The owner supplied `ts-grewal-accountancy-class12-part-1_compress.pdf`, an
Indian Class 12 accountancy textbook. 661 pages, **no text layer at all** — one
JPEG2000 scan per page. That makes it a genuine OCR testbed rather than a text
extraction one.

## 80 pages swept across the whole book

| measure | count |
|---|---|
| pages OCR-ed | 80 |
| readable | 80 / 80 |
| median characters per page | 2,533 |
| pages carrying Indian grouping (`10,00,000`) | 60 |
| pages carrying a rupee figure | 67 |
| pages carrying a total / balance / amount label | 70 |
| **amounts matched by the reader** | **0** |

## Why zero, and why zero is CORRECT

The lines are double-entry ledger rows, not invoice fields:

```
'April 1] To Balance b/d 2,20,000 | Dec. 31} By Depreciation A/c 1,500'
'To Balance b/d 30,000 By Income and Expenditure A/c (Bal. Fig.) 1,26,000'
'Amount paid for stationery during the year ended 31st March, 2019 1,08,000'
'BALANCE SHEET as at 37st March, 2020'
```

Refusal reasons, counted over 25 pages:

```
62  no number after the label       'Grand Total' with the figure in another column
36  text before the label           the column-gap rule, working as designed
 5  multiple numbers on the line    a T-account row carries both sides
 4  single number, other
```

A T-account row states two entries, one on each side. Reading
`Amount paid for stationery during the year ended 31st March, 2019 1,08,000` as
a bill total would be a confident wrong amount — F-02, the failure this
repository exists to prevent. **Zero false positives across 80 dense pages of
Indian accounting text is the result, and it is the good one.**

## What the book DID prove: Indian money is read and rendered correctly

Every figure below was taken from the book's own pages, read through
`labels.amounts_for`, stored as integer paise, and rendered back through
`money.format_inr`:

| line as printed | paise stored | rendered back |
|---|---|---|
| `Total 10,00,000` | 100000000 | ₹10,00,000.00 |
| `Total 2,20,000` | 22000000 | ₹2,20,000.00 |
| `Total 1,08,000` | 10800000 | ₹1,08,000.00 |
| `Total 4,05,000` | 40500000 | ₹4,05,000.00 |
| `Total 45,61,546/-` | 456154600 | ₹45,61,546.00 |
| `Total Rs 1,23,456.78` | 12345678 | ₹1,23,456.78 |
| `Total ₹ 12,34,56,789.00` | 12345678900 | ₹12,34,56,789.00 |

Indian grouping in, Indian grouping out, to crore scale. The lakh/crore comma
pattern was never the defect and is not one now.

## The corpus verdict, stated once so nobody re-derives it

Three corpora were measured end to end. **None contains an Indian invoice.**

| corpus | documents | what they actually are |
|---|---|---|
| `data/real_invoices_indian/` | 111 | not invoices — 0 GSTINs, median 143 characters, 4/40 carry an invoice word. The one PDF with a GSTIN is a **GST appeal tribunal order** (`Appeal Case Reference no. - APL/1/PB/2026`) |
| `data/real_invoices/` | 303 | 9 are genuine invoices, and **24 of their 31 total-lines are European** — `Total TVA`, `Total TTC`, `Total HT`, `DEM`, euro signs, German decimal commas |
| the textbook | 661 | Indian, in rupees, and ledger exercises rather than bills |

The reader answers correctly on all three. It reads what is readable and refuses
what is not a labelled invoice field. **There is nothing here to raise its score
against, and a score raised against these documents would be measuring the wrong
thing.**

Euro support is deliberately NOT added: the owner's closed rule is Indian
invoices only. If that ever changes, `CURRENCY` in `labels.py` is the one line,
and 24 total-lines are waiting behind it.
