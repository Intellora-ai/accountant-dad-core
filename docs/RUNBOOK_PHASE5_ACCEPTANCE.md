# Runbook — Phase 5 acceptance run (N = 10) against a real TallyPrime

**Status: PREPARATION. Nothing in this file has been run against a real Tally.**

Written 2026-08-10. Everything here is a plan for a run that has not happened.
No live evidence is claimed anywhere in this document. When the run happens, the
evidence is the JSON bundle the tool writes — not this file.

---

## 0. What this runbook is, in one paragraph

Accountant Dad can write entries into TallyPrime and take them back out again.
`ci/acceptance.py` is a program that proves this end to end: it writes ten
entries, checks each one, tries to write a duplicate, removes all ten, and
checks that the books are back to exactly where they started, down to the last
paisa. This runbook says how a person runs that program against a real
TallyPrime, and what "it passed" is allowed to mean afterwards.

**It does not rebuild anything.** The fifteen steps and the fifteen pass
conditions already exist in code (`ci/acceptance.py:198-397`). This file
documents how to run them, and nothing else.

---

## 1. Words this runbook uses

Define once, then used plainly.

| Word | Plain meaning |
|---|---|
| **TallyPrime** | The Windows accounting program the books live in. Runs in a VM here. |
| **Company** | One set of books inside Tally. Ours is called `Demo Co`. |
| **Ledger** | One named account inside a company — `Cash`, `Purchases`. Tally calls accounts "ledgers". |
| **Group** | The family a ledger belongs to. The group decides which side of the books the ledger sits on. |
| **Voucher** | One accounting entry. Money out of one ledger and into another. |
| **Paise** | 1/100 of a rupee. Every amount in this system is a whole number of paise. No decimals anywhere. |
| **Trial balance** | A list of every ledger and its balance. Ours is `{ledger name: paise}`, debit positive, credit negative. |
| **XML gateway** | Tally's HTTP port (9000). Programs talk to Tally by posting XML to it. |
| **Operation id** | Our unique label for one write. Shape `ad_` + 32 hex characters (`accountant/tallyio/client.py:31`). |
| **Marker** | The operation id written into the voucher's narration as `[ACCOUNTANT_DAD:<op>]` (`client.py:37`). It is how we find our own entries again. |
| **Evidence class** | A label saying what kind of Tally a result came from. It decides what the result may be used to claim. |
| **Educational mode** | A free, restricted TallyPrime. It only accepts vouchers dated the 1st, 2nd or 31st of a month. |

---

## 2. The two request shapes, and the rule that must not be broken

This connector sends **exactly two shapes** of XML to Tally:

1. `Export` + `Collection` — reads (`accountant/tallyio/real.py:705-706`)
2. `Import` + `Data` — writes and deletes (`real.py:945-947`, `real.py:1041-1043`)

There is a third shape, `Export` + `Function`, used only for the licence read
(`real.py:1736`). It is chosen for how it fails: it errors immediately.

**A custom TDL `<REPORT>` request once wedged a live Tally behind a modal dialog
box that nobody could close** (`real.py:1727-1730`). If any step of this runbook
seems to need a request shape outside the two above, the correct action is to
write **"NOT MEASURABLE — would require a forbidden request shape"** in the
report and send nothing. Do not improvise a request against a Tally you cannot
restart.

---

# PART A — OWNER GUI STEPS

## A.0 Why a human has to do this part

The XML gateway **cannot create a company**. This was tried and Tally refused,
word for word:

```
<RESPONSE>Unknown Request, cannot be processed</RESPONSE>
```

Recorded at `ci/educational_slice.py:32-40`. There is no flag, no other envelope
and no workaround. A company must be created by a person clicking in the
TallyPrime window, and it must be **open** in Tally before any command below is
run. The same is true of ledgers: `RealTally.write_voucher` refuses to post if a
ledger is missing, because Tally will not create one on the fly
(`real.py:2274-2297`).

This is the single owner action that unblocks the whole phase.

## A.1 Create the company `Demo Co`

Click by click, in TallyPrime (measured against Release 7.0; menu wording may
differ slightly on other builds).

1. Start TallyPrime in the VM. Wait for it to finish loading.
2. If the **Welcome to TallyPrime** screen is showing, click **Create Company**.
   If you are already at **Gateway of Tally**, press **F3** (the Company button
   in the top bar), then choose **Create**.
3. In **Company Creation**, type the name exactly:

   ```
   Demo Co
   ```

   Two words, one space, capital D, capital C. The name is compared **exactly**
   by `real_tally` (`accountant/tallyio/factory.py:142-148`). `Demo co`,
   `Demo  Co` or a trailing space will all be treated as a different company and
   the run will refuse to start.
4. Country: **India**. State: whichever is true. Neither affects the run.
5. **Financial year beginning from: `1-Apr-2026`.**
   This matters. The acceptance run posts on **31-Aug-2026**. If the financial
   year starts on `1-Apr-2025`, that date is in a future year and Tally will
   argue with you. Set the year so that 31-Aug-2026 falls inside it.
6. **Books beginning from:** same date, `1-Apr-2026`.
7. Leave everything else at its default. Press **Ctrl+A** to save, or press
   Enter through to the end and answer **Yes** to "Accept?".
8. You should now be at **Gateway of Tally** with `Demo Co` named at the top
   left.

## A.2 Create the four ledgers

Go to **Gateway of Tally → Create → Ledger**. (In TallyPrime, "Create" is on the
Gateway menu; type `Ledger` and press Enter.)

For each ledger: type the **Name**, set **Under** to the group named below, then
press **Ctrl+A** to save.

| # | Ledger name (exact) | Under (group) | Why this group |
|---|---|---|---|
| 1 | `Purchases` | **Purchase Accounts** | Goods bought for the business. A debit-side trading account. This is the ledger every acceptance voucher debits. |
| 2 | `Sundry Expenses` | **Indirect Expenses** | General running costs. A debit-side profit-and-loss account. Not touched by the acceptance run, but the contract fixture's chart of accounts names it. |
| 3 | `Cash` | **Cash-in-Hand** | Money in the till. A debit-side asset. This is the ledger every acceptance voucher credits. |
| 4 | `Sharma Traders` | **Sundry Creditors** | A supplier we buy from and owe money to. A credit-side liability. This is the `party` name on every acceptance voucher, sent as `<PARTYLEDGERNAME>` (`real.py:959`), so the ledger must exist. |

**`Cash` usually already exists.** TallyPrime creates it by default under
Cash-in-Hand. Check first at **Gateway of Tally → Chart of Accounts → Ledgers**.
If it is there, do not create a second one — two ledgers cannot share a name and
Tally will reject the attempt.

**On `Sharma Traders`:** when Tally asks *Maintain balances bill-by-bill?*,
answer **No**. Bill-by-bill tracking makes Tally demand a bill reference on
every entry, which the connector does not send.

### Why the group is not a detail

The group decides which side of the trial balance a ledger's balance lands on.

- `Sharma Traders` under **Sundry Creditors** → a liability → balance is a credit.
- `Sharma Traders` under **Sundry Debtors** → an asset → balance is a debit.

Same name, opposite sign. The acceptance run compares the trial balance before
and after, so a wrong group does not by itself fail the comparison — but every
figure a human reads off Tally afterwards would be on the wrong side, and any
later detector that reasons about supplier balances would be reasoning about a
customer. Put them in the groups in the table.

## A.3 Take a backup, and record it

The connector refuses to write to a company with no recorded backup
(`accountant/tallyio/client.py:66`, `real.py:2376-2379`). Tally does not tell us
whether a backup exists; **we assert it on the command line** with `--backed-up`
(`real.py:1926-1938`).

So:

1. In TallyPrime, take a real backup of `Demo Co`. Gateway of Tally → **F3
   (Company) → Backup**. Write down where the backup file went.
2. Only then are you entitled to pass `--backed-up`. That flag is a statement of
   fact by the operator. Passing it without having taken a backup is the one
   place in this runbook where a human can lie to the machine.

## A.4 Leave `Demo Co` open

Close nothing. `Demo Co` must be the open company when the commands run. The
connector lists open companies and refuses if `Demo Co` is not among them
(`factory.py:142-148`).

## A.5 Owner checklist

- [ ] TallyPrime is running.
- [ ] `Demo Co` exists and is **open**.
- [ ] Financial year covers **31-Aug-2026**.
- [ ] Ledger `Purchases` exists under Purchase Accounts.
- [ ] Ledger `Sundry Expenses` exists under Indirect Expenses.
- [ ] Ledger `Cash` exists under Cash-in-Hand.
- [ ] Ledger `Sharma Traders` exists under Sundry Creditors, bill-by-bill = No.
- [ ] A real backup of `Demo Co` has been taken and its location noted.
- [ ] The VM's IP address and Tally's port are known (default port 9000).

If any box is unticked, stop. Nothing below will work and some of it will fail
in a way that is harder to read than "the company is not there".

---

# PART B — THE COMMANDS, IN ORDER, WITH EXPECTED OUTPUT

All commands run from the repository root, `/Users/tanveersidhu/ACCOUNTANT`.

Replace `192.168.64.2` with the VM's actual address everywhere.

## B.1 Prove the harness is green locally first

This touches no Tally. It proves the tool itself is not broken before it is
pointed at anybody's books.

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  tests/test_acceptance_n10.py tests/test_acceptance_cli.py \
  tests/test_tally_contract.py tests/test_phase5b_readiness.py \
  tests/test_evidence_classes.py
```

Expected, exactly:

```
94 passed in <n>s
```

(Measured 2026-08-10 on branch `closure/flag-cap-and-truth`: **94 passed**. If
the count differs, tests were added or removed — read the diff before
continuing.)

**If this is not green, stop.** Do not run anything against Tally with a
failing harness.

## B.2 Pre-flight — reads only, writes nothing

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m ci.acceptance_cli \
  --host 192.168.64.2 --port 9000 \
  --company "Demo Co" --backed-up \
  --evidence-class EDUCATIONAL_TALLY
```

No `--yes`. This connects, identifies everything, prints it, and exits without
touching the books (`ci/acceptance_cli.py:187-190`).

Expected output — this exact shape, with your values in place of the examples:

```
PRE-FLIGHT — nothing has been written yet
  backend identity     RealTally
  endpoint             http://192.168.64.2:9000
  company identity     'Demo Co' (exists: True)
  companies visible    1
  backup identity      recorded=True
  licence mode         unknown
  licence detail       the licence mode has not been read
  write enabled        False
  evidence class       EDUCATIONAL_TALLY

  voucher set          10 controlled vouchers dated 2026-08-31
                       Purchases / Cash, 100000 to 100009 paise
  expected movement    Purchases +1000045, Cash -1000045 paise, then back
  operation ids        minted one per voucher immediately before each
                       write, and every one recorded in the bundle

  cleanup plan         all of them reversed by operation id in one batch,
                       each reversal verified against the trial balance
  reconciliation plan  a batch that stops leaves a durable per-voucher
                       state; reconcile with a read-only lookup, then
                       resume explicitly. Vouchers already reversed are
                       never re-reversed.

pre-flight only. nothing was written. pass --yes to run it.
```

Exit code **0**.

Read every line before going on:

- `company identity ... (exists: True)` — if this says `False` you never get
  here; the command refuses earlier with exit code 2.
- `companies visible` — if this is more than 1, more than one company is open in
  Tally. That is allowed, but confirm you meant it.
- `licence mode unknown` — **expected today.** The gateway does not answer the
  licence question (`real.py:2168-2180`). See Part F.
- `expected movement Purchases +1000045, Cash -1000045` — memorise this number.
  It is the only figure you have to check by eye.

## B.3 The run itself

Same command, plus `--yes` and `--out`.

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m ci.acceptance_cli \
  --host 192.168.64.2 --port 9000 \
  --company "Demo Co" --backed-up \
  --evidence-class EDUCATIONAL_TALLY \
  --yes --out evidence/acceptance-2026-08-31-educational.json
```

Expected output: the pre-flight block again (with `write enabled  True`), then a
blank line, then this — measured against `FakeTally` on 2026-08-10, and the
identical shape is what a real run produces:

```
N = 10 acceptance run
  run id          run_<32 hex>
  company         'Demo Co'
  backend         RealTally
  evidence class  EDUCATIONAL_TALLY
  backup recorded True
  voucher date    2026-08-31

  ok   vouchers_posted: actual 10, expected 10
  ok   operation_ids_distinct: actual 10, expected 10
  ok   voucher_identities_distinct: actual 10, expected 10
  ok   correct_company: actual True, expected True
  ok   postings_read_back: actual 10, expected 10
  ok   duplicate_created_nothing: actual 0, expected 0
  ok   no_user_voucher_selected: actual 0, expected 0
  ok   user_vouchers_untouched: actual 0, expected 0
  ok   reversals_succeeded: actual 10, expected 10
  ok   reversals_read_back: actual 0, expected 0
  ok   no_unknown_outcome: actual 0, expected 0
  ok   no_wrong_movement: actual 0, expected 0
  ok   cleanup_completed: actual 'completed', expected 'completed'
  ok   trial_balance_restored: actual {...}, expected {...}
  ok   evidence_complete: actual True, expected True

  VERDICT: PASSED

  evidence bundle written to evidence/acceptance-2026-08-31-educational.json
```

Exit codes (`ci/acceptance_cli.py:59-61`):

| Code | Meaning | What to do |
|---|---|---|
| **0** | Every condition passed. | File the bundle. |
| **2** | Refused before touching anything. Tally unreachable, company not open, or the evidence class was not honest. | Read the message. Nothing was written. |
| **3** | The run happened and did **not** pass. | **Go to Part E.** There may be vouchers left in the books. |

On `FakeTally`, `trial_balance_restored` reads `actual {}, expected {}` because
the fake starts empty. On a real `Demo Co` both sides will be whatever the
company held before the run — the claim is that they are **equal**, not that
they are empty.

## B.4 If the run leaves entries behind — the cleanup command

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m accountant.tallyio --reverse-all \
  --company "Demo Co" --host 192.168.64.2 --port 9000 --backed-up
```

Without `--yes` this is a **preview**. It prints every operation id it would
remove and touches nothing (`accountant/tallyio/__main__.py:125-170`):

```
resolved configuration
  company    'Demo Co'
  endpoint   http://192.168.64.2:9000
  backed up  True
  confirmed  False
  backend    RealTally

batch <id>: 0 voucher(s) of ours
  (none)

preview only. nothing was reversed. pass --yes to confirm this list.
```

`0 voucher(s) of ours` after a passing run is the proof that nothing was left
behind. If it lists any, **read the list**, confirm every id is one this run
created, then re-run with `--yes`.

This command only ever selects vouchers carrying our marker
(`real.py:2265-2270`). A voucher a human typed into Tally by hand has no marker
and can never be selected by it.

---

# PART C — THE 15 STEPS AND THE 15 PASS CONDITIONS

The steps are the order things happen in `ci/acceptance.py:198-397`. The
conditions are what gets checked. They are not one-to-one — some steps produce
two conditions, some conditions need two steps.

Tick a box only when you have seen the observable named next to it, in the
printed report or in the bundle.

## C.1 The fifteen steps

| # | Step | Code | Observable that ticks it |
|---|---|---|---|
| 1 | Identify the backend | `acceptance.py:219` | `backend  RealTally` in the report |
| 2 | Identify the company | `acceptance.py:220-231` | `company 'Demo Co'`, and condition `correct_company` is `True` |
| 3 | Record the backup fact | `acceptance.py:222-224` | `backup recorded True` |
| 4 | Capture the baseline trial balance | `acceptance.py:242-252` | `baseline_trial_balance` present in the bundle |
| 5 | Mint 10 distinct operation ids | `acceptance.py:255-259` | `operation_ids` has 10 entries, all different |
| 6 | Post 10 distinct vouchers | `acceptance.py:262` | `voucher_ids` has 10 entries, all different, none empty |
| 7 | Read each one back, field by field | `acceptance.py:269-277` | `postings_read_back` is 10 |
| 8 | Retry one operation id on purpose | `acceptance.py:284` | log line `the duplicate operation id was refused, as it must be` |
| 9 | Prove the retry created nothing | `acceptance.py:292` | `duplicate_created_nothing` is 0 |
| 10 | Select only ours, via the marker filter | `acceptance.py:299-301` | `no_user_voucher_selected` is 0 |
| 11 | Reverse all ten as one batch | `acceptance.py:309-315` | `reversals_succeeded` is 10 |
| 12 | Verify every reversal individually | `acceptance.py:320-321` | `voucher_states` is ten entries of `reversed_verified` |
| 13 | Read the final trial balance | `acceptance.py:324-328` | `final_trial_balance` present in the bundle |
| 14 | Compare final against baseline | `acceptance.py:388-390` | `trial_balance_restored` shows `actual` equal to `expected` |
| 15 | Check the evidence bundle is complete | `acceptance.py:391-396`, `:418-433` | `evidence_complete` is `True` |

## C.2 The fifteen pass conditions

Every one must pass. **There is no partial credit and failures are not
averaged** (`acceptance.py:39-40`, `:128-131`).

- [ ] **1. `vouchers_posted`** — actual **10**, expected **10**.
      Ten controlled vouchers went out.
- [ ] **2. `operation_ids_distinct`** — actual **10**, expected **10**.
      No two writes shared a label.
- [ ] **3. `voucher_identities_distinct`** — actual **10**, expected **10**.
      Tally gave ten different voucher identities back. Ten identical ones would
      mean we wrote one voucher ten times.
- [ ] **4. `correct_company`** — actual **True**, expected **True**.
      `Demo Co` was open in Tally.
- [ ] **5. `postings_read_back`** — actual **10**, expected **10**.
      Each voucher was read back and matched on amount, party, date, debit
      ledger and credit ledger (`accountant/pipeline.py:351-357`). The narration
      is deliberately not compared — we stamp the marker into it, so it is meant
      to differ.
- [ ] **6. `duplicate_created_nothing`** — actual **0**, expected **0**.
      Writing the same operation id twice created zero extra vouchers.
- [ ] **7. `no_user_voucher_selected`** — actual **0**, expected **0**.
      The ownership filter picked up nothing a human typed.
- [ ] **8. `user_vouchers_untouched`** — actual **equals the count before the
      run**. Entries that were already in `Demo Co` are still there, same count.
- [ ] **9. `reversals_succeeded`** — actual **10**, expected **10**.
- [ ] **10. `reversals_read_back`** — actual **0**, expected **0**.
      Zero reversals failed their read-back check.
- [ ] **11. `no_unknown_outcome`** — actual **0**, expected **0**.
      Zero vouchers ended in "we cannot tell what happened". This is the one
      that matters most: an unknown outcome must never be retried automatically,
      because a retry after a write that *did* land puts two statutory entries
      in somebody's books (`real.py:2419-2431`).
- [ ] **12. `no_wrong_movement`** — actual **0**, expected **0**.
      Zero cases where Tally's answer and Tally's books disagreed.
- [ ] **13. `cleanup_completed`** — actual **`'completed'`**, expected
      **`'completed'`**. The batch finished; it did not stop halfway.
- [ ] **14. `trial_balance_restored`** — actual **equals** expected.
      Every ledger, exact paise. This is the whole point of the exercise.
- [ ] **15. `evidence_complete`** — actual **True**, expected **True**.
      Ten operation ids, all non-empty; ten voucher identities, all non-empty;
      ten recorded states. A record with holes in it is refused, because a
      missing field is indistinguishable from a field nobody looked at
      (`acceptance.py:421-425`).

**A run that stopped early can never report PASSED**, even if every condition
above happens to be green (`acceptance.py:128-131`). Check the report for a
`STOPPED EARLY:` line.

---

# PART D — THE 10 CONTROLLED VOUCHERS

Built by `ci/acceptance.py:166-181`. Every field below is generated by that
function; nothing here is a choice made on the day.

## D.1 Common to all ten

| Field | Value |
|---|---|
| Date | **2026-08-31** (`acceptance.py:66`) |
| Party | `Sharma Traders` — sent as `<PARTYLEDGERNAME>` (`real.py:959`) |
| Debit ledger | `Purchases` |
| Credit ledger | `Cash` |
| Voucher type in Tally | **Journal** (`real.py:1881`) |
| Operation id shape | `ad_` + 32 lowercase hex characters, e.g. `ad_a060365f2e8b4ebdbb191457c6eb2af7` (`client.py:31-34`) |
| Marker in narration | `[ACCOUNTANT_DAD:<operation id>]` appended to the narration (`client.py:37-38`, `:47-56`) |

Why the amounts are all different: ten identical vouchers would be a perfectly
legitimate thing for a business to post and a useless thing to test with —
"did we get all ten back?" is unanswerable when they cannot be told apart
(`acceptance.py:167-171`).

## D.2 The ten, one row each

Amounts are **integer paise**. Movement is in our convention: **debit positive,
credit negative**.

| n | Draft id | Narration (before the marker) | Amount (paise) | Debit leg | Credit leg | Movement this voucher | Running total (Purchases) |
|---|---|---|---|---|---|---|---|
| 0 | `acceptance-00` | acceptance run, controlled voucher 0 of 10 | **100000** | Purchases +100000 | Cash −100000 | Purchases +100000, Cash −100000 | +100000 |
| 1 | `acceptance-01` | acceptance run, controlled voucher 1 of 10 | **100001** | Purchases +100001 | Cash −100001 | Purchases +100001, Cash −100001 | +200001 |
| 2 | `acceptance-02` | acceptance run, controlled voucher 2 of 10 | **100002** | Purchases +100002 | Cash −100002 | Purchases +100002, Cash −100002 | +300003 |
| 3 | `acceptance-03` | acceptance run, controlled voucher 3 of 10 | **100003** | Purchases +100003 | Cash −100003 | Purchases +100003, Cash −100003 | +400006 |
| 4 | `acceptance-04` | acceptance run, controlled voucher 4 of 10 | **100004** | Purchases +100004 | Cash −100004 | Purchases +100004, Cash −100004 | +500010 |
| 5 | `acceptance-05` | acceptance run, controlled voucher 5 of 10 | **100005** | Purchases +100005 | Cash −100005 | Purchases +100005, Cash −100005 | +600015 |
| 6 | `acceptance-06` | acceptance run, controlled voucher 6 of 10 | **100006** | Purchases +100006 | Cash −100006 | Purchases +100006, Cash −100006 | +700021 |
| 7 | `acceptance-07` | acceptance run, controlled voucher 7 of 10 | **100007** | Purchases +100007 | Cash −100007 | Purchases +100007, Cash −100007 | +800028 |
| 8 | `acceptance-08` | acceptance run, controlled voucher 8 of 10 | **100008** | Purchases +100008 | Cash −100008 | Purchases +100008, Cash −100008 | +900036 |
| 9 | `acceptance-09` | acceptance run, controlled voucher 9 of 10 | **100009** | Purchases +100009 | Cash −100009 | Purchases +100009, Cash −100009 | +1000045 |

The Cash running total is the same figure with the sign flipped, at every row.

## D.3 The total

| | Paise | Rupees |
|---|---|---|
| Purchases moves by | **+1 000 045** | ₹10,000.45 |
| Cash moves by | **−1 000 045** | −₹10,000.45 |
| Net across both | **0** | ₹0.00 |

Then all ten are reversed, and both figures return to **0** movement — the trial
balance goes back to exactly what it was before the run started.

The pre-flight prints this same number before anything is written:
`expected movement    Purchases +1000045, Cash -1000045 paise, then back`.
If the pre-flight ever prints a different figure, the harness has changed and
this table is stale. **Trust the pre-flight, not this table.**

---

# PART E — THE MEASUREMENT STEPS, ONE COMMAND EACH

The single `ci.acceptance_cli` command in B.3 does all of these in order. This
section names the command for each **when you have to do one on its own** —
after a partial failure, or when checking by hand.

## E.1 Baseline trial-balance capture

Inside the run: `ci/acceptance.py:242-252`, calling `client.trial_balance()`.

On its own (read only, writes nothing):

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python -c "
from accountant.tallyio.factory import real_tally
from accountant.tallyio.real import TallyConfig
c, i = real_tally(TallyConfig(host='192.168.64.2', port=9000), 'Demo Co')
print('companies visible:', i.companies_visible)
print('trial balance:', c.trial_balance('Demo Co'))
print('vouchers:', len(c.read_vouchers('Demo Co')))
print('ours:', len(c.list_our_vouchers('Demo Co')))
"
```

No backup is recorded here, so this client **cannot write** even if something
tried (`real.py:1930-1933`). Expected on a fresh `Demo Co`:

```
companies visible: 1
trial balance: {}
vouchers: 0
ours: 0
```

An empty trial balance is `{}` because zero balances are dropped and Tally's
derived heads (like `Profit & Loss A/c`) are excluded by their `RESERVEDNAME`
attribute (`real.py:1255-1301`).

## E.2 Read-back of each posting

Inside the run: `ci/acceptance.py:269-277`. It calls
`client.read_by_operation_id()` once per voucher and compares five fields —
`amount_paise`, `party`, `date`, `debit_account`, `credit_account`
(`accountant/pipeline.py:351-357`).

Observable: condition **5**, `postings_read_back: actual 10, expected 10`.

If it is less than 10, the bundle's `log` names each one:
`voucher <i> did not read back as written`.

## E.3 Duplicate-retry proof

Inside the run: `ci/acceptance.py:279-292`. It counts our vouchers, writes
operation id **0 again**, then counts again.

Two observables, both required:

1. Bundle `log` contains
   `the duplicate operation id was refused, as it must be`.
2. Condition **6**, `duplicate_created_nothing: actual 0, expected 0`.

If the log instead says `a duplicate operation id was ACCEPTED`, the connector's
idempotency guard did not fire. **That is an abort condition — see F.3.**

## E.4 Reversal

Inside the run: `ci/acceptance.py:305-321`. Three library calls in sequence —
`reversal.preview()` (`accountant/reversal.py:273`), `reversal.confirm()`
(`:328`), `reversal.execute()` (`:466`).

On its own, as an operator command:

```bash
COVERAGE_CORE=pytrace /Users/tanveersidhu/ACCOUNTANT/.venv/bin/python \
  -m accountant.tallyio --reverse-all \
  --company "Demo Co" --host 192.168.64.2 --port 9000 --backed-up --yes
```

Expected tail:

```
batch <id>: COMPLETED
  <detail sentence>
  ad_<hex>  reversed_verified
  ...  (ten lines)
  every paise accounted for: True
```

Observables: conditions **9**, **10**, **11**, **12**, **13**.

## E.5 Final comparison

Inside the run: `ci/acceptance.py:324-328` reads the final trial balance,
`:388-390` compares it to the baseline.

Observable: condition **14**, `trial_balance_restored`, with `actual` and
`expected` printed side by side. They must be **equal**, not merely both
balanced.

By hand, re-run the E.1 command. The dictionary it prints must be character-for-
character the same as the one you captured before the run.

## E.6 What is NOT measurable by command today

- **Reconciling and resuming a batch after the process died.**
  `reversal.reconcile()` (`accountant/reversal.py:534`) and `reversal.resume()`
  (`:602`) exist as library functions and are tested
  (`tests/test_phase5b_readiness.py:56`, `:68`). There is **no command-line
  entry point** for either, and `ci/acceptance_cli.py` passes no action-log sink
  to `run_acceptance`, so a batch that dies mid-flight leaves nothing on disk to
  reconcile from. See the readiness audit, `artifacts/realtally_readiness.md`.
- **Reading the licence mode.** The gateway does not answer `$$LicenseInfo`
  (`real.py:2168-2180`). It returns an honest `unknown`. A custom TDL report
  would be needed to get further, and that is the shape that wedged a live
  Tally. **NOT MEASURABLE — would require a forbidden request shape.**

---

# PART F — ABORT CONDITIONS

**Stop means stop.** Do not retry, do not improvise a different command, do not
"just check one thing" against a Tally that is misbehaving.

## F.1 Stop before anything is written

| Signal | What it means | Action |
|---|---|---|
| Exit code **2** with `REAL TALLY REQUIRED: no operation performed.` | Tally unreachable, or `Demo Co` not open (`factory.py:132-148`). | Fix Tally. Nothing was written. Restart from B.2. |
| Pre-flight shows `company identity ... (exists: False)` | Cannot happen — the command refuses first. If you somehow see it, the tool has been modified. | Stop. Report it. |
| Pre-flight shows `backup identity recorded=False` | You did not pass `--backed-up`. Every write and every reversal will be refused (`real.py:2376`). | Take a backup, then re-run with the flag. |
| Pre-flight shows `companies visible` greater than 1 and you did not expect it | Another company is open. The run itself is still safe, but you cannot be sure which books a human is looking at. | Close the others in Tally, re-run B.2. |
| Exit code **2** with `refusing to label this run LICENSED_REALTALLY` | You asked for the live evidence class on a Tally that did not prove it is licensed. | Use `EDUCATIONAL_TALLY`. See Part G. |
| B.1 test run is not `94 passed` | The harness itself is broken or changed. | Stop. Do not touch Tally. |

## F.2 Stop during the run — the harness stops itself

`run_acceptance` never raises for a Tally-shaped failure; it records the failure
and reports it (`acceptance.py:200-204`). A `STOPPED EARLY:` line in the report
means it gave up part way. Known texts:

- `'Demo Co' is not open in Tally; N company/companies are: [...]` — nothing was
  read or written.
- `reading the baseline: <Error>: <message>` — nothing was written.
- `posting voucher <i>: CompanyNotBackedUp: 'Demo Co' has no recorded backup;
  refusing to write` — nothing was written.
- `posting voucher <i>: <Error>: <message>` — vouchers **0 to i−1 are in the
  books**. Go to F.4.
- `the batch could not start: <Error>: <message>` — **all ten are in the books**.
  Go to F.4.

## F.3 Stop and call a human — do not continue, do not retry

These are the ones where continuing makes a bad situation bigger.

| Signal | Why it is serious |
|---|---|
| `no_unknown_outcome` is **not 0**, or a voucher state is `unknown_outcome` | Tally said it wrote something and the register does not show it. It may have landed somewhere we cannot read, or never landed. **A retry after a write that did land puts two statutory entries in somebody's books** (`real.py:2419-2431`). Never retry automatically. A person must look in Tally. |
| `no_wrong_movement` is **not 0**, or a state is `wrong_movement` | Tally's answer and Tally's books disagree. `reversal.resume()` refuses to run on this and says so (`reversal.py:618-626`). |
| Batch state is `critical_failure` | Cannot be resumed at all. A person has to reconcile by hand (`reversal.py:622-626`). |
| Log says `a duplicate operation id was ACCEPTED` | The idempotency guard did not fire. Two entries may now share one marker, and automatic reversal refuses to touch an ambiguous marker (`real.py:2246-2253`). |
| Any message containing `AmbiguousMarker` | Two vouchers carry the same operation id. Every destructive action is refused rather than aimed at a coin flip. |
| Any message containing `MALFORMED_RESPONSE` | The register answered with something unreadable. That is evidence of nothing — not evidence the voucher is missing, and not evidence it is there. |
| TallyPrime shows a modal dialog box and stops responding | This is the wedge. **Do not send another request.** Note the exact last command. If the Tally cannot be restarted, write "NOT MEASURABLE" and stop. |

## F.4 Cleanup after a stop

Only when F.3 does **not** apply.

1. Run the preview: B.4 **without** `--yes`.
2. Read every operation id it lists. Confirm each one came from this run — cross
   check against `operation_ids` in the bundle if one was written.
3. If and only if all of them are yours, re-run B.4 **with** `--yes`.
4. Re-run E.1 and confirm the trial balance is back to the baseline.

If the preview lists an id you cannot account for, stop and get a person to look
at the books.

---

# PART G — THE EVIDENCE BUNDLE

## G.1 Where it lands

Wherever `--out` says. The tool creates the parent directory if needed
(`ci/acceptance_cli.py:202-206`).

Convention for this project:

```
evidence/acceptance-<voucher date>-<evidence class, lowercase>.json
```

For example `evidence/acceptance-2026-08-31-educational.json`.

`evidence/` is **not** in `.gitignore`, so the bundle can and should be
committed. It is the record; this runbook is not.

## G.2 Every field the bundle carries

Produced by `AcceptanceRun.bundle()` (`ci/acceptance.py:140-160`), serialised
with `indent=1, sort_keys=True` (`:162-163`).

| Field | Type | What it records |
|---|---|---|
| `run_id` | string | One id per process start, `run_` + 32 hex (`factory.py:104-106`). |
| `company` | string | The company name as given. |
| `backend` | string | The Python class that talked to Tally — `RealTally` or `FakeTally`. Measured, not declared. |
| `evidence_class` | string | One of `UNIT_TEST`, `FAKETALLY`, `SIMULATOR`, `EDUCATIONAL_TALLY`, `LICENSED_REALTALLY` (`acceptance.py:70-82`). |
| `backed_up` | boolean | Whether a backup was recorded for this company. |
| `voucher_date` | string | ISO date, e.g. `2026-08-31`. |
| `n` | integer | Always **10**. Owner decision, not configurable (`acceptance.py:60-61`). |
| `baseline_trial_balance` | object | `{ledger: paise}` before the run. |
| `final_trial_balance` | object | `{ledger: paise}` after the run. |
| `operation_ids` | list of 10 strings | Every label minted, in order. |
| `voucher_ids` | list of 10 strings | Tally's own identity for each voucher. |
| `batch_state` | string | `completed`, or the state it stopped in, or `not_run`. |
| `voucher_states` | list of 10 strings | Per-voucher reversal outcome, e.g. `reversed_verified`. |
| `conditions` | list of 15 objects | Each has `metric`, `actual`, `expected`, `pass_rule`, `passed`. |
| `failed_early` | string | Empty when the run got all the way through. Otherwise the reason. |
| `verdict` | string | `PASSED` or `NOT_PASSED`. |
| `log` | list of strings | Every note the run made, in order. |

## G.3 What to write down beside the bundle

The bundle records the machine's side. Record the human's side in the commit
message or an adjacent note:

- Date and time of the run, and who ran it.
- TallyPrime release, series and build (from Tally's own **Help → About**).
- Where the `Demo Co` backup file is.
- Whether anything unexpected appeared on the TallyPrime screen during the run.
- The exact command line, copied.

---

# PART H — THE EVIDENCE CLASS, AND THE RULE THAT DECIDES IT

## H.1 The five classes

`ci/acceptance.py:70-82`.

| Class | What produced it | What it may be used to claim |
|---|---|---|
| `UNIT_TEST` | Pure functions, no client. | Our arithmetic is right. |
| `FAKETALLY` | The in-memory double. | Our logic is right. **Says nothing about TallyPrime.** |
| `SIMULATOR` | `RealTally` over an XML double. | Our XML is shaped the way we think. Says nothing about a real Tally. |
| `EDUCATIONAL_TALLY` | A real TallyPrime in Educational mode, on a permitted date. | The mechanism works against real Tally software. **Says nothing about the 2026-08-07 fixture.** |
| `LICENSED_REALTALLY` | A real, licensed TallyPrime. | Live proof. **Never yet produced.** |

Only three can be asked for from the command line — `SIMULATOR`,
`EDUCATIONAL_TALLY`, `LICENSED_REALTALLY` (`acceptance_cli.py:63-71`).
`UNIT_TEST` and `FAKETALLY` are excluded on purpose: this command always talks
to a real connector, so offering them would let a real run be filed under a
class that means "no Tally was involved".

## H.2 The rule

**`LICENSED_REALTALLY` is refused unless the connector itself measured
`licence_mode == licensed`.** `ci/acceptance_cli.py:143-162`.

The refusal, word for word:

```
refusing to label this run LICENSED_REALTALLY: the connector measured
licence_mode='unknown', not 'licensed'. Live proof requires a licensed Tally
that says so, and the licence read on this gateway currently returns UNKNOWN
by design (A11). Use EDUCATIONAL_TALLY, or make the licence read succeed.
```

Exit code **2**. Nothing is written.

Only one direction is dangerous. Filing a licensed run as compatibility evidence
understates it and harms nobody. Filing an Educational or unknown run as live is
the mislabelling that would quietly close the last open question in this
project. So the check is one-way (`acceptance_cli.py:146-149`).

## H.3 The consequence today

The gateway does not answer the licence question at all (`real.py:2168-2180`),
so `licence_mode` reads `unknown`, so **this tool cannot currently produce the
`LICENSED_REALTALLY` class at any setting**. That is the design, not a bug: if
somebody wants the live label they have to make the licence read succeed, and
that is the same thing as actually having a licence.

## H.4 An Educational run is never relabelled

A run made in Educational mode is `EDUCATIONAL_TALLY` forever. It is not
upgraded later, not "counted as" live because it passed, and not summarised as
live in a report. The label is a property of the run, printed beside every
verdict, never a label chosen afterwards when writing it up
(`acceptance.py:27-29`). `tests/test_evidence_classes.py` fails the build if the
compatibility harness ever starts claiming more than it measured.

---

# PART I — THE EDUCATIONAL-MODE LIMITATION, STATED PLAINLY

## I.1 What the restriction is

TallyPrime in Educational mode **accepts vouchers dated only the 1st, 2nd and
31st of a month**. Every other date is refused.

Literally the 31st — not "the last day of the month". February has only two
usable dates.

Measured 2026-08-08 against TallyPrime Release 7.0, Series A Release 7.0.0,
Build 27974: `2026-08-07` **REJECTED**, `2026-08-31` **ACCEPTED**.

## I.2 What that does to the contract fixture

`tests/test_tally_contract.py` posts every voucher on **2026-08-07**
(`tests/test_tally_contract.py:53`). That date is not the 1st, the 2nd or the
31st. **Educational mode refuses it.** So the contract tests cannot run
unmodified against an Educational TallyPrime.

## I.3 The fixture is not edited

Changing `2026-08-07` to `2026-08-31` would make the suite green on a
configuration nobody intends to ship on, and would delete the only evidence that
the restriction exists.

This is enforced, not promised.
`tests/test_evidence_classes.py::test_the_contract_fixture_still_posts_on_the_seventh`
reads the actual file and fails the build if the date changes.

## I.4 What the acceptance run does instead

`ci/acceptance.py:66` uses **2026-08-31** as its default — a date every
environment permits, so a run that has to move to a real Tally does not also
have to change its data.

That makes the acceptance run and the contract fixture **two different
questions**:

| Question | Date | Answerable in Educational mode? |
|---|---|---|
| Does the write-and-reverse mechanism work against real Tally software? | 2026-08-31 | **Yes** — this runbook. |
| Does the unchanged 2026-08-07 contract pass against real Tally? | 2026-08-07 | **No.** Needs a non-Educational licence. |

A passing run of this runbook answers the first. It **must never** be reported
as answering the second.

## I.5 Standing owner decision

2026-08-08, Option 2: Tally stays in Educational mode. Phase 2 is therefore
`ENVIRONMENT-LIMITED`, and the 2026-08-07 fixture is never edited to make it
pass. This runbook is written to work inside that decision, not around it.

---

## Appendix — file map

| What | Where |
|---|---|
| The fifteen steps and fifteen conditions | `ci/acceptance.py:198-397` |
| The ten controlled vouchers | `ci/acceptance.py:166-181` |
| The evidence classes | `ci/acceptance.py:70-82` |
| The evidence bundle format | `ci/acceptance.py:140-163` |
| The bundle-completeness rule | `ci/acceptance.py:418-433` |
| The command line | `ci/acceptance_cli.py:74-103` |
| The pre-flight | `ci/acceptance_cli.py:106-140` |
| The evidence-class honesty check | `ci/acceptance_cli.py:143-162` |
| Exit codes | `ci/acceptance_cli.py:59-61` |
| Connect-or-refuse | `accountant/tallyio/factory.py:109-166` |
| The backup gate | `accountant/tallyio/real.py:1926-1938`, `:2376-2379` |
| The ledger-exists check | `accountant/tallyio/real.py:2274-2297` |
| The licence read | `accountant/tallyio/real.py:2168-2203` |
| The bulk-reversal command | `accountant/tallyio/__main__.py` |
| Company creation refused over XML | `ci/educational_slice.py:32-40` |
| The contract tests | `tests/test_tally_contract.py` |
| Readiness audit for this runbook | `artifacts/realtally_readiness.md` |
