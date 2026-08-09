"""Shared fixtures, so two test files can drive one server without two copies.

`tests/test_web.py` owns the demo company, the threaded HTTP server and the
request helpers. `tests/test_bulk_reversal_web.py` needs the same three. Copying
a threaded fixture is how two spin-up paths drift apart, so it is re-exported
here instead: pytest finds fixtures declared in `conftest.py` for every test in
the directory, without either file importing a fixture by name from the other.

Re-export, not re-implementation. The definitions stay in `test_web.py` beside
the tests that established them.
"""

from __future__ import annotations

from tests.test_web import server

__all__ = ["server"]
