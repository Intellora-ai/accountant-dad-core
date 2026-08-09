# Reversal and recovery: owner items 17-21

Run date 2026-08-10. Branch `closure/flag-cap-and-truth`.
Files written: `tests/test_reversal_recovery.py`, `tests/test_contract_differences.py`,
this report. No source file was touched.

---

## 1. Counts

| owner floor | required | delivered | where |
|---|---|---|---|
| backup / reversal | >= 15 | **19** | `tests/test_reversal_recovery.py`, item 17 section |
| reconciliation | >= 15 | **17** | same file, item 19 section |
| contract differences | >= 15 | **60** | `tests/test_contract_differences.py` (30 functions, 60 parametrised cases) |
| batch row key (item 18) | not floored | 8 | same file, item 18 section |
| retry behaviour (item 20) | not floored | 14 | same file, item 20 section |

Collected: 58 in `test_reversal_recovery.py`, 60 in `test_contract_differences.py` = **118**.

Result of my two files: **109 pass, 9 fail.** All nine failures are deliberate
defect claims, described in section 3.

Full suite, `COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider`:
**1752 passed, 13 failed, 5 xfailed, 119.6 s.**
Nine of the thirteen are mine. The other four belong to work landing in the same
tree from other agents and are listed in section 5.

`ruff check`, `ruff format --check` and `pyright --strict` are clean on both new
files.

---

## 2. Item 17 — the backup gate, enumerated

Every path in the repository that can remove a voucher, what gates it, and the
test that proves it.

| # | path | gate | evidence | test |
|---|---|---|---|---|
| 1 | `FakeTally.reverse_by_operation_id` | `co.backed_up` | `fake.py:234` | `test_the_fake_connector_refuses_to_delete_without_a_recorded_backup` |
| 2 | `RealTally.reverse_by_operation_id` | `self._backups.has_backup` | `real.py:2467` | `test_the_real_connector_refuses_to_delete_without_a_recorded_backup` — also asserts **zero requests left the connector** |
| 3 | `pipeline.reverse_operation` | inherited from the connector | `pipeline.py:590` | `test_the_verified_doorway_refuses_without_a_recorded_backup` |
| 4 | `pipeline.reverse` | delegates to (3) | `pipeline.py:636` | `test_the_draft_level_reverse_refuses_without_a_recorded_backup` |
| 5 | `reversal.preview` | explicit `client.backed_up` | `reversal.py:292` | `test_preview_refuses_before_it_even_reads_the_candidate_list` — proves the order, by counting `list_our_vouchers` calls |
| 6 | `reversal.execute` → `_drive` → `_classify` | inherited | `reversal.py:357` | `test_a_confirmed_batch_deletes_nothing_when_the_backup_record_is_withdrawn` |
| 7 | `reversal.resume` → `_drive` | inherited | `reversal.py:641` | `test_a_resume_deletes_nothing_when_the_backup_record_is_gone` |
| 8 | `reversal.reconcile` | **none, by design** — read-only | `reversal.py:534` | `test_reconciliation_needs_no_backup_because_it_deletes_nothing` |
| 9 | `python -m accountant.tallyio --reverse-all` | via (5); no `--backed-up` means an empty `RecordedBackups` | `__main__.py:129` | `test_the_command_line_refuses_without_the_backed_up_flag_and_touches_nothing` |
| 10 | `POST /reverse` | via (3) | `web/app.py`, `do_POST` | `test_the_web_single_undo_refuses_when_the_backup_record_is_gone` (503, register intact) |
| 11 | `POST /reverse-all` preview | via (5) | `web/app.py`, `do_POST` | `test_the_web_undo_everything_preview_refuses_when_the_backup_record_is_gone` |
| 12 | `POST /reverse-all` confirm | via (6) | `web/app.py`, `do_POST` | `test_the_web_undo_everything_confirmation_removes_nothing_without_a_backup` |

**No ungated delete path was found.** The enumeration itself is held in place by
three AST tests rather than by memory:

- `test_the_connectors_delete_has_exactly_one_caller_in_the_whole_package` —
  across all of `accountant/**/*.py`, `reverse_by_operation_id` is called from
  exactly one place: `pipeline.reverse_operation`.
- `test_the_verified_doorway_has_exactly_the_three_callers_this_file_covers` —
  `reverse_operation` is called from exactly `pipeline.reverse`,
  `reversal._classify` and `web/app.py::do_POST`.
- `test_the_batch_entry_points_are_reached_from_exactly_two_operator_surfaces` —
  `reversal.preview` and `reversal.execute` are each called from exactly the CLI
  and the web app; `reversal.resume` has no shipped caller at all.

A fourth surface appearing in any of those sets fails the test, which is how a
new delete path becomes a new backup test instead of a silent gap.

**One classification note, not a defect.** When the backup record disappears
between confirmation and execution, the voucher lands in `UNKNOWN_OUTCOME`
rather than `PRECHECK_REFUSED`, because `_classify` sees only "an exception came
out of `reverse_operation`" and cannot tell a client-side gate from a dropped
socket. That is the documented fail-closed reading (`reversal.py:354`, "anything
else at all → UNKNOWN_OUTCOME") and it errs toward caution: the batch stops,
nothing further is deleted, and a later `reconcile` reads the voucher as still
present and returns it to `NOT_ATTEMPTED`. Recorded here because it is a place
where the state name is less precise than it could be, and a one-line fix exists
if the owner wants it (catch `CompanyNotBackedUp` in `_classify` and map it to
`PRECHECK_REFUSED`).

---

## 3. Defects found

### D1 — CRITICAL for the double's credibility. `FakeTally` accepts seven vouchers `RealTally` refuses

**Where:** `accountant/tallyio/fake.py:139` (`write_voucher`) versus
`accountant/tallyio/real.py:871` (`_check_writable`) and
`accountant/tallyio/real.py:842` (`check_amount_is_paise`).

**Failing test:** `tests/test_contract_differences.py::test_both_backends_refuse_the_same_unwritable_voucher`
— 7 parametrised cases, all failing.

W6 was fixed by adding the chart lookup to `fake.py:158-168`. The other five
clauses of `_check_writable` and the type check in `check_amount_is_paise` were
never mirrored. Measured, both backends driven with the same voucher:

| voucher | FakeTally | RealTally |
|---|---|---|
| a leg naming no ledger (`credit_account=""`) | **ACCEPTED** | `TallyDataError` |
| `amount_paise = 0` | **ACCEPTED** | `ValueError` |
| `amount_paise = -500` | **ACCEPTED** | `ValueError` |
| `amount_paise = 1000.5` | **ACCEPTED** | `TallyRejected` |
| `amount_paise = True` | **ACCEPTED** | `TallyRejected` |
| debit and credit the same ledger | **ACCEPTED** | `ValueError` |
| `gst_paise = 1800` | **ACCEPTED** | `ValueError` |
| ledger absent from the chart | `TallyDataError` | `TallyDataError` ✔ |
| ledger differing only in case | `TallyDataError` | `TallyDataError` ✔ |

Exact failure, empty-leg case:

```
AssertionError: FakeTally ACCEPTED a_leg_naming_no_ledger and RealTally refused
it with TallyDataError: refusing to write operation 'ad_...' to 'Demo Co': the
ledger(s) '' do not exist there. ... The register now holds 1 voucher(s) of ours
and the trial balance is {'Purchases': 100000, '': -100000}.
```

There is now a ledger named the empty string in the books of the double that
every test in this repository runs against.

This is `fake.py`'s own stated failure mode: "a double that makes an easier call
than the thing it stands in for does not merely fail to catch a bug; it issues
an alibi." `pipeline.post` will not send one of these today, but the alibi is
the point — a test that drives `client.write_voucher` and shows an invalid
voucher being handled is showing what the double did, not what the connector
would do.

**Smallest fix:** call `real._check_writable(voucher)` from
`FakeTally.write_voucher`. It is a module-level function in `real.py`, which
`fake.py` already imports from, so the dependency direction does not change.

**Warning before fixing:** this will refuse writes that existing tests currently
make. Worth running the whole suite immediately after the one-line change rather
than alongside anything else.

### D2 — LOW. The ambiguity refusal is a different exception class on each backend

**Where:** `accountant/tallyio/fake.py:216` raises the bare `TallyDataError`;
`accountant/tallyio/real.py:2248` raises `AmbiguousMarker`
(defined `real.py:341`, a `TallyDataError` subclass).

**Failing test:** `tests/test_contract_differences.py::test_the_ambiguity_refusal_is_the_same_exception_class_on_both_backends`

```
AssertionError: the ambiguity refusal is TallyDataError on FakeTally and
AmbiguousMarker on RealTally, so a caller branching on the class is tested
against a backend that cannot produce it
```

`fake.py:23-27` records the agreement — "raise the SAME `TallyDataError`, worded
the same way, so one assertion holds both backends" — and it was true when
written. `AmbiguousMarker` was added afterwards, explicitly "so a caller can
branch on the ambiguity without reading the English", and the fake was not moved
with it. `RealTally._prove_it_is_ours` already branches on the class
(`real.py:2326`).

The failure mode is loud rather than silent (an `except AmbiguousMarker` around
the fake simply does not catch), so this is low severity. The wording is
identical and is separately asserted by
`test_the_ambiguity_refusal_is_worded_the_same_on_both_backends`, which passes.

**Smallest fix:** `from accountant.tallyio.real import AmbiguousMarker` in
`fake.py` and raise that at line 216.

### D3 — MEDIUM, owner decision needed. `reconcile()` reports success when it settled nothing

**Where:** `accountant/reversal.py:596-599` sets `reconciled=True` on every
return path, including the one where every read raised.

**Failing test:** `tests/test_reversal_recovery.py::test_a_resume_writes_nothing_more_when_the_reconciliation_settled_nothing`

```
AssertionError: a resume gated on a reconciliation that reconciled nothing
removed 6 further voucher(s) from a company holding one voucher whose fate is
unknown
```

The module says two things that cannot both be true:

- `reversal.py:547` — "If the read itself fails, the voucher stays unknown — a
  reconciliation that cannot read has not reconciled anything."
- `reversal.py:629-632`, the gate `resume` enforces — "call reconcile() first so
  every unknown outcome is settled by a read **before anything else is
  written**."

Measured: 10 vouchers, voucher 4 drops the connection, batch stops at
`UNKNOWN_OUTCOME`. A `reconcile` in which every read also fails leaves voucher 4
unknown — correctly — and still returns `reconciled=True`. An approved `resume`
then passes the gate, skips voucher 4 (correctly, it is not in `RETRYABLE`) and
deletes vouchers 5-10. Nine of ten are gone from a company where one voucher's
fate is unknown.

The three owner rules are all still honoured: the unknown is not treated as a
rejection, transport success is not treated as accounting success, and the
uncertain write is not retried. What is violated is the batch's own headline
claim — that it stops at the first unresolved voucher — and the gate's stated
meaning.

**Two candidate fixes, and this is an owner call:**

1. Do not set `reconciled` when any `UNKNOWN_OUTCOME` or `REQUEST_SENT` survives
   the pass. A resume then needs a reconciliation that actually worked.
2. Leave the flag alone and add the question the gate's message already claims
   to ask: refuse a resume while an unsettled unknown remains.

The test asserts only the consequence — no further deletes — so either fix
satisfies it. If the owner decides the current behaviour is intended (an
approved operator may finish the rest, knowingly), the correct action is to
delete this test and change the two sentences in `reversal.py` so the code and
the prose agree.

---

## 4. Differences that are findings, not defects

### F1 — the connector reports a company it cannot see as an empty company

`RealTally` has no company-existence check anywhere. Whatever the transport says
about a company name is what the connector reports; `FakeTally` answers from its
own company list and raises `KeyError: no such company`.

The sharpest instance needs no assumption about any gateway:
`RealTally.backed_up("Ghots Co")` returns `True` with **zero round trips**, purely
from the operator's declared list — asserted by
`test_the_connector_answers_backed_up_for_a_company_with_no_round_trip_at_all`.

Consequence at the layer an operator sees: a typo in `--company` produces a
batch with zero candidates, a `COMPLETED` state, "nothing of ours in 'Ghots Co'"
and exit 0. On the double the same typo raises. This is the W4/W6 alibi shape
running the other way — the double being HARDER than the thing it stands in for,
so a test written against it shows a refusal that does not exist.

**Why it is a finding and not a defect:** `factory.real_tally` lists the open
companies and refuses a name that is not among them before any client is handed
out, and both operator surfaces construct their client through it. Pinned by
`test_the_factory_is_what_closes_that_gap_on_both_operator_surfaces`.

The residual exposure is direct `RealTally(...)` construction, which only tests
and future code do. Worth a line in the connector's docstring saying the company
check lives in the factory, so the next caller does not have to rediscover it.

### F2 — the missing-ledger refusal carries different advice on each backend

Same class, same identifying opening sentence (asserted). `RealTally` adds
"Tally will not create them for us, so the import would be rejected or silently
ignored. Create them in Tally first." The fake stops after the opening. The
decision is identical; only the helpfulness differs. Not worth a source change
unless someone is diffing messages.

### F3 — the ambiguity refusal's locator list differs, necessarily

The fake names draft ids and amounts; the connector names `VCHTYPE` and
`MASTERID`. Both name two locators separated by one `;`, which is what a person
needs to find them. Asserted as a structural property rather than as text.

---

## 5. Full-suite failures that are not mine

Four, all from work landing in the same tree while this ran:

- `tests/test_company_routes.py::test_a_bulk_reversal_previewed_for_another_company_never_reverses_it`
- `tests/test_company_routes.py::test_a_draft_built_for_another_company_is_never_drawn_under_ours`
- `tests/test_company_unicode.py::test_the_same_company_typed_in_two_encodings_is_never_two_companies`
- `tests/test_reverse_all_cli.py::test_only_the_command_imports_above_the_connector_boundary` —
  now fails because `accountant/tallyio/factory.py` has acquired an import of
  `accountant.memory.identity`, which is above the connector boundary. That is a
  pre-existing guard catching a change made outside this task; whoever added the
  import owns it.

---

## 6. What is NOT proven

Evidence class **FAKETALLY** and **SIMULATOR**.

- No licensed TallyPrime has seen any of this. `TallySim` is a program in this
  repository that answers the envelopes `real.py` builds; agreement with it is
  agreement between two of our own programs.
- Nothing here is evidence about a real Tally's delete semantics, its
  Educational-mode date refusals, its behaviour for a company that is not open,
  or what happens when two clients touch one company at once.
- The web tests drive a real HTTP server against an in-memory double. They prove
  the route refuses and returns 503; they prove nothing about how a browser
  renders it.
- The trial-balance claims are exact in paise against `FakeTally`'s arithmetic
  and the simulator's closing-balance encoding, not against Tally's.
- Nothing was injected into any component other than `FakeTally` subclasses and
  the simulator's transport.
