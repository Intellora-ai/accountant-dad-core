# Problem 1 — final blocker report

## 1. Status

**BLOCKED BY INVALID / NON-REPRESENTATIVE CORPUS.**

Not blocked by the extraction code. The code was measured from three independent
directions and the corpus failed all three.

## 2. Baseline

    1 of 300 slots reaching a candidate

Corpus: 60 image documents from `data/real_invoices_indian` and
`data/real_invoices`. 5 fields x 60 documents = 300 slots.
Path: `read_page` + `_scored` — the same path a real upload takes.
Command: `.venv/bin/python scripts/measure_field_slots.py`

## 3. Preprocessing result

    1 of 300

Unchanged.

## 4. Labels recovered by preprocessing

    0

Ten deterministic Pillow-only methods, four documents, forty trials.
Legible word rows rose from 5 to 159 across the sample — a 32x increase in text —
and **not one invoice label appeared in any of them.**

    upscale_x3            159 rows    0 labels
    threshold_128         132 rows    0 labels
    upscale_x2            124 rows    0 labels
    grayscale             119 rows    0 labels
    original                5 rows    0 labels

The bar was written into `scripts/diagnose_image_quality.py` BEFORE the numbers
arrived: *"a transformation must raise the count of legible word rows AND produce
at least one recognisable LABEL. More characters is not a result."* It fails on
the half that matters. `measure_field_slots.py` counts fields, not glyphs.

One document, `open-datasets-and-photos-011.jpg`, returns 0 rows under all ten
methods.

## 5. Unmatched-slot breakdown

| category | slots | % | solvable by extraction work? |
|---|---:|---:|---|
| NO_LABEL_ON_PAGE | 119 | 39.7% | no — the page never names the field |
| IMAGE_QUALITY_FAILURE | 105 | 35.0% | **no — measured, 0 labels recovered** |
| WRONG_FIELD_FAMILY | 45 | 15.0% | no — the page carries other fields' labels |
| LABEL_NORMALIZATION_FAILURE | 17 | 5.7% | fixed and tested; worth 0 here |
| UNKNOWN_LABEL | 9 | 3.0% | partially |
| OCR_LABEL_CORRUPTION | 3 | 1.0% | partially |
| PARSER_PATH_FAILURE | 2 | 0.7% | yes |

**269 of 287 are measured as not solvable by extraction work on this corpus.**
The honest remaining ceiling is 14 slots — about 15 of 300 — and only if every
one landed perfectly.

## 6. Why the corpus cannot validate invoice extraction

Traced to the end on 2026-08-15. **300 of 422 documents came from Wikimedia
Commons.** Every licence is redistributable: 134 CC BY-SA 4.0, 95 public domain,
46 CC BY 4.0.

The corpus was selected for LICENCE, because it lives in a public repository.
Nobody CC-licenses their bills. **Openly-licensed documents and real commercial
invoices are near-disjoint sets.** More scraping cannot fix it.

Independently confirmed twice:
- 413 documents read zero fields; **333 of them are not bills at all** —
  government forms, circulars, tribunal orders, menus, letters.
- A local LLM said "Not an invoice" on **10 of 10** read-nothing documents.

## 7. Code changes that are safe and tested

| change | evidence |
|---|---|
| net handoff + conservation pairing (`e783074`) | law INDETERMINATE -> PASS; 3 of 3 document laws pass on a real bill; mutation-tested |
| positional party disabled (`64b6bce`) | ground-truth party WRONG 8 -> 5 |
| artifact ceiling (`e163e5b`) | party WRONG 5 -> 4; 8 of 12 artifacts caught, 0 of 10 real names lost |
| `dates.py` + `trace.py` (`5e025c3`) | 96 tests |
| three `trace` defects (`fac48ac`) | gate restored; 0.0-score conflation fixed |
| version-number date fix (`647ea03`) | 6 false positives killed, 12 real formats kept |
| nearby-value extraction (`a7f72f6`) | 9 of 9 fixtures; 0 on corpus, stated |
| CI typecheck fix (`0b2d229`) | repo pyright 1 error -> 0 |
| whitespace label normalisation | 45 label tests; **0 on corpus, not counted as an improvement** |
| `party_known` from history | `party_known` at the gate 0 of 299 -> 169 of 299 |

Ground truth throughout: `total_paise` 0 wrong, `tax_paise` 0 wrong. Never moved.

## 8. Changes NOT made, and why

- **`period_open` wiring — REVERTED.** It was written, but no test supplies a real
  period reader, so nothing proved its runtime effect: `period_open` stayed `None`
  on 299 of 299 gate calls. Owner rule: do not merge an incomplete wiring fix
  without a test proving its effect. Removed rather than left looking done.
- **Image preprocessing** — not wired into the reading path. Measured as worth 0
  labels.
- **No-label fallback** — would require inventing a label. Forbidden.
- **Field-family redesign** — the 45 slots are pages genuinely lacking the field.
- **Fuzzy matching, proximity-only acceptance, threshold changes** — all forbidden
  and none attempted.

## 9. Exact next input required to resume

**Real Indian invoices with ground truth.** See
`artifacts/problem1_next_data_requirements.md`.

Until that exists, every extraction change is measured against documents that
are not bills, and no number produced here means anything about real accuracy.
