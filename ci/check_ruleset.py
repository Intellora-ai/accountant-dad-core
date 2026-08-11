"""Branch-protection drift audit. Read-only, always.

Compares the live GitHub ruleset against what ci/gates.toml says it should be.
If protection has been weakened, this opens an issue.

IT NEVER REPAIRS THE RULESET. An auditor that can fix what it audits is not an
auditor - it is a second way for protection to be changed quietly. Repair
requires the owner's separate admin identity, which is deliberately outside
Claude's environment.

The identity running this cannot edit rulesets at all. Verified 2026-08-08:
attempting to disable or delete the ruleset returns HTTP 403, "Resource not
accessible by personal access token".

    python ci/check_ruleset.py            report drift, exit 1 if any
    python ci/check_ruleset.py --json     machine-readable, for the workflow
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"


@dataclass
class Drift:
    """Everything that no longer matches what the contract requires."""

    problems: list[str] = field(default_factory=list[str])
    checked: list[str] = field(default_factory=list[str])
    observed: list[str] = field(default_factory=list[str])
    unmeasured: list[str] = field(default_factory=list[str])

    def require(self, ok: bool, description: str, detail: str) -> None:
        self.checked.append(description)
        if not ok:
            self.problems.append(f"{description} — {detail}")

    def cannot_measure(self, description: str, detail: str) -> None:
        """A thing this identity was not permitted to look at.

        The third state, added 2026-08-11. It is neither `require` nor
        `observe`, and collapsing it into either was a live defect:

          * `require` would report a violation nobody measured - the same false
            statement `ci/test_protection.py` used to make, see that file's
            docstring.
          * `observe` reads a value successfully and prints it without judging.
            That is for settings the owner has not chosen. This field WAS
            chosen; we were simply not shown it.

        NEVER added to `checked`, so it can never be printed with a tick, and
        it makes the audit not-clean so the run cannot end in PASS. This
        repository's standing rule is that an unmeasured value is NOT_MEASURED
        and never a zero.
        """
        self.unmeasured.append(f"{description} — {detail}")

    def observe(self, description: str, value: object) -> None:
        """Print a live value without judging it.

        For settings the owner has not chosen yet. Asserting a floor here would
        mean inventing a number the owner never gave, and a floor of zero
        defends nothing. Printing it means a silent revert is at least VISIBLE
        in the step summary, which is strictly better than nothing and is not
        dressed up as enforcement.
        """
        self.observed.append(f"{description}: {value!r}")

    @property
    def clean(self) -> bool:
        """Clean means every check was RUN and every one of them passed.

        A check that could not run does not count as passed. Until 2026-08-11
        `unmeasured` did not exist and an unreadable `bypass_actors` printed
        `✓ no bypass actors` and exited 0.
        """
        return not self.problems and not self.unmeasured


def gh_api(path: str) -> Any:
    """Read from the GitHub API. Read-only by construction: no method is passed,
    so this can only ever GET."""
    result = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def check_bypass_actors(drift: Drift, body: dict[str, Any], description: str) -> None:
    """Judge `bypass_actors` for one ruleset. The only place it is judged.

    ABSENT IS NOT EMPTY, and until 2026-08-11 both call sites wrote

        list(body.get("bypass_actors") or [])

    which turned an absent field, a null field and an empty list into the same
    `[]`. `not []` is True, so a field this identity was never shown printed
    `✓ no bypass actors` and the audit exited 0. It reported as measured a thing
    it could not read - and it did so in the OPPOSITE direction to the same
    conflation in `ci/test_protection.py`, which failed closed. One response
    body, two audits, two contradictory verdicts.

    That absent case is not hypothetical. `.github/workflows/watchdog.yml` runs
    this file under `GH_TOKEN: ${{ github.token }}`, and that identity receives a
    ruleset body with `bypass_actors` withheld - measured 2026-08-10 on hosted
    run 31386545513, and recorded in `artifacts/gate_integrity_blocked.md`.
    Reading it needs repository `Administration: read`, a fine-grained
    personal-access-token permission that no workflow `permissions:` block can
    grant. `docs/OWNER_WORK.md` tracks the secret that would supply it.

    Three states, matching `ci/test_protection.py` exactly:

        absent or null   NOT_MEASURED - no tick, and the audit is not clean
        []               measured, nobody can bypass - PASS
        [...]            measured, somebody can - FAIL
    """
    actors = body.get("bypass_actors")
    if actors is None:
        drift.cannot_measure(
            description,
            "NOT_MEASURED: the ruleset read succeeded but `bypass_actors` was "
            f"not in the response (keys received: {sorted(body)}). This is NOT a "
            "finding that anyone can get past the required check, and it is not "
            "a finding that nobody can - the field was withheld. Reading it "
            "needs repository `Administration: read`; see docs/OWNER_WORK.md "
            "and artifacts/gate_integrity_blocked.md.",
        )
        return
    found: list[Any] = list(actors)
    drift.require(
        not found,
        description,
        f"{len(found)} actor(s) can bypass the rules: {found}",
    )


def audit(repo: str) -> Drift:
    meta = contract()["meta"]
    expected_check = str(meta["required_pr_check"])
    expected_mq_check = str(meta["required_mq_check"])
    expected_app_id = int(meta["required_check_app_id"])
    drift = Drift()

    rulesets = gh_api(f"repos/{repo}/rulesets")
    active = [r for r in rulesets if r.get("target") == "branch"]

    drift.require(
        bool(active),
        "a branch ruleset exists",
        "no branch ruleset found — main is unprotected",
    )
    if not active:
        return drift

    # EVERY branch ruleset, not just the first. `active[0]` used to be the whole
    # audit: a second ruleset could carry a bypass actor and this would never
    # look at it.
    for extra in active[1:]:
        extra_full = gh_api(f"repos/{repo}/rulesets/{extra['id']}")
        check_bypass_actors(
            drift,
            extra_full,
            f"ruleset {extra['id']} ({extra.get('name')!r}) has no bypass actors",
        )
    drift.observe("branch rulesets on this repository", [r["id"] for r in active])

    full = gh_api(f"repos/{repo}/rulesets/{active[0]['id']}")
    rules = {r["type"]: r.get("parameters", {}) for r in full.get("rules", [])}

    drift.require(
        full.get("enforcement") == "active",
        "ruleset is active",
        f"enforcement is {full.get('enforcement')!r}",
    )

    # A ruleset whose conditions match nothing has every rule intact and
    # protects nothing. Every check below would still pass.
    ref_name: dict[str, Any] = dict(full.get("conditions", {}).get("ref_name", {}))
    include: list[str] = list(ref_name.get("include") or [])
    exclude: list[str] = list(ref_name.get("exclude") or [])
    drift.require(
        "~DEFAULT_BRANCH" in include or "~ALL" in include,
        "the ruleset actually applies to the default branch",
        f"conditions.ref_name.include is {include}, which may not cover main. "
        "Every rule below can be intact while the ruleset matches no branch.",
    )
    drift.require(
        not exclude,
        "no branch is excluded from the ruleset",
        f"conditions.ref_name.exclude is {exclude}",
    )

    check_bypass_actors(drift, full, "no bypass actors")

    status = rules.get("required_status_checks")
    drift.require(
        status is not None,
        "status checks are required",
        "the required_status_checks rule is gone",
    )

    if status is not None:
        entries: list[dict[str, Any]] = list(status.get("required_status_checks", []))
        contexts = [c.get("context") for c in entries]
        for wanted in (expected_check, expected_mq_check):
            drift.require(
                wanted in contexts,
                f"{wanted} is required",
                f"required checks are {contexts}, missing {wanted!r}",
            )

        # WHO is allowed to report the check, not just that the name is listed.
        #
        # A required context is satisfied by whoever writes a status with that
        # string. Any GitHub App installed on this repository with
        # `commit statuses: write` can write `pr-fast: success` and satisfy the
        # requirement without a single gate having run. Pinning integration_id
        # means only that app's reports count.
        #
        # The owner pinned both contexts to app 15368 on 2026-08-10T06:51:46Z.
        # This repository has more than one app that reports checks - measured
        # the same day, `github-actions` is 15368 and `claude` is 1236702 - so
        # the pin is load-bearing, not decorative.
        #
        # Until 2026-08-10 this audit asserted only the NAME. An unpinned
        # context would have reported a clean 9/9.
        for entry in entries:
            name = entry.get("context")
            if name not in (expected_check, expected_mq_check):
                continue
            got = entry.get("integration_id")
            drift.require(
                got is not None,
                f"{name} is pinned to a single app",
                "integration_id is absent, so ANY app or user with "
                "`commit statuses: write` can report this check as successful "
                "and satisfy the requirement without running a gate",
            )
            drift.require(
                got is None or int(got) == expected_app_id,
                f"{name} is pinned to app {expected_app_id}",
                f"it is pinned to app {got}, not to the app the contract names",
            )

        drift.require(
            bool(status.get("strict_required_status_checks_policy")),
            "branch must be up to date with main",
            "the strict policy is off, so a stale branch could merge",
        )

    drift.require(
        "non_fast_forward" in rules,
        "force-push is blocked",
        "the non_fast_forward rule is gone",
    )
    drift.require(
        "deletion" in rules,
        "branch deletion is blocked",
        "the deletion rule is gone",
    )
    drift.require(
        "pull_request" in rules,
        "direct pushes to main are blocked",
        "the pull_request rule is gone, so main can be pushed to directly",
    )

    # OBSERVED, NOT ASSERTED - and this is the honest gap in this file.
    #
    # The owner has not set a review requirement: as of 2026-08-10 the live
    # values are required_approving_review_count = 0 and
    # require_code_owner_review = false. Asserting a floor would mean writing a
    # number the owner never gave, and a floor of zero defends nothing.
    #
    # So they are printed instead. The consequence, stated plainly: if the owner
    # raises the review count tomorrow and it is silently reverted the day
    # after, THIS AUDIT WILL STILL REPORT CLEAN. The moment a number is set,
    # move these two lines from observe() to require() against the owner's
    # value. See artifacts/gate_integrity_audit.md, R2.
    pull_request = rules.get("pull_request")
    if pull_request is not None:
        for key in (
            "required_approving_review_count",
            "require_code_owner_review",
            "require_last_push_approval",
            "dismiss_stale_reviews_on_push",
        ):
            drift.observe(f"pull_request.{key}", pull_request.get(key))
        drift.observe(
            "pull_request.allowed_merge_methods",
            pull_request.get("allowed_merge_methods"),
        )

    return drift


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo", default=None, help="owner/name, default from the contract"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    repo = args.repo or str(contract()["meta"]["repository"])

    try:
        drift = audit(repo)
    except RuntimeError as exc:
        payload = {"repository": repo, "error": str(exc), "clean": False}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"FAIL — could not read the ruleset: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "repository": repo,
                    "clean": drift.clean,
                    "checked": drift.checked,
                    "observed": drift.observed,
                    "unmeasured": drift.unmeasured,
                    "problems": drift.problems,
                },
                indent=2,
            )
        )
        return 0 if drift.clean else 1

    print(f"branch protection on {repo}\n")
    for description in drift.checked:
        broken = any(p.startswith(description) for p in drift.problems)
        print(f"  {'✗' if broken else '✓'} {description}")

    if drift.observed:
        print("\n  observed, NOT asserted — a silent revert here is invisible:")
        for line in drift.observed:
            print(f"    · {line}")

    # NOT_MEASURED items never appear in `checked`, so they cannot be printed
    # with a tick. They get their own marker and their own section, because a
    # reader scanning this output must not be able to mistake "we were refused
    # the field" for either "we checked and it was fine" or "we found a bypass".
    if drift.unmeasured:
        print("\n  NOT_MEASURED — could not be read, so NOT reported as clean:")
        for line in drift.unmeasured:
            print(f"    ? {line}")

    if drift.clean:
        print("\nPASS — protection matches the contract")
        return 0

    counts = [f"{len(drift.problems)} drift(s)"]
    if drift.unmeasured:
        counts.append(f"{len(drift.unmeasured)} NOT_MEASURED")
    print(f"\nFAIL — {', '.join(counts)}:\n")
    for problem in drift.problems:
        print(f"  {problem}")
    print(
        "\nThis audit does NOT repair anything. Protection is changed only by the "
        "owner, using their own admin identity:\n"
        f"  https://github.com/{repo}/settings/rules"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
