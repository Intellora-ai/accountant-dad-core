# Phase 7 — the extraction adapter

Branch `phase7/adapter-contract`, on top of `main` at `1ca65a9` (D-06).
Last measured 2026-08-10 at `dd65b8c`.

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
| 7.1 | swapping the backend changes NO code outside `accountant/extract/` | **0 sites, 0 names** | 0 | none | `tests/test_adapter_contract.py:769` `test_backend_selection_happens_nowhere_outside_the_package`; ratchet at `tests/test_adapter_contract.py:751` `KNOWN_SELECTION_SITES = frozenset()`; the lever is `accountant/extract/registry.py:67` `DEFAULT_BACKEND` | **PASS** |
| 7.2 | a backend outage returns every field `not_found` with a stated reason, and the person types the entry instead | **10 scenarios × 7 properties**, 112 cases | 10 scenarios | none for the adapter; the HTTP surface is a separate row below | `tests/test_extract_outage.py:280`, `:300`, `:310`, `:389`, `:420` | **PASS** |
| 7.3 | a STATIC test fails if a reader appears in `accountant/extract/` | **5 guards, 28 cases** | ≥1 guard | none | `tests/test_no_reader.py:187`, `:205`, `:305`, `:322`, `:419`, `:475` | **PASS** |
| GST | a GST bill must not reach VALID and then be refused by the connector | **fixed 2026-08-10**; 4/4 tests pass with no marker; 30/30 sweep | 4/4 and 30/30 | none | `tests/test_adapter_contract.py:1147`, `:1160`, `:1176`, `:1287`; `tests/test_gst_safety_sweep.py` | **PASS** |
| — | a reader outage over HTTP | unreachable: `app.configure()` takes no extractor | reachable | one parameter on `configure()` | see "A reader outage over HTTP" below | **BLOCKED** |
| — | a GST bill that POSTS, with tax lines on it | no tax line can be built at all | Phase 8 | the whole of GST posting | nothing here builds one, and nothing was invented | **NOT_MEASURED** |
| — | third-party backend accuracy | no backend is connected | owner's call | — | `artifacts/extraction_backends.md`, untracked | **NOT_MEASURED** |

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
3. **Measured at `dd65b8c`:**

       backends derived     ServiceExtractor, StubExtractor,
                            TypedTextExtractor, UnavailableExtractor
       modules scanned      45  (accountant/**, excluding accountant/extract/**)
       core sites           0
       whole-package sites  {}
       cost of a swap       0 lines outside the package

   Confirmed independently of the AST scan by a plain text search for the four
   class names across `accountant/`, excluding `accountant/extract/`: no hits.

4. **What the rest of the repository takes from the package:**

       accountant/pipeline.py       ExtractedRecord, Extractor, NOT_FOUND
       accountant/ingest/spend.py   NOT_FOUND
       accountant/web/app.py        default_extractor

   All four are the abstract contract. `accountant/web/app.py:39` imports
   `default_extractor`; `accountant/web/app.py:1254` calls it. No concrete
   backend is named. `pipeline.build_draft` and `pipeline.run` annotate
   `extractor: Extractor`, asserted from the AST at
   `tests/test_adapter_contract.py:810`.

`default_extractor` is in `CONTRACT`, the set of names a core module may depend
on. That is a widening, not a weakening: the function names no backend, so a
module calling it cannot be made to change by choosing a different one.

---

## 7.2 — outage fallback · PASS

Ten scenarios, seven properties each, all asserted separately. "It failed
safely" is four different properties wearing one sentence and only one of them
is about not crashing.

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

Five guards, 28 cases, at `tests/test_no_reader.py`.

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
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` `:1147` | outcome is not VALID | NOT_VALID | **PASS** |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` `:1160` | UNCLEAR/NOT_VALID and the words mention the tax | NOT_VALID, reason names GST and the tax line | **PASS** |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` `:1176` | VALID means the connector will take it | asserted both directions; no `write_attempted` row for the refused bill | **PASS** |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` `:1287` | HTTP 200, explains the tax | HTTP 200, no "broke", the reason names the tax | **PASS** |

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

## A reader outage over HTTP — BLOCKED

```
web/app.py calls registry.default_extractor(), so the backend is chosen inside
accountant/extract/ — but app.configure() takes a client, an identity and a
store, and NO extractor. There is no seam through which a test can hand the
RUNNING app a failing backend.
```

Editing `DEFAULT_BACKEND` is monkey-patching a `Final` constant and proves
something about the patch rather than about the shipped path.

**What lifts it:** one parameter on `configure()`, defaulting to
`default_extractor()`, so a fixture can inject `UnavailableExtractor` the same
way it already injects `FakeTally`.

**A previous justification here was wrong and is corrected rather than carried
forward.** This document used to say that adding the parameter "is a change to
the web app beyond the one line exit 7.1 allows". That is not right: exit 7.1 is
about backend SELECTION appearing outside the package, and a parameter defaulting
to `default_extractor()` names no backend, so it would create no selection site
and the AST guard would say so. The real reason it is not done here is narrower
and should be stated as such: it changes the web app's public signature, and
that belongs to whoever next opens that file deliberately.

Status: **BLOCKED**. Not measured, not failed — unreachable without that change.

---

## Mutants — 9 applied, 9 killed, 0 survived

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
| full suite, `COVERAGE_CORE=pytrace`, run 1 | local, `dd65b8c` | **PASS** — 2055 passed, 6 xfailed, 118s |
| full suite, run 2 (determinism) | local, `dd65b8c` | **PASS** — 2055 passed, 6 xfailed, 118s, identical |
| `tests/test_adapter_contract.py` | local | **PASS** — 94 passed, 0 xfailed |
| `tests/test_extract_outage.py` | local | **PASS** — 112 passed |
| `tests/test_no_reader.py` | local | **PASS** — 28 passed |
| `tests/test_gst_safety_sweep.py` | local | **PASS** — 43 passed |
| `ruff check .` | local | **PASS** — all checks passed |
| `ruff format --check .` | local | **PASS** — 148 files |
| `pyright` (strict) | local | **PASS** — 0 errors, 0 warnings |
| `scripts/validate_project_truth.py` | local | **PASS** — 30 checks, 30 passed |
| `scripts/guards` | local | **PASS** — all guards passed |
| `git diff --check` | local | **PASS** — clean |
| mutation score ≥ 90 | not run here | **GITHUB_REQUIRED** |
| changed-line coverage ≥ 90 | not run here | **GITHUB_REQUIRED** |
| full-suite coverage ≥ 90 | not run here | **GITHUB_REQUIRED** |
| pr-fast · pr-full · ci-gate | not run here | **GITHUB_REQUIRED** |
| security · dependency scan · workflow validation | not run here | **GITHUB_REQUIRED** |
| a reader outage over HTTP | unreachable | **BLOCKED** |
| a GST bill that posts with tax lines | not built, not invented | **NOT_MEASURED** |
| question rate | nobody measured it | **NOT MEASURED** |

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
| backend-swap | 10 | 23 (10 behavioural, 13 structural) |
| malformed-response | 5 | 15 |
| timeout/outage | 5 | 112 |
| outage scenarios | 10 | 10, each with 7 properties |
| GST | — | 9 in `test_adapter_contract.py` + 43 in `test_gst_safety_sweep.py` |
| no-reader guards | — | 6 guards, 28 cases |

---

## Two things a reader should not conclude

**That Phase 7 is merged.** It is not. This branch is committed locally at
`dd65b8c`; no PR has been opened, on the owner's instruction.

**That the control plane knows any of this.** `docs/CONTROL_PLANE.yaml:360-378`
still records Phase 7 as `NOT_STARTED` with all three exit criteria
`met: false`. That is stale as of this branch and was deliberately left alone:
moving a phase status is an owner action, not an agent's. Flagged here so it is
not discovered later as a silent contradiction.
