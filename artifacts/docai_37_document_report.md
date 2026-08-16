# Gemini - the 37 development documents

model `gemini-3.6-flash` - 17 of 37 documents scored - 85 scored field slots
cloud calls this run: 42 (ceiling 66, 2 attempts per document)

| metric | count |
|---|---|
| correct | 73 |
| incorrect | 0 |
| missing | 4 |
| false positive | 1 |
| review-required | 7 |
| documents with all five correct | 10 of 17 |

## By field

| field | correct | incorrect | missing | false positive | review-required |
|---|---|---|---|---|---|
| party | 16 | 0 | 0 | 0 | 1 |
| invoice_date | 16 | 0 | 0 | 0 | 1 |
| invoice_number | 16 | 0 | 0 | 0 | 1 |
| tax | 11 | 0 | 4 | 0 | 2 |
| total | 14 | 0 | 0 | 1 | 2 |

**GATE: FAIL** - incorrect 0 (limit 1), false positives 1 (limit 0)

## Tokens, as the API reported them

```
candidatesTokenCount 402
promptTokenCount 2712
thoughtsTokenCount 4258
totalTokenCount 7372
```

Cost is NOT MEASURED: no price table is configured in this repository, and a rate nobody supplied would be an invented figure.

LINE ITEMS: NOT MEASURED - neither the response schema (`gemini_invoice_poc.FIELDS`) nor `problem1_ground_truth.json` carries them. SUBTOTAL and CURRENCY: not scored fields either.

Validation and locked sets untouched. No Tally write, no cage submission.

## Calls that failed

| document | exception | status | provider | retryable | attempts |
|---|---|---|---|---|---|
| synthetic-007 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-011 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-013 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-010 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| real-voxel51-01 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-012 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| real-commons-01 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-045 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-006 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| real-voxel51-11 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-038 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-017 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-028 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-021 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| real-voxel51-05 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| real-voxel51-09 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-023 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-008 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-003 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
| synthetic-036 | HTTPError | 429 | RESOURCE_EXHAUSTED | True | 2 |
