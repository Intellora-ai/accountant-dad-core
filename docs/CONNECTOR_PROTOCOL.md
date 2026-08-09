# CONNECTOR PROTOCOL — Accountant Dad

**Written 2026-08-10. This protocol is not implemented. No code exists.**

The wire contract between the cloud and the local Windows connector. Read
[`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) first — this file is the
detail of the arrow between the two boxes in §2 of that document.

---

## 0. The five rules this protocol exists to enforce

Everything below is a consequence of these. If a message shape ever conflicts
with one of them, the rule wins.

```
1  the connector dials OUT. The cloud never dials in. There is no listening
   port on the customer's machine.

2  no message is replayable.

3  a write is NEVER retried. An unknown outcome is settled by a READ.

4  a retry, when a person approves one, uses the SAME operation id — never a
   new one.

5  the connector may refuse the cloud. Company mismatch, no write lease, or a
   local stop flag beats a perfectly signed instruction.
```

---

## 1. Terms

| Word | Meaning |
|---|---|
| **envelope** | the outer part of every message: version, ids, sequence, type |
| **body** | the inside of the message. Its shape depends on the type. |
| **MAC** | Message Authentication Code — a tag proving the bytes were not altered and came from the key holder |
| **canonical bytes** | the one and only byte string that can represent a given message |
| **session** | one connected period. Starts when the connector connects, dies when it disconnects. |
| **sequence** | a counter inside a session: 1, 2, 3, … with no gaps and no reuse |
| **long-poll** | the connector asks "any work?" and the cloud holds the question open until there is some |
| **operation** | one intended statutory write. Has an id. Exists before anything is sent. |
| **journal** | the connector's own durable list of operations and where each one got to |
| **terminal state** | a state nothing moves out of |
| **lease** | the exclusive right to write to one company. Exactly one connector holds it. |

---

## 2. The envelope

Every message, in both directions, has the same outer shape.

```json
{
  "v":         1,
  "msg_id":    "m_<32 hex>",
  "session":   "s_<32 hex>",
  "seq":       17,
  "sent_at":   "2026-08-10T09:14:02.481Z",
  "connector": "cx_<32 hex>",
  "tenant":    "t_<32 hex>",
  "type":      "operation.dispatch",
  "body":      { }
}
```

| Field | Meaning | Rules |
|---|---|---|
| `v` | protocol version | integer. Negotiated once per session (§6). |
| `msg_id` | unique per message | never reused. Used for logging and for de-duplicating a message the receiver already handled. |
| `session` | the session this belongs to | minted by the **cloud**, not the connector |
| `seq` | position in the session | strictly `previous + 1`. A gap tears the session down. |
| `sent_at` | when the sender says it sent it | **advisory only. Never used to accept or reject.** Clocks on customer machines are not ours. |
| `connector` | which connector | must match the key that signed it |
| `tenant` | which customer | must match the connector's registration |
| `type` | the message kind | §4 lists all of them. An unknown type is refused, never ignored. |
| `body` | the payload | shape depends on `type` |

### 2.1 Canonical bytes, and what the MAC covers

```
canonical = json.dumps(envelope,
                       sort_keys=True,
                       ensure_ascii=True,
                       separators=(",", ":")).encode("ascii")
```

Keys sorted, ASCII only, no spaces. These are the same serialisation rules
`accountant/generate/serialise.py` already uses, so the project has one
canonicalisation rule rather than two.

```
mac_input = session_nonce || canonical
```

`session_nonce` is 32 random bytes the **cloud** minted when the session opened
(§3.2). It is never transmitted inside a message body — both sides already hold
it. Including it in the signed input is what makes a message from an old session
unusable in a new one, and it is the load-bearing part of the replay defence.

The MAC travels **outside** the JSON, as an HTTP header, so it is not part of
the bytes it covers:

```
X-AD-MAC: <hex>
```

**Why a detached MAC.** A MAC inside the object it signs is a circular
definition, and every implementation solves it differently — which is exactly
how canonicalisation bugs are born.

### 2.2 Verification order — and why the order matters

```
1  parse the header. Reject if absent.
2  read the raw body bytes EXACTLY as received. Do not re-serialise.
3  recompute the MAC. Reject on mismatch, in constant time.
4  ONLY NOW parse the JSON.
5  check v, tenant, connector, session, seq.
6  dispatch on type.
```

**Nothing is parsed before it is authenticated.** A parser is an attack surface,
and the whole point of a MAC is to keep unauthenticated bytes away from it. This
is the same reasoning already recorded in `ARCHITECTURE.md` §9, where CI reads
one value with a regex rather than an XML parser.

### 2.3 What the MAC key is

Established at pairing, per connector. Symmetric HMAC-SHA256 or an asymmetric
signature — this is **owner decision D-16**, and §5.1 of
[`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) states what each costs. The
protocol shape is identical either way; only the header value changes.

---

## 3. Handshakes

### 3.1 Pairing — once per connector, ever

```
CONNECTOR                                CLOUD
─────────                                ─────
                          the user, in the browser, has already been shown a
                          short code. The cloud holds a PAIRING TICKET:
                              ticket_id, code, secret, expiry +15 min,
                              bound to (tenant, company), single use

pair.begin  ────────────────────────────►
  body: { code, protocol_version,
          tally_endpoint,
          companies_tally_reports: [...],
          machine_fingerprint,
          connector_public_key }        ← under D-16 option B/C.
                                           Under option A the cloud returns
                                           a key instead, which is the
                                           weaker branch.

                          CHECKS, all of which must pass:
                            ticket exists, unexpired, unused
                            the ticket's company is IN companies_tally_reports
                            the tenant is under its connector cap
                          any failure → pair.refused, naming the reason,
                          and the ticket is burned anyway

◄──────────────────────────  pair.complete
  body: { connector_id, cloud_cert_fingerprint,
          protocol_version_agreed, lease_granted: true|false }

connector writes its id and key to Windows protected storage
the ticket is dead
```

**The code is typed at the keyboard of the machine being paired.** Not emailed,
not a clickable link. A link can be forwarded; typing proves somebody is sitting
at the machine.

**Pairing is not signed**, because there is no key yet. It is protected by TLS
with a pinned certificate, by the 15-minute single-use ticket, and by the
requirement that Tally on that machine actually reports the company on the
ticket. That last check is the strongest of the three: it means a stolen code
cannot pair a machine that has different books on it.

### 3.2 Session — every time the connector connects

```
CONNECTOR                                CLOUD
─────────                                ─────
session.begin  ─────────────────────────►
  UNSIGNED — there is no session_nonce yet.
  body: { connector_id, protocol_version,
          connector_build,
          journal_unresolved: [op ids...],
          journal_results_unreported: [ {op_id, state, detail}... ] }

                          CHECKS:
                            connector known and not revoked
                            protocol_version supported (§6)
                            tenant not suspended
                          mints: session id, session_nonce (32 random bytes)

◄─────────────────────────  session.accepted
  body: { session, session_nonce, v,
          lease: held|standby,
          server_time,
          stop_engaged: true|false,
          reconciliation_required: true|false }

FROM HERE ON, every message in both directions is signed and carries seq,
starting at 1.
```

**`session.begin` is the only unsigned message in the protocol**, and it can be,
because it grants nothing: the cloud's reply establishes state but dispatches no
work, and everything the connector declared in it is re-verified before any
operation moves. A replayed `session.begin` produces a *new* session with a
*new* nonce, which is useless to an attacker.

**Sequence resets to 1 at every session.** The nonce is what makes that safe.

---

## 4. The message catalogue

`C→S` = connector to cloud. `S→C` = cloud to connector, carried as the answer to
a held long-poll.

| Type | Dir | Body | Retryable? |
|---|---|---|---|
| `pair.begin` | C→S | see §3.1 | yes — nothing has been granted |
| `pair.complete` / `pair.refused` | S→C | see §3.1 | — |
| `session.begin` | C→S | see §3.2 | yes |
| `session.accepted` / `session.refused` | S→C | see §3.2 / §6 | — |
| `work.poll` | C→S | `{ capacity }` — how many operations it can take | **yes, always.** It is a question. |
| `work.none` | S→C | `{}` — the long-poll expired | — |
| `operation.dispatch` | S→C | `{ operation_id, kind, company, voucher, expected }` | **NEVER, once `SENDING` is journalled.** See §7. |
| `operation.accepted` | C→S | `{ operation_id }` | yes — it is a statement about the journal |
| `operation.result` | C→S | `{ operation_id, state, tally_id, moved, measured, detail }` | **yes.** Idempotent: the cloud keys on `operation_id`. |
| `read.request` | S→C | `{ operation_id, company }` | **yes, always. A read is the safe direction.** |
| `read.result` | C→S | `{ operation_id, found, tally_id, voucher }` | yes |
| `stop.engage` | S→C | `{ scope: tenant, reason }` | yes |
| `stop.release` | S→C | `{ scope, approved_by }` | yes |
| `stop.state` | C→S | `{ cloud_stop, local_stop }` | yes |
| `lease.grant` / `lease.revoke` | S→C | `{ company, expires_at }` | yes |
| `heartbeat` | C→S | `{ tally_reachable, company_open, journal_unresolved_count }` | yes |
| `error` | both | `{ about_msg_id, code, detail }` | — |

### 4.1 `operation.dispatch` body

```json
{
  "operation_id": "ad_<32 hex>",
  "kind":         "post",
  "company":      "Demo Co",
  "voucher": {
    "date":            "2026-08-31",
    "party":           "Sharma Traders",
    "narration":       "cement 40 bags",
    "debit_account":   "Purchases",
    "credit_account":  "Cash",
    "amount_paise":    380000,
    "gst_paise":       0,
    "provenance":      { "...": "..." }
  },
  "expected": {
    "debit_account":  -380000,
    "credit_account":  380000
  }
}
```

`kind` is one of `post`, `reverse`, `reverse_batch`.

**`amount_paise` is an integer. There is no float anywhere in this protocol**,
including in a diagnostic field. A reversal that must restore a trial balance to
the exact paise cannot survive one JSON number being parsed as a double.

**`expected` is what the trial balance should move by.** The connector compares
what actually moved against it, in exact paise, exactly as
`pipeline.reverse_operation` already does. The cloud stating its expectation and
the connector measuring the reality is the whole point: neither side can quietly
agree with itself.

**`narration` arrives WITHOUT the marker.** The connector stamps
`[ACCOUNTANT_DAD:<op_id>]` itself, via `tallyio.client.stamp`. The marker is the
identity, and the program that owns the identity is the one that talks to Tally.

### 4.2 `operation.result` body

```json
{
  "operation_id": "ad_<32 hex>",
  "state":        "posted_verified",
  "tally_id":     "<what TALLY returned, never what we sent>",
  "moved":        { "Purchases": 380000, "Cash": -380000 },
  "measured":     true,
  "detail":       "..."
}
```

`measured` carries the same meaning as `reversal.VoucherOutcome.measured`: was
this movement bracketed by two trial-balance snapshots we took, or was it settled
later by a read? **A read proves a voucher exists. It cannot prove by how much
the books moved.** Reporting `measured: false` is how the connector refuses to
claim a measurement it did not take.

---

## 5. The operation state machine

One operation, from the moment the cloud mints its id to the moment nothing more
can happen to it. Both sides are shown because both keep state, and the two must
never disagree about what is outstanding.

### 5.1 Cloud-side states

| State | Terminal | Meaning |
|---|---|---|
| `DRAFTED` | no | id minted at draft creation. The user has not pressed post. Cannot be dispatched. |
| `INTENDED` | no | the user pressed post. The row is **committed to disk**. Nothing has been sent. |
| `DISPATCHED` | no | handed to a connector on its long-poll |
| `POSTED_VERIFIED` | **yes** | the connector reported a verified post, with measured movement |
| `REFUSED` | **yes** | the connector reported a refusal. **Nothing was written.** |
| `UNKNOWN` | no | no report by the deadline, or the connector reported an unknown outcome |
| `READING` | no | a reconciliation read is in flight |
| `RECONCILED_POSTED` | **yes** | a read proved the voucher exists. Movement unmeasured. |
| `RECONCILED_ABSENT` | no | a read proved the voucher does not exist. Needs a person. |
| `ABANDONED` | **yes** | a person decided not to retry |
| `STOPPED` | **yes** | an emergency stop caught it before dispatch |

### 5.2 Cloud-side transitions — every one of them

| From | To | Trigger | Guard |
|---|---|---|---|
| — | `DRAFTED` | draft created in the browser | — |
| `DRAFTED` | `INTENDED` | user presses post | decision outcome is VALID **and** `Decision.operation_id == Draft.operation_id` (`pipeline.post` already enforces this) |
| `DRAFTED` | `STOPPED` | stop engaged | — |
| `INTENDED` | `DISPATCHED` | connector polls and has capacity | connector holds the write lease for this company · company matches · no stop · tenant under its daily cap |
| `INTENDED` | `STOPPED` | stop engaged | — |
| `DISPATCHED` | `POSTED_VERIFIED` | `operation.result` state = posted_verified | `measured` is true |
| `DISPATCHED` | `REFUSED` | `operation.result` state = refused | — |
| `DISPATCHED` | `UNKNOWN` | `operation.result` state = unknown, **or** 120 s deadline passes with no report | — |
| `UNKNOWN` | `READING` | cloud sends `read.request` | — |
| `READING` | `RECONCILED_POSTED` | `read.result` found = true | — |
| `READING` | `RECONCILED_ABSENT` | `read.result` found = false | — |
| `READING` | `UNKNOWN` | the read itself failed | **a failed read reconciles nothing.** Back to unknown. |
| `RECONCILED_ABSENT` | `DISPATCHED` | **a person approves a retry** | **same operation id.** Never a new one. |
| `RECONCILED_ABSENT` | `ABANDONED` | a person declines | — |
| any non-terminal | `UNKNOWN` | cloud restarted or restored from a backup | see §9.3 |

**The transition that does not exist, and must never be added:**

```
UNKNOWN ──X──► DISPATCHED
```

There is no path from an unknown outcome directly back to a dispatch. The only
way out of `UNKNOWN` is a read. This is the same rule
`accountant/reversal.py` already enforces — `resume()` refuses to run until
`batch.reconciled` is true — and it is the single most important line in this
document.

### 5.3 Connector-side journal states

| State | Terminal | Written when |
|---|---|---|
| `ACCEPTED` | no | fsynced the moment the dispatch is authenticated, **before** anything else |
| `SENDING` | no | fsynced immediately before the first XML byte goes to Tally |
| `POSTED_VERIFIED` | **yes** | write, read-back, field-identity comparison and unfiltered-register check all passed |
| `REFUSED` | **yes** | refused before any XML was sent — wrong company, no lease, stop engaged, no recorded backup, or Tally unreachable |
| `UNKNOWN` | no | anything at all went wrong between `SENDING` and a terminal answer |
| `ABSENT` | no | a reconciliation read proved the voucher does not exist |

### 5.4 The two journal writes that carry the whole guarantee

```
ACCEPTED  ── fsync ──►  nothing has touched Tally yet
SENDING   ── fsync ──►  the next thing that happens is XML on a socket
```

A `SENDING` row with no terminal row after it is the durable signature of "we
started a write and cannot say how it ended". It is the same idea as the
`write_attempted` row `pipeline.post` already writes before the socket opens —
and for the same stated reason: **no `except` clause runs when the machine loses
power.**

### 5.5 The full path, drawn

```
  cloud                                       connector                    Tally
  ─────                                       ─────────                    ─────
  DRAFTED
     │  user presses post
     ▼
  INTENDED  ── committed to disk BEFORE anything is sent
     │
     │  connector polls, holds the lease, no stop
     ▼
  DISPATCHED ──── operation.dispatch ────►  authenticate
                                            check company
                                            check lease
                                            check local stop
                                            check backed_up()
                                               │ any fails → REFUSED ──┐
                                               ▼                       │
                                            ACCEPTED  (fsync)          │
                                               ▼                       │
                                            SENDING   (fsync)          │
                                            write_attempted row        │
                                               │                       │
                                               ├── write_voucher ─────────► Tally
                                               ├── read_by_operation_id ──► Tally
                                               ├── compare VERIFIED_FIELDS │
                                               ├── read_vouchers (register)► Tally
                                               │                       │
                                        ┌──────┴──────┐                │
                                        ▼             ▼                │
                              POSTED_VERIFIED     UNKNOWN              │
                                        │             │                │
     ◄──── operation.result ────────────┴─────────────┴────────────────┘
     │
     ├─ posted_verified ─► POSTED_VERIFIED   (terminal)
     ├─ refused ─────────► REFUSED           (terminal)
     └─ unknown / silence past 120 s ─► UNKNOWN
                                          │
                                          │  read.request — a READ, never a write
                                          ▼
                                       READING
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                     RECONCILED_POSTED        RECONCILED_ABSENT
                        (terminal,                   │
                         measured=false)             │ a PERSON approves
                                                     ▼
                                              DISPATCHED, SAME op id
```

---

## 6. Version negotiation — refuse, never degrade

```
connector sends  v  on session.begin
cloud answers    session.accepted with the agreed v
      OR         session.refused:
                   { code: "version_unsupported",
                     connector_version: 1,
                     cloud_supports: [2, 3],
                     action: "update the connector" }
```

**The cloud supports the current protocol version and the one before it**, for
the length of one update window. The length of that window is
**owner decision D-19**.

**Degrading is forbidden.** A cloud that quietly speaks version 1 to an old
connector is two systems, two sets of bugs and two sets of security assumptions
running side by side. Refusing costs a customer an afternoon. Degrading costs
somebody a wrong voucher, eventually, in a code path nobody tests.

A refused connector does exactly one thing: it reports the refusal to whoever is
at the machine, and stops. It does not fall back, does not retry a lower version,
and does not touch Tally.

---

## 7. Timeouts and retries

### 7.1 Timeouts

| What | Value | Where the number comes from |
|---|---|---|
| long-poll hold | 25 s | short enough that a stop propagates fast, long enough that reconnect churn is ~0.8 req/s across 20 connectors |
| connector → Tally, one request | 30 s | `TallyConfig.timeout_seconds`, the existing default |
| operation deadline at the cloud | **120 s** | 30 s write + 30 s read-back + 30 s register read + 30 s slack |
| connector considered offline | 75 s | three missed 25-second cycles |
| pairing ticket | 15 min | single use, and short because it is a write capability |
| write lease | 120 s, renewed by heartbeat | longer than one operation deadline, so a lease cannot expire mid-write |
| session idle | 5 min with no message of any kind | a session with no heartbeat is not a session |

### 7.2 What may be retried

Freely, as often as needed:

```
session.begin
work.poll
heartbeat
read.request  and  read.result
operation.result       ← idempotent; the cloud keys on operation_id
stop.state
```

Every one of these is either a question or a statement of fact. Asking twice
changes nothing.

### 7.3 What may NEVER be retried

```
operation.dispatch, once the connector has journalled SENDING
   — until a READ has settled what happened

any write, at the transport level
   — already enforced: Transport.send(retry=False) for writes in real.py

a reverse whose outcome was UNKNOWN
   — same rule, same reason

anything at all, after WRONG_MOVEMENT
   — Tally's answer and Tally's books disagree. The batch is CRITICAL_FAILURE
     and cannot be resumed. A program that carries on writing into that is
     making a bad situation larger.
```

**The reason, in one line:** a connection that dies after Tally committed is
indistinguishable from one that died before it did. That sentence is already in
`real.py`'s `Transport` docstring. The network between cloud and connector adds a
second place it is true, and the answer is the same in both places.

### 7.4 The retry that IS allowed, and its exact conditions

```
state is RECONCILED_ABSENT          a READ proved the voucher does not exist
   AND a person approved            not a timer, not a threshold, a person
   AND the SAME operation id        never a new one
   AND the connector holds the lease
   AND no stop is engaged
```

**Why the same id.** If the read was wrong — a race, a filter Tally did not
honour, a voucher not yet visible — then reusing the id means Tally itself
refuses the second write with `DuplicateOperation`. A fresh id would throw away
the one defence that survives every other layer being wrong.

---

## 8. Reconciliation

Reconciliation is the only permitted response to an unknown outcome. It is
**read-only**, and it writes nothing anywhere.

```
CLOUD                                     CONNECTOR
─────                                     ─────────
for each operation in UNKNOWN:
  read.request { operation_id, company } ───►
                                              read_by_operation_id(...)
                                              NO write. NO delete. NO retry.
                                          ◄─── read.result { found, tally_id, voucher }

  found = true   → RECONCILED_POSTED, measured = false
  found = false  → RECONCILED_ABSENT, awaiting a person
  read failed    → still UNKNOWN. A read that failed reconciled nothing.
```

**`measured = false` on a reconciled post is not a detail.** Nobody took a trial
balance either side of that write, so the books moved while nobody was watching.
Claiming a measured movement there would be inventing a measurement, which is
exactly what `reversal.Batch.accounted` already refuses to do by returning `None`.

**The gate that makes this worth having:** a connector with any unresolved
operation is dispatched no new work. Same rule as `reversal.resume` requiring
`reconciled=True` before it writes anything. Unfinished business blocks new
business.

---

## 9. Coming back after being away

### 9.1 The connector has been offline for a week

```
 1  connector starts. Reads its journal FIRST, before any network call.
 2  finds non-terminal rows: ACCEPTED, SENDING, UNKNOWN
 3  session.begin, declaring:
        journal_unresolved:        the ids it cannot account for
        journal_results_unreported: terminal results the cloud may not have
 4  cloud answers session.accepted with a NEW session and a NEW nonce.
        the old sequence space is dead. Nothing captured from it can be replayed.
        reconciliation_required = true
 5  connector replays its unreported RESULTS first. Idempotent — the cloud keys
    on operation_id, so a result the cloud already has changes nothing.
 6  cloud sends read.request for every operation still unresolved on EITHER side.
        reads only. No dispatch. No write.
 7  each read settles into RECONCILED_POSTED or RECONCILED_ABSENT.
 8  ONLY when the unresolved count is zero does the cloud dispatch new work.
 9  any RECONCILED_ABSENT waits for a person. It does not expire into a retry.
```

**No clock is consulted anywhere in that sequence.** A week offline, a month
offline, and a wrong system clock all follow the same path, because the replay
defence is a session nonce and not a timestamp (`CLOUD_ARCHITECTURE.md` §6).

**The connector performs no write at any point in steps 1–8.** Its first write
after a long absence is an operation a person explicitly approved.

### 9.2 The lease, after a long absence

A connector returning after a week may find its lease revoked and held by the
replacement machine. It then runs in **standby**: it may report results and it
may answer `read.request`, and it is dispatched nothing. That is the correct
behaviour — its reads are still useful evidence, and its writes are not wanted.

### 9.3 The cloud restarted, or was restored from a backup

The dangerous direction. A restore can resurrect an operation register that is
older than reality, turning a completed operation back into `DISPATCHED`.

```
after ANY cloud restart or restore:
    every operation not in a terminal state → UNKNOWN
    every one is reconciled by a READ before ANY dispatch happens
    no exception, including operations that "look fine"
```

This is §9.1 applied to the other side. It is also the reason
`CLOUD_ARCHITECTURE.md` §11.7 insists that `DuplicateOperation` at the Tally
boundary must exist independently: if a restore ever slips a re-dispatch through,
that is the defence that stops it becoming a second voucher.

---

## 10. Emergency stop propagation

Two stops. One is a convenience and one is real.

```
CLOUD STOP                              CONNECTOR STOP
──────────                              ──────────────
a per-tenant flag                       a local file / registry value / tray item
set from the website or by us           set by whoever is at the keyboard
pushed down the HELD long-poll,         read by the connector BEFORE EVERY WRITE
   so it arrives immediately            not at startup — before every write
worst case 25 s + RTT if the            immediate
   connection had just closed
works when the cloud is healthy         WORKS WHEN THE CLOUD IS HOSTILE
```

### 10.1 What a stop does

```
cloud stop     → the cloud dispatches no new write operation
connector stop → the connector refuses every write, whatever the cloud says
both           → reads keep working. Reconciliation keeps working.
```

**Reconciliation must never be blocked by a stop.** A read is the one thing that
resolves an unknown, and a stop that blocked it would freeze the system in its
most dangerous state.

### 10.2 What a stop honestly cannot do

```
CANNOT un-send a request already in flight to Tally
CANNOT undo a voucher that has already landed
```

A stop button that claimed either would be a lie. Undoing a landed voucher is
reversal, and reversal is its own operation with its own trial-balance proof.

### 10.3 Messages

```
S→C  stop.engage   { scope: "tenant", reason }
C→S  stop.state    { cloud_stop: true, local_stop: false }   ← on every heartbeat
S→C  stop.release  { scope, approved_by }
```

The connector reports **both** flags on every heartbeat, so the website can show
the truth — including the case where the cloud thinks the stop is clear and the
machine's local flag is still set.

**Clearing a stop is not the reverse of setting one.** Setting is cheap and
reversible; clearing re-arms a system that writes into statutory books. Who may
clear it, and what they must see first, is **owner decision D-20**. The default
this protocol assumes: clearing is refused while any operation is unresolved.

---

## 11. Errors

```json
{ "type": "error",
  "body": { "about_msg_id": "m_...", "code": "...", "detail": "..." } }
```

| Code | Meaning | Sender continues? |
|---|---|---|
| `mac_invalid` | the MAC did not verify | **no.** Session torn down. Security event logged. |
| `seq_gap` | sequence was not `previous + 1` | no. Session torn down and reopened. |
| `version_unsupported` | see §6 | no. Connector stops and tells the operator. |
| `company_mismatch` | the operation names a company this connector is not bound to | the operation is `REFUSED`. Security event logged. |
| `no_lease` | dispatched a write while not holding the lease | operation `REFUSED` |
| `stop_engaged` | a write arrived while a stop is set | operation `REFUSED` |
| `not_backed_up` | `client.backed_up(company)` is false | operation `REFUSED`. Already-built behaviour. |
| `tally_unreachable` | `RealTallyRequired` | operation `REFUSED`. **Nothing was written.** |
| `unresolved_operations` | new work dispatched while the journal has unresolved rows | operation `REFUSED`, and the connector asks for reconciliation |
| `cap_exceeded` | daily operation cap | the cloud never dispatches; the user is told |
| `unknown_type` | a message type this version does not know | **refused, never ignored.** Ignoring an unknown type is how a downgrade attack works. |

**Every refusal names its reason and is durable on both sides.** A refusal with
no recorded reason is indistinguishable from a message that vanished, and those
two need opposite responses.

---

## 12. Concurrency

| Limit | Value | Why |
|---|---|---|
| operations in flight per connector | **1** | TallyPrime processes one XML request at a time. `HttpTransport` already serialises with a lock. Two in flight would queue at the connector anyway, and would make the journal harder to reason about for no gain. |
| connectors holding a write lease per company | **1** | see `CLOUD_ARCHITECTURE.md` §3.2 |
| long-polls per connector | 1 | a second would be a second delivery path for the same work |
| browser sessions per tenant | 10 (a launch cap) | and see the naming problem in `CLOUD_ARCHITECTURE.md` §17 |

**One operation in flight per connector is a real product cost:** posting ten
entries is ten sequential round trips, not ten parallel ones. At 120 s worst case
each, that is a bad afternoon. The mitigation is that the normal case is seconds,
not the deadline — and the deadline only bites when something has already gone
wrong.

---

## 13. What is deliberately NOT in this protocol

| Not here | Why |
|---|---|
| any way for the cloud to read the books | the cloud has no business holding them (`ARCHITECTURE.md` §2) |
| any way for the cloud to run arbitrary Tally requests | the two-family whitelist (`ARCHITECTURE.md` §15) lives in the connector and is not parameterised over the wire. A "send this XML" message would hand the wedge-the-gateway bug to anyone who compromised the cloud. |
| a `cancel` message | you cannot cancel a request already at Tally. Reversal is the honest answer. |
| a compression layer | one more parser in front of the MAC check, for a payload measured in kilobytes |
| a message that changes the connector's company binding | pairing binds it. Rebinding is re-pairing, at the keyboard. |
| a way to disable the read-back | it is the proof. There is no reason to want it off that is not a reason to worry. |

---

## 14. The tests this protocol needs before it may be trusted

None of these exist.

| # | Test |
|---|---|
| 1 | a message with a tampered byte is refused before the JSON is parsed |
| 2 | a message replayed inside its session is refused on sequence |
| 3 | a message replayed into a new session fails its MAC |
| 4 | with defences 2 and 3 **disabled**, a replayed dispatch still produces exactly one voucher |
| 5 | a dispatch naming the wrong company is refused before any XML is built |
| 6 | a dispatch to a connector without the lease is refused by **both** sides |
| 7 | kill the connector between `SENDING` and the journal's terminal write; on restart it performs a read, never a write, and reports the truth |
| 8 | a connector with an unresolved journal row is dispatched no new work |
| 9 | a `read.request` that fails leaves the operation `UNKNOWN`, not reconciled |
| 10 | a `RECONCILED_ABSENT` operation never retries without an explicit human approval |
| 11 | an approved retry uses the same operation id, and a second landing is refused by `DuplicateOperation` |
| 12 | the local stop is read before every write, not once at startup |
| 13 | a hostile harness impersonating the cloud cannot dispatch a write past a local stop |
| 14 | a version mismatch is refused, and the connector touches Tally zero times afterwards |
| 15 | an unknown message type is refused, not ignored |
| 16 | a cloud restore marks every non-terminal operation `UNKNOWN` and reads before it dispatches |
| 17 | no float appears anywhere in a serialised message, asserted structurally |
| 18 | the canonical serialisation is byte-identical across both implementations for a fixed fixture |
