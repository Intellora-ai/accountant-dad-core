# N1 detector measurement — evidence

Branch `closure/flag-cap-and-truth`, commit `3445992b98295a6658542f5d9211c91ab91480de`, working tree clean.
Measured 2026-08-10. Machine-readable twin: `artifacts/detector_evidence.json`.

Every number below was produced by the shipping code path, not read from a doc.
No threshold was changed, no case excluded, no denominator altered, nothing rounded
into a pass.

---

## 0. Headline — the four owner-stated numbers

| # | claim | owner states | measured | reproduced? |
|---|-------|-------------|----------|-------------|
| 1 | old pre-calibration, MHCLG only | 27.59 | **27.59** (8 of 29) | **YES — exact** |
| 2 | current aggregate | 6.29 | **6.29** (9 of 143) | **YES — exact** |
| 3 | current held-out | 2.90 | **2.90** (2 of 69) | **YES — exact** |
| 4 | worst department | 33.33 | **33.33** (7 of 21, DHSC) | **YES — exact** |

All four reproduce to the hundredth. Nothing in the owner's statement is wrong.

Against the target of `<= 10`:

- aggregate **PASS**
- held-out **PASS**
- worst department (DHSC) **NOT_PASSED** — and the owner already says so

Test suite: `1357 passed in 85.72s`, 0 failed, 0 skipped, 0 errored.

---

## 1. The formula, quoted from code

There is **no `python -m accountant.score` CLI**. `accountant/score/` is a package with
no `__main__.py`; the only entry points in the tree are `accountant/tallyio/__main__.py`
and `accountant/web/__main__.py`. N1 is reached as a library call. Verified:

```
$ .venv/bin/python -m accountant.score
No module named accountant.score.__main__; 'accountant.score' is a package
and cannot be directly executed
```

### The rate

`accountant/score/harness.py:91-93`

```python
def scaled_rate(numerator: int, denominator: int, scale: int) -> int:
    """numerator over denominator, times scale, rounded half up, in integers."""
    return (numerator * scale * 2 + denominator) // (denominator * 2)
```

`accountant/score/harness.py:75`

```python
PERCENT_SCALE = 10_000
```

So N1 in hundredths is `scaled_rate(false_alarms, clean, 10_000)`. That expression **is**
round-half-up over positive integers: `(2ns + d) // 2d == floor(ns/d + 1/2)`. It matches
the frozen plan's `round_half_up(false_alarm_entries / clean_entries * 10000)` exactly.
The unit is **hundredths of one false alarm per 100 clean entries** — `629` reads as
`6.29 per 100`, not basis points.

Two call sites produce the same number:

`accountant/score/harness.py:381` (whole-book harness)

```python
measured_hundredths=scaled_rate(false_alarms, clean, PERCENT_SCALE),
```

`accountant/score/calibration.py:144-148` (multi-book measurement — the one the real-data
numbers come from)

```python
@property
def per_100_hundredths(self) -> int | None:
    """False alarms per 100 clean entries, in hundredths. None if unmeasured."""
    if not self.measured:
        return None
    return scaled_rate(self.flagged, self.clean, PERCENT_SCALE)
```

### The verdict — decided on integers, not on the printed number

`accountant/score/harness.py:382-384`

```python
status=Status.MET
if false_alarms * 100 <= N1_MAX_FALSE_ALARMS_PER_100 * clean
else Status.MISSED,
```

`accountant/score/calibration.py:150-152`

```python
def within(self, target_per_100: int) -> bool:
    """True only when the rate was measured AND is inside the target."""
    return self.measured and self.flagged * 100 <= target_per_100 * self.clean
```

`accountant/score/harness.py:70`: `N1_MAX_FALSE_ALARMS_PER_100 = 10`.

Probed the boundary directly:

| flagged / clean | printed | verdict |
|---|---|---|
| 10 / 100 | 10.00 | PASS |
| 1 / 10 | 10.00 | PASS |
| 1000 / 9999 | **10.00** | **FAIL** |
| 2001 / 20000 | 10.01 | FAIL |

`1000/9999` prints `10.00` and still FAILs. The printed figure and the verdict can
disagree, and they disagree **in the safe direction** — the exact comparison refuses a
rate that only rounds down to the cap. This is correct behaviour, recorded so nobody
later "fixes" it.

### Numerator and denominator

`accountant/score/calibration.py:164-178`

```python
flagged = 0
clean = 0
for book in books:
    index = MemoryIndex.from_vouchers(book.history)
    injected = book.truth.by_voucher()
    for entry in book.entries:
        if entry.id in injected:
            continue
        clean += 1
        flags, _ = detectors.run(
            entry, book.history, index, detector_set, dedupe=False
        )
        if flags:
            flagged += 1
return CleanMeasurement(flagged=flagged, clean=clean)
```

Two properties worth stating out loud:

- The numerator counts **entries**, not flags. One entry caught by three detectors is
  one false alarm. Correct — that is what N1 is defined as.
- It runs with `dedupe=False`, so suppression cannot shrink the numerator.

Same counting in the whole-book harness, `accountant/score/harness.py:492-493`:

```python
clean = sum(1 for r in results if r.error_type is None)
false_alarms = sum(1 for r in results if r.error_type is None and r.flagged)
```

---

## 2. The denominator — 143 clean entries

**143.** Every entry is clean: these are real published UK central-government ledgers
with nothing injected, so ground truth is empty and every entry counts toward the
denominator.

Cross-checked two independent ways, both give 143:

- `cal.measure(books(), ACTIVE_DETECTORS).clean` = 143
- sum of `harness.score(book, ...).clean_entries` over the seven books = 143

Where 143 comes from — this is a **subset of the fixture rows**, and the reduction is
worth stating because a small denominator makes the rate jumpy:

| dept | fixture rows | loaded | rejected | history | **scored entries** |
|---|---|---|---|---|---|
| MHCLG | 57 | 57 | 0 | 28 | 29 |
| DHSC | 41 | 41 | 0 | 20 | 21 |
| DFT | 47 | 47 | 0 | 23 | 24 |
| DWP | 54 | 54 | 0 | 27 | 27 |
| DEFRA | 38 | 38 | 0 | 19 | 19 |
| HMT | 46 | 46 | 0 | 23 | 23 |
| DBT | 28 | **0** | **28** | 0 | **0** |
| **total** | **311** | **283** | **28** | **140** | **143** |

Two facts behind the drop from 311 to 143:

1. **DBT contributes nothing.** All 28 committed rows are rejected with
   `narration is empty`. That is a property of the real published file, documented at
   `accountant/ingest/sources.py:146-150`. DBT is therefore *unmeasured*, not passing.
2. **Half of every department is history, not entries.** `split_point` at
   `accountant/ingest/spend.py:524-531` returns `count // 2`; the earlier half becomes
   the memory the detectors learn from and the later half is what gets scored. 283
   loaded vouchers split into 140 history + 143 entries.

`tests/test_n1.py:110-120` guards this denominator against being silently shrunk.

---

## 3. Flagged clean-entry count

**9** of 143, aggregate, with `ACTIVE_DETECTORS`.

| scope | flagged | clean | N1 |
|---|---|---|---|
| aggregate (all 7) | **9** | 143 | 6.29 |
| calibration half | 7 | 74 | 9.46 |
| held-out half | **2** | 69 | 2.90 |

Cross-checked against the whole-book harness: summed `false_alarms` over the seven
`ScoreReport`s = 9. The two code paths agree.

---

## 4. Raw flags and duplicate views

A "duplicate view" here is the same entry counted twice through different detectors —
`len(raw) - len(deduped)` per entry, per `accountant/detect/detectors.py:370-400` and
`accountant/score/harness.py:279,295`.

| configuration | raw flags | distinct alerts | **duplicate views** | flagged entries |
|---|---|---|---|---|
| aggregate, ACTIVE_DETECTORS | **9** | 9 | **0** | 9 |
| MHCLG, pre-calibration | 9 | 8 | **1** | 8 |
| all 7, pre-calibration | 113 | 83 | **30** | 79 |

**Duplicate views are zero on the shipping configuration.** On these seven files no two
active detectors ever fire on the same entry, so raw flags == distinct alerts == flagged
entries == 9. Independently confirmed by summing `ScoreReport.duplicate_flags` across the
seven books: 0, with `distinct_problems` = 9.

This matters for interpreting the number: **de-duplication is buying nothing on the
current data.** The 6.29 is not being helped by folding. The 30 duplicate views in the
pre-calibration column show the mechanism does work when detectors overlap — it is idle
now because `first_use`, which shared a concern with `vendor_switch`
(`accountant/detect/detectors.py:296-301`), is withdrawn.

---

## 5. Aggregate N1

| field | value |
|---|---|
| metric | N1, aggregate over all 7 committed departments |
| actual | **6.29** per 100 clean entries (629 hundredths; 9 of 143) |
| owner stated | 6.29 |
| reproduced | **YES, exact** |
| pass rule | `false_alarms * 100 <= 10 * clean` → `900 <= 1430` |
| **verdict** | **PASS** |
| evidence | `accountant/score/calibration.py:150-152`, `:164-178` |

Command:

```
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  /private/tmp/claude-501/.../scratchpad/measure_n1.py
```

Pinned in-repo at `tests/test_n1.py:102-107`.

---

## 6. Held-out N1

| field | value |
|---|---|
| metric | N1, held-out half only |
| actual | **2.90** per 100 clean entries (290 hundredths; 2 of 69) |
| owner stated | 2.90 |
| reproduced | **YES, exact** |
| pass rule | `200 <= 690` |
| **verdict** | **PASS** |
| evidence | `accountant/score/calibration.py:221-229` (split), `:150-152` |

The split is `sorted(books, key=company)` then alternate — `calibration.py:228-229`. No
randomness, no seed, no judgement call. It resolves to:

- **calibration half** (74 clean): DBT, DFT, DHSC, MHCLG
- **held-out half** (69 clean): DEFRA, DWP, HMT

Confirmed the thresholds were not chosen on the held-out books: `tests/test_n1.py:222-235`
recalibrates on the calibration half alone and gets identical settings. That test passes.

---

## 7. Worst department

| field | value |
|---|---|
| metric | worst single department, ACTIVE_DETECTORS |
| **department** | **DHSC — Department of Health and Social Care** |
| actual | **33.33** per 100 clean entries (3333 hundredths; 7 of 21) |
| owner stated | 33.33 |
| reproduced | **YES, exact** |
| pass rule | `700 <= 210` → false |
| **verdict** | **NOT_PASSED — 3.33x over target** |
| evidence | `tests/test_n1.py:329-346`; measured directly |

Every department, after calibration, ranked:

| dept | flagged | clean | N1 | verdict | half | pre-calibration |
|---|---|---|---|---|---|---|
| **DHSC** | 7 | 21 | **33.33** | **NOT_PASSED** | calibration | 80.95 |
| DEFRA | 1 | 19 | 5.26 | PASS | held-out | 84.21 |
| DWP | 1 | 27 | 3.70 | PASS | held-out | 29.63 |
| MHCLG | 0 | 29 | 0.00 | PASS | calibration | 27.59 |
| DFT | 0 | 24 | 0.00 | PASS | calibration | 45.83 |
| HMT | 0 | 23 | 0.00 | PASS | held-out | 82.61 |
| DBT | 0 | 0 | n/a | **NOT_PASSED (unmeasured)** | calibration | n/a |

DHSC is in the **calibration** half. The procedure had it in front of it the whole time
and still could not bring it inside the target without withdrawing the detector
completely. Within DHSC: `magnitude` alone accounts for 6 of 21, `vendor_switch` for 1.

DBT reports NOT_PASSED because `CleanMeasurement.within` returns False on zero clean
entries (`calibration.py:150-152`) — absent evidence is not a pass. Correct, and worth
keeping visible: DBT is a hole in the coverage, not a clean department.

---

## 8. The historical 27.59 — the code path still exists

**Reproduced exactly: 27.59 (8 of 29 clean entries), MHCLG only.**

The pre-calibration path is not deleted. It is reconstructed from parameters, not
remembered, via the configured-copy factories at `accountant/detect/detectors.py:209-241`:

```python
(vendor_switch_at(1), first_use, magnitude_at(1, 100), gst_anomaly)
```

which is `vendor_switch` firing after a **single** prior posting, `magnitude` firing at
**one paise** over a maximum taken from a **single** observation, plus `first_use`, which
now ships withdrawn. `tests/test_n1.py:53-64` defines it; `tests/test_n1.py:136-146` pins
the reproduction.

| measurement | flagged | clean | N1 | verdict |
|---|---|---|---|---|
| MHCLG, pre-calibration | 8 | 29 | **27.59** | NOT_PASSED |
| MHCLG, shipping today | **0** | 29 | **0.00** | PASS |
| all 7, pre-calibration | 79 | 143 | 55.24 | NOT_PASSED |
| all 7, shipping today | 9 | 143 | 6.29 | PASS |

**What changed**, per detector, over all 143 clean entries:

| detector | before | after | change |
|---|---|---|---|
| `vendor_switch` | 38 of 143 (26.57) at `min_postings=1` | 2 of 143 (1.40) at `min_postings=2` | threshold raised |
| `magnitude` | 31 of 143 (21.68) at `over_percent=100, min_obs=1` | 7 of 143 (4.90) at `over_percent=300, min_obs=2` | threshold raised |
| `first_use` | 44 of 143 (30.77) | **not run** | **withdrawn** |
| `gst_anomaly` | 0 of 143 | 0 of 143 | unchanged |

An important caveat on the 27.59 → 0.00 comparison for MHCLG specifically: MHCLG going to
**zero** does not mean the detectors improved on MHCLG. It means they stopped firing there
at all. Zero false alarms on 29 entries with no injected errors is also what a detector
that reads nothing looks like. The catch rate on MHCLG is unmeasurable — real ledgers have
no answer key — so the honest statement is "MHCLG contributes no false alarms", not
"MHCLG is now handled correctly".

---

## 9. Per-detector breakdown — flags on clean entries

Each detector run **alone** over all 143 clean entries:

| detector | active? | clean entries flagged | raw flags | N1 solo | within 10? |
|---|---|---|---|---|---|
| `magnitude` | yes | **7** / 143 | 7 | **4.90** | yes |
| `vendor_switch` | yes | **2** / 143 | 2 | **1.40** | yes |
| `gst_anomaly` | yes | **0** / 143 | 0 | **0.00** | yes |
| `first_use` | **no — withdrawn** | **44** / 143 | 44 | **30.77** | **no** |

As charged inside the shipping harness union run (`harness.score`, `per_detector`, which
charges a detector even when its alert was folded):

| detector | false alarms | clean | N1 |
|---|---|---|---|
| `magnitude` | 7 | 143 | 4.90 |
| `vendor_switch` | 2 | 143 | 1.40 |
| `gst_anomaly` | 0 | 143 | 0.00 |

Solo and union agree exactly (7 + 2 + 0 = 9), which is the arithmetic consequence of zero
duplicate views. **`magnitude` owns 78% of N1** (7 of 9).

`gst_anomaly` reports 0.00. That is a **rate of nought, not evidence of quietness** — the
code says so itself at `accountant/score/calibration.py:250-266`. UK government spend files
carry no GST column, so the detector reads a field the data does not have. It has never
been exercised on this corpus. Treating its 0.00 as a passing measurement would be a
mistake.

Calibration record, both halves, per detector:

| detector | kept | setting | on calibration | on held-out |
|---|---|---|---|---|
| `vendor_switch` | yes | `min_postings=2` | 1/74 = 1.35 | 1/69 = 1.45 |
| `first_use` | **no** | — | 17/74 = **22.97** | 27/69 = **39.13** |
| `magnitude` | yes | `min_observations=2, over_percent=300` | 6/74 = 8.11 | 1/69 = 1.45 |
| `gst_anomaly` | yes | no setting to turn | 0/74 = 0.00 | 0/69 = 0.00 |

---

## 10. Per-rule breakdown

**There are no rules. `accountant/rules/` does not exist.**

The taxonomy defines a `RULE` route at `accountant/taxonomy/coverage.py:81-91` — "an
accounting rule or an invariant check" — and every finding routed to it is marked as
needing `accountant/rules/`, scheduled for Phase 8
(`accountant/taxonomy/coverage.py:253, :339, :407, :506`; `accountant/taxonomy/__init__.py:25`).
Nothing on that route runs today, so nothing on that route contributes to N1.

Every current N1 event comes through the detector route. A per-rule breakdown is
`null` in the JSON because the concept has no implementation to measure, not because it
was skipped.

---

## 11. Top false alarms — all 9, ranked

There are only **9** flagged clean entries in total, so all of them are listed; there is no
tenth. Ranking rule, stated so it is checkable: severity descending, then raw-flag count
descending, then amount descending, then voucher id. No score is invented.

| # | voucher | dept | vendor / party | amount (paise) | detector | why it fired |
|---|---|---|---|---|---|---|
| 1 | DWP-00037 | DWP | G4S FACILITIES MANAGEMENT (UK) LTD | 59,404,920 | `vendor_switch` (sev 3) | posted to `…CONTACT CENTRES` 2 times; this one goes to `…SOCIAL CARE` |
| 2 | DHSC-00039 | DHSC | NHS ENGLAND CBA033 | 33,188,500 | `vendor_switch` (sev 3) | posted to `Grant in Aid Funding (Cash) - ENDPBs` 2 times; this one goes to `Research & development` |
| 3 | DHSC-00037 | DHSC | BOLTON NHS FOUNDATION TRUST | 830,000,000 | `magnitude` (sev 2) | `Additions NCB PDC` max is 21,300,000 across 10 entries; this is >300% of it |
| 4 | DHSC-00029 | DHSC | NORTHUMBRIA HEALTHCARE NHS FT | 740,000,000 | `magnitude` (sev 2) | same account, same 21,300,000 ceiling |
| 5 | DEFRA-00035 | DEFRA | WSP UK LTD | 237,382,815 | `magnitude` (sev 2) | `…RESEARCH & DEVELOPMENT - OTHER OUTSOURCED` max is 5,000,000 across 4 entries |
| 6 | DHSC-00036 | DHSC | TAVISTOCK AND PORTMAN NHS FT | 217,000,000 | `magnitude` (sev 2) | same account, same 21,300,000 ceiling |
| 7 | DHSC-00028 | DHSC | MID CHESHIRE HOSPITALS NHS FT | 187,500,000 | `magnitude` (sev 2) | same account, same 21,300,000 ceiling |
| 8 | DHSC-00035 | DHSC | MAIDSTONE & TUNBRIDGE WELLS NHS TRUST | 174,400,000 | `magnitude` (sev 2) | same account, same 21,300,000 ceiling |
| 9 | DHSC-00027 | DHSC | DORSET COUNTY HOSPITAL NHS FT | 81,800,000 | `magnitude` (sev 2) | same account, same 21,300,000 ceiling |

**The pattern, which is the actionable finding here.** Six of the nine false alarms
(#3, #4, #6, #7, #8, #9) are the *same account* in the *same department*: DHSC
`Additions NCB PDC`, ceiling 21,300,000 paise from 10 history entries. Six different NHS
trusts each post a capital amount two to forty times that ceiling.

This is not six independent false alarms. It is **one wrong ceiling, counted six times**.
`Additions NCB PDC` is Public Dividend Capital — lumpy capital injections, where a 10-entry
history is not a range in any useful sense. `magnitude` is doing exactly what it was
written to do (`accountant/detect/detectors.py:144-177`) and the account is the wrong shape
for it.

That single account is the whole of DHSC's 33.33 overshoot: remove those six and DHSC drops
to 1 of 21 = 4.76, and the aggregate drops from 6.29 to 3 of 143 = 2.10. **I did not do
that** — excluding hard cases is forbidden and it would be a lie. It is stated so the next
person knows where the leverage is, and that the leverage is one account, not a threshold.

---

## 12. Gap table

| metric | current | desired | gap | PASS / NOT_PASSED |
|---|---|---|---|---|
| N1 aggregate (all 7) | **6.29** | <= 10 | 3.71 under | **PASS** |
| N1 held-out | **2.90** | <= 10 | 7.10 under | **PASS** |
| N1 calibration half | **9.46** | <= 10 | **0.54 under** | **PASS (no headroom)** |
| N1 DHSC | **33.33** | <= 10 | **23.33 over — 3.33x** | **NOT_PASSED** |
| N1 DEFRA | 5.26 | <= 10 | 4.74 under | PASS |
| N1 DWP | 3.70 | <= 10 | 6.30 under | PASS |
| N1 MHCLG | 0.00 | <= 10 | 10.00 under | PASS |
| N1 DFT | 0.00 | <= 10 | 10.00 under | PASS |
| N1 HMT | 0.00 | <= 10 | 10.00 under | PASS |
| N1 DBT | n/a | <= 10 | **unmeasurable — 0 entries** | **NOT_PASSED** |
| `magnitude` solo | 4.90 | <= 10 | 5.10 under | PASS |
| `vendor_switch` solo | 1.40 | <= 10 | 8.60 under | PASS |
| `gst_anomaly` solo | 0.00 | <= 10 | never exercised | PASS (vacuous) |
| `first_use` solo | 30.77 | <= 10 | 20.77 over | NOT_PASSED — withdrawn |
| duplicate views | 0 | — | dedup idle | n/a |
| test suite | 1357 passed | all pass | 0 | **PASS** |
| determinism | 2 identical sha256 | identical | 0 | **PASS** |

---

## 13. Determinism proof

Full measurement run twice, JSON artifact hashed both times:

```
$ COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
    <scratchpad>/measure_n1.py
sha256=9f06e70e50869cae004bcf732a3c6ccf661c30daf968cd8fbcaeebb44c5d77b1
bytes=18533

$ COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
    <scratchpad>/measure_n1.py
sha256=9f06e70e50869cae004bcf732a3c6ccf661c30daf968cd8fbcaeebb44c5d77b1
bytes=18533
```

| run | sha256 |
|---|---|
| pass 1 | `9f06e70e50869cae004bcf732a3c6ccf661c30daf968cd8fbcaeebb44c5d77b1` |
| pass 2 | `9f06e70e50869cae004bcf732a3c6ccf661c30daf968cd8fbcaeebb44c5d77b1` |

**Identical.**

Two identical runs in the same environment is a weak test — it would not catch
hash-order dependence, because CPython fixes the hash seed for a process only. So the run
was repeated under five different `PYTHONHASHSEED` values, which is the specific thing
that would expose set/dict iteration leaking into the output:

| `PYTHONHASHSEED` | sha256 |
|---|---|
| 0 | `9f06e70e...4c5d77b1` |
| 1 | `9f06e70e...4c5d77b1` |
| 12345 | `9f06e70e...4c5d77b1` |
| 99991 | `9f06e70e...4c5d77b1` |
| random | `9f06e70e...4c5d77b1` |

All five identical. **No nondeterminism found.** Consistent with the code: `split` sorts
by company name (`calibration.py:228`), `EntryResult.fired` is `tuple(sorted({...}))`
(`harness.py:294`), `_per_type` iterates `sorted(injected)` (`harness.py:340`), flags are
sorted on a total key (`detectors.py:393`), and no `random`, `time` or `uuid` appears
anywhere on the scoring path.

---

## 14. Attacking the conclusion

Three things I went looking for that would make the PASS look worse than it reads.

### 14a. The calibration half has zero headroom

| scope | flagged now | max flagged still passing | **headroom** |
|---|---|---|---|
| aggregate | 9 | 14 | 5 entries |
| held-out | 2 | 6 | 4 entries |
| **calibration half** | **7** | **7** | **0 entries** |

The calibration half sits at 9.46 against a cap of 10 with **exactly zero spare entries**.
One more flagged clean entry there gives 8/74 = 10.81 and the calibration half fails. This
is not an accident — it is what the greedy rule at `calibration.py:243-247` produces: it
takes the most sensitive setting that still fits the budget, so it spends the budget by
construction. Worth knowing that the "PASS" was fitted right up against the line on the
half it was allowed to see.

### 14b. The whole result rests on 9 events

143 clean entries means one flagged entry moves the aggregate by 0.70. The aggregate PASS
is 9 events on 143 rows drawn from 6 departments (7th contributes nothing). The held-out
2.90 is **two entries**. That is a real measurement, and it is the first N1 on real data —
but it is a small-sample measurement, and it should not be quoted as though it had the
weight of a large one.

### 14c. The PASS is bought by withdrawing `first_use`

Same union with `first_use` switched back on at the calibrated settings the other three
ship at:

| scope | with `first_use` | verdict | vs shipping |
|---|---|---|---|
| aggregate | 52 / 143 = **36.36** | NOT_PASSED | 6.29 |
| calibration half | 23 / 74 = **31.08** | NOT_PASSED | 9.46 |
| held-out | 29 / 69 = **42.03** | NOT_PASSED | 2.90 |

The withdrawal is declared, reasoned and reported — `accountant/detect/detectors.py:267-278`,
and every `ScoreReport` carries it in `withdrawn` (`harness.py:522-530`). Nothing is hidden.
But the arithmetic is worth stating plainly: **N1 passes because one of the four detectors
was turned off**, and the frozen `first_use` concern ("this account has never been used
before") is now covered by nothing. That is a coverage gap sitting behind a passing metric.

---

## 14d. The working tree was not clean when this ran

The tree was clean at the start of this task. Partway through, a concurrent agent modified
three files in the same checkout:

```
 M accountant/detect/detectors.py    (+19, additive only)
 M accountant/pipeline.py            (+26 -2)
 M accountant/web/app.py             (+30 -4)
?? tests/test_flag_cap.py
?? docs/TAXONOMY.md
```

That is a live confound: a measurement taken against an edited tree is not a measurement of
the commit. The `detectors.py` change is a new `check_cap(cap)` guard rejecting a negative
flag cap, called at the top of `run`. Every call on the N1 path passes `cap=None`, so
`check_cap` is a no-op there, and neither `pipeline.py` nor `web/app.py` is on the scoring
path at all — `harness.py:46-53` states explicitly that scoring composes checks, detectors,
problems and decision directly rather than through the pipeline, so scoring cannot move when
the pipeline does.

Reasoning is not evidence, so the four numbers were re-measured in an isolated
`git worktree` checked out at `3445992` with `PYTHONPATH` pointed at it, confirmed to be
importing the pristine module (`hasattr(detectors, "check_cap") == False`):

| number | working tree | pristine `3445992` |
|---|---|---|
| aggregate | 9/143 = 6.29 | **9/143 = 6.29** |
| held-out | 2/69 = 2.90 | **2/69 = 2.90** |
| DHSC | 7/21 = 33.33 | **7/21 = 33.33** |
| MHCLG pre-calibration | 8/29 = 27.59 | **8/29 = 27.59** |

Identical. The concurrent edits do not touch N1. The worktree was removed afterwards.

---

## 15. Defects found

**No code defect found on the N1 path.** The formula is round-half-up as specified, the
verdict is decided on exact integers, the denominator cannot be shrunk by silencing a flag,
the numerator runs de-dupe off, unmeasured scopes report NOT_PASSED rather than a vacuous
pass, and the whole path is deterministic under a randomised hash seed.

Four things recorded as observations, not defects, none of which I changed:

1. `scaled_rate` (`harness.py:91-93`) divides by zero if `denominator == 0`. Every one of
   the three call sites guards it first (`harness.py:161-163`, `harness.py:366`,
   `calibration.py:146-147`), so it cannot be reached today. It is an unguarded public
   function one careless call away from a crash.
2. A rate that rounds to exactly `10.00` can print `10.00` and FAIL. Correct direction;
   documented above so it is not "fixed" into a bug.
3. `gst_anomaly`'s 0.00 is unexercised, not quiet — the corpus has no GST column. The code
   already says this at `calibration.py:250-266`; the number should never be quoted as a
   measured pass.
4. DBT contributes 0 entries and reports NOT_PASSED. Correct, and it means one seventh of
   the source set is outside every N1 figure in this document.

---

## 16. Commands run

```
git rev-parse --abbrev-ref HEAD        # closure/flag-cap-and-truth
git rev-parse HEAD                     # 3445992b98295a6658542f5d9211c91ab91480de
git status --porcelain                 # clean

COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m pytest -q -p no:cacheprovider
# 1357 passed in 85.72s

COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m accountant.score
# No module named accountant.score.__main__  -> there is no score CLI

COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  <scratchpad>/measure_n1.py            # x2, plus x5 under varying PYTHONHASHSEED

shasum -a 256 artifacts/detector_evidence.json
```

The measurement script lives in the session scratchpad, not in the repo — it is a
read-only harness over `accountant/score/` and `accountant/ingest/`, and this task's file
ownership permits writing only the two artifacts.

Files written by this task, and no others:

- `artifacts/detector_evidence.md`
- `artifacts/detector_evidence.json`
