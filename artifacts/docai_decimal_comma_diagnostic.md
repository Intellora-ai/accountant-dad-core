# Diagnostic — the two `incorrect` fields on `real-voxel51-03.jpg`

2026-08-16. **Zero new cloud calls.** The full values were already in
`artifacts/docai_five_document_results.csv`; only my terminal display truncated
them, not the file.

## document: `real-voxel51-03`

### field 1 — `tax`

```
raw Gemini value        : '$ 7,14'
Gemini status           : ACCEPTED_CANDIDATE
ground truth raw text   : '7,14'
ground truth paise      : 714
comparator interpretation:
    paise_or_none('$ 7,14')  ->  None
    None != 714              ->  INCORRECT
classification          : COMPARATOR_WRONG
```

### field 2 — `total`

```
raw Gemini value        : '$ 78,58'
Gemini status           : ACCEPTED_CANDIDATE
ground truth raw text   : '78,58'
ground truth paise      : 7858
comparator interpretation:
    paise_or_none('$ 78,58')  ->  None
    None != 7858              ->  INCORRECT
classification          : COMPARATOR_WRONG
```

## Gemini read both PERFECTLY

The ground truth records the printed text as `7,14` and `78,58`. Gemini returned
`$ 7,14` and `$ 78,58` — **the same digits, the same comma, plus the currency
symbol the page actually prints.** The prompt asked it to preserve raw printed
text and it did, more completely than the ground truth itself.

Gemini also avoided the trap this document is built around. The ground truth
carries a warning on `total`:

> The same 'Total' row also prints 'Net worth' $ 71,44, which is the PRE-TAX
> figure. Reading the leftmost money cell posts the bill short by exactly the tax.

Gemini returned `78,58`, the gross. It did not take `71,44`.

## Two separate comparator faults, both proved

```
paise_or_none('$ 7,14')   ->  None      <- fault 1
paise_or_none('7,14')     ->  71400     <- fault 2
paise_or_none('7.14')     ->  714
```

**Fault 1 — `$` is not stripped.** `labels._DECORATION` strips
`₹ £ \xa0 RS. RS INR GBP , (space)`. **`$` is absent.** The repository is
INR-facing and no Indian bill prints a dollar sign, so the omission never showed
until a USD document arrived. Any value carrying `$` returns `None`.

**Fault 2 — the comma is always a thousands separator.** `,` is in
`_DECORATION`, so it is deleted before parsing. `7,14` becomes `714`, then
`× 100` gives **71400 paise** — a hundred times the truth. On this European
document the comma is a DECIMAL POINT, and there is no code path that considers
that reading.

Fault 2 is the more dangerous one. It is silent, it is a factor of 100, and it
would fire even if `$` were stripped.

## What this means for the gate

The five-document run reported:

```
correct 17 · incorrect 2 · missing 0 · false positive 0 · review-required 6
GATE: FAIL  (incorrect 2, limit 1)
```

**Both `incorrect` were the comparator, not the model.** Nothing has been
changed on the strength of that — the comparator is untouched, per instruction —
so the recorded result still reads FAIL until the owner rules on it.

This is the **fourth** time a comparator in this project has accused a correct
reader: money once scored 50 exact matches as INCORRECT, dates scored 33, a
month-first US date scored 1. Every time the reader was right and the ruler was
broken, and every time it argued for "fixing" something that already worked.

## Not changed

```
cloud calls made          : 0
documents processed       : 0 additional
production files changed  : no
comparator changed        : no
ground truth changed      : no
Tally writes              : 0
cage submissions          : 0
```
