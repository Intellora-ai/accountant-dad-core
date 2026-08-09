# DECISIONS — the register of what only the owner can settle

Created 2026-08-09. **Nothing in this file is decided by code, by a test, or by
an agent.** Each entry is a question that has a real consequence either way, and
where picking one side is a business or risk judgement rather than an
engineering one.

## How to use it

Answer in one line in this chat. The answer gets written into the **Owner
answer** row with the date, and the decision moves to `SETTLED`. Until then it
stays `OPEN` and everything that depends on it reports `OWNER_BLOCKED` — never
`PASSED`, never quietly defaulted.

**A decision is only in here if code cannot safely settle it.** Anything that is
objectively a defect — a wrong key, a missing gate, an unreachable branch — is
fixed, not asked about.

## Status vocabulary

```
OPEN                 the question is live and something is blocked on it
SETTLED              the owner answered; the date and the words are recorded
SUPERSEDED           a later decision replaced it; both are kept
NOT_YET_RELEVANT     real, but nothing is blocked on it today
```

---

## D-01 · The Tally licence — and two instructions that contradict each other

**Status: `OPEN`. This is the single largest blocker in the project.**

Two lines in `docs/PROJECT_STATE.md` say opposite things:

| Where | What it says |
|---|---|
| §19 step 20, line 1084 | `OWNER: buy a non-Educational TallyPrime licence → unblocks 16 and 19` |
| §24, line 1493 (dated 2026-08-08) | *"Do not purchase, activate, bypass or simulate a non-Educational licence."* |

§24 is the later of the two and is titled `OWNER DECISION, 2026-08-08`, so it
**probably** supersedes. **That is a guess and it is not being acted on.** One of
these two lines is stale and only the owner can say which.

**What it blocks.** The 15 client-fixture tests in `tests/test_tally_contract.py`
cannot run against a real Tally. Educational mode accepts vouchers dated only the
1st, 2nd and 31st; the fixture posts on `2026-08-07` and is refused. That is
measured, not assumed (`PROJECT_STATE.md` §24, the three-row table).

**Options:**

| | Consequence |
|---|---|
| **A. Buy a licence** | ₹885 per the earlier price check. The 15 tests can run. Phase 2's exit closes. Live evidence becomes obtainable. |
| **B. Stay on Educational** | Phase 2 stays `ENVIRONMENT_LIMITED` for good. No live evidence is ever obtainable. Everything downstream keeps reporting `BLOCKED_ENVIRONMENT`. |

**Default if unanswered:** B, by inaction. Nothing is bought and nothing is
bypassed.

**Exact answer needed:** "buy the licence" or "stay on Educational", plus which
of the two contradictory lines to delete.

---

## D-02 · The fixture date — frozen or changeable

**Status: `OPEN`, but with a strong recorded position.**

`tests/test_tally_contract.py:53` posts on `2026-08-07`. `ARCHITECTURE.md` §7
Phase 2 says the date is **part of the acceptance criteria, not an
implementation detail** — editing it to suit an environment changes what the
phase means, so it is an owner decision and never a repair.

**Nothing here has changed it and nothing will without a yes in words.**

**Options:** keep it frozen (the recorded position) · approve a specific new
date and record why.

**Default if unanswered:** frozen.

**Exact answer needed:** only if the owner wants it changed. Silence keeps it.

---

## D-03 · Is Tally.ERP 9 in scope

**Status: `OPEN`.**

Frozen criterion #6.8 requires reading on **both** TallyPrime and Tally.ERP 9,
or naming the unsupported version explicitly in the error. Only TallyPrime 7 has
ever answered. ERP 9 has never been tested and nobody has said whether it must
be.

**Options:** in scope, and the criterion stands · out of scope, and #6.8 is
narrowed to TallyPrime with the reason recorded.

**Default if unanswered:** the criterion stands and stays unmet.

**Exact answer needed:** "ERP 9 in scope" or "TallyPrime only".

---

## D-04 · The frontend's final shape

**Status: `OPEN`, deferred by the owner.**

Recorded as open item M-b: *"first figure our tally thing"*. Today the front door
is stdlib `http.server` rendering HTML on the server, no framework, zero runtime
dependencies.

**Options:** keep the stdlib app · approve a framework (which would be the first
runtime dependency the project has ever had).

**Default if unanswered:** the stdlib app. No framework is added.

---

## D-05 · Are `Ltd`, `Pvt Ltd`, `LLP` and `& Co` the same supplier

**Status: `OPEN`. This is a business rule, not a bug.**

`Acme Ltd` and `Acme LLP` are two different legal entities. They can have
different GST numbers, different bank accounts and different contracts. They can
also be the same shop that changed its registration last year.

Today the normalisation collapses some of these together. **Whether that is
right is not something code can decide** — it depends on how the owner's
customers actually name their suppliers.

`tests/test_memory.py:994-1001` cements the current behaviour, so changing it
means changing a test that was written on purpose.

**Options:**

| | Consequence |
|---|---|
| **A. Same supplier** | fewer questions asked, and an LLP invoice can post against Ltd-only history |
| **B. Different suppliers** | safer, and every suffix variant becomes a new unknown vendor that has to be answered for |

**Default if unanswered:** A, the current behaviour, which is the less safe one.
That is why it is in this register.

**Exact answer needed:** "same" or "separate".

---

## D-06 · May a stale memory index outvote the live ledger

**Status: `OPEN`.**

Recorded as defect D4. The scenario: the live ledger shows forty vouchers going
to one account, and our stored index says another. Today the index can win.

**Options:** the live ledger always wins and the index is rebuilt · the index
wins and staleness is surfaced to the person · refuse and ask.

**Default if unanswered:** the current behaviour, where the index can win.

**Exact answer needed:** which source wins when they disagree.

---

## D-07 · May a declared licence mode be trusted when the real one cannot be read

**Status: `OPEN`.**

The connector returns `licence_mode = UNKNOWN` **by design**. Every attempt to
read `$$LicenseInfo` over the gateway was refused, and the TDL workaround is what
wedged a live Tally — see `ARCHITECTURE.md` §15. So the screen can only say *"we
could not tell"*.

A person genuinely in Educational mode is therefore warned about the date
restriction but never told plainly that they are restricted.

**Options:** let the operator declare the mode and carry the declaration as
declared-not-measured · keep it `UNKNOWN` and say so.

**Default if unanswered:** `UNKNOWN`. The safe one, and the honest one.

---

## D-08 · When may cloud and multi-user work begin

**Status: `NOT_YET_RELEVANT` — the trigger has not fired.**

`ARCHITECTURE.md` §10 defers cloud hosting, multi-user and mobile until **"the
single-machine vertical slice works end to end"**. That is a documented trigger,
not a vague intention, and the checklist in §11 is what measures it.

**Nothing about cloud is being designed or built.** Tally has no cloud API and
listens on `localhost:9000` on the customer's own machine, so a hosted version
would be **two** programs — a website plus a small connector installed next to
Tally. `docs/EPIC.md:107` is the only line in the repository that says this, and
it is eleven words long.

**Exact answer needed:** none yet. Ask again when §11 is ticked.

---

## D-09 · A second mutation engine

**Status: `SETTLED`, recorded here for completeness.**

Owner decision G5: the engine is `pytest-gremlins`. mutmut, MutPy and Cosmic Ray
are forbidden. `ARCHITECTURE.md` §10 lists a second engine under "deliberately
outside this architecture".

**No question outstanding.**

---

## D-10 · The five merge-queue policy values

**Status: `OPEN`, and nothing is blocked on it.**

The merge queue is off. Turning it on needs five numbers the owner has never
given: `max_entries_to_build`, `min_entries_to_merge`, `max_entries_to_merge`,
`min_entries_to_merge_wait_minutes`, `check_response_timeout_minutes`.

**They have not been invented.** `merge_group` support stays in the workflow, so
enabling the queue later is configuration rather than redesign.

**Default if unanswered:** the queue stays off. `pull_request` already checks out
the merge commit, so the queue adds nothing this project needs today.

---

## D-11 · N, the acceptance batch size

**Status: `SETTLED` 2026-08-09.**

```
N = 10
```

Owner-set. Fixed for this gate, not configurable, never lowered to make a failing
run pass. Written into `ci/acceptance.py`, `ARCHITECTURE.md` §7 and
`PROJECT_STATE.md` §40 so it is findable by search.

---

## D-12 · The bulk-reversal partial-failure policy

**Status: `SETTLED` 2026-08-09.**

Fail-closed, resumable. The batch stops at the first unresolved voucher.
Vouchers already reversed are never re-reversed — reversing is **cleanup**, not a
rollback, so a batch that stops at voucher 4 correctly leaves 1–3 gone. Four
failure categories, and `UNKNOWN_OUTCOME` is never treated as a rejection.

Full state machine in `ARCHITECTURE.md` §4.14.

---

## D-13 · The phase-number collision

**Status: `SETTLED` 2026-08-09.**

An external planning message defined Phase 6 as the operational-readiness gate.
The repository already defined Phase 6 as the first detector. The owner ruled:
the repository's Phase 6 stands, and the readiness work becomes **Phase 5B**.

Nothing was renumbered. Phases 6 to 10 keep the numbers they have always had.

---

## Open at a glance

| # | Question | Blocks |
|---|---|---|
| D-01 | licence: buy, or stay on Educational | the 15 contract tests, all live evidence |
| D-02 | fixture date frozen | nothing while it stays frozen |
| D-03 | Tally.ERP 9 in scope | criterion #6.8 |
| D-04 | frontend shape | nothing today |
| D-05 | `Ltd` vs `LLP` — same supplier or not | a real wrong-vendor risk |
| D-06 | stale index vs live ledger | a real wrong-account risk |
| D-07 | declared licence mode | what the screen may say |
| D-10 | merge-queue numbers | nothing today |

**D-01, D-05 and D-06 are the three worth answering first.** The other five cost
nothing while they wait.
