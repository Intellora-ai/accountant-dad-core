# Observability — what this product emits, where it comes from, and what it deliberately does not measure

Written 2026-08-11, Task 15.

Before this, the product could answer exactly one question about itself:
`GET /health`, which is a genuine measurement — every value read off the live
runtime — and answers "can this thing receive work right now". It could not
answer the other two questions anybody actually has:

- **how much has it done, and how did that go**
- **which of these log lines belong to the entry the customer is asking about**

One entry is **several HTTP requests**: `POST /entry`, then `POST /answer` once
or twice. Nothing tied them together, because nothing was logged at all —
`accountant/web/app.py::Handler.log_message` is overridden to `pass`, so the
whole observable output of the product was whatever `serve()` printed at
startup.

---

## 1. The two rules everything here obeys

### Rule 1 — a count comes from the durable store, or it is not a count

`MemoryStore.actions(company)` is append-only, has no update path and no delete
path, and it survives the process. Every count on `/metrics` is derived from it
at read time.

A process-local counter is not an acceptable substitute. It resets on restart
and then reports a **smaller number than the truth**, which is worse than no
number at all, because a person will act on it.

The one exception is `uptime_seconds`, which is a fact *about this process* and
has nothing durable it could be read from. It is labelled as what it is.

### Rule 2 — an unmeasured value is `NOT_MEASURED`, never `0`

Standing owner rule. Zero and unknown look identical in every dashboard ever
built, and only one of them is a fact.

- **A measured zero is written `0`.** We counted the rows and there were none.
  `outcome_not_valid: 0` is a fact: `NOT_VALID` is currently unreachable from a
  typed entry, and saying so is useful.
- **An unmeasured value is written `NOT_MEASURED`**, followed by `  # ` and the
  reason. Never `0`, never blank, and never an omitted line — an omitted line
  reads as a broken scrape.

`question_rate` is the case the rule exists for. The share of entries we had to
ask about is **undefined** when there are no entries. Writing `0` there says
"this system never has to ask a question", on the day it has done nothing at
all — the most flattering thing it could possibly say about itself.

---

## 2. Correlation ids — joining lines and joining requests

Two ids, doing two different jobs.

| id | scope | where it comes from |
|---|---|---|
| `request=req_…` | one HTTP request | minted per request in `Handler.handle_one_request` |
| `entry=…` | one **entry**, so two or three requests | the draft id: set by `/entry` once the draft exists, and by `/answer` and `/dismiss` off the form |

Both live in `contextvars.ContextVar`, **not** in a module global. Task 11
replaces `HTTPServer` with a threading one, and a global would then be one
customer's request id stamped on another customer's lines — which makes a
correlation id worse than useless, because it joins together lines that have
nothing to do with each other. `accountant/web/app.py::_principal` is the same
pattern for the same reason.

The request id is **minted here and never taken from the caller**. An
`X-Request-Id` chosen by whoever is calling can be repeated on purpose, which
files one caller's lines under another caller's investigation.

Absent ids read `NOT_RECORDED` — this codebase's existing word for "we did not
record this" (`ActionLog.actor`, audit rows with no principal). A blank would
render `request= entry=` and read as a formatter bug rather than a fact.

### It is on every line, and not because each caller remembers

A `logging.Filter` (`observability._Correlation`) is attached to the **handler**
and stamps both ids onto every record that reaches it. On the *logger* it would
run only for records logged directly there and not for records propagated from a
child logger, so `logging.getLogger("accountant.web")` would produce lines with
no ids at all.

---

## 3. What is written, line by line

Install with `observability.install_logging()`. `serve()` calls it first thing,
before anything can fail.

Format:

```
%(asctime)s %(levelname)s request=<id> entry=<id> event=<name> key=value …
```

### `event=request` — one per HTTP request

```
2026-08-11 09:14:02,113 INFO request=req_4f2b91c0a7de entry=d-19 event=request
  method=POST path=/answer status=200 ms=41.2 tally_ms=33.8 app_ms=7.4
  tally_calls=3 slow=no
```

| field | meaning |
|---|---|
| `method`, `path` | the route. **The query string is cut off** — this app puts nothing in one, so anything found there arrived from outside and must not be copied into a file that gets mailed around |
| `status` | what we answered. Captured by overriding `send_response`, so `send_error` and the cookie-setting path are covered too |
| `ms` | the whole request |
| `tally_ms` | of that, the part spent waiting for Tally |
| `app_ms` | `ms - tally_ms`. **This is us** |
| `tally_calls` | how many round trips it took |
| `slow` | `yes` when `ms >= SLOW_REQUEST_MS` |

Written in a `finally`, so a request that **raised** is still timed. A request
that failed slowly and one that failed instantly are different problems and the
duration is what separates them.

### `event=tally_call` — one per call out to Tally

```
… event=tally_call name=read_vouchers ms=12.9 slow=no
```

Measured in a `finally` too. A Tally that has stopped answering is slow first
and absent second, and the timing of the failure is the evidence for which.

The named calls today: `list_companies`, `read_accounts`, `read_vouchers`,
`post_voucher`, `list_our_vouchers`, `trial_balance`, `reverse_operation`,
`reversal_preview`, `reversal_execute`.

### Reading it: is it us, or is it Tally?

- `slow=yes` on a `tally_call` line, `slow=no` on the `request` line → **Tally**
- `slow=no` on every `tally_call`, `slow=yes` on the `request` line → **us**;
  look at `app_ms`
- `tally_calls` climbing for one route → we added a round trip

### What "slow" means, as a number

```
SLOW_TALLY_MS   = 500     one Tally round trip
SLOW_REQUEST_MS = 2000    one HTTP request end to end
```

Both in `accountant/observability.py`, and `tests/test_observability.py` asserts
those two numbers appear in this document, so the prose and the constants cannot
drift.

**THESE ARE THRESHOLDS, NOT MEASURED BASELINES, AND THIS DOCUMENT WILL NOT
PRETEND OTHERWISE.** There is no recorded round-trip time for a licensed
TallyPrime anywhere in this repository — `docs/PROJECT_STATE.md` records the
connector as licence-blocked, so nobody here has ever timed one. They are chosen
from what the two numbers have to separate: `Runtime.confirm_company` makes one
Tally round trip on *every* request by design and a person typing an entry
tolerates roughly a second in total, so a single trip over 500ms is already
eating the budget; and `POST /entry` makes at least three Tally calls before it
decides anything, so the request threshold is deliberately more than three times
the call threshold rather than equal to it. **The first real measurement should
replace both, in that one place.**

### `event=audit_row` — one per durable `action_log` row

```
… event=audit_row action=posted operation=op_7c31… outcome=valid
```

Written by `record()` and `note()`, right after the durable row. It carries the
**operation id**, which is the key that joins the log to the audit trail.

### Why the request id is NOT a column on `action_log` — decided 2026-08-11

`MemoryStore._migrate` would have taken one. It is additive-only, every existing
row would be left `NULL`, and `NULL` reads back as `NOT_RECORDED` — the same
rule `actor`, `previous_state`, `tenant_id` and `user_id` already follow. So the
question was never whether the migration was possible. It was whether the column
should exist, and the answer is **no**:

- **It is a key into something that expires.** A request id identifies a line in
  a log file, and log files rotate. Six months later the column names a line
  that no longer exists anywhere — a foreign key to nothing, inside an
  append-only statutory record that cannot be corrected.
- **The durable joins already exist.** `run_id` says which process, and
  `operation_id` says which entry. Both are on every row and both outlive any
  log.
- **The join is needed in the other direction.** The question actually asked is
  "given this voucher, what happened", not "given this log line, which row". So
  the operation id goes on the LOG LINE instead, which is the direction that
  keeps working after the log has been rotated away.

`tests/test_observability.py` asserts both halves: that the column is absent,
and that a `posted` row in the database and an `event=audit_row` line in the log
really do meet on the same operation id.

### What never reaches a line

Session tokens, cookie values, passwords, and uploaded document bytes. There is
no field for any of them and `log()` is never handed one. A log is the copy of
your data that ends up in the widest number of places; it gets identifiers and
durations and nothing a thief could use.

Every value is passed through `observability._field`, which strips `\n`, `\r`
and `\t`. Without it, a newline in a path or an exception message would end the
line early and let the rest be read as a **second, forged line** carrying
whatever request id the forger chose.

---

## 4. `GET /metrics`

**Authentication is REQUIRED.** `do_GET` calls `_identify()` before it reaches
the route; an unauthenticated caller gets 401 and not one number.

Why, when `/health` is open: `/health` says whether the service can receive
work — useful to a load balancer, worth nothing to a competitor. `/metrics`
carries a **named company's trading volume**: how many bills they typed, how
many we posted into their books, how many we had to ask about, how many were
undone. Nothing on it is exposed unauthenticated.

**No company confirmation, deliberately.** `_confirm_company()` costs a Tally
round trip. A scrape runs every fifteen seconds and would otherwise add one
forever — and the moment a person most needs these numbers is the moment Tally
has stopped answering. An endpoint that needs Tally in order to report on Tally
reports nothing exactly when it matters, which is the defect `/health` had when
it was a hardcoded constant.

`runtime()` is still called, so a server with nothing connected **refuses** here
(503) rather than serving zeros that read as a quiet day.

### The format, and why it is not Prometheus

Plain text, `name: value`, one metric per line, `#` comments. **Deliberately not
the Prometheus exposition format.** That format has exactly one value type, a
float, so there is no way to write `NOT_MEASURED` in it. The two ways out are to
emit `0` — which rule 2 forbids and which is a lie — or to omit the series,
which reads as a broken scrape rather than an honest gap. A scraper for this
format is nine lines of `split(": ")`.

### Example

```
# Accountant Dad metrics. Plain text; one metric per line.
# Counts are read from the durable action log for this company, so
# they survive a restart. A 0 below means we counted and found none.
# NOT_MEASURED means this system has not measured it. It is never 0.

company: Accountant Dad Final
uptime_seconds: 1841.3
action_log_rows: 12
entries_seen: 3
outcome_not_valid: 0
outcome_unclear: 4
outcome_valid: 2
writes_attempted: 2
writes_outcome_unknown: 0
reversals_single: 1
reversals_bulk_vouchers: 0
refused_replays: 1
auth_refusals_401: NOT_MEASURED  # an auth refusal writes no durable row …
auth_refusals_403: NOT_MEASURED  # an auth refusal writes no durable row …
question_rate: 0.667
```

### Every metric, and where it comes from

| metric | source | notes |
|---|---|---|
| `company` | `Runtime.company` | measured off the live backend identity, never the module default |
| `uptime_seconds` | `time.monotonic()` at import | the one process-local value. Monotonic so it cannot go backwards when somebody corrects the machine clock |
| `action_log_rows` | `len(store.actions(company))` | every durable row for this company |
| `entries_seen` | distinct `operation_id` over decision rows | **entries, not rows.** One entry asked about and then posted writes two rows and is one entry; counting rows would report the app doing twice the work whenever it had to ask |
| `outcome_valid` / `outcome_unclear` / `outcome_not_valid` | decision rows by `outcome` | **decisions, not entries** — one entry can contribute an `unclear` and then a `valid`. Every kind is always printed, including the ones at zero: a kind that vanishes when it has not happened is a kind nobody can alert on |
| `writes_attempted` | rows with `action = write_attempted` | the write-ahead row `pipeline.post` writes before it touches Tally |
| `writes_outcome_unknown` | rows with `action = write_outcome_unknown` | **the number to watch.** Each one means a voucher may exist in somebody's books and nobody can say |
| `reversals_single` | rows with `action = reversed`, `outcome = reversed` | the undo button. `not_found` rows are excluded — an undo of something we never posted is not a reversal |
| `reversals_bulk_vouchers` | rows with `action = bulk_reverse`, `outcome = reversed_verified` | vouchers actually reversed by a batch, not batches started |
| `refused_replays` | rows with `action = refused_replay` or `write_refused_duplicate` | somebody posted the same entry twice. Both kinds counted together, because that is one question; they are separate rows for whoever needs to tell them apart |
| `question_rate` | entries with an `unclear` decision ÷ `entries_seen` | `NOT_MEASURED` when there are no entries |

---

## 5. What is deliberately NOT measured, and why

These are the values a reader will look for and not find as a number. Each one
prints `NOT_MEASURED` with its reason attached, rather than a zero.

### `refused_replays` — CORRECTED 2026-08-11, and it is now measured

This section said `refused_replays` could not be measured, and gave the reason:
a refused write replay raised `DuplicateOperation`, which `pipeline.post`
recorded as `write_outcome_unknown` — **defect I2**, held by a strict `xfail`.
Counting those rows would have reported a defect as fixed while the customer's
audit trail still said a voucher may exist and must be checked by hand.

**I2 was fixed while this file was being written.** There are now two named
durable rows, and the metric reads both:

- `refused_replay` — our own write-once operation register caught it before the
  socket opened. Nothing went out.
- `write_refused_duplicate` — our register did not know the id and **Tally
  did**, which is what a database restored from a backup older than the books
  looks like. The voucher carrying that id was written earlier.

Counted together, because "how often does somebody post twice" is one question.
They stay separate rows for whoever needs to tell them apart.

The one half that is still unrecorded is a refused **answer** replay — an
answer bound to a question the entry is no longer asking. That is a 400 and
writes no row at all, by design: nothing was touched, so there is nothing to
record. It is not counted here and is not claimed to be.

### `auth_refusals_401` and `auth_refusals_403`

An auth refusal writes no durable row **on purpose**. Writing one would let a
caller with no credential append an unbounded number of rows to whichever
company this server is bound to — a log-flooding hole opened in order to produce
a metric. The refusals are on the request log lines (`status=401`, `status=403`),
which costs nothing and is where they belong.

### Everything else not on the list

There is no latency histogram, no percentile, no per-tenant breakdown and no
error-rate metric. None of them would be measured from anything; they would be
computed from a process-local window and would reset on restart. When there is a
place to read them from, they get added here.

---

## 6. How to read it when something is wrong

1. **Is it up?** `GET /health`. `ready: false` names its own `failure_code`.
2. **Is it working?** `GET /metrics`. `writes_outcome_unknown` above zero is the
   one number that means somebody has to go and look at a real set of books.
3. **Which customer, which entry?** Find the `event=request` line with the
   status you care about, take its `entry=` value, and `grep` for that. Every
   line of every request for that entry comes back — the typing, the answers,
   and every Tally call inside each.
   Starting from a **voucher** instead: take its operation id out of the
   `action_log` row and `grep` for `operation=<that id>`; the `event=audit_row`
   line carries the `request=` and `entry=` ids to follow from there.
4. **Why was it slow?** Compare `tally_ms` and `app_ms` on the `event=request`
   line. Then look at the `event=tally_call` lines inside that request to see
   which round trip cost the time.

---

## 7. Related

- `accountant/observability.py` — ids, the log, the timing, the metric text
- `accountant/web/app.py::health` — the measured readiness endpoint this follows
- `accountant/web/app.py::metrics` — the route, and why it is behind the credential
- `tests/test_observability.py` — every claim above, asserted
- `docs/AUTH.md` — what a credential is and how a session is opened
