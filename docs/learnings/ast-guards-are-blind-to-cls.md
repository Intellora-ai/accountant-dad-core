# An AST guard that looks for `ClassName(` cannot see `cls(...)`

- A structural guard scanning for calls to `LedgerEntry(` finds nothing when the
  class builds itself through `@classmethod ... return cls(...)`.
- The guard still **passes** — it is asserting that a set is a subset of
  `{allowed_file}`, and the empty set satisfies that. It would keep passing after
  the thing it guards was deleted.
- Caught here by a **control test** whose only job was to prove the scanner could
  find *any* construction. It could not.
- Two fixes, both needed: construct by name inside the class so the scan has a
  subject, and add a second assertion that the found set is **exactly**
  `{expected_file}` rather than merely a subset of it.

**Applies:** every AST guard asserting "only X does Y". Always pair it with a test
proving the scanner is not blind.

**Does not apply:** runtime guards — they see `cls` fine.
