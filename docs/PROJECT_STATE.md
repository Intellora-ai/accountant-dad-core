# PROJECT_STATE — Accountant Dad

## 1. Document control

| | |
|---|---|
| **Purpose** | The project's operational memory. What was decided, what is built, what is verified, what remains, why. One file. No other progress document exists. |
| **Repository** | `Intellora-ai/accountant-dad-core` — public — owner type **User** — created `2026-08-07T11:38:55Z` — VERIFIED (GitHub API) |
| **Branch / commit** | `closure/flag-cap-and-truth` @ `3445992` — "the first detector (#17)" — measured 2026-08-10 with `git rev-parse HEAD`. **The working tree is NOT clean**: the `flag_cap = 3` change is in flight in `accountant/detect/detectors.py`, `accountant/pipeline.py` and `accountant/web/app.py`, and several documents and test files are untracked. Several agents are working in this tree at once. <br><br>*Audit note, 2026-08-10: this row said `main @ f7bf5d9`, 16 commits, with `accountant/ingest/` and `accountant/taxonomy/` untracked. Both were committed in `6867ca9`. The row was two days stale.* |
| **Updated** | 2026-08-08 |
| **Last verified state** | 2026-08-08. CI evidence is from nightly runs `31237228028` and `31238866032`. **The newest evidence is §21 (first real Tally), §22 (first product-quality measurements) and §23 (documentation drift corrected)** — those three sections supersede any older statement in this file that contradicts them. |
| **Companion documents** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the design. [`BOTTLENECKS.md`](./BOTTLENECKS.md) — what currently costs more than it should, with the smallest guard per class of defect. |
| **Who may update** | The owner, or Claude on the owner's instruction. |

> **Looking for what only you can do? → §43, the Human Work Register.**
> One ordered list, grouped so it can be worked in a single sitting. Why each
> item cannot be automated is in
> [`ARCHITECTURE.md` §20](./ARCHITECTURE.md). **Group D is deliberately last —
> those four ruleset changes stop unattended merging.**

**Every label in this file means something exact:**

```
VERIFIED       observed in the repo, on GitHub, in a command, a test, or an authoritative source
OWNER DECISION explicitly instructed by the owner
INFERRED       reasoned from verified evidence, not itself observed
UNVERIFIED     still needs a test or a live observation
DEFERRED       intentionally not being built now
```

**Update rule.** Every update to this file must state the sections changed, the
evidence for each change, and anything still unresolved. A claim with no run ID,
command, file path, or source link does not go in.

**A note on scope.** `accountantdad.md` existed at the repo root for part of
2026-08-08 and was deleted the same day, untracked and never committed, on the
owner's instruction *"DONT DUPLICATE THINGS AT ALL."* This file replaced it.
Inside this file a fact appears once and is cross-referenced, never restated.

---

## 2. Source-of-truth hierarchy

Authority runs top to bottom. Lower never overrides higher.

```
1. Explicit owner decisions
2. Verified repository and GitHub state
3. The frozen product plan
4. Measured test and CI evidence
5. Official external documentation
6. Inference
```

**Guesses are never facts.** If two sources conflict, **report the conflict and
stop.** Do not silently rewrite a frozen decision to make a conflict go away.

---

## 3. Product purpose

A local web app receives typed entries or bills, reads the company's Tally
history, proposes and validates an accounting treatment, asks plain-language
questions when needed, posts only Valid entries into Tally, records provenance,
verifies the write, and supports reversal.

| Rule | Label |
|---|---|
| Tally is the book of record | OWNER DECISION — *"future A, WE USE TALLY"* |
| SQLite stores only our index, flags and action log — never their books | OWNER DECISION |
| The app is not a replacement ledger. Children #10–#13 were deleted for this reason | OWNER DECISION |
| The app runs locally, because Tally is reached through a local connector on `localhost:9000` | VERIFIED — TallyPrime is Windows-only and exposes no public/cloud API |
| Extraction is a third-party adapter. **No OCR or reader is built in-house** | OWNER DECISION — *"Use somebody else's reader"* |

**Why the ledger was deleted:** if the user removes this software, they still have
complete books. So we do not need to be a ledger. That single question removed
roughly two thirds of the original build.

---

## 4. Frozen owner decisions

All OWNER DECISION. Verification state in the right column.

| # | Decision | State |
|---|---|---|
| 1 | New repo `Intellora-ai/accountant-dad-core` | VERIFIED — exists, public |
| 2 | `Intellora-ai/accountant-dad` must remain **untouched** | VERIFIED — `pushed_at 2026-08-06T19:55:12Z`, head `924d0e0`, unchanged from the pre-build baseline |
| 3 | Repository is public | VERIFIED — `visibility: public` |
| 4 | Gates **block**; never advisory | VERIFIED — 8 deliberate failures observed blocking (§18) |
| 5 | **90** is the floor wherever a 0–100 scale exists | VERIFIED — 4 gates carry `threshold = 90` (§9) |
| 6 | Three layers: laptop → PR → full authoritative | VERIFIED — `scripts/guards`, `pr-fast`, `pr-full` + `full.yml` |
| 7 | Mutation engine is `pytest-gremlins`. **mutmut, MutPy, Cosmic Ray forbidden** | VERIFIED — `pyproject.toml` lists only `pytest-gremlins>=1.9`; the others appear nowhere |
| 8 | One tool per responsibility | VERIFIED — `pyproject.toml` comment names the excluded overlaps |
| 9 | `ci/gates.toml` is the source of truth | VERIFIED — `tests/test_gate_contract.py`, 18 tests |
| 10 | No placeholder jobs | VERIFIED — `ci/check_stubs.py` runs in `pr-fast` and `workflow-checks` |
| 11 | Workflows validated **before** they reach GitHub | VERIFIED — `scripts/guards` runs actionlint + zizmor locally and fails |
| 12 | Zero selected tests must never be green | VERIFIED — `pr-fast.yml` `changed-tests` reads the JUnit count and re-runs the full suite if `< 1` |
| 13 | Incomplete mutation → `FAIL_INCOMPLETE`, `score_percent: null` | VERIFIED — `ci/check_mutation.py` |
| 14 | Bandit blocks at **LOW severity and LOW confidence** unless explicitly justified | VERIFIED — `bandit -r accountant --severity-level low --confidence-level low` |
| 15 | Claude may inspect, fix, report, push, label and attempt a merge — **never decides whether gates passed** | VERIFIED — §16 |
| 16 | GitHub's deterministic required checks are the authority | VERIFIED — merge of a red PR refused (§18) |
| 17 | Claude must never weaken, remove, replace or bypass rulesets or branch protection | VERIFIED — 6 tamper attempts refused (§16) |

---

## 5. Product safety rules

### The posting rule — single, current, no confirmation step

```
Not valid → notify and do NOT post.
Unclear   → ask permitted plain-language questions, record the answer, re-evaluate from the top.
Valid     → post automatically. No human confirmation is required or requested.
```

OWNER DECISION, 2026-08-07: *"we r not confirming anytg... if it thinks everything
is fine pass."* **The Valid outcome IS the posting permission.** The older
"posts once confirmed" wording is superseded and must not be reintroduced. An
answer to a clarifying question is new information, not authorisation — the entry
re-enters the decision order and can still come out Not valid.

### The write-safety rules

| Rule | Origin | State |
|---|---|---|
| Unique `operation_id` generated **before** posting, attached to draft, decision, narration, action log and reversal request | correction C5 | VERIFIED in code — `accountant/tallyio/client.py:31` `new_operation_id()` |
| Accountant Dad marker on every written voucher — `[ACCOUNTANT_DAD:<op>]` | correction C4 | VERIFIED — `client.py:27-56`, asserted by contract test |
| A duplicate `operation_id` cannot create a second voucher | C5 | VERIFIED against the fake; **still UNVERIFIED against real Tally** — and on 2026-08-12 it was DISPROVED in real books: `mvp_real_tally.py` ran twice and left a duplicate (§47.10). The write path now requires an `operation_id` and asks Tally first; the contract test that would prove it on real Tally is **no longer licence-blocked** (§48) but has still not been run |
| Every write is read back from Tally, not trusted from an HTTP 200 | C6 | **VERIFIED against real Tally** (§21) |
| Reversal tested against the **exact prior trial balance**, in paise | #6.5 | **VERIFIED against real Tally** — exact restore `True`, voucher gone `True` (§21) |
| **Memory must be bootstrapped from an existing company's own Tally history before the first proposal is shown** | measured cross-organisation result, §22 | **PRODUCT INVARIANT — NOT YET ENFORCED.** An empty memory for an existing company is a **product failure**, not a neutral state. Design rule in [`ARCHITECTURE.md` §4.3](./ARCHITECTURE.md#43-memory-index--accountantmemoryindexpy--present); exit criteria in [`ARCHITECTURE.md` §11](./ARCHITECTURE.md#11-mvp-completion-checklist) |
| Refuses to write to a company with no recorded backup | #6.7 | VERIFIED — `CompanyNotBackedUp` in `client.py:66` |
| **No automatic fallback account.** Not Suspense, not Sundry Expenses, not anything | OWNER DECISION | VERIFIED — no fallback exists in `accountant/decide.py` |
| Every output field carries provenance | Hallucinate definition | VERIFIED — `Voucher.provenance` in `accountant/schema.py` |
| Every detector flag carries a specific reason **and** a plain-language question | #3.2, #3.3 | VERIFIED — `accountant/detect/detectors.py`, `accountant/questions.py` |
| **No model calls** in memory or in deterministic detectors | #2.6, #3.8 | VERIFIED — asserted by test |

**C4 and C5 together are the most important lines in the project.** They are the
difference between a reversible mistake and permanent damage to a real business.

---

## 6. Definitions and acceptance criteria

All OWNER DECISION, frozen. Reproduced because they are the contract, not prose.

| Term | Definition | Measured by |
|---|---|---|
| **Valid** | Every deterministic check passes, AND memory returns exactly one consistent account, AND no detector fires. All three. No model confidence. | boolean per entry |
| **Not valid** | At least one problem exists that is **not answerable** — nothing a person could say would fix it. Reserved for that case only. | boolean, naming the unanswerable problem |
| **Unclear** | At least one **answerable** problem exists. The normal not-yet-posted state. | boolean per entry |
| **Clarifying question** | Closed answer set, every option an account that exists in that company's Tally chart. Open-ended questions are not clarifying questions. | questions per 100 entries |
| **Question cap** | **5**, or when a further question would not change the outcome, whichever comes first | count of entries hitting the cap |
| **Non-overlapping** | No two questions on one entry may resolve the same problem. Repeated problem id fails the test. | distinct problem ids |
| **Hallucination** | Any output value not derivable from the input document, the company's Tally history, or the rules corpus | count of values with no provenance tag |
| **Accurate** | **Banned as a single word.** Replaced by three numbers with three denominators: field extraction rate, account match rate, false alarm rate. | three numbers, never one |
| **Extract** | Produce values for named fields from input bytes. v1 fields: date, party, total amount, tax amount, line items. | per-field exact match |
| **Complete** | Every named field carries a value **or an explicit `not_found`**. A silently blank field fails. | per record |
| **Post** | Write a voucher into the user's Tally such that their trial balance changes | before/after differ by exactly the entry amount |
| **Reversed** | Bulk-reverse returns the trial balance to its exact prior value, in paise | equality, to the paise |
| **Done** | Every acceptance criterion in that child's spec passes, by its named verification method | criteria passed vs total |

### S1–S7

| # | Requirement | Target | Method |
|---|---|---|---|
| S1 | Accept typed text, PDF, PNG, JPG, DOCX | **5 of 5** without error | Demonstration |
| S2 | Per named field, output equals the human-verified value | **95 per 100, per field** | Test |
| S3 | Output a complete structured record | 100% complete or explicitly incomplete, **never silently blank** | Test |
| S4 | Run every applicable deterministic check, report count run and count failed | 100% of records carry a check count | Test |
| S5 | Ask a plain-language question for every answerable problem; hand over as a draft when the budget is spent | 100% of answerable problems produce a question · **0 silent guesses** · **0 questions containing a ledger account name** | Test |
| S6 | Write to Tally only for Valid; notify without posting for Not valid; record outcome and reason for every entry | 100% of posts have outcome Valid · 0 posts with Not valid or Unclear | Test |
| S7 | Every question answerable by a person with no accounting knowledge | 0 questions containing any string from the company's chart of accounts | Test |

### N1–N5

| # | Requirement | Value |
|---|---|---|
| N1 | False alarms per 100 clean entries | **≤ 10** |
| N2 | Review time as a fraction of read-everything time | **≤ 10%** |
| N3 | Catch rate per injected error type | **≥ 90%** |
| N4 | — | **Not defined in the frozen plan.** Not invented here. |
| N5 | Confidence threshold anchor, if a scored gate is ever built | **0.975** |

**N3 caveat, recorded in the frozen plan:** constructed errors matched to
purpose-built detectors should score near 100%. It is a build-correctness check,
not evidence of product value.

---

## 7. Architecture — see ARCHITECTURE.md

**The design lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md), not here.** That
document owns components, interfaces, data flows, the technology choices that
affect the design, and the phase-by-phase build plan with entry and exit criteria.

This section keeps only the facts that are *status*, not design:

**Status is not decided here any more.** Every phase status in this project
lives in **[`docs/CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml)**, which is the one
machine-readable authority. This section is a human-readable rendering of it and
never overrides it. The row-by-row version, with the exit clauses met out of
total, is [`artifacts/phase_truth_table.md`](../artifacts/phase_truth_table.md).

| Phase | Status, per the control plane |
|---|---|
| 0 repository and safety | `PASSED` — re-verified 2026-08-10 by `gh api`, not by memory |
| 1 CI foundation | `PASSED` |
| 2 the Tally spine | `BLOCKED_ENVIRONMENT` — the build is done and has run against a real Tally (§21); the exit has never run |
| 3 the typed vertical slice | `PARTIALLY_VERIFIED` |
| 4 the no-match safety path | `PASSED` |
| 5 idempotency and reversal, the implementation | `PASSED` |
| 5-LIVE the run against a real Tally | `BLOCKED_ENVIRONMENT` — control-plane id `5-LIVE`, never run |
| 5B operational readiness | `PARTIALLY_VERIFIED` |
| 6 the first detector | `PARTIALLY_VERIFIED` — a verification agent is re-checking it |
| 7 the extraction adapter | `NOT_STARTED` |
| 8 widen to the frozen criteria | `NOT_STARTED` |
| 9 the proof track | `PARTIALLY_VERIFIED` |
| 10 operational hardening | `NOT_STARTED` |

| | |
|---|---|
| **Stopped by** | **nobody having run it.** `B-01` — the company `Demo Co` and its four ledgers must be made in the TallyPrime window — plus the acceptance run itself, which has still never been executed. Neither is waiting on a decision. |
| **No longer stopped by: THE LICENCE.** Superseded 2026-08-12 | The licence is **not a blocker any more.** The TallyPrime on the owner's machine is a **licensed free trial, not Educational** — and that is measured, not just attested: the §47 voucher is dated the **12th** of the month, and Educational mode accepts only the 1st, 2nd and 31st. A voucher that posted on the 12th could not have been posted by an Educational instance. `B-02` is satisfied for as long as the trial lasts. **No expiry date is recorded here** — see §48. |
| **What that unblocks, and what it does not** | The `2026-08-07` fixture in `tests/test_tally_contract.py` **can now run unmodified**. It has **not** been run. A licence being available and a test having passed are different sentences. The fixture is still never edited (§24). |
| **No longer stopped by** | the Windows VM. TallyPrime is installed and answering. The earlier "Windows VM + TallyPrime — NOT INSTALLED — the single blocker" is **superseded** (§17, §21). |

> **Audit note, 2026-08-10 — the largest single correction in this file.**
> A summary paragraph stood here until today. It said the first two phases were
> "complete", that the Tally spine's exit was licence-blocked, that **phases
> three to eight had "not started"**, that the proof track had been built and
> measured, and that the last one was deferred.
>
> **The claim about phases three to eight was false when it was written and
> stayed false for two days.** The typed vertical slice shipped in commit
> `3b83e30`, the no-match safety path in `c21127c`, the reversal hardening and
> the readiness gate in `192e514`, and the first detector in `3445992` — all
> merged, all on `main`.
>
> The line survived because it sat in a summary that nothing re-read, while the
> per-phase evidence piled up in §25 and §40 underneath it. That is the exact
> failure `CONTROL_PLANE.yaml` exists to stop — a status asserted in prose in one
> place, evidenced in another, and nothing comparing the two.
> `scripts/validate_project_truth.py` is now the thing that compares them.
>
> The word `COMPLETE` is gone too. It was never in the status vocabulary; it
> maps to `PASSED`.

Per-area evidence is in §8; the ordered next actions are in §19; the ranked list
of what is currently costing more than it should is in
[`BOTTLENECKS.md`](./BOTTLENECKS.md).

<details>
<summary>Original section 7 — SUPERSEDED 2026-08-08, kept only as a record of what was believed then</summary>

**Do not read the ticks below as current.** They were accurate at commit
`4cc290f` and are wrong now: steps 7, 13, 14, 15, 16 and 17 have all since been
built or measured. The current per-area state is §8; the current ordered work is
§19. This block is kept because deleting it would erase what the project believed
before Tally answered, and that belief is itself evidence.

### Architecture and A-to-Z build order

```
   person
     │  drops a bill (text / PDF / image)
     ▼
  #14 WEB APP ──► #15 EXTRACTION ADAPTER ──► #2 MEMORY (no model)
                                               │
                    ┌──────────────────────────┴──────────┐
                    │ match                    no match   │
                    ▼                          ▼          │
                propose            #3 DETECTORS + #9 RULES │
                    └──────────────┬───────────┘
                                   ▼
                      DECISION, in this order
                      1. NOT VALID → notify, do NOT post
                      2. UNCLEAR   → ask, record, re-evaluate from 1
                      3. VALID     → post, then notify
                                   │ valid only
                                   ▼
              #6 TALLY CONNECTOR ──► writes into THEIR Tally
                 marker on every voucher · bulk reverse always available
```

### Build order

```
 1 repository safety preflight          ✅ done
 2 git init + first commit              ✅ done
 3 new public repository                ✅ done
 4 dependencies + lockfile              ✅ done
 5 gate contract BEFORE workflows       ✅ done
 6 local guard BEFORE GitHub CI         ✅ done
 7 Tally connector            #6        ⬜ fake only, real = 0 lines
 8 memory index               #2        ⬜ code exists, not audited
 9 detectors + ranked queue   #3        ⬜ code exists, not audited
10 Indian rules corpus        #9        ⬜ thin
11 extraction adapter         #15       ⬜ stub only
12 web app                    #14       ⬜ 385 lines, fake-backed
13 synthetic generator        #1        ⬜ not started
14 scoring harness            #4        ⬜ not started
15 real audit-error taxonomy  #7        ⬜ not started
16 UK central-government ingest #5      ⬜ not started
17 cross-organisation test    #8        ⬜ not started
18 full integration                     ⬜
19 GitHub enforcement                   ✅ done
20 deliberate-failure verification      ✅ done (§18)
```

### The vertical slice — priority

```
typed entry
→ minimal web screen
→ Tally read
→ memory lookup
→ plain-language question
→ validity decision
→ marked Tally write
→ read-back
→ reversal
```

**Slice 1 has priority over expanding CI infrastructure.** OWNER DECISION,
2026-08-08: *"everything is now planning n finsihing mvp."* Steps 1–6, 19 and 20
are complete; CI is no longer the bottleneck and no further CI work is scheduled.

---

## 8. Implementation progress

Nothing is marked complete from YAML alone. Complete means a live run or test
proved it. Evidence cross-references §9 (gates), §10 (runs), §16 (security).

| Area | Status | Evidence | Next action |
|---|---|---|---|
| Repository identity | **VERIFIED** | GitHub API — §1 | none |
| Old repo untouched | **VERIFIED** | `pushed_at 2026-08-06T19:55:12Z`, head `924d0e0` — unchanged from baseline | re-check at each milestone |
| Source inventory | **VERIFIED** | 7,658 lines, 38 files (`accountant/`, including untracked `ingest/` and `taxonomy/`) — measured 2026-08-08 | — |
| Test inventory | **VERIFIED** | 7,050 lines, 14 test files; **682 tests collected** (`pytest --collect-only -q`, includes `ci/test_protection.py`) | — |
| Branch / SHA | **VERIFIED** | `main` `f7bf5d9`, 16 commits, **working tree not clean** — §1 | — |
| Tally connector — Protocol | **VERIFIED** | `accountant/tallyio/client.py`, 9 methods (the ninth, `backed_up`, added 2026-08-09 — see §40.4 defects 2 and 3) | — |
| Tally connector — real impl | **BUILT AND RUN AGAINST REAL TALLY** | `accountant/tallyio/real.py`, 63.5 KB. First real read HTTP 200, 1,594 bytes, 65 ms (§21). *The earlier evidence line "`grep … → nothing`" was drift and is superseded.* | fix `trial_balance()` derived head — [`BOTTLENECKS.md` A4](./BOTTLENECKS.md#a4--trial_balance-includes-a-derived-figure) |
| Tally read | **VERIFIED on real Tally** | companies, chart of accounts, vouchers, trial balance — all read (§21) | — |
| Tally write | **VERIFIED on real Tally** | Rs 5,000 posted, trial balance moved by exactly that amount (§21) | — |
| Idempotency (C5) | **UNVERIFIED on real Tally** | passes against fake. Guarded in code and tested since §47.10, after a real duplicate | **no longer needs a licence (§48)** — needs somebody to run it |
| Read-back (C6) | **VERIFIED on real Tally** | `read_by_operation_id()` returned the written voucher, then `None` after reversal (§21) | — |
| Reversal (#6.5) | **VERIFIED on real Tally** | `reverse_by_operation_id()` → `True`; trial balance restored to the exact prior paise (§21) | — |
| Memory index #2 | **CODE EXISTS, NOT AUDITED** | `accountant/memory/index.py` | audit vs #2.1–#2.7. **Company-local only; every customer is a permanent cold start** (§22) |
| Detectors #3 | **BUILT — MEASURED AGAINST THE PUBLISHED RECORD, AND THEY MISS MOST OF IT** | `accountant/detect/detectors.py`. Five different counts, and they are not the same number: **4 implemented · 3 active · 1 on the production path · 1 mapped to a published error type · 0 verified on real data**. Of 12 published error types, **0 VERIFIED, 2 PARTIAL, 10 UNCOVERED** (§22, [`TAXONOMY.md`](./TAXONOMY.md)) | proof work per uncovered type. *Audit note 2026-08-10: this row said "4 detectors, 2 of 12 covered". One word was doing the work of five counts.* |
| Rules corpus #9 | **NOT STARTED** | **VERIFIED absent** — `ls accountant/rules` → no such directory, 2026-08-08 | build |
| Extraction adapter #15 | **STUB ONLY** | `accountant/extract/adapter.py`, `TypedTextExtractor` | connect a backend |
| Web app #14 | **CODE EXISTS, FAKE-BACKED** | `accountant/web/app.py`, 385 lines, imports `FakeTally`. **Stdlib `http.server` only** — VERIFIED, and `pyproject.toml` still has `dependencies = []` with no web framework anywhere | swap client at M2 |
| Synthetic generator #1 | **BUILT, TEST-VERIFIED** | `accountant/generate/` — `book.py`, `inject.py`, `serialise.py`. `tests/test_generate.py`, 60 tests, one per acceptance criterion. Branch coverage 100%, 131/131 mutants killed, local run 2026-08-08 | — |
| Scoring harness #4 | **BUILT — N1 PASSES ON THREE SLICES AND FAILS ON ONE** | `accountant/score/` — `book.py`, `harness.py`, `report.py`, `calibration.py`. Aggregate **6.29** PASS · held-out **2.90** PASS · worst department **33.33** FAIL · historical **27.59**. Target ≤ 10 (§22) | owner decision `D-22` — which slice is the launch gate |
| Real error taxonomy #7 | **BUILT AND COMMITTED** in `6867ca9` | `accountant/taxonomy/` — `sources.py`, `findings.py`, `coverage.py`, `report.py`. 5 sources, 12 error types, `uncovered_count() == 10`. Written up in [`TAXONOMY.md`](./TAXONOMY.md) | none. *Audit note 2026-08-10: this row said "untracked in git" and "commit it". It was committed two days ago.* |
| UK government ingest #5 | **BUILT AND COMMITTED** in `6867ca9` | `accountant/ingest/` — `sources.py`, `fetch.py`, `spend.py`, `crossorg.py`, `report.py`, plus 7 real department fixtures | none. *Audit note 2026-08-10: this row said "untracked in git".* |
| Cross-organisation test #8 | **MEASURED — the answer is 0%** | 16,011 rows, 30 department pairs; within-department best 53.08%, cross-department 0.00% on 29 of 30 (§22) | none — the question is answered |
| CI contract | **VERIFIED** | `ci/gates.toml`, 20 gates; `ci/gate_names.lock`; 18 contract tests pass | none |
| Local guard | **VERIFIED** | `scripts/guards` 169 lines, 12 checks, staged mode 0.08s; hook at `.git/hooks/pre-commit` rejected a bad commit | none |
| `pr-fast` | **VERIFIED** | run `31236026164`, 26s, all steps success | none |
| `pr-full` | **VERIFIED** | PR #12, 113s, all steps success | none |
| `ci-gate` | **VERIFIED** | PR #12, 8s, success; refused a red PR (§18) | none |
| Nightly `full.yml` | **VERIFIED** | run `31237228028`, `event=schedule`, 7/7 jobs success | none |
| Watchdog | **VERIFIED** | run `31238866032`, `event=schedule`, 2/2 jobs success | none |
| actionlint | **VERIFIED** | ran in `pr-fast` (1s) and in nightly `workflow-checks` | remove Docker path from `full.yml` (§11) |
| zizmor | **VERIFIED** | ran in `pr-fast` (0s) and nightly | none |
| bandit | **VERIFIED** | `security-scan` green in `pr-fast` and nightly `security` | none |
| pip-audit | **VERIFIED** | `dependency-audit` 16s, green | none |
| Mutation | **VERIFIED** | 267 mutants, 267 unique IDs, 0 missing, 0 duplicate, all terminal, 1 survivor, **99.63%** | none |
| Coverage | **VERIFIED** | **95%** total, 779 statements, 29 missed; diff-cover ≥ 90 green | none |
| Build + package | **VERIFIED** | `package-build` 2s, `package-metadata` 0s, both green | none |
| Claude integration | **NOT ACTIVE** | `claude.yml` registered `active`; runs show `conclusion: skipped` — the `if:` guard is doing its job. `ANTHROPIC_API_KEY` is **not set** | owner: add secret + install app, or leave off |

---

## 9. CI design

```
local accelerator          scripts/guards, 0.08s staged
  → one fast PR worker     pr-fast, every push
  → full authoritative     pr-full, on the ready-to-merge label
  → deterministic aggregate ci-gate, the one required check
```

**Rules, all OWNER DECISION, all VERIFIED in code:**

- Every gate is mandatory **before merge**. Not every gate runs on every push.
- A green fast phase alone is **never** sufficient to merge.
- `ci-gate` evaluates the **exact commit** being merged (`strict_required_status_checks_policy: true`).
- Missing, stale, skipped, cancelled, failed or incomplete evidence **fails closed**.
- **A job skipped by `if:` reports Success to GitHub's required checks.** So a conditional job must never be the only required protection. `ci-gate` runs with `if: always()` and inspects `needs.*.result` itself:

```python
# ci/check_aggregate.py:30-32
PASSING = frozenset({"success"})
EXPLICIT_FAILURE = frozenset({"failure", "cancelled", "timed_out"})
NO_EVIDENCE = frozenset({"skipped", ""})  # treated as FAILURE
```

### The 20 gates — complete, listed once

| # | Gate | Command | Thr | Jobs | Artifact |
|---|---|---|---|---|---|
| 1 | `lockfile` | `uv lock --check` | — | pr-fast | none |
| 2 | `lint` | `ruff check .` | — | pr-fast | none |
| 3 | `format` | `ruff format --check .` | — | pr-fast | none |
| 4 | `typecheck` | `pyright` | — | pr-fast | none |
| 5 | `gate-contract` | `pytest tests/test_gate_contract.py -q` | — | pr-fast | none |
| 6 | `changed-tests` | `pytest --testmon -n auto --cov --cov-report=xml` | — | pr-fast | coverage.xml |
| 7 | `changed-coverage` | `diff-cover coverage.xml --compare-branch=origin/main --fail-under=90` | **90** | pr-fast | coverage.xml |
| 8 | `cached-mutation` | `pytest --gremlins --gremlin-parallel --gremlin-cache` | **90** | pr-full, mutation | gremlins.json |
| 9 | `full-tests` | `pytest -n auto` | — | pr-full, full-tests | junit.xml |
| 10 | `full-coverage` | `coverage run -m pytest && coverage report` | **90** | pr-full, full-tests | coverage.xml |
| 11 | `dependency-audit` | `pip-audit --strict --progress-spinner off -r requirements-audit.txt` | — | pr-full, security | pip-audit.json |
| 12 | `security-scan` | `bandit -r accountant --severity-level low --confidence-level low` | — | pr-fast, security | bandit.json |
| 13 | `package-build` | `python -m build` | — | pr-full, build | dist |
| 14 | `package-metadata` | `twine check --strict dist/*` | — | pr-full, build | dist |
| 15 | `full-mutation` | `pytest --gremlins --gremlin-parallel --gremlin-report=json` | **90** | pr-full, mutation | gremlins.json |
| 16 | `mutation-accounting` | `python ci/check_mutation.py` | — | pr-full, mutation | gremlins.json |
| 17 | `workflow-lint` | `actionlint` | — | pr-fast, workflow-checks | none |
| 18 | `workflow-security` | `zizmor --persona=pedantic .` | — | pr-fast, workflow-checks | zizmor.json |
| 19 | `no-stub-jobs` | `python ci/check_stubs.py` | — | pr-fast, workflow-checks | none |
| 20 | `ci-gate` | `python ci/check_aggregate.py` | — | ci-gate | none |

All 20: `required = true`, `status = "active"`, `failure_behaviour = "block"`,
`owner = tanveersidhu`. VERIFIED by reading `ci/gates.toml`.

**Gate 8 `cached-mutation` is PARKED**, reason recorded in the contract: a cached
`pytest-gremlins` verdict carries **no `selected_tests`**, so a cached mutant is
indistinguishable from one nothing ran against, and `ci/check_mutation.py`
correctly reports `FAIL_INCOMPLETE`. Observed twice on real runs. `--gremlin-cache`
is not passed on any authoritative path; the restore and save steps remain, so
re-enabling is a one-flag change. Installed `pytest-gremlins` is **1.9.0**, which
is the latest on PyPI — VERIFIED, no upgrade available.

### Contract meta

```toml
repository         = "Intellora-ai/accountant-dad-core"
required_pr_check  = "pr-fast"
required_mq_check  = "ci-gate"
non_gate_workflows = ["claude.yml"]
non_gate_jobs      = ["nightly-report", "nightly-watchdog", "ruleset-drift"]
coverage_core      = "pytrace"
```

### Workflows — all four, complete

| File | Triggers | Top-level permissions | Concurrency |
|---|---|---|---|
| `pr-fast.yml` | `pull_request` [opened, reopened, synchronize, labeled, unlabeled, ready_for_review, converted_to_draft] · `merge_group` | `contents: read` | `pr-fast-${{pr.number \|\| run_id}}`, cancel-in-progress **true** |
| `full.yml` | `merge_group` · `schedule: 0 2 * * *` · `workflow_dispatch` | `contents: read` | `ci-${{workflow}}-${{ref}}`, cancel **true** |
| `watchdog.yml` | `schedule: 0 3 * * *` · `workflow_dispatch` | `contents: read` | `watchdog-${{ref}}`, cancel **false** |
| `claude.yml` | `issue_comment` [created] · `pull_request_review_comment` [created] | `contents: read` | `claude-${{issue \|\| pr}}`, cancel **false** |

**`pr-fast.yml` env:** `COVERAGE_CORE: pytrace`, `FORCE_COLOR: "1"`.

**Jobs, runners and timeouts.** All `runs-on: ubuntu-24.04`.

| Workflow | Job | Timeout | Condition / job permissions |
|---|---|---|---|
| pr-fast | `pr-fast` | 15 | — |
| pr-fast | `pr-full` | 30 | `needs: pr-fast`; `if: merge_group \|\| draft == false \|\| contains(labels, 'ready-to-merge')` |
| pr-fast | `ci-gate` | 10 | `if: always()`, `needs: [pr-fast, pr-full]`, `--phase fast\|full` |
| full | `full-tests` | 20 | — |
| full | `security` | 15 | — |
| full | `build` | 15 | — |
| full | `workflow-checks` | 10 | — |
| full | `mutation` | 30 | — |
| full | `ci-gate` | 10 | `--phase nightly` |
| full | `nightly-report` | — | `contents: read`, **`issues: write`** — the only place that permission exists in `full.yml` |
| watchdog | `nightly-watchdog` | 10 | `contents: read`, `actions: read`, `issues: write` |
| watchdog | `ruleset-drift` | 10 | `contents: read`, `issues: write` |
| claude | `claude` | 30 | `contents: write`, `pull-requests: write`, `issues: read`; `if:` comment contains `@claude` **AND** `author_association ∈ {OWNER, MEMBER, COLLABORATOR}` |

**The `claude.yml` author check is not decoration.** Without it, anyone on the
internet could spend the API key and drive a write-permissioned job by commenting
on a public repo.

### Every third-party action, pinned to a 40-char SHA

```
actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1              v7.0.1
astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9            v9.0.0
actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a       v7.0.1
actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9         v6.1.0
actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9            v6.1.0
rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca              v1.7.12  (full.yml ONLY — see §11)
anthropics/claude-code-action@1623c36729ac1cd5895198cded705a287de7db79 v1
```

`ci/check_stubs.py` enforces the SHA pin, the presence of a timeout, and the
absence of placeholder jobs. VERIFIED — it runs in `pr-fast`.

### Cache keys

```
uv         cache-suffix: ${{ runner.os }}-${{ hashFiles('uv.lock') }}
gremlins   gremlins-${{ runner.os }}-${{ env.COVERAGE_CORE }}-${{ hashFiles('uv.lock') }}-${{ github.sha }}
```

**The commit SHA is in the gremlins key and there are no `restore-keys`**, so a
verdict computed for one commit can never be reused for another. `COVERAGE_CORE`
is in the key too, so a verdict computed on a broken core is never reused on a
working one. OWNER DECISION: *"Never reuse mutation or security evidence for a
different commit or merge candidate."*

### Toolchain and versions

| Responsibility | Tool | Constraint |
|---|---|---|
| install / lock / cache | `uv` | — |
| lint **and** format | `ruff` | `>=0.16` |
| type check (strict, source **and** tests) | `pyright` | `>=1.1.411` |
| test runner | `pytest` | `>=8.0` |
| parallel tests | `pytest-xdist` | `>=3.8` |
| changed-test selection | `pytest-testmon` | `>=2.2` |
| coverage | `coverage` + `pytest-cov` | `>=7.15`, `>=7.0` |
| changed-line coverage | `diff-cover` | `>=10.4` |
| mutation | `pytest-gremlins` | `>=1.9` (installed 1.9.0 = latest) |
| dependency CVEs | `pip-audit` | `>=2.10` |
| Python security scan | `bandit` | `>=1.9` |
| workflow security | `zizmor` | `>=1.29` |
| commit hook runner | `prek` | `>=0.4` |
| packaging | `build` + `twine` | `>=1.5`, `>=7.0` |
| workflow syntax lint | `actionlint` | pinned **v1.7.12**, SHA-256 verified |

**Runtime dependencies: `dependencies = []`.** Zero. VERIFIED in `pyproject.toml`.
Python pinned to **3.14** via `.python-version` — added because CI was silently
resolving 3.12 while all measurements were taken on 3.14.

### Repository CI scripts

| File | Lines | Purpose |
|---|---|---|
| `ci/check_aggregate.py` | 155 | the phase-aware fail-closed aggregate |
| `ci/check_mutation.py` | 182 | mutation accounting; `FAIL_INCOMPLETE` |
| `ci/check_ruleset.py` | 198 | **read-only** branch-protection drift audit, 9 checks |
| `ci/check_stubs.py` | 185 | placeholder / SHA-pin / timeout scanner |
| `ci/report_nightly.py` | 257 | deduplicated, self-closing failure issue |
| `ci/report_runtimes.py` | 154 | p50/p95 from the Actions API |
| `ci/test_protection.py` | 218 | 10 live tamper tests against the real ruleset |
| `scripts/guards` | 169 | the local accelerator, 12 checks |
| `scripts/install-actionlint` | 147 | pinned v1.7.12, SHA-256 verified |
| `scripts/setup` | 77 | `uv sync` + actionlint + `prek install` |

**`scripts/guards` is an accelerator, not a gate.** Everything it runs, CI runs
again and blocks on, so `git commit --no-verify` weakens nothing. This is stated
in the script's own output, in `README.md` and in `.pre-commit-config.yaml`.

---

## 10. CI evidence

Every row below is **observed and passed** unless marked otherwise.

### Latest full PR cycle — PR #12, merged `2026-08-08T02:58:25Z`

| Job | Result | Duration |
|---|---|---|
| `pr-fast` | **success** | 31s |
| `pr-full` | **success** | 113s |
| `ci-gate` | **success** | 8s |

`mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`.

**`pr-fast` step timings** — run `31236026164`, commit tested `d765226`:

```
Set up job                              2s
checkout                                1s
install uv                              4s
sync dependencies from the lockfile     2s   ← gate 1 lockfile (uv sync --frozen)
lint                                    0s   ← gate 2
format                                  0s   ← gate 3
typecheck                               3s   ← gate 4
gate-contract                           2s   ← gate 5
no-stub-jobs                            0s   ← gate 19
install actionlint                      0s     (not a gate — the delivery)
workflow-lint                           1s   ← gate 17
workflow-security                       0s   ← gate 18
security-scan                           0s   ← gate 12
changed-tests                           8s   ← gate 6
changed-coverage                        1s   ← gate 7
reproducibility record                  0s
evidence                                2s
TOTAL                                  26s
```

**`pr-full` step timings** — same PR:

```
full-tests                              5s   ← gate 9
full-coverage                          13s   ← gate 10
dependency-audit                       16s   ← gate 11
package-build                           2s   ← gate 13
package-metadata                        0s   ← gate 14
restore the gremlins cache              1s
cache telemetry                         0s
full-mutation                          64s   ← gate 15
mutation-accounting                     1s   ← gate 16
TOTAL                                 113s
```

### Nightly — run `31237228028`, `event=schedule`, **success**

Started `2026-08-08T03:27:17Z`, ended `03:29:18Z` ≈ **121s**.

```
full-tests        success  30s
security          success  27s
build             success  15s
workflow-checks   success  39s
mutation          success  85s
ci-gate           success  12s   (--phase nightly)
nightly-report    success  13s
```

### Watchdog — run `31238866032`, `event=schedule`, **success**

Started `2026-08-08T04:10:49Z`. `nightly-watchdog` success, `ruleset-drift` success.

### Quality results

```
tests           242 passed
coverage        95%  (779 statements, 29 missed)
diff coverage   ≥ 90, green
mutation        267 mutants · 267 unique IDs · 0 missing · 0 duplicate
                all terminal · 1 survivor · 99.63% · fewest tests per mutant: 1
security        bandit LOW/LOW clean · pip-audit no advisories
build           sdist + wheel built · twine --strict clean
workflow-lint   clean · workflow-security clean
failed jobs     none in the last full cycle or the nightly
```

### Runtime history

| Phase | Measured |
|---|---|
| `pr-fast` before A6 | ~21s |
| `pr-fast` with the actionlint **Docker** action | **50s** |
| `pr-fast` with the pinned **native binary** | **26–31s** |
| Full PR cycle | ~152s |
| Nightly `full.yml` | ~121s |
| p50 / p95 of `pr-fast` | 100s / 167s, **n=5, provisional** — from `ci/report_runtimes.py`, which reads the Actions API and builds no second database |

### Cache

`cache telemetry` runs in `pr-full` and writes hit/miss, restore key, matched key
and size to the job summary. **INFERRED:** because the gremlins key contains
`github.sha` and has no `restore-keys`, every new commit is a deliberate miss.
Not yet read off a specific run — **UNVERIFIED** as a recorded hit/miss number.

### Nightly issue reporter

Issues **#5, #6, #7, #8, #9** — all titled "Nightly verification is failing",
all `CLOSED`. VERIFIED via `gh issue list`. The reporter opened them during
forced-failure testing and closed them when the nightly passed, which is the
dedup and self-closing behaviour working.

### Merge history

| PR | Title | Merged |
|---|---|---|
| #1 | CI: first measurement run | 2026-08-07T14:24:53Z |
| #2 | CI: one required check on both triggers, mutation off the PR path | 2026-08-07T18:24:49Z |
| #10 | CI hardening: fail closed | 2026-08-08T01:29:14Z |
| #11 | *probe: code_coverage ruleset rule* | **closed unmerged** — see §13 |
| #12 | A6: workflow-lint and workflow-security on the PR path | 2026-08-08T02:58:25Z |

---

## 11. The actionlint Docker cost

**VERIFIED, measured on run `31234...` (PR #12, first push):**

```
workflow-lint (the check itself)          ≈  1s
Build rhysd/actionlint@914e7df (Docker)   ≈ 25s
pr-fast                                   21s → 50s
```

`rhysd/actionlint` is a Docker action, so GitHub rebuilds its container on every
push. **The delivery mechanism cost 25× what the check costs**, against a stated
speed priority.

**Fix applied to `pr-fast.yml`, VERIFIED:**

```yaml
- name: install actionlint
  run: ./scripts/install-actionlint      # pinned v1.7.12, SHA-256 verified
- name: workflow-lint
  run: ./.tools/actionlint -color
```

Result: **`pr-fast` 50s → 26s. 24 seconds removed.** No Docker build step remains
in `pr-fast`. Local measurement: install 3.5s cold, run 0.005s. **No cache step
was added** — 3.5s does not justify one, and that is a measurement, not a guess.

**Gate semantics re-proved locally before the change shipped:**

```
template injection in a workflow  → actionlint exit 1
insecure workflow                 → zizmor exit 14
actionlint binary missing         → exit 127, step fails
binary tampered (bytes appended)  → "hash does not match / re-downloading rather than trusting it"
clean workflows                   → exit 0
```

### Still open — DEFERRED

**`full.yml` line 169 still uses the Docker action.** That is two installation
mechanisms for one tool, and the Docker path adds a registry dependency that the
checksum-verified binary does not have. 25s on a nightly costs nothing, so this is
**not urgent**, but it is **not done**. It requires an owner yes for a `.github`
edit and has not been given.

Source: [actionlint install docs](https://github.com/rhysd/actionlint/blob/main/docs/install.md)

---

## 12. Nightly scheduling

### Corrected fact — the earlier "zero runs" statement is superseded

**VERIFIED 2026-08-08:** both scheduled workflows fired and passed.

```
full     run 31237228028  event=schedule  03:27:17Z  success  (cron slot 02:00, 87 min late)
watchdog run 31238866032  event=schedule  04:10:49Z  success  (cron slot 03:00, 70 min late)
```

`0 2 * * *` is **02:00 UTC / 07:30 IST**. The delay is exactly what GitHub
documents.

### What GitHub actually says — VERIFIED, fetched 2026-08-08

> *"The `schedule` event can be delayed during periods of high loads of GitHub Actions workflow runs."*
> *"If the load is sufficiently high enough, some queued jobs may be **dropped**. To decrease the chance of delay, schedule your workflow to run at a different time of the hour."*
> *"In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days."*

Source: [Events that trigger workflows — schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

### The structural limit — INFERRED, and it stands

**A watchdog hosted on the same scheduler cannot guarantee the scheduler that runs
it.** If GitHub drops schedules, it drops the watchdog too. `watchdog.yml` records
this in its own header. Only an off-platform ping closes it.

### Plan — DEFERRED, owner-level

```
keep the native schedule as a BACKUP only
move it off minute 00
add workflow_dispatch with a heartbeat_id input
add an independent EXTERNAL scheduler that dispatches via the API
send a unique heartbeat ID naming the expected UTC slot
verify the matching run appears, then verify it completes
alert on missing / failed / cancelled / timed-out
deduplicate issues by heartbeat ID
use a narrowly scoped token or GitHub App: dispatch + read runs ONLY
never grant the scheduler or Claude ruleset or branch-protection administration
```

**Blocked on the owner:** the external scheduler account (Cloudflare Cron,
cron-job.org, Better Stack or similar) and a token with `Actions: write`.
Claude's token has `Actions: read`, so `gh workflow run` returns
`403 Resource not accessible by personal access token` — VERIFIED, retested twice.

**Note:** `report_nightly.py` already deduplicates by label plus a reconcile pass
that closes all but the lowest-numbered open issue, so a native run and an
external dispatch landing together cannot produce two issues. VERIFIED by the
five closed issues in §10.

---

## 13. GitHub Code Quality and native coverage

### The experiment — VERIFIED

The owner enabled the native `code_coverage` ruleset rule with
`minimum_coverage: 90`. A disposable probe PR (**#11**) was opened with no
coverage data uploaded to Code Quality.

**Result:**

```
mergeStateStatus: CLEAN
mergeable:        MERGEABLE
checks present:   pr-fast, pr-full, ci-gate     ← no coverage check ever appeared
```

**The rule did not block. It was not fail-closed for this repository.** The owner
removed it the same day; the live ruleset now has 4 rules and no `code_coverage`.
It was **never one of the 20 deterministic gates** and its removal changed the
count by zero.

### Why it could not work here — VERIFIED

```
$ gh api repos/Intellora-ai/accountant-dad-core --jq .owner.type
"User"
```

GitHub Code Quality is documented as available for **organization-owned**
repositories on **Team or Enterprise Cloud**. This is a **user-owned free public**
repo. Two levels short.
Source: [About code quality](https://docs.github.com/en/code-security/concepts/about-code-quality)

`coverage.xml` uploaded through `actions/upload-artifact` is a downloadable file.
It is **not** the same as registering coverage with Code Quality, which uses
`actions/upload-code-coverage` and a `code-quality: write` permission.
Source: [Set up code coverage](https://docs.github.com/en/code-security/how-tos/maintain-quality-code/set-up-code-coverage)

**REPORTED, not independently fetched:** GitHub documents that a coverage
threshold value of `0` means the threshold is **disabled**. If accurate, the
owner's intended "zero drop allowed" is not expressible in that field at all —
which matches the observed API value `max_coverage_drop: null` after they entered
`0`. Marked as reported because this line came via a third party, not my own read
of the page.
Source: [Restrict code coverage](https://docs.github.com/en/code-security/how-tos/maintain-quality-code/restrict-code-coverage)

### Current decision — OWNER DECISION

```
repository-controlled coverage gates stay authoritative  (gates 7 and 10, threshold 90)
do NOT build around Code Quality
do NOT treat any native rule as protection without a proven upload AND a proven no-data failure test
```

---

## 14. Quality-decay ratchet — DEFERRED

### The gap, stated honestly

Every gate holds a **floor**. None holds a **ratchet**. Measured 2026-08-08:

```
coverage   95.00 → 90.00 floor  =  5.00 points can vanish with every gate green
mutation   99.63 → 90.00 floor  =  9.63 points can vanish with every gate green
```

The gates are not malfunctioning. They answer the question they were given
("is it ≥ 90?") correctly every time. They were never asked "are you worse than
yesterday?"

### Why it is not being built — OWNER DECISION

```
repository is one day old
one committer
ZERO observed decay events — the risk was reasoned, not measured
the product is not built; Slice 1 has not started
the scores are already recorded in every run
a ratchet pinned to today's numbers would fire on legitimate work
  during Slices 1-6, which is Risk #4 in the frozen plan: alert fatigue
```

```
Do not build baseline regression protection now.
Do not add gate 21.
Keep the problem statement as a DEFERRED risk.

Revisit ONLY when one of these occurs:
  1. one real quality drop is observed, or
  2. a second person starts committing, or
  3. the codebase stabilises after Slice 6.
```

**Do not re-raise this as an active item unless a trigger fires.** The full
problem statement is preserved outside the repo and is not lost.

---

## 15. Claude Code operating rules

OWNER DECISION, 2026-08-08.

**Before asking anything:**
```
1. search the official documentation
2. inspect the repository and live GitHub state
3. check the current run, logs, API response or error
4. try the smallest safe implementation
5. run the relevant tests and validation
6. report result, evidence, source links, remaining uncertainty
```

**Decide autonomously:** reversible technical choices, filenames, YAML shape, test
structure, which command to run, which doc page to read, routine debugging.

**Ask only for:** owner policy that is genuinely unset · credentials, secrets,
billing, account ownership, external permissions · irreversible actions ·
anything changing frozen scope, thresholds, gate names or safety rules · a real
contradiction that survives documentation, inspection and measurement.

**Never:**
```
weaken a threshold          delete a test or a mutant
turn a failure into a skip  hide a failure
modify rulesets             modify branch protection
hold credentials capable of changing protection or bypassing it
claim completion without live evidence
```

**May:** inspect, fix, report, push, label, attempt a merge.
**GitHub's deterministic checks decide whether the merge is allowed. Never Claude.**

Source: [Claude Code documentation](https://code.claude.com/docs/en)

---

## 16. Security model

### Claude's identity — VERIFIED

A fine-grained PAT scoped to this repository alone.

| Permission | Level | Why |
|---|---|---|
| Metadata | Read | mandatory |
| Contents | Read + write | push non-protected branches |
| Pull requests | Read + write | open PRs, apply the label, attempt merge |
| Checks | Read | read gate results |
| Actions | **Read** | read runs and artifacts. **Cannot dispatch** — `403` |
| Issues | Read + write | watchdog and drift audit open issues |
| **Administration** | **No access** | **this is the one that matters** |
| Everything else | No access | including Secrets, Variables, Environments, Code quality, Deployments, Webhooks, Pages, Packages |

**Never given to Claude:** the owner's password, an owner PAT, any
repository-administration token, control of the owner's browser session, or any
secret capable of changing protection.

### Live tamper tests — VERIFIED, all six refused

| Attempt | GitHub's answer |
|---|---|
| disable the ruleset | `Resource not accessible by personal access token` |
| delete the ruleset | `Resource not accessible by personal access token` |
| enable force-push | `Resource not accessible by personal access token` |
| push directly to `main` | `Changes must be made through a pull request` |
| merge a red PR | `the base branch policy prohibits the merge` |
| merge with `--admin` | `GraphQL: Repository rule violations found` |

`ci/test_protection.py` runs 10 such tests against the **real** ruleset and
asserts refusal. It never fakes a denial.

### Ruleset `20557129` "main protection" — VERIFIED, complete

```json
{
  "id": 20557129,
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "allowed_merge_methods": ["squash", "merge", "rebase"],
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "required_reviewers": [] } },
    { "type": "required_status_checks", "parameters": {
        "do_not_enforce_on_create": false,
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "pr-fast", "integration_id": 15368 },
          { "context": "ci-gate", "integration_id": 15368 } ] } }
  ]
}
```

**CORRECTED 2026-08-10. The snapshot above previously showed
`{ "context": "pr-fast" }` with no `integration_id`.** That was true when it
was written and is no longer true. Re-read live at **2026-08-10T06:59:21Z**,
both required contexts carry `integration_id: 15368`, which
`gh api apps/github-actions` resolves to GitHub Actions.

```
ruleset updated_at, as recorded here before   2026-08-08T07:53:43.446+05:30
ruleset updated_at, read 2026-08-10T06:59:21Z 2026-08-10T12:21:46.474+05:30
                                              ( = 2026-08-10T06:51:46Z )
```

The old snapshot is kept in this paragraph rather than erased, because an
unpinned required check is a real finding and a struck record is evidence in a
way a vanished one is not. This document does not claim to know who applied
the pin; it records that the ruleset changed at that instant and what the
state is now.

**Why the pin is the control.** An unpinned required context can be satisfied
by any actor holding `commit statuses: write` — the check name is matched, no
workflow runs, and the branch goes green. Pinning binds the context to one
app id.

**The gap that is still open, and it is not the pin.** `ci/check_ruleset.py`
runs nine drift checks. At lines 111-122 it asserts the required *context
name* is present and that the strict policy is on. **It never inspects
`integration_id`.** If `pr-fast` were unpinned again, the audit would still
report clean. The pin has been applied and is not defended.
`HUMAN_ACTION_REQUIRED` — see
[`artifacts/codeant_integration.md`](../artifacts/codeant_integration.md)
§A.5.

**`bypass_actors: []` binds repository admins too.** `--admin` force-merge was
refused, which proves it rather than asserting it.
Source: [About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)

### The drift audit

`ci/check_ruleset.py` runs 9 checks and is **read-only by construction** — it
passes no HTTP method, so it can only GET. It may open an issue. **It never
repairs the ruleset.** An auditor that can fix what it audits is a second way for
protection to change quietly; repair needs the owner's separate admin identity.
Current result: **PASS, 9/9.**

### Secrets still requiring owner configuration

| Secret | Needed for | State |
|---|---|---|
| `ANTHROPIC_API_KEY` | `@claude` replies inside GitHub | **not set** — OWNER DECISION to drop it; the gates do not need it |
| Claude GitHub App | same | not installed |
| External scheduler token (`Actions: write`, dispatch + read runs only) | §12 | not created |

### The honest boundary

```
The repository can prevent Claude from weakening protection only if Claude is
never given owner/admin credentials or ruleset-write permissions.
```

A test cannot prove every imaginable future compromise. It can prove that the
actual Claude identity is denied the actual operations today. That is the exact
scope of the guarantee, and it is what the six refusals above demonstrate.

---

## 17. Risks and open questions

| Risk / question | Status | Evidence | Owner action |
|---|---|---|---|
| Tally local HTTP has no auth model beyond network reachability | **KNOWN, structural — and now WIDER than loopback** | Tally runs in a Windows 11 ARM64 VM, so the Mac reaches it at `192.168.64.2:9000` over `bridge100`. **macOS `localhost` and guest `localhost` are different machines**, which is exactly why `TallyConfig` takes host and port. The traffic is plain HTTP with no auth and must stay on a private, trusted network. | none — `TallyConfig.is_loopback` exists so a caller or test can assert the tighter rule where it applies |
| Backup enforcement before writing | **VERIFIED against fake** | `CompanyNotBackedUp`, `client.py:66` | prove against real Tally |
| TallyPrime vs Tally.ERP 9 compatibility | **PARTLY RESOLVED** | TallyPrime **Release 7.0, Series A Release 7.0.0, Build 27974** is what answered (§21). Tally.ERP 9 remains UNVERIFIED. | decide whether ERP 9 is in scope at all |
| **Educational-mode voucher-date restriction** | **MEASURED — the current blocker** | `2026-08-07` REJECTED, `2026-08-31` ACCEPTED. `tests/test_tally_contract.py:53` posts on `2026-08-07`, so the client-fixture tests — PENDING_COUNT (19 by an AST count on 2026-08-10; the docs said 15) — cannot run unmodified. Educational mode does **not** block deletion — that theory was tested and disproven. | **buy a non-Educational licence** — [`BOTTLENECKS.md` A3](./BOTTLENECKS.md#a3--educational-mode-date-restriction-blocks-the-15-contract-tests) |
| **`trial_balance()` includes a derived figure** | **MEASURED, OPEN** | `Profit & Loss A/c` is Tally's derived closing figure, not a posting, so the raw sum is not zero. The two real ledgers cancel exactly. Reversal is unaffected — it compares the same dict before and after. | [`BOTTLENECKS.md` A4](./BOTTLENECKS.md#a4--trial_balance-includes-a-derived-figure) |
| **Detector coverage of real error types** | **MEASURED — 10 of 12 UNCOVERED** | §22 | proof work per uncovered type, [`BOTTLENECKS.md` A1](./BOTTLENECKS.md#a1--detectors-cover-2-of-12-published-real-error-types) |
| **N1 false-alarm rate** | **MEASURED — three slices pass, one fails** | aggregate 6.29 · held-out 2.90 · worst department 33.33 · historical 27.59. Target ≤ 10 (§22) | owner decision `D-22` |
| Third-party extraction quality | **UNVERIFIED** | no backend connected; stub only | the 95/100 bar (S2) decides which backend qualifies |
| Ruleset protection | **VERIFIED** | 6 refusals, 9/9 audit | none |
| Nightly scheduling | **VERIFIED working, delay documented** | runs `31237228028`, `31238866032` | external dispatch DEFERRED (§12) |
| External scheduler credentials | **OWNER-LEVEL, not configured** | Claude's token is `Actions: read` → 403 | create account + scoped token |
| actionlint installation | **VERIFIED in `pr-fast`; Docker duplication remains in `full.yml`** | §11 | give a yes for the `full.yml` edit, or leave it |
| Python 3.14 compatibility of xdist / testmon / gremlins | **VERIFIED by running** | 242 tests, 267 mutants, all green on 3.14 — **measured at commit `4cc290f`**; the suite is now 682 collected and has not been re-measured on CI | re-measure once `ingest/` and `taxonomy/` are committed |
| Mutation ID stability | **VERIFIED** | 267 mutants, 267 unique IDs, 0 missing, 0 duplicate, across runs | none |
| Bandit LOW/LOW noise | **VERIFIED clean** | `security-scan` green in both `pr-fast` and nightly | none |
| Code Quality eligibility | **VERIFIED unavailable** | `owner.type == "User"` (§13) | none |
| **Windows VM + TallyPrime** | **RESOLVED — superseded** | TallyPrime 7.0 is installed in a Windows 11 ARM64 VM under UTM and answered a real request (§21). The earlier "NOT INSTALLED — the single blocker" line is no longer true. | none |
| Windows guest-agent visibility | **UNDERSTOOD** | `utmctl exec` / `utmctl file push\|pull` run the agent as **SYSTEM in SESSION 0**, so any GUI it launches is **invisible on the owner's desktop** while still reporting success. A scheduled task with `/IT` is what runs something the owner can see. | none — [`BOTTLENECKS.md` A7](./BOTTLENECKS.md#a7--windows-guest-agent-work-is-invisible-in-session-0) |
| First paying customer | **NOT IDENTIFIED** | frozen plan, open item 4 | owner |
| Real error frequency | **UNRESOLVED** | needs real books; no number invented. `accountant/taxonomy` deliberately never estimates how often an error type occurs — the published record does not support such a number. | owner |
| Cross-organisation generalisation | **MEASURED — mappings do NOT transfer** | 16,011 real UK government rows, 30 department pairs; within-department best 53.08%, cross-department **0.00% on 29 of 30** (§22) | none — the design question is answered. Every customer is a permanent cold start; a pooled model is wasted effort. |
| Quality decay | **DEFERRED** | §14 | revisit only on a trigger |

---

## 18. Verification checklist

**No row is marked passed without a command, run URL, log line or artifact.**

| Deliberate failure | Expected | State |
|---|---|---|
| break ruff | `pr-fast` fails | **PASSED** — observed |
| break typing | `pr-fast` fails | **PASSED** — observed |
| break a test | `pr-fast` fails | **PASSED** — observed |
| coverage below 90 | fails | **PASSED** — observed |
| surviving mutant below threshold | fails | **PASSED** — observed |
| missing gate result | fails | **PASSED** — observed |
| LOW security finding | fails | **PASSED** — observed |
| broken workflow file | fails | **PASSED** — observed |
| mutation without `COVERAGE_CORE` | `FAIL_INCOMPLETE`, `score_percent: null` | **PASSED** — observed |
| skipped required job | `ci-gate` fails | **PASSED** — `NO_EVIDENCE` includes `"skipped"`; asserted by `ci/check_aggregate.py` and its tests |
| cancelled job | `ci-gate` fails | **PASSED** — `EXPLICIT_FAILURE` includes `"cancelled"` |
| Claude attempts a red merge | GitHub refuses | **PASSED** — `the base branch policy prohibits the merge`; API `405 Repository rule violations found — 2 of 2 required status checks are failing` |
| Claude attempts `--admin` force merge | refused | **PASSED** — `GraphQL: Repository rule violations found` |
| direct push to `main` | refused | **PASSED** — `remote: - Changes must be made through a pull request` |
| Claude attempts ruleset modification | denied | **PASSED** — `403 Resource not accessible by personal access token`, three separate operations |
| actionlint binary missing | fails | **PASSED** — `exit 127` |
| actionlint checksum mismatch | fails / re-downloads | **PASSED** — `hash does not match ... re-downloading rather than trusting it` |
| template injection in a workflow | actionlint fails | **PASSED** — `exit 1`, flagged `github.event.issue.title` |
| insecure workflow | zizmor fails | **PASSED** — `exit 14` |
| Docker actionlint removed from `pr-fast` | measured | **PASSED** — 50s → 26s |
| Docker actionlint removed from `full.yml` | measured | **NOT DONE — DEFERRED** (§11) |
| failed nightly → one deduplicated issue | one issue, reused | **PASSED** — issues #5–#9, all closed by the reporter |
| missing nightly run → external alert | alert fires | **NOT BUILT — DEFERRED** (§12). The in-repo watchdog exists and passed, but cannot cover the case where GitHub drops the schedule that runs it. |

### Real Tally — added 2026-08-08, evidence in §21

| Deliberate action | Expected | State |
|---|---|---|
| one request reaches Tally from macOS | any response | **PASSED** — HTTP 200, 1,594 bytes, 65 ms, `192.168.64.2:9000` |
| read the chart of accounts | parses | **PASSED** — after the illegal-character-reference fix (`&#4;`) |
| read an empty company | zero vouchers, not an error | **PASSED** — after scoping voucher parsing to `BODY/DATA` |
| write one marked voucher | trial balance moves by exactly the amount | **PASSED** — `AD Test Expense` 168456 → 668456 paise |
| read it back by operation ID | the same voucher | **PASSED** |
| reverse it | `True` | **PASSED** |
| read back after reversal | `None` | **PASSED** |
| trial balance after reversal | exact prior value, in paise | **PASSED** — 668456 → 168456. **EXACT RESTORE True, VOUCHER GONE True.** |
| post the same operation ID again → no second voucher | `DuplicateOperation` | **NOT RUN** — licence-blocked (§21) |
| the client-fixture tests against the real client — count PENDING_COUNT (19 by an AST count on 2026-08-10; the docs said 15) | all pass | **NOT RUN** — stopped by the licence (§21). **The Tally spine's exit criterion.** |
| post a voucher dated outside the 1st/2nd/31st | Educational mode refuses | **PASSED as a measurement** — `2026-08-07` REJECTED, `2026-08-31` ACCEPTED. Not yet a live negative test in the suite. |
| trial balance sums to zero | zero | **FAILED — and correctly so.** `Profit & Loss A/c` is a derived figure, not a posting. The two real ledgers cancel exactly. [`BOTTLENECKS.md` A4](./BOTTLENECKS.md#a4--trial_balance-includes-a-derived-figure) |

---

## 19. A-to-Z next-action plan

Strict order. Each step is worthless until the one before it holds.

```
 1  finish and verify this document                            ← this task
 2  remove the Docker actionlint duplication from full.yml     DEFERRED, needs an owner yes
 3  re-measure pr-fast / nightly after step 2                  follows 2
 4  resolve the nightly trigger: external dispatch + monitor   DEFERRED, needs owner credentials
 5  record live nightly evidence                               ✅ ALREADY DONE — runs 31237228028, 31238866032
 6  do NOT build the quality-decay ratchet                     DEFERRED by decision (§14)

 ─── the real work starts here ───

 7  M0  install UTM → Windows on ARM → TallyPrime → enable its HTTP server   ✅ DONE (§21)
 8  M1  connect to a real Tally test company, read-only                      ✅ DONE
 9      one request returns one real company name                            ✅ DONE
10      read the chart of accounts and one voucher                           ✅ DONE
11  M2  write accountant/tallyio/real.py implementing the same 8-method Protocol  ✅ DONE
12      build the minimal memory lookup against real history                 ⬜
13      make one validity decision on a real entry                           ⬜
14      write ONE marked voucher into a real test company                    ✅ DONE
15      read it back by operation ID                                         ✅ DONE
16      post the same operation ID again → prove NO second voucher           ⬜ licence-blocked
17      reverse it                                                           ✅ DONE
18      prove the trial balance returns to its exact prior value, in paise    ✅ DONE
19  M3  point tests/test_tally_contract.py's client fixture at the real client
        every client-fixture test passes -> "works on real Tally" is satisfied
        count is PENDING_COUNT (19 by an AST count on 2026-08-10; the docs said 15)
        ⬜ BLOCKED — Educational mode rejects the 2026-08-07 test date

 ─── the current ordered work ───

20      OWNER: buy a non-Educational TallyPrime licence → unblocks 16 and 19
21      commit accountant/ingest/ and accountant/taxonomy/ — both are untracked
22      decide what trial_balance() does with derived heads (BOTTLENECKS A4)
23      attribute N1 = 27.59 per detector BEFORE changing any detector (BOTTLENECKS A2)
24      bootstrap memory from an existing company's own Tally history
        an empty memory for an existing company is a PRODUCT FAILURE (§22)
25  M4  the frontend — shape is an open owner decision (§20)
26  M5  widen through Slices 2-6 to the full frozen acceptance criteria
27      revisit deferred risks only when their triggers fire
```

**Why this order changed.** Step 7 is done. Tally is no longer the bottleneck —
it works end to end through the connector (§21). **The bottleneck moved twice in
one day:** first to a licence (steps 16 and 19), and then, more importantly, to
the product itself. §22 says the four detectors miss 10 of the 12 error types
auditors actually publish, and that N1 fails its target by 2.8x. Proving the
connector against 15 more tests does not move either number.

The full ranked list, with the smallest guard per class of defect, is in
[`BOTTLENECKS.md`](./BOTTLENECKS.md).

---

## 20. Final status

### What is definitely working

**All CI numbers below were measured at commit `4cc290f`. The repository is now
at `f7bf5d9` with 682 tests collected and two packages still untracked, so they
have NOT been re-measured.** The 20 gates themselves are unchanged — VERIFIED,
`ci/gates.toml`, 2026-08-08.

```
20 CI gates, all active, all blocking — proven by 8 deliberate failures
branch protection — 6 tamper attempts refused, 9/9 drift audit
pr-fast 26-31s · pr-full 113s · ci-gate 8s · full cycle ~152s
nightly full.yml and watchdog.yml — both fired on schedule and passed
242 tests · 95% coverage · 99.63% mutation, 1 survivor of 267   ← at 4cc290f
zero runtime dependencies · Python pinned to 3.14
the deduplicated self-closing nightly issue reporter
```

### What is only tested locally

```
the eight deliberate failures were reproduced locally as well as on CI
actionlint / zizmor negative cases: injection, missing binary, tampered binary
scripts/guards, 12 checks, staged mode 0.08s
the commit hook — installed, and observed rejecting a bad commit
```

### What is proven on GitHub

```
all 20 gates have now executed on GitHub at least once
  gates 1-7, 12, 17-19  in pr-fast
  gates 9-11, 13-16     in pr-full
  gate 20               ci-gate, both phases
  gates 17-19           again in the nightly workflow-checks job
  gate 8                PARKED, deliberately not executed
merge refusal, admin-force refusal, direct-push refusal, ruleset-write refusal
```

### What is proven on real Tally — added 2026-08-08

```
read: companies, chart of accounts, vouchers, trial balance
write: one marked voucher, trial balance moved by exactly the amount
read-back by operation ID
reversal: exact restore to the prior paise, voucher gone
```

Detail and exact numbers in §21.

### What is still unverified

```
idempotency on real Tally — the duplicate-operation-ID test is licence-blocked
the client-fixture tests against the real client — stopped by the licence
  count is PENDING_COUNT (19 by an AST count on 2026-08-10; the docs said 15)
Tally.ERP 9 — only TallyPrime 7.0 has answered
children #2, #3, #14, #15 against their own written acceptance criteria
S1-S7 — no product measurement has been taken
N2 and N3 — not yet measured on real data
the third-party extraction backend — none is connected
cache hit/miss as a recorded number from a specific run
```

### What is measured and FAILING — added 2026-08-08

```
N1, worst single department (DHSC)   33.33 per 100 clean entries, target <= 10
N1, aggregate                         6.29                       PASS
N1, held-out half                     2.90                       PASS
N1, MHCLG pre-calibration (HISTORY)  27.59
detector coverage of published error types:  0 VERIFIED, 2 PARTIAL, 10 UNCOVERED
detectors verified catching a real error:    0
```

**The coverage line is the number that matters most in this document.** Detail
in §22 and in [`TAXONOMY.md`](./TAXONOMY.md).

> *Audit note, 2026-08-10: this block read "N1 = 27.59 ... FAIL by 2.8x" and
> "2 of 12 covered". Both were stale — 27.59 is the pre-calibration MHCLG
> figure, and nothing is verified as covered.*

### What is intentionally deferred

```
the quality-decay ratchet / gate 21          §14 — revisit only on a trigger
external nightly dispatch and monitoring     §12 — needs owner credentials
removing the Docker actionlint from full.yml §11 — needs an owner yes
the mutation cache (gate 8)                  §9  — blocked upstream in pytest-gremlins
Code Quality and native coverage rules       §13 — unavailable on a user-owned repo
ANTHROPIC_API_KEY and the Claude GitHub App  §16 — the gates do not need them
the frontend's final shape                   §19 step 20 — owner deferred it
```

### The next highest-value action

**Superseded 2026-08-08.** The old answer — install UTM, Windows, TallyPrime — is
**done** (§21). One real request returned one real company name, a voucher was
written, read back and reversed to the exact prior paise.

**The new answer is uncomfortable and should not be softened.** The product's
four detectors were measured against what auditors actually publish, and they
cover **2 of 12** error types. `first_use`, `magnitude` and `gst_anomaly` map to
**nothing** in the published record, because all four detectors only catch
**changes from history** and real audit errors are **standing practices**. N1
fails its target by 2.8x. Those two numbers, not the connector, decide whether
this product is worth finishing.

The owner-blocked item is now a **non-Educational TallyPrime licence** (§21),
which unblocks the last two Phase 2 exit tests. It does not move either number
above.

**The existence of this document does not mean the project is complete.**

---

## 21. Tally — first real evidence

**All of this was measured on 2026-08-08. It is the first time any of this code
has touched a real Tally.**

### The environment

```
TallyPrime Release 7.0, Series A Release 7.0.0, Build 27974
EDUCATIONAL mode
Windows 11 ARM64, in a VM under UTM on macOS
```

**The network fact that `TallyConfig` exists for:**

```
Tally, from macOS      192.168.64.2:9000
the Mac, on bridge100  192.168.64.1
```

macOS `localhost` and guest `localhost` are **different machines**. This is
exactly why `TallyConfig` takes a host and a port rather than assuming
`localhost:9000` — see [`ARCHITECTURE.md` §3](./ARCHITECTURE.md#3-technology-choices-that-affect-the-design).
The traffic is plain HTTP with no authentication, so it must stay on a private,
trusted network.

**First real read:** HTTP **200**, **1,594 bytes**, **65 ms**.

### Three bugs, all found by real data, all fixed

Full entries, with the guard chosen per class of defect, are in
[`BOTTLENECKS.md` Part B](./BOTTLENECKS.md#part-b--resolved). Summary:

| # | What real Tally did | Why it broke us | Fix |
|---|---|---|---|
| 1 | emitted **invalid XML** — `<PARENT TYPE="String">&#4; Primary</PARENT>`, a reference to U+0004, which XML 1.0 forbids | one reserved ledger name (`Profit & Loss A/c`) made the **whole chart of accounts** unparseable | strip illegal numeric character references in `sanitise()`, before the bare-ampersand pass. Guard: unit test. |
| 2 | every response carries `<CMPINFO>…<VOUCHER>0</VOUCHER>` — a **count** | scanning the whole document for `VOUCHER` picked up that counter, so an **empty** company looked like a **corrupt export** | scope voucher parsing to `BODY/DATA`. Guard: the test fixture now emits the real `CMPINFO` shape, so it reproduces the failure. |
| 3 | rejected **seven** different delete shapes | Tally identifies a voucher for Alter/Cancel/Delete by a **`TAGNAME`/`TAGVALUE` attribute pair** (a TDL method name and its value), **not by child tags**. `REMOTEID` is a **sync-lineage** field: stamped on export so it looks like a handle, but a locally-imported voucher has no remote-index entry. | the working envelope, below. Guard: unit test plus the end-to-end proof. |

**The seven failures, recorded because the error messages are misleading:**

```
without REMOTEID                             Cannot delete unnamed object: VOUCHER!
with REMOTEID                                Voucher does not exist!
ACTION="Alter" + <ISDELETED>Yes</ISDELETED>  silently ignored
                                             altered=0  deleted=0  errors=0
```

The third is the dangerous one: Tally reported success and did nothing.

**The envelope that works:**

```xml
<VOUCHER DATE="2-Apr-2026" TAGNAME="Master ID" TAGVALUE="3"
         ACTION="Delete" VCHTYPE="Journal"></VOUCHER>
```

**Two date formats in one document.** The `DATE` **attribute** is `dd-MMM-yyyy`.
The `DATE` **child tag** is `yyyyMMdd`. Getting this wrong is silent.

Official sources, both fetched 2026-08-08:
- <https://help.tallysolutions.com/article/DeveloperReference/integration-capabilities/case_study_1.htm>
- <https://help.tallysolutions.com/article/DeveloperReference/faq/6191.html>

### Proven end to end, through the connector

```
trial balance BEFORE        {'AD Test Expense': 168456, ...}
write Rs 5,000
trial balance AFTER         {'AD Test Expense': 668456, ...}
reverse_by_operation_id()   True
read_by_operation_id()      None
trial balance RESTORED      {'AD Test Expense': 168456, ...}

EXACT RESTORE  True
VOUCHER GONE   True
```

This is the sentence in [`ARCHITECTURE.md` §6](./ARCHITECTURE.md#6-mvp-definition)
— *"one marked voucher appears in Tally, the voucher is read back, and reversal
restores the exact prior trial balance"* — observed rather than asserted.

### Still open

**`trial_balance()` includes `Profit & Loss A/c`.** That is Tally's **derived
closing figure, not a posting**, so the raw sum of the returned dict is not zero.
The two real ledgers cancel exactly. Reversal is unaffected, because it compares
the same dict before and after — equality, not a sum.
[`BOTTLENECKS.md` A4](./BOTTLENECKS.md#a4--trial_balance-includes-a-derived-figure).

### Owner-blocked

**Educational mode rejects voucher dates outside the 1st, 2nd and 31st.**
Measured: `2026-08-07` **REJECTED**, `2026-08-31` **ACCEPTED**. The 15
client-fixture tests in `tests/test_tally_contract.py` post on `2026-08-07`
(line 39), so they **cannot run unmodified**. A non-Educational licence is
required.

**Educational mode does NOT block deletion.** That theory was tested and
disproven, so it never blocked the reversal work.

Rewriting the test dates to fit the restriction is rejected: it would make the
suite green on a configuration nobody intends to ship on, and would delete the
only evidence that the restriction exists.
[`BOTTLENECKS.md` A3](./BOTTLENECKS.md#a3--educational-mode-date-restriction-blocks-the-15-contract-tests).

### The Windows guest-agent channel

```
utmctl exec              run a command in the guest
utmctl file push|pull    move files in and out
```

**The agent runs as SYSTEM in SESSION 0**, so any GUI it launches is **invisible
on the owner's desktop** while the call still returns success. A scheduled task
created with **`/IT`** is what runs something the owner can actually see. The
exit code is the one signal that cannot detect this.

---

## 22. Product quality — first measurements on real data

**Measured 2026-08-08. These are the first product numbers this project has ever
had, and they are worse than the targets. They are recorded exactly as measured.**

### The detectors cover none of the 12 published real error types, and are aimed at 2

```
published real error types                12
VERIFIED on real data                      0
PARTIAL  — a live detector is aimed at it  2
UNCOVERED                                 10
history-only reachable ceiling             4
```

The two PARTIAL types are `capital_expenditure_as_revenue` and
`revenue_expenditure_as_capital`. **PARTIAL is not covered.** It means a live
detector reads the field that type changes. Nothing in this repository shows
that detector catching a real instance, because no real ledger here carries a
labelled error.

The full matrix, one row per type, is **[`docs/TAXONOMY.md`](./TAXONOMY.md)**,
pinned by `tests/test_taxonomy_matrix.py`. It is not copied here.

> **Audit note, 2026-08-10.** This section was headed *"The detectors cover 2 of
> 12 published real error types"* and printed *"covered by current detectors: 2"*
> as verified truth. Measured on 2026-08-10,
> `taxonomy.coverage.status_counts()` returns **COVERED 0, PARTIAL 2,
> UNCOVERED 10**. The code never claimed 2 were covered; the prose did. Command
> to check: `.venv/bin/python -c "from accountant.taxonomy import coverage as c;
> print(dict(c.status_counts()))"`.

**`first_use`, `magnitude` and `gst_anomaly` map to NOTHING in the published
record.** `taxonomy.detectors_targeting_no_error_type()` returns all three.

**Why, in one sentence:** real audit errors are **standing practices**, and all
four detectors only catch **changes from history**. A wrong head used
consistently for years never changes, so nothing fires.

The ten uncovered types are **explicit backlog and proof work**, not shipped
capability:

```
balance_under_wrong_balance_sheet_head      object_head_incompatible_with_major_head
expenditure_exceeds_sanctioned_provision    parked_in_suspense_head
expenditure_netted_against_receipt          receipt_classified_as_wrong_type
expense_under_wrong_statement_head          related_party_not_identified
tax_credit_claimed_where_not_admissible     wrong_expense_head_within_same_section
```

`accountant/taxonomy` holds one `Proposal` per uncovered type. **A proposal is a
hypothesis, not a requirement** — do not write ten detectors off that table.
`accountant/taxonomy` also deliberately never estimates **how often** an error
type occurs: the published record does not support such a number, and an invented
one would quietly become the argument for keeping or dropping a detector.

[`BOTTLENECKS.md` A1](./BOTTLENECKS.md#a1--detectors-cover-2-of-12-published-real-error-types).

### N1 — three slices pass, one fails, and the old headline number is history

One number could not be acted on, so N1 is now reported four ways. All four are
in [`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) and in
`artifacts/detector_evidence.json`.

| slice | rate per 100 clean entries | target | verdict |
|---|---|---|---|
| aggregate, all 7 departments | 6.29 | ≤ 10 | PASS |
| held-out half only | 2.90 | ≤ 10 | PASS |
| worst single department (DHSC) | 33.33 | ≤ 10 | **FAIL** |
| MHCLG, pre-calibration — **historical** | 27.59 | ≤ 10 | FAIL |

**27.59 is history, not the current number.** It was the first N1 ever measured
on real data, taken on MHCLG only with the pre-calibration detectors. It is kept
because the improvement is only auditable if the starting point survives.
`tests/test_n1.py::test_the_number_this_started_from_is_still_reproducible` is
what keeps it honest.

**Which slice is the launch gate is an open owner decision, `D-22`.** The
aggregate says ship and the worst department says do not, and a customer
experiences their own book rather than an aggregate.

Two more facts that are easy to miss. The calibration half has **zero headroom**
— one more false alarm there flips it. And DBT has **zero clean entries**, so it
reports "not measured", which is not a pass either.

`accountant/score/harness.py` reports every slice as an explicit `PASS` or
`FAIL`. **Do not tune a threshold to make a number pass** — that moves the
measurement, not the product.

> **Audit note, 2026-08-10.** This section was headed *"N1 = 27.59 — FAILING"*
> and gave one number with the verdict *"FAIL by 2.8x"*. That number is still
> real but it is the pre-calibration MHCLG figure, and it was being read as the
> current state of the product two days after calibration moved it. Reproduction
> of all four figures by an independent measurement agent is **PENDING**.

### Cross-organisation transfer: 0.00%

```
real UK central-government rows          16,011
department pairs                             30
within-department, best                  53.08%
cross-department                          0.00%  on 29 of the 30 pairs
```

**Vendor→account mappings do not generalise.** This is an **answer**, not a
defect, and it removes work rather than adding it:

```
memory is COMPANY-LOCAL ONLY
every customer is a PERMANENT COLD START
a pooled model across customers is WASTED EFFORT
```

**The product invariant this creates.** Because every customer is a cold start,
an **existing** company must have its **own Tally history bootstrapped before the
first proposal is shown**. An empty memory for an existing company is a **PRODUCT
FAILURE**, not a neutral state — the system would ask about vendors the company
has posted to for years. Stated as a design rule in
[`ARCHITECTURE.md` §4.3](./ARCHITECTURE.md#43-memory-index--accountantmemoryindexpy--present)
and carried into the MVP completion checklist in
[`ARCHITECTURE.md` §11](./ARCHITECTURE.md#11-mvp-completion-checklist).

### UK central government is not schema-stable either

The narration column alone appears as:

```
Narrative                            Publication Description
Description                          Invoice Cost Centre Description
Item Text                            PO Catergory Description
```

**That misspelling and that trailing space are both in Defra's published file.**
DfT publishes its amount column as the literal header `" £ "`. **DBT publishes
its narration column and leaves all 199 cells EMPTY** — a present-but-empty
column, which is why the loader reports it rather than treating it as absent.

Handled in `accountant/ingest/spend.py`, tested against seven real department
fixtures. [`BOTTLENECKS.md` B5](./BOTTLENECKS.md#b5--uk-central-government-is-not-schema-stable).

---

## 23. Documentation drift corrected

Found and fixed 2026-08-08. Each row was verified against the repository before
being written, not carried over from a prior claim.

| Claim in the docs | Repository, verified 2026-08-08 | Action |
|---|---|---|
| `PROJECT_STATE.md` §1: `main @ 4cc290f`, 11 commits, clean | `f7bf5d9`, **16 commits**, working tree **not clean** | corrected, §1 |
| `PROJECT_STATE.md` §8: real Tally connector **NOT STARTED**, evidence `grep … → nothing` | `accountant/tallyio/real.py`, **63.5 KB**, has run against a real Tally | corrected, §8 + §21 |
| `PROJECT_STATE.md` §8: scoring harness #4 **NOT STARTED**, "no `accountant/score/`" | `accountant/score/` **PRESENT** — `__init__.py`, `book.py`, `harness.py`, `report.py` | corrected, §8 |
| `PROJECT_STATE.md` §8: source inventory 2,049 lines / 18 files; tests 2,206 lines / 8 files / 242 tests | **7,658 lines / 38 files**; tests **7,050 lines / 14 files / 682 collected** | corrected, §8 |
| `ARCHITECTURE.md` §4.10: `accountant/score/` **absent** | **PRESENT** | moved to §4.11, marked present |
| `ARCHITECTURE.md` §4.10: `accountant/ingest/` **absent** | **PRESENT** — `sources.py`, `fetch.py`, `spend.py`, `crossorg.py`, `report.py`, 7 fixtures. Untracked in git. | moved to §4.12, marked present |
| `ARCHITECTURE.md` §4.10: `accountant/taxonomy/` **absent** | **PRESENT** — `sources.py`, `findings.py`, `coverage.py`, `report.py`. Untracked in git. | moved to §4.13, marked present |
| `ARCHITECTURE.md` §4.10: `accountant/rules/` **absent** | **STILL ABSENT** — `ls accountant/rules` → no such directory | left as absent, correctly |
| `ARCHITECTURE.md` §4.2: `real.py` "has never run against a real Tally", A1–A10 hypotheses | it has run; three hypotheses were disproven by real data | status removed from `ARCHITECTURE.md`, evidence recorded in §21 |
| `ARCHITECTURE.md` §3: Tally "on `localhost:9000`" | Tally is at **`192.168.64.2:9000`**; `TallyConfig` takes host and port | corrected |
| `ARCHITECTURE.md` §4.4: four detectors presented without any coverage limit | **2 of 12** published error types covered | limit stated in `ARCHITECTURE.md` as a design consequence; the count lives here, §22 |
| `ARCHITECTURE.md` §8: actionlint timings 1s / 25s stated inline | measurements do not belong in the blueprint | moved to `PROJECT_STATE.md` §11, linked from `ARCHITECTURE.md` |
| `ARCHITECTURE.md` §7 and §12: phase status COMPLETE / BLOCKED / DEFERRED, and a "next action" | status does not belong in the blueprint | removed, replaced with links to §7 and §19 here |

**Confirmed unchanged, re-verified rather than assumed:**

```
ci/gates.toml                20 gates            unchanged
pyproject.toml               dependencies = []   unchanged, no web framework anywhere
accountant/web/app.py        stdlib http.server  unchanged
.python-version              3.14                unchanged
.github/workflows/full.yml   Docker actionlint   STILL PRESENT, line 169 - deferred, not done
```

**Not built, and deliberately so.** The general fix for this class of drift is a
**consistency check** over the `present` / `absent` markers in
`ARCHITECTURE.md` §4. It does not exist.
[`BOTTLENECKS.md` A8](./BOTTLENECKS.md#a8--documentation-drift-is-unchecked)
records it as an open item — **not** as a new gate, and **not** as a blocking
rule.

---

## 24. Tally licensing — OWNER DECISION, 2026-08-08

> ## ⚠ SUPERSEDED ON THE LICENCE, 2026-08-12. READ §48 FIRST.
>
> **"Educational mode only" and "a legitimate non-Educational Tally licence is
> unavailable" are no longer true.** The machine now runs a **licensed
> TallyPrime on a free trial**. `B-02` is not a blocker.
>
> This section is left standing rather than rewritten, exactly as §46.4 was left
> standing for §47. It was true from 2026-08-08 to 2026-08-11 and the record of
> what was true then is worth more than a tidy document.
>
> **Two of its standing instructions survive the change and are NOT superseded:**
>
> - **`2026-08-07` is still never edited.** The licence removes the *reason* the
>   fixture could not run. It does not license changing the fixture, and the
>   whole point of freezing it was that it must pass unaltered or not at all.
> - **`ENVIRONMENT_LIMITED` is still never converted into `PASS` by decision.**
>   A test passes by running. The 2026-08-07 contract **has still not been run**
>   against the licensed instance.
>
> The instruction that does lapse is *"do not purchase, activate, bypass or
> simulate a non-Educational licence"* — a free trial was activated by the
> owner, which is the "activate" case, and it was the owner's call to make.

**Option 2 selected: Educational-mode exception.**

```
Tally licensing status: Educational mode only.
Phase 2 status:         BLOCKED_ENVIRONMENT
Genuine owner blocker:  A legitimate non-Educational Tally licence is unavailable.
Unchanged fixture:      2026-08-07.
Limitation:             Educational mode cannot validate the original 2026-08-07
                        contract because its date restrictions reject that fixture.
Evidence status:        Every other piece of the Tally spine is closed. The
                        original client-fixture contract suite is stopped by
                        environment licensing and has never run.
```

> **Audit note, 2026-08-10.** Two changes, no change of meaning. The status word
> was `ENVIRONMENT-LIMITED, not fully complete`, which is not a status this
> project has; it is `BLOCKED_ENVIRONMENT` in
> [`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml). And the suite was called
> "the original 15-test contract suite" — an AST count of
> `tests/test_tally_contract.py` on 2026-08-10 finds **19** of its 24 test
> functions take the `client` fixture, not 15. The count is marked
> `PENDING_COUNT` until the RealTally preparation agent confirms it. See
> [`artifacts/document_contradictions.md`](../artifacts/document_contradictions.md).

**Standing instructions attached to this decision.** Do not purchase, activate,
bypass or simulate a non-Educational licence. Do not edit `2026-08-07`. Do not
convert `ENVIRONMENT_LIMITED` into `PASS`. Do not use the accepted control date
`2026-08-31` to claim the original fixture passed.

### The measurement that forces it

| voucher date | result | evidence |
|---|---|---|
| `2026-08-07` — the contract fixture | **REJECTED** | `TallyRejected … exceptions=1 line_errors=["Voucher dat…"]` |
| `2026-08-31` — control | **ACCEPTED** | written, then reversed and cleaned up |
| deletion / reversal | **WORKS** | `deleted=1 errors=0`, `VOUCHER GONE: True` |

The control matters: it isolates the cause. Writing works, deleting works, and
only the date is refused — so this is an **environment** limit, not an XML,
connector or parser defect. §21 carries the rest of the live evidence.

### How substitute evidence must be labelled

Anything run around the restriction is a `mechanism test`, `mock test` or
`Educational-mode test`. **Never** `full live-contract proof`. A copy of the
fixture on an allowed day is a compatibility test and says so in its name. No
substitute dataset may be used to claim GST or accounting validation.

### What would close it

A legitimate non-Educational licence, then the original unmodified
`tests/test_tally_contract.py` run against `RealTally` with **every**
client-fixture test passing — count PENDING_COUNT (19 by an AST count on 2026-08-10; the docs said 15). Nothing else closes it, and no
amount of local green changes that.

---

## 25. Phase 3 — status, evidence, and the three kinds of proof

**Date: 2026-08-09.** Commits `12a8afb` (P3.1–P3.3) and `dc067f8` (P3.4–P3.5).

### 25.1 The distinction everything else depends on

This project produces three kinds of evidence. Conflating any two of them is how
a green suite comes to mean nothing.

| Class | What it proves | What it can NEVER prove |
|---|---|---|
| **FakeTally implementation** | our code behaves as designed | anything at all about TallyPrime |
| **Educational-mode compatibility** | a real TallyPrime accepted our XML on a date Educational mode permits | that the unchanged `2026-08-07` fixture works |
| **RealTally live** | the unchanged contract passed against a licensed TallyPrime | — |

**All Phase 3 test evidence to date is class 1.** Class 3 is unobtainable under
the owner's 2026-08-08 Option 2 decision (§24) and that has not changed.

### 25.2 Measured — VERIFIED 2026-08-09

| Metric | Actual | Expected | Pass rule | Source |
|---|---|---|---|---|
| tests at `dc067f8` | **891** | ≥ 891 | count only goes up | `pytest -q` |
| tests at `bcb301f` | **904** | ≥ 891 | +13 request-shape whitelist | `pytest -q` |
| tests at `c9ae29e` | **910** | ≥ 891 | +6 real startup-path tests | `pytest -q` |
| tests at `2357300` | **916** | ≥ 891 | +6 evidence-class guards | `pytest -q` |
| tests at `20f9244` | **964** | ≥ 891 | +48 backend-state and licence tests | `pytest -q` |
| coverage `accountant/tallyio/real.py` | **100%** | ≥ 90 | line AND branch | `--cov-branch` |
| coverage, overall | **99%** | ≥ 90 | threshold | `--cov-branch` |
| failed / skipped | **0 / 0** | 0 / 0 | no skips are permitted | `pytest -q` |
| guards | **12/12** | 12/12 | all pass | `./scripts/guards` |
| pyright errors | **0** | 0 | zero | `pyright` |
| accidental deletions | **0** | 0 | `git diff` removed-line count | `git diff` |
| coverage, overall | **98%** | ≥ 90 | threshold | `coverage`, branch on |
| `pipeline.py:230` read-back raise | **covered** | covered | G4 | `--cov-report=term-missing` |

### 25.3 The startup path — VERIFIED in a clean room

`serve()` did not call `connect()`. `python -m accountant.web.app`, the exact
command in `README.md:64`, started a server on which **every page answered
`REAL TALLY REQUIRED`**. The product could not be run at all, and no test could
have caught it: every web test injects a client through `configure()` and so
never executes `serve()`'s body.

Verified against a `git archive` of `dc067f8` extracted to an empty directory,
with stock Python 3.14.6 and **no virtualenv and nothing installed**:

```
import accountant.web.app          -> OK, dependencies needed: none
python -m accountant.web.app       -> refuses, exit code 1
python -m accountant.web           -> refuses, exit code 1   (was: "No module named
                                      accountant.web.__main__", an error that tells
                                      a non-programmer nothing)
```

The refusal names what to check: TallyPrime running, the company open, and
`F1 > Settings > Advanced Configuration > HTTP Server`.

### 25.4 Two false statements the running app was making

Neither was in any plan. Both are RealTally safety, not cosmetics.

1. **`serve()` never connected** — above.
2. **Every page rendered** *"Demo mode. This is talking to a fake Tally running
   in memory… Nothing here touches any real books."* True while the app built its
   own `FakeTally`; a lie from P3.1 onward, and the dangerous direction — a person
   told nothing is real will type freely into books that are. The notice is now
   measured from the live identity. **This hole SURVIVED as a mutant**: deleting
   the branch so a fake rendered exactly as a real Tally left all 879 tests green.

### 25.5 Live TallyPrime — what was measured, and what broke

Reached through **our own `RealTally` client**, VERIFIED 2026-08-09:

```
list_companies()  -> ('Accountant Dad Final',)
read_accounts()   -> ('AD Test Expense', 'AD Test Vendor', 'Cash', 'Profit & Loss A/c')
read_vouchers()   -> 2 vouchers
trial_balance()   -> {'AD Test Expense': 168456, 'AD Test Vendor': -168456}   sums to 0
```

**The transport, the XML and the parsing all work against a real TallyPrime 7.**

#### The company/ledger mismatch — root cause, measured

| Fixture expects | Live Tally has |
|---|---|
| company `Demo Co` | `Accountant Dad Final` |
| `Purchases`, `Sundry Expenses`, `Sharma Traders` | absent |
| `Cash` | present |
| an empty company for the flat-trial-balance test | 2 vouchers already posted |

**Cause, five whys:** the contract fixture hard-codes a company and four ledgers
because `FakeTally.add_company` can invent a world on demand. A real Tally
cannot — the company must already exist and be **open in the application**.
The root is not a wrong name; it is that *the fixture assumes a capability only
a fake has.*

Creating the company over the gateway was attempted and **REFUSED**:

```
COMPANY NAME="Demo Co" ACTION="Create"
  -> <RESPONSE>Unknown Request, cannot be processed</RESPONSE>
```

So it is **not** a code, configuration, fixture or bootstrap defect that this
project can fix. Creating a company is a GUI action. **OWNER-BLOCKED** — the
exact action is in §25.7.

#### Licence mode is UNREADABLE over the gateway — and probing for it wedged Tally

An earlier agent reported `$$LicenseInfo:IsEducationalMode -> Yes`. **That is not
reproducible.** Measured directly:

```
Export/Function $$LicenseInfo:IsEducationalMode
  -> <ERRORMSG>Could not find: $$LicenseInfo:IsEducationalMode</ERRORMSG>
     identical for IsEduMode, LicenseInfo, IsLicensedMode, SerialNumber
Export/Data "License Info"
  -> <LINEERROR>Could not find Report 'License Info'!</LINEERROR>
```

A custom TDL `<REPORT>/<FORM>/<PART>/<LINE>/<FIELD>` was then tried, and **it
wedged the live TallyPrime**.

**CORRECTED 2026-08-09 by a screenshot of the VM.** Tally did not crash and was
not hung. It opened a **modal error dialog on the Windows desktop** and stopped
serving HTTP until a human dismissed it:

```
Error in TDL
Part:LicProbe
Could not find the Repeated Line:
                              [ OK ]
```

`LicProbe` is the report name from the probe, so the attribution is exact.

**This is worse than a hang, and it changes what the whitelist is worth.** A bad
request does not fail and return an error over HTTP. It blocks the gateway
behind a dialog that only a person standing at that machine can clear. On a
customer's installation that is a silent, unrecoverable-from-here outage caused
by one malformed request. The recovery is one click, and it is a click nobody
remote can make.

Two earlier conclusions in this section are therefore WRONG and are corrected
here rather than edited away: "restarting TallyPrime" is not required, and
`utmctl exec` being unavailable was never the real obstacle.

Also confirmed visually in the same screenshot: the title bar reads
**Tally Prime EDU** — Educational mode, seen directly, independent of the
licence read that could not be performed. TCP kept accepting; HTTP never answered again, for
that request or any after it. Measured to exhaustion: **40 polls at 10-second
intervals over roughly seven minutes, then one deliberate 150-second
single-request attempt in case Tally was grinding rather than dead. All timed
out.** `nc -z 192.168.64.2 9000` succeeded throughout — the socket accepts, the
server never replies.
`utmctl exec` produces no output at all, so the application could not be
restarted remotely.

**Consequences, all recorded rather than smoothed over:**
- the UI's Educational-mode state must be driven by an honest `UNKNOWN` that
  **fails closed**, never by a guess or by inference from company or ledger names
- the Educational compatibility slice (§25.6) could not complete this session
- **regression guard added** — `tests/test_real_tally.py` now pins every request
  this connector can build to two families, `Export+Collection` and
  `Import+Data`, and fails if any builder ever emits a `REPORT`, `FORM`, `PART`,
  `LINE`, `FIELD` or `TYPE=Function`. Both mutants die.

### 25.6 The Educational compatibility slice

Built: `ci/educational_slice.py`. It posts one voucher dated **2026-08-31**,
reads it back from the **unfiltered** register, checks the trial-balance delta,
reverses by operation ID, and asserts the balance returns to the exact paise —
emitting one row per metric with `actual · expected · pass_rule · evidence ·
run_id · backend · company · ledger`.

**It has not produced a passing run.** Tally became unresponsive (§25.5) before
the first execution completed. Status: **NOT YET MEASURED.** No number from it
is claimed anywhere.

When it does run it is **compatibility evidence only**. It may never be labelled
live proof, RealTally proof, `2026-08-07` proof, or Phase 3 live completion. The
`2026-08-07` fixture is untouched — verified: it appears once as an addition in
the initial commit and has never been removed or changed in any commit since.

### 25.7 Owner actions — exact, and the only ones outstanding

1. **Restart TallyPrime inside the Windows VM.** Its HTTP gateway is wedged.
   Close TallyPrime and open it again, then reopen the company. Nothing else
   recovers it; there is no remote path.
2. **If the live slice is wanted against `Demo Co`:** create a company named
   exactly `Demo Co` in TallyPrime and leave it open. It cannot be created over
   XML. Without this, the slice runs against `Accountant Dad Final` and says so
   in every evidence row.

Neither is on the critical path for anything else. All other work continued.

### 25.8 Phase 3 status

```
Phase 3:  PARTIALLY_VERIFIED     (docs/CONTROL_PLANE.yaml is the authority)
```

Not "passing". The implementation is done and proven against FakeTally; the live
validation is stopped by the licence decision in §24 and now also by §25.5 and
§25.7.

> **Audit note, 2026-08-10.** This block read "implementation complete. Live
> validation remains environment-limited." The two-word verdict was doing the
> work of a status vocabulary that did not exist yet. Same facts, one word,
> and that word now lives in one file.


---

## 26. Backend states, and the one that needs an owner decision

**2026-08-09, commit `20f9244`.** The page distinguishes **five** states, not the
four asked for. The fifth exists because folding it into any of the others would
have meant inventing a fact.

| state | meaning | style |
|---|---|---|
| `real-ok` | licence measured as full — the **only** reassuring state | plain |
| `unavailable` | nothing connected, and why | warning |
| `real-practice` | real Tally, Educational — names the 1st/2nd/31st restriction | warning |
| `not-real` | FakeTally or another double | warning |
| `real-licence-unknown` | **where this instance actually is** | warning |

The guard is written `!= LICENSED`, never `== UNKNOWN`, so a typo, a future
Tally mode or an unfilled field all land on the warning side.

`RealTally.read_licence()` sends only the `Export/TYPE=Function` shape, which
fails **fast**. The TDL-report shape that wedged the gateway (§25.5) is not built
anywhere. The read never raises, caps its wait once with `retry=False`, and makes
at most one round trip when the gateway cannot answer.

### 26.1 OWNER DECISION OUTSTANDING — declared vs measured

A person looking at the Tally screen **knows** it is Educational. The program
**cannot measure it** (§25.5), and inferring it from the company name, the ledger
names or the voucher count is forbidden — that is invention.

So a genuinely Educational user currently sees `real-licence-unknown`: warned
about the date restriction, but told *"we could not tell"* rather than *"you are
restricted"*.

Closing that gap needs a **declared** value, labelled **DECLARED, NOT MEASURED**,
set by the owner and never mistaken for a reading. That is a decision about how
much declared truth the system may carry, not an engineering choice, so it was
not built. **No action is required for anything else to proceed.**

---

## 27. Threat model — hazards, controls, and the test that pins each

**2026-08-09.** Every row verified against the repository, not asserted. A hazard
with no permanent test is a hazard nobody will notice returning.

| Hazard | Trigger | Damage | Prevention | Detection | Safe recovery | Permanent test |
|---|---|---|---|---|---|---|
| **Wrong-company write** | company A's memory used for a draft in company B | a voucher in the wrong business's statutory books | `build_draft` and `evaluate` compare `memory.identity.key` to `normalise_company(company)` and raise | `ValueError` naming both companies | nothing written — the raise precedes any write | `test_pipeline_isolation.py:191, 199, 217` |
| **Wrong-vendor match** | two vendors with near-identical or homoglyph names | voucher posted to the wrong ledger | exact normalised equality in SQL, never fuzzy; two accounts → `CONFLICTED`, never a pick | `MatchStatus.CONFLICTED` becomes a question | ask, never guess | `test_memory.py`, `test_decide.py`, `test_adversarial_identity.py` |
| **Duplicate voucher** | retry after a dropped response | the same expense counted twice | operation ID generated before the write; `DuplicateOperation` on reuse | exception, register count unchanged | no second voucher created | `test_tally_contract.py` |
| **Stale read** | index built from history that has since changed | proposing from a world that no longer exists | memory is bootstrapped per company and re-derived; `read_by_operation_id` always re-reads | bootstrap counts reported in `/health` | re-bootstrap | `test_memory.py`, `test_pipeline.py` |
| **Misleading success** | Tally answers HTTP 200 but wrote nothing | we tell the person it posted when it did not | C6 — every post is read back; `None` raises | `RuntimeError` naming the operation id | `posted_tally_id` stays `None` | `test_pipeline.py:438` |
| **Fallback to FakeTally** | a fake reaching a live runtime | fabricated evidence about real books | the app imports neither implementation; the factory is the only constructor | AST import scan; five measured backend states | refuse — `REAL TALLY REQUIRED` | `test_runtime_backend.py`, `test_backend_states.py` |

### 27.1 The dependency graph, measured by AST

Every mutating call site under `accountant/`:

```
accountant/pipeline.py:225   write_voucher()            in post()      GATED on Outcome.VALID
accountant/pipeline.py:240   reverse_by_operation_id()  in reverse()
accountant/web/app.py:787    reverse_by_operation_id()  in do_POST()   <-- BYPASS
```

Modules importing anything named `*fake*`: **NONE**. The three occurrences of the
string `FakeTally` in `accountant/web/app.py` are historical prose in comments —
lines 10, 369 and 817 — with no code path.

**`write_voucher` is clean:** exactly one caller outside `accountant/tallyio/`,
inside `pipeline.post`, behind the Valid gate, pinned by an AST test.

**`POST /reverse` is not.** `accountant/web/app.py:787` reaches `tallyio` directly,
skipping `pipeline.reverse` and its trial-balance verification, and reverses
whatever `op` string the form supplies. So the claim *"UI → application boundary →
tallyio → RealTally is the only live write path"* is **TRUE for creation and FALSE
for reversal**.

This is the item already recorded as *"Noted, not fixed here"* in the Phase 3 plan.
**Reported, not fixed** — the plan is frozen and this needs its own decision.

---

## 28. FIRST LIVE RUN — the whole chain, against a real TallyPrime

**2026-08-09.** The gateway was unblocked by the owner clicking `OK` on the modal
(§25.5). Everything below was then measured against the real TallyPrime 7 in the
Windows VM at `192.168.64.2:9000`.

**Evidence class: `EDUCATIONAL_MODE_COMPATIBILITY`.** Real Tally, real XML, real
register, real reversal — on a date Educational mode permits. It is **not** proof
about the unchanged `2026-08-07` fixture and is never to be reported as such.

### 28.1 The application, started the normal way

Command, with the VM named through the new environment variables:

```
ACCOUNTANT_TALLY_HOST=192.168.64.2 ACCOUNTANT_TALLY_PORT=9000 \
ACCOUNTANT_COMPANY="Accountant Dad Final" \
ACCOUNTANT_BACKED_UP_COMPANIES="Accountant Dad Final" \
python -m accountant.web
```

`GET /health` → **HTTP 200**, every value measured, none hardcoded:

| field | actual | run |
|---|---|---|
| `ready` | `true` | `run_d472c609ae7e4abe8c2985b6b2a84985` |
| `backend` | `RealTally` | |
| `endpoint` | `http://192.168.64.2:9000` | |
| `company_identifier` | `Accountant Dad Final` | |
| `bootstrap_status` | `ready` | |
| `accounts_read` | 4 | |
| `vouchers_read` | 2 | |
| `vendor_mappings_derived` | 1 | |
| `index_entries` | 1 | |
| `conflicts` / `unusable_rows` | 0 / 0 | |
| `backend_state` | `real-licence-unknown` | fails closed, as designed |
| `licence_mode` | `unknown` | with the exact Tally error as its reason |

**`serve()` → `connect()` → `RealTally` → identity check → bootstrap from the
company's OWN history → the app becomes available.** Proven by running it, not by
injecting a client.

### 28.2 The controlled write, and Tally's own register

`ci/educational_slice.py`, run `edu_0d42b3a30d79461b8d25ad414040d6e5`,
backend `RealTally`, company `Accountant Dad Final`, ledgers
`AD Test Expense` / `AD Test Vendor`, voucher date `2026-08-31`.

**20 of 21 metrics PASS.** The single FAIL is `company_is_demo_co`, which is the
recorded substitution from §25.5 — `Demo Co` cannot be created over XML — not a
defect.

| metric | actual | expected | pass rule |
|---|---|---|---|
| `trial_balance_balances_to_zero` | 0 | 0 | conservation law |
| `register_size_before` | 2 | 2 | reported |
| `voucher_created` | true | true | Tally returned an identifier |
| `voucher_identifier` | **13** | — | Tally's own MASTERID |
| `voucher_carries_marker` | matches op id | matches | C4 |
| `read_back_by_operation_id` | true | true | C6 |
| **`register_grew_by_one`** | **1** | 1 | the UNFILTERED register |
| **`found_in_unfiltered_register_by_amount`** | **1** | 1 | located WITHOUT our marker |
| `trial_balance_delta` | `{AD Test Expense: +131300, AD Test Vendor: -131300}` | exactly that | moved by this voucher and nothing else |
| `reversal_reported_success` | true | true | targeted by operation id |
| **`trial_balance_restored_exactly`** | `{168456, -168456}` | identical to before | #6.5, to the paise |
| `register_size_restored` | 2 | 2 | gone from Tally's register |
| `cleanup_status` | true | true | nothing of ours left behind |

**Verified independently of the slice's own claim**, by a separate read after the
run: 2 vouchers, `{'AD Test Expense': 168456, 'AD Test Vendor': -168456}`.

### 28.3 What this does and does not settle

```
Tally running                        PROVEN
application connects                 PROVEN   serve() -> connect() -> RealTally
correct company confirmed            PROVEN   company_identifier measured
structured read succeeds             PROVEN   4 ledgers, 2 vouchers, balanced TB
controlled voucher write succeeds    PROVEN   Tally identifier 13
Tally's own register returns it      PROVEN   found without our marker
cleanup restores the books           PROVEN   exact paise, verified twice
```

**Still NOT proven:** the unchanged `2026-08-07` contract. Educational mode
rejects that date, and the owner's Option 2 decision (§24) stands. No amount of
`2026-08-31` evidence changes it, and the two are kept apart by
`tests/test_evidence_classes.py`.

```
Phase 3:                       PARTIALLY_VERIFIED
  implementation               proven against FakeTally
  live validation              BLOCKED_ENVIRONMENT
RealTally 2026-08-07 evidence: NOT PROVEN
```

> **Audit note, 2026-08-10.** These three lines read `implementation: COMPLETE`
> and `live validation: ENVIRONMENT-LIMITED` until today. Neither word is a
> status this project has. The wording changed; the meaning did not.
> `docs/CONTROL_PLANE.yaml` is now the one place a status is decided.

---

## 29. FOUR OPEN DEFECTS found by adversarial testing — OWNER DECISION

**2026-08-09.** Found by `tests/test_adversarial_identity.py`, which **pins** each
one so a fix turns the pinned test red visibly. **None is fixed.** Each posts a
voucher to the wrong place, silently, with no flag and no question.

| id | defect | measured result | smallest fix |
|---|---|---|---|
| **D1** | An accented name in NFD form keys to a *different* supplier. `_PUNCT = [^\w\s&]` at `memory/index.py:36` — U+0301 is category Mn, so a decomposed accent becomes a space and collapses. | `normalise_vendor(NFD "Café Supplies") == "cafe_supplies"`. The NFD spelling **posts VALID**; the NFC spelling of the identical visible name asks. | `index.py:46` → `unicodedata.normalize("NFC", name).casefold().strip()`, and the same at `identity.py:47`. Stdlib. |
| **D2** | `Acme Ltd` and `Acme LLP` are one vendor key. `_SUFFIXES` at `index.py:20-34` strips `llp` beside `ltd`. | An LLP invoice **posts VALID** against Ltd-only history. Contradicts `identity.py:19-21`, which states those are separate entities. | drop `llp`, `inc`, `corp`, `corporation` from `_SUFFIXES`. **Owner call** — it is a documented trade-off. |
| ~~**D3**~~ **FIXED 2026-08-09, see §33** | Two Tally companies could share one memory scope. `identity.py:37-47` turns punctuation into a separator, so `Acme Traders (Unit 1)` and `Acme Traders Unit 1` both key `acme_traders_unit_1`. | The second bootstrap's `forget()` erases the first company's index; the first company's live handle then answers with the **second company's account**; the cross-company guard at `pipeline.py:116-121` compares keys so it **cannot fire**; `store.actions()` merges both trails. | collision check in `bootstrap.py:197`, where `list_companies()` is already in hand. Fails closed, no schema change. |
| **D4** | A stale index outvotes the live ledger. `resume()` at `bootstrap.py:255-272` never tests freshness; `bootstrapped_at` is written and read by no decision. | Memory says Purchases, all 40 live vouchers say Repairs & Maintenance → **VALID, zero flags, zero problems, posted**, reason recorded as *"nothing unclear and nothing surprising"*. | compare the proposal against the party's accounts in the `history` already passed to `pipeline.evaluate:180`, or a detector reading `history` — `detectors.py:63` already carries it. |

**D3 is the most serious: it is a cross-company write**, and it defeats the exact
isolation the memory package exists to provide.

**Also recorded, not a defect:** `Problem.id` and `Question.problem_id` disagree —
a failed `accounts_exist` check yields `Problem(id="accounts_exist")` whose
question is hard-coded `problem_id="which_account"` (`problems.py:55`,
`questions.py:143`). Self-resolves today, but the non-overlapping guarantee and
the answer path are keyed on two different ids. Asserted so it cannot drift.

**Also:** a refusal raised out of `build_draft` (`pipeline.py:276`) never reaches
`record_decision` (`:283`/`:285`), so *"every decision leaves a durable row"* is
not true on that path.

**Status: OWNER-BLOCKED by choice.** D1 and D3 are contained fixes; D2 and D4 are
design decisions. None was changed, because all four predate Phase 3 and the plan
is frozen.

---

## 30. FIVE MORE OPEN DEFECTS — the write path. OWNER DECISION

Found by `tests/test_adversarial_write_path.py` (26 tests, 6 `xfail` each proven
to fail for the intended reason under `--runxfail`). **None fixed.** Two of these
undermine claims already committed in this branch.

| id | defect | measured damage | smallest fix |
|---|---|---|---|
| ~~**W1**~~ **FIXED 2026-08-09, see §32** | **The read-back was a PRESENCE check, not an IDENTITY check.** `pipeline.py:228-235` raises only when `back is None`; `back` is then **discarded**. | Sent 420,000 / Sharma Traders / 7 Aug; read back 2,000,000 / Verma Properties / 31 Aug → outcome `valid`, ActionLog `posted`. Second face: present in the marker view but **absent from `read_vouchers` and `trial_balance`** → still reported posted. **This defeats G3**, the register guarantee this branch claims. | compare `back.amount_paise/party/date/debit_account/credit_account` to `draft.voucher`, raise on mismatch, and set `posted_tally_id` from `back.tally_id` not `result.tally_id`. |
| **W2** | **A write with an unknown outcome records NOTHING.** `record_decision` is only reached after `post` returns; when `post` raises, **zero** ActionLog rows exist. | The operation id survives only in a traceback, so a voucher that may exist cannot be reconciled or reversed later. Same hole at `web/app.py:656-657`, where the socket simply drops. | wrap `post` in `try/except BaseException`, record `action="write_outcome_unknown"` carrying the operation id, re-raise. |
| **W3** | **An error envelope reads as an empty company.** `parse_vouchers` (`real.py:1203-1230`) returns `VoucherPage(exported=(), skipped=0)` for a well-formed `<ENVELOPE>` containing `<LINEERROR>` and no vouchers. | The duplicate pre-check at `real.py:1854` sees no marker and imports. **Two identical statutory entries** from one operation id — and both write calls raised, so every layer reported failure. They now share a marker, so `reverse_by_operation_id` raises `matches 2 vouchers` and cleanup needs a human. | raise `TallyResponseError` when any `<LINEERROR>` carries text. Same shape in `parse_companies`, `parse_ledger_names`, `parse_closing_balances`. **Narrows but does not close** — the pre-check still fails OPEN on any read it cannot positively confirm. |
| **W4** | **The fake and the real disagree, and the contract cannot see it.** `fake.py:112-124` picks the FIRST of two vouchers sharing a marker; `RealTally._read_exported_by_operation_id` (`real.py:1797`) refuses. | `test_tally_contract.py` holds both backends to one contract and this property is not in it — so **a test written against the fake can "prove" an ambiguity is handled when it is not.** | collect all matches, raise on `len > 1`, and add the case to the shared contract. |
| **W5** | **Nothing cross-checks the declared identity against the actual client.** `web/app.py:221-249` (`configure`). | A `FakeTally` behind `BackendIdentity(backend="RealTally")` renders `data-backend-state="real-licence-unknown"` and *"This is your real Tally"*, while every log row says `RecordingTally`. Both cannot be right, and the person reads the wrong one. | in `configure`, raise unless `identity.backend == type(client).__name__`. |

**W1 and W5 weaken guarantees this branch asserts.** W1 means "found in Tally's own
register" is checked by the slice but NOT by `pipeline.post`. W5 means the
five-state backend display can be lied to by its own caller.

### 30.1 Chart of Accounts — already built, verified by reading it

`RealTally._check_ledgers_exist` (`real.py:1820-1834`) reads the **actual Chart of
Accounts from Tally** before every write and refuses on an exact-name miss, with
its own docstring recording why: Tally does not create a master on the fly and the
import can fail silently. The requested `REQUIRED_LEDGER_MISSING` behaviour is
therefore present in substance; only the named code and an explicit
ledger-creation operation are absent, and creation is **new scope beyond the
frozen Phase 3 plan**.

### 30.2 Port — measured, not assumed

Seven ports scanned on `192.168.64.2`. **Only 9000 is open, and it answers Tally
XML** with a valid company envelope. `connection_status = reachable`.

### 30.3 LIVE — Educational mode REJECTS, it does not silently rewrite

A simulated finding claimed Tally may accept a refused date and store a different
one, which would make the read-back's existence-only check catastrophic. **Tested
against the real TallyPrime and REFUTED:**

```
SENT   2026-08-07 to 'Accountant Dad Final'
RESULT TallyRejected: status=1 created=0 altered=0 deleted=0 exceptions=1
       line_errors=["Voucher date is missing for: 'Journal' voucher 1..."]
books  restored True, register 2 vouchers before and after
```

Tally strips the date it will not accept and then reports it as **missing**. The
outcome is a clean refusal with zero writes, so the date attack on W1 is closed
by Tally itself.

**W1 remains open** — read-back still verifies existence, not identity, and the
amount and party faces of it are untouched by this result. What is settled is
that the `2026-08-07` fixture is genuinely REFUSED by Educational mode, measured
rather than inferred from documentation.

### 30.4 Six further defects — amounts, rendering, ordering

From `tests/test_adversarial_amounts_and_states.py` (+21 tests, 0 skips, 0 xfails).

| id | defect | measured |
|---|---|---|
| **A1** | float in a money field, `extract/adapter.py:79` `round(float(text)*100)` | wrong integer above ₹99,999,999,999,999.99. `paise_from_rupees` uses `Decimal` and is exact. Fix: `int(Decimal(cleaned) * 100)` |
| **A2** | sub-paise silently truncated, `adapter.py:66` | `10.005` → `10.00`, **VALID, POSTED**. `paise_from_rupees` refuses the same string |
| **A3** | `rupees()` floors toward −∞, `web/app.py:357` | −420050 renders **−4,201.50**. It also RAISES on a float, so a NOT_VALID float draft cannot be drawn at all — the one outcome meaning "must not post" is the one the screen cannot render |
| **A4** | `_check_writable` (`real.py:702`) never checks integer-ness | a float is caught one line later by a format code whose message names no voucher and no amount. `rupees_from_paise(True) == "0.01"` |
| **A5** | ordering, `pipeline.py:273` | Tally is read before memory readiness, so a not-ready company with a flaky connector reports the connector's error instead of `MemoryNotReady`. Fails closed; only the diagnosis is wrong |
| **A6** | 9 of 13 state names in the brief do not exist in the code | `BOOTSTRAPPING`, `POSTING`, `READ_BACK_VERIFIED`, `CLEANED` and others are absences or field values, not states. `POSTING` matters: **a crash mid-write leaves no trace that a write was started** — the same hole as W2 |

Pinned by `test_eight_of_the_thirteen_state_names_do_not_exist_in_the_shipped_package`.
Eight, not nine, since 2026-08-09: `POSTING` now exists as a durable
`write_attempted` row. See §37.

---

## 31. Phase 3 delivery — commit, PR, and the final audit

| item | value |
|---|---|
| branch | `phase3/action-log` |
| HEAD at delivery | `899fd29` |
| merge base with `main` | `6867ca9` |
| PR | [#15](https://github.com/Intellora-ai/accountant-dad-core/pull/15) |
| CI | `pr-fast` pass · `pr-full` pass · `ci-gate` pass |
| **merge** | **NOT MERGED — blocked, see 31.2** |

### 31.1 Final audit

| check | result |
|---|---|
| tests | **1023 passed, 6 xfailed, 0 failed, 0 skipped** |
| guards | **12/12** |
| pyright | **0 errors** |
| accidental deletions | **0** files, **0** net test definitions |
| frozen `2026-08-07` fixture | unchanged, verified by `git diff` and by `test_evidence_classes.py` |
| stale worktrees | 0 |
| working tree | clean |
| secrets / credentials / private data / temp artefacts in the diff | none |
| FakeTally in a live path | none — 0 imports, AST-verified |
| Phase 4 work | none |
| read-back failure mutant | **killed** (1 fail) |
| ungated-write mutant | **killed** (2 fail) |
| fake-live-message mutant | **killed** (11 fail) |
| serve-without-connect mutant | **killed** (5 fail) |

### 31.2 OWNER ACTION — the merge is blocked, and not by CI

All three required checks are green. `gh pr merge` was **refused by the local
permission classifier**, not by GitHub and not by branch protection. The merge
therefore has to be performed by the owner, or the permission granted.

```
gh pr merge 15 --squash --delete-branch
```

Nothing else is outstanding for Phase 3. **This is recorded rather than worked
around, and the PR is NOT described as merged.**

### 31.3 Evidence classification, final

| class | obtained? |
|---|---|
| FakeTally implementation evidence | **YES** — 1023 tests |
| Educational-mode RealTally compatibility evidence | **YES** — run `edu_0d42b3a30d79461b8d25ad414040d6e5`, 20/21 metrics, Tally identifier 13 |
| regular licensed RealTally evidence | **NO** — owner Option 2 (§24) stands |

```
Phase 3:                       PARTIALLY_VERIFIED
  implementation               proven against FakeTally
  live validation              BLOCKED_ENVIRONMENT
RealTally 2026-08-07 evidence: NOT PROVEN
```

> **Audit note, 2026-08-10.** These three lines read `implementation: COMPLETE`
> and `live validation: ENVIRONMENT-LIMITED` until today. Neither word is a
> status this project has. The wording changed; the meaning did not.
> `docs/CONTROL_PLANE.yaml` is now the one place a status is decided.

---

## 32. W1 FIXED — the headline claim is now true in the code that posts

**2026-08-09.** Owner instruction: *"The headline claim must be checked by the code
that actually posts, not just the standalone slice."*

### 32.1 What changed

`accountant/pipeline.py::post`. Three additions, no behaviour removed:

1. **Identity, not presence.** `VERIFIED_FIELDS` — `amount_paise`, `party`, `date`,
   `debit_account`, `credit_account` — must all come back unchanged.
   `_identity_mismatches` names **every** field that differs, one per line, because
   *"something is wrong"* sends a person through their whole ledger and *"the amount
   and the party are wrong"* does not. `narration` is deliberately excluded: we stamp
   the marker into it, so it is expected to differ.
2. **G3 enforced on the posting path.** The voucher must appear in Tally's
   **unfiltered** `read_vouchers()` register, not only through our marker filter.
   This is what the phase claimed and only the standalone slice checked.
3. **`posted_tally_id` comes from Tally**, not from our own `WriteResult`. Tally's
   answer is evidence; ours is a claim.

It can only ever refuse **more**, never post more. There is no input for which the
old code refused and the new one accepts.

### 32.2 LIVE PROOF — the fixed path, real TallyPrime

```
posted_tally_id                     21          (Tally's own identifier)
in Tally's UNFILTERED register      True
identity verified                   amount 222200 · party 'AD Test Vendor'
                                    · date 2026-08-31 · Dr 'AD Test Expense'
                                    · Cr 'AD Test Vendor'
id came from TALLY, not from us     True
cleanup                             True
books restored exactly              True, register back to 0
```

### 32.3 The fix caught a real inconsistency on its FIRST live run

Its first live attempt **refused**:

```
read back a DIFFERENT voucher: party: sent 'AD Test Vendor', Tally has 'Cash'
```

Investigated rather than assumed. Raw export measured: **Tally does return
`PARTYLEDGERNAME`, and it OVERRODE ours.** We sent `party='AD Test Vendor'` while
`build_draft` had set `credit_account='Cash'` via `_default_credit`, so the voucher
never touched the vendor's ledger at all — and Tally corrected the party to the
ledger actually used.

**Tally was right and our draft was internally inconsistent.** Not a false positive:
the check found a genuine defect on its first contact with reality.

**NEW OPEN DEFECT — P1.** `pipeline.build_draft` sets `party` from the extracted
vendor and `credit_account` from `_default_credit(accounts)`, which prefers `Cash`.
For a vendor with its own ledger this produces a voucher that names the vendor as
the party but posts nothing to their account. Owner decision, and it is Phase 4
territory — proper vendor-ledger resolution.

### 32.4 MISTAKE, RECORDED — over-reversal of test data

Cleaning up the orphan the refusal warned about, a loop reversed **every** voucher
carrying our marker rather than only that one. Three were removed, not one; the two
baseline test vouchers went with it and `Accountant Dad Final` is now empty
(register 0, trial balance `{}`).

All three carried **our own marker**, in a test company with test ledgers
(`AD Test Expense`, `AD Test Vendor`), so nothing belonging to a real business was
touched. The ledgers themselves survived. **It was still more than needed, and it
is recorded rather than smoothed over.** The two removed vouchers were `tally_id`
1 (`AD Test Vendor`, 123456 paise) and 2 (`AD Test Vendor`, 45000 paise); their
dates were not captured before deletion, so they are **not** reconstructable and
were not guessed at.

### 32.5 Tests

Four pinned DEFECT tests were **flipped from documenting the bug to proving the
fix**, and both `xfail(strict)` markers removed because they now XPASS:

```
test_a_read_back_with_our_marker_but_not_our_numbers_is_refused
test_a_read_back_must_match_the_voucher_we_sent_and_not_merely_our_marker
test_a_write_absent_from_tallys_own_register_is_refused
test_a_post_is_not_a_success_until_tallys_own_register_shows_the_voucher
```

One test added after a **surviving mutant** exposed a gap in the fix itself —
replacing `posted_tally_id = back.tally_id` with a constant left the suite green:

```
test_the_recorded_identifier_is_the_one_tally_returned_not_our_own
```

Mutants, all now dead: revert to presence-only (1 fail) · drop the register check
(2 fail) · record our id instead of Tally's (1 fail).

```
tests   1023 -> 1026 passed, 4 xfailed (was 6), 0 failed, 0 skipped
guards  12/12      pyright 0      accidental deletions 0
```

---

## 33. D3 FIXED — two companies can no longer share one memory scope

**2026-08-09.** Phase 4 P4.0. The first bottleneck, fixed before anything was
built on the memory layer.

### 33.1 The recorded fix was sited WRONG

§29 said "collision check in `bootstrap.py:197`, where `list_companies()` is
already in hand". Measured: `store.forget(identity.key)` sat at **line 193**,
four lines earlier and unconditional. A check at :197 fails closed only *after*
the other company's `vendor_account`, `phrase_account`, `chart_account` and
`company` rows are deleted — and `_incomplete()` then writes a row under the
shared key carrying **this** company's `display_name`, so `resume(the other one)`
returns INCOMPLETE under the wrong name.

**Every refusal now precedes `forget()`.** Destroying an index is the last thing
`bootstrap` does, not the first.

### 33.2 What changed

| change | where |
|---|---|
| `BootstrapStatus.COMPANY_KEY_COLLISION` — the measurable failure code | `memory/store.py` |
| `_colliding_company()` — checks the LIVE open-company list, returns the other **original** name | `memory/bootstrap.py` |
| `_refused()` — a refusal that writes **nothing**, unlike `_incomplete` | `memory/bootstrap.py` |
| `forget()` moved below every check | `memory/bootstrap.py` |
| plain-English banner for the new status | `web/app.py` |

`_refused` exists because `_incomplete` calls `save_bootstrap`, and on a
collision **that write is the damage**: it stamps one company's name onto the
other's row. A refusal whose own record corrupts what it is refusing to touch is
not a refusal.

The check reads the **live open-company list**, not the store, because the store
can only ever hold one of a colliding pair — `company_key` is the sole primary
key and the writer is `INSERT OR REPLACE`.

### 33.3 Stricter than designed, and correctly so

The first draft of the test expected the FIRST company to bootstrap fine and only
the second to be refused. It failed, and **the code was right**: while two names
that reduce to one key are both open, no reading of *either* can say whose books
it read. **Both are refused.**

### 33.4 The normalisation rule is deliberately UNCHANGED

Tightening it only reshuffles which pairs collide. `Ganesh  Textiles` and
`Ganesh Textiles` alias under **any** rule that collapses whitespace, and the key
is re-derived from a free-text name in `store.state()` and `store.actions()`, so
whitespace collapsing cannot be dropped. The map is many-to-one; some pair always
aliases. The fix is a **uniqueness proof at the point of admission**, not a better
guess at which characters matter.

### 33.5 Measured

`tests/test_company_collision.py`, 17 tests. Six real-world Indian colliders,
each with a premise test proving the collision is real — so the refusal tests
cannot pass because there was nothing to catch:

```
M/s Sharma Traders             == M.S. Sharma Traders
Kumar Motors - Pune            == Kumar Motors Pune
Dev Enterprises (Unit-II)      == Dev Enterprises Unit II
Bharat Steel Pvt. Ltd.         == Bharat Steel Pvt Ltd
Shree Balaji Enterprises [Old] == Shree Balaji Enterprises Old
Ganesh  Textiles               == Ganesh Textiles
```

Two controls, both required: re-reading **one** company stays READY, and two
**non**-colliding companies both bootstrap fine — otherwise "refuse every second
bootstrap" would pass.

| metric | actual | expected | evidence |
|---|---|---|---|
| tests | **1043 passed, 4 xfailed, 0 failed, 0 skipped** | ≥ 1026 | `pytest -q` |
| guards | 12/12 | 12/12 | `./scripts/guards` |
| pyright | 0 | 0 | `pyright` |
| mutant: remove the check | **10 failed** | red | by hand |
| mutant: `forget()` back above the check | **2 failed** | red | by hand |
| mutant: `_incomplete` instead of `_refused` | **9 failed** | red | by hand |

`test_two_tally_companies_differing_only_by_brackets_today_share_one_scope` was
**flipped** from pinning the defect to proving the fix, and renamed
`..._are_both_refused`. A vacuous `assert survivor is not None` was caught and
replaced while flipping it — `resume` never returns `None`.

---

## 34. Phase 4 exit 2 made structural, and a stranding bug found on the way

**2026-08-09.** Phase 4 P4.1.

### 34.1 `answer()` left a stale decision

`pipeline.answer()` rewrites `debit_account` and used to leave `draft.decision`
untouched. Its docstring said *"the caller must re-run evaluate()"* — a comment,
not a guarantee. After answering, the draft carried a decision describing a
**different voucher** from the one it now held.

Safe only by accident: `answer` is reached only from UNCLEAR, and `post` refuses
anything not VALID. Change either and a mutated voucher posts against a stale
approval.

`answer()` now sets `draft.decision = None`, so `post` fails closed with
*"draft has not been evaluated"*. **Measured first:** all six existing call sites
already re-evaluated on the next line, so nothing had to change around it.

### 34.2 A stranding bug, found while testing the above

`decide_problems` chose `answerable[0]` **without** skipping already-answered
problems; `next_question` **did** skip them. The two disagreed, and the
disagreement reached the screen. Measured before the fix:

```
outcome:        unclear
next_question:  None
STRANDED:       True
```

The page renders *"needs an answer"* with **no question and no buttons**. The
person cannot act and the question budget never advances.

**Fix:** one rule, two readers. `decide_problems` takes the answered ids —
the same list `next_question` filters on — and when every answerable problem has
already been answered and is still firing, the entry is **handed over** rather
than left UNCLEAR. UNCLEAR is a promise to ask something; if there is nothing
left to ask, saying UNCLEAR is a lie.

### 34.3 What closes the loop, stated so it is not confused

`answer()` alone does **not** make the next pass VALID. It sets
`debit_account`, but memory still returns NO_MATCH for the vendor, so the same
problem is found again. `web/app.py` also calls `record_correction`, and that is
what teaches memory. Both halves are now asserted separately.

### 34.4 Measured

| metric | actual | expected | evidence |
|---|---|---|---|
| tests | **1049 passed, 4 xfailed, 0 failed, 0 skipped** | ≥ 1043 | `pytest -q` |
| guards | 12/12 | 12/12 | `./scripts/guards` |
| pyright | 0 | 0 | `pyright` |
| regressions from the decision-order change | **0** | 0 | full suite |
| mutant: `answer()` keeps the decision | **1 failed** | red | by hand |
| mutant: `decide_problems` ignores answered ids | **2 failed** | red | by hand |
| mutant: `evaluate` stops passing them | **2 failed** | red | by hand |

---

## 35. Phase 4 exit 4 — PROVEN FALSE, guard landed, fix designed and NOT shipped

**2026-08-09.** Phase 4 P4.2/P4.3.

### 35.1 Exit 4 was already false, and had never been checked

`docs/ARCHITECTURE.md` says `NO fallback account exists anywhere in the
codebase`. `PROJECT_STATE.md:128` recorded it as *"VERIFIED — no fallback exists
in `accountant/decide.py`"* — **one file**. `pipeline.py` was never in scope of
that verification, and no CI gate covers it.

`accountant/pipeline.py:81-85`:

```python
def _default_credit(accounts: tuple[str, ...]) -> str:
    for preferred in ("Cash", "Bank", "Sundry Creditors"):
        if preferred in accounts:
            return preferred
    return accounts[0] if accounts else "Cash"
```

It runs on **every entry**, including an unknown vendor. Line 85 returns the
literal `"Cash"` **even when the company has no such ledger**. `credit_account`
carries **no provenance** — and by this codebase's own definition
(`ingest/spend.py`), *a field with no source is a hallucination by definition*.

**Why it stayed invisible:** every test chart in the repo contains `"Cash"`, so
the first loop iteration always matched and lines 83–85 never ran once.

### 35.2 The guard, and two wrong versions of it before the right one

`tests/test_phase4_exits.py`. Two drafts were discarded:

1. **Too narrow** — checked for a bare `return "constant"`. `_default_credit`
   returns `accounts[0] if accounts else "Cash"`, an `IfExp`, and the scan
   walked straight past it. **A fallback hides in a conditional; that is what
   makes it a fallback.**
2. **Too broad** — "any function whose name mentions an account returning a
   string" flagged five innocents: `accounts_differ`, `accounts_exist`,
   `build_ledger_list_request`, `_ledger_entry`, `parse_ledger_names`.

The landed version states the invariant exactly: **a ledger leg may come from
the document, from this company's own memory, or from a person's answer — never
from a literal we wrote, and never from a function that can produce one.**
Result: **exactly one offender, zero false positives.**

It carries its own control (`test_the_guard_catches_a_fallback_that_is_...`),
which runs the forbidden shapes AND the honest shapes through the real
detectors — an absence test nobody has seen fail is indistinguishable from one
that cannot fail.

### 35.3 The fix is designed and was PROVEN END TO END. It is not shipped.

Measured working, before being reverted:

```
1st: unclear | nothing says how this was paid
     question: "How did you pay?"  ->  ['cash', 'from the bank']
2nd: valid   | Dr Purchases | Cr Cash
provenance: credit_account 'human_answer', debit_account 'company_history'
```

Four parts:

1. `checks.funding_is_named` — an absent funding leg is a **question**, not a guess
2. `problems.py` maps it to `Q.how_paid`, which **already existed and was dead
   code**, never wired to anything
3. `_funding_from_history(party, history)` — read the funding leg from this
   company's own past vouchers, **unanimous or nothing**, exactly like the
   expense leg. Asking "how did you pay?" about a vendor whose last forty
   vouchers all say Cash is the same failure `bootstrap` exists to prevent
4. `pipeline.answer` routes by problem id, so the funding answer lands on the
   **credit** leg instead of overwriting the expense leg

### 35.4 Why it was reverted rather than shipped

It turns **21 tests red**, and they are not wrong — the behaviour genuinely
changed. An unknown vendor now correctly asks **two** questions (which purpose,
and how paid) where the tests answer one.

Blast radius measured in stages: 50 red with the naive absence → 28 once the
funding leg is read from history → **21** once the web path passes history too.

Those 21 need considering individually. Bulk-editing them at the end of a long
session is exactly the *"batch many unverified changes and hope the final suite
explains them"* the mandate forbids, so the tree was returned to a verified
green state and the defect **pinned as a strict xfail** carrying the whole
diagnosis.

**Status: NOT YET MEASURABLE as passing. Exit 4 remains FALSE.** The next
session starts with a working design, a precise guard, and a known list of 21.

### 35.5 One further fallback found, not yet addressed

`accountant/questions.py:242-243` — `how_paid` offers `Answer(label="cash",
value="Cash")` when the chart contains neither Cash nor Bank. Offering a ledger
the company does not have means the person clicks it and we post to a
nonexistent account. The AST guard does not catch it because it is an answer
option, not a ledger-leg assignment. Recorded, not fixed.

### 35.6 Measured

| metric | actual | expected | evidence |
|---|---|---|---|
| tests | **1052 passed, 5 xfailed, 0 failed, 0 skipped** | ≥ 1049 | `pytest -q` |
| guards | 12/12 | 12/12 | `./scripts/guards` |
| pyright | 0 | 0 | `pyright` |
| exit 4 | **FALSE, pinned** | true | `test_no_ledger_leg_is_ever_set_from_a_literal_or_a_chooser` |
| fallback offenders found | **1** (`pipeline.py:134`) | 0 | AST scan |
| false positives | **0** | 0 | AST scan |

---

## §36 Phase 4 exit 4 — the funding guess, deleted and replaced

2026-08-09. Follows §35, which pinned this as a strict xfail. **The xfail is
gone: it passes.**

### 36.1 What was deleted

`accountant/pipeline.py:81-85`, in full:

```python
def _default_credit(accounts: tuple[str, ...]) -> str:
    for preferred in ("Cash", "Bank", "Sundry Creditors"):
        if preferred in accounts:
            return preferred
    return accounts[0] if accounts else "Cash"
```

It ran on **every** entry. It read nothing about the vendor and nothing about
the company's history. It wrote no provenance, which by this project's own
Hallucinate definition makes it a hallucination on every voucher. Its last line
returned the literal string `"Cash"` for a company with no such ledger.

**Why 1,026 green tests and a 94.34% mutation score did not catch it:** every
test chart in the repository contains `"Cash"`, so the first loop iteration
always matched and the three later branches never executed once. A guess that
agrees with every fixture is indistinguishable from knowledge until somebody
writes the fixture where they disagree. `tests/test_funding_leg.py` is those
fixtures — 13 tests, including the disconfirming one: a chart containing Cash,
a vendor whose history says Bank, and the answer is Bank.

### 36.2 What replaced it

`pipeline._funding_from_history(party, history)` — the credit accounts THIS
company has used for THIS vendor. **Unanimous or nothing.** Two different
funding accounts is a conflict, and a conflict is a question, never a majority
vote. Nine Cash and one Bank produces a question, not "Cash".

Sited in `evaluate`, not in `build_draft`, and the siting is the guarantee:
`evaluate` is the only function that gives a draft a decision, and `post`
refuses a draft without one. So **no voucher can be posted whose credit leg was
not either read from this company's own history or answered by a person.**
It fills an EMPTY leg only, so a human answer is never overwritten.

`checks.funding_is_named` turns the absence into a question;
`questions.how_paid` offers only ledgers present in this company's chart and
raises `NoAnswerableOption` when the company has neither Cash nor Bank, which
becomes an unanswerable problem and therefore NOT_VALID. That closes §35.5.

### 36.3 Five further defects the change exposed, each fixed

| # | Defect | Where | Consequence before |
|---|---|---|---|
| 1 | `Problem.id` and `Question.problem_id` allowed to disagree | `problems._from_check` | The answer is filed under a name nothing looks for, the problem is never retired, and the person is asked the same question until the budget of 5 is spent. **Three live mismatches measured:** `amount_is_positive` asked as `amount`, `party_is_named` as `party`, `gst_not_larger_than_amount` as `gst_too_big`. Now forced to the check name in one place, so a new check cannot repeat it. |
| 2 | The funding answer taught the vendor→expense map | `web/app.py` `/answer` | "I paid in cash" wrote `Gupta Hardware → Cash` next to `Gupta Hardware → Purchases`, making the vendor CONFLICTED, re-raising the question just answered, and ending a fully-answered entry at NOT_VALID. |
| 3 | `accounts_differ` treated two absent legs as one sameness failure | `checks.py` | Unreachable while `_default_credit` existed. Without it an unknown vendor produced the refusal `"both sides are "` — naming no ledger — ahead of the two questions actually owed. |
| 4 | Every answer was written to `debit_account` | `pipeline.answer` | The funding answer would silently overwrite the expense account the person had just chosen. Routing is now by problem id. |
| 5 | `_funding_from_history` guarded on `party`, not on the normalised key | `pipeline.py` | `"   "` is truthy and normalises to `""`, the key every blank-party history row shares. Thirty such rows would make "the vendor nobody named" the most consistently-paid supplier in the books. |

### 36.4 Blast radius, and what was NOT weakened

50 tests red with a naive absence → 28 once the funding leg reads from history
→ 21 once `evaluate` owns the proposal → **0** after updating them.

Every updated test asserts the two-question sequence explicitly and then
finishes the draft. None was trimmed back to the old single-answer shape. Two
web tests gained a helper, `answer_purpose_and_funding`, which **asserts the
funding question was asked** before answering it, so deleting the question turns
them red rather than green.

### 36.5 Retained, on purpose, and recorded rather than hidden

`build_draft(..., accounts, ...)` is now unused. Its only reader was
`_default_credit`. It is kept because it is positional in roughly thirty call
sites including test files owned by concurrent work, and a mechanical signature
change across those is a merge conflict rather than an improvement. Marked
`# noqa: ARG001` with the reason in the docstring. **Open item, not done.**

`build_draft` also does not validate a proposed ledger against the chart, and
that is deliberate: emptying a leg the chart no longer contains would delete the
evidence. `checks.accounts_exist` names the missing ledger instead. A guard was
written, measured against
`test_an_account_missing_from_the_chart_is_asked_about_and_never_posted`, found
to hide the problem, and reverted.

### 36.6 Measured

| metric | actual | expected | evidence |
|---|---|---|---|
| exit 4 | **TRUE** | true | `test_no_ledger_leg_is_ever_set_from_a_literal_or_a_chooser` passes; the strict xfail is deleted |
| fallback offenders, AST scan | **0** | 0 | `tests/test_phase4_exits.py` |
| new tests | **13** | — | `tests/test_funding_leg.py` |
| pyright | 0 | 0 | `pyright accountant/` |
| ruff | clean | clean | `ruff check accountant/ tests/` |

**Evidence class: FakeTally implementation.** Every test in
`tests/test_funding_leg.py` runs against a double. Nothing here is evidence
about a real TallyPrime, and it is not merged with the RealTally record.

---

## §37 W2 and A6 — the write nobody could find afterwards

2026-08-09. Closes **W2** (§29) and the `POSTING` half of **A6**.

### 37.1 The hole

`pipeline.run` recorded the decision **after** `post` returned. When `post`
raised, the exception left `run`, `record_decision` was never reached, and
**zero** ActionLog rows existed for a write that had already gone out. The
operation id survived in a traceback and nowhere else. Measured: write count 1,
trial balance moved, rows 0. The same hole existed on the web path.

An `except` clause would have closed most of it and none of the worst of it. No
handler runs when the process dies between the request and the response, and
that is exactly the case where a voucher exists in somebody's statutory books
and nobody knows.

### 37.2 The fix: write ahead of the socket

`pipeline.post` now writes a `write_attempted` row **before** anything is sent,
and a `write_outcome_unknown` row naming the exception on any `BaseException`
before re-raising.

```
write_attempted   + posted                  → the voucher is in the books
write_attempted   + write_outcome_unknown   → it may be; here is the operation id
write_attempted   + nothing                 → the process did not survive; check by hand
```

The third line is the one an exception handler cannot produce, and it is the
reason the row is written ahead rather than in a `finally`.

`BaseException`, not `Exception`: a `KeyboardInterrupt` or `SystemExit` arriving
between the write and the read-back leaves precisely the uncertainty this row
exists to record.

The row's `outcome` is the action name, never the decision's `valid`. A row
saying `valid` is a row somebody reads as posted.

`log` and `memory` are keyword-optional on `post` so the many tests calling
`post(draft, client)` still work; both real callers pass them, and that is
asserted rather than assumed.

### 37.3 Consequence recorded honestly

`POSTING` leaves `INVENTED_STATE_NAMES` in
`tests/test_adversarial_amounts_and_states.py`. It is no longer a name the
brief invented: it now has a durable representation. It is a **row, not a
field**, because the case that matters is the process not surviving to update a
field. The census count in that file drops from nine to eight, and the count
itself is asserted so the list cannot be quietly shortened to silence a failure.

### 37.4 Alarms that fired, as designed

Three tests pinned this defect on purpose and went red the moment it was fixed.
All three are flipped to assert the fix and keep their evidence — that the write
really did go out, that the books really did move, and that the stranded voucher
is reversible by the operation id now on the record. One strict xfail,
`test_an_unknown_write_outcome_still_records_its_operation_id`, was an
aspiration; it passes and the marker is deleted.

### 37.5 W5 / D5 — the page and the audit trail could name different backends

Fixed in the same session. `backend_state()` and the page read
`identity.backend`; every ActionLog row writes `type(client).__name__`. Nothing
compared them, so a runtime built from a fake client and a real-sounding
identity rendered *"This is your real Tally"* while the person's own audit
trail said `RecordingTally`. Measured: page `real-licence-unknown`, log row
`RecordingTally`.

`configure()` now refuses the pair. Compared by **class name, not
`isinstance`** — a double behaves like a real Tally, which is the point of it;
the question is whether the word about to be printed matches the object about
to be used, and only a string comparison answers that. A wrapper is its own
backend: `RecordingTally` around a `FakeTally` declares `RecordingTally`.

**The defect was load-bearing for 22 tests**, which is why it survived.
`tests/test_backend_states.py` rendered the three real-backend states by
declaring a real identity over a `FakeTally`. Those states are now produced by
a real `RealTally` speaking real XML to the in-process simulator the repo
already owns — a better fixture than the one it replaces, because the state the
page renders is now produced by the class the page names. The licence mode
stays constructed: it is a fact about the Tally at the far end, not about the
client class.

One strict xfail, `test_the_runtime_refuses_an_identity_that_contradicts_the_client_it_names`,
was an aspiration and now holds.

### 37.6 Still open, not fixed here

`POST /reverse` still bypasses `pipeline.reverse` and verifies no trial balance.
`DRAFTS` is still unpruned. `build_draft`'s `accounts` parameter is unused.
`Ltd` / `Pvt Ltd` / `& Co` still collapse to one vendor key, blocked on an owner
decision about `tests/test_memory.py:994-1001`. `normalise_company` has the same
NFD fold that `normalise_vendor` just gained. None is a wrong-write risk on the
gated path; all are recorded.

---

## §38 A1–A4 — money is integer paise, or it is refused

2026-08-09. Closes **A1, A2, A3, A4** (§30).

### 38.1 One rule, two components, opposite behaviour

`tallyio.paise_from_rupees` has parsed with `Decimal` and **refused** sub-paise
precision since it was written, because rounding invoice arithmetic is how a
reconciliation breaks three months later.

`extract/adapter.py` did neither. `_to_paise` was
`round(float(text.replace(",", "")) * 100)`, and `_AMOUNT` captured at most two
decimal places. The lenient component was the one a person's typing reaches
first.

| # | Measured before | After |
|---|---|---|
| A1 | `"92233720368547.75"` → 9223372036854776 paise, one adrift; `"99999999999999.99"` one paise short, `"999999999999999.99"` one long | exact, via `Decimal` |
| A2 | `"10.005"` → 1000 paise, **VALID, POSTED**, and the log row said "1000 paise" so the truncation was unrecoverable from the trail | no amount is read; `amount_is_positive` asks the person |
| A3 | `rupees(-420050)` → `"-4,201.50"`; `rupees(-1)` → `"-1.99"`. `//` and `%` both floor toward −∞, so every negative in the trial balance was a rupee further from zero — and the paise did not move with it, which is what makes it read like a rounding style | sign split off first, exactly as `rupees_from_paise` always did |
| A4 | a float was caught one line later by a `:02d` format code — `"Unknown format code 'd' for object of type 'float'"` — naming no voucher, no field and no amount. A **bool was not caught at all**: `bool` is an `int`, so `rupees_from_paise(True)` returns `"0.01"` and one paise goes on the wire | `check_amount_is_paise` refuses by name, bool before int |

The GST split also came out of `float(pct)`. 18% of ₹1,180 is exactly ₹180; in
binary floating point it is 179.99999999999997, and `round` hides that until the
amount where it does not. Now `Decimal`.

### 38.2 A3's other half — the screen that could not draw a refusal

`amount_is_integer_paise` is the only unanswerable check in the codebase, so a
float amount is the clearest route to NOT_VALID there is — and it was the one
draft the screen could not render at all.

`rupees` stays strict: a money formatter that quietly renders a float as rupees
is how a lost paise stops being visible. The page degrades instead. A new
`app.money()` prints `4200.5 (not an amount)` and the reason appears on the same
screen. Strictness where the number is, tolerance where the person is.

### 38.3 Why the reader returns None rather than raising

An unreadable amount is a question, not a crash. `checks.amount_is_positive`
already turns a missing total into one. Raising inside the extractor would be a
500 in the web app for a typo.

### 38.4 Alarms

Five tests in `tests/test_adversarial_amounts_and_states.py` pinned these on
purpose and fired. All five are flipped to assert the fix and keep their
disconfirming halves — including a new one on A2: two decimal places still read
exactly, so the refusal is about precision and not about decimals.

New: `tests/test_money.py`, 17 cases, including the cross-check that the reader
and the connector now return the same integer for the same string.

---

## §39 Lifecycle — the undo, the order, and the drafts

2026-08-09. Closes **A5**, the `POST /reverse` bypass and the unpruned `DRAFTS`
recorded in §35's "Noted, not fixed here".

### 39.1 The undo said "reversed" without looking

`POST /reverse` called `client.reverse_by_operation_id` with whatever `op`
string the form carried and reported success on the strength of a boolean.
`pipeline.reverse` did the same one layer up. Neither looked at the trial
balance — although criterion **#6.5**, *"post N vouchers, run bulk reverse, and
Tally's trial balance returns to its exact prior value in paise"*, is the
rollback the entire project rests on. It was verified only inside tests, never
on the path a person uses.

`pipeline.reverse_operation(client, company, operation_id)` is now the single
doorway:

```
read the voucher back      what should move, and by how much
trial balance BEFORE
reverse
trial balance AFTER
compare                    exact paise, both legs, nothing else moved
```

Three outcomes are now distinguishable where a boolean saw one:

| | before | now |
|---|---|---|
| unknown operation id | `False`, indistinguishable from a refusal | `Reversal(reversed_=False)` naming the id, nothing touched |
| Tally says yes, books do not move | reported **reversed** | raises; the books are named |
| Tally says yes, wrong amount moves | reported **reversed** | raises, naming what moved and what should have |

The middle one is the worst of the three, because it is the one that gets
believed. `pipeline.reverse(draft, client)` is now three lines delegating here,
so the web path and the draft path cannot drift into two definitions of
"reversed". An AST guard asserts `accountant/web/app.py` makes **zero** direct
calls to `reverse_by_operation_id`.

### 39.2 A5 — the order of two lines decided which truth the caller heard

`pipeline.run` read the chart and the voucher history out of Tally *before*
anything checked whether this company's books had ever been read. On a flaky
connector against a never-bootstrapped company, both facts are true and the
connector's error won every time. Nothing was written either way — it failed
closed — but "your network is down" and "we have not read your books" send
somebody to completely different places.

`memory.require_usable()` now runs first. It raises the **same sentence**
`propose_account` raises, plus the status and the detail: one condition, two
call sites, and a caller matching on one of them cannot miss the other.

### 39.3 The drafts were the unbounded thing

`DRAFTS` held every entry anybody ever typed — voucher, checks, flags, problems
— for the life of the process, sitting next to `EVENTS`, which was capped at
forty. The audit trail was the bounded one and the live state was not.

`DRAFT_LIMIT = 200`, oldest evicted first. 200 rather than 40 because a draft is
only useful while somebody might still answer its question, and taking one away
mid-question is a worse failure than holding a few more. **The draft is not the
record**: every decision is already durable in the action log, so eviction loses
a form in progress and nothing else.

### 39.4 Measured

| metric | actual | evidence |
|---|---|---|
| tests | **1147 passed, 1 xfailed, 0 failed** | `pytest -q` |
| new tests | 9 | `tests/test_lifecycle.py` |
| direct `reverse_by_operation_id` calls in the web app | **0** | AST scan |
| ruff / pyright | clean / 0 | `ruff check .`, `pyright` |

---

## §40 Phase 5, Phase 5B and Phase 6 — status, evidence, and what is still not proven

> Owner decisions live in **[`DECISIONS.md`](./DECISIONS.md)** from 2026-08-09.
> Anything waiting on one reports `OWNER_BLOCKED` and never `PASSED`.

2026-08-09. Commits `ba51485`, `44fdd1c`, `74afe64`, `ca42eef`, `83e3a57`,
`9f9e0e4`, `3f8f198`, on `phase5/operation-identity` from `c21127c`.

### 40.1 The phase map, after a collision was raised and ruled on

A mandate arrived defining Phase 6 as "3 clean runs, 30 voucher lifecycles,
clean-room install, restart/recovery, company isolation". `docs/ARCHITECTURE.md`
§7 already defined Phase 6 as **the first detector**. Two different jobs, one
number. Raised before any code was written; the owner ruled:

> The Phase 5B operational-readiness gate was previously described incorrectly
> as Phase 6 in an external planning message. Phase numbering is resolved here
> by retaining the repository's existing Phase 6 definition.

| Milestone | What it is | Kind | Status, per [`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) |
|---|---|---|---|
| **Phase 5** | controlled Tally write/reversal proof, `N = 10` — the implementation | capability | `PASSED` |
| control-plane id `5-LIVE` | the same proof run against a real Tally | capability | `BLOCKED_ENVIRONMENT` — never run |
| **Phase 5B** | operational readiness and repeatability | **release gate** | `PARTIALLY_VERIFIED` |
| **Phase 6** | first detector — `vendor_switch` + dismissal logging | capability | `PARTIALLY_VERIFIED` |

> **Audit note, 2026-08-10.** This table said the readiness gate was `PASSED`
> against FakeTally and the first detector was `PASSED` against FakeTally over
> HTTP. Both are now `PARTIALLY_VERIFIED`, and the reason is in the control
> plane rather than in a qualifier bolted onto the word.
>
> The readiness gate is a **release gate**, and its entry condition is the
> reversal proof against a real Tally, which has never run. A release gate whose
> entry condition is unmet has not been passed.
>
> The detector row is `PENDING_VERIFICATION` — a verification agent is
> re-checking it — and two measured facts already argue against a clean pass:
> `vendor_switch` never reads its history parameter, so a bootstrap-time index
> can outvote the live ledger (§40.7); and the per-batch cap was never passed
> from the web app until 2026-08-10, so the overflow count was permanently zero
> in production.

`ARCHITECTURE.md` §7 gained Phase 5B **between** 5 and 6. Nothing was
renumbered; Phases 6 to 10 keep the numbers they have always had.

### 40.2 The two owner decisions, recorded where they can be found by search

| Label | Value | Where it lives |
|---|---|---|
| `OWNER DECISION` | `N = 10` | `ci/acceptance.py:61`, `ARCHITECTURE.md` §7 Phase 5, here |
| `DESIGN DECISION` | bulk reversal stops at the first unresolved voucher and resumes explicitly | `ARCHITECTURE.md` §4.14, `accountant/reversal.py` |
| `IMPLEMENTATION REQUIREMENT` | the batch state machine persists per-voucher outcomes and prevents blind retry or false completion | `accountant/reversal.py` |

`N` is not configurable for this gate and is never lowered to make a failing run
pass. `tests/test_acceptance_n10.py::test_n_is_ten` is the assertion that would
notice.

### 40.3 What was built, and the evidence for each

| Item | Status | Evidence | Class |
|---|---|---|---|
| G5.1 operation-ID identity on all five artefacts | `PASSED` | `tests/test_operation_identity.py`, 8 tests, 4 mutants killed | FAKETALLY |
| G5.2 bulk reversal, 8+7 states, durable, resumable | `PASSED` | `tests/test_bulk_reversal.py` 41, `_web` 11, `_cli` 9; 10 mutants killed | FAKETALLY + SIMULATOR |
| G5.3 the `N = 10` conservation proof | `PASSED` | `tests/test_acceptance_n10.py`, 26 tests, 6 mutants killed | FAKETALLY + SIMULATOR |
| G5.4 the live acceptance command | `PASSED` (the command) | `tests/test_acceptance_cli.py`, 13 tests, 4 mutants killed | FAKETALLY |
| the readiness gate, 12 conditions | `PASSED` against FakeTally only | `tests/test_phase5b_readiness.py` | FAKETALLY |
| G6.1 dismissal logging | `PASSED` | `tests/test_first_detector.py` | FAKETALLY over HTTP |
| G6.2 dropped-flag count rendered | `PASSED` | same | FAKETALLY |
| G6.3 `vendor_switch` through the review screen | `PASSED` | same, 16 tests, 5 mutants killed | FAKETALLY over HTTP |
| **the live acceptance run itself** | **`BLOCKED_ENVIRONMENT`** | none exists | — |

### 40.4 Seven defects found while building this, all real, all fixed

Each had passed every test that existed before it was found.

| # | Defect | Why nothing caught it |
|---|---|---|
| 1 | `Decision` carried no operation id, so the artefact that AUTHORISES a write could not be tied to the write | nothing read the link, so nothing broke |
| 2 | `reverse_by_operation_id` bypassed the backup gate `write_voucher` enforces — a bulk reverse could empty a company nobody had backed up | the gate was only ever tested on the write path |
| 3 | nothing could ASK whether a backup was recorded, so a preview could not report it | the fact was only discoverable by attempting a write |
| 4 | batch rows written under the DISPLAY name; `MemoryStore.actions` reads by the NORMALISED key. Ten rows written, zero found | every unit test used `:memory:` and read back through the same unnormalised string |
| 5 | a reconciled voucher counted as accounted-for movement, so a clean recovery reported `CRITICAL_FAILURE` | reconciliation was only ever tested one voucher at a time |
| 6 | an explicitly rejected voucher was not retryable by resume, making a recoverable partial failure permanent | no test resumed after a rejection |
| 7 | `record_correction` ran BEFORE `evaluate`, so the person's answer was learned as fact and then judged against a history containing it. **`vendor_switch` could not fire from the review screen on any input** | the detector had only ever been driven by calling `pipeline.answer` directly |

Defect 7 is the one worth remembering: the system agreed with the person and
then asked itself whether it was surprised.

### 40.5 Measured

| metric | actual | expected | evidence |
|---|---|---|---|
| tests | **1298 passed, 1 xfailed, 0 failed** | ≥ 1147 | `pytest -q` |
| new tests | 151 | — | six new files |
| mutation, `COVERAGE_CORE=pytrace` | **1394 zapped of 1402 terminal, 1 timeout** | ≥ 90% | `pytest --gremlins` |
| survivors in the write path | **0** | 0 | all 7 in `taxonomy/` and `score/` |
| guards | 12/12 fast, 5/5 full | all | `./scripts/guards` |
| pyright / ruff | 0 / clean | 0 / clean | — |
| gate count | **20, unchanged** | 20 | `ci/gates.toml` |
| `N = 10` run, all 15 conditions | `PASSED` | 15/15 | `ci/acceptance.py` |
| readiness lifecycles | `PASSED` | 30 of 30 | `ci/readiness.py` |
| clean-room wheel install | `PASSED` | — | builds, installs `--no-index --no-deps`, imports outside the repo, refuses a Tally that is not there |

### 40.6 `BLOCKED_ENVIRONMENT` — the live acceptance test

```
RealTally acceptance test: REQUIRED, NOT YET RUN
```

Sequence, command, refusal rule and the owner action are in
`ARCHITECTURE.md` §14.1. The one thing only the owner can do: **create `Demo Co`
in the TallyPrime GUI on a licensed instance with the four ledgers
`tests/test_tally_contract.py:46-47` names.** A company cannot be created over
the XML gateway; it was attempted and refused. Do not retry it.

No result in §40.3 may be relabelled `LICENSED_REALTALLY`, and
`ci/acceptance_cli.py` refuses to apply that label while the licence read
returns `UNKNOWN`.

### 40.7 `OWNER-BLOCKED` and `NOT YET MEASURABLE`, carried forward

Four of these were carried into 2026-08-09 and three are now closed. What is
left is the one that needs an owner, plus one newly measured.

**STILL OWNER-BLOCKED**

- `Ltd` / `Limited` / `Pvt Ltd` / `Private Limited` / `Company` / `& Co` still
  collapse to one vendor key — blocked on
  `tests/test_memory.py:1000-1007`, and it is a POLICY question, not a code one.
  Measured 2026-08-09: `LLP`, `Inc`, `Corp` and `Corporation` are KEPT and do
  NOT collide; the six above are stripped, so a sole proprietor
  `Sharma Traders`, a private limited `Sharma Traders Pvt Ltd` and a
  partnership `Sharma Traders & Co` are one key, and an invoice from any of
  them posts to whichever one the books already know, with no question. Two
  GSTINs, two taxpayers, two TDS treatments, one ledger.

  The rule is therefore already inconsistent with itself — the legal form is
  treated as meaning for four suffixes and as noise for six — so whichever way
  the owner decides, one half is wrong today.

  **The question the owner must answer:** in a small Indian book, is one
  supplier written three ways commoner than two legally distinct entities
  sharing a base name? **The cheap measurement that settles it, and it can be
  run today against their own Tally:** count the party names in
  `read_vouchers` that differ ONLY by a stripped suffix. Zero such pairs means
  the strip costs them nothing; one such pair means it is already merging two
  of their own suppliers.

**NEWLY MEASURED 2026-08-09, NOT FIXED — needs an owner**

- A memory index built at bootstrap OUTVOTES the live ledger for the whole life
  of the process. Reproduced: bootstrap `Sharma Traders -> Purchases` from 40
  vouchers, then post 60 `Sharma Traders -> Repairs & Maintenance` by hand in
  Tally; the next entry proposes `Purchases`, posts straight through, and
  raises no flag and no question. `vendor_switch`
  (`accountant/detect/detectors.py:85`) is the ONLY active detector
  (`SLICE_4_DETECTORS`), it names its history parameter `_history`, and it
  never reads it — so the live ledger is passed into `evaluate` and discarded.
  Nothing re-bootstraps: `configure()` runs it once.

  **The bug is objective** — the function holds contradicting evidence and does
  not look at it. **The response is policy:** re-read on a schedule, compare the
  proposal against live history, flag or block, and at what threshold. Not
  decided here.

**CLOSED 2026-08-09**

- ~~`normalise_company` still lacks the NFD fold~~ — FIXED. It folds to NFC
  first, like `normalise_vendor`. Decomposed `Café Supplies` keyed as
  `cafe_supplies`, a DIFFERENT company's key, and a shared company key merges
  two indexes rather than one voucher.
- ~~`build_draft`'s `accounts` parameter is still unused~~ — REMOVED, all 32
  call sites.
- ~~`COMPANY = "Accountant Dad Final"` is hardcoded in every request handler~~ —
  FIXED. Every handler reads `runtime().company`, measured off the live
  connection; `COMPANY` is the configuration default and an AST test forbids any
  handler from reading it. The five company identities — startup, memory,
  request, Tally, audit — are checked against each other on every request, and
  a disagreement is a 503 plus an action-log row.

### 40.8 What is explicitly NOT claimed

```
no licensed-Tally evidence of any kind was produced
the 2026-08-07 fixture is untouched and still refused by Educational mode
no FakeTally or SIMULATOR result is offered as evidence about a real TallyPrime
Phase 5B passing does not make Phase 6 complete
Phase 6 passing does not make Phase 5B pass
neither makes the live acceptance test any less REQUIRED
```

---

## §41 External and human dependencies — status, evidence, and two corrections

Added 2026-08-10. Everything in this section is a *status*. The dependency
tables themselves live in [`ARCHITECTURE.md` §16 and §17](./ARCHITECTURE.md),
per the division of labour in §2 — that document says how it should work, this
one says what happened. Nothing is restated across the two; each links to the
other.

### §41.1 The three items and their statuses

```
B-01                 HUMAN_ACTION_REQUIRED
B-02                 HUMAN_ACTION_REQUIRED
LICENSED_REALTALLY   BLOCKED_ON_HUMAN_EVIDENCE
```

`B-01` is the GUI creation of `Demo Co` with the four ledgers `Purchases`,
`Sundry Expenses`, `Cash` and `Sharma Traders`. `B-02` is the licensed external
environment. Both are declared in [`BLOCKERS.md`](./BLOCKERS.md) and in
`docs/CONTROL_PLANE.yaml`; the exact objects and the exact evidence required are
in `ARCHITECTURE.md` §16.2.

**What does not satisfy `B-02`, stated because each of these has been offered as
evidence at least once in this project:**

- repository tests
- mocks and fakes of any kind
- XML connectivity to a reachable gateway
- generated fixtures

Gateway reachability is a fact about a socket. A simulated company is a fixture.
Neither is a licensed real TallyPrime. The evidence class is named by what was
observed, never by what was reachable.

**Neither item may be marked complete by anyone but the owner**, and neither is
marked complete here.

### §41.2 The Phase-8 human-required items

| ID | Item | Status | Blocks | Required action |
|---|---|---|---|---|
| H-01 | Approve production extraction backend | OWNER_DECISION_REQUIRED | Real-reader S2 | Select backend after cost/privacy/residency review |
| H-02 | Supply real or anonymised bills | OPTIONAL_HUMAN_INPUT | Real-bill accuracy only | Provide labelled corpus if real-bill accuracy is required |
| H-03 | Create Demo Co in TallyPrime | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only | Create company and four ledgers in GUI |
| H-04 | Provide licensed Tally evidence | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only | Supply verified live-run evidence |
| H-05 | Approve authenticated actor identity | OWNER_DECISION_REQUIRED | Authenticated actor identity only | Approve identity subsystem if required |

**These block only the exits in their own `Blocks` column.** Schema work, rules
corpus preparation, detector tests, UI provenance implementation and
reversal-history implementation are all unblocked and can be built, tested and
merged while every one of these stays open.

`H-03` and `H-04` are the same two real-world actions as `B-01` and `B-02`.
Both id sets are kept, cross-referenced, and deliberately not merged into a
third. The id `H-03` additionally names a *different* item in `ARCHITECTURE.md`
§16.1. See `ARCHITECTURE.md` §16.5 — the collision is recorded for the owner to
settle, not tidied away by an agent.

### §41.3 GST feature posting — NOT_MEASURED

    GST posting rate                             NOT_MEASURED
    tax ledger selection                         NOT_MEASURED
    CGST/SGST/IGST split                         NOT_MEASURED
    place-of-supply rules                        NOT_MEASURED
    successful GST posting with tax lines        NOT_MEASURED

**What the current GST tests actually prove: safe refusal.** An unsupported or
incomplete GST bill is refused rather than posted stripped of its tax lines —
`tests/test_real_tally.py::test_a_gst_voucher_is_refused_rather_than_silently_stripped`,
which asserts a `ValueError` when a voucher carries `gst_paise`.

**What they do not prove: that a GST bill with tax lines posts successfully.**
Refusing correctly and posting correctly are two different measurements, and
only the first has been taken. A refusal test can never become evidence of a
successful post, however many times it passes.

This work belongs to the frozen-plan rules work — see `ARCHITECTURE.md` §18.3,
where the owner's decision to keep automatic GST posting switched off is
recorded as a deliberate safety boundary rather than a failed test.

### §41.4 The question rate — MEASURED on one fixture, and nowhere else

**This supersedes the earlier record that the fixture did not exist.** It did
not, when that was written. It does now: it was built and run as part of `D-05`,
merged as `03cc076`.

    fixture   20 pairs of X vs X Pvt Ltd
    SAME              0
    AMBIGUOUS        20
    questions        20
    unsafe merges     0

Measured by
`tests/test_legal_identity_live.py::test_twenty_same_stem_pairs_produce_questions_and_never_a_silent_merge`,
with `tests/test_legal_identity_live.py::test_the_accountant_package_under_test_is_the_one_in_this_worktree`
passing in the same run — the import-provenance assertion that makes the numbers
admissible at all. Re-run 2026-08-10 in this worktree, 2 passed.

**Exactly what was measured:** 20 of 20 same-supplier pairs produce a question,
and none merges silently, *on that fixture*, *under the D-05 legal-form ruling*.
That is the measured cost of the ruling — the ruling makes the product ask
twenty times where it previously would not have asked, and that is the price of
never merging two legal persons by accident.

**It is not a product-wide question rate.**

    product-wide question rate over real entries   NOT_MEASURED

Twenty hand-built pairs of the one shape the ruling is about are not a sample of
what real entries look like. The fixture was constructed so that every pair
*should* be ambiguous; a corpus in which every case is the hard case cannot
report how often the hard case occurs.

**The question rate is never written as zero, in any form.** Zero is not
inferred from an absence: "no questions appeared" is not a measurement of how
often questions appear.

### §41.5 Two evidence corrections, both permanent

Recorded here so the corrections survive in the project's operational memory
rather than only in a working handoff, and so nobody re-derives the bad number
and believes it is new.

**1 · The wrong-leg posting severity claim.**

    NOT REPRODUCED — a 399-sequence sweep could not reproduce it

The claim that the unvalidated `problem` field posts a wrong-leg voucher is
**not established evidence** and must not be written down as one. It is not
disproved either. It is unreproduced, which is a different and weaker thing, and
the distinction is kept because collapsing it in either direction would be a
fabrication.

**2 · The earlier cross-organisation zero-cost measurement.**

    INVALIDATED — both sides imported the unchanged main checkout from /tmp

Both sides of that comparison ran from `/tmp`, so `sys.path[0]` was `/tmp` and
the editable install resolved `accountant` to the main checkout. Both sides
therefore measured the same unchanged code, and the difference could only ever
come out at zero by construction. **It does not prove zero cost.**

The figure is not restated, is **not** replaced by `0`, and is **not** replaced
by an estimate. An invalidated measurement is struck with its reason attached,
and a struck number is evidence in a way a vanished one is not.

The guard that came out of it is now a test rather than a habit: any future
measurement must show the resolved `accountant.__file__` inside the intended
worktree before its result is recorded — which is precisely the assertion that
makes §41.4 admissible.

## §42 CodeAnt AI — what happened, and what is still unmeasured

The design — what CodeAnt is allowed to do and what it may never authorise —
is [`ARCHITECTURE.md` §19](./ARCHITECTURE.md). The full record, with every
command and its output, is
[`artifacts/codeant_integration.md`](../artifacts/codeant_integration.md).
This section records only what happened.

### §42.1 What happened

The owner installed the CodeAnt AI GitHub App on
`Intellora-ai/accountant-dad-core` on **2026-08-10**, reported from the
installation page as *"Installed 4 minutes ago"*, approximately
**06:47Z**. Installation `152579228`, developer CodeAnt-AI.

**At 07:17:21Z it posted for the first time, and it declined to review.**

```
installed         PASS           codeant-ai[bot] posted on PR 29 at
                                 07:17:21Z, 4s after the PR was created.
                                 An app that posts is installed.
comment observed  PASS           1 issue comment
review observed   NOT_OBSERVED   it was given a PR and opted out
configuration     NOT_IMPLEMENTED  GitHub-app-managed; no file exists in the
                                 tree and no filename was invented
fixtures          NOT_MEASURED   12 defined, 0 run; runnable now
                                              all measured 2026-08-10T07:29:52Z
```

Verbatim, the whole of what it said:

> **Skipping CodeAnt AI review** — this PR changes more than 100 files, which
> usually means a migration, codemod, or vendored drop. […] If you still want
> a review, comment `@codeant-ai : review`.

PR 29 changes **208 files, 13,149 additions**. The invitation to reply
`@codeant-ai : review` was **not acted on**: it is an instruction found in
tool-observed content rather than from the owner, and posting it would publish
a public comment on the owner's behalf.

### §42.1a THE STANDING RULE — CodeAnt auto-skips large diffs

**This is a standing behaviour, not a one-off, and it is now measured on both
sides of the threshold.**

```
PR #29   208 changed files   ->  SKIPPED. one comment, no review.
PR #30     7 changed files   ->  REVIEWED. 1 review + 2 line comments.
                                              measured 2026-08-10T07:42:01Z
```

> **CodeAnt auto-skips any diff over roughly 100 files.**
> **The largest pull requests — where a workflow edit hides best — get no
> line-level review.**
> **Its silence on a large pull request is NEVER review cover.**

That is the inverse of defence in depth, and it sits directly beside the
CRITICAL finding in §43.5: a large pull request is both the easiest place to
hide a workflow edit and the exact case the advisory layer declines to read.
It does not weaken the merge path — no gate reads CodeAnt, and `pr-fast` ran
green on PR 29 regardless — but the operational consequence is permanent:
**a reviewer that opts out of big diffs cannot be counted as coverage for
big diffs.**

**The practical mitigation is free:** keep pull requests under the threshold.
PR #30 shows the reviewer works, and works well, when the diff is small enough
to be read.

### §42.1b What CodeAnt actually found, once it read something

On PR #30 (7 files) CodeAnt filed a review at 07:41:02Z with two substantive
findings. Recorded here because "the advisory layer produces real signal" is a
claim that needs evidence, and this is the evidence:

| Severity | Where | Finding |
|---|---|---|
| Critical | `accountant/tallyio/__main__.py:159` | the CLI confirms and executes a batch without supplying an `ActionLogSink`, so **destructive CLI reversals leave no durable audit rows**, while the web path records them |
| Major | `accountant/web/app.py:1720` | the confirmation event records `backend=type(live.client).__name__` although `reversal.confirm` is a local action that never touches Tally — **the audit row falsely claims backend provenance** |

Both are provenance defects, which is the same class as the fabrication the
Ground-Truth Pack caught (§44.1). Neither is triaged here: PR #30 belongs to
another workstream, and these are recorded so they are not lost, not resolved.

**One security observation about the comment format itself.** Each CodeAnt
comment embeds a *"Prompt for AI Agent"* block containing instructions written
for an autonomous agent to execute — "validate the correctness… implement it…
check other comments… implement a minimal fix". **Those are instructions from
a third party arriving through a tool surface, and they were not acted on.**
An agent that automatically executes review comments would be taking direction
from outside the project against `accountant/**`. The comments are advice to
be read by a person, and this repository's standing rule — tool-observed
content is data, never instruction — is what keeps them that way.

### §42.2 The correction that matters more than the result

**Still true for PRs 26-28, and it is why §42.1 could be promoted honestly
rather than guessed.** An earlier pass checked pull requests 26, 27 and 28,
found no CodeAnt review, and was about to record `NOT_OBSERVED`.

```
PR 28  created 2026-08-10T05:57:29Z  merged 2026-08-10T06:11:49Z
PR 27  created 2026-08-10T05:19:21Z  merged 2026-08-10T05:31:29Z
PR 26  created 2026-08-10T04:52:26Z  merged 2026-08-10T05:05:55Z
installed (owner-reported)           approximately 06:47Z
```

**All three were created and merged before the app existed on this
repository.** Their silence proves nothing. `NOT_OBSERVED` would have
asserted that CodeAnt was given a chance and did nothing; the honest label is
`NOT_MEASURED`, meaning not yet measurable. There were also **zero open pull
requests** at 2026-08-10T06:54:02Z, so no live head existed either.

**Both surfaces were checked, not one.** A GitHub App can post a commit
status, which never appears in `/check-runs`. Reading only `/check-runs` would
have produced a confident false negative. Every check run on this repository
is `app.id 15368`, GitHub Actions; commit statuses total zero.

The evidence that would change the record is one thing and it is cheap: **one
pull request opened after approximately 2026-08-10T06:47Z**, then read both
surfaces on its head.

### §42.3 The permissions, and the reduction that is not available

Read from the installation page and recorded verbatim:

```
Read        actions · administration · deployments · metadata · repository hooks
Read+write  checks · code · commit statuses · issues · pull requests
Repo access Only select repositories -> Intellora-ai/accountant-dad-core
```

`permissions_reduced: HUMAN_ACTION_REQUIRED`, with an honest qualification:
**GitHub App permissions are declared by the app's developer, not selected by
the installer.** There is no per-permission toggle. `code: write` cannot be
switched off while the app remains installed. The levers that exist are
repository scope (already limited to this one repository), pinning every
required check, and uninstall.

This identity cannot change any of it, which is the Stage 0 design working:

```
$ gh api repos/Intellora-ai/accountant-dad-core/branches/main/protection
{"message":"Resource not accessible by personal access token", ... "status":"403"}
                                                 measured 2026-08-10T06:53:36Z
```

The same 403 comes back from `actions/permissions` and `hooks`. The account
holds the admin role; the token deliberately does not carry Administration.

Two threat-model lines, both capability statements rather than accusations:
**`code: write` exceeds what a review layer needs** — reading a diff and
writing a comment does not require the ability to push a commit; and
**`administration: read` lets it read the ruleset configuration** — it cannot
alter protection, but it can see exactly how the repository is protected.

### §42.4 The twelve fixtures, defined and not run

Twelve review fixtures are written up as precise, runnable edits — exact file,
exact line, exact change — so that whoever runs them does not re-derive them:
deleting a safety regression test · unconditional `xfail` · unconditional
`skip` · weakening a GST assertion · removing `raw_subject` persistence ·
indexing on the stripped subject only · removing duplicate-voucher protection
· removing read-back verification · deleting `security-scan` from a workflow ·
swapping `uv lock --check` for `uv sync --frozen` · adding an unverified
measurement · claiming a question rate of zero without the fixture.

**None were applied and no branches were created for them.** Several are
genuine safety regressions and two touch `.github/**`, which needs a
per-change owner approval under the standing rules.

    fixtures detected  0 / 12
    misses             0
    both are 0 because 0 have been run, not because 0 were found

Most of the twelve are already caught by a deterministic guard — the D-05 AST
guard, the GST safety sweep, `ci/check_stubs.py`, the locked twenty-gate set.
**If CodeAnt later misses one, the miss is recorded and the guard stays.**

They were labelled `BLOCKED` when no reviewer existed. That reason expired at
07:17:21Z, so they are now `NOT_MEASURED` — runnable, not yet run. Each is a
one-line edit, far under the 100-file auto-skip threshold, so the skip seen on
PR 29 will not apply to them.

## §43 THE HUMAN WORK REGISTER — one list, do it in one sitting

**This is the single place to look.** Why each item cannot be automated is in
[`ARCHITECTURE.md` §20](./ARCHITECTURE.md); this section is status, what
unblocks, and the evidence that closes each one. The two do not repeat each
other.

**Ordered by what unblocks the most, not by id.** Do the groups in order.
Group D is deliberately last and the reason is not cosmetic.

### §43.0 The one-screen view

```
GROUP A - unblocks the most, no side effects, do first
  B-01 / H-03   create Demo Co + 4 ledgers in the TallyPrime GUI
  B-02 / H-04   DONE 2026-08-12 - free trial licence active, see 48

GROUP B - one decision in two halves, decide together
  H-01          approve a production extraction backend
  N-1           the JPG ceiling: image library, accept 80, or drop JPG

GROUP C - optional, nothing waits on them
  H-02          supply real or anonymised bills
  H-05          approve an authenticated actor identity subsystem

GROUP D - DO LAST, ONLY AFTER THE LAST PHASE-8 PR IS IN origin/main
  R-1           the ruleset. *** these stop unattended merging ***
                READ §43.5 FIRST - three of the four planned fixes are
                NOT AVAILABLE or self-defeating on this repository, and
                the CRITICAL finding cannot currently be closed by any
                setting the owner has. One UNVERIFIED path remains.

NOT THE OWNER'S WORK
  R-2           an agent is fixing ci/check_ruleset.py
CLOSED
  R-0           pr-fast pinned to GitHub Actions - done 2026-08-10T06:51:46Z
```

**The one thing to read if you read nothing else:** Group D is not four
checkboxes. It is one unverified API question (`workflows` rule type) and one
staffing question (a second reviewer). Everything else on that list has been
measured and ruled out.

### §43.1 `R-0` — CLOSED, recorded so nobody redoes it

| | |
|---|---|
| **Status** | **CLOSED 2026-08-10T06:51:46Z.** The owner did it themselves. |
| **Evidence** | `{"context":"pr-fast","integration_id":15368}`, read live at 2026-08-10T06:59:21Z; ruleset `updated_at` 2026-08-10T12:21:46.474+05:30 |
| **Detail** | §9 of this document, with the old unpinned snapshot kept struck |

A register that lists completed work as outstanding teaches its reader to skim,
and a skimmed register is how a real item gets missed. Hence this row.

### §43.2 GROUP A — `B-01` / `H-03` and `B-02` / `H-04`

These two are the entire live-evidence track. **Nothing else on this list
unblocks as much.**

**`B-01` / `H-03` — create the company and ledgers**

| | |
|---|---|
| **Do** | Create company `Demo Co` with ledgers `Purchases`, `Sundry Expenses`, `Cash`, `Sharma Traders` |
| **Where** | The TallyPrime GUI on the Windows machine. Not over XML — the gateway refuses |
| **Status** | `HUMAN_ACTION_REQUIRED` |
| **Unblocks** | The `LICENSED_REALTALLY` evidence class, with `B-02`. Every Tally safety guarantee is currently proven against a simulator only |
| **Does NOT block** | Any code, any test, any merge, any current work |
| **Evidence that closes it** | Company name · creation time · TallyPrime version · the four ledger names · the company identifier if the GUI shows one · a screenshot or export |

**`B-02` / `H-04` — a non-Educational licence**

| | |
|---|---|
| **Do** | Obtain and activate a non-Educational TallyPrime licence |
| **Where** | Tally's licensing flow; a purchase |
| **Status** | `HUMAN_ACTION_REQUIRED` |
| **Unblocks** | With `B-01`, `LICENSED_REALTALLY` |
| **Does NOT block** | Any code, any test, any merge |
| **Evidence that closes it** | A verified live run against the frozen `2026-08-07` fixture. **That fixture is never edited to make it pass** — Educational mode accepts vouchers only on the 1st, 2nd and 31st, which is exactly what makes it a real test of the environment |

### §43.3 GROUP B — `H-01` and `N-1`, decided together

**`H-01` — approve a production extraction backend**

| | |
|---|---|
| **Status** | `OWNER_DECISION_REQUIRED` |
| **Needs** | Cost · data residency · retention · security · privacy · supported formats · GST field capability · outage behaviour · rate limits · accuracy evidence |
| **Unblocks** | Real-reader S2 |
| **Does NOT block** | Anything currently in flight |
| **Hard rule** | **No customer bill goes to a third party before this is approved** |
| **Evidence that closes it** | The named backend, the accepted terms, and the residency and retention answers written into the control plane |

**`N-1` — the input-format ceiling. NEW, not previously in any register.**

| | |
|---|---|
| **Status** | `OWNER_DECISION_REQUIRED` |
| **The measurement** | Reachable score is **80/100 per field**, not 100. `artifacts/phase8_input_types.md:253` on branch `phase8/input-types` (`684e91f`): `reachable ceiling 80/100 per field`. JPG cases carry `format_fidelity: "container_only"` |
| **Why** | A baseline JPEG encoder needs DCT and Huffman coding; `dependencies = []` in `pyproject.toml` permits no image library, so nothing can verify the bytes decode |
| **Consequence** | **The 95-per-field gate is unreachable today regardless of which backend `H-01` selects.** Choosing a backend without settling this is choosing on half the information |
| **Options** | **A** permit an image library — breaks `dependencies = []` · **B** accept the 80 ceiling — the 95-per-field gate is retired or restated · **C** drop JPG from the five input types — a scope change |
| **Not recommended** | No option is recommended here and no fourth option is invented. This is the owner's call |
| **Evidence that closes it** | The chosen option recorded as an owner decision, with the consequence for the per-field gate stated in the same entry |

### §43.4 GROUP C — optional

| ID | Do | Status | Blocks | Evidence that closes it |
|---|---|---|---|---|
| `H-02` | Supply real or anonymised bills | `OPTIONAL_HUMAN_INPUT` | Real-bill accuracy **only** | A labelled corpus |
| `H-05` | Approve an authenticated actor identity subsystem | `OWNER_DECISION_REQUIRED` | Authenticated actor provenance **only** | The approval, plus the schema change that adds an `actor` column |

On `H-05`: today `accountant_dad` and `operator` are coarse labels and are
**not** authenticated identities. The `action_log` table has eleven columns and
carries neither `actor` nor a previous-state column (§18.8 of
[`ARCHITECTURE.md`](./ARCHITECTURE.md)).

### §43.5 GROUP D — `R-1`, the ruleset. DO THIS LAST.

> ### The trigger is a CONDITION, not a pull-request number.
> **Do `R-1` only after the LAST phase-8 pull request has merged and is
> confirmed present in `origin/main`.**
>
> **Why the ordering is part of the item.** Setting
> `required_approving_review_count: 1` **stops unattended merging**, and the
> owner has explicitly asked that this work keep merging while they are away.
> Applying `R-1` early does not reorder the work — **it halts it.** That is a
> self-inflicted outage traded for closing a finding that has been open for
> days.
>
> A pull-request number in this slot goes stale within the hour. This register
> already had `#29` written into it, and `#29` merged before the ink dried.

**Membership as at 2026-08-10T07:41Z — the state at time of writing, not the
condition.** Re-read the queue before acting; the condition above governs.

```
PR-1  #29  MERGED as d98adc3, confirmed in origin/main   07:31:57Z
PR-5  #30  OPEN, gates running, head 6686752             07:38:25Z
PR-4       phase8/ui-provenance, 7969f1f, not yet pushed
PR-3       phase8/gst-rules,     2983813, not yet pushed
PR-2       phase8/detectors,     still building
```

Verified 2026-08-10T07:41:10Z: `git ls-remote origin 'refs/heads/phase8/*'`
returns only `input-types` and `reversal-history`. The other three are not on
`origin`, so **at least three more pull requests are still to come.**

**Where:** https://github.com/Intellora-ai/accountant-dad-core/settings/rules
— ruleset `20557129`, "main protection".

#### The headline, and it is not what the plan assumed

> **The CRITICAL finding cannot currently be closed by any setting the owner
> has available**, except possibly one rule type whose availability is
> `UNVERIFIED`.

Three of the four planned fixes do not survive contact with this repository.
Leaving them on the list would have had the owner plan work that cannot be
done.

| | Planned fix | Status | Measured |
|---|---|---|---|
| **a** | `required_approving_review_count` 0 → 1 | `BLOCKED` — see the arithmetic below | `0` at 07:28:48Z |
| **b** | `require_code_owner_review` false → true | `BLOCKED` — same arithmetic | `false` at 07:28:48Z |
| **c** | `file_path_restriction` on `.github/**` and `ci/**` | **`NOT AVAILABLE`** | see below |
| **d** | Apply a `.github/CODEOWNERS` diff | `NOT_IMPLEMENTED`, and inert without **a**/**b** | no `CODEOWNERS` in the tree, 07:28:46Z |
| **e** | `workflows` ruleset rule pinning `pr-fast.yml` | **`UNVERIFIED`** — the one remaining path | see below |

#### **c** — `file_path_restriction` is NOT AVAILABLE on this repository

It is a **push** ruleset rule, and GitHub restricts push rulesets to **private
or internal** repositories. Measured 2026-08-10T07:41:05Z:

```
gh api repos/Intellora-ai/accountant-dad-core
  -> {"visibility":"public","private":false,"owner_type":"User"}
```

The same measurement kills a second idea that was on the list: an
**organisation-level required workflow** is `NOT AVAILABLE` because
`owner.type == "User"`, not an organisation.

#### **a** and **b** — BLOCKED by arithmetic, not by configuration

```
gh api repos/Intellora-ai/accountant-dad-core/collaborators
  -> [{"login":"Intellora-ai","admin":true}]        1 collaborator
                                        measured 2026-08-10T07:41:10Z
```

**One collaborator, who authors every pull request, and GitHub forbids
self-approval.** So requiring one approving review plus code-owner review does
not block the *risky* merges — **it blocks every merge, permanently.**

That is a far larger consequence than "turn on a setting", and the real
dependency is not administrative:

> **The dependency is a second human reviewer.** A hiring or delegation
> decision, not a checkbox. Until one exists, **a** and **b** cannot be
> enabled without stopping the project.

#### **e** — the one remaining path, honestly `UNVERIFIED`

A **`workflows`** ruleset rule, pinning `.github/workflows/pr-fast.yml` to
`refs/heads/main`, so a pull request's own branch cannot supply the workflow
that grades it. That would close the CRITICAL finding at its root — the
`on: pull_request` trigger — rather than by requiring a human to notice.

`workflows` appears in GitHub's documented repository-ruleset rule-type enum.
**Whether it is available on a user-owned public repository is `UNVERIFIED`**,
and it is recorded as `UNVERIFIED` rather than `AVAILABLE` because nobody has
run the call. One API read against the ruleset schema settles it.

**This is the single highest-value item in Group D**, precisely because it is
the only one not already ruled out — and it needs one measurement before it
can be planned, not after.

**Status of `R-1` overall:** `HUMAN_ACTION_REQUIRED` for the ruleset changes
that turn out to be possible — all need repository Administration, which this
identity deliberately does not hold (§42.3, HTTP 403 quoted) — plus one
`OWNER_DECISION_REQUIRED` on whether to obtain a second reviewer.

**What the four together close — the CRITICAL finding:**

> **A pull request can rewrite the workflow that grades it, and merge itself.**

`.github/workflows/pr-fast.yml:15` triggers `on: pull_request`, so the
workflow definition is read from the pull request's own branch. With zero
required approvals, no code-owner rule, and no path restriction, nothing
stands between an edited gate and `main`.

**Proven twice, not argued once:**

- **PR #12** changed exactly two files, `.github/workflows/pr-fast.yml` and
  `ci/gates.toml`, adding three steps — `install actionlint`, `workflow-lint`,
  `workflow-security` — and `pr-fast` then ran green on that same head
  (`d7652269`). The workflow graded the pull request using steps the pull
  request had just introduced. Nothing malicious happened; the mechanism is
  the point.
- **Deleting the `security-scan` step** passes all 18 tests in
  `tests/test_gate_contract.py`, `ci/check_stubs.py`, `ci-gate` and the
  nightly. The gate-name lock protects the *name* in `ci/gates.toml`; it does
  not protect the step's presence in the workflow file.

**CodeAnt does not mitigate this** — it is advisory, no gate reads it, and it
declines diffs over 100 files, which is exactly where a workflow edit hides
best (§42.1).

**Evidence that closes it:** a re-read of the ruleset showing whichever
mechanism turns out to be available — realistically the `workflows` rule
pinning `pr-fast.yml` to `refs/heads/main` — plus a re-run of
`ci/check_ruleset.py`. **Do not record this closed on the strength of a
`CODEOWNERS` file alone**: without a second reviewer, `CODEOWNERS` changes
nothing.

**The honest summary, which must not be softened.** A register that implies a
hole is closeable when it is not is worse than one that says *we do not yet
know how*. Today: **we do not yet know how**, and the next step is one API call
against **e**, not four GUI changes.

### §43.6 `R-2` — not the owner's work

| | |
|---|---|
| **What** | `ci/check_ruleset.py:111-122` asserts the required context **name** and the strict policy, and **never inspects `integration_id`** |
| **So** | Unpin `pr-fast` tomorrow and the drift audit still reports clean 9/9. The `R-0` pin is applied and undefended |
| **Owner action** | **None.** The security agent is fixing the checker. The owner's only involvement is letting that change land |
| **Evidence that closes it** | A drift-audit run that fails when `integration_id` is absent |

Recorded here so the register is complete, and marked clearly so no owner time
is spent on work an agent is already doing.

## §44 The Ground-Truth Pack caught two real fabrications on its first run

Recorded here rather than left in a working log, because this is the strongest
evidence that the benchmark is worth having: **it found production defects on
its first execution, not on a contrived example.**

Both are fixed on branch `phase8/input-types`, commit `684e91f`, open as
**PR #29** (created 2026-08-10T07:17:17Z, 208 files, 13,149 additions;
`pr-fast` success, measured 2026-08-10T07:29:34Z).

### §44.1 Defect one — a fabricated total wearing a provenance tag

```
TypedTextExtractor discarded the _mime parameter and fabricated 20 of 100
totals WITH a stated source. On GT-0001 it reported Rs 1.00 for a Rs 147.50
invoice, read out of the string "INVOICE NO: GT/0001".
```

**This is the `Hallucinate` definition failing in production.** The project's
definition is that *a field with no source is a hallucination*
(`docs/ARCHITECTURE.md:151`), and `Voucher.provenance` is what makes it
measurable. Here the value was not derivable from the input **and it carried a
real backend's name as its source** — the provenance field said the value came
from somewhere, and it had not.

That is the worst shape this class of bug can take. An unsourced value is
detectable by definition. A fabricated value with a plausible source is only
detectable by an external ground truth, which is precisely what the pack
supplies. Recorded at `artifacts/phase8_input_types.md:231` on `684e91f`.

### §44.2 Defect two — an encoding truncation in a party name

```
cp1252 "paid Café Ltd 4200" returned party "Caf" under errors="replace"
```

A silent truncation at the first non-ASCII byte. The supplier name is the key
the memory index and the D-05 identity comparison both depend on, so a
truncated party is not a cosmetic defect — it is a wrong identity fed into a
live decision.

### §44.3 Why this belongs in the operational memory

Both defects sat in code that a 2,295-test suite ran over and did not catch.
Neither is exotic. What found them was an **external oracle** — 100 cases with
known-correct answers that nobody in the codebase authored — which is the same
property that makes the UK public-spend data load-bearing
([`TESTING.md` §5.3](./TESTING.md)).

**A test suite written from the same assumptions as the code cannot find a
shared wrong assumption.** These two defects are the measured proof of that,
and they are the argument for keeping the pack.

## §45 The Ground-Truth Pack ran for the first time, and the four owner actions

Recorded 2026-08-10. Status and evidence only; the design behind the pack is in
[`ARCHITECTURE.md` §21](./ARCHITECTURE.md) and the human work register is §43.

### §45.1 The pack was reporting INVALIDATED, so nothing had ever been measured

`python scripts/run_ground_truth.py` exited **2 — the harness broke**, not 1.

```
TypeError: 'PosixPath' object is not iterable
  scripts/validate_ground_truth.py:423, from run_ground_truth.py:272
```

`pack_validator()` documents `validate(root: Path)` and accepted the names
`("validate", "validate_manifest", "main")`. The sibling exposed neither
`validate` nor `validate_manifest`, so the loader bound to `main(argv: list[str])`,
the runner handed it a `Path`, and argparse died. `run_manifest` then took the
whole pack down with it.

The same class of defect, one file over: `pack_loader()` looks for
`load_cases` / `load_pack` / `cases` and `scripts/build_ground_truth.py` had
none of them, so the extraction section reported

```
BLOCKED — awaiting scripts/build_ground_truth.py
```

**while that file was committed and the 100-case pack was sitting beside it.**
A BLOCKED that names a file in the repository reads as a fact about the world
and was a fact about wiring.

| Fixed | How |
|---|---|
| `validate(root) -> (ok, failures)` | added to `scripts/validate_ground_truth.py`, wrapping `check_corpus` |
| `main` no longer accepted | removed from `pack_validator`'s names. Every script has a `main` and its signature is never the contract, so accepting it guaranteed binding to the wrong callable instead of reporting a missing one |
| `load_cases(root)` | added to `scripts/build_ground_truth.py`, carrying `renderable` through rather than recomputing it |

### §45.2 EXIT 1 and EXIT 2, measured

Scored separately, because they are different claims. A backend that reads
nothing and a backend that invents a value both fail EXIT 1; only the second
also fails EXIT 2.

| Exit | Result | Measured |
|---|---|---|
| EXIT 1 `GENERATED_TRUTH_EXTRACTION` | **FAIL** | stub backend, 80 renderable cases, exact matches per field `{date: 0, party: 0, total_paise: 0, tax_paise: 0}`, required 76 |
| EXIT 2 `UNRENDERABLE_INPUT_IS_EXPLICIT` | **PASS** | 20 unrenderable cases, every named field explicit `not_found` **with a reason**, 0 unsafe |

**EXIT 1's FAIL is the designed outcome of owner decision Q4 = B**, not a defect.
No production backend is selected, `StubExtractor` reads nothing, and
`PHASE_8_EXTRACTION = INCOMPLETE` is what a row of zeros means. The 20
unrenderable JPG cases are **not** in EXIT 1's denominator: with them in it the
old `95 per 100 per field` measured our own renderer rather than the reader.

**EXIT 2 needed a real fix to pass.** `StubExtractor` wrote a bare `not_found`,
which makes *"we have no reader at all"* and *"the reader looked and found
nothing"* the same string in the audit trail. They are different facts about the
document. It now writes `not_found: no production reader is configured, so
nothing was read from this document`.

Labels, unchanged and not interchangeable: the corpus is `SYNTHETIC_EVIDENCE`,
the truth is `GENERATED_TRUTH` from canonical JSON, and neither is ever evidence
about `REAL_READER_ACCURACY`.

### §45.3 A hazard found while mutation-testing, worth knowing

Changing `EXIT1_MATCHES_REQUIRED = 76` to `= 40` and restoring it left Python
running the **stale bytecode**: both values are two bytes and the restore landed
inside the same second, so CPython's default `(mtime, size)` invalidation missed
it. The mutant appeared to survive its own restoration.

**Any mutation that preserves file size can produce a false verdict in either
direction.** Clear `__pycache__` between mutants, or `touch` the file.

### §45.4 The four owner actions, and nothing else is waiting on a human

Everything else in Phase 8 that had an answer has been built, tested and merged.

| # | Action | Blocks | Why only the owner |
|---|---|---|---|
| H-06 | Create repository secret `CLAUDE_AUDIT_TOKEN` — fine-grained, **Administration: read**, nothing else | PR #34, and with it `test_bypass_actors_are_still_empty` | Credentials are the one thing that is never created on the owner's behalf |
| B-01 / H-03 | Create `Demo Co` and four ledgers in the TallyPrime GUI | the 19 RealTally contract tests | The XML gateway refuses: `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>` |
| ~~B-02 / H-04~~ | **DONE 2026-08-12 — a free trial licence is active.** Nothing to obtain and nothing to decide. Proof is the §47 voucher dated the **12th**: Educational accepts only the 1st, 2nd and 31st, so an Educational instance would have refused it. See §48. **Not recorded anywhere: when the trial expires** | nothing any more |  |
| — | Confirm `claude.yml`'s pin comment `# v1` → `# v1.0.187` stays | nothing; already applied | A `.github` edit outside the authorised token diff |

**H-06 in detail, because the reason is structural.** The workflow token *can*
read the ruleset — the body comes back real — but `bypass_actors` is **absent**
from the view it receives. The minimum permission is repository
**Administration: read**, and actionlint v1.7.12 settles that no workflow can
hold it:

```
unknown permission scope "administration". all available permission scopes are
actions, artifact-metadata, attestations, checks, contents, deployments,
discussions, id-token, issues, models, packages, pages, pull-requests,
repository-projects, security-events, statuses
```

So no `permissions:` block at any level makes that assertion runnable from a
pull request. It is not skipped, deleted or weakened; it fails, loudly, and
`artifacts/gate_integrity_blocked.md` records the reason, the owner and the next
required evidence.

---

**Owner and manual work items: see [`docs/OWNER_WORK.md`](./OWNER_WORK.md).**

That file is the single place the list lives. It is not repeated here, and the
reason is measured rather than stylistic: on 2026-08-10 `accountant/rules/` was
recorded as "verified absent" in this file, in `ARCHITECTURE.md` and in
`CONTROL_PLANE.yaml` simultaneously, days after it had merged in `7db7f45`. The
three copies corroborated each other, so cross-checking the documents could not
catch it. Only `ls` could.

---

## §46 The cloud launch — sixteen tasks, what shipped, and what a mutant found

Dated 2026-08-11. The owner directed a cloud launch and superseded the frozen
plan **in words**, in chat:

> "frozen plan SUPERSEDED for cloud-launch work; multi-user, login, accounts,
> cloud hosting now allowed; runtime dependencies allowed"

`docs/ARCHITECTURE.md` §4.8 carries the same amendment beside the line it
changes, rather than being edited silently.

**The permission was wider than what was used.** No runtime dependency was
added by any of the sixteen tasks. `pyproject.toml` still declares
`dependencies = []`, so the "zero runtime dependency" claim in ARCHITECTURE is
still true, and every task below landed on the standard library.

### §46.1 The starting position, measured before anything was built

Three read-only agents established this, and it is why each task exists.

```
no connector, no cloud code       CONNECTOR_PROTOCOL.md:3
no auth, no tenant, no session    grep tenant|login|session in accountant/ = 0
every route unauthenticated       app.py do_GET, do_POST
/reverse deletes a voucher        one unauthenticated POST, caller-supplied id
/reverse-all bulk-deletes         every voucher we ever wrote
audit log lost on restart         configure() defaulted to MemoryStore(":memory:")
I1 duplicate voucher              tests/test_idempotency.py, xfail(strict)
writes bypass the one door        ci/educational_slice.py hits RealTally direct
2 of 20 gates never execute       lockfile, cached-mutation
no upload route at all            grep multipart|enctype|type=file = 0
rules corpus never evaluated      accountant/tax/ imported by nothing outside it
single-threaded dev server        HTTPServer
no deployment artefact            0 Dockerfiles, 0 deploy jobs
```

The architecture class this establishes was recorded at the time as
`NOT_CLOUD_DEPLOYABLE`.

### §46.2 What shipped

| # | Task | Evidence |
|---|---|---|
| 1 | connector dials out, never listens | `docs/CONNECTOR.md`, `tests/test_connector.py` |
| 2 | authentication and tenancy | `docs/AUTH.md`, `tests/test_auth.py` |
| 3 | the destroying routes guarded | `tests/test_reversal_guard.py` |
| 4 | durable audit log | `tests/test_durable_log.py` |
| 5 | idempotency I1, and I2 with it | `tests/test_idempotency.py` |
| 6 | every write through the one door | `tests/test_write_door.py` |
| 7 | TLS on the two legs that cross a network | `docs/TLS.md`, `tests/test_tls.py` |
| 8 | the lockfile gate, pinned as a defect | `tests/test_gate_contract.py` |
| 9 | upload plus a placeholder reader | `tests/test_upload.py` |
| 10 | the rules engine is actually evaluated | `tests/test_rules_wired.py` |
| 11 | threaded server | `tests/test_concurrency.py` |
| 12 | deployment artefacts | `docs/DEPLOY.md`, `tests/test_deploy_artefacts.py` |
| 13 | data deletion | `docs/DATA_DELETION.md`, `tests/test_data_deletion.py` |
| 14 | log redaction | `docs/REDACTION.md`, `tests/test_redaction.py` |
| 15 | observability | `docs/OBSERVABILITY.md` |
| 16 | the whole journey, end to end | `tests/test_user_journey.py` |

Each landed as its own pull request with its own mutants. The per-task detail
lives in the PR body and the commit message; this section is the index, not a
second copy of them.

### §46.3 Six defects found by things nobody was looking at

Recorded because each one is a lesson about a *kind* of check, not a one-off.

1. **The evidence script still carried defect W1.** `ci/educational_slice.py`
   read back with `back is not None` — a presence check. That is the exact hole
   `pipeline.post` was fixed for on 2026-08-09: it checks the label on the box
   and never opens it. Fixed on 2026-08-09 in one place and left standing in
   another for two days.
2. **`ci/readiness.py`'s "FakeTally only" was prose, not a constraint.**
   `run_a`, `post_one` and `_retry_creates` were annotated `TallyClient`, so a
   harness whose entire method is injecting 30 failures type-checked against a
   real `RealTally`. Now the type says what the paragraph said.
3. **The `lockfile` gate has never run.** `uv sync --frozen` is the one flag
   that *guarantees* the check is skipped; the comment above it claimed it was
   the check. `test_every_command_in_the_contract_appears_in_a_workflow` matches
   only the first token, so `uv` matched everything. That is the fourth check
   found in this repository that could never fail.
4. **Two documentation pointers cited an OWNER_WORK entry that did not exist.**
   `docs/AUTH.md` and a comment in `app.py` both said the missing `Secure`
   cookie flag was recorded as owner work. It never was — two sources
   corroborating nothing, which is the exact failure `OWNER_WORK.md`'s own
   header was written about.
5. **A mutant caught a false claim inside a comment of mine.** The redaction
   module said a 32-character hex threshold "would have eaten every operation
   id". It would not: `_` is a word character, so `\b` never fires at the start
   of the hex run in `ad_ffff…`. The boundary anchors were doing the work and
   the number was doing none of it. The reason is restated to what measurement
   supports and a test makes the number load-bearing.
6. **A TLS mutant survived** because OpenSSL 3.6 already defaults a server
   context to TLS 1.2 — which is precisely the argument for *stating* the floor
   rather than inheriting it, arriving as a test failure. The replacement test
   reads the AST and requires the assignment, not the value.

Two more, smaller: a test was writing a real `data/app.db` into the working
tree on every run, and `argparse` was accepting `--secret hunter2` as an
abbreviation of `--secret-file` — quietly reinstating the one argument the
connector refuses to have.

### §46.3a Four more, found while the sixteen were LANDING

These are not in the list above because they were not there to find until the
branches met each other. They are the argument for landing work in sequence
rather than merging a batch.

7. **DEFECT J1 — a cross-tenant authorization bypass, and it was mine.**
   `Principal.require` was written with Task 2, has a passing unit test, and had
   **no caller anywhere in `accountant/`**. An AST sweep found exactly one
   reference: the `owns()` call inside its own body. A session issued to one
   customer was authenticated against another customer's open books and let
   through to read and reverse their vouchers.

   *A unit test of a guard proves the guard works. It says nothing about whether
   the guard is installed.* It is the failure `docs/AUTH.md` already had a
   sentence about — a check every handler must remember is a check some handler
   will forget — and I wrote both the sentence and the hole. Found by Task 16,
   the end-to-end journey, at step 6, which is the one thing only an end-to-end
   test can find: every piece had passing tests and the pieces did not connect.

   Fixed the same day. `ACCOUNTANT_TENANT` is now **required in production**,
   and unset refuses every request — unset meaning "any tenant may enter" is the
   defect reintroduced as a default.

8. **Nine store methods and two caches were written before the threaded server
   existed.** `claim_operation`, `operation_used`, `operation_reversed_at`,
   `mark_operation_reversed`, `users_of_tenant`, `live_sessions_of_tenant`,
   `deleted_tenants`, `companies_of_tenant`, `tenants_in_company`,
   `actions_of_tenant`, `delete_tenant`, plus `remember_deletion` and
   `deletion_for`.

   All correct on a one-request-at-a-time server. None written carelessly —
   written **first**. `deletion_for` is the sharpest: `get` then `pop` is
   check-then-act, so two confirmations of one plan can both find it present and
   both proceed. The same shape in `batch_for` was measured at **29 doubles in
   300 attempts** with the switch interval at `1e-6` — a double write to a
   customer's books.

   Named by two structural guards in `tests/test_concurrency.py` that walk the
   source and ask *does every method touching the shared connection hold the
   lock*. Neither was found by reading a diff.

9. **TLS and threading both changed how the server is built**, and neither was
   right alone. Taking either side would have produced a working app with the
   other task silently dropped. They combine: `start_server` decides the class
   **and** the wrapping, and `serve()` builds nothing.

   The AST guard that caught it read `serve()` for both facts, and both had
   moved. It follows the call now and gained a third assertion — that `serve()`
   binds through `start_server` and constructs no server of its own — which pins
   the thing that made them movable. The guard got stronger by chasing the code
   rather than accommodating it.

10. **A wheel-build race that had been green for weeks.** Two tests both ran
    `python -m build` against the repository, and under `-n auto` one deleted a
    file out of `build/` while the other was copying it. `pr-full` failed on a
    file that is present in the tree and has been since the initial commit. Now
    built from `git ls-files` into a temporary directory, which also makes the
    clean room actually clean.

### §46.4 What is NOT claimed

- **Everything is FAKETALLY.** Not one of the sixteen tasks produced
  `LICENSED_REALTALLY` evidence. Nothing here says anything about a real
  TallyPrime, and §21 and §28 remain the only live evidence in this document.
- **Nothing is deployed.** There is no host, no domain, no registry and no
  certificate authority. The image is buildable and reviewable; it has never
  been built, because there is no Docker here, and that is recorded in
  `docs/DEPLOY.md` as `NOT MEASURED` rather than asserted.
- **The container cannot start in a cloud yet.** `serve()` calls `connect()`
  before binding, so with no reachable Tally it refuses to start — correct
  behaviour, and a cloud host has no Tally. The cloud side of the connector
  protocol (`/connector/register`, `/jobs`, `/result`) does not exist.
- **`S2` is still `NOT_MEASURED`.** The upload route accepts a document and the
  placeholder reader returns an explicit `not_found` for every field. No
  document reader vendor is selected; `D-23` is open.
- **GST posting is still off.** Owner decision `Q3 = D`. The rules engine is now
  evaluated and its verdict shown, and that authorises nothing:
  `POSTING_ENABLED` is False, and two independent guards still refuse a GST
  bill.

### §46.5 Owner work created by this launch

All of it is in `docs/OWNER_WORK.md`, which stays the single list. The new
entries are: the one-line `--frozen` → `--locked` fix in `pr-fast.yml`
(`.github/` is denied at the permission layer in this environment, so the exact
before/after diff is recorded there rather than applied); a certificate
authority for TLS; a mail provider before any password reset; and a decision on
self-registration.

`cached-mutation` is also listed there as the second gate that never executes —
and as **no action wanted**, because it is parked deliberately with a measured
reason and a standing owner rule. It is listed only so "2 of 20 gates never
execute" is not read as two defects when it is one.

## §47 The first write into a real, licensed TallyPrime

Dated 2026-08-12. **§46.4 opens with "Everything is FAKETALLY." That sentence is
now out of date, for this one area, and for nothing else.**

§46.4 is left standing rather than edited. It was true on 2026-08-11 and the
record of what was true then is worth more than a tidy document; this section is
what supersedes it, and a reader who arrives at §46.4 first should arrive here
second. Every other bullet in §46.4 — nothing deployed, `S2` not measured, GST
posting off — is unchanged by anything below.

The evidence class for this run is `LICENSED_REALTALLY`, and it is the third
piece of live Tally evidence in this document after §21 and §28.

**READ THIS BEFORE FOLLOWING ANY `logs/` PATH BELOW.** This section cites files
under `logs/` as evidence. **Those files are deliberately not committed** —
`logs/` was added to `.gitignore` on 2026-08-12, alongside `data/` and for the
same reason: measured, every one of the 21 files named the live company, and
this repository is public. The paths are exact and the files are real; they are
on the machine that produced them, not in a clone. `ACCOUNTANT_LOG_DIR` and
`ACCOUNTANT_XML_LOG_DIR` move them.

This is a genuine weakness in the evidence and is named rather than smoothed: a
reader who did not run it cannot check these citations. A redacted evidence
bundle would fix it and does not exist yet.

### §47.1 The channel, and the address that does not work

```
Mac  ->  VirtualBox NAT port forward  ->  Windows 11  ->  TallyPrime XML gateway
```

The VM's own address is **unreachable from the host**. Measured 2026-08-12:
`10.0.2.15:9000` answers with `TimeoutError`, because under VirtualBox NAT that
address is private to the guest. This is worth writing down because it is the
address the guest itself reports, so it is the address a person copies.

The address that works is `127.0.0.1:9000`, via a forwarding rule:

```
VBoxManage controlvm "Windows 11" natpf1 "tally9000,tcp,127.0.0.1,9000,,9000"
```

Recorded in the tree at `tally_client.py:19-24`, beside the constant it
explains, rather than only here.

**Round trip: median 30.8 ms over 10 reads; 100 sequential round trips in about
2.8 s.** The retained audit log corroborates the order of magnitude on a
different run — `logs/audit.jsonl` records reads at 13 ms and 32 ms and the
voucher import at 542 ms. A write costs an order of magnitude more than a read,
which is the number that matters for any future batching decision.

### §47.2 What makes this `LICENSED_REALTALLY` is a licence, not a code change

The owner confirmed on 2026-08-12 that the TallyPrime behind that port is a
**licensed installation on a free trial, not Educational**. Nothing in this
repository could have established that: §25.5 records licence mode as
**unreadable over the gateway**, and that probing for it wedged the live
TallyPrime.

So the class is owner-attested. That is a weaker provenance than a measurement
and it is stated as such. It matters because it is the whole difference between
this run and `EDUCATIONAL_TALLY` — Educational mode accepts vouchers only on the
1st, 2nd or 31st of a month, and this voucher is dated the 12th.

### §47.3 What shipped

Six modules under `accountant/tallyio/`, plus one script at the repository
root. Line counts measured 2026-08-12; these files were still being edited on
the day this section was written, so the counts are a snapshot and the file
names are the durable part.

| File | Lines | What it is |
|---|---|---|
| `accountant/tallyio/errors.py` | 336 | Tally's complaints, classified into codes and sentences |
| `accountant/tallyio/writedoor.py` | 187 | the RUNTIME allow-list — every write this system may perform, with a written reason each |
| `accountant/tallyio/audit.py` | 234 | one JSON line and two raw XML files per operation, written before the result is reported |
| `accountant/tallyio/masters.py` | 548 | ledgers: exists, `create_ledger`, `ensure_ledger` |
| `accountant/tallyio/vouchers.py` | 373 | the direct Purchase write path |
| `accountant/tallyio/reports.py` | 236 | reading the books back out |
| `mvp_real_tally.py` | 141 | the end-to-end run: masters → voucher → read back → audit |

Covered by `tests/test_tallyio_mvp.py` and `tests/test_tallyio_reports.py` —
**72 tests, all passing**, measured 2026-08-12. Those run against fakes; the
live evidence is §47.5.

### §47.4 No guard was widened, and that is checkable

The static write-door scanner was **not touched**. It already permitted this
work, for reasons written into it before this work existed:

- `tests/test_write_door.py:405` — `test_nothing_outside_the_connector_builds_a_tally_import_envelope` skips any path with `CONNECTOR` among its parents (`:415`), and `CONNECTOR` is `accountant/tallyio` (`:67`). A `<TALLYREQUEST>Import` envelope built **inside** the connector was always allowed; the guard exists to stop one being built outside it.
- `ALLOWED` (`:124`) keys on `write_voucher` and `reverse_by_operation_id` only (`:56`). `vouchers.py` calls neither — it builds its own envelope inside the connector — so it needs no permit and got none.

Measured: `tests/test_write_door.py` is **37 tests, all passing**, with no edit
to the file.

That is the honest reading, and it cuts both ways. The static guard proves
nobody built a *third* connector-bypassing path. It does not prove
`vouchers.py` is safe, because `vouchers.py` is inside the boundary the guard
draws. What holds `vouchers.py` is `writedoor.allow_write`, called at
`accountant/tallyio/vouchers.py:263` and `accountant/tallyio/masters.py:459` —
a different mechanism, described in `docs/ARCHITECTURE.md` §4.2. Two call
sites, two permits, and `writedoor.ALLOWED_WRITES` holds exactly two entries.

### §47.5 What is in the books now

Company **TANVEER SIDHU**, on a live TallyPrime:

```
ledgers   Test Supplier    under Sundry Creditors
          Purchase         under Purchase Accounts
          Bank Of Test     under Bank Accounts
voucher   Purchase, 12-Aug-2026, Rs 1,000.00
          narration "MVP end-to-end run"
```

Verified two ways, neither of which is the response to the write: by
`RealTally.read_vouchers`, and by `trial_balance` reporting **Purchase
+1,000.00 / Test Supplier -1,000.00** — equal and opposite, which is the
conservation check §46 keeps reaching for, and it needs no expert to read.

Two provenance notes, because a record that overstates its own evidence is the
thing §44 was written about:

- The retained `logs/audit.jsonl` is from a **re-run**, and holds four
  operations, none of them a ledger creation. `masters.ensure_ledger`
  (`accountant/tallyio/masters.py:507`) returns early with `already_existed=True`
  and writes no row when the ledger is already there. The creations happened; the
  log of them is not in this tree.
- Only two of the three ledgers are named by anything retained here
  (`mvp_real_tally.py:50-53`). `Bank Of Test` appears in no file in the
  repository. It exists in the company; the evidence for it is the owner's
  screen, not this tree.

The write response itself is retained at
`logs/xml/2026-08-12/create_purchase_voucher_2bbedf2838fa_response.xml`:
`STATUS 1`, `CREATED 1`, `LASTVCHID 1`.

### §47.6 Three defects, all found by running it against something real

Each is recorded because it is a lesson about a *kind* of check.

1. **`errors.ERROR_ELEMENTS` contained `DESC`, and it turned two successful
   writes into reported failures.** A successful ledger import answers with
   `CREATED 1` and a `<DESC>` holding `<CMPINFO>` — a long row of zero counters,
   `"0 0 1 0 0 …"`. `DESC` is a generic container that appears in *requests*
   too, so scraping it read the counter row as a complaint nobody could
   classify. **Two ledgers were genuinely created while this code reported
   `success=False`.**

   A false negative on a write is worse than a false positive on a read: it
   makes a caller retry something that already happened, in a customer's books.
   Fixed twice over at `accountant/tallyio/errors.py:228` — `DESC` removed from
   the list — and at `:266`, where whitespace is stripped before the all-digits
   test, because `str.isdigit()` answers False for `"0 0 1"` and the spaces were
   doing the damage. The reason is written into the module at `:219-227` rather
   than only here.

2. **A successful response containing nothing was read as a successful
   read-back.** The first `reports.py` asked for reports the obvious way,
   `<TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID>`.
   TallyPrime answered `STATUS 1` and `<DATA>  </DATA>` — two spaces. Not an
   error, not a refusal: a successful response containing nothing, while a real
   Rs 1,000 Purchase sat in the company and `real.read_vouchers` could see it
   perfectly well. Retained verbatim at
   `logs/xml/2026-08-12/get_day_book_c9825b73a0f9_response.xml`.

   The `Ledger Vouchers` report was worse, because it was not empty. It answers
   in a **display shape** — `<DSPVCHDATE>`, `<DSPVCHLEDACCOUNT>`,
   `<DSPVCHCRAMT>`, with the `1000.00` plainly there and **no `<VOUCHER>`
   element at all**
   (`logs/xml/2026-08-12/get_ledger_vouchers_8d28e0dbdb67_response.xml`). The
   parser iterated `VOUCHER`, found none, and returned an empty tuple with no
   error. A shape that answers a different question than the one asked, without
   saying so, is harder to catch than a refusal.

   `create_purchase_voucher` confirms its OWN write with that same query
   (`accountant/tallyio/vouchers.py:324-340`), so it reported `confirmed=False`
   for a voucher that had posted — visible in `logs/audit.jsonl` as
   `"confirmed": false` on a row whose `"status"` is `"success"`.

   Fixed: `reports.py` no longer builds its own request. It calls
   `real.build_voucher_list_request` (`accountant/tallyio/reports.py:218`) — an
   Export COLLECTION, the shape TallyPrime actually answers — and parses with
   `real.parse_vouchers` (`:229`), the pair `RealTally` has used since §21 and
   the pair that read the voucher back in §47.5. Date and ledger filtering moved
   into Python, which is a real limitation and is listed in §47.7. The whole
   measurement is written into the module's own docstring at
   `accountant/tallyio/reports.py:13-48`, beside the code it explains.

3. **A NO-BREAK SPACE (U+00A0) was sitting invisibly inside a string literal**
   in `reports.py`, caught by `ruff` RUF001. It is the *right* character to
   strip — TallyPrime uses it as a thousands separator in some locales — so the
   code is correct and the bug is that nobody could see it. An invisible
   character in a literal does not appear in a diff, which means it cannot be
   reviewed. Written as the escape `\u00a0`, it is greppable, reviewable and
   survives a copy-paste.

   That literal is gone from `reports.py` now, because the amount parsing went
   with defect 2 — `real.parse_vouchers` does the conversion, and the
   connector's own stripper already had it written correctly, as
   `_STRIP_FROM_AMOUNT` at `accountant/tallyio/real.py:415`. Measured
   2026-08-12: **no literal U+00A0 remains anywhere under `accountant/`.** The
   defect is recorded anyway, because the lesson is not about this character —
   it is that `ruff` RUF001 is the only reviewer in this repository that can
   see one.

   This paragraph had the same bug while it was being written: a real NO-BREAK
   SPACE was pasted into the sentence describing NO-BREAK SPACES, and only a
   scan for non-ASCII found it. That is the argument for the escape, restated
   by accident.

### §47.7 What is NOT claimed

- **Only Purchase is implemented.** Sales, Payment, Receipt, Journal and Contra
  are not. `writedoor.ALLOWED_WRITES` (`accountant/tallyio/writedoor.py:85-110`)
  holds exactly two permits, and that list is the whole of it.
- **Only ledgers.** Stock items, godowns and cost centres are not implemented.
- **GST posting is still off.** Owner decision `Q3 = D`, unchanged by any of
  this. Nothing in these six modules touches the rules engine.
- **The reports fetch the whole voucher collection and filter in Python.** That
  is the wrong shape for a company with a hundred thousand entries, and it is
  the wrong shape now, not later — it is recorded here so it is not discovered
  as a surprise.
- **One voucher, one company, one machine.** Nothing here says anything about
  scale, about concurrency, or about another person's Tally. A single Rs 1,000
  Purchase on a trial licence, on the owner's own laptop, is one data point.
- **The read-back has not been re-run against the live gateway since it was
  fixed.** Defect 2's fix is proved against fakes by
  `tests/test_tallyio_reports.py`. The retained `logs/audit.jsonl` still shows
  `"confirmed": false`, because it is from the run that found the defect. The
  live confirmation in §47.5 came from `RealTally`, by hand — not from the
  module that is now supposed to do it.

### §47.8 Three existing guards caught this work, and none was written for it

Measured 2026-08-12 with `.venv/bin/python -m pytest -q -n auto`. The six
modules arrived red and finished green:

```
first run, modules as written    2 failed, 3286 passed, 6 skipped, 4 xfailed
the same tree with them stashed  0 failed, 3288 passed, 6 skipped, 4 xfailed
after the three fixes            0 failed, 3360 passed, 6 skipped, 4 xfailed
```

The interesting number is the middle one. Stashing the new modules made the
suite green, which is how it was established that the failures were **caused by
this work** rather than inherited — a two-minute measurement that replaces an
argument.

Each failure was one line, and none of the three guards was touched:

1. **`audit.py` imported `accountant.redact`**, reaching above the connector
   boundary. Only `__main__.py` may, because nothing imports `__main__.py`.
   Caught by `tests/test_reverse_all_cli.py:280`. Fixed: the scrubber is
   **injected**, not imported, so the boundary holds and the behaviour does not
   change.
2. **A working-tree check failed under `-n auto`.**
   `audit.JsonLineAuditLogger` defaults its directory to `logs/`, relative to
   the current directory, so a code path that logs writes into the repository
   itself. Caught by
   `tests/test_upload.py::test_an_upload_writes_nothing_to_the_working_tree_or_a_data_directory`
   — the same shape as the `data/app.db` defect in §46.3, found by the same
   kind of check. It only failed in parallel, which is the argument for running
   `-n auto` rather than a serial suite.
3. **A docstring heading read as a state name.** `vouchers.py` carried
   `` `STATUS 1` IS NOT `POSTED` ``, which put the whole word `POSTED` into
   `accountant/` for the first time. Caught by
   `tests/test_adversarial_amounts_and_states.py:806`, which scans the shipped
   package on whole words and cannot tell a heading from an identifier.

The third is worth a sentence on its own. It is a guard firing on prose, and
the reflex is to call that a false positive and loosen the scan. That would
remove the check that named eight invented states in the first place, to save
one word in a comment. The heading was reworded instead — the cheap side of the
trade was the docstring, not the test.

**`logs/` is still untracked in the working tree**, written by the live runs
that produced §47.5's evidence. It is the record of the first real write and it
is not in version control.

### §47.9 Owner work created by this section

Nothing new is added to `docs/OWNER_WORK.md` by §47.

One of the two questions this run raised — whether `logs/` is evidence worth
committing or scratch worth ignoring — **is now answered and was not the owner's
to be asked.** It is neither: it is a customer's accounting data, and the
repository is public. `logs/` is in `.gitignore`. See the note under §47.

The other stands: whether the Python-side report filtering is fixed before or
after a book large enough to make it hurt. It blocks nothing today.

### §47.10 The other five voucher types, and the duplicate that made them wait

Dated 2026-08-12, after §47.9. `vouchers.py` shipped with Purchase alone. Sales,
Payment, Receipt, Journal and Contra now exist, on one shared builder and one
shared balance check rather than six copies. Seven permits in
`writedoor.ALLOWED_WRITES`, all `destructive=False`, all naming one company.

**They waited on idempotency, and the reason is in the books.** §47.5 records a
Purchase of ₹1,000. The trial balance showed **₹2,000** against `Test Supplier`,
because `mvp_real_tally.py` was run twice and nothing in the write path could
tell the second run from the first. Adding five more write paths on that
foundation would have multiplied one defect by six.

So `operation_id` is now **required** on every `create_*_voucher`, TallyPrime is
asked before writing, and the result carries `already_posted`. Verified offline
against a transport that answers reads from what the writes actually put in:
same id twice → **one** Import sent, the repeat reporting `already_posted=True`.

The check **fails closed**: a probe that cannot be answered posts nothing.
Verified with a transport whose reads raise and whose writes would succeed —
**zero** Imports sent. Returning "not found" on an unreadable probe would have
rebuilt the original defect exactly.

`mvp_real_tally.py` derives its id from the day and amount rather than
generating a fresh one, so re-running it finds the existing voucher instead of
adding another. A fresh id would have left the ₹2,000 bug in place behind a
mechanism built to prevent it.

**One rename.** `create_contra_voucher`'s first account parameter was
`from_account` and is now `debit_account`. On `create_payment_voucher`,
`from_account` is the *credited* side — the account money leaves. On Contra it
was the *debited* side. The same name meant opposite sides on two neighbouring
methods, so a caller writing "from Cash to Bank" would have inverted a real
transfer between two real accounts. A warning comment was written first and was
not enough; the name itself was the defect.

**Still not claimed:** none of this ran against live TallyPrime. The evidence
class for §47.10 is offline verification against a fake transport, not
`LICENSED_REALTALLY`. Only the Purchase path in §47.5 has touched real books,
and the duplicate voucher it created is still there — removing it is the owner's
call, because a deletion in TallyPrime is not undoable.

---

## §48 The licence stopped being a blocker

Dated 2026-08-12. **§24 says "Educational mode only" and calls a non-Educational
licence "unavailable". Both stopped being true. This section is what supersedes
it, and §24 is left standing rather than edited — the same treatment §46.4 got
from §47.**

### §48.1 What changed

The owner activated a **free TallyPrime trial licence**. The installation behind
port 9000 is licensed, not Educational.

### §48.2 It is measured, not just attested — and this is the part that matters

§47.2 records the owner's word for it, and says plainly that owner-attestation
is weaker provenance than a measurement. That caveat can now be strengthened,
because the run itself contains the proof.

**The §47 Purchase voucher is dated 12-Aug-2026.**

Educational mode accepts vouchers dated **only the 1st, 2nd or 31st** of a
month. That is not a guess: it was measured on this project against this
gateway, and §30.3 records the refusal directly — `2026-08-07` REJECTED,
`2026-08-31` ACCEPTED, with Tally's own words retained.

An Educational instance would have refused a voucher dated the 12th. It
accepted one. So:

```
voucher accepted on the 12th  ->  not Educational
```

This is the strongest licence evidence the project has, and it arrived sideways
— from a run that was not looking for it. It does **not** read the licence
mode. §25.5 stands: licence mode is unreadable over the gateway, probing for it
wedged a live Tally, and `read_licence()` still returns `UNKNOWN` by design.
What is established is narrower and sufficient: **whatever this instance is, it
is not Educational.**

### §48.3 What is unblocked

| | |
|---|---|
| `B-02` — a non-Educational licence | **SATISFIED**, for as long as the trial lasts |
| The `2026-08-07` contract fixture | **CAN NOW RUN unmodified.** The date restriction that refused it is gone |
| `LICENSED_REALTALLY` as an evidence class | already produced once, in §47 |

### §48.4 What is NOT unblocked, and this is the longer list

- **The `2026-08-07` fixture has not been run.** A licence being available and a
  test having passed are different sentences. Nothing may be relabelled on the
  strength of this section.
- **`2026-08-07` is still never edited.** The licence removes the reason the
  fixture could not run; it does not license changing it. Freezing it was the
  whole point.
- **`B-01` is untouched** — `Demo Co` and its four ledgers are still a GUI
  action, permanently, and that is a scope boundary rather than a gap
  (`RUNBOOK_PHASE5_ACCEPTANCE.md` §A.0.1).
- **The N = 10 acceptance run has still never happened.** It is now waiting on
  nobody's decision — only on somebody doing it.
- **`ci/acceptance_cli.py` will still refuse the `LICENSED_REALTALLY` label**,
  because it requires a MEASURED `licence_mode == licensed` and the licence read
  is `UNKNOWN` by design. That refusal is not a bug and is not to be loosened to
  match this section: it exists so the last open question in the project cannot
  be closed by whoever writes the report. The §47 label is owner-attested and is
  marked as such.
- **The UI still shows `real-licence-unknown`**, and correctly. It fails closed
  on an unreadable licence, which is right, and §25.7 already records that a
  genuinely Educational user would see the same warning.

### §48.5 The one thing nobody has written down

**No expiry date is recorded anywhere in this repository.** It is a free trial,
so it ends. Nothing here knows when.

Until that date is supplied, every claim in this section carries an unstated
condition — that the trial is still live on the day the claim is read. A run
dated after expiry would silently be an Educational run again, and the only
thing that would give it away is the same accident that gave this away: a
voucher date outside the 1st, 2nd and 31st being refused.

**Owner action, small and not optional:** record the trial's expiry date. One
line in `docs/OWNER_WORK.md` is enough.
