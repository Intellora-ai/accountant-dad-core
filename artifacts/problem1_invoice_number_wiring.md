# Step 4 — invoice-number vocabulary, centralized and wired

2026-08-15, branch `cage/safety-layer`. `invoice_number` read **0 of 55** before
this step and **20 of 55** after it, with one incorrect that the cage refuses.

## It was an ABSENT path, not a broken one

`party` fails at a boundary. `invoice_number` had no boundary to fail at. Four
things were missing at once, and removing any single one still leaves 0:

```
invoice_number = 0 of 55
│
├── labels.py held no invoice-number vocabulary
├── Reading had no invoice_number field
├── read_page never searched for one
└── _scored never produced one
```

**The systemic cause is worth naming.** `read_page`'s docstring says *"The four
fields and the net."* The bill's own reference was never in its scope. It exists
on the PDF path — `invoice/parse.py` held `INVOICE_NUMBER_LABELS` and
`invoice/bridge.py:276` uses it. `labels.py` was created precisely so two
readers could not drift apart on vocabulary. The vocabulary was centralized; the
**field set was not**. Same drift, one layer up.

## The ceiling was measured BEFORE anything was built

`scripts/diagnose_invoice_number_reach.py`, over the 55 documents that state an
invoice number:

| where the true value sits | count |
|---|---|
| **same line as a label** | **37** |
| next line | 2 |
| elsewhere on the page | 5 |
| no label printed | 5 |
| engine returned no words | 4 |
| value not in the text at all | 2 |
| **ceiling for a label-based reader** | **39 of 55** |

This ran first on purpose. An hour earlier the party step added `SELLER` to the
vocabulary on the strength of 13 documents printing it, gained **zero**, and was
reverted — the real blocker was a two-column layout no vocabulary can reach. A
ceiling of 39 is what justified building four files of machinery here; a ceiling
of 0 is what stopped it there.

## `BILL NO` stays out, and it costs 5 documents

Measured label frequency on the reachable pages: `INVOICE NO` **32**, `BILL NO`
**5**.

`BILL NO` was not added. `tests/test_invoice_parse.py` already carries the
reason and the measurement:

> `E-Way Bill No:` contains `Bill No` after a space, so with that label on the
> list every e-invoice's number read as nothing.

Five documents given up rather than break every Indian e-invoice. `INVOICE NO`
carries 32 on its own.

## What changed, in four places

| file | change |
|---|---|
| `extract/labels.py` | `INVOICE_NUMBER_LABELS` now defined here — the one authoritative list. Also `cut_at_the_next_label`, below. |
| `invoice/parse.py` | re-exports the name. `parse.INVOICE_NUMBER_LABELS` still resolves, so `invoice/bridge.py` and the test asserting on it are untouched. |
| `extract/freeocr.py` | `Reading.invoice_number`, `_Answer.invoice_number`, `_read_invoice_number`, and the field judged and sourced like every other. |
| `extract/pagereader.py` | `read_page` searches for it. **No positional fallback, by design.** |

**No positional fallback and there must not be one.** A reference has no
arithmetic anyone can check it against, so a guessed one is a wrong value with
nothing downstream able to notice. A total at least has to satisfy
`net_plus_tax_equals_gross`.

## A bug I introduced, found by measurement and fixed

First corpus run after wiring: `correct` 74 → 88, but **`incorrect` 0 → 6**.

Five of the six were one defect. `values_for` returns everything after a label
to the end of its line, which is right when a line carries one field and wrong
when it carries two:

```
expected 'IYE/2025/1003'
read     'IYE/2025/1003 Date: 20/08/2025'
```

A correct reference with a whole other field stapled to it. `labels.cut_at_the_
next_label` now ends a value where the next field's label begins. It **cuts and
never rewrites**: nothing before the cut is touched, no spelling mended, no
shape normalised. After the fix: `correct` 93, `incorrect` 1.

## The one remaining incorrect, and why the cage handles it

```
real-commons-01   expected '320/10/2014/OL/DC'   read '320/10/2014/0L/DC'
```

The engine read the letter `O` as the digit `0`. There is no reader-level fix:
refusing every reference containing a zero beside letters would refuse the other
19 that are right. This is F-02 — a misread the reader cannot detect.

**It is scored `incorrect` and it is invisible to a user.** Its confidence is
**0.67**, below `ASK_FLOOR` 0.70. It cannot auto-post, and it cannot even spend
a question. The corpus metric counts it as wrong; the product never shows it.

## Result

| | before | after |
|---|---|---|
| correct, all 5 fields | 74 | **93** |
| incorrect | 0 | 1 (cage-refused, 0.67) |
| false positive | 0 | **0** |
| `invoice_number` | 0 of 55 | **20 correct** |

Suite went **174 failing → 173** — one net fix, no regressions. `ruff check`,
`ruff format --check` and `pyright` all clean.

## One test changed, and only its expected set

`test_a_record_from_this_backend_states_a_source_for_every_named_field` asserts
an EXACT set of sourced fields, deliberately, so that a source going missing
cannot pass. `invoice_number` is a genuinely new named field that states a
source, so the set gained it — the same way `net_paise` joined earlier the same
day, and for the same reason it stays out of `ExtractedRecord.FIELDS`.
