# CLOUD ARCHITECTURE — Accountant Dad

**Written 2026-08-10. Nothing in here is built. Not one line of it exists.**

This is a design document for a product that has three parts:

```
a cloud website  +  a local Windows connector  +  the customer's own Tally
```

The launch promise, in the owner's words:

> A customer can open Accountant Dad in a browser, type a bill, review the
> proposed entry, answer plain-English questions, and safely post it into their
> own Tally through a local Windows connector.

---

## 0. Read this before anything else

### 0.1 This document is ahead of its own gate

[`DECISIONS.md` D-08](./DECISIONS.md) says cloud and multi-user work is
`NOT_YET_RELEVANT`, and that the trigger is the MVP completion checklist in
[`ARCHITECTURE.md` §11](./ARCHITECTURE.md) being ticked. That checklist is not
ticked. The `RealTally` acceptance test has never been run.

So this file is **a design, written early, on purpose**. It is not permission to
start. The owner's own rule is quoted here so it does not get lost:

> No cloud code until the architecture is internally consistent.

Everything below is the attempt to make it internally consistent, plus an honest
list of the places where it is not yet, and cannot be until a person decides
something.

### 0.2 It does not overrule `ARCHITECTURE.md`

Where this design would contradict the existing architecture, the contradiction
is **flagged in §20 and left standing**, not resolved in this file. The existing
architecture is the authority.

### 0.3 It assumes no new runtime dependency, and says what that costs

`pyproject.toml` says `dependencies = []`. Today the product installs and runs
with a plain Python and nothing else. `ARCHITECTURE.md` §10 lists "a web
framework" as deliberately outside the architecture, and
[`DECISIONS.md` D-04](./DECISIONS.md) is the open question about whether one may
ever be added.

**This design is written so it can be built with the standard library alone.**
Every place where that is expensive or awkward is named, with the cheaper
alternative and its dependency cost beside it. Nowhere does this document assume
the constraint away.

### 0.4 Words this document uses

Defined once, here, and then used without re-explaining.

| Word | Meaning in this document |
|---|---|
| **cloud** | the website and the server behind it, run by us |
| **connector** | a small Windows program installed on the customer's machine, next to Tally |
| **Tally** | TallyPrime, the customer's accounting program, holding the real books |
| **tenant** | one paying customer. One tenant = one organisation. |
| **operation** | one intended statutory write. It has an id, and it exists before anything is sent. |
| **statutory books** | the legal accounting record. Tally holds it. We never do. |
| **voucher** | one accounting entry inside Tally |
| **trial balance** | the list of every ledger and its balance. Used as proof, in exact paise. |
| **paise** | 1/100 of a rupee. All money is a whole number of paise, never a decimal. |
| **fail closed** | when unsure, do nothing and say so. Never guess, never proceed. |
| **idempotent** | doing it twice has the same effect as doing it once |
| **read-back** | after writing, read the thing from Tally to prove it is really there |
| **lease** | a time-limited exclusive right. Only the holder may write. |
| **long-poll** | the connector asks the cloud "any work?" and the cloud holds the question open, answering the moment work appears |
| **MAC** | Message Authentication Code. A short tag proving a message was not altered and came from someone holding the key. |
| **mTLS** | mutual TLS. Both ends prove who they are with a certificate, not just the server. |

---

## 1. The one fact that shapes everything

```
TallyPrime is Windows-only.
It listens on http://localhost:9000.
It has NO authentication. None. Reaching the port IS the permission.
A cloud server can never reach a customer's localhost.
```

Two consequences, and everything in this document falls out of them.

**First: the connector exists only because the cloud cannot reach Tally.** There
is no other reason for it. If Tally had a cloud API, there would be two programs
here instead of three.

**Second, and this is the sentence to remember:**

> Port 9000 has no authentication, so the connector is the only thing standing
> between the internet and a customer's statutory books.

Not the login page. Not the TLS certificate. Not the password. Those protect the
cloud. The thing that protects the *books* is a small Windows program that
decides what to send to a port that will do whatever it is told.

That is why almost every safety rule in this document lives in the connector and
not in the cloud, and why the connector is allowed to refuse the cloud.

---

## 2. The three programs, and the direction of the arrow

```
   the customer's staff
          │
          │  HTTPS, from a browser
          ▼
   ┌─────────────────────┐
   │   THE CLOUD         │   accounts, sessions, operation register,
   │   run by us         │   the website, the work queue
   └─────────────────────┘
          ▲
          │  the CONNECTOR dials OUT to the cloud.
          │  the cloud NEVER dials in. There is no inbound
          │  port, no tunnel, no port forward on the
          │  customer's machine.
          │
   ┌─────────────────────┐
   │   THE CONNECTOR     │   the memory index, the action log,
   │   customer's        │   the operation journal, the pipeline,
   │   Windows machine   │   the decision, the read-back
   └─────────────────────┘
          │
          │  plain HTTP, no auth, loopback only
          ▼
   ┌─────────────────────┐
   │   TALLYPRIME        │   the statutory books
   │   localhost:9000    │
   └─────────────────────┘
```

**The arrow between cloud and connector points up, always.** The connector opens
the connection; the cloud answers on it. This is not a style preference:

| Why outbound-only | What it costs |
|---|---|
| the customer opens no firewall port, ever | the cloud cannot reach a connector that is not currently connected |
| a scan of the customer's public IP finds nothing of ours | work waits in a queue instead of arriving instantly |
| there is no listening service on the machine holding the books | the connector must reconnect, and reconnection is a state machine |
| a home or office router needs no configuration | latency is bounded by the poll cycle, not by the network |

**How the connection is held: HTTP long-poll.** The connector sends a request
that means "any work for me?" and the cloud holds it open for up to 25 seconds,
answering the instant something appears. If nothing appears, the cloud answers
"nothing" and the connector immediately asks again.

**Why long-poll and not a WebSocket.** A WebSocket would be the normal choice.
Python's standard library has no WebSocket client. Long-poll needs only
`http.client`, which is in the standard library, so this choice is what keeps
`dependencies = []` true. The cost is one held TCP connection per connector and
a small amount of reconnect churn.

**The arithmetic that says this is survivable at launch scale:**

```
10 customers × 2 connectors            =  20 held connections, worst case
25-second hold, immediate reconnect    =  20 / 25  ≈  0.8 requests/second idle
10 concurrent browser users × 10       = 100 concurrent browser sessions
```

A hundred sessions and twenty held connections is inside what a threaded
standard-library HTTP server does. **The zero-dependency constraint survives
the launch caps because the caps are small, not because the standard library is
sufficient in general.** Raise the caps and this paragraph stops being true, and
that is the moment D-04 has to be answered.

**TLS, rate limiting and DDoS protection are infrastructure, not a runtime
dependency.** They sit in front of the application — a managed load balancer or
a reverse proxy — and they do not appear in `pyproject.toml`. That distinction is
what makes "no runtime dependency" an honest claim rather than a dangerous one.

---

## 3. Identity — five kinds, and none of them are the same thing

Most cloud bugs that end in the wrong data going to the wrong person are one
identity being used where another was meant. So there are five, they are named,
and the rules about which one gates what are written down.

| Identity | What it is | Who mints it | Where it lives | Format |
|---|---|---|---|---|
| **tenant** | one paying customer | cloud, at signup | cloud database | `t_<uuid4hex>` |
| **user** | one person who can log in | cloud, at signup | cloud database | `u_<uuid4hex>` |
| **company** | one Tally company | **Tally**, not us | connector config, cloud database | the exact company string Tally reports, plus its `normalise_company` key |
| **connector** | one installed program on one machine | cloud, at pairing | cloud database, connector's local store | `cx_<uuid4hex>` |
| **operation** | one intended statutory write | **cloud**, before anything is sent | cloud register, connector journal, the Tally narration, the action log | `ad_<uuid4hex>` — the format `tallyio.client.new_operation_id` already produces |

### 3.1 Company identity is read, never chosen

The existing `accountant/tallyio/factory.py` already does the right thing and it
is copied straight into the cloud design:

```
list the companies Tally has open
is the company we expect in that list?
   no  → RealTallyRequired. Refuse. Nothing is read, nothing is written.
   yes → proceed
```

In the cloud version this check happens **twice, in two different programs, for
two different reasons**:

| Where | Question | Failure |
|---|---|---|
| cloud | does this operation's company match the company this connector is paired to? | refuse before dispatch; nothing goes on the wire |
| connector | is the company in this message the company I am bound to, and is it open in Tally right now? | refuse before any XML is built |

Both must pass. The connector's check is the one that matters, because it is the
one that still works when the cloud is lying.

**Wrong company always fails closed.** That invariant does not weaken because a
network appeared; it gets a second copy.

### 3.2 The company write lease — and a contradiction in the launch caps

The launch caps say **1 company per customer** and **2 connectors per customer**,
while the product description says **one local connector per company**. Those
cannot all be true at once.

Two connectors that can both write to one company is a duplicate-voucher machine.
The exactly-once story in §11 depends on there being exactly one program
deciding what reaches Tally.

**The reading this design assumes** — and it needs owner confirmation
(**D-21**) — is that the second connector exists for *replacement*: the customer
is moving to a new PC, and both are installed for a while.

**The mechanism that makes that safe is a write lease:**

```
at most ONE connector holds the write lease for a company at any moment
the lease is granted by the cloud, has an expiry, and is renewed by heartbeat
a connector without the lease may READ and may report results
a connector without the lease may NEVER be dispatched a write
transferring the lease requires the old connector to be seen offline for a
   stated period, AND an explicit human action
```

The lease is a mechanism, not a rule in a document: a dispatch to a
lease-less connector is refused by the cloud, and the connector refuses a write
it receives while it does not hold the lease. Both sides check. A test proves
both refusals.

---

## 4. Pairing — how a connector comes to exist

Pairing is the moment a piece of software on somebody's PC becomes trusted to
write into their books. It happens once, in person, at the keyboard.

```
1  the user, logged into the website, clicks "add a connector"
2  the cloud creates a PAIRING TICKET:
       ticket_id, a short human code, a one-time secret, an expiry of 15 minutes,
       bound to (tenant, company), single use
3  the website SHOWS the short code. It is not emailed, not sent in a link.
4  the user runs the connector installer on the Windows machine
5  the connector asks: "type the code from the website"
6  the connector types the code back to the cloud, over TLS, together with:
       - a fresh connector key it generated locally  (see §5)
       - its protocol version
       - the Tally endpoint it can see, and the companies Tally reports
7  the cloud checks: ticket valid, unexpired, unused, and the company Tally
   reports MATCHES the company on the ticket
8  the cloud mints cx_<...>, stores the connector's public identity, burns the
   ticket, and returns the connector id
9  the connector writes its key and its id to local protected storage
10 the ticket is now dead. It cannot be reused, and a second attempt is logged.
```

**Why a typed code and not a link.** A link can be forwarded, pasted into a chat
and clicked by the wrong person. A code typed at the keyboard of the machine
being paired proves somebody is sitting at that machine.

**Why the connector generates its own key.** A key the cloud generates and sends
has existed on our server. A key the connector generates has not. This matters
in exactly one scenario and it is the scenario that matters most: our database is
stolen. See §5 for why it is not fully solved.

**What pairing costs.** Fifteen minutes of validity is short enough to be
annoying if the customer is interrupted. That is deliberate: a long-lived pairing
ticket sitting in somebody's inbox is a write capability into their books.

**What fails if pairing is wrong.** A connector paired to the wrong company will
refuse every operation at step 3.1's second check, so the failure mode is
"nothing works", not "vouchers go to the wrong books". That is the correct
direction for this failure to point.

---

## 5. Authenticating every message

Two layers, and they answer different questions.

| Layer | Question it answers | Mechanism |
|---|---|---|
| **channel** | am I talking to the real cloud, and is anybody listening? | TLS 1.3, server-authenticated. The connector **pins** the cloud's certificate or its issuing key, shipped inside the installer. |
| **message** | did this exact message come from this exact connector, unaltered? | a MAC over the canonical bytes of the message |

**Every message carries a MAC. There is no unauthenticated message type**, not
even a heartbeat. A protocol with one exception has one hole.

### 5.1 The uncomfortable part, stated plainly

The natural stdlib choice is **HMAC-SHA256** (`hmac` and `hashlib`, both standard
library, no dependency). It is symmetric: the same key signs and verifies.

That means **our cloud database holds a key that can forge a valid instruction to
a customer's connector.** If the database is stolen, the thief can tell every
connector to post vouchers into every customer's books.

The fix is asymmetric signatures — the connector holds a private key that never
leaves the machine, the cloud holds only the public half and cannot forge
anything. Two ways to get there:

| Option | Dependency cost | What it buys |
|---|---|---|
| **A. HMAC-SHA256** | none. Pure stdlib. | fast, simple, and a stolen cloud DB is a write capability into every customer's books |
| **B. mTLS with a connector-generated keypair** | stdlib `ssl` can *use* a client certificate, but cannot *generate* a keypair. Needs a bundled OpenSSL binary in the Windows installer, or a Windows CNG call. | a stolen cloud DB cannot forge a connector |
| **C. Ed25519 message signatures** | a runtime dependency (`cryptography`). This is **D-04** territory. | same as B, simpler protocol, first runtime dependency the project has ever had |

**This design's position:** B is correct and A is what gets built first if the
installer work is too expensive. **It is an owner decision — D-16** — because
the cost is a Windows installer complication or a runtime dependency, and the
benefit is a specific breach scenario.

**Be honest about the limit of asymmetric.** It stops a stolen *database*. It
does not stop a compromised *server*, because a compromised server is the real
cloud and can legitimately issue commands. What limits that damage is not
cryptography at all — it is the per-tenant operation cap, the local emergency
stop, and the fact that every voucher we write is marked, read back, and
reversible in bulk. See [`CLOUD_THREAT_MODEL.md`](./CLOUD_THREAT_MODEL.md) T-06.

### 5.2 What is signed

The MAC covers the **canonical bytes** of the whole envelope, not the parsed
object. Canonical means one and only one byte string can represent a given
message:

```
JSON, keys sorted, ASCII only, no spaces:
    json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
```

Those are exactly the rules `accountant/generate/serialise.py` already uses, so
the project has one canonicalisation rule and not two.

Full envelope shape and the byte-level rules are in
[`CONNECTOR_PROTOCOL.md`](./CONNECTOR_PROTOCOL.md) §2.

---

## 6. Replay protection

A replay is an attacker capturing a real, correctly signed message and sending it
again. Signatures do not stop it — the message really was genuine.

**Three candidates, and why one wins:**

| Candidate | Why not chosen alone |
|---|---|
| **timestamp window** | needs both clocks to agree. We do not control the clock on a customer's Windows PC, and a machine that has been off for a week is exactly the case where it is wrong. A clock-based reject would lock out the customer who most needs to come back. |
| **nonce cache** | the receiver remembers every message id it has seen. The cache must be bounded, and the bound is exactly the window an attacker waits out. A connector offline for a week is the case where the cache has forgotten. |
| **sequence number within a session** | **chosen.** |

### 6.1 The chosen mechanism

```
a SESSION is opened by the connector and its id is minted by the CLOUD,
   together with a fresh random session_nonce the cloud has never used before

within a session, every message carries seq: 1, 2, 3, ...
   strictly increasing, no gaps allowed, no reuse

the receiver rejects any message whose seq is not exactly
   (highest accepted in this session) + 1

the session_nonce is part of the bytes the MAC covers, so a message from an
   OLD session cannot be replayed into a NEW one — its MAC will not verify
```

**Why this handles the week-offline case, which the other two do not.** A
connector that has been off for a week comes back and opens a *new* session. The
old sequence space is dead. Nothing captured from the old session can be
replayed, because the new session's nonce is in the signed bytes and the attacker
cannot produce a MAC over it. No clock is consulted. No unbounded cache is kept.

**What it costs.** The receiver must keep one small piece of state per live
session (the session nonce and the highest sequence). A dropped message means a
gap, and a gap means the session is torn down and reopened rather than the
message being skipped. That is intentional: skipping is how a replay slips
through, and reopening a session is cheap.

**A timestamp is still carried, and is advisory only.** It is logged so a human
can read the audit trail. It is never used to accept or reject.

### 6.2 Replay protection is not what makes writes safe

This is the important sentence in this section.

> Even a perfectly replayed "post operation X" produces **at most one voucher**,
> because `write_voucher` raises `DuplicateOperation` on a repeated operation id,
> and that is already built and already tested.

Replay protection stops noise, wasted round trips and confused audit trails. The
thing that stops a duplicate statutory entry is the operation id, in Tally, at
the bottom of the stack — a mechanism, not a network control. Two independent
defences, and the lower one is the one that cannot be bypassed by anything that
happens on a network.

---

## 7. Operation ownership and tenant isolation

### 7.1 Every operation is owned

An operation id is not a capability. Knowing one gets you nothing.

```
every operation row carries: (operation_id, tenant, company, connector, user)
every request naming an operation is checked against the caller's tenant
a mismatch is refused, logged as a security event, and answered identically
   to "no such operation" so the caller learns nothing
```

`ad_` + 32 hex characters is 128 bits from `uuid4`. Guessing is not the threat.
**Reuse is the threat**, and reuse is what the register prevents: an operation id
is minted once, stored once, and can only ever be in one tenant's register.

### 7.2 Tenant isolation, ranked by how much they can be trusted

| Level | Mechanism | How much it can be trusted |
|---|---|---|
| **strongest** | the customer's books never leave their machine, so there is no cross-tenant book data to leak | absolute. You cannot leak what you do not hold. |
| **strong** | the memory index is on the connector, one company per machine | absolute for the same reason. Also satisfies `ARCHITECTURE.md` §4.3 invariant 1 by construction rather than by a query filter. |
| **medium** | every cloud table carries `tenant`, and every read is scoped | as good as the code. One missing `WHERE` clause breaks it. |
| **weak** | a policy that says "do not cross tenants" | worth nothing on its own |

**The design deliberately pushes as much as possible into the top two rows.**
Isolation by not having the data is the only kind that survives a bug.

### 7.3 The cross-tenant test that has to exist

A single test, run in CI, that:

```
creates tenant A and tenant B
posts one operation as A
attempts, as B, every request shape that names A's operation id
asserts every one is refused, and that B cannot tell "refused" from "no such thing"
asserts a security-event row exists for each attempt
```

Without that test, tenant isolation is a policy. With it, it is a mechanism.

---

## 8. Storage boundaries — what lives where, and what NEVER leaves

`ARCHITECTURE.md` §2 already states the rule this section obeys:

> **We never store the customer's books.**

The cloud version does not get to weaken that. It gets a stricter version of it.

### 8.1 The split

```
CLOUD — identifiers, states and scheduling      CONNECTOR — accounting content
──────────────────────────────────────────      ───────────────────────────────
tenant, user, session records                   the memory index (SQLite)
connector registry and keys                     the action log (SQLite, append-only)
the company NAME and its normalised key         the operation journal
the operation register: id + state + times      the chart of accounts
security events                                 the proposed voucher
billing                                         the trial balance
                                                every voucher Tally holds
                                                the Tally endpoint and port
```

**The cloud stores identifiers and states. It does not store amounts, ledger
names, party names or narrations.** That is the line, and it is checkable: a
dump of the cloud database should contain no rupee figure and no ledger name.
That is a test, not an intention.

### 8.2 Why the memory index stays on the machine

It is derived entirely from the customer's own posted history, which never leaves
the machine. Moving it to the cloud would mean our server holds a picture of
every supplier the customer buys from and what they spend on them. That is the
customer's books in a thin disguise, and §2 forbids it.

Two more reasons that are not about privacy:

- `ARCHITECTURE.md` §4.3 invariant 1 says no index is ever built from more than
  one company. On the machine, that is physically true. In a shared database, it
  is one `WHERE company_key = ?` away from being false.
- A cloud outage does not stop the connector proposing accounts, because the
  thing it proposes from is local.

**What it costs, and this is a real cost.** The connector is not thin. It carries
`accountant/memory/`, `accountant/pipeline.py`, `accountant/detect/`,
`accountant/decide.py` and SQLite. Improving a detector then means **shipping a
new connector to every customer**, not deploying the cloud. Feedback loops get
slower. That is the price of the customer's books never touching our disk, and
the design pays it.

### 8.3 The one that is genuinely a fork: the typed text

The user types a bill into a browser. Those bytes reach our server. What happens
next is a real decision with two defensible answers.

| Option | What the cloud holds | Cost |
|---|---|---|
| **RELAY (default)** — the cloud passes the typed text straight to the connector and keeps nothing | nothing | if the connector is offline, the customer cannot even type an entry. The website says "your connector is offline" and refuses input. |
| **QUEUE** — the cloud holds the typed text until the connector comes back | invoice content, at rest, until delivered | the customer can work while their PC is off. Our server now holds accounting content, and every question in [`DATA_POLICY.md`](./DATA_POLICY.md) applies to it. |

**Default is RELAY**, because it is consistent with the existing architecture's
temperament: `ARCHITECTURE.md` §13 already refuses to start the server at all
when Tally is unreachable, on the grounds that a half-working application is
worse than a clear refusal.

**This is an owner decision — D-14.** It is a business judgement about whether
"you can only work when your PC is on" is an acceptable product, not something
code can settle.

### 8.4 What NEVER leaves the customer's machine, under any option

```
the Tally company file
the trial balance
posted voucher history
the memory index
the chart of accounts, except transiently in a single browser session
the Tally endpoint, port and any Tally credentials
```

"Transiently" in that list means: the chart of accounts travels cloud→browser so
a person can be shown the account that was chosen, and is never written to cloud
disk. Under RELAY it exists in cloud memory for the life of one request.

---

## 9. Secrets

| Secret | Where it lives | How it is protected | Who can read it |
|---|---|---|---|
| user password | **nowhere.** Only a verifier is stored. | `hashlib.scrypt` — standard library, no dependency. Cost parameters set from a measured time budget, never invented. | nobody |
| session token | cloud database | random from `secrets.token_urlsafe`, stored hashed, `HttpOnly` `Secure` `SameSite=Strict` cookie | the cloud, to compare |
| connector key | connector's machine, in Windows protected storage (DPAPI, machine+user scoped) | never written to a log, never sent after pairing, never displayed | the connector process, and — under option A in §5 — our cloud database |
| pairing ticket secret | cloud, for ≤15 minutes | single use, burned on redemption | nobody after redemption |
| the cloud's TLS private key | the hosting platform's key store | outside this architecture, owner-managed, exactly as `ANTHROPIC_API_KEY` is today | the platform |
| Tally endpoint and port | connector config file on the customer's machine | it is not a secret in the usual sense — it is a *target*, and it is dangerous because port 9000 has no auth. It never travels to the cloud. | the connector |

**A secret in a log is a secret that leaked.** The existing rule in
`ARCHITECTURE.md` §9 — "Credentials: never in files, never in logs" — is carried
forward, and it needs a mechanism: the audit writer takes a fixed set of named
fields (`ActionLog` already does exactly this) rather than a free-form blob, so
there is no field a secret can accidentally end up in.

---

## 10. Source documents

**Not promised at launch.** No PDF, PNG, JPG or DOCX. Typed text only.

That is not a limitation to work around, it is the safest possible starting
position, and it should be stated as such:

```
no file upload      →  no file storage
no file storage     →  no file retention question
no file retention   →  no file deletion question
no file at rest     →  nothing to leak from a stolen bucket
```

**If source documents are ever accepted**, the following must be answered
*before* the first byte is accepted, not after:

- may a source document reach our server at all, or must it go browser →
  connector only? (**D-14**)
- how long is it kept, and by whom? (**D-15**)
- is it in the backup? (**D-17**)
- what does deletion mean for a copy already inside a backup? (**D-15**)

`ARCHITECTURE.md` §4.7 already says extraction sits behind an adapter and that
the reading of documents is somebody else's problem. That stays true. Sending a
customer's bill to a third-party extraction service is a **data-sharing
decision** and belongs to D-14, not to an engineer choosing a backend.

---

## 11. THE HARD PROBLEM — exactly-once across a network

### 11.1 Why the network makes this worse

Today, `pipeline.post` runs in the same process as the write. The dangerous
window is short.

With a cloud in the middle, there are **three** places a reply can be lost, and
each of them turns a completed action into an unknown one:

```
browser ──X── cloud          the user does not know if their click landed
cloud   ──X── connector      the cloud does not know if the work was accepted
connector ──X── Tally        the connector does not know if the voucher exists
                             ← this one already exists today
```

The middle one is new and it is the nasty one. The cloud says "post this". The
connector posts it. The connector's reply is lost. From the cloud's point of view
nothing happened. **If the cloud retries, the customer gets a second voucher in
their statutory books.**

### 11.2 The rule

> **An unknown outcome is resolved by a READ. Never by a retry. Ever.**

This is not new. It is exactly what `accountant/reversal.py` already does:
`reconcile()` is read-only, and `resume()` refuses to run until
`batch.reconciled` is true. The cloud design uses the same shape and the same
vocabulary so there is one idea in the system rather than two.

### 11.3 Who mints the operation id, and when

**The cloud mints it, and it mints it when the DRAFT is created — not when
"post" is pressed.**

Why the cloud and not the connector: the cloud is the only place that sees the
user's intent. If the connector minted the id, a re-sent instruction would arrive
as a fresh intent and get a fresh id, and two ids mean two vouchers.

Why at draft time and not at post time: a user who double-clicks "post" must not
create two operations. Minting at draft time means the second click finds an
operation that already exists and shows its status instead. This mirrors what
`pipeline.build_draft` already does — `operation_id=new_operation_id()` at draft
construction — and it means `Decision.operation_id` can be checked against
`Draft.operation_id`, which `pipeline.post` already enforces.

### 11.4 Durable before the socket, on both sides

```
CLOUD                                    CONNECTOR
─────                                    ─────────
1  mint ad_<...>
2  COMMIT operation row: INTENDED
   (nothing has been sent yet)
3  state → DISPATCHED, offer on the
   connector's held long-poll
                                    ──►  4  FSYNC journal row: ACCEPTED
                                         5  FSYNC journal row: SENDING
                                         6  ActionLog "write_attempted"
                                            (this row already exists today)
                                         7  write_voucher → read_by_operation_id
                                            → register check   (all existing)
                                         8  FSYNC journal terminal row
                                    ◄──  9  report the terminal state
10 record the reported state
```

**Nothing goes on a socket that is not already on disk.** Step 2 before step 3;
step 4 and 5 before step 7. That ordering is the whole guarantee.

Step 6 is not new — `pipeline.post` already writes a `write_attempted` row before
the socket opens, precisely so that a machine losing power mid-write leaves a
durable trace. The connector journal is the same idea one layer up.

### 11.5 How the connector proves what it did after a restart

The connector starts. Before it accepts one byte of new work:

```
1  read the journal
2  find every row not in a terminal state  (ACCEPTED, SENDING)
3  for each one, do a READ:  read_by_operation_id(company, operation_id)
       found     → POSTED_VERIFIED, and mark it measured=False
       not found → NOT_POSTED
       read fails→ stays unknown; the connector accepts NO new work
4  report every settled result to the cloud
5  only when zero rows are unresolved may the connector be dispatched new work
```

**`measured=False` is not decoration.** A read proves the voucher exists. It
cannot prove *by how much the books moved*, because no trial-balance snapshot
brackets a write that happened while nobody was watching. This is exactly the
distinction `reversal.VoucherOutcome.measured` already draws, and the cloud must
carry it or it will claim a conservation check it never performed.

**Rule 5 is the same rule as `reversal.resume` refusing to run on an
unreconciled batch.** A connector with unfinished business is not allowed to
start new business.

### 11.6 What the cloud does when a report never arrives

```
DISPATCHED, and the deadline passes with no report
        │
        ▼
    UNKNOWN                     ← never, under any circumstance, re-dispatched
        │
        │  the ONLY permitted next action is a read.request
        ▼
   ┌────────────┬─────────────────┐
   │ read says  │ read says       │
   │ it EXISTS  │ it is ABSENT    │
   ▼            ▼                 │
RECONCILED   RECONCILED_ABSENT    │
_POSTED           │               │
                  │               │
                  ▼               │
        a PERSON approves a retry │
                  │               │
                  ▼               │
        DISPATCHED again, with    │
        the SAME operation id  ◄──┘
```

**The retry uses the same operation id, never a new one.** A new id would throw
away the one defence that works even if every other layer is wrong:
`DuplicateOperation` at the Tally boundary. Reusing the id means that if the
first attempt *did* land after all — if the read was itself wrong or raced — the
second attempt creates nothing.

**The deadline is arithmetic, not a guess:**

```
Tally write timeout           30 s   (TallyConfig.timeout_seconds, existing default)
read-back                     30 s
unfiltered register read      30 s
slack for a slow machine      30 s
                             ─────
operation deadline           120 s
```

### 11.7 The three independent defences

| # | Defence | Where it lives | What it stops |
|---|---|---|---|
| 1 | one operation id per intent, minted at draft time | cloud | two ids for one intended write |
| 2 | `DuplicateOperation` on the narration marker | **Tally boundary** — already built | a second voucher even when the same id arrives twice |
| 3 | unknown outcomes settled by a read, never a retry | cloud + connector | a lost reply becoming a duplicate |

Each one alone has a hole. Defence 1 fails if the cloud's database is restored
from an old backup. Defence 2 fails if the marker is stripped. Defence 3 fails if
the read itself lies. **All three would have to fail together to produce a
duplicate statutory entry**, and they fail for unrelated reasons.

### 11.8 The invariants, carried through unchanged

These come from the existing architecture. A network does not weaken any of them,
and each gets a note about where it is enforced in the cloud shape.

| Invariant | Where it is enforced in the cloud design |
|---|---|
| one operation id creates AT MOST ONE statutory voucher | connector, at the Tally boundary. Unchanged. |
| every write has a verified company identity | twice — cloud before dispatch, connector before XML |
| every post is read back from Tally | connector. The cloud never sees Tally and cannot do this. |
| every reversal is read back from Tally | connector, through `pipeline.reverse_operation` |
| UNKNOWN_OUTCOME is never blindly retried | §11.6. Read-driven, human-approved. |
| wrong company always fails closed | §3.1, both checks |
| HTTP/XML success is NOT accounting success | connector. **A cloud `200 OK` is even less of a proof than an XML one** — it means our server accepted a message, nothing more. |
| Trial Balance is compared exactly, in integer paise | connector. The trial balance never leaves the machine. |
| FakeTally never proves RealTally behaviour | unchanged, and now also: **a simulated connector never proves connector behaviour.** A fourth evidence class is needed — see §15.3. |

---

## 12. When things are down

Three things can be down independently: the cloud, the connector, Tally.

| Cloud | Connector | Tally | What the person sees | What may be written |
|---|---|---|---|---|
| up | up | up | normal | posts allowed |
| up | up | **down** | "Tally is not answering on this machine", with the exact setting to check | **nothing** |
| up | **down** | — | "your connector has been offline since HH:MM" | **nothing** |
| **down** | up | up | the website does not load | **nothing new.** See below. |
| **down** | up | up, with work in flight | nothing, until the cloud returns | **only what was already accepted** |

### 12.1 Tally unavailable

The existing behaviour is already right and is reused: `factory.real_tally`
raises `RealTallyRequired` and nothing is read or written. The cloud version adds
one thing — the connector reports the refusal *upward*, so the website can say
what is wrong instead of just timing out.

**It never falls back to a fake.** `factory.py`'s docstring already explains why,
and it is worth repeating because a cloud makes the temptation stronger: *"a
fallback would turn 'your books are unreachable' into 'your books are empty', and
those are opposite facts."*

### 12.2 Connector offline

The cloud knows within 75 seconds (three missed 25-second cycles). Until it
reconnects:

- the website shows the offline state and the last-seen time
- no new operation may be created (under RELAY, §8.3)
- any operation left `DISPATCHED` passes its deadline and becomes `UNKNOWN`,
  awaiting the read that will settle it

### 12.3 Cloud unavailable — the important row

**An operation the connector has already ACCEPTED is finished, not abandoned.**
Stopping halfway through a write is worse than finishing it: a half-written
voucher with no terminal journal row is precisely the unknown state this whole
design exists to avoid. So the connector:

```
finishes what it accepted
journals the terminal state
writes the ActionLog rows locally, as it already does
accepts NO new work
holds its results until the cloud returns, then reports them
```

The connector's local action log means **the audit trail survives a cloud
outage**, and survives us. That is a property worth naming: if this company went
out of business tomorrow, the customer's evidence of what was posted is on their
own machine, in SQLite, next to their books.

### 12.4 Unknown outcome — the summary

```
who can produce one     any of the three links breaking at the wrong moment
who resolves it         a READ, performed by the connector
who approves a retry    a person, never a timer
what a retry uses       the SAME operation id
what may never happen   a write dispatched to resolve an unknown
```

---

## 13. The emergency write stop

### 13.1 Two stops, because one is not enough

| Stop | Where | Who can set it | Works when |
|---|---|---|---|
| **cloud stop** | a per-tenant flag in the cloud | the user, and us | the cloud is healthy and we want to stop dispatching |
| **connector stop** | a local flag on the customer's machine — a file, a registry value, or a tray menu item | whoever is at the keyboard | **always, including when the cloud is compromised or lying** |

A stop that lives only in the cloud is useless in the one case that matters most:
the cloud is the thing that has gone wrong. **The connector stop is the real
one.** The cloud stop is a convenience.

### 13.2 What a stop does, and what it honestly cannot do

```
DOES     refuse to dispatch any new write operation        (cloud stop)
DOES     refuse to execute any write, whatever the cloud says  (connector stop)
DOES     leave every read working, so people can still see what happened
DOES     leave reconciliation reads working — a stop must not block
             the one thing that resolves an unknown

CANNOT   un-send a request already in flight to Tally
CANNOT   undo a voucher that has already landed
```

**A stop button that claimed to un-send would be a lie.** What undoes a landed
voucher is reversal, and reversal is a separate, verified, trial-balance-checked
operation.

### 13.3 Propagation, with a number

The cloud stop is pushed down the held long-poll immediately. If the connection
had just closed, the worst case is one reconnect cycle:

```
worst-case cloud-stop propagation  =  25 s (long-poll hold) + network RTT
connector-stop propagation         =  immediate, it is a local file read
                                      checked before every write
```

The connector reads its local stop flag **before every write**, not at startup.
A flag only read at startup is a flag that does not work in an emergency.

### 13.4 Clearing a stop

Clearing is not the reverse of setting. **Who may clear it, and what they must
see first, is an owner decision — D-20.** The default this design assumes: a stop
can be set by anyone, and cleared only after the reconciliation view shows zero
unresolved operations. Setting is cheap and reversible; clearing is the dangerous
direction.

---

## 14. Connector updates and supported versions

### 14.1 Updates

The connector is a program that can write into somebody's statutory books. An
automatic update channel into it is a remote-code-execution path into the machine
holding the books. That is not an argument against updating; it is an argument
for saying out loud what an update is.

```
the update package is SIGNED
the verifying key ships INSIDE the installer, and is never fetched
an update is REFUSED while any journal row is non-terminal
after updating, the connector reconciles BEFORE accepting new work
the connector's protocol version is reported on every session
a downgrade is refused
```

**Automatic or operator-approved is an owner decision — D-19.** The default this
design assumes is **operator-approved**, with the cloud able to *require* an
update by refusing the session (§14.2). That makes "you must update" a hard stop
the customer sees, rather than something that happens to their machine while they
are not looking.

### 14.2 Version negotiation — refuse, never degrade

```
connector sends  protocol_version on session.begin
cloud answers    session.accepted with the agreed version
      OR         session.refused, naming BOTH versions and what to do

the cloud supports the CURRENT protocol version and the ONE before it,
for the length of one update window (length is D-19)
```

**Degrading is forbidden.** A cloud that quietly speaks an old protocol to an old
connector is two systems to reason about, two sets of bugs, and two sets of
security assumptions. Refusing costs a customer their afternoon. Degrading costs
somebody a wrong voucher eventually.

### 14.3 Supported versions

| Thing | Supported | Note |
|---|---|---|
| TallyPrime | yes | the only Tally that has ever answered this connector |
| Tally.ERP 9 | **undecided** | [`DECISIONS.md` D-03](./DECISIONS.md) is open. This design does not settle it and must not. |
| Windows | the connector runs on the machine that runs Tally, or one that can reach it on a private network | Tally is Windows-only; that is not our choice |
| browsers | current desktop browsers. **Mobile is not promised.** | |

---

## 15. Audit and evidence

### 15.1 The audit trail is the customer's, and it lives on their machine

`accountant/memory/store.py` already has an append-only `action_log` table with
no update and no delete path. `ActionLog` already requires a `reason` on every
row, and already records `backend`, `run_id` and `operation_id`.

**That table stays on the connector.** The cloud keeps its own, thinner log:

| Cloud log holds | Connector log holds |
|---|---|
| who logged in, when, from where | every decision and its reason |
| which operation was created, dispatched, reported | every write attempt, before the socket |
| every refusal and every security event | every read-back result |
| connector pairing, lease grants, stop events | every reversal and the exact paise that moved |
| **no amounts, no ledger names** | the accounting detail |

Two logs, one story. Joined by `operation_id`, which both carry.

### 15.2 What an evidence bundle must contain

Extending the existing evidence discipline (`ARCHITECTURE.md` §14) to the cloud:

```
backend identity        which Tally, which endpoint, which company     (exists)
licence mode            measured, or UNKNOWN and why                   (exists)
backup identity         what backup was recorded before the write      (exists)
connector identity      cx_<...>, its version, and its protocol version   NEW
lease identity          which connector held the write lease              NEW
cloud identity          the cloud release that dispatched the operation   NEW
the exact voucher set   what was sent                                  (exists)
the trial-balance delta expected vs measured, exact paise              (exists)
```

### 15.3 A fourth evidence class is needed

`ARCHITECTURE.md` §14 defines three classes and five source labels. The cloud
shape adds a way to be wrong that none of them cover:

> A test that exercises the cloud against a **simulated connector** proves
> nothing about a real connector, in exactly the way `FakeTally` proves nothing
> about `RealTally`.

So the label set needs one more member, and the rule that a harness hard-codes
its own label (rather than choosing one at report-writing time) must apply to it:

```
existing:  UNIT_TEST · FAKETALLY · SIMULATOR · EDUCATIONAL_TALLY · LICENSED_REALTALLY
needed:    SIMULATED_CONNECTOR    — the cloud talked to a stub, not a real connector
```

This is a change to `ARCHITECTURE.md` §14 and to `ci/` code, neither of which
this document may make. **It is recorded here as a required change, flagged in
§20.**

---

## 16. Retention, deletion, backup, restore

**This document does not state a single retention period, and it does not state a
legal position.** Both are owner decisions and one of them needs a lawyer.

The complete table — one row per data class, with columns for cloud/local,
encryption, access, retention, deletion, backup, export and logging — is in
[`DATA_POLICY.md`](./DATA_POLICY.md). Every cell that is an owner decision is
marked there.

The three architectural facts that constrain whatever the owner decides:

**1. Deletion of the cloud side does not delete the books.** The books are on the
customer's machine, in Tally, and always were. If the customer deletes their
account, their statutory record is complete and untouched. That is the same
promise `ARCHITECTURE.md` §2 already makes, and it is the strongest thing in the
whole data policy.

**2. A backup is a second copy with its own lifetime.** "Deleted" that leaves a
copy in a backup for another 90 days is not deleted, and saying otherwise in a
policy is the kind of statement that becomes a legal problem. Whether backups
exist at all, where, and for how long, is **D-17**.

**3. Restore is the dangerous direction.** Restoring the cloud database from a
backup can resurrect an operation register that is older than reality — it can
turn a completed operation back into `DISPATCHED`, which under §11.6 is a state
that leads to a re-dispatch. **After any restore, every operation not in a
terminal state must be reconciled by a READ before the cloud dispatches anything
at all.** That is the same rule as §11.5, applied to the cloud instead of the
connector, and it is the reason defence 2 in §11.7 must exist independently.

---

## 17. The launch caps, as mechanisms

A cap that lives in a document is not a cap. Each of the owner's caps needs an
enforcement point and a test, or it will be exceeded by accident.

| Cap | Enforced where | What happens at the limit | Test that proves it |
|---|---|---|---|
| 10 customers | signup path | signup refused, named reason | create 11, assert the 11th is refused |
| 1 company per customer | pairing, and every dispatch | pairing refused | pair a second company, assert refusal |
| 2 connectors per customer | pairing | pairing refused | pair a third, assert refusal |
| **1 write lease per company** | dispatch, both sides | dispatch refused; connector refuses too | dispatch to a lease-less connector, assert both refusals (§3.2) |
| 100 operations per customer per day | operation creation | creation refused, counter shown to the user | create 101, assert the 101st is refused |
| 10 concurrent cloud users per customer | session creation | oldest session ended, or new one refused | open 11, assert the stated behaviour |

**Two of these caps contradict the stated scope and need the owner:**

- **10 concurrent users per customer**, when the scope says **one user per
  customer**. Ten concurrent sessions for one person is ten browser tabs. If that
  is what is meant, the cap should be named "concurrent sessions", because a cap
  called "users" that is really about tabs will be read wrongly later.
- **2 connectors per customer** against **one connector per company** — resolved
  in §3.2 by the write lease, and needing confirmation (**D-21**).

---

## 18. What each choice costs, and what fails if it is wrong

| Choice | Cost | If it is wrong |
|---|---|---|
| connector dials out, cloud never dials in | work waits for a poll cycle; no instant push to a disconnected connector | nothing breaks; the product is slower |
| long-poll instead of WebSocket | one held connection per connector; reconnect churn | at higher scale, connection count becomes the bottleneck before anything else does |
| HMAC (option A in §5) | none in build effort | a stolen cloud database is a **write capability into every customer's books** |
| sequence-in-session replay protection | session state on both sides; a gap tears down the session | a replayed message is accepted; §11.7 defence 2 still prevents a duplicate voucher |
| memory index on the connector | detector improvements need a connector release | our server would hold a picture of every customer's suppliers |
| RELAY, not queue | the customer cannot work while their PC is off | our server holds invoice content and inherits every question in `DATA_POLICY.md` |
| cloud mints the operation id at draft time | the cloud must be up to start an entry | a connector-minted id turns a re-sent instruction into a second voucher |
| unknown resolved by read, human-approved retry | a stuck operation needs a person | an automatic retry writes a duplicate into somebody's statutory books |
| refuse on version mismatch | a customer with an old connector cannot work until they update | two protocol versions live at once, and every security assumption doubles |
| operator-approved updates | a fix reaches customers slowly | an auto-update channel is a remote-code path into the machine holding the books |
| the connector may finish work when the cloud dies | a write can complete that nobody is watching | stopping mid-write leaves the exact unknown state this design exists to avoid |

---

## 19. Owner decisions this architecture depends on

**A note on numbering, and a conflict that must be resolved by the owner.** The
brief that commissioned this document referred to the retention and deletion
questions as **D-07 and D-08**, and to the runtime-dependency question as
**D-11**. In this repository those ids are already taken and mean different
things:

| id | What `DECISIONS.md` actually says |
|---|---|
| D-07 | may a *declared* licence mode be trusted when the real one cannot be read |
| D-08 | when may cloud and multi-user work begin — `NOT_YET_RELEVANT` |
| D-11 | N = 10, the acceptance batch size — `SETTLED` |

The runtime-dependency / framework question is **D-04**, not D-11.

**This document does not renumber anything.** It uses the next free ids, D-14
onward, and the owner should confirm the mapping before any of these are quoted
elsewhere.

| New id | Question | Blocks | Default if unanswered |
|---|---|---|---|
| **D-14** | What accounting content may the cloud hold at all? Typed entry text, proposed vouchers, chart of accounts, and — if ever accepted — source documents. | §8.3, the whole of `DATA_POLICY.md` | RELAY. The cloud holds none of it, and the customer cannot work while their PC is off. |
| **D-15** | Retention and deletion periods for every data class, and what deletion *means* when a backup exists. | every retention cell in `DATA_POLICY.md` | **none. There is no safe default and none is invented here.** |
| **D-16** | Message authentication: symmetric HMAC, or asymmetric with a connector-held private key? | §5, and threat T-06 | HMAC, which is the option that leaves the stolen-database risk open. |
| **D-17** | Do cloud backups exist? Where, encrypted how, retained how long, and who may restore? | §16, `DATA_POLICY.md` backup column | **none.** A backup nobody decided on is the worst of both worlds. |
| **D-18** | The legal position: data residency for Indian statutory records, what the terms say about ownership of the audit log, and what breach notification obligations apply. **This needs a lawyer, not an engineer.** | anything customer-facing | **none. No legal position is invented in this document.** |
| **D-19** | Connector updates: automatic or operator-approved, and how long the previous protocol version is supported. | §14 | operator-approved, one version back. |
| **D-20** | Who may set and, more importantly, *clear* the emergency write stop, and what they must see before clearing. | §13.4 | anyone may set; clearing needs zero unresolved operations. |
| **D-21** | Confirm the reading of the launch caps: is the second connector a replacement, and is "10 concurrent users" really 10 sessions for one person? | §3.2, §17 | the write-lease reading in §3.2. |

**Existing decisions this design is blocked behind or shaped by:**

| id | Effect on this design |
|---|---|
| **D-08** | **the gate.** Cloud work may not begin until `ARCHITECTURE.md` §11 is ticked. It is not. |
| **D-04** | whether a runtime dependency, and specifically a web framework, may exist. Everything here is designed to work without one; §2's arithmetic shows why that is possible at launch scale and not in general. |
| **D-03** | Tally.ERP 9 in scope or not. §14.3 carries the question forward unchanged. |
| **D-01** | the licence. Until it is settled, no live evidence exists for the *single-machine* product, let alone the cloud one. |
| **D-06** | stale index versus live ledger. In the cloud shape the index is on the connector, so the question is unchanged — but a connector that has been offline for a week has a staler index than one that has not, and that makes D-06 sharper, not softer. |

---

## 20. Conflicts with existing documents — flagged, not overridden

Four places where this design does not fit the repository as it stands. None of
them are resolved here.

### 20.1 `ARCHITECTURE.md` §4.8 forbids what this describes

> **Forbidden:** multi-user, login, accounts, cloud hosting, mobile

That is the current, correct rule for `accountant/web/app.py`. The cloud website
is a **different program**, not a modification of that one. Nothing in this
design proposes changing `app.py`. If cloud work ever starts, §4.8 needs a
sentence distinguishing "the local app" from "the cloud app", and that sentence
is the owner's to write.

### 20.2 `ARCHITECTURE.md` §10 defers cloud hosting

The trigger is "the single-machine vertical slice works end to end". It has not
fired. This document is a design written before its trigger, and says so in §0.1.

### 20.3 `ARCHITECTURE.md` §14 needs a sixth evidence label

`SIMULATED_CONNECTOR`, per §15.3. Without it, a test of the cloud against a stub
connector can be reported under `SIMULATOR`, which currently means "no Tally was
involved" and would let a cloud test masquerade as something stronger.

### 20.4 `ARCHITECTURE.md` §9 says "Tally: loopback binding only"

That rule is right and this design keeps it. But it now needs a companion rule,
because a second program is on the machine:

> The connector binds nothing. It makes outbound connections only — to Tally on
> loopback, and to the cloud over TLS. It never listens on a port.

A connector that listened, even on loopback, would be a second unauthenticated
door next to the first one.

---

## 21. What would prove this design wrong

Written down deliberately, because a design with no falsifier is an opinion.

| Claim in this document | What would show it is wrong |
|---|---|
| the stdlib is enough at launch caps | a measured load test at 10 tenants × 10 sessions × 2 connectors where the standard-library server fails to keep 20 long-polls alive |
| a lost reply never becomes a duplicate voucher | an injected fault test — kill the connector between `write_voucher` and its journal write, restart, and find two vouchers |
| the cloud database contains no accounting content | a script that dumps the cloud schema and greps for a rupee figure or a ledger name, run in CI |
| tenant isolation holds | the cross-tenant test in §7.3 |
| the emergency stop works when the cloud is hostile | set the local stop, then have a test harness impersonate the cloud and dispatch a write; assert nothing reaches the transport |
| a week-offline connector rejoins safely | leave a connector off for a simulated week with two non-terminal journal rows; assert it reads before it writes, and accepts no new work until both are settled |

**None of these tests exist.** Nothing in this document is implemented.
