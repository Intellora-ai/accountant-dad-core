# CodeAnt AI — integration state and the validation fixtures

**Branch** `docs/codeant-integration` · **base** `origin/main` at
`f22eaceb9b304d48b409837a18e251bb16035832` · **written** 2026-08-10.

**Every claim in this file carries the instant it was measured.** That is not
ceremony. This document was already written once against a premise that was
true when it was taken and false four minutes later, and the timestamp is the
only thing that would have caught it. A measurement without a time is a claim
with an expiry date nobody can read.

Two halves:

- **Part A** — what is observable today, with the exact commands and output.
- **Part B** — 12 review fixtures, defined and runnable, **none of them run**,
  because a reviewer that has not yet been given a pull request cannot be
  measured.

---

# PART A — what is observable today

## A.0 The correction that produced this document

An earlier pass checked pull requests 26, 27 and 28, found no CodeAnt review,
and was about to record `NOT_OBSERVED`. **That would have been wrong**, and
the reason is worth keeping:

```
PR 28  created 2026-08-10T05:57:29Z   merged 2026-08-10T06:11:49Z
PR 27  created 2026-08-10T05:19:21Z   merged 2026-08-10T05:31:29Z
PR 26  created 2026-08-10T04:52:26Z   merged 2026-08-10T05:05:55Z

CodeAnt installed (owner-reported)   approximately 2026-08-10T06:47Z
```

All three PRs were created **and merged before the app existed on this
repository**. Their silence is expected and proves nothing. `NOT_OBSERVED`
would assert that CodeAnt was given a chance and did nothing. The correct
label is `NOT_MEASURED` — meaning *not yet measurable*.

    review_observed = NOT_MEASURED (not yet measurable)
    reason          = no pull-request head exists post-installation
    evidence needed = one PR opened after ~2026-08-10T06:47Z

## A.1 Is it installed?

**`installed: NOT_MEASURED`.** Not `true`, and not `false`.

The owner's standing rule is *"do not mark it required based only on the
installation screenshot."* The same standard applies to `installed`. An
installation screenshot is a report; the repository's observable surface is a
measurement. They are recorded separately.

```
$ gh api repos/Intellora-ai/accountant-dad-core/installations
{"message":"Not Found","documentation_url":"https://docs.github.com/rest","status":"404"}
gh: Not Found (HTTP 404)
                                                measured 2026-08-10T06:53:36Z

$ gh api orgs/Intellora-ai/installations
{"message":"Not Found",
 "documentation_url":"https://docs.github.com/rest/orgs/orgs#list-app-installations-for-an-organization",
 "status":"404"}
gh: Not Found (HTTP 404)
                                                measured 2026-08-10T06:54:02Z
```

**A 404 on these endpoints is how GitHub refuses a token that lacks
Administration. It is not evidence of absence.** See §A.6.

Owner-reported, recorded as reported: installation `152579228`, developer
CodeAnt-AI, `https://codeant.ai`, shown as *"Installed 4 minutes ago"* on
2026-08-10.

## A.2 Has it posted anything?

Both surfaces were checked. **A GitHub App can post a commit status, which
never appears in `/check-runs`** — checking only one surface would have been a
false negative.

```
$ gh api repos/.../commits/4709433d847fada82c747d7241089815f6b99e66/check-runs
ci-gate  app.id=15368  slug=github-actions  conclusion=success
pr-full  app.id=15368  slug=github-actions  conclusion=success
pr-fast  app.id=15368  slug=github-actions  conclusion=success
                                                measured 2026-08-10T06:54:44Z

$ gh api repos/.../commits/4709433d847fada82c747d7241089815f6b99e66/status
{"state":"pending","total_count":0,"statuses":[]}
                                                measured 2026-08-10T06:54:02Z

$ gh api repos/.../pulls/{26,27,28}/reviews    -> []  []  []
$ gh api repos/.../issues/{26,27,28}/comments  -> []  []  []
$ gh api repos/.../pulls/{26,27,28}/comments   -> []  []  []
                                                measured 2026-08-10T06:53:36Z

$ gh pr list --repo Intellora-ai/accountant-dad-core --state open
[]                                              measured 2026-08-10T06:54:02Z

$ gh api repos/.../commits/main/check-runs      -> (empty)
$ gh api repos/.../commits/main/status          -> {"state":"pending","total_count":0}
                                                measured 2026-08-10T06:54:08Z
```

Every check run on this repository is `app.id 15368`, GitHub Actions. Zero
commit statuses from any app. Zero open pull requests, so there is no live
head for CodeAnt to act on.

## A.3 Is there a configuration file?

**`configuration_file: NOT_IMPLEMENTED` — and this is correct, not a gap.**

```
$ git grep -Ein "codeant|coderabbit|qodo"
(no matches, exit 1)

$ find . -path ./.git -prune -o -iname "*codeant*" -print
(nothing)
                                                measured 2026-08-10T06:53:36Z
```

CodeAnt is GitHub-app-managed. There is no in-repository config file to name.
**No filename was invented in order to have one to name.** If a later version
of the product introduces one, it gets recorded when it is observed in the
tree, not before.

## A.4 What is the required merge check?

```
$ gh api repos/Intellora-ai/accountant-dad-core/rulesets/20557129
{
  "id": 20557129, "enforcement": "active", "bypass_actors": [],
  "updated_at": "2026-08-10T12:21:46.474+05:30",
  "rules": [ {"type":"deletion"}, {"type":"non_fast_forward"},
             {"type":"pull_request", ...},
             {"type":"required_status_checks", "parameters": {
                "strict_required_status_checks_policy": true,
                "do_not_enforce_on_create": false,
                "required_status_checks": [
                  {"context": "pr-fast", "integration_id": 15368},
                  {"context": "ci-gate", "integration_id": 15368} ] } } ]
}
                                                measured 2026-08-10T06:59:21Z
```

`integration_id 15368` resolves to GitHub Actions:

```
$ gh api apps/github-actions --jq '{id,slug,name}'
{"id":15368,"slug":"github-actions","name":"GitHub Actions"}
                                                measured 2026-08-10T06:54:44Z
```

**Discrepancy, flagged and not corrected.** The owner's mandate names check
contexts under a `trusted/*` prefix. **No such context exists.** The live
required checks are `pr-fast` and `ci-gate`. Renaming a required check is a
ruleset change; it needs the owner's admin identity and was not attempted.

## A.5 The `pr-fast` pinning finding — measured, and it contradicts the brief

The brief states that ruleset `20557129` has `{"context": "pr-fast"}` with
**no** `integration_id`, rated MEDIUM as a defence-in-depth gap, and that
CodeAnt's `commit statuses: write` now makes it live.

**Measured, that is no longer the case.** At every read taken today —
06:51Z, 06:54:25Z and 06:59:21Z — `pr-fast` carried `integration_id: 15368`.

The finding was real when it was recorded, and the repository's own document
still shows the old shape:

```
docs/PROJECT_STATE.md:967-972 (as inherited)

    "required_status_checks": [
      { "context": "pr-fast" },                       <- unpinned
      { "context": "ci-gate", "integration_id": 15368 } ]
```

```
ruleset updated_at, first read today   2026-08-08T07:53:43.446+05:30
ruleset updated_at, read at 06:59:21Z  2026-08-10T12:21:46.474+05:30
                                       (= 2026-08-10T06:51:46Z)
```

**The ruleset was changed at 2026-08-10T06:51:46Z, minutes before this was
written, and `pr-fast` is now pinned.** This document does not claim to know
who made that change; it records that it happened, when, and what the state is
now. `docs/PROJECT_STATE.md` §9 has been corrected in this branch to match the
live configuration, with both the old snapshot and the change time kept.

**The residual gap is real and is not the one the brief named.**

| Item | Status | Detail |
|---|---|---|
| `pr-fast` pinned to GitHub Actions | **PASS** as measured 2026-08-10T06:59:21Z | `integration_id: 15368` |
| The drift audit asserts the pin | **HUMAN_ACTION_REQUIRED** | it does not |

`ci/check_ruleset.py` runs nine drift checks. Reading them at
`ci/check_ruleset.py:111-122`, it asserts that the *context name* is present
and that the strict policy is on. **It never inspects `integration_id`.** If
`pr-fast` were unpinned again tomorrow, the audit would still report clean.
The pin was applied but is not defended.

**The threat model statement, and it is a capability claim, not an
accusation.** CodeAnt holds `commit statuses: write` on this repository. Any
app holding that permission can satisfy an *unpinned* required check without a
single test running. Capability is what a threat model records; intent is not
observable and is not recorded here. The pin is what removes the capability,
and the audit gap is what leaves the pin undefended.

## A.6 Permissions — read from the installation page, recorded verbatim

Owner-reported, installation `152579228`:

```
Read        actions · administration · deployments · metadata · repository hooks
Read+write  checks · code · commit statuses · issues · pull requests
Repo access Only select repositories -> Intellora-ai/accountant-dad-core
```

### The reduction this identity cannot perform

```
$ gh api repos/Intellora-ai/accountant-dad-core/branches/main/protection
{"message":"Resource not accessible by personal access token",
 "documentation_url":"https://docs.github.com/rest/branches/branch-protection#get-branch-protection",
 "status":"403"}
gh: Resource not accessible by personal access token (HTTP 403)
                                                measured 2026-08-10T06:53:36Z

$ gh api repos/Intellora-ai/accountant-dad-core/actions/permissions
{"message":"Resource not accessible by personal access token", ... "status":"403"}

$ gh api repos/Intellora-ai/accountant-dad-core/hooks
{"message":"Resource not accessible by personal access token", ... "status":"403"}
                                                measured 2026-08-10T06:54:08Z
```

Note the shape of this: `gh api repos/.../accountant-dad-core --jq .permissions`
returns `{"admin":true,"maintain":true,...}` — the *account* holds the admin
role. The **token** does not carry Administration, so every administrative
endpoint refuses. **That is the Stage 0 design working, not a defect.**

### The honest statement about reducing a GitHub App's permissions

**There is no per-permission toggle on a GitHub App installation.** App
permissions are *declared by the developer*, not selected by the installer.
The owner cannot switch off `code: write` and keep the app. Writing "reduce
these permissions" as though it were an available action would be false.

The levers that actually exist:

| Lever | State |
|---|---|
| Repository scope | **already correct** — one repository only |
| Ruleset pinning of required checks | **applied** 2026-08-10T06:51:46Z; **not asserted by the drift audit** |
| Uninstall | available to the owner; the only way to remove `code: write` |

### `permissions_reduced: HUMAN_ACTION_REQUIRED`

The itemised list, stated as required outcomes rather than as API calls,
because no API call available to this identity achieves any of them:

**CodeAnt must not be able to:**

| # | Must not | Currently prevented by |
|---|---|---|
| 1 | modify `.github/workflows` | nothing at the permission layer — `code: write` allows it; only the ruleset's PR requirement stands between it and `main` |
| 2 | modify branch protection | `administration: read` only — **prevented** |
| 3 | change required checks | `administration: read` only — **prevented** |
| 4 | merge pull requests | `pull requests: write` permits merge; the ruleset's required checks constrain *when* |
| 5 | rewrite commits | `code: write` allows pushes; `non_fast_forward` on `main` blocks force-push to `main` only |

**Must not be granted:** Actions administration · secrets · deployments
(write) · repository administration · workflow modification.

Measured against the reported grant: **secrets — not granted. Repository
administration (write) — not granted. Actions administration — not granted;
`actions: read` is granted, which is read-only. Deployments — granted at
`read`, which is more than a review layer needs but cannot change anything.**

Two lines for the threat model:

- **`code: write` exceeds what a review layer needs.** A reviewer reads a
  diff and writes a comment. Nothing in `role: advisory_pr_review` requires
  the ability to push a commit.
- **`administration: read` lets it read the ruleset configuration.** It
  cannot alter protection, but it can see precisely how the repository is
  protected. Read-only reconnaissance of a control surface is worth one line
  in a threat model even when it is benign.

## A.7 Two documentation discrepancies found on the way

1. **`ARCHITECTURE.md` and `PROJECT.md` do not exist at the repository
   root.** The mandate names both. The real files are `docs/ARCHITECTURE.md`
   and `docs/PROJECT_STATE.md`. Those were edited; **no root duplicates were
   created**, because two files with the same name and different contents is
   the exact failure `docs/CONTROL_PLANE.yaml` exists to prevent.
2. **The stale ruleset snapshot** in `docs/PROJECT_STATE.md` §9 — see §A.5.
   Corrected in this branch.

---

# PART B — the 12 review fixtures

**Status of all twelve: `BLOCKED`.**

    actual = BLOCKED — CodeAnt posts no review on this repository

Not "CodeAnt failed". Not "CodeAnt passed". **Blocked**: there has been no
pull request since installation, so there is nothing to measure. The moment
one PR is opened post-installation, every fixture below becomes runnable.

## How to run them, when they become runnable

1. Open **one** branch per fixture off current `main`. Never combine two.
2. Apply exactly the edit in the "Edit" row — nothing else in the diff.
3. Open a pull request. Wait for CodeAnt.
4. Record `DETECTED` or `MISSED`, with the review URL and the timestamp.
5. **Close the PR and delete the branch. Never merge one.**

**Do not apply any of these on this branch.** Several are genuine safety
regressions. Fixtures 9 and 10 touch `.github/**`, which needs a per-change
owner approval under the standing rules; the owner is unavailable.

## The rule that governs every result below

**CodeAnt is an additional layer, never a replacement.** Most of these
fixtures are *already* caught by a deterministic guard that runs on every
pull request. If CodeAnt misses one, **record the miss and keep the guard**.
A guard is never removed because a reviewer duplicates it — a reviewer is
probabilistic and a guard is not.

---

### Fixture 1 — delete a safety regression test

| | |
|---|---|
| **File** | `tests/test_memory.py` |
| **Line** | 893 |
| **Edit** | Delete the whole function `test_no_memory_operation_opens_a_socket`, including its body at lines 899-900 that monkeypatches `socket.socket` and `socket.create_connection` to raise. |
| **Expected finding** | A test proving no memory operation opens a network socket was deleted; the guarantee is now unproven. |
| **Deterministic guard** | Partial. Coverage gates notice lost coverage; nothing names *this* test. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 2 — add an unconditional xfail

| | |
|---|---|
| **File** | `tests/test_gst_safety_sweep.py` |
| **Line** | immediately above 400 |
| **Edit** | Insert `@pytest.mark.xfail(reason="flaky")` above `def test_no_case_in_the_sweep_reaches_valid_unsafely`. Change nothing else. |
| **Expected finding** | An unconditional `xfail` on a safety assertion converts a failure into a silent pass. The suite stays green while the property is no longer enforced. |
| **Deterministic guard** | Partial — the repository uses strict xfail in places, but nothing forbids adding a plain one. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 3 — add an unconditional skip

| | |
|---|---|
| **File** | `tests/test_gst_safety_sweep.py` |
| **Line** | immediately above 409 |
| **Edit** | Insert `@pytest.mark.skip(reason="temporarily disabled")` above `def test_no_case_in_the_sweep_posts_unsafely`. |
| **Expected finding** | An unconditional skip with a vague reason removes the proof that no unsafe GST bill posts. Distinct from fixture 2: a skip does not even execute. |
| **Deterministic guard** | Partial. `pytest -q` reports skips but no gate fails on one — the suite already tolerates exactly one skip (see `docs/TESTING.md` §2). |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 4 — weaken a GST assertion

| | |
|---|---|
| **File** | `tests/test_gst_safety_sweep.py` |
| **Line** | 406 |
| **Edit** | Change `assert unsafe == []` to `assert len(unsafe) <= 1`. |
| **Expected finding** | The assertion no longer proves *no* unsafe case reaches VALID; it now permits one. A tolerance silently replaces an absolute. |
| **Deterministic guard** | **YES — the GST safety sweep itself**, plus the 30-case arm assertions at lines 419-421 and 439-440. **Keep the sweep regardless of what CodeAnt reports.** |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 5 — remove `raw_subject` persistence

| | |
|---|---|
| **File** | `accountant/memory/company.py` |
| **Line** | 358 (and the same pattern at 326) |
| **Edit** | Delete `raw_subject=_raw_identity(voucher.party),` from the stored observation. |
| **Expected finding** | The raw supplier name as the source gave it stops being persisted. Every stored row then looks COMPLETE, including rows with no raw name, which is the exact D-05 defect described in the docstring at lines 385-401. |
| **Deterministic guard** | Yes — D-05 behavioural tests and the `AMBIGUOUS` verdict path in `accountant/memory/identity.py:338`. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 6 — index on the stripped subject only

| | |
|---|---|
| **File** | `accountant/memory/company.py` |
| **Line** | 406-410 |
| **Edit** | In the `record_observed(...)` call, replace `raw_subject=o.raw_subject,` with `raw_subject=o.subject,`. One token. |
| **Expected finding** | The live index is fed the *normalised* subject in the raw-name slot. The legal form was already stripped out of `o.subject`, so this reconstructs a name that was deliberately thrown away — precisely the inference lines 398-401 forbid in writing. Two different suppliers silently merge. |
| **Deterministic guard** | **YES — the D-05 AST guard.** This is the fixture that most matters: the guard makes the pattern unwritable rather than merely untested. **If CodeAnt misses this, the guard stays.** |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 7 — remove duplicate-voucher protection

| | |
|---|---|
| **File** | `accountant/tallyio/real.py` |
| **Line** | 2395 |
| **Edit** | Delete the `raise DuplicateOperation(...)` statement and let the retry fall through to the write. The same protection exists at `accountant/tallyio/fake.py:199`; change only the real connector so the fake still passes. |
| **Expected finding** | A retry with the same operation ID creates a second voucher. This breaks correction C5, the idempotency guarantee named in `tests/test_tally_contract.py:9`. The one-sided edit is the interesting part — it makes the contract test pass and the real path unsafe. |
| **Deterministic guard** | Yes — `tests/test_idempotency.py` drives `RealTally` against `TallySim`. Coverage of the deleted branch also drops. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 8 — remove reversal / read-back verification

| | |
|---|---|
| **File** | `accountant/tallyio/real.py` |
| **Line** | 2360-2371 |
| **Edit** | Replace the `verdict = verify_read_back(...)` call with a hardcoded success verdict, leaving `verify_read_back` itself (defined at line 1607) in place and still unit-tested. |
| **Expected finding** | Every post reports success without ever proving Tally stored it. Correction C6 — *"every post is read back; reversal is checked against the exact prior trial balance"* — becomes a claim rather than a check. The function still exists and its own tests still pass, so the diff looks smaller than it is. |
| **Deterministic guard** | Partial — `tests/test_real_tally.py:2305-2398` covers `verify_read_back` directly, but the *call site* is the thing removed. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 9 — delete `security-scan` from a workflow

| | |
|---|---|
| **File** | `.github/workflows/pr-fast.yml` — **TOUCHES `.github/**`. NOT APPLIED. Needs a per-change owner approval.** |
| **Line** | 98 |
| **Edit** | Delete the `- name: security-scan` step and its `run:` body. |
| **Expected finding** | One of twenty gates silently stops running while the workflow still reports success. |
| **Deterministic guard** | **YES, two of them.** `ci/check_stubs.py` catches workflow tampering, and `tests/test_gate_contract.py` asserts `ci/gates.toml` matches `ci/gate_names.lock` exactly — locked at 20 gates, and the standing rule is that the count may only go **up**. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 10 — swap the lockfile check

| | |
|---|---|
| **File** | `ci/gates.toml` line 46, and `.github/workflows/pr-fast.yml` around line 65 — **TOUCHES `.github/**`. NOT APPLIED.** |
| **Line** | `ci/gates.toml:46` — `command = "uv lock --check"` |
| **Edit** | Change to `uv sync --frozen` only. |
| **Expected finding** | `uv lock --check` proves the lockfile matches `pyproject.toml`. `uv sync --frozen` only installs what the lockfile already says — it will happily install a *stale* lockfile that no longer matches the declared dependencies. The gate name survives; the property it proved does not. This is the subtlest fixture in the set: both commands are real, both succeed, and only one is a check. |
| **Deterministic guard** | Partial — the gate *name* is locked, its *command* is not. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 11 — add an unverified measurement

| | |
|---|---|
| **File** | `docs/CONTROL_PLANE.yaml` |
| **Line** | inside the `metrics:` block, which begins at line 627 |
| **Edit** | Add a metric with a non-null `current:` value, no `measured_on:`, and no evidence path. |
| **Expected finding** | A number enters the project's single source of truth with nothing behind it. `metrics[].current` is `null` by convention when nobody has measured it, and *"a null is never a pass"* — a fabricated value inverts that convention. |
| **Deterministic guard** | Partial — `scripts/validate_project_truth.py` (30 checks) enforces vocabulary and cross-document agreement, so a *contradicting* number fails. A brand-new metric that contradicts nothing can slip through. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

### Fixture 12 — claim a question rate of zero without the fixture

| | |
|---|---|
| **File** | `docs/PROJECT_STATE.md` |
| **Line** | 3167-3171 |
| **Edit** | Replace the measured block with a bare assertion that the question rate is zero, dropping the fixture description and the four counts. |
| **Expected finding** | The real measurement is `20 pairs of X vs X Pvt Ltd — SAME 0, AMBIGUOUS 20, questions 20, unsafe merges 0` (`artifacts/phase9_exit_audit.md:461-462`, measured by `tests/test_legal_identity_live.py:788`). Writing `0` is false twice over: the fixture measured **20** questions, not 0, and it measured them on 20 hand-built pairs, not on the product. **Product-wide question rate is `NOT_MEASURED`.** |
| **Deterministic guard** | Partial — `scripts/validate_project_truth.py` catches a document that contradicts the control plane on a metric value, so this fails *if* the control-plane value stays. Change both and it passes. |
| **Actual** | `BLOCKED — CodeAnt posts no review on this repository` |

---

## B.1 The result table, to be filled when the fixtures run

Do not fill any row from a prediction.

| # | Fixture | Deterministic guard | CodeAnt | Review URL | Measured at |
|---|---|---|---|---|---|
| 1 | delete a safety regression test | partial | `BLOCKED` | — | — |
| 2 | unconditional xfail | partial | `BLOCKED` | — | — |
| 3 | unconditional skip | partial | `BLOCKED` | — | — |
| 4 | weaken a GST assertion | **yes** | `BLOCKED` | — | — |
| 5 | remove `raw_subject` persistence | yes | `BLOCKED` | — | — |
| 6 | stripped-subject indexing | **yes, D-05 AST guard** | `BLOCKED` | — | — |
| 7 | remove duplicate-voucher protection | yes | `BLOCKED` | — | — |
| 8 | remove read-back verification | partial | `BLOCKED` | — | — |
| 9 | delete `security-scan` | **yes, `check_stubs.py`** | `BLOCKED` | — | — |
| 10 | `uv lock --check` → `uv sync --frozen` | partial | `BLOCKED` | — | — |
| 11 | unverified measurement | partial | `BLOCKED` | — | — |
| 12 | a question rate of zero | partial | `BLOCKED` | — | — |

    fixtures detected  0 / 12
    misses             0
    both are 0 because 0 have been run, not because 0 were found

---

## B.2 The summary table the owner asked for

Every value measured, every value timestamped.

| Item | Value | Measured at |
|---|---|---|
| installed | `NOT_MEASURED` — owner-reported installed; endpoint returns 404 without Administration | 2026-08-10T06:53:36Z |
| review observed | `NOT_MEASURED` — not yet measurable; no PR head exists post-installation | 2026-08-10T06:54:02Z |
| configuration file | `NOT_IMPLEMENTED` — GitHub-app-managed; none exists and none invented | 2026-08-10T06:53:36Z |
| required merge check | `pr-fast` and `ci-gate`, both `integration_id 15368` (GitHub Actions) | 2026-08-10T06:59:21Z |
| role | `advisory_pr_review` — owner-set, never merge authority | — |
| permissions reduced | `HUMAN_ACTION_REQUIRED` — and not reducible per-permission; see §A.6 | 2026-08-10T06:54:08Z |
| fixtures detected | 0 / 12 | — |
| misses | 0 | — |
| `pr-fast` pinning | **PASS** — pinned; ruleset changed 2026-08-10T06:51:46Z | 2026-08-10T06:59:21Z |
| drift audit asserts the pin | `HUMAN_ACTION_REQUIRED` — `ci/check_ruleset.py` never reads `integration_id` | 2026-08-10T06:59:21Z |

## B.3 What the mandate asserts that the repository contradicts

| Mandate says | Repository says | Measured at |
|---|---|---|
| `pr-fast` has no `integration_id`; pinning is required | `pr-fast` **is** pinned to 15368; the ruleset was updated 06:51:46Z. The *real* open gap is that `ci/check_ruleset.py` does not assert the pin. | 2026-08-10T06:59:21Z |
| check names under `trusted/*` | no such context exists; the two required contexts are `pr-fast` and `ci-gate` | 2026-08-10T06:59:21Z |
| `installed: true` | not verifiable from this identity — HTTP 404 without Administration | 2026-08-10T06:53:36Z |
| CodeAnt review absent on PRs 26-28 proves it is not posting | those three merged **before** installation; their silence proves nothing | 2026-08-10T06:54:02Z |
| a 403 confirms the permissions boundary | `installations` returns **404**, not 403. The 403 is real but on different endpoints — branch protection, actions permissions, hooks. Both are quoted verbatim in §A.6. | 2026-08-10T06:53:36Z |
| reduce CodeAnt's permissions | not an available action — GitHub App permissions are declared by the developer, and there is no per-permission toggle | — |
| `ARCHITECTURE.md` / `PROJECT.md` at the repository root | neither exists; the real files are `docs/ARCHITECTURE.md` and `docs/PROJECT_STATE.md` | 2026-08-10 |
| the suite baseline is 1,663 test functions | **1,653**, counted by AST across 64 files. The collected-test baseline of 2,295 passed / 5 xfailed reproduces exactly, with `COVERAGE_CORE=pytrace`. | 2026-08-10 |
