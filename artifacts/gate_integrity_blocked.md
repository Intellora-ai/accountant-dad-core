# BLOCKED — `test_bypass_actors_are_still_empty` cannot run on the pull-request path

Measured 2026-08-10 on hosted run 31386545513, commit `f6494ad`, job `pr-fast`.

## What happened

The owner-authorised `GH_TOKEN: ${{ github.token }}` worked. The live protection
tests **executed in CI for the first time** instead of skipping:

    9 of 10 passed
    1 failed

The one failure, verbatim:

    test_bypass_actors_are_still_empty
      data.get("bypass_actors")  ->  None
      AssertionError: someone can bypass the rules: None
      assert None == []
      ci/test_protection.py:286

## Reason

The ruleset read itself SUCCEEDS. The response body is real:

    {'id': 20557129, 'name': 'main protection', 'target': 'branch',
     'source_type': 'Repository', ...}

`bypass_actors` is simply **absent from the body** that the `GITHUB_TOKEN`
identity receives. Not a 401, not a 403 — a redacted view. The same request with
the owner's fine-grained token returns `bypass_actors: []`, which is why all ten
tests pass locally.

## The permission that would fix it, and why no workflow can grant it

Minimum required: repository **Administration: read**.

That scope exists **only as a fine-grained personal-access-token permission**. It
is not a GitHub Actions workflow-token scope. actionlint v1.7.12 settles it:

    unknown permission scope "administration". all available permission scopes
    are actions, artifact-metadata, attestations, checks, contents, deployments,
    discussions, id-token, issues, models, packages, pages, pull-requests,
    repository-projects, security-events, statuses

So there is no `permissions:` block, at any level, that makes this assertion
runnable from a pull request. `artifacts/gate_integrity_audit.md` recommended
`administration: read` and was wrong; it was never checked against actionlint.

## What was NOT done, deliberately

    not skipped        the test still runs and still fails
    not deleted        the assertion is unchanged
    not weakened       `None` is not accepted as `[]`
    not suppressed     no pytestmark, no xfail, no nosec
    no widened permission without a separate authorisation

The failure message is itself imprecise and that is recorded rather than fixed
here: `someone can bypass the rules: None` states a violation when what actually
happened is that the field could not be read. Correcting that wording without an
owner decision would still leave the gate red, so it is noted, not changed.

## Owner

Repository owner. Two possible actions, and this is not a recommendation between
them:

1. Create repository secret `CLAUDE_AUDIT_TOKEN` holding a fine-grained token
   with **Administration: read** and nothing else, then authorise audit Diff A4 —
   which moves the live protection test to the scheduled `watchdog` workflow on
   the default branch, where a secret is not reachable from pull-request code.
   This is the shape the audit already proposes, and it is unapplied and
   unauthorised.

2. Decide that `bypass_actors` is verified by the drift audit on a schedule
   rather than on every pull request.

## Next required evidence

A hosted run in which `test_bypass_actors_are_still_empty` executes under an
identity that can read the field, and reports `bypass_actors == []`.

Until then:

    live protection tests executed   9 of 10
    live protection tests skipped    0
    live protection tests failed     1, for a named and measured reason
    PR #34                           red, open, not merged
