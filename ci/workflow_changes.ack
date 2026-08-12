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
# `GH_TOKEN: ${{ github.token }}` under the workflow-level `env:`, in
# pr-fast.yml and full.yml. TWO added lines across two files, no deletion, no
# step, no job, no gate, no threshold.
#
# CORRECTED 2026-08-11. This paragraph said "`administration: read` under
# `permissions:`, and `GH_TOKEN` ... Four added lines". That was wrong twice
# over: the diff is two lines, not four, and `administration: read` is not in
# it and cannot be - actionlint v1.7.12 refuses it, because `administration` is
# a fine-grained PAT scope and not a workflow permission scope
# (artifacts/gate_integrity_blocked.md:38-47). This file exists to be the one
# place a reader can find what was authorised, so a wrong description here is
# worse than no file.
#
# THE FINGERPRINTS BELOW NOW CARRY A CONTENT HASH, owner directive 2026-08-11.
# They used to be `CODE:location`, which acknowledged a PLACE - so these two
# lines permanently pre-authorised every future rewrite of the triggers,
# permissions, concurrency and env of the two most sensitive workflows here.
# Measured: `contents: read` swapped for `write-all` in pr-fast.yml, nothing
# else touched, no ack added, and the checker returned PASS by consuming one of
# these lines. With the hash in, an ack dies the moment its content changes.
# Run `python ci/check_workflow_integrity.py` and copy the line it prints.
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
WORKFLOW_HEADER_CHANGED:.github/workflows/pr-fast.yml:header:61a6892d56a3
WORKFLOW_HEADER_CHANGED:.github/workflows/full.yml:header:1e28c333e269
# ---------------------------------------------------------------------------
STEP_RUN_CHANGED:.github/workflows/pr-fast.yml:pr-fast:sync dependencies from the lockfile:779447263fe4
WORKFLOW_HEADER_CHANGED:.github/workflows/pr-fast.yml:header:bae3af9003be
STEP_ADDED:.github/workflows/watchdog.yml:ruleset-drift:measure bypass_actors:759881b8b312
