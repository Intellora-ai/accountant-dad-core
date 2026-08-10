# PHASE 9 EXIT AUDIT

Read-only audit. Nothing outside this file and its two companions was written.
Working tree `37ec1d8` on `main`, 2026-08-10. Two files were modified by other
agents while this ran (`accountant/reversal.py`, `tests/test_reversal_recovery.py`);
neither is on the Phase 9 path.

Companions: `artifacts/phase9_error_coverage.md` · `artifacts/phase9_data_quality.md`

**Provenance note, added 2026-08-10 during evidence correction.** Every command
in this file names the interpreter, the commit and the input. None of them
records *which* `accountant` package was imported — `accountant.__file__` was
never printed. That is the exact check the invalidated `/tmp` measurement failed
(see "Evidence corrections that must stay corrected", below). The import path
behind these numbers is therefore `UNVERIFIED`: not shown to be wrong, not shown
to be right. Any re-run must print `accountant.__file__` beside the result.

---

## Plain-words summary, before any table

Phase 9 was asked to do five things. Four of them are **reporting** jobs: print a
number, print a table, print PASS or FAIL. A build can finish all four while the
product does not work, because printing "FAIL" counts as printing.

The fifth is the only one that asks a number to actually be **good**.

That fifth one is the one that is not met.

So "4 of 5 exits met" is true and also misleading. The honest sentence is: **every
exit that could be satisfied by building something is satisfied, and the single
exit that could only be satisfied by the product working is not.**

---

## Words used in this file

| word | plain meaning |
|---|---|
| exit | a condition the phase must satisfy before it can be called finished |
| N1 | false alarms per 100 clean entries. Lower is better. Target 10 or less |
| N2 | time spent reviewing flags, as a percentage of the time to read everything. Target 10% or less |
| N3 | of the errors deliberately planted, what percent were caught. Target 90% or more |
| slice | one cut of the data — all of it, half of it, or one department |
| held-out | the half of the departments no threshold was ever tuned on |
| answer key | a list saying which entries are wrong, written by someone who knows |
| detector | a small rule that raises a flag on an entry |

---

## Evidence labels, applied to every row in this file

| label | what it means | what it does NOT mean |
|---|---|---|
| `BUILD_CORRECTNESS` | the code does the thing it was written to do | that the thing is worth doing |
| `SYNTHETIC_EVIDENCE` | measured on a book this project wrote itself | that a real accountant makes this error |
| `PUBLIC_DATA_EVIDENCE` | measured on real published third-party data | that the data resembles the target customer |
| `REAL_COMPANY_EVIDENCE` | measured on a real customer's real books | — none of this exists in this repository |
| `PRODUCT_VALUE_EVIDENCE` | a user got value | — none of this exists in this repository |
| `NOT_MEASURABLE` | the input needed to measure it does not exist here | that it will never exist |

**Repository totals: `REAL_COMPANY_EVIDENCE` 0. `PRODUCT_VALUE_EVIDENCE` 0.**

---

## Where the exits are actually written

Two documents define them, and they **do not agree on how many there are**.

| source | exits stated | note |
|---|---|---|
| `docs/ARCHITECTURE.md` lines 1083-1099, "Phase 9 — the proof track" | **4** | the frozen wording |
| `docs/CONTROL_PLANE.yaml` lines 406-441 | **5** | adds E5 below |
| `docs/LAUNCH_GATES.md` line 112 (`LG-20`) | echoes E5 | calls it a launch gate, not a phase exit |

**Finding A-1 — the fifth exit is not in the frozen architecture.**
`ARCHITECTURE.md` asks only that N1, N2 and N3 be *reported* as PASS or FAIL. It
never says N1 must pass. `CONTROL_PLANE.yaml` adds "N1 is inside its target on
every slice that is reported" as exit criterion 5. That is a **harder** bar than
the frozen plan, added later, and it is the only unmet one.

This is not a reason to drop it. It is a reason the owner must decide, on the
record, whether Phase 9's bar is *reporting* (frozen plan, already met) or
*passing* (control plane, not met). `docs/LAUNCH_GATES.md` already logs this as
open owner decision **`D-22`** — "which slice is the gate".

The frozen plan's children #1, #4, #5, #7 and #8 are package specifications in
`ARCHITECTURE.md` §4.9-§4.12, not separate exits. They map like this:

| child | package | `ARCHITECTURE.md` | feeds exit |
|---|---|---|---|
| #1 | `accountant/generate/` | §4.9 line 456 | E1 |
| #4 | `accountant/score/` | §4.10 line 486 | E2 |
| #7 | `accountant/taxonomy/` | §4.11 line 515 | E3 |
| #5, #8 | `accountant/ingest/` | §4.12 line 540 | E4 |

All five packages exist. Package existence is `BUILD_CORRECTNESS` and nothing more.

---

## The five exits, one row each

### E1 — reproducibility

| field | value |
|---|---|
| **exact requirement, quoted** | "The same seed produces byte-identical output." (`CONTROL_PLANE.yaml:410`) · "same seed → byte-identical output" (`ARCHITECTURE.md:1095`) |
| **reported status** | met |
| **what decides it** | `tests/test_generate.py::test_the_same_seed_produces_byte_identical_vouchers`, `::test_the_same_seed_produces_a_byte_identical_answer_key`, `::test_the_same_seed_produces_byte_identical_files_on_disk`, `::test_the_bytes_are_locked_to_a_known_digest`, `::test_a_different_seed_produces_different_bytes`; `tests/test_ingest.py::test_the_same_bytes_produce_the_same_vouchers` |
| **evidence artifact** | `artifacts/detector_evidence.md` §12 — "determinism · 2 identical sha256 · PASS" |
| **label** | `BUILD_CORRECTNESS` |
| **limitation** | Determinism is a property of the generator, not of the product. A generator that reproducibly produces an irrelevant book is still reproducible. The criterion is tested from both sides (a *different* seed must differ), which is good practice and still proves only that the code is a pure function of its inputs. |
| **gap** | none against the requirement as written |
| **FINAL STATUS** | **COMPLETE** |

### E2 — N1, N2 and N3 are each reported PASS or FAIL

| field | value |
|---|---|
| **exact requirement, quoted** | "N1, N2 and N3 are each reported as an explicit PASS or FAIL." (`CONTROL_PLANE.yaml:414`) · "N1 ≤ 10, N2 ≤ 10%, N3 ≥ 90% each reported **PASS or FAIL**" (`ARCHITECTURE.md:1095-1096`) |
| **reported status** | met |
| **what decides it** | `accountant/score/harness.py:360-442` — `_n1`, `_n2`, `_n3` each return a `MetricResult` carrying `Status.MET` or `Status.MISSED` on every path, including the no-evidence path |
| **evidence artifact** | `artifacts/detector_evidence.md` §0 and §12 |
| **label** | `BUILD_CORRECTNESS` |
| **limitation** | This is a requirement to **print a verdict**, not to earn one. Read against real data the three verdicts are: N1 printed and mixed; **N2 has never produced a value at all**; **N3 has never produced a value on real data at all**. Both absences are correct behaviour — the harness refuses a default for R and D (`ARCHITECTURE.md:502` forbids one) and refuses a vacuous N3 pass with no answer key. The exit is met by a harness that says "not measured" three times. |
| **gap** | N2 real-data value: absent, `NOT_MEASURABLE` (no measured read-second or dismiss-second; `accountant/score/report.py:67` records that the seconds are assumed). N3 real-data value: absent, `NOT_MEASURABLE` (no labelled real ledger; pinned by `tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key`). |
| **FINAL STATUS** | **COMPLETE** as a reporting requirement. The two values behind it are **NOT_MEASURABLE**. |

### E3 — the coverage table

| field | value |
|---|---|
| **exact requirement, quoted** | "The coverage table maps every real error type to a detector or to UNCOVERED, with UNCOVERED reported as a number." (`CONTROL_PLANE.yaml:417`) |
| **reported status** | met |
| **what decides it** | `.venv/bin/python -c "from accountant.taxonomy import coverage as c; print(dict(c.status_counts()), c.uncovered_count())"` · `tests/test_taxonomy.py`, `tests/test_taxonomy_matrix.py` |
| **re-measured here, 2026-08-10** | `COVERED 0, PARTIAL 2, UNCOVERED 10`; `uncovered_count() == 10`; `len(ERROR_TYPE_NAMES) == 12`. **Matches `docs/TAXONOMY.md` exactly.** |
| **evidence artifact** | `docs/TAXONOMY.md`; `artifacts/phase9_error_coverage.md` |
| **label** | `PUBLIC_DATA_EVIDENCE` for the twelve types (each traces to a published audit paragraph with a URL and a retrieval date, refused at load time if either is missing). `BUILD_CORRECTNESS` for the mapping — a mapping is an intention, not an observation. |
| **limitation** | The table records which detector is *aimed* at a type. It records no instance of a detector *hitting* one. `VERIFIED` is 0 and the code cannot be edited into saying otherwise: `tests/test_taxonomy_matrix.py` refuses a `VERIFIED` row that does not name a test function this repository actually contains. |
| **gap** | none against the requirement as written. The requirement asks for the gap to be *counted*, and 10 is counted. |
| **FINAL STATUS** | **COMPLETE** |

### E4 — cross-department accuracy

| field | value |
|---|---|
| **exact requirement, quoted** | "Cross-department accuracy is reported for at least 3 pairs, with the gap as a single number per pair." (`CONTROL_PLANE.yaml:420`) · "cross-department accuracy reported for ≥ 3 pairs, with the gap as a single number per pair" (`ARCHITECTURE.md:1098-1099`) |
| **reported status** | met |
| **what decides it** | `accountant/ingest/crossorg.py`; `tests/test_ingest.py::test_at_least_three_department_pairs_are_measured`, `::test_every_pair_reports_within_cross_and_one_gap_number`, `::test_the_gap_is_within_minus_cross` |
| **re-measured here, 2026-08-10** | 6 departments, **30 ordered pairs**, one `gap_hundredths` per pair. Requirement satisfied — 30 ≥ 3. |
| **label** | `PUBLIC_DATA_EVIDENCE` |
| **limitation** | Three, all in the section below: the recorded numbers do not reproduce (**A-2**), the "history" half is not earlier in time (**A-3**), and 30 pairs contain only 6 independent measurements (**A-4**). |
| **gap** | The requirement is a *reporting* requirement and is met. **The recorded evidence line is wrong.** |
| **FINAL STATUS** | **COMPLETE** as a reporting requirement, with a **documentation defect** that must be corrected before the number is quoted again. See A-2. |

### E5 — N1 inside its target on every reported slice

| field | value |
|---|---|
| **exact requirement, quoted** | "N1 is inside its target on every slice that is reported." (`CONTROL_PLANE.yaml:423`) |
| **reported status** | **not met** |
| **what decides it** | `COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_n1.py::test_one_department_is_still_above_the_target_and_is_not_hidden` |
| **evidence artifact** | `artifacts/detector_evidence.md` §7 and §12; `artifacts/detector_evidence.json` key `worst_department` |
| **the four numbers** | **27.59** historical (MHCLG-only, pre-calibration, 8 of 29) · **6.29** aggregate (9 of 143) **PASS** · **2.90** held-out (2 of 69) **PASS** · **33.33** DHSC (7 of 21) **NOT_PASSED** |
| **label** | `PUBLIC_DATA_EVIDENCE` |
| **limitation** | DHSC is **3.33 times** over target. It is also in the **calibration** half — the procedure had it in front of it the whole time and still could not bring it inside the target. And a seventh department, DBT, reports `NOT_PASSED (unmeasured)` on zero entries, so "every slice" is failed twice, for two different reasons. |
| **gap** | 23.33 percentage points on DHSC. Six of DHSC's seven false alarms are **one account** — `Additions NCB PDC`, Public Dividend Capital — where `magnitude` bounds a lumpy capital injection by a ceiling taken from ten history entries (`artifacts/detector_evidence.md` §11). |
| **FINAL STATUS** | **NOT_PASSED.** Unblocking it also needs **OWNER_DECISION_REQUIRED** — `D-22`, which slice is the gate. |

---

## Exit scoreboard

| exit | requirement type | final status | strongest label available |
|---|---|---|---|
| E1 reproducibility | reporting / build | **COMPLETE** | `BUILD_CORRECTNESS` |
| E2 N1·N2·N3 printed PASS or FAIL | reporting | **COMPLETE** (values: `NOT_MEASURABLE` ×2) | `BUILD_CORRECTNESS` |
| E3 coverage table | reporting | **COMPLETE** | `PUBLIC_DATA_EVIDENCE` |
| E4 cross-department pairs | reporting | **COMPLETE** + documentation defect | `PUBLIC_DATA_EVIDENCE` |
| E5 N1 inside target everywhere | **quality** | **NOT_PASSED** | `PUBLIC_DATA_EVIDENCE` |

**4 of 5 met. All four met ones are reporting requirements. The one quality
requirement is the one that failed.**

**Phase 9 overall: `PARTIALLY_VERIFIED` is correct and should stay** — meaning
the phase must not be closed by lowering E5. `PARTIALLY_VERIFIED` is not a
permitted label from 2026-08-10; the verdict in the current set is immediately
below, and it says the same thing.

### Summary verdict in the current label set

From 2026-08-10 the only permitted values are:

    PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED

The rows above keep the words they were written with — this is not a rewrite of
history. **This table is the verdict to quote.**

| exit | current label | why this label |
|---|---|---|
| E1 reproducibility | **PASS** | the reporting requirement as written is satisfied |
| E2 N1·N2·N3 each printed PASS or FAIL | **PASS** as a reporting requirement | and the two values behind it are **NOT_MEASURED**: N2 has no measured read-second or dismiss-second, N3 has no real-data answer key |
| E3 coverage table | **PASS** | 12 types, `uncovered_count() == 10`, counted |
| E4 cross-department pairs | **PASS** as a reporting requirement | 30 ≥ 3. The recorded evidence line is still wrong — A-2 is open |
| E5 N1 inside its target on every reported slice | **FAIL** | measured: DHSC 33.33 against a target of ≤ 10. The DBT slice is a second, separate **FAIL** — source unusable: narration empty in all 28 committed rows, and the loader refuses the department with `DBT has 0 history and 0 entries`. Two slices fail, for two different reasons |
| **Phase 9 overall** | **FAIL** | one exit was measured and missed. Closing it honestly is **BLOCKED** on two owner items: `D-22` (which slice is the gate) and one real book with an accountant's markup |

Where the older words appear above, they read: `PARTIALLY_VERIFIED` → **FAIL**;
`NOT_PASSED` → **FAIL**; `NOT_PASSED (unmeasured)`, used only of DBT, →
**FAIL**; `OWNER_DECISION_REQUIRED` → **BLOCKED**.

**`NOT_MEASURABLE` splits in two, and the test is whether anything ran.**

| where it is used | current label | why |
|---|---|---|
| **DBT** — `NOT_PASSED (unmeasured)`, zero entries | **FAIL** | the input exists and was read. The loader resolved `Description`, found all 28 committed rows empty, and refused the department: `ValueError: DBT has 0 history and 0 entries` (`accountant/ingest/crossorg.py:73-79`). Something ran and missed a bar. That is a failure, not an absence |
| **N2 real-data value** | **NOT_MEASURED** | no read-second or dismiss-second was ever recorded. There is no input to fail on |
| **N3 real-data value** | **NOT_MEASURED** | no labelled real ledger exists anywhere in this repository. Nobody has looked because there is nothing to look at |

`NOT_MEASURED` is the label that stops an **unrun** thing being scored as a
zero — `question rate` is the case it exists to protect. Spending it on
something that ran and failed weakens it for that case. Every `FAIL` above
therefore carries its reason, because the label alone no longer distinguishes a
detector that was too loud from a source that was unusable.

The companion `artifacts/phase9_data_quality.md` recommendation 4 argues for
separate *words* rather than a shared word with a reason attached. It is left
standing and unwithdrawn; the disagreement is recorded there in full.

---

## Defects found. All written up, none fixed — this audit is read-only.

### A-2 · Two headline cross-organisation numbers do not reproduce · **HIGH**

The repository states, in **ten** places including three shipping source files,
that within-department account prediction reaches **53.08%** and that
cross-department accuracy is **0.00% on 29 of 30** pairs.

Re-measured on the committed fixtures at working tree `37ec1d8`:

```
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -c "
from accountant.ingest import sources as S, spend as sp, crossorg as x
r = x.compare(sp.load_all(S.COMPARABLE_SOURCES))
print('pairs', len(r.pairs))
print('best within', max(p.within.percent_hundredths for p in r.pairs)/100)
print('best cross ', r.best_cross_hundredths/100)
print('non-zero cross pairs', [p for p in r.pairs if p.cross.percent_hundredths])"
```

| claim recorded | recorded value | measured 2026-08-10 | verdict |
|---|---|---|---|
| pairs | 30 | **30** | reproduces |
| within-department, best | **53.08%** | **86.21%** (MHCLG) | **DOES NOT REPRODUCE** |
| cross-department 0.00% on | **29 of 30** | **30 of 30** — every pair is 0.00% | **DOES NOT REPRODUCE** |

Per-department within-accuracy, measured: MHCLG 86.21 · DWP 62.96 · DFT 50.00 ·
DHSC 33.33 · DEFRA 5.26 · HMT 4.35. Aggregate 63 of 143 = 44.06%. **None of
these is 53.08.** `53.08% == 69/130`, and no current configuration produces a
denominator of 130.

Where the stale numbers live:

| file | line |
|---|---|
| `docs/CONTROL_PLANE.yaml` | 418, 434 |
| `docs/BOTTLENECKS.md` | 278 |
| `docs/PROJECT_STATE.md` | 353, 1034, 1481 |
| `accountant/memory/__init__.py` | 8 |
| `accountant/memory/identity.py` | 8 |
| `accountant/memory/company.py` | 26 |
| `accountant/pipeline.py` | 167 |
| `tests/test_memory.py` | 6 |
| `tests/test_pipeline_isolation.py` | 28 |

**Re-counted 2026-08-10 during evidence correction, and the count holds.**
`git grep -n "53\.08" 37ec1d8 -- '*.md' '*.py' '*.yaml'` (excluding `artifacts/`)
returns **10 lines in 8 files**, of which **3 are shipping source files** —
`accountant/memory/__init__.py`, `accountant/memory/identity.py`,
`accountant/pipeline.py`. The prose above is exact. The table has **12** rows
because it also lists the two sites of the *other* stale claim, `0.00% on 29 of
30`: `docs/CONTROL_PLANE.yaml:418` and `accountant/memory/company.py:26` carry
that sentence and not the 53.08 figure. Stated here so a reader who counts the
table does not conclude the "ten" is wrong.

**Which way it cuts.** The corrected numbers make the design conclusion
*stronger*, not weaker. Cross-organisation transfer is 0.00% on **30** of 30
pairs, not 29 of 30 — there is no surviving exception. The invariant in
`ARCHITECTURE.md` §4.3 ("mappings do not transfer, so memory is company-local
and every customer is a permanent cold start") is correct and now has no
counter-example at all. Only the *within* figure moves, and it moves up.

**None of these lines is inside this audit's ownership. Not edited. Reported.**

### A-3 · The "history" half is not earlier in time · **HIGH**

`accountant/ingest/spend.py:524-531`, `split_point`, carries this docstring:

> "The earlier half is the history an index learns from; the later half is what
> gets predicted. Published order, no shuffling…"

**Published order is not date order.** Measured directly from the loader:

| dept | history rows | scored rows | last date in "history" | first date in "entries" | scored entries dated **before** history ends |
|---|---|---|---|---|---|
| MHCLG | 28 | 29 | 2025-11-03 | 2025-11-03 | 0 of 29 |
| DHSC | 20 | 21 | 2025-11-03 | 2025-11-03 | 0 of 21 |
| DFT | 23 | 24 | 2025-11-28 | 2025-11-06 | **24 of 24** |
| DWP | 27 | 27 | 2025-11-28 | 2025-11-03 | **26 of 27** |
| DEFRA | 19 | 19 | 2025-11-28 | 2025-11-03 | **16 of 19** |
| HMT | 23 | 23 | 2025-11-27 | 2025-11-06 | **23 of 23** |

For four of six departments the split does not separate past from future. The
detectors are shown entries dated *after* the ones they are then scored on.

**Why this matters, in plain words.** A real user has only the past. This
measurement gives the detectors a random half of the same month, including days
that had not happened yet. Detectors that fire on "I have never seen this
supplier before" see fewer surprises than they would in production, so the false
alarm rate is measured on an **easier** problem than the real one.

**Direction of the bias: N1 is optimistic. Magnitude: unquantified.**
The effect is bounded — all seven files are a single month, November 2025, so
nothing leaks across a period boundary. It is not bounded to zero.

Not quantified here on purpose: another agent owns the scoring harness and is
changing it now, and re-running it would collide. The experiment that settles it
is one line — sort each department's vouchers by date before `split_point`, and
re-measure N1 aggregate, held-out and DHSC. **That belongs to the harness owner.**

The same `split_point` is used by `crossorg.split` (`crossorg.py:82-91`), so the
within-department figures in A-2 carry the same optimism. The *gap* is less
affected, because within and cross are both measured on leaked splits.

### A-4 · 30 pairs are 6 measurements · **MEDIUM**

`crossorg.compare` (`crossorg.py:188-203`) builds one index per department and
computes `within` **once**, then copies that same `within` object into all five
pairs that department indexes. The 30 rows contain **6 distinct `within` values
and 6 distinct indexes.** "30 department pairs" reads as 30 independent results
and is 6 index builds measured 5 ways each. The claim is still sound — 0.00% on
all 30 is 0.00% on all 6 indexes against all 5 other departments — but the
number 30 overstates how much independent evidence there is.

### A-5 · "16,011 rows" is the size of the files, not the size of the measurement · **HIGH**

`docs/BOTTLENECKS.md:278`, `docs/PROJECT_STATE.md:353` and `:1034` all state
**"16,011 real UK central-government rows, 30 department pairs."**

16,011 is `sum(s.published_rows for s in ALL_SOURCES)` — the row counts of the
seven **published files**. It is not what was measured. What was measured:

**`16,011` is itself `UNVERIFIED`, added 2026-08-10.** Its provenance is the
`published_rows` constants in `accountant/ingest/sources.py`, not a count of the
published files. No row of any published file was counted in this repository and
no network call was made — the companion report records the same gap for DBT's
`199`. So 16,011 is an asserted figure standing on seven asserted figures. It is
labelled, not deleted: it is the number the three documents below actually quote.

| stage | rows | share of 16,011 |
|---|---|---|
| published, across the seven real files | 16,011 | 100% |
| **committed as fixtures** | **311** | 1.94% |
| loaded (28 DBT rows rejected) | 283 | 1.77% |
| used as history | 140 | 0.87% |
| **actually scored** | **143** | **0.89%** |

Every N1 figure in this repository — 6.29, 2.90, 33.33 — has a denominator of at
most **143**. Quoting 16,011 alongside them invites the reader to think the
sample is 56 times larger than it is. On 143 entries, **one** entry changing side
moves the aggregate rate by 0.70 points.

**Not edited — outside this audit's ownership.**

### A-6 · `scaled_rate` divides by zero if called with an empty denominator · **LOW**

`accountant/score/harness.py:91-93`. All three current call sites guard it first
(`harness.py:161-163`, `harness.py:366`, `calibration.py:146-147`), so it cannot
be reached today. Already recorded by the previous agent in
`artifacts/detector_evidence.md` §12 observation 1. Repeated here so it is not
lost, not because it is new.

---

## The inversion question, applied to every metric

> **"How could this report look healthy while the product is useless?"**

Nine ways. Each is a real mechanism, not a worry.

| # | mechanism | is it happening? | evidence | can this audit test it? |
|---|---|---|---|---|
| 1 | **The aggregate hides one disastrous department.** 6.29 PASS is an average over seven; DHSC alone is 33.33 | **YES** | `detector_evidence.md` §7 | tested — the repo does not hide it, `tests/test_n1.py` names the failure. Good practice |
| 2 | **Synthetic truth is defined by detector names.** `generate/inject.py` corrupts into exactly `vendor_switch`, `first_use`, `magnitude`, `gst_anomaly` — the four detector names and no others | **YES, by construction** | `ARCHITECTURE.md:459` | tested by reading. N3 on synthetic data is a spelling test: the injector writes the answer key the detectors were written to read. The repo says so in every report it prints (`ARCHITECTURE.md:509-511`) |
| 3 | **A midpoint split can leak history.** The "earlier" half is not earlier | **YES — 4 of 6 departments** | A-3 above, measured | detected and measured here; the effect on N1 is **unquantified**, and belongs to the harness owner |
| 4 | **Flags can be double-counted.** | **NO at entry level, YES at cause level** | `detector_evidence.md` §3, §11 | N1 uses the union, so two detectors on one entry count once. But six of nine aggregate false alarms are **one wrong ceiling on one account** counted six times. Nine alarms is not nine problems; it is closer to four |
| 5 | **A data source with no true positives cannot measure catch rate.** No real ledger here carries a labelled error | **YES** | pinned by `tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key` | N3 on real data is `NOT_MEASURABLE`, and the repo pins the absence rather than papering over it |
| 6 | **Empty narration silently removes a whole department.** DBT | **YES, but not silently** | `artifacts/phase9_data_quality.md` | DBT is counted, named and reported `NOT_PASSED (unmeasured)`. The loader is right. The *consequence* is under-stated: one seventh of the source set is outside every N1 number |
| 7 | **A tiny denominator makes a rate look stable.** 143 scored entries quoted next to "16,011 rows" | **YES** | A-5 above | one entry moves the aggregate 0.70 points |
| 8 | **A detector that never fires reports a perfect score.** `gst_anomaly` measures 0.00 false alarms | **YES** | `detector_evidence.md` §12 observation 3 | the UK corpus has no GST column, so `gst_anomaly` was never exercised. 0.00 is a vacuous pass and must never be quoted as a measured one |
| 9 | **The production path is not the measured path.** N1 is measured on `ACTIVE_DETECTORS` (3 detectors); `pipeline.evaluate` and `pipeline.run` default to `SLICE_4_DETECTORS` (**1** detector) | **YES** | `docs/TAXONOMY.md`, "What is actually wired into the production path" | **No N1 figure in this repository describes what a user would actually run.** The shipped default is one detector; every headline number is for three |

**Number 9 is the one with no mitigation written anywhere.** All four preserved
numbers — 27.59, 6.29, 2.90, 33.33 — describe a detector set that is not the
production default.

---

## Would-I-be-wrong check

Before concluding, the disconfirming evidence was looked for specifically.

| if this audit is wrong, I would expect to see | looked? | found |
|---|---|---|
| a Phase 9 exit somewhere else that E1-E4 fail | yes — `ARCHITECTURE.md`, `CONTROL_PLANE.yaml`, `LAUNCH_GATES.md`, `PROJECT_STATE.md`, `artifacts/phase_truth_table.md` | no. The four are reporting requirements in every source |
| a real-company or product-value measurement hiding in `artifacts/` | yes — all 13 artifacts scanned | none. Zero rows of either label exist |
| a test proving a detector fires on a labelled real error | yes — `coverage.status_counts()` and `tests/test_taxonomy_matrix.py` | none. `VERIFIED` is 0 and the matrix test refuses a `VERIFIED` row naming a non-existent test |
| the 53.08% figure reproducing under some other call | yes — best, aggregate, mean, per-department, 5- and 6-department subsets | none produce 53.08. `69/130` fits the arithmetic; no configuration produces 130 |
| DBT's rejection being a loader bug | yes — every column counted directly from the CSV | no. The narration column resolves correctly and is empty in the file. See the companion report |

The one thing that **could** overturn E5's status is `D-22`: if the owner rules
that the aggregate is the gate, E5 is met at 6.29 and Phase 9 closes. That is a
decision, not a measurement, and it is the owner's.

---

## Evidence corrections that must stay corrected

Added 2026-08-10. Each of these has been wrong once in this project's records.
**None of them is restated as fact anywhere in this file or its two companions**
— that was checked by search, not by memory. They are recorded here so the
correction survives in the repository rather than only in a working handoff, and
so nobody re-derives the bad number and thinks it is new.

**1 · The earlier D-05 cross-organisation zero-cost measurement.**

    INVALIDATED — both measurements imported unchanged main code from /tmp

Both sides of that comparison ran from `/tmp`, so `sys.path[0]` was `/tmp` and
the editable install resolved `accountant` to the main checkout. Both sides
measured the same unchanged code, so the comparison could only ever come out at
zero. **It does not prove zero cost.** The figure is not restated here, it is
**not** replaced by `0`, and it is **not** replaced by an estimate — an
invalidated measurement is struck with its reason, and a struck number is
evidence in a way a vanished one is not. Any future cost measurement must print
the resolved `accountant.__file__` and show it inside the intended worktree
before its result is recorded.

**2 · The question rate.**

    question rate = NOT_MEASURED

Never `0`. **Zero is never inferred from an absence** — "no questions appeared"
is not a measurement of how often questions appear. The fixture that would
measure it (the same supplier represented two ways) does not exist yet.

**3 · The wrong-leg posting claim.**

    NOT REPRODUCED — a 399-sequence sweep could not reproduce it

The claim that the unvalidated `problem` field posts a wrong-leg voucher is
**not established evidence** and must not be written down as one. It is not
disproved either; it is unreproduced, which is a different and weaker thing.

---

## `LICENSED_REALTALLY = BLOCKED` until B-01 is verified

Recorded here, in the audit that owns this repository's evidence classes, so the
fact survives independently of any handoff file.

    LICENSED_REALTALLY = BLOCKED until B-01 is verified.

**B-01 is a manual TallyPrime GUI action and only the operator can perform it:**
create the company `Demo Co` with the ledgers `Purchases`, `Sundry Expenses`,
`Cash` and `Sharma Traders`.

The XML gateway cannot create a company. Its refusal is verbatim:

    <RESPONSE>Unknown Request, cannot be processed</RESPONSE>

**The project must never be labelled `LICENSED_REALTALLY` because the XML
gateway is reachable, or because a simulated company exists.** Gateway
reachability is a fact about a socket. A simulated company is a fixture. Neither
is a licensed real TallyPrime, and neither substitutes for B-01. This sits
alongside the two repository totals above — `REAL_COMPANY_EVIDENCE` 0 and
`PRODUCT_VALUE_EVIDENCE` 0 — for the same reason: the evidence class is named by
what was actually observed, never by what was reachable.

---

## What the bottleneck actually is

Not DHSC. Not DBT. Not the 53.08 correction.

**The bottleneck is that this repository holds no book where somebody who knows
the answer has written down which entries are wrong.**

Everything downstream is stuck behind that one missing thing:

- N3 on real data — `NOT_MEASURABLE`, no answer key
- all 12 coverage rows — capped at `PARTIAL`, no labelled instance
- `REAL_COMPANY_EVIDENCE` — 0
- `PRODUCT_VALUE_EVIDENCE` — 0

No amount of building moves any of these. They move when one accountant marks up
one real book. That is an owner action, and it is worth more than every
engineering task currently open on this phase.

**The second bottleneck is the owner, on `D-22`** — a decision only the owner can
make, currently sitting behind a wall of detector-tuning work that cannot resolve
it. Tuning `magnitude` until DHSC drops below 10 would satisfy E5 without
answering whether the product works, and `ARCHITECTURE.md:502` already forbids
"tuning a threshold so a metric passes".

---

## Recommendation

**Phase 9 has done its job.** It was built to invalidate the product if the
evidence said so, and it produced exactly that kind of evidence: mappings do not
transfer between organisations (0.00% on 30 of 30 pairs), the production path
runs one detector, that detector is aimed at two error types, and neither has
ever been observed firing on a real instance.

That is a **successful proof track**. It is not a reason to stop; it is a reason
to stop guessing.

Phase 9 should stay `PARTIALLY_VERIFIED` — **FAIL** in the current label set,
one exit measured and missed — and **must not be closed by lowering E5**. Three
things unblock it, in order of value:

| # | action | owner | unblocks |
|---|---|---|---|
| 1 | one real book with one accountant's markup of which entries are wrong | owner | N3, all 12 coverage rows, the first `REAL_COMPANY_EVIDENCE` in the repository |
| 2 | rule on `D-22` — is the gate the aggregate, the held-out half, or the worst department? | owner | E5 either closes or stays open honestly |
| 3 | sort by date before `split_point`, re-measure N1 | harness owner | A-3; tells us whether 6.29 and 2.90 survive contact with a real timeline |

Correcting A-2 and A-5 is documentation work and changes no conclusion — the
design invariant they support gets stronger, not weaker.
