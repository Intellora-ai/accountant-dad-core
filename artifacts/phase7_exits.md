# Phase 7 — the extraction adapter

Branch `phase7/adapter-contract`, from `main` at `27333e9`.

The rule this phase serves: **we write an adapter, never a reader.** Reading a
bill is a commodity. TallyPrime 7.1 ships it. myBillBook gives it away. Others
sell it from free to about Rs 599 a month, unlimited. Choosing the right ACCOUNT
is the part nobody has solved. Building the solved half would have cost about a
month and delayed the unsolved half by the same.

Nothing in `accountant/extract/` reads a document. What was added is a second
backend that adapts somebody else's answer, a place for the choice of backend to
live, and the tests that make all three claims falsifiable.

## How to read a status

| word | meaning |
| --- | --- |
| PASSED | the exit is met and a named test proves it |
| PARTIALLY_VERIFIED | most of it is proved; a named part is not |
| BLOCKED_BY_GST_RULES | correct behaviour is written down and pinned, and needs work that does not exist yet |

`BLOCKED_BY_GST_RULES` replaces `BLOCKED_BY_D06`, which was this document's own
prediction and was wrong — see "The GST defect" below for the measurement that
settled it. It is not an ownership block: it is missing work plus an unanswered
accounting-policy question, both Phase 8.

Every result below is labelled with how it was obtained:
`LOCAL_PASS` · `LOCAL_FAIL` · `NOT_RUN` · `GITHUB_REQUIRED` · `BLOCKED_ENVIRONMENT`.
A local run is not a gate. Mutation score, changed-line coverage, full-suite
coverage, pr-fast, pr-full, ci-gate, security, dependency scan and workflow
validation are GitHub's answer and are `GITHUB_REQUIRED` here.

---

## The exits

| # | requirement | current | target | gap | test | evidence | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 7.1a | two interchangeable backends behind one Protocol | 4 backends: `TypedTextExtractor`, `StubExtractor`, `UnavailableExtractor`, `ServiceExtractor` | 2 or more | none | `tests/test_adapter_contract.py` 47 record-contract cases | every backend satisfies `Extractor`, returns `ExtractedRecord`, sources all 4 fields, and raises on nothing — including a JPEG and empty bytes. LOCAL_PASS | PASSED |
| 7.1b | identical draft / decision / posting-gate behaviour across a swap | `StubExtractor` and `ServiceExtractor` on the same facts | identical | none | 10 backend-swap cases | same record values, same draft, same decision (`VALID`, "nothing unclear and nothing surprising"), same voucher, and `{'Purchases': +420000, 'Cash': -420000}` from both. LOCAL_PASS | PASSED |
| 7.1c | a swap changes ZERO code outside `accountant/extract/` | zero selection sites; `accountant/web/app.py` calls `registry.default_extractor()` | zero | none | 13 structural cases, AST | core (39 modules, everything but `web/`) names no backend at all. Whole package: `{}` — no file, no name. The lever is `DEFAULT_BACKEND` in `accountant/extract/registry.py`. Was `{'accountant/web/app.py': ['TypedTextExtractor']}` at 27333e9; closed 2026-08-10. LOCAL_PASS | PASSED |
| 7.2 | ten outage scenarios, seven properties each | none of the ten was reachable before; `ServiceExtractor` did not exist | all ten safe | none | `tests/test_extract_outage.py` 112 cases | every field `not_found` with this outage's own reason, reason visible on the draft, zero blanks, nothing raises, zero vouchers, trial balance identical in exact paise, and the typed entry still posts afterwards. LOCAL_PASS | PASSED |
| 7.3 | a STATIC test fails if a reader appears in `accountant/extract/` | 2 guards (imports, identifiers) | 5 guards | none | `tests/test_no_reader.py` 28 cases | added: what the package may TOUCH, what the project DECLARES, what the package SHIPS. LOCAL_PASS | PASSED |
| GST | a GST bill must not reach VALID and then be refused by the connector | it does, measured over HTTP, re-measured 2026-08-10 on top of D-06 and unchanged | UNCLEAR or NOT_VALID, explained, nothing written | GST rules + accounting policy, both Phase 8, neither exists | 9 cases, 4 of them `xfail(strict=True)` | see the GST section below. LOCAL_PASS on the 5 pins; the safe behaviour is BLOCKED | BLOCKED_BY_GST_RULES |

---

## 7.1 — the backend swap

### What was added

`accountant/extract/service.py` — `ServiceExtractor`. A second backend of a
genuinely different kind: it adapts the answer of a third-party reader service.
The transport is INJECTED (`ServiceCall = (bytes, mime, document_key) ->
answer`), so the module opens no socket and ships no client. Somebody else reads
the bill; this turns their answer into an `ExtractedRecord` or says why it
could not.

`accountant/extract/registry.py` — `DEFAULT_BACKEND`, `build(name)`,
`default_extractor()`. The one place a backend is chosen. `build` raises rather
than falling back, because a typo that quietly returns the default is a machine
reading bills with something other than what the deployment asked for.

`accountant/extract/adapter.py` — one change: `UnavailableExtractor` takes a
`name`. It is now the single place that builds an outage record, and a record
that cannot say WHICH backend was down is not evidence about any of them. The
default is unchanged, so every existing caller reads exactly as before.

### The structural proof, and why a behavioural one is not enough

The falsifying question for "a swap changes nothing outside the package" is:
**what test would fail if it did?** Nothing behavioural would. A second
`from accountant.extract.adapter import SomeBackend` in a module outside the
package leaves every behavioural test green.

So the proof reads the AST:

1. **The list of backends is DERIVED, not hand-kept.** A class in
   `accountant/extract/` that defines `extract` is a backend. The `Extractor`
   Protocol is excluded by its base — it is the contract, not an
   implementation. A backend added tomorrow is covered without anybody
   remembering to add it.
2. **Every reference to those names is counted**, outside the package —
   `ast.Name`, `ast.Attribute` and `ast.alias`, so an import, a renamed import,
   a construction and a method call are all caught. Comments and docstrings are
   not read, so the prose in this repository about `StubExtractor` cannot trip it.
3. **Measured at 27333e9:**

       backends derived     ServiceExtractor, StubExtractor,
                            TypedTextExtractor, UnavailableExtractor
       modules scanned      45  (accountant/**, excluding accountant/extract/**)
       core sites           0   (everything except accountant/web/)
       whole-package sites  {'accountant/web/app.py': ['TypedTextExtractor']}
       cost of a swap       1 file, 1 name

4. **What the rest of the repository takes from the package**, name by name:

       accountant/pipeline.py       ExtractedRecord, Extractor, NOT_FOUND
       accountant/ingest/spend.py   NOT_FOUND
       accountant/web/app.py        TypedTextExtractor

   Three of those four names are the abstract contract. The fourth is the
   selection. `pipeline.build_draft` and `pipeline.run` annotate `extractor:
   Extractor` and that is asserted from the AST too, because a concrete
   annotation there would end the swap.

### Why this is now PASSED — closed 2026-08-10

It was `PARTIALLY_VERIFIED`: `accountant/web/app.py` still named a backend, so
swapping the runtime backend edited the web app. That change has been made, and
it was the two lines predicted:

```python
-from accountant.extract.adapter import TypedTextExtractor
+from accountant.extract.registry import default_extractor
...
-        TypedTextExtractor(),
+        default_extractor(),
```

Measured with the same AST scan, before and after:

```
before   {'accountant/web/app.py': ['TypedTextExtractor']}    1 site,  1 name
after    {}                                                   0 sites, 0 names
```

The allowance is now spent. `KNOWN_SELECTION_SITES` was
`{'accountant/web/app.py'}` and is the empty set; the assertion moved from
`<= 1` to `== 0`. The ratchet was checked by breaking it on purpose rather than
by trusting it: planting
`accountant/_ratchet_probe.py` containing
`from accountant.extract.adapter import StubExtractor` turned four structural
tests red, naming the planted file, and the probe was then deleted.

`default_extractor` was added to `CONTRACT`. That widens what a core module may
depend on, and it is not a weakening: the function names no backend, so a
module calling it still cannot be made to change by choosing a different one.

---

## 7.2 — outage fallback

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
2. the reason is this outage's own reason, on every field, and it is visible on
   `Draft.provenance` and on `Voucher.provenance` — stored is not the same as
   visible, and a reason that stops at the record reaches nobody
3. no source is blank and none is a bare `not_found` with no explanation
4. nothing raises, out of the extractor or out of `pipeline.run`
5. the person is asked something with real answers on it, and typing the entry
   afterwards posts, moving the trial balance by exactly +420000 / -420000
6. `posted_tally_id is None`
7. `list_our_vouchers` is empty, the voucher count is unchanged,
   `trial_balance` is byte-identical, and `read_by_operation_id` returns `None`
8. one durable `blocked` row is written to the action log with a reason

### The design decision inside 7.2

**An answer we cannot fully account for is not a partial answer. It is a failed
one.** A field the service says it could not find comes back `null` — that is
an ANSWER, it becomes an explicit `not_found` for that one field, and such a
record posts perfectly well. A field the service does not mention at all is
different: nothing distinguishes "not on the bill" from "the service stopped
halfway", and those two mean opposite things to the person reading the screen.
So the shape is checked as a conservation law — the fields we asked about must
equal the fields the answer accounts for — and a shortfall refuses the whole
response rather than trusting the half that arrived.

Same for a value of the wrong type. `"4200.00"` is refused, not parsed. A float
is refused, not rounded. `True` is refused, because `isinstance(True, int)` is
true and an unguarded check makes it one paise.

### What 7.2 does NOT cover

**A reader outage over HTTP. STILL BLOCKED_ENVIRONMENT at 2026-08-10, and the
7.1c fix did NOT lift it.** That was the prediction and the prediction was
wrong, so the reason is restated rather than carried forward:

```
was    web/app.py named TypedTextExtractor, so the app could never reach a
       service at all
now    web/app.py calls registry.default_extractor(), so the backend is chosen
       inside accountant/extract/ — but app.configure() takes a client, an
       identity and a store, and NO extractor. There is no seam through which
       a test can hand the running app a failing backend.
```

Two ways to reach it exist and neither was taken. Editing `DEFAULT_BACKEND` is
monkey-patching a `Final` constant and proves something about the patch, not
about the shipped path. Adding an `extractor` argument to `app.configure()` is
a change to the web app beyond the one line 7.1 allows, and it belongs to
whoever next opens that file deliberately.

**What lifts it:** one parameter on `configure()`, defaulting to
`default_extractor()`, so the fixture can inject `UnavailableExtractor` the
same way it already injects `FakeTally`. That is the whole change, and it is
not in this phase.

---

## 7.3 — no reader built

`tests/test_no_reader.py` had two guards. Both left the same hole open, and it
is the cheapest reader anybody can build:

```python
import subprocess

subprocess.run(["tesseract", path, "out"])
```

Stdlib only. No banned import. No `pixel`, no `deskew`, no `bbox`. It passed.

| guard | what it reads | what it catches |
| --- | --- | --- |
| imports (existing) | `ast` import roots | any third-party library, present or not yet invented |
| identifiers (existing) | `ast.Name` / `ast.Attribute` / def names | a reader written by hand, importing nothing |
| **reach** (new) | import roots against `subprocess`, `socket`, `ctypes`, `urllib`, `http`, `tempfile`, `shutil`, `importlib`, … | a reader that shells out, opens a socket, or loads a native library |
| **calls** (new) | bare-name calls | `open`, `exec`, `eval`, `compile`, `__import__` — a model has to live on disk |
| **declared** (new) | `pyproject.toml` | `project.dependencies == []`, and no dependency in ANY group naming reader work |
| **shipped** (new) | every file in the package | a `.traineddata`, a weight, a font, a sample page — none of which any AST scan would see |

`dependencies = []` was the load-bearing fact the import allowlist rested on
and nothing checked it. A reader could have arrived by widening the project
rather than by widening the guard.

Nine more words were added to `READER_WORDS` on the same rationale the list
already gives — the general vocabulary of a hand-rolled reader, not product
names: `dpi`, `crop`, `histogram`, `morpholog`, `template_match`,
`connected_components`, `field_detect`, `line_detection`, `table_detection`.

Each new guard has a companion test proving it can fail, and one proving it does
NOT fire on prose about readers — this file and the package both discuss OCR by
name, and a guard that read comments would flag the docstring stating the rule.

---

## The GST defect — measured, pinned, NOT fixed

### The dependency as recorded, and the correction

Recorded at 27333e9:

```
dependency = D-06 pipeline change
file       = accountant/pipeline.py
reason     = GST-carrying bills must not reach VALID without required tax lines
status     = BLOCKED_BY_D06
```

**That was wrong, and 2026-08-10 is when it was found out.** D-06 landed in
main as `1ca65a9` and did change `accountant/pipeline.py` — for stale vendor
memory against the live ledger. It touches neither GST nor tax:
`git diff 27333e9 1ca65a9 -- accountant/pipeline.py` matches neither word, zero
hits. This branch was rebased onto it and all four marked tests were re-run
with `--runxfail`. All four fail exactly as they did at 27333e9:

```
test_a_gst_bill_without_tax_lines_cannot_be_valid                     outcome is still VALID
test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over  no question, no "tax"/"gst"
test_a_connector_refusal_cannot_happen_after_the_application_said_valid post still raises
test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage  still HTTP 503
```

So the blocker was never D-06. It is the GST rules work, which does not exist,
plus the accounting-policy question of what a tax line must contain before a
bill carrying one may be called VALID. Both Phase 8.

```
dependency = GST rules + accounting policy (Phase 8)
reason     = GST-carrying bills must not reach VALID without required tax lines
status     = BLOCKED_BY_GST_RULES
```

The `xfail(strict=True)` markers stay. Naming D-06 made a real dependency look
smaller and nearer than it was; the marker reason now says what is actually
missing.

### The unsafe path, reproduced

Over the pipeline, and over real HTTP against the demo company:

```
POST /entry  text="paid Sharma Traders 4200 for cement including 18% GST"

  extraction        total_paise 420000, tax_paise 64068, source "typed_text"
  evaluate          VALID, "nothing unclear and nothing surprising"
  pipeline.post     write_attempted row written
  connector         REFUSES: "it carries GST of 64068 paise and this connector
                    builds no tax lines. Writing it would silently drop the
                    tax, producing a wrong statutory entry."
  pipeline.post     write_outcome_unknown row written, ValueError re-raised
  web               HTTP 503, "Something in Accountant Dad broke"

  trial balance     UNCHANGED
  our vouchers      ()
```

The connector is RIGHT to refuse. What is wrong is that the application said
VALID first: it promised a write the connector will not take, and an ordinary
bill with tax on it surfaces to the person as a breakage.

### The expected safe behaviour, written down

1. A GST bill without the required tax lines reaches `UNCLEAR` or `NOT_VALID`,
   never `VALID`.
2. No Tally post is attempted — no `write_attempted` row for a write that was
   never entitled to start.
3. The person gets a plain-English explanation that mentions the tax, not a
   breakage page and not a ledger name.
4. The decision and its reason are recorded durably, as every other decision is.

### What is pinned, and what is xfail

| test | claim | marker |
| --- | --- | --- |
| `test_the_extraction_of_a_gst_bill_is_exactly_what_the_defect_starts_from` | 420000 total, 64068 tax, sourced `typed_text` | none — the input to the defect |
| `test_the_connector_refuses_a_gst_voucher_and_says_why` | the connector holds | none — true before and after D-06 |
| `test_a_gst_bill_writes_nothing_and_moves_the_trial_balance_by_zero_paise` | **THE PIN** | none — must stay true however it is fixed |
| `test_a_gst_bill_over_http_writes_nothing_and_moves_no_paise` | **THE PIN, over HTTP** | none |
| `test_a_gst_bill_over_http_is_answered_rather_than_dropped` | no dropped socket, no traceback, no internal field name on screen | none |
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` | outcome is not VALID | `xfail(strict=True)` |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` | the words mention the tax | `xfail(strict=True)` |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` | VALID means the connector will take it | `xfail(strict=True)` |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` | the page explains, not "broke" | `xfail(strict=True)` |

`strict=True` means the moment one of these starts passing, it turns red and
somebody has to come back and remove the marker deliberately. No tax rate was
invented and no tax handling was added.

---

## Mutants killed

A guard that has never failed is unproven whatever the green count is. Each
mutation was applied to the real file, the tests were run, and the file was
reverted.

| # | mutation | killed by |
| --- | --- | --- |
| M1 | trust a partial answer instead of refusing it | `test_extract_outage.py` |
| M2 | check `ConnectionError` before `ConnectionRefusedError` | `test_a_refused_connection_is_not_reported_as_a_generic_outage` |
| M3 | let a true/false value count as money | `test_adapter_contract.py` |
| M4 | let a transport failure escape instead of becoming a record | `test_extract_outage.py` |
| M5 | stop checking which document the answer is about | `test_extract_outage.py` |
| M6 | quietly turn `"4200.00"` into paise | `test_adapter_contract.py` |
| M7 | `import subprocess` inside `accountant/extract/` | `test_no_reader.py` reach guard |
| M8 | drop the reason from an outage record | `test_extract_outage.py` |
| M9 | default to a backend the registry cannot build | `test_adapter_contract.py` |
| M10 | fall back to the default when the name is unknown | `test_adapter_contract.py` |
| M11 | name a concrete backend from a core module (`accountant/decide.py`) | `test_swapping_the_backend_changes_no_module_in_the_core` |
| M12 | count the `Extractor` Protocol as a concrete backend | `test_the_backend_scan_does_not_mistake_the_contract_for_an_implementation` |
| M13 | ship `model.traineddata` inside the package | `test_the_extraction_package_ships_source_and_nothing_else` |

13 applied, 13 killed, 0 survived.

---

## Results, labelled

| what | how it was run | result |
| --- | --- | --- |
| full suite, `COVERAGE_CORE=pytrace` | local, rebased on `1ca65a9` | **LOCAL_PASS** — 2008 passed, 10 xfailed, 122s |
| `tests/test_adapter_contract.py` | local | **LOCAL_PASS** — 90 passed, 4 xfailed (94 collected) |
| `tests/test_extract_outage.py` | local | **LOCAL_PASS** — 112 passed |
| `tests/test_no_reader.py` | local | **LOCAL_PASS** — 28 passed (12 before, 16 added) |
| `ruff check .` | local | **LOCAL_PASS** |
| `ruff format --check .` | local | **LOCAL_PASS** — 146 files |
| `pyright` (strict) | local | **LOCAL_PASS** — 0 errors, 0 warnings |
| `scripts/validate_project_truth.py` | local | **LOCAL_PASS** — 30 checks, 30 passed |
| `scripts/guards` | local | **LOCAL_PASS** — all guards passed |
| mutation score >= 90 | not run here | **GITHUB_REQUIRED** |
| changed-line coverage >= 90 | not run here | **GITHUB_REQUIRED** |
| full-suite coverage >= 90 | not run here | **GITHUB_REQUIRED** |
| pr-fast · pr-full · ci-gate | not run here | **GITHUB_REQUIRED** |
| security · dependency scan · workflow validation | not run here | **GITHUB_REQUIRED** |
| a reader outage over HTTP | still unreachable | **BLOCKED_ENVIRONMENT** — 7.1c did not lift it; `app.configure()` has no extractor seam |
| the GST safe behaviour | pinned, not fixed | **BLOCKED_BY_GST_RULES** — re-measured on top of D-06, unchanged |

## Test counts against the minimums

| required | minimum | delivered |
| --- | --- | --- |
| adapter-contract | 25 | 47 |
| backend-swap | 10 | 23 (10 behavioural, 13 structural) |
| malformed-response | 5 | 15 |
| timeout/outage | 5 | 112 |
| outage scenarios | 10 | 10, each with 7 properties |
| GST bridge | — | 9 (5 pins, 4 strict xfail) |
| no-reader guards | — | 5 (2 existing, 3 added), 28 cases |
