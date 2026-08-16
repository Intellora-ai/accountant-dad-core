# Step 1 — the document-type contract

2026-08-16. Written from measurement, not from opinion. The measurement is in
`artifacts/problem1_172_failure_classification.csv` (173 rows, zero unknown).

## The measurement this rests on

The canonical fixture, run through the live pipeline, with the cage's own words:

```
"paid Sharma Traders 4200 for cement"

  all 8 checks PASS
  typed extractor: party 1.0, total 1.0    <- EXACT, a person typed it
  gate inputs: party_known=True, period_open=None, net_paise=None

  Decided(action=BLOCK, reasons=(
    "There is something on this bill I could not check at all..."
    "I could not tell whether the books for this date are still open..."
    "I am less than 70 out of 100 sure about what this bill says..."
  ))
```

Three refusals, and **not one of them is about this document being wrong.**

| refusal | why it fires | is it true of this document? |
|---|---|---|
| could not check at all | `lines_sum_to_total` / `net_plus_tax_equals_gross` are INDETERMINATE | **no** — a typed sentence has no line items and no net. There is nothing to check, which is not the same as failing to check it. |
| period unknown | the pipeline passes `period_open=None` | **no** — the pipeline holds the date and could answer |
| less than 70 sure | `date` and `tax_paise` score 0.0 and drag the average under `ASK_FLOOR` | **no** — a typed expense note has no invoice date and no tax line. Scoring absent-by-nature fields as unread is scoring a field that does not apply. |

The cage is not being too strict. **It is answering invoice questions about a
document that is not an invoice.** That is the systemic cause, and 167 of the
173 failures descend from it.

## The four types

| type | what it is | example |
|---|---|---|
| `INVOICE` | a supplier's bill, with line items, a total, and usually tax | an uploaded PDF or photograph of a GST bill |
| `NON_INVOICE_EXPENSE_NOTE` | a person stating a payment in words | `paid Sharma Traders 4200 for cement` |
| `CREDIT_NOTE` | a reversal document from a supplier | not produced by any reader today |
| `UNSUPPORTED` | anything else that reached the reader | a government form, a ticket, a textbook page |

## Which documents enter the invoice cage

**Only `INVOICE`.** Nothing else.

## What the invoice cage may claim about each type

| check | INVOICE | NON_INVOICE_EXPENSE_NOTE | CREDIT_NOTE | UNSUPPORTED |
|---|---|---|---|---|
| `lines_sum_to_total` | applies | **NOT_APPLICABLE** | applies | not run |
| `net_plus_tax_equals_gross` | applies | **NOT_APPLICABLE** | applies | not run |
| invoice date in the confidence term | applies | **NOT_APPLICABLE** | applies | not run |
| tax in the confidence term | applies | **NOT_APPLICABLE** | applies | not run |
| party is named | applies | **applies** | applies | not run |
| amount is positive integer paise | applies | **applies** | sign inverted | not run |
| accounts differ / exist | applies | **applies** | applies | not run |
| `period_open` | applies | **applies** | applies | not run |

**`NOT_APPLICABLE` IS A THIRD VERDICT AND IT IS NOT `PASS`.** It says the
question does not arise for this kind of document. It is not "checked and fine"
and it is not "could not check". Collapsing it into `PASS` would be exactly the
thing this repository refuses — and collapsing it into `INDETERMINATE` is what
is happening today and is why 47 drafts block for a reason that is not true of
them.

## What happens to a non-invoice

Per the owner's Step 1, verbatim:

> If no separate expense-note posting policy exists, the safe result is:
> `NON_INVOICE_EXPENSE_NOTE` → `REVIEW_REQUIRED` or `BLOCKED`.
> Never silently allow it into the invoice posting path.

**No separate expense-note posting policy exists today.** So a typed expense note
is `REVIEW_REQUIRED` — a person sees it and decides.

### The consequence, stated plainly because it is uncomfortable

**This does not turn the 47 failing tests green.** They assert `Outcome.VALID`
for a typed sentence, and the contract above says a typed sentence is
`REVIEW_REQUIRED`. The owner's own Step 1 anticipated this:

> A typed sentence such as `paid Sharma Traders 4200 for cement` must not be
> treated as a valid invoice merely because an old test says `VALID`.

So implementing this contract **reclassifies** those 47 rather than fixing them.
They move from *blocked for three reasons that are false about them* to *held for
review because no policy yet says an expense note may post*. That is a correct
outcome replacing an incorrect one, and the suite count barely moves.

Writing a policy that lets an expense note post is a **separate owner decision**
and is not taken here.

## Why the 173 old VALID cases are what they are

| classification | count | verdict |
|---|---|---|
| `NON_INVOICE_FIXTURE_IN_INVOICE_CAGE` | 47 | **misclassified** — the fixture is not an invoice; the expectation of `VALID` predates the cage and predates any expense-note policy |
| `CASCADE_OF_THE_BLOCK` | 120 | **not independent failures** — they need a posted entry, and nothing posts. They resolve or move with the 47. |
| `UNRELATED_PRE_EXISTING` | 4 | **out of scope** — the `nearby.py` architecture allow-list, unrelated to the cage |
| `STALE_EXPECTATION_FROM_THIS_WORK` | 2 | **genuinely stale** — the ground-truth harness's recorded counts moved because dates now read. Regenerate the recorded file. |

Not one of the 173 is `GENUINE_SAFETY_FALSE_BLOCK` on an invoice. **No safety
block on a real invoice was found to be wrong.**

## Reason codes

```
NON_INVOICE_DOCUMENT              this is not an invoice, so the invoice cage did not judge it
INVOICE_CONSERVATION_INDETERMINATE  an INVOICE whose arithmetic could not be checked
UNKNOWN_DOCUMENT_TYPE             nothing classified this, so nothing may post
```

`UNKNOWN_DOCUMENT_TYPE` blocks. An unclassified document has not been shown to
be safe, and absence of a classification is not permission.

## What is NOT changed by this contract

- `ASK_FLOOR` stays **0.70**. `AUTO_POST_FLOOR` stays **0.95**.
- No `INDETERMINATE` becomes `PASS`. A new verdict is added; none is renamed.
- Invoice conservation laws are untouched for invoices.
- No gate is removed. The gate count may only go up.

The contract **narrows what the invoice cage claims to have checked.** A cage
that says "I could not check this bill's line items" about a sentence with no
line items is not being safe — it is being wrong in a direction that happens to
block, and a wrong reason is not made right by pointing the safe way.

## Status

**Written, not implemented.** The threading — `DocumentType` through
`cage/decision.py`'s `Situation`, `cage/gate.py`'s signature, and
`pipeline.evaluate` — is the next change, and it lands with the 47 test
expectations updated to the policy above rather than to `VALID`.
