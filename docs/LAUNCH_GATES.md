# LAUNCH_GATES — what has to be true before this ships

**Authority.** Every gate id here is declared in
[`docs/CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) under `launch_gates`, and the
statuses here are copied from it. If the two ever disagree, the control plane is
right.

**The rule this file is built on.** *No gate without both a test and an
evidence line.* A gate with no test is a wish. A gate with a test but no
evidence is a test nobody has run. Both columns are filled on every row below,
and `scripts/validate_project_truth.py` fails the build if either is empty.

**Where the gates come from.** The MVP completion checklist in
[`ARCHITECTURE.md`](./ARCHITECTURE.md) §11, plus the frozen acceptance criteria
`S1`–`S7` and `N1`–`N3`, plus the owner's fixed numbers. Nothing here was
invented for this document.

---

## The score

| status | count |
|---|---|
| `PASSED` | 26 |
| `PARTIALLY_VERIFIED` | 2 |
| `NOT_PASSED` | 12 |
| **total** | **40** |

Counted from `docs/CONTROL_PLANE.yaml` on 2026-08-10, not typed by hand:

```bash
.venv/bin/python -c "import re,pathlib,collections; t=pathlib.Path('docs/CONTROL_PLANE.yaml').read_text(); \
print(collections.Counter(re.findall(r'^    status: (\w+)\$', t[t.index(chr(10)+'launch_gates:'+chr(10)):], re.M)))"
```

**12 gates are not passed, and they are not 12 separate problems.** They cluster
into four:

| cluster | gates | one sentence |
|---|---|---|
| no live Tally | `LG-09` `LG-14` `LG-18` `LG-19` | no licence → no test company → no live run. One chain, one owner decision at the head of it. |
| no answer key | `LG-21` `LG-22` `LG-23` | no real ledger here carries a labelled error, so the catch rate cannot be measured and no detector can be verified |
| the product is one detector wide | `LG-20` `LG-39` `LG-40` | one detector runs in production, one input type works, and the false-alarm rate fails on the worst book |
| owner-gated infrastructure | `LG-05` `LG-38` | a `.github` edit and an external scheduler account, both waiting on a yes |

---

## Repository and CI

| id | gate | test | evidence | status |
|---|---|---|---|---|
| `LG-01` | repository identity is verified | `gh api repos/Intellora-ai/accountant-dad-core` | run 2026-08-10 — public, created `2026-08-07T11:38:55Z` | `PASSED` |
| `LG-02` | the old repository is byte-identical to its pre-build baseline | `gh api repos/Intellora-ai/accountant-dad --jq .pushed_at` and `.../commits/HEAD --jq .sha` | run 2026-08-10 — `2026-08-06T19:55:12Z` and `924d0e06b52577b563c265105d8dea142e0d205d`, both match | `PASSED` |
| `LG-03` | the gate contract passes and the count has not fallen | `tests/test_gate_contract.py` | 18 tests; `ci/gates.toml` and `ci/gate_names.lock` both hold the same 20 names, counted 2026-08-10 | `PASSED` |
| `LG-04` | the local guard blocks a bad commit before it leaves the laptop | `./scripts/guards` | 12 checks, 0.08s staged; the hook was observed rejecting a bad commit | `PASSED` |
| `LG-05` | one tool, one install mechanism | `./scripts/install-actionlint` | passes on the `pr-fast` path; **fails as a repository-wide claim** — `full.yml` still uses the Docker action. Stopped by `B-06`. | `NOT_PASSED` |
| `LG-06` | `pr-fast` passes on GitHub | `.github/workflows/pr-fast.yml` | run `31236026164`, 26s, all steps success. Measured at `4cc290f`, not re-run at HEAD. | `PASSED` |
| `LG-07` | `pr-full` passes on GitHub | `.github/workflows/pr-full.yml` | PR #12, 113s, all steps success. Measured at `4cc290f`, not re-run at HEAD. | `PASSED` |
| `LG-08` | `ci-gate` blocks deliberate failures and refuses a red PR | `ci/check_aggregate.py` and the `ci-gate` job | 8 deliberate failures observed blocking; red merge refused, API `405`; force merge refused; direct push refused | `PASSED` |
| `LG-36` | coverage ≥ 90 and mutation score ≥ 90 | `pytest --cov=accountant` / `pytest --gremlins`, both with `COVERAGE_CORE=pytrace` | 95% coverage at `4cc290f`; 1394 of 1402 terminal mutants killed at the 2026-08-09 work. **Neither re-measured at HEAD**, so this is carried forward, not observed. | `PARTIALLY_VERIFIED` |
| `LG-37` | the full test suite passes with zero failures | `COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider` | 2026-08-10, final run after every agent's work landed — **1764 passed, 6 xfailed, 0 failed**. Includes the project-truth validator, which reports 30 checks, 30 passed. | `PASSED` |
| `LG-38` | an external alert fires when a nightly run is dropped | `.github/workflows/watchdog.yml` plus an off-platform monitor that does not exist | **not built.** The watchdog runs on the scheduler it is watching. Stopped by `B-07`. | `NOT_PASSED` |

---

## Real Tally

This is the block where the product either is or is not real. Four of its ten
rows have never run.

| id | gate | test | evidence | status |
|---|---|---|---|---|
| `LG-09` | a dedicated Tally test company is connected and read from | `python -m ci.acceptance_cli` | reads were proven against a real TallyPrime on 2026-08-08, but **`Demo Co` does not exist**. Stopped by `B-01`. | `NOT_PASSED` |
| `LG-10` | the chart of accounts is read from a real Tally | `tests/test_real_tally.py` plus the live read in `PROJECT_STATE.md` §21 | HTTP 200, 1,594 bytes, 65 ms, after the illegal-character-reference fix for `&#4;` | `PASSED` |
| `LG-11` | vouchers read, and an empty company reads as empty rather than as an error | `tests/test_adversarial_write_path.py::test_a_company_that_really_is_empty_still_reads_as_empty` | after scoping voucher parsing to `BODY/DATA`, an empty company no longer reads its own `CMPINFO` counter as a voucher | `PASSED` |
| `LG-12` | one marked voucher written, trial balance moves by exactly that amount | `tests/test_tally_contract.py::test_a_posted_voucher_moves_the_trial_balance_by_its_own_amount`, plus §21 | `AD Test Expense` 168456 → 668456 paise, real TallyPrime 7, 2026-08-08 | `PASSED` |
| `LG-13` | every write is read back, never trusted from an HTTP 200 | `tests/test_tally_contract.py::test_read_back_returns_what_was_written` | proven on real Tally — the written voucher came back, then `None` after reversal | `PASSED` |
| `LG-14` | a duplicate operation ID cannot create a second voucher on a real Tally | `tests/test_tally_contract.py::test_duplicate_operation_id_is_rejected` | passes against FakeTally. **Never run against a real Tally.** Stopped by `B-02`. | `NOT_PASSED` |
| `LG-15` | reversal restores the exact prior trial balance, to the paise | `tests/test_tally_contract.py::test_reverse_restores_the_exact_prior_trial_balance` | proven on real Tally — 668456 → 168456, exact restore `True`, voucher gone `True` | `PASSED` |
| `LG-18` | **the RealTally acceptance test has passed** | `python -m ci.acceptance_cli --yes` | **required, never run.** No evidence of any kind exists and none is claimed. The command refuses to label itself live while the licence read returns `UNKNOWN`. Stopped by `B-01`, `B-02`, `B-03`. | `NOT_PASSED` |
| `LG-19` | the client-fixture contract tests pass on a real Tally, fixture unchanged | `tests/test_tally_contract.py` with `client` pointed at `RealTally` | **zero have ever run.** The count of client-fixture tests is `PENDING_COUNT` — the documents said 15, an AST count on 2026-08-10 says 19. Stopped by `B-02`. | `NOT_PASSED` |
| `LG-33` | the app refuses to start when Tally is unreachable | `tests/test_startup_path.py::test_serve_refuses_to_start_when_tally_is_not_listening_and_writes_nothing` | 6 tests. A refused startup leaves no server listening on the port. | `PASSED` |

---

## Safety — the rules that make a mistake reversible

Every gate in this block passes. They are the reason the product is allowed to
write into somebody's books at all.

| id | gate | test | evidence | status |
|---|---|---|---|---|
| `LG-16` | the `N = 10` acceptance run passes all fifteen conditions | `ci/acceptance.py`; `tests/test_acceptance_n10.py` | 15 of 15 conditions, 21 tests. FakeTally and simulator only — the live version is `LG-18`. | `PASSED` |
| `LG-17` | the readiness gate passes twelve conditions across three differing runs | `ci/readiness.py`; `tests/test_phase5b_readiness.py` | 3 of 3 runs, 30 of 30 lifecycles, clean-room wheel install succeeds. FakeTally and simulator only. | `PASSED` |
| `LG-24` | memory is bootstrapped from the company's own history before the first proposal | `tests/test_bootstrap_readiness.py`; `tests/test_web.py::test_the_app_bootstraps_this_companys_memory_before_serving` | passes. An existing company that yields no mappings is a bootstrap failure and proposes nothing. | `PASSED` |
| `LG-25` | an existing company with an empty index never proposes anything | `tests/test_bootstrap_readiness.py::test_a_bootstrap_failure_proposes_nothing_at_all` | passes. This gate exists because cross-organisation transfer measured 0.00%, which makes every customer a permanent cold start. | `PASSED` |
| `LG-26` | no memory index is ever built from more than one company | `tests/test_pipeline_isolation.py`; `tests/test_company_collision.py` | 11 plus 7 tests. One company's memory cannot build or evaluate a draft for another, and a refused collision destroys nothing belonging to the first. | `PASSED` |
| `LG-27` | a typed entry works, and a Valid entry posts with no confirmation step | `tests/test_web.py::test_a_known_vendor_posts_without_asking` | passes against FakeTally. The live equivalent is `LG-18`. | `PASSED` |
| `LG-28` | an Unclear entry asks a question containing no ledger account name | `tests/test_questions.py::test_no_question_contains_a_ledger_account_name` | passes. A word inside another word is not a leak, and a deliberately leaked account name is caught. | `PASSED` |
| `LG-29` | a Not valid entry never posts, by any path | `tests/test_adversarial_write_path.py`; `tests/test_phase4_exits.py` | 37 plus 10 tests, including a capped review screen never posting | `PASSED` |
| `LG-30` | the action log records an outcome and a reason for every entry | `tests/test_action_log.py::test_a_posted_entry_records_its_outcome_and_its_reason`; `tests/test_dismissal_durability.py` | passes. Batch rows are now written under the normalised key — ten rows were once written and zero found. | `PASSED` |
| `LG-31` | no fallback account exists anywhere in the codebase | `tests/test_phase4_exits.py::test_no_shipped_module_names_a_fallback_account_in_executable_code` | passes, and the guard itself is tested by introducing a fallback on purpose and watching it fail | `PASSED` |
| `LG-34` | a complete evidence report is produced, and no result carries a class it did not earn | `tests/test_acceptance_cli.py::test_the_live_evidence_class_is_refused_while_the_licence_is_unknown` | 13 tests. FakeTally is not an offered evidence class, because the command always talks to a real connector. | `PASSED` |
| `LG-35` | money is integer paise everywhere, and a float in a money field is refused | `tests/test_money.py`; `tests/test_web.py::test_the_amount_reaches_tally_as_integer_paise` | passes | `PASSED` |

---

## Product quality — the block that decides whether this is worth shipping

| id | gate | test | evidence | status |
|---|---|---|---|---|
| `LG-20` | the false-alarm rate is inside its target on **every** reported slice | `tests/test_n1.py::test_one_department_is_still_above_the_target_and_is_not_hidden` | the worst department measures 33.33 against a target of 10. The aggregate (6.29) and the held-out half (2.90) both pass. **Which slice is the gate is open owner decision `D-22`.** | `NOT_PASSED` |
| `LG-21` | review time is at or under 10 percent of read-everything time | `accountant/score/harness.py` lines 396-417 | **never measured on real data.** The seconds it depends on are assumed, not measured. Stopped by `B-08`. | `NOT_PASSED` |
| `LG-22` | catch rate is at or above 90 percent on real data | `tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key` | **cannot be measured.** No real ledger here carries a labelled error. Stopped by `B-08`. | `NOT_PASSED` |
| `LG-23` | at least one detector is verified catching a real published error type | `docs/TAXONOMY.md`, pinned by `tests/test_taxonomy_matrix.py` | verified coverage is 0 of 12; at most 2 are partial; 8 of the 12 are out of reach of a history-contradiction detector at all. Stopped by `B-08`. | `NOT_PASSED` |
| `LG-32` | the first detector works in the real review flow, cap applied, overflow counted | `tests/test_first_detector.py`; `tests/test_flag_cap.py` | 16 plus 16 tests against FakeTally over HTTP. **Pending verification** — an agent is re-checking. The cap half only became true on 2026-08-10, in commit `a19a100`. | `PARTIALLY_VERIFIED` |
| `LG-39` | all five input types are accepted without error | no test exists; the criterion has never been measured | only typed text works. `accountant/extract/adapter.py` is a stub with no backend. Open decision `D-23`. | `NOT_PASSED` |
| `LG-40` | the production path runs every detector calibration kept | `accountant/pipeline.py:236`, the default detector set | the default is one detector. Three survived calibration. Four are implemented. | `NOT_PASSED` |

---

## What is deliberately not a gate

| not a gate | why |
|---|---|
| a quality-decay ratchet, or a 21st CI gate | explicitly forbidden by the owner. `BOTTLENECKS.md` adds none and neither does this file. |
| the number of tests | a count is not a quality. Coverage, mutation score and the suite passing are the gates; the total is not. |
| how often an error type occurs in the wild | the published record does not support such a number, and an invented one would quietly become the argument for keeping or dropping a detector |
| anything cloud | it sits behind `D-08`, which is a gate on *starting*, not on shipping |
