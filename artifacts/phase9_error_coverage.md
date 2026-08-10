# PHASE 9 — THE 12-TYPE ERROR COVERAGE MATRIX

Read-only. Verified against `docs/TAXONOMY.md`, `accountant/taxonomy/` and
`accountant/detect/detectors.py` at working tree `37ec1d8`, 2026-08-10.

Companions: `artifacts/phase9_exit_audit.md` · `artifacts/phase9_data_quality.md`

**Provenance note, added 2026-08-10 during evidence correction.** The command
below names the interpreter and the commit but does not print
`accountant.__file__`, so *which* `accountant` package was imported is
`UNVERIFIED` — the check the invalidated `/tmp` measurement failed
(`artifacts/phase9_exit_audit.md`, "Evidence corrections that must stay
corrected"). Any re-run should print it beside the counts.

---

## The headline, in one line

**0 VERIFIED. 2 PARTIAL. 10 with no detector at all.**

**Never write "2 of 12 verified."** Two types have a detector *pointed at* them.
Neither has ever been seen catching a real one.

**Summary verdict in the current label set (from 2026-08-10):**

    error-type verification = NOT_MEASURED   (0 of 12)

`NOT_MEASURED` is one of the six permitted values — `PASS · FAIL · BLOCKED ·
NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED`. It is the right one: no detector
has been *seen* to miss a labelled real instance either, because no labelled real
instance exists. **Not `FAIL`.** `VERIFIED`, `PARTIAL`, `UNCOVERED`,
`HISTORY_ONLY_LIMITATION` and the `REQUIRES_*` values used below are the coverage
matrix's own vocabulary in `accountant/taxonomy`, not project status labels, and
are left exactly as they are.

---

## The four N1 numbers, carried unchanged

Repeated in every Phase 9 report so no one of them can be quoted alone. Source:
`artifacts/detector_evidence.md`. Not re-run here — the harness has another owner.

| slice | rate per 100 clean entries | counts | target | verdict |
|---|---|---|---|---|
| historical — MHCLG only, **pre-calibration** | **27.59** | 8 of 29 | ≤ 10 | NOT_PASSED · **not a current number, never quote it as one** |
| aggregate, all departments | **6.29** | 9 of 143 | ≤ 10 | **PASS** |
| held-out half | **2.90** | 2 of 69 | ≤ 10 | **PASS** |
| worst department — DHSC | **33.33** | 7 of 21 | ≤ 10 | **NOT_PASSED** |

**Current label set, added 2026-08-10.** The `verdict` column is carried
unchanged from `artifacts/detector_evidence.md` and is left as written. In the
six permitted values `NOT_PASSED` reads **FAIL** — the MHCLG-only 27.59 is a
**FAIL** measured before calibration and must never be quoted as a current
number, and DHSC 33.33 is a **FAIL** measured now. The aggregate and held-out
rows stay **PASS**.

**None of these figures says anything about this matrix.** N1 counts false alarms
on clean entries. This matrix counts whether a detector has ever been seen
catching a **real** error. A detector can score a perfect N1 by never firing.

---

## First: is the previous agent's matrix right?

A previous agent produced a 12-row matrix in `docs/TAXONOMY.md`. This audit did
not redo it. It **checked** it, then added the columns this audit needs.

Re-measured here, 2026-08-10:

```
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -c "
from accountant.taxonomy import coverage as c
print(dict(c.status_counts()))
print(c.partial_types())
print(c.uncovered_count(), len(c.ERROR_TYPE_NAMES))
print(c.detectors_targeting_no_error_type())"
```

| check | `docs/TAXONOMY.md` says | measured here | agree? |
|---|---|---|---|
| published error types | 12 | **12** | **YES** |
| `VERIFIED` / `COVERED` | 0 | **0** | **YES** |
| `PARTIAL` | 2 | **2** | **YES** |
| `UNCOVERED` | 10 | **10** | **YES** |
| the two PARTIAL types | `revenue_expenditure_as_capital`, `capital_expenditure_as_revenue` | **identical** | **YES** |
| history-only reachable ceiling | 4 | **4** | **YES** |
| types with no source citation | 0 | **0** | **YES** |
| detectors aimed at nothing | 3 | **`first_use`, `magnitude`, `gst_anomaly`** | **YES** |

**Verdict: the previous agent's matrix is correct. Every count reproduces
exactly. Nothing in it needed changing.** It is extended below, not replaced.

---

## Words used in this file

| word | plain meaning |
|---|---|
| error type | a kind of accounting mistake that published auditors have actually found and written down |
| real-data label | somebody who knows the answer has marked a specific real entry as being this mistake |
| history-detectable | the fact that proves it is wrong is already inside the company's own past entries |
| detector | a small rule that raises a flag on an entry |
| aimed at | somebody decided this detector is the right one for this type. Nobody has checked |
| Schedule III | the Indian rulebook saying which heading each item goes under in the accounts |
| sanction | the written approval that authorised a government payment, naming the budget head |
| related party | a counterparty connected to the company — an owner, a director, a sister firm |
| suspense head | a temporary holding account, used while nobody has decided where something belongs |
| blocked credit | tax you are not allowed to reclaim, even though tax was charged |

---

## The classification rule, stated before the table

Exactly one label per row. The rule is mechanical so the table can be checked
rather than argued about.

| label | the test it must pass |
|---|---|
| `VERIFIED` | a **named** test in this repository runs a live detector over real data and asserts it flags a **labelled** instance of this type |
| `PARTIAL` | a detector in `ALL_DETECTORS` is mapped to this type in `coverage.COVERAGE`, and no such test exists |
| `HISTORY_ONLY_LIMITATION` | the deciding fact **is** inside the company's own Tally history, but no detector reads it. Reachable, unwritten |
| `REQUIRES_DOCUMENT` | the deciding fact is on a piece of paper outside the ledger — a sanction, a budget approval |
| `REQUIRES_RELATED_PARTY_DATA` | the deciding fact is *who the counterparty is*, which only a register the company supplies can say |
| `REQUIRES_COUNTERPARTY_DATA` | the deciding number was never written down on this side at all; the other party holds it |
| `REQUIRES_ACCOUNTING_STANDARD` | the deciding fact is a published rule — Schedule III, a head-pair table, a blocked-credit list — needing `accountant/rules/`, which does not exist |
| `REQUIRES_EXTERNAL_INPUT` | some other input from outside, not covered above |
| `UNREACHABLE_WITH_CURRENT_PRODUCT` | no input of any kind could make this checkable in this design |

**`PARTIAL` outranks `HISTORY_ONLY_LIMITATION`.** If a detector is already aimed
at a type, the row says so, even though the type is also history-reachable.

---

## The four things this matrix keeps separate

Collapsing any two of these is how a coverage table starts lying.

| stage | question | count today |
|---|---|---|
| 1 · theoretically reachable | could **any** check ever see this, given some input? | 12 of 12 — with `accountant/rules/`, a related-party register, sanctions, and counterparty data |
| 2 · reachable **today**, from Tally history alone | can it be seen with only what the connector already returns? | **4 of 12** |
| 3 · implemented | does a detector exist and is it mapped to this type? | **2 of 12** |
| 4 · **verified on real labelled data** | has it ever been seen catching a real one? | **0 of 12** |

Every honest sentence about coverage names which of these four it means.

---

## THE MATRIX

Columns: the type · does a real labelled example exist here · is it
history-detectable · which detector today · verified · what evidence would
upgrade it · status.

| # | error type | source citation | real-data label exists? | history-detectable? | current detector | verified? | required evidence to upgrade | STATUS |
|---|---|---|---|---|---|---|---|---|
| 1 | `revenue_expenditure_as_capital` — revenue spend booked to a capital head | `CAG-4-2020` Para 3.11(b), Annexure 3.6, Sl. 1-5 | **NO** | **YES** — the head this party's spend went to before | `vendor_switch` | **NO** | one real ledger entry labelled as this type that `vendor_switch` flags | **PARTIAL** |
| 2 | `capital_expenditure_as_revenue` — capital spend booked to a revenue head | `CAG-4-2020` Para 3.11(b), Annexure 3.6, Sl. 6-8 | **NO** | **YES** — same fact, opposite direction | `vendor_switch` | **NO** | the same: a labelled real instance that `vendor_switch` flags | **PARTIAL** |
| 3 | `object_head_incompatible_with_major_head` — revenue object head under a capital major head | `CAG-4-2020` Para 3.11(a), Annexure 3.5, Sl. 1-4 | **NO** | **NO** — needs the permitted head-pair table | NONE | **NO** | the head-pair rule table in `accountant/rules/`. `head_pair_invalid` is proposed, not planned | **REQUIRES_ACCOUNTING_STANDARD** |
| 4 | `wrong_expense_head_within_same_section` — right section, wrong head inside it | `CAG-4-2020` Para 3.11(c); `CAG-PR-2025-08-12` Para 3.5.2.1 | **NO** | **NO** — decided by the sanction the entry cites | NONE | **NO** | an entry carrying its sanction: a schema change **plus** a document the system never sees | **REQUIRES_DOCUMENT** |
| 5 | `receipt_classified_as_wrong_type` — receipt booked to the wrong side of the revenue/capital split | `CAG-4-2020` Para 2.4.2.8; `CAG-PR-2025-08-12` Para 3.5 | **NO** | **YES** — the credit account this party's receipts went to | NONE | **NO** | write `receipt_side_switch` and measure it. **It needs no new input at all** | **HISTORY_ONLY_LIMITATION** |
| 6 | `parked_in_suspense_head` — final head emptied by a later transfer to suspense | `CAG-4-2020` Para 3.13 | **NO** | **YES**, with a caveat below | NONE | **NO** | `transfer_out_of_final_head`, plus transfer entries and the suspense-account list | **HISTORY_ONLY_LIMITATION** |
| 7 | `expenditure_netted_against_receipt` — an outgoing shown as a smaller receipt | `CAG-4-2020` Para 3.14 | **NO** | **NO** — the gross figure was never recorded | NONE | **NO** | the gross figure, from the counterparty. **No detector, rule or question can recover a number nobody wrote down** | **REQUIRES_COUNTERPARTY_DATA** |
| 8 | `expense_under_wrong_statement_head` — expense shown under the wrong statement heading | `ICAI-FRRB-IndAS-II` Ch. 4, Obs. 4 | **NO** | **NO** — needs Schedule III | NONE | **NO** | the Schedule III head table and phrasebook in `accountant/rules/` | **REQUIRES_ACCOUNTING_STANDARD** |
| 9 | `balance_under_wrong_balance_sheet_head` — balance grouped under the wrong heading | `ICAI-FRRB-IndAS-II` Ch. 1, Obs. 11; Ch. 3, Obs. 5-6 | **NO** | **NO** — needs Schedule III | NONE | **NO** | the same Schedule III table, read against the ledger groups Tally returns | **REQUIRES_ACCOUNTING_STANDARD** |
| 10 | `related_party_not_identified` — a connected counterparty recorded as an ordinary one | `NFRA-132.2-2023-03` Exec. Summary (f), Paras 43-63 | **NO** | **NO** — every field can look exactly as it always has | NONE | **NO** | a related-party register the company supplies. Until then the fact is in nothing the system reads | **REQUIRES_RELATED_PARTY_DATA** |
| 11 | `expenditure_exceeds_sanctioned_provision` — spend passes the approved limit | `CAG-PR-2025-08-12` Para 4.2.1.1 | **NO** | **NO** — the limit is a sanctioned provision, not a historical high | NONE | **NO** | the sanctioned amount per head — a document outside the ledger | **REQUIRES_DOCUMENT** |
| 12 | `tax_credit_claimed_where_not_admissible` — input tax credit claimed on a blocked supply | `CAG-11-2019-ch4` Para 4.7.5 | **NO** | **NO** — needs the blocked-supply list | NONE | **NO** | the blocked-credit list in `accountant/rules/` | **REQUIRES_ACCOUNTING_STANDARD** |

**Real-data label exists: 0 of 12. Verified: 0 of 12.**

---

## Totals

| status | count | which |
|---|---|---|
| `VERIFIED` | **0** | — |
| `PARTIAL` | **2** | 1, 2 |
| `HISTORY_ONLY_LIMITATION` | **2** | 5, 6 |
| `REQUIRES_ACCOUNTING_STANDARD` | **4** | 3, 8, 9, 12 |
| `REQUIRES_DOCUMENT` | **2** | 4, 11 |
| `REQUIRES_COUNTERPARTY_DATA` | **1** | 7 |
| `REQUIRES_RELATED_PARTY_DATA` | **1** | 10 |
| `REQUIRES_EXTERNAL_INPUT` | **0** | — |
| `UNREACHABLE_WITH_CURRENT_PRODUCT` | **0** | — |
| **total** | **12** | |

### These totals reconcile exactly with `docs/TAXONOMY.md`

Not by coincidence — the same rows, sorted by *why* rather than by *whether*.

| `TAXONOMY.md` label | count | this file's labels | count |
|---|---|---|---|
| `VERIFIED` | 0 | `VERIFIED` | 0 |
| `PARTIAL` | 2 | `PARTIAL` | 2 |
| `UNREACHABLE` | 4 | `REQUIRES_DOCUMENT` 2 + `REQUIRES_COUNTERPARTY_DATA` 1 + `REQUIRES_RELATED_PARTY_DATA` 1 | **4** |
| `UNSUPPORTED` | 6 | `REQUIRES_ACCOUNTING_STANDARD` 4 + `HISTORY_ONLY_LIMITATION` 2 | **6** |

Two independent groupings, same twelve rows, same totals. That is the check.

### Why nothing is `UNREACHABLE_WITH_CURRENT_PRODUCT`

That label is reserved for a type no input could ever make checkable. All twelve
become checkable **given the right input** — a rule corpus, a register, a
document, a counterparty figure. Four of them (7, 10, 4, 11) are called
`UNREACHABLE` in `docs/TAXONOMY.md`, and that is correct **in that file's
vocabulary**, which means "unreachable from the ledger". They are named here by
the input they need instead, because naming the missing input is actionable and
"unreachable" is not.

Type 7 is the closest to permanently unreachable: the gross figure **was never
written down on this side**. It is not hidden, it does not exist. Only the
counterparty can supply it.

---

## The ceiling: how many could this design ever reach?

**4 of 12** — types 1, 2, 5, 6. The two `PARTIAL` plus the two
`HISTORY_ONLY_LIMITATION`. These are the only ones whose deciding fact is inside
data the connector already returns.

**The caveat, stated rather than buried.** Type 6 counts as history-only because
Tally returns the chart of accounts alongside the vouchers, so which accounts are
suspense accounts is readable from what the connector already supplies. Read
strictly — treating the suspense-account list as something a person must assemble
— **the ceiling is 3, not 4.**

The count of 4 is confirmed two independent ways: by reading each row's deciding
fact, and by `coverage.types_by_route(Route.DETECTOR)`, whose own definition is
"needs no input the connector does not already return". Same answer.

### Why the ceiling is 4 and not 12 — the structural reason

All four detectors fire on a **change** from the company's own past behaviour.

> A standing wrong practice never changes, so it never contradicts anything.

Most published audit findings are standing practices: the Department of Atomic
Energy used the same invalid head pair across two grants and **defended** it. A
change-detector is silent on a mistake made consistently.

**`UNVERIFIED`, added 2026-08-10** — two claims in that sentence, neither
sourced in this file. The Department of Atomic Energy example carries **no
citation**, unlike every row of the matrix above, which names a paragraph in a
published report. And "most published audit findings are standing practices" has
**no count behind it** — nothing here counted findings and sorted them into
standing versus one-off. Both are kept because the structural argument they
illustrate stands on its own (a change-detector cannot see an unchanging
practice); neither should be quoted as measured. Closing them is cheap: cite the
CAG paragraph for the first, and count the findings for the second. That is not a bug in
the detectors; it is the shape of the design, and it caps this approach at 4 of
12 no matter how many detectors are written.

---

## What Phase 8 would and would not do

Phase 8 builds `accountant/rules/`. That would supply the head-pair table,
Schedule III and the blocked-credit list — the input the four
`REQUIRES_ACCOUNTING_STANDARD` rows are waiting on.

| stage | today | after Phase 8, at best |
|---|---|---|
| reachable from Tally history alone | 4 | 4 — unchanged |
| theoretically reachable with the inputs on hand | 4 | **8** (adds 3, 8, 9, 12) |
| implemented | 2 | 2 until detectors are written |
| **verified on real labelled data** | **0** | **0** |

**Phase 8 raises the ceiling. It verifies nothing.** A rule table makes a check
*possible*; only a real labelled instance makes it *proven*. The last row does
not move until an accountant marks up a real book.

---

## Labels on this section's results

| result | label | why not stronger |
|---|---|---|
| the 12 types exist and are what auditors find | `PUBLIC_DATA_EVIDENCE` | every type traces to a published paragraph with a URL and retrieval date, refused at load time if either is missing (`Source.__post_init__`) |
| 2 types have a detector mapped to them | `BUILD_CORRECTNESS` | a mapping is an intention. Nobody checked it |
| 0 types verified | `NOT_MEASURABLE` | no real ledger here carries a labelled instance of any of the twelve |
| the ceiling is 4 | `BUILD_CORRECTNESS` | derived by reading each type's deciding fact against the connector's output |
| any detector catches any published error type | — | **no evidence of any kind exists** |
| how often any type occurs | — | **deliberately absent.** `accountant/taxonomy/` has no field to store a frequency in, because an invented one would quietly become the argument for keeping or dropping a detector |

---

## The inversion question, applied to this matrix

> **"How could this table look healthy while the product is useless?"**

| # | mechanism | happening? | note |
|---|---|---|---|
| 1 | **`PARTIAL` reads as "half done".** It reads like 2 of 12 progress. It means "somebody pointed a detector at it" | **YES** | the fix is vocabulary, and `docs/TAXONOMY.md` already applies it. `docs/BOTTLENECKS.md` A1 and `docs/PROJECT_STATE.md` §22 still say **"Detectors cover 2 of 12"** as measured fact. That overstates what is proven. Both are outside this audit's ownership — **reported, not edited** |
| 2 | **Three of four detectors are aimed at nothing.** `coverage.detectors_targeting_no_error_type()` returns `first_use`, `magnitude`, `gst_anomaly` | **YES** | 75% of the detector suite targets no published error type. `magnitude` is also the sole cause of the 33.33 DHSC failure. It is producing false alarms in service of no published finding |
| 3 | **The production path runs one detector.** `pipeline.evaluate` and `pipeline.run` default to `SLICE_4_DETECTORS` = `vendor_switch` only | **YES** | the one shipped detector is at least the only one aimed at anything. But **every N1 number in this repository measures three detectors, not the one a user runs** |
| 4 | **Synthetic truth is defined by detector names.** `generate/inject.py` corrupts into exactly the four `ALL_DETECTORS` names | **YES, by construction** | a synthetic N3 near 100% is a spelling test between the injector and the detector list. It is **not** on this matrix, and must never be counted as coverage |
| 5 | **Counting "12 published types" implies the taxonomy is complete** | **UNKNOWN — untestable here** | 12 is what four published documents contained. A thirteenth type in a fifth report would make today's 0-of-12 a 0-of-13. The denominator is a reading of the literature, not a census of reality |
| 6 | **The ceiling of 4 could be read as "4 will work"** | risk | 4 is how many are *conceivably* reachable. 2 have a detector. 0 are proven. Reading 4 as achievement skips two whole stages |
| 7 | **The UK data cannot label any of these types** | **YES** | the twelve come from Indian audit reports (CAG, ICAI-FRRB, NFRA). The only real data here is UK government spend. **The corpus that could verify these types and the corpus this repo holds are different countries.** No amount of UK data upgrades a single row |

**Number 7 is the deepest one.** The taxonomy is Indian. The real data is
British. The verification column can stay 0 forever no matter how many UK
departments are added.

---

## Would-I-be-wrong check

| if this matrix is wrong, I would expect | looked? | found |
|---|---|---|
| a test asserting a detector flags a labelled real instance | yes — `status_counts()`, `tests/test_taxonomy_matrix.py` | none. The matrix test **refuses** a `VERIFIED` row naming a test function that does not exist |
| a 13th type, or a type with no citation | yes — `len(ERROR_TYPE_NAMES)`, load-time refusals | 12 exactly; 0 uncited. `Source.__post_init__`, `Finding.__post_init__` and `CoverageRow.__post_init__` all refuse at load |
| the PARTIAL pair being different from what the docs say | yes — `partial_types()` | identical to `docs/TAXONOMY.md` |
| a frequency estimate leaking in somewhere | yes | none. There is no field to store one in — the strongest possible guarantee |
| the ceiling being higher than 4 | yes — checked each row's deciding fact against `Voucher`'s fields, then cross-checked with `Route.DETECTOR` | 4 both ways; 3 under the strict reading of type 6 |

---

## What would actually move the last column

Ranked by how much each moves `VERIFIED` away from 0.

| # | action | moves | owner |
|---|---|---|---|
| 1 | **one real Indian book with an accountant's markup** of which entries are wrong, by type | types 1 and 2 from `PARTIAL` to `VERIFIED` **or** to a measured miss. Either answer is progress; today there is no answer | **owner** |
| 2 | write `receipt_side_switch` (type 5) | 1 type from unimplemented to `PARTIAL`. **Needs no new input** — the connector already returns `credit_account`, and nothing reads it. Cheapest engineering item on this matrix | engineering |
| 3 | build `accountant/rules/` (Phase 8) | raises the theoretical ceiling from 4 to 8. **Verifies nothing** | engineering |
| 4 | correct "2 of 12 covered" in `BOTTLENECKS.md` A1 and `PROJECT_STATE.md` §22 | nothing measurable — stops the repository contradicting itself | docs owner |

**Item 1 is the bottleneck.** Items 2 and 3 are engineering that can proceed
without it and will not change the last column by one row.
