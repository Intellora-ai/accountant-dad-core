# PROJECT_STATE — Accountant Dad

## 1. Document control

| | |
|---|---|
| **Purpose** | The project's operational memory. What was decided, what is built, what is verified, what remains, why. One file. No other progress document exists. |
| **Repository** | `Intellora-ai/accountant-dad-core` — public — owner type **User** — created `2026-08-07T11:38:55Z` — VERIFIED (GitHub API) |
| **Branch / commit** | `main` @ **`4cc290f`** — "A6: run workflow-lint and workflow-security on the pull-request path (#12)" — 11 commits — working tree clean — VERIFIED (`git rev-parse`, `git status --porcelain` → empty) |
| **Updated** | 2026-08-08 |
| **Last verified state** | 2026-08-08, after nightly runs `31237228028` and `31238866032` |
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
| A duplicate `operation_id` cannot create a second voucher | C5 | VERIFIED against the fake; UNVERIFIED against real Tally |
| Every write is read back from Tally, not trusted from an HTTP 200 | C6 | VERIFIED against the fake; UNVERIFIED against real Tally |
| Reversal tested against the **exact prior trial balance**, in paise | #6.5 | VERIFIED against the fake; UNVERIFIED against real Tally |
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

## 7. Architecture and A-to-Z build order

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
| Source inventory | **VERIFIED** | 2,049 lines, 18 files (`accountant/`) | — |
| Test inventory | **VERIFIED** | 2,206 lines, 8 test files, 242 tests | — |
| Branch / SHA | **VERIFIED** | `main` `4cc290f`, clean | — |
| Tally connector — Protocol | **VERIFIED** | `accountant/tallyio/client.py`, 8 methods | — |
| Tally connector — real impl | **NOT STARTED** | `grep -rn 'xml\|9000\|urllib.request' accountant/` → nothing | write `accountant/tallyio/real.py` |
| Tally read | **UNVERIFIED** | fake only | needs M0 (§19) |
| Tally write | **UNVERIFIED** | fake only | needs M0 |
| Idempotency (C5) | **UNVERIFIED on real Tally** | passes against fake | contract test vs real |
| Read-back (C6) | **UNVERIFIED on real Tally** | passes against fake | contract test vs real |
| Reversal (#6.5) | **UNVERIFIED on real Tally** | passes against fake | contract test vs real |
| Memory index #2 | **CODE EXISTS, NOT AUDITED** | `accountant/memory/index.py`, 115 lines | audit vs #2.1–#2.7 |
| Detectors #3 | **CODE EXISTS, NOT AUDITED** | `accountant/detect/detectors.py`, 143 lines | audit vs #3.1–#3.8 |
| Rules corpus #9 | **NOT STARTED** | no `accountant/rules/` directory | build |
| Extraction adapter #15 | **STUB ONLY** | `accountant/extract/adapter.py`, 187 lines, `TypedTextExtractor` | connect a backend |
| Web app #14 | **CODE EXISTS, FAKE-BACKED** | `accountant/web/app.py`, 385 lines. Line 3: *"Runs against FakeTally. NOTHING here touches real Tally"* | swap client at M2 |
| Synthetic generator #1 | **NOT STARTED** | no `accountant/generate/` | build |
| Scoring harness #4 | **NOT STARTED** | no `accountant/score/` | build |
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
| Tally local HTTP has no auth model beyond network reachability | **KNOWN, structural** | Tally developer docs; recorded in child #6 | none — bind loopback only, stated openly |
| Backup enforcement before writing | **VERIFIED against fake** | `CompanyNotBackedUp`, `client.py:66` | prove against real Tally |
| TallyPrime vs Tally.ERP 9 compatibility | **UNVERIFIED** | #6.8 requires both, or the unsupported version named in the error | decided at M2 against whatever is installed |
| Third-party extraction quality | **UNVERIFIED** | no backend connected; stub only | the 95/100 bar (S2) decides which backend qualifies |
| Ruleset protection | **VERIFIED** | 6 refusals, 9/9 audit | none |
| Nightly scheduling | **VERIFIED working, delay documented** | runs `31237228028`, `31238866032` | external dispatch DEFERRED (§12) |
| External scheduler credentials | **OWNER-LEVEL, not configured** | Claude's token is `Actions: read` → 403 | create account + scoped token |
| actionlint installation | **VERIFIED in `pr-fast`; Docker duplication remains in `full.yml`** | §11 | give a yes for the `full.yml` edit, or leave it |
| Python 3.14 compatibility of xdist / testmon / gremlins | **VERIFIED by running** | 242 tests, 267 mutants, all green on 3.14 | none |
| Mutation ID stability | **VERIFIED** | 267 mutants, 267 unique IDs, 0 missing, 0 duplicate, across runs | none |
| Bandit LOW/LOW noise | **VERIFIED clean** | `security-scan` green in both `pr-fast` and nightly | none |
| Code Quality eligibility | **VERIFIED unavailable** | `owner.type == "User"` (§13) | none |
| **Windows VM + TallyPrime** | **NOT INSTALLED — the single blocker** | `UTM.dmg` 238 MB in `~/Downloads`, not installed. No Windows ISO, no Tally installer found by `find` or `mdfind` | **install UTM → Windows on ARM → TallyPrime → enable its HTTP server** |
| First paying customer | **NOT IDENTIFIED** | frozen plan, open item 4 | owner |
| Real error frequency | **UNRESOLVED** | needs real books; no number invented | owner |
| Cross-organisation generalisation | **UNVERIFIED** | child #8 not started; UK central-government data is the free test | build #5 then #8 |
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

 7  M0  install UTM → Windows on ARM → TallyPrime → enable its HTTP server
        OWNER ONLY. Everything below waits on this.
 8  M1  connect to a real Tally test company, read-only
 9      one request returns one real company name  ← the whole transport proven at once
10      read the chart of accounts and one voucher
11  M2  write accountant/tallyio/real.py implementing the same 8-method Protocol
        reuse the XML envelope shapes from ~/a c d/backend/app/tally/xml_build.py
        nothing outside accountant/tallyio/ changes — that is what C3 exists for
12      build the minimal memory lookup against real history
13      make one validity decision on a real entry
14      write ONE marked voucher into a real test company
15      read it back by operation ID
16      post the same operation ID again → prove NO second voucher
17      reverse it
18      prove the trial balance returns to its exact prior value, in paise
19  M3  point tests/test_tally_contract.py's client fixture at the real client
        all 15 client-fixture tests pass → "works on real Tally" is satisfied
20  M4  the frontend — shape is an open owner decision (§20)
21  M5  widen through Slices 2-6 to the full frozen acceptance criteria
22      run all product acceptance criteria (S1-S7, N1-N3, N5)
23      revisit deferred risks only when their triggers fire
```

**Why this order:** steps 2–4 are polish on a system that already blocks
correctly. Step 7 is the only thing standing between 2,049 lines of code and the
first real voucher. Anything that delays step 7 delays the entire product.

---

## 20. Final status

### What is definitely working

```
20 CI gates, all active, all blocking — proven by 8 deliberate failures
branch protection — 6 tamper attempts refused, 9/9 drift audit
pr-fast 26-31s · pr-full 113s · ci-gate 8s · full cycle ~152s
nightly full.yml and watchdog.yml — both fired on schedule and passed
242 tests · 95% coverage · 99.63% mutation, 1 survivor of 267
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

### What is still unverified

```
EVERYTHING involving real Tally:
  read, write, idempotency, read-back, reversal, trial-balance restoration
children #2, #3, #14, #15 against their own written acceptance criteria
S1-S7 and N1-N3 — no product measurement has been taken
the third-party extraction backend — none is connected
cache hit/miss as a recorded number from a specific run
```

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

**Install UTM, then Windows on ARM, then TallyPrime, then switch on Tally's HTTP
server.** It is the only owner-blocked step, and every remaining product
milestone sits behind it. Until one real request returns one real company name,
2,049 lines of code and 20 CI gates are guarding something that has never touched
reality.

**The existence of this document does not mean the project is complete.**
