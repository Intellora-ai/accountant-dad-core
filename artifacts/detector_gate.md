# Detector launch gate

**Verdict: NOT_PASSED.**

Branch `owner/d22-detector-gate`, from commit `27333e9`. Measured 2026-08-10.
Owner decision D-22, answered 2026-08-10.

Every number here was produced by running the code. None was copied from a
document.

---

## 1. The short version

Two numbers decide this gate. Not one.

| what | number | target | verdict |
|---|---|---|---|
| aggregate, all 7 departments | **6.29** per 100 | <= 10 | PASS |
| held-out half | **2.90** per 100 | <= 10 | PASS |
| **worst department — DHSC** | **33.33** per 100 | <= 10 | **NOT_PASSED** |
| DBT | not measured | <= 10 | **NOT_PASSED** |
| **the gate** | | | **NOT_PASSED** |

The average is fine. One department is 3.33 times over the line.

An average over seven departments is not a promise about any one of them. The
department a customer actually has is the one that matters to them. So the
gate reads both, and the failing one wins.

**The gate stays NOT_PASSED until one of two things happens:**

1. Every department comes inside 10 per 100 clean entries.
2. The owner writes down, in words, that a department is out of scope.

Nothing else clears it. Not a new threshold. Not dropping a department. Not a
smaller denominator. Not deleting a hard case. The code refuses all four —
see section 8.

---

## 2. What the numbers mean, in plain words

**N1** is "false alarms per 100 clean entries".

- A **clean entry** is a real entry with nothing wrong with it.
- A **false alarm** is a clean entry the software flagged anyway.
- **6.29 per 100** means: out of every 100 good entries, about 6 get flagged
  for no reason.
- The owner's limit is 10.

The data is real. Seven UK government departments published their spending for
November 2025. Nobody planted errors in those files. So every entry in them is
a clean entry, and every flag on one is a false alarm.

---

## 3. Every row: current, desired, gap, pass rule

The pass rule is the same for every row: `false_alarms * 100 <= 10 * clean`.
It is checked on whole numbers, never on the printed one.

### Scopes

| row | current | desired | gap | pass rule | verdict |
|---|---|---|---|---|---|
| aggregate, all 7 | **6.29** (9 of 143) | <= 10 | 3.71 **under** | `900 <= 1430` | PASS |
| held-out half | **2.90** (2 of 69) | <= 10 | 7.10 **under** | `200 <= 690` | PASS |
| calibration half | **9.46** (7 of 74) | <= 10 | 0.54 **under** | `700 <= 740` | PASS, no room |

### Departments — all seven, the quiet ones too

| department | current | desired | gap | pass rule | verdict |
|---|---|---|---|---|---|
| MHCLG | **0.00** (0 of 29) | <= 10 | 10.00 under | `0 <= 290` | PASS |
| **DHSC** | **33.33** (7 of 21) | <= 10 | **23.33 over** | `700 <= 210` is false | **NOT_PASSED** |
| DFT | **0.00** (0 of 24) | <= 10 | 10.00 under | `0 <= 240` | PASS |
| DWP | **3.70** (1 of 27) | <= 10 | 6.30 under | `100 <= 270` | PASS |
| DEFRA | **5.26** (1 of 19) | <= 10 | 4.74 under | `100 <= 190` | PASS |
| HMT | **0.00** (0 of 23) | <= 10 | 10.00 under | `0 <= 230` | PASS |
| **DBT** | **not measured** (0 clean) | <= 10 | **cannot be measured** | needs at least 1 clean entry | **NOT_PASSED** |

Two departments fail. DHSC fails on its number. DBT fails because there is no
number.

**Why DBT has no number.** DBT publishes a description column and leaves every
cell in it empty. All 199 rows of the real file, not just the slice committed
here. So no entry loads, and no detector has anything to fire on. Nothing
measured is not a pass. It is a hole in the coverage, and it is reported as
one.

**A note on the calibration half.** 9.46 against a cap of 10, with exactly
zero spare entries. One more flagged clean entry there and it fails. That is
not bad luck. The threshold-picking rule takes the most sensitive setting that
still fits the budget, so it spends the budget by design.

---

## 4. Where the leverage is: one account, six of the nine alarms

This is the most useful thing in this document.

There are 9 false alarms in total. **Six of them are the same account.**

| | |
|---|---|
| department | DHSC |
| account | `Additions NCB PDC` |
| false alarms | **6 of 9** — 66.67% of them all |
| detector | `magnitude` |
| the ceiling it used | 21,300,000 paise |
| where the ceiling came from | 10 prior entries |
| how many different NHS trusts went past it | **6** |

The six entries:

| voucher | trust | amount (paise) | times the ceiling |
|---|---|---|---|
| DHSC-00037 | BOLTON NHS FOUNDATION TRUST | 830,000,000 | ~39x |
| DHSC-00029 | NORTHUMBRIA HEALTHCARE NHS FOUNDATION TRUST | 740,000,000 | ~35x |
| DHSC-00036 | TAVISTOCK AND PORTMAN NHS FOUNDATION TRUST | 217,000,000 | ~10x |
| DHSC-00028 | MID CHESHIRE HOSPITALS NHS FOUNDATION TRUST | 187,500,000 | ~9x |
| DHSC-00035 | MAIDSTONE & TUNBRIDGE WELLS NHS TRUST | 174,400,000 | ~8x |
| DHSC-00027 | DORSET COUNTY HOSPITAL NHS FOUNDATION TRUST | 81,800,000 | ~4x |

### What this means

This is **not** six separate problems. It is **one wrong ceiling, counted six
times**.

`Additions NCB PDC` is Public Dividend Capital. That is lumpy capital
injection — big, irregular payments into NHS trusts. A ten-entry history of a
thing like that is not a range. It is ten samples of something that has no
normal size.

The `magnitude` detector is doing exactly what it was written to do. The
account is the wrong shape for it.

### The size of the prize

If that one ceiling were right:

- DHSC would drop from 7 of 21 (**33.33**) to 1 of 21 (**4.76**) — inside target.
- The aggregate would drop from 9 of 143 (**6.29**) to 3 of 143 (**2.10**).

**I have not done that.** Excluding a hard case is forbidden, and it would be
a lie. It is written here so the next person knows where to push.

**The fix is one account's ceiling. It is not a threshold.** Raising the
`magnitude` threshold for everyone would hide six real oddities and lose the
detector's value on every other account. Changing what `Additions NCB PDC` is
compared against would fix six alarms and touch nothing else.

Making that change is a detector decision, and it is a separate decision. It
is not made here.

---

## 5. The other three false alarms

All nine are listed, not a sample. Here are the three that are not the PDC
account.

| voucher | dept | detector | party | why it fired |
|---|---|---|---|---|
| DWP-00037 | DWP | `vendor_switch` | G4S FACILITIES MANAGEMENT (UK) LTD | posted to `…CONTACT CENTRES` 2 times; this one goes to `…SOCIAL CARE` |
| DHSC-00039 | DHSC | `vendor_switch` | NHS ENGLAND CBA033 | posted to `Grant in Aid Funding (Cash) - ENDPBs` 2 times; this one goes to `Research & development` |
| DEFRA-00035 | DEFRA | `magnitude` | WSP UK LTD | ceiling for that account is 5,000,000 paise from 4 entries; this is 237,382,815 |

The full nine, with the exact reason each detector gave, are printed by
`render_gate`.

---

## 6. The PASS is bought by switching a detector off

This makes the aggregate look worse than it reads.

There were four detectors. One of them, `first_use`, is **withdrawn**. It does
not run. The withdrawal is declared in the code and printed in every report.

Switch it back on, at the settings the other three ship at:

| scope | with `first_use` off (ships today) | with `first_use` on | verdict with it on |
|---|---|---|---|
| aggregate | **6.29** (9 of 143) | **36.36** (52 of 143) | NOT_PASSED |
| held-out | **2.90** (2 of 69) | **42.03** (29 of 69) | NOT_PASSED |

So: **N1 passes because one of the four detectors is turned off.**

`first_use` covered one concern — *"this account has never been used before"*.
Nothing covers that concern now. That is a gap sitting behind a passing
number.

Why it was withdrawn: there is no threshold that separates an account this
company genuinely never used from one it simply has not loaded yet. It has no
knob to turn. On real ledgers it fires on about three clean entries in ten.

---

## 7. Denominator and formula

### The denominator

Every clean entry in the books measured. An entry with no injected error.

- 143 clean entries in total, across 7 departments.
- Silencing a flag cannot shrink it.
- An entry nobody reported is still counted.

The denominator is the number of entries in the files, not the number of
entries the software chose to say something about. That is on purpose: if the
denominator were "entries reported", hiding a flag would improve the score.

### The formula

The rate, in hundredths:

```
(false_alarms * PERCENT_SCALE * 2 + clean_entries) // (clean_entries * 2)
PERCENT_SCALE = 10000
```

That expression is round-half-up over whole numbers. `accountant/score/harness.py:91-93`,
with `PERCENT_SCALE` at `:75`.

The verdict:

```
false_alarms * 100 <= target * clean_entries
```

Decided on whole numbers, never on the printed one. So 10.05 per 100 fails
even though it prints as "about 10".

No float touches any of it.

---

## 8. Four ways to make this look better, and why none of them work

The gate object refuses to be built three of these ways. It raises an error
instead of producing a nicer number.

| the shortcut | what stops it |
|---|---|
| tune a threshold | the target is the owner constant `N1_MAX_FALSE_ALARMS_PER_100 = 10`, pinned by a test |
| drop a department | the departments' false alarms and clean entries must add up to the aggregate's |
| shrink the denominator | the same check, on the clean-entry count |
| delete a hard case | the false-alarm examples must account for every false alarm counted, per department |

These are arithmetic checks in `DetectorGate.__post_init__`, not a reviewer
remembering to look. A gate missing DHSC does not report a better number. It
raises.

### Are the tests actually load-bearing?

A test that passes no matter what the code does is not a test. So the code was
deliberately broken three ways, and the suite had to notice each time.

| the break | tests that went red | result |
|---|---|---|
| decide the gate on the aggregate alone | 3 | **caught** |
| drop the worst department from the headline | 2 | **caught** |
| round a failing number down to the whole percent (33.33 -> 33.00) | 11 | **caught** |

Three breaks, three caught, none survived. The code was restored after each.

---

## 9. How to reproduce this

```
COVERAGE_CORE=pytrace .venv/bin/python -m pytest tests/test_detector_gate.py -q
```

68 tests. All pass. They cover `accountant/score` to 100%, branches included.
The whole suite is 1832 passed, 6 xfailed.

To print the report itself:

```python
from accountant.ingest import sources, spend
from accountant.score import calibration as cal
from accountant.score.harness import gate_from_books
from accountant.score.report import render_gate

books = {s.code: spend.as_score_book(spend.load_source(s)) for s in sources.ALL_SOURCES}
code_of = {b.company: c for c, b in books.items()}
_, held = cal.split(list(books.values()))

print(render_gate(gate_from_books(books, held_out=[code_of[b.company] for b in held])))
```

### The same report twice

Rendered under five different `PYTHONHASHSEED` values. That is the specific
thing that would expose set or dictionary ordering leaking into the output.

| `PYTHONHASHSEED` | sha256 of the report |
|---|---|
| 0 | `6e382b3ed1c542f577eee68288bb9b112a7ed8195e6dbcb2c0762661975a1f83` |
| 1 | `6e382b3e…975a1f83` |
| 12345 | `6e382b3e…975a1f83` |
| 99991 | `6e382b3e…975a1f83` |
| random | `6e382b3e…975a1f83` |

All five identical. A test hashes two separate measurement runs and compares
them, so this stays true.

---

## 10. What this gate does not tell you

- **It is 9 events on 143 rows.** One more flagged entry moves the aggregate
  by 0.70. The held-out 2.90 is two entries. This is a real measurement and a
  small one. Do not quote it as though it had the weight of a large one.
- **A 0.00 is not proof of quietness.** MHCLG, DFT and HMT contribute no false
  alarms. That is also what a detector that reads nothing looks like. The
  catch rate on real ledgers cannot be measured — there is no answer key.
- **`gst_anomaly` has never fired.** UK government spend files carry no GST
  column, so it reads a field the data does not have. Its 0.00 is a rate of
  nought, not evidence.
- **N3 is not measured here at all.** Nobody injected errors into a real
  government ledger.

---

## 11. Where things live

| what | where |
|---|---|
| the gate and its verdict | `accountant/score/harness.py`, `DetectorGate` |
| the rate and the pass rule | `accountant/score/harness.py:91-93`, `:75` |
| the printed report | `accountant/score/report.py`, `render_gate` |
| the tests | `tests/test_detector_gate.py` |
| the underlying measurement evidence | `artifacts/detector_evidence.md`, `artifacts/detector_evidence.json` |
| the data | `accountant/ingest/fixtures/*.csv`, Open Government Licence v3.0 |
