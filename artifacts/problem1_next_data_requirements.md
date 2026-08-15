# What Problem 1 needs before it can resume

The extraction code cannot be validated against the current corpus. This is the
replacement.

## The corpus

- Real invoice or bill images / PDFs
- **Permission to hold them in this repository** — note it is PUBLIC today; these
  documents almost certainly require it to be private, or a private data path
- Redacted customer / supplier information where necessary
- Ground-truth values for the five measured fields
- **At least 60 documents**
- Multiple suppliers and multiple layouts
- Clear labels AND difficult cases
- GST and non-GST examples
- Same-line and next-line values
- Multi-page examples where relevant
- Low-quality scans and photographed bills
- Credit notes, if they are to be supported
- Documents with missing fields
- Documents with ambiguous totals

## Minimum metadata per document

    document_id
    source_type
    permission_status
    field_ground_truth        party, invoice date, total, tax, invoice number
    document_type
    image_quality
    expected_review_status

## Why each part matters

**Ground truth is the part that cannot be skipped.** Without it the measurement
counts REACH — did a value come back — and never ACCURACY. A reader that
confidently returns the wrong total scores identically to one that returns the
right one. Every accuracy number in this repository today is either synthetic or
absent, and it is labelled as such.

**Sixty is the floor, not the target.** Rule of three: zero errors in n trials
gives a 95% upper bound of 3/n. Sixty documents with zero silent wrong posts
buys a 5% upper bound. Three hundred buys 1%.

**Difficult cases must be included deliberately.** A corpus of only clean bills
measures the easy path and hides every failure mode the cage exists for.

## The standing constraint

Recorded 2026-08-15: no real bill photograph is coming, and that instruction was
given twice. If that still holds, Problem 1 cannot be validated at all, and the
honest position is that the extraction path is UNMEASURED rather than working or
broken.
