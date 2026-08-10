# Full reversal history — PR-5, owner decision Q8 = A

Written 2026-08-10 on branch `phase8/reversal-history`, cut from `origin/main`
at `f22eace`.

- The **answer** is in [`docs/OWNER_DECISIONS.md` §Q8](../docs/OWNER_DECISIONS.md).
- The **counts and gates** are in [`artifacts/phase8_scope.md` §Q8](./phase8_scope.md).
- The **tests** are `tests/test_reversal_history.py`, 39 of them.

Every number below was measured on this branch, from this worktree, with the
provenance assertion the project requires:

```
from pathlib import Path
import accountant
assert str(Path(accountant.__file__).resolve()).startswith(str(Path.cwd().resolve()))
```

    PROVENANCE OK
    .../scratchpad/wt-p8-reversal/accountant/__init__.py

**Permitted labels, and nothing else:**

```
PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED
HUMAN_ACTION_REQUIRED · OWNER_DECISION_REQUIRED · OPTIONAL_HUMAN_INPUT
BLOCKED_ON_HUMAN_EVIDENCE · NOT_IMPLEMENTED · SOURCE_UNVERIFIED
NOT_SELECTED · INCOMPLETE
```

---

## 1. The acceptance table, measured

| Requirement | Required | Measured | Result |
|---|---:|---:|---|
| events preserving all seven fields | 20/20 | 20/20 | PASS |
| overwritten | 0 | 0 | PASS |
| missing actors | 0 | 0 | PASS |
| missing timestamps | 0 | 0 | PASS |
| missing scopes | 0 | 0 | PASS |
| missing reasons | 0 | 0 | PASS |
| unrecorded transitions | not asked for | 0 | PASS |

`tests/test_reversal_history.py::test_twenty_reversal_events_each_preserve_all_seven_fields`.

The counts are read back out of SQLite, not counted off the objects the run held
in memory. A history that only exists in the process that wrote it is not a
history.

`unrecorded transitions` is not in the owner's table. It is here because the
five rows above can all be zero while the log is still wrong — see §5.

---

## 2. The starting point, verified rather than relayed

The audit that preceded this work reported:

> `action_log` has 11 columns and no `actor` column and no previous-state
> column. Two of the seven fields do not exist.

**Checked against the live schema on this branch. The claim is correct.**
`accountant/memory/store.py` defined `action_log` with exactly eleven columns:

```
company_key · ts · action · outcome · reason · run_id
backend · operation_id · voucher_id · vendor_id · detail
```

Mapping the seven required fields onto them:

| Required field | Column before this work |
|---|---|
| reason | `reason` |
| timestamp | `ts` |
| company/document scope | `company_key` + `operation_id` |
| new state | `outcome` |
| evidence | `detail` |
| **actor** | **absent** |
| **previous state** | **absent** |

`tests/test_reversal_history.py::test_the_action_log_was_missing_exactly_two_of_the_seven`
pins the eleven by name and in order, so this is a measurement in the repository
rather than a number carried in from a report.

One correction to the audit's arithmetic, not to its finding: the scope needs
`operation_id` and not `voucher_id`. `voucher_id` is Tally's own id, which the
batch never learns; the operation id is the handle the register, the log and
Tally all agree on, and it is what `reversal.py` already wrote.

---

## 3. What was added, and why each column earns its place

Three nullable `TEXT` columns on `action_log`, and nothing else:

| Column | Why |
|---|---|
| `actor` | field 4 of the seven; absent before |
| `previous_state` | field 1 of the seven; absent before |
| `batch_id` | groups a batch's events without parsing `detail` |

`batch_id` is the one addition the owner's decision does not name, so it is
called out rather than slipped in. Without it the only way to know which batch
an event belongs to is to parse the string `batch <id>; moved {...}` out of the
evidence field. `BATCH_ACTION` exists in this module precisely so the log can be
filtered "without parsing prose", and re-introducing prose-parsing to rebuild the
chain would contradict it. It is additive, nullable and carries no meaning of
its own.

**A schema change is not an authentication dependency.** `dependencies = []` in
`pyproject.toml` is unchanged and
`tests/test_reversal_history.py::test_no_authentication_dependency_was_added`
asserts it. No second event store was created: `action_log` is the one table,
and it stays out of `_DELETES` so `forget()` cannot erase it.

---

## 4. The actor, and its exact limit

Two values, and no third:

```
accountant_dad   system-generated actions
operator         actions answered through the UI
```

```
authenticated user identity = NOT_IMPLEMENTED
actor provenance            = coarse-grained system/operator
```

**`operator` is not an authenticated user identity.** It records that a person
was in the loop, not which person. There is no login, no session and no user
table behind it. Anyone reading a row that says `operator` may conclude that a
human hand was on the control and nothing more.

If authenticated identity is ever needed:

```
OWNER_DECISION_REQUIRED: approve an authenticated identity subsystem
```

That is **H-05** in the human-required table. Nothing in this branch builds it,
and an agent may not decide to.

The limitation is recorded in three places so it cannot be lost by reading only
one of them: the module docstring of `accountant/reversal.py`, the docstring of
`Actor` in `accountant/schema.py`, and this file.
`test_the_limit_of_the_actor_field_is_recorded_in_the_code_itself` asserts the
first two.

### Where each label lands, measured

`ActionLog.__post_init__` refuses any actor that is not one of the two literals
or the explicit `NOT_RECORDED` marker. Across the 20-event run:

| Transition | Actor | Why |
|---|---|---|
| `preview -> confirmed` | `operator` | a person confirmed the exact candidate list |
| `partial_failure -> reversing` (resume) | `operator` | `approved=True` is a person's answer after the refusal |
| `confirmed -> reversing` (execute) | `accountant_dad` | the system starting the work it was confirmed to do |
| every per-voucher transition | `accountant_dad` | nobody chose those one at a time |
| every reconciliation settlement | `accountant_dad` | a read the system performed |
| every batch settling | `accountant_dad` | derived from the voucher states |

---

## 5. The part that is actually hard

Checking that the recorded events carry seven fields only proves that the events
somebody **remembered to write** are well-formed. It cannot see the one that was
never written.

So the history is replayed. For each scope — one batch, or one voucher inside
one batch — the events must run from the state `preview` created it in, link by
link, to the state the object is in now:

```
preview  ->  confirmed  ->  reversing  ->  unknown_outcome
         ->  partial_failure  ->  reversing  ->  completed
```

and for the voucher whose connection dropped:

```
not_attempted -> request_sent -> unknown_outcome
              -> not_attempted -> request_sent -> reversed_verified
```

A transition with no event leaves the replay short of where the object actually
is. An event whose `previous state` does not match where the chain had reached
is a reordered, duplicated or invented row. `reversal.audit` reports both as
gaps, naming the scope.

**Measured, with the recording call deleted from `accountant/reversal.py`:**

```
events=15  complete=15  gaps=5
  GAP b1/ad_04ff…: a transition happened that no event records —
                   the recorded chain reaches request_sent and
                   reversed_verified is unaccounted for
  … four more, each naming its operation id
```

Fifteen events, **all fifteen carrying all seven fields**, and the log is still
wrong. That is the number this section exists for.

`REVERSING` is now a state the batch actually occupies. Before this work it was
a name in the enum that nothing ever set, so a replayed history would have
jumped from `confirmed` straight to whatever the batch rested at, leaving out
the interval in which the vouchers were being deleted.

---

## 6. The migration: never invent what nobody recorded

A database written before these columns existed has them added by
`MemoryStore._migrate`, additively, with **every existing row left NULL**. NULL
reads back as `NOT_RECORDED`.

```
a pre-existing row's actor      = NOT_RECORDED
a pre-existing row's prev state = NOT_RECORDED
```

`NOT_RECORDED` counts as **missing** in the completeness check. It is an honest
description of a row, not a value that satisfies the requirement — if it passed,
every legacy row would report seven of seven.

**No row is ever back-filled with `accountant_dad`.** That is the cheapest
possible fix and it would make every historical row a false record of the same
size, including the ones a person took. An explicit "we did not record this" can
be reasoned about; a plausible guess cannot be told apart from evidence. This is
the same rule, and the same reason, as `raw_subject` under D-05.

Measured, against a file built with the eleven-column `CREATE TABLE` verbatim:

| Check | Measured |
|---|---|
| legacy row's actor | `NOT_RECORDED` |
| legacy row's previous state | `NOT_RECORDED` |
| legacy row's actor equals `accountant_dad` | no |
| legacy row reports `missing` | `("previous state", "actor")` |
| legacy row's five other fields | intact |
| `actor`/`previous_state` columns in the file after opening | still SQL `NULL` |
| opening the same file twice changes any row | no |

`test_a_row_written_before_the_columns_existed_reads_as_unrecorded`,
`test_a_legacy_row_is_never_mistaken_for_a_system_action`,
`test_the_migration_does_not_rewrite_any_row`.

---

## 7. Append-only: proved, not assumed

`0 overwritten` is one of the required results, so it is measured rather than
inferred from the table having no primary key.

| Proof | How |
|---|---|
| structural | every SQL string in `store.py` naming `action_log` is an INSERT or a SELECT; no `UPDATE`, no `DELETE FROM`, no `INSERT OR REPLACE`. The same scan finds the lookup tables' real `DELETE`, so a scan matching nothing cannot pass. |
| no primary key | `primary_key_of("action_log") == ()`, so SQLite cannot collapse a repeat |
| two identical events | written twice, read back as two rows |
| positional comparison | all 20 written events present, in order, with identical seven fields |
| `forget()` | 20 events before, the same 20 after |

`vendor_account` and `phrase_account` **do** upsert — deliberately, because a
repeated observation there is a higher count. `action_log` must not, because two
identical decisions are two things that happened. Both shapes exist in the same
file on purpose; the append-only one is the audit trail.

`HistoryAudit.overwritten` is `None` — NOT_MEASURED — when nobody supplied the
written events to compare against, and `whole` is then `False`. An audit
reporting 0 because it never compared anything is the shape of every number this
project has had to strike out later.

---

## 8. The seven guards, each proved load-bearing

Each mutant was injected into the source, the suite run, and the source
restored. A guard whose removal changes nothing is decoration.

| # | Mutant | Result | First test to go red |
|---|---|---|---|
| M1 | an event is written without an actor | RED | `test_twenty_reversal_events_each_preserve_all_seven_fields` |
| M2 | an event is written without a timestamp | RED | `test_an_event_missing_any_one_field_is_refused_by_name[ts-None-timestamp]` |
| M3 | an event is written without a scope | RED | `test_twenty_reversal_events_each_preserve_all_seven_fields` |
| M4 | an event is written without a reason | RED | `test_twenty_reversal_events_each_preserve_all_seven_fields` |
| M5 | a second write overwrites an existing event row | RED | `test_twenty_reversal_events_each_preserve_all_seven_fields` |
| M6 | a legacy row is back-filled with `accountant_dad` | RED | `test_a_row_written_before_the_columns_existed_reads_as_unrecorded` |
| M7 | a state transition happens with no event written | RED | `test_twenty_reversal_events_each_preserve_all_seven_fields`, and the replay named 5 gaps |

```
7 mutants applied · 7 RED · 0 survived
```

M1, M3 and M4 each required removing the write-path guard **as well as**
blanking the field, because `ReversalEvent.demand_complete` refuses an
incomplete event before it reaches the sink. Both halves are therefore
load-bearing, which is the point of injecting them together.

---

## 9. D-29 is untouched

Owner decision D-03, landed in `d1436c2`: one `UNKNOWN_OUTCOME` refuses the
whole batch resume. Six tests pin it. All six pass on this branch:

```
test_a_resume_writes_nothing_more_when_the_reconciliation_settled_nothing   PASSED
test_one_unknown_voucher_blocks_the_resume_of_the_whole_batch               PASSED
test_no_known_voucher_is_deleted_while_one_outcome_is_unknown               PASSED
test_a_second_reconciliation_resolves_the_voucher_the_refusal_named         PASSED
test_a_resume_is_permitted_once_every_voucher_has_a_state_a_read_established PASSED
test_the_exact_evidence_survives_the_refusal_unchanged                      PASSED

6 passed
```

Two design choices exist to keep them that way.

**Batch transitions use a second action name, `bulk_reverse_batch`.** Every
existing reader of this log filters on `action == BATCH_ACTION` and expects one
row shape — one voucher, identified by operation id. Widening `BATCH_ACTION` to
carry batch rows too would have shifted the strides those tests index by
(`rows[::2]`, `rows[1::2]`) and changed row counts they assert exactly.

**A refused resume still writes nothing.** All three gates in `resume` run
before `_drive`, so nothing is recorded on the refusal path.
`test_the_exact_evidence_survives_the_refusal_unchanged` asserts this for
`bulk_reverse`; `test_a_refused_resume_still_writes_no_event_of_any_kind`
asserts the same over both names, so the new one cannot open a hole beside the
old assertion.

### One behaviour did change, deliberately

`reconcile` used to write nothing at all. It now appends to `action_log`.

Settling an unknown **changes a voucher's state** — `unknown_outcome` becomes
`not_attempted` or `reversed_verified` — and a state change with no event is
exactly the hole this work exists to close. A voucher settled silently reads
afterwards as though it were never attempted.

What has not changed is the claim `reconcile` is built on: it writes nothing
into the customer's books.

| Check after a reconciliation | Measured |
|---|---|
| deletes sent to the connector | 0 |
| trial balance moved | no, to the paise |
| vouchers of ours still in the register | unchanged |

`test_reconcile_still_writes_nothing_into_the_customers_books`. And a
reconciliation whose reads all failed settled nothing, so it records nothing —
`test_a_reconciliation_that_could_not_read_records_no_transition`.

---

## 10. The 20 events, itemised

One company, two batches, four vouchers. Every event from a real run.

| # | Scope | Transition | Actor |
|---:|---|---|---|
| 1 | batch b1 | `preview -> confirmed` | operator |
| 2 | batch b1 | `confirmed -> reversing` | accountant_dad |
| 3-4 | voucher 1 | `not_attempted -> request_sent -> reversed_verified` | accountant_dad |
| 5-6 | voucher 2 | `not_attempted -> request_sent -> reversed_verified` | accountant_dad |
| 7-8 | voucher 3 | `not_attempted -> request_sent -> unknown_outcome` | accountant_dad |
| 9 | batch b1 | `reversing -> unknown_outcome` | accountant_dad |
| 10 | voucher 3 | `unknown_outcome -> not_attempted` (settled by a read) | accountant_dad |
| 11 | batch b1 | `unknown_outcome -> partial_failure` | accountant_dad |
| 12 | batch b1 | `partial_failure -> reversing` | operator |
| 13-14 | voucher 3 | `not_attempted -> request_sent -> reversed_verified` | accountant_dad |
| 15-16 | voucher 4 | `not_attempted -> request_sent -> reversed_verified` | accountant_dad |
| 17 | batch b1 | `reversing -> completed` | accountant_dad |
| 18 | batch b2 | `preview -> confirmed` | operator |
| 19 | batch b2 | `confirmed -> reversing` | accountant_dad |
| 20 | batch b2 | `reversing -> completed` | accountant_dad |

Voucher 4 is never attempted in the first pass — the batch stops at voucher 3 —
and correctly has no events until the resume. Batch b2 has no candidates at all
and still records its own three transitions; a batch with nothing to do is
exactly the history that would go missing if only vouchers were recorded.

States exercised: 6 of 7 batch states and 4 of 8 voucher states.
`CRITICAL_FAILURE`, `PRECHECK_REFUSED`, `EXPLICIT_REJECTION`, `WRONG_MOVEMENT`
and `READBACK_FAILED` are not in this run; they are covered as behaviour by
`tests/test_bulk_reversal.py` and `tests/test_reversal_recovery.py`, and their
event recording goes through the identical `_record` call, but **their presence
in a recorded history is `NOT_MEASURED`**.

---

## 11. Suite

Measured three times, because the branch was rebased onto PR-1 between the
first two and reviewed between the last two. A count carried across a rebase is
a count measured on a different tree.

```
origin/main f22eace            2295 passed, 5 xfailed   the original baseline
  + PR-5, before the rebase    2334 passed, 5 xfailed   +39 = the new test file

branch base, PR-1 merged       2401 passed, 5 xfailed   the rebased baseline
  + the PR review round        2408 passed, 5 xfailed   +7  = the new guards
```

`tests/test_reversal_history.py` is 46 tests: 39 for the seven fields, the
migration and the append-only proof, and 7 added by the review round.

```
ruff check .            All checks passed
ruff format --check .   all files already formatted
pyright                 0 errors, 0 warnings
validate_project_truth  30 checks, 30 passed, 0 failed
provenance assertion    PASS, from wt-p8-reversal
D-29, the six tests     6 passed
```

---

## 12. PR review, 2026-08-10 — two defects in this branch's own diff

Both were found by review, both were verified against the code before being
acted on, and both were real.

### Defect 1 — CRITICAL. The command line recorded nothing.

`accountant/tallyio/__main__.py` passed no `log=` to either `reversal.confirm`
or `reversal.execute`. Both signatures default `log` to `None`, and both
recorders return immediately on `None`, so:

```
python -m accountant.tallyio --reverse-all --yes   audit rows written: 0
the same operation through the web app             audit rows written: all 7 fields
```

**A destructive bulk reversal wrote no audit trail at all.**

§1 of this document said `20/20` and `0 unrecorded transitions`. Both numbers
were true and **both were measured on the web path only.** The CLI was never in
the fixture, so the transitions it caused were outside every scope the replay
walked. That is the exact failure §5 exists to catch, and it slipped through
because `audit` can only replay scopes the caller hands it.

**Fix.** A `--audit-log PATH` flag, opened as a `MemoryStore` and passed to both
calls, closed in a `finally`. Required whenever `--yes` is passed and refused
otherwise, before the connection is opened — the same fail-closed shape as
`--backed-up`. **There is no default path**, deliberately: where a customer's
audit trail lives is not a decision this command may make on its own, and a
default would put the file somewhere nobody chose while looking considered.

### Defect 2 — MAJOR. An audit row claimed a backend that did not act.

`accountant/web/app.py` passed `backend=type(live.client).__name__` into
`reversal.confirm`. Confirming is a local act — a person said yes to a list
already on their screen — and `backend` on an `action_log` row answers "which
Tally is this row evidence about". The row asserted that a connector produced
evidence for an operation it never saw.

Same defect class as a provenance tag naming a reader that did not extract the
value. **A false attribution in an audit trail is worse than a missing one,
because the missing one is visible.**

**Fix.** The `backend` parameter is gone from `confirm` entirely. Emptying it
would have left the mistake one keystroke away; removing it means a caller
cannot pass what the signature does not accept. `_record_batch` now defaults
`backend=""`, and that default is the honest answer for a transition no
connector took part in.

### The guards, which matter more than the fixes

Neither defect was caught by anything the repository had. Coverage did not fail
— the CLI code ran. Mutation did not fail — nothing had been deleted. The
seven-field test passed — it only ever looked at one path. **The tests measured
what was tested, and the untested path was invisible to all of them.**

Both defects were *an unenumerated case*, so neither guard enumerates.

| Guard | What it does |
|---|---|
| `test_every_entry_point_that_causes_a_transition_records_it` | Walks every `.py` in the shipped package, resolves the import (both styles), and requires `log=` on every call to `confirm`/`execute`/`resume`/`reconcile`. Lists no callers. |
| `test_the_caller_scan_detects_a_missing_log_before_it_is_trusted` | Feeds the scanner a synthetic module containing the defect and requires it to be found. A structural guard that silently matches nothing passes forever. |
| `test_the_command_line_writes_a_whole_history` | Runs `cli.main` as an operator would, then reopens the named file in a second store and replays the chains. Proves durability, not just that a `log=` was typed. |
| `test_the_command_refuses_to_destroy_anything_it_cannot_record` | 0 vouchers reversed, trial balance unmoved, no file created anywhere. |
| `test_no_locally_decided_transition_names_a_backend` | Structural over `reversal.py`: a function holding no `TallyClient` may not name a backend, with a control asserting the functions that DO hold one are found naming theirs. |
| `test_confirm_cannot_be_handed_a_backend_at_all` | The parameter is absent from the signature and a runtime caller gets `TypeError`, with the same call minus the argument as the control. |
| `test_the_confirmation_row_records_no_backend_and_the_driven_rows_do` | Both halves in one test so neither drifts into the other. |

`preview` is deliberately outside `TRANSITION_FUNCTIONS`: it creates the batch,
and creation has no previous state, so it is not a transition and there is
nothing to record.

### Mutants — 10 applied, 10 RED, 0 survived

The original seven were re-run against the current tree rather than carried
forward from the earlier measurement.

| # | Mutant | Result | Caught by |
|---|---|---|---|
| M1-M7 | the original seven guards | RED ×7 | as recorded in §8 |
| M8 | the CLI passes no log (defect 1, reverted) | RED | the structural scan **and** the CLI end-to-end test |
| M9 | a confirmation names a backend (defect 2, reverted) | RED | the structural scan **and** the behavioural test |
| M10 | **a third entry point nobody enumerated** | RED | the structural scan |

M10 is the one that matters. A new module `accountant/sweeper.py` calling
`reversal.execute(reversal.confirm(batch), client, company_key=company)` with no
log was added — a caller no test, list or docstring mentions. The scan found it
by walking the package.

An existing test, `test_the_batch_entry_points_are_reached_from_exactly_two_operator_surfaces`,
also went red on M10. That is worth stating precisely rather than claiming as
extra credit: it counts operator surfaces, so it catches a *new* caller but
would not have caught either defect here — the CLI was already one of the two
surfaces it knows about, and it says nothing about logging. The dimension it
does not cover is exactly the one the new guard adds.

---

## 13. What is not done, and is not claimed

| Item | Status |
|---|---|
| authenticated user identity | `NOT_IMPLEMENTED` — H-05, `OWNER_DECISION_REQUIRED` |
| which person `operator` was | `NOT_IMPLEMENTED` |
| event recording on a licensed TallyPrime | `NOT_MEASURED` — evidence class FAKETALLY throughout |
| `CRITICAL_FAILURE` / `WRONG_MOVEMENT` / `READBACK_FAILED` in a recorded history | `NOT_MEASURED` |
| actor and previous state on non-reversal `action_log` rows | `NOT_RECORDED` by design — see below |
| `docs/CONTROL_PLANE.yaml` entry for this work | `INCOMPLETE` — not written; the file is held by another agent |
| the web app's own store is on disk | **`NOT_IMPLEMENTED`** — see below |

**The web app's audit trail is in memory.** Found while fixing defect 1 and
recorded rather than worked around. `accountant/web/app.py:738` reads
`store if store is not None else MemoryStore(":memory:")`, and `serve()` passes
no store, so `python -m accountant.web.app` keeps its `action_log` in RAM and
loses it on restart. `config_from_environment` resolves the Tally host, port,
company and backup list, and no path for the store.

So the seven-field history the web path records is durable **within one process
run** and no further. That is a product decision — where the file lives, who
owns it, what happens on upgrade — and not one an agent may take, so nothing
here changes it. The CLI now takes its path as a required flag precisely because
that question has no answer yet. Flagged as a candidate defect for whoever owns
`web/app.py`; it is outside PR-5's scope and is not claimed as fixed.

**Non-reversal rows.** A post, a dismissal or a company mismatch has no state
machine behind it and therefore no previous state. Those rows keep
`NOT_RECORDED` in both new columns rather than being given a manufactured one.
Giving every row an actor would be a larger change with a real chance of
inventing provenance, and Q8 scopes the requirement to reversal events. Recorded
here rather than worked around.

**`allowed_statuses` was NOT widened.** Nothing in this work changed
`docs/CONTROL_PLANE.yaml`. The evidence labels above come from the wider
vocabulary the file's own note reserves for exactly this purpose.
