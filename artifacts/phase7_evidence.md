# Phase 7 — evidence report

One row per requirement: current, target, gap, evidence, status. Written
2026-08-10, on `phase7/adapter-contract` rebased onto `origin/main` at
`1ca65a9` ("D-06: live Tally wins over stale memory").

This report is the record of what was OBSERVED. Where something was not
observed it says so, and it never lets a local run stand in for a gate.

---

## The five words, and why they are not interchangeable

Blurring these is how a phase gets called done. They are kept apart on purpose.

| word | what it means | what it does NOT mean |
| --- | --- | --- |
| **measured** | a number was produced by a run whose provenance was asserted | that the number is good |
| **not measured** | nobody ran it. There is no number | that it would fail, or that it would pass |
| **invalidated** | it WAS run, and the run turned out not to describe the code under test | that it failed |
| **blocked** | the correct behaviour is written down and pinned, and it cannot be reached from here | that it is unimportant, or that it is nearly done |
| **passed** | the exit observable was seen, by a named test, on this code | that CI agrees — CI is a separate authority |

## Result labels

Every result below carries exactly one.

| label | meaning |
| --- | --- |
| `LOCAL_PASS` | ran here, passed here |
| `LOCAL_FAIL` | ran here, failed here |
| `NOT_RUN` | not attempted. No number exists |
| `GITHUB_REQUIRED` | only GitHub can answer this. Nothing local is evidence for it |
| `BLOCKED_ENVIRONMENT` | cannot be reached from here at all, for a stated reason |

**`GITHUB_REQUIRED` is not a soft label.** Mutation score, changed-line
coverage, full-suite coverage, `pr-fast`, `pr-full`, `ci-gate`, security,
dependency scan and workflow validation are GitHub's answer. None of them has
been run. A green local suite is not any of them and is never reported as one.

---

## Provenance — why these numbers describe this checkout

Every measurement in this report was taken from the Phase 7 worktree with this
assertion passing first:

```python
from pathlib import Path

import accountant

assert Path(accountant.__file__).resolve().is_relative_to(Path.cwd().resolve())
```

```
provenance OK: accountant -> .../scratchpad/wt-phase7/accountant/__init__.py
                     cwd -> .../scratchpad/wt-phase7
```

**The guard was checked by breaking it, not by trusting it.** Run from
`scratchpad/` instead of the worktree, the same script fails:

```
AssertionError: WRONG CHECKOUT: `accountant` resolved to
/Users/tanveersidhu/ACCOUNTANT/accountant/__init__.py, which is not under the
current working directory .../scratchpad
```

So the failure mode is real in this repository and not hypothetical: from the
wrong directory the editable install resolves `accountant` to the MAIN
checkout, and a before/after comparison would read the same unchanged files
twice and show whatever was hoped for. Any Phase 7 number produced without this
assertion should be treated as **invalidated**, not as evidence.

---

## The three exit verdicts

| exit | claim | current | target | gap | evidence | status |
| --- | --- | --- | --- | --- | --- | --- |
| **7.1** | swapping the backend changes no code outside `accountant/extract/` | **0 sites, 0 names** outside the package | 0 | **none** | `tests/test_adapter_contract.py`, 13 structural AST cases + 23 behavioural | **PASSED** · `LOCAL_PASS` |
| **7.2** | a backend outage returns every field `not_found` with a stated reason | **10 scenarios × 7 properties**, 112 cases | 10 scenarios | **none for the adapter**; the HTTP surface is separately blocked, below | `tests/test_extract_outage.py` | **PASSED** · `LOCAL_PASS` |
| **7.3** | a static test fails if a reader appears in `accountant/extract/` | **5 guards, 28 cases** | ≥1 guard | **none** | `tests/test_no_reader.py` | **PASSED** · `LOCAL_PASS` |

All three of the control plane's declared Phase 7 exit criteria are met on this
branch. Two findings sit OUTSIDE those three criteria and are open; they are
rows in their own right below, not footnotes to a pass.

---

## 7.1 — the backend swap, measured

The AST scan counts concrete-backend references in every module under
`accountant/` except `accountant/extract/` itself. Both numbers below were
produced by the same script, in the same worktree, with provenance asserted;
the only difference between the runs is the state of `accountant/web/app.py`.

| | `backend_sites()` | files | concrete names |
| --- | --- | --- | --- |
| **before** (`app.py` at `HEAD`) | `{'accountant/web/app.py': ['TypedTextExtractor']}` | **1** | **1** |
| **after** (`app.py` pointed at the registry) | `{}` | **0** | **0** |

The change, in full — this is the whole of it:

```python
-from accountant.extract.adapter import TypedTextExtractor
+from accountant.extract.registry import default_extractor
...
-        TypedTextExtractor(),
+        default_extractor(),
```

**The ratchet was tightened to match.** `KNOWN_SELECTION_SITES` was
`{'accountant/web/app.py'}` and is now the empty set; the assertion moved from
`<= 1` to `== 0`. There is no longer an allowlisted file for a new selection
site to hide inside.

**The ratchet was then verified by breaking it.** A file
`accountant/_ratchet_probe.py` containing
`from accountant.extract.adapter import StubExtractor` was planted, and four
structural tests turned red naming the planted file:

```
FAILED test_swapping_the_backend_changes_no_module_in_the_core
FAILED test_backend_selection_happens_nowhere_outside_the_package
FAILED test_the_core_takes_only_the_contract_from_the_extraction_package
FAILED test_the_measured_cost_of_a_backend_swap_is_no_line_outside_the_package
  AssertionError: {'accountant/_ratchet_probe.py': ['StubExtractor']}
```

The probe was deleted. A guard that has never failed is not a guard.

**One widening, stated plainly.** `default_extractor` was added to `CONTRACT`,
the set of names a core module may depend on. It is not a weakening: the
function names no backend, so a module that calls it still cannot be made to
change by choosing a different one. The lever is now `DEFAULT_BACKEND`, one
line inside `accountant/extract/registry.py`.

---

## 7.2 — the ten outage scenarios

Ten scenarios, seven properties each, asserted separately, because "it failed
safely" is several different properties wearing one sentence and only one of
them is about not crashing.

| # | scenario | status |
| --- | --- | --- |
| 1 | unavailable | **PASSED** · `LOCAL_PASS` |
| 2 | timeout | **PASSED** · `LOCAL_PASS` |
| 3 | malformed response | **PASSED** · `LOCAL_PASS` |
| 4 | partial response | **PASSED** · `LOCAL_PASS` |
| 5 | authentication failure | **PASSED** · `LOCAL_PASS` |
| 6 | rate limit | **PASSED** · `LOCAL_PASS` |
| 7 | empty response | **PASSED** · `LOCAL_PASS` |
| 8 | connection refused | **PASSED** · `LOCAL_PASS` |
| 9 | a response about a different document | **PASSED** · `LOCAL_PASS` |
| 10 | a response missing the named fields | **PASSED** · `LOCAL_PASS` |

The seven properties, each asserted per scenario: every named field explicitly
`not_found`; the reason stored on the record; the reason visible on the draft;
no silent blank; nothing raises; no automatic Tally post; zero vouchers and the
trial balance unchanged **in exact paise**. The person can then type the entry
in by hand, and that entry posts.

`tests/test_extract_outage.py` — **112 passed**, `LOCAL_PASS`.

---

## 7.3 — no reader in the package

| guard | asks | status |
| --- | --- | --- |
| imports | does the package import an OCR / image / layout library | **PASSED** · `LOCAL_PASS` |
| identifiers | does it name one | **PASSED** · `LOCAL_PASS` |
| what it may TOUCH | allowlist: stdlib and `accountant.*` only | **PASSED** · `LOCAL_PASS` |
| what the project DECLARES | no such dependency in `pyproject.toml` / lockfile | **PASSED** · `LOCAL_PASS` |
| what the package SHIPS | no model weights or trained data files | **PASSED** · `LOCAL_PASS` |

An allowlist, not a list of banned libraries: a banned-list guard is defeated
by any library nobody thought of. `tests/test_no_reader.py` — **28 passed**,
`LOCAL_PASS`.

A consequence worth naming, because it constrains the backend choice: **no
vendor SDK can be imported inside `accountant/extract/`.** The selection
criterion is not "best SDK", it is "plain HTTPS JSON API".

---

## The GST defect — BLOCKED, and the recorded dependency was wrong

### The four marked tests: all four stayed blocked

The brief was to remove the four `xfail(strict=True)` markers if — and only if
— the tests genuinely pass now that D-06 is in main. **They do not pass. All
four markers stay.** Re-run with `--runxfail` on the rebased branch:

| test | required behaviour | what actually happened | verdict |
| --- | --- | --- | --- |
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` | outcome is not `VALID` | outcome is still `VALID` | **STAYED BLOCKED** · `LOCAL_FAIL` |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` | `UNCLEAR`/`NOT_VALID`, and the words mention the tax | no question asked; neither "tax" nor "gst" in what the person is told | **STAYED BLOCKED** · `LOCAL_FAIL` |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` | `VALID` means the connector will take it | app says `VALID`, `pipeline.post` still raises the tax-line refusal | **STAYED BLOCKED** · `LOCAL_FAIL` |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` | HTTP 200, explains the tax | still **HTTP 503** | **STAYED BLOCKED** · `LOCAL_FAIL` |

A marker removed from a failing test is a lie, so none was removed.

### The correction: the blocker was never D-06

The markers said `BLOCKED_BY_D06` and named "the D-06 change to
`accountant/pipeline.py`" as what would unblock them. **That was this project's
own prediction and it was wrong.**

D-06 landed in main as `1ca65a9` and did change `accountant/pipeline.py` — for
memory that has gone stale against the live ledger for a **vendor**. It touches
neither GST nor tax:

```
$ git diff 27333e9 1ca65a9 -- accountant/pipeline.py | grep -ciE 'gst|tax'
0
```

Zero hits. The branch was rebased onto D-06 and the four tests fail exactly as
they did at `27333e9`.

The real dependency is the **GST rules work, which does not exist**, plus the
**accounting-policy question** of what a tax line must contain before a bill
carrying one may be called `VALID`. Both are Phase 8. Naming D-06 made a real
dependency look smaller and nearer than it is.

The marker reason was corrected in place — from `BLOCKED_BY_D06` to
`BLOCKED_BY_GST_RULES`, carrying the measurement — rather than left to age. The
markers themselves stay `strict=True`, so the day this starts working, it turns
red and somebody has to come back deliberately.

### What IS proved about GST: the safety half

Five unmarked tests pin it, and all five pass. They are the claim this phase
actually makes, and they hold whatever the eventual fix looks like:

| test | claim | status |
| --- | --- | --- |
| `test_the_extraction_of_a_gst_bill_is_exactly_what_the_defect_starts_from` | 420000 total, 64068 tax, sourced `typed_text` | `LOCAL_PASS` |
| `test_the_connector_refuses_a_gst_voucher_and_says_why` | the connector is the last line and it holds | `LOCAL_PASS` |
| `test_a_gst_bill_writes_nothing_and_moves_the_trial_balance_by_zero_paise` | **THE PIN** — zero vouchers, trial balance identical in exact paise | `LOCAL_PASS` |
| `test_a_gst_bill_over_http_writes_nothing_and_moves_no_paise` | the pin, over the surface a person touches | `LOCAL_PASS` |
| `test_a_gst_bill_over_http_is_answered_rather_than_dropped` | no dropped socket, no traceback, no internal field name on screen | `LOCAL_PASS` |

So the honest summary: **a GST bill is handled unsafely as a DECISION and
safely as a WRITE.** It should be asked about and instead it is called `VALID`;
but nothing is written, no paise move, and the person gets a page rather than a
dropped connection. The first half is blocked. The second half is passed.

---

## A reader outage over HTTP — still BLOCKED_ENVIRONMENT

**The 7.1 fix was expected to lift this. It did not.** Recording the falsified
prediction rather than the hoped-for outcome:

```
was    web/app.py named TypedTextExtractor, so the app could never reach a
       service at all
now    web/app.py calls registry.default_extractor(), so the backend is chosen
       inside accountant/extract/ — but app.configure() takes a client, an
       identity and a store, and NO extractor. There is no seam through which
       a test can hand the RUNNING app a failing backend.
```

Two ways to reach it exist, and neither was taken:

- editing `DEFAULT_BACKEND` is monkey-patching a `Final` constant, and proves
  something about the patch rather than about the shipped path;
- adding an `extractor` argument to `app.configure()` is a change to the web
  app beyond the one line exit 7.1 allows.

**What lifts it:** one parameter on `configure()`, defaulting to
`default_extractor()`, so a fixture can inject `UnavailableExtractor` the same
way it already injects `FakeTally`. That is the whole change and it is not in
this phase.

Status: **BLOCKED_ENVIRONMENT**. Not measured, not failed — unreachable.

---

## The backend comparison

**Outcome: `third-party backend selection = OWNER_DECISION_REQUIRED`.**

See `artifacts/extraction_backends.md` — **which is not committed anywhere at
the time of writing.** It exists only as an untracked file in the main working
copy. It was NOT added to this branch: it is 1011 lines this agent did not
write, and several other branches are being integrated in parallel, so
committing it here risks a collision with whoever owns it. Flagged rather than
quietly adopted or quietly ignored — an untracked file is one `git clean` away
from gone. 16 backends checked, 11 real
candidates, every claim carrying a source URL and a retrieval date. Nothing in
this phase selects one, and nothing in this phase should: the choice sets a
per-page cost, a data-residency position and a dependency the product cannot
easily leave.

Five findings from that document that bear on Phase 7:

1. `accountant/extract/` may import stdlib and `accountant.*` only, enforced by
   `tests/test_no_reader.py`. **No vendor SDK can be imported there.**
2. **No vendor publishes a number comparable to the 95-per-100-per-field bar**,
   and none will. It can only be settled by measuring on the owner's own bills.
3. **Not one backend returns a CGST/SGST/IGST split or an HSN code.** Every
   option needs custom GST post-processing, at a cost that is
   vendor-independent.
4. A large class of Indian bills — GST e-invoices with a signed IRN and QR code
   — **may need no OCR at all.**
5. `D-23` is still `OPEN` and its stated default is **typed text only**. If
   D-23 resolves that way, none of this is needed.

Status: **NOT MEASURED, by design** — this is an owner decision, not a test
result. No accuracy claim is made for any backend.

---

## Results, labelled

### Measured here

| what | result |
| --- | --- |
| full suite, `COVERAGE_CORE=pytrace`, rebased on `1ca65a9` | **`LOCAL_PASS`** — 2008 passed, 10 xfailed, 120s |
| `tests/test_adapter_contract.py` | **`LOCAL_PASS`** — 90 passed, 4 xfailed (94 collected) |
| `tests/test_extract_outage.py` | **`LOCAL_PASS`** — 112 passed |
| `tests/test_no_reader.py` | **`LOCAL_PASS`** — 28 passed |
| AST selection sites, before | **measured** — 1 file, 1 name |
| AST selection sites, after | **measured** — 0 files, 0 names |
| ratchet sensitivity (planted site) | **`LOCAL_PASS`** — 4 tests went red, then the probe was removed |
| `ruff check .` | **`LOCAL_PASS`** |
| `ruff format --check .` | **`LOCAL_PASS`** — 147 files |
| `pyright` (strict) | **`LOCAL_PASS`** — 0 errors, 0 warnings |
| `scripts/validate_project_truth.py` | **`LOCAL_PASS`** — 30 checks, 30 passed |
| `scripts/guards` | **`LOCAL_PASS`** — all guards passed |

### Blocked

| what | status |
| --- | --- |
| the four GST decision tests | **`LOCAL_FAIL`**, markers retained — `BLOCKED_BY_GST_RULES` |
| a reader outage over HTTP | **`BLOCKED_ENVIRONMENT`** — no extractor seam on `configure()` |

### Not measured

| what | why |
| --- | --- |
| third-party backend accuracy | `OWNER_DECISION_REQUIRED`; no backend is connected |
| mutation score at this HEAD | not re-run on this branch — see below |

### GitHub is the only authority for these

Not run. Nothing local is evidence for any of them.

| gate | status |
| --- | --- |
| mutation score ≥ 90 | **`GITHUB_REQUIRED`** |
| changed-line coverage ≥ 90 | **`GITHUB_REQUIRED`** |
| full-suite coverage | **`GITHUB_REQUIRED`** |
| `pr-fast` | **`GITHUB_REQUIRED`** |
| `pr-full` | **`GITHUB_REQUIRED`** |
| `ci-gate` | **`GITHUB_REQUIRED`** |
| security | **`GITHUB_REQUIRED`** |
| dependency scan | **`GITHUB_REQUIRED`** |
| workflow validation | **`GITHUB_REQUIRED`** |

---

## Two things a reader should not conclude from this document

**That Phase 7 is merged.** It is not. At the time of writing this branch is
rebased onto `1ca65a9` and committed locally; no PR has been opened, on the
owner's instruction, because the Phase 7 PR must also carry D-05's
legal-identity policy and raw-evidence hardening, and several other branches
have to be integrated first.

**That the control plane knows any of this.** `docs/CONTROL_PLANE.yaml` still
records Phase 7 as `NOT_STARTED` with all three exit criteria `met: false`.
That is stale as of this branch, and it was deliberately left alone: the
control plane is the authority document, and moving a phase status is an owner
action, not an agent's. It is flagged here so it is not discovered later as a
silent contradiction.
