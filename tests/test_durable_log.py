"""The audit trail has to survive the process that wrote it.

THE DEFECT, MEASURED
--------------------
`accountant/web/app.py::configure` ended with

    owned = store if store is not None else MemoryStore(":memory:")

and `connect()` — the only path `serve()` takes — passed no store. So every
decision this product made about somebody's books went into a database that
existed exactly as long as the process did.

`action_log` is append-only, has no update path and no delete path, and carries
a mandatory `reason` on every row precisely because it is the record of what was
done to a real business's books. All of that was true, and all of it was written
to `:memory:`.

THE FIX IS NOT A DIFFERENT DEFAULT
----------------------------------
A default that silently loses data and a default that silently writes a file are
both `configure()` deciding something its caller should have said. So it says
neither: it REFUSES a missing store, `connect()` opens the durable one, and a
test that wants a throwaway passes `MemoryStore(IN_MEMORY)` where anybody
reading it can see that is what it asked for.

WHAT IS NOT PROVED HERE
-----------------------
FakeTally throughout. Nothing here says anything about a real TallyPrime.
"""

from __future__ import annotations

import datetime
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from accountant.memory.identity import normalise_company
from accountant.memory.store import IN_MEMORY, MemoryStore
from accountant.schema import ActionLog
from accountant.tallyio.factory import BackendIdentity
from accountant.tallyio.fake import FakeTally
from accountant.tallyio.real import TallyConfig
from accountant.web import app
from tests.test_web import demo_company, draft_id, fake_backend, serving

NOW = datetime.datetime(2026, 8, 10, 9, 0, tzinfo=datetime.UTC)


def post(base: str, path: str, **fields: str) -> str:
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(base + path, data=data, timeout=5) as answer:  # noqa: S310
        return answer.read().decode()


def type_an_entry_that_posts(base: str) -> None:
    """Through the surface, answering both questions, so the row is a real one."""
    asked = post(base, "/entry", text="paid Gupta Hardware 1500 for tools")
    draft = draft_id(asked)
    post(base, "/answer", draft=draft, value="Purchases", problem="which_account")
    done = post(base, "/answer", draft=draft, value="Cash", problem="funding_is_named")
    assert "posted" in done.lower(), done


# ---------------------------------------------------------------------------
# a missing store is a refusal, not a guess
# ---------------------------------------------------------------------------


def test_configure_refuses_rather_than_quietly_opening_a_throwaway() -> None:
    """The defect, stated as a test.

    A silent `:memory:` here is not a small mistake. It is the difference
    between a product that keeps an audit trail and one that appears to.
    """
    with pytest.raises(ValueError, match="needs a store"):
        app.configure(demo_company(), fake_backend())


def test_the_refusal_says_both_of_the_two_things_a_caller_can_do() -> None:
    """A refusal that does not say what to do instead is a dead end. Both
    answers are real: a throwaway for a test, `connect()` for a person."""
    with pytest.raises(ValueError) as refused:
        app.configure(demo_company(), fake_backend())
    assert IN_MEMORY in str(refused.value)
    assert "connect()" in str(refused.value)


def test_a_throwaway_store_still_works_when_it_is_asked_for() -> None:
    """The control. A refusal that refuses everything is not a check."""
    live = app.configure(demo_company(), fake_backend(), store=MemoryStore(IN_MEMORY))
    try:
        assert live.company == app.COMPANY
    finally:
        app.disconnect()


# ---------------------------------------------------------------------------
# where the durable database goes
# ---------------------------------------------------------------------------


def test_the_default_path_is_a_file_beside_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(app.ENV_DB, raising=False)
    assert app.default_store_path() == Path("data") / "app.db"


def test_a_deployment_can_move_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that cannot be moved is a path that ends up on a container's
    disposable filesystem, which is the original defect wearing a hat."""
    monkeypatch.setenv(app.ENV_DB, "/mnt/volume/accountant.db")
    assert app.default_store_path() == Path("/mnt/volume/accountant.db")


def test_a_blank_environment_variable_is_not_a_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ACCOUNTANT_DB=` is a common way to think you unset something. Read
    loosely it names the empty path, and SQLite would take that as a temporary
    database — the original defect, reintroduced by a typo."""
    monkeypatch.setenv(app.ENV_DB, "   ")
    assert app.default_store_path() == Path("data") / "app.db"


def test_the_directory_is_made_on_first_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing folder is the FIRST RUN, not a misconfiguration. Refusing to
    start because it does not exist yet would be a product that cannot be
    installed by running it."""
    target = tmp_path / "not" / "there" / "app.db"
    monkeypatch.setenv(app.ENV_DB, str(target))

    store = app.default_store()

    assert target.exists()
    assert "action_log" in store.table_names()


def test_the_resolved_database_is_printed_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every resolved value is printed with where it came from, because a value
    with no provenance is the same ambiguity as no value at all."""
    monkeypatch.setenv(app.ENV_DB, "/mnt/volume/accountant.db")
    _config, _company, _backups, provenance = app.config_from_environment()
    assert any(
        app.ENV_DB in line and "/mnt/volume/accountant.db" in line
        for line in provenance
    ), provenance


def test_connect_opens_the_durable_store_when_it_is_given_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shipped path, which is the only one that matters here.

    `serve()` calls `connect()` and passes no store. Written by a mutant: with
    `connect` handing `store=None` straight through, every test above still
    passed - they all drive `configure()`, and NOTHING exercised the one line
    that decides where a real installation's audit trail goes.

    `real_tally` is replaced rather than reached, because the subject is the
    wiring between `connect` and `configure` and a real TallyPrime would prove
    nothing extra about it. The backend double is honest about being one.
    """
    db = tmp_path / "elsewhere" / "app.db"
    monkeypatch.setenv(app.ENV_DB, str(db))
    client = demo_company()

    def instead(*_args: object, **_kwargs: object) -> tuple[FakeTally, BackendIdentity]:
        return client, fake_backend()

    monkeypatch.setattr(app, "real_tally", instead)

    try:
        live = app.connect(TallyConfig(), app.COMPANY)
        assert live.store is not None
    finally:
        app.disconnect()

    assert db.exists(), "connect() opened a database that was not the durable one"
    assert "action_log" in MemoryStore(db).table_names()


# ---------------------------------------------------------------------------
# the rows survive a restart
# ---------------------------------------------------------------------------


def test_a_decision_written_by_one_process_is_readable_by_the_next(
    tmp_path: Path,
) -> None:
    """The whole point, end to end.

    A server is started on a FILE, an entry is typed and posted through the
    surface, and the server is shut down. A completely separate `MemoryStore`
    is then opened on the same path — a different connection, in a different
    thread, after the writer is gone — and the row is still there with its
    reason intact.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        type_an_entry_that_posts(base)

    rows = MemoryStore(db).actions(app.COMPANY)

    assert rows, "the decision did not survive the process that made it"
    assert rows[-1].reason.strip(), "a row without a reason is a timestamp"
    assert rows[-1].action
    assert rows[-1].backend == "FakeTally"


def test_two_runs_against_one_database_leave_two_trails_not_one(
    tmp_path: Path,
) -> None:
    """Append-only ACROSS restarts, not merely within one.

    A store that recreated its schema, or opened with the wrong flags, would
    pass the test above and silently truncate here — and the failure would look
    exactly like nothing having happened.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        type_an_entry_that_posts(base)
    after_one = len(MemoryStore(db).actions(app.COMPANY))
    assert after_one

    with serving(demo_company(), fake_backend(), store_path=db) as base:
        type_an_entry_that_posts(base)
    after_two = len(MemoryStore(db).actions(app.COMPANY))

    assert after_two > after_one, "the second run overwrote the first run's trail"


def test_a_database_written_by_an_older_build_is_migrated_not_rejected(
    tmp_path: Path,
) -> None:
    """The columns tenancy added are nullable and back-fill to NOT_RECORDED.

    An existing customer's database must open. Rejecting it would mean an
    upgrade destroys the exact history this whole task is about keeping.
    """
    db = tmp_path / "app.db"
    old = MemoryStore(db)
    old.record_action(
        ActionLog(
            ts=NOW,
            action="posted",
            company_key=normalise_company(app.COMPANY),
            outcome="POSTED",
            reason="written before tenancy existed",
            run_id="run-1",
            backend="FakeTally",
        )
    )

    with serving(demo_company(), fake_backend(), store_path=db) as base:
        type_an_entry_that_posts(base)

    rows = MemoryStore(db).actions(app.COMPANY)
    assert len(rows) >= 2
    assert rows[0].reason == "written before tenancy existed"


def test_the_log_survives_a_rebuild_of_the_memory_index(tmp_path: Path) -> None:
    """`forget()` runs on every rebuild and must not touch `action_log`.

    The index is a statement about our memory and may be thrown away. What we
    already did to somebody's books is a different fact, and the table is
    deliberately absent from the delete list.
    """
    db = tmp_path / "app.db"

    def rebuild(store: MemoryStore) -> None:
        """Runs on the serving thread, which is the only thread that may touch
        this connection. SQLite binds one to the thread that opened it."""
        store.forget(normalise_company(app.COMPANY))

    with serving(demo_company(), fake_backend(), store_path=db) as base:
        type_an_entry_that_posts(base)
    before = len(MemoryStore(db).actions(app.COMPANY))
    assert before

    with serving(demo_company(), fake_backend(), store_path=db, seed=rebuild) as _base:
        pass

    assert len(MemoryStore(db).actions(app.COMPANY)) == before
