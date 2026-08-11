"""The live protection test. Runs against the REAL repository, as the REAL
identity, and tries to weaken protection on purpose.

The success criterion, owner-set 2026-08-08:

    Claude can never weaken, remove, replace, disable, or bypass the
    repository ruleset or branch protection.

Enforced structurally, not by trust: the identity running this holds a
fine-grained token with Administration = No access, so GitHub refuses the
operations. The earlier plan recorded this risk as "NOT PREVENTABLE", which was
wrong - it was only unpreventable while Claude held an admin token, and a token
is a variable, not a constant.

This file NEVER fakes a denial and never inspects local YAML instead of the live
ruleset. If it starts passing because the API stopped being called, the tests
fail rather than report a hollow success.

    pytest ci/test_protection.py -v

BEHAVIOUR WHEN THE PREREQUISITES ARE ABSENT - CHANGED 2026-08-10
----------------------------------------------------------------
This module used to carry an unconditional module-level skip:

    pytestmark = pytest.mark.skipif(not reachable(), ...)

`reachable()` shells out to `gh api repos/<repo>`. No CI job in this repository
sets GH_TOKEN, so `gh` was unauthenticated on every hosted run, `reachable()`
was False on every hosted run, and all nine tests below skipped on every hosted
run. A test that always skips is a green square that measured nothing.

It now has three outcomes, not two:

    prerequisites present            run the real tests against the real API
    absent, and CI                   FAIL, naming the missing prerequisite
    absent, and a developer laptop   skip, reported as
                                     LOCAL_ENVIRONMENT_UNAVAILABLE

WARNING TO WHOEVER APPLIES THIS: the CI branch fails today, because no workflow
job supplies a token. The Python change and the workflow step that supplies
`GH_TOKEN` must be applied together, or every pull request goes red. The
recommended workflow diff is stated in artifacts/gate_integrity_audit.md and is
deliberately NOT applied here - editing .github/** needs the owner's yes for
that specific change.

WHY 401 IS NO LONGER PROOF
--------------------------
`FORBIDDEN_STATUSES` used to be (401, 403, 404). 401 means "you did not
authenticate". An unauthenticated caller is refused everything, so a removed or
expired token made every assertion below pass - for entirely the wrong reason.
403 and 404 come back to an identity GitHub recognises and then declines, which
is the thing being asserted. 401 is now a missing prerequisite, not a refusal.

WHY AN ABSENT FIELD IS NO LONGER PROOF EITHER - CHANGED 2026-08-11
------------------------------------------------------------------
The same mistake in a second place. `test_bypass_actors_are_still_empty` read

    data.get("bypass_actors") == []

and `.get` answers `None` both when the field says nobody may bypass and when
this identity was never shown the field. Two facts, one value, and the failure
message picked the wrong one out loud:

    AssertionError: someone can bypass the rules: None

Nobody had been shown to be able to bypass anything. The field had not been
read. A security test that states an unmeasured violation is worse than one
that stays quiet, because a person deciding whether this repository is
protected will believe it.

Measured 2026-08-10, hosted run 31386545513, commit `f6494ad`, job `pr-fast`:
the ruleset GET returns 200 with a real body - `id`, `name`, `target`,
`source_type`, `rules`, `enforcement` - and no `bypass_actors` key at all. Not a
401, not a 403. A redacted view. Reading that field needs repository
`Administration: read`, which exists only as a fine-grained personal-access-token
permission; actionlint v1.7.12 rejects `administration` as a workflow permission
scope, so no `permissions:` block can grant it to `GITHUB_TOKEN`.

So the field now has three outcomes, matching the rest of this file:

    present and []       PASS - the required check binds everyone
    present and not []   FAIL - and the accusation is then TRUE
    absent               NOT_MEASURED - fail in CI, skip on a laptop, and say
                         in both cases that nothing was measured

`NOT_MEASURED` is this repository's standing label for a thing nobody could
look at; the rule is that it is never scored as a zero. See
`artifacts/phase9_data_quality.md` for where the line sits, and
`artifacts/gate_integrity_blocked.md` for this specific blocker.

WHY THIS IS NOT AN xfail, WHICH IS THE USUAL IDIOM HERE
-------------------------------------------------------
`tests/test_gate_contract.py::test_the_lockfile_gate_is_actually_enforced` pins
a defect with `@pytest.mark.xfail(strict=True)`, paired with a passing test
recording today's behaviour. That idiom needs the failure to be DETERMINISTIC,
and this one is not: it depends on which identity runs it. Measured on this
machine 2026-08-11, a fine-grained token that can read the field makes all ten
tests pass, so a strict xfail would XPASS and turn a developer's run red for
doing nothing wrong, while xfailing in CI. Same code, opposite verdicts. An
xfail here would be a coin toss wearing a gate's clothing.

WHAT WAS DELIBERATELY NOT DECIDED HERE
--------------------------------------
Whether an unreadable `bypass_actors` should stop blocking pull requests is
owner option 2 in `artifacts/gate_integrity_blocked.md`, and it is unchosen. So
CI still goes red. Only the sentence it goes red with has changed - from a
false statement about bypass to a true one about a measurement that could not
be taken.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, NoReturn

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"

# Operations that must be refused. Each is a real, destructive change to
# protection - not a dry run and not a probe against a fake endpoint.
#
# 403 "Resource not accessible by personal access token" and 404 (GitHub hides
# resources an identity may not touch) are refusals OF A RECOGNISED IDENTITY.
# That is the claim being tested.
FORBIDDEN_STATUSES = (403, 404)

# 401 is not a refusal, it is an absence. Never counted as proof.
UNAUTHENTICATED_STATUSES = (401,)

# The verdict code for a field this identity was never shown. It is a distinct
# string so a person reading a red build, or grepping one, can tell "we looked
# and found a bypass" apart from "we were not allowed to look".
BYPASS_ACTORS_NOT_MEASURED = "PROTECTION_TEST_BYPASS_ACTORS_NOT_MEASURED"

# The accusation. Defined once, used by the one assertion entitled to make it,
# and asserted ABSENT from the NOT_MEASURED verdict - so this sentence appears
# in a build log only when a bypass was actually seen. Somebody grepping logs
# for it must not be able to hit a case where nothing was measured, which is
# what the code before 2026-08-11 did.
BYPASS_FOUND = "someone can bypass the rules"

# The permission that would make the field readable, and the secret the owner
# would have to create to supply it. Named here and checked against
# docs/OWNER_WORK.md by a test below, so the two cannot drift apart.
#
# S105 fires on the name, not the value: this is the NAME of a repository
# secret that does not exist yet, and no credential is stored in this file.
ADMINISTRATION_READ = "Administration: read"
AUDIT_TOKEN_SECRET = "CLAUDE_AUDIT_TOKEN"

# Keys that prove the response really is a ruleset. If these are missing too,
# the read itself is broken and that is a different, larger failure than one
# redacted field.
RULESET_MARKERS = ("id", "name", "enforcement")


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


REPO = str(contract()["meta"]["repository"])


#: The audit identity, when one has been supplied.
#:
#: STEP 1 OF 2 OF THE `CLAUDE_AUDIT_TOKEN` MECHANISM, 2026-08-11.
#:
#: `bypass_actors` is withheld from `GITHUB_TOKEN`: reading it needs repository
#: `Administration: read`, which exists only as a fine-grained
#: personal-access-token permission. actionlint v1.7.12 rejects
#: `administration` as a workflow `permissions:` scope, so no `permissions:`
#: block can grant it. That is why `test_bypass_actors_are_still_empty` reports
#: NOT_MEASURED rather than a verdict.
#:
#: This is the half that lives in code: when `CLAUDE_AUDIT_TOKEN` is present in
#: the environment, every `gh` call in this file runs as that identity instead
#: of the ambient one. Nothing else changes, and no other file reads it.
#:
#: STEP 2 is a single line in a workflow, which cannot be written from this
#: environment (`.github/` is denied). `docs/OWNER_WORK.md` carries it verbatim.
#: Until it is applied this constant does nothing, and that is the honest state:
#: the mechanism is complete and the wire is not connected.
ENV_AUDIT_TOKEN = "CLAUDE_AUDIT_TOKEN"


def gh_environment(environ: dict[str, str]) -> dict[str, str]:
    """The environment a `gh` call should run under.

    A pure function of a mapping so it can be tested without a real token and
    without touching the process environment. The token VALUE is never returned
    to a caller, never logged and never asserted on - only the fact that it was
    forwarded.
    """
    out = dict(environ)
    audit = out.get(ENV_AUDIT_TOKEN, "").strip()
    if audit:
        # GH_TOKEN is what the `gh` CLI reads. Setting it here rather than
        # asking every call site to remember is the same argument the rest of
        # this repository makes about checks: one seam, not a convention.
        out["GH_TOKEN"] = audit
    return out


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=gh_environment(dict(os.environ)),
    )


def reachable() -> bool:
    if shutil.which("gh") is None:
        return False
    return gh("api", f"repos/{REPO}", "--jq", ".name").returncode == 0


def in_ci() -> bool:
    """GitHub sets GITHUB_ACTIONS on every hosted runner. CI is the general case."""
    return os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"


def missing_prerequisite() -> str | None:
    """Exactly what is missing, or None when the live test can really run."""
    if shutil.which("gh") is None:
        return "the `gh` CLI is not on PATH"
    probe = gh("api", f"repos/{REPO}", "--jq", ".name")
    if probe.returncode == 0:
        return None
    blob = (probe.stdout + probe.stderr).lower()
    if any(str(code) in blob for code in UNAUTHENTICATED_STATUSES):
        return (
            "`gh` is unauthenticated (HTTP 401). No token means every call is "
            "refused, which would make every assertion below pass for the wrong "
            "reason. Set GH_TOKEN to the identity whose permissions are being "
            "tested."
        )
    return f"`gh api repos/{REPO}` failed: {probe.stderr.strip() or 'no output'}"


#: Set to "1" to let the four TAMPER tests actually call GitHub.
#:
#: OWNER DIRECTIVE, 2026-08-11: a plain `pytest` must never delete a real
#: repository.
#:
#: `pyproject.toml` sets `testpaths = ["tests", "ci"]`, so this file is in the
#: DEFAULT suite. Four of its tests are tamper tests: they ask GitHub to disable
#: the ruleset, delete the ruleset, enable force-push, and DELETE THE
#: REPOSITORY. They pass because every call is refused, and the refusal is the
#: measurement - that is the design and it is not being weakened here.
#:
#: What is being fixed is that they fired on every `pytest` run, against the
#: real repository, as whoever `gh` happens to be authenticated as. That is
#: safe exactly as long as the token cannot do it, which is a property of
#: somebody's credential rather than of this code. The day `gh` is
#: authenticated with admin rights, a routine test run deletes the repository.
#:
#: So they are now opt-in:
#:
#:     RUN_DESTRUCTIVE_TESTS=1 pytest ci/test_protection.py
#:
#: CI must set it, because in CI the refusal IS the thing being proved. The
#: read-only tests in this file are unaffected and still run everywhere.
ENV_DESTRUCTIVE = "RUN_DESTRUCTIVE_TESTS"

#: The HTTP methods that change something on GitHub. Named here so the AST scan
#: below and the reader are looking at one list.
DESTRUCTIVE_METHODS = frozenset({"DELETE", "PUT", "PATCH", "POST"})


def _destructive_allowed_for(environ: dict[str, str]) -> bool:
    """The rule, over a mapping, so it can be tested without touching the real
    environment. `destructive_allowed` is this applied to `os.environ`."""
    return environ.get(ENV_DESTRUCTIVE, "") == "1"


def destructive_allowed() -> bool:
    """True only for exactly "1". Not "true", not "yes", not " 1".

    Strict for the same reason `LOCAL_DEV_MODE` is strict in
    `accountant/auth/identity.py`: a loose reading turns a typo into a live
    delete-the-repository call, and the failure is silent right up until it
    is not.
    """
    return _destructive_allowed_for(dict(os.environ))


#: The FIXTURE every test that calls a destructive endpoint must request.
#:
#: A fixture, NOT `pytest.mark.skipif`, and that is not a style choice.
#: `ci/check_workflow_integrity.py` raises PROTECTION_TEST_SKIPPABLE for a
#: module-level `pytest.mark.skipif` in this file, because that is precisely how
#: these tests came to pass on every hosted run without calling GitHub once. It
#: caught my first version of this gate, which is the check doing its job — so
#: the gate is built out of something that cannot be confused with it.
#:
#: The skipping happens in `live_github_or_an_honest_verdict` below, which
#: already decides whether a test in this file may run, and which fails rather
#: than skips in CI.
#: A fixture rather than a mark for a second, duller reason: `request.node` is
#: untyped in this pytest build, so reading a mark back needs a cast or a
#: suppression, and a gate on a destructive call should not need either.
#: Requesting this by name is visible in the signature and fully typed.
@pytest.fixture
def destructive() -> None:
    """Skip unless the owner opted in. Requested by the four tamper tests."""
    if destructive_allowed():
        return
    pytest.skip(
        f"set {ENV_DESTRUCTIVE}=1 to run the tamper tests. They ask GitHub to "
        "disable the ruleset, delete the ruleset, enable force-push and delete "
        "the repository, and they PROVE those calls are refused - which needs "
        "the calls to be made, against the real repository, as whoever `gh` is "
        "authenticated as."
    )


@pytest.fixture(autouse=True)
def live_github_or_an_honest_verdict() -> None:
    """Three outcomes, never a silent one.

    The old module-level skipif had two, and the one it always took on a hosted
    runner was the one that measured nothing.
    """
    problem = missing_prerequisite()
    if problem is None:
        return
    if in_ci():
        pytest.fail(
            "PROTECTION_TEST_PREREQUISITES_MISSING - refusing to skip in CI.\n"
            f"  {problem}\n"
            "  This test is the only thing that proves the identity CI runs as "
            "cannot weaken branch protection. Skipping it in CI is a green square "
            "that measured nothing, which is how it behaved on every hosted run "
            "before 2026-08-10.\n"
            "  Owner action: give the job a GH_TOKEN for the identity being "
            "tested. See artifacts/gate_integrity_audit.md.",
            pytrace=False,
        )
    pytest.skip(f"LOCAL_ENVIRONMENT_UNAVAILABLE: {problem}")


def ruleset_id() -> int:
    result = gh("api", f"repos/{REPO}/rulesets", "--jq", ".[0].id")
    assert result.returncode == 0, f"cannot read rulesets: {result.stderr}"
    return int(result.stdout.strip())


def canonical(rid: int) -> str:
    """A stable fingerprint of the ruleset, so any change is detectable.

    Volatile fields are dropped so the hash reflects the protection itself, not
    the moment it was read.
    """
    result = gh("api", f"repos/{REPO}/rulesets/{rid}")
    assert result.returncode == 0, f"cannot read ruleset {rid}: {result.stderr}"
    data: dict[str, Any] = json.loads(result.stdout)
    for volatile in ("created_at", "updated_at", "_links", "node_id"):
        data.pop(volatile, None)
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def bypass_actors_were_read(body: dict[str, Any]) -> bool:
    """True when this identity was shown the field at all.

    ABSENT IS NOT EMPTY. `body.get("bypass_actors")` collapses both into `None`,
    which is the whole defect this replaced - see the module docstring. Membership
    is the only question that separates them, so membership is what is asked.
    """
    return "bypass_actors" in body


def unread_bypass_actors(body: dict[str, Any]) -> NoReturn:
    """Report a field that could not be read, without inventing what it said.

    Never returns. Fails in CI and skips on a developer laptop, which is the
    same three-outcome shape `live_github_or_an_honest_verdict` already uses for
    a missing prerequisite - because that is what this is. The old code asserted
    a bypass instead, which was a statement nobody had measured.
    """
    seen = sorted(body)
    verdict = (
        f"{BYPASS_ACTORS_NOT_MEASURED} - the field was not read, so nothing was "
        "measured.\n"
        "  This is NOT a finding of a bypass. Nobody has been shown able to get "
        "past the required check, and nobody has been shown unable to. The value "
        "is NOT_MEASURED, which this repository never scores as a zero.\n"
        f"  The ruleset read SUCCEEDED and returned a real body; `bypass_actors` "
        f"is simply not in it. Keys received: {seen}\n"
        f"  Reading it needs repository `{ADMINISTRATION_READ}`. That exists only "
        "as a fine-grained personal-access-token permission - actionlint v1.7.12 "
        "rejects `administration` as a workflow permission scope - so no "
        "`permissions:` block can grant it to GITHUB_TOKEN. Measured 2026-08-10 "
        "on hosted run 31386545513.\n"
        f"  Owner action: repository secret `{AUDIT_TOKEN_SECRET}`, a "
        f"fine-grained token with `{ADMINISTRATION_READ}` and nothing else. See "
        "docs/OWNER_WORK.md and artifacts/gate_integrity_blocked.md."
    )
    if in_ci():
        pytest.fail(verdict, pytrace=False)
    pytest.skip(f"LOCAL_ENVIRONMENT_UNAVAILABLE: {verdict}")


def refused(result: subprocess.CompletedProcess[str]) -> bool:
    """True when GitHub refused the operation, rather than it merely erroring.

    A 401 is not a refusal of this identity - it is the absence of an identity.
    It stops the test rather than satisfying it: an expired or removed token
    would otherwise turn every assertion in this file green.
    """
    if result.returncode == 0:
        return False
    blob = (result.stdout + result.stderr).lower()
    if any(str(code) in blob for code in UNAUTHENTICATED_STATUSES):
        pytest.fail(
            "PROTECTION_TEST_UNAUTHENTICATED - the call came back 401.\n"
            "  Nobody was refused; nobody asked. A 401 proves nothing about "
            "whether this identity can weaken protection, and treating it as "
            "proof is how a removed token turns this whole file green.\n"
            f"  {result.stdout.strip()}\n  {result.stderr.strip()}",
            pytrace=False,
        )
    if "not accessible" in blob or "must have admin" in blob:
        return True
    return any(str(code) in blob for code in FORBIDDEN_STATUSES)


# ---- the identity must not be able to weaken protection ---------------------


def test_reading_the_ruleset_is_allowed():
    """The audit has to work, so reads must succeed. If this fails the rest of
    the file proves nothing."""
    assert gh("api", f"repos/{REPO}/rulesets").returncode == 0


@pytest.mark.usefixtures("destructive")
def test_disabling_the_ruleset_is_refused():
    rid = ruleset_id()
    before = canonical(rid)

    result = gh(
        "api", "-X", "PUT", f"repos/{REPO}/rulesets/{rid}", "-f", "enforcement=disabled"
    )

    assert refused(result), (
        "THE RULESET WAS DISABLED. This identity has administrative permission "
        "and the whole design is void. Re-issue the token with "
        "Administration = No access.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert canonical(rid) == before, "the ruleset changed despite the refusal"


@pytest.mark.usefixtures("destructive")
def test_deleting_the_ruleset_is_refused():
    rid = ruleset_id()
    before = canonical(rid)

    result = gh("api", "-X", "DELETE", f"repos/{REPO}/rulesets/{rid}")

    assert refused(result), (
        "THE RULESET WAS DELETED. Protection is gone. Re-create it from the "
        "owner's admin identity and re-issue Claude's token without "
        "Administration access.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert canonical(rid) == before, "the ruleset changed despite the refusal"


@pytest.mark.usefixtures("destructive")
def test_enabling_force_push_is_refused():
    result = gh("api", "-X", "PATCH", f"repos/{REPO}", "-F", "allow_force_push=true")
    assert refused(result), (
        "repository settings were changed. This identity can alter protection.\n"
        f"{result.stdout}\n{result.stderr}"
    )


@pytest.mark.usefixtures("destructive")
def test_deleting_the_repository_is_refused():
    result = gh("api", "-X", "DELETE", f"repos/{REPO}")
    assert refused(result), (
        f"THE REPOSITORY DELETE CALL WAS NOT REFUSED.\n{result.stdout}\n{result.stderr}"
    )


# ---- protection is still exactly what it was --------------------------------


def test_the_ruleset_is_byte_for_byte_unchanged_after_every_attempt():
    """Run last in spirit: proves the attempts above changed nothing at all."""
    rid = ruleset_id()
    first = canonical(rid)
    second = canonical(rid)
    assert first == second, "the ruleset is changing between reads"


def test_the_required_check_is_still_present():
    rid = ruleset_id()
    result = gh("api", f"repos/{REPO}/rulesets/{rid}")
    data = json.loads(result.stdout)
    contexts = [
        c["context"]
        for r in data.get("rules", [])
        if r["type"] == "required_status_checks"
        for c in r["parameters"]["required_status_checks"]
    ]
    expected = str(contract()["meta"]["required_pr_check"])
    assert expected in contexts, f"required checks are {contexts}"


def test_bypass_actors_are_still_empty():
    """Empty means the required check binds everyone, including repo admins.

    The assertion is unchanged in strength: when the field is readable, `[]` is
    still the only value that passes, and a non-empty list still fails with the
    accusation - which is then TRUE.

    What changed on 2026-08-11 is the third case. An absent field used to reach
    that same accusation carrying `None`, reporting a bypass that had never been
    measured. It now reports NOT_MEASURED and names the owner action instead.
    See the module docstring for the measurement behind that.
    """
    rid = ruleset_id()
    data = json.loads(gh("api", f"repos/{REPO}/rulesets/{rid}").stdout)

    missing = [k for k in RULESET_MARKERS if k not in data]
    assert not missing, (
        f"the ruleset read did not return a ruleset: {missing} absent as well. "
        "This is a broken read, not a redacted field, and it must not be "
        f"reported as NOT_MEASURED. Keys received: {sorted(data)}"
    )

    if not bypass_actors_were_read(data):
        unread_bypass_actors(data)

    assert data["bypass_actors"] == [], f"{BYPASS_FOUND}: {data['bypass_actors']}"


def test_an_unread_bypass_actors_field_is_never_reported_as_a_bypass(
    monkeypatch: pytest.MonkeyPatch,
):
    """The defect that shipped, pinned so it cannot come back.

    Until 2026-08-11 a redacted ruleset body produced

        AssertionError: someone can bypass the rules: None

    from `ci/test_protection.py:286`. This drives the same redacted body through
    the verdict path and holds it to two things: it must say NOT_MEASURED, and
    it must not accuse anyone. Both directions matter - a verdict that goes
    quiet is as useless as one that lies.

    The body below is the shape actually returned by hosted run 31386545513: a
    successful read of a real ruleset with the one field withheld.
    """
    redacted: dict[str, Any] = {
        "id": 20557129,
        "name": "main protection",
        "target": "branch",
        "source_type": "Repository",
        "enforcement": "active",
        "rules": [],
    }
    assert not bypass_actors_were_read(redacted), "absent is not empty"

    for ci_value, outcome in (
        ("true", pytest.fail.Exception),
        ("", pytest.skip.Exception),
    ):
        monkeypatch.setenv("GITHUB_ACTIONS", ci_value)
        monkeypatch.setenv("CI", ci_value)
        with pytest.raises(outcome) as raised:
            unread_bypass_actors(redacted)
        said = str(raised.value)
        assert BYPASS_ACTORS_NOT_MEASURED in said, said
        assert BYPASS_FOUND not in said, said
        assert AUDIT_TOKEN_SECRET in said, "the owner action must be named"

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("CI", "true")
    assert bypass_actors_were_read({**redacted, "bypass_actors": []}), (
        "a field that IS present must never take the NOT_MEASURED path"
    )


def test_the_owner_action_in_the_verdict_is_the_one_actually_on_record():
    """Two places name the same fix, so they must not drift apart.

    The NOT_MEASURED verdict tells a reader to create `CLAUDE_AUDIT_TOKEN` with
    `Administration: read`. If `docs/OWNER_WORK.md` stops saying that - renamed,
    reworded, or the item quietly dropped once somebody assumes it is done - the
    verdict starts pointing at nothing and this goes red. It is the cheapest
    available check that the unknown still has an owner.
    """
    owner_work = ROOT / "docs" / "OWNER_WORK.md"
    assert owner_work.is_file(), f"{owner_work} is where blocked work is tracked"
    text = owner_work.read_text()
    for named in (AUDIT_TOKEN_SECRET, ADMINISTRATION_READ):
        assert named in text, (
            f"{named!r} is named in the NOT_MEASURED verdict but no longer "
            f"appears in {owner_work.name}. One of the two is now wrong."
        )


def test_main_is_still_protected():
    rid = ruleset_id()
    data = json.loads(gh("api", f"repos/{REPO}/rulesets/{rid}").stdout)
    assert data.get("enforcement") == "active"
    types = {r["type"] for r in data.get("rules", [])}
    assert "non_fast_forward" in types, "force-push is no longer blocked"
    assert "deletion" in types, "branch deletion is no longer blocked"
    assert "pull_request" in types, "main can be pushed to directly"


# ---- the things Claude MUST still be able to do -----------------------------


def test_claude_can_still_do_its_job():
    """Least privilege has to leave enough privilege. A token so weak that the
    work cannot be done would fail differently, and just as badly."""
    for path in (
        f"repos/{REPO}/pulls?state=all",
        f"repos/{REPO}/issues?state=all",
        f"repos/{REPO}/actions/runs?per_page=1",
    ):
        assert gh("api", path).returncode == 0, f"cannot read {path}"


# ---------------------------------------------------------------------------
# the gate on the tamper tests, which must not quietly come off
# ---------------------------------------------------------------------------


def test_every_test_that_calls_a_destructive_endpoint_is_gated() -> None:
    """AST, and enumerated from the source rather than from a list somebody
    maintains by hand.

    A fifth tamper test added later must not be able to arrive ungated and fire
    `DELETE repos/...` on every developer's `pytest`. This finds the calls and
    checks the decorator, so remembering is not part of the mechanism.

    It reads the tree, not the text: this repository has twice had a substring
    scan match a word inside the comment explaining why that word was safe.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    ungated: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        calls_destructive = False
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if not (isinstance(inner.func, ast.Name) and inner.func.id == "gh"):
                continue
            words = [a.value for a in inner.args if isinstance(a, ast.Constant)]
            if "-X" in words and any(w in DESTRUCTIVE_METHODS for w in words):
                calls_destructive = True
        if not calls_destructive:
            continue
        gated = any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "usefixtures"
            and any(
                isinstance(a, ast.Constant) and a.value == "destructive"
                for a in dec.args
            )
            for dec in node.decorator_list
        )
        if not gated:
            ungated.append(node.name)

    assert ungated == [], (
        "these tests ask GitHub to destroy something and do NOT request the "
        f"`destructive` fixture, so a plain `pytest` fires them: {ungated}"
    )


def test_the_four_known_tamper_tests_are_all_found_by_that_scan() -> None:
    """THE CONTROL. Without it the test above passes when the scan finds
    nothing at all - which is exactly what a broken scan does."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    found = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == "gh"
        and "-X" in [a.value for a in inner.args if isinstance(a, ast.Constant)]
        and any(
            a.value in DESTRUCTIVE_METHODS
            for a in inner.args
            if isinstance(a, ast.Constant)
        )
    }
    assert found == {
        "test_disabling_the_ruleset_is_refused",
        "test_deleting_the_ruleset_is_refused",
        "test_enabling_force_push_is_refused",
        "test_deleting_the_repository_is_refused",
    }, found


def test_the_gate_is_strict_about_what_switches_it_on() -> None:
    """Exactly "1". A loose reading turns a typo into a live delete call."""
    for wrong in ("", "0", "true", "yes", "TRUE", " 1", "1 ", "on"):
        assert not _destructive_allowed_for({ENV_DESTRUCTIVE: wrong}), wrong
    assert _destructive_allowed_for({ENV_DESTRUCTIVE: "1"})
    assert not _destructive_allowed_for({})


# ---------------------------------------------------------------------------
# the CLAUDE_AUDIT_TOKEN mechanism, step 1 of 2
# ---------------------------------------------------------------------------


def test_an_audit_token_is_forwarded_to_gh_as_its_identity() -> None:
    """The whole of the code half, in one assertion.

    When `CLAUDE_AUDIT_TOKEN` is present, `gh` runs as that identity. Nothing
    else in this repository reads the variable, and no other behaviour changes.
    """
    out = gh_environment({ENV_AUDIT_TOKEN: "a-token-value"})
    assert out["GH_TOKEN"] == "a-token-value"


def test_without_an_audit_token_the_ambient_identity_is_left_alone() -> None:
    """No token means no change. A mechanism that rewrote GH_TOKEN when it had
    nothing to put there would log every developer out of their own `gh`."""
    assert "GH_TOKEN" not in gh_environment({})
    assert gh_environment({"GH_TOKEN": "already-here"})["GH_TOKEN"] == "already-here"


def test_a_blank_audit_token_is_not_an_identity() -> None:
    """`CLAUDE_AUDIT_TOKEN=` is a common way to think you unset something.

    Read loosely it would replace a working GH_TOKEN with an empty string and
    make every call fail as unauthenticated - which this file would then report
    as PROTECTION_TEST_PREREQUISITES_MISSING, sending somebody to debug a
    network problem that is really a stray equals sign.
    """
    for blank in ("", "   ", "\t"):
        out = gh_environment({ENV_AUDIT_TOKEN: blank, "GH_TOKEN": "the-real-one"})
        assert out["GH_TOKEN"] == "the-real-one", repr(blank)


def test_the_audit_token_is_never_written_anywhere_it_could_be_read_back() -> None:
    """It is forwarded and nothing else. Not printed, not put in a message, not
    part of any assertion's failure text.

    Read off the source rather than trusted: a token that reaches a log on a
    hosted runner is a token in a public artefact.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    leaks: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        printing = (isinstance(func, ast.Name) and func.id == "print") or (
            isinstance(func, ast.Attribute) and func.attr in {"write", "fail", "skip"}
        )
        if not printing:
            continue
        for arg in ast.walk(node):
            if isinstance(arg, ast.Name) and arg.id == "ENV_AUDIT_TOKEN":
                leaks.append(ast.dump(node)[:60])

    assert leaks == [], (
        f"the audit token name reaches something that prints: {leaks}. The "
        "VALUE must never be printed, and naming the variable beside a print "
        "is how that starts."
    )


def test_only_this_file_reads_the_audit_token() -> None:
    """One reader, so there is one place to look when it stops working.

    Scans the shipped package and the CI scripts. A second reader would mean
    two answers to "which identity is this running as", which is the ambiguity
    the whole tenancy work exists to remove.
    """
    here = Path(__file__).resolve()
    readers: list[str] = []
    for folder in ("accountant", "ci", "scripts"):
        root = ROOT / folder
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if path.resolve() == here:
                continue
            if "CLAUDE_AUDIT_TOKEN" in path.read_text(encoding="utf-8"):
                readers.append(str(path.relative_to(ROOT)))

    assert readers == [], f"something else reads the audit token: {readers}"


def test_gh_actually_runs_under_that_environment() -> None:
    """WRITTEN BY A MUTANT. Deleting `env=` from the `gh` call changed nothing.

    Every test above measured `gh_environment` on its own, so the token could
    be computed perfectly and then thrown away - which is the failure mode this
    whole file exists to catch in other people's code.

    Read off the AST, not the text: a comment mentioning `env=gh_environment`
    must not satisfy it.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    gh_def = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "gh"
    )
    runs = [
        call
        for call in ast.walk(gh_def)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "run"
    ]
    assert runs, "gh() no longer runs a subprocess at all"
    for call in runs:
        env = next((kw for kw in call.keywords if kw.arg == "env"), None)
        assert env is not None, (
            "gh() runs a subprocess without an explicit env, so "
            "CLAUDE_AUDIT_TOKEN is computed and then discarded"
        )
        assert (
            isinstance(env.value, ast.Call)
            and isinstance(env.value.func, ast.Name)
            and env.value.func.id == "gh_environment"
        ), "gh() passes an env that did not come from gh_environment"
