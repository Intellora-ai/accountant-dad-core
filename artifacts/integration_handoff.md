# Integration handoff

## STANDING MANDATE — FINAL AUTONOMOUS RUN, 2026-08-10

**The owner is unavailable and will not return to answer questions or approve
intermediate decisions.** This mandate applies for the whole session and to any
session that resumes this work.

Continue autonomously until every repository task is finished, committed,
pushed, tested, reviewed and merged. Do not: ask for clarification · ask for
approval · wait for another message · stop after a partial result · leave
completed work uncommitted or unpushed · leave a PR unreviewed · leave a green
PR unmerged · start a duplicate implementation.

**The loop, for every branch and every PR, no exceptions:**

    inspect -> implement -> targeted tests -> affected suite -> full suite
    -> inspect diff -> git diff --check -> commit -> push -> wait for GitHub
    -> review the PR diff -> review CI logs -> merge -> fetch
    -> confirm the merge in origin/main

Nothing is called complete until its commit is created, pushed, reviewed,
merged **and confirmed in `origin/main`**.

**Self-review before every merge:** scope correct · no unrelated files · no
Phase 8 code in the Phase 7 PR · no secrets · no invalidated measurement
presented as valid · no question rate written as zero · no xfail presented as
PASS · no unsafe GST VALID path · no silent vendor merge · no dropped
`raw_subject` evidence · no test weakened to obtain green.

**Phase 7 hard gates — all must pass before merge:**

    GST safety tests            4/4 ordinary PASS
    remaining GST xfails        0
    unsafe GST VALID            0
    unsafe GST posts            0
    HTTP outage tests           3/3 safe
    full outage matrix          10/10 safe
    backend swap tests          10/10 PASS
    adapter contract tests      25/25 PASS
    no-reader findings          0
    D-05 defects :250/:272/:300 0
    D-05 live identity cases    5/5 PASS
    D-05 persistence cases      30/30 PASS
    AST regression guard        PASS

**Final-report labels, and only these:** COMPLETE · PASS · MERGED ·
BLOCKED_ON_HUMAN_EVIDENCE · NOT_MEASURED · INVALIDATED. Banned: "almost done",
"basically complete", "should pass", "waiting for owner" — the last is
permitted only on an item explicitly marked BLOCKED_ON_HUMAN_EVIDENCE.

**Do not wait for any human action.** Finish all repository work around the
blockers. Mark only these as externally blocked, and never as complete:
B-01 · B-02 · production extraction backend approval · real-bill corpus ·
authenticated actor identity. `LICENSED_REALTALLY = BLOCKED_ON_HUMAN_EVIDENCE`.


One file, updated at every checkpoint. If a session dies, this is what the next
one reads first.

## Owner decisions, 2026-08-10 — FINAL, not to be re-asked

    GST policy            = OPTION B
    Phase 8 scope         = FROZEN-PLAN
    question rate         = NOT_MEASURED
    invalid measurements  = discarded

**GST has no waiver.** `3/4 GST tests pass = FAIL`. `4/4 = PASS`. A failing GST
test is diagnosed and repaired, never accepted, hidden or waived. Every failure
is classified as exactly one of `CODE_DEFECT · FIXTURE_DEFECT ·
ENVIRONMENT_DEFECT · CONTRACT_DEFECT · TEST_DEFECT` — "known limitation",
"nonblocking", "acceptable xfail" and "out of scope" are not classifications.

**Phase 8 is exactly the frozen plan** — five input types, four detectors, the
rules corpus with source URLs, provenance in the UI, full reversal history. The
"twelve workstreams" wording was never a scope definition and no twelve tasks
are invented from it.

## Owner rulings on the four open questions, 2026-08-10

**Q1 — stale pins: ALLOWED WITH PROVENANCE.** A stale pin on a merged test may be
updated when a later ruling changes the expected value, **only if the finding is
unchanged**, and only when the update publishes all of:

    old value -> new value
    the ruling or commit that caused the change
    the dataset and the command used
    proof the measurement and the finding are unchanged

If the finding itself changes, the pin is **not** silently updated. The result is
marked as changed and investigated as a regression or a policy consequence.

**Q2 — the two omitted branches: MERGE BOTH, after D-05.** Order is D-05, then
`owner/answer-problem-binding` (`f8daa90`), then rebase and merge
`owner/d03-fail-closed-resume` (`1619318`). Neither is parked, neither is
deleted. The d03 rebase must preserve the D-29 ruling — **refuse the whole
batch** — and is pushed with `--force-with-lease`, merged only on fresh gates.

**Q3 — the 3,083 untracked lines: COMMIT AND PR, after evidence cleanup.** A
documentation/evidence branch separate from the Phase 7 adapter PR. Every
invalidated cross-org number is marked, verbatim, `INVALIDATED — both
measurements imported unchanged main code from /tmp`. It is never replaced with
`0`, and question rate is never reported as zero.

**Q4 — `identity.py`'s stale metric: INVESTIGATE, THEN CORRECT.** The old text is
`UNVERIFIED/CONFLICTING` until the dataset, commit, branch, worktree, command,
normalisation policy and pair count are established. If 86.21% / 30-of-30
reproduces from the intended committed data and worktree, the docstring is
updated to those values with the old-to-new correction in the commit. If the
datasets genuinely differ, **both** results are kept and labelled by dataset.
Neither is presented as universal.

**B-01 — manual TallyPrime GUI action, operator only.** Create company `Demo Co`
with ledgers `Purchases`, `Sundry Expenses`, `Cash`, `Sharma Traders`, then
record company name, creation time, TallyPrime version, ledger names, company
identifier if available, and a screenshot or exported verification.

    LICENSED_REALTALLY = BLOCKED until B-01 evidence is verified

Never labelled `LICENSED_REALTALLY` because the XML gateway is reachable, or
because a simulated company exists.

## Phase 8 acceptance decomposition — AUTHORIZED by the owner, 2026-08-10

Recorded as an **explicitly authorized change**, not a silent one. The frozen
plan names the 95-per-field S2 target, the third-party reader adapter and Tally
posting as separate requirements; this decomposes them into exits that can
actually be proven, without relabelling anything.

**Why the old S2 exit was wrong, and this is the whole justification:** with 20
unrenderable JPG cases in the denominator, `95 per 100 per field` was measuring
**our renderer**, not the reader. A test whose ceiling is set by our own tooling
limitation says nothing about the thing under test.

**EXIT 1 — renderable extraction benchmark.** 80 renderable synthetic documents,
truth from canonical JSON, labelled `GENERATED_TRUTH`. **>= 76/80 exact matches
per named field**, scored and reported per field. The 20 unrenderable JPG cases
are **not** in this denominator.

    The corpus is SYNTHETIC_EVIDENCE and is NOT human-verified.
    "human-verified" was corrected to "GENERATED_TRUTH from canonical JSON"
    before adoption. It measures the extraction pipeline on renderable inputs
    and claims nothing about real-world reader accuracy.

**EXIT 2 — JPG safety.** JPG bytes reach the adapter. A successful reader
response becomes a structured record with provenance. If JPG cannot be
processed, **every field is explicit `NOT_FOUND` with a reason**. No JPG failure
becomes a silent blank or a fabricated value. Labelled `ADAPTER_CONTRACT`, never
reader-accuracy evidence.

**EXIT 3 — extraction seam qualification.** No approved production reader and no
real bills are required in Phase 8. Prove the agent-solvable seam: the adapter
accepts supported bytes · the backend swaps without changing downstream code ·
the returned object satisfies the `ExtractedRecord` contract · every value
carries a source tag · missing or malformed backend output becomes explicit
`NOT_FOUND` · backend errors are visible and safe · the application works with a
deterministic fake reader.

    EXTRACTION_SEAM = PASS            is the claim
    REAL_READER_ACCURACY = PASS       is NOT the claim, ever

**EXIT 4 — GST safety path through FakeTally. Q3 = D is NOT reversed.** Full GST
posting stays outside Phase 8. Prove only the equivalent safety path: calculate
one supported GST purchase case · validate tax and balanced debit/credit · pass
the voucher through the `TallyClient` boundary · write to FakeTally · read it
back · reject a duplicate operation id · reverse it · prove the exact prior
FakeTally trial balance is restored. **Every result labelled `FAKETALLY`. This
is never called a real Tally write.**

**EXIT 5 — existing CI, unchanged.** Unit, integration, system, safety, mutation
and workflow-integrity through the existing GitHub Actions gates.

**Final reporting must distinguish these, and never merge them:**

    PHASE_8_ENGINEERING_EXIT     PASS/FAIL
    GENERATED_TRUTH_EXTRACTION   PASS/FAIL
    EXTRACTION_SEAM              PASS/FAIL
    FAKETALLY_GST_SAFETY         PASS/FAIL
    REAL_READER_ACCURACY         BLOCKED_ON_H-01/H-02
    LICENSED_REALTALLY           BLOCKED_ON-B-01/B-02
    REAL_WORLD_PRODUCT_ACCEPTANCE PENDING

**Standing prohibitions restated by the owner with this change:** do not add a
licence requirement · do not require owner-supplied bills · do not implement full
GST posting · **do not relabel synthetic, fake or adapter evidence as real-world
evidence.**

## Phase 8 — the eight owner answers, 2026-08-10. FINAL. Never ask again.

    Q1 = A     official CBIC and Income Tax Department notifications/circulars
    Q2 = C     only codes seen in the company's verified history
    Q3 = D     do NOT implement GST posting in Phase 8
    Q4 = B     stub extractor only, no production backend selected
    Q5 = C     UK government data where it fits, synthetic elsewhere, labelled
    Q6 =       five sequential PRs
    Q7 = B     fix the DHSC "Additions NCB PDC" root cause BEFORE four detectors
    Q8 = A     explicit accountant_dad / operator actor labels
    SCOPE      FROZEN-PLAN

**Q3 is a deliberate safety boundary, not a failed test.** GST posting stays off.
`CGST/SGST/IGST split`, `place of supply`, `GST ledger selection` are all
`NOT_IMPLEMENTED`; `successful GST posting with tax lines` is `NOT_MEASURED`.
Never post from a supplier GSTIN alone, company history alone, a guessed state
or a guessed rate.

**Q4 means Phase 8 extraction cannot be called complete.** A stub returning
`not_found` cannot satisfy the real extraction-quality exit.
`S2 = NOT_MEASURED`, `production backend = NOT_SELECTED`,
`five-input-type real extraction = INCOMPLETE`.

**Q7 forbids the shortcut.** Reproduce 6.29 aggregate / 36.36 all-detector /
6-of-9 from DHSC first, fix the root cause not the threshold, add a regression
for that account, then enable four detectors and measure. `N1 <= 10` or the
detector exit is `FAIL`. A feature flag is development-only and can never be
used to claim the all-four exit while production runs one.

**The five Phase 8 PRs, in order, each confirmed in main before the next:**

    PR-1  five input-type contracts and fixtures
    PR-2  four detector expansion and measurements
    PR-3  rules corpus and source provenance
    PR-4  UI provenance
    PR-5  full reversal history

## Human-required, and it is only these five

| ID | Item | Status | Blocks |
|---|---|---|---|
| H-01 | approve a production extraction backend | OWNER_DECISION_REQUIRED | real-reader S2 |
| H-02 | supply real or anonymised bills | OPTIONAL_HUMAN_INPUT | real-bill accuracy only |
| H-03 / B-01 | create Demo Co in the TallyPrime GUI | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only |
| H-04 / B-02 | provide licensed Tally evidence | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only |
| H-05 | approve an authenticated actor identity | OWNER_DECISION_REQUIRED | authenticated identity only |

H-03/H-04 are the same real-world actions as B-01/B-02. Both id sets are kept
and cross-referenced rather than reconciled into a third — this project has
already suffered two decision-id collisions from tidying.

**None of these blocks** Phase 8 schema, rules-corpus preparation, detector
tests, UI provenance or reversal history. They block only the listed exits.

## The closed merge order

    1  merge #22 on fresh green gates                     DONE  003b66c
    2  confirm #22 in origin/main                         DONE
    3  rebase and merge #23 on fresh green gates          DONE  8072b5c
    4  confirm #23 in origin/main                         DONE
    5  finish and merge D-05 hardening
    6  re-measure stale D-05 pins, old->new protocol
    7  merge owner/answer-problem-binding
    8  rebase, test and merge owner/d03-fail-closed-resume
    9  commit corrected evidence artifacts, separate PR
    10 reconcile and correct identity.py's stale metric
    11 complete B-01 manually in TallyPrime            OWNER
    12 keep LICENSED_REALTALLY blocked until B-01 verified
    13 finish Phase 7, only after its gates pass
    14 start Phase 8, frozen-plan scope only

## Status labels

From 2026-08-10 the only permitted values are:

    PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED

plus `RUNNING` for work that has not reported yet.

---

## Where things stand

| | |
|---|---|
| current `origin/main` | `6693a4c` |
| last completed step | **7 — `owner/answer-problem-binding` merged and confirmed** |
| next exact action | step 8 — rebase, test and merge `owner/d03-fail-closed-resume` |

Steps 1–7 of the owner's closed merge order are complete:

    6693a4c  Bind every answer to the question that offered it            (#25)
    03cc076  D-05: the raw supplier name reaches the live decision        (#24)
    8072b5c  D-22: the detector gate says NOT_PASSED, and which department (#23)
    003b66c  Phase 9: account mappings do not transfer between orgs       (#22)
    0046072  Six owner answers: D-01, D-04, D-05, D-06, D-22, D-29        (#21)
    1ca65a9  D-06: live Tally wins over stale memory                      (#20)

**Branches pushed and waiting, not merged:**

    docs/evidence-correction   03081f6   2,283 lines rescued, step 9
    phase7/adapter-contract    0812d37   GST 4/4 + 30/30 done, step 13, not pushed

**Phase 7 is measured and ready but deliberately unmerged.** All four GST xfails
cleared as ordinary passing tests, the 30-case sweep is 30/30, exits 7.1/7.2/7.3
PASS with `file:line` evidence, 9 mutants injected and 9 killed, suite `2055
passed, 6 xfailed` verified independently. It waits because the owner's order
puts it at step 13.

**The honest limit on that result, which must not be lost:** the GST tests pass
because the system now **refuses** GST bills, not because it handles GST. Arm C
of the sweep is *tax correctly absent*. No GST bill ever posts. No rate, ledger,
CGST/SGST/IGST split or place-of-supply rule was invented — correctly, that is
the Phase 8 rules corpus.

    GST bill that posts with tax lines = NOT_MEASURED

**Question rate — measured for the first time**, on a fixture that had never
existed, provenance asserted:

    20 pairs of X vs X Pvt Ltd
    SAME 0 · AMBIGUOUS 20 · questions 20 · unsafe merges 0

That is the measured cost of the D-05 ruling: the system asks about every
same-supplier pair written two ways rather than merging silently.

**Rebase conflicts, probed in advance in throwaway detached worktrees so no
branch ref moved:**

    owner/answer-problem-binding   clean, zero conflicts        (confirmed, merged)
    owner/d03-fail-closed-resume   5 conflicts, ALL docs/artifacts, ZERO code
                                   accountant/reversal.py does NOT conflict,
                                   so the D-29 ruling is not at risk from the merge

Both #22 and #23 had to be rebased and re-run before they could merge.
`strict_required_status_checks_policy: true` means a green run stops counting
the moment `main` moves. GitHub refused with `2 of 2 required status checks are
expected`. That is the protection working. No stale green was merged.

**Not scheduled, flagged to the owner:** `owner/answer-problem-binding`
(`f8daa90`) and `owner/d03-fail-closed-resume` (`1619318`) appear in the earlier
checkpoint list but not in the 18-step order. They are not merged and will not
be without explicit instruction.

---

## Checkpoints

| # | What | Status | Evidence |
|---|---|---|---|
| 1 | #21/#22/#23 gate status recorded | **PASSED** | all three showed `pr-fast` SUCCESS, `pr-full` SUCCESS, `ci-gate` SUCCESS, `mergeable: MERGEABLE`, `state: CLEAN` |
| 2 | #21 merged and confirmed in main | **PASSED** | `0046072` on `origin/main`, 3 files +664 −139 |
| 3 | #22 merged and confirmed in main | RUNNING | rebased `a1310ee` → `49f7fcf`, force-with-lease pushed, gates re-running |
| 4 | #23 merged and confirmed in main | NOT_STARTED | needs a rebase onto main after #22 lands |
| 5 | D-05 `:250`/`:272`/`:300` fixes committed | RUNNING | agent working in `wt-d05` on `owner/d05-legal-identity` |
| 6 | D-05 production-path tests pass | NOT_STARTED | |
| 7 | D-05 branch pushed and PR opened | NOT_STARTED | |
| 8 | D-05 merged and confirmed in main | NOT_STARTED | |
| 9 | remaining branches integrated | NOT_STARTED | `owner/answer-problem-binding`, `owner/d03-fail-closed-resume` |
| 10 | Phase 7 rebased and validated | RUNNING | agent verifying the four GST xfails and exits 7.1/7.2/7.3 in `wt-phase7` |

**Why #22 needed a rebase.** The ruleset has
`strict_required_status_checks_policy: true`, so a branch must be up to date
with `main` before it can merge. #21 moved `main`, which invalidated #22's
green run. GitHub refused with `2 of 2 required status checks are expected`.
That is the protection working, not a fault. #23 will need the same treatment
after #22 lands.

---

## Branches not yet in main

| branch | head | state |
|---|---|---|
| `phase9/cross-organisation` | `49f7fcf` | PR #22, rebased, gates running |
| `owner/d22-detector-gate` | `31a053a` | PR #23, needs rebase after #22 |
| `owner/d05-legal-identity` | `8d37c83` | no PR — hardening in progress |
| `owner/answer-problem-binding` | `f8daa90` | no PR |
| `owner/d03-fail-closed-resume` | `1619318` | no PR, stale base `3445992`, 0 conflict markers |
| `phase7/adapter-contract` | `cb6348e` | no PR, GST xfails NOT cleared |

---

## The root cause still open — D-05

One connected data-integrity defect, not three separate bugs:

> the system destroys or fails to preserve identity evidence before the live
> company-decision layer uses it

All three sites are in `accountant/memory/company.py`:

    :300  the live index reads the stripped Observation.subject, not the raw subject
    :272  observe() drops raw vendor names for everything learned after bootstrap
    :250  record_correction() stores a human's explicit answer as INCOMPLETE

The fix is storage and data flow, not another comparison rule layered on top of
missing evidence. Three things are preserved separately — `raw_subject`,
`normalized_subject`, `identity_evidence` — and the normalized key may find
candidates but must never be the only input to the identity decision.

---

## The 53.08% was never real — settled 2026-08-10, verdict A

`accountant/memory/identity.py` claimed `53.08% at best within` and `0.00% on 29
of the 30 pairs`, over `16,011 rows`. Investigated read-only against every
dataset, subset, denominator and split point in the repository:

    NOT REPRODUCED — no dataset in this repository produces 53.08% or 29-of-30

The experiment was re-run at `6867ca9`, the commit that introduced the claim.
Output byte-identical to today. **The docstring did not match its own commit on
the day it was written.** All 42 department subsets, four alternative
denominators and six alternative split points were tried; none hits 53.08, and
none produces exactly one non-zero cross pair. `53.08% == 69/130` and 130 is not
a reachable denominator here.

Ruled out: only one dataset has ever existed in this repo's history; D-05 is
unmerged and measured identical anyway; the merged `c21127c` normalisation
change moved these numbers by zero.

**The measured truth, provenance asserted, on committed fixtures:**

    within the same department   86.21% best (MHCLG) ... 4.35% worst (HMT)
    across departments            0.00% on ALL 30 pairs
    aggregate within             63/143 = 44.06%

**`16,011` is the size of the SOURCE, not of the measurement.** It is
`sum(published_rows)` over seven published files. The run loads 283 vouchers,
uses 140 as history and scores **143**. Quoting the two as one number overstates
the sample 56-fold. It is also impossible as stated: DBT's narration column is
empty in all 199 of its rows so it can enter no pair, and seven departments give
42 pairs, not 30.

**The conclusion survives and is stronger** — 30-of-30 beats the claimed
29-of-30. But at n=143 one entry moves the within-department aggregate by 0.70
points, so **the number that survives this sample size is the ZERO, not the
86.21%.**

**Why it survived:** `5308` appears nowhere as an integer. The suite pins pair
count, DBT rejection, determinism and the report SHA-256 — never the headline.
Documented in prose, never falsifiable. That is the defect; the wrong number is
the symptom.

**Correction scope: 26 stale lines across 11 tracked files** —
`accountant/memory/__init__.py`, `identity.py`, `pipeline.py:195-197`,
`company.py:26`, three docs, `docs/CONTROL_PLANE.yaml:445,461`, three test
docstrings. Applied at owner step 10, after the docs PR, because `identity.py`
and `company.py` are also touched by D-05 which merges at step 5.

**Do NOT change `tests/test_cross_organisation.py:31`.** It cites 16,011
correctly and deliberately, to contrast the seven published files with the
committed slice.

## Evidence corrections that must stay corrected

These have each been wrong once in this project's records. They do not get
quietly restated.

- **The wrong-leg posting severity claim was NOT reproduced** by the
  399-sequence sweep. It is not established evidence.
- **The earlier D-05 cross-org zero-cost measurement was invalid.** Both sides
  ran from `/tmp`, `sys.path[0]` was `/tmp`, and the editable install resolved
  `accountant` to the main checkout — so both sides measured the same unchanged
  code. It does not prove zero cost. Every future measurement must verify that
  the imported `accountant` package came from the intended worktree.
- **`question rate = NOT MEASURED`.** Never `0`. Zero is not inferred from "no
  questions appeared in the generated book". The fixture that would measure it
  — the same supplier represented as `X` and as `X Pvt Ltd` — does not exist
  yet, and is built only after the storage root fix works.

---

## Owner blockers, unchanged

    B-01  create Demo Co and its four ledgers in the TallyPrime GUI
    B-02  a non-Educational TallyPrime licence
    B-03  the RealTally acceptance run itself, which needs B-01 and B-02

None of these blocks any of the work above.
