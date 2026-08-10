# PHASE 9 — THE DBT DATA-QUALITY HOLE

Read-only. Measured directly from the committed fixture at working tree
`37ec1d8`, 2026-08-10. No code was changed and no narration was invented.

Companions: `artifacts/phase9_exit_audit.md` · `artifacts/phase9_error_coverage.md`

**Provenance note, added 2026-08-10 during evidence correction.** The commands
below name the interpreter, the commit and the fixture, but none of them prints
`accountant.__file__`, so *which* `accountant` package was imported is
`UNVERIFIED` — the same check the invalidated `/tmp` measurement failed
(`artifacts/phase9_exit_audit.md`, "Evidence corrections that must stay
corrected"). Three figures here need no import at all and were re-checked on
2026-08-10 by reading the CSV with the stdlib only: the DBT fixture is **28 data
rows**, `Description` is **0 of 28** non-empty at column index 8, and the seven
fixtures total **311** data rows. Those three reproduce exactly.

---

## The verdict, first

**DBT is a SOURCE-DATA LIMITATION, and the rejection is LEGITIMATE.**

It is **not** a loader bug, **not** a schema-mapping bug, and **not** an invalid
fixture. The loader finds the right column, reads it correctly, and finds it
empty — because it **is** empty in the file the UK government published.

**For every N1 measurement, DBT is `NOT_MEASURABLE`.** Reporting it as
`NOT_PASSED` is the right call and should stay: absent evidence must never
report as a pass.

**The gap is real and it must not be filled.** There is no narration to recover.
Writing one would turn a measurement into fiction.

**Summary verdict in the current label set (from 2026-08-10):**

    DBT slice = FAIL — source unusable: narration empty in all 28 committed
                rows, and the loader refuses the department outright

The permitted values are `PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED ·
GITHUB_REQUIRED`. `NOT_MEASURABLE` and `NOT_PASSED (unmeasured)` appear
throughout this file as historical text and are not rewritten; both read
**FAIL** here.

**Why `FAIL` and not `NOT_MEASURED`.** `NOT_MEASURED` says *nobody looked*.
Somebody looked. The loader ran, resolved `Description` at index 8, read all 28
committed rows, found every one empty, and refused the department:

    ValueError: DBT has 0 history and 0 entries; a department needs both sides
    to take part in a comparison

verbatim from `accountant/ingest/crossorg.py:73-79`, pinned by
`tests/test_ingest.py::test_a_department_with_no_usable_rows_cannot_take_part`.
A measurement that ran and missed a data-quality bar is a **FAIL**.
`NOT_MEASURED` exists to stop an *unrun* thing being scored as a zero — the
question rate is the case it protects. Spending it on something that ran and
failed weakens it for that case.

**Two figures in that reason, kept apart.** *Empty in all 28 committed rows* is
counted and reproduces. *Empty in all 199 rows of the published file* is
`UNVERIFIED` — 171 of those rows have never been seen here, and no network call
was made. The verdict rests on the 28, which is enough: 0 usable rows is 0
usable rows.

**This file's own recommendation 4 disagrees, and is left standing.** It asks to
*"separate `NOT_MEASURABLE` from `NOT_PASSED` in the reporting vocabulary"*,
because *"the detector was too loud" and "there was no data" are different
failures. Today they print the same word.* Under this ruling they still print
the same word — **FAIL** — and the distinction moves into the reason string
attached to the label. Recommendation 4 is not withdrawn and it is not silently
followed; the reader can see both and judge.

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
**FAIL** measured before calibration and still must never be quoted as a current
number, and DHSC 33.33 is a **FAIL** measured now. The aggregate and held-out
rows stay **PASS**. The DBT slice, which appears in none of these four rows, is
a fifth verdict and it is **FAIL** — source unusable, narration empty in all 28
committed rows, loader refuses the department. See "Why `FAIL` and not
`NOT_MEASURED`" above.

**DBT is inside none of them.** Its 28 rows are in no numerator and no
denominator above. The 143 and the 69 are what is left after DBT is removed — see
"What DBT's absence does to the numbers" and finding **D-2** below.

---

## The five candidate causes, each ruled in or out

| candidate cause | verdict | the evidence that settles it |
|---|---|---|
| **source-data limitation** | **THIS ONE** | the `Description` column exists in the header and holds an empty string in all 28 committed rows. Counted cell by cell |
| loader bug | **RULED OUT** | the same loader reads the other six departments with **0** rejections out of 283 rows. The DBT rows fail one check and pass all four others |
| schema-mapping bug | **RULED OUT** | `Description` is in `NARRATION_HEADERS` (`spend.py:77`), resolves to index 8, and index 8 **is** the `Description` column. The mapping is correct. There is no other narration-shaped column being missed — see "the near-miss" below |
| invalid fixture | **RULED OUT** | 8 of 13 columns are fully populated, so the file is not truncated or corrupt. `tests/test_ingest.py::test_every_recorded_fixture_row_count_matches_the_committed_bytes` pins the row count. `ARCHITECTURE.md:502` forbids an invented fixture, and this one carries the publisher's own shape |
| legitimate rejection | **TRUE, as a consequence** | given the loader's contract — a voucher needs a narration — refusing these rows is correct. The rejection is a symptom; the source data is the cause |

---

## The numbers asked for

| question | answer |
|---|---|
| **source row count** — the published file | **199** — `UNVERIFIED`. Its only provenance is the `DBT.published_rows` constant in `accountant/ingest/sources.py`; **no published file was counted**, only 28 rows are committed, and this audit made no network call. The number is kept and labelled, not deleted |
| **committed fixture rows** | **28** (`dbt-2025-11.csv`, 29 lines = 1 header + 28 data rows) |
| **rows rejected** | **28** — 100% |
| **every rejection reason, with counts** | **`narration is empty` × 28.** That is the complete list. One reason, no others |
| **rows surviving** | **0** |
| **rows with empty narration** | **28 of 28** |
| **rows with usable narration** | **0 of 28** |

Reproduced with:

```
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -c "
from accountant.ingest import sources as S, spend as sp
r = sp.load_source(S.DBT)
print(r.row_count, r.loaded_count, r.rejected_count)
print(r.rejected_by_reason())
print(sorted({row.reasons for row in r.rejected}))"
```

```
28 0 28
(('narration is empty', 28),)
[('narration is empty',)]
```

The set of distinct reason-sets has exactly **one** member. No DBT row failed for
any second reason. `why_rejected` collects **all** reasons, not the first
(`spend.py:390`), so this is a complete list, not a first-failure short-circuit.

---

## Every column in the DBT file, counted

```
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -c "
import csv
rows = list(csv.DictReader(open('accountant/ingest/fixtures/dbt-2025-11.csv',
                                encoding='utf-8-sig')))
for col in rows[0]:
    print(col, sum(1 for r in rows if (r.get(col) or '').strip()), '/', len(rows))"
```

| # | published column | non-empty | used by the loader as |
|---|---|---|---|
| 0 | `Department` | 28 / 28 | — |
| 1 | `Entity` | 28 / 28 | — |
| 2 | `Date of Payment` | 28 / 28 | **date** |
| 3 | `Expense Type` | 28 / 28 | **account** |
| 4 | `Expense Area` | 28 / 28 | — (see the near-miss) |
| 5 | `Supplier` | 28 / 28 | **party** |
| 6 | `Transaction Number` | 28 / 28 | — |
| 7 | `Amount` | 28 / 28 | **amount** |
| 8 | **`Description`** | **0 / 28** | **narration** ← the whole problem |
| 9 | `Supplier Post Code` | 28 / 28 | — |
| 10 | `Contract Number` | 0 / 28 | — |
| 11 | `Project Code` | 0 / 28 | — |
| 12 | `Expenditure Type` | 0 / 28 | — |

**Four of the five fields a voucher needs parse perfectly.** Date, account, party
and amount are all 28 of 28. Only narration is missing, and it is missing because
the publisher left it blank.

---

## The rejection logic, quoted with file and line

**Quote fidelity, corrected 2026-08-10.** Every block below was re-read against
the source. Two of them had been **prettified**: the `NARRATION_HEADERS` tuple
and the five-assignment gate had their comments and `=` signs column-aligned,
and the gate had its indentation stripped. Neither changed a word of code, but a
quote that is presented as source must *be* the source, so both now carry the
file's actual bytes. Nothing else in the excerpts moved. The one remaining
deviation is deliberate and marked: `def why_rejected(...)` elides the parameter
list with `...`, and the four quoted lines are `spend.py:390-393` exactly.

**1 · `Description` is an accepted narration header.** The mapping is right.

`accountant/ingest/spend.py:75-82`
```python
NARRATION_HEADERS: tuple[str, ...] = (
    "narrative",  # MHCLG
    "description",  # DHSC, DBT
    "item text",  # DfT
    "publication description",  # HM Treasury
    "invoice cost centre description",  # DWP
    "po catergory description",  # Defra - the typo is in the published file
)
```

**2 · The reason string.**

`accountant/ingest/spend.py:104`
```python
EMPTY_NARRATION = "narration is empty"
```

**3 · The test that rejects.** An empty string is falsy, so `not narration` is true.

`accountant/ingest/spend.py:390-393`
```python
def why_rejected(...) -> tuple[str, ...]:
    """Every reason one row could not be read. All of them, not the first."""
    found: list[str] = []
    if not narration:
        found.append(EMPTY_NARRATION)
```

**4 · The gate that requires all five fields.**

`accountant/ingest/spend.py:412-418`
```python
    narration = clean(cells[columns.narration.index])
    account = clean(cells[columns.account.index])
    party = clean(cells[columns.party.index])
    when = parse_date(cells[columns.date.index])
    pence = parse_pence(cells[columns.amount.index])

    if when is not None and pence is not None and narration and account and party:
```

`clean` (`spend.py:154-156`) collapses whitespace **including non-breaking
spaces**, so a cell holding only invisible characters would also be empty. Here
the cells are genuinely `''` — nothing was stripped away.

**5 · Rejected rows are counted and named, never dropped.** This is the design
decision that makes the hole visible instead of silent.

`accountant/ingest/spend.py:35-39`
> "MALFORMED ROWS — Counted, named and reported. Never dropped. A loader that
> quietly discards the rows it cannot read will report a clean load of a broken
> file, and every number downstream inherits the lie."

`RejectedRow.__post_init__` (`spend.py:316-318`) refuses a rejection with no
reason. `LoadResult.row_count` (`spend.py:338-341`) is loaded **plus** rejected,
so the denominator cannot quietly shrink.

**6 · The source file records the cause, in advance.**

`accountant/ingest/sources.py:146-150`
> "DBT publishes the narration column and leaves every cell in it empty — all 199
> rows of the real file, not just the slice committed here. It is kept precisely
> because it is real: it is what a department looks like when the column exists
> and the data does not, and it is the reason malformed rows are counted and
> named rather than dropped."

**7 · DBT is excluded from the cross-organisation comparison by measurement.**

`accountant/ingest/sources.py:167-170`
```python
# The departments that publish a usable narration, and therefore the ones a
# cross-organisation comparison can run over. DBT is excluded by measurement,
# not by opinion: every one of its rows fails the empty-narration rule.
COMPARABLE_SOURCES: tuple[Source, ...] = (MHCLG, DHSC, DFT, DWP, DEFRA, HMT)
```

`tests/test_ingest.py:680` pins the exclusion: `crossorg.split` on DBT **raises**,
because `Split.__post_init__` (`crossorg.py:73-79`) refuses a department with no
history or no entries.

---

## The near-miss, and why it must stay a near-miss

`Expense Area` is populated **28 of 28**. It is the only unused populated
text column. The obvious temptation is to use it as narration. **Do not.**

What it actually contains:

| `Expense Area` (populated) | `Expense Type` = account | `Description` = narration |
|---|---|---|
| `DBT - Business Group - DBT - BG - Business Intelligence & Engagement` | `Other Professional Services` | `''` |
| `DBT - Competition, Markets and Regulatory Reform (CMRR) - DBT - CMRR - Employment Rights` | `Grant-In-Aid to Arms Length Bodies` | `''` |
| `DBT - Corporate Services - DBT - CS - Digital, Data and Technology` | `Other ICT Costs` | `''` |

`Expense Area` is an **org-chart path** — which team spent the money. It is not a
description of what was bought. Narration answers *what it was for*; this answers
*who spent it*. Substituting one for the other would not measure the product's
claim; it would measure a different claim, quietly.

**Three reasons to leave it alone:**

1. **It is not narration.** The product predicts *account from description of the
   purchase*. `Expense Area` describes the buyer, not the purchase.
2. **It would leak the answer.** Row 3's `Expense Area` ends in "Digital, Data and
   Technology" and its account is `Other ICT Costs`. A team name that names the
   spending category is close to the label the model is asked to predict. Any
   accuracy gained would partly be the answer copied from the question.
3. **`ARCHITECTURE.md:502` forbids it.** Inventing an input is exactly the failure
   mode the package was built to avoid.

### But there is a real inconsistency to flag · **DEFECT D-1 · MEDIUM**

`NARRATION_HEADERS` already accepts **`invoice cost centre description`** as
DWP's narration (`spend.py:80`). That is also a cost-centre field, not a
description of a purchase. Measured content confirms it:

| dept | narration header | an actual narration value | its account |
|---|---|---|---|
| MHCLG | `Narrative` | `Grant payable by the Exchequer` | `Current (non AEF) Grants to Local Authorities` |
| **DWP** | **`Invoice Cost Centre Description`** | **`FG CMPD FAS LOT 1`** | `EXP - PURCHASE OF GOODS/SERVICES - MEDICAL - DELIVERY PARTNER` |

DWP's "narration" is an internal cost-centre code, not a human description. So
the alias table's semantic bar is **not uniform**: a cost-centre field is
accepted for DWP and an org-unit field would be rejected for DBT.

This is not an argument for adding `Expense Area`. It is an argument that
**"narration" does not mean the same thing across the six departments already
being measured**, which weakens every cross-department number built on them.

DWP measures 62.96% within-department — second-highest — on narration values like
`FG CMPD FAS LOT 1`. If a cost-centre code predicts an account well, it may be
predicting it because the cost centre determines the account inside DWP's own
system, not because a description implies a category. **That is a different
finding from the one the project thinks it has.**

**Not fixed. Reported.** `accountant/ingest/` is outside this audit's ownership.

---

## What DBT's absence does to the numbers

| dept | fixture rows | loaded | rejected | history | **scored entries** |
|---|---|---|---|---|---|
| MHCLG | 57 | 57 | 0 | 28 | 29 |
| DHSC | 41 | 41 | 0 | 20 | 21 |
| DFT | 47 | 47 | 0 | 23 | 24 |
| DWP | 54 | 54 | 0 | 27 | 27 |
| DEFRA | 38 | 38 | 0 | 19 | 19 |
| HMT | 46 | 46 | 0 | 23 | 23 |
| **DBT** | **28** | **0** | **28** | **0** | **0** |
| **total** | **311** | **283** | **28** | **140** | **143** |

Matches `artifacts/detector_evidence.md` §2 exactly.

**One seventh of the source set sits outside every N1 figure in this repository.**

### The consequence nobody has written down yet · **FINDING D-2 · HIGH**

`artifacts/detector_evidence.md` §6 records the calibration split — sort by
company name, take every other one:

- **calibration half** — DBT, DFT, DHSC, MHCLG
- **held-out half** — DEFRA, DWP, HMT

**DBT is in the calibration half and contributes zero entries.**

So the split that reads as "4 departments to tune on, 3 to check on" is really
**3 and 3**. And the held-out result — **2.90 (2 of 69), PASS**, one of the four
numbers this audit must preserve — rests on **three** departments, not the
half-of-seven it appears to rest on.

**How this could look healthy while the product is useless:** a held-out set of
three departments and 69 entries is small enough that two entries decide it. One
more false alarm moves 2.90 to 4.35. Four more move it past the target. The
number is honest; it is just not sturdy, and nothing currently says so.

The empty-narration hole is therefore **not only a missing department. It
silently shrank the calibration set by a quarter**, and the split rule — sort by
name, alternate — had no way to know it was allocating an empty department.

---

## The inversion question, applied to DBT

> **"How could this report look healthy while a whole department is missing?"**

| # | mechanism | happening? | note |
|---|---|---|---|
| 1 | **empty narration silently removes a department** | **partly** | not silent — DBT is counted, named, reported `NOT_PASSED (unmeasured)`, and `CleanMeasurement.within` returns False on zero entries (`calibration.py:150-152`). The loader behaves correctly. What is **not** stated anywhere is D-2: it also shrank the calibration half from 4 to 3 |
| 2 | **`NOT_PASSED (unmeasured)` reads like a normal failure** | **YES** | in `detector_evidence.md` §12 DBT sits in the same table as DHSC's 33.33. One is a detector that is too loud; the other is a department that does not exist in the measurement. Same words, different kinds of nothing |
| 3 | **six clean departments look like a clean loader** | risk | "0 rejections out of 283" is the loader working. It is also **one department away** from a different story. A seventh file with two-thirds empty narration would produce a partly-loaded department and a rate computed on a biased subset of its own rows — and nothing today would flag that shape |
| 4 | **the fix is one line and it is the wrong line** | **live risk** | adding `"expense area"` to `NARRATION_HEADERS` would move DBT from 0 to 28 rows and the aggregate denominator from 143 to about 157. Every headline number would change and look better sourced. **It would be manufacturing evidence.** Written here so the temptation is on the record before somebody acts on it |
| 5 | **199 published rows is asserted, not verified** | **YES** | the claim that all 199 rows are empty covers 171 rows nobody in this repository has seen. Only 28 are committed. The claim is plausible — 28 of 28 is a strong signal — but it is `NOT_MEASURABLE` here, because verifying it needs a network call this audit did not make |

---

## Would-I-be-wrong check

The disconfirming evidence was looked for specifically.

| if "source-data limitation" is wrong, I would expect | looked? | found |
|---|---|---|
| another column holding real narration that the alias table misses | yes — all 13 columns counted | only `Expense Area`, which is an org-unit path, not a description. Ruled out above with reasons |
| the loader failing on other departments too | yes — all seven loaded | 0 rejections across the other 283 rows |
| a wrong column index or an off-by-one | yes — printed the resolved mapping | `narration <- 'Description' at index 8`; index 8 **is** `Description` |
| whitespace or a non-breaking space being stripped into emptiness | yes — read `clean` at `spend.py:154-156` and checked raw cells | the cells are genuinely `''`. Nothing was stripped |
| the fixture being truncated or corrupt | yes — 8 of 13 columns fully populated; row count pinned by test | the file is intact. Only specific columns are blank |
| a second rejection reason hiding behind the first | yes — `why_rejected` collects **all** reasons, and the distinct reason-set has one member | `narration is empty` and nothing else |

---

## Recommendation

| # | action | why | owner |
|---|---|---|---|
| 1 | **keep DBT committed and keep it rejected** | it is the only real example of a published file where the column exists and the data does not. Deleting it would make the loader look better and the world look simpler than it is | — |
| 2 | **never add `Expense Area` to `NARRATION_HEADERS`** | it is an org-unit path, it partly leaks the account, and `ARCHITECTURE.md:502` forbids inventing an input | engineering |
| 3 | **state D-2 wherever the 2.90 held-out PASS is quoted** | the held-out half is 3 departments and 69 entries, and the calibration half is 3, not 4 | docs owner |
| 4 | **separate `NOT_MEASURABLE` from `NOT_PASSED` in the reporting vocabulary** | "the detector was too loud" and "there was no data" are different failures. Today they print the same word | engineering |
| 5 | **decide whether a cost-centre code counts as narration** (D-1) | DWP is measured on `FG CMPD FAS LOT 1`. If that is not narration, DWP's 62.96% is measuring something other than what the project thinks | **owner** |
| 6 | if a narration for DBT is genuinely wanted, **get it from the source** | the published file is the only legitimate origin. Nothing in this repository may supply it | owner |

**Do not close this as fixed.** For DBT, the honest final status is

    FAIL — source unusable: narration empty in all 28 committed rows, and the
           loader refuses the department with "DBT has 0 history and 0 entries"

and no engineering task in this repository can change that. The body above
writes this as `NOT_MEASURABLE`; the permitted label from 2026-08-10 is **FAIL**
and the reason travels with it, because the label alone no longer says which
kind of failure it was. `NOT_MEASURED` would be the wrong word: the file was
read, and it lost on the merits.
