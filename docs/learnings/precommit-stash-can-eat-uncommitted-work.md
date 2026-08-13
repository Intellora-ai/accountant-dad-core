# A pre-commit hook can delete uncommitted work, silently

- `prek` (and `pre-commit`) stash **unstaged** changes to
  `~/.cache/prek/patches/<timestamp>-<pid>.patch` before running hooks, then
  restore them afterwards. You see it as
  `Unstaged changes detected. Temporarily saving them to …`.
- If the hook run **times out or is killed**, the restore never happens. The
  patch file survives; the working copy does not.
- **Measured 2026-08-13:** a whole-tree typecheck hook hit a 2-minute timeout in
  a repo several agents were writing to. Three tests vanished from
  `tests/test_decision.py` — each one written *after* a mutation run to close a
  branch nothing else reached. Nothing failed. Nothing warned. The suite went
  green because the tests that would have caught the gap were the ones deleted.
- Recovery is real and cheap: the patch is intact, so
  `git apply --3way <patch>`, or read the `+` lines out of it and re-verify each
  hunk against the current file rather than pasting on trust.

**Applies:** any repo with a slow whole-tree hook, and especially any tree with
more than one writer. The risk scales with hook runtime, not with repo size.

**Does not apply:** staged changes — those are in the index and are not stashed.
Committing more often shrinks the window to nothing.

**The tell:** a green suite with fewer tests than the last run. Watch the count,
not just the colour.
