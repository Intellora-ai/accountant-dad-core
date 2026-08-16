# `# noqa` at the start of an explanatory comment becomes a directive

- Writing `# noqa on the next line: S105 reads PASS as a credential…` as prose
  makes ruff parse that line as a blanket `noqa` directive.
- It then reports `RUF100 Unused blanket noqa directive` — pointing at the
  *comment*, not at the real suppression on the line below.
- Confusing because the actual `# noqa: S105` was correct and in the right place.

**Fix:** never begin a comment with the token `noqa`. Reword: *"S105 reads this as
a hardcoded credential. It is a verdict."*

**Applies:** any linter with inline suppression comments — ruff, flake8, mypy.

**Does not apply:** the same words mid-sentence; only the leading token is parsed.
