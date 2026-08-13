# The `.github` change for the OCR binary — stated in full, before it is made

Standing rules 11–13: **before** any `.github` edit, state every line that will
change; **after**, state every line that did; anything not on the list must not
be in the diff. Recorded memory also says `.github/**` is write-denied in this
environment even with explicit authorisation, so this file **is** the
deliverable — the diff, ready to apply, not a plan to write one.

## THE PREVIOUS VERSION OF THIS FILE WAS WRONG. Two things, both measured.

**1. It said TWO jobs. The correct number is THREE.**

It claimed job `pr-fast` "runs `pytest tests/test_gate_contract.py -q` only —
one file, no OCR in it". That is one *step* of that job, not the job. The
`changed-tests` step at `.github/workflows/pr-fast.yml:120-137` runs
`uv run pytest -n auto` — the **complete suite** — whenever `.testmondata` is
absent, and a hosted runner starts with no testmon database, so the fallback is
taken on every run. The step even says so in its own comment: *"A hosted runner
starts with no testmon database, so in practice this runs the complete suite."*

Job `pr-fast` is exactly the job that failed on run **31674135775**. The old
table would have had the owner apply the fix to two jobs and leave the failing
one alone.

**2. It said applying this "will NOT move `s2_extraction`". It does more than
that — it turns a red CI green.**

Two tests in `tests/test_gst_ground_truth_runner.py` FAIL without the binary.
Reproduced here, `PATH=/usr/bin:/bin`, 2026-08-13:

```
FAILED test_the_extraction_section_scores_exit_one_and_exit_two_separately
        {'party': 20} != {'party': 23}
FAILED test_a_refusal_that_states_a_reason_is_still_a_refusal
        {'party': 20} != {'party': 28}
2 failed, 26 deselected in 0.22s
```

With `tesseract` on `PATH`, the same file is **28 passed in 6.0s**. The old
claim was reasoning from `docs/OCR_CORPUS_FINDING.md` — that the corpus's 20
JPEGs hold no pixels — and it was true about the JPEGs and wrong about the 20
PNGs, which the picture rung reads. `party` is engine-dependent; the other three
fields are not.

A doc that quietly corrects its own numbers is worse than one that records it
was wrong, so both errors stay written down here.

## Scope, and what is deliberately outside it

```
IN     install the tesseract-ocr system binary in the THREE jobs that run the
       whole test suite
OUT    every other job
OUT    any threshold          they stay at 0 or 90
OUT    any gate               gate_names.lock stays at 20 or higher, never lower
OUT    ci/gates.toml          not opened
OUT    new jobs, new triggers, new permissions, action version bumps
OUT    any change to the two tests   no skip guard is added; see the last
                                     section for why that is a decision
```

## Which jobs, and why exactly these three

Measured by reading every step of every job, not by reading job names:

| File | Job | What it actually runs | Needs the binary? |
|---|---|---|---|
| `pr-fast.yml` | `pr-fast` | step `changed-tests` → `uv run pytest -n auto`, the whole suite, because `.testmondata` never exists on a hosted runner | **YES** — and this is the job that went red on run 31674135775 |
| `pr-fast.yml` | `pr-full` | step `full-tests` → `uv run pytest -n auto`, the whole suite | **YES** |
| `pr-fast.yml` | `ci-gate` | `ci/check_aggregate.py` — reconciles results, runs no tests | no |
| `full.yml` | `full-tests` | step `full-tests` → `uv run pytest -n auto`, plus `coverage run -m pytest` | **YES** |
| `full.yml` | `security` | `pip-audit`, `bandit` | no |
| `full.yml` | `build` | `python -m build`, `twine check` | no |
| `full.yml` | `workflow-checks` | actionlint, zizmor, `ci/check_stubs.py` | no |
| `full.yml` | `mutation` | `pytest --gremlins` | **unmeasured — see the open question below** |
| `full.yml` | `ci-gate` | reconciles results | no |
| `full.yml` | `nightly-report` | opens/closes the failure issue | no |
| `claude.yml` | `claude` | the Claude action | no |
| `watchdog.yml` | `nightly-watchdog`, `ruleset-drift` | branch-protection checks | no |

Thirteen jobs, not twelve. The old table also listed a `full.yml` job called
`schedule`; there is no such job — `schedule:` is a trigger.

**Three jobs. Three byte-identical steps. No other line in any file is touched.**

## The step — identical in all three places

```yaml
      - name: install the text reader
        # Tesseract is a system binary, not a Python package, so uv cannot
        # bring it. Two tests in tests/test_gst_ground_truth_runner.py FAIL
        # without it, deliberately: no skip guard, because they are the only
        # CI coverage of the s2_extraction benchmark. docs/CI_OCR_INSTALL.md.
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng
```

Six lines. **Both packages are named.** An earlier version of this file asked
for `tesseract-ocr` alone; that is the engine with no trained data, and it is
not what `docs/DEPLOY.md` installs into the image.

## Where it goes — three insertion points, with the lines around each

The three anchors look alike: `cache-dependency-glob: uv.lock`, then
`- name: sync dependencies from the lockfile`. The line that tells them apart is
the `uv sync` flag — **`--locked` is job `pr-fast`, `--frozen` is `pr-full`** —
and `full.yml` is a different file with no blank lines between its steps.

**Apply them bottom-up — 3, then 2, then 1 — and no line number in this
document moves under you.**

### Insertion 1 of 3 — `pr-fast.yml`, job `pr-fast` (job starts line 55)

Insert the six step lines **plus one blank line** immediately before line 76.
This job separates its steps with blank lines, so one is added.
**Seven lines added. Zero removed. Zero modified.**

```yaml
          cache-suffix: ${{ runner.os }}-${{ hashFiles('uv.lock') }}   # 73
          cache-dependency-glob: uv.lock                               # 74
                                                                       # 75 blank
      - name: sync dependencies from the lockfile                      # 76  <- insert above this
        # --locked, NOT --frozen. This step IS the `lockfile` gate ...  # 77
```

After: the new step occupies 76–81, a blank line is 82, and the old line 76 is
now 83.

### Insertion 2 of 3 — `pr-fast.yml`, job `pr-full` (job starts line 229)

Same shape, same blank line. **Seven lines added. Zero removed. Zero modified.**

```yaml
          cache-suffix: ${{ runner.os }}-${{ hashFiles('uv.lock') }}   # 254
          cache-dependency-glob: uv.lock                               # 255
                                                                       # 256 blank
      - name: sync dependencies from the lockfile                      # 257  <- insert above this
        run: uv sync --extra dev --frozen                              # 258
```

If insertion 1 is applied first, this anchor has moved from 257 to **264**.
Applying 2 before 1 avoids the arithmetic entirely.

### Insertion 3 of 3 — `full.yml`, job `full-tests` (job starts line 32)

This file has **no blank lines between steps, so none is added.**
**Six lines added. Zero removed. Zero modified.**

```yaml
          cache-suffix: ${{ runner.os }}-${{ hashFiles('uv.lock') }}   # 45
          cache-dependency-glob: uv.lock                               # 46
      - name: sync dependencies from the lockfile                      # 47  <- insert above this
        run: uv sync --extra dev --frozen                              # 48
```

## The whole change, counted

```
files touched      2
jobs touched       3 of 13
lines added        20   (7 + 7 + 6 — the step is identical, the blank line is not)
lines removed      0
lines modified     0
pr-fast.yml        405 lines before, 419 after
full.yml           334 lines before, 340 after
thresholds moved   0
gates removed      0
gate count         20 before, 20 after
```

`git diff --stat` must read exactly `2 files changed, 20 insertions(+)` with no
deletions column at all.

## What it fixes, and what it costs

**Fixes.** The two tests above stop failing. That is the whole of it, and it is
the difference between a red required check and a green one. Nothing else in the
suite depends on the binary: every other OCR test carries
`pytest.mark.skipif(shutil.which("tesseract") is None, ...)` —
`tests/test_pagereader.py:76`, `tests/test_labels.py:88`,
`tests/test_freeocr.py:126`.

That is the claim the step rests on, so it was attacked rather than asserted:
the whole suite was run at `PATH=/usr/bin:/bin` with those two deselected —
**4398 passed, 46 skipped, 0 failures attributable to the missing engine**. The
16 failures that run did produce are all in `tests/test_ledger_placement.py`
(another agent's in-flight file, untracked) and two in
`tests/test_phase5b_readiness.py`, and both of the latter fail identically
*with* `tesseract` on `PATH` — they want `uv`, which this machine has not got.
Neither set is OCR. One apt step is sufficient; no second missing dependency is
hiding behind it.

**Does not fix.** The `s2_extraction` gate still FAILS. 23 of 80 against a
required 76 is a fail, and so is 20 of 80. The binary changes what CI *measures*,
not whether the benchmark passes. Anyone reading "it turns CI green" as "the
benchmark passes" has read it wrong.

**Costs — measured, and not known when this file was first written.**
`docs/DEPLOY.md` measured the same two packages in the Docker image:

```
tesseract-ocr + tesseract-ocr-eng layer    107 MB uncompressed    12.0 s
```

107 MB is the largest single layer in that image — larger than the interpreter
build layer (45 MB) and four times the virtualenv (25.8 MB). On a GitHub runner
the cost is the download and unpack, once per job, uncached: budget the same
order, ~12 s, three times. Against a 15-minute `pr-fast` timeout and a
20-minute `full-tests` timeout, that is affordable. It is not free, and it is
paid on every run.

## Why `--no-install-recommends`, and why both package names

Without it, `tesseract-ocr` pulls a long recommends chain onto every run.

`tesseract-ocr-eng` is a hard dependency of `tesseract-ocr` on Debian, so it
would arrive regardless — it is named anyway, because **which languages this
runner reads is a decision, and not apt's to make.** It is also what
`docs/DEPLOY.md` names, so CI and the shipped image read with the same two
packages and the same trained data. Naming any other language pack, or
`tesseract-ocr-all`, fails a test.

`sudo apt-get update` is required on a GitHub runner — its package lists are
stale and `install` fails without it. It is on the same line deliberately, so a
failed update cannot be followed by an install that silently picks an old
version.

## After it is applied — what to check, and what must be true

1. `actionlint` and `zizmor --persona=pedantic` pass.
2. `tests/test_gate_contract.py` passes — `ci/gates.toml` matches
   `ci/gate_names.lock` exactly.
3. `ci/check_workflow_integrity.py` passes. It reads `gate_names.lock` from
   `origin/main`, which a pull request cannot edit while it is being graded, and
   fails on any name main has that the branch does not. This change adds no name
   and removes none.
4. `git diff` on the two files shows **exactly the twenty lines above and
   nothing else.** If it shows anything more, that is reported, not explained
   away.
5. The new step's log shows `tesseract-ocr` and `tesseract-ocr-eng` both
   installed — two packages, not one.
6. In the job log, `tests/test_gst_ground_truth_runner.py` reports **28 passed**.
   If `{'party': 20}` appears anywhere in the output, the binary is not on
   `PATH` in that job and the step did not take effect. 20 is the signature of a
   missing engine, not of a product regression.

## The open question this change does NOT settle

The old table excused job `mutation` on the grounds that "OCR tests skip". That
reasoning is void: these two tests do not skip, they fail. Whether that matters
to `pytest --gremlins` is **not measured** — a mutant can look killed by a test
that was already failing before any mutation, which would inflate the score
rather than deflate it.

`Assumption: two baseline failures corrupt the mutation score · Confidence: 60%
· Check: run `uv run pytest --gremlins --gremlin-targets=accountant -q` on a
machine with no tesseract and compare `ci/check_mutation.py` output against a
run with the binary present.`

Not included in this diff, because it is not measured and this diff is. Owner's
call once the check above is run.

## Why the two tests have no skip guard, and must not get one

Owner decision. `test_the_extraction_section_scores_exit_one_and_exit_two_separately`
and `test_a_refusal_that_states_a_reason_is_still_a_refusal` are the **only CI
coverage of the whole `s2_extraction` benchmark table**. A
`skipif(shutil.which("tesseract") is None)` on them would make CI green by
checking nothing — the benchmark would stop being measured, and the loudest
possible signal that the engine is missing would become a quiet skip line.

So the failure is the design: CI installs the binary, or CI says out loud that
it did not.

## If it is never applied

CI stays red on every pull request, in the required check, until it is. This is
no longer a "nothing stalls" change — the previous version of this file said it
was, and that was written before the two tests existed in their current form.

Everything else still works: the text-layer tier needs no binary, the
application refuses cleanly with a plain sentence rather than crashing when the
engine is absent, no gate is weakened, and the gate count does not change.
