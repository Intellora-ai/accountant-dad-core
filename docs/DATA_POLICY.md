# DATA POLICY — Accountant Dad cloud

**Written 2026-08-10. Describes a system that does not exist.** Nothing in
[`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) is built, so no data of any
kind is being held under this policy today.

**Two things this document deliberately does not do:**

1. **It does not invent a retention period.** Every cell that asks "how long?"
   for customer content or for an audit record says `OWNER DECISION` and names
   the decision id. A number written here by an engineer would be quoted back as
   policy later.
2. **It does not state a legal position.** Data residency for Indian statutory
   records, ownership of the audit trail, and breach-notification duties are
   **D-18**, and D-18 needs a lawyer, not a document.

---

## 0. Words used in the columns

| Word | Meaning |
|---|---|
| **cloud** | on our server |
| **local** | on the customer's own Windows machine, next to Tally |
| **at rest** | while stored on a disk |
| **in transit** | while travelling over a network |
| **retention** | how long we keep it |
| **deletion** | what "delete" actually removes, and what it leaves behind |
| **backup** | whether a second copy exists, and where |
| **export** | whether the customer can get a copy out |
| **logging** | whether it ever appears in a log line |
| **OWNER DECISION** | not an engineering choice. Named decision id. Blocked until answered. |

**The rule behind every row:** `ARCHITECTURE.md` §2 says *"We never store the
customer's books."* This policy is that sentence, applied one data class at a
time.

---

## 1. Table A — where it lives and how it is protected

Same rows, same order, as Table B.

| # | Data class | Cloud or local | Encrypted at rest | Encrypted in transit | Who can access |
|---|---|---|---|---|---|
| 1 | **passwords** | **neither.** Only a verifier is stored, in the cloud. | n/a — a `hashlib.scrypt` verifier is not the password | yes, TLS 1.3 | nobody. It cannot be read back. |
| 2 | **sessions** (tokens/cookies) | cloud | yes — stored hashed, not raw | yes, TLS 1.3 + `HttpOnly` `Secure` `SameSite=Strict` | the cloud, to compare. The user's browser holds the raw value. |
| 3 | **connector keys** | cloud **and** local | local: Windows protected storage (DPAPI, machine+user scoped). Cloud: **depends on D-16.** | never re-transmitted after pairing | the connector process. Under D-16 option A, also our cloud database. See §3.1. |
| 4 | **Tally connection info** (host, port, company name) | **local only** | disk-level only | **never sent to the cloud**, except the company *name*, which the cloud needs to match dispatches | the connector. |
| 5 | **the company books** (Tally's own data) | **local only. Never anywhere else.** | whatever Tally and Windows provide. Not ours. | never transmitted to us | the customer, and Tally |
| 6 | **invoices — the typed entry text** | **default: neither.** Relayed, not stored. See §3.2. | n/a under RELAY | yes, TLS 1.3 | the user, and the connector |
| 7 | **source documents** (PDF/PNG/JPG/DOCX) | **none exist.** Not accepted at launch. | n/a | n/a | n/a |
| 8 | **proposed vouchers** | **local.** Rendered to the browser, never written to cloud disk. | local SQLite, disk-level | yes, TLS 1.3 | the user, and the connector |
| 9 | **operation ids and states** | cloud **and** local | disk-level both sides | yes | the tenant that owns them, and us |
| 10 | **audit / action log** | **local is the real one.** The cloud keeps a thinner one with no amounts and no ledger names. | disk-level both sides | yes | the customer owns the local one. We can read the cloud one. |
| 11 | **memory index** (vendor → account) | **local only** | local SQLite, disk-level | never transmitted | the connector |
| 12 | **security events** | cloud | disk-level | yes | us, and the tenant sees its own |
| 13 | **billing records** | cloud | disk-level | yes | us, and the tenant |
| 14 | **backups** | **unknown — D-17.** No backup is assumed to exist. | **D-17** | **D-17** | **D-17** |

---

## 2. Table B — lifecycle

| # | Data class | Retention | Deletion behaviour | Backup | Export | Logging |
|---|---|---|---|---|---|---|
| 1 | passwords | while the account exists | the verifier is removed with the account | D-17 | never — there is nothing to export | **never logged.** Not the password, not the verifier, not a prefix. |
| 2 | sessions | **engineering parameter**, not an owner decision: 12 h idle, 30 d absolute | ended on logout, password change, or connector revocation | **never backed up** | no | the session **id** may be logged. The token never. |
| 3 | connector keys | while the connector is paired | destroyed on revocation, on both sides | D-17 | no | **never logged**, in any form |
| 4 | Tally connection info | while the connector is installed | removed by uninstalling | not ours to back up | shown to the user in the connector UI | host and port may be logged. They are a target, not a secret — see §3.3. |
| 5 | the company books | **not ours.** Tally's, forever, on the customer's machine. | **deleting our software deletes none of it.** See §3.4. | the customer's own Tally backup. The backup *gate* already checks one was recorded. | it is already theirs | never — we never hold it |
| 6 | invoices — typed entry text | **OWNER DECISION — D-14 and D-15.** Under RELAY the answer is "zero"; under QUEUE it is a real retention period nobody has set. | D-15 | D-17 | via the local action log | **the text is never logged.** Its length may be. |
| 7 | source documents | **OWNER DECISION — D-14, then D-15.** Must be answered *before* the first byte is accepted, not after. | D-15 | D-17 | D-14 | never |
| 8 | proposed vouchers | lives on the connector for the life of the draft; drafts are capped (200 today in `web/app.py`) | dropped when the cap rolls over, or when the entry is posted or abandoned | not backed up by us | via the local action log | field *names* and provenance may be logged. Amounts appear in the local action log by design, never in a cloud log. |
| 9 | operation ids and states | **OWNER DECISION — D-15.** These are the join key between the two audit trails, so their retention decides how long the trail is readable. | see §3.5 — a deleted operation id orphans a voucher that still exists in the books | D-17 | yes, to the tenant | logged everywhere. That is their purpose. |
| 10 | audit / action log | **OWNER DECISION — D-15**, and it interacts with D-18: an accounting audit trail may have a statutory minimum nobody here has checked. | **append-only by construction.** `memory/store.py` has no update and no delete path. Deleting means deleting the file. | D-17 | yes — it is the customer's evidence | it **is** the log |
| 11 | memory index | while the connector is installed | removed by uninstalling. Rebuildable from the customer's own Tally at any time. | not ours | yes | vendor keys may appear in local diagnostics. Never sent to the cloud. |
| 12 | security events | **OWNER DECISION — D-15.** Too short and a slow attack is invisible; too long and we are holding a log of somebody's failures. | D-15 | D-17 | the tenant sees its own | it is the log |
| 13 | billing records | **OWNER DECISION — D-15 and D-18.** Tax law probably sets a floor. Nobody has checked which law. | D-15 | D-17 | yes | amounts logged. No book data. |
| 14 | backups | **OWNER DECISION — D-17.** | **D-17, and this is the one that makes "deleted" honest or dishonest.** See §3.6. | — | D-17 | D-17 |

---

## 3. The rows that need more than a cell

### 3.1 Connector keys, and the D-16 fork

Under **D-16 option A** (HMAC, zero dependency), our cloud database holds a key
that can forge a valid instruction to a customer's connector. Under **option B or
C** (asymmetric), it holds only a public half and cannot forge anything.

This is not a privacy question — it is a *write capability* question, and it is
the single sharpest sentence in the security design:

> Under option A, a stolen cloud database is a write capability into every
> customer's statutory books.

Recorded in [`CLOUD_THREAT_MODEL.md`](./CLOUD_THREAT_MODEL.md) T-03 and §6.5.

### 3.2 The typed entry text — the one real fork in this policy

The customer types a bill into a browser. Those bytes reach our server. What
happens next has two defensible answers:

| | Cloud holds | Cost |
|---|---|---|
| **RELAY** (default) | nothing. Passed straight through, held in memory for one request. | the customer cannot type an entry while their PC is off |
| **QUEUE** | invoice content at rest until the connector collects it | the customer can work offline, and our server now holds accounting content — inheriting every row of this table |

**Default is RELAY**, chosen for consistency with `ARCHITECTURE.md` §13, which
already refuses to start the local server at all when Tally is unreachable rather
than serve a half-working application.

**This is D-14.** It is a business judgement about whether "you can only work
when your PC is on" is an acceptable product.

### 3.3 Why the Tally host and port may be logged

They are not a secret in the usual sense. Anyone on the customer's network can
find them by scanning. They are dangerous for a different reason — the port has
no authentication — and secrecy is not the control. Loopback binding is.

The rule that *does* apply: the connector logs the endpoint it used, because a
log row that cannot say which Tally it came from cannot be used as evidence about
any of them. `ActionLog.backend` already carries this reasoning.

### 3.4 The strongest sentence in this policy

> If the customer deletes this software tomorrow, their statutory books are
> complete and untouched.

That is `ARCHITECTURE.md` §2's "no second ledger" rule, restated as a data
guarantee. It is what makes every other row in this table less frightening: the
worst thing we can lose is our own record *of* their bookkeeping, never their
bookkeeping.

### 3.5 Deleting an operation id orphans a voucher that still exists

An operation id is stamped into the Tally narration as
`[ACCOUNTANT_DAD:<op_id>]`. It is the identity used by read-back, duplicate
detection and reversal.

**If the cloud's operation register is purged, the voucher does not disappear.**
It stays in the customer's books with a marker nothing on our side recognises.
The consequences:

- bulk reversal still finds it — `list_our_vouchers` matches the marker itself,
  not our register. **The customer is not stranded.**
- but the *reason* for that voucher — the decision, the questions asked, the
  answers given — lives in the local action log, and only there.

So the retention answer for row 9 is coupled to the retention answer for row 10,
and setting them independently would produce a state where vouchers exist that
nothing can explain. **D-15 must answer them together.**

### 3.6 "Deleted" means nothing until D-17 is answered

If a backup exists and a deleted record survives in it for ninety days, then
"deleted" meant "hidden for ninety days". Saying otherwise to a customer is the
kind of statement that becomes a legal problem, and it would be a statement this
project's whole discipline is against.

**Until D-17 is answered, this policy cannot honestly use the word "deleted".**
That is not a drafting problem to be worked around. It is the reason D-17 has to
be answered before a single customer signs up.

---

## 4. Cross-checks — what should be true, and how to prove it

Each of these is a test that could exist. **One of them now does** — the
deletion row, built by Task 13 on 2026-08-11. The other five still do not.

| Claim | Test |
|---|---|
| the cloud holds no book data | dump the cloud schema and every row; grep for a rupee figure, a ledger name and a party name. Fail on any hit. |
| no secret is ever logged | the log writer takes a fixed set of named fields — `ActionLog` already works this way — so there is no free-form field a secret can land in. Assert structurally that no logging call takes an arbitrary dict. |
| the memory index never leaves the machine | assert no message type in `CONNECTOR_PROTOCOL.md` §4 carries a vendor→account mapping |
| retention is enforced, not intended | once D-15 is answered: a scheduled job, and a test that ages a row past the boundary and asserts it is gone |
| export produces what it claims | export a tenant, re-import into an empty instance, assert byte-identical |
| deletion is real | **BUILT.** `tests/test_data_deletion.py` deletes a tenant and asserts, in the primary store, that the learned index is gone, that the account is closed, that every session is dead and that the audit log survives with its rows marked. It says **nothing** about backups, and neither does the screen, because D-17 is unanswered — see [`DATA_DELETION.md`](./DATA_DELETION.md) |

---

## 5. The owner decisions this policy is blocked on

| id | Question | What it blocks here | Default |
|---|---|---|---|
| **D-14** | may the cloud hold accounting content at all — typed text, proposed vouchers, chart of accounts, source documents? | rows 6, 7 | RELAY. The cloud holds none of it. |
| **D-15** | retention and deletion periods for every class, and what deletion means | rows 6, 7, 9, 10, 12, 13 | **none. No period is invented here.** |
| **D-16** | symmetric or asymmetric connector keys | row 3, and §3.1 | HMAC — the branch that leaves the risk open |
| **D-17** | do backups exist? Where, encrypted how, kept how long, restored by whom? | row 14, and the backup column of every row | **none.** A backup nobody decided on is the worst of both worlds. |
| **D-18** | the legal position: residency for Indian statutory records, ownership of the audit trail, breach notification. **Needs a lawyer.** | rows 10 and 13, and anything customer-facing | **none. No legal position is invented in this document.** |

**A note on numbering.** The brief that commissioned this document referred to
the retention and deletion questions as D-07 and D-08. In this repository those
ids already mean other things — D-07 is the declared-licence-mode question and
D-08 is the *"when may cloud work begin"* gate, which is `NOT_YET_RELEVANT` and
has not fired. The ids used here are the next free ones, D-14 onward, and the
owner should confirm the mapping before any of them is quoted elsewhere. This is
also recorded in [`CLOUD_ARCHITECTURE.md`](./CLOUD_ARCHITECTURE.md) §19.
