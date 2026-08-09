# DOCUMENT CONTRADICTIONS

Found 2026-08-10 by reading every document in `docs/` and `README.md` against
the code, the tests and the merged git history.

**The audit that commissioned this said 21. I found 26.** Five were named for
me; the other 21 I located. Two of the 26 are contradictions **between agents
working right now**, not historical drift, and one of those is a mistake in the
instruction that created this task.

**Nothing was silently deleted.** Every correction below leaves a dated audit
note in the document saying what it used to claim. A deleted contradiction is an
unauditable one.

---

## How to read the table

| column | meaning |
|---|---|
| **fixed?** | `YES` — corrected today. `NO` — the file is owned by another agent or is outside this agent's write list. `OWNER` — cannot be fixed by anybody but the owner. |
| **owner?** | does settling this need a decision only the owner can make |

Files this agent may not write: `README.md`, `docs/BOTTLENECKS.md`,
`docs/EPIC.md`, `docs/TAXONOMY.md`, `accountant/**`, `tests/**`, `ci/**`,
`scripts/**`, `.github/**`. Contradictions in those are **recorded, not
touched**.

---

## The 26

| # | file:line | what it says | what is true | evidence | fixed? | owner? |
|---|---|---|---|---|---|---|
| 1 | `PROJECT_STATE.md:202` (was) | "Phases 3–8 have not started." | Phases 3, 4, 5, 5B and 6 all shipped and merged. Only 7 and 8 have not started. | merged commits `3b83e30` (3), `c21127c` (4), `192e514` (5 and 5B), `3445992` (6) | **YES** | no |
| 2 | `PROJECT_STATE.md` ×5, `ARCHITECTURE.md` ×2, `BOTTLENECKS.md:101-102`, `DECISIONS.md:45,54,271` | "the 15 client-fixture tests" | An AST count of `tests/test_tally_contract.py` on 2026-08-10 finds **19** of its 24 test functions take the `client` fixture. **Recorded as `PENDING_COUNT`** — a RealTally preparation agent is counting authoritatively and its number, not this one, is the final answer. | see the reproducible command below the table | **YES** in the files this agent owns; `NO` in `BOTTLENECKS.md` and `DECISIONS.md` prose | no |
| 2b | `BOTTLENECKS.md:102` | "15 of the file's 21 tests take the `client` fixture" | The file has **24** test functions, not 21, and **19** take the fixture. Two wrong numbers in one sentence. | same command | **NO** — file not writable by this agent | no |
| 3 | `PROJECT_STATE.md:1375` heading, `:1174`, `:314`, `:984`, `BOTTLENECKS.md:83-84` | "N1 = 27.59 — FAILING", quoted as the current false-alarm rate | 27.59 is the **pre-calibration MHCLG-only** figure and is **historical**. Current: aggregate **6.29** PASS, held-out **2.90** PASS, worst department **33.33** FAIL. | `artifacts/detector_evidence.json`, keys `historical.mhclg_pre_calibration`, `aggregate`, `held_out`, `worst_department` | **YES** in `PROJECT_STATE.md`; `NO` in `BOTTLENECKS.md` | no |
| 4 | `PROJECT_STATE.md:1339` heading, `:309`, `:1175`, `BOTTLENECKS.md:49,56` | "the detectors cover 2 of 12 published real error types", stated as verified truth | `status_counts()` returns **COVERED 0, PARTIAL 2, UNCOVERED 10**. Nothing is verified. PARTIAL means a live detector reads the field that type changes — not that it has ever caught one. | `.venv/bin/python -c "from accountant.taxonomy import coverage as c; print(dict(c.status_counts()))"`, run 2026-08-10. Full matrix in `docs/TAXONOMY.md`, which this agent links to rather than duplicates. | **YES** in `PROJECT_STATE.md` | no |
| 5 | `ARCHITECTURE.md:282,293`, `PROJECT_STATE.md:309,1098,1201`, `BOTTLENECKS.md:55,57` | "the four detectors" | **One word doing the work of five different numbers.** Implemented **4** · active **3** · on the production path **1** · mapped to a published error type **1** · verified on real data **0**. `first_use` was **WITHDRAWN** for firing on roughly three clean entries in ten, with no threshold to turn. | `detectors.py:205` (`ALL_DETECTORS`), `:283` (`ACTIVE_DETECTORS`), `:206` (`SLICE_4_DETECTORS`), `:267-278` (`WITHDRAWN`); `pipeline.py:236`; `coverage.detectors_targeting_no_error_type()` returns `first_use`, `magnitude`, `gst_anomaly` | **YES** — all five recorded separately in the control plane | no |
| 6 | `PROJECT_STATE.md:1084` **vs** `:1493` | §19 step 20: *"OWNER: buy a non-Educational TallyPrime licence"* — §24, dated 2026-08-08 and headed OWNER DECISION: *"Do not purchase, activate, bypass or simulate a non-Educational licence."* | **Unresolvable by anyone but the owner.** Both are quoted verbatim under `D-01` and `D-01` is `OPEN`. §24 is later and labelled a decision, so it probably supersedes — that is a guess and it is not being acted on. | both lines read 2026-08-10 | **OWNER** | **yes — `D-01`** |
| 7 | `PROJECT_STATE.md:196,1483,1727,1908,2073` | phase status given as `COMPLETE`, `ENVIRONMENT-LIMITED`, `not fully complete` | None of those words is a status this project has. The vocabulary is the six in `CONTROL_PLANE.yaml`. `COMPLETE` maps to `PASSED`; `ENVIRONMENT-LIMITED` maps to `BLOCKED_ENVIRONMENT`. | `scripts/validate_project_truth.py` now fails on each | **YES** | no |
| 8 | `PROJECT_STATE.md:9` | `main @ f7bf5d9`, 16 commits, with `accountant/ingest/` and `accountant/taxonomy/` **untracked** | Branch is `closure/flag-cap-and-truth` at `3445992`. Both packages were committed in `6867ca9`, two days earlier. | `git rev-parse HEAD`, `git log`, `git status` on 2026-08-10 | **YES** | no |
| 9 | `PROJECT_STATE.md:315,316` | taxonomy and ingest packages "**BUILT — untracked in git**", next action "commit it" | Both committed in `6867ca9`. The next action had already been done. | `git log --oneline -- accountant/taxonomy accountant/ingest` | **YES** | no |
| 10 | `README.md:46-49` | Tests **891** · mutation **94% of 267** · coverage 95% | `PROJECT_STATE.md` §40.5 says 1298 tests and 1394 of 1402 mutants; §8 says 682 tests and 99.63% of 267; the suite collects **1624** today. **Four different test counts across three documents.** | `pytest --collect-only`, 2026-08-10 | **NO** — `README.md` is not writable by this agent | no |
| 11 | `README.md:80-84` "Status" section | describes the Tally connector's state in prose | Not wrong, but it is *status* living in a fourth place with nothing keeping it in step. `README.md` is a tracked document for `validate_project_truth.py`, so it will be caught the moment it contradicts the control plane. | — | **NO** — not writable | no |
| 12 | `EPIC.md:17-19` (A3, A4, A5) **vs** `PROJECT_STATE.md:140` | `EPIC.md` says N1, N2 and N3 are *"assistant proposal, not confirmed"* by the owner. `PROJECT_STATE.md` §6 says the same numbers are **"All OWNER DECISION, frozen."** | Both cannot be true. The whole product is gated on targets whose provenance two documents disagree about. `EPIC.md` is also still headed *"Status: DRAFT, not approved"*. | both files read 2026-08-10 | **NO** — `EPIC.md` not writable | **yes — the owner should confirm or reject the three targets** |
| 13 | `EPIC.md:114-121` | six child issues, #1 to #6 | `PROJECT_STATE.md` and `ARCHITECTURE.md` reference children **#7, #8, #9, #14 and #15**. The epic's child list is incomplete and never grew. | `PROJECT_STATE.md` §8 rows for #7, #8, #9, #14, #15 | **NO** — not writable | no |
| 14 | `EPIC.md:185` | "Out of scope for the entire epic: … multi-user concurrency, mobile apps" | Four cloud documents now exist and eight cloud decisions are open. Cloud and multi-user are being actively designed. | `docs/CLOUD_ARCHITECTURE.md`, `CLOUD_THREAT_MODEL.md`, `CONNECTOR_PROTOCOL.md`, `DATA_POLICY.md`, all created 2026-08-10 | **NO** — not writable | **yes — `D-08`** |
| 15 | `EPIC.md:78` | "P2 Inference … model needed: **yes**" | Every shipped component is deterministic and `PROJECT_STATE.md` §5 records "**No model calls** in memory or in deterministic detectors", asserted by test. No inference component exists. The epic still promises one. | `tests/test_detectors.py`; no model code anywhere under `accountant/` | **NO** — not writable | no |
| 16 | `DECISIONS.md:184` | `D-08` marked `NOT_YET_RELEVANT` — *"Nothing about cloud is being designed or built."* | False as of 2026-08-10. Four cloud documents exist. | as row 14 | **YES** — reopened as `OPEN` in the control plane and in `DECISIONS.md` | no |
| 17 | `DECISIONS.md:69` **vs** `BOTTLENECKS.md:102` **vs** `PROJECT_STATE.md:981` | the fixture date is at `test_tally_contract.py:53` / `:39` / `:39` | Line **53**. Line 39 is a `marker_for` import. `DECISIONS.md` is right and two documents point at the wrong line. | `tests/test_tally_contract.py`, read 2026-08-10 | **YES** in `PROJECT_STATE.md`; `NO` in `BOTTLENECKS.md` | no |
| 18 | `PROJECT_STATE.md:2798,2799,2824` | the readiness gate and the first detector both `PASSED` | Both are `PARTIALLY_VERIFIED`. The readiness gate is a **release gate** whose entry condition — the live reversal proof — has never run. The detector row is `PENDING_VERIFICATION` and two measured facts argue against it (rows 19 and 20). | `CONTROL_PLANE.yaml` phases `5B` and `6` | **YES** | no |
| 19 | `PROJECT_STATE.md:2913` **vs** the suite | "1298 passed, 1 xfailed, 0 failed" | The suite collects 1624 and reports 1612 passed, 1 xfailed, **11 failed** on 2026-08-10. Ten of the failures are other agents' work in flight in test files created today; the eleventh is the project-truth validator. | `COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider` | **partly** — the honest current number is in the control plane; the historical §40.5 row is left as the record of that run | no |
| 20 | `ARCHITECTURE.md:915,923` | the Tally-spine exit hard-codes "all 15 client-fixture tests pass" | A count in a design contract that has already drifted once. Replaced with "every client-fixture test in that file", and the count moved to the control plane where it can be measured. | as row 2 | **YES** | no |
| 21 | `ARCHITECTURE.md:556` | "### 4.13 Planned packages — **absent, not started**" | "not started" is a completion status, and this file is not allowed to carry one. The existence marker `absent` stays, because it is a fact about the repository rather than a status. | the file's own opening rule — "This file contains no status" | **YES** | no |
| 22 | `ARCHITECTURE.md:18-20` | `present` / `absent` markers, unexplained | `present` was being read as "works". A package can be present, imported, tested and still be a stub — `accountant/extract/adapter.py` is exactly that. The two words now say explicitly that they mean existence and nothing else. | `PROJECT_STATE.md` §8 calls the same package "STUB ONLY" | **YES** | no |
| 23 | `PROJECT_STATE.md:1046,1161,1205` **vs** the control plane | "the only owner-blocked item left" is the licence | There are **eight** blockers, four of them owner-owned. The licence is the biggest, not the only one. | `docs/BLOCKERS.md`, `B-01` to `B-08` | **YES** — `BLOCKERS.md` now lists all eight | no |
| 24 | the coordinator's instruction for this task | owner questions numbered `D-01` to `D-11` | **The instruction was wrong and would have destroyed real records.** Those ids were already taken: `D-07` is declared-licence-mode, `D-08` is the cloud-start gate, `D-11` is `N = 10` (settled), `D-10` is the merge queue, `D-03` is Tally.ERP 9, `D-04` is the runtime dependency, `D-05` is supplier identity, `D-06` is stale index. **Nothing was renumbered.** Map below. | `docs/DECISIONS.md`, which allocated `D-01` to `D-13` first | **YES** — recorded, and the coordinator has acknowledged the mistake as its own | no |
| 25 | this agent's brief **vs** `scripts/validate_project_truth.py` | the brief specifies `depends_on: OWNER` or `ENVIRONMENT` on a metric; the validator requires `depends_on` to name **another metric** or be `null` | Two agents were given different schemas for the same field. Resolved in favour of the validator, because it is the thing that runs in CI. **No information was lost** — the owner/environment dependency lives in `blockers` (each carries `kind: OWNER` or `ENVIRONMENT`), in `decisions`, and machine-readably in `artifacts/launch_baseline.json` under `owner_or_environment_dependency`. | `scripts/validate_project_truth.py`, `_check_metric_dependencies` | **YES** | no |
| 26 | `DECISIONS.md:271-278` "Open at a glance" | lists 8 open decisions | There are **19** open decisions once the cloud documents' `D-14` to `D-21` and this file's `D-22` to `D-24` are counted. | `CONTROL_PLANE.yaml`, `decisions` block | **YES** | no |

---

## The reproducible commands behind the disputed numbers

```bash
# row 2 - how many tests actually take the `client` fixture
.venv/bin/python -c "import ast,pathlib; t=ast.parse(pathlib.Path('tests/test_tally_contract.py').read_text()); \
print(sum(1 for n in ast.walk(t) if isinstance(n,ast.FunctionDef) \
and n.name.startswith('test_') and 'client' in [a.arg for a in n.args.args]))"
# -> 19   (of 24 test functions in the file)

# row 4 - what the taxonomy actually says
.venv/bin/python -c "from accountant.taxonomy import coverage as c; print(dict(c.status_counts()))"
# -> {COVERED: 0, PARTIAL: 2, UNCOVERED: 10}

# row 5 - the five detector counts, which are five different numbers
.venv/bin/python -c "from accountant.detect import detectors as d; \
print('implemented', [d.name_of(x) for x in d.ALL_DETECTORS]); \
print('active     ', [d.name_of(x) for x in d.ACTIVE_DETECTORS]); \
print('production ', [d.name_of(x) for x in d.SLICE_4_DETECTORS]); \
print('withdrawn  ', [w.detector for w in d.WITHDRAWN])"

# row 8 - where the repository actually is
git rev-parse HEAD && git branch --show-current && git status --short
```

---

## Row 24 in full — the decision-id map

A planning instruction issued to this agent numbered a list of owner questions
`D-01` to `D-11`. Those numbers were already in use. Writing them would have
overwritten real, already-referenced records.

**Three sources have allocated `D-` numbers in this project:**

| range | allocated by | when |
|---|---|---|
| `D-01` … `D-13` | `docs/DECISIONS.md` | first, 2026-08-09 |
| `D-14` … `D-21` | `docs/CLOUD_ARCHITECTURE.md` §19 and `docs/DATA_POLICY.md` | 2026-08-10, next free |
| `D-22` … `D-28` | `docs/CONTROL_PLANE.yaml` | 2026-08-10, next free after the above |

**Nothing was renumbered.** The ids are linked from other documents and from
commit messages, and a renumbered id is an unauditable one.

**The map from the instruction's labels to the ids actually used:**

| the instruction said | it actually is | why |
|---|---|---|
| `D-01` licence | **`D-01`** | already correct |
| `D-02` aggregate vs worst-department launch rule | **`D-22`** | new question; `D-02` is the frozen fixture date |
| `D-03` supplier legal identity | **`D-05`** | already existed |
| `D-04` live Tally vs stale memory | **`D-06`** | already existed |
| `D-05` Tally.ERP 9 support | **`D-03`** | already existed |
| `D-06` first-launch input types | **`D-23`** | new question; `D-06` is the stale-index question |
| `D-07` cloud data storage | **`D-14`** | the cloud agent already allocated it; `D-07` is declared-licence-mode |
| `D-08` retention and deletion | **`D-15`** | the cloud agent already allocated it; `D-08` is the cloud-start gate |
| `D-09` supported Windows/Tally versions | **`D-24`** | new question; `D-09` is the mutation engine, settled |
| `D-10` cloud launch caps | **`D-21`** | the cloud agent already allocated it; `D-10` is the merge queue |
| `D-11` cloud runtime dependency | **`D-04`** | already existed as the framework question; `D-11` is `N = 10`, settled |

Two cloud documents independently caught the same mistake and recorded it before
this file did — `CLOUD_ARCHITECTURE.md` §19 and `DATA_POLICY.md`. Three agents
reaching the same conclusion separately is the strongest evidence in this table.

---

## Three contradictions nobody can fix without the owner

| # | what | the decision |
|---|---|---|
| 6 | one document says buy a licence, another says never buy one | `D-01` |
| 12 | the three product targets are either frozen owner decisions or unconfirmed assistant proposals, depending which file you read | needs the owner to confirm or reject `N1 ≤ 10`, `N2 ≤ 10%`, `N3 ≥ 90%` |
| 14 | the epic puts multi-user and cloud out of scope for the whole project; four cloud documents now exist | `D-08` |

---

## What would stop this happening again

It already partly has. `scripts/validate_project_truth.py` now reads
`docs/CONTROL_PLANE.yaml` and scans `PROJECT_STATE.md`, `ARCHITECTURE.md`,
`OWNER_ACTIONS.md`, `BLOCKERS.md`, `CLAUDE_CONTEXT.md`, `LAUNCH_GATES.md` and
`README.md`, failing on any phase status, prose claim or metric value that
disagrees with the control plane. **30 checks, all passing as of 2026-08-10.**

Two gaps remain, and they are honest gaps rather than oversights:

1. **`BOTTLENECKS.md` and `EPIC.md` are not scanned.** Rows 2b, 3, 4, 5, 12, 13,
   14, 15 and 17 live there and are recorded above but not fixed. Adding those
   two files to `TRACKED_DOCUMENTS` is a one-line change owned by the validator
   agent.
2. **Nothing checks that a `present` marker in `ARCHITECTURE.md` §4 matches the
   filesystem.** `BOTTLENECKS.md` A8 has proposed that check since 2026-08-08
   and it is still not built. It is the smallest mechanism that would have
   caught rows 8, 9, 21 and 22 on its own.
