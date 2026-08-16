# Step 3 — party extraction: where the evidence dies, measured

2026-08-15, branch `cage/safety-layer`. Party read **0 of 59** before this step
and **0 of 59** after it. That is not a failed step — it is a step that found
the reason, proved the obvious fix makes things worse, and reverted it.

## The measurement, not the opinion

`scripts/diagnose_party_boundaries.py` walks the same boundaries the live reader
crosses, in the same order, and records the FIRST one at which party evidence is
gone. Over all 62 ground-truth documents:

| boundary | count |
|---|---|
| A — engine returned no words at all | 7 |
| B — words came back, page names no party | 27 |
| **C — a party label IS printed, `PARTY_LABELS` did not match it** | **28** |
| D — matched, but no words mapped back | 0 |
| E — words mapped, refused downstream | 0 |
| F — value survived, scored 0.0 | 0 |
| G — party read | 0 |

**D, E and F are all zero, and that is the most useful line in the table.**
Nothing downstream is eating party evidence. No ceiling is over-refusing, no
scoring rule is zeroing a good read, no mapping is dropping words. Whatever is
wrong is at or before the label match.

That also closes the subtraction question: there was **no safe subtraction
available**. The reader is not over-refusing. It is not looking.

## What the 28 pages actually print

| label | pages |
|---|---|
| `BILL TO` | 14 |
| `SELLER` | 13 |
| `CLIENT` | 13 |
| `FROM` | 1 |

`PARTY_LABELS` held four labels, all supplier-side: `SUPPLIER`, `VENDOR`,
`BILLED BY`, `SOLD BY`.

**Only `SELLER` was a candidate.** `BILL TO` and `CLIENT` name the BUYER. On a
purchase bill the buyer is the owner's own company, so reading one as the
supplier files a vendor's ledger under the customer's name — F-03 by
construction, and one supplier's balance is then wrong for ever. `FROM` was
already measured and rejected in an earlier round: four of its seven values were
a place, a date range and OCR noise.

`SELLER` had never been tried. It is supplier-side, means what `SOLD BY` means,
and is not ordinary English the way `FROM` and `PARTY` are. It was a fair
candidate.

## It was added, measured, and reverted within the hour

| | before | after `SELLER` |
|---|---|---|
| party correct | 0 | **0** |
| party INCORRECT | 0 | **1** |

The single value it produced was the characters **`Client:`** — a label, not a
name.

## Why: the page is two columns and the engine reports no geometry

`real-voxel51-05` reaches the reader as:

```
line 2   'Seller: Client:'
line 3   'Padilla, Webb and Pearson Marsh-Kennedy'
```

Two labels on one line. Two company names on the next. The same-line search
found `SELLER` and took everything after it, which is the neighbouring column's
label. A next-line read would have been no better: line 3 glues both companies
together.

`freeocr.Word` carries no geometry — a closed owner decision — so the column gap
a person sees is not in the data at all. All 13 `SELLER` pages are Voxel51 bills
with this layout.

**Widening the vocabulary cannot fix this and can only hurt**, because the defect
is upstream of which words the reader knows.

## A guard was tried too, and also reverted

`_is_a_label` was taught that any line ending in a colon is a label whatever the
word is — so `Client:` could never be read as a value even though `CLIENT` is
deliberately not in any family.

It is a sound rule. **It did not move the number**, because this failure is on
the SAME line, not the next one, and `_is_a_label` guards the next-line search.
It was reverted with the label. An unmeasured guard kept because it feels right
is the habit every other measurement in these files exists to prevent.

## The first explanation was incomplete — the other 41 were measured too

Two-column layout explains 13 documents. It does not explain 41. Two further
measurements finished the tree.

**Buyer labels gain nothing, even setting the safety objection aside.** For the
14 documents printing `BILL TO` or `CLIENT` and no supplier label:

| where the true party sits | count |
|---|---|
| same line as a buyer label | **0** |
| next line after one | **0** |
| somewhere else on the page | 13 |
| not in the text at all | 2 |

So `BILL TO` and `CLIENT` would have won **zero** documents. The F-03 argument
against them was right, and it turns out it was not even the binding reason.

**Where the unread parties actually are.** Across all 54 documents where the
party is unread and ground truth names one:

| position of the true name | count |
|---|---|
| **lines 0-2, the letterhead** | **38** |
| lines 3-9 | 12 |
| not on the page at all | 4 |

and **42 of the 50 that are on the page are ALONE on their line.**

That is a strong, specific signal - top of the page, alone on the line, no label
anywhere near it. It is how a bill actually names its supplier: on the
letterhead, because the letterhead IS the claim.

## Which makes the remaining question an owner decision, not an engineering one

A letterhead reader is buildable and would reach 38 documents. It is also
exactly what Step 3 of the owner's own spec forbids:

> never infer party only from page position

That rule is not arbitrary and the repository has the measurement behind it:
positional party was wired once and took party WRONG from **5 to 8** on the
20-PNG corpus, producing `'TNoIte Noe eTvan42'` and two other pieces of engine
noise as supplier names. It was disabled the same day.

Two things have changed since that measurement, and they are why this is worth
putting back to the owner rather than simply closing:

1. `extract/artifacts.py` now exists - the artifact ceiling that caught 8 of 12
   OCR artifacts while losing **0 of 10 real names**. The noise that made
   positional party dangerous is the exact thing it was built to refuse.
2. The signal here is narrower than "positional". It is *lines 0-2, alone on the
   line, past the artifact ceiling, not a label, not money, not a date* - not
   "the nearest name-shaped thing to a number".

**It would still be capped at `BY_POSITION` = 0.50**, below `ASK_FLOOR` 0.70, so
a letterhead party could never post AND could never even spend a question. It
would raise the corpus `correct` count by up to 38 while changing nothing a user
can see - which is worth saying plainly, because a metric that moves while the
product does not is the kind of number this file exists to distrust.

**No code was written for this.** The rule says no, so the answer is no until the
owner says otherwise.

## What this leaves

| documents | why party is unread | can the reader fix it |
|---|---|---|
| 27 | the page names no party at all | no — nothing to read |
| 13 | two-column layout, no geometry | **not without reversing a closed owner decision** |
| 14 | only a buyer label printed; true name is elsewhere | **no** — measured at 0 reachable |
| 7 | engine returned no words | no — an image problem, not a reading one |

The honest outcome for a two-column bill today is the one it already produces:
party unread, and a person is asked who the bill is from. That is a miss, not a
lie, and the alternative measured above is a lie.

## Files

- `scripts/diagnose_party_boundaries.py` — the measurement, re-runnable
- `artifacts/problem1_party_diagnostics.csv` — one row per document, boundary and detail
- `accountant/extract/pagereader.py` — its module docstring said this failure
  "never happened" on the twenty PNGs. That was true then and is false now;
  corrected in place with the 13 documents and the reverted experiment.
