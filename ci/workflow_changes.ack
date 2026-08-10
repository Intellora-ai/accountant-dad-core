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
# DIFF B IS APPLIED. DIFF A IS NOT.
#
# The owner authorised Diff B and only Diff B, in writing, on 2026-08-10:
# `administration: read` under `permissions:`, and `GH_TOKEN: ${{ github.token }}`
# under the workflow-level `env:`, in pr-fast.yml and full.yml. Four added
# lines across two files, no deletion, no step, no job, no gate, no threshold.
# The two fingerprints below are what that produces, and they are the only
# lines in this file that are live.
#
# WHY IT WAS NEEDED. ci/test_protection.py's live protection tests call
# `gh api`. No job set GH_TOKEN, so `gh` was unauthenticated on every hosted
# run and all of them skipped - green squares that measured nothing.
#
# Diff A - the repairs. STILL NOT APPLIED, STILL NOT AUTHORISED.
# Measured against a patched tree on 2026-08-10 and kept here so applying it
# later is uncommenting rather than re-deriving. Uncommenting any line below
# without the owner's yes for that specific change is a standing-rule 6
# violation, and the line is not evidence that the step exists.
# STEP_ADDED:.github/workflows/pr-fast.yml:pr-fast:lockfile
# STEP_ADDED:.github/workflows/pr-fast.yml:pr-fast:gate-integrity
# STEP_RUN_CHANGED:.github/workflows/pr-fast.yml:pr-fast:evidence
# STEP_ADDED:.github/workflows/full.yml:workflow-checks:gate-integrity
# STEP_RUN_CHANGED:.github/workflows/full.yml:workflow-checks:checkout
# STEP_RUN_CHANGED:.github/workflows/full.yml:workflow-checks:evidence
# STEP_ADDED:.github/workflows/watchdog.yml:ruleset-drift:sync dependencies from the lockfile
# STEP_ADDED:.github/workflows/watchdog.yml:ruleset-drift:live-protection-test
#
# Diff B - the token that lets the protection test actually run. APPLIED.
WORKFLOW_HEADER_CHANGED:.github/workflows/pr-fast.yml:header
WORKFLOW_HEADER_CHANGED:.github/workflows/full.yml:header
# ---------------------------------------------------------------------------
