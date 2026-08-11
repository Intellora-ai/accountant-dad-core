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
answer** row with the date, and the decision moves to `ANSWERED`. Until then it
stays `OPEN` and everything that depends on it reports
`OWNER_DECISION_REQUIRED` or `BLOCKED_ENVIRONMENT` — never `PASSED`, never
quietly defaulted.

**`ANSWERED` is not `IMPLEMENTED`.** An answer whose code does not exist yet is
`ANSWERED`. It becomes `IMPLEMENTED_AFTER_OWNER_DECISION` only when a **named
test** proves the code now does what the owner said. This distinction was
tightened on 2026-08-10, because "the owner said so" and "the program does so"
are two different facts and only one of them is measurable.

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
| `D-29` | this file, 2026-08-10, next free after `D-28` |

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

### It nearly happened a second time — 2026-08-10, the owner's six answers

Six owner answers arrived labelled `D-01`, `D-03`, `D-04`, `D-05`, `D-06` and
`D-22`. **Five of those six labels are right.** They were written straight onto
the id they name. **One collides, and both readings are recorded here rather
than one of them being quietly overwritten.**

| the label on the answer | the id that really asks it | what happened |
|---|---|---|
| `D-01` Tally licence | `D-01` | correct |
| **`D-03` reconciliation policy** | **`D-12`, plus the new `D-29`** | **collision — see below** |
| `D-04` first runtime dependency | `D-04` | correct |
| `D-05` supplier legal identity | `D-05` | correct |
| `D-06` live Tally vs stale memory | `D-06` | correct |
| `D-22` detector launch gate | `D-22` | correct |

**The collision, in plain words.** `D-03` in this repository asks whether
Tally.ERP 9 is in scope. It has nothing to do with reversals, it was not
touched, and it is still open.

The question the owner actually answered is *what a bulk reversal does when one
voucher's outcome cannot be named*. That is **`D-12`**, settled 2026-08-09.
`D-12` left exactly one half open — refuse the whole batch, or skip the unknown
voucher and finish the rest — and **that half never carried a `D-` number at
all**. It lived only as a comment in `accountant/reversal.py` calling itself
*"Defect D3, OPEN OWNER DECISION"*, and as a strict-`xfail` test.

So the half now has an id: **`D-29`**. `D-12` is not superseded; every word of
its 2026-08-09 answer still stands.

---

## D-01 · The Tally licence — and two instructions that contradict each other

**Status: `BLOCKED_ENVIRONMENT`, answered 2026-08-10. This is still the single
largest blocker in the project.**

> **Owner answer, 2026-08-10, word for word:**
> *"Use a legitimate non-Educational licence if you want real Tally validation.
> Until physically available, remain BLOCKED_ENVIRONMENT."*

**A stated preference is not a licence.** The owner has said which side he would
take *if* real Tally validation is wanted. He has not put a licence on the
machine. Nothing here buys one, and the standing instructions recorded in `D-26`
are unchanged.

So the two contradictory lines below turn out not to be a contradiction after
all. **Both stay. Neither is deleted.** One says what the owner would prefer.
The other says what may be done from here, which is nothing.

**Until a licence physically exists on the machine:**

| thing | state |
|---|---|
| RealTally validation | `BLOCKED_ENVIRONMENT` |
| the live validation run in phase 5 | `BLOCKED_ENVIRONMENT` |
| `B-02` | stays open |
| `LG-14`, `LG-18`, `LG-19` | stay `NOT_PASSED` |

**Four things stay forbidden, restated because this is the entry people reach
for.** Never change the frozen `2026-08-07` fixture. Never bypass Educational
mode. Never simulate a licence. Never claim RealTally evidence that a licensed
run did not produce.

**What this answer unblocks:** nothing today. **What it settles:** that nobody
has to keep guessing which of the two lines below is live.

---

### The two lines, kept for the record

Two lines in `docs/PROJECT_STATE.md` say opposite things:

| Where | What it says |
|---|---|
| §19 step 20, line 1084 | `OWNER: buy a non-Educational TallyPrime licence → unblocks 16 and 19` |
| §24, line 1493 (dated 2026-08-08) | *"Do not purchase, activate, bypass or simulate a non-Educational licence."* |

§24 is the later of the two and is titled `OWNER DECISION, 2026-08-08`. Before
2026-08-10 the guess was that it supersedes. **The 2026-08-10 answer above says
neither line is stale** — one is a preference, the other is the rule for today.

**What it blocks.** the client-fixture tests (count PENDING_COUNT — 19 by an AST count on 2026-08-10, the docs said 15) in `tests/test_tally_contract.py`
cannot run against a real Tally. Educational mode accepts vouchers dated only the
1st, 2nd and 31st; the fixture posts on `2026-08-07` and is refused. That is
measured, not assumed (`PROJECT_STATE.md` §24, the three-row table).

**Options:**

| | Consequence |
|---|---|
| **A. Buy a licence** | ₹885 per the earlier price check. The contract tests can run. The Tally spine's exit closes. Live evidence becomes obtainable. |
| **B. Stay on Educational** | The Tally spine stays `BLOCKED_ENVIRONMENT` for good. No live evidence is ever obtainable, and everything downstream keeps reporting it. |

**Recommended safe default, before the answer:** B, by inaction. Nothing is
bought and nothing is bypassed. **The answer did not change that.**

**Evidence.** Measured 2026-08-08 — `2026-08-07` REJECTED, `2026-08-31`
ACCEPTED, deletion works. Only the date is refused, so this is an environment
limit and not a defect in our connector.

**What would move it:** a licence, physically, on the machine. Nothing an agent
can do.

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

### A standing rule arrived on 2026-08-10 — the question is still open

> **Owner, 2026-08-10, word for word:**
> *"no new runtime dependency is approved automatically."*

The **question** — *which* dependency, if any — is still `OPEN`. What is now
fixed is **how** one may ever be added. **Eight things must be written down
before any dependency is added, not after:**

| # | what must be recorded |
|---|---|
| 1 | the exact dependency, by name and version |
| 2 | why it is needed, and what breaks without it |
| 3 | its licence |
| 4 | its security impact |
| 5 | its deployment impact |
| 6 | whether it violates the current policy, which is `dependencies = []` |
| 7 | the smallest alternative that was considered, and why it lost |
| 8 | the register entry recording all seven of the above |

**What may still proceed.** Cloud work on architecture, threat model, protocol,
test design, data flow and connector boundaries. That work **must not add the
dependency**, and **must not build an irreversible runtime around one**.

**What stays blocked:** option C of `D-16`, and any framework for the local
front door.

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

**Status: `ANSWERED` 2026-08-10 — separate. Not yet implemented.**

> **Owner answer, word for word:**
> *"Treat legal forms as meaningful by default. Do not silently merge Ltd, Pvt
> Ltd, LLP, Inc, Corp, or & Co. If identity is ambiguous, ask or hand over."*

**Two things are now separate, and must stay separate.**

| | what it is | may it change a supplier's identity? |
|---|---|---|
| technical normalisation | Unicode form, upper/lower case, spare spaces | **no** |
| business identity | which legal entity this actually is | that is the question |

Normalisation **must not destroy legal-form information**. When the identity is
genuinely unclear, the answer is a question to the person or a hand-over —
**never a silent merge**.

**What this unblocks:** the wrong-vendor risk now has a rule to build to.
**What stays blocked:** nothing; no other decision waits on it.

**Why it is `ANSWERED` and not `IMPLEMENTED`.** The code still strips the six
suffixes, and `tests/test_memory.py:1000-1007` still cements that. This becomes
`IMPLEMENTED_AFTER_OWNER_DECISION` only when a **named test** proves a bare name
and its `Ltd` / `Pvt Ltd` / `LLP` / `Inc` / `Corp` / `& Co` variants are
different suppliers, *and* that Unicode and whitespace normalisation still
happens.

### The record as it stood before the answer

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

**Recommended safe default, before the answer:** A, the current behaviour, which
is the less safe one. That is why it was in this register. **The owner chose B.**

---

## D-06 · May a stale memory index outvote the live ledger

**Status: `ANSWERED` 2026-08-10 — live Tally wins. Not yet implemented.**

> **Owner answer, word for word:**
> *"Live Tally wins over stale memory. If live Tally and memory disagree, make
> the entry UNCLEAR and ask instead of silently posting."*

**Required behaviour, three parts:**

- show the person the conflict
- record **both** sources
- never let stale memory silently override contradictory current Tally data

A disagreement is a **question**, not a posting.

**What this unblocks:** the wrong-account risk now has a rule.
**What stays blocked:** nothing else waits on it.

**Why it is `ANSWERED` and not `IMPLEMENTED`.** `vendor_switch` at
`accountant/detect/detectors.py:85` still names its history parameter
`_history` and still never reads it, so today the live ledger is passed in and
thrown away. This becomes `IMPLEMENTED_AFTER_OWNER_DECISION` only when a **named
test** proves the reproduction below now ends in an Unclear entry with a
question, showing both sources, instead of a silent post.

### The record as it stood before the answer

Recorded as defect D4. The scenario: the live ledger shows forty vouchers going
to one account, and our stored index says another. Today the index can win.

**Options:** the live ledger always wins and the index is rebuilt · the index
wins and staleness is surfaced to the person · refuse and ask.

**Recommended safe default, before the answer:** the current behaviour, where
the index can win. **The owner chose the first option and added the question.**

**Evidence.** Reproduced 2026-08-09 — bootstrap `Sharma Traders → Purchases`
from 40 vouchers, then post 60 `Sharma Traders → Repairs & Maintenance` by hand
in Tally. The next entry proposes `Purchases`, posts straight through, and
raises no flag and no question.

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

**Status: `IMPLEMENTED_AFTER_OWNER_DECISION`, settled 2026-08-09. Narrowed
2026-08-10 by `D-29` — not superseded.**

Fail-closed, resumable. The batch stops at the first unresolved voucher.
Vouchers already reversed are never re-reversed — reversing is **cleanup**, not a
rollback, so a batch that stops at voucher 4 correctly leaves 1–3 gone. Four
failure categories, and an unknown outcome is never treated as a rejection.

Full state machine in `ARCHITECTURE.md` §4.14.

**One half of this was left open and is now `D-29`.** `D-12` says the batch
stops. It never said what a **resume** may then do while one voucher's fate is
still unknown. That half is answered in `D-29` below: refuse the whole batch.

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

**Status: `ANSWERED` 2026-08-10 — both, and a failing department is not hidden.**

> **Owner answer, word for word:**
> *"Use both aggregate and worst-department results. For launch, do not hide a
> department that fails."*

**The gate that follows from that:**

| slice | rate per 100 clean entries | target | verdict |
|---|---|---|---|
| aggregate, all 7 departments | 6.29 | ≤ 10 | PASS |
| held-out half | 2.90 | ≤ 10 | PASS |
| **worst single department (DHSC)** | **33.33** | ≤ 10 | **NOT_PASSED** |
| **overall detector launch gate** | — | — | **`NOT_PASSED`** |

**The detector launch gate is `NOT_PASSED`.** It stays that way until one of two
things happens: the worst-department rule is satisfied, or that department is
taken out of scope by an **explicit owner scope decision**. Nothing else clears
it. That gate is `LG-20` in [`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml), and
`D-22` is now the reason recorded on it.

**Every detector report must now carry seven things:**

1. the aggregate
2. the held-out slice
3. the worst department
4. every department's own value
5. the denominator
6. the formula
7. false-alarm examples

**All seven departments, per 100 clean entries** — measured, from
[`artifacts/detector_evidence.md`](../artifacts/detector_evidence.md):

| department | clean entries | rate | verdict |
|---|---|---|---|
| MHCLG | 29 | 0.00 | PASS |
| DFT | 24 | 0.00 | PASS |
| HMT | 23 | 0.00 | PASS |
| DWP | 27 | 3.70 | PASS |
| DEFRA | 19 | 5.26 | PASS |
| **DHSC** | 21 | **33.33** | **NOT_PASSED** |
| DBT | 0 | not measured | not a pass either |

**A customer does not experience an aggregate. They experience their own book.**

Two more facts. The calibration half has **zero headroom** — one more false
alarm there flips it. And DBT has zero clean entries, so "not measured" is the
honest word and it is not a pass.

**Options that were on the table:** A, launch on the aggregate · B, launch only
when the worst book is inside the target · C, a named intermediate rule such as
"no book above 20 and the aggregate inside 10". **Recommended safe default
before the answer:** B. **The owner chose B, and added that both numbers are
always reported.**

**What this unblocks:** the launch rule itself. It can no longer be settled by
whichever number somebody quotes first.
**What stays blocked:** launch, until DHSC is inside 10 or is scoped out by a
named owner decision, and until every detector report carries the seven items
above.

**Why it is `ANSWERED` and not `IMPLEMENTED`.** No test yet refuses a detector
report that is missing one of the seven items.

---

## D-23 · Which input types must work at first launch

**Status: `ANSWERED 2026-08-11`.**

> D-23 (2026-08-11): First launch supports typed-text entry and uploaded
> documents (PDF/PNG/JPG) via Azure Document Intelligence. Azure backend is
> implemented; real-invoice verification is required before general
> availability.

**DOCX is not in that list, and its absence is the answer rather than an
oversight.** Frozen criterion S1 wanted five of five. Four ship.

**What "implemented" means, precisely, and what it does not.** The Azure backend
exists, is registered as `azure`, is the default, and refuses rather than guesses
when it is not configured. No request from this repository has ever reached
Azure. The parser was written from Azure's *documented* response shape and its
tests supply responses written by the same author, so a green suite proves those
two agree with each other — not that either agrees with Azure. The status is
`UNVERIFIED_VENDOR_SHAPE` and it can only be changed by running real invoices.

**So launch is beta / early access, not GA.** `docs/OWNER_WORK.md` carries the
verification plan that has to be finished first.

Before this answer: only typed text worked; `accountant/extract/adapter.py` was
a stub with no backend connected.

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

## D-29 · A resume, while one voucher's fate is unknown

**Status: `ANSWERED` 2026-08-10 — refuse the whole batch. Not yet implemented.**

**This is the half `D-12` left open**, and until 2026-08-10 it had no `D-`
number. It lived only as a comment in `accountant/reversal.py` calling itself
*"Defect D3, OPEN OWNER DECISION"*, and as a strict-`xfail` test. The owner sent
this answer under the label `D-03`; `D-03` is the Tally.ERP 9 question and was
**not** renumbered. See the header of this file.

> **Owner answer, word for word:**
> *"REFUSE THE WHOLE BATCH WHEN ANY VOUCHER HAS UNKNOWN_OUTCOME. Safety beats
> partial cleanup. Never delete six known vouchers while one voucher's fate is
> unknown."*

**Required behaviour, six parts:**

1. a resume is **refused** while any voucher is still unknown
2. **no further voucher is deleted**
3. every known result **stays recorded**
4. the operator **is shown** the unresolved voucher
5. a **separate reconciliation operation** is required
6. a resume is permitted **only once every voucher has a known verified state**

**Options that were on the table:**

| | Consequence |
|---|---|
| **A. Refuse the whole batch** | safest. Outstanding cleanup can never finish while one voucher's fate is unknown. |
| **B. Skip the unknown voucher and carry on** | cleanup finishes, and a batch continues past an unresolved unknown. |

**Recommended safe default:** none was invented. `accountant/reversal.py` says
in its own comment that both readings are defensible, that they are mutually
exclusive, and that the choice is an accounting-operations decision rather than
an engineering one. **The owner chose A.**

**Evidence — the defect this settles, measured.** `reconcile()` returns
`reconciled=True` on every path, including the one where every read raised.
`resume()` then opens, and after a reconciliation that settled nothing it
removed **six more vouchers** from a company holding one voucher whose fate was
unknown. Nine of ten gone. The strict-`xfail` test that pins it is
`tests/test_reversal_recovery.py::test_a_resume_writes_nothing_more_when_the_reconciliation_settled_nothing`.

**One existing test now encodes the losing option** and has to change:
`test_an_unknown_outcome_voucher_is_never_retried_by_a_resume` currently
requires a resume to proceed and skip. `D-12`'s own state machine already agrees
with the owner in spirit — the retryable list in `accountant/reversal.py`
deliberately excludes the unknown state.

**What this unblocks:** the reversal recovery path, which had two mutually
exclusive readings and now has one.
**What stays blocked:** nothing else waits behind it.

**Why it is `ANSWERED` and not `IMPLEMENTED`.** Another agent is changing
`accountant/reversal.py` now. This becomes
`IMPLEMENTED_AFTER_OWNER_DECISION` only when that strict `xfail` flips to a
passing test under its own name.

---

## Open at a glance

**16 open and 1 blocked by the environment, of 29.**

Answered on 2026-08-10: `D-05`, `D-06`, `D-22` and the new `D-29`. `D-01` was
answered too, but the answer does not unblock it — it is `BLOCKED_ENVIRONMENT`
until a licence physically exists.

| # | Question | Blocks | State |
|---|---|---|---|
| **D-01** | the licence | the contract tests, and all live evidence | `BLOCKED_ENVIRONMENT` |
| D-02 | fixture date frozen | nothing while it stays frozen | `OPEN` |
| D-03 | Tally.ERP 9 in scope | criterion #6.8 | `OPEN` |
| D-04 | the first runtime dependency, locally and in the cloud | option C of D-16 | `OPEN`, with a standing rule |
| D-07 | declared licence mode | what the screen may say | `OPEN` |
| D-08 | may cloud work begin at all | D-14 to D-21, all eight | `OPEN` |
| D-10 | merge-queue numbers | nothing today | `OPEN` |
| D-14 | what the cloud may hold | the whole data policy | `OPEN` |
| D-15 | retention and deletion | every retention cell | `OPEN` |
| D-16 | connector authentication | one named breach scenario | `OPEN` |
| D-17 | do backups exist | the backup column | `OPEN` |
| D-18 | the legal position | anything customer-facing | `OPEN` |
| D-19 | connector update policy | the version-support window | `OPEN` |
| D-20 | who may clear the emergency stop | the recovery path | `OPEN` |
| D-21 | confirm the launch caps | the write-lease reading | `OPEN` |
| D-23 | launch input types | whether extraction is on the critical path | `ANSWERED 2026-08-11` — typed text + PDF/PNG/JPG |
| D-24 | supported Windows and Tally versions | what may be claimed | `OPEN` |

**Answered but not yet built into the code — these are the ones with work
attached:**

| # | Answer | What must exist before it is `IMPLEMENTED` |
|---|---|---|
| D-05 | legal forms are meaningful; do not merge them | a named test proving the variants are separate suppliers |
| D-06 | live Tally beats stale memory; ask instead of posting | a named test proving the disagreement ends in a question |
| D-22 | both aggregate and worst department; hide nothing | a named test refusing a report missing one of the seven items |
| D-29 | refuse the whole batch while a voucher is unknown | the strict `xfail` flipping to a passing test |

**What is now the front of the queue.** `D-01` is not answerable by anyone here.
`D-05`, `D-06`, `D-22` and `D-29` are answered and are waiting on code, not on
the owner. The next owner question worth the owner's time is `D-08`, because
eight cloud decisions sit behind it — though it costs nothing while it waits,
since nothing cloud is being built.
