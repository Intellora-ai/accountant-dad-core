# Gate integrity audit

**Repository** `Intellora-ai/accountant-dad-core` · **public** · owner type
`User` · one collaborator
**Trusted ref audited** `origin/main` = `f22eace` at the time of the audit.
Main advanced to `d98adc3` while this branch was being written (Phase 8, a
parallel workstream). None of the files this audit owns changed in it, and
`ci/check_workflow_integrity.py` was re-run against `d98adc3`: still **PASS**.
**Ruleset** `20557129`, read live 2026-08-10
**Gate count** before **20**, after **20** — no gate added, none removed
**Verdict** `gate integrity = FAIL` — see §10 for exactly what is missing

Everything below was verified against the files and the live API in this
session. Two numbers relayed to me in the brief were checked and were correct;
one line reference was off by one and is corrected in place.

---

## 1. The questions, answered

### What workflow is authoritative?

Two, on two paths.

| Path | Workflow | Required context | Where |
|---|---|---|---|
| Every push to a PR | `.github/workflows/pr-fast.yml`, job `pr-fast` | `pr-fast` | `pr-fast.yml:42-43`; `ci/gates.toml:16` |
| Merge queue | `pr-fast.yml` job `ci-gate` **and** `full.yml` job `ci-gate` | `ci-gate` | `pr-fast.yml:359-360`, `full.yml:244-245`; `ci/gates.toml:17` |

Both names match the live ruleset character-for-character:

```
required_status_checks: [ {"context": "pr-fast", "integration_id": 15368},
                          {"context": "ci-gate", "integration_id": 15368} ]
strict_required_status_checks_policy: true
```

### Where is it sourced from? Which branch or commit supplies it?

**The pull request's own branch.** This is the root cause.

- `pr-fast.yml:14-15` — `on:` / `pull_request:`. GitHub runs the copy of the
  workflow that is in the head ref of the pull request.
- `pr-fast.yml:23` — `merge_group:`. The merge-group ref is the PR's content
  merged into main, so the merge queue also runs the PR's copy.
- `full.yml:11-12` — `on:` / `merge_group:`. Same.

**Proven twice on this repository:**

1. **PR #12** changed exactly two files — `.github/workflows/pr-fast.yml` (+15
   lines) and `ci/gates.toml` (+2/−2). The 15 lines added three steps:
   `install actionlint`, `workflow-lint`, `workflow-security`. Its own
   `pr-fast` run, `31236026164` / job `93048552980`, executed those three steps
   as steps 10, 11 and 12, all `success`. The workflow that graded PR #12 was
   the workflow PR #12 wrote. Zero reviews on the PR.
2. **Deleting the `security-scan` step** (`pr-fast.yml:98-103`; the brief said
   98-104, line 104 is blank) passes all **18** tests in
   `tests/test_gate_contract.py`, passes `ci/check_stubs.py`, passes `ci-gate`
   and passes the nightly. Reproduced as fixture 1 of 17, below.

The reason is one line: `tests/test_gate_contract.py:227-236` binds a gate to a
**job**, via `g["command"].split()[0]`, and `ci/gates.toml:186` says
`jobs = ["pr-fast", "security"]`. Both jobs still exist after the step is
deleted, so nothing objects.

### Which jobs are required?

Two contexts, five job definitions behind them.

| Required context | Job that publishes it | File |
|---|---|---|
| `pr-fast` | `pr-fast` | `pr-fast.yml:42` |
| `ci-gate` | `ci-gate` (needs `pr-fast`, `pr-full`) | `pr-fast.yml:359,364` |
| `ci-gate` | `ci-gate` (needs `full-tests`, `security`, `build`, `workflow-checks`, `mutation`) | `full.yml:244,251-256` |

`ci/check_aggregate.py:37-45` decides which of those must have reported, per
phase, and reads the job list from `ci/gates.toml` rather than from a list typed
into the workflow.

### Which **steps** are required?

**None. That is the whole finding.**

Nothing anywhere in this repository, before this work, asserted the existence of
a single step. `ci/gates.toml` has a `jobs` key and no `steps` key.
`tests/test_gate_contract.py` checks `jobs`. `ci/check_aggregate.py` reads
`needs.*.result`, which is a job-level result. GitHub's required status checks
are job-level. A step can be deleted and every layer still reports success.

`ci/check_workflow_integrity.py` (new, this change) is the first thing in the
repository that compares steps at all. It treats every step name that exists on
`origin/main` as required.

### Who can modify it?

Anyone who can open a pull request — on a **public** repository, that is anyone
with a GitHub account, via a fork. Merging additionally requires `pr-fast` and
`ci-gate` green, which the PR's own workflow decides.

### Who can approve it?

Nobody has to.

```
pull_request rule:
  required_approving_review_count: 0
  require_code_owner_review:       false
  required_reviewers:              []
  require_last_push_approval:      false
  required_review_thread_resolution: false
```

No `CODEOWNERS` file exists — checked `.github/CODEOWNERS`, `/CODEOWNERS` and
`docs/CODEOWNERS`; `git ls-files | grep -i codeowners` returns nothing.

And `gh api repos/.../collaborators` returns exactly one row:
`Intellora-ai  admin`. Every merged PR (#1–#28) was authored by that same
account. **Even if CODEOWNERS and a review requirement were added today, there
is no second person who could satisfy them, and GitHub does not let an author
approve their own pull request.**

### Can a PR delete or weaken a required step?

**Before this change: yes, silently.** After: caught, provided the PR does not
also delete the checker. See §4 and §5.

### Can a PR change the expected gate manifest?

**Before: yes.** `ci/gate_names.lock` cannot detect its own shortening.
`tests/test_gate_contract.py:242-281` compares `ci/gates.toml` with
`ci/gate_names.lock` — two files in the same commit. Delete a name from both and
`set(locked) - set(current)` is empty and `len(gates()) >= len(locked)` reads
`19 >= 19`. Both assertions pass.

**After:** the lock is read from `origin/main`, which the PR cannot edit while it
is being graded. Fixtures 7 and 8.

### Can skipped protection tests pass?

**Before: they did, on every hosted run.** `ci/test_protection.py:70-73` was

```python
pytestmark = pytest.mark.skipif(
    not reachable(),
    reason="no gh or no network: refusing to fake a protection result",
)
```

`reachable()` (`:64-67`) shells out to `gh api repos/<repo>`. **No job in any of
the four workflows sets `GH_TOKEN`** — grepped, confirmed. `gh` is therefore
unauthenticated on every GitHub-hosted run, `reachable()` is `False`, and all
nine protection tests skip. Nine green squares that called GitHub zero times.

Compounding it, `FORBIDDEN_STATUSES = (401, 403, 404)` at `:42` treated **401
Unauthenticated** as proof of refusal. A removed or expired token would have
turned the entire file green for exactly the wrong reason.

**After:** three outcomes, never a silent one — run, or fail in CI naming the
missing prerequisite, or skip locally reporting `LOCAL_ENVIRONMENT_UNAVAILABLE`.
`FORBIDDEN_STATUSES` is now `(403, 404)`; 401 stops the test rather than
satisfying it.

### Is lock drift detected?

**Before: no.** See above.
**After: yes, against `origin/main`, for the PR under review only.** Once the PR
merges, main's lock is the merged lock. Stated, not hidden.

---

## 2. The three claims from the brief, re-derived

| Claim | Verdict | Evidence found in this session |
|---|---|---|
| **C1** a PR writes its own gates | **CONFIRMED** | PR #12 run `31236026164`; fixture 1 |
| **H2** the lock cannot detect a deletion | **CONFIRMED** | `tests/test_gate_contract.py:242-281`; fixture 8 |
| **H3** `uv lock --check` never runs in CI | **CONFIRMED** | Gate 1 of 20, `ci/gates.toml:44-54`, `required = true`, `failure_behaviour = "block"`. The only `uv lock` string in `.github/workflows/` is the comment at `pr-fast.yml:65`. Independently re-derived by the new checker, which searches comment-stripped step bodies and reports `lockfile` as declared-but-never-executed. |
| **M10** the protection test always skips in CI | **CONFIRMED** | `ci/test_protection.py:64-73`, `:42` |
| **M5** `split()[0]` matches a version print | **CONFIRMED** | `tests/test_gate_contract.py:233`; `bandit` still appears at `pr-fast.yml:188` in the reproducibility record after the step is deleted |

One extra gate is unwired that the brief did not mention: **`cached-mutation`**.
Its command contains `--gremlin-cache`, which appears only inside comments
(`pr-fast.yml:308-320`, `full.yml`). That one is *deliberate* — `ci/gates.toml:138`
records it as PARKED with the reason, and the note is accurate. It is reported
as a standing warning, not as a regression, and
`tests/test_gate_integrity.py::test_the_declared_but_unexecuted_gates_are_exactly_the_two_known_ones`
pins the list at exactly `['cached-mutation', 'lockfile']` so a third cannot
appear quietly.

### What the brief said was strong — confirmed, and left untouched

`bypass_actors: []`, `current_user_can_bypass: "never"`,
`strict_required_status_checks_policy: true`, 39/39 actions SHA-pinned, no
`pull_request_target`, no `workflow_run`, `persist-credentials: false` on every
checkout, `ci/check_aggregate.py` fail-closed with an `else:` catch-all at
`:118-120` — all confirmed live, none modified.

Add to that list a positive fact worth stating as a design win rather than only
as a limitation: **the account holds admin and the token withholds it.**

```
gh api repos/Intellora-ai/accountant-dad-core --jq .permissions
  {"admin": true, "maintain": true, "pull": true, "push": true, "triage": true}
```

The account is a repository admin. The fine-grained token Claude runs as has
`Administration = No access`, so every administrative call is refused. That
separation is the whole of Stage 0, and it is working. The refusals, quoted with
their own status codes rather than merged into one claim:

| Endpoint | Status | What it means |
|---|---|---|
| `repos/.../installations` | **404 Not Found** | how GitHub refuses a token without Administration on this endpoint — it hides the resource rather than admitting it exists |
| `repos/.../branches/main/protection` | **403 Forbidden** | refused outright |
| `repos/.../actions/permissions` | **403 Forbidden** | refused outright |
| `repos/.../hooks` | **403 Forbidden** | refused outright |
| `repos/.../rulesets/{id}/rule-suites` | **403 Forbidden** | refused outright |
| `repos/.../` | **200 OK** | reads that the audit needs still work |

404 and 403 are not interchangeable. A checker that treats "404" as "refused"
will also treat "the resource was deleted" as "refused".

---

## 2a. M9 — the unpinned required check. Closed by the owner, mid-session.

**Measured, not relayed.** I read the ruleset twice today and it changed between
the reads:

```
first read   updated_at 2026-08-08T07:53:43.446+05:30
second read  updated_at 2026-08-10T12:21:46.474+05:30   (= 06:51:46Z)

  required_status_checks[0]:
-   {"context": "pr-fast"}
+   {"context": "pr-fast", "integration_id": 15368}
```

`ci-gate` already carried the pin; `pr-fast` did not. **`pr-fast` unpinned is no
longer a live finding.**

### Why it mattered, and why the timing is the story

A required status check is satisfied by *whoever writes a status with that exact
string*. Unpinned, **any** GitHub App installed on the repository holding
`commit statuses: write` could write `pr-fast: success` and satisfy the required
check with no gate having run at all.

That is not hypothetical here. This repository has more than one app that
reports checks — measured on commit `f22eace`:

```
check-suites:  {"app": "github-actions", "id": 15368}
               {"app": "claude",         "id": 1236702}
```

So the pin excludes something real. `15368` is GitHub Actions, verified rather
than assumed: the check runs on `f22eace` report `app.slug: "github-actions"`
with `app.id: 15368`.

**Sequence, in UTC, not two separate rows:**

| Time | Event | How I know |
|---|---|---|
| 06:11:49Z | PR #28 merges; `f22eace` becomes `main` | GitHub API |
| ~06:47Z | **CodeAnt AI installed with `commit statuses: write`** | **RELAYED, not verified by me.** `repos/.../installations` returns **404** to this token, so I cannot read the install time or the permission set |
| 06:51:46Z | Owner pins both required contexts to app `15368` | ruleset `updated_at`, read live |
| 06:59:21Z | Coordinator's measurement | relayed |
| 07:17:21Z | `codeant-ai[bot]` acts on the repository for the first time I can see (`IssueCommentEvent`) | `repos/.../events`, read live |

What I can corroborate independently: the bot is real and active here. What I
cannot: its install time or its permissions. The exploit acquired a concrete
actor and the pin closed it about four minutes later — on the relayed timing,
roughly 26 minutes before that actor was first observed doing anything.

**Who could still change it:** the owner, from the GitHub web UI or an admin
token. `bypass_actors` is `[]` and `current_user_can_bypass` is `"never"`, so no
app and no CI job can. Claude cannot — four live refusals this session.

---

## 2b. NEW FINDING — the pin was applied but undefended. Now fixed.

`ci/check_ruleset.py:111-122` asserted that the required context **name** was
present and that the strict policy was on. **It never looked at
`integration_id`.** So if `pr-fast` were unpinned tomorrow — by an admin, by a
UI mis-click, by an app — the drift audit would have reported a clean **9/9**
and nobody would have been told.

Same defect class as C1 and H2: **a protection that exists but is not itself
guarded.**

I then applied the same question to every other assertion in the file — *would
this audit notice if the setting were silently reverted?* Four more said no.

| # | Assertion the audit made | Would a silent revert be noticed? | Fixed? |
|---|---|---|---|
| 1 | required context **name** present | yes | — |
| 2 | required context **pinned to an app** | **NO — not checked at all** | **FIXED** — `integration_id` must be present, and must equal the app the contract names |
| 3 | `meta.required_mq_check` (`ci-gate`) required | **NO** — only `required_pr_check` was ever checked. `ci-gate` could be dropped from the required contexts and the audit stayed green | **FIXED** |
| 4 | the ruleset's `conditions` actually match `main` | **NO** — `conditions.ref_name.include` was never read. Point it at `refs/heads/nope` and every rule below stays intact while `main` is unprotected. The old audit passed 9/9 on exactly that | **FIXED** — `include` must cover the default branch, `exclude` must be empty |
| 5 | rulesets 2..n | **NO** — `audit()` read `active[0]` and ignored every other branch ruleset, including any bypass actor in one | **FIXED** — all branch rulesets are checked for bypass actors, and their ids are printed |
| 6 | `pull_request` rule **parameters** | **NO** — only the rule's presence was checked, never `required_approving_review_count` or `require_code_owner_review` | **NOT FIXED — deliberately.** See below |
| 7 | anything outside the assertion list | **NO** — there is no stored fingerprint of the ruleset anywhere in the repository | **NOT FIXED — owner decision, R8** |

**Why #6 is reported rather than fixed.** The owner has not set a review
requirement: the live values are `required_approving_review_count: 0` and
`require_code_owner_review: false`. Asserting a floor means writing a number the
owner never gave, and a floor of zero defends nothing. So the values are
**printed** under a heading that says what they are:

```
  observed, NOT asserted — a silent revert here is invisible:
    · branch rulesets on this repository: [20557129]
    · pull_request.required_approving_review_count: 0
    · pull_request.require_code_owner_review: False
    · pull_request.require_last_push_approval: False
    · pull_request.dismiss_stale_reviews_on_push: False
    · pull_request.allowed_merge_methods: ['squash', 'merge', 'rebase']
```

Stated plainly: **the moment the owner raises the review count, a silent revert
the next day will still report clean.** The fix is one line — move those two
from `observe()` to `require()` against the owner's number — and it must wait
for the owner to give the number.

The audit went from **9 assertions to 16**, all passing live. Seven new tests in
`tests/test_gate_integrity.py` prove each new assertion actually catches its
revert, using a canned copy of ruleset `20557129` and a fake `gh` on `PATH`, so
they need neither network nor token.

`ci/gates.toml` gained one `[meta]` key, `required_check_app_id = 15368`. That
is not a number I chose: it is the value the owner applied at 06:51:46Z, read
live, and verified to be GitHub Actions. No gate was added; the count is still
20.

---

## 3. What was built, for real

Everything in this section is applied in the working tree and committed.

| File | Status | What it does |
|---|---|---|
| `ci/check_workflow_integrity.py` | **new, 1054 lines, stdlib only** | Compares this tree against `git show origin/main:<path>` |
| `tests/test_gate_integrity.py` | **new** | 17 malicious-PR fixtures + 9 assertions. Runs inside the existing `changed-tests` and `full-tests` gates, so it executes on every PR **today**, with no `.github` change |
| `ci/test_protection.py` | **modified** | Skip removed; 401 no longer proof |
| `ci/check_ruleset.py` | **modified** | 9 assertions -> 16. Pins, both required contexts, ruleset conditions, all rulesets |
| `ci/gates.toml` | **modified** | one `[meta]` key, `required_check_app_id = 15368`. No gate added or removed |
| `ci/gate_names.lock` | **modified** | comments only. The 20 names are byte-identical |
| `ci/workflow_changes.ack` | **new** | Where a `.github` change is declared. Policy, labelled as such in its own header |
| `artifacts/gate_threat_model.md` | new | Mechanism vs policy, actor by actor |
| `artifacts/gate_integrity_audit.md` | this file | |
| `artifacts/gate_integrity_results.json` | new | The fixture run, machine-readable |

**`.github/**` was not touched.** Not one byte. `git diff --stat origin/main --
.github/` is empty. The patch is stated in §7 and left for the owner.

### Where the trusted policy is read from

`ci/check_workflow_integrity.py`, class `GitSource`:

```python
def read(self, rel: str) -> str | None:
    result = self._git("show", f"{self.ref}:{rel}")
```

Refs tried in order: `origin/main`, `refs/remotes/origin/main`, `main`. If none
resolves, the checker **fails closed in CI** (exit 1) and reports
`LOCAL_ENVIRONMENT_UNAVAILABLE` locally (exit 2). It never falls back to reading
the working tree, because grading the tree against itself is the bug.

### What it compares

`REMOVAL` — never acknowledgeable, blocks unconditionally:
`WORKFLOW_FILE_REMOVED`, `JOB_REMOVED`, `STEP_REMOVED`, `GATE_REMOVED`,
`LOCK_NAME_REMOVED`, `FLAG_REMOVED`, `GATE_UNWIRED`,
`REQUIRED_CONTEXT_JOB_MISSING`, `GUARD_FILE_REMOVED`, `GUARD_SYMBOL_REMOVED`,
`FAIL_CLOSED_BRANCH_REMOVED`, `PROTECTION_TEST_SENTINEL_MISSING`,
`ANCHOR_MISSING`, `GATES_FILE_REMOVED`.

`WEAKENING` — never acknowledgeable: `UNSAFE_TRIGGER`, `SECRET_ON_PR_PATH`,
`CONTINUE_ON_ERROR_ADDED`, `ACTION_UNPINNED`, `PERMISSION_WIDENED`,
`PROTECTION_TEST_SKIPPABLE`.

`CHANGE` — blocked unless the fingerprint is in `ci/workflow_changes.ack`:
`ACTION_SHA_CHANGED`, `STEP_RUN_CHANGED`, `STEP_ADDED`, `JOB_ADDED`,
`WORKFLOW_FILE_ADDED`, `WORKFLOW_HEADER_CHANGED`.

**Rule 7 note.** The checker only ever fails on a removal or a weakening; it
never asks for one. A renamed job is reported as `JOB_REMOVED` **plus**
`JOB_ADDED`, with the message: *"Renaming a job removes it: add the new name
alongside the old one."* If the design ever needs new names — for example
`trusted/pr-fast` — they are **added alongside**, both names stay, and the
ruleset must require both until the owner explicitly retires one in writing.

---

## 4. The 17 malicious-PR fixtures

The mandate said fifteen. Its own itemised list has **seventeen** entries.
Seventeen were built and seventeen were run. Nothing was dropped to reach a
number and nothing was padded. Each is applied to a **copy** in a temporary
directory; the repository is never modified.

**Control: an untouched copy → ACCEPTED.** Without that, every rejection below
would prove nothing.

| # | Injected | Verdict | Caught by |
|---|---|---|---|
| 1 | delete `security-scan` step (`pr-fast.yml`) | REJECTED | `STEP_REMOVED`, `FLAG_REMOVED` |
| 2 | delete `dependency-audit` step (`full.yml`) | REJECTED | `STEP_REMOVED`, `FLAG_REMOVED` |
| 3 | delete `full-mutation` step (`full.yml`) | REJECTED | `STEP_REMOVED`, `FLAG_REMOVED` |
| 4 | delete `changed-coverage` step (`pr-fast.yml`) | REJECTED | `STEP_REMOVED`, `GATE_UNWIRED`, `FLAG_REMOVED` |
| 5 | rename the required job `pr-fast` → `pr-fast-trusted` | REJECTED | `JOB_REMOVED`, `REQUIRED_CONTEXT_JOB_MISSING`, `JOB_ADDED` |
| 6 | rename the `security-scan` step | REJECTED | `STEP_REMOVED`, `STEP_ADDED` |
| 7 | delete a name from `ci/gate_names.lock` | REJECTED | `LOCK_NAME_REMOVED` |
| 8 | delete the step, the gate **and** the lock name in one commit (the H2 bypass) | REJECTED | `LOCK_NAME_REMOVED`, `GATE_REMOVED`, `STEP_REMOVED`, `FLAG_REMOVED` |
| 9 | `ruff format --check .` → `ruff format .` | REJECTED | `FLAG_REMOVED`, `GATE_UNWIRED`, `STEP_RUN_CHANGED` |
| 10 | re-add a module-level skip to `ci/test_protection.py` | REJECTED | `PROTECTION_TEST_SKIPPABLE` |
| 11 | `continue-on-error: true` on `security-scan` | REJECTED | `CONTINUE_ON_ERROR_ADDED`, `STEP_RUN_CHANGED` |
| 12 | rename the real job and add a do-nothing job called `pr-fast` | REJECTED | `STEP_REMOVED`, `JOB_ADDED`, `STEP_ADDED` |
| 13 | delete the `else:` catch-all from `ci/check_aggregate.py` | REJECTED | `FAIL_CLOSED_BRANCH_REMOVED` |
| 14 | change `actions/checkout`'s pinned SHA | REJECTED | `ACTION_SHA_CHANGED`, `STEP_RUN_CHANGED` |
| 15 | add `actions/setup-python@v5`, unpinned | REJECTED | `ACTION_UNPINNED`, `STEP_ADDED` |
| 16 | add `pull_request_target` to `on:` | REJECTED | `UNSAFE_TRIGGER` |
| 17 | expose `secrets.ANTHROPIC_API_KEY` to a `pull_request` job | REJECTED | `SECRET_ON_PR_PATH`, `STEP_RUN_CHANGED` |

**17 rejected · 0 accepted · 0 false green.**

Fixtures 15, 16 and 17 are also caught today by `ci/check_stubs.py` and
`zizmor --persona=pedantic`. Fixtures 1–14 were caught by nothing.

### The negative control — read this before reading 17/17 as safety

**Fixture 1 + delete `ci/check_workflow_integrity.py` + delete
`tests/test_gate_integrity.py` → NOT CAUGHT.**

The checker lives in the tree the fixture is editing. The step that would invoke
it is gone and the test that would invoke it is gone, so nothing runs and
nothing objects. Asserted explicitly in
`tests/test_gate_integrity.py::test_the_checker_cannot_survive_its_own_deletion`
and recorded in `gate_integrity_results.json` under `negative_control`, with
verdict **`BLOCKED_ON_OWNER_ACTION`**.

Had the checker run, it would have reported `ANCHOR_MISSING`,
`GUARD_FILE_REMOVED`, `STEP_REMOVED` and `FLAG_REMOVED`. The check is not weak.
The check is **optional**, and only GitHub can make it compulsory.

Two anchors instead of one raises the cost of the evasion from one deletion to
two, and forces the diff to name both. That is a cost increase, not a mechanism.

---

## 5. Restoring the two missing checks

### `uv lock --check` — gate 1 of 20, declared, blocking, never executed

Not a new gate. `ci/gates.toml:44-54` already declares it, `required = true`,
`failure_behaviour = "block"`, owner `tanveersidhu`. Wiring it up is making a
declared gate run.

- The workflow step is a `.github` change → **stated in §7, Diff A, not applied.**
- What **is** applied: the checker computes, for every gate, whether any single
  comment-stripped step body actually contains the command's executable and all
  of its flags. `lockfile` fails that test.
  `test_the_declared_but_unexecuted_gates_are_exactly_the_two_known_ones` pins
  the answer at `['cached-mutation', 'lockfile']`. A third unwired gate fails
  the suite; wiring one up also fails it, at which point the name is deleted
  from the list — the list may only shrink.
- Verified: applying Diff A removes `lockfile` from that warning and leaves only
  `cached-mutation`.

### `ci/test_protection.py` — no longer skips in CI

Applied, in full:

| Before | After |
|---|---|
| `pytestmark = pytest.mark.skipif(not reachable(), ...)` at `:70-73` | removed; replaced by an autouse fixture with three outcomes |
| skip on every hosted run | **`pytest.fail("PROTECTION_TEST_PREREQUISITES_MISSING")`** when `GITHUB_ACTIONS=true` or `CI=true` |
| skip silently on a laptop | `pytest.skip("LOCAL_ENVIRONMENT_UNAVAILABLE: <exactly what is missing>")` |
| `FORBIDDEN_STATUSES = (401, 403, 404)` | `FORBIDDEN_STATUSES = (403, 404)`; `UNAUTHENTICATED_STATUSES = (401,)` |
| a 401 satisfied `refused()` | a 401 calls `pytest.fail("PROTECTION_TEST_UNAUTHENTICATED")` |

All nine tests still exist. None was deleted, weakened or renamed. They run and
pass locally today against the live API (part of the 2322 passing).

> ### ⚠ APPLY THESE TWO TOGETHER
>
> `ci/test_protection.py` is collected by `pytest` — `pyproject.toml:39` sets
> `testpaths = ["tests", "ci"]`. With the fix in place and **no** token in CI,
> every job that runs pytest fails, and `pr-fast` goes red on every pull
> request. **That is the honest state** — a check that always skips is a green
> square that measured nothing — but it must not be landed alone.
> Land it with **Diff B**, or with a deliberate owner decision to accept red.

---

## 6. `CODEOWNERS` — which location, and why

**Recommendation: `.github/CODEOWNERS`.** Not the repository root.

GitHub: *"create a new file called CODEOWNERS in the `.github/`, root, or
`docs/` directory… GitHub will search for them in that order and use the first
one it finds."* `.github/` is the highest-precedence slot. Put the file at the
root and anyone can later add an empty `.github/CODEOWNERS` that silently
shadows it — a one-file, zero-noise way to switch code ownership off. Occupying
the top slot removes that move.

`.github/CODEOWNERS` is under `.github/**`, so it is **stated below, not
created**. A root `CODEOWNERS` is outside `.github/**` and I could have written
it without a yes — I did not, because using the weaker location to dodge the
rule would be choosing the worse design for procedural reasons.

**It is inert today regardless of location**, for two independent reasons:

1. `require_code_owner_review: false` and `required_approving_review_count: 0`.
   Both are owner-only ruleset settings.
2. GitHub: *"The people you choose as code owners must have write permissions
   for the repository."* Exactly one account has write permission —
   `Intellora-ai` — and it authors every pull request. GitHub does not allow a
   PR author to approve their own PR. Turning the settings on without adding a
   second human makes **every** pull request permanently unmergeable.

`@tanveersidhu` exists as a GitHub user but is **not** a collaborator, so naming
them today produces an unsatisfiable CODEOWNERS entry.

---

## 7. The `.github/**` patch — stated in full, NOT applied

Rule 6: editing `.github/**` needs a yes for that specific change, and one yes
is not a standing yes. The owner is unavailable, so this is Rule 11 satisfied in
advance: **every added, removed and changed line is below.** Nothing else must
appear in the diff (Rule 13).

Summary of every line that changes:

| File | + | − | Nature |
|---|---|---|---|
| `.github/CODEOWNERS` | 27 | 0 | new file |
| `.github/workflows/pr-fast.yml` (A) | 26 | 2 | 2 steps added; 1 comment corrected; 1 artifact path added |
| `.github/workflows/pr-fast.yml` (B) | 18 | 0 | 1 permission scope; 1 env var |
| `.github/workflows/full.yml` (A) | 12 | 1 | `fetch-depth: 0`; 1 step added; artifact path becomes a list |
| `.github/workflows/full.yml` (B) | 18 | 0 | 1 permission scope; 1 env var |
| `.github/workflows/watchdog.yml` (A) | 18 | 0 | 2 steps added |

**No job, step, gate, check or assertion is removed anywhere in this patch.**
The two `−` lines in Diff A are (a) a two-line comment in `pr-fast.yml` that
made a false claim about `--frozen`, replaced by a longer correct one, and (b)
`path: zizmor.json` in `full.yml` becoming `path: |` followed by two entries.

Validated before being written down: `ci/check_stubs.py --dir <patched>` →
`PASS - 4 workflow(s)`; `ci/check_workflow_integrity.py --tree <patched>` → 10
findings, **all CHANGE class, zero REMOVAL, zero WEAKENING**; the ten
fingerprints are pre-written (commented) in `ci/workflow_changes.ack`.
`actionlint` and `zizmor` were **not** run against the patched files — no
verdict is claimed for them.

### Diff A1 — `.github/CODEOWNERS` (new file)

```diff
--- /dev/null
+++ b/.github/CODEOWNERS
@@ -0,0 +1,27 @@
+# Who must review a change to the things that decide whether a gate passes.
+#
+# THIS FILE DOES NOTHING ON ITS OWN. It is inert until the repository ruleset
+# sets, on the `pull_request` rule:
+#
+#     require_code_owner_review       = true
+#     required_approving_review_count = 1        (or more)
+#
+# Both are owner-only settings. Read live 2026-08-10, ruleset 20557129, they are
+# false and 0.
+#
+# SECOND BLOCKER, AND IT IS THE BINDING ONE: GitHub requires a code owner to
+# have WRITE permission, and does not let a pull request author approve their
+# own pull request. This repository has exactly one collaborator,
+# @Intellora-ai, and that account authors every pull request. Turning the two
+# settings on before adding a second human makes every pull request
+# permanently unmergeable.
+#
+# Location: .github/ deliberately. GitHub searches .github/, then root, then
+# docs/, and uses the FIRST one it finds. Holding the top slot means an empty
+# .github/CODEOWNERS cannot later be added to shadow a root one.
+
+/.github/                       @Intellora-ai
+/ci/                            @Intellora-ai
+/scripts/guards                 @Intellora-ai
+/tests/test_gate_contract.py    @Intellora-ai
+/tests/test_gate_integrity.py   @Intellora-ai
```

### Diff A2 — `.github/workflows/pr-fast.yml`

```diff
--- a/.github/workflows/pr-fast.yml
+++ b/.github/workflows/pr-fast.yml
@@ -60,9 +60,22 @@
           cache-suffix: ${{ runner.os }}-${{ hashFiles('uv.lock') }}
           cache-dependency-glob: uv.lock
 
+      - name: lockfile
+        # Gate 1 of the 20 in ci/gates.toml: `uv lock --check`, required = true,
+        # failure_behaviour = "block". Until this step existed it ran NOWHERE in
+        # CI. The comment below claimed --frozen was the gate; it is not. Astral
+        # documents --frozen as SKIPPING the up-to-date check, and --locked as
+        # the flag that errors. The real check lived only in scripts/guards:119,
+        # whose own header says THIS IS AN ACCELERATOR, NOT A GATE, and which
+        # `git commit --no-verify` skips.
+        #
+        # This is not a new gate. It is the declared gate, running.
+        run: uv lock --check
+
       - name: sync dependencies from the lockfile
-        # --frozen fails if uv.lock does not match pyproject.toml, which is the
-        # `uv lock --check` gate.
+        # --frozen installs exactly what uv.lock says and nothing else. It does
+        # NOT verify the lock against pyproject.toml - the `lockfile` step above
+        # is what does that.
         run: uv sync --extra dev --frozen
 
       - name: lint
@@ -79,6 +92,18 @@
 
       - name: no-stub-jobs
         run: uv run python ci/check_stubs.py
+
+      - name: gate-integrity
+        # Compares this pull request's workflows, gate contract, gate-name lock
+        # and guard sources against `git show origin/main:<path>` - a policy the
+        # pull request cannot edit while it is being graded. Needs the full
+        # history that `fetch-depth: 0` on the checkout above already provides.
+        #
+        # What this closes: pr-fast.yml is `on: pull_request`, so GitHub grades a
+        # pull request with the copy of the grader that is IN the pull request.
+        # What it does not close: a pull request that deletes this step as well.
+        # That needs a GitHub-side rule - see artifacts/gate_integrity_audit.md.
+        run: uv run python ci/check_workflow_integrity.py --json gate-integrity.json
 
       - name: install actionlint
         # NOT the rhysd/actionlint action. It is a Docker action, so GitHub
@@ -200,6 +225,7 @@
             coverage.xml
             junit.xml
             reproducibility.txt
+            gate-integrity.json
           if-no-files-found: warn
           retention-days: 14
```

### Diff A3 — `.github/workflows/full.yml`

```diff
--- a/.github/workflows/full.yml
+++ b/.github/workflows/full.yml
@@ -154,6 +154,10 @@
       - name: checkout
         uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
         with:
+          # gate-integrity reads the policy from origin/main. A shallow
+          # single-branch checkout has no origin/main, and the checker fails
+          # closed rather than grading the tree against itself.
+          fetch-depth: 0
           persist-credentials: false
       - name: install uv
         uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9  # v9.0.0
@@ -173,12 +177,19 @@
         # The same scanner the local guards run. Belt and braces: if a workflow
         # is ever committed without running ./scripts/guards, this catches it.
         run: uv run python ci/check_stubs.py
+      - name: gate-integrity
+        # The merge-queue and nightly copy of the pr-fast step. merge_group runs
+        # the PR's content too, so this is the same comparison against the same
+        # trusted ref, not a duplicate for its own sake.
+        run: uv run python ci/check_workflow_integrity.py --json gate-integrity.json
       - name: evidence
         if: ${{ !cancelled() }}
         uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
         with:
           name: workflow-evidence
-          path: zizmor.json
+          path: |
+            zizmor.json
+            gate-integrity.json
           if-no-files-found: ignore
           retention-days: 30
```

### Diff A4 — `.github/workflows/watchdog.yml`

```diff
--- a/.github/workflows/watchdog.yml
+++ b/.github/workflows/watchdog.yml
@@ -118,6 +118,26 @@
           uv run --no-project python ci/check_ruleset.py \
             | tee -a "$GITHUB_STEP_SUMMARY"
 
+      - name: sync dependencies from the lockfile
+        # pytest is needed for the step below. The audit step above runs with
+        # --no-project and does not need it.
+        run: uv sync --extra dev --frozen
+
+      - name: live-protection-test
+        # ci/test_protection.py asserts that CLAUDE'S OWN identity cannot
+        # disable, delete or bypass the ruleset. That identity is a personal
+        # access token; it does not exist inside GitHub Actions, so no
+        # pull-request job can ever test it. This is a scheduled job on the
+        # default branch, so the secret is not reachable from pull-request code.
+        #
+        # OWNER ACTION REQUIRED: create the repository secret
+        # CLAUDE_AUDIT_TOKEN holding the same fine-grained token Claude uses
+        # (Administration = No access). Without it this step fails loudly, which
+        # is the intended behaviour - the test used to SKIP on every hosted run.
+        env:
+          GH_TOKEN: ${{ secrets.CLAUDE_AUDIT_TOKEN }}
+        run: uv run pytest ci/test_protection.py -q
+
       - name: evidence
         if: ${{ always() }}
         uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
```

### Diff B — the token that keeps the PR path green (**an owner decision**)

Identical hunks in `pr-fast.yml` and `full.yml`. Without it, the applied
`ci/test_protection.py` change reds every pull request (see the warning in §5).

**What it costs, stated:** pull-request code can then make read-only GitHub API
calls with the job token. **What it buys:** `ci/test_protection.py` runs in CI
and proves that `GITHUB_TOKEN` cannot weaken protection — a real assertion, and
a different one from the assertion about Claude's personal access token, which
Diff A4 covers. Every checkout still sets `persist-credentials: false`.

**Alternative, if the owner declines:** leave the PR path alone and accept that
`pr-fast` is red until `CLAUDE_AUDIT_TOKEN` and Diff A4 land, at which point the
nightly carries the assertion and the PR path still fails. There is no third
option that is not a skip in costume.

```diff
--- a/.github/workflows/pr-fast.yml     (and, identically, full.yml)
+++ b/.github/workflows/pr-fast.yml
@@ -24,6 +24,10 @@
 
 permissions:
   contents: read
+  # Read-only. `gh api repos/<repo>/rulesets` is a READ of branch protection,
+  # which ci/test_protection.py must do before it can assert that this token
+  # cannot CHANGE it. There is no write scope here.
+  administration: read
 
 concurrency:
@@ -37,6 +41,20 @@
   COVERAGE_CORE: pytrace
   FORCE_COLOR: "1"
+  # ci/test_protection.py is collected by `pytest` (pyproject testpaths =
+  # ["tests", "ci"]). Since 2026-08-10 it FAILS rather than skips when it
+  # cannot reach GitHub, so every job here that runs pytest needs a token.
+  #
+  # This is the JOB token, not a repository secret: scoped by the
+  # `permissions:` block above, expires with the run, and a DIFFERENT
+  # identity from the personal access token Claude uses. What it proves here
+  # is that GITHUB_TOKEN cannot weaken protection. The assertion about
+  # Claude's own token lives in watchdog.yml, off the pull-request path.
+  #
+  # COST, STATED: pull-request code can now make read-only GitHub API calls
+  # with this token. Every checkout in this file still sets
+  # persist-credentials: false, so nothing is written to .git/config.
+  GH_TOKEN: ${{ github.token }}
 
 jobs:
```

**Unverified, and I cannot verify it from here:** whether
`gh api repos/<repo>/rulesets` succeeds with a `GITHUB_TOKEN` holding
`administration: read` on this repository. If it does not, the step fails loudly
rather than passing — which is the correct direction of failure — and the fix is
either a wider read scope or dropping Diff B for the alternative above.

---

## 8. Historical audit

`git log --oneline -- .github/` and `-- ci/gate_names.lock` return four commits
in the entire history of the repository.

| Commit / PR | When | Workflows changed | Gate manifest changed | `security-scan` present after | Trusted checks available then | Manual review | Revalidation |
|---|---|---|---|---|---|---|---|
| `2be19e4` "Add the CI workflows: pr-fast, full, claude" | 2026-08-07 14:15 UTC | **all three created** | `ci/gates.toml` | yes | **none — the ruleset did not exist yet** | **none** | `HISTORICAL_GATE_UNVERIFIED` |
| PR #2 `7705463` | 2026-08-07 18:24 UTC | `pr-fast.yml` +29/−14, `full.yml` +24/−2 | `ci/gates.toml` +3/−3 | yes | `pr-fast` only | **0 reviews** | `HISTORICAL_GATE_UNVERIFIED` |
| PR #10 `ca502ea` | 2026-08-08 01:29 UTC | `pr-fast.yml` +219/−1, `full.yml` +68/−9, `watchdog.yml` created | **`ci/gate_names.lock` created**, `ci/gates.toml` +27/−22 | yes | `pr-fast`, `pr-full`, `ci-gate` | **0 reviews** | `HISTORICAL_GATE_UNVERIFIED` |
| PR #12 `4cc290f` | 2026-08-08 02:58 UTC | `pr-fast.yml` +15/−0 | `ci/gates.toml` +2/−2 | yes | `pr-fast`, `pr-full`, `ci-gate` | **0 reviews** | `HISTORICAL_GATE_UNVERIFIED` |

**`2be19e4` belongs to no pull request.** `gh api repos/.../commits/2be19e4/pulls`
returns `[]`, as it does for `b44b5d2` (initial commit) and `080c28a` (the gate
contract, the CI checkers and the local guards). Those commits were pushed
straight to `main`. The ruleset was created at **2026-08-07T19:55:54+05:30**
(14:25 UTC), **ten minutes after** `2be19e4` was authored. Every gate in this
repository was defined by commits that no gate could have examined.

**No pull request that has ever touched `.github/**` or `ci/gate_names.lock` was
reviewed by anybody.** All three have `reviews: []`.

Why every row says `HISTORICAL_GATE_UNVERIFIED`:

- PR #2's head reported **only** `pr-fast`; `ci-gate` did not exist as a check
  yet. Whether `ci-gate` was a required context on 2026-08-07 cannot be
  reconstructed — the ruleset exposes only the current state, and
  `GET /rulesets/{id}/rule-suites` returns `403 Resource not accessible by
  personal access token` for this identity.
- Every one of those runs was graded by the workflow the pull request itself
  supplied. A green square from a grader the graded party wrote is not evidence
  about the grader.

**History is not rewritten and no old gate is marked trusted because it looks
fine.**

### Revalidation of today's `main`, run now — reported separately

This is what the *current* gates say about `origin/main` = `f22eace`, as of
2026-08-10. It is a statement about today, not about what ran at the time.

| Check | Result |
|---|---|
| `pytest -q` (`COVERAGE_CORE=pytrace`), pristine `origin/main` | **2295 passed, 5 xfailed** |
| `pytest -q`, this branch | **2329 passed, 5 xfailed** (+34, none removed) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 163 files already formatted |
| `pyright` (strict, `accountant` + `tests` + `ci`) | 0 errors, 0 warnings |
| `ci/check_stubs.py` | PASS — 4 workflows, every action SHA-pinned |
| `tests/test_gate_contract.py` | 18 passed |
| `ci/check_ruleset.py` (live), before hardening | PASS — 9/9 |
| `ci/check_ruleset.py` (live), after hardening | PASS — **16/16**, plus 6 values printed as observed-not-asserted |
| `ci/test_protection.py` (live, Claude's PAT) | 9 passed — the ruleset could not be disabled, deleted, or bypassed, and did not change |
| `ci/check_workflow_integrity.py` vs `origin/main` | PASS, with the standing warning `['cached-mutation', 'lockfile']` |
| Gate count | 20 before, 20 after |

---

## 9. The five questions, answered twice

Column 3 is **conditional on the owner applying the patch in §7** and, where
marked, on the owner-only settings in §10. Nothing in column 3 has been applied.

| Question | **Today** | **After the patch — conditional** |
|---|---|---|
| Can a PR delete `security-scan` and still pass? | **YES.** All 18 contract tests, `check_stubs`, `ci-gate` and the nightly pass. Fixture 1. | **NO**, unless the same PR also deletes `ci/check_workflow_integrity.py` and `tests/test_gate_integrity.py`. In that case **YES** — see the negative control, §4. Making it an unconditional NO needs setting **R1** or **R2** in §10. |
| Can a PR edit `gate_names.lock` and redefine its required gates? | **YES.** `19 >= 19` passes. Fixtures 7 and 8. | **NO** for the PR under review — main's lock is the reference. **Still YES across a merge**: once merged, main's lock is the merged lock. |
| Can a PR make `ci/test_protection.py` skip? | It does not have to. **It already skips on every hosted run**, and has since it was written. | **NO.** The skip is gone, the CI path fails closed, and re-adding a module-level skip is `PROTECTION_TEST_SKIPPABLE`. Applied **today**, not conditional. Requires Diff B or an accepted red PR path. |
| Can a PR rename a required job to evade a check? | **YES**, and it can hang the required name on a stub job. Fixtures 5 and 12. | **NO**, same caveat as row 1. `REQUIRED_CONTEXT_JOB_MISSING` reads the required names from **main's** `ci/gates.toml`, so renaming what the contract says is required does not help. |
| Can a PR change workflow commands without protected review? | **YES.** `required_approving_review_count: 0`, `require_code_owner_review: false`, no CODEOWNERS, no `file_path_restriction`. | **YES — STILL.** The checker turns a silent change into a red check and a written statement. It cannot create a reviewer. `required_approving_review_count: 0` is an owner-only ruleset setting, and there is no second human with write access to be that reviewer. **BLOCKED_ON_OWNER_ACTION: R2 + R4.** |

Three "No"s, one "No with a named exception", one honest **Yes**.

---

## 10. Remaining human configuration, itemised

Every item is something Claude's identity is deliberately unable to do. Each has
the exact command or click, and an honest availability verdict.

**R1 — pin the grader to a trusted ref. This is the one that actually fixes C1.**
Add a `workflows` rule to ruleset 20557129. GitHub then runs the named workflow
**from the ref you specify**, not from the pull request.

```bash
gh api -X PUT repos/Intellora-ai/accountant-dad-core/rulesets/20557129 \
  --input - <<'JSON'
{ "rules": [ { "type": "workflows", "parameters": { "do_not_enforce_on_create": false,
    "workflows": [ { "path": ".github/workflows/pr-fast.yml",
                     "repository_id": 1326598701,
                     "ref": "refs/heads/main" } ] } } ] }
JSON
```
*(a real PUT must resend the existing `deletion`, `non_fast_forward`,
`pull_request` and `required_status_checks` rules alongside it — GitHub replaces
the whole rule array.)*
**Availability: UNVERIFIED.** `workflows` is in the documented rule-type enum for
`POST /repos/{owner}/{repo}/rulesets`. Whether GitHub accepts it on a
user-owned public repository is not something I can determine without writing to
repository settings, which this identity must not do. **One command settles it.**

**R2 — require a review.** Ruleset → `pull_request` rule →
`required_approving_review_count: 1`, `require_code_owner_review: true`,
and create `.github/CODEOWNERS` from Diff A1.
**Blocked by R4.** Turning this on with one collaborator makes every pull
request unmergeable.

**R3 — restrict file paths.** `file_path_restriction` on `.github/**` and `ci/**`.
**NOT AVAILABLE.** It is a **push** ruleset rule, and GitHub states *"You can
create a push ruleset for private or internal repositories."* This repository is
public. It also cannot be added to the existing `target: branch` ruleset. To use
it the owner would have to make the repository private — a product decision, not
a security one.

**R4 — add a second human with write access.** The binding constraint behind R2.
`gh api repos/.../collaborators` returns one row. Without a second person, "a
`.github` change needs a second human" cannot be true, whatever the settings say.

**R5 — create the repository secret `CLAUDE_AUDIT_TOKEN`.** Settings → Secrets
and variables → Actions. Value: the same fine-grained token Claude uses,
`Administration = No access`. Consumed by Diff A4. Without it that step fails
loudly, which is correct.

**R6 — apply the patch in §7.** Diff A is the repair. Diff B is a decision with a
stated cost. Then uncomment the ten fingerprints already written in
`ci/workflow_changes.ack`.

**R7 — organisation-level required workflow.** **NOT AVAILABLE.**
`owner.type == "User"`. There is no organisation. Superseded by R1 in any case.

**R8 — decide whether to store a ruleset fingerprint.** The ruleset changed
during this session (§2a) and nothing in the repository noticed until I read it
twice by hand. `ci/check_ruleset.py` now checks sixteen named properties instead
of nine, but anything outside those sixteen is still invisible. Committing a
baseline hash of the whole ruleset would make **every** change visible, at the
cost of a required commit whenever the owner legitimately changes protection.
Not done unilaterally — it is a choice about how much friction the owner wants.

**R9 — give a number for the review requirement, then let the audit defend it.**
`ci/check_ruleset.py` prints `required_approving_review_count` and
`require_code_owner_review` but does not assert them, because the owner has not
set them and a floor of zero defends nothing. The moment R2 lands, move those
two lines from `observe()` to `require()`. Until then, a silent revert of a
review requirement is invisible to the audit. Named in the file itself.

**R10 — read the app installations.** `repos/.../installations` returns **404**
to Claude's token, so the set of installed GitHub Apps and their permissions
cannot be audited from here. An app with `commit statuses: write` is exactly the
actor the M9 pin now excludes, so knowing which apps hold it matters. Either the
owner checks Settings → GitHub Apps by hand, or a token with Administration read
is provided to the scheduled watchdog only — never to the pull-request path.

---

## 11. Verdict

**`gate integrity = FAIL`.**

What is fixed, applied and running today:

- The self-grading loop is closed **for the pull request under review**. 17/17
  malicious fixtures rejected, 0 accepted, 0 false green, with a passing control.
- The lock file is tamper-evident against `origin/main`.
- `ci/test_protection.py` no longer skips in CI, and 401 is no longer proof.
- `uv lock --check` being unwired is now a fact a test asserts rather than a
  fact nobody knew.
- The enforcement runs inside the existing `changed-tests` and `full-tests`
  gates, so it takes effect with **no `.github` change at all**.

What is still missing, and why it is FAIL rather than PASS:

1. **A pull request that deletes the checker along with the gate is not caught
   by anything.** `BLOCKED_ON_OWNER_ACTION: R1`, or `R2 + R4`.
2. **A workflow command can still be changed with no protected review.**
   `required_approving_review_count: 0`. `BLOCKED_ON_OWNER_ACTION: R2 + R4`.
3. **`uv lock --check` still does not run.** The step is a `.github` change.
   `BLOCKED_ON_OWNER_ACTION: R6`.
4. **The live protection test still cannot test Claude's own token in CI.**
   `BLOCKED_ON_OWNER_ACTION: R5 + R6`.

The single highest-leverage action is **R1**, and it costs one API call to find
out whether it is available. If it works, the grader stops being editable by the
graded, and items 1 and 2 both close. Everything else in this document is a cost
increase on an attack that R1 removes.
