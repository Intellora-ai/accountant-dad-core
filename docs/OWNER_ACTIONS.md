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

## Do these three first

They cost the least and unstick the most.

### 1 · `B-01` — make `Demo Co` in TallyPrime · about 2 minutes

Create a company named exactly **`Demo Co`**, with four ledgers:

```
Purchases · Sundry Expenses · Cash · Sharma Traders
```

A company **cannot** be created over the XML gateway — it was tried and Tally
refused. This is a GUI action or it does not happen.

**What it unsticks:** the live acceptance run has somewhere to run. On its own it
is not enough — item 2 is the other half.

---

### 2 · `D-01` — the licence, and two instructions that contradict each other

**Two lines in the repository say opposite things.** Both are quoted here word
for word. Neither is being acted on.

| where | what it says |
|---|---|
| `PROJECT_STATE.md` §19 step 20 | *"OWNER: buy a non-Educational TallyPrime licence → unblocks 16 and 19"* |
| `PROJECT_STATE.md` §24, dated 2026-08-08, headed OWNER DECISION | *"Do not purchase, activate, bypass or simulate a non-Educational licence."* |

The second is later and is labelled an owner decision, so it **probably**
supersedes the first. **That is a guess and nobody is acting on it.**

| option | what follows |
|---|---|
| **A. Buy a licence** | roughly ₹885 per the earlier price check. The contract fixture can run. The Tally spine's exit closes. Live evidence becomes obtainable for the first time. |
| **B. Stay on Educational** | the Tally spine stays `BLOCKED_ENVIRONMENT` for good. No live evidence is ever obtainable. Everything downstream keeps reporting `BLOCKED_ENVIRONMENT`, correctly. |

**If you say nothing:** B happens by inaction. Nothing is bought and nothing is
bypassed.

**What is needed:** *"buy the licence"* or *"stay on Educational"* — plus which
of the two lines above to delete.

---

### 3 · `D-06` — when the memory and the live ledger disagree, who wins?

**This one has a real wrong-account risk and it is reproducible today.**

Bootstrap `Sharma Traders → Purchases` from 40 vouchers. Then post 60
`Sharma Traders → Repairs & Maintenance` by hand in Tally. The next entry
proposes `Purchases`, posts straight through, and raises **no flag and no
question**.

The cause is objective: the only detector on the production path never reads the
history it is given. The live ledger is passed in and thrown away, and nothing
ever re-reads it.

**The bug is a bug. The response is policy**, and that is the part only you can
set: re-read on what schedule, compare against what, flag or block, at what
threshold.

| option | consequence |
|---|---|
| the live ledger always wins, index rebuilt | safest; more reads |
| the index wins, staleness shown to the person | fastest; the person carries the risk |
| refuse and ask | most questions |

**If you say nothing:** the current behaviour stands, and it is the unsafe one.

---

## The rest of the open decisions

### About the product you already have

| id | question | if you say nothing |
|---|---|---|
| `D-02` | is the `2026-08-07` fixture date frozen? | it stays frozen. Silence keeps it. |
| `D-03` | is Tally.ERP 9 in scope, or TallyPrime only? | the criterion stands and stays unmet |
| `D-04` | may this project take its first runtime dependency — a web framework locally, or a signing library in the cloud? | stdlib only. Dependencies stay empty. |
| `D-05` | are `Ltd`, `Pvt Ltd`, `& Co` the same supplier as the bare name? | the current behaviour, which is the less safe one |
| `D-07` | may a person *declare* the Tally licence mode when the program cannot read it? | it stays `UNKNOWN`, and the screen says so |
| `D-10` | the five merge-queue policy numbers | the queue stays off, and nothing is worse for it |
| `D-22` | **does the product launch on the aggregate false-alarm rate, or on the worst single customer's book?** | undecided, and the launch question stays open |
| `D-23` | which input types must work at first launch? | typed text only, because it is the only one that exists |
| `D-24` | which Windows and Tally versions are supported at launch? | only what has been tested — one TallyPrime release, one Windows build |

**`D-05`, the supplier one, has a cheap measurement that settles it** and it can
be run today against your own Tally: count the party names that differ only by a
stripped suffix. Zero pairs means the current rule costs you nothing. One pair
means it is already merging two of your own suppliers into one ledger.

**`D-22` is worth reading twice.** The aggregate false-alarm rate passes at
6.29 against a target of 10. The worst single department fails at 33.33. Those
two numbers give opposite launch answers, and a customer does not experience an
aggregate — they experience their own book.

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
