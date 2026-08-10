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
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

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


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


REPO = str(contract()["meta"]["repository"])


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
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


def test_enabling_force_push_is_refused():
    result = gh("api", "-X", "PATCH", f"repos/{REPO}", "-F", "allow_force_push=true")
    assert refused(result), (
        "repository settings were changed. This identity can alter protection.\n"
        f"{result.stdout}\n{result.stderr}"
    )


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
    """Empty means the required check binds everyone, including repo admins."""
    rid = ruleset_id()
    data = json.loads(gh("api", f"repos/{REPO}/rulesets/{rid}").stdout)
    assert data.get("bypass_actors") == [], (
        f"someone can bypass the rules: {data.get('bypass_actors')}"
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
