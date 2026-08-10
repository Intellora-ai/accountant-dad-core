# TESTING — what this suite actually proves

**Status of this document.** Every number below was measured on the branch
`docs/codeant-integration`, taken from `origin/main` at commit
`f22eaceb9b304d48b409837a18e251bb16035832`, on **2026-08-10**. Nothing was
copied from another document on trust. Where a figure is quoted from an
existing artifact rather than re-measured here, it says so and names the file.

This document says what the tests prove and — more usefully — what they do
**not** prove. `docs/ARCHITECTURE.md` §14 owns the evidence-class design;
this document owns the measurement.

---

## 1. The one sentence that matters

> **Every Tally safety guarantee in this repository is proven against a
> simulator and an in-memory double. None of it is proven against Tally.**

That is not a criticism of the suite. It is the suite's own stated boundary,
and the suite enforces it: `ci/acceptance_cli.py` refuses to label a run
`LICENSED_REALTALLY` while the licence read returns `UNKNOWN`. A machine
cannot quietly promote the evidence class.

---

## 2. The size of the suite, measured

| Thing counted | How it was counted | Value |
|---|---|---|
| Python files under `tests/` | `pathlib.Path("tests").rglob("*.py")` | **64** |
| …of which named `test_*.py` | same | **62** |
| Test functions | `ast.walk`, `FunctionDef`/`AsyncFunctionDef` named `test_*` | **1,653** |
| Collected tests, whole suite | `pytest -q`, `testpaths = ["tests", "ci"]` | **2,295 passed, 5 xfailed** |
| Gates in `ci/gates.toml` | `tomllib`, length of `[[gate]]` | **20** |

**Two counts, and they are not the same number.** 1,653 is the number of test
*functions*. 2,295 is the number of *collected* tests: parametrised functions
expand, and `testpaths` also pulls in `ci/`. Neither number is a quality.

**The suite result depends on one environment variable.**

```
COVERAGE_CORE unset   2294 passed, 1 skipped, 5 xfailed
COVERAGE_CORE=pytrace 2295 passed,            5 xfailed
```

The skip is `tests/test_mutation_environment.py:168` —
`"COVERAGE_CORE unset: running outside the mutation path"`. `ci/gates.toml`
sets `coverage_core = "pytrace"`. **Run the suite without it and the mutation
score silently under-reports**; the workflow comments say the same thing.

---

## 3. The two doubles, and which one every guarantee rests on

| Double | What it is | Where | Test files referencing it |
|---|---|---|---|
| `FakeTally` | An in-memory Python object implementing `TallyClient` | `accountant/tallyio/fake.py` | **44** |
| `TallySim` | An in-test XML simulator that answers the envelopes `real.py` builds | `tests/test_real_tally.py:163` | **9** |
| `RealTally` | The real connector, 104 KB, `accountant/tallyio/real.py` | — | **22** reference it by name |

Read the third row carefully. **`RealTally` is exercised through `TallySim`,
never against a live Tally.** Of the 22 test files that name `RealTally`:

- **9** drive it against `TallySim` — a simulator that speaks Tally's XML and,
  in its own docstring, *"holds no opinions beyond the assumptions `real.py`
  already documents"*. That last clause is the whole limitation: **the
  simulator was built from the same assumptions as the code it tests.** A
  shared wrong assumption is invisible to both.
- **13** never construct a backend at all. They reference the class name for
  structural checks — imports, `isinstance`, AST scans of which modules may
  select a backend.

### The contract test names its own gap

`tests/test_tally_contract.py` is the file that defines what *any* backend must
do. Its fixture, at **line 63**:

```python
@pytest.fixture
def client() -> TallyClient:
    """Add the real connector here once it exists. Same tests, both backends."""
    t = FakeTally()
```

The docstring is the honest part. The contract runs against `FakeTally` today.
The file's own header says the G3 section *"does not prove anything about a
real TallyPrime."* It is written so the same tests run unchanged once the
fixture also yields the real connector. **That has not happened.**

### The suite proves it never touched the network

Several tests replace `socket` with a function that raises, then assert the
code path still completes:

```
tests/test_decide.py:196-197     monkeypatch.setattr(socket, "socket", explode)
tests/test_detectors.py:494-495  same
tests/test_memory.py:893         test_no_memory_operation_opens_a_socket
tests/test_taxonomy.py:599       forbids importing urllib, requests,
                                 http.client, socket, anthropic
```

This is a genuinely strong result — it is a *conservation law*, not an
opinion. It also proves the opposite of live coverage: **the guarantees hold
precisely because nothing ever leaves the process.**

---

## 4. The evidence classes, and the empty one

`ci/acceptance.py:74` and `docs/ARCHITECTURE.md:1406` define five:

```
UNIT_TEST · FAKETALLY · SIMULATOR · EDUCATIONAL_TALLY · LICENSED_REALTALLY
```

| Class | Evidence in this repository |
|---|---|
| `UNIT_TEST` | the bulk of 2,295 collected tests |
| `FAKETALLY` | 44 test files |
| `SIMULATOR` | 9 test files, via `TallySim` |
| `EDUCATIONAL_TALLY` | limited; the owner's standing decision keeps Tally in Educational mode |
| `LICENSED_REALTALLY` | **zero. Every occurrence in the tree is a `BLOCKED` marker.** |

Grep it and the pattern is unmistakable — `artifacts/phase9_exit_audit.md:479`
(`LICENSED_REALTALLY = BLOCKED until B-01 is verified`),
`artifacts/phase8_scope.md:417-418` (two rows, both `HUMAN_ACTION_REQUIRED`),
`artifacts/realtally_readiness.md:317` (listed under what must not be
claimed). There is no row anywhere that records a passing licensed run,
because there has not been one.

**The blocker is one human action, not an engineering task.** Someone has to
create a company with four named ledgers in the TallyPrime GUI; the XML
gateway refuses to create a company. Until then this class stays empty and no
amount of test-writing moves it.

---

## 5. What is *not* fake, and is genuinely load-bearing

It would be wrong to read section 3 as "the suite proves nothing". Three
things in here carry real weight, and none of them depend on a backend.

### 5.1 The AST structural guards

**23 test functions across 13 files** parse the project's own source with
`ast` and assert on its *shape*, not its behaviour. Measured by walking each
test function's source segment for `ast.` usage.

Behavioural tests can be satisfied by a lucky code path. A structural guard
cannot: it asserts that a particular call does not exist, that a module does
not import another, that a name is never loaded in a given scope. Examples:

```
tests/test_adapter_contract.py:647-920  which modules may select a backend
tests/test_company_identity.py:581-594  scope-aware Name/Load walk
tests/test_no_reader.py                 no reader enforcement
tests/test_flag_cap.py:294              the cap is in the code, not the test
```

The **D-05 guard** is the one to understand. D-05 is the owner's supplier
legal-identity decision — a bare name against a `Pvt Ltd` is `AMBIGUOUS`, and
guessing `SAME` is the dangerous error. The guard exists because the live
index once keyed on a *stripped* subject, which silently merged two suppliers.
`accountant/memory/index.py:24` records it, and the structural test makes the
stripped-subject indexing pattern unwritable rather than merely untested.

### 5.2 Mutation testing, including mutants injected by hand

`pytest-gremlins` runs as two of the twenty gates (`cached-mutation`,
`full-mutation`) plus `mutation-accounting`.

The part that is not automatable is the part that convinces:
`artifacts/detector_gate.md:267` records **"Three breaks, three caught, none
survived. The code was restored after each."** Someone deliberately broke
working code and confirmed the suite turned red. That is the only test of a
test suite that a test suite cannot fake.

**Recorded scores, quoted not re-measured — and they disagree with each
other:**

| Source | Figure |
|---|---|
| `artifacts/launch_baseline.md:213` | 1,394 of 1,402 terminal mutants, at commit `4cc290f` |
| `README.md:49` | 94% of 267 mutants |

The two are not reconcilable from the documents alone; they name different
mutant populations and neither says which scope it covers. **Neither was
re-run on this branch** — mutation score is `GITHUB_REQUIRED`
(`artifacts/phase7_evidence.md:701`), meaning it is only valid from the CI
run. The discrepancy is recorded here rather than resolved, because resolving
it by picking the nicer number is exactly the failure this repository keeps
guarding against.

### 5.3 Real UK public-spend data

`accountant/ingest/` loads **real UK central-government transaction-level
spend over £25,000**, published under the Open Government Licence v3
(`accountant/ingest/sources.py:31`). `accountant/memory/__init__.py:5` records
the working set as **16,011 rows**.

This matters because it is the one input in the whole project that nobody in
this repository authored. Synthetic fixtures agree with the assumptions of
whoever wrote them. Real filings do not. `accountant/ingest/fetch.py:35`
pins `PERMITTED_HOST_SUFFIX = ".gov.uk"` and refuses anything that is not
HTTPS on a `gov.uk` host, so the data source cannot silently drift.

### 5.4 The question-rate measurement, and its exact scope

    fixture        20 pairs of X against X Pvt Ltd
    SAME           0
    AMBIGUOUS      20
    questions      20
    unsafe merges  0

Source: `artifacts/phase9_exit_audit.md:461-462`, measured by
`tests/test_legal_identity_live.py:788` (`_measure_question_rate`).

**This is one fixture, and it is the only fixture.** Product-wide question
rate is `NOT_MEASURED`. Asserting a question rate of zero would be false in
two directions at once: the fixture measured 20 questions, not 0, and it
measured them on 20 hand-built pairs, not on the product.

---

## 6. Where CodeAnt sits in this picture

CodeAnt AI is an **advisory pull-request review layer**. Its place is defined
by what it is not:

```
   deterministic guards          CodeAnt
   ────────────────────          ───────
   20 gates in ci/gates.toml     reads a diff, may post a review
   23 AST structural guards      no gate depends on it
   30-case GST safety sweep      required_for_merge: false
   ci/check_stubs.py             role: advisory_pr_review
   pass/fail, reproducible       advisory, non-reproducible
```

Three rules, and none of them are negotiable by configuration:

1. **CodeAnt is never merge authority.** Ruleset `20557129` requires exactly
   two contexts, `pr-fast` and `ci-gate`, both pinned to `integration_id`
   15368 (GitHub Actions), measured 2026-08-10T06:59:21Z.
2. **A CodeAnt approval is never evidence that the tests passed.** Only the
   check runs are that, and only from the pinned app.
3. **CodeAnt is an additional layer, never a replacement.** If it later finds
   something a deterministic guard also catches, the guard stays. If it misses
   something, the miss is recorded and the guard still stays.

**Its first observed act was to decline.** On PR #29 — the first pull request
opened after installation — `codeant-ai[bot]` posted within 4 seconds and
skipped the review, because the diff changes more than 100 files (208).
Measured 2026-08-10T07:29:52Z.

    installed         PASS           an app that posts is installed
    comment observed  PASS           1 issue comment
    review observed   NOT_OBSERVED   given a PR, it opted out
    fixtures          NOT_MEASURED   12 defined, 0 run

So a fourth rule joins the three above:

4. **A CodeAnt silence on a large pull request is not review cover.** Above
   ~100 changed files it does not look, which is the inverse of defence in
   depth — the biggest diff is the easiest place to hide a change. Measured on
   both sides of the threshold at 2026-08-10T07:42:01Z:

```
PR #29   208 files  ->  SKIPPED    1 comment, 0 reviews
PR #30     7 files  ->  REVIEWED   1 review + 2 line comments,
                                   1 Critical and 1 Major, both real
```

The mitigation is free: **keep pull requests small enough to be read.**

The full record, the commands, and the 12 review fixtures are in
[`artifacts/codeant_integration.md`](../artifacts/codeant_integration.md).

---

## 7. Reproducing every number in this document

```bash
# counts
python - <<'PY'
import ast, pathlib
files = sorted(pathlib.Path("tests").rglob("*.py"))
n = sum(
    isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name.startswith("test_")
    for f in files for x in ast.walk(ast.parse(f.read_text()))
)
print(len(files), "files,", n, "test functions")
PY

git grep -l FakeTally -- tests/ | wc -l     # 44
git grep -l RealTally -- tests/ | wc -l     # 22
git grep -l TallySim  -- tests/ | wc -l     #  9

# the suite - the env var is not optional
COVERAGE_CORE=pytrace pytest -q              # 2295 passed, 5 xfailed

# the gate set
python -c "import tomllib;print(len(tomllib.load(open('ci/gates.toml','rb'))['gate']))"

# the document validator
python scripts/validate_project_truth.py     # 30 checks, 30 passed
```

**If a number in this document cannot be reproduced by the command next to
it, the document is wrong and the command is right.**
