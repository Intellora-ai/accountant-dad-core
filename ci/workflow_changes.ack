# Acknowledged CHANGE-class findings from ci/check_workflow_integrity.py.
#
# READ THIS BEFORE TRUSTING IT.
#
# This file lives in the repository, so a pull request can add its own lines to
# it. It is POLICY, not mechanism. What it buys is that a change to `.github/**`
# has to be written down, once, in one greppable place, instead of hiding in a
# 200-line YAML diff. It does not stop anyone.
#
# The mechanism that would stop someone is a GitHub-side rule the branch cannot
# edit. Neither exists on this repository today:
#
#   * a file_path_restriction ruleset rule covering .github/** and ci/**
#   * CODEOWNERS with require_code_owner_review = true and
#     required_approving_review_count >= 1
#
# Both are owner actions. See artifacts/gate_integrity_audit.md.
#
# REMOVAL and WEAKENING findings can never be acknowledged here. Only CHANGE:
#   ACTION_SHA_CHANGED      STEP_RUN_CHANGED   STEP_ADDED
#   JOB_ADDED               WORKFLOW_FILE_ADDED
#   WORKFLOW_HEADER_CHANGED
#
# One fingerprint per line, exactly as the checker prints it.
#
# ---------------------------------------------------------------------------
# Nothing is acknowledged yet, because nothing under .github/** has changed.
#
# The workflow diff recommended in artifacts/gate_integrity_audit.md is stated
# there and deliberately NOT applied - editing .github/** needs the owner's yes
# for that specific change (standing rule 6).
#
# These ten fingerprints are what Diff A + Diff B produce, measured by running
# the checker against the patched tree on 2026-08-10. Uncommenting them is the
# second half of applying the patch; the first half is the YAML.
#
# Diff A - the repairs
# STEP_ADDED:.github/workflows/pr-fast.yml:pr-fast:lockfile
# STEP_ADDED:.github/workflows/pr-fast.yml:pr-fast:gate-integrity
# STEP_RUN_CHANGED:.github/workflows/pr-fast.yml:pr-fast:evidence
# STEP_ADDED:.github/workflows/full.yml:workflow-checks:gate-integrity
# STEP_RUN_CHANGED:.github/workflows/full.yml:workflow-checks:checkout
# STEP_RUN_CHANGED:.github/workflows/full.yml:workflow-checks:evidence
# STEP_ADDED:.github/workflows/watchdog.yml:ruleset-drift:sync dependencies from the lockfile
# STEP_ADDED:.github/workflows/watchdog.yml:ruleset-drift:live-protection-test
#
# Diff B - the token that keeps the protection test from failing the PR path
# WORKFLOW_HEADER_CHANGED:.github/workflows/pr-fast.yml:header
# WORKFLOW_HEADER_CHANGED:.github/workflows/full.yml:header
# ---------------------------------------------------------------------------
