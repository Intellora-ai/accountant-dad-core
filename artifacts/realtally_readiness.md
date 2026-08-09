# RealTally readiness audit — Phase 5 acceptance

**Status: PREPARATION. No real Tally was contacted to produce this document.**

Written 2026-08-10, branch `closure/flag-cap-and-truth`. Every claim below is
read off source, off `pytest` collection, or off a `FakeTally` run on this
machine. Nothing here is live evidence and nothing here claims to be.

Companion document: `docs/RUNBOOK_PHASE5_ACCEPTANCE.md`.

---

## 0. How to read the verdicts

| Verdict | Means |
|---|---|
| **PREPARED** | The code exists and will execute this step against a real TallyPrime the moment `Demo Co` exists and is open. Named with `file:line`. |
| **BLOCKED_ENVIRONMENT** | Code cannot fix it. It needs a human clicking in the TallyPrime window, or a non-Educational licence. |
| **MISSING** | Nothing exists yet. The thing that must be built is named exactly. |

**The one gate in front of everything.** Every step below needs `Demo Co` to
exist and be open in TallyPrime. That is a GUI action and the XML gateway cannot
do it — verbatim refusal `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>`
(`ci/educational_slice.py:32-40`). To keep the table useful, the verdicts below
are stated **assuming `Demo Co` exists**. Section 4 covers what that one action
unblocks.

---

## 1. The canonical test count — settled

The definition of "works on real Tally" is: point the `client` fixture in
`tests/test_tally_contract.py` at a real `TallyClient` and those tests pass. A
wrong count means the definition of done is wrong, so the count was recounted
from the file itself.

### 1.1 The numbers

| Measure | Count |
|---|---|
| Test functions in `tests/test_tally_contract.py` | **24** |
| Take the `client` fixture | **19** |
| Do not take the fixture | **5** |

Method, reproducible:

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m pytest -q -p no:cacheprovider tests/test_tally_contract.py --collect-only
# -> 24 tests collected
```

and an AST walk of the same file for functions whose parameter list contains
`client`, which yields 19.

### 1.2 The 19 client-fixture tests

| # | Test | Line |
|---|---|---|
| 1 | `test_fake_satisfies_the_protocol` | `tests/test_tally_contract.py:73` |
| 2 | `test_reads_the_chart_of_accounts` | `:77` |
| 3 | `test_empty_company_has_no_vouchers_and_a_flat_trial_balance` | `:81` |
| 4 | `test_every_written_voucher_carries_the_marker` | `:89` |
| 5 | `test_written_voucher_is_findable_by_operation_id_alone` | `:95` |
| 6 | `test_our_vouchers_are_distinguishable_from_the_users_own` | `:101` |
| 7 | `test_duplicate_operation_id_is_rejected` | `:134` |
| 8 | `test_a_rejected_retry_does_not_create_a_second_voucher` | `:141` |
| 9 | `test_read_back_returns_what_was_written` | `:157` |
| 10 | `test_read_back_of_an_unknown_operation_is_none` | `:167` |
| 11 | `test_reverse_restores_the_exact_prior_trial_balance` | `:171` |
| 12 | `test_reverse_all_restores_the_exact_prior_trial_balance` | `:182` |
| 13 | `test_reverse_targets_the_exact_voucher_not_a_lookalike` | `:196` |
| 14 | `test_reversing_an_unknown_operation_reports_false_and_changes_nothing` | `:218` |
| 15 | `test_reversing_twice_is_safe` | `:229` |
| 16 | `test_trial_balance_is_in_paise_and_balances_to_zero` | `:257` |
| 17 | `test_a_posted_voucher_appears_in_the_unfiltered_register` | `:283` |
| 18 | `test_a_posted_voucher_moves_the_trial_balance_by_its_own_amount` | `:309` |
| 19 | `test_the_unfiltered_register_also_holds_vouchers_we_did_not_write` | `:332` |

### 1.3 The 5 that do not take the fixture

Three are pure-function tests — they call `new_operation_id` and `stamp` and
touch no client at all:

- `test_operation_ids_are_unique_across_calls` — `:116`
- `test_stamping_twice_with_the_same_id_is_idempotent` — `:120`
- `test_restamping_with_a_different_id_is_refused` — `:125`

Two build their own `FakeTally` inline to test the backup gate. They are not
pure functions, but they do not use the fixture and cannot be pointed at a real
client without being rewritten:

- `test_refuses_to_write_to_a_company_with_no_backup` — `:239`
- `test_a_refused_write_leaves_the_company_untouched` — `:246`

### 1.4 Which number is right

**19 is the right answer to "how many tests take the `client` fixture".**
The audit is correct; the docs are wrong.

### 1.5 Where the wrong number 15 came from

15 is not invented. It is the number of contract tests currently **re-bound to
`RealTally`** in `tests/test_real_tally.py:428-466` — fifteen module-level
aliases, counted. The docs took that number and wrote it as the count of
client-fixture tests. Two different quantities, one number.

The gap is 4 tests. They take the `client` fixture but are **not** re-bound to
the real connector:

| Not re-bound | Why |
|---|---|
| `test_our_vouchers_are_distinguishable_from_the_users_own` (`:101`) | Rewritten by hand for the real client instead, `tests/test_real_tally.py:469`. |
| `test_a_posted_voucher_appears_in_the_unfiltered_register` (`:283`) | Added in P3.4. Never re-bound. |
| `test_a_posted_voucher_moves_the_trial_balance_by_its_own_amount` (`:309`) | Added in P3.4. Never re-bound. |
| `test_the_unfiltered_register_also_holds_vouchers_we_did_not_write` (`:332`) | Added in P3.4. Never re-bound. |

The three P3.4 tests are the ones that prove a voucher lands in **Tally's own
report** rather than in our own marker-filtered view. That is exactly the claim
a real-Tally run is supposed to establish, and it is the part not currently
exercised against `RealTally` at all — not even over the simulator.

### 1.6 Every place in `docs/` that states a wrong number

**Not edited. Another agent owns `docs/` except for the runbook.**

Line numbers read 2026-08-10 against the working tree of
`closure/flag-cap-and-truth`, in which none of these four files was modified.
If another agent edits them before the fix lands, re-locate by the quoted text
rather than by the line number.

| File:line | What it says | Correct |
|---|---|---|
| `docs/ARCHITECTURE.md:915` | "**all 15 client-fixture tests pass.**" | 19 |
| `docs/ARCHITECTURE.md:923` | "CONTRACT_PASS  all 15 pass against the real client" | 19 |
| `docs/BOTTLENECKS.md:95` | heading "blocks the 15 contract tests" | 19 |
| `docs/BOTTLENECKS.md:101` | "The 15 client-fixture tests" **and** "15 of the file's 21 tests take the `client` fixture" | 19 of the file's 24 |
| `docs/PROJECT_STATE.md:197` | "the 15 client-fixture tests" | 19 |
| `docs/PROJECT_STATE.md:981` | "the 15 client-fixture tests cannot run unmodified" | 19 |
| `docs/PROJECT_STATE.md:1046` | "15 client-fixture tests against the real client" | 19 |
| `docs/PROJECT_STATE.md:1079` | "all 15 client-fixture tests pass" | 19 |
| `docs/PROJECT_STATE.md:1162` | "the 15 client-fixture tests" | 19 |
| `docs/PROJECT_STATE.md:1307-1308` | "The 15 / client-fixture tests in `tests/test_tally_contract.py`" (the number is at the end of `:1307`) | 19 |
| `docs/PROJECT_STATE.md:1520-1521` | "with all 15 / client-fixture tests passing" | 19 |
| `docs/DECISIONS.md:45` | "The 15 client-fixture tests" | 19 |
| `docs/DECISIONS.md:271` | "the 15 contract tests" | 19 |

**13 places.** Two anchor links also embed the stale number and would break if
the `BOTTLENECKS.md` heading is renamed:
`docs/PROJECT_STATE.md:981` and `docs/PROJECT_STATE.md:1318`, both pointing at
`#a3--educational-mode-date-restriction-blocks-the-15-contract-tests`.

A related stale citation, same family: `docs/PROJECT_STATE.md:1309` and
`docs/PROJECT_STATE.md:981` cite `tests/test_tally_contract.py:39` as the line
holding `datetime.date(2026, 8, 7)`. It is now **line 53**.
`ci/educational_slice.py:22` carries the same stale `:39`.

### 1.7 Two of the 19 cannot pass as written against a real client

This is the part that matters more than the count, because it means the exit
criterion as written is not achievable by only editing the fixture.

**a) Two tests hard-assert the backend is the fake.**

- `tests/test_tally_contract.py:105` — `assert isinstance(client, FakeTally)`
- `tests/test_tally_contract.py:346` — `assert isinstance(client, FakeTally)`

The file's own docstring calls these "isinstance guards"
(`tests/test_tally_contract.py:23-24`), but an `assert isinstance(...)` is a
**failure**, not a skip. Point the fixture at `RealTally` and these two go red
immediately, before touching Tally. They call `client.seed_voucher()`, a
`FakeTally`-only setup helper that plants a hand-typed voucher.

*Fix (nobody owns it yet):* make the fixture parametrised over both backends and
turn the two guards into `pytest.skip(...)` for the real backend, or give the
real path an equivalent setup (`tests/test_real_tally.py:469-485` already does
this by hand for one of them).

**b) One test cannot pass against any real company.**

`test_reads_the_chart_of_accounts` (`:77`) asserts

```python
client.read_accounts(COMPANY) == (
    "Purchases",
    "Sundry Expenses",
    "Cash",
    "Sharma Traders",
)
```

Exact tuple equality. `RealTally.read_accounts` (`accountant/tallyio/real.py:2130-2134`)
returns **every** ledger name Tally reports, and `parse_ledger_names`
(`real.py:1230-1236`) applies no filtering — unlike `parse_closing_balances`,
which excludes Tally's derived heads by their `RESERVEDNAME` attribute
(`real.py:1296-1297`). A real TallyPrime company always carries at least
`Profit & Loss A/c`, so the tuple will never be exactly those four.

It passes today only because the simulator is seeded with exactly those four
ledger names (`tests/test_real_tally.py:415`, `:69`).

*Fix (nobody owns it yet):* either exclude reserved ledgers in
`parse_ledger_names` the way `parse_closing_balances` already does, or change
the assertion to a subset check. Both are code changes outside this agent's
ownership.

**Net:** of the 19, **16** would run unchanged against a real client, **2** need
a skip or a parametrised fixture, and **1** needs a code or assertion change.
"Point the fixture at RealTally and all of them pass" is not true today for
reasons that have nothing to do with the licence.

---

## 2. Readiness of the 15 acceptance steps

Steps are the ones implemented in `ci/acceptance.py:198-397`. See
`docs/RUNBOOK_PHASE5_ACCEPTANCE.md` Part C for what each observable looks like.

| # | Step | Verdict | Evidence / what is missing |
|---|---|---|---|
| 1 | Identify the backend | **PREPARED** | `accountant/tallyio/factory.py:157-166` builds `BackendIdentity`; `ci/acceptance.py:219` records `type(client).__name__`. Printed by `ci/acceptance_cli.py:114`. |
| 2 | Identify the company | **PREPARED** | `factory.py:142-148` refuses if the company is not open; `ci/acceptance.py:220-231` records it and fails early. Test: `tests/test_acceptance_n10.py:269`. |
| 3 | Record the backup fact | **PREPARED** | `accountant/tallyio/client.py:125-144` (`backed_up`), `real.py:1926-1938` (`RecordedBackups`), `ci/acceptance.py:222-224`. Test: `tests/test_acceptance_n10.py:294`. |
| 4 | Capture the baseline trial balance | **PREPARED** | `ci/acceptance.py:242-252`; `real.py:2220-2224` and `parse_closing_balances` `real.py:1255-1301` (zeros dropped, derived heads excluded). |
| 5 | Mint 10 distinct operation ids | **PREPARED** | `accountant/tallyio/client.py:31-34`; `ci/acceptance.py:255-259`. Conditions 1 and 2. |
| 6 | Post 10 distinct vouchers | **PREPARED** | `ci/acceptance.py:166-181` builds them; `real.py:2360-2452` writes one. Condition 3. |
| 7 | Read each back, field by field | **PREPARED** | `ci/acceptance.py:269-277` against `accountant/pipeline.py:351-357`; `real.py:2299-2358` proves identity, not mere presence. |
| 8 | Retry one operation id | **PREPARED** | `ci/acceptance.py:284`; guard at `real.py:2381-2384`. Test: `tests/test_acceptance_n10.py:226`. |
| 9 | Prove the retry created nothing | **PREPARED** | `ci/acceptance.py:292`. Condition 6. |
| 10 | Select only ours | **PREPARED** | `real.py:2265-2270` (marker filter), `ci/acceptance.py:299-301`. Conditions 7 and 8. Test: `tests/test_acceptance_n10.py:122`. |
| 11 | Reverse all ten as one batch | **PREPARED** | `accountant/reversal.py:273` / `:328` / `:466`, driven by `ci/acceptance.py:309-315`. Delete envelope `real.py:995-1058`. |
| 12 | Verify every reversal | **PREPARED** | `ci/acceptance.py:320-321`; states from `accountant/reversal.py:82-100`. Conditions 10, 11, 12, 13. |
| 13 | Read the final trial balance | **PREPARED** | `ci/acceptance.py:324-328`. |
| 14 | Compare final against baseline | **PREPARED** | `ci/acceptance.py:388-390`. Condition 14. Test: `tests/test_acceptance_n10.py:111`. |
| 15 | Check the bundle is complete | **PREPARED** | `ci/acceptance.py:391-396` and `:418-433`. Test: `tests/test_acceptance_n10.py:148`, `:385`. |

**All fifteen steps are PREPARED.** The harness is complete. No step is
licence-blocked; all fifteen can run in Educational mode on 2026-08-31, which is
`ci/acceptance.py:66`'s default date precisely so that they can.

Verified locally 2026-08-10 against `FakeTally`: all fifteen conditions pass,
verdict `PASSED`, `Purchases +1000045 / Cash -1000045` then back to the baseline.
Evidence class of that run: `FAKETALLY`. It says nothing about TallyPrime.

---

## 3. Everything else — the gaps around the fifteen steps

These are not steps of the sequence. They are the things a real run needs that
the sequence does not itself provide.

| Item | Verdict | Detail |
|---|---|---|
| `Demo Co` exists and is open | **BLOCKED_ENVIRONMENT** | GUI only. `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>`, `ci/educational_slice.py:32-40`. |
| Four ledgers exist with the right groups | **BLOCKED_ENVIRONMENT** | GUI only. `real.py:2274-2297` refuses to write if any is missing; Tally does not create masters on the fly. |
| A backup of `Demo Co` taken | **BLOCKED_ENVIRONMENT** | GUI only. The `--backed-up` flag is the operator asserting it; the software cannot check. |
| Pre-flight showing every autonomy-boundary item | **PREPARED** | `ci/acceptance_cli.py:106-140`. Test: `tests/test_acceptance_cli.py:144`. |
| Evidence bundle written to disk | **PREPARED** | `ci/acceptance_cli.py:202-206`. Test: `tests/test_acceptance_cli.py:223`. |
| Evidence-class honesty check | **PREPARED** | `ci/acceptance_cli.py:143-162`. Tests: `tests/test_acceptance_cli.py:67`, `:94`, `:120`. |
| Bulk-reversal cleanup command | **PREPARED** | `accountant/tallyio/__main__.py`, preview without `--yes`. |
| Reading the licence mode | **BLOCKED_ENVIRONMENT** | `real.py:2168-2203` returns an honest `unknown`. The gateway does not answer `$$LicenseInfo` (A11). Getting further would need a custom TDL report — **NOT MEASURABLE, would require a forbidden request shape**, and that shape is what wedged a live Tally (`real.py:1727-1730`). |
| **Durable per-voucher state if the process dies mid-batch** | **MISSING** | `ci/acceptance_cli.py:192-198` calls `run_acceptance` with **no `log=` sink and no `company_key=`**, so nothing reaches SQLite. The bundle is written only after the run returns (`:202-206`). Yet the pre-flight text promises "a batch that stops leaves a durable per-voucher state" (`ci/acceptance_cli.py:135-138`). **Must be built:** pass a `MemoryStore` (`accountant/memory/store.py:388`, `record_action` at `:445`) into `run_acceptance`, which already accepts `log` and `company_key` (`ci/acceptance.py:195-196`) and forwards them to `reversal.execute` (`:309-315`). |
| **A command to reconcile a stopped batch** | **MISSING** | `reversal.reconcile()` (`accountant/reversal.py:534`) exists and is tested (`tests/test_phase5b_readiness.py:56`), but there is **no CLI entry point** and no way to rebuild a `Batch` object from disk after the process exits. `reversal.resume()` (`:602`) refuses unless `batch.reconciled` is true (`:627-632`), so a dead process's work cannot be finished by any command that exists today. **Must be built:** batch persistence plus `python -m accountant.tallyio --reconcile` / `--resume`. |
| **P3.4 contract tests bound to `RealTally`** | **MISSING** | Three tests (`tests/test_tally_contract.py:283`, `:309`, `:332`) are not re-bound in `tests/test_real_tally.py:428-466`. **Must be built:** three more aliases, plus simulator seeding for the hand-typed voucher the third one needs. |
| **The two `isinstance(client, FakeTally)` asserts** | **MISSING** | `tests/test_tally_contract.py:105`, `:346`. See §1.7a. **Must be built:** a parametrised fixture and `pytest.skip` for the real backend. |
| **`read_accounts` exact-tuple assertion** | **MISSING** | See §1.7b. **Must be built:** reserved-ledger filtering in `parse_ledger_names` (`real.py:1230-1236`), or a subset assertion. |
| Acceptance run as a CI gate | **Not applicable** | The 20 locked gates (`ci/gate_names.lock`) contain no acceptance gate. Correct — the run needs a real Tally, and CI has none. |

### 3.1 One unmeasured risk, stated as a risk

*Assumption:* TallyPrime will accept a **Journal** voucher whose credit leg is
the `Cash` ledger, when the voucher arrives over the XML import gateway.
*Confidence:* 60%.
*Why it is in doubt:* TallyPrime has a setting, *Allow Cash Accounts in
Journals*, which is **off** by default and blocks exactly this combination on
the voucher-entry screen. Whether it also applies to XML import is not measured.
The voucher type is hard-coded to `Journal` (`accountant/tallyio/real.py:1881`)
and `ci/acceptance_cli.py` exposes no `--voucher-type` flag, so there is no way
to change it from the command line.
*Check that settles it:* run the pre-flight, then the real run. If voucher 0
fails with a Tally rejection mentioning cash or the journal voucher type, the
assumption is false. **Falsifier is the first write.** The cheap version fails in
the same place as the expensive one, so there is no smaller test worth building
first.
*If it is false:* the fix is a `--voucher-type` flag defaulting to `Journal`, or
turning the Tally setting on in `F12` configuration. Both are small; neither
exists today.

---

## 4. What the owner creating `Demo Co` unblocks

The single action: create `Demo Co` with the four ledgers in the TallyPrime GUI,
take a backup, leave it open. Steps in `docs/RUNBOOK_PHASE5_ACCEPTANCE.md`
Part A.

The moment that is done, and **without any code change or any licence**:

- All **15 acceptance steps** and all **15 pass conditions** become runnable
  against a real TallyPrime, on 2026-08-31.
- The pre-flight (`ci/acceptance_cli.py` without `--yes`) becomes runnable —
  a read-only confirmation of backend, company, backup, licence mode, the
  voucher set and the expected movement, touching nothing.
- The `EDUCATIONAL_TALLY` evidence class becomes producible for the first time.
- `ci/educational_slice.py` becomes runnable against `Demo Co` by name instead
  of "whatever company happens to be open" (`ci/educational_slice.py:37-40`).
- The bulk-reversal command (`python -m accountant.tallyio --reverse-all`)
  becomes exercisable against a real company.
- **The mechanism question gets answered:** does write → read-back → duplicate
  refusal → reverse → exact trial-balance restoration work against real Tally
  software? Today that is unanswered against anything but a fake and a
  simulator.

What it does **not** unblock, and must not be reported as unblocking:

- Anything about the 2026-08-07 fixture.
- Anything labelled `LICENSED_REALTALLY`.

---

## 5. What still needs a non-Educational licence after that

Two things, and only two.

### 5.1 The 19 contract tests on the frozen date

`tests/test_tally_contract.py:53` posts on **2026-08-07**. Educational mode
accepts only the 1st, 2nd and 31st. Measured 2026-08-08 against TallyPrime
Release 7.0, Series A Release 7.0.0, Build 27974: `2026-08-07` **REJECTED**,
`2026-08-31` **ACCEPTED**.

The fixture is not edited to fit the environment. That rule is enforced by
`tests/test_evidence_classes.py::test_the_contract_fixture_still_posts_on_the_seventh`,
which reads the real file and fails the build if the date changes.

Caveat carried forward from §1.7: even with a licence, 3 of the 19 do not pass
as written. Two hard-assert `FakeTally`; one asserts an exact four-ledger chart
of accounts that no real company has. **A licence is necessary and not
sufficient.**

### 5.2 The `LICENSED_REALTALLY` evidence class

`ci/acceptance_cli.py:143-162` refuses the label unless the connector measured
`licence_mode == licensed`. Today the gateway will not answer the licence
question at all (`real.py:2168-2180`), so the read is `unknown` and the label is
refused at every setting. The verbatim refusal is in
`docs/RUNBOOK_PHASE5_ACCEPTANCE.md` §H.2.

This is deliberate. The separation between compatibility evidence and live proof
is enforced by the machine and not by whoever writes the report afterwards. To
get the live label somebody has to make the licence read succeed, which is the
same thing as actually having a licence.

Standing owner decision, 2026-08-08, Option 2: Tally stays in Educational mode.
Under that decision §5.1 and §5.2 stay open, Phase 2 stays
`ENVIRONMENT-LIMITED`, and no amount of local green changes it.

---

## 6. The bottleneck, named

Ranked by what unblocks the most for the least effort.

1. **The owner, for ten minutes in the TallyPrime GUI.** Creating `Demo Co` and
   four ledgers converts fifteen PREPARED steps into fifteen executed steps and
   produces the project's first `EDUCATIONAL_TALLY` bundle. Nothing else on this
   list is worth doing first, and no code can substitute for it.
2. **Then:** the three test defects in §1.7, which are what stand between "the
   licence arrived" and "the contract actually passes". Fixing them is cheap now
   and expensive on the day a licence shows up.
3. **Then:** the durable-state and reconcile gaps in §3. They only matter for a
   run that dies mid-flight, which has not happened yet.
4. **Last:** the licence. It is the only item with a price tag and the only one
   that unblocks nothing until items 1 and 2 are done.

---

## Appendix — reproducing the measurements in this file

```bash
cd /Users/tanveersidhu/ACCOUNTANT

# the test count
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_tally_contract.py --collect-only | tail -1        # -> 24 tests collected

# the harness, green, against the fake only
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_acceptance_n10.py tests/test_acceptance_cli.py \
  tests/test_tally_contract.py tests/test_phase5b_readiness.py \
  tests/test_evidence_classes.py                               # -> 94 passed

# a FAKETALLY acceptance run, for the expected report shape
COVERAGE_CORE=pytrace .venv/bin/python -c "
from accountant.tallyio.fake import FakeTally
from ci import acceptance
t = FakeTally()
t.add_company('Demo Co', accounts=('Purchases','Sundry Expenses','Cash','Sharma Traders'), backed_up=True)
print(acceptance.render(acceptance.run_acceptance(
    t, 'Demo Co', run_id='run_demo', evidence_class=acceptance.FAKETALLY)))
"
```

None of the above opens a socket to Tally.
