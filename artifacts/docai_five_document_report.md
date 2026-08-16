# Gemini - five development documents (RE-SCORED)

model `gemini-3.6-flash` - 5 documents - 25 field slots

**Re-scored 2026-08-16 from the saved answers in `docai_five_document_results.csv`. Zero cloud calls, zero network calls, no invoice reopened, raw results unchanged.**

| metric | count |
|---|---|
| correct | 19 |
| incorrect | 0 |
| missing | 0 |
| false positive | 0 |
| review-required | 6 |
| documents with all five correct | 3 of 5 |

**GATE: PASS** - incorrect 0 (limit 1), false positives 0 (limit 0)

## What changed against the first scoring

- `real-voxel51-03.tax: incorrect -> correct`
- `real-voxel51-03.total: incorrect -> correct`

The first run recorded `incorrect 2` and therefore `GATE: FAIL`. Both were `real-voxel51-03`, whose page prints `$ 7,14` and `$ 78,58`; Gemini returned both exactly and the comparator could parse neither - `$` was not stripped and the comma was always read as a thousands separator. `measure_gemini_five._paise_for` reads the comma by the currency the ground truth states, and it is the only thing that moved. The model's answers are byte-for-byte the ones it gave.

Five documents is not the 37-document gate. Validation and locked sets untouched. No Tally write, no cage submission.
