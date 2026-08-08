"""The canary. Proves the mutation measurement works, not merely that it is set.

Measured on this repository 2026-08-07: the same code and the same tests scored
87.92% on one coverage core and 94.34% on another. Nothing was wrong with the
code. The MEASUREMENT was wrong, and it was wrong silently.

The mechanism, isolated 2026-08-08:

    cov.start()
    for name in ("one", "two", "three"):
        cov.switch_context(name)     # what pytest-gremlins does per mutant
        run_something()

    COVERAGE_CORE=sysmon   ->  contexts recorded: ['one']
    COVERAGE_CORE=pytrace  ->  contexts recorded: ['one', 'two', 'three']

On sysmon only the FIRST switch takes effect. Every later one is silently
dropped, so every mutant after the first is scored against the wrong set of
tests. Coverage does warn - "Dynamic contexts aren't supported with
core=sysmon" - but a warning on stderr in a 267-mutant run is not a gate.

Checking that COVERAGE_CORE is set proves the seatbelt is buckled. This file
proves it is attached to the car.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REQUIRED_CORE = "pytrace"
ROOT = Path(__file__).resolve().parent.parent

# Three switches, so a core that honours only the first is caught. One switch
# would pass on both cores and prove nothing.
CONTEXTS = ("ctx_one", "ctx_two", "ctx_three")

PROBE = """
import json
import pathlib
import sys
import tempfile
import warnings

import coverage

work = pathlib.Path(tempfile.mkdtemp())
(work / "probe_mod.py").write_text("def add(a, b):\\n    return a + b\\n")
sys.path.insert(0, str(work))

cov = coverage.Coverage(data_file=None, branch=True, source=[str(work)])
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    cov.start()
    import probe_mod
    for name in {contexts!r}:
        cov.switch_context(name)
        probe_mod.add(1, 1)
    cov.stop()
    messages = [str(w.message) for w in caught]

print(json.dumps({{
    "contexts": sorted(c for c in cov.get_data().measured_contexts() if c),
    "warnings": messages,
}}))
"""


def _probe(core: str) -> dict[str, list[str]]:
    """Run the switch_context pattern under one core and report what stuck."""
    script = textwrap.dedent(PROBE).format(contexts=CONTEXTS)
    # S603: the script is the PROBE constant in this file and CONTEXTS, also in
    # this file. Nothing here comes from outside the repository. A subprocess is
    # required rather than an in-process check, because COVERAGE_CORE is read
    # once when coverage starts and cannot be changed inside a running process.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        env={**os.environ, "COVERAGE_CORE": core},
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("{"):
            parsed: dict[str, list[str]] = json.loads(line)
            return parsed
    raise AssertionError(
        f"probe produced no result under core={core}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---- the canary -------------------------------------------------------------


def test_every_context_switch_is_recorded_on_the_required_core():
    """The mapping works: all three switches stick, so gremlins can trust it."""
    got = _probe(REQUIRED_CORE)
    assert got["contexts"] == sorted(CONTEXTS), (
        f"COVERAGE_CORE={REQUIRED_CORE} recorded {got['contexts']} of "
        f"{sorted(CONTEXTS)}. pytest-gremlins switches context once per mutant, "
        "so a dropped switch means mutants are scored against the wrong tests "
        "and the mutation percentage is not evidence."
    )


def test_the_required_core_emits_no_context_warning():
    got = _probe(REQUIRED_CORE)
    offending = [w for w in got["warnings"] if "dynamic context" in w.lower()]
    assert not offending, f"coverage warned about dynamic contexts: {offending}"


def test_the_canary_can_fail():
    """A test that cannot fail is not a test.

    Proves the canary distinguishes a working mapping from a broken one, by
    running the identical probe on the core known to break it.

    The sysmon core only exhibits the fault on interpreters where coverage
    actually USES it. On Python 3.12 coverage silently falls back to a core
    that does support dynamic contexts, so the probe comes back complete and
    there is nothing to discriminate against. Measured 2026-08-08: broken on
    local 3.14, not reproducible on the 3.12.3 GitHub runner.

    Where the fault does not reproduce this skips LOUDLY rather than passing
    quietly. A green tick that checked nothing is the exact dishonesty these
    gates exist to prevent.
    """
    broken = _probe("sysmon")
    warned = any("dynamic context" in w.lower() for w in broken["warnings"])

    if not warned and broken["contexts"] == sorted(CONTEXTS):
        pytest.skip(
            f"the sysmon fault does not reproduce on {sys.version.split()[0]}: "
            "coverage fell back to a core that supports dynamic contexts, so "
            "there is nothing here to discriminate against. The positive "
            "canary above still proves the mapping works on this interpreter."
        )

    assert broken["contexts"] != sorted(CONTEXTS), (
        "the sysmon core warned about dynamic contexts yet recorded every "
        "switch. The warning and the behaviour disagree; re-derive this check "
        "before trusting any mutation score."
    )


# ---- the environment is wired in one place ---------------------------------


def test_the_required_core_is_named_in_one_place_only():
    """ci/check_mutation.py is the single authority on which core is required."""
    checker = (ROOT / "ci" / "check_mutation.py").read_text()
    assert f'REQUIRED_CORE = "{REQUIRED_CORE}"' in checker


def test_the_guards_script_exports_the_core():
    guards = (ROOT / "scripts" / "guards").read_text()
    assert f"export COVERAGE_CORE={REQUIRED_CORE}" in guards


@pytest.mark.skipif(
    os.environ.get("COVERAGE_CORE") is None,
    reason="COVERAGE_CORE unset: running outside the mutation path",
)
def test_coverage_core_is_pytrace_whenever_it_is_set():
    """A bare pytest run may leave it unset. It must never be set to a value
    that silently breaks the mapping."""
    assert os.environ["COVERAGE_CORE"] == REQUIRED_CORE
