# TAXONOMY - the twelve published error types, and what is actually proven

Measured 2026-08-08 against `accountant/taxonomy/` and `accountant/detect/detectors.py`
on branch `closure/flag-cap-and-truth`. Pinned by `tests/test_taxonomy_matrix.py`,
which fails when this file and the code disagree.

## What this document is, and what it refuses to be

It is one row per published error type, saying what is **proven**, what is
**plausible**, and what is **out of reach with the data this system has**.

It refuses to state coverage as verified truth. Nothing in this repository
proves that any detector catches any published error type on real data, because
no real ledger this repository holds carries a labelled instance of any of the
twelve. That absence is itself pinned by a test:
`tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key`.

It also refuses to estimate how often any type occurs. The published record does
not support such a number, and `accountant/taxonomy/` carries no field one could
be stored in.

## The four headline counts

| measurement | measured here | owner stated | agree? |
|---|---|---|---|
| published error types | 12 | 12 | AGREE |
| VERIFIED coverage | 0 | 0 | AGREE |
| PARTIAL coverage (a live detector is aimed at the type) | 2 | at most 2 | AGREE |
| history-only reachable ceiling | 4 | 4 | AGREE |

Sources for each number:

- **12** - `len(accountant.taxonomy.findings.ERROR_TYPES)`.
- **0** - `coverage.status_counts()` returns `COVERED 0, PARTIAL 2, UNCOVERED 10`,
  and no test anywhere runs a detector over a labelled real instance of a
  published type.
- **2** - `coverage.partial_types()` returns exactly
  `revenue_expenditure_as_capital` and `capital_expenditure_as_revenue`, both
  mapped to `vendor_switch`.
- **4** - the types whose evidence is entirely inside a company's own Tally
  voucher stream. This is the same set as `coverage.types_by_route(Route.DETECTOR)`,
  which the code defines as "needs no input the connector does not already
  return". Two independent readings, one answer.

### Where this disagrees with the rest of the repository

`docs/BOTTLENECKS.md` A1 and `docs/PROJECT_STATE.md` §22 both state
**"covered by current detectors 2"** and **"Detectors cover 2 of 12"** as a
measured fact. That **overstates** what is proven. The code those documents cite
says `COVERED 0, PARTIAL 2`: two types have a detector *aimed* at them, and
neither has a test showing it fires on a real instance. Those two documents are
outside this file's ownership and were not edited; the disagreement is recorded
here rather than silently reconciled.

## The classification rule, stated once and applied without exception

Exactly one of four per type. The rule is mechanical, so the table is checkable
rather than a matter of taste.

| classification | the test it must pass |
|---|---|
| `VERIFIED` | a **named** test in this repository runs a live detector over real data and asserts it flags a labelled instance of this type |
| `PARTIAL` | a detector in `ALL_DETECTORS` is mapped to this type in `coverage.COVERAGE`, and no such test exists |
| `UNREACHABLE` | the fact that decides it is not in the ledger and not in any rule corpus - it lives in a document this system never sees, or was never written down at all |
| `UNSUPPORTED` | no detector exists, and the input it would need is obtainable. A `Proposal` in `coverage.PROPOSALS` is a backlog hypothesis, not a plan |

`Route` in `coverage.py` answers a different question - *how* a type would be
checked - and is not this classification. A type can be `Route.RULE` and
`UNREACHABLE` at the same time: the check shape is an invariant, and the number
it needs sits outside the ledger.

## The matrix

| type id | short name | source citation | mapped detector | classification | why | what evidence would upgrade it |
|---|---|---|---|---|---|---|
| `revenue_expenditure_as_capital` | revenue spend booked to a capital head | `CAG-4-2020` Para 3.11(b), Annexure 3.6, Sl. 1-5 | `vendor_switch` | PARTIAL | `vendor_switch` fires only when the head contradicts this party's own posting history; the published cases are standing practices, and a standing practice contradicts nothing | one real ledger entry labelled as this type that `vendor_switch` flags. No book in this repository carries such a label |
| `capital_expenditure_as_revenue` | capital spend booked to a revenue head | `CAG-4-2020` Para 3.11(b), Annexure 3.6, Sl. 6-8 | `vendor_switch` | PARTIAL | the same detector and the same limit, in the opposite direction | the same: a labelled real instance that `vendor_switch` flags |
| `object_head_incompatible_with_major_head` | revenue object head against a capital major head | `CAG-4-2020` Para 3.11(a), Annexure 3.5, Sl. 1-4 | NONE | UNSUPPORTED | each head on its own is in ordinary use, so no detector reading one field at a time can see the pair; the Department of Atomic Energy used the same invalid pair across two grants and defended it | the head-pair rule table in `accountant/rules/`, which does not exist. `head_pair_invalid` is proposed, not planned |
| `wrong_expense_head_within_same_section` | right section, wrong head inside it | `CAG-4-2020` Para 3.11(c); `CAG-PR-2025-08-12` Para 3.5.2.1 | NONE | UNREACHABLE | the fact that settles it is the sanction the entry was made under - the Ministry of Power said it used the head its own sanction gave - and `Voucher` carries no sanction field and Tally returns none | an entry that carries the sanction it cites: a schema change plus a document the system never sees today |
| `receipt_classified_as_wrong_type` | receipt booked to the wrong side of the revenue/capital split | `CAG-4-2020` Para 2.4.2.8; `CAG-PR-2025-08-12` Para 3.5 | NONE | UNSUPPORTED | every live detector reads `debit_account`, `amount_paise` or `gst_paise` on a purchase entry; nothing reads `credit_account`, which the connector already returns | `receipt_side_switch` written and measured. It needs no new input at all |
| `parked_in_suspense_head` | final head emptied by a later transfer to suspense | `CAG-4-2020` Para 3.13 | NONE | UNSUPPORTED | the original entry is correct and passes every detector; the error is a later transfer entry, and no detector reads transfers | `transfer_out_of_final_head`, plus transfer entries from the connector and the list of suspense accounts |
| `expenditure_netted_against_receipt` | outgoing shown as a reduction of a receipt | `CAG-4-2020` Para 3.14 | NONE | UNREACHABLE | there is no expenditure entry to inspect; the ledger holds the net figure and nothing else, and the gross figure was never written down | the gross figure, from the counterparty. No detector, no rule and no question to the person who made the entry can recover a number that was never recorded |
| `expense_under_wrong_statement_head` | expense presented under the wrong statement head | `ICAI-FRRB-IndAS-II` Chapter 4, Observation 4 | NONE | UNSUPPORTED | the account is the one this entity has always used for the expense; the standard says it is wrong and the history says it is ordinary | the Schedule III head table and the plain-English phrasebook in `accountant/rules/`, neither of which exists |
| `balance_under_wrong_balance_sheet_head` | balance grouped under the wrong balance sheet head | `ICAI-FRRB-IndAS-II` Chapter 1, Observation 11; Chapter 3, Observations 5-6 | NONE | UNSUPPORTED | the statement-head case applied to a balance: grouping is settled once and repeated, so nothing contradicts | the same Schedule III table, read against the ledger groups Tally returns |
| `related_party_not_identified` | counterparty is related and is recorded as ordinary | `NFRA-132.2-2023-03` Executive Summary (f), Paras 43 to 63 | NONE | UNREACHABLE | party, account, amount and tax can every one of them look exactly as they always have; what is wrong is who the party is, and no history can infer that | a related-party register the company supplies. Until one is supplied the fact is in nothing the system reads |
| `expenditure_exceeds_sanctioned_provision` | spend passes the sanctioned provision | `CAG-PR-2025-08-12` Para 4.2.1.1 | NONE | UNREACHABLE | `magnitude` bounds an amount by that account's own historical high, and the limit breached here is a sanctioned provision that can sit either side of it | the sanctioned amount per head. It is a document outside the ledger that the system never sees |
| `tax_credit_claimed_where_not_admissible` | input tax credit claimed on a blocked supply | `CAG-11-2019-ch4` Para 4.7.5 | NONE | UNSUPPORTED | `gst_anomaly` fires on an account that has never carried tax, and these accounts carry admissible tax the rest of the time | the blocked-credit list in `accountant/rules/`, which does not exist |

Counts over the twelve rows: **VERIFIED 0, PARTIAL 2, UNREACHABLE 4, UNSUPPORTED 6.**

### Types with no source citation: 0

Every one of the twelve traces to at least one published paragraph, and every
paragraph carries a `Source` with a URL and a retrieval date. This is enforced
at load time, not trusted: `Source.__post_init__` refuses a document with no URL
or no retrieval date, `Finding.__post_init__` refuses a finding assigned to a
type that does not exist, and `CoverageRow.__post_init__` refuses a row that
cites nothing.

## What the connector already returns, and what it does not

The ceiling question: how many of the twelve are detectable from a company's own
Tally voucher history alone, with no other data source?

| type id | the fact that decides it | in Tally history alone? |
|---|---|---|
| `revenue_expenditure_as_capital` | the account this party's spend has gone to before | YES |
| `capital_expenditure_as_revenue` | the account this party's spend has gone to before | YES |
| `object_head_incompatible_with_major_head` | which head pairs the chart's rules permit | NO - rule table |
| `wrong_expense_head_within_same_section` | the head named in the sanction the entry cites | NO - external document |
| `receipt_classified_as_wrong_type` | the credit account this party's receipts have gone to | YES |
| `parked_in_suspense_head` | later transfer entries, and which accounts are suspense | YES - see the caveat below |
| `expenditure_netted_against_receipt` | the gross figure, which was never recorded | NO - held by the counterparty |
| `expense_under_wrong_statement_head` | the head Schedule III assigns to the description | NO - rule table |
| `balance_under_wrong_balance_sheet_head` | the head Schedule III assigns to the balance | NO - rule table |
| `related_party_not_identified` | whether the party is related | NO - a register the company holds |
| `expenditure_exceeds_sanctioned_provision` | the sanctioned amount for the head | NO - external document |
| `tax_credit_claimed_where_not_admissible` | whether credit is blocked on this supply | NO - rule table |

**Measured ceiling: 4 of 12.**

**The caveat, stated rather than buried.** `parked_in_suspense_head` counts as
history-only because Tally returns the chart of accounts along with the vouchers,
so which accounts are suspense accounts is readable from data the connector
already supplies. Read strictly - treating the suspense-account list as a
separate input somebody has to assemble - the ceiling is **3**, not 4. The count
of 4 is the same set the code independently reaches through
`Route.DETECTOR`, whose own definition is "needs no input the connector does not
already return".

## What is actually wired into the production path

Reading `accountant/pipeline.py` rather than the detector module:

| tuple | contents | where it runs |
|---|---|---|
| `ALL_DETECTORS` | `vendor_switch`, `first_use`, `magnitude`, `gst_anomaly` | nowhere by default - it is the catalogue the coverage table maps against |
| `ACTIVE_DETECTORS` | `vendor_switch`, `magnitude`, `gst_anomaly` | the scoring harness default (`accountant/score/harness.py`) |
| `SLICE_4_DETECTORS` | `vendor_switch` | **the production default** - `pipeline.evaluate` and `pipeline.run` both default `detector_set` to this |
| `WITHDRAWN` | `first_use` | off, with the measured reason: 39.13 false alarms per 100 clean entries on held-out real data |

So the production path runs **one** detector, and that one detector is the only
one aimed at any published error type. The other three - `first_use`,
`magnitude`, `gst_anomaly` - map to nothing in the published record;
`coverage.detectors_targeting_no_error_type()` returns all three.

## How this file is kept honest

`tests/test_taxonomy_matrix.py` pins it. That file holds a third, hand-typed copy
of the twelve rows and fails when this document, that copy, or
`accountant/taxonomy/` disagree about a type name, its order, its citation, its
mapped detector or its classification. It also refuses a `VERIFIED` row that does
not name a test function this repository actually contains.

Upgrading a row from `PARTIAL` to `VERIFIED` therefore takes a real labelled
instance and a real test, not an edit to this table.
