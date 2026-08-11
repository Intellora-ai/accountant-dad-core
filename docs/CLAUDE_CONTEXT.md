# CLAUDE_CONTEXT — the permanent operating rules

**What this file is.** The rules that do not change between sessions. Read it
first, every session, before touching anything.

**What this file is not.** It is not a status report. There is no phase table
here, no metric table, and no list of what is done. Those exist in exactly one
place and duplicating them is how this project ended up with documents that
disagreed with each other.

---

## 1. The authority map

Six documents, six jobs. Nothing does two jobs.

| Document | What it is allowed to say | What it must never say |
|---|---|---|
| **`docs/CONTROL_PLANE.yaml`** | **THE AUTHORITY.** Machine-readable current truth — phase status, metric values, decisions, blockers, launch gates, and the evidence for each. | nothing is off limits; this is the file everything else agrees with |
| **`docs/ARCHITECTURE.md`** | the product and design contract. How the system is built, what it forbids, what each phase's exit *means*. | any claim about how far the build has got |
| **`docs/DECISIONS.md`** | owner decisions only — id, date, the owner's own words, evidence. | a status for anything other than the decision itself |
| **`docs/PROJECT_STATE.md`** | human-readable status and the project's history, written to **agree with** the control plane. | anything that overrides the control plane |
| **`docs/OWNER_ACTIONS.md`** | the open owner actions, each linking a decision id. | an action nobody can act on, or one with no id |
| **`docs/BLOCKERS.md`** | the active blockers, each linking a blocker id. | a blocker with no id and no unblocking step |
| **`docs/CLAUDE_CONTEXT.md`** | this file. Permanent operating rules. | a second status table or a second metric table |

**The rule in one line:** if two documents disagree, the control plane wins, and
the disagreement gets written into
[`artifacts/document_contradictions.md`](../artifacts/document_contradictions.md)
rather than quietly edited away.

`scripts/validate_project_truth.py` enforces this. It reads the control plane,
scans the tracked documents, and fails when one of them contradicts it. Run it:

```bash
.venv/bin/python scripts/validate_project_truth.py
```

---

## 2. The status vocabulary, and nothing else

```
PASSED · NOT_PASSED · PARTIALLY_VERIFIED · BLOCKED_ENVIRONMENT ·
OWNER_DECISION_REQUIRED · NOT_STARTED
```

`COMPLETE`, `DONE`, `ENVIRONMENT-LIMITED`, `OWNER_BLOCKED`, `NOT_RUN` and
`SETTLED` are **not statuses this project has**. Older documents used them; they
map onto the six above and the mapping is recorded where it happened.

### What PASSED is not

A phase is **never** PASSED because:

- the code exists
- unit tests exist and are green
- FakeTally passes
- GitHub is green
- a document says so

PASSED means **the phase's own exit observable was seen**. Nothing else.

### The three evidence classes, never merged

| Class | Backend | Proves | May never be used to claim |
|---|---|---|---|
| implementation | `FakeTally` | our logic is right | anything at all about TallyPrime |
| compatibility | real TallyPrime, permitted date | the XML, transport and reversal mechanism work | that the unchanged contract fixture passes |
| live | real TallyPrime, contract fixture unchanged | the product works on real books | — |

This is enforced in code, not by whoever writes the report:
`ci/acceptance_cli.py` refuses the `LICENSED_REALTALLY` label while the licence
read returns `UNKNOWN`.

---

## 3. Things that are never done

Each of these has already cost this project something.

| Never | Because |
|---|---|
| edit the frozen `2026-08-07` fixture in `tests/test_tally_contract.py` | the date is part of the acceptance criteria, not an implementation detail. Editing it changes what the phase means. |
| tune a threshold to make a metric pass | that moves the measurement, not the product |
| let the gate count fall below 20 | `ci/gate_names.lock` locks the set. The standing rule is that the number may only go **up**. |
| edit anything under `.github/**` without an explicit owner yes | it is the enforcement layer; changing it is not an engineering decision |
| merge a pull request any way other than `scripts/merge-pr-with-codeant` | that script is the only place the review evidence is checked against the exact head being merged. See section 5. |
| send a custom TDL `REPORT`/`FORM`/`PART`/`LINE`/`FIELD` request, or `TYPE=Function`, to a Tally | one such request wedged a live TallyPrime gateway. TCP kept accepting connections and nothing was ever answered again. Recovery needed a human to restart the application. |
| probe a Tally nobody can restart | same reason. There is no remote recovery path. |
| purchase, activate, bypass or simulate a non-Educational Tally licence | standing owner instruction, 2026-08-08, decision `D-26` |
| answer an owner decision on the owner's behalf | a decision is in the register precisely because code cannot settle it |
| delete a claim that turned out to be wrong | a deleted contradiction is unauditable. Correct it and leave a dated note saying what it used to say. |
| run mutation testing without `COVERAGE_CORE=pytrace` | on the default `sysmon` core the test-to-line mapping is silently incomplete and the score under-reports badly |

### The two request families the connector may send. There is no third.

```
Export + Collection   the four reads    companies · ledgers · closing balances · vouchers
Import + Data         the two writes    voucher create · voucher delete
```

This is a **whitelist**, not a blacklist. A blacklist only forbids the harmful
shapes somebody already thought of, and the one that wedged a live Tally was on
nobody's list until it happened.

---

## 4. How to run things

```bash
# the full suite
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider

# every gate, locally, before pushing
./scripts/guards

# the project-truth check
.venv/bin/python scripts/validate_project_truth.py
```

**Agents working in a git worktree have no `.venv` of their own.** Call the main
one by its full path — `$PWD/.venv/bin/python` from the repository root — or
nothing runs at all.

---

## 5. How a pull request merges

**All merges must use `scripts/merge-pr-with-codeant`. Never call `gh pr merge`
directly. Never merge from the GitHub UI during this controlled-merger phase.**

```bash
scripts/merge-pr-with-codeant <PR_NUMBER>
scripts/merge-pr-with-codeant <PR_NUMBER> --dry-run
```

### What the script does that a raw merge does not

It reads the pull request's exact current head, fetches every CodeAnt review,
line comment and conversation comment, matches that evidence to **that exact
head**, prints every finding, writes an evidence file to
`.audit/merges/pr-<PR>-<HEAD>.json` **before** merging, re-reads the head
immediately before merging, and refuses if the head moved.

Four outcomes. Exactly one fires.

| Outcome | What it means | What happens |
|---|---|---|
| `REVIEWED`, no findings | a CodeAnt review whose `commit_id` **is** the head, and nothing flagged on it | merges |
| `REVIEWED`, findings | the same review, with findings on that head | refuses. Fix it, add a regression test or a structural guard, push, and let CodeAnt review the **new** head. Code changing is not resolution. |
| `STALE` | CodeAnt reviewed a different SHA | refuses. An older review is never current evidence, and there is no fallback to "the latest review". |
| `SKIPPED` / `ABSENT` | CodeAnt declined, or never ran | merges, and the evidence says in words that CodeAnt did not review this head |

A finding marked `FALSE_POSITIVE` or `ACCEPTED_RISK` prints its reason and needs
`--confirm-exceptions` on the command line before anything merges.

### The limitation. Do not oversell this.

```
MERGE_CONTROL_MODEL=single-controlled-merger
GITHUB_UI_MERGE_BLOCKING=NOT_ENABLED
DIRECT_GH_PR_MERGE_BYPASS=OUTSIDE_CONTROL
```

This is a process control over a single merging actor, not a guarantee GitHub
enforces. There is no required check behind it and no branch protection rule
naming CodeAnt. A different human or agent who runs the raw command, or who
clicks Merge in the web interface, bypasses all of it. That is why every
evidence file records `direct_github_merge_protection: false` — it is false
because it is false of reality.

The chokepoint works because this repository has exactly one actor that merges.
The day that stops being true, this stops working, and nothing will announce it.

---

## 6. Writing style, and it is not optional

The owner has ADHD and autism and has asked for this more than once.

- Plain words. Short sentences.
- Define every technical term the first time it appears, in the same breath.
- Tables over paragraphs. Numbers over adjectives.
- No adjective you cannot attach a number to. "Robust", "significant", "clean"
  — cut it or quantify it.
- Never write "this should work". Either it was run or it was not.
- Say the uncomfortable thing in one line, then move on. No softening, no
  moralising.

---

## 7. When you find something wrong

In this order:

1. **Write it down before fixing it.** Add a row to
   `artifacts/document_contradictions.md` — what it says now, what is true, the
   evidence, whether it is fixed, and whether the owner has to decide.
2. **Fix the control plane first.** It is the authority; everything else follows
   from it.
3. **Correct the secondary documents to agree**, each keeping a dated note of
   what it used to claim and why that changed.
4. **Run the validator.** If it passes, the documents agree. If it fails, they
   do not, and the failure names the line.
5. **If only the owner can settle it, do not settle it.** Add it to
   `docs/DECISIONS.md` with the next free id, add the action to
   `docs/OWNER_ACTIONS.md`, and leave it `OPEN`.

### Decision ids are never reused and never renumbered

They are referenced from other documents and from commit messages. Three
different sources have allocated `D-` numbers in this project and two of them
collided; the map that untangles it is in the header of
`docs/CONTROL_PLANE.yaml`. **Take the next free id. Check the control plane
first.**
