# DECISIONS — the register of what only the owner can settle

Created 2026-08-09. **Nothing in this file is decided by code, by a test, or by
an agent.** Each entry is a question that has a real consequence either way, and
where picking one side is a business or risk judgement rather than an
engineering one.

> **Authority, from 2026-08-10.** The machine-readable copy of every decision —
> id, status, options, the owner's own words, evidence and next action — lives in
> **[`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml)**. This file is the human
> write-up. If the two disagree, the control plane is right and this file is the
> bug. `scripts/validate_project_truth.py` fails the build if a `D-` id appears
> here that the control plane does not declare.
>
> The open actions, sorted by what to do first, are in
> [`OWNER_ACTIONS.md`](./OWNER_ACTIONS.md).

## How to use it

Answer in one line in this chat. The answer gets written into the **Owner
answer** row with the date, and the decision moves to
`IMPLEMENTED_AFTER_OWNER_DECISION`. Until then it stays `OPEN` and everything
that depends on it reports `OWNER_DECISION_REQUIRED` or `BLOCKED_ENVIRONMENT` —
never `PASSED`, never quietly defaulted.

**A decision is only in here if code cannot safely settle it.** Anything that is
objectively a defect — a wrong key, a missing gate, an unreachable branch — is
fixed, not asked about.

## Status vocabulary

```
OPEN                              the question is live and something waits on it
ANSWERED                          the owner answered; date and words recorded
WAITING_FOR_EVIDENCE              the answer needs a measurement first
BLOCKED_ENVIRONMENT               it cannot be answered from here
IMPLEMENTED_AFTER_OWNER_DECISION  answered, and the code now matches
SUPERSEDED                        a later decision replaced it; both are kept
```

> *Audit note, 2026-08-10: this list used to be `OPEN · SETTLED · SUPERSEDED ·
> NOT_YET_RELEVANT`. `SETTLED` maps to `IMPLEMENTED_AFTER_OWNER_DECISION`.
> `NOT_YET_RELEVANT` is gone — the one entry that carried it, `D-08`, turned out
> to be live after all.*

## Where the ids come from — read this before allocating one

**Three sources have allocated `D-` numbers, and two of them collided.**

| range | allocated by |
|---|---|
| `D-01` … `D-13` | this file, first |
| `D-14` … `D-21` | [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) §19 and [`DATA_POLICY.md`](./DATA_POLICY.md) |
| `D-22` … `D-28` | [`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) |

**Nothing has been renumbered and nothing ever will be.** These ids are linked
from other documents and from commit messages; a renumbered id is an unauditable
one. **Take the next free id, and check the control plane first.**

A planning instruction issued on 2026-08-10 used `D-01` to `D-11` for a
different list of questions. Those numbers were already taken. The full map of
what that instruction meant versus what each id actually is lives in
[`artifacts/document_contradictions.md`](../artifacts/document_contradictions.md),
row 24. The short version: **the launch-rule question is `D-22`, not `D-02`.
Cloud storage is `D-14`, not `D-07`. Retention is `D-15`, not `D-08`. The
runtime-dependency question is `D-04`, not `D-11`.**

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

**What it blocks.** the client-fixture tests (count PENDING_COUNT — 19 by an AST count on 2026-08-10, the docs said 15) in `tests/test_tally_contract.py`
cannot run against a real Tally. Educational mode accepts vouchers dated only the
1st, 2nd and 31st; the fixture posts on `2026-08-07` and is refused. That is
measured, not assumed (`PROJECT_STATE.md` §24, the three-row table).

**Options:**

| | Consequence |
|---|---|
| **A. Buy a licence** | ₹885 per the earlier price check. The contract tests can run. The Tally spine's exit closes. Live evidence becomes obtainable. |
| **B. Stay on Educational** | The Tally spine stays `BLOCKED_ENVIRONMENT` for good. No live evidence is ever obtainable, and everything downstream keeps reporting it. |

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

## D-04 · The first runtime dependency — the frontend, and now the cloud too

**Status: `OPEN`, deferred by the owner.**

Recorded as open item M-b: *"first figure our tally thing"*. Today the front door
is stdlib `http.server` rendering HTML on the server, no framework, and
`pyproject.toml` still reads `dependencies = []` — verified 2026-08-10.

**Options:** keep the stdlib app · approve a framework, which would be the first
runtime dependency the project has ever had.

**Default if unanswered:** the stdlib app. No framework is added.

> **Widened 2026-08-10.** This is now the same question on two fronts.
> [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) §5 records that option C for
> connector authentication — Ed25519 message signatures — needs the
> `cryptography` package, and calls it *"D-04 territory"*. So answering this one
> also unblocks option C of `D-16`.
>
> A planning instruction called the cloud runtime-dependency question `D-11`.
> `D-11` is `N = 10` and is settled. It is this entry.

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

**Status: `OPEN`. Reopened 2026-08-10.**

`ARCHITECTURE.md` §10 defers cloud hosting, multi-user and mobile until **"the
single-machine vertical slice works end to end"**. That is a documented trigger,
not a vague intention, and the checklist in §11 is what measures it. **It is not
ticked.**

Tally has no cloud API and listens on port 9000 on the customer's own machine,
so a hosted version would be **two** programs — a website plus a small connector
installed next to Tally.

> **Audit note, 2026-08-10 — this entry was wrong.** It read
> `NOT_YET_RELEVANT`, on the grounds that *"nothing about cloud is being designed
> or built"*. Four cloud documents were created on 2026-08-10 —
> [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md),
> [`CLOUD_THREAT_MODEL.md`](./CLOUD_THREAT_MODEL.md),
> [`CONNECTOR_PROTOCOL.md`](./CONNECTOR_PROTOCOL.md) and
> [`DATA_POLICY.md`](./DATA_POLICY.md) — and eight owner decisions, `D-14` to
> `D-21`, now sit behind this one. Cloud IS being designed. The gate is live and
> the entry is `OPEN`.
>
> `CLOUD_ARCHITECTURE.md` §19 calls this decision **"the gate"** and states
> plainly that the gate is not met. Nothing in those documents is being built.

**Exact answer needed:** either confirm the trigger stands — in which case
`D-14` to `D-21` all wait behind it — or name what may proceed early, and why.

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

## D-14 to D-21 · the cloud decisions

**These eight are written up in [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md)
§19 and [`DATA_POLICY.md`](./DATA_POLICY.md), which own the wording.** They are
listed here so the register is complete, and summarised in
[`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml). **All eight sit behind `D-08`.**

| # | Question | Default if unanswered |
|---|---|---|
| D-14 | what accounting content may the cloud hold at all | relay only — the cloud holds none of it, and the customer can only work while their PC is on |
| D-15 | retention and deletion periods, and what deletion means when a backup exists | **none. There is no safe default and none was invented.** |
| D-16 | connector authentication — a shared secret, or a key only the connector holds | the shared secret, which leaves the stolen-database risk open |
| D-17 | do cloud backups exist — where, encrypted how, kept how long, restored by whom | **none.** A backup nobody decided on is the worst of both worlds. |
| D-18 | the legal position — data residency, who owns the audit log, breach notification | **none. This needs a lawyer, not a document.** |
| D-19 | connector updates — automatic or operator-approved, and the version-support window | operator-approved, one version back |
| D-20 | who may **clear** the emergency write stop, and what they must see first | anyone may set it; clearing needs zero unresolved operations |
| D-21 | confirm the reading of the launch caps | the write-lease reading in the design |

---

## D-22 · Does launch use the aggregate false-alarm rate, or the worst book?

**Status: `OPEN`. This is the launch question and it has two opposite answers.**

| slice | rate per 100 clean entries | target | verdict |
|---|---|---|---|
| aggregate, all 7 departments | 6.29 | ≤ 10 | PASS |
| held-out half | 2.90 | ≤ 10 | PASS |
| **worst single department (DHSC)** | **33.33** | ≤ 10 | **FAIL** |

**A customer does not experience an aggregate. They experience their own book.**

Two more facts worth knowing before answering. The calibration half has **zero
headroom** — one more false alarm there flips it. And one department (DBT) has
zero clean entries, so it reports "not measured", which is not a pass either.

**Options:** A, launch on the aggregate · B, launch only when the worst book is
inside the target · C, a named intermediate rule such as "no book above 20 and
the aggregate inside 10", written down with its reason.

**Recommendation, not a decision:** B.

**Default if unanswered:** none. The question stays open rather than being
answered by whichever number gets quoted first.

---

## D-23 · Which input types must work at first launch

**Status: `OPEN`.**

Frozen criterion S1 wants five of five — typed text, PDF, PNG, JPG, DOCX. Today
only typed text works; `accountant/extract/adapter.py` is a stub with no backend
connected.

`EPIC.md` also records that bill extraction is now a vendor feature with a free
price floor, and argues against entering that market at all. So "all five" is a
real cost with a recorded argument against it.

**Default if unanswered:** typed text only, because it is the only one that
exists.

**Why it matters:** if the answer is typed text only, the extraction phase leaves
the critical path entirely.

---

## D-24 · Which Windows and Tally versions are supported at launch

**Status: `OPEN`.**

Everything measured so far was measured against **one** configuration:
TallyPrime Release 7.0, Series A Release 7.0.0, Build 27974, in Educational mode,
inside a UTM Windows-on-ARM guest.

**Options:** support only what has been tested · publish a wider list with the
untested entries labelled untested.

**Default if unanswered:** only what has been tested. Anything wider is a claim
with no measurement behind it.

**Answer this together with `D-03`.** This is *not* `D-19` — `D-19` is about our
own connector's protocol versions, which is a different thing.

---

## D-25 to D-28 · answered, recorded so nobody asks twice

| # | Question | Owner answer | Date |
|---|---|---|---|
| D-25 | how many concerns may the review screen show at once | **3.** Display only — every concern is kept in evidence and the screen says how many it hid. | 2026-08-10 |
| D-26 | does the project run under Tally's Educational mode for now | **Option 2, the Educational-mode exception.** Do not purchase, activate, bypass or simulate a licence. Do not edit the fixture. Do not convert an environment limit into a pass. | 2026-08-08 |
| D-27 | the cached-mutation gate | **Parked.** It stays in the contract and stays counted; it is deliberately not executed, and the gate count does not fall. | 2026-08-08 |
| D-28 | may Claude merge a pull request | **Yes, when the gates are green** — and never deciding *whether* they passed. GitHub's required checks are the authority. | 2026-08-08 |

`D-26` answers "what do we do today". It does **not** resolve `D-01`, which asks
which of two contradictory licence instructions is the live one. Both entries are
kept and cross-linked rather than merged, because merging them would hide the
contradiction.

---

## Open at a glance

**19 open, of 28.**

| # | Question | Blocks |
|---|---|---|
| **D-01** | licence: buy, or stay on Educational | the contract tests, and all live evidence |
| D-02 | fixture date frozen | nothing while it stays frozen |
| D-03 | Tally.ERP 9 in scope | criterion #6.8 |
| D-04 | the first runtime dependency, locally and in the cloud | option C of D-16 |
| **D-05** | `Ltd` vs `LLP` — same supplier or not | a real wrong-vendor risk |
| **D-06** | stale index vs live ledger | a real wrong-account risk |
| D-07 | declared licence mode | what the screen may say |
| D-08 | may cloud work begin at all | D-14 to D-21, all eight |
| D-10 | merge-queue numbers | nothing today |
| D-14 | what the cloud may hold | the whole data policy |
| D-15 | retention and deletion | every retention cell |
| D-16 | connector authentication | one named breach scenario |
| D-17 | do backups exist | the backup column |
| D-18 | the legal position | anything customer-facing |
| D-19 | connector update policy | the version-support window |
| D-20 | who may clear the emergency stop | the recovery path |
| D-21 | confirm the launch caps | the write-lease reading |
| **D-22** | aggregate or worst book | **the launch rule itself** |
| D-23 | launch input types | whether extraction is on the critical path |
| D-24 | supported Windows and Tally versions | what may be claimed |

**Four are worth answering first: `D-01`, `D-05`, `D-06` and `D-22`.**

- `D-01` unsticks four phases at once.
- `D-05` and `D-06` are each a live wrong-posting risk in code that already runs.
- `D-22` decides whether the product is launchable at all, and nobody can build
  their way past it.

Everything else costs nothing while it waits. The eight cloud decisions cost
nothing because nothing cloud is being built.
