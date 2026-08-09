# CLOUD THREAT MODEL — Accountant Dad

**Written 2026-08-10. Describes a system that does not exist.** Nothing in
[`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) is built, so nothing in this
file is a description of a live defence. Every "what stops it" column is a
*design intention* until the test in the last column exists and passes.

---

## 0. Terms, defined once

| Word | Meaning |
|---|---|
| **threat** | a way somebody could make the system do something it should not |
| **attacker** | whoever is doing that. May be a stranger, a customer, or one of us. |
| **mitigation** | the thing that stops it |
| **MECHANISM** | a mitigation the code enforces. It works when nobody is watching. |
| **POLICY** | a mitigation that is a rule, a habit, or a document. It works only while somebody follows it. |
| **residual risk** | what is left over after the mitigation. The part we are choosing to live with. |
| **STRIDE** | a checklist of six ways things go wrong: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege |
| **attack tree** | one goal at the top, every route to it branching underneath |
| **crown jewel** | the one thing worth protecting above everything else |

**The crown jewel here is one thing and it is easy to name:**

> the customer's statutory books — the legal accounting record inside their Tally

Everything else — our server, our database, our uptime — matters only because
losing it is a route to that.

---

## 1. Which method, and why both

**STRIDE, applied per trust boundary — plus one attack tree for the crown
jewel.**

**Why STRIDE.** This system has very few boundaries and they are unusually sharp.
Four of them, and each one is a place where data crosses from something we
control to something we do not. STRIDE is a per-boundary checklist, so it fits a
system whose risk is concentrated at its seams.

**Why an attack tree as well.** STRIDE-per-boundary is good at finding threats
*at* a boundary and bad at finding routes that cross several. There is exactly
one goal worth mapping that way — an unauthorised voucher in somebody's books —
and §3 maps it.

**What was rejected.** PASTA and full attack-library approaches want a threat
intelligence input this project does not have and cannot honestly fake. There is
one developer, ten customers at launch, and no incident history. A method that
needs data we do not have produces confident-looking output that is invented,
and this project's whole discipline is against that.

---

## 2. The trust boundaries

```
   ┌──────────┐   B1    ┌──────────┐   B2    ┌────────────┐   B3   ┌────────┐
   │ browser  │◄───────►│  CLOUD   │◄───────►│ CONNECTOR  │◄──────►│ TALLY  │
   └──────────┘  HTTPS  └──────────┘   TLS   └────────────┘  HTTP  └────────┘
                             ▲                                       ▲
                             │ B5                                    │ B4
                        ┌────┴─────┐                        ┌────────┴────────┐
                        │ us — the │                        │ ANYTHING on the │
                        │ operator │                        │ same LAN. No    │
                        └──────────┘                        │ auth. At all.   │
                                                            └─────────────────┘
```

| Boundary | Between | Trusted side | Untrusted side |
|---|---|---|---|
| **B1** | person and our server | neither fully | the browser is untrusted input |
| **B2** | our server and the customer's machine | neither fully | **the cloud is untrusted from the connector's point of view** |
| **B3** | the connector and Tally | the connector decides | Tally's XML responses are untrusted input (already the rule) |
| **B4** | Tally's port 9000 and the whole local network | nothing | **there is no boundary here. That is the problem.** |
| **B5** | us and the running system | we are trusted, and that is itself a risk | |

**B2 deserves the strange-looking claim.** From the connector's point of view the
cloud is untrusted. The connector checks the company, checks its lease, checks
its local stop flag, and refuses instructions that fail any of them — *even
though the instruction came from us with a valid signature*. That is deliberate.
The connector is the last thing between the internet and the books, so it does
not get to assume the internet is friendly.

---

## 3. The attack tree — one unauthorised voucher in the books

```
GOAL: write a voucher into a customer's statutory books without authority
│
├── A. go around us entirely — talk to port 9000 directly
│   ├── A1  rogue process on the same Windows machine            ← NOT MITIGABLE BY US
│   ├── A2  another device on the same LAN, if Tally is not loopback-bound
│   └── A3  malware on the customer's PC
│
├── B. make the connector do it
│   ├── B1  compromise the connector binary (supply chain, update channel)
│   ├── B2  steal the connector key and impersonate the cloud
│   ├── B3  compromise the cloud, and issue a legitimate-looking instruction
│   └── B4  replay a captured real instruction
│
├── C. make the cloud do it
│   ├── C1  steal a user session
│   ├── C2  steal the cloud's key database and forge connector traffic
│   ├── C3  a malicious insider (us) issuing an operation
│   └── C4  cross-tenant: act as tenant B against tenant A's connector
│
└── D. make a LEGITIMATE write happen twice
    ├── D1  replay
    ├── D2  a lost reply followed by an automatic retry
    └── D3  restore the cloud database from an old backup, re-dispatching
```

**Read the tree and the design falls out of it.**

- Branch **A** is not defensible by this product. Nothing we build affects it.
  Everything under A is residual risk, and §6 says so plainly.
- Branch **B** and **C** all end at the same place, and cryptography does not
  stop the worst of them (B3, C3). What limits them is the *shape of a write*:
  marked, capped, read back, reversible, stoppable locally.
- Branch **D** is the one this project has already solved, at the bottom of the
  stack, with the operation id and `DuplicateOperation`.

---

## 4. STRIDE per boundary

Only the entries that are real for this system. A STRIDE table padded to
thirty-six cells to look complete is a table nobody reads.

### B1 — browser ↔ cloud

| STRIDE | Threat | Mitigation | Kind |
|---|---|---|---|
| Spoofing | somebody logs in as the customer | password verifier via `hashlib.scrypt`; session cookie `HttpOnly` `Secure` `SameSite=Strict`; rate-limited login | MECHANISM |
| Tampering | the typed entry is altered in transit | TLS 1.3, HSTS | MECHANISM |
| Repudiation | "I never posted that" | every operation carries a user id, and the connector's own append-only action log records the reason | MECHANISM |
| Information disclosure | account names and amounts visible in the browser | unavoidable — that is the product. Limited by there being **one user per customer**. | — |
| Denial of service | flooding the site | rate limiting at the load balancer; the 100-operations-per-day cap | MECHANISM (infrastructure) |
| Elevation | a normal user acts on another tenant | every query scoped by tenant; the cross-tenant test in `CLOUD_ARCHITECTURE.md` §7.3 | MECHANISM, **only once that test exists** |

### B2 — cloud ↔ connector

| STRIDE | Threat | Mitigation | Kind |
|---|---|---|---|
| Spoofing | something pretends to be the connector | per-connector key established at pairing; every message MAC'd | MECHANISM |
| Spoofing | something pretends to be the cloud | TLS with the cloud's certificate **pinned in the installer**, not fetched | MECHANISM |
| Tampering | an instruction is altered | MAC covers the canonical bytes of the whole envelope | MECHANISM |
| Repudiation | disagreement about what was dispatched | two logs joined by `operation_id`, one on each side, both append-only | MECHANISM |
| Information disclosure | the wire carries book data | it carries very little: identifiers and states. Under RELAY (`CLOUD_ARCHITECTURE.md` §8.3) the typed text passes through but is not stored. | MECHANISM (by not holding it) |
| Denial of service | the cloud never answers, so nothing can be posted | fails closed. Nothing is written. The customer is stopped, not harmed. | MECHANISM |
| Elevation | a connector is dispatched work for a company it is not paired to | checked twice — cloud before dispatch, connector before XML | MECHANISM |

### B3 — connector ↔ Tally

| STRIDE | Threat | Mitigation | Kind |
|---|---|---|---|
| Tampering | a malicious XML response | already hardened: expat parser, DOCTYPE screen, entity handlers, external-entity refusal, size cap enforced twice (`ARCHITECTURE.md` §9) | MECHANISM, **already built** |
| Tampering | a request shape that wedges Tally's gateway | the two-family whitelist — `Export+Collection` and `Import+Data`, nothing else (`ARCHITECTURE.md` §15) | MECHANISM, **already built** |
| Repudiation | "the voucher is not mine" | the narration marker `[ACCOUNTANT_DAD:<op_id>]` | MECHANISM, **already built** |
| Denial of service | our own connector wedges the customer's Tally | the whitelist, plus serialised transport (Tally handles one request at a time) | MECHANISM, **already built** |
| Elevation | a write that Tally accepts but the books do not reflect | read-back, identity comparison across `VERIFIED_FIELDS`, unfiltered-register check | MECHANISM, **already built** |

### B4 — the local network ↔ port 9000

| STRIDE | Threat | Mitigation | Kind |
|---|---|---|---|
| **everything** | anything that can reach the port can do anything to the books | **none that we control.** Tally bound to loopback, and the connector on the same machine, is the only reduction available, and it is the customer's configuration. | POLICY, and not ours to enforce |

This row is the honest centre of this document. See §6.1.

### B5 — us, the operator

| STRIDE | Threat | Mitigation | Kind |
|---|---|---|---|
| Elevation | an operator issues an operation against a customer | there is currently **no mechanism**. One person runs this project. | POLICY |
| Repudiation | an operator's action is indistinguishable from the customer's | every operation carries the user id that created it, and an operator-created operation would carry an operator id or none | MECHANISM, **if built that way** |
| Information disclosure | an operator reads a customer's books | they cannot. The books are not on our server. | MECHANISM, by not holding the data |

---

## 5. The ten named threats

Each: **attack · what it gets · what stops it · MECHANISM or POLICY · the test
that would prove it.**

---

### T-01 · A malicious customer

**Attack.** A paying customer tries to reach another tenant, or to attack our
server, or to make our connector do something odd on a machine they control.

**What it gets, if it works.** Another customer's data — which is almost nothing,
because we hold almost nothing. More realistically: our server, or a way to make
the operation register inconsistent.

**What stops it.**

| Sub-attack | Stopped by | Kind |
|---|---|---|
| naming another tenant's operation id | ownership check; refused identically to "no such thing" | MECHANISM |
| XML injection through the typed party or narration | `_escaped()` in `real.py`, already built and already applied to company names and voucher fields | MECHANISM, built |
| oversized typed entry | length cap at the cloud, and `max_response_bytes` at the connector | MECHANISM |
| burning the daily operation cap to deny themselves | it is their own cap. Not a threat. | — |
| pointing their connector at a Tally they should not have | **nothing stops this.** It is their machine and their network. | residual |

**Test.** The cross-tenant test (`CLOUD_ARCHITECTURE.md` §7.3). Plus a fuzz test
that feeds control characters, XML metacharacters and a 10 MB body into the typed
entry field and asserts the connector's transport is never handed a malformed
envelope.

**Residual.** A customer pointing a connector at somebody else's Tally on their
own network is outside anything we can see. Recorded, not solved.

---

### T-02 · A compromised connector

**Attack.** The connector binary on a customer's machine has been replaced or
patched — by malware, by a bad update, or by a supply-chain compromise of our
build.

**What it gets.** Everything. It is the program that talks to a port with no
authentication. It can write unmarked vouchers, skip the read-back, lie upward,
and read the entire book.

**What stops it.**

| | Kind |
|---|---|
| the update package is signed, and the verifying key ships inside the installer rather than being fetched | MECHANISM |
| a downgrade is refused | MECHANISM |
| an update is refused while any journal row is non-terminal | MECHANISM |
| the build is reproducible, so a shipped binary can be compared against the source | MECHANISM, **if built** |
| **nothing at all, once the binary is compromised** | — |

**The honest statement:** a compromised connector is game over for that customer.
The defences above are about *preventing* compromise, not surviving it. There is
no runtime defence, because the thing that would enforce it is the thing that is
compromised.

What *slightly* limits the blast radius: an attacker who wants to be quiet must
also forge the local action log, and that log is append-only SQLite on the
customer's own disk — evidence they do not control the retention of.

**Test.** Ship a deliberately modified package and assert the connector refuses
it. Ship a lower version number and assert refusal. Attempt an update with a
non-terminal journal row and assert refusal.

**Residual. High and unavoidable.** Named as the single worst outcome in the
model.

---

### T-03 · A stolen connector key

**Attack.** Somebody copies the connector's key off the Windows machine — from a
backup, a disk image, or by having local administrator rights.

**What it gets.** The ability to *be* that connector, talking to our cloud: to
receive dispatched operations and to report false results. Under the symmetric
HMAC option (`CLOUD_ARCHITECTURE.md` §5, option A) the same key also lets them
**impersonate the cloud to the real connector**, which is far worse.

**What stops it.**

| | Kind |
|---|---|
| the key is in Windows protected storage (DPAPI), machine and user scoped | MECHANISM |
| it is never logged, never displayed, never re-sent after pairing | MECHANISM |
| the write lease means a second connector claiming the same identity cannot silently take over — the takeover is an explicit, human-visible event | MECHANISM |
| **asymmetric keys would mean the stolen key cannot forge cloud→connector traffic** | MECHANISM, **only under D-16 option B or C** |
| key rotation, and revocation of a connector from the website | MECHANISM, **if built** |

**Test.** Copy a paired connector's key to a second machine, start a second
connector with it, and assert: the cloud detects two claimants for one lease, the
second is refused a write, and a security event is recorded and shown to the user.

**Residual.** Somebody with local administrator rights on the machine holding the
books does not need our key — see T-09. Stealing the key is the *long* way round.

---

### T-04 · A replayed message

**Attack.** A captured, correctly signed message is sent again.

**What it gets.** Under a naive design: a duplicate voucher. Under this design:
nothing.

**What stops it.**

| | Kind |
|---|---|
| strictly increasing sequence within a session; a gap tears the session down | MECHANISM |
| the cloud-minted `session_nonce` is inside the signed bytes, so an old session's messages cannot be replayed into a new one | MECHANISM |
| **and beneath all of it:** `DuplicateOperation` at the Tally boundary means the same operation id produces at most one voucher, whatever happens on the network | MECHANISM, **already built and already tested** |

**Test.** Capture a real `operation.dispatch`, replay it inside the session
(assert rejected on sequence), replay it after a session restart (assert the MAC
fails), and then — with both network defences deliberately disabled — replay it
again and assert Tally still holds exactly one voucher. That third case is the
one worth running, because it proves the bottom defence works alone.

**Residual.** Low. This is the best-defended threat in the model, because the
deepest defence was built for a different reason and happens to cover it.

---

### T-05 · A guessed operation id

**Attack.** Somebody guesses or enumerates operation ids.

**What it gets.** By itself, **nothing**. An operation id is not a capability.

**What stops it.**

| | Kind |
|---|---|
| every operation is owned by a `(tenant, company, connector, user)` tuple, and a request from the wrong tenant is refused | MECHANISM |
| the refusal is identical to "no such operation", so enumeration learns nothing | MECHANISM |
| 128 bits of `uuid4` entropy makes guessing pointless anyway | MECHANISM |
| refusals are rate-limited and logged as security events | MECHANISM |

**The real risk is not guessing, it is reuse.** An operation id reused across two
intents would produce one voucher where two were meant — which fails *safe*, but
silently. The register makes an id single-use by construction.

**Test.** Request a valid operation id as the wrong tenant; assert the response
is byte-identical to a request for a random id, and that a security event exists
for both.

**Residual.** Negligible.

---

### T-06 · A compromised cloud server

**Attack.** Somebody owns our server — the running process, not just the
database.

**What it gets.** The ability to issue **genuine, correctly signed instructions**
to every connector. Every authentication mechanism in this document authenticates
the compromised server perfectly, because it *is* the server.

**What stops it. Not cryptography. Say it out loud.**

| | Kind |
|---|---|
| the 100-operations-per-tenant-per-day cap bounds the number of vouchers that can be created before somebody notices | MECHANISM |
| every voucher we write carries the marker, so every one is findable and **bulk-reversible** | MECHANISM, **already built** |
| every write is read back, so an attacker cannot post silently | MECHANISM, but it protects *correctness*, not *authority* |
| the **connector's local emergency stop** works when the cloud is hostile — it is a local file, read before every write | MECHANISM |
| the backup gate: a company with no recorded backup refuses every write | MECHANISM, **already built** |
| the connector's local action log is on the customer's disk and the attacker cannot edit it from the cloud | MECHANISM |
| the cloud holds no book data, so this is a **write** compromise, not a **read** one | MECHANISM, by not holding the data |

**The one-sentence summary:** a compromised cloud can write up to 100 marked,
read-back, reversible vouchers per customer per day into their books, and cannot
read those books at all.

That is bad. It is also bounded, visible and undoable, which is the most that can
be claimed for a threat where the attacker holds legitimate authority.

**Test.** Run the cloud as a hostile harness against a real connector with the
local stop set; assert no write reaches the transport. Then clear the stop, let
it dispatch 101 operations, and assert the 101st is refused and the first 100 are
all reversible in one batch.

**Residual. High.** This is the threat that would justify the asymmetric option
being irrelevant and the *caps* being the real control.

---

### T-07 · A malicious insider

**Attack.** Whoever runs this project acts against a customer.

**What it gets.** The same as T-06, plus the ability to change the code.

**What stops it.**

| | Kind |
|---|---|
| the books are not on our server, so no amount of insider access reads them | MECHANISM |
| every operation records the user who created it, so an operator-created operation is distinguishable | MECHANISM, **if built that way** |
| the customer's local action log is outside our control | MECHANISM |
| the connector's local emergency stop is outside our control | MECHANISM |
| the existing repository discipline — Claude cannot administer rulesets, cannot change thresholds, cannot delete tests (`ARCHITECTURE.md` §9) | MECHANISM, **already built**, but it constrains the *agent*, not the *owner* |
| **there is one person on this project. Separation of duties does not exist.** | — |

**The honest boundary, mirroring the one `ARCHITECTURE.md` §9 already states:**

> The repository can prevent an agent from weakening protection. Nothing in the
> repository can prevent the owner from doing it. That is not a gap to be closed
> by code; it is what having one owner means.

**Test.** Assert that an operation created without a user id is refused, so
"someone at the company did this" is always attributable to a named user or is
visibly not.

**Residual. Structural.** Recorded, not solved.

---

### T-08 · A network attacker

**Attack.** Somebody on the path between browser and cloud, or between connector
and cloud.

**What it gets.** With TLS: almost nothing. Timing, sizes, and the fact that a
connector is connected.

**What stops it.**

| | Kind |
|---|---|
| TLS 1.3 everywhere, HSTS on the website | MECHANISM |
| the connector **pins** the cloud's certificate or issuing key, shipped in the installer — so a corporate TLS-inspection proxy or a rogue certificate authority cannot silently sit in the middle | MECHANISM |
| every message MAC'd independently of TLS, so breaking the channel is not enough | MECHANISM |
| replay defences (T-04) | MECHANISM |

**Cost of pinning, stated:** a customer behind a corporate proxy that intercepts
TLS will find the connector refuses to connect. That is the correct behaviour and
it will generate support calls. The alternative — accepting an inspected
connection — means accepting that something in the middle can read and rewrite
instructions to a program that writes into statutory books.

**Test.** Point a connector at a proxy with a valid-but-different certificate;
assert refusal, with a message naming the pinning failure.

**Residual.** Traffic analysis: an observer learns when a customer is posting
entries. Accepted.

---

### T-09 · The customer's own machine is compromised

**Attack.** Malware, or a hostile person with local administrator rights, on the
Windows machine running Tally and the connector.

**What it gets.** Everything on that machine: the books, the memory index, the
action log, the connector key, and direct unauthenticated access to port 9000.

**What stops it.** **Nothing we build.**

| | Kind |
|---|---|
| the connector key in DPAPI raises the effort slightly | MECHANISM, marginal |
| the append-only action log makes silent tampering slightly harder | MECHANISM, marginal |
| everything else | the customer's own IT |

**The point worth making:** an attacker on this machine does not need our
connector at all. They can talk to port 9000 directly. Our software is not on the
critical path of this attack, so hardening our software does not reduce it.

**Test.** None meaningful. A test would prove only that we cannot defend it.

**Residual. Total, and outside the product's control.** This is the same class as
"the customer left their books on a shared PC", and the honest response is to say
so in the terms and in the setup guide, not to pretend otherwise.

---

### T-10 · A rogue process on the LAN reaching port 9000 directly

**Attack.** Any device or process on the customer's local network sends XML to
Tally's port 9000.

**What it gets.** Full read and write of the statutory books. No marker, no
operation id, no read-back, no audit row, no reversal path. **Vouchers created
this way are invisible to every safety mechanism this project has built**,
because every one of them keys off our own marker.

**What stops it.**

| | Kind |
|---|---|
| Tally bound to loopback, with the connector on the same machine | MECHANISM in `TallyConfig.is_loopback`, but only if the deployment uses it — **and the customer configures Tally, not us** |
| the installer defaulting to `localhost:9000` and warning loudly on anything else | MECHANISM for the default; POLICY for what the customer does next |
| the customer's own network segmentation | POLICY, and not ours |
| **nothing else. There is no authentication to add. Tally does not have one.** | — |

**One thing this design can honestly add:** the connector can *notice*. Vouchers
in the register that carry no marker of ours already have a name in the code —
`_unmarked_lookalikes` in `real.py` collects them. A periodic count of unmarked
vouchers appearing between our own writes would not prevent anything, but it
would mean somebody finds out. **Detection is not prevention, and it must not be
described as if it were.**

**Test.** Send a hand-built voucher to port 9000 from a second process, then
assert: our bulk reversal does **not** touch it (the marker filter excludes it —
this test already exists as the register control test), and the connector's
unmarked-voucher count increases by one.

**Residual. Total, for prevention. Partial, for detection.** This is the reason
the sentence in `CLOUD_ARCHITECTURE.md` §1 is written the way it is.

---

## 6. Residual risk — what is left

A threat model with no residual risk is a threat model nobody thought about.
These are ranked by how bad the outcome is, not by how likely they are, because
likelihood here would be an invented number.

### 6.1 Port 9000 has no authentication, and never will

**Nothing this product builds changes it.** Anything that can reach the port owns
the books. The connector is a *voluntary* discipline layered on top of an open
door — it marks what it writes, reads back what it wrote, and can undo it. None
of that applies to anybody who walks past it.

The best available reduction is: Tally bound to loopback, connector on the same
machine, and a customer network that is not hostile. All three are the customer's
configuration and none is enforceable by us.

### 6.2 A compromised connector is unrecoverable for that customer

There is no runtime defence against the program that holds the only key to the
door. Everything in T-02 is prevention. Once prevention fails, nothing catches
it.

### 6.3 A compromised cloud can write, and no cryptography stops it

Bounded to 100 marked, read-back, reversible vouchers per customer per day. The
caps and the local stop are the controls. Authentication is not.

### 6.4 One owner means no separation of duties

Recorded in T-07. Not closable by code.

### 6.5 Symmetric keys, if D-16 defaults

If **D-16** is left unanswered and HMAC ships, a stolen cloud database is a write
capability into every customer's books. That is a specific, nameable, avoidable
risk that is being accepted by default rather than by decision, and this is the
document that says so.

### 6.6 Every mitigation marked MECHANISM is currently a POLICY

Because none of it is built. A design intention enforced by nothing is a policy
wearing a mechanism's clothes. **Every row in the "test" column has to exist and
pass before the "kind" column may be read as written.**

---

## 7. Mitigation ledger — mechanism or policy, honestly counted

| Mitigation | Kind today | Kind if built as designed |
|---|---|---|
| XML hardening at the Tally boundary | **MECHANISM, built** | — |
| request-shape whitelist | **MECHANISM, built** | — |
| operation id + `DuplicateOperation` | **MECHANISM, built** | — |
| read-back with field-identity comparison | **MECHANISM, built** | — |
| unfiltered-register check | **MECHANISM, built** | — |
| backup gate before write and before delete | **MECHANISM, built** | — |
| trial-balance equality in exact paise | **MECHANISM, built** | — |
| marker-scoped bulk reversal | **MECHANISM, built** | — |
| write-ahead action log | **MECHANISM, built** | — |
| company identity fail-closed | **MECHANISM, built** (single machine) | MECHANISM, twice |
| tenant scoping | nothing | MECHANISM |
| write lease | nothing | MECHANISM |
| message MAC | nothing | MECHANISM |
| sequence-in-session replay defence | nothing | MECHANISM |
| certificate pinning | nothing | MECHANISM |
| signed connector updates | nothing | MECHANISM |
| launch caps | **POLICY — written in a brief** | MECHANISM, once each has an enforcement point and a test (`CLOUD_ARCHITECTURE.md` §17) |
| local emergency stop | nothing | MECHANISM |
| cloud emergency stop | nothing | MECHANISM |
| "credentials never in logs" | POLICY | MECHANISM, via a fixed-field log writer |
| separation of duties | **POLICY, and there is one person** | POLICY |
| Tally bound to loopback | POLICY, customer-side | POLICY, customer-side |

**Count: nine mitigations are real mechanisms today. All nine were built for the
single-machine product. Every cloud-specific mitigation in this document is
currently nothing at all.**

---

## 8. What would make this threat model wrong

| Claim | What would disprove it |
|---|---|
| a replay cannot produce a duplicate voucher | disable both network defences, replay a dispatch, and find two vouchers |
| a compromised cloud is bounded by the caps | dispatch 101 operations and find the 101st succeeds |
| the local stop works against a hostile cloud | set the stop, dispatch from a hostile harness, and find a write reaching the transport |
| the cloud holds no book data | dump the cloud schema and find a rupee figure or a ledger name |
| unmarked vouchers are excluded from our reversal | hand-write a voucher into Tally and find bulk reversal touching it |
| tenant isolation holds | the cross-tenant test passing while a real cross-tenant read succeeds |

**None of these tests exist yet.**
