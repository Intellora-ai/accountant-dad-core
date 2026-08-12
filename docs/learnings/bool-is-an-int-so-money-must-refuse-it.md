# `bool` is an `int` in Python, so a money check must refuse it explicitly

- `isinstance(True, int)` is `True`, and `True == 1`. A flag passed where a paise
  amount belonged will therefore **balance a one-paisa entry** and pass every
  arithmetic check.
- `isinstance(x, int) and not isinstance(x, bool)` works but reads badly and
  pyright flags the first half as redundant against an `int` annotation.
- **`type(x) is not int`** does both jobs in one expression: it rejects `bool`
  (whose type is `bool`, not `int`) and every non-int, and pyright does not
  complain.
- The check is not redundant at run time even with an `int` annotation.
  Annotations are not enforced, and a CSV row or a tool-call arrives untyped —
  `accountant/tallyio/vouchers.py:394` records the same hazard.

**Applies:** every boundary where money enters typed code.

**Does not apply:** internal arithmetic after the boundary has already checked.
