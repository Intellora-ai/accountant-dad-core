# Deleting a customer's data

Task 13 of the cloud-launch plan. Written 2026-08-11.

## The question this had to answer

A customer must be able to have their data deleted. The obvious version of that
feature is wrong in both directions:

- **Delete everything, including the audit log.** The audit log is the record of
  what we did to a real business's statutory books — what we posted, when, on
  whose instruction, and why. A regulator, an auditor or the customer themselves
  may need it. A "deletion" that destroys it destroys the one document that could
  answer *what did you do to my accounts*.
- **Keep everything and call it deleted.** That is not a deletion feature. It is
  a checkbox, and telling a customer their data is gone when it is not is the
  kind of sentence that becomes a legal problem.

So the answer is not one behaviour. It is three, because there are three
different kinds of row here and they are three different kinds of fact.

## What happens, in plain words

| Thing | What we do | Why |
|---|---|---|
| your sign-in accounts | **closed, not removed** | so *"this account was closed on the 11th"* stays answerable. A row that vanishes and a row that never existed look identical afterwards, and support has to tell them apart |
| your signed-in sessions | **all ended, immediately** | one surviving session is an account that is still open |
| what we learned about your suppliers | **erased** | this is *our* guesswork about *your* books. It is rebuildable from your own Tally at any time, so losing it costs you nothing you cannot get back — and keeping it after you have left is us holding learning about a company that is no longer a customer |
| the chart of accounts we cached, and our bootstrap record | **erased** | same thing, same reason |
| the record of what we did to your books | **kept, and marked as a closed account** | it is the evidence, and the marking is what stops anyone reading it as a live customer |
| your books in Tally | **untouched. We never had a copy.** | `ARCHITECTURE.md` §2 — we never store the customer's books. Delete us tomorrow and your statutory records are complete |

## The audit log is kept, with the supplier names and the amounts

That is an **owner decision**, and this task did not reopen it.

The rows keep `vendor_id` and they keep the amounts that appear in `reason` and
`detail`. A trail with the supplier and the figure stripped out cannot answer
what was done to somebody's accounts, which is the only reason it is being kept
at all — a redacted audit trail costs the same to store and answers nothing.

What was **added** is the marking. Every kept row now reads back with the
customer's `deleted_at` beside it, so nobody can mistake retained evidence for
an active customer.

### The mark is derived, never written

`RetainedAction.tenant_deleted_at` is read off the `tenant` row at the moment
the log is read. It is not a column on `action_log` and nothing updates one.
Two reasons, both load-bearing:

1. **A second copy can disagree with the first.** A stored mark saying "active"
   over a closed account is exactly the false confidence that
   `Observation.identity_evidence` is derived rather than stored to avoid.
2. **`action_log` has no update path and no delete path, anywhere.** A row a
   later write can edit is not an audit row.
   `tests/test_reversal_history.py::test_the_store_has_no_update_or_delete_path_for_the_action_log`
   scans the module source and fails if one appears. Writing the mark would have
   meant breaking the property the whole table exists for, in order to record
   that we were respecting a deletion request.

## The audit log is also the only thing that knows whose books were whose

Nothing in the schema says which companies a tenant owns. `company_key` scopes
the books, `tenant_id` scopes the account, and the only place the two ids appear
on one row is `action_log`.

So a deletion is scoped by reading the trail: *which companies is this customer
recorded as having worked in*. That is a measurement of what happened, not a
claim about what is owned — and it means a deletion can never erase books this
customer was never seen touching.

It is also the sharpest practical argument for keeping the log: **erase it and a
later deletion request could not even be scoped**, because nothing would be left
that knows whose books were whose.

## A company two live customers share is left alone

If two customers are both recorded as having worked in one company key, deleting
one of them **does not** erase that company's learned index. Erasing it would
delete somebody else's learning as a side effect of this person's request —
cross-tenant harm committed by the feature that exists to protect them.

The index goes when the **last live owner** goes. A company shared only with an
already-deleted customer is erased on the second deletion, so two departed
customers do not leave an index nobody owns and nobody can ask to have removed.

An **unattributed** row — one whose `tenant_id` is `NOT_RECORDED`, written
before tenancy existed or by a script with no session — does not count as
another owner. `NOT_RECORDED` means *nobody wrote this down*, not *another
customer*, and treating it as a co-owner would make deletion impossible in every
database written before tenancy.

## Getting back in afterwards: three closures, none relying on the others

```
delete_tenant   revokes every session in the same transaction that closes the account
login           already refuses a user whose deleted_at is set
authenticate    refuses a session whose tenant is not live
```

The first covers every credential that existed at the moment of the deletion.
The second covers signing in again. The third covers a session row that did not
exist then — one opened by a request already in flight, or written by some later
code path that forgot to ask. Each is enough on its own; none is trusted to be.

All three answer **401**, not 403. 403 means *"I know you, and no"*, which would
be a claim about a customer we have just stopped having.

## Only your own data, and the tenant never comes from the request

`POST /delete-my-data` reads the tenant off the credential, through
`current_principal()`, which `Handler._identify` built from the session. There is
no code path that reads a tenant id out of a form.

That is `docs/AUTH.md`'s one rule — **a tenant id is derived from the credential,
never read from the request** — arriving at the most destructive route in the
product. A tenant id taken from this form would let any customer delete any other
customer's data with one edited field.

It is checked twice, on purpose:

- **behaviourally** — a test posts `tenant=<somebody else>` and watches the
  caller's *own* account be the one that closes;
- **structurally** — a test walks the module's AST, finds every `form.get(...)`
  and fails if any of them names a tenant. The behavioural test proves today's
  handler; the scan proves tomorrow's.

## Two steps, and the person who confirms is the person who was shown

Same shape as `/reverse-all`, for a stronger reason. A bulk reversal destroys
vouchers that can be typed again; this closes an account and erases an index.

```
POST /delete-my-data                     measures and writes NOTHING. Shows the
                                         counts and the company keys, and states
                                         what will be kept.
POST /delete-my-data confirm=yes plan=…  executes the plan that was shown.
```

The plan is held against the **user who asked for it**, not against its id
alone. Two colleagues sharing one customer account is the normal shape of an
accounts department: keyed by id alone, colleague A could ask for the preview and
colleague B could post the confirmation, and B would have deleted the account
having been shown nothing at all — with every other check passing on the way
through, because the session is valid, the customer matches and the plan is real.

A plan that is not the caller's is **left in place** rather than consumed. The
person who took it may still be reading it.

## What the running server does afterwards

`CompanyMemory` answers from the store rather than from a cached index, so the
learning really is gone the moment the rows are. But its bootstrap *report* still
says `READY`, and a `READY` report is the app claiming it has read books whose
derived index no longer exists — so the runtime's memory is `invalidate`d when
this company's index was one of the erased ones. From then on every entry is a
question rather than a proposal, which is the truth.

Only when it was erased. A company kept because another live customer shares it
is still legitimately readable, and invalidating it would take that customer's
service away as a side effect of somebody else's request.

## What this does NOT promise

- **Nothing about backups.** Whether backups exist, where they are, how they are
  encrypted and how long they are kept is **D-17** in
  [`DATA_POLICY.md`](./DATA_POLICY.md) §3.6 and is unanswered. Until it is, no
  test here claims a row is gone from anywhere except the primary store, and the
  screen does not tell the customer a copy is gone from a place nobody has
  described.
- **Nothing about a real TallyPrime.** Every test runs against `FakeTally`, so
  all of it is `FAKETALLY` evidence.
- **No retention schedule.** Nothing here deletes anything on a timer. **D-15**
  is unanswered and no period is invented; this is deletion **on request** and
  only that.
- **No account re-opening.** A closed account stays closed. Reinstating one is a
  product decision nobody has made, not a defect.
- **No export.** "Give me a copy of my data" is a different feature and is not
  built. Deleting is offered without it, which is the safe order of the two.

## The policy is data, and it covers the whole schema

`ERASED_BY_DELETION` and `KEPT_BY_DELETION` in `accountant/memory/store.py` are
tuples, not prose, and
`tests/test_data_deletion.py::test_the_deletion_policy_names_every_table_in_the_live_schema`
asserts they cover `table_names()` **exactly**.

A table in neither list fails that test. That is the point: the next table
anybody adds cannot be swept into a customer deletion by accident, and cannot be
left out of one by accident either.

**The operation register is the worked example, and it has now happened.** The
write-once `(company_key, operation_id)` row that closes defect I1 landed with
the idempotency task on 2026-08-11. That test went red on the merge, exactly as
it was written to, and stayed red until the table was named. The answer was
already decided and is now recorded in the tuple: **KEPT**.

Releasing a spent operation id would recreate I1 exactly, because two vouchers
would then share one identity. The vouchers those ids name are in the customer's
**own Tally** and do not disappear because they closed an account here, so an id
freed by a deletion could be minted again for a second voucher that could never
afterwards be told from the first. That is as true after a customer leaves as it
was before.

The erase list is also **the same tuple `forget()` already uses**, rather than a
second copy of it. `forget()` runs on every rebuild and already answers *what of
ours may be dropped and rebuilt from their books*; a deletion asks the same
question. Two lists of that is one list too many, and a test pins them equal.

## Mutants

Each guard was reverted, the tests were watched failing, and it was put back.
`__pycache__` is cleared between mutants: CPython invalidates on `(mtime, size)`,
and a size-preserving change restored inside the same second has already produced
one false verdict in this project.

Eleven mutants, eleven deaths, each killed by `tests/test_data_deletion.py`
alone — the file was run on its own for every one, so no death is borrowed from
a test written for something else.

| # | Mutant | Verdict | Killed by |
|---|---|---|---|
| M1 | `delete_tenant` erases `action_log` as well | DIED | `the_action_log_survives_a_deletion`, +4 |
| M2 | `delete_tenant` does not revoke sessions | DIED | `every_session_of_a_deleted_customer_dies_the_moment_it_happens`, +3 |
| M3 | `delete_tenant` does not close the tenant row | DIED | `the_tenant_row_records_when_the_account_was_closed`, +8 |
| M4 | `delete_tenant` erases a company another live customer shares | DIED | `a_company_two_live_customers_share_is_kept_rather_than_erased`, +1 |
| M5 | the tenant-scoped read does not mark its rows | DIED | `every_kept_row_is_marked_as_belonging_to_a_deleted_customer`, +1 |
| M5b | the company-scoped read does not mark its rows | DIED | `a_row_that_never_named_a_customer_is_not_marked_as_a_deleted_one`, +1 |
| M6 | `delete_tenant` proceeds without a customer record | DIED | `a_tenant_with_no_customer_record_cannot_be_half_deleted` |
| M7 | `authenticate` ignores a deleted customer | DIED | `a_session_opened_after_the_deletion_still_cannot_authenticate` |
| M8 | the route reads the tenant id out of the form | DIED | `a_caller_cannot_delete_a_customer_that_is_not_theirs`, and the AST scan |
| M9 | the confirmation is not bound to the person who was shown | DIED | `a_colleague_cannot_confirm_a_deletion_they_were_never_shown` |
| M10 | a confirmation with no preview proceeds anyway | DIED | `a_confirmation_with_no_preview_at_all_deletes_nothing`, +1 |
| M11 | the first request deletes instead of previewing | DIED | `the_first_request_only_previews_and_deletes_nothing`, +4 |

**M5 and M5b are two mutants because they are two readers.** One test covering
"the mark" would have passed with either read left unmarked, and the reader who
needs the mark is whichever one somebody happens to call.

**M7 is the one worth reading.** The obvious test — delete, then present the old
token — passes on the session revocation alone and says nothing about the check
in `authenticate`. It took a session opened *after* the deletion to reach that
guard at all, which is the only reason the backstop is measured rather than
merely present.
