# Company safety report

Branch `closure/flag-cap-and-truth`. Evidence class throughout: **FAKETALLY over
real HTTP**. Nothing here says anything about real TallyPrime.

Files written: `tests/test_company_routes.py` (25 tests),
`tests/test_company_unicode.py` (29 tests). No file in `accountant/**`,
`docs/**`, `ci/**`, `scripts/**`, `.github/**` was touched, and no existing test
was edited or weakened.

---

## 1. Headline

Three defects found in `accountant/**`. All three are proven by a test that
fails today. None is fixed here.

| # | Defect | Class | Reachable from `serve()` today |
|---|--------|-------|-------------------------------|
| D-A | A bulk reversal previewed for one company is confirmed against **that** company after the app is bound to another. Vouchers are deleted from books the session was never bound to, and the audit row is filed under the other company's key. | **Wrong-company WRITE** | No — needs two `configure()` calls in one process |
| D-B | `/dismiss` (and any route that re-renders a cached draft) draws a draft belonging to one company inside a page whose header names another. | **Wrong-company DISPLAY** | No — same precondition as D-A |
| D-C | One visible company name typed in two Unicode encodings is treated as two different companies. The refusal that results prints two identical-looking names and an instruction that cannot be followed. | Fails closed, but **unusable** | **Yes** — `ACCOUNTANT_COMPANY` typed on macOS is NFD; Tally on Windows returns NFC |

Zero wrong-company writes were found on any path reachable from the shipped
entry point. D-A is reachable only through the public injection seam.

---

## 2. Per item

| # | Item | Verdict |
|---|------|---------|
| 1 | wrong-company display | **ALREADY PROVEN** by `test_the_home_page_reads_the_configured_company_and_not_the_default`. **NEWLY PROVEN** across every route by `test_no_route_lets_a_form_field_choose_a_different_company` (5), `test_a_query_string_cannot_choose_a_different_company`, `test_a_company_that_was_never_connected_to_is_named_by_nothing`. **DEFECT FOUND — D-B**, `test_a_draft_built_for_another_company_is_never_drawn_under_ours` |
| 2 | company identity propagation | **ALREADY PROVEN** by `test_every_function_that_reaches_tally_takes_its_company_from_runtime` and `test_a_non_default_company_can_be_used_end_to_end` |
| 3 | hardcoded company removal | **ALREADY PROVEN** by `test_no_request_handler_reads_the_module_default`, kept honest by `test_the_two_configuration_readers_really_do_read_it` |
| 4 | home page company | **ALREADY PROVEN** by `test_the_home_page_reads_the_configured_company_and_not_the_default`. **NEWLY PROVEN** with a second company open the whole time: `test_a_company_that_was_never_connected_to_is_named_by_nothing` |
| 5 | Trial Balance company | **NEWLY PROVEN** by `test_the_home_page_never_shows_the_other_open_companys_trial_balance`. The other company's chart holds `Freight Inward`, which ours does not, so a leak is a substring with one possible source |
| 6 | party/company display | **NEWLY PROVEN** by `test_the_home_page_never_shows_the_other_open_companys_parties` (party **and** operation id) |
| 7 | reverse-all company | **ALREADY PROVEN** for the honest path by `test_bulk_reversal_previews_the_configured_company`. **NEWLY PROVEN** that the preview lists only our own vouchers when a marked voucher of ours also sits in the other company: `test_the_bulk_reversal_preview_lists_only_our_own_vouchers`. **DEFECT FOUND — D-A**, `test_a_bulk_reversal_previewed_for_another_company_never_reverses_it` |
| 8 | health company | **ALREADY PROVEN** by `test_health_reports_the_configured_company` and `test_health_says_which_two_companies_disagree_without_raising`. **NEWLY PROVEN** that it never names the other open company: `test_health_names_our_company_and_never_the_other_open_one` |
| 9 | audit trail company | **ALREADY PROVEN** by `test_the_audit_trail_is_written_under_the_configured_company`. **NEWLY PROVEN** that the on-screen activity list is scoped too: `test_the_activity_log_never_shows_the_other_open_companys_rows` |
| 10 | Unicode NFC/NFD | **ALREADY PROVEN** for one pair by `test_an_accented_company_name_never_borrows_an_unaccented_companys_scope`. **NEWLY PROVEN** across full width, three zero-width characters, non-breaking space, tab, newline, doubled and edge whitespace, case, and end-to-end over HTTP for an NFD company. **DEFECT FOUND — D-C**, `test_the_same_company_typed_in_two_encodings_is_never_two_companies` |
| 11 | normalised company-key collision | **ALREADY PROVEN** at `bootstrap` level by `tests/test_company_collision.py`. **NEWLY PROVEN** through the running app for all six documented pairs (`test_every_known_collider_pair_is_refused_by_the_running_app_naming_both`), plus two collider mechanisms not previously covered — zero-width characters and a fold that emits a combining mark |

---

## 3. The defects, exactly

### D-A — wrong-company WRITE via a stale bulk-reversal batch

**Failing test**
`tests/test_company_routes.py::test_a_bulk_reversal_previewed_for_another_company_never_reverses_it`

**Exact failure**

```
AssertionError: a batch previewed for 'Pathak Cement Works' was confirmed by a
session bound to 'Sable Iron Traders' and it deleted 'Pathak Cement Works''s
voucher. That is a wrong-company WRITE into a real business's books
assert 0 == 1
 +  where 0 = len(())
 +    where () = list_our_vouchers('Pathak Cement Works')
```

**Mechanism**

- `accountant/web/app.py`, `Handler.do_POST`, `/reverse-all` branch:
  `shown = BATCHES.pop(form.get("batch", ""), None)`, then
  `reversal.execute(reversal.confirm(shown), live.client,
  company_key=live.memory.identity.key, ...)`.
- `accountant/reversal.py::_drive` reverses in **`batch.company`**
  (`_classify(client, batch.company, outcome.operation_id)`), and
  `_settle` reads `client.trial_balance(batch.company)`.
- So the company that is CHANGED comes off the cached batch, and the company the
  change is RECORDED against comes off the live runtime. Nothing compares them.
- `BATCHES` is a module-level dict. `configure()` clears `_recorded_mismatches`
  and nothing else; `disconnect()` does the same. A batch therefore outlives the
  runtime it was made for.

**Measured consequence**: the voucher is gone from Alpha's books, Alpha's own
audit trail still says `posted` and never says `reversed`, and the three
`bulk_reverse` / `bulk_reversed` rows are filed under Bravo. The company that
was actually changed has no record of it.

**Why this is not caught by the existing guards**: `company_mismatch` compares
the runtime's own two identities, which agree. `Runtime.confirm_company` checks
that our stored row is still ours and that our company is still open in Tally.
`batch.company` is a third axis and no check looks at it.

**Suggested shape of a fix (owner's call)**: refuse in the `/reverse-all`
handler when `shown.company != live.company`, and clear `DRAFTS` and `BATCHES`
in `install()` so cached work cannot outlive its runtime. The refusal must name
both companies, like every other one in this module.

---

### D-B — wrong-company DISPLAY via a stale draft

**Failing test**
`tests/test_company_routes.py::test_a_draft_built_for_another_company_is_never_drawn_under_ours`

**Exact failure**

```
AssertionError: a page served for 'Sable Iron Traders' (HTTP 200) drew a draft
belonging to 'Pathak Cement Works', naming its party. Nothing scopes app.DRAFTS
by company
assert 'Sharma Traders' not in '<!doctype h...back</a></p>'
  'Sharma Traders' is contained here:
    y</td><td>Sharma Traders</td></tr><tr><td>Debit</td><td>Purchases</td></tr>
    <tr><td>Credit</td><td>Cash</td></tr><tr><td>Amount</td><td>₹4,200.00</td>
```

**Mechanism**: `DRAFTS` is keyed by draft id alone. `/dismiss` looks one up,
renders it with `render_decision`, and `page()` wraps the result in a header
naming `runtime().company`. The response is HTTP 200 and carries the other
company's party, both ledgers, the amount, the Tally id and an "Undo this entry"
form holding that draft's operation id.

**Blast radius, measured**: display only. Pressing the offered undo button hits
`/reverse`, which looks the operation up in the *current* company and reports
`not_found` — proven by `test_reversing_an_operation_id_from_another_company_changes_nothing`.
Nothing was written to either company in this test.

**The write half of the same hole HOLDS**, and that is worth recording:
`test_a_draft_built_for_another_company_cannot_be_answered_into_ours` passes.
`pipeline.evaluate` refuses memory whose key is not the draft's company, so a
foreign draft cannot be answered into anybody's books. It arrives as the generic
"something in Accountant Dad broke" 503 rather than as a company refusal — the
last line of defence doing the first one's job — but no voucher moves.

---

### D-C — one visible company name, two encodings, two companies

**Failing test**
`tests/test_company_unicode.py::test_the_same_company_typed_in_two_encodings_is_never_two_companies`

**Exact failure**

```
AssertionError: the app could not read 'Café Exports' out of a Tally whose only
open company is the same visible name spelt 'Café Exports'
assert <BootstrapStatus.INCOMPLETE: 'incomplete'> is <BootstrapStatus.READY: 'ready'>
```

The two names in that message are byte-different and render identically.

**Mechanism**: `normalise_company` folds to NFC, so the *key* is correct and
identical for both spellings. Every comparison of the company **name**, however,
is an exact string comparison:

- `accountant/tallyio/factory.py::real_tally` — `if company not in companies:`
- `accountant/web/app.py::Runtime.confirm_company` — `if self.company not in open_now:`
- `accountant/web/app.py::Runtime.confirm_company` — `stored.identity.name != self.company`
- `accountant/memory/bootstrap.py::bootstrap` — `if company not in open_companies:`

**Reachability**: this is the one defect reachable from the shipped entry point.
`ACCOUNTANT_COMPANY` is typed by a person. macOS hands typed text to a program in
NFD; TallyPrime runs on Windows and returns the precomposed NFC spelling from
`list_companies`. The two never match.

**What the operator sees** (measured, via `confirm_company`):

```
REAL TALLY REQUIRED: no operation performed. 'Café Exports' is no longer open in
Tally. 1 company/companies are open: ['Café Exports']. Nothing was read and
nothing was written. Open 'Café Exports' in Tally again, or start this app again
for the company you mean to work in.
```

The third variant, measured separately: a second run of the *same* company typed
in the other encoding rewrites the store row, and the live runtime then refuses
with

```
our stored memory under key 'café_exports' now names company 'Café Exports', not
'Café Exports'. Another company whose name reduces to the same key has
overwritten our index ... Give one of those two companies a clearly different
name in Tally
```

There is only one company, and the instruction cannot be carried out.

**Severity**: fails CLOSED — nothing is read, nothing is written. It is an
availability and legibility defect, not a correctness one. But it cannot be got
past, and the message sends the reader to rename a company that does not exist.

**Suggested shape of a fix (owner's call)**: compare company names the same way
keys are compared — NFC-fold both sides, or compare `normalise_company(...)` —
in all four sites above.

---

## 4. Measured facts that are NOT defects

Recorded so the next reader does not re-derive them.

**Full width does not fold, and that is right.** `normalise_company("ＡＣＭＥ
Traders")` is `ａｃｍｅ_traders`, not `acme_traders`. NFC does not touch
compatibility forms; only NFKC would, and NFKC would merge distinctions. The two
companies key apart, both bootstrap, and each answers only for its own account.
Proven by `test_a_full_width_company_name_never_borrows_the_ascii_companys_books`.

**Zero-width characters are a collider, and the collision fails closed.**
`"Acme‍Traders"` keys as `acme_traders`, the same as `"Acme Traders"` — the
`‍` is neither `\w` nor `\s`, so `_PUNCT` turns it into a space. The two
names render *differently* (one word versus two) and share a key, which is the
dangerous direction. `bootstrap` refuses both while both are open and names
both, and because the message uses `{name!r}` the invisible character appears as
`‍` — the only rendering that tells a reader it is there. Proven for ZWJ,
ZWNJ and ZWSP by `test_an_invisible_character_hides_a_collision_and_the_pair_is_refused`.

**`casefold()` reintroduces the combining mark that the NFC fold was added to
keep out.** The docstring in `accountant/memory/identity.py` explains that NFC
runs first so a combining mark cannot reach `_PUNCT`. That invariant is not
established: `casefold` runs *after* the fold and is itself a source of marks.
U+0130 expands to `i` + U+0307, nothing recomposes it, and `_PUNCT` turns it
into a space. Measured:

```
normalise_company("İnci Traders") == "i_nci_traders"   # a break inside a word
normalise_company("İTC Traders")  == "i_tc_traders"    # == "I TC Traders"
```

The safety property still holds — the pair is refused at admission and both
names are given — so this is a wrong key, not a wrong write. Proven by
`test_a_fold_that_emits_a_combining_mark_still_reaches_the_punctuation_rule` and
`test_a_name_whose_casefold_emits_a_combining_mark_still_fails_closed`. Likelihood
in this market is low; the fix is to keep category-M characters out of `_PUNCT`
rather than to add a second normalisation pass, because `NFC("i" + U+0307)` does
not recompose.

**A name that normalises to nothing is refused, but not through the standard
refusal.** `CompanyIdentity.from_name` is called on the first line of
`bootstrap`, *outside* its `try`, so a name of emoji, punctuation, whitespace or
zero-width characters raises `ValueError: company name '...' carries no
identity` straight out of `bootstrap` and out of `app.configure`. Nothing is
read, nothing is stored, and no runtime is installed — proven by
`test_a_company_name_that_carries_no_identity_is_refused_and_reads_nothing`. It
is not prefixed `REAL TALLY REQUIRED`, so `serve()` would end on a traceback
rather than on the standard refusal sentence, and `__main__` catches only
`RealTallyRequired`. Not filed as a defect: the input is implausible from a real
Tally and the message already names the company.

**`/health` reports readiness from the last bootstrap and does not re-ask
Tally.** So after the company closes underneath us, `/health` still answers 200
with `ready: true` for the configured company while every other route answers
503. That is the documented decision in `Handler._confirm_company` — a readiness
endpoint that needs Tally to answer cannot report that Tally is not answering —
and it never names another company. Not filed as a defect.

---

## 5. Test counts against the floors

| Floor | Required | Before | Added here | Total |
|-------|----------|--------|-----------|-------|
| company identity tests | ≥ 20 | 39 — `test_company_identity.py` 22, `test_company_collision.py` 17 | 54 | 93 |
| Unicode tests | ≥ 10 | 3 company-name assertions in `test_adversarial_identity.py` | **29** (`test_company_unicode.py`) | 32 |
| wrong-company route tests | ≥ 10 | 8 over HTTP plus 1 renderer-level, in `test_company_identity.py` | **25** (`test_company_routes.py`) | 34 |

The company-identity floor was already met before this work started; the
shortfall was in the other two, and in per-route coverage.

**Result of the two new files in isolation**

```
tests/test_company_routes.py tests/test_company_unicode.py
3 failed, 51 passed
```

The three failures are D-A, D-B and D-C. Every other test in both files passes.

**Full suite**, `COVERAGE_CORE=pytrace python -m pytest -q`:

```
17 failed, 1665 passed, 2 xfailed in 118.30s
```

Three of those seventeen are mine and are the defects above. The other fourteen
are in `tests/test_contract_differences.py`, `tests/test_project_truth.py` and
`tests/test_reversal_recovery.py` — files that did not exist when this task
started and that changed twice during it, so the suite is a moving target right
now. That my two files are not the cause is measured, not assumed: running them
alongside those files produces exactly the failures those files produce alone.

---

## 6. What is still not proven

- Anything about real TallyPrime. Every result here is `FakeTally` behind real
  HTTP sockets. In particular, nothing says how a real Tally XML gateway encodes
  a non-ASCII company name in `list_companies`, which is the input D-C turns on.
- Concurrency. Every test drives one request at a time. Two browser tabs
  answering the same draft, or two processes bootstrapping colliding companies
  at the same instant, are not covered.
- D-A and D-B are proven through the public `configure()` seam. Whether a future
  route reaches the same state by another path is not, and cannot be, proven
  here.
