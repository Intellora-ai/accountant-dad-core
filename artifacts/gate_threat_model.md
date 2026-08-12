# Gate threat model

**Repository** `Intellora-ai/accountant-dad-core` · public · owned by a **personal
user account**, not an organisation (`gh api repos/... --jq .owner.type` →
`User`, read 2026-08-10)
**Ruleset** `20557129` "main protection", `enforcement: active`
**Gate count** 20, unchanged (`ci/gate_names.lock`, 20 names)
**Measured** 2026-08-10, against `origin/main` = `f22eace`; re-verified against
`d98adc3` after a parallel workstream landed, with no change to any finding

---

## The one distinction that matters

**Mechanism** — enforced by code or by GitHub. It holds whether or not anyone is
paying attention, and it holds against someone who is trying.

**Policy** — written down and trusted. It holds while everyone co-operates. It
tells you what was intended; it does not stop anything.

A policy described as a mechanism is worse than no control at all, because the
green square is taken as evidence.

---

## 1. Who can change what, from where

| Actor | Identity | Can push to `main`? | Can open a PR? | Can approve? | Can merge? | Can change the ruleset? |
|---|---|---|---|---|---|---|
| Owner | `Intellora-ai` (admin, the **only** collaborator) | No — `pull_request` rule, `bypass_actors: []`, `current_user_can_bypass: "never"` | Yes | Cannot approve their own PR | Yes, once `pr-fast` + `ci-gate` are green | Yes (repo admin, via the web UI) |
| Claude | fine-grained PAT, `Administration = No access` | No | Yes | No | Yes, same two checks | **No** — verified live, 9 assertions in `ci/test_protection.py`, all passing locally 2026-08-10 |
| `GITHUB_TOKEN` in a job | ephemeral installation token, `permissions: contents: read` | No | n/a | n/a | n/a | No |
| Anyone on the internet | none | No | Yes (public repo, fork PR) | No | No | No |

**There is exactly one collaborator.** `gh api repos/.../collaborators` returns a
single row: `Intellora-ai  admin`. Every merged pull request (#1–#28) was
authored by `Intellora-ai`. This single fact is why three of the four candidate
fixes in this document are blocked.

### Where the code that grades a PR comes from

| Trigger | Workflow file GitHub uses | Editable by the PR? |
|---|---|---|
| `pull_request` | **the PR's branch** | **YES** |
| `merge_group` | **the merge-group ref** (PR content merged into main) | **YES** |
| `schedule` | the default branch | No |
| `workflow_dispatch` | the ref you dispatch | No, if you dispatch `main` |
| `issue_comment`, `pull_request_review_comment` | the default branch | No |

`pr-fast.yml:14-15` is `on: pull_request`. `full.yml:11-12` is `on: merge_group`.
Both authoritative paths therefore execute a grader supplied by the thing being
graded. `claude.yml` and `watchdog.yml` are the only two workflows a PR cannot
rewrite for its own run — and neither is a gate.

---

## 2. Every protection, classified

### Mechanism — holds against someone trying

| # | Protection | Where | Evidence |
|---|---|---|---|
| M1 | `main` cannot be pushed to directly | ruleset rule `pull_request` | live API |
| M2 | No force-push, no branch deletion | rules `non_fast_forward`, `deletion` | live API |
| M3 | Nobody can bypass, including admins | `bypass_actors: []`, `current_user_can_bypass: "never"` | live API |
| M4 | `pr-fast` and `ci-gate` must be green to merge | `required_status_checks`, contexts match published job names character-for-character | live API + `ci/gates.toml:16-17` |
| M4b | **Only GitHub Actions may report those two checks** | `integration_id: 15368` on both contexts, pinned by the owner 2026-08-10T06:51:46Z | live API; see §2a below |
| M5 | A stale branch cannot merge | `strict_required_status_checks_policy: true` | live API |
| M6 | Claude's token cannot touch protection | GitHub refuses; the **account** is admin (`.permissions.admin == true`) and the **token** withholds Administration. The separation is the control, and it is working | `ci/test_protection.py`, 9 live assertions; 403 on `branches/main/protection`, `actions/permissions`, `hooks`, `rule-suites`; 404 on `installations` |
| M7 | Missing, skipped or cancelled evidence blocks the merge | `ci/check_aggregate.py:101-141`, incl. an `else:` catch-all at 118-120 | code |
| M8 | A mutation score cannot be partial | `ci/check_mutation.py`, `FAIL_INCOMPLETE` with `score_percent: null` | code |
| M9 | Zero selected tests cannot be green | `pr-fast.yml:110-132`, re-runs the full suite and re-checks | code |
| M10 | Every action is pinned to a 40-char SHA (39/39) | `ci/check_stubs.py:64-82` + zizmor | code |
| M11 | No placeholder or echo-only job can be committed | `ci/check_stubs.py` | code |
| M12 | A gate in the contract must name a job that exists, and vice versa | `tests/test_gate_contract.py`, 18 tests | code |
| M13 | **NEW 2026-08-10** — no job, step, gate name, lock name, guard function, fail-closed branch or CLI flag that exists on `origin/main` may disappear in a PR | `ci/check_workflow_integrity.py`, invoked from `tests/test_gate_integrity.py` | 17/17 fixtures rejected |
| M14 | **NEW 2026-08-10** — `ci/test_protection.py` fails in CI instead of skipping, and 401 is no longer read as proof of refusal | `ci/test_protection.py:77-80, 131-153, 177-199` | code |
| M15 | **NEW 2026-08-10** — the drift audit now defends the pin itself, both required contexts, the ruleset's `conditions`, and every branch ruleset rather than only the first | `ci/check_ruleset.py`; 9 assertions -> 16 | 7 revert tests in `tests/test_gate_integrity.py` |

### Policy — written down, trusted, stops nothing

| # | Policy | Where | What it actually buys |
|---|---|---|---|
| P1 | "The number of gates may only go UP" | `ci/gate_names.lock:7-8` | A sentence. The commit that shortens the list shortens the check. Until M13, nothing enforced it. |
| P2 | Thresholds are owner-set and not adjustable | `ci/gates.toml:10-12` | Discipline. `tests/test_gate_contract.py:138-144` does mechanise *one* half of it — only 0 or 90 may appear. |
| P3 | "Never reuse mutation or security evidence for a different commit" | cache keys in both workflows | This one **is** mechanism: the commit SHA is in the key and there are no `restore-keys`. Listed here because the rule is policy and the implementation is mechanism. |
| P4 | Claude may not decide a failed gate passed | `claude.yml:3-7` | A comment. The mechanism underneath is that `claude.yml` is not a required check and Claude cannot bypass M4. |
| P5 | `scripts/guards` runs every gate locally | `scripts/guards` | Nothing. Its own header says **"THIS IS AN ACCELERATOR, NOT A GATE"** and `git commit --no-verify` skips it. |
| P6 | **NEW** — `.github` changes are declared in `ci/workflow_changes.ack` | that file | The change must be written down in one greppable place. The PR can write its own ack line. Policy, labelled as such in the file's own header. |

### Absent — named so it is not mistaken for present

| # | Not there | Consequence |
|---|---|---|
| A1 | No `CODEOWNERS` file anywhere (`.github/`, root, `docs/` — all checked) | Nothing marks `.github/**` or `ci/**` as needing a particular reviewer |
| A2 | `required_approving_review_count: 0` | A PR merges with zero human eyes |
| A3 | `require_code_owner_review: false` | A1 would be inert even if it existed |
| A4 | No `file_path_restriction` rule | Nothing stops a PR touching `.github/**` |
| A4b | No assertion on `pull_request` rule **parameters** | The owner has set no review requirement, so there is no number to defend. The day one is set, a silent revert is invisible unless `ci/check_ruleset.py` moves those two lines from `observe()` to `require()` |
| A4c | No stored fingerprint of the ruleset | Any change outside the sixteen named assertions is invisible. The ruleset changed during this session and only a manual double-read caught it |
| A4d | The installed GitHub Apps cannot be audited from here | `repos/.../installations` returns **404** to Claude's token. An app with `commit statuses: write` is the exact actor M4b excludes, and nobody in CI can enumerate them |
| A5 | No `workflows` ruleset rule | Nothing pins the grader to a trusted ref |
| A6 | No organisation | Organisation-level required workflows are unavailable |
| A7 | One collaborator | Even with A1–A3 fixed, nobody exists who could approve |

---

## 2a. The one-hour sequence on 2026-08-10 — read it as a sequence, not two rows

An unpinned required status check is satisfied by **whoever writes a status with
that exact string**. Any GitHub App installed on the repository holding
`commit statuses: write` can write `pr-fast: success` and satisfy the
requirement with no gate having run.

Until 06:51:46Z today, `pr-fast` was unpinned. `ci-gate` was already pinned.

| Time (UTC) | Event | Source |
|---|---|---|
| 06:11:49Z | PR #28 merges; `f22eace` becomes `main` | GitHub API, read by me |
| ~06:47Z | **CodeAnt AI installed with `commit statuses: write`** | **RELAYED.** I cannot verify it: `repos/.../installations` returns **404** to Claude's token, so neither the install time nor the permission set is readable from here |
| 06:51:46Z | Owner pins **both** required contexts to app `15368` | ruleset `updated_at`, read live by me |
| 07:17:21Z | `codeant-ai[bot]` first observed acting on the repository (`IssueCommentEvent`) | `repos/.../events`, read live by me |

**The exploit acquired a concrete actor roughly four minutes before the pin
closed it.** That is the sequence. Treating "an app was installed" and "the
required check was pinned" as unrelated housekeeping misses the only interesting
thing about either.

The pin is load-bearing rather than decorative, because this repository has more
than one app that reports checks. Measured on `f22eace`:

```
check-suites:  {"app": "github-actions", "id": 15368}   <- the pinned one
               {"app": "claude",         "id": 1236702} <- excluded by the pin
```

That also settles what `15368` is, without assuming: the check runs on `f22eace`
report `app.slug: "github-actions"` with `app.id: 15368`.

**Who can still unpin it:** the owner, from the web UI or with an admin token.
Not an app, not a CI job, not Claude — `bypass_actors` is `[]` and
`current_user_can_bypass` is `"never"`.

**And until today nothing would have noticed if they did.**
`ci/check_ruleset.py:111-122` asserted the required context *name* and never
read `integration_id`; an unpinned `pr-fast` reported a clean 9/9. Fixed in this
change (M15), with a test that unpins the canned ruleset and requires the audit
to fail.

---

## 3. The attack, in one paragraph

Open a pull request. In the same commit, delete the `security-scan` step from
`.github/workflows/pr-fast.yml`, delete the `security-scan` gate from
`ci/gates.toml`, and delete the `security-scan` line from
`ci/gate_names.lock`. All eighteen tests in `tests/test_gate_contract.py` pass:
the contract binds a gate to a **job** (`ci/gates.toml:186`,
`tests/test_gate_contract.py:195-203`), never to a step, and the lock-file guard
compares two files that you just edited together
(`tests/test_gate_contract.py:242-281`; `19 >= 19` holds). `ci/check_stubs.py`
passes. `zizmor` and `actionlint` pass. `pr-fast` runs **your** copy of the
workflow, so it never runs bandit. `ci-gate` sees `pr-fast: success` and
`pr-full: success`. Both required contexts are green. Nobody has to approve.
Merge.

Cost: one commit. Detection before this work: none.

---

## 4. Trust boundaries, drawn

```
   ┌─────────────────────────────────────────────────────────────┐
   │ GITHUB, owner-only         ruleset 20557129                 │
   │   required contexts · bypass_actors · review count          │
   │   Claude cannot reach this. Verified, 9 live assertions.    │
   └───────────────┬─────────────────────────────────────────────┘
                   │ requires the contexts "pr-fast" and "ci-gate"
                   │ to report success — by NAME, not by content
   ┌───────────────▼─────────────────────────────────────────────┐
   │ THE PULL REQUEST'S OWN TREE          <-- the whole problem  │
   │   .github/workflows/*.yml   the grader                      │
   │   ci/gates.toml             what a gate is                  │
   │   ci/gate_names.lock        how many gates there are        │
   │   ci/*.py, tests/*.py       every checker                   │
   │   ci/workflow_changes.ack   the acknowledgements            │
   │                                                             │
   │   Anyone who can open a PR can rewrite all of it.           │
   └───────────────┬─────────────────────────────────────────────┘
                   │ NEW: compared against
   ┌───────────────▼─────────────────────────────────────────────┐
   │ origin/main, read at runtime via `git show origin/main:...` │
   │   The PR cannot edit this while it is being graded.         │
   │   It CAN edit it by merging. And it can delete the step     │
   │   that performs the comparison.                             │
   └─────────────────────────────────────────────────────────────┘
```

The new layer narrows the hole. It does not close it. Closing it requires moving
the grader **above** the PR's tree, which only GitHub can do.

---

## 5. The four candidate platform fixes, with verdicts

| Option | Available here? | Evidence |
|---|---|---|
| `file_path_restriction` on `.github/**` and `ci/**` | **NO** | `file_path_restriction` is a **push** ruleset rule. GitHub: *"You can create a push ruleset for private or internal repositories."* This repository is **public** (`"private": false`). It also cannot be added to the existing `target: branch` ruleset. |
| `CODEOWNERS` + `require_code_owner_review` + `>= 1` approval | **PARTIALLY** — the settings exist; the people do not | GitHub: *"The people you choose as code owners must have write permissions."* One collaborator exists, and they author every PR. GitHub does not let an author approve their own PR, so this configuration makes every PR unmergeable until a **second human with write access** is added. |
| Organisation-level required workflow | **NO** | `owner.type == "User"`. There is no organisation. |
| Read the policy from `refs/heads/main` at runtime | **YES — built, applied, running** | `ci/check_workflow_integrity.py`. 17/17 fixtures rejected. Limits stated in §6. |

### A fifth option the brief did not list, and it is the best one

The repository-ruleset API accepts a rule of type **`workflows`** —
*"require workflows to pass before merging"* — whose parameters name a workflow
file **by repository and by ref**:

```json
{ "type": "workflows",
  "parameters": { "workflows": [
      { "path": ".github/workflows/pr-fast.yml",
        "repository_id": 1326598701,
        "ref": "refs/heads/main" } ] } }
```

GitHub runs the file **from that ref**, not from the pull request. That is the
mechanism that actually fixes C1: the grader stops being editable by the graded.
It appears in the documented rule-type enum for
`POST /repos/{owner}/{repo}/rulesets`. Whether GitHub accepts it for a
user-owned public repository is **UNVERIFIED** — confirming it is a write to
repository settings, which Claude's identity is deliberately unable to make and
must not attempt. The exact command for the owner is in
`artifacts/gate_integrity_audit.md`, §"Remaining human configuration".

---

## 6. What the new checker does not protect against

1. **A PR that deletes the checker.** The checker is invoked from two places
   inside the PR's own tree: a step in `pr-fast.yml` (stated, not yet applied)
   and `tests/test_gate_integrity.py` (applied, runs today inside the existing
   `changed-tests` and `full-tests` gates). Delete both and nothing objects.
   Tested as a negative control: `gate_integrity_results.json` →
   `negative_control`, verdict `BLOCKED_ON_OWNER_ACTION`. Two anchors instead of
   one raises the cost from one deletion to two, and makes the diff name them.
   That is a cost increase, not a mechanism.
2. **The merge.** Once a PR lands, `main` is the merged content, so the next
   PR's trusted policy already contains the weakening. This closes the loop for
   the pull request under review and for nothing after it.
3. **A change nobody looks at.** Every CHANGE-class finding can be acknowledged
   by a line the PR itself writes. It converts a silent change into a stated
   one. With `required_approving_review_count: 0`, no human is required to read
   the statement.
4. **The action a SHA points at.** The checker sees that a pin moved. It cannot
   see what the new commit does.
5. **`ci/test_protection.py` in a hosted job tests `GITHUB_TOKEN`, not Claude's
   personal access token.** That identity does not exist inside GitHub Actions.
   The assertion about Claude's token can only run where the token is: locally
   (where it runs and passes today) or in a scheduled job holding a secret
   (`watchdog.yml`, stated diff, needs `CLAUDE_AUDIT_TOKEN`).

---

## 7. Residual risk register

| Risk | Likelihood control | Impact if it happens | Status |
|---|---|---|---|
| A PR silently removes a gate | M13 catches it | A gate stops running and the badge stays green | **CLOSED for a PR that leaves the checker in place** |
| A PR removes the gate and the checker together | none in-repo | Same, undetected | **BLOCKED_ON_OWNER_ACTION** — needs the `workflows` ruleset rule, or CODEOWNERS + a second human |
| `.github/**` changed with no human reading it | none | Any of the above | **BLOCKED_ON_OWNER_ACTION** — `required_approving_review_count: 0` today |
| The nightly stops running | `watchdog.yml` `nightly-watchdog` | Advisories go unnoticed | Open, and honestly recorded in `watchdog.yml:17-20`: a watchdog on GitHub cannot catch GitHub switching itself off |
| Protection drifts | `watchdog.yml` `ruleset-drift` (read-only, never repairs) | Everything downstream becomes decoration | Detected next morning, not prevented |
| `uv lock --check` never runs | now recorded by a test that fails if a third gate goes unwired | A lockfile can drift from `pyproject.toml` and no CI job objects | **STATED DIFF, not applied** — fixing it is a `.github` change |
