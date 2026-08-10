# Phase 7 — evidence report

One row per requirement: measured, target, gap, evidence, status. Written
2026-08-10 on `phase7/adapter-contract`, re-measured at `fa7ba97`, on top of
`origin/main` at `1ca65a9` ("D-06: live Tally wins over stale memory").

This report is the record of what was OBSERVED. Where something was not
observed it says so, and it never lets a local run stand in for a gate.

**One row changed status since `dd65b8c` and it is the one that mattered.** A
reader outage over HTTP was **BLOCKED**; it is now **PASS**, 3/3, driven over a
real socket. Nothing in this document is BLOCKED any more. Nothing about GST
changed: this system still cannot post a tax line, every GST row here is a
safety result, and the section "Not measured" says exactly which parts of GST
do not exist.

---

## The five words, and why they are not interchangeable

Blurring these is how a phase gets called done.

| word | what it means | what it does NOT mean |
| --- | --- | --- |
| **measured** | a number was produced by a run whose provenance was asserted | that the number is good |
| **not measured** | nobody ran it. There is no number | that it would fail, or that it would pass |
| **invalidated** | it WAS run, and the run turned out not to describe the code under test | that it failed |
| **blocked** | the correct behaviour is written down and pinned, and it cannot be reached from here | that it is unimportant, or that it is nearly done |
| **passed** | the exit observable was seen, by a named test, on this code | that CI agrees — CI is a separate authority |

## Status labels

Every result below carries exactly one:
`PASS` · `FAIL` · `BLOCKED` · `NOT_MEASURED` · `INVALIDATED` · `GITHUB_REQUIRED`.

**`GITHUB_REQUIRED` is not a soft label.** Mutation score, changed-line
coverage, full-suite coverage, `pr-fast`, `pr-full`, `ci-gate`, security,
dependency scan and workflow validation are GitHub's answer. None of them has
been run. A green local suite is not any of them and is never reported as one.

---

## Provenance — why these numbers describe this checkout

Asserted before every measurement in this report:

```python
from pathlib import Path

import accountant

assert str(Path(accountant.__file__).resolve()).startswith(str(Path.cwd().resolve()))
```

```
accountant.__file__ = .../scratchpad/wt-phase7/accountant/__init__.py
cwd                 = .../scratchpad/wt-phase7
PROVENANCE OK
branch              = phase7/adapter-contract
commit              = fa7ba97   (previous round: dd65b8c; baseline: cb6348e)
```

**The guard was checked by breaking it, not by trusting it — and it caught a
real run, twice.** Run from `scratchpad/` instead of the worktree, the same
assertion fails:

```
AssertionError: WRONG CHECKOUT: `accountant` resolved to
/Users/tanveersidhu/ACCOUNTANT/accountant/__init__.py, which is not under the
current working directory .../scratchpad
```

It fired again on the `fa7ba97` measurement round. The measurement script lives
outside the worktree, so `sys.path[0]` was its own directory and the FIRST run
imported the main checkout's `accountant` rather than this branch's. That run
was discarded as **INVALIDATED**, the path was fixed, and every number in this
document comes from the rerun. This is the second time the guard has caught a
live mistake rather than a hypothetical one.

From the wrong directory the editable install resolves `accountant` to the MAIN
checkout, and a before/after comparison would read the same unchanged files
twice and show whatever was hoped for. Any Phase 7 number produced without this
assertion is **INVALIDATED**, not evidence.

**Note on the worktree venv.** `wt-phase7/.venv` is an untracked symlink to
`/Users/tanveersidhu/ACCOUNTANT/.venv`. Every command below was still issued
against the interpreter by absolute path, so the symlink changes nothing about
which files were executed — the provenance assertion above is what settles that,
and it was run inside the worktree.

---

## The three exit verdicts

| exit | claim | measured | target | gap | evidence | status |
| --- | --- | --- | --- | --- | --- | --- |
| **7.1** | swapping the backend changes no code outside `accountant/extract/` | **0 sites, 0 names**, over 45 modules | 0 | **none** | `tests/test_adapter_contract.py:789`, `:774`, `:811`, `:923`, `:830`; ratchet at `:771`; new rule at `:868` | **PASS** |
| **7.2** | a backend outage returns every field `not_found` with a stated reason, and the person types the entry instead | **13 scenarios**, 147 cases; 10 through the pipeline with 7 properties, 3 over HTTP with 5 | 10 scenarios | **none** | `tests/test_extract_outage.py:304`, `:324`, `:334`, `:413`, `:444`, `:752`, `:777`, `:823`, `:851`, `:894`, `:908` | **PASS** |
| **7.3** | a static test fails if a reader appears in `accountant/extract/` | **6 guards, 28 cases** | ≥1 guard | **none** | `tests/test_no_reader.py:187`, `:205`, `:305`, `:322`, `:419`, `:475` | **PASS** |

All three of the control plane's declared Phase 7 exit criteria are met on this
branch. The findings that sit OUTSIDE those three are rows in their own right
below, not footnotes to a pass.

---

## The exits as exact counts, target beside measurement

Where a target does not match how the suite is actually organised, the real
number is reported and the mismatch is named. Nothing was split, renamed or
padded to reach a figure.

### 7.1 — adapter / backend swap

| required | target | **measured** |
| --- | --- | --- |
| adapter contract tests | 25/25 | **47/47** |
| backend swap tests | 10/10 | **24/24** — 10 behavioural + 14 structural |
| interchangeable backends | 2/2 | **2/2** — `stub_backend`, `service_backend` |
| non-extract changes for a backend replacement | 0 | **0** |

Counted off the section banners `tests/test_adapter_contract.py` already had,
so the split is the file's own and not a judgement made afterwards:

```
  47  THE RECORD CONTRACT + the one place a backend is chosen  (206-493)
  10  THE SWAP, BEHAVIOURALLY                                  (494-636)
  14  THE SWAP, STRUCTURALLY                                   (637-961)
  15  MALFORMED ANSWERS                                        (962-1040)
   9  THE GST DEFECT                                           (1041-)
  ---
  95  collected
```

**"Backend swap = 10" is exactly the behavioural half.** Reporting the swap as
10 would undercount by the 14 structural tests, and those are the ones that
actually settle exit 7.1 — a behavioural test passes just as happily with a
selection site present, which was MEASURED as mutant MU3b.

### 7.2 — outage fallback

| required | target | **measured** |
| --- | --- | --- |
| general outage scenarios safe | 10/10 | **10/10** |
| HTTP outage scenarios safe | 3/3 | **3/3** |
| explicit fallback results | 13/13 | **13/13** |
| reasons recorded | 13/13 | **13/13** |
| silent blanks | 0 | **0** |
| unsafe VALID | 0 | **0** |
| automatic posts | 0 | **0** |

### 7.3 — no reader

| required | target | **measured** |
| --- | --- | --- |
| OCR imports | 0 | **0** |
| image-reading imports | 0 | **0** |
| layout-analysis imports | 0 | **0** |
| reader dependencies | 0 | **0** — `project.dependencies == []` |
| forbidden AST findings | 0 | **0** |
| no-reader test | PASS | **PASS** — 6 guards, 28 cases |

The three import figures are one measurement and not three. The guard is an
ALLOWLIST — `sys.stdlib_module_names` plus `accountant` — so zero third-party
imports of any kind is zero OCR, zero image and zero layout imports, and it also
covers the library nobody has thought of yet. A banned-list guard would be
defeated by any one of them.

### GST safety bridge

| required | target | **measured** |
| --- | --- | --- |
| GST safety tests, ordinary, no marker | 4/4 PASS | **4/4 PASS** |
| remaining GST xfails | 0 | **0** — no `xfail` string in either GST file |
| unsafe GST VALID | 0 | **0**, over 30 sweep cases |
| unsafe GST posts | 0 | **0**, over 30 sweep cases |

---

## 7.1 — the backend swap, measured

The AST scan counts concrete-backend references in every module under
`accountant/` except `accountant/extract/` itself.

| | `backend_sites()` | files | concrete names | backends derived |
| --- | --- | --- | --- | --- |
| at `27333e9` | `{'accountant/web/app.py': ['TypedTextExtractor']}` | 1 | 1 | 4 |
| at `dd65b8c` | `{}` | **0** | **0** | 4 |
| at `fa7ba97` | `{}` | **0** | **0** | **5** |

45 modules outside the package were scanned. The derived list grew to five —
`GuardedExtractor` joined it, because it defines `extract` and the scan derives
rather than hand-keeps — and the count of sites did not move. That is the point
of the derivation: a backend added today is covered without anybody remembering.

Verified two ways that do not share a failure mode: the AST scan, and a plain
text search for the backend class names across `accountant/` excluding
`accountant/extract/` — no hits.

`accountant/web/app.py:40` imports `default_extractor` and `guarded`;
`accountant/web/app.py:713` calls them; `:1303` uses the stored result. The
lever is `accountant/extract/registry.py:86`, `DEFAULT_BACKEND`.

**The ratchet is at zero.** `KNOWN_SELECTION_SITES` is the empty frozenset at
`tests/test_adapter_contract.py:771`, and the assertion is `== 0`, not `<= 1`.
There is no allowlisted file for a new selection site to hide inside.

**The ratchet was verified by breaking it** — mutant MU3b. A WORKING `app.py`
that imports and constructs `TypedTextExtractor` directly:

```
FAILED test_backend_selection_happens_nowhere_outside_the_package
FAILED test_the_core_takes_only_the_contract_from_the_extraction_package
FAILED test_the_measured_cost_of_a_backend_swap_is_no_line_outside_the_package
3 failed, 91 passed
```

**91 passed** is the number that matters. Every behavioural test in that file is
perfectly happy with the regression. That is the measured demonstration of why a
structural guard was needed, and it is not an argument — it is a run.

**Two widenings, both stated plainly, and the second was CHECKED rather than
assumed.** `CONTRACT` is the set of names a core module may depend on.
`default_extractor` is in it; `guarded` joined it at `fa7ba97`. Both are
`(...) -> Extractor` and neither takes or returns a named backend, so a module
calling either still cannot be made to change by choosing a different one.

The check: `test_the_core_takes_only_the_contract_from_the_extraction_package`
**FAILED** the moment `accountant/web/app.py` imported `guarded`, naming the
file and the name. That is the guard working, and the widening was made
knowingly rather than discovered later.

**What was NOT widened, which is the distinction that matters.**
`KNOWN_SELECTION_SITES` is still the empty set and
`test_backend_selection_happens_nowhere_outside_the_package` passed unchanged
through the whole change. CONTRACT says which ABSTRACT names the core may take;
the ratchet says how many CONCRETE backends it may name, and that bound is zero
and did not move. A new test at `tests/test_adapter_contract.py:868` now fails
if a backend class is ever added to CONTRACT — closing the cheapest way to
silence the allowlist check, which is to add the offending name to the
allowlist.

---

## 7.2 — the thirteen outage scenarios

Ten through `pipeline.run`, three over real HTTP against the running web app.

| # | scenario | surface | status |
| --- | --- | --- | --- |
| 1 | unavailable | pipeline | **PASS** |
| 2 | timeout | pipeline | **PASS** |
| 3 | malformed response | pipeline | **PASS** |
| 4 | partial response | pipeline | **PASS** |
| 5 | authentication failure | pipeline | **PASS** |
| 6 | rate limit | pipeline | **PASS** |
| 7 | empty response | pipeline | **PASS** |
| 8 | connection refused | pipeline | **PASS** |
| 9 | a response about a different document | pipeline | **PASS** |
| 10 | a response missing the named fields | pipeline | **PASS** |
| 11 | unavailable | **HTTP** | **PASS** |
| 12 | timeout | **HTTP** | **PASS** |
| 13 | malformed response | **HTTP** | **PASS** |

The seven properties, each asserted per pipeline scenario: every named field
explicitly `not_found`; the reason stored on the record; the reason visible on
the draft; no silent blank; nothing raises; no automatic Tally post; zero
vouchers and the trial balance unchanged **in exact paise**. The person can then
type the entry in by hand, and that entry posts —
`tests/test_extract_outage.py:413`.

The five properties, each asserted per HTTP scenario: explicit safe fallback,
the reason recorded, no silent blank, no unsafe VALID, no automatic post — plus
two more that only the HTTP surface can check: the page is an answer rather
than *"Something in Accountant Dad broke"*, and the question on it has real
answers on it.

| | scenarios | explicit fallback | reasons recorded | silent blanks | unsafe VALID | automatic posts |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 10 | 10/10 | 10/10 | 0 | 0 | 0 |
| HTTP | 3 | 3/3 | 3/3 | 0 | 0 | 0 |
| **total** | **13** | **13/13** | **13/13** | **0** | **0** | **0** |

Counted by `tests/test_extract_outage.py:908`, which drives all thirteen and
asserts a dictionary of counts — so the 13/13 is a figure a run produced, not
one added up by hand from a list of test names.

`tests/test_extract_outage.py` — **147 passed**.

Proved load-bearing by seven mutants across two rounds. MU7, dropping the reason
from the outage record, turns **38** red. MU8, letting the record claim a value
instead of `not_found`, turns **11** red. MU-H1 to MU-H5 cover the HTTP seam and
are in their own section below; all five were killed.

---

## 7.3 — no reader in the package

| guard | line | asks | status |
| --- | --- | --- | --- |
| imports | `:187` | does the package import an OCR / image / layout library | **PASS** |
| identifiers | `:205` | does it name one | **PASS** |
| what it may TOUCH | `:305` | allowlist: stdlib and `accountant.*` only, minus the reaching ones | **PASS** |
| what it may CALL | `:322` | `open`, `exec`, `eval`, `compile`, `__import__` | **PASS** |
| what the project DECLARES | `:419` | `dependencies == []`, and no reader in any group | **PASS** |
| what the package SHIPS | `:475` | no model weights or trained data files | **PASS** |

An allowlist, not a list of banned libraries: a banned-list guard is defeated by
any library nobody thought of. `tests/test_no_reader.py` — **28 passed**.

Proved load-bearing by three mutants: MU1 (`import subprocess`), MU5
(`def deskew`), MU4 (`model.traineddata` shipped in the package), MU6
(`pytesseract` declared in `pyproject.toml`). Each turned its own guard red and
nothing else.

A consequence worth naming: **no vendor SDK can be imported inside
`accountant/extract/`.** The selection criterion is not "best SDK", it is "plain
HTTPS JSON API".

---

## The GST defect — FIXED, and the recorded blocker was wrong twice

### The four marked tests: 4/4 now PASS as ordinary tests

The brief was Option B: repair, not report. All four markers are gone and all
four pass with no marker, run twice with identical results.

| test:line | required | actual | status |
| --- | --- | --- | --- |
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` `:1190` | outcome is not `VALID` | `NOT_VALID` | **PASS** |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` `:1203` | `UNCLEAR`/`NOT_VALID`, words mention the tax | `NOT_VALID`; the reason names GST and the tax line | **PASS** |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` `:1219` | `VALID` means the connector will take it | asserted both ways; no write-ahead row for the refused bill | **PASS** |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` `:1321` | HTTP 200, explains the tax | HTTP 200, no "broke", tax named | **PASS** |

### Classification and repair, per test

All four were the same defect seen from four angles, so they share one
classification and one repair.

```
classification   CODE_DEFECT
                 The invariant "missing required tax lines -> UNCLEAR or
                 NOT_VALID -> no automatic Tally post" was enforced at the wire
                 and nowhere else. The decision order had no check to decide on.

repair           accountant/schema.py:112       Voucher.needs_tax_lines
                 accountant/checks.py:110       tax_lines_can_be_posted
                 accountant/checks.py:165       registered in ALL_CHECKS
                 accountant/problems.py:32      added to UNANSWERABLE_CHECKS
                 accountant/tallyio/real.py:903 reads the same expression

not changed      accountant/pipeline.py. The decision order did not need
                 changing; it needed a check to decide on.
```

Test 3 additionally carried a **TEST_DEFECT**: a `pytest.skip` that fired the
moment the code was fixed, deleting the test at exactly the point its named
invariant became guardable. It was rewritten to assert the invariant in both
directions and now also forbids a write-ahead row for a refused entry. The five
required elements are recorded in the test's own docstring.

### The blocker was wrong twice, and both are kept

1. **`BLOCKED_BY_D06`.** D-06 landed as `1ca65a9`, changed
   `accountant/pipeline.py` for stale VENDOR memory, and touched neither GST nor
   tax:

   ```
   $ git diff 27333e9 1ca65a9 -- accountant/pipeline.py | grep -ciE 'gst|tax'
   0
   ```

2. **"the blocker is Phase 8 GST rules work plus an accounting-policy
   question."** Also wrong, and the more expensive error, because it made a
   two-line rule look like a quarter of statutory engineering and it held for a
   full document revision. POSTING a tax line is Phase 8. REFUSING to call a
   bill VALID when its tax cannot be posted is one check. The policy question
   never needed answering, because the answer to "can this system build ANY tax
   line" is no.

   The lesson is not "D-06 was mis-recorded". It is that a blocker was believed
   because it had been written down, and nobody re-derived it. The measurement
   that settled it took under a minute.

### The 30-case safety sweep — 30/30

`tests/test_gst_safety_sweep.py`, **43 passed**. Every case runs end to end
through `pipeline.run` against `FakeTally`, which calls `RealTally`'s own
`check_writable`, so a refusal is the connector's and not a restatement.

| required | target | measured | status |
| --- | --- | --- | --- |
| missing-tax → UNCLEAR or NOT_VALID | 10/10 | **10/10**, all NOT_VALID | **PASS** |
| incomplete-tax → UNCLEAR or NOT_VALID | 10/10 | **10/10**, all NOT_VALID | **PASS** |
| valid-tax → the expected valid result | 10/10 | **10/10**, VALID and posted | **PASS** |
| unsafe VALID results | 0 | **0** | **PASS** |
| unsafe Tally posts | 0 | **0** | **PASS** |
| connector refusals after VALID | 0 | **0** | **PASS** |
| silent blanks | 0 | **0** | **PASS** |

Not required, also measured: **0** write-ahead rows opened across the 20
unsafe-arm cases, and **0** paise of movement on the trial balance.

The sweep is load-bearing. Removing `tax_lines_can_be_posted` from `ALL_CHECKS`
(mutant MU-GST) turns 21 tests red and errors 9 more.

**What arm C is, and what it is not.** Arm C is "the tax question does not
arise": `tax_paise` absent, so no tax line is required and the entry posts. It
is **NOT** "a GST bill that posts with CGST/SGST/IGST lines on it". No such case
exists in this system and none was invented for the sweep — no rate, no tax
ledger, no intra- versus inter-state rule, no place of supply. That reading of
the third arm is **NOT_MEASURED**, with that as the reason.

Arm C is also the disconfirming arm. Without it, an implementation that refused
every bill in the system would score 20/20 on the first two arms.

### The honest summary

A GST bill is now handled safely as a DECISION and safely as a WRITE. It is
refused, the person is told the bill carries GST and that Accountant Dad cannot
post a tax line, and they are asked to enter that one in Tally themselves.

**That is safe. It is not GST support.** The product still cannot post a bill
with tax on it. What changed is that it now says so, in advance, instead of
promising and then breaking.

### The GST truth, verbatim

```
GST unsafe-VALID prevention                 = PASS
GST safe refusal                            = PASS
GST bill with tax lines successfully posted = NOT_MEASURED
GST posting rate                            = NOT_MEASURED
CGST/SGST/IGST split                        = NOT_IMPLEMENTED
place-of-supply rules                       = NOT_IMPLEMENTED
GST ledger mapping                          = NOT_IMPLEMENTED
GST rules corpus                            = the next phase
```

**Arm C of the 30-case sweep is "tax correctly absent, or an unsupported tax
state".** It is **not** "a GST bill with tax lines successfully posted". No such
case exists in this system and none was invented. Arm C is the disconfirming
arm and it is what makes the other twenty mean anything: without it, a system
that refuses every bill scores 20/20.

Nowhere in this document, in `artifacts/phase7_exits.md`, or in
`docs/CONTROL_PLANE.yaml` is GST support recorded as PASS. Every GST `PASS` in
all three is a safety result and is labelled as one.

---

## A reader outage over HTTP — was BLOCKED, now PASS

### The block, and the two reasons given for it — one wrong, one narrow

This row read **BLOCKED** at `dd65b8c`. The mechanism was real:

```
web/app.py called registry.default_extractor() INSIDE the request handler, so
the backend was chosen per request and app.configure() took no extractor. There
was no seam through which a test could hand the RUNNING app a failing backend.
```

Two reasons for deferring were recorded, and the record is kept rather than
tidied:

1. **Wrong, and already corrected once.** "Adding the parameter is a change to
   the web app beyond the one line exit 7.1 allows." Exit 7.1 is about backend
   SELECTION outside the package; a parameter defaulting to
   `default_extractor()` names no backend. The AST guard says so — measured
   after the change, `backend_sites() == {}`, exactly as before it.
2. **Narrow, and overruled.** "It changes the web app's public signature, which
   belongs to whoever next opens that file deliberately." The owner ruled on
   2026-08-10 that this branch is that deliberate opening.

### What was built

| file:line | what |
| --- | --- |
| `accountant/web/app.py:637` | `configure(..., extractor: Extractor \| None = None)` |
| `accountant/web/app.py:249` | `Runtime.extractor`, a field with **no default** |
| `accountant/web/app.py:713` | `guarded(default_extractor() if extractor is None else extractor)` |
| `accountant/web/app.py:1303` | `_run` uses `live.extractor` and builds nothing |
| `accountant/extract/registry.py:152` | `GuardedExtractor` |
| `accountant/extract/registry.py:225` | `guarded()`, a function |

Three design points, each with the failure it avoids.

**The route does not instantiate anything.** `default_extractor()` per request
meant the handler decided what read the bill, so "which backend is this
deployment on" had two answers — the one `configure()` was given and the one
the route made.

**The guard is reached through a function.** The AST scan derives backends from
the package: a class defining `extract` is one. `GuardedExtractor` defines
`extract`, so it was derived as a fifth backend the moment it existed, and
`web/app.py` spelling that class would have been a selection site. Measured
list: `GuardedExtractor, ServiceExtractor, StubExtractor, TypedTextExtractor,
UnavailableExtractor`. Measured sites outside the package: `{}`.

**`connect()` did NOT get the parameter.** No caller needs one, and this
repository already carries the note that a parameter no caller supplies is not
a feature — `flag_cap` sat on `pipeline.evaluate` for the whole of Phase 6 and
was never passed.

### The guard closes two failures, and the second is the commoner one

| failure | why it is not hypothetical | what the guard does |
| --- | --- | --- |
| the backend RAISED | `ServiceExtractor` promises never to raise; an injected object promises nothing, and `pipeline.build_draft` has no `try` | `service.reason_for(exc)` → this outage's own sentence, every field `not_found` |
| the backend answered with a NON-RECORD | returning `None` on failure is one of the commonest third-party client shapes | `MALFORMED`, naming what arrived instead |

Without the guard the first is HTTP 503 *"Something in Accountant Dad broke"* —
measured, as mutant MU-H4 — and the second is an `AttributeError` two frames
later, which is the same 503 with even less to go on.

`KeyboardInterrupt` and `SystemExit` are not caught. Somebody is stopping the
process and a tidy record would fight them.

`_whatever_it_returned` exists for one reason worth stating: pyright strict
narrows an assignment to the callee's annotated return type, so calling
`extract` directly makes the `isinstance` check provably dead and strict mode
rejects it. The check is not dead — the annotation on somebody else's object is
a promise, not a fact, and that helper is where it stops being treated as one.

### The three scenarios, measured over a real socket at `fa7ba97`

| # | scenario | arrives by | the sentence |
| --- | --- | --- | --- |
| 1 | unavailable | **raising** `ConnectionError` | the reading service is not available |
| 2 | timeout | **raising** `TimeoutError` | the reading service did not answer in time |
| 3 | malformed response | a real `ServiceExtractor` with a broken transport; **no raise** | the reading service sent an answer we cannot use |

Two shapes on purpose. Only-raising would prove the guard and say nothing about
the shipped adapter; only-reporting would leave the guard unexercised on the
HTTP path, which is the same as not having measured it.

| property | target | measured | test |
| --- | --- | --- | --- |
| explicit safe fallback | 3/3 | **3/3** | `tests/test_extract_outage.py:752` |
| reasons recorded | 3/3 | **3/3** | `:777` |
| no silent blank | 3/3 | **3/3** | `:823` |
| no unsafe VALID | 3/3 | **3/3** | `:851` |
| no automatic post | 3/3 | **3/3** | `:894` |
| answered, not reported as a breakage | 3/3 | **3/3**, HTTP 200 | `:861` |
| the injected backend is the one that ran | 1/1 | **PASS** | `:1034` |
| the default path is unchanged | 1/1 | **PASS**, still posts | `:1057` |

"Reason recorded" is asserted in three places, because stored is not visible:
on `record.per_field_source`, on `Draft.provenance`, and in the rendered
*"Where each field came from"* table, parsed out of the HTML the person got.

The durable row is checked by fetching the home page rather than by reading
`MemoryStore` from the test thread. SQLite hands a connection to the thread
that opened it, and the store is opened on the serving thread; the home page
renders the log ON that thread, which is both correct and stronger evidence —
it is what the person sees.

### The complete matrix — 13 scenarios

| | scenarios | explicit fallback | reasons recorded | silent blanks | unsafe VALID | automatic posts |
| --- | --- | --- | --- | --- | --- | --- |
| through `pipeline.run` | 10 | 10/10 | 10/10 | 0 | 0 | 0 |
| over HTTP | 3 | 3/3 | 3/3 | 0 | 0 | 0 |
| **total** | **13** | **13/13** | **13/13** | **0** | **0** | **0** |

Status: **PASS**.

---

## The backend comparison

**Outcome: third-party backend selection is the owner's, and NOT_MEASURED here.**

See `artifacts/extraction_backends.md` — **which is not committed anywhere at
the time of writing.** It exists only as an untracked file in the main working
copy. It was NOT added to this branch: it is 1011 lines this agent did not
write, and several other branches are being integrated in parallel. Flagged
rather than quietly adopted or quietly ignored — an untracked file is one
`git clean` away from gone.

Five findings from that document that bear on Phase 7:

1. `accountant/extract/` may import stdlib and `accountant.*` only, enforced by
   `tests/test_no_reader.py`. **No vendor SDK can be imported there.**
2. **No vendor publishes a number comparable to the 95-per-100-per-field bar**,
   and none will. It can only be settled by measuring on the owner's own bills.
3. **Not one backend returns a CGST/SGST/IGST split or an HSN code.** Every
   option needs custom GST post-processing, at a cost that is
   vendor-independent. This is now doubly relevant: the fix above refuses every
   bill carrying tax, so tax post-processing is on the critical path to a
   product that can post an Indian bill at all.
4. A large class of Indian bills — GST e-invoices with a signed IRN and QR code
   — **may need no OCR at all.**
5. `D-23` is still `OPEN` and its stated default is **typed text only**. If D-23
   resolves that way, none of this is needed.

Status: **NOT_MEASURED, by design.** No accuracy claim is made for any backend.

---

## Mutants, round two — the HTTP seam. 5 applied, 5 killed, 0 survived

A seam written today is unproven by a green suite today. Each mutation went
into the real file, ran against `tests/test_extract_outage.py`,
`tests/test_adapter_contract.py` and `tests/test_web.py` — 275 tests, green
baseline — and was restored with `git checkout --`. Everything was committed at
`fa7ba97` first, so the restore is exact; `git diff HEAD` was empty afterwards.

| # | mutation | result | what it establishes |
| --- | --- | --- | --- |
| MU-H1 | the route ignores the configured extractor: `_run` calls `default_extractor()` again | **KILLED** — 26 failed, 249 passed | the seam is load-bearing; the injected backend is the one that runs |
| MU-H2 | a failure returns a silent blank: every source becomes a bare `not_found` | **KILLED** — 22 failed, 253 passed | the reason is load-bearing, not decoration |
| MU-H3 | a failure reports a generic reason instead of this outage's own | **KILLED** — 6 failed, 269 passed | two outages stay distinguishable to the person |
| MU-H4 | the guard is removed: `guarded()` returns the backend unchanged | **KILLED** — 23 failed, 252 passed | a raising backend really does reach the 503 without it, so the guard is not redundant with `ServiceExtractor`'s own promise |
| MU-H5 | the guard stops checking the answer is a record | **KILLED** — 1 failed, 274 passed | the `None`-returning-backend case is covered, by exactly one test, and it is real |

MU-H1 and MU-H2 are the two the brief required. MU-H5's single failure is
reported as a single failure rather than dressed up: one test covers that
branch, and one is enough to kill it, but it is a thinner margin than the
others and saying so is the point of counting.

---

## Mutants, round one — 9 applied, 9 killed, 0 survived

| # | mutation | result |
| --- | --- | --- |
| MU1 | `import subprocess` in `accountant/extract/service.py` | RED — 1 failed |
| MU2 | concrete backend named from `accountant/decide.py` | RED — 4 failed |
| MU3b | working `web/app.py` naming `TypedTextExtractor` | RED — 3 failed, 91 passed |
| MU4 | `model.traineddata` shipped in the package | RED — 1 failed |
| MU5 | `def deskew` in `accountant/extract/adapter.py` | RED — 1 failed |
| MU6 | `pytesseract` declared in `pyproject.toml` | RED — 2 failed |
| MU7 | outage record loses its reason | RED — 38 failed |
| MU8 | outage record claims a value instead of `not_found` | RED — 11 failed |
| MU-GST | `tax_lines_can_be_posted` removed from `ALL_CHECKS` | RED — 21 failed, 9 errors |

Each was applied to the real file and restored afterwards; the restore was
verified by re-running the same tests and seeing them green again. One earlier
attempt at MU3 changed only the import line, leaving `default_extractor()`
unbound — that mutant broke the app rather than merely naming a backend, so it
was re-run as MU3b with both lines changed. The imperfect first attempt is
recorded rather than quietly replaced.

---

## Results, labelled

### Measured here

| what | result |
| --- | --- |
| full suite, `COVERAGE_CORE=pytrace`, at `fa7ba97` | **PASS** — 2091 passed, 6 xfailed, 135s |
| full suite at `dd65b8c`, twice, identical | **PASS** — 2055 passed, 6 xfailed, 118s |
| baseline before any Phase 7 change (`cb6348e`) | 2008 passed, 10 xfailed, 0 failed, 0 xpassed |
| `tests/test_adapter_contract.py` | **PASS** — 95 passed, 0 xfailed |
| `tests/test_extract_outage.py` | **PASS** — 147 passed |
| `tests/test_no_reader.py` | **PASS** — 28 passed |
| `tests/test_gst_safety_sweep.py` | **PASS** — 43 passed |
| AST selection sites | **measured** — 0 files, 0 names, over 45 modules |
| ratchet sensitivity (MU3b) | **PASS** — 3 tests red, 91 green, then restored |
| 9 round-one guard mutants | **PASS** — 9 applied, 9 killed, 0 survived |
| 5 HTTP-seam mutants | **PASS** — 5 applied, 5 killed, 0 survived |
| `ruff check .` | **PASS** |
| `ruff format --check .` | **PASS** — 148 files |
| `pyright` (strict) | **PASS** — 0 errors, 0 warnings, 0 informations |
| `scripts/validate_project_truth.py` | **PASS** — 30 checks, 30 passed, before and after the control-plane edit |
| `scripts/guards` | **PASS** — all guards passed |
| `git diff --check` | **PASS** — clean |

### Blocked

Nothing. The one BLOCKED row in the previous version of this document — a
reader outage over HTTP — is measured above at 3/3.

### Not measured

| what | why |
| --- | --- |
| a GST bill that POSTS with tax lines | no tax line can be built; nothing was invented to fake one |
| GST posting rate | **NOT_MEASURED** — there is no posting path for a taxed bill to have a rate about |
| CGST/SGST/IGST split | **NOT_IMPLEMENTED** |
| place-of-supply rules | **NOT_IMPLEMENTED** |
| GST ledger mapping | **NOT_IMPLEMENTED** |
| the GST rules corpus | belongs to the next phase |
| third-party backend accuracy | owner decision; no backend is connected |
| mutation score at this HEAD | not re-run on this branch |
| question rate | **NOT_MEASURED**. Nobody ran it, and 0 is not the answer |

**Arm C of the 30-case sweep is "tax correctly absent, or an unsupported tax
state".** It is **not** "a GST bill with tax lines successfully posted". No such
case exists in this system and none was invented. Without arm C the other twenty
mean nothing — a system that refuses every bill would score 20/20.

### GitHub is the only authority for these

| gate | status |
| --- | --- |
| mutation score ≥ 90 | **GITHUB_REQUIRED** |
| changed-line coverage ≥ 90 | **GITHUB_REQUIRED** |
| full-suite coverage | **GITHUB_REQUIRED** |
| `pr-fast` · `pr-full` · `ci-gate` | **GITHUB_REQUIRED** |
| security · dependency scan · workflow validation | **GITHUB_REQUIRED** |

---

## Two things a reader should not conclude from this document

**That Phase 7 is merged.** It is not. This branch is committed locally; no PR
has been opened, on the owner's instruction, because the Phase 7 PR must also
carry D-05's legal-identity policy and raw-evidence hardening, and several other
branches have to be integrated first.

**That the control plane still disagrees.** It did, and it no longer does.
`docs/CONTROL_PLANE.yaml` recorded Phase 7 as `NOT_STARTED` with all three exit
criteria `met: false` and the evidence *"None of the three exit observables has
been attempted"*. Corrected at `fa7ba97` on the owner's instruction. The
before/after, so the change is auditable rather than silent:

| | before | after |
| --- | --- | --- |
| `phases[id: "7"].status` | `NOT_STARTED` | `PARTIALLY_VERIFIED` |
| the three `exit_criteria[].met` | `false`, `false`, `false` | `true`, `true`, `true`, each with its own measurement as evidence |
| a `phase7:` record | absent | a new top-level key, `status: READY_FOR_PR` |

Three things about that edit are worth stating.

**`PARTIALLY_VERIFIED`, not `PASSED`.** The exit observables were seen. Every
gate that blocks a merge has not run and no pull request exists. A green local
suite is not a gate.

**`allowed_statuses` was NOT widened.** Six values, and every phase status is
checked against them. The richer record needs `READY_FOR_PR`, `NOT_MEASURED`
and `NOT_IMPLEMENTED`, which are not among them. The cheap move is to add them
to the list; that check exists to guard the phase table, and widening it to fit
one record is the weakening this project forbids. The record lives under a
separate top-level key instead, where it binds nothing else.

**The line numbers in the instruction were checked against the file, not
trusted.** A mid-task correction warned that lines 360-378 sit inside phase 6.
In this checkout they do not — phase 6 is 318-359, phase 7 is 360-378, phase 8
starts at 380 — so the block was located by reading and the phase 6, 8, 9 and
10 blocks were diffed byte for byte afterwards and are identical.
`scripts/validate_project_truth.py` was 30/30 before the edit and is 30/30
after it.

**That any GST feature works.** It does not. See "Not measured".
