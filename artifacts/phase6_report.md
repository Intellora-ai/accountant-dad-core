# Phase 6 re-verification — the first detector

**Branch** `closure/flag-cap-and-truth` · **base** `3445992` (Phase 6) + `a19a100` (flag_cap = 3)
**Measured** 2026-08-10 · **Evidence class** FAKETALLY over real HTTP
**Pins** `tests/test_phase6_exits.py` (34 tests), alongside `tests/test_first_detector.py`,
`tests/test_dismissal_durability.py`, `tests/test_review_flow_defects.py`

Canonical scope, `docs/ARCHITECTURE.md` §7: wire `vendor_switch` into the review screen,
show the result, log dismissals durably. Deterministic ranking by severity, ties by
voucher id. Per-batch cap with overflow reported as a count.

## What this report does not prove

Nothing here is evidence about a real TallyPrime. Every measurement below ran against
`FakeTally` behind `app.configure()`, over a real HTTP server on a real socket. It does
not prove that a real chart of accounts ever loses a ledger, that a real book contains a
vendor whose two legs are one ledger, or that Tally accepts any of these vouchers. It
proves what this code does with those books.

---

## 1. Exit table

| # | criterion | verdict | evidence |
|---|---|---|---|
| 1 | `vendor_switch` is invoked BY THE REVIEW-SCREEN PATH, not only as a unit call | **PASSED** | three live HTTP routes, §2. AST: one product call site (`pipeline.evaluate`), reached from `app._run` and `app.do_POST` |
| 2 | the detector result is visible in the review screen | **PASSED** | `data-detector="vendor_switch"` plus the vendor, the usual account and the count, asserted on the rendered page on all three routes |
| 3 | the result has a deterministic, testable representation | **PASSED** | `data-detector` / `data-dismissed` attributes; ranking `(-severity, detector, voucher_id)` proved order-independent; cap keeps the highest-ranked and reports the rest as a count |
| 4 | dismissal is explicitly available where intended | **PASSED** | a `/dismiss` form beside every undismissed flag, none where there is no flag, none once dismissed |
| 5 | every dismissal creates a durable audit event | **PASSED** | read back from a SECOND `MemoryStore` opened on the same file after the server was torn down — two routes, two vendors |
| 6 | the event carries company, review, detector, timestamp, reason/context | **PARTIALLY_VERIFIED** | `company_key`, `ts` (tz-aware), detector (in `reason` and `detail`), `reason`, `operation_id`, `vendor_id`, `run_id`, `backend` all present. `voucher_id` is **always empty and cannot be otherwise** — a draft carrying a flag can never post, so there is no voucher to name. Frozen criterion #3.7 asks for it by name; that clause is unmeetable as written, not unmet |
| 7 | repeated review does not silently create duplicate dismissal events | **PASSED** (with a caveat the owner should read) | re-clicking writes one row (in memory and on disk). Retyping the same entry writes none — but only because the detector has been silenced by then, §3 |
| 8 | detector failure does not bypass existing accounting safety gates | **PASSED** | raise → HTTP 503, no decision, `post` refuses, 0 writes. Malformed output → same, through the real ranking code. Unknown detector → NOT_VALID, no question, refuses to post |
| 9 | existing non-detector review behaviour is unchanged | **PASSED** | 102 tests across `test_web.py` + the three Phase 6 files green against the new `flag_cap=3` app; a clean matched vendor still posts with provenance and the checks line |
| 10 | tests cover positive, negative and dismissal paths | **PASSED** | 34 new + 47 existing across the four Phase 6 files; the adversarial table in §4 covers 19 cases |

### Owner-named items

| item | verdict | evidence |
|---|---|---|
| 23 · `vendor_switch` review-flow invocation | **PASSED** | routes D1, D2 and F all fire through `/entry` → `/answer` over HTTP |
| 24 · durable dismissal storage | **PASSED** | file-backed `MemoryStore`, row written by the server thread |
| 25 · durable dismissal read-back | **PASSED** | second store on the same file, opened after teardown; every field asserted off the row |

Dismissal tests backed by the STORE (owner minimum: 3): **9** — 6 in
`tests/test_dismissal_durability.py`, 3 new (route F read-back, two vendors two rows, the
row outliving the flag). Four more assert on the store without reopening it.

---

## 2. The complete route enumeration

### The frame, proved mechanically

`vendor_switch` returns a flag on exactly three conditions, read straight off the
function:

```
index.lookup(party).status == "match"          one indexed account, call it `usual`
proposed.debit_account != usual
index.times_posted(party, usual) >= 2
```

So every route is a way for a voucher to reach `detectors.run` with a debit leg that is
not the vendor's one indexed account. Three exact-set AST assertions bound the search,
and each fails on the commit that adds a caller:

| scan | result |
|---|---|
| `detectors.run` call sites in `accountant/` | `pipeline.evaluate` (product) · `score/calibration.py::measure` · `score/harness.py::_evaluate_one` (offline proof track, no draft, no screen, no Tally) |
| `pipeline.evaluate` call sites in `accountant/` | `pipeline.run` · `web/app.py::_run` · `web/app.py::do_POST` |
| writers of a draft's debit leg | `pipeline.build_draft` (from `propose_account`) · `pipeline.answer` (from a human answer) — `evaluate` touches only the credit leg |

### Leaf 1 — `/entry` and `pipeline.run`: CLOSED, three ways

| case | debit leg | why it cannot fire |
|---|---|---|
| vendor MATCH | `usual` | `propose_account` and `vendor_switch` read the SAME `vendor_account` rows (`_one` vs `_all`, one table). Equal by construction, not by luck |
| vendor NO_MATCH / CONFLICTED | `""` | the index is not MATCH, so condition 1 fails |
| party empty | `""` | `bootstrap` drops any voucher whose vendor key is empty, so the empty key never reaches the store; a book made only of blank-party rows reports EMPTY_VENDOR_INDEX and is not askable |

**No book can fire the detector on the entry path.** The earlier audit's first claim was
right about this and wrong about what followed from it.

### Leaf 2 — `/answer`: every question, and which leg its answer writes

`/answer` refuses any value not in `Decision.question_options`, and `pipeline.answer`
chooses the leg by problem id (`funding_is_named` → credit, everything else → debit).
The set of questions is enumerable from source: `problems.QUESTION_FOR` (6 entries),
`problems.find`'s two `which_account` forms, and `problems._from_flag`'s four.

| problem | question | offered values | leg written | can fire? |
|---|---|---|---|---|
| `amount_is_positive` | `how_much` | RETYPE | — (handled before evaluate) | no |
| `party_is_named` | `who_was_it` | RETYPE | — | no |
| `gst_not_larger_than_amount` | `tax_bigger_than_total` | RETYPE | — | no |
| `funding_is_named` | `how_paid` | Cash / Bank | **credit** | no |
| `accounts_exist` | `which_purpose` | chart expense accounts + HANDOVER | **debit** | **YES — routes D1, D2** |
| `accounts_differ` | `which_purpose` | chart expense accounts + HANDOVER | **debit** | **YES — route F** |
| `which_account` (NO_MATCH) | `which_purpose` | chart expense accounts + HANDOVER | debit | no — vendor is not MATCH |
| `which_account` (CONFLICTED) | `which_purpose_narrowed` | the conflicted accounts + HANDOVER | debit | no — vendor is not MATCH |
| `vendor_switch` | `different_from_usual` | YES / `usual` | none / debit = `usual` | no — both silence it |
| `magnitude`, `first_use`, `gst_anomaly` | YES / RETYPE | not accounts | — | no |

HANDOVER is offered by three of these and never reaches `pipeline.answer`.

### The live routes

| route | condition | chart | pinned by |
|---|---|---|---|
| **D1** | the vendor's EXPENSE ledger is no longer in the chart; `accounts_exist` asks; the answer contradicts the history | stale | `tests/test_first_detector.py` (pre-existing) |
| **D2** | the vendor's FUNDING ledger is no longer in the chart; `accounts_exist` still asks "what did you get?", whose answer rewrites the DEBIT leg the person never got wrong | stale, in the credit direction | `test_the_detector_fires_when_the_missing_ledger_is_the_funding_one` (**new**) |
| **F** | both legs resolve to ONE ledger, so `accounts_differ` fails on an untouched entry — bank charges debited to Bank and paid from Bank | **complete** | `test_the_detector_fires_on_a_complete_chart_when_both_legs_are_one_ledger` (**new**) |
| **G** | a hand-made POST files an offered value under a different `problem` id, so it lands on the other leg | any | `test_a_hand_made_post_can_reach_the_detector_through_the_unvalidated_problem` (**new**) |

**Verdict on gap (a): the earlier audit's refutation was correct. Route F is real.**
The claim that only a stale chart reaches the detector is FALSE. Route F needs no renamed
ledger, no deleted ledger and no migration — a bank-charges vendor in an ordinary small
book is enough, and `test_route_f_needs_no_account_missing_from_the_chart` asserts every
ledger either leg names is in the chart the app read out of Tally, before and after.

Route D2 is new here and reads differently from D1: the expense leg was never wrong, and
a closed bank account makes the person re-answer it anyway.

---

## 3. The dismissed-marker question

### What the audit said

> dismiss a flag, change the draft, and the flag returns undismissed

### What actually happens

Measured, not argued. **The marker does not come back undismissed. THE FLAG GOES, and
the marker goes with it.**

```
/answer     evaluate FIRST, then record_correction   <- the fix that made route D/F reachable
            the correction adds a SECOND account to this vendor's row
            one account is MATCH; two is CONFLICTED
            vendor_switch returns nothing for a CONFLICTED vendor
next        evaluate on the same draft finds no flag at all
```

Sequence, over HTTP, route F:

| step | flag on page | `data-dismissed` | `Draft.dismissed` | durable rows | writes |
|---|---|---|---|---|---|
| `/entry` | no | — | `[]` | 0 | 0 |
| `/answer` "Purchases" | **yes** | `false` | `[]` | 0 | 0 |
| `/dismiss` | yes | `true` | `["vendor_switch"]` | 1 | 0 |
| `/answer` YES (re-evaluates) | **no** | absent | `["vendor_switch"]` | 1 | 0 |

Three consequences, each pinned:

1. **The concern leaves the screen without anybody resolving it.** The person dismissed
   it, answered the next question, and it vanished.
2. **`Draft.dismissed` is process-local.** No column, no reader, nothing rebuilds it. The
   EVENT is durable; the STATE is not. A restart shows a fresh, unmarked flag while the
   row recording that somebody looked is still on the file.
3. **The detector cannot fire twice for one vendor in one memory.** Retyping the
   identical entry produces a fresh draft with an empty marker list — if the flag could
   fire again it would fire UNDISMISSED and a second row could be written for the same
   concern. It cannot, because the vendor is CONFLICTED from the first answer onwards.
   Criterion 7 therefore holds, but by silencing rather than by recognising the repeat.

### Which way I think it should go, and why

**The flag should survive the correction that produced it, and the dismissed marker
should survive with it.**

- The correction is *evidence the person just supplied*. Feeding it back into the
  comparison the detector is making destroys the comparison — the same defect that was
  fixed on 2026-08-09 by moving `record_correction` after `evaluate`, arriving one
  request later instead of one line later. The fix moved the collision from "always" to
  "on the second evaluation"; it did not remove it.
- The asymmetry is the tell. `vendor_switch` fires **once**, on the response to a single
  request, and is gone. A detector whose output has a lifetime of one HTTP response is
  not a review surface; it is a toast notification.
- The concrete cost: an entry can currently end at VALID and post with a
  `vendor_switch` concern recorded only in the log, because by the time the last
  question is answered the flag is no longer in `draft.flags` and no longer in
  `draft.problems`. Nothing bad has been measured yet on the three live routes (every
  one of them ends UNCLEAR or NOT_VALID, write count 0 everywhere), but the property
  holding is a coincidence of those routes, not a gate.

**Cheapest fix that keeps the safety property**: compute the flags against the memory as
it was at the START of this draft — snapshot the index on the draft at `build_draft`
time and pass that to `detectors.run` — so a person's answer to *this* entry can never
be the history *this* entry is judged against. `record_correction` keeps working
unchanged for the next entry.

**Owner question, not a code question**: whether a dismissed marker should carry across
a change to the entry at all. If the person dismisses a concern about "Purchases" and
then changes the leg to "Rent", the new concern is a different concern and arguably
deserves a fresh, undismissed flag. Today's `dismissed` list is keyed on the DETECTOR
NAME alone, so it would mark the new one as already looked at. Keying it on
`(detector, debit_account)` would fix that, and it is a one-line change to a field I do
not own.

---

## 4. The adversarial set

Write count is zero on every row where a flag is outstanding. The one non-zero write is
the clean control, which is supposed to post.

| input | evidence | sev | rank | question | jargon | dropped | dismissal | final decision | WRITES | run id |
|---|---|---|---|---|---|---|---|---|---|---|
| no anomaly | (no flag) | - | 0 flags | (none) | clean | - | not offered | valid, posted | **1** | run_bcbd724beba34fd88e8aa714b6c410d5 |
| fires once (route F) | HDFC Charges posted to Bank 6 times; this one goes to Purchases | 3 | 1 of 1 | With HDFC Charges it's usually from the bank — 6 times so far. Is this one different? | clean | 0 | marked dismissed | unclear | 0 | run_6937bc665b2c4d90a3486b30beda6b8e |
| fires repeatedly | (no second flag — the vendor is CONFLICTED after the first answer) | - | - | (none) | clean | 0 | nothing to dismiss | not_valid | 0 | run_c06c8f26d55748a4b1ff5548588efb97 |
| two detectors one concern | HDFC Charges posted to Bank 6 times; this one goes to Freight & Transport; **also** Freight & Transport has never been used in this company across 6 posted vouchers | 3 | 1 of 1 after dedupe (2 raw) | (unit call) | n/a | 0 | n/a | n/a | 0 | n/a |
| no evidence | refused: `detector 'vendor_switch' fired without a reason` | - | - | - | n/a | - | n/a | ValueError at construction | 0 | n/a |
| no question / unknown detector | a meteor hit the warehouse | 4 | 1 of 1 | **None** | n/a — no question exists | 0 | offered, changes nothing | not_valid | 0 | n/a |
| question contains an account name (stale chart, jargon `usual`) | Sharma Traders posted to Old Ledger 6 times; this one goes to Purchases | 3 | 1 of 1 | With Sharma Traders it's usually **the same thing** — 6 times so far. Is this one different? | clean | 0 | marked dismissed | unclear | 0 | run_3306006d06a34618bac3c1df48722095 |
| question contains jargon | `is_jargon("Old Ledger")` is True, so the question falls back to "the same thing" rather than printing it | - | - | as above | clean | 0 | — | — | 0 | same run |
| ranking tie (equal severity) | first_use; magnitude | 2,2 | first_use then magnitude, identical in both input orders | (unit call) | n/a | 0 | n/a | n/a | 0 | n/a |
| rank changes with the voucher id | two flags, same detector, same severity | 3,3 | v-aaa then v-zzz | (unit call) | n/a | 0 | n/a | n/a | 0 | n/a |
| per-batch cap | three flags, `cap=2` | 3,2,1 | high, middle kept | (unit call) | n/a | **1** | n/a | n/a | 0 | n/a |
| dismissal does not authorise posting | HDFC Charges posted to Bank 6 times; this one goes to Purchases | 3 | 1 of 1 | (as route F) | clean | 0 | marked dismissed; `decision.post=False` | unclear | 0 | run_dbc271b6520c4fbf85746e7f86b8fd20 |
| wrong-company history | A says `('Purchases',)`, B says `('Rent',)` | - | - | - | n/a | - | n/a | refused: `memory for company 'Someone Else Ltd Books' was passed to a draft for …` | 0 | n/a |
| stale history (closed funding ledger, route D2) | Sharma Traders posted to Purchases 6 times; this one goes to Repairs & Maintenance | 3 | 1 of 1 | With Sharma Traders it's usually stuff you'll sell on — 6 times so far. Is this one different? | clean | 0 | marked dismissed | unclear | 0 | run_a6a2614ea950417cbd94d99b3773d515 |
| missing vendor history | (no flag) | - | 0 flags | How did you pay? | clean | 0 | not offered | unclear | 0 | run_dd1ab08afd4140ae9c8d7d8a5f035beb |
| ambiguous vendor | (no flag) | - | 0 flags | (none) | clean | 0 | not offered | not_valid | 0 | run_967ae743431f49e8b65c6527efbfb56c |
| invalid draft carrying a flag (float amount) | HDFC Charges posted to Bank 6 times; this one goes to Purchases | 3 | 1 of 1 | none — NOT_VALID asks nothing | n/a | 0 | offered; changes nothing | not_valid; `post` refused: `outcome is not_valid`; page renders "(not an amount)" | 0 | n/a |
| detector raises | (never returned one) | - | - | none — the request is refused | n/a | - | n/a | **HTTP 503**, "could not be finished", no internals leaked | 0 | run_7cdffe5ad3914b4aa5e7946ccf672c1a |
| malformed detector output | (never returned one) | - | - | none — the request is refused | n/a | - | n/a | **HTTP 503**, through the REAL ranking code (`-f.severity` on a `str`) | 0 | run_072771603d074945a488bb8e0dc5cc99 |

Run ids differ per row because each row spins up its own server; they are recorded so a
row in the action log can be tied back to the measurement that produced it.

---

## 5. Out of scope, found on the way — REPORTED, NOT PINNED

**`/answer` validates the value and not the problem, and the problem chooses the ledger
leg.** `accountant/web/app.py::do_POST` checks `value in decision.question_options` and
then passes `form["problem"]` straight to `pipeline.answer`, which uses it to decide
whether the answer lands on the debit or the credit leg.

Measured, on route F's book: a POST carrying `problem=funding_is_named` and
`value=Purchases` — an offered value, filed under a problem nobody asked — sets
`credit_account = "Purchases"`, leaves `debit_account = "Bank"`, satisfies every check,
reaches VALID and **POSTS**, `posted_tally_id = "TALLY-1"`, write count 1. Money
"came from" the Purchases ledger.

This is not a detector defect and it is not pinned in `tests/test_phase6_exits.py`,
because pinning a hole as correct is how a hole survives a review. It needs a change to
`accountant/web/app.py`, which another agent owns:

```python
# alongside the existing question_options check
offered_problem = d.decision.question_id if d.decision else ""
if offered_problem and problem != offered_problem:
    -> 400, same shape as the value refusal
```

`Decision` does not currently carry the problem id it asked about; `question_options` is
already there and the id would sit beside it.

---

## 6. Suite state

`COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider`

- `tests/test_phase6_exits.py` — **34 passed**
- the four Phase 6 files plus `tests/test_web.py` — **103 passed**
- whole suite — **1752 passed, 13 failed, 5 xfailed**

Ruff and pyright (strict) are clean on the new file.

None of the 13 failures is in Phase 6 work. Twelve are in files another agent added to
this branch during this session — `test_company_routes.py`, `test_company_unicode.py`,
`test_contract_differences.py`, `test_reversal_recovery.py` — and fail identically with
this file removed from the run. The thirteenth,
`test_reverse_all_cli.py::test_only_the_command_imports_above_the_connector_boundary`, is
a live regression from an uncommitted edit to `accountant/tallyio/factory.py`, which has
gained an import of `accountant.memory.identity` above the connector boundary. That is
somebody else's in-flight change and is flagged here only so it is not lost.
