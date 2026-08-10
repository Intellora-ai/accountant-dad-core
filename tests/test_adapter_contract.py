"""Phase 7 exit 7.1 — the extraction seam, and what a backend swap costs.

THE RULE THIS FILE SERVES
-------------------------
We write an adapter, never a reader. Reading a bill is a commodity somebody
else already sells. Choosing the right ACCOUNT is the unsolved part. So the
question here is never "how well does it read" — it is "can the thing that
reads be replaced without touching anything else", because that is what keeps
a month of work off the solved half.

TWO KINDS OF EVIDENCE, AND ONLY ONE OF THEM SETTLES THE CLAIM
--------------------------------------------------------------
The behavioural half — two backends, the same facts, the same draft, the same
decision, the same paise on the trial balance — is necessary and is not
sufficient. A behavioural test passes just as happily on the day somebody adds
a second `from accountant.extract.adapter import SomeBackend` to a module
outside the package. The falsifying question for "a swap changes nothing
outside `accountant/extract/`" is: WHAT TEST WOULD FAIL IF IT DID? Nothing
behavioural would. So the structural half below reads the AST and counts the
concrete-backend references outside the package. That is the test that fails.

The repository already uses this idiom for claims of this shape:
`tests/test_runtime_backend.py` scans the call graph for `write_voucher`, and
`tests/test_no_reader.py` scans imports and identifiers inside the package.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any third-party reader exists, is accurate, or is worth paying for. No
transport ships with `accountant/extract/service.py`; the "service" in every
test here is a function this file wrote that returns a dict.

That real TallyPrime behaves like `FakeTally`. Every posting assertion below
runs against the double. `FakeTally` calls `RealTally`'s own `check_writable`,
so the refusals are the connector's real refusals and not a restatement of
them — but the integration itself is `tests/test_real_tally.py` and the live
evidence in `docs/PROJECT_STATE.md`.

That the GST work is DONE. What is fixed is one specific defect: the
application called a GST bill VALID and the connector then refused the write
VALID had promised. `checks.tax_lines_can_be_posted` closes that, the four
tests at the bottom are ordinary passing tests with no marker, and
`tests/test_gst_safety_sweep.py` holds the rule over thirty cases.

What is NOT fixed, and was not attempted: this system still cannot POST a tax
line. It refuses such bills and hands them to the person, which is safe and is
not the same as supporting GST. No tax rate, tax ledger, CGST/SGST/IGST split
or place-of-supply rule was invented here. That work is Phase 8.

EVIDENCE CLASS
--------------
Structural (AST over the shipped package) for the swap-cost claim.
Behavioural against a double for everything else.
"""

from __future__ import annotations

import ast
import datetime
import pathlib
from collections.abc import Callable
from dataclasses import replace

import pytest

from accountant import pipeline
from accountant.extract import registry
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    Extractor,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.extract.service import (
    ALL_REASONS,
    DOCUMENT_KEY,
    MALFORMED,
    TEXT_KEY,
    ServiceExtractor,
    document_key,
)
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_web import post_for_status

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "accountant"
EXTRACT = PACKAGE / "extract"

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)
BILL = b"paid Sharma Traders 4200 for cement"


# ---- a company, its books, and its own memory -------------------------------


def past(party: str, account: str, amount: int = 100000, n: int = 1) -> list[Voucher]:
    return [
        Voucher(
            id=f"hist-{party}-{account}-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} purchase",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(n)
    ]


def tally(history: list[Voucher] | None = None) -> FakeTally:
    t = FakeTally()
    t.add_company(
        COMPANY, accounts=ACCOUNTS, vouchers=tuple(history or []), backed_up=True
    )
    return t


def memory_for(t: FakeTally) -> CompanyMemory:
    """A fresh store per call. A shared one lets a test pass for the wrong reason."""
    return bootstrap(t, COMPANY, MemoryStore(":memory:"))


# ---- standing up a service backend without a service ------------------------


def answer_about(data: bytes, **fields: object) -> dict[str, object]:
    """A well-formed answer about THESE bytes, with every named field spoken to.

    The default is "the service looked and found nothing", which is an answer.
    A test that wants a field absent from the answer altogether deletes it, and
    that is a different case on purpose.
    """
    return {
        DOCUMENT_KEY: document_key(data),
        "date": None,
        "party": None,
        "total_paise": None,
        "tax_paise": None,
    } | fields


def service_saying(payload: object) -> ServiceExtractor:
    """A backend whose service always sends this exact answer, whatever it is."""

    def call(_data: bytes, _mime: str, _key: str) -> object:
        return payload

    return ServiceExtractor(call)


def service_raising(exc: BaseException) -> ServiceExtractor:
    """A backend whose transport fails this way every time."""

    def call(_data: bytes, _mime: str, _key: str) -> object:
        raise exc

    return ServiceExtractor(call)


def service_for(data: bytes, **fields: object) -> ServiceExtractor:
    """A backend whose service answers correctly, about these exact bytes."""
    return service_saying(answer_about(data, **fields))


#: Every backend the package ships that can be built with no configuration.
CONFIGURATION_FREE: tuple[Callable[[], Extractor], ...] = (
    TypedTextExtractor,
    StubExtractor,
    UnavailableExtractor,
)


def every_backend() -> list[Extractor]:
    """One live instance of every backend, service one included."""
    return [make() for make in CONFIGURATION_FREE] + [service_for(BILL)]


# ---- reading a record without depending on which backend wrote it ------------


def field_values(record: ExtractedRecord) -> dict[str, object]:
    return {name: getattr(record, name) for name in ExtractedRecord.FIELDS}


def not_found_fields(record: ExtractedRecord) -> set[str]:
    return {
        name
        for name, source in record.per_field_source.items()
        if source.startswith(NOT_FOUND)
    }


def sourced_fields(record: ExtractedRecord) -> set[str]:
    return set(record.per_field_source) - not_found_fields(record)


# =============================================================================
# THE RECORD CONTRACT
# =============================================================================


def test_a_record_cannot_be_built_without_a_source_for_every_named_field() -> None:
    with pytest.raises(ValueError, match="no source stated for"):
        ExtractedRecord(
            date=None,
            party="Sharma Traders",
            total_paise=420000,
            tax_paise=None,
            per_field_source={"party": "somewhere"},
        )

    # The state assertion `pytest.raises` cannot make: the four names the
    # constructor demands are the four the rest of the system reads, and a
    # record built with all four really does exist.
    whole = ExtractedRecord(
        date=None,
        party="Sharma Traders",
        total_paise=420000,
        tax_paise=None,
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, "typed_text"),
    )
    assert set(whole.per_field_source) == set(ExtractedRecord.FIELDS)


def test_a_record_that_states_a_source_for_every_field_reports_itself_complete() -> (
    None
):
    record = TypedTextExtractor().extract(BILL, "text/plain")

    assert record.complete is True
    assert set(record.per_field_source) == set(ExtractedRecord.FIELDS)


def test_the_record_names_exactly_the_four_fields_the_whole_system_agrees_on() -> None:
    """A fifth field added here without a matching draft field is a silent blank."""
    assert ExtractedRecord.FIELDS == ("date", "party", "total_paise", "tax_paise")


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_every_backend_satisfies_the_extractor_protocol(backend: Extractor) -> None:
    assert isinstance(backend, Extractor)


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_every_backend_returns_a_record_whose_every_field_carries_a_source(
    backend: Extractor,
) -> None:
    record = backend.extract(BILL, "text/plain")

    assert record.complete is True
    assert not_found_fields(record) | sourced_fields(record) == set(
        ExtractedRecord.FIELDS
    )


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_every_backend_stamps_its_own_name_on_the_record_it_produced(
    backend: Extractor,
) -> None:
    """A record that cannot say who wrote it is not usable as evidence."""
    record = backend.extract(BILL, "text/plain")

    assert record.backend
    assert record.backend != "unknown"


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_a_field_with_no_value_says_not_found_and_never_leaves_the_source_blank(
    backend: Extractor,
) -> None:
    record = backend.extract(BILL, "text/plain")

    for name in ExtractedRecord.FIELDS:
        source = record.per_field_source[name]
        assert source.strip(), f"{name} carries an empty source, which is a blank"
        if getattr(record, name) is None:
            assert source.startswith(NOT_FOUND), f"{name} is absent but not not_found"


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_no_backend_raises_on_bytes_that_are_not_text_at_all(
    backend: Extractor,
) -> None:
    """A person uploads a photograph. That is not an exception, it is a record."""
    record = backend.extract(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "image/jpeg")

    assert record.complete is True


@pytest.mark.parametrize("backend", every_backend(), ids=lambda b: type(b).__name__)
def test_no_backend_raises_on_empty_input(backend: Extractor) -> None:
    record = backend.extract(b"", "text/plain")

    assert record.complete is True


def test_the_record_is_frozen_so_a_later_stage_cannot_edit_the_evidence() -> None:
    record = TypedTextExtractor().extract(BILL, "text/plain")

    with pytest.raises(AttributeError):
        record.total_paise = 1  # pyright: ignore[reportAttributeAccessIssue]

    assert record.total_paise == 420000


def test_a_not_found_source_is_distinguishable_from_a_backend_name() -> None:
    """The two are read by the same code. If they can collide, provenance lies."""
    absent = UnavailableExtractor("nothing answered").extract(BILL, "text/plain")
    present = TypedTextExtractor().extract(BILL, "text/plain")

    assert not_found_fields(absent) == set(ExtractedRecord.FIELDS)
    assert "total_paise" in sourced_fields(present)
    assert not present.per_field_source["total_paise"].startswith(NOT_FOUND)


def test_the_typed_text_backend_never_guesses_a_date() -> None:
    record = TypedTextExtractor().extract(
        b"paid Sharma Traders 4200 on 7 Aug", "text/plain"
    )

    assert record.date is None
    assert record.per_field_source["date"] == NOT_FOUND


def test_the_typed_text_backend_refuses_an_amount_it_cannot_hold_exactly() -> None:
    """Sub-paise precision is a question for the person, never a rounded number."""
    record = TypedTextExtractor().extract(b"paid Sharma Traders 10.005", "text/plain")

    assert record.total_paise is None
    assert record.per_field_source["total_paise"] == NOT_FOUND


def test_the_service_marks_a_field_it_could_not_find_as_not_found() -> None:
    record = service_for(BILL, party="Sharma Traders", total_paise=420000).extract(
        BILL, "text/plain"
    )

    assert record.party == "Sharma Traders"
    assert record.total_paise == 420000
    assert not_found_fields(record) == {"date", "tax_paise"}
    assert "found no date" in record.per_field_source["date"]


def test_the_service_backend_never_converts_a_money_string_into_paise() -> None:
    record = service_for(BILL, total_paise="4200.00").extract(BILL, "text/plain")

    assert record.total_paise is None
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert "money is only ever whole paise" in record.per_field_source["total_paise"]


def test_the_service_backend_refuses_a_float_amount() -> None:
    record = service_for(BILL, total_paise=4200.0).extract(BILL, "text/plain")

    assert record.total_paise is None
    assert "float" in record.per_field_source["total_paise"]


def test_the_service_backend_refuses_a_true_false_value_where_money_belongs() -> None:
    """`isinstance(True, int)` is True, so an unguarded check makes True one paise."""
    record = service_for(BILL, tax_paise=True).extract(BILL, "text/plain")

    assert record.tax_paise is None
    assert "true/false" in record.per_field_source["tax_paise"]


def test_the_service_backend_refuses_a_date_carrying_a_time_of_day() -> None:
    """`datetime` subclasses `date`, and a voucher date with a clock on it is
    a different value that compares unequal to the day it looks like."""
    stamp = datetime.datetime(2026, 8, 7, 14, 30)
    record = service_for(BILL, date=stamp).extract(BILL, "text/plain")

    assert record.date is None
    assert "time of day" in record.per_field_source["date"]


def test_the_service_backend_accepts_an_iso_date_string_and_a_real_date_alike() -> None:
    as_text = service_for(BILL, date="2026-08-07").extract(BILL, "text/plain")
    as_date = service_for(BILL, date=datetime.date(2026, 8, 7)).extract(
        BILL, "text/plain"
    )

    assert as_text.date == datetime.date(2026, 8, 7)
    assert as_date.date == as_text.date
    assert not_found_fields(as_text) == not_found_fields(as_date)


def test_a_whitespace_only_party_becomes_an_explicit_not_found_and_not_a_blank() -> (
    None
):
    record = service_for(BILL, party="   ").extract(BILL, "text/plain")

    assert record.party is None
    assert "party" in not_found_fields(record)


def test_the_document_key_is_the_same_for_the_same_bytes_and_different_otherwise() -> (
    None
):
    assert document_key(BILL) == document_key(bytes(BILL))
    assert document_key(BILL) != document_key(BILL + b" ")


def test_the_document_key_looks_inside_nothing_it_is_given() -> None:
    """Identifying a document is not reading one: same shape for every kind."""
    keys = {
        document_key(b"paid Sharma Traders 4200"),
        document_key(b"%PDF-1.7\n1 0 obj"),
        document_key(b"\xff\xd8\xff\xe0\x00\x10JFIF"),
    }

    assert len(keys) == 3
    assert {len(k) for k in keys} == {16}


def test_the_reasons_the_service_can_give_are_ten_distinct_sentences() -> None:
    """The brief names ten outage scenarios. Ten reasons, none sharing wording."""
    assert len(ALL_REASONS) == 10
    assert len(set(ALL_REASONS)) == 10
    assert all(reason.strip() for reason in ALL_REASONS)


def test_an_extractor_is_recognised_by_shape_and_not_by_inheritance() -> None:
    """The seam is a Protocol. A third party's class inherits nothing of ours."""

    class SomebodyElsesAdapter:
        def extract(self, data: bytes, _mime: str) -> ExtractedRecord:
            return ExtractedRecord(
                date=None,
                party=None,
                total_paise=None,
                tax_paise=None,
                raw_text=data.decode(errors="replace"),
                backend="somebody_else",
                per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, NOT_FOUND),
            )

    outsider = SomebodyElsesAdapter()

    assert isinstance(outsider, Extractor)
    assert outsider.extract(BILL, "text/plain").complete is True


def test_every_extractor_the_package_defines_returns_the_same_type() -> None:
    kinds = {type(backend.extract(BILL, "text/plain")) for backend in every_backend()}

    assert kinds == {ExtractedRecord}


# ---- the one place a backend is chosen --------------------------------------


def test_the_registry_builds_every_backend_it_says_is_available() -> None:
    built = {name: registry.build(name) for name in registry.available()}

    assert registry.available() == ("stub", "typed_text", "unavailable")
    assert all(isinstance(b, Extractor) for b in built.values())


def test_the_registry_refuses_an_unknown_name_rather_than_returning_the_default() -> (
    None
):
    """A typo that silently returns the default is a machine reading bills with
    something other than what the deployment asked for."""
    with pytest.raises(registry.UnknownBackend, match="no extraction backend named"):
        registry.build("typo_text")

    assert isinstance(registry.build(), TypedTextExtractor)


def test_the_registry_says_what_a_backend_still_needs_instead_of_unknown() -> None:
    with pytest.raises(registry.UnknownBackend, match="it needs a transport") as caught:
        registry.build("reader_service")

    assert "reader_service" not in registry.available()
    assert "ServiceExtractor" in str(caught.value)


def test_the_default_backend_is_one_the_registry_can_actually_build() -> None:
    """A default naming something absent fails on the first request, not here."""
    assert registry.DEFAULT_BACKEND in registry.available()
    assert isinstance(registry.default_extractor(), Extractor)


# =============================================================================
# THE SWAP, BEHAVIOURALLY
# =============================================================================
#
# Same facts, two different backends. Everything downstream must be identical.
# `per_field_source` is deliberately NOT compared string for string: the two
# backends SHOULD say different things about where a field came from, because
# that is the whole point of recording it. What must match is which fields have
# a value, what those values are, and which came back not_found.

PARTY = "Sharma Traders"
TOTAL = 420000


def stub_backend() -> StubExtractor:
    return StubExtractor(party=PARTY, total_paise=TOTAL)


def service_backend() -> ServiceExtractor:
    return service_for(BILL, party=PARTY, total_paise=TOTAL)


SWAPPABLE: tuple[Callable[[], Extractor], ...] = (stub_backend, service_backend)


def test_two_backends_given_the_same_facts_produce_the_same_record() -> None:
    a = stub_backend().extract(BILL, "text/plain")
    b = service_backend().extract(BILL, "text/plain")

    assert (
        field_values(a)
        == field_values(b)
        == {
            "date": None,
            "party": PARTY,
            "total_paise": TOTAL,
            "tax_paise": None,
        }
    )
    assert not_found_fields(a) == not_found_fields(b) == {"date", "tax_paise"}


def test_two_backends_given_the_same_facts_differ_only_in_who_they_say_they_are() -> (
    None
):
    a = stub_backend().extract(BILL, "text/plain")
    b = service_backend().extract(BILL, "text/plain")

    assert a.backend != b.backend
    assert replace(a, backend="", per_field_source=dict.fromkeys(a.FIELDS, "")) == (
        replace(
            b,
            backend="",
            per_field_source=dict.fromkeys(b.FIELDS, ""),
            raw_text=a.raw_text,
        )
    )


@pytest.mark.parametrize("make", SWAPPABLE, ids=lambda m: m.__name__)
def test_two_backends_given_the_same_facts_produce_the_same_draft(
    make: Callable[[], Extractor],
) -> None:
    t = tally(past(PARTY, "Purchases", n=40))
    draft = pipeline.build_draft(
        COMPANY, BILL, "text/plain", make(), memory_for(t), today=TODAY
    )

    assert draft.voucher.party == PARTY
    assert draft.voucher.amount_paise == TOTAL
    assert draft.voucher.debit_account == "Purchases"
    assert draft.voucher.gst_paise is None
    assert draft.voucher.date == TODAY


@pytest.mark.parametrize("make", SWAPPABLE, ids=lambda m: m.__name__)
def test_two_backends_given_the_same_facts_produce_the_same_decision(
    make: Callable[[], Extractor],
) -> None:
    t = tally(past(PARTY, "Purchases", n=40))
    d = pipeline.run(COMPANY, BILL, "text/plain", make(), t, memory_for(t), today=TODAY)

    assert d.outcome is Outcome.VALID
    assert d.reason == "nothing unclear and nothing surprising"


@pytest.mark.parametrize("make", SWAPPABLE, ids=lambda m: m.__name__)
def test_two_backends_given_the_same_facts_post_the_same_voucher(
    make: Callable[[], Extractor],
) -> None:
    t = tally(past(PARTY, "Purchases", n=40))
    d = pipeline.run(COMPANY, BILL, "text/plain", make(), t, memory_for(t), today=TODAY)
    back = t.read_by_operation_id(COMPANY, d.operation_id)

    assert d.posted_tally_id is not None
    assert back is not None
    assert (back.party, back.amount_paise, back.debit_account, back.credit_account) == (
        PARTY,
        TOTAL,
        "Purchases",
        "Cash",
    )


def test_two_backends_move_the_trial_balance_by_the_same_paise() -> None:
    moves: list[dict[str, int]] = []
    for make in SWAPPABLE:
        t = tally(past(PARTY, "Purchases", n=40))
        before = t.trial_balance(COMPANY)
        pipeline.run(COMPANY, BILL, "text/plain", make(), t, memory_for(t), today=TODAY)
        after = t.trial_balance(COMPANY)
        moves.append(
            {
                ledger: after.get(ledger, 0) - before.get(ledger, 0)
                for ledger in set(before) | set(after)
                if after.get(ledger, 0) != before.get(ledger, 0)
            }
        )

    assert moves[0] == {"Purchases": TOTAL, "Cash": -TOTAL}
    assert moves[0] == moves[1]


def test_a_backend_that_finds_nothing_still_leaves_the_posting_gate_shut() -> None:
    """The swap must not be able to open a door. A silent backend posts nothing."""
    t = tally(past(PARTY, "Purchases", n=40))
    before = t.trial_balance(COMPANY)
    d = pipeline.run(
        COMPANY,
        BILL,
        "text/plain",
        service_for(BILL),
        t,
        memory_for(t),
        today=TODAY,
    )

    assert d.outcome is not Outcome.VALID
    assert d.posted_tally_id is None
    assert t.trial_balance(COMPANY) == before
    assert t.list_our_vouchers(COMPANY) == ()


# =============================================================================
# THE SWAP, STRUCTURALLY — the half a behavioural test cannot reach
# =============================================================================


def modules_under(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def tree_of(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def relative(path: pathlib.Path) -> str:
    return path.relative_to(REPO).as_posix()


def backend_class_names() -> frozenset[str]:
    """Every concrete backend the package defines. DERIVED, never hand-kept.

    A class in `accountant/extract/` that defines `extract` is a backend. The
    `Extractor` Protocol also defines `extract`, and it is excluded by its base
    — it is the contract, not an implementation of it. Deriving the list means
    a backend added tomorrow is covered without anybody remembering to add it
    here, which is the same argument `tests/test_no_reader.py` makes for
    preferring an allowlist over a list of banned libraries.
    """
    found: set[str] = set()
    for path in modules_under(EXTRACT):
        for node in ast.walk(tree_of(path)):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases):
                continue
            if any(
                isinstance(item, ast.FunctionDef) and item.name == "extract"
                for item in node.body
            ):
                found.add(node.name)
    return frozenset(found)


def references(tree: ast.Module, names: frozenset[str]) -> set[str]:
    """Every one of `names` this module's CODE mentions. No comments, no prose."""
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            hits.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            hits.add(node.attr)
        elif isinstance(node, ast.alias):
            hits |= names & {node.name.split(".")[-1]}
    return hits


def backend_sites(*, skip: tuple[pathlib.Path, ...] = ()) -> dict[str, list[str]]:
    """Modules outside the package that name a concrete backend, and which one."""
    backends = backend_class_names()
    out: dict[str, list[str]] = {}
    for path in modules_under(PACKAGE):
        if EXTRACT in path.parents or any(root in path.parents for root in skip):
            continue
        hits = sorted(references(tree_of(path), backends))
        if hits:
            out[relative(path)] = hits
    return out


def names_imported_from_extract() -> dict[str, list[str]]:
    """What each module outside the package takes from `accountant.extract.*`."""
    out: dict[str, list[str]] = {}
    for path in modules_under(PACKAGE):
        if EXTRACT in path.parents:
            continue
        taken: set[str] = set()
        for node in ast.walk(tree_of(path)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "accountant.extract"
            ):
                taken.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                taken.update(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("accountant.extract")
                )
        if taken:
            out[relative(path)] = sorted(taken)
    return out


#: The abstract half of the package. Depending on these costs a swap nothing.
#:
#: `default_extractor` belongs here for the same reason `Extractor` does: it
#: names no backend, so a module that calls it cannot be made to change by
#: choosing a different one. What it returns is decided by `DEFAULT_BACKEND`,
#: one line inside the package. Adding it here widens the contract, not the
#: blast radius.
#:
#: `guarded` joined on the same argument, 2026-08-10, and the argument was
#: CHECKED rather than assumed: this test failed the moment
#: `accountant/web/app.py` imported the name, which is the guard working. It is
#: `(Extractor) -> Extractor`. It takes no name, returns no named thing, and
#: choosing a different backend does not change one character of a module that
#: calls it — so a swap still costs nothing outside the package.
#:
#: WHAT WAS NOT WIDENED, and this is the distinction that matters:
#: `KNOWN_SELECTION_SITES` below is still the empty set, and
#: `test_backend_selection_happens_nowhere_outside_the_package` passed
#: unchanged through this commit. CONTRACT says which ABSTRACT names the core
#: may take. The ratchet says how many CONCRETE backends it may name, and that
#: bound is zero and did not move. Widening the first to keep the second at
#: zero is the whole point; widening the second would be the weakening.
CONTRACT = frozenset(
    {
        "Extractor",
        "ExtractedRecord",
        "LineItem",
        "NOT_FOUND",
        "default_extractor",
        "guarded",
    }
)

#: Selection sites outside the package. EMPTY — that is the whole of exit 7.1.
#:
#: Measured at 27333e9: `{'accountant/web/app.py': ['TypedTextExtractor']}`. One
#: file, one name, so swapping the runtime backend edited the web app.
#: Measured at 2026-08-10, after `web/app.py` was pointed at
#: `registry.default_extractor()`: `{}`.
#:
#: Kept as a named empty set rather than deleted, because it is the ratchet.
#: Any module outside `accountant/extract/` that names a concrete backend is
#: now a failure, and there is no longer an allowlisted file to hide inside.
KNOWN_SELECTION_SITES: frozenset[str] = frozenset()


def test_swapping_the_backend_changes_no_module_in_the_core() -> None:
    """Everything except the web app depends on the Protocol and nothing else.

    This is the claim in its provable form. `pipeline`, `memory`, `detect`,
    `score`, `ingest`, `tallyio` — none of them can name a backend, so none of
    them can be made to change by choosing a different one.
    """
    offenders = backend_sites(skip=(PACKAGE / "web",))

    assert offenders == {}, (
        "a module in the core names a concrete extraction backend, so a "
        f"backend swap now edits code outside accountant/extract/: {offenders}"
    )


def test_backend_selection_happens_nowhere_outside_the_package() -> None:
    """The ratchet, at its final setting.

    Until 2026-08-10 this allowed exactly one site, `accountant/web/app.py`,
    and read `<= 1`. That file now resolves its backend through
    `registry.default_extractor()`, so the allowance is spent and the bound is
    zero. ANY module outside `accountant/extract/` that names a concrete
    backend fails here — which is the exact regression no behavioural test can
    see, because every behavioural test passes just as happily with the site
    present.
    """
    offenders = backend_sites()

    assert set(offenders) <= KNOWN_SELECTION_SITES, (
        "a module outside accountant/extract/ names a concrete extraction "
        "backend, so a backend swap no longer costs one edit inside the "
        "package. Backend selection belongs in "
        f"accountant/extract/registry.py. Sites found: {offenders}"
    )
    assert offenders == {}


def test_the_core_takes_only_the_contract_from_the_extraction_package() -> None:
    """What the rest of the repository imports, name by name, measured."""
    taken = names_imported_from_extract()
    core = {
        module: names
        for module, names in taken.items()
        if module not in KNOWN_SELECTION_SITES
    }
    outside_the_contract = {
        module: sorted(set(names) - CONTRACT) for module, names in core.items()
    }

    assert taken, "nothing imports from accountant.extract; the scan found nothing"
    assert {m: n for m, n in outside_the_contract.items() if n} == {}, (
        "a module outside accountant/extract/ depends on something other than "
        f"the contract {sorted(CONTRACT)}: {outside_the_contract}"
    )


def test_the_pipeline_asks_for_the_protocol_and_never_for_a_backend_class() -> None:
    """The signature is the seam. A concrete annotation there ends the swap."""
    tree = tree_of(PACKAGE / "pipeline.py")
    seen: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in {
            "build_draft",
            "run",
        }:
            continue
        for arg in node.args.args:
            if arg.arg == "extractor":
                seen[node.name] = ast.unparse(arg.annotation) if arg.annotation else ""

    assert seen == {"build_draft": "Extractor", "run": "Extractor"}


def test_the_backend_scan_derives_its_list_from_the_package() -> None:
    """A scan that found no backends would pass every assertion above at zero."""
    found = backend_class_names()

    assert {
        "TypedTextExtractor",
        "StubExtractor",
        "UnavailableExtractor",
        "ServiceExtractor",
        # Added by the derivation itself on 2026-08-10, not by anybody
        # remembering: `GuardedExtractor` defines `extract`, so the scan
        # counted it as a backend the moment it was written. It is named here
        # to record that the scan noticed, which is the property this test is
        # for. It is also why `accountant/web/app.py` reaches the guard through
        # the `guarded()` FUNCTION — spelling the class there would be a
        # selection site.
        "GuardedExtractor",
    } <= found
    assert len(found) >= 5


def test_no_name_in_the_contract_is_a_backend() -> None:
    """The ratchet on the ratchet.

    `CONTRACT` is the allowlist `test_the_core_takes_only_the_contract_from_the
    _extraction_package` measures against, so the cheapest way to silence that
    test is to add the offending name here. If the offending name is a concrete
    backend, that turns exit 7.1 into a list of exceptions. This makes the
    cheap fix fail.
    """
    assert CONTRACT & backend_class_names() == frozenset(), (
        "a concrete backend was added to CONTRACT, which turns the allowlist "
        "into a way of permitting exactly what exit 7.1 forbids"
    )


def test_the_backend_scan_does_not_mistake_the_contract_for_an_implementation() -> None:
    """`Extractor` defines `extract` too. Counting it would make every module
    that imports the Protocol look like a selection site, and the guard would
    have to be weakened to shut it up."""
    assert "Extractor" not in backend_class_names()


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("an import", "from accountant.extract.adapter import StubExtractor\n"),
        (
            "a renamed import",
            "from accountant.extract.adapter import StubExtractor as S\n",
        ),
        ("a construction", "def f():\n    return StubExtractor()\n"),
        ("an attribute", "def f(m):\n    return m.StubExtractor()\n"),
    ],
)
def test_the_backend_scan_catches_a_backend_planted_outside_the_package(
    label: str, source: str
) -> None:
    assert references(ast.parse(source), frozenset({"StubExtractor"})), (
        f"{label} slipped past the scan, so the scan proves nothing"
    )


def test_the_backend_scan_does_not_fire_on_prose_about_backends() -> None:
    """The disconfirming case. This file and the package both discuss the
    backends by name. A scan that read comments would flag the docstring
    stating the rule, and the usual fix for that is to weaken the scan."""
    prose = '''
"""We used to construct StubExtractor here. TypedTextExtractor too."""
# and UnavailableExtractor
NOTE = "ServiceExtractor belongs behind the registry"
def build(): return None
'''
    assert references(ast.parse(prose), backend_class_names()) == set()


def test_the_measured_cost_of_a_backend_swap_is_no_line_outside_the_package() -> None:
    """The number, reported rather than described.

    Measured at 27333e9: `{'accountant/web/app.py': ['TypedTextExtractor']}` —
    one file, one name.
    Measured at 2026-08-10: `{}` — no file, no name.

    The lever moved inside the package. It is `DEFAULT_BACKEND` in
    `accountant/extract/registry.py`, and the assertions below say so in the
    two ways that can fail: the count outside, and the existence of the one
    line inside. A zero that came from a scan pointed at nothing would pass the
    first assertion and fail the second.
    """
    measured = backend_sites()

    assert sum(len(names) for names in measured.values()) == 0, (
        f"a backend swap touches a line outside the package: {measured}"
    )
    assert (REPO / "accountant/extract/registry.py").exists(), (
        "the swap lever is supposed to live in accountant/extract/registry.py "
        "and that file does not exist, so the zero above means the scan found "
        "nowhere to look rather than nothing to find"
    )
    assert registry.DEFAULT_BACKEND in registry.available(), (
        f"DEFAULT_BACKEND is {registry.DEFAULT_BACKEND!r}, which the registry "
        "cannot build; the one line the swap costs would not start the app"
    )


def test_the_structural_scan_covers_the_modules_it_claims_to_cover() -> None:
    scanned = [relative(p) for p in modules_under(PACKAGE) if EXTRACT not in p.parents]

    assert "accountant/pipeline.py" in scanned
    assert "accountant/web/app.py" in scanned
    assert "accountant/memory/store.py" in scanned, "rglob missed a subpackage"
    assert len(scanned) >= 30, f"measured 39 at 27333e9, saw {len(scanned)}"
    assert not [p for p in scanned if p.startswith("accountant/extract/")]


# =============================================================================
# MALFORMED ANSWERS — nothing from a broken answer reaches the record
# =============================================================================


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("a list", ["Sharma Traders", 420000]),
        ("a bare string", "Sharma Traders 4200"),
        ("a number", 420000),
        ("nothing at all", None),
        ("keys that are not names", {1: "Sharma Traders", 2: 420000}),
    ],
)
def test_an_answer_that_is_not_a_set_of_named_fields_is_refused_whole(
    label: str, payload: object
) -> None:
    record = service_saying(payload).extract(BILL, "text/plain")

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS), label
    assert field_values(record) == dict.fromkeys(ExtractedRecord.FIELDS)
    assert all(MALFORMED in s for s in record.per_field_source.values())


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("total_paise", "4200.00", "whole paise"),
        ("total_paise", 4200.0, "float"),
        ("tax_paise", ["18%"], "list"),
        ("tax_paise", False, "true/false"),
        ("date", "not sure", "is not a date"),
        ("date", 20260807, "arrived as int"),
        ("party", 4200, "arrived as int"),
    ],
)
def test_an_answer_with_a_field_of_the_wrong_type_is_refused_whole(
    field: str, value: object, expected: str
) -> None:
    """No coercion anywhere. A total that arrives as "4200.00" is the service
    breaking its contract, and quietly parsing it is how a rounded rupee lands
    in somebody's books."""
    record = service_for(BILL, **{field: value}).extract(BILL, "text/plain")

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert expected in record.per_field_source[field]


def test_an_answer_whose_carried_text_is_not_text_is_refused_whole() -> None:
    record = service_for(BILL, **{TEXT_KEY: 42}).extract(BILL, "text/plain")

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert record.raw_text == ""


def test_a_malformed_answer_names_the_field_that_was_wrong() -> None:
    """A reason nobody can act on is a reason nobody reads."""
    record = service_for(BILL, total_paise=4200.0, party=7).extract(BILL, "text/plain")
    reason = record.per_field_source["date"]

    assert "total_paise" in reason
    assert "party" in reason


def test_nothing_from_a_malformed_answer_reaches_the_draft() -> None:
    """The half that parsed is not kept. Half a bill is not a voucher."""
    t = tally(past(PARTY, "Purchases", n=40))
    before = t.trial_balance(COMPANY)
    broken = service_for(BILL, party=PARTY, total_paise="4200.00")
    d = pipeline.run(COMPANY, BILL, "text/plain", broken, t, memory_for(t), today=TODAY)

    assert d.voucher.party == ""
    assert d.voucher.amount_paise == 0
    assert d.outcome is not Outcome.VALID
    assert t.trial_balance(COMPANY) == before
    assert t.list_our_vouchers(COMPANY) == ()


# =============================================================================
# THE GST DEFECT — measured, then FIXED 2026-08-10
# =============================================================================
#
# The unsafe path, as measured at 27333e9 and again on top of D-06:
#
#     a bill carrying GST reaches VALID
#       -> pipeline.post writes a `write_attempted` row
#       -> the connector REFUSES, because it builds no tax lines
#       -> a `write_outcome_unknown` row is written and the error escapes
#       -> over HTTP the person gets "Something in Accountant Dad broke"
#
# The connector was RIGHT to refuse. Writing the voucher would drop the tax
# silently and produce a wrong statutory entry. What was wrong is that the
# application said VALID first: it promised something the connector would not
# take, and the failure surfaced as a breakage instead of a sentence.
#
# TWO WRONG PREDICTIONS ARE RECORDED HERE RATHER THAN DELETED, because both
# shaped what got built and both were this project's own.
#
#   1. "BLOCKED_BY_D06". D-06 landed as 1ca65a9 and did change
#      `accountant/pipeline.py`, but for stale vendor memory, not for tax:
#      `git diff 27333e9 1ca65a9 -- accountant/pipeline.py` matches neither
#      "gst" nor "tax", zero hits. All four tests failed on top of it exactly
#      as they had before. The blocker was never D-06.
#
#   2. "the blocker is GST rules work, Phase 8". Also wrong, and it was the
#      more expensive error, because it made a two-line rule look like a
#      quarter of statutory engineering. Posting a tax line IS Phase 8 work.
#      Refusing to call a bill VALID when its tax cannot be posted is not: it
#      is one check that asks the question the connector was already asking.
#      The accounting-policy question — "what must a tax line contain before a
#      bill carrying one may be VALID" — never had to be answered to close
#      this, because the answer to "can we build ANY tax line" is no.
#
# THE FIX, in full:
#
#     schema.Voucher.needs_tax_lines      the condition, written once
#     checks.tax_lines_can_be_posted      the application asks it before deciding
#     problems.UNANSWERABLE_CHECKS        so it hands over rather than asks
#     tallyio.real.check_writable         now reads the same expression
#
# `accountant/pipeline.py` is untouched. The decision order did not need
# changing; it needed a check to decide on.
#
# WHAT IS STILL NOT BUILT: a tax line. This system refuses GST bills and hands
# them to the person. That is safe and it is not GST support. No rate, ledger,
# CGST/SGST/IGST split or place-of-supply rule was invented.
#
# The rule is held over thirty cases in `tests/test_gst_safety_sweep.py`,
# including the arm that fails if this ever becomes "refuse everything".

GST_BILL = b"paid Sharma Traders 4200 for cement including 18% GST"
GST_PAISE = 64068  # 18% of 420000 inclusive, exactly, from the typed-text backend


def gst_company() -> FakeTally:
    """A vendor with enough consistent history that nothing else asks a question."""
    return tally(past(PARTY, "Purchases", n=40))


def gst_draft(t: FakeTally) -> pipeline.Draft:
    memory = memory_for(t)
    draft = pipeline.build_draft(
        COMPANY, GST_BILL, "text/plain", TypedTextExtractor(), memory, today=TODAY
    )
    return pipeline.evaluate(
        draft, t.read_accounts(COMPANY), t.read_vouchers(COMPANY), memory
    )


def test_the_extraction_of_a_gst_bill_is_exactly_what_the_defect_starts_from() -> None:
    """Not the defect — the input to it. Pinned so the numbers below stay real."""
    record = TypedTextExtractor().extract(GST_BILL, "text/plain")

    assert record.total_paise == 420000
    assert record.tax_paise == GST_PAISE
    assert record.per_field_source["tax_paise"] == "typed_text"


def test_the_connector_refuses_a_gst_voucher_and_says_why() -> None:
    """The connector is the LAST line and it holds, independently of the first.

    CORRECTED 2026-08-10, mechanism only; the claim is unchanged and now proved
    more directly.

    Old form: `pipeline.post(draft, t)` and expect `match="builds no tax lines"`.
    Why that became wrong: `checks.tax_lines_can_be_posted` now stops the same
    bill at the application gate, so `pipeline.post` raises "refusing to post:
    outcome is not_valid" and the connector is never reached. The old form
    would have started passing for the wrong reason if the match string were
    loosened, and failing for the right one if it were not — either way it would
    no longer be testing the connector.

    New form: hand the voucher STRAIGHT to the client, which is the only way to
    ask the connector a question the application gate cannot answer first. This
    is defence in depth stated as a test: a caller who builds a voucher by hand
    and skips `evaluate` entirely still cannot write tax into somebody's books.
    """
    t = gst_company()
    draft = gst_draft(t)

    assert draft.voucher.gst_paise == GST_PAISE, "the fixture stopped carrying tax"

    with pytest.raises(ValueError, match="builds no tax lines"):
        t.write_voucher(COMPANY, draft.voucher, draft.operation_id)

    assert draft.posted_tally_id is None
    assert t.list_our_vouchers(COMPANY) == ()


def test_a_gst_bill_writes_nothing_and_moves_the_trial_balance_by_zero_paise() -> None:
    """THE PIN. However this is fixed, this must stay true to the paise.

    CORRECTED 2026-08-10, mechanism only; every number below is unchanged.

    Old form wrapped the call in `pytest.raises(ValueError)`. Why that became
    wrong: it pinned HOW the bill was stopped, not THAT it was stopped, and the
    how was the defect. `pipeline.run` used to reach `post`, hit the connector's
    refusal and let a `ValueError` escape all the way to the caller; it now
    decides NOT_VALID and takes the `blocked` branch, so nothing raises. Keeping
    `pytest.raises` would have made a fixed system look broken and, worse, would
    have made the exception itself load-bearing.

    New form asserts the outcome AND the three numbers. It is strictly stronger:
    the old version was satisfied by any `ValueError` from anywhere, including
    one raised after a partial write; this one says the entry was refused before
    the write path, nothing raised, and the books did not move by one paise.
    """
    t = gst_company()
    before = t.trial_balance(COMPANY)

    draft = pipeline.run(
        COMPANY,
        GST_BILL,
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )

    assert draft.outcome is not Outcome.VALID
    assert draft.posted_tally_id is None
    assert t.trial_balance(COMPANY) == before
    assert t.list_our_vouchers(COMPANY) == ()
    assert len(t.read_vouchers(COMPANY)) == 40


def test_a_gst_bill_without_tax_lines_cannot_be_valid() -> None:
    """Measured at 27333e9: it IS valid, with the reason "nothing unclear and
    nothing surprising". The application promises a write the connector will
    refuse.

    Re-measured 2026-08-10 on top of D-06 (1ca65a9): unchanged, still VALID.
    """
    draft = gst_draft(gst_company())

    assert draft.voucher.gst_paise == GST_PAISE
    assert draft.outcome is not Outcome.VALID


def test_a_gst_bill_with_incomplete_tax_data_asks_a_question_or_hands_over() -> None:
    """Nothing in the system can post a tax line, so every GST amount is
    incomplete tax data. The person should be asked, or the entry handed to
    them — in words that mention the tax.

    Re-measured 2026-08-10 on top of D-06 (1ca65a9): no question is asked and
    neither "tax" nor "gst" appears in what the person is told.
    """
    draft = gst_draft(gst_company())
    question = pipeline.next_question(draft)
    said = (draft.reason + " " + (question.text if question else "")).lower()

    assert draft.outcome in {Outcome.UNCLEAR, Outcome.NOT_VALID}
    assert "tax" in said or "gst" in said


def test_a_connector_refusal_cannot_happen_after_the_application_said_valid() -> None:
    """The contract between the two halves: VALID means "the connector will take
    this". Asserted in BOTH directions, because one direction proves nothing.

    CORRECTED 2026-08-10. The five things a test change owes:

    old assertion   `if draft.outcome is not Outcome.VALID: pytest.skip(...)`,
                    then `pipeline.post(draft, t)` and
                    `assert draft.posted_tally_id is not None`.
    why it was wrong
                    It was written as a defect probe while the bill still
                    reached VALID. The moment the defect was fixed the premise
                    went false and the test SKIPPED — deleting itself at exactly
                    the point where the invariant in its own name became
                    guardable. A skip is not a pass, and nothing else in the
                    suite asserted "VALID implies the connector accepts".
    new assertion   the GST bill is not VALID, `post` refuses it at the
                    application gate, and NO `write_attempted` row is written;
                    and a bill the application DOES call VALID posts and is
                    accepted by the connector.
    safety impact   strictly more is now forbidden. The old form could not fail
                    at all once the fix landed. This one fails if a GST bill
                    reaches VALID again, if a refused entry still opens a write,
                    or if VALID ever stops meaning "postable".
    new result      PASS, as an ordinary test, no marker.

    The `write_attempted` row is the load-bearing half of the first direction.
    A write that was never entitled to start must leave no write-ahead row: that
    row is the durable signature of "a voucher may exist in the books and we
    cannot say", and writing one for an entry we refused would send somebody
    hunting through Tally for a voucher that was never sent.
    """
    t = gst_company()
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    # Direction 1: refused before the write path, and the connector is never asked.
    refused = pipeline.run(
        COMPANY,
        GST_BILL,
        "text/plain",
        TypedTextExtractor(),
        t,
        memory,
        today=TODAY,
        log=store,
        run_id="phase7-gst",
    )

    assert refused.voucher.gst_paise == GST_PAISE, "the fixture stopped carrying tax"
    assert refused.outcome is not Outcome.VALID
    assert refused.posted_tally_id is None
    assert [r.action for r in store.actions(COMPANY)] == ["blocked"]
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(refused, t)

    # Direction 2: the same company, a bill with no tax on it. VALID has to be
    # worth something, or direction 1 is satisfied by refusing everything.
    posted = pipeline.run(
        COMPANY, BILL, "text/plain", TypedTextExtractor(), t, memory, today=TODAY
    )

    assert posted.voucher.gst_paise is None
    assert posted.outcome is Outcome.VALID
    assert posted.posted_tally_id is not None


# ---- the same defect, over real HTTP ----------------------------------------
#
# `post_for_status` was defined here until 2026-08-10 and now lives beside the
# other HTTP helpers in `tests/test_web.py`. `tests/test_extract_outage.py`
# needed the same thing for the HTTP reader outage, and two copies of a helper
# that decides what a 503 means is how two files end up measuring differently.


def test_a_gst_bill_over_http_writes_nothing_and_moves_no_paise(server: str) -> None:
    """THE PIN, over the surface a person actually touches.

    Whatever D-06 makes the page say, these three numbers must not change.
    """
    live = app.runtime()
    before = live.client.trial_balance(live.company)
    written_before = len(live.client.list_our_vouchers(live.company))

    status, _ = post_for_status(server, "/entry", text=GST_BILL.decode())

    assert status in {200, 503}
    assert live.client.trial_balance(live.company) == before
    assert len(live.client.list_our_vouchers(live.company)) == written_before


def test_a_gst_bill_over_http_is_answered_rather_than_dropped(server: str) -> None:
    """Failing safely and failing legibly are two properties. This is the first
    one: the socket is not dropped and the person gets a page."""
    status, body = post_for_status(server, "/entry", text=GST_BILL.decode())

    assert status in {200, 503}
    assert "<html" in body.lower() or "<div" in body.lower()
    assert "Traceback" not in body
    assert "gst_paise" not in body, "an internal field name reached the screen"


def test_a_gst_bill_over_http_explains_the_tax_instead_of_reporting_a_breakage(
    server: str,
) -> None:
    """Measured at 27333e9: the page says "Something in Accountant Dad broke"
    for an ordinary bill with GST on it. The bill is not broken and neither is
    the app; the application promised a write the connector will not take.

    Re-measured 2026-08-10 on top of D-06 (1ca65a9): still HTTP 503.
    """
    status, body = post_for_status(server, "/entry", text=GST_BILL.decode())

    assert status == 200
    assert "broke" not in body.lower()
    assert "tax" in body.lower() or "gst" in body.lower()
