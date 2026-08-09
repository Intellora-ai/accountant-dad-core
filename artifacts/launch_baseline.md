# LAUNCH BASELINE — every number, where it is now, and where it has to be

Generated 2026-08-10 from `docs/CONTROL_PLANE.yaml` at commit
`53dd60d6c9b0dd9bfb2a1564398c3510cf93a7b8`, plus an uncommitted working tree.

**The machine-readable copy is [`launch_baseline.json`](./launch_baseline.json).**
It is generated, never typed, and its keys are sorted at every level so the file
hashes stably — two runs over an unchanged control plane produce byte-identical
output, so any diff is a real change rather than dictionary ordering.

**30 metrics.**

| status | count |
|---|---|
| `PASSED` | 15 |
| `NOT_PASSED` | 10 |
| `NOT_STARTED` | 5 |

---

## How to read the gap column

| value | meaning |
|---|---|
| a number | how far from target, in the metric's own unit |
| `0` | inside target |
| `NOT MEASURED` | nobody has ever measured it. **A null is never a pass.** |
| `NOT BUILT` | the thing the metric describes does not exist yet |

Two conventions worth knowing:

- **`reproduction_status: PENDING`** on the four false-alarm figures. A detector-
  measurement agent is re-measuring them independently and its report is not in
  yet. The numbers below are the owner's recorded values, and an artifact in the
  working tree agrees with all four — but **"an agent has not finished checking"
  is not the same as "reproduced"**, and it is not written down as if it were.
- **`owner_or_environment_dependency`** says whether a metric is waiting on a
  person or on the world. It lives here rather than in the control plane's
  `depends_on` field, because that field means "waits on another metric" and is
  enforced that way by `scripts/validate_project_truth.py`. Two agents were given
  different schemas for one field; this is where the information went. Recorded
  as row 25 of `document_contradictions.md`.

---

## The false-alarm rate — the number that decides launch

| id | current | desired | gap | unit | status | waits on |
|---|---|---|---|---|---|---|
| `N1_AGGREGATE_CURRENT` | 6.29 | ≤ 10 | **0** | false alarms per 100 clean entries | `PASSED` | — |
| `N1_HELD_OUT_CURRENT` | 2.90 | ≤ 10 | **0** | same | `PASSED` | — |
| `N1_WORST_DEPARTMENT_CURRENT` | 33.33 | ≤ 10 | **23.33** | same | `NOT_PASSED` | OWNER |
| `N1_OLD_BEFORE` *(historical)* | 27.59 | ≤ 10 | 17.59 | same | `NOT_PASSED` | — |

**Formula, and it is the union not the sum.** Clean entries carrying at least one
flag, divided by clean entries, times 100. Two detectors firing on one entry are
**one** false alarm, because the target constrains entries a person has to look
at, not flags.

**Commands:**

```bash
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_n1.py::test_n1_over_every_committed_department_is_within_the_target
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_n1.py::test_one_department_is_still_above_the_target_and_is_not_hidden
```

**Evidence:** `artifacts/detector_evidence.json`. The companion `.md` is not
written yet.

**27.59 is history.** It was the first false-alarm rate ever measured on real
data, taken on one department with the pre-calibration detectors. It is kept
because an improvement is only auditable if the starting point survives. It is
**not** the current number, and several documents were quoting it as if it were.

**Two things the table does not show, and both matter:**

- The calibration half has **zero headroom**. One more false alarm flips it.
- One department (DBT) has **zero clean entries**, so it reports "not measured",
  which is not a pass either.

**The open question is `D-22`:** the aggregate says ship, the worst book says do
not, and a customer experiences their own book rather than an aggregate.

---

## The two frozen numbers nobody has been able to measure

| id | current | desired | gap | unit | status | waits on |
|---|---|---|---|---|---|---|
| `N2_REVIEW_TIME_FRACTION` | **null** | ≤ 10 | `NOT MEASURED` | percent of read-everything time | `NOT_PASSED` | ENVIRONMENT |
| `N3_CATCH_RATE` | **null** | ≥ 90 | `NOT MEASURED` | percent of injected errors caught, per type | `NOT_PASSED` | ENVIRONMENT |

These are written down as nulls rather than left out. **A frozen acceptance
criterion that is missing from the baseline is exactly the gap this file exists
to close.**

`N2` depends on an assumed dismissal time — `accountant/score/report.py` line 67
says so in the code — and the number moves directly with an assumption nobody has
measured.

`N3` cannot be measured on real data at all, because no real ledger this project
holds carries a labelled error. That absence is pinned by a test rather than left
as prose:

```bash
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key
```

Both are blocked by `B-08`, and `B-08` may never clear.

---

## What the detectors actually cover

| id | current | desired | gap | status | waits on |
|---|---|---|---|---|---|
| `PUBLISHED_ERROR_TYPES` | 12 | == 12 | 0 | `PASSED` | — |
| `VERIFIED_COVERAGE` | **0** | ≥ 12 | **12** | `NOT_PASSED` | OWNER |
| `PARTIAL_COVERAGE` | 2 | ≤ 2 | 0 | `PASSED` | — |
| `HISTORY_ONLY_REACHABLE_CEILING` | 4 | == 4 | 0 | `PASSED` | — |

```bash
.venv/bin/python -c "from accountant.taxonomy import coverage as c; print(dict(c.status_counts()))"
# -> {COVERED: 0, PARTIAL: 2, UNCOVERED: 10}
```

**Read the last row before the second one.** The ceiling is 4. All four detectors
fire on a **change** from the company's own history, and a standing wrong
practice — the same wrong head, entry after entry, for years — changes nothing.
So **eight of the twelve published error types are out of reach of this design no
matter how many detectors get written.** That is a design fact, not a backlog
item.

`VERIFIED_COVERAGE`'s target of 12 is **derived, not owner-set** — it is the full
published set, used so the gap reads honestly as "0 of 12". There is no owner
target for this metric, which is why it waits on the owner.

The full matrix is [`docs/TAXONOMY.md`](../docs/TAXONOMY.md), pinned by
`tests/test_taxonomy_matrix.py`. It is not copied here.

---

## How many detectors there are — five different numbers

The documents said "four detectors" in seven places. That one phrase was standing
in for five separate counts, and only one of them is four.

| id | current | desired | gap | status | waits on |
|---|---|---|---|---|---|
| `DETECTORS_IMPLEMENTED` | 4 | == 4 | 0 | `PASSED` | — |
| `DETECTORS_ACTIVE` | 3 | == 3 | 0 | `PASSED` | — |
| `DETECTORS_ON_THE_PRODUCTION_PATH` | **1** | ≥ 3 | **2** | `NOT_PASSED` | OWNER |
| `DETECTORS_MAPPED_TO_A_PUBLISHED_ERROR_TYPE` | **1** | ≥ 4 | **3** | `NOT_PASSED` | OWNER |
| `DETECTORS_VERIFIED_ON_REAL_DATA` | **0** | ≥ 1 | **1** | `NOT_PASSED` | OWNER |

```bash
.venv/bin/python -c "from accountant.detect import detectors as d; \
print('implemented', [d.name_of(x) for x in d.ALL_DETECTORS]); \
print('active     ', [d.name_of(x) for x in d.ACTIVE_DETECTORS]); \
print('production ', [d.name_of(x) for x in d.SLICE_4_DETECTORS])"
```

**`first_use` was withdrawn**, and the reason is recorded verbatim in
`accountant/detect/detectors.py`: measured on real published ledgers it fires on
roughly **three clean entries in ten**, and it has no threshold to turn. It is
kept, tested and importable — it just does not run. Turning it back on takes the
aggregate false-alarm rate from 6.29 to **36.36**.

**One detector runs in production.** `accountant/pipeline.py:236` defaults to
`SLICE_4_DETECTORS`, which is `(vendor_switch,)`. Widening it to the three that
survived calibration is Phase 8 work, and Phase 8 has not started.

**Only `vendor_switch` maps to any published error type.** The other three map to
nothing in the published record.

---

## Numbers the owner fixed

| id | current | desired | gap | status |
|---|---|---|---|---|
| `FLAG_CAP` | 3 | == 3 | 0 | `PASSED` |
| `MAX_QUESTIONS_PER_ENTRY` | 5 | == 5 | 0 | `PASSED` |
| `PHASE_5_BATCH_N` | 10 | == 10 | 0 | `PASSED` |
| `PHASE_5B_LIFECYCLES` | 30 | ≥ 30 | 0 | `PASSED` |

`FLAG_CAP` landed in commit `a19a100` on 2026-08-10. It is display only: every
concern still survives in `Draft.suppressed_flags` and the screen says how many
it hid. Before that commit the web app never passed a cap at all, so the
overflow count was permanently zero in production — a parameter no caller
supplied.

`PHASE_5B_LIFECYCLES` is 3 runs × 10 vouchers, and the requirement is **3 of 3**,
not 2 of 3. Measured against FakeTally and the simulator only, which is by design
— a failure is never manufactured in real statutory books.

---

## Engineering health

| id | current | desired | gap | status |
|---|---|---|---|---|
| `CI_GATE_COUNT` | 20 | ≥ 20 | 0 | `PASSED` |
| `CHANGED_LINE_COVERAGE` | **null** | ≥ 90 | `NOT MEASURED` | `NOT_PASSED` |
| `FULL_SUITE_COVERAGE` | 95 | ≥ 90 | 0 | `PASSED` |
| `MUTATION_SCORE` | 99.43 | ≥ 90 | 0 | `PASSED` |
| `TEST_FAILURES` | 0 | == 0 | 0 | `PASSED` |

**Two of these are carried forward, not observed.** Coverage was 95% at commit
`4cc290f` and the mutation score was 1394 of 1402 terminal mutants at the
2026-08-09 work. **Neither has been re-measured at the baseline commit**, and the
`commit` field in the JSON says so for each. A carried-forward number is labelled
as carried forward rather than quietly presented as current.

**The suite is green.** 1764 passed, 6 xfailed, 0 failed on the final run of
2026-08-10, after every agent's work had landed. An earlier run the same day
reported 11 failures — ten were other agents' work in flight in test files
created that morning, and the eleventh was `tests/test_project_truth.py`, the
validator that checks the control plane against the documents. All eleven are
now green, and that validator reports **30 checks, 30 passed**.

Mutation testing needs `COVERAGE_CORE=pytrace`. On the default `sysmon` core the
test-to-line mapping is silently incomplete and the score under-reports badly.

---

## The real-Tally gap

| id | current | desired | gap | status | waits on |
|---|---|---|---|---|---|
| `REALTALLY_CONTRACT_TESTS_PASSING` | **0** | ≥ 19 | **19** | `NOT_PASSED` | ENVIRONMENT |

**Zero contract tests have ever run against a real Tally.** That is the whole
number and there is nothing behind it.

**The denominator is disputed and marked `PENDING_COUNT`.** Every document said
15. An AST count on 2026-08-10 says 19 of the file's 24 test functions take the
`client` fixture. A RealTally preparation agent is counting authoritatively and
**its number, not this one, is the final answer.**

```bash
.venv/bin/python -c "import ast,pathlib; t=ast.parse(pathlib.Path('tests/test_tally_contract.py').read_text()); \
print(sum(1 for n in ast.walk(t) if isinstance(n,ast.FunctionDef) \
and n.name.startswith('test_') and 'client' in [a.arg for a in n.args.args]))"
```

Blocked by `B-01` (no test company) and `B-02` (no licence).

---

## Cloud launch caps — recorded, enforced by nothing

| id | current | desired | gap | status | waits on |
|---|---|---|---|---|---|
| `CLOUD_MAX_CUSTOMERS` | 0 | ≤ 10 | `NOT BUILT` | `NOT_STARTED` | OWNER |
| `CLOUD_MAX_COMPANIES_PER_CUSTOMER` | 0 | ≤ 1 | `NOT BUILT` | `NOT_STARTED` | OWNER |
| `CLOUD_MAX_CONNECTORS_PER_CUSTOMER` | 0 | ≤ 2 | `NOT BUILT` | `NOT_STARTED` | OWNER |
| `CLOUD_MAX_OPS_PER_CUSTOMER_PER_DAY` | 0 | ≤ 100 | `NOT BUILT` | `NOT_STARTED` | OWNER |
| `CLOUD_MAX_CONCURRENT_USERS_PER_CUSTOMER` | 0 | ≤ 10 | `NOT BUILT` | `NOT_STARTED` | OWNER |

**These are recorded so the numbers exist before the code does.** `current` is 0
because no cloud code exists — a search of `accountant/` on 2026-08-10 found no
mention of a customer cap anywhere.

**Nothing enforces any of them.** Decision `D-21` asks the owner to confirm the
reading of the caps and say whether they are enforced in code or advisory. All
five sit behind `D-08`, the gate on starting cloud work at all.

---

## How to regenerate this

The JSON is produced from the control plane by a script, so the two cannot drift.
The script lives in this session's scratchpad; the important part is the contract
it implements:

```
read   docs/CONTROL_PLANE.yaml, the metrics block
emit   id · current · desired · comparison · gap · unit · formula · command ·
       commit · evidence · status · owner_or_environment_dependency ·
       reproduction_status
sort   every key at every level, so the output hashes stably
fail   loudly if a metric exists that this document does not group
```

That last line is the guard that matters. **If somebody adds a metric to the
control plane and forgets this document, generation stops rather than silently
producing an incomplete baseline.**
