# Agent status

Maintained by the coordinator. One row per agent, plus the detail each agent
owes. Statuses are the only six allowed anywhere in this project:

    PASSED · NOT_PASSED · PARTIALLY_VERIFIED · BLOCKED_ENVIRONMENT
    OWNER_DECISION_REQUIRED · NOT_STARTED

plus `RUNNING` for an agent that has not reported yet.

Base commit for every agent: `3445992` (main, after PR #17 merged).
Branch: `closure/flag-cap-and-truth`.

**File ownership is exclusive.** No two agents write the same file. Only the
coordinator writes `accountant/**`; an agent that finds a source defect writes
the failing test and reports it rather than fixing it. That rule is why ten
agents can run at once without a merge conflict.

---

## Summary

| # | Agent | Status | Files owned | Blocker |
|---|---|---|---|---|
| 1 | Detector measurement | COMPLETE | `artifacts/detector_evidence.{md,json}` | none |
| 2 | Taxonomy | COMPLETE | `docs/TAXONOMY.md`, `tests/test_taxonomy_matrix.py` | none |
| 3 | RealTally preparation | COMPLETE | `docs/RUNBOOK_PHASE5_ACCEPTANCE.md`, `artifacts/realtally_readiness.md` | B-01, B-02 |
| 4 | Cloud architecture | COMPLETE | `docs/CLOUD_ARCHITECTURE.md`, `docs/CLOUD_THREAT_MODEL.md`, `docs/CONNECTOR_PROTOCOL.md`, `docs/DATA_POLICY.md` | owner decisions |
| 5 | Flag cap (coordinator) | COMPLETE | `accountant/**`, `tests/test_flag_cap.py` | none |
| 6 | Company safety | RUNNING | `tests/test_company_routes.py`, `tests/test_company_unicode.py`, `artifacts/company_safety_report.md` | — |
| 7 | Idempotency | RUNNING | `tests/test_idempotency.py`, `tests/test_error_responses.py`, `artifacts/idempotency_report.md` | — |
| 8 | Reversal / recovery | RUNNING | `tests/test_reversal_recovery.py`, `tests/test_contract_differences.py`, `artifacts/reversal_report.md` | — |
| 9 | Phase 6 exits | RUNNING | `tests/test_phase6_exits.py`, `artifacts/phase6_report.md` | — |
| 10 | Control plane / truth | RUNNING | `docs/CONTROL_PLANE.yaml` + secondary docs + `artifacts/phase_truth_table.md`, `artifacts/document_contradictions.md`, `artifacts/launch_baseline.{md,json}` | — |
| 11 | Truth validator | RUNNING | `scripts/validate_project_truth.py`, `tests/test_project_truth.py` | depends on #10 |

**HELD, not started — five agents.** Cloud website, local connector, cloud
security/isolation, installation, release integration. The owner's own mandate
says no cloud code until the architecture is internally consistent, and cloud
code additionally needs a runtime dependency that the frozen plan forbids
without explicit approval (`dependencies = []` today). That is an owner
decision, recorded, not a blocker on anything else.

---

## 1. Detector measurement — COMPLETE

- **Assignment:** reproduce 27.59 / 6.29 / 2.90 / 33.33, or prove they do not reproduce.
- **Files changed:** `artifacts/detector_evidence.md`, `artifacts/detector_evidence.json`.
- **Tests added:** none (measurement, not code).
- **Commands run:** the score harness twice, plus five `PYTHONHASHSEED` values.

| metric | current | desired | gap | result |
|---|---|---|---|---|
| historical MHCLG-only | 27.59 (8/29) | — | historical | reproduced exactly |
| aggregate | 6.29 (9/143) | <= 10 | 3.71 under | PASSED |
| held-out | 2.90 (2/69) | <= 10 | 7.10 under | PASSED |
| worst department (DHSC) | 33.33 (7/21) | <= 10 | 23.33 over | NOT_PASSED |

- **Determinism:** identical sha256 across two runs and five hash seeds.
- **Evidence:** `artifacts/detector_evidence.md`; formula quoted from `accountant/score/harness.py:91-93`.
- **Finding that matters:** six of the nine false alarms are ONE account — DHSC
  `Additions NCB PDC`, ceiling set from a 10-entry history that six NHS trusts
  each exceed. One wrong ceiling counted six times IS the worst-department
  overshoot. The leverage is one account, not a threshold.
- **Second finding:** the aggregate PASS is bought by withdrawing `first_use`.
  Switch it back on and the aggregate is 36.36.
- **Next action:** none from this agent. The DHSC ceiling is a candidate fix and
  deliberately was not tuned — root cause before threshold.

## 2. Taxonomy — COMPLETE

- **Assignment:** classify all 12 published error types honestly.
- **Files changed:** `docs/TAXONOMY.md`, `tests/test_taxonomy_matrix.py`.
- **Tests added:** 27, all passing.

| count | measured | owner stated | verdict |
|---|---|---|---|
| published types | 12 | 12 | agree |
| VERIFIED | 0 | 0 | agree |
| PARTIAL | 2 | at most 2 | agree |
| history-only ceiling | 4 | 4 | agree (3 if the suspense list counts as external input) |

- Classification totals: VERIFIED 0 · PARTIAL 2 · UNREACHABLE 4 · UNSUPPORTED 6.
- Types with no source citation: **0**.
- **Finding:** production runs **one** detector. `SLICE_4_DETECTORS` is
  `vendor_switch` alone and is the default for both `pipeline.evaluate` and
  `pipeline.run`. `ACTIVE_DETECTORS` (3) is the scoring harness only.
- **Contradiction recorded, not fixed:** `docs/BOTTLENECKS.md` A1 and
  `docs/PROJECT_STATE.md` §22 state "2 of 12 covered" as measured truth. Owned
  by agent 10.

## 3. RealTally preparation — COMPLETE, then BLOCKED_ENVIRONMENT

- **Files changed:** `docs/RUNBOOK_PHASE5_ACCEPTANCE.md` (884 lines), `artifacts/realtally_readiness.md`.
- **The canonical count, settled:** `tests/test_tally_contract.py` has **24 test
  functions, 19 of which take the `client` fixture**. Docs state 15 in **13
  places**. The 15 is a different quantity — the aliases at
  `tests/test_real_tally.py:428-466`.
- **Three defects that a licence does not fix:**
  - `tests/test_tally_contract.py:105` and `:346` contain
    `assert isinstance(client, FakeTally)`. Point the fixture at `RealTally` and
    they fail before touching Tally.
  - `test_reads_the_chart_of_accounts:77` asserts the chart equals exactly four
    ledgers. Every real company carries `Profit & Loss A/c`, and
    `RealTally.read_accounts` does not filter reserved names. **This test cannot
    pass against any real Tally.**
  - The three P3.4 register tests are not bound to `RealTally` at all.
- **Readiness:** all 15 acceptance steps PREPARED; 15/15 verified against
  FakeTally. Five items MISSING, each with a named fix — chiefly that
  `ci/acceptance_cli.py` promises a durable per-voucher state and passes no
  `log=` sink, and that there is no reconcile/resume CLI.
- **Unmeasured risk, stated as a risk:** voucher type is hard-coded `Journal`
  (`real.py:1881`) and every acceptance voucher credits `Cash`; TallyPrime's
  *Allow Cash Accounts in Journals* is off by default and whether that applies
  to XML import is not measured. First write is the falsifier.
- **Next action:** owner, ~10 minutes in the TallyPrime GUI (B-01).

## 4. Cloud architecture — COMPLETE (design only, no code)

- **Files changed:** the four cloud documents.
- **Design:** three programs; the connector **dials out only** and never listens
  on a port; five identities each with a named minter; company identity read
  from Tally and checked twice; the cloud holds identifiers and states while the
  connector holds all book data, which makes the one-company-per-index invariant
  physically true rather than one `WHERE` clause from false.
- **Exactly-once:** cloud mints the operation id at draft creation; nothing goes
  on a socket that is not already on disk; a lost reply becomes `UNKNOWN` after
  a 120 s deadline and **the transition `UNKNOWN -> DISPATCHED` does not exist**
  — the only way out is a read-only reconcile; an approved retry reuses the SAME
  operation id, because a new one throws away `DuplicateOperation`.
- **Residual risks that cannot be designed away:** port 9000 has no
  authentication and never will · a compromised connector is total for that
  customer · a compromised cloud server can write because it IS the authority ·
  one owner means no separation of duties · every cloud mitigation today is
  POLICY not MECHANISM, because none of it is built.
- **Two contradictions in the brief, flagged not resolved:** "2 connectors per
  customer" vs "one connector per company" (two live writers on one company is a
  duplicate-voucher machine — a write lease is proposed, needs confirmation);
  and "10 concurrent users" vs "one user per customer".
- **Next action:** owner decisions before any cloud code.

## 5. Flag cap — COMPLETE (coordinator)

- **Commit:** `a19a100`.
- **Files changed:** `accountant/detect/detectors.py`, `accountant/pipeline.py`,
  `accountant/web/app.py`, `tests/test_flag_cap.py`, `tests/test_first_detector.py`.
- **Tests added:** 28. **Mutants killed:** 5 of 5.
- **Current vs desired:** owner asked for >= 7 flag-cap tests; 28 exist.
- **Evidence:** the owner's six-row table asserted on the rendered page, plus an
  AST guard that no route in the web app calls `evaluate` without a cap.
- **Two defects fixed:** the cap was never passed from the web, so the overflow
  line could not render in production; and concerns past the cap were discarded
  rather than kept, so a display decision was deleting findings.

---

## Coordinator error, recorded

The decision ids in the coordinator's instructions to agents 4 and 10 (`D-07`,
`D-08`, `D-11`) **collide with ids already used in `docs/DECISIONS.md`** for
different decisions. Agent 4 caught it and allocated from the next free ids
instead; agent 10 has been sent a correction before it writes. The mistake was
the coordinator's, and it is recorded here and in
`artifacts/document_contradictions.md` rather than quietly fixed — the whole
point of the control plane is that an id cannot silently change meaning.
