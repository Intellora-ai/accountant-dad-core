# Phase 8 PR-2 — the four detectors, the root cause, and the N1 measurement

**Detector exit: FAIL.** N1 with all four detectors running on real published
ledgers is **34.27 per 100 clean entries**, against an owner-set target of 10.
The root cause the owner named was found and fixed and the number improved, and
it is still 3.4 times over the line. The reason is named in section 6 and it is
not the account this pull request was about.

**Production runs ONE detector.** `accountant/pipeline.py` still defaults to
`SLICE_4_DETECTORS`, which is `(vendor_switch,)`. Nothing here changed that,
because the owner's instruction is explicit: *do not ship four detectors with
N1 failing.* Section 8 says so plainly rather than leaving it to be inferred.

Written 2026-08-10. Branch `phase8/detectors`, from `f22eace`.

Every number below was produced by running the code in this worktree, with

```python
from pathlib import Path
import accountant

assert str(Path(accountant.__file__).resolve()).startswith(str(Path.cwd().resolve()))
```

passing first. Two measurements in this project were void for exactly that
reason, so no number that did not clear the assertion is in this document.

**Permitted labels, and nothing else:**

```
PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED
HUMAN_ACTION_REQUIRED · OWNER_DECISION_REQUIRED · OPTIONAL_HUMAN_INPUT
BLOCKED_ON_HUMAN_EVIDENCE · NOT_IMPLEMENTED · SOURCE_UNVERIFIED
NOT_SELECTED · INCOMPLETE
```

---

## 1. The three baseline numbers: all three reproduce

Owner answer Q7 requires the baseline be reproduced before anything is built on
it. All three did.

| On record | Reproduced | Measured as |
|---|---|---|
| current aggregate false-alarm rate = **6.29** | **yes** | 9 of 143 clean entries, 629 hundredths |
| all-detector result = **36.36** | **yes** | 52 of 143 clean entries, 3636 hundredths |
| DHSC `Additions NCB PDC` contributes **6 of 9** false alarms | **yes** | 6 of the 9, all from `magnitude`, all on that account |

Command, run from the worktree root:

```
COVERAGE_CORE=pytrace .venv/bin/python
```

```python
from accountant.ingest import sources, spend
from accountant.score import calibration as cal
from accountant.score.harness import gate_from_books

books = {s.code: spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES}
code_of = {b.company: c for c, b in books.items()}
_, held = cal.split(list(books.values()))
gate_from_books(books, held_out=[code_of[b.company] for b in held])
```

`accountant.__file__` for every run in this document:

```
/private/tmp/claude-501/-Users-tanveersidhu-ACCOUNTANT/
  173e27c0-9c4e-4793-a6c4-594143728ef9/scratchpad/wt-p8-detectors/accountant/__init__.py
```

The full baseline, all seven departments:

| department | false alarms | clean entries | per 100 |
|---|---:|---:|---:|
| MHCLG | 0 | 29 | 0.00 |
| **DHSC** | **7** | **21** | **33.33** |
| DFT | 0 | 24 | 0.00 |
| DWP | 1 | 27 | 3.70 |
| DEFRA | 1 | 19 | 5.26 |
| HMT | 0 | 23 | 0.00 |
| DBT | 0 | 0 | not measured |
| **aggregate** | **9** | **143** | **6.29** |

### 1.1 The ceiling claim: verified, with one correction

On record: *"the ceiling for that account was set from a 10-entry history that
six NHS trusts each exceed — one wrong ceiling counted six times."*

| Part of the claim | Verdict | Measured |
|---|---|---|
| the ceiling came from a 10-entry history | **verified** | 10 prior rows on the account, all dated 2025-11-03 |
| the ceiling is 21,300,000 paise | **verified** | `max` of those ten |
| six NHS trusts each exceed it | **verified, and it is narrower than it sounds** | the six flagged entries are six *different* trusts; but **twelve** of the sixteen scored rows exceed the ceiling and only six clear the 300 per cent margin |
| one wrong ceiling counted six times | **verified** | one account, one detector, six entries |

The correction matters. "Six trusts exceed the ceiling" understates it: twelve
of the sixteen scored entries on that account are above 21,300,000, and the
detector is silent on six of them only because the margin is three times the
top. The ceiling is not marginally low. It is below three quarters of the
account's own later traffic.

---

## 2. The root cause, in one sentence

> `magnitude` takes its ceiling from `max(history)`, and the evaluation book
> hands it a history that is the **cheapest** rows of the account — DHSC
> publishes rows sorted by ascending amount inside each payment date, and the
> split cuts a department at a fixed row position — so **21,300,000 is where
> the cut fell, not the top of `Additions NCB PDC`**.

### The mechanism, shown

Every `Additions NCB PDC` row in the published file, in published order. Rows
11–29 all carry the same payment date and rise monotonically.

```
row   date         paise            trust
 11   2025-11-03     4,700,000      NORFOLK COMMUNITY HEALTH AND CARE
 12   2025-11-03     4,900,000      NORTHUMBRIA HEALTHCARE
 ...
 20   2025-11-03    21,300,000      WEST HERTFORDSHIRE TEACHING     <- the cut
 21   2025-11-03    25,400,000      NORTHERN CARE ALLIANCE
 ...
 29   2025-11-03   740,000,000      NORTHUMBRIA HEALTHCARE
 35   2025-11-17   174,400,000      MAIDSTONE & TUNBRIDGE WELLS
 36   2025-11-17   217,000,000      TAVISTOCK AND PORTMAN
 37   2025-11-17   830,000,000      BOLTON
```

The department has 41 loaded rows, so `split_point` cuts at 20. The cut lands
inside the 2025-11-03 block, and inside that block the publisher sorted by
amount. The "ten prior entries" are therefore the ten cheapest payments of that
day, and the 740,000,000 payment made **on the same day, to the same account**
sits nine rows further down on the other side of the line.

### Three discriminating measurements

| Experiment | Fires |
|---|---:|
| the actual split — ten cheapest rows as history | **6 of 16** |
| ten rows drawn at random from the same twenty-six, 10,000 draws | mean **0.83**, `P(>= 6) = 0.0009` |
| history = every row of the first payment date (n = 19, max 740,000,000) | **0 of 7** |

Same detector. Same thresholds. Same account. The detector behaves sensibly on
honest evidence and badly on a slice that is biased by construction.

### The two rival explanations, tested and weaker

**"The account pools entities that should not share one ceiling."** Refuted,
and the decisive number is sharper than the one first written here: **not one
of the six flagged entries has a trust with enough prior evidence for a
per-trust ceiling to exist at all.** Four of the six trusts have no earlier row
on this account in the history supplied and the other two have exactly one,
against a minimum of two.

So a per-trust ceiling would not have *corrected* those six. It would have
abstained on every one of them, and on all sixteen scored rows. That reads like
a fix and is not one: a detector that goes quiet everywhere on an account has
not found the right ceiling, it has stopped answering.

The eleven trusts hold 1, 2 or 3 entries each — one with 1, five with 2, five
with 3 — and within a *single* trust the amounts on this one account span
**151x**, so even where a per-trust ceiling could be computed it would be no
better founded than the one it replaced.

| entries | min paise | max paise | ratio | trust |
|---:|---:|---:|---:|---|
| 2 | 4,900,000 | 740,000,000 | 151.0x | NORTHUMBRIA HEALTHCARE |
| 3 | 4,600,000 | 174,400,000 | 37.9x | MAIDSTONE & TUNBRIDGE WELLS |
| 3 | 30,000,000 | 830,000,000 | 27.7x | BOLTON |
| 2 | 11,400,000 | 217,000,000 | 19.0x | TAVISTOCK AND PORTMAN |

**"The history is too small to be a ceiling."** Not on its own. Counted across
all seven departments, the false-alarm rate is not monotonic in history size:

| prior entries | eligible | fired |
|---:|---:|---:|
| 2 | 4 | 0 |
| 3 | 10 | 1 |
| **10** | **7** | **3** |
| 28 | 27 | 0 |

The twenty-eight-entry accounts are silent and the two-entry accounts are
silent. One row of that table is the whole problem, and a minimum-history rule
tuned to remove it would have been a threshold in a structural disguise.

---

## 3. What changed, and why it is not a threshold tweak

`accountant/detect/detectors.py:prior_amounts`, new:

```python
def prior_amounts(proposed: Voucher, history: Sequence[Voucher]) -> list[int]:
    return [
        v.amount_paise
        for v in history
        if v.debit_account == proposed.debit_account and v.date < proposed.date
    ]
```

`magnitude` takes its ceiling from that list, and abstains when the list is
shorter than `MIN_OBSERVATIONS_FOR_A_RANGE`.

**It is not a threshold tweak, for four reasons that can each be checked.**

1. **Neither calibrated number moved.** `MIN_OBSERVATIONS_FOR_A_RANGE` is still
   `2` and `MAGNITUDE_OVER_PERCENT` is still `300`.
   `test_the_fix_did_not_move_either_calibrated_number` pins both.
2. **It changes what counts as evidence, not how high the bar sits.** A payment
   made on 2025-11-03 is not evidence about the range a payment made on
   2025-11-03 falls outside of.
3. **It is not a one-way quietening knob.** Dropping a same-day row can *lower*
   a ceiling and make the detector speak where it was silent — the dropped row
   may hold the maximum.
   `test_dropping_a_same_day_row_can_make_the_detector_speak` holds a case that
   does exactly that, and the corpus carries the same case at
   `magnitude/synthetic/dropping-a-same-day-row-can-make-it-fire`.
4. **The denominator did not move.** Same books, same split, same **143** clean
   entries. Nothing was excluded, no department dropped, no hard case deleted.

Of the 1,103 history rows previously counted into a ceiling across all seven
departments, **222 were not prior to the entry they were counted against**.

### 3.0 The flag may not claim more than the evidence it was handed

Fixing the ceiling was not enough, and a reviewer caught the half that was
left. The reason string still *said* `highest posted to it before this entry`,
and on this exact data that is false. `history` is only the slice the caller
handed over, and `as_score_book` hands over the first half of the published
rows, so postings that really do precede the entry sit outside it.

Measured on DHSC-00035:

| | |
|---|---:|
| what the flag quotes | 21,300,000 across 10 earlier entries |
| postings to the same account, dated **before** that entry, that the detector was never shown | **10** |
| the largest of those | **740,000,000** |
| ratio to the number in the flag | **35x** |

`Flag.reason` is the one field the frozen plan requires to name real evidence,
so a reason wider than its evidence is the same defect as a fabricated total
carrying a source tag — and it is the frozen-history problem of section 6
leaking out of the harness and into what the flag tells the operator.

**Route taken: narrow the claim.** The flag now reads

```
830000000 paise to Additions NCB PDC; of the 10 earlier entries on it in
the history this check was given, the largest is 21300000 paise, and this
is over 300 percent of that
```

**Why not "state the evidence window explicitly" instead.** The detector cannot
state the window honestly. It does not know what the caller withheld — it knows
only the rows it holds, so any sentence describing the window ("the first half
of the file", "the loaded history") would itself be a claim it cannot check.
That is the same defect one level up. What it *can* say truthfully is how many
earlier entries it could see and the largest among them, which is the narrowing
and carries the honest part of the window for free: `n` is in the flag, so the
bound on the evidence is visible to the operator.

**Not widening the history to make the old claim true.** That is the harness
change in section 6, measured and deliberately not shipped.

Three tests pin the claim against the evidence available, and mutant M8 in
section 7 restores the old wording and confirms they go red:

| test | what it holds |
|---|---|
| `test_the_flag_does_not_claim_to_know_the_highest_posting_that_preceded_it` | the scoping clause is present, four unscoped phrasings are absent, and the 10 withheld prior postings are shown to exist |
| `test_every_number_in_the_reason_comes_from_the_evidence_supplied` | the quoted maximum and count are exactly those of the supplied evidence, and 740,000,000 never appears in a flag |
| `test_a_withheld_prior_posting_changes_nothing_the_flag_says` | the same guard on a synthetic book, so it is not a fact about one file |

The second is the one that survives a rewording: widen the history and the
quoted maximum changes, so the claim cannot silently grow again.

### Measured effect

| Scope | Before | After |
|---|---:|---:|
| aggregate | 9 of 143 (**6.29**) | 6 of 143 (**4.20**) |
| held-out half | 2 of 69 (**2.90**) | 2 of 69 (**2.90**) |
| DHSC | 7 of 21 (**33.33**) | 4 of 21 (**19.05**) |
| `magnitude` alone, all books | 7 | 4 |
| `magnitude` alone, DHSC | 6 of 21 | 3 of 21 |
| every other department | — | unchanged |
| false alarms **created** | — | **0** |

The launch gate is still **NOT_PASSED**: DHSC at 19.05 is over the target, and
DBT still has no clean entry to be measured on.

### 3.1 A divergence the owner should see: the procedure now derives 150

Taking noise out of the calibration half lets the keep-rule — "the most
sensitive setting whose union rate still fits the budget" — reach one grid point
further down. `accountant/score/calibration.py` now derives
`over_percent=150` where it derived `300`.

**It was reported, not adopted.** Measured on the same books:

| department | at the shipped 300 | at the derived 150 |
|---|---:|---:|
| MHCLG | 0.00 | 6.90 |
| DHSC | 19.05 | 19.05 |
| DWP | 3.70 | 3.70 |
| **DEFRA** | **5.26** | **10.53 — over target** |
| aggregate | 4.20 | 6.29 |

Adopting 150 would create a *new* failing department. The keep-rule bounds the
union rate on the calibration half; owner decision D-22 later added a
per-department gate the procedure knows nothing about, so the procedure can now
derive a setting a department fails. Moving a shipped threshold is not a
root-cause fix, so the shipped margin stays at 300, which is the **stricter** of
the two.

`test_the_shipped_magnitude_margin_is_never_looser_than_the_derived_one` and
`test_adopting_the_derived_margin_would_put_a_department_over_the_target` hold
both halves. A shipped margin *looser* than the derived one still fails.

**Status: `OWNER_DECISION_REQUIRED`** — whether to adopt 150 and accept DEFRA
at 10.53, or leave 300 and accept a detector less sensitive than its budget
allows. Not decided here.

---

## 4. The regression test, naming the account

`tests/test_phase8_detectors.py`. The account is named as a module constant so
it cannot come back under a different description:

```python
DHSC_ACCOUNT = "Additions NCB PDC"
```

| Test | What it holds |
|---|---|
| `test_the_dhsc_pdc_account_is_the_one_this_regression_is_about` | 10 prior rows, ceiling 21,300,000, all dated 2025-11-03 |
| `test_a_same_day_dhsc_pdc_entry_is_no_longer_a_false_alarm` | DHSC-00027, -00028, -00029: `prior_amounts` is empty, no flag |
| `test_the_other_three_still_fire_and_the_reason_is_recorded` | DHSC-00035, -00036, -00037: still flagged, pinned as a **failure that is still present** |
| `test_the_account_now_carries_three_false_alarms_and_not_six` | the count, on the whole department, by account name |
| `test_the_fix_did_not_move_either_calibrated_number` | 2 and 300 |

The three that remain are also carried in the corpus as
`magnitude/real/DHSC-00035` and its two neighbours, and the three that were
fixed are carried as cases that must **not** fire.

---

## 5. The corpus: four detectors, twenty-five cases each

`accountant/score/corpus.py`, run by `tests/test_phase8_corpus.py`.

| Required | Measured |
|---|---|
| detector cases | **100/100** |
| classified | **100/100** |
| silently skipped | **0** |
| unsafe classifications | **0** |
| provenance | **100/100** |
| expected outputs | **100/100** |
| labelled | **100/100** |
| detectors active in test mode | **4/4** |
| detectors with tests | **4/4** |
| detectors with provenance | **4/4** |
| detector crashes | **0** |
| matched expected output | **100/100** |

### Per detector

| detector | cases | public | synthetic | fired | silent | crashed | unsafe |
|---|---:|---:|---:|---:|---:|---:|---:|
| `vendor_switch` | 25 | 15 | 10 | 5 | 20 | 0 | 0 |
| `first_use` | 25 | 15 | 10 | 14 | 11 | 0 | 0 |
| `magnitude` | 25 | 15 | 10 | 7 | 18 | 0 | 0 |
| `gst_anomaly` | 25 | 0 | 25 | 13 | 12 | 0 | 0 |
| **total** | **100** | **45** | **55** | **39** | **61** | **0** | **0** |

Every detector both fires and stays silent inside its own twenty-five, so no
detector's block tests one branch.

### The labels, per owner answer Q5 = C

```
THIRD_PARTY_PUBLIC_EVIDENCE       45   real rows from committed UK
                                       central-government spend files,
                                       Open Government Licence v3.0
SYNTHETIC_EVIDENCE                55   constructed boundaries
REAL_ANONYMISED_EVIDENCE           0   NOT USED
HELD_OUT_CUSTOMER_LIKE_EVIDENCE    0   NOT USED
```

Two labels have nothing behind them because nobody has supplied a real
anonymised or held-out customer book. That is `H-02`, it is
`OPTIONAL_HUMAN_INPUT`, and `unused_labels()` returns both so the corpus cannot
be read as though it had customer evidence in it.

`gst_anomaly` is 25 of 25 synthetic and has to be: no UK spend file publishes a
tax column, so every loaded row carries `gst_paise` of `None`. The test checks
that against the files rather than asserting it.

### Two kinds of expected output, kept apart

| oracle | cases | what it is |
|---|---:|---|
| `CONSTRUCTED` | 55 | the input was built to have a known property, so the expectation is independent of any run |
| `PINNED` | 45 | read off a measured run once and frozen. It catches a change. **It is not an independent judgement about the payment** and must never be quoted as one |

Nobody injected an error into a published government ledger, so nothing in this
corpus is an answer key about whether an entry is *wrong*.

### What "unsafe" means, measured per case

A case is UNSAFE when the run breaks one of four invariants: a flag with no
checkable reason, a flag that became an unanswerable problem (a detector
refusing an entry instead of asking), a question leaking a ledger name from the
chart, or a flag raised against the wrong voucher. None of the four is a matter
of degree, so it is a count and not a score. It is **0**.

---

## 6. N1 with all four detectors: the exit number, and it FAILS

```
detector exit = FAIL
```

| Scope | Before this pull request | After |
|---|---:|---:|
| all four, aggregate | 52 of 143 (**36.36**) | 49 of 143 (**34.27**) |
| all four, held-out half | 29 of 69 (**42.03**) | 29 of 69 (**42.03**) |
| target | <= 10 | <= 10 |
| verdict | FAIL | **FAIL** |

Pinned by
`tests/test_phase8_detectors.py::test_n1_with_all_four_detectors_on_real_books_is_a_measured_failure`,
which asserts the failure rather than avoiding it.

### It is one detector

| detector alone | false alarms, of 143 clean entries |
|---|---:|
| `vendor_switch` | 2 |
| **`first_use`** | **44** |
| `magnitude` | 4 |
| `gst_anomaly` | 0 |

`first_use` is 44 of the 49. Removing every other detector's contribution
leaves N1 at 30.77 — still three times the target.

### Why `first_use` fails, measured rather than argued

Its false-alarm count **rises by three quarters — a factor of 1.77 — when the
only thing that changes is how much of the same book it is shown**:

| evidence window | false alarms |
|---|---:|
| the history it is given today | **44** |
| history narrowed to entries strictly before each one | **78** |

Its logic is untouched in both. It has no threshold to turn — that is recorded
in `WITHDRAWN` and predates this work. A detector whose answer moves that far
with the size of the evidence window is reporting the window, not the entry.

Every one of the 44 is an account that **is in the department's published chart
of accounts**, so `accounts_exist` passes on all of them. The claim in the
flag's own words — *"has never been used in this company"* — is true about the
fragment of history the detector holds and unsupported about the company.

**This is a missing input, not a missing threshold.** "Never used in this
company" needs to know whether the history it holds is the company's whole
posting history, and nothing in the detector's signature
`(proposed, history, index)` can tell it. Defining that input is a contract
change and it was not invented here under time pressure.

**Contract required, for whoever owns it:**

```
first_use needs a stated answer to "is this history the company's complete
posting history?"  Absent that answer it must abstain, and abstaining must be
reported as an abstention with its reason, never as a silence.
```

### The second contributor, and it is in this agent's own area

Three of the four remaining `magnitude` false alarms are DHSC-00035, -00036 and
-00037. Their history genuinely precedes them, so the detector is answering
honestly. The evidence is incomplete:
`accountant/score/harness.py:_evaluate_one` hands **every** entry the same
frozen half-book, so an entry at position 40 sees exactly what an entry at
position 21 sees, and the 740,000,000 payment made to the same account on
2025-11-03 — prior in fact — is on the entries side of the split and invisible.

Measured, by giving each entry the entries that actually preceded it by date:

| history model | `magnitude` | `vendor_switch` | `first_use` | active-3 aggregate | all-four aggregate |
|---|---:|---:|---:|---:|---:|
| frozen half-book (ships today) | 4 | 2 | 44 | 6 of 143 (**4.20**) | 49 of 143 (**34.27**) |
| grown with entries that precede each one | 2 | 5 | 34 | 7 of 143 (**4.90**) | 38 of 143 (**26.57**) |

**It was measured and not shipped, and the reason is not that it flatters the
number — it does not.** Growing the history makes the shipped three-detector
aggregate *worse*, 4.20 to 4.90, because `vendor_switch` gains parties. It was
left alone because it changes what every recorded score in this project means,
it would need `test_score.py`, `test_n1.py`, `test_acceptance_n10.py`,
`test_detector_gate.py` and `calibration.py` re-measured together, and it does
not change the exit either way: all four still fails at 26.57. Recorded as a
measured finding with the experiment beside it.

**Status: `OWNER_DECISION_REQUIRED`.**

---

## 7. The guards are load-bearing: eight mutants, eight caught

Each defect was injected on its own, the whole suite was run, and the file was
restored and byte-compared afterwards. `git status` was clean after every one.

| Mutant | File | Result | Tests red |
|---|---|---|---:|
| M1 a detector refuses an entry instead of asking | `accountant/problems.py` | **CAUGHT** | 17 |
| M2 a flag ships with an empty reason | `accountant/detect/detectors.py` | **CAUGHT** | 125 |
| M3 a question contains a chart account name | `accountant/questions.py` | **CAUGHT** | 2 |
| M4 the ranking becomes non-deterministic | `accountant/detect/detectors.py` | **CAUGHT** | 2 |
| M5 the cap silently drops overflow | `accountant/detect/detectors.py` | **CAUGHT** | 5 |
| M6 the DHSC regression is removed | `accountant/detect/detectors.py` | **CAUGHT** | 36 |
| M7 N1 is computed from a different denominator | `accountant/score/harness.py` | **CAUGHT** | 2 |
| M8 the flag claims to know the highest posting that preceded it | `accountant/detect/detectors.py` | **CAUGHT** | 3 |

Eight injected, eight caught, none survived. M8 was added on 2026-08-10 with the
reason-string narrowing in section 3.0; it restores the unscoped wording and the
suite notices.

The named test for each:

```
M1  test_detectors.py::test_a_fired_detector_asks_and_never_refuses
    test_decide.py::test_fired_detector_now_asks_instead_of_refusing
    test_phase8_corpus.py::test_no_case_produced_an_unsafe_classification

M2  Flag.__post_init__ refuses the construction, so 125 tests fall over,
    including every gate and evidence test that reads a reason

M3  test_questions.py::test_no_question_contains_a_ledger_account_name
    test_phase8_corpus.py::test_no_case_produced_an_unsafe_classification

M4  test_phase6_exits.py::test_a_severity_tie_is_broken_by_the_detector_name
    test_phase6_exits.py::test_the_rank_falls_through_to_the_voucher_id_when_all_else_ties

M5  test_detectors.py::test_the_cap_drops_nothing_silently
    test_flag_cap.py::test_the_cap_arithmetic_holds_at_the_detector_boundary
    test_phase6_exits.py::test_the_cap_reports_the_overflow_as_a_count_and_drops_the_lowest_ranked

M6  test_phase8_detectors.py::test_a_same_day_dhsc_pdc_entry_is_no_longer_a_false_alarm
    test_phase8_detectors.py::test_the_account_now_carries_three_false_alarms_and_not_six
    test_phase8_corpus.py::test_every_case_matched_its_expected_output
    plus 31 more

M7  test_score.py::test_a_book_with_no_clean_entries_fails_n1_rather_than_passing
    test_score.py::test_the_measured_value_is_printed_with_its_unit

M8  test_phase8_detectors.py::test_the_flag_does_not_claim_to_know_the_highest_posting_that_preceded_it
    test_phase8_detectors.py::test_a_withheld_prior_posting_changes_nothing_the_flag_says
    test_detector_gate.py::test_half_the_false_alarms_are_still_one_account
```

M3 is worth a line on its own. Two tests caught it, and one of them is the new
corpus safety count — so the hundred cases are not decoration, they are a guard
that fires.

---

## 8. Does production run one detector or four?

**One.**

```
accountant/pipeline.py:365   detector_set = detectors.SLICE_4_DETECTORS
accountant/pipeline.py:811   detector_set = detectors.SLICE_4_DETECTORS
accountant/detect/detectors.py
                             SLICE_4_DETECTORS = (vendor_switch,)
```

Unchanged by this pull request, deliberately. Enabling all four in production
would ship a detector set measured at 34.27 against a target of 10, and the
owner's instruction is *do not ship four detectors with N1 failing*.

No feature flag was used to claim otherwise. All four were enabled **in the
measurement and in the test corpus**, which is where "4/4 active in test mode"
comes from, and they are not enabled in the decision path.

**The all-four exit is not met, and this section is the plain statement of it.**

---

## 9. The exit table

| Acceptance condition | Required | Measured | Verdict |
|---|---|---|---|
| detector cases | 100 | 100 | PASS |
| cases classified | 100/100 | 100/100 | PASS |
| silently skipped | 0 | 0 | PASS |
| unsafe classifications | 0 | 0 | PASS |
| provenance | 100% | 100% | PASS |
| detectors active in test mode | 4/4 | 4/4 | PASS |
| detectors with tests | 4/4 | 4/4 | PASS |
| detectors with provenance | 4/4 | 4/4 | PASS |
| detector crashes | 0 | 0 | PASS |
| **N1, all four, real ledgers** | **<= 10** | **34.27** | **FAIL** |
| production runs four detectors | yes | **no — one** | **FAIL** |
| **detector exit** | | | **FAIL** |

Phase 8 is not complete on this exit. What would close it is named in section 6
and neither half of it is a threshold.

---

## 10. What this document does not tell you

- **N1 on the hundred-case corpus is 0.00 per 100** — 0 false alarms of 61
  cases whose correct answer is silence. That is a fact about a corpus written
  in this repository and it proves nothing about a real book. It is not the
  exit number and `test_the_corpus_n1_is_not_the_exit_number` asserts the real
  one beside it.
- **This is still 49 events on 143 rows.** One more flagged entry moves the
  aggregate by 0.70. A real measurement, and a small one.
- **`gst_anomaly` has never fired on real data** and cannot: the published
  files carry no tax column. Its 0 in section 6 is a rate of nought, not
  evidence.
- **N3 is not measured on these files at all.** Nobody injected errors into a
  real government ledger, so there is no answer key.
- **Real-bill accuracy stays `NOT_MEASURED`** and `S2` stays `NOT_MEASURED`.
  Nothing here is evidence about an Indian customer book.

---

## 11. Where things live

| what | where |
|---|---|
| the fix | `accountant/detect/detectors.py`, `prior_amounts` |
| the regression, naming the account | `tests/test_phase8_detectors.py` |
| the hundred cases | `accountant/score/corpus.py` |
| the corpus counts | `tests/test_phase8_corpus.py` |
| the launch gate | `accountant/score/harness.py`, `DetectorGate` |
| the earlier gate measurement | `artifacts/detector_gate.md` |
| the data | `accountant/ingest/fixtures/*.csv`, Open Government Licence v3.0 |
