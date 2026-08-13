# The `.github` change for the OCR binary — stated in full, before it is made

Standing rules 11–13: **before** any `.github` edit, state every line that will
change; **after**, state every line that did; anything not on the list must not
be in the diff. Recorded memory also says `.github/**` is write-denied in this
environment even with explicit authorisation, so this file **is** the
deliverable — the diff, ready to apply, not a plan to write one.

Blocked on the owner applying it. Blocking nothing else: without it the OCR
tests skip loudly with a reason, the text-layer path still runs, and no gate is
weakened.

## Scope, and what is deliberately outside it

```
IN     install the tesseract-ocr system binary in the two jobs that run the
       whole test suite
OUT    every other job
OUT    any threshold          they stay at 0 or 90
OUT    any gate               gate_names.lock stays at 20 or higher, never lower
OUT    ci/gates.toml          not opened
OUT    new jobs, new triggers, new permissions, action version bumps
```

## Which jobs, and why only these two

Measured by reading every workflow, not assumed:

| File | Job | Runs | Needs the binary? |
|---|---|---|---|
| `pr-fast.yml` | `pr-fast` | `pytest tests/test_gate_contract.py -q` only | **no** — one file, no OCR in it |
| `pr-fast.yml` | `pr-full` | `pytest -n auto` — the whole suite | **yes** |
| `pr-fast.yml` | `ci-gate` | aggregates results | no |
| `full.yml` | `full-tests` | `pytest -n auto` — the whole suite | **yes** |
| `full.yml` | `security` / `build` / `workflow-checks` | audit, wheel, actionlint | no |
| `full.yml` | `mutation` | mutation run | **no** — OCR tests skip; adding a 30 s apt step to every mutant's job for tests that skip is cost with no signal |
| `full.yml` | `schedule` / `nightly-report` | scheduling, reporting | no |

**Two jobs. Two identical steps. No other line in either file is touched.**

## Change 1 of 2 — `.github/workflows/pr-fast.yml`, job `pr-full`

Inserted between `install uv` (ends line 255) and `sync dependencies from the
lockfile` (line 257). **Six lines added. Zero lines removed. Zero lines
modified.**

```yaml
      - name: install the text reader
        # Tesseract is a system binary, not a Python package, so uv cannot
        # bring it. The app starts and refuses cleanly without it - a missing
        # binary is a plain sentence, never a crash - so this is not a hard
        # requirement. Without it the OCR tests skip and s2_extraction stays
        # where it is.
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr
```

Context, unchanged, for the reviewer:

```yaml
          cache-dependency-glob: uv.lock                    # 255, unchanged
                                                            # 256, blank
      - name: sync dependencies from the lockfile           # 257, unchanged
        run: uv sync --extra dev --frozen                   # 258, unchanged
```

## Change 2 of 2 — `.github/workflows/full.yml`, job `full-tests`

Inserted between `install uv` (ends line 46) and `sync dependencies from the
lockfile` (line 47). Byte-identical step to Change 1. This file has no blank
lines between steps, so none is added. **Six lines added. Zero removed. Zero
modified.**

```yaml
      - name: install the text reader
        # Tesseract is a system binary, not a Python package, so uv cannot
        # bring it. The app starts and refuses cleanly without it - a missing
        # binary is a plain sentence, never a crash - so this is not a hard
        # requirement. Without it the OCR tests skip and s2_extraction stays
        # where it is.
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr
```

## The whole change, counted

```
files touched      2
lines added        12   (6 + 6, identical)
lines removed      0
lines modified     0
jobs touched       2 of 12
thresholds moved   0
gates removed      0
gate count         20 before, 20 after
```

## Why `--no-install-recommends`

Without it, `tesseract-ocr` pulls a long recommends chain onto every run. The
package plus `tesseract-ocr-eng` is all that is used; English traineddata ships
as a hard dependency and is not requested separately.

`sudo apt-get update` is required on a GitHub runner — its package lists are
stale and `install` fails without it. It is on the same line deliberately, so a
failed update cannot be followed by an install that silently picks an old
version.

## After it is applied — what to check, and what must be true

1. `actionlint` and `zizmor` pass.
2. `tests/test_gate_contract.py` passes — `ci/gates.toml` matches
   `ci/gate_names.lock` exactly.
3. `ci/check_workflow_integrity.py` passes. It reads `gate_names.lock` from
   `origin/main`, which a pull request cannot edit while it is being graded, and
   fails on any name main has that the branch does not. This change adds no name
   and removes none.
4. `git diff` on the two files shows **exactly the twelve lines above and
   nothing else.** If it shows anything more, that is reported, not explained
   away.

## If it is never applied

Nothing stalls. The OCR tests are marked and skip **loudly, with a reason** —
and a test asserts the skip is loud rather than silent, because a quiet skip is
indistinguishable from a pass. The text-layer tier needs no binary and runs
normally. No gate is weakened and the gate count does not change.

Separately, and worth knowing before anyone rushes this: `docs/OCR_CORPUS_FINDING.md`
records that the corpus's 20 PNGs and 20 JPEGs cannot be read by any OCR engine
— the JPEGs contain no pixels at all. So applying this change will **not** move
`s2_extraction`. It makes the OCR path testable in CI, which is worth having on
its own, and nothing more than that is claimed for it.
