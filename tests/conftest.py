"""Shared fixtures, so two test files can drive one server without two copies.

Also the one place the suite's authentication mode is set. See
`local_dev_by_default` at the bottom.

`tests/test_web.py` owns the demo company, the threaded HTTP server and the
request helpers. `tests/test_bulk_reversal_web.py` needs the same three. Copying
a threaded fixture is how two spin-up paths drift apart, so it is re-exported
here instead: pytest finds fixtures declared in `conftest.py` for every test in
the directory, without either file importing a fixture by name from the other.

Re-export, not re-implementation. The definitions stay in `test_web.py` beside
the tests that established them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from accountant.auth import ENV_LOCAL_DEV_MODE
from tests.test_web import server

__all__ = ["server"]


@pytest.fixture(scope="session", autouse=True)
def local_dev_by_default() -> Iterator[None]:
    """Every test runs in LOCAL_DEV_MODE unless it says otherwise.

    WHY THIS EXISTS, AND WHAT IT COSTS
    ----------------------------------
    Authentication landed after roughly sixty tests that drive the app over
    HTTP with no credential. Left alone, every one of them would now assert
    that a 401 is a 401 — sixty copies of one measurement, and nothing left
    measuring what those tests were actually written for.

    So the suite's default is the mode a developer runs in, and it is set HERE
    rather than in each of the fourteen files that start a server, because a
    setting repeated fourteen times is a setting thirteen of them will
    eventually disagree about.

    WHAT IT DOES NOT COVER, AND WHERE THAT IS PAID FOR
    --------------------------------------------------
    The credential itself. `tests/test_auth.py` deletes this variable and
    asserts the production path in full: every route refusing an
    unauthenticated caller, an unknown token, a revoked one, an expired one,
    and a cross-tenant refusal. The shipped auth code still runs on every
    request under this fixture — `_identify` is called, a principal is built,
    and the audit rows carry it — so what is skipped is the credential, not the
    check.

    SESSION SCOPED, and that is not an optimisation. `tests/test_ui_provenance.py`
    starts its servers from a MODULE-scoped fixture, which pytest builds before
    any function-scoped fixture exists — a function-scoped default would simply
    not be there yet when that server came up, and those tests would 401. A
    per-test override still works on top of it: `monkeypatch` in
    `tests/test_auth.py` restores whatever was here when the test ends.
    """
    previous = os.environ.get(ENV_LOCAL_DEV_MODE)
    os.environ[ENV_LOCAL_DEV_MODE] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ENV_LOCAL_DEV_MODE, None)
        else:
            os.environ[ENV_LOCAL_DEV_MODE] = previous
