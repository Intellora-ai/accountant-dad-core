# Idempotency and error-response closure — owner items 12–16

Branch `closure/flag-cap-and-truth`. Written 2026-08-10.
Evidence class: **FAKETALLY** plus `RealTally` over `tests.test_real_tally.TallySim`.
Nothing here ran against a real TallyPrime, and nothing here changes
`docs/PROJECT_STATE.md`'s record that real-Tally idempotency is UNVERIFIED and
licence-blocked.

## What was delivered

| file | tests | floor | over |
|---|---|---|---|
| `tests/test_idempotency.py` | 39 (37 pass, 2 xfail-strict) | 25 | +14 |
| `tests/test_error_responses.py` | 82 (79 pass, 3 xfail-strict) | 25 | +57 |

Command: `COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider`

Both files pass. In the full suite the two files contribute
**116 passed, 5 xfailed, 0 failed**. Ten unrelated failures were present in the
same run, all inside files another agent is editing concurrently
(`tests/test_contract_differences.py`, `tests/test_reversal_recovery.py`, and
`tests/test_reverse_all_cli.py::test_only_the_command_imports_above_the_connector_boundary`
which now sees `factory.py` importing `accountant.memory.identity`). The same
ten fail with these two files removed from the run.

## The headline number, item 12

**The same typed entry submitted twice creates 2 drafts and 2 vouchers.**

`POST /entry` has no request-level idempotency key. `pipeline.build_draft` mints
a fresh `operation_id` per call, so the second submission is a different
operation *by construction* — C5 is not defeated, it is never consulted. One
bill of Rs 4,200 typed twice charges Purchases Rs 8,400 and credits Cash
Rs 8,400. Nothing asks and nothing flags: `detectors.SLICE_4_DETECTORS` is
`(vendor_switch,)` and no detector compares an entry against one posted a second
earlier.

This is **not** filed as a defect. Whether a second identical bill on the same
day is a slip or a real second payment is an owner decision. The number is
proved (`test_the_same_typed_entry_submitted_twice_creates_two_drafts_and_two_vouchers`);
the judgement is left where it belongs.

Item 13, double-click: two POSTs fired from two threads at one barrier give the
same result. `HTTPServer` is single-threaded, so the two requests serialise
inside the handler and never interleave — the duplicate needs **no race**, which
makes it worse and not better. A lock would close nothing.

## Where the invariant HELD

One operation id, one voucher — proved by count, never by `pytest.raises` alone:

- the same operation id twice at `write_voucher`: `DuplicateOperation`,
  `list_our_vouchers` unchanged at 1, trial balance unchanged
- the real connector refuses **before** the import envelope goes out
  (import count stays at 1)
- both backends refuse identically
- a replayed `pipeline.post`: one voucher, one `posted` row, 41 vouchers in the
  register (40 history + ours)
- retry after `WRITE_OUTCOME_UNKNOWN`: asked twice, wrote once
- a reply dropped after the import landed: retry never reaches the import
- a read-back that raises mid-write: one voucher, retry refused
- replaying the `/answer` that posted an entry: 503, one voucher, books unmoved
- the same answer twice, and two different answers: 400, one answer recorded,
  the ledger leg does not move
- a stale draft id, and an evicted one (past `DRAFT_LIMIT` = 200): "expired",
  nothing posted
- `/reverse` replayed: `reversed` once, `not_found` once, books unmoved
- `/reverse-all` confirmed twice: the batch id is popped, the second says "had
  no preview", one `bulk_reversed` row
- a dismissal replayed: one entry in `Draft.dismissed`, one log row
- two vouchers wearing one marker: every path refuses, count stays at 2

## Three defects found. None fixed here.

Each is an `xfail(strict=True)` paired with a passing test pinning the measured
behaviour, in the idiom `tests/test_adversarial_write_path.py` established.

### I1 — a reversed operation id can be written again

`accountant/pipeline.py:456` (`client.write_voucher` inside `post`), guard at
`accountant/tallyio/fake.py:170` and `accountant/tallyio/real.py:2381`.

C5 asks Tally whether the marker is present. After a reversal it is not, so the
guard passes and the same operation id is written a second time against a
**different Tally id**. One identity, two vouchers over the life of the books,
two `posted` rows in the audit log. The trial balance ends exactly where a
single posting leaves it, which is why nobody notices: the money is right and
the identity is not.

Reachable from a browser: post → undo → re-submit the form that posted it.

```
test_an_operation_id_that_was_reversed_is_never_written_again
E   Failed: DID NOT RAISE DuplicateOperation
tests/test_idempotency.py:818
```

`docs/ARCHITECTURE.md` §7 and `accountant/tallyio/client.py` both say the
operation id **is** the identity — reads, duplicate detection and reversal match
on it and nothing else. An identity reusable after a delete is a slot, not an
identity.

### I2 — a duplicate refusal is recorded as an unknown outcome

`accountant/pipeline.py:484-499`.

`DuplicateOperation` is raised **before** any import goes out, on both backends,
so at that instant there is positive evidence that the attempt wrote nothing.
`pipeline.post`'s `except BaseException` arm records `write_outcome_unknown`
anyway — the row whose whole meaning is "a voucher may exist and must be checked
by hand".

```
test_a_duplicate_refusal_is_never_recorded_as_an_unknown_outcome
E   AssertionError: assert 'write_outcome_unknown' not in
E     ['write_attempted', 'write_outcome_unknown']
tests/test_idempotency.py:896
```

`accountant/tallyio/real.py` argues the opposite direction at length — an
UNKNOWN must never be flattened into a failure. The mirror costs less and is
still untrue: it sends somebody to look in Tally for a voucher we know we did
not write, and it dilutes the row that means a person really is needed.

### E1 — a well-formed answer from something that is not Tally reads as empty books

`accountant/tallyio/real.py:1179` (`parse_read_response`).

`parse_read_response` refuses a response carrying an error tag, and `parse_xml`
refuses one that will not parse. **Nothing checks the document is a Tally
response at all.** An HTML 404 page, a proxy sign-in page and an unrelated
service's XML are all well formed, carry no error tag, and read as:

```
parse_companies        ()
parse_ledger_names     ()
parse_closing_balances {}
parse_vouchers         VoucherPage(exported=(), skipped=0, company=None)
```

That is the collapse `accountant/tallyio/factory.py` forbids in writing: "your
books are unreachable" becoming "your books are empty".

```
test_a_well_formed_answer_from_something_else_is_never_read_as_books
E   Failed: DID NOT RAISE TallyResponseError   (× 3 parametrised bodies)
```

**Honest severity: low today, and defended by accident.** Two layers above the
connector catch the common shape, and both do it with the same arithmetic — is
our company in the list Tally named? A non-Tally body yields an empty list, our
company is not in an empty list, so `real_tally` refuses at startup and
`Runtime.confirm_company` refuses on every request. Nobody decided the answer
was not Tally. The write path is separately protected: a non-Tally body parses
as `created=0` and `write_voucher` refuses on `created < 1` — asserted in
`test_a_write_against_something_that_is_not_tally_still_creates_nothing`, which
also records that the duplicate pre-check does **not** stop that write.
Anything reading the connector without those two layers — `bootstrap`, a script,
a future caller — gets "this company is empty" and believes it.

## One observation, below the defect line

A replayed handover appends a **second** `handed_over` row for one entry.
`/dismiss` has an explicit already-done guard for exactly this reason ("a log
that gains a row every time somebody reloads is a log nobody can count anything
in"); the handover branch of `/answer` has none. No books move. Pinned in
`test_handing_over_twice_posts_nothing_and_never_reports_a_valid_outcome`.

## Item 15 and 16 coverage

Every failure case asserts the full bundle — no unsafe retry (import envelope
count), a durable audit row naming the operation id, an explicit named state,
a truthful report, and no `posted` row / no COMPLETED batch. The checklist is
one helper, `refused_safely`, because a failure path satisfying four of five is
not a safe failure path.

Shapes covered: `LINEERROR`, `ERRORMSG`, `EXCEPTION`, the measured bare
`<RESPONSE>Unknown Request, cannot be processed</RESPONSE>`, a refusal sitting
**beside** real data, empty body, whitespace, truncated XML, plain text, half a
voucher, a voucher that lost its ledger entries, HTTP 500, an oversized body,
`created=0`, `altered=1`, `ignored=1`, `errors=1`, `exceptions=1`, a write that
**landed and was reported as failed**, `UNKNOWN_OUTCOME`, `MALFORMED_RESPONSE`,
a register answering for a different company (`WRONG_COMPANY`), a company list
naming only someone else, an empty company list, two vouchers wearing one
marker, connection refused, timeout, and a connection dropped after the request
was sent.

Two controls keep the refusals from being vacuous: a company that really is
empty still reads as empty, and a company that really has a voucher still reads
as having one.
