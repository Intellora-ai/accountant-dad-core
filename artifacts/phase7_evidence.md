# Phase 7 — evidence report

One row per requirement: measured, target, gap, evidence, status. Written
2026-08-10 on `phase7/adapter-contract` at `dd65b8c`, on top of `origin/main` at
`1ca65a9` ("D-06: live Tally wins over stale memory").

This report is the record of what was OBSERVED. Where something was not
observed it says so, and it never lets a local run stand in for a gate.

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
commit              = dd65b8c   (baseline measurements: cb6348e)
```

**The guard was checked by breaking it, not by trusting it.** Run from
`scratchpad/` instead of the worktree, the same script fails:

```
AssertionError: WRONG CHECKOUT: `accountant` resolved to
/Users/tanveersidhu/ACCOUNTANT/accountant/__init__.py, which is not under the
current working directory .../scratchpad
```

So the failure mode is real in this repository and not hypothetical: from the
wrong directory the editable install resolves `accountant` to the MAIN checkout,
and a before/after comparison would read the same unchanged files twice and show
whatever was hoped for. Any Phase 7 number produced without this assertion is
**INVALIDATED**, not evidence.

**Note on the worktree venv.** `wt-phase7/.venv` is an untracked symlink to
`/Users/tanveersidhu/ACCOUNTANT/.venv`. Every command below was still issued
against the interpreter by absolute path, so the symlink changes nothing about
which files were executed — the provenance assertion above is what settles that,
and it was run inside the worktree.

---

## The three exit verdicts

| exit | claim | measured | target | gap | evidence | status |
| --- | --- | --- | --- | --- | --- | --- |
| **7.1** | swapping the backend changes no code outside `accountant/extract/` | **0 sites, 0 names** | 0 | **none** | `tests/test_adapter_contract.py:769`, `:754`, `:791`, `:880`, `:810`; ratchet at `:751` | **PASS** |
| **7.2** | a backend outage returns every field `not_found` with a stated reason, and the person types the entry instead | **10 scenarios × 7 properties**, 112 cases | 10 scenarios | **none for the adapter**; the HTTP surface is separately blocked | `tests/test_extract_outage.py:280`, `:300`, `:310`, `:389`, `:420` | **PASS** |
| **7.3** | a static test fails if a reader appears in `accountant/extract/` | **5 guards, 28 cases** | ≥1 guard | **none** | `tests/test_no_reader.py:187`, `:205`, `:305`, `:322`, `:419`, `:475` | **PASS** |

All three of the control plane's declared Phase 7 exit criteria are met on this
branch. The findings that sit OUTSIDE those three are rows in their own right
below, not footnotes to a pass.

---

## 7.1 — the backend swap, measured

The AST scan counts concrete-backend references in every module under
`accountant/` except `accountant/extract/` itself.

| | `backend_sites()` | files | concrete names |
| --- | --- | --- | --- |
| at `27333e9` | `{'accountant/web/app.py': ['TypedTextExtractor']}` | 1 | 1 |
| at `dd65b8c` | `{}` | **0** | **0** |

Verified two ways that do not share a failure mode: the AST scan, and a plain
text search for the four backend class names across `accountant/` excluding
`accountant/extract/` — no hits.

`accountant/web/app.py:39` imports `default_extractor`;
`accountant/web/app.py:1254` calls it. The lever is
`accountant/extract/registry.py:67`, `DEFAULT_BACKEND`.

**The ratchet is at zero.** `KNOWN_SELECTION_SITES` is the empty frozenset at
`tests/test_adapter_contract.py:751`, and the assertion is `== 0`, not `<= 1`.
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

**One widening, stated plainly.** `default_extractor` is in `CONTRACT`, the set
of names a core module may depend on. Not a weakening: the function names no
backend, so a module that calls it still cannot be made to change by choosing a
different one.

---

## 7.2 — the ten outage scenarios

| # | scenario | status |
| --- | --- | --- |
| 1 | unavailable | **PASS** |
| 2 | timeout | **PASS** |
| 3 | malformed response | **PASS** |
| 4 | partial response | **PASS** |
| 5 | authentication failure | **PASS** |
| 6 | rate limit | **PASS** |
| 7 | empty response | **PASS** |
| 8 | connection refused | **PASS** |
| 9 | a response about a different document | **PASS** |
| 10 | a response missing the named fields | **PASS** |

The seven properties, each asserted per scenario: every named field explicitly
`not_found`; the reason stored on the record; the reason visible on the draft;
no silent blank; nothing raises; no automatic Tally post; zero vouchers and the
trial balance unchanged **in exact paise**. The person can then type the entry in
by hand, and that entry posts — `tests/test_extract_outage.py:389`.

`tests/test_extract_outage.py` — **112 passed**.

Proved load-bearing by two mutants. MU7, dropping the reason from the outage
record, turns **38** of those 112 red. MU8, letting the record claim a value
instead of `not_found`, turns **11** red.

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
| `test_a_gst_bill_without_tax_lines_cannot_be_valid` `:1147` | outcome is not `VALID` | `NOT_VALID` | **PASS** |
| `test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over` `:1160` | `UNCLEAR`/`NOT_VALID`, words mention the tax | `NOT_VALID`; the reason names GST and the tax line | **PASS** |
| `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` `:1176` | `VALID` means the connector will take it | asserted both ways; no write-ahead row for the refused bill | **PASS** |
| `test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage` `:1287` | HTTP 200, explains the tax | HTTP 200, no "broke", tax named | **PASS** |

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

**The reason previously given here was wrong and is corrected.** This document
used to say the parameter would be "a change to the web app beyond the one line
exit 7.1 allows". Exit 7.1 is about backend SELECTION outside the package; a
parameter defaulting to `default_extractor()` names no backend and would create
no selection site — the AST guard would confirm that. The honest reason it is
not done here is narrower: it changes the web app's public signature, which
belongs to whoever next opens that file deliberately.

Status: **BLOCKED**. Not measured, not failed — unreachable without that change.

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

## Mutants — 9 applied, 9 killed, 0 survived

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
| full suite, `COVERAGE_CORE=pytrace`, run 1 | **PASS** — 2055 passed, 6 xfailed, 118s |
| full suite, run 2 | **PASS** — 2055 passed, 6 xfailed, 118s, identical |
| baseline before any change (`cb6348e`) | 2008 passed, 10 xfailed, 0 failed, 0 xpassed |
| `tests/test_adapter_contract.py` | **PASS** — 94 passed, 0 xfailed |
| `tests/test_extract_outage.py` | **PASS** — 112 passed |
| `tests/test_no_reader.py` | **PASS** — 28 passed |
| `tests/test_gst_safety_sweep.py` | **PASS** — 43 passed |
| AST selection sites | **measured** — 0 files, 0 names |
| ratchet sensitivity (MU3b) | **PASS** — 3 tests red, 91 green, then restored |
| 9 guard mutants | **PASS** — 9 applied, 9 killed, 0 survived |
| `ruff check .` | **PASS** |
| `ruff format --check .` | **PASS** — 148 files |
| `pyright` (strict) | **PASS** — 0 errors, 0 warnings |
| `scripts/validate_project_truth.py` | **PASS** — 30 checks, 30 passed |
| `scripts/guards` | **PASS** — all guards passed |
| `git diff --check` | **PASS** — clean |

### Blocked

| what | status |
| --- | --- |
| a reader outage over HTTP | **BLOCKED** — no extractor seam on `configure()` |

### Not measured

| what | why |
| --- | --- |
| a GST bill that POSTS with tax lines | no tax line can be built; nothing was invented to fake one |
| third-party backend accuracy | owner decision; no backend is connected |
| mutation score at this HEAD | not re-run on this branch |
| question rate | **NOT MEASURED**. Nobody ran it, and 0 is not the answer |

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

**That the control plane knows any of this.** `docs/CONTROL_PLANE.yaml:360-378`
still records Phase 7 as `NOT_STARTED` with all three exit criteria
`met: false`. Stale as of this branch, and deliberately left alone: the control
plane is the authority document, and moving a phase status is an owner action.
Flagged so it is not discovered later as a silent contradiction.
