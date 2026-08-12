# OWNER_ACTIONS — the things only you can do

**Authority.** Every `D-` id here is declared in
[`docs/CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) and written up in
[`docs/DECISIONS.md`](./DECISIONS.md). Every `B-` id is in
[`docs/BLOCKERS.md`](./BLOCKERS.md).

**Nothing on this list is a task an agent can take.** Each one is either a
purchase, a click in an application, a business judgement, or a legal position.
Code cannot settle any of them, and none has been guessed at.

**How to answer.** One sentence per item, in chat. The answer gets written into
the decision with today's date and your own words as the evidence. Until then
the item stays `OPEN` and everything depending on it reports
`OWNER_DECISION_REQUIRED` or `BLOCKED_ENVIRONMENT` — never a quiet default.

---

## What you answered on 2026-08-10

Five of these are now off your list. One is not, and the reason is worth
reading.

| id | your answer, in short | where it stands now |
|---|---|---|
| `D-01` | use a real licence if you want real Tally validation; until one physically exists, stay blocked | **still blocked.** A stated preference is not a licence. Nothing was bought, bypassed or simulated. |
| `D-29` | refuse the whole batch when any voucher's outcome is unknown | answered; the code is being changed now |
| `D-04` | no new runtime dependency is approved automatically | **the question is still open** — what is fixed is how one may ever be added |
| `D-05` | legal forms are meaningful; do not silently merge them | answered; the code still merges them and has to change |
| `D-06` | live Tally beats stale memory; ask instead of posting silently | answered; the code still ignores the live ledger and has to change |
| `D-22` | use both the aggregate and the worst department; hide neither | answered; the detector launch gate is `NOT_PASSED` for a decided reason |

**You labelled the reversal answer `D-03`.** In this repository `D-03` is a
different question — is Tally.ERP 9 supported. Nothing was renumbered. Your
answer went onto `D-12` (the reversal policy) and the half it left open is now
`D-29`. Both are recorded and cross-linked in
[`DECISIONS.md`](./DECISIONS.md).

**Two of these still need something physical from you.** `D-01` needs a licence
on the machine, and it is the only thing that moves it. `D-04` needs one named
dependency, with eight things written down first, or a "stdlib only, final".

---

## The top of the list

**Two of the three below are now answered and need nothing more from you.** They
are kept here because they are the ones everything else refers to. After the
2026-08-10 answers, only **two things** are still yours to do here: make
`Demo Co`, and put a real licence on the machine.

### 1 · `B-01` — make `Demo Co` in TallyPrime · about 2 minutes

Create a company named exactly **`Demo Co`**, with four ledgers:

```
Purchases · Sundry Expenses · Cash · Sharma Traders
```

A company **cannot** be created over the XML gateway — it was tried and Tally
refused. This is a GUI action or it does not happen.

**Settled 2026-08-12, so nobody asks again.** This is a permanent scope
boundary, not a gap waiting to be closed. Tally's XML gateway imports and
exports into a company that is *already loaded*; creating one is an
administrative flow in the Tally window and is not on the documented integration
surface. The same split applies to every future customer: a person creates and
opens the company, the software does the rest. Full reasoning in
`RUNBOOK_PHASE5_ACCEPTANCE.md` §A.0.1. **No XML workaround will be attempted** —
the last search for one wedged a live gateway behind a dialog box nobody could
close.

**One thing to add while you are in there:** switch the HTTP gateway on — F1 →
Settings → Advanced Configuration → **HTTP Server: Yes**, port **9000**. The
checklist used to ask only that the port be *known*, which is not the same as it
being open.

**What it unsticks:** the live acceptance run has somewhere to run. On its own it
is not enough — item 2 is the other half.

**What is NOT waiting on this:** the integration pattern already ran against a
real licensed TallyPrime on 2026-08-12 — your `TANVEER SIDHU` company, ledgers
created over XML, a Purchase posted and read back (`PROJECT_STATE.md` §47).
That is **not** an acceptance pass and is not written down as one: it touched
four of the fifteen conditions, one of those four failed, and condition 14
(`trial_balance_restored`) is failing in those books right now because of the
duplicate ₹1,000 voucher. Condition by condition in
`RUNBOOK_PHASE5_ACCEPTANCE.md` PART J.

---

### 2 · `D-01` — the licence · answered 2026-08-10, and still blocked

> **Your words:** *"Use a legitimate non-Educational licence if you want real
> Tally validation. Until physically available, remain BLOCKED_ENVIRONMENT."*

**A stated preference is not a licence.** Nothing here bought one, and nothing
bypassed or simulated one. Until a licence physically exists on the machine:

- RealTally validation is `BLOCKED_ENVIRONMENT`
- the live validation run is `BLOCKED_ENVIRONMENT`
- `B-02` stays open, and `LG-14`, `LG-18` and `LG-19` stay `NOT_PASSED`
- the frozen `2026-08-07` fixture is never changed to make anything pass

**The one thing that moves this: a licence, on the machine.** Roughly ₹885 per
the earlier price check.

Your answer also settled something that had been guessed at for two days — the
two lines below are **not** a contradiction. One is a preference, the other is
the rule for today. Both stay.

**The two lines, kept for the record.** Both are quoted word for word.

| where | what it says |
|---|---|
| `PROJECT_STATE.md` §19 step 20 | *"OWNER: buy a non-Educational TallyPrime licence → unblocks 16 and 19"* |
| `PROJECT_STATE.md` §24, dated 2026-08-08, headed OWNER DECISION | *"Do not purchase, activate, bypass or simulate a non-Educational licence."* |

| option | what follows |
|---|---|
| **A. A real licence** | roughly ₹885. The contract fixture can run. Live evidence becomes obtainable for the first time. |
| **B. Stay on Educational** | no live evidence is ever obtainable, and everything downstream keeps saying so, correctly. |

**Where it sits now:** you chose A *as the preference*, and B *as the reality
until a licence exists*. Nothing needs saying again. Something needs buying.

---

### 3 · `D-06` — memory versus the live ledger · answered 2026-08-10

> **Your words:** *"Live Tally wins over stale memory. If live Tally and memory
> disagree, make the entry UNCLEAR and ask instead of silently posting."*

**Nothing more is needed from you on this one.** It is recorded, and the code
has to change to match. Three things the code must now do: show the conflict,
record both sources, and never let stale memory quietly override what Tally says
today.

**The risk it closes, and it is reproducible today.** Bootstrap
`Sharma Traders → Purchases` from 40 vouchers. Then post 60
`Sharma Traders → Repairs & Maintenance` by hand in Tally. The next entry
proposes `Purchases`, posts straight through, and raises **no flag and no
question**. The cause is objective: the only detector on the production path
never reads the history it is given.

---

### And one more, answered the same day · `D-29` — bulk reversal

> **Your words:** *"REFUSE THE WHOLE BATCH WHEN ANY VOUCHER HAS UNKNOWN_OUTCOME.
> Safety beats partial cleanup. Never delete six known vouchers while one
> voucher's fate is unknown."*

**Nothing more is needed from you.** Measured before the answer: after a
reconciliation where every read failed, a resume removed six more vouchers from
a company holding one voucher whose fate was unknown. Nine of ten gone. That is
the exact thing your answer forbids.

---

## The rest of the open decisions

### About the product you already have

| id | question | if you say nothing |
|---|---|---|
| `D-02` | is the `2026-08-07` fixture date frozen? | it stays frozen. Silence keeps it. |
| `D-03` | is Tally.ERP 9 in scope, or TallyPrime only? | the criterion stands and stays unmet |
| `D-04` | may this project take its first runtime dependency — a web framework locally, or a signing library in the cloud? | stdlib only. Dependencies stay empty. |
| `D-07` | may a person *declare* the Tally licence mode when the program cannot read it? | it stays `UNKNOWN`, and the screen says so |
| `D-10` | the five merge-queue policy numbers | the queue stays off, and nothing is worse for it |
| `D-23` | which input types must work at first launch? | typed text only, because it is the only one that exists |
| `D-24` | which Windows and Tally versions are supported at launch? | only what has been tested — one TallyPrime release, one Windows build |

**`D-04` is half-answered.** You said no new runtime dependency is approved
automatically, and that rule is now recorded. What is still open is whether
there is ever to be **one named dependency**. Before any is added, eight things
must be written down first: the exact dependency · why it is needed · its
licence · its security impact · its deployment impact · whether it breaks the
current `dependencies = []` policy · the smallest alternative and why it lost ·
the register entry holding all seven. Cloud design work may continue on
architecture, threat model, protocol, test design, data flow and connector
boundaries — as long as it adds no dependency and builds nothing irreversible
around one.

---

### The detector launch gate · `D-22`, answered 2026-08-10

> **Your words:** *"Use both aggregate and worst-department results. For launch,
> do not hide a department that fails."*

**What follows from that, in one table:**

| slice | rate per 100 clean entries | target | verdict |
|---|---|---|---|
| aggregate, all 7 departments | 6.29 | ≤ 10 | PASS |
| worst single department (DHSC) | 33.33 | ≤ 10 | `NOT_PASSED` |
| **the detector launch gate overall** | — | — | **`NOT_PASSED`** |

**The gate clears one of two ways.** DHSC comes inside 10, or you take that
department out of scope with a named scope decision. Nothing else clears it, and
no aggregate number clears it.

Every detector report must now carry seven things: the aggregate · the held-out
slice · the worst department · every department's own value · the denominator ·
the formula · false-alarm examples. Full evidence is in
[`artifacts/detector_evidence.md`](../artifacts/detector_evidence.md).

---

### About the cloud, if there is ever a cloud

These belong to [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) and
[`DATA_POLICY.md`](./DATA_POLICY.md). **All of them sit behind `D-08`**, which is
the gate saying cloud work may not begin until the single-machine slice works end
to end. It does not yet.

| id | question | if you say nothing |
|---|---|---|
| `D-08` | **the gate.** May cloud and multi-user work begin at all? | it waits for the trigger |
| `D-14` | what accounting content may the cloud hold — none, or typed text queued while the PC is off? | none. The customer can only work while their PC is on. |
| `D-15` | retention and deletion periods, and what deletion means when a backup exists | **no safe default exists and none was invented** |
| `D-16` | connector authentication — shared secret, or a key only the connector holds? | the shared secret, which leaves a stolen-database risk open |
| `D-17` | do cloud backups exist — where, encrypted how, kept how long, restorable by whom? | **none.** A backup nobody decided on is the worst of both worlds. |
| `D-18` | the legal position — data residency, who owns the audit log, breach notification | **none. This needs a lawyer, not an engineer.** |
| `D-19` | connector updates — automatic, or the operator approves each one? | operator-approved, one version back |
| `D-20` | who may **clear** the emergency write stop, and what must they see first? | anyone may set it; clearing needs zero unresolved operations |
| `D-21` | confirm the launch caps, and are they enforced in code or advisory? | the write-lease reading in the design |

**`D-15`, `D-17` and `D-18` have no default at all.** That is deliberate. There
is no safe guess for a retention period, a backup policy or a legal position, so
none was made.

---

## Owner actions that are not decisions

| what | why only you | id |
|---|---|---|
| restart TallyPrime — its gateway may still be wedged from 2026-08-09 | there is no remote path. Close it, open it, reopen the company. | `B-04` |
| add `ANTHROPIC_API_KEY` and install the Claude GitHub App, or say leave it off | it is your account | `B-05` |
| approve one edit under `.github/**` | it is the enforcement layer, so changing it is not an engineering call | `B-06` |
| create an external scheduler account and a token scoped to dispatch plus read-runs | nothing inside GitHub can watch GitHub drop its own schedule | `B-07` |

---

## What has already been answered

Kept here so nobody asks twice.

| id | your answer | when |
|---|---|---|
| `D-09` | `pytest-gremlins` is the mutation engine. mutmut, MutPy and Cosmic Ray are forbidden. | recorded as G5 |
| `D-11` | `N = 10`, the acceptance batch size. Fixed, not configurable, never lowered to make a failing run pass. | 2026-08-09 |
| `D-12` | bulk reversal is fail-closed and resumable. It stops at the first voucher it cannot resolve, and already-reversed vouchers are never put back — reversing is cleanup, not a rollback. | 2026-08-09 |
| `D-13` | the repository's own phase numbering stands. The readiness gate becomes 5B and nothing is renumbered. | 2026-08-09 |
| `D-25` | the review screen shows at most three concerns at once. Every concern is still kept in evidence and the screen says how many it hid. | 2026-08-10 |
| `D-26` | Educational mode, Option 2. Do not purchase, activate, bypass or simulate a licence. Do not edit the fixture. | 2026-08-08 |
| `D-27` | the cached-mutation gate is parked. It stays in the contract and stays counted; it is deliberately not executed. | 2026-08-08 |
| `D-28` | Claude merges when the gates are green — and never decides *whether* they passed. | 2026-08-08 |
| `D-05` | legal forms are meaningful. Do not silently merge `Ltd`, `Pvt Ltd`, `LLP`, `Inc`, `Corp` or `& Co`. If identity is ambiguous, ask or hand over. | 2026-08-10 |
| `D-06` | live Tally wins over stale memory. On a disagreement, make the entry Unclear and ask. | 2026-08-10 |
| `D-22` | use both the aggregate and the worst department. Do not hide a department that fails. | 2026-08-10 |
| `D-29` | refuse the whole batch when any voucher's outcome is unknown. Safety beats partial cleanup. | 2026-08-10 |

**Four of those are answered but not yet built.** `D-05`, `D-06`, `D-22` and
`D-29` are waiting on code, not on you. Each becomes "done" only when a named
test proves the program does what you said — the owner saying it and the program
doing it are two different facts, and only one of them can be measured.

---

## A correction, recorded rather than quietly fixed

A planning instruction issued in this project listed these owner questions under
the ids `D-01` to `D-11`. **Those numbers were already taken by different, real
decisions**, and using them would have overwritten records that other documents
and commit messages already link to.

Nothing was renumbered. The map from that instruction's labels to the ids
actually used is in the header of
[`CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml) and in
[`artifacts/document_contradictions.md`](../artifacts/document_contradictions.md).

The short version: **the aggregate-versus-worst-department launch rule is `D-22`,
not `D-02`. Cloud data storage is `D-14`, not `D-07`. Retention is `D-15`, not
`D-08`. The runtime-dependency question is `D-04`, not `D-11`.**

**It nearly happened again on 2026-08-10.** Six answers arrived labelled `D-01`,
`D-03`, `D-04`, `D-05`, `D-06` and `D-22`. Five of the six labels were right and
went straight onto the id they name. The sixth, `D-03`, is the Tally.ERP 9
question — not the reversal question. The reversal answer went onto `D-12`, and
the half `D-12` had left open became the new `D-29`. **Nothing was renumbered
and both readings are written down.**
