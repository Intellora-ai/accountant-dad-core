# PROJECT_STATE — Accountant Dad

## 1. Document control

| | |
|---|---|
| **Purpose** | The project's operational memory. What was decided, what is built, what is verified, what remains, why. One file. No other progress document exists. |
| **Repository** | `Intellora-ai/accountant-dad-core` — public — owner type **User** — created `2026-08-07T11:38:55Z` — VERIFIED (GitHub API) |
| **Branch / commit** | `main` @ **`f7bf5d9`** — "feat: Phase 9 proof track - synthetic books and the scoring harness" — **16 commits** — VERIFIED (`git rev-parse`, `git log`). **Working tree is NOT clean**: `accountant/tallyio/real.py` and `tests/test_real_tally.py` modified; `accountant/ingest/`, `accountant/taxonomy/`, `tests/test_ingest.py`, `tests/test_taxonomy.py` untracked. Work is in flight in other sessions. |
| **Updated** | 2026-08-08 |
| **Last verified state** | 2026-08-08. CI evidence is from nightly runs `31237228028` and `31238866032`. **The newest evidence is §21 (first real Tally), §22 (first product-quality measurements) and §23 (documentation drift corrected)** — those three sections supersede any older statement in this file that contradicts them. |
| **Companion documents** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the design. [`BOTTLENECKS.md`](./BOTTLENECKS.md) — what currently costs more than it should, with the smallest guard per class of defect. |
| **Who may update** | The owner, or Claude on the owner's instruction. |

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
| A duplicate `operation_id` cannot create a second voucher | C5 | VERIFIED against the fake; **still UNVERIFIED against real Tally** — the contract test that proves it is licence-blocked (§21) |
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

| | |
|---|---|
| **Current phase** | **Phase 2 — the Tally spine — ENVIRONMENT-LIMITED, not fully complete** (§21, §24) |
| **Blocked on** | a **non-Educational TallyPrime licence**. Educational mode rejects voucher dates outside the 1st, 2nd and 31st, so the 15 client-fixture tests in `tests/test_tally_contract.py` — which post on `2026-08-07` — cannot run unmodified. This is the Phase 2 **exit** criterion, and it is the only owner-blocked item left. |
| **Owner decision, 2026-08-08** | **Option 2 — Educational-mode exception.** No licence is to be purchased, activated, bypassed or simulated. See §24. |
| **No longer blocked on** | the Windows VM. TallyPrime is installed and answering. The earlier "Windows VM + TallyPrime — NOT INSTALLED — the single blocker" is **superseded** (§17, §21). |

Phases 0 and 1 are complete. Phase 2's build is done and proven end to end
against a real Tally (§21); its formal exit is licence-blocked. Phases 3–8 have
not started. **Phase 9 has been built and measured** — `generate/`, `score/`,
`taxonomy/` and `ingest/` all exist, and its numbers are in §22, which is the
uncomfortable part of this document. Phase 10 is deferred.

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
| Idempotency (C5) | **UNVERIFIED on real Tally** | passes against fake; the contract test that proves it is licence-blocked (§21) | needs a non-Educational licence |
| Read-back (C6) | **VERIFIED on real Tally** | `read_by_operation_id()` returned the written voucher, then `None` after reversal (§21) | — |
| Reversal (#6.5) | **VERIFIED on real Tally** | `reverse_by_operation_id()` → `True`; trial balance restored to the exact prior paise (§21) | — |
| Memory index #2 | **CODE EXISTS, NOT AUDITED** | `accountant/memory/index.py` | audit vs #2.1–#2.7. **Company-local only; every customer is a permanent cold start** (§22) |
| Detectors #3 | **BUILT — MEASURED AGAINST THE PUBLISHED RECORD, AND THEY MISS MOST OF IT** | `accountant/detect/detectors.py`, 4 detectors. **2 of 12** published real error types covered; **10 UNCOVERED** (§22) | proof work per uncovered type — [`BOTTLENECKS.md` A1](./BOTTLENECKS.md#a1--detectors-cover-2-of-12-published-real-error-types) |
| Rules corpus #9 | **NOT STARTED** | **VERIFIED absent** — `ls accountant/rules` → no such directory, 2026-08-08 | build |
| Extraction adapter #15 | **STUB ONLY** | `accountant/extract/adapter.py`, `TypedTextExtractor` | connect a backend |
| Web app #14 | **CODE EXISTS, FAKE-BACKED** | `accountant/web/app.py`, 385 lines, imports `FakeTally`. **Stdlib `http.server` only** — VERIFIED, and `pyproject.toml` still has `dependencies = []` with no web framework anywhere | swap client at M2 |
| Synthetic generator #1 | **BUILT, TEST-VERIFIED** | `accountant/generate/` — `book.py`, `inject.py`, `serialise.py`. `tests/test_generate.py`, 60 tests, one per acceptance criterion. Branch coverage 100%, 131/131 mutants killed, local run 2026-08-08 | — |
| Scoring harness #4 | **BUILT — AND N1 IS FAILING** | `accountant/score/` — `book.py`, `harness.py`, `report.py`. **N1 = 27.59 false alarms per 100 clean entries against a target of ≤ 10. FAIL by 2.8x** (§22) | [`BOTTLENECKS.md` A2](./BOTTLENECKS.md#a2--n1-fails-by-28x) |
| Real error taxonomy #7 | **BUILT — untracked in git** | `accountant/taxonomy/` — `sources.py`, `findings.py`, `coverage.py`, `report.py`. 5 sources, 12 error types, `uncovered_count() == 10` | commit it |
| UK government ingest #5 | **BUILT — untracked in git** | `accountant/ingest/` — `sources.py`, `fetch.py`, `spend.py`, `crossorg.py`, `report.py`, plus 7 real department fixtures | commit it |
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
          { "context": "pr-fast" },
          { "context": "ci-gate", "integration_id": 15368 } ] } }
  ]
}
```

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
| **Educational-mode voucher-date restriction** | **MEASURED — the current blocker** | `2026-08-07` REJECTED, `2026-08-31` ACCEPTED. `tests/test_tally_contract.py:39` posts on `2026-08-07`, so the 15 client-fixture tests cannot run unmodified. Educational mode does **not** block deletion — that theory was tested and disproven. | **buy a non-Educational licence** — [`BOTTLENECKS.md` A3](./BOTTLENECKS.md#a3--educational-mode-date-restriction-blocks-the-15-contract-tests) |
| **`trial_balance()` includes a derived figure** | **MEASURED, OPEN** | `Profit & Loss A/c` is Tally's derived closing figure, not a posting, so the raw sum is not zero. The two real ledgers cancel exactly. Reversal is unaffected — it compares the same dict before and after. | [`BOTTLENECKS.md` A4](./BOTTLENECKS.md#a4--trial_balance-includes-a-derived-figure) |
| **Detector coverage of real error types** | **MEASURED — 10 of 12 UNCOVERED** | §22 | proof work per uncovered type, [`BOTTLENECKS.md` A1](./BOTTLENECKS.md#a1--detectors-cover-2-of-12-published-real-error-types) |
| **N1 false-alarm rate** | **MEASURED — FAILING** | 27.59 per 100 against a target of ≤ 10 (§22) | [`BOTTLENECKS.md` A2](./BOTTLENECKS.md#a2--n1-fails-by-28x) |
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
| 15 client-fixture tests against the real client | all pass | **NOT RUN** — licence-blocked (§21). **Phase 2's exit criterion.** |
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
        all 15 client-fixture tests pass → "works on real Tally" is satisfied
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
the 15 client-fixture tests against the real client — licence-blocked
Tally.ERP 9 — only TallyPrime 7.0 has answered
children #2, #3, #14, #15 against their own written acceptance criteria
S1-S7 — no product measurement has been taken
N2 and N3 — not yet measured on real data
the third-party extraction backend — none is connected
cache hit/miss as a recorded number from a specific run
```

### What is measured and FAILING — added 2026-08-08

```
N1 = 27.59 false alarms per 100 clean entries, target <= 10.  FAIL by 2.8x.
detector coverage of published real error types: 2 of 12.  10 UNCOVERED.
```

**These are the two numbers that matter most in this document.** Detail in §22.

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

### The detectors cover 2 of 12 published real error types

```
published real error types                12
covered by current detectors               2
UNCOVERED                                 10
```

Covered: `capital_expenditure_as_revenue`, `revenue_expenditure_as_capital`.

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

### N1 = 27.59 — FAILING

```
N1  false alarms per 100 clean entries   27.59
    target                               <= 10
    verdict                              FAIL by 2.8x
```

**The first N1 ever measured on real data.** Every earlier N1 statement in this
project was an unmeasured target.

`accountant/score/harness.py` reports it as an explicit `PASS` or `FAIL`.
**Do not tune a threshold to make it pass** — that moves the measurement, not the
product. [`BOTTLENECKS.md` A2](./BOTTLENECKS.md#a2--n1-fails-by-28x).

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

**Option 2 selected: Educational-mode exception.**

```
Tally licensing status: Educational mode only.
Phase 2 status:         ENVIRONMENT-LIMITED, not fully complete.
Genuine owner blocker:  A legitimate non-Educational Tally licence is unavailable.
Unchanged fixture:      2026-08-07.
Limitation:             Educational mode cannot validate the original 2026-08-07
                        contract because its date restrictions reject that fixture.
Evidence status:        All other Phase 2 work is closed. The original 15-test
                        contract suite remains blocked by environment licensing.
                        Do not report Phase 2 as complete.
```

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
`tests/test_tally_contract.py` run against `RealTally` with all 15
client-fixture tests passing. Nothing else closes it, and no amount of local
green changes that.

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
Phase 3 implementation complete.
Phase 3 live validation remains environment-limited.
```

Not "complete". Not "passing". The implementation is done and proven against
FakeTally; the live validation is blocked by the licence decision in §24 and now
also by §25.5 and §25.7.


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
Phase 3 implementation:        COMPLETE
Phase 3 live validation:       ENVIRONMENT-LIMITED
RealTally 2026-08-07 evidence: NOT PROVEN
```

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
Phase 3 implementation:        COMPLETE
Phase 3 live validation:       ENVIRONMENT-LIMITED
RealTally 2026-08-07 evidence: NOT PROVEN
```

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

| Milestone | What it is | Kind | Status |
|---|---|---|---|
| **Phase 5** | controlled Tally write/reversal proof, `N = 10` | capability | implementation `PASSED`, live `BLOCKED_ENVIRONMENT` |
| **Phase 5B** | operational readiness and repeatability | **release gate** | `PASSED` against FakeTally |
| **Phase 6** | first detector — `vendor_switch` + dismissal logging | capability | `PASSED` against FakeTally over HTTP |

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
| Phase 5B readiness gate, 12 conditions | `PASSED` | `tests/test_phase5b_readiness.py`, 25 tests | FAKETALLY |
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
| Phase 5B, 30 of 30 lifecycles | `PASSED` | 30 | `ci/readiness.py` |
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

- `Ltd` / `Pvt Ltd` / `& Co` still collapse to one vendor key — blocked on
  `tests/test_memory.py:994-1001` (unchanged from §37.6)
- `normalise_company` still lacks the NFD fold `normalise_vendor` has
- `build_draft`'s `accounts` parameter is still unused
- **NEW, found 2026-08-09 and not fixed:** `COMPANY = "Accountant Dad Final"` is
  hardcoded in every request handler (`accountant/web/app.py:52`), while startup
  honours `ACCOUNTANT_COMPANY` (`:1194`). A person setting their own company name
  gets memory keyed to their company and handlers asking for the constant, so
  `pipeline.build_draft`'s cross-company guard raises. **It fails closed —
  nothing is written to the wrong books** — but the app today only works for a
  company literally named `Accountant Dad Final`. Out of Phase 5/6 scope;
  recorded rather than fixed.

### 40.8 What is explicitly NOT claimed

```
no licensed-Tally evidence of any kind was produced
the 2026-08-07 fixture is untouched and still refused by Educational mode
no FakeTally or SIMULATOR result is offered as evidence about a real TallyPrime
Phase 5B passing does not make Phase 6 complete
Phase 6 passing does not make Phase 5B pass
neither makes the live acceptance test any less REQUIRED
```
