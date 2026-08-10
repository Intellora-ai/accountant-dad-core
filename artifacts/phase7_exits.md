# Phase 7 — the extraction adapter

Branch `phase7/adapter-contract`, on top of `main` at `1ca65a9` (D-06).
Last measured 2026-08-10 at `fa7ba97`.

> **WHAT CHANGED SINCE `dd65b8c`, IN ONE PARAGRAPH.** The HTTP reader outage was
> the one row in this document reading **BLOCKED**. The owner ruled it fixed in
> this branch rather than deferred to Phase 8, and it is: `app.configure()` now
> takes an extractor, `Runtime` holds it, the route uses it, and a backend
> failure becomes an explicit safe fallback. The outage matrix went from ten
> scenarios to thirteen and every figure below was re-measured on `fa7ba97`.
> `docs/CONTROL_PLANE.yaml` was corrected in the same commit and is no longer a
> contradiction — see the last section.
>
> **WHAT DID NOT CHANGE.** This system still cannot post a tax line. Everything
> the GST rows say is a SAFETY result. None of it is GST support.

The rule this phase serves: **we write an adapter, never a reader.** Reading a
bill is a commodity. TallyPrime 7.1 ships it. myBillBook gives it away. Others
sell it from free to about Rs 599 a month, unlimited. Choosing the right ACCOUNT
is the part nobody has solved. Building the solved half would have cost about a
month and delayed the unsolved half by the same.

Nothing in `accountant/extract/` reads a document. What was added is a second
backend that adapts somebody else's answer, a place for the choice of backend to
live, and the tests that make all three claims falsifiable.

## Status vocabulary

Exactly one label per row. Nothing else is used.

| label | meaning |
| --- | --- |
| PASS | the observable was seen, by a named test, on this code |
| FAIL | it was run and it did not hold |
| BLOCKED | cannot be reached from here at all, for a stated reason |
| NOT_MEASURED | nobody ran it. There is no number, in either direction |
| INVALIDATED | it WAS run, and the run turned out not to describe this code |
| GITHUB_REQUIRED | only GitHub can answer this. Nothing local is evidence |

**`GITHUB_REQUIRED` is not a soft label.** Mutation score, changed-line
coverage, full-suite coverage, `pr-fast`, `pr-full`, `ci-gate`, security,
dependency scan and workflow validation are GitHub's answer. None has been run.
A green local suite is not any of them and is never reported as one.

---

## The exits

| # | requirement | measured | target | gap | evidence | status |
| --- | --- | --- | --- | --- | --- | --- |
| 7.1 | swapping the backend changes NO code outside `accountant/extract/` | **0 sites, 0 names**, over 45 modules | 0 | none | `tests/test_adapter_contract.py:789` `test_backend_selection_happens_nowhere_outside_the_package`; ratchet at `:771` `KNOWN_SELECTION_SITES = frozenset()`; the lever is `accountant/extract/registry.py:86` `DEFAULT_BACKEND` | **PASS** |
| 7.2 | a backend outage returns every field `not_found` with a stated reason, and the person types the entry instead | **13 scenarios**, 10 through the pipeline + 3 over HTTP; 147 cases | 10 scenarios | none | `tests/test_extract_outage.py:304`, `:324`, `:334`, `:413`, `:444`, `:752`, `:777`, `:823`, `:851`, `:894`, `:908` | **PASS** |
| 7.3 | a STATIC test fails if a reader appears in `accountant/extract/` | **6 guards, 28 cases** | ≥1 guard | none | `tests/test_no_reader.py:187`, `:205`, `:305`, `:322`, `:419`, `:475` | **PASS** |
| GST | a GST bill must not reach VALID and then be refused by the connector | **fixed 2026-08-10**; 4/4 tests pass with no marker; 30/30 sweep | 4/4 and 30/30 | none | `tests/test_adapter_contract.py:1190`, `:1203`, `:1219`, `:1321`; `tests/test_gst_safety_sweep.py` | **PASS** |
| — | a reader outage over HTTP | **3/3 scenarios safe**, driven over a real socket | 3 | none | `tests/test_extract_outage.py:752` onward; the seam is `accountant/web/app.py:637`, `:713`, `:1303` | **PASS** (was BLOCKED at `dd65b8c`) |
| — | a GST bill that POSTS, with tax lines on it | no tax line can be built at all | next phase | the whole of GST posting | nothing here builds one, and nothing was invented | **NOT_MEASURED** |
| — | third-party backend accuracy | no backend is connected | owner's call | — | `artifacts/extraction_backends.md`, untracked | **NOT_MEASURED** |
| — | question rate | nobody ran it | — | — | — | **NOT_MEASURED** |

---

## 7.1 — the backend swap · PASS

### What was added

`accountant/extract/service.py` — `ServiceExtractor`. A second backend of a
genuinely different kind: it adapts the answer of a third-party reader service.
The transport is INJECTED (`ServiceCall = (bytes, mime, document_key) ->
answer`), so the module opens no socket and ships no client.

`accountant/extract/registry.py` — `DEFAULT_BACKEND`, `build(name)`,
`default_extractor()`. The one place a backend is chosen. `build` raises rather
than falling back, because a typo that quietly returns the default is a machine
reading bills with something other than what the deployment asked for.

### The structural proof, and why a behavioural one is not enough

The falsifying question for "a swap changes nothing outside the package" is:
**what test would fail if it did?** Nothing behavioural would. A second
`from accountant.extract.adapter import SomeBackend` in a module outside the
package leaves every behavioural test green. That is not a guess — it was
MEASURED, as mutant MU3b below: a working `app.py` naming `TypedTextExtractor`
directly turns exactly three structural tests red and leaves the other 91 tests
in that file green.

So the proof reads the AST:

1. **The list of backends is DERIVED, not hand-kept.** A class in
   `accountant/extract/` that defines `extract` is a backend. The `Extractor`
   Protocol is excluded by its base. A backend added tomorrow is covered
   without anybody remembering to add it.
2. **Every reference is counted** — `ast.Name`, `ast.Attribute` and `ast.alias`,
   so an import, a renamed import, a construction and a method call are all
   caught. Comments and docstrings are not read.
3. **Measured at `fa7ba97`:**

       backends derived     GuardedExtractor, ServiceExtractor, StubExtractor,
                            TypedTextExtractor, UnavailableExtractor
       modules scanned      45  (accountant/**, excluding accountant/extract/**)
       core sites           0
       whole-package sites  {}
       cost of a swap       0 lines outside the package

   The derived list grew from four to five and the site count did not move.
   `GuardedExtractor` was added to the list by the DERIVATION, not by anybody
   remembering — which is the property this design was chosen for, and it is
   also the reason `web/app.py` reaches the guard through the `guarded()`
   function: spelling the class there would have been a selection site.

   Confirmed independently of the AST scan by a plain text search for the
   class names across `accountant/`, excluding `accountant/extract/`: no hits.

4. **What the rest of the repository takes from the package:**

       accountant/pipeline.py       ExtractedRecord, Extractor, NOT_FOUND
       accountant/ingest/spend.py   NOT_FOUND
       accountant/web/app.py        Extractor, default_extractor, guarded

   Every one is the abstract contract. `accountant/web/app.py:40` imports
   `default_extractor` and `guarded`; `:713` calls them; `:1303` uses the
   stored result. No concrete backend is named. `pipeline.build_draft` and
   `pipeline.run` annotate `extractor: Extractor`, asserted from the AST at
   `tests/test_adapter_contract.py:830`.

`default_extractor` and, since `fa7ba97`, `guarded` are in `CONTRACT` — the set
of names a core module may depend on. Both are widenings and neither is a
weakening: both are `(...) -> Extractor`, neither takes or returns a named
backend, so a module calling either cannot be made to change by choosing a
different one.

**The `guarded` widening was checked, not assumed.**
`test_the_core_takes_only_the_contract_from_the_extraction_package` FAILED the
moment `web/app.py` imported the name, and the name was added deliberately
afterwards. `KNOWN_SELECTION_SITES` — the bound that actually carries exit 7.1 —
is still the empty set and did not move. A new test at
`tests/test_adapter_contract.py:868` now fails if a backend class is ever added
to `CONTRACT`, closing the cheapest way to silence the allowlist check.

---

## 7.2 — outage fallback · PASS

**Thirteen scenarios: ten through `pipeline.run`, three over real HTTP.** The
ten are below with seven properties each, all asserted separately — "it failed
safely" is four different properties wearing one sentence and only one of them
is about not crashing. The three HTTP scenarios have their own section, "A
reader outage over HTTP", further down; they were BLOCKED at `dd65b8c` and are
measured here.

| # | scenario | how it arrives | the sentence the person gets |
| --- | --- | --- | --- |
| 1 | unavailable | `ConnectionError` | the reading service is not available |
| 2 | timeout | `TimeoutError` | the reading service did not answer in time |
| 3 | malformed response | a string, a list, a number, `None`, non-name keys, or a field of the wrong type | the reading service sent an answer we cannot use |
| 4 | partial response | two of the four fields absent from the answer | the answer leaves fields out: nothing was said about total_paise, tax_paise |
| 5 | authentication failure | `ExtractionFailed` or `PermissionError` | we are not signed in to the reading service |
| 6 | rate limit | `ExtractionFailed` | the reading service is not taking more requests just now |
| 7 | empty response | `{}` | the reading service sent an empty answer |
| 8 | connection refused | `ConnectionRefusedError` | the reading service refused the connection |
| 9 | a response about a different document | the echoed key does not match | the answer is about a different bill: we asked about X and this answers about Y |
| 10 | a response missing the named fields | the service used its own key names | the answer leaves fields out: nothing was said about date, party, total_paise, tax_paise |

For each of the ten, proved:

1. all four named fields come back `None` and every source starts `not_found:`
2. the reason is this outage's own, on every field, and it is visible on
   `Draft.provenance` and on `Voucher.provenance` — stored is not the same as
   visible, and a reason that stops at the record reaches nobody
3. no source is blank and none is a bare `not_found` with no explanation
4. nothing raises, out of the extractor or out of `pipeline.run`
5. the person is asked something with real answers on it, and typing the entry
   afterwards posts, moving the trial balance by exactly +420000 / -420000
6. `posted_tally_id is None`
7. `list_our_vouchers` is empty, the voucher count is unchanged,
   `trial_balance` is byte-identical, `read_by_operation_id` returns `None`
8. one durable `blocked` row is written to the action log with a reason

### The design decision inside 7.2

**An answer we cannot fully account for is not a partial answer. It is a failed
one.** A field the service says it could not find comes back `null` — that is
an ANSWER, it becomes an explicit `not_found` for that one field, and such a
record posts perfectly well. A field the service does not mention at all is
different: nothing distinguishes "not on the bill" from "the service stopped
halfway". So the shape is checked as a conservation law — the fields we asked
about must equal the fields the answer accounts for — and a shortfall refuses
the whole response rather than trusting the half that arrived.

Same for a value of the wrong type. `"4200.00"` is refused, not parsed. A float
is refused, not rounded. `True` is refused, because `isinstance(True, int)` is
true and an unguarded check makes it one paise.

---

## 7.3 — no reader built · PASS

Six guards, 28 cases, at `tests/test_no_reader.py`.

| guard | line | what it reads | what it catches |
| --- | --- | --- | --- |
| imports | `:187` | `ast` import roots against `sys.stdlib_module_names` | any third-party library, present or not yet invented |
| identifiers | `:205` | `ast.Name` / `ast.Attribute` / def names | a reader written by hand, importing nothing |
| reach | `:305` | import roots against `subprocess`, `socket`, `ctypes`, `urllib`, `http`, `tempfile`, `shutil`, `importlib`, … | a reader that shells out, opens a socket, or loads a native library |
| calls | `:322` | bare-name calls | `open`, `exec`, `eval`, `compile`, `__import__` — a model has to live on disk |
| declared | `:419` | `pyproject.toml` | `project.dependencies == []`, and no dependency in ANY group naming reader work |
| shipped | `:475` | every file in the package | a `.traineddata`, a weight, a font, a sample page — none of which any AST scan would see |

An allowlist, not a list of banned libraries: a banned-list guard is defeated by
any library nobody thought of.

`dependencies = []` was the load-bearing fact the import allowlist rested on and
nothing checked it until the `declared` guard was added. A reader could have
arrived by widening the project rather than by widening the guard.

Each guard has a companion test proving it CAN fail, and one proving it does NOT
fire on prose about readers — this file and the package both discuss OCR by
name, and a guard that read comments would flag the docstring stating the rule.

A consequence worth naming, because it constrains the backend choice: **no
vendor SDK can be imported inside `accountant/extract/`.** The selection
criterion is not "best SDK", it is "plain HTTPS JSON API".

---

## The GST defect — FIXED 2026-08-10

### The unsafe path, as it was

```
POST /entry  text="paid Sharma Traders 4200 for cement including 18% GST"

  extraction        total_paise 420000, tax_paise 64068, source "typed_text"
  evaluate          VALID, "nothing unclear and nothing surprising"
  pipeline.post     write_attempted row written
  connector         REFUSES: "it carries GST of 64068 paise and this connector
                    builds no tax lines."
  pipeline.post     write_outcome_unknown row written, ValueError re-raised
  web               HTTP 503, "Something in Accountant Dad broke"

  trial balance     UNCHANGED
  our vouchers      ()
```

The connector was RIGHT to refuse. What was wrong is that the application said
VALID first: it promised a write the connector would not take, and an ordinary
bill with tax on it surfaced to the person as a breakage.

### Two wrong predictions, both this project's own, both kept

1. **`BLOCKED_BY_D06`.** D-06 landed as `1ca65a9` and did change
   `accountant/pipeline.py` — for stale vendor memory, not for tax.
   `git diff 27333e9 1ca65a9 -- accountant/pipeline.py | grep -ciE 'gst|tax'`
   returns **0**. All four tests failed on top of it exactly as before.

2. **"the blocker is Phase 8 GST rules work."** Also wrong, and the more
   expensive error, because it made a two-line rule look like a quarter of
   statutory engineering. POSTING a tax line is Phase 8. REFUSING to call a bill
   VALID when its tax cannot be posted is one check. The accounting-policy
   question — "what must a tax line contain before a bill carrying one may be
   VALID" — never had to be answered, because the answer to "can we build ANY
   tax line" is no.

### The fix, in full

| file:line | what |
| --- | --- |
| `accountant/schema.py:112` | `Voucher.needs_tax_lines` — the condition, written once |
| `accountant/checks.py:110` | `tax_lines_can_be_posted` — the application asks it before deciding |
| `accountant/checks.py:165` | registered in `ALL_CHECKS` |
| `accountant/problems.py:32` | added to `UNANSWERABLE_CHECKS`, so it hands over rather than asks |
| `accountant/tallyio/real.py:903` | `check_writable` now reads the same expression |

**One expression, two readers.** Two copies of `gst_paise is not None` is what
let the halves drift apart. It lives on `Voucher` because `accountant/schema.py`
is the one module both sides may import: `accountant/tallyio/` must not import
the product layer (correction C3, enforced at
`tests/test_reverse_all_cli.py:242`), and `accountant/checks.py` must not import
the connector. Asserted at `tests/test_gst_safety_sweep.py:507`.

`accountant/pipeline.py` is untouched. The decision order did not need changing;
it needed a check to decide on.

**Unanswerable, on purpose.** No answer a person can give makes this system able
to build a CGST/SGST/IGST line, so a question would spend one of their five on
something their answer cannot fix. The outcome is NOT_VALID with the tax named:

    tax_lines_can_be_posted: this bill carries GST of 64068 paise, and
    Accountant Dad cannot post a tax line yet — posting it would drop the tax
    and leave a wrong statutory entry, so please enter this one in Tally
    yourself

### The four tests: 4/4 PASS, no markers

Run as ordinary tests, twice, same result both times.

| test:line | required | actual | status |
| --- | --- | --- | --- |
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` `:1190` | outcome is not VALID | NOT_VALID | **PASS** |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` `:1203` | UNCLEAR/NOT_VALID and the words mention the tax | NOT_VALID, reason names GST and the tax line | **PASS** |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` `:1219` | VALID means the connector will take it | asserted both directions; no `write_attempted` row for the refused bill | **PASS** |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` `:1321` | HTTP 200, explains the tax | HTTP 200, no "broke", the reason names the tax | **PASS** |

### Three other tests corrected, none weakened

Each carries its old assertion, why it was wrong, the new assertion, the safety
impact and the new result, in its own docstring.

| test | old | why wrong | new |
| --- | --- | --- | --- |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` | `pytest.skip` when not VALID | it deleted itself the moment the defect was fixed; a skip is not a pass | both directions asserted, plus no write-ahead row for a refused entry |
| `test_a_gst_bill_writes_nothing_and_moves_the_trial_balance_by_zero_paise` `:1109` | `pytest.raises(ValueError)` | pinned HOW the bill was stopped, and the how was the defect | outcome + the same three numbers; satisfied by no exception from anywhere |
| `test_the_connector_refuses_a_gst_voucher_and_says_why` `:1078` | `pipeline.post(...)` expecting the connector's message | the application gate now refuses first, so it stopped reaching the connector | calls `write_voucher` directly — defence in depth, stated |
| `tests/test_detectors.py` `test_a_fired_detector_asks_and_never_refuses` | `all(p.answerable ...)` over EVERY problem | scope: the claim is about detector problems; it ranged over check problems too | flag-derived problems specifically, plus the refusing check named |

The fixtures were NOT altered. `gst=64068` stays in the detector test because it
is what makes `gst_anomaly` fire, and removing it would quietly drop a detector
from the test's reach.

### The 30-case safety sweep — 30/30

`tests/test_gst_safety_sweep.py`, 43 tests, all passing. Every case runs end to
end through `pipeline.run` against `FakeTally`, which calls `RealTally`'s own
`check_writable`, so a refusal is the connector's refusal and not a restatement.

| required | target | measured |
| --- | --- | --- |
| missing-tax → UNCLEAR or NOT_VALID | 10/10 | **10/10** (all NOT_VALID) |
| incomplete-tax → UNCLEAR or NOT_VALID | 10/10 | **10/10** (all NOT_VALID) |
| valid-tax → the expected valid result | 10/10 | **10/10** (VALID and posted) |
| unsafe VALID results | 0 | **0** |
| unsafe Tally posts | 0 | **0** |
| connector refusals after the application said VALID | 0 | **0** |
| silent blanks | 0 | **0** |

Also measured, and not required: **0** write-ahead rows opened for a refused
entry, across all 20 unsafe-arm cases.

**What "valid-tax" means here, stated plainly.** Arm C is "the tax question does
not arise": `tax_paise` is absent, so no tax line is required and the entry
posts. It is NOT "a GST bill that posts with CGST/SGST/IGST lines on it". **No
such case exists in this system and none was invented.** That reading of the
third arm is the NOT_MEASURED row at the top of this document.

Arm C is the disconfirming arm and it is why the other twenty mean anything:
without it, a system that refuses every bill scores 20/20.

The sweep spans both the shipped typed-text parser (10 cases) and a stubbed
backend (20 cases) that can produce tax states the parser cannot reach — a tax
of zero, a tax with no total behind it, a negative tax, a tax larger than the
bill. A fixture check asserts every case in an unsafe arm really does carry a
tax field, and every arm-C case really does not.

---

## A reader outage over HTTP — PASS. It was BLOCKED, and it is fixed.

### What the block actually was

```
web/app.py called registry.default_extractor() INSIDE the request handler.
The backend was chosen inside accountant/extract/, which satisfied exit 7.1 —
but it was chosen PER REQUEST, so nothing could hand the RUNNING app a failing
backend, and app.configure() took a client, an identity and a store.
```

Two routes were rejected and both are worth keeping written down. Editing
`DEFAULT_BACKEND` is patching a `Final` constant and proves something about the
patch. Building a failing extractor inside the route would test a branch the
shipped path does not have.

**The previous justification for deferring was itself corrected once, and the
correction stood.** This document used to say adding the parameter "is a change
to the web app beyond the one line exit 7.1 allows". That was wrong — exit 7.1
is about backend SELECTION appearing outside the package, and a parameter
defaulting to `default_extractor()` names no backend. The narrower reason given
instead was that it changes the web app's public signature and belongs to
whoever next opens that file deliberately. **The owner ruled on 2026-08-10 that
it be fixed in this branch.** It is.

### The seam, in four lines

| file:line | what |
| --- | --- |
| `accountant/web/app.py:637` | `configure(..., extractor: Extractor \| None = None)` |
| `accountant/web/app.py:249` | `Runtime.extractor` — a field, **no default** |
| `accountant/web/app.py:713` | `guarded(default_extractor() if extractor is None else extractor)` |
| `accountant/web/app.py:1303` | `_run` passes `live.extractor`, and builds nothing |
| `accountant/extract/registry.py:152` | `GuardedExtractor` — never raises, never blank |
| `accountant/extract/registry.py:225` | `guarded()` — a FUNCTION, which is what keeps 7.1 at zero |

**Why the guard is reached through a function and not a class name.** The AST
scan derives its list of backends from the package: a class defining `extract`
is a backend. `GuardedExtractor` defines `extract`, so the scan counted it as a
fifth backend the moment it was written — and `web/app.py` spelling that class
would have been a selection site. Measured after the change:
`backend_sites() == {}`. Unchanged.

### Why the guard has to exist at all

`pipeline.build_draft` calls `extract` with no `try` around it, and
`Handler.handle_one_request` turns any escaping exception into HTTP 503
*"Something in Accountant Dad broke"*. `ServiceExtractor` promises never to
raise; an object a deployment injects promises nothing. So the guard closes two
failures, not one:

| failure | what it looks like | what the guard does |
| --- | --- | --- |
| it raised | any `Exception` from a third-party backend | `service.reason_for(exc)` → this outage's own sentence |
| it answered with junk | a client that returns `None` on failure | `MALFORMED` naming what arrived instead of a record |

`KeyboardInterrupt` and `SystemExit` are deliberately not caught: somebody is
stopping the process, and a tidy record would fight them.

### The three scenarios, and why they are not all the same shape

| # | scenario | how it arrives | the sentence the person gets |
| --- | --- | --- | --- |
| 1 | unavailable | a backend that **raises** `ConnectionError` | the reading service is not available |
| 2 | timeout | a backend that **raises** `TimeoutError` | the reading service did not answer in time |
| 3 | malformed response | a real `ServiceExtractor` with a broken transport — it **does not raise** | the reading service sent an answer we cannot use |

Using only the raising kind would prove the guard and say nothing about the
shipped adapter. Using only the reporting kind would leave the guard
unexercised on the HTTP path, which is the same as not having measured it.

### Measured, over a real socket, at `fa7ba97`

| property | target | measured |
| --- | --- | --- |
| explicit safe fallback | 3/3 | **3/3** |
| reasons recorded | 3/3 | **3/3** |
| no silent blank | 3/3 | **3/3** |
| no unsafe VALID | 3/3 | **3/3** — all three UNCLEAR |
| no automatic post | 3/3 | **3/3** |
| HTTP status | 200 | **200**, and the page never says "broke" |

"Reason recorded" is checked in three places, because stored is not the same as
visible: on `record.per_field_source`, on `Draft.provenance`, and in the
rendered *"Where each field came from"* table the person is looking at.

### The complete outage matrix — 13 scenarios

| | scenarios | explicit fallback | reasons recorded | silent blanks | unsafe VALID | automatic posts |
| --- | --- | --- | --- | --- | --- | --- |
| through `pipeline.run` | 10 | 10/10 | 10/10 | 0 | 0 | 0 |
| over HTTP | 3 | 3/3 | 3/3 | 0 | 0 | 0 |
| **total** | **13** | **13/13** | **13/13** | **0** | **0** | **0** |

Counted by `tests/test_extract_outage.py:908`, which drives all thirteen and
asserts the dictionary of counts, so the figure is measured rather than added
up by hand from a list of test names.

---

## The exits as exact counts

Every figure was produced by the run recorded in "Provenance" and not carried
forward from an earlier version of this document.

### Exit 7.1 — adapter / backend swap

| required | target given | **measured** | verdict |
| --- | --- | --- | --- |
| adapter contract tests | 25/25 | **47/47** | PASS, and the target is a floor, not the count |
| backend swap tests | 10/10 | **24/24** (10 behavioural + 14 structural) | PASS |
| interchangeable backends | 2/2 | **2/2** (`stub_backend`, `service_backend`) | PASS |
| non-extract changes for a backend swap | 0 | **0** | PASS |
| selection sites outside the package | 0 | **0**, over 45 modules | PASS |

**The two targets do not match how this suite is organised, and the real
numbers are reported instead of being made to fit.** The counts come from the
section banners `tests/test_adapter_contract.py` already had, not from a
judgement about what a test is "about":

```
  47  THE RECORD CONTRACT  +  the one place a backend is chosen   (lines 206-493)
  10  THE SWAP, BEHAVIOURALLY                                     (lines 494-636)
  14  THE SWAP, STRUCTURALLY                                      (lines 637-961)
  15  MALFORMED ANSWERS                                           (lines 962-1040)
   9  THE GST DEFECT                                              (line 1041 on)
  ---
  95  collected
```

"Backend swap = 10/10" matches the behavioural half exactly. The structural 14
are the half a behavioural test cannot reach, so reporting the swap as 10 would
undercount by the tests that actually settle exit 7.1. Nothing was split,
renamed or padded to reach any figure.

### Exit 7.2 — outage fallback

| required | target | **measured** |
| --- | --- | --- |
| general outage scenarios safe | 10/10 | **10/10** |
| HTTP outage scenarios safe | 3/3 | **3/3** |
| explicit fallback results | 13/13 | **13/13** |
| reasons recorded | 13/13 | **13/13** |
| silent blanks | 0 | **0** |
| unsafe VALID | 0 | **0** |
| automatic posts | 0 | **0** |

### Exit 7.3 — no reader

| required | target | **measured** |
| --- | --- | --- |
| OCR imports | 0 | **0** |
| image-reading imports | 0 | **0** |
| layout-analysis imports | 0 | **0** |
| reader dependencies | 0 | **0** (`project.dependencies == []`) |
| forbidden AST findings | 0 | **0** |
| imports that reach another program or a socket | 0 | **0** |
| non-source files shipped in the package | 0 | **0**, of 4 files |
| no-reader test | PASS | **PASS** — 28 cases, 6 guards |

The three import figures are one measurement, not three: the guard is an
ALLOWLIST of `sys.stdlib_module_names` plus `accountant`, so every third-party
import is caught whether or not anybody has heard of the library. Zero
third-party imports of any kind is therefore zero OCR, zero image and zero
layout imports, and it also covers the library nobody has thought of yet.

### GST safety bridge

| required | target | **measured** |
| --- | --- | --- |
| GST safety tests, as ordinary tests | 4/4 PASS | **4/4 PASS** |
| remaining GST xfails | 0 | **0** — no `xfail` string anywhere in either GST file |
| unsafe GST VALID | 0 | **0**, over 30 sweep cases |
| unsafe GST posts | 0 | **0**, over 30 sweep cases |

---

## Mutants — the HTTP seam, 5 applied, 5 killed, 0 survived

The seam is new, so a green suite proves nothing about it until the guard has
been made to fail. Every mutation was applied to the real file, run against
`tests/test_extract_outage.py`, `tests/test_adapter_contract.py` and
`tests/test_web.py` (275 tests, green baseline), and restored with
`git checkout --`. Everything was committed at `fa7ba97` first, so each restore
is exact — confirmed by `git diff HEAD` being empty afterwards.

| # | mutation | result | what it proves |
| --- | --- | --- | --- |
| MU-H1 | the route ignores the configured extractor: `_run` calls `default_extractor()` again | **KILLED** — 26 failed, 249 passed | the injected backend is the one that runs, and the seam is not decoration |
| MU-H2 | a failure returns a silent blank: every source becomes a bare `not_found` | **KILLED** — 22 failed, 253 passed | the reason is load-bearing, not cosmetic |
| MU-H3 | a failure reports a generic reason instead of this outage's own | **KILLED** — 6 failed, 269 passed | two different outages cannot be told apart by a person if this survives |
| MU-H4 | the guard is removed: `guarded()` hands the backend straight back | **KILLED** — 23 failed, 252 passed | a raising backend really would reach the 503 without it |
| MU-H5 | the guard stops checking the answer is a record | **KILLED** — 1 failed, 274 passed | the `None`-returning backend case is covered by exactly one test, and it is a real one |

MU-H1 and MU-H2 are the two the brief named. MU-H4 is the one that shows the
guard is not redundant with `ServiceExtractor`'s own promise.

---

## Mutants from the earlier rounds — 9 applied, 9 killed, 0 survived

A guard that has never failed is unproven whatever the green count is. Each
mutation was applied to the real file, the tests were run, and the file was
restored. Everything was committed first, so the restore is exact.

| # | mutation | result | killed by |
| --- | --- | --- | --- |
| MU1 | `import subprocess` inside `accountant/extract/service.py` | RED, 1 failed | `test_the_extraction_package_starts_no_other_program_and_opens_no_socket` |
| MU2 | name a concrete backend from a core module (`accountant/decide.py`) | RED, 4 failed | `test_swapping_the_backend_changes_no_module_in_the_core` + 3 |
| MU3b | a WORKING `web/app.py` that names `TypedTextExtractor` directly | RED, 3 failed, **91 other tests in the same file stayed green** | `test_backend_selection_happens_nowhere_outside_the_package`, `test_the_core_takes_only_the_contract_from_the_extraction_package`, `test_the_measured_cost_of_a_backend_swap_is_no_line_outside_the_package` |
| MU4 | ship `model.traineddata` inside the package | RED, 1 failed | `test_the_extraction_package_ships_source_and_nothing_else` |
| MU5 | `def deskew(page)` inside `accountant/extract/adapter.py` | RED, 1 failed | `test_no_module_in_the_extraction_package_names_the_work_of_reading` |
| MU6 | `dependencies = ["pytesseract>=0.3"]` in `pyproject.toml` | RED, 2 failed | `test_the_project_declares_no_runtime_dependency_at_all`, `test_no_dependency_in_any_group_names_a_document_reader` |
| MU7 | drop the reason from an outage record | RED, 38 failed | `tests/test_extract_outage.py` |
| MU8 | let an outage record claim a value instead of `not_found` | RED, 11 failed | `test_every_outage_leaves_every_named_field_explicitly_not_found` |
| MU-GST | remove `tax_lines_can_be_posted` from `ALL_CHECKS` | RED, 21 failed + 9 errors | the sweep and all four GST tests |

MU3b is the one that matters most for 7.1. It is the exact regression exit 7.1
exists to prevent, and it is invisible to every behavioural test in the file.

---

## Results, labelled

| what | how it was run | result |
| --- | --- | --- |
| full suite, `COVERAGE_CORE=pytrace` | local, `fa7ba97` | **PASS** — 2091 passed, 6 xfailed, 135s |
| full suite at `dd65b8c`, for comparison | local, twice, identical | **PASS** — 2055 passed, 6 xfailed |
| `tests/test_adapter_contract.py` | local | **PASS** — 95 passed, 0 xfailed |
| `tests/test_extract_outage.py` | local | **PASS** — 147 passed |
| `tests/test_no_reader.py` | local | **PASS** — 28 passed |
| `tests/test_gst_safety_sweep.py` | local | **PASS** — 43 passed |
| `ruff check .` | local | **PASS** — all checks passed |
| `ruff format --check .` | local | **PASS** — 148 files |
| `pyright` (strict) | local | **PASS** — 0 errors, 0 warnings, 0 informations |
| `scripts/validate_project_truth.py` | local, after the control-plane edit | **PASS** — 30 checks, 30 passed |
| `scripts/guards` | local | **PASS** — all guards passed |
| `git diff --check` | local | **PASS** — clean |
| HTTP seam mutants | local, 5 applied and restored | **PASS** — 5 killed, 0 survived |
| mutation score ≥ 90 | not run here | **GITHUB_REQUIRED** |
| changed-line coverage ≥ 90 | not run here | **GITHUB_REQUIRED** |
| full-suite coverage ≥ 90 | not run here | **GITHUB_REQUIRED** |
| pr-fast · pr-full · ci-gate | not run here | **GITHUB_REQUIRED** |
| security · dependency scan · workflow validation | not run here | **GITHUB_REQUIRED** |
| a reader outage over HTTP | 3/3 safe over a real socket | **PASS** (was BLOCKED) |
| a GST bill that posts with tax lines | not built, not invented | **NOT_MEASURED** |
| question rate | nobody measured it | **NOT_MEASURED** |

The suite grew by 36: 35 in `tests/test_extract_outage.py` and one in
`tests/test_adapter_contract.py` (the new rule that forbids adding a backend
class to `CONTRACT`). No test was removed, skipped or weakened.

### The six xfails that remain, none of them GST

Phase 7 removed four. The six left belong to other phases and are named so
nobody has to go looking:

| test | marker reason |
| --- | --- |
| `test_error_responses.py::test_a_well_formed_answer_from_something_else_is_never_read_as_books` ×3 | DEFECT E1 — `accountant/tallyio/real.py:1179` |
| `test_idempotency.py::test_an_operation_id_that_was_reversed_is_never_written_again` | DEFECT I1 — `accountant/pipeline.py:456` |
| `test_idempotency.py::test_a_duplicate_refusal_is_never_recorded_as_an_unknown_outcome` | DEFECT I2 — `accountant/pipeline.py:490` |
| `test_reversal_recovery.py::test_a_resume_writes_nothing_more_when_the_reconciliation_settled_nothing` | Defect D3, OPEN OWNER DECISION |

## Test counts against the minimums

| required | minimum | delivered |
| --- | --- | --- |
| adapter-contract | 25 | 47 |
| backend-swap | 10 | 24 (10 behavioural, 14 structural) |
| malformed-response | 5 | 15 |
| timeout/outage | 5 | 147 |
| outage scenarios | 10 | 13 — 10 with 7 properties each, 3 over HTTP with 5 |
| GST | — | 9 in `test_adapter_contract.py` + 43 in `test_gst_safety_sweep.py` |
| no-reader guards | — | 6 guards, 28 cases |

---

## The GST truth, recorded verbatim

This is the section to read if the only thing being taken from this document is
whether GST works. It does not.

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
case exists in this system, and none was invented. Arm C is the disconfirming
arm and it is why the other twenty mean anything: without it, a system that
refuses every bill scores 20/20.

What this system does with a bill carrying GST is refuse it and hand it to the
person, with the tax named. That is safe. It is not GST support, and the two
must never be written as one PASS.

---

## The control plane — corrected in this branch

`docs/CONTROL_PLANE.yaml` recorded Phase 7 as `NOT_STARTED` with all three exit
criteria `met: false` and the evidence *"None of the three exit observables has
been attempted"*. Every clause of that was false. Corrected at `fa7ba97`, and
the before/after is stated so the change is auditable rather than silent:

| | before | after |
| --- | --- | --- |
| `phases[id: "7"].status` | `NOT_STARTED` | `PARTIALLY_VERIFIED` |
| exit criterion 1 (`met`) | `false` | `true`, with the AST measurement as evidence |
| exit criterion 2 (`met`) | `false` | `true`, with the 13-scenario matrix as evidence |
| exit criterion 3 (`met`) | `false` | `true`, with the six-guard measurement as evidence |
| a `phase7:` record | absent | a new top-level key |

**`PARTIALLY_VERIFIED` and not `PASSED`, deliberately.** The three exit
observables were seen. What has not run against this branch is every gate that
blocks a merge — mutation, changed-line coverage, full-suite coverage, security,
dependency scan, workflow validation, `pr-fast`, `pr-full`, `ci-gate` — and no
pull request exists.

**`allowed_statuses` was NOT widened.** It has six values and every phase status
is checked against it. The richer Phase 7 record — `READY_FOR_PR`,
`NOT_MEASURED`, `NOT_IMPLEMENTED`, and the pointer to the next phase — needs a
vocabulary that list does not have, and the cheap move is to widen the list.
That check exists to guard the phase table; widening it to fit one record is the
weakening this project forbids. So the vocabulary stays at six and the record
lives under a separate top-level `phase7:` key that binds nothing else.

`scripts/validate_project_truth.py` was 30/30 before the edit and is 30/30
after it. No document under `docs/` asserts a Phase 7 status, so nothing
contradicts the change.

**Lines actually edited:** the `phases[id: "7"]` block, which is at lines
360-378 in this checkout, plus an insertion immediately before `metrics:`.
Phases 6, 8, 9 and 10 were diffed byte for byte afterwards and are identical.

---

## Two things a reader should not conclude

**That Phase 7 is merged.** It is not. This branch is committed locally; no PR
has been opened and nothing has been pushed, on the owner's instruction. The
coordinator opens the PR and moves the status on from `READY_FOR_PR`.

**That any GST feature works.** See "The GST truth" above. The GST rows in this
document are safety results and nothing else.
