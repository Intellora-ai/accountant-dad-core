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
from accountant.cage.confidence import EXACT
from accountant.extract import ladder, registry
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    Extractor,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.extract.freeocr import FreeReader
from accountant.extract.ladder import Ladder
from accountant.extract.placeholder import PlaceholderReader
from accountant.extract.service import (
    ALL_REASONS,
    DOCUMENT_KEY,
    MALFORMED,
    TEXT_KEY,
    ServiceExtractor,
    document_key,
)
from accountant.extract.textlayer import TextLayerReader
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
#:
#: `PlaceholderReader` joined 2026-08-11. It reads nothing on purpose, which is
#: exactly why it belongs here: the record contract below — every field
#: sourced, no silent blank, its own name on the row, no exception on bytes
#: that are not text — is what stops "we have no reader" being expressed as a
#: blank, and a backend exempt from those checks could express it as one.
#:
#: `TextLayerReader` and `Ladder` joined 2026-08-13 with their registration.
#: They are the first two entries here that can actually READ something, which
#: makes the record contract matter more rather than less: a rung that returns
#: a value has somewhere new to leave a silent blank, and the router has a whole
#: new way to lose one — by handing back a rung's record without checking it.
CONFIGURATION_FREE: tuple[Callable[[], Extractor], ...] = (
    TypedTextExtractor,
    TextLayerReader,
    Ladder,
    StubExtractor,
    UnavailableExtractor,
    PlaceholderReader,
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
    """CORRECTED 2026-08-13 TWICE: four names became six, then six became seven.

    `pdf_text_layer` and `ladder` joined `_READY` the day `D-30` cleared the two
    reader modules; `free_ocr` joined it the day `pagereader.py` gave
    `FreeReader` the page reader it had always been missing. The tuple was not
    weakened to a subset either time and is not going to be: this is still an
    EXACT equality, because the thing it guards is that a backend cannot appear
    in the registry without somebody writing its name down here — and a reader
    arriving unannounced is precisely the event this repository spends
    `tests/test_no_reader.py` on.
    """
    built = {name: registry.build(name) for name in registry.available()}

    assert registry.available() == (
        "free_ocr",
        "ladder",
        "no_reader",
        "pdf_text_layer",
        "stub",
        "typed_text",
        "unavailable",
    )
    assert all(isinstance(b, Extractor) for b in built.values())


def test_the_registry_refuses_an_unknown_name_rather_than_returning_the_default() -> (
    None
):
    """A typo that silently returns the default is a machine reading bills with
    something other than what the deployment asked for.

    THE SECOND ASSERTION PINS THE DEFAULT'S IDENTITY, and it was corrected on
    2026-08-13 rather than deleted. It read `TypedTextExtractor` and the default
    became `ladder` that day, because `typed_text` reads a sentence somebody
    typed and refuses everything else — so the product accepted a PDF and a
    photograph on `/upload` and then handed both to a regex that could not read
    either. Measured before the change, through the call `app.py:1444` makes: a
    corpus PDF, PNG and JPG all came back four `not_found`s; through the ladder,
    the same PDF came back with its date, supplier, total and tax.

    Kept rather than removed because a test that pins WHICH backend a bare
    `build()` returns is the only thing standing between "the default changed
    for a reason" and "the default changed because somebody edited a dict".
    Pointed at the new one, it still fails the day it moves again.
    """
    with pytest.raises(registry.UnknownBackend, match="no extraction backend named"):
        registry.build("typo_text")

    assert isinstance(registry.build(), Ladder)
    assert not isinstance(registry.build(), TypedTextExtractor)


def test_the_registry_says_what_a_backend_still_needs_instead_of_unknown() -> None:
    with pytest.raises(registry.UnknownBackend, match="it needs a transport") as caught:
        registry.build("reader_service")

    assert "reader_service" not in registry.available()
    assert "ServiceExtractor" in str(caught.value)


def test_the_picture_reader_builds_from_its_name_now_that_it_has_a_page_reader() -> (
    None
):
    """CORRECTED 2026-08-13. `free_ocr` used to be the second `_NEEDS_WIRING`
    entry, because `FreeReader` takes a page reader — the thing that says which
    words on a page are the total, the tax, the date and the supplier — and
    nothing here answered it. `accountant/extract/pagereader.py` does, so the
    name builds and the table entry would now be a lie.

    The distinction `_NEEDS_WIRING` draws is not weakened by one name leaving
    it: `reader_service` still demonstrates it in the test above, and the
    assertion that `free_ocr` is no longer in it is what stops a backend being
    listed as both buildable and blocked.
    """
    built = registry.build("free_ocr")

    assert isinstance(built, FreeReader)
    assert built.name == "free_ocr"
    assert "free_ocr" in registry.available()
    assert "free_ocr" not in registry._NEEDS_WIRING  # pyright: ignore[reportPrivateUsage]


def test_the_router_hands_typed_text_to_the_rung_that_already_read_it() -> None:
    """The safety argument for `DEFAULT_BACKEND = "ladder"`, in its testable
    form: on the media type the product runs on today, the router is not a
    change. Same rung, same record, same number."""
    routed = Ladder().extract(BILL, "text/plain")
    direct = TypedTextExtractor().extract(BILL, "text/plain")

    assert routed.backend == "typed_text"
    assert field_values(routed) == field_values(direct)
    assert routed.per_field_source == direct.per_field_source


def test_the_router_hands_a_pdf_to_the_text_layer_rung_and_names_it() -> None:
    """Routing is provable without a real PDF: these bytes reach the PDF rung
    and are refused BY IT, in its words, under its name. A router that had
    swallowed the document would answer under its own."""
    record = Ladder().extract(b"not a pdf at all", "application/pdf")

    assert record.backend == "pdf_text_layer"
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert "do not begin with %PDF-" in record.per_field_source["total_paise"]


@pytest.mark.parametrize(
    ("label", "mime"),
    [
        ("a Word file", "application/vnd.openxmlformats-officedocument"),
        ("a spreadsheet", "text/csv"),
        ("nothing declared", ""),
    ],
)
def test_the_router_refuses_what_no_rung_reads_and_says_what_to_do_instead(
    label: str, mime: str
) -> None:
    """A refusal that says "unsupported" tells a person to wait for a feature.
    This one tells them what to do now.

    THE TWO IMAGE CASES LEFT THIS LIST ON 2026-08-13 and became the test below,
    because they stopped being refusals: the picture rung is wired. The old
    sentence asked the person to type the bill in instead, which would now be
    asking them to retype a bill this system can read."""
    record = Ladder().extract(b"\xff\xd8\xff\xe0", mime)

    assert record.backend == "ladder", label
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert all(
        ladder.NOT_A_KIND_WE_READ in source
        for source in record.per_field_source.values()
    )


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_a_picture_reaches_the_reading_engine_rather_than_a_refusal(mime: str) -> None:
    """The wiring, at the router. These bytes are not a picture, so what comes
    back is still every field unread — but it is the READING ENGINE'S refusal,
    under the engine's name, which is what proves the document got there. A
    router that had swallowed it would answer under `ladder`."""
    record = Ladder().extract(b"\xff\xd8\xff\xe0", mime)

    assert record.backend == "free_ocr"
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert ladder.NOT_A_KIND_WE_READ not in record.per_field_source["total_paise"]


def test_the_router_reads_only_what_it_says_it_reads() -> None:
    """The list is asserted rather than described, so a rung cannot arrive
    without the count moving. The five picture types are `freeocr.READABLE_MEDIA`
    and are not written twice — the router claiming to read a kind the rung
    refuses is the drift this equality catches."""
    assert Ladder().reads() == (
        "application/pdf",
        "image/bmp",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "text/plain",
    )


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
    """The same facts as `stub_backend`, INCLUDING how sure it says it is.

    `confidence` joined the answer on 2026-08-13, when `ExtractedRecord` grew a
    per-field score. It is stated here because "the same facts" has to mean the
    same facts: a service that says nothing about its own certainty is not
    telling us what the stub tells us, and the drafts below would then differ
    for a real reason rather than because a backend was swapped.

    Which is itself the new rule, and it is asserted separately in
    `test_a_backend_that_states_no_confidence_does_not_name_the_supplier`
    below rather than being folded in here: a party name that was estimated -
    or whose certainty nobody stated - is never used as a supplier's identity.
    """
    return service_for(
        BILL,
        party=PARTY,
        total_paise=TOTAL,
        confidence={"party": EXACT, "total_paise": EXACT},
    )


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


def test_a_backend_that_states_no_confidence_does_not_name_the_supplier() -> None:
    """The price of silence, asserted rather than assumed. F-03.

    A service that reports no per-field confidence is not a service that read
    the bill exactly - it is one that never said. Absent is not certain, so its
    party name is not handed to `propose_account` and does not land on the
    voucher; the person is asked who it was instead.

    The reading is NOT thrown away: the record still carries the name, the
    source and the fact that no score was stated. Only the identity is withheld.
    """
    silent = service_for(BILL, party=PARTY, total_paise=TOTAL)
    record = silent.extract(BILL, "text/plain")

    assert record.party == PARTY
    assert record.confidence_of("party") is None
    assert not record.read_exactly("party")

    t = tally(past(PARTY, "Purchases", n=40))
    draft = pipeline.build_draft(
        COMPANY, BILL, "text/plain", silent, memory_for(t), today=TODAY
    )

    assert draft.voucher.party == ""
    assert draft.voucher.debit_account == ""
    # The control on the same two lines: the SAME service, saying how sure it
    # is, names the supplier exactly as before. Without this the assertion
    # above would also pass if the service backend had simply stopped working.
    speaking = pipeline.build_draft(
        COMPANY, BILL, "text/plain", service_backend(), memory_for(t), today=TODAY
    )

    assert speaking.voucher.party == PARTY
    assert speaking.voucher.debit_account == "Purchases"


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


# =============================================================================
# PHASE 8 PR-1 — the typed-text backend refuses what it cannot read
# =============================================================================
#
# `TypedTextExtractor.extract` took its second parameter as `_mime` and threw
# it away. Measured on the five input types before the parameter was honoured:
#
#     %PDF-1.7 ...           total_paise = 170,     source "typed_text"
#     PNG with a tEXt chunk  total_paise = 420000,  source "typed_text"
#     JPEG with a COM        total_paise = 3133700, source "typed_text"
#
# None of those is a blank and none is a `not_found`. Each is an invented
# number carrying a real backend's name, which is the one failure this
# repository defines as worse than a refusal: a refusal is visible and asks the
# person a question, an invented total posts.
#
# A second case, on real published bytes: `"paid Café Ltd 4200 for supplies"`
# encoded cp1252 came back with `party == "Caf"`, sourced `typed_text` — the é
# became U+FFFD under `errors="replace"`, `_PARTY` stopped at it, and a
# TRUNCATED supplier name went on to `propose_account`, where a name that does
# not match history creates a new vendor.
#
# Neither could be fixed by choosing different fixtures. No arrangement of
# bytes on disk stops a money regex matching the digits in `%PDF-1.7`, and
# picking documents that happen to dodge it is how a corpus is tuned to pass.


TYPED_TEXT_REFUSALS = (
    (
        "a PDF",
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
        "application/pdf",
    ),
    ("a PNG", b"\x89PNG\r\n\x1a\n\x00\x00\x00\x10tEXtnote\x004200", "image/png"),
    ("a JPEG", b"\xff\xd8\xff\xfe\x00\x10invoice 31337\xff\xd9", "image/jpeg"),
    (
        "a DOCX",
        b"PK\x03\x04word/document.xml 4200",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("no media type at all", b"paid Sharma Traders 4200", ""),
)


@pytest.mark.parametrize(
    ("label", "data", "mime"),
    TYPED_TEXT_REFUSALS,
    ids=[c[0] for c in TYPED_TEXT_REFUSALS],
)
def test_the_typed_text_backend_invents_nothing_from_a_document(
    label: str, data: bytes, mime: str
) -> None:
    record = TypedTextExtractor().extract(data, mime)

    assert record.complete is True, label
    assert field_values(record) == dict.fromkeys(ExtractedRecord.FIELDS, None), label
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS), label
    for name in ExtractedRecord.FIELDS:
        reason = record.per_field_source[name]
        assert reason.startswith(f"{NOT_FOUND}: "), (label, name, reason)
        assert "typed_text" in reason, (label, name)


def test_a_refusal_by_media_type_still_names_the_backend_that_refused() -> None:
    """A row that cannot say WHICH backend declined is not evidence about any
    of them - the same argument `UnavailableExtractor` records for its `name`."""
    record = TypedTextExtractor().extract(b"%PDF-1.7\n", "application/pdf")

    assert record.backend == "typed_text"
    assert "application/pdf" in record.per_field_source["total_paise"]


def test_the_charset_parameter_is_not_a_reason_to_refuse_a_typed_sentence() -> None:
    """A real form sends `text/plain; charset=utf-8`. Refusing over eight
    trailing characters would be a refusal nobody could act on."""
    with_parameter = TypedTextExtractor().extract(BILL, "text/plain; charset=utf-8")
    plain = TypedTextExtractor().extract(BILL, "text/plain")

    assert with_parameter.total_paise == plain.total_paise == 420000
    assert with_parameter.party == plain.party == "Sharma Traders"


def test_bytes_that_are_not_utf8_are_refused_rather_than_half_decoded() -> None:
    """The cp1252 case, on the encoding the published DEFRA, DfT, DWP and
    MHCLG files actually use. `errors="replace"` returned party "Caf", which is
    not a missing supplier - it is a DIFFERENT supplier, and it is sourced."""
    cp1252 = "paid Café Ltd 4200 for supplies".encode("cp1252")

    record = TypedTextExtractor().extract(cp1252, "text/plain")

    assert record.party is None
    assert record.total_paise is None
    assert record.per_field_source["party"].startswith(f"{NOT_FOUND}: ")
    assert "not UTF-8" in record.per_field_source["party"]


def test_a_typed_sentence_with_real_accented_letters_still_reads() -> None:
    """The disconfirming case. A guard that refused every non-ASCII supplier
    would be deleted the first time somebody typed one, and the fix would be to
    put `errors="replace"` back."""
    record = TypedTextExtractor().extract(
        "paid Café Ltd 4200 for supplies".encode(), "text/plain"
    )

    assert record.total_paise == 420000
    assert record.party == "Café Ltd"


def test_the_refusal_reaches_the_draft_as_a_question_and_never_as_a_number() -> None:
    """The consequence, end to end. A PDF used to produce a draft carrying
    ₹1.70 that came from the string `%PDF-1.7`."""
    memory = memory_for(tally())

    draft = pipeline.build_draft(
        COMPANY,
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
        "application/pdf",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )

    assert draft.voucher.amount_paise == 0
    assert draft.voucher.party == ""


# ---------------------------------------------------------------------------
# EXIT 2 — a not_found that says nothing is a silent blank wearing the right key
# ---------------------------------------------------------------------------


def test_the_stub_states_why_a_field_was_not_read():
    """Owner EXIT 2: every unread field is explicit not_found WITH A REASON.

    The stub used to write a bare `not_found`. In an audit trail that makes
    "we have no reader at all" and "the reader looked and found nothing" the
    same string, and those are different facts about the document. The stub is
    the backend a JPG meets while owner decision Q4 = B holds, so this is the
    string that actually reaches the ground-truth pack's 20 unrenderable cases.
    """
    from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, StubExtractor

    record = StubExtractor().extract(b"anything at all", "image/jpeg")

    for name in ExtractedRecord.FIELDS:
        source = record.per_field_source[name]
        assert source.startswith(NOT_FOUND), (name, source)
        assert source.strip() != NOT_FOUND, f"{name} carries no reason"
        assert len(source) > len(NOT_FOUND) + 2, (name, source)


def test_a_field_the_stub_was_handed_still_names_the_stub_and_not_a_refusal():
    """The control. Widening the reason must not swallow a value that IS there."""
    import datetime

    from accountant.extract.adapter import NOT_FOUND, StubExtractor

    record = StubExtractor(date=datetime.date(2026, 8, 10)).extract(b"x", "text/plain")

    assert record.per_field_source["date"] == "stub"
    assert not record.per_field_source["date"].startswith(NOT_FOUND)
    assert record.per_field_source["party"].startswith(NOT_FOUND)


# =============================================================================
# THE FIRST NUMBER IS NOT THE AMOUNT
# =============================================================================
#
# `_AMOUNT.findall(text)[0]` took the FIRST number in the document. On a
# sentence a person typed that is the amount; on an invoice layout it is the
# invoice number. Measured on the committed corpus before this section existed,
# `.venv/bin/python scripts/run_ground_truth.py`:
#
#     20 of 20 text/plain cases returned a WRONG total_paise, sourced
#     "typed_text", with no confidence anywhere on the record.
#     GT-0001: TOTAL 147.50 on the bill, total_paise = 100, from "GT/0001".
#
# The media type could never have caught it. `text/plain` is `text/plain`
# whether a person typed one line or pasted a whole bill, and that conflation
# IS the defect - so what follows tests the SHAPE of the text and never the
# type it arrived as.

#: Every `text/plain` case in the committed pack. A `.txt` in that directory IS
#: a `text/plain` case - `build_ground_truth.py` writes one extension per input
#: type - so this cannot silently shrink to the cases that happen to pass.
CORPUS_TEXT_CASES = sorted(
    (REPO / "artifacts" / "ground_truth" / "documents").glob("GT-*.txt")
)

#: One corpus bill, verbatim. Six invoice signals: TAX INVOICE, INVOICE NO,
#: HSN/SAC, PLACE OF SUPPLY, a TOTAL line, and five label:value lines.
INVOICE_TEXT = (
    REPO / "artifacts" / "ground_truth" / "documents" / "GT-0001.txt"
).read_bytes()


def test_an_invoice_shaped_text_refuses_the_amount_in_the_owners_words() -> None:
    """GT-0001. It used to answer 100 paise for a bill of 14750."""
    record = TypedTextExtractor().extract(INVOICE_TEXT, "text/plain")

    assert record.total_paise is None
    assert record.per_field_source["total_paise"] == (
        f"{NOT_FOUND}: This document looks like an invoice, but the amount "
        "could not be reliably read. Please upload a clearer image or a "
        "proper PDF."
    )


@pytest.mark.parametrize("path", CORPUS_TEXT_CASES, ids=lambda p: p.stem)
def test_every_text_plain_corpus_case_refuses_instead_of_returning_a_number(
    path: pathlib.Path,
) -> None:
    """The measurement, pinned. 20 wrong extractions become 20 refusals.

    A value here is not a near miss to be tuned away later - it is a number
    with `typed_text` on it reaching the ledger, which this repository counts
    as worse than reading nothing.
    """
    assert len(CORPUS_TEXT_CASES) == 20, "the pack holds twenty text/plain cases"
    record = TypedTextExtractor().extract(path.read_bytes(), "text/plain")

    assert record.total_paise is None, path.stem
    assert record.tax_paise is None, path.stem
    assert record.per_field_source["total_paise"].startswith(f"{NOT_FOUND}: ")
    assert "looks like an invoice" in record.per_field_source["total_paise"]


def test_the_control_a_typed_sentence_is_not_invoice_shaped_and_still_reads() -> None:
    """THE CONTROL ON THE WHOLE SECTION. A refusal that refuses everything is
    not a fix, it is a deletion, and it would pass every test above."""
    record = TypedTextExtractor().extract(BILL, "text/plain")

    assert record.total_paise == 420000
    assert record.per_field_source["total_paise"] == "typed_text"


def test_a_total_line_on_its_own_is_one_signal_and_one_signal_is_not_a_layout() -> None:
    """THE THRESHOLD, pinned. `TOTAL 4200` fires exactly one invoice signal -
    the total line - and nothing else. A person typing that into the box is
    stating an amount, so one signal must not be enough to refuse.

    Written after the mutant `_SIGNALS_FOR_AN_INVOICE = 1` survived every other
    test in this section: the control below used `TOTAL` mid-sentence, which
    fires ZERO signals, so it never reached the threshold at all. Both sides of
    the boundary are here, so moving it either way goes red.
    """
    one = TypedTextExtractor().extract(b"TOTAL 4200", "text/plain")
    two = TypedTextExtractor().extract(b"TOTAL 4200\nHSN/SAC: 9954", "text/plain")

    assert one.total_paise == 420000
    assert two.total_paise is None
    assert "looks like an invoice" in two.per_field_source["total_paise"]


def test_the_word_total_inside_a_sentence_is_not_a_total_line_at_all() -> None:
    """The anchor, separately. A figure in a column begins its line; the same
    word in the middle of something somebody typed is a person talking."""
    record = TypedTextExtractor().extract(b"paid for the lot, TOTAL 4200", "text/plain")

    assert record.total_paise == 420000


def test_two_numbers_in_a_note_refuse_rather_than_taking_the_first() -> None:
    """Not invoice-shaped, so rule 1 does not fire - and picking the first of
    two is the same guess that produced the 20."""
    record = TypedTextExtractor().extract(
        b"paid Sharma Traders 1180 for cement plus 180 GST", "text/plain"
    )

    assert record.total_paise is None
    assert record.per_field_source["total_paise"] == (
        f"{NOT_FOUND}: Multiple numbers were found and the amount could not be "
        "determined. Please specify the amount explicitly or upload a clearer "
        "document."
    )


def test_the_control_a_rate_is_not_counted_as_a_second_number() -> None:
    """`18%` is a rate, not an amount. Counting it would refuse every GST
    sentence the product is built around, which is a fix that refuses
    everything wearing the right sentence."""
    record = TypedTextExtractor().extract(GST_BILL, "text/plain")

    assert record.total_paise == 420000
    assert record.tax_paise == 64068


def test_a_four_digit_year_alone_is_not_read_as_an_amount() -> None:
    """SANITY CHECK 1. One number in the text, and it is a year."""
    record = TypedTextExtractor().extract(b"software renewal 2026", "text/plain")

    assert record.total_paise is None
    assert "a year" in record.per_field_source["total_paise"]


def test_the_control_a_year_sized_amount_written_as_money_still_reads() -> None:
    """The escape hatch, and the disconfirming case for the year check: ₹2,000
    is an ordinary rent. Written as money it is money."""
    record = TypedTextExtractor().extract(b"paid Landlord Rs. 2000 rent", "text/plain")

    assert record.total_paise == 200000


def test_something_phone_shaped_is_not_read_as_an_amount() -> None:
    """SANITY CHECK 2. Ten bare digits is an Indian mobile number, and
    ₹98,76,543.21 is what reading it as money would post."""
    record = TypedTextExtractor().extract(b"call Sharma on 9876543210", "text/plain")

    assert record.total_paise is None
    assert "phone number" in record.per_field_source["total_paise"]


def test_something_id_shaped_is_not_read_as_an_amount() -> None:
    """SANITY CHECK 3. `GT/0001` is the string that produced the original 100
    paise, and it is still not an amount when it is the only number there."""
    record = TypedTextExtractor().extract(b"our reference GT/0001", "text/plain")

    assert record.total_paise is None
    assert "identifier" in record.per_field_source["total_paise"]


def test_a_refused_amount_is_never_a_guess_carrying_a_low_score() -> None:
    """The shape of every refusal above: no value, and a sentence saying why.

    `ExtractedRecord` has no confidence field, so 'confidence 0.0' is expressed
    the way `textlayer._field` already expresses it - a `None` value cannot
    carry a score, and the source string is where the reason lives.
    """
    for data in (INVOICE_TEXT, b"paid X 1180 plus 180", b"renewal 2026"):
        record = TypedTextExtractor().extract(data, "text/plain")

        assert record.total_paise is None, data[:30]
        assert record.per_field_source["total_paise"].startswith(f"{NOT_FOUND}: ")
        assert record.per_field_source["total_paise"].strip() != NOT_FOUND


# =============================================================================
# WHERE THE DETECTOR WAS WALKED AROUND, MEASURED 2026-08-13
# =============================================================================
#
# Everything above this line held. What did not hold is the sentence "it
# refuses invoice-shaped text", because "invoice-shaped" was not a property of
# the text - it was the name of one function's specific holes. Measured on the
# committed detector at dd96a26, through `pipeline.build_draft` +
# `pipeline.evaluate` + `app.render_decision`:
#
#     TAX INVOICE / Invoice 2451 / paid Sharma Traders as per order /
#     Amount: Rupees Four Thousand Two Hundred Only
#         -> outcome VALID, amount_paise 245100, provenance "typed_text"
#
# The bill says four thousand two hundred rupees, in words, the way a cash memo
# is written. 2451 is the invoice number. That is the same defect the section
# above closes, on a document whose FIRST LINE SAYS TAX INVOICE, and it posted.
#
# Three holes let it through, and each has its own test below:
#
#     a bare header line was worth one signal, and one is not enough
#     the invoice-number mark only matched "invoice no", never "Invoice 2451"
#     an identifier was only spotted when a separator GLUED it to a word

#: Four texts a person would call a bill on sight. Each returned the invoice,
#: bill or challan NUMBER as the amount, at the gate's full confidence.
WALKED_AROUND_THE_DETECTOR = (
    b"TAX INVOICE\nInvoice 2451\npaid Sharma Traders as per order\n"
    b"Amount: Rupees Four Thousand Two Hundred Only\n",
    b"TAX INVOICE\nInvoice 7788\nSharma Traders\n",
    b"SHARMA TRADERS\nTax Invoice\nBill 3097\n",
    b"DELIVERY CHALLAN\nChallan 6612\nSharma Traders\ngoods delivered\n",
)


@pytest.mark.parametrize("data", WALKED_AROUND_THE_DETECTOR)
def test_a_document_whose_own_first_line_calls_it_a_bill_is_a_layout(
    data: bytes,
) -> None:
    """A line that says nothing but INVOICE / BILL / CHALLAN is not somebody
    talking. Nobody types that line into a one-line box, and no sentence
    contains it, so on its own it settles the question the two-signal rule was
    invented to hedge."""
    record = TypedTextExtractor().extract(data, "text/plain")

    assert record.total_paise is None, data[:24]
    assert "looks like an invoice" in record.per_field_source["total_paise"]


def test_the_control_a_total_a_person_typed_is_not_a_header_line() -> None:
    """THE CONTROL ON THE HEADER RULE, and the one it must not break. `TOTAL
    4200` carries a word AND a figure, so it is not a bare header, and a rule
    that scored it as one would delete this backend's whole job."""
    typed = TypedTextExtractor().extract(b"TOTAL 4200", "text/plain")
    bulleted = TypedTextExtractor().extract(b"- total 4200", "text/plain")

    assert typed.total_paise == 420000
    assert bulleted.total_paise == 420000


def test_the_invoice_number_form_a_supplier_actually_prints_is_a_signal() -> None:
    """`invoice\\s*(?:no|number|#)` never matched `Invoice 2451`, which is the
    commonest form there is. Two signals here, neither of them a header line,
    so this goes red if the mark alone is dropped."""
    record = TypedTextExtractor().extract(
        b"Invoice 2451\nPLACE OF SUPPLY: GUJARAT\nAmount 4200\n", "text/plain"
    )

    assert record.total_paise is None
    assert "looks like an invoice" in record.per_field_source["total_paise"]


def test_a_total_line_is_a_total_line_behind_whatever_the_printer_put_first() -> None:
    """`^[ \\t|]*` scored zero on a leader or a rule character in front of the
    word. The decoration a supplier prints is not evidence about the line."""
    record = TypedTextExtractor().extract(
        b"..... TOTAL 4200\nGSTIN: 24ABCDE1234F1Z5\n", "text/plain"
    )

    assert record.total_paise is None
    assert "looks like an invoice" in record.per_field_source["total_paise"]


#: A number that follows one of these words is that identifier. No separator
#: glues any of them, so the positional check saw an ordinary amount.
LABELLED_IDENTIFIERS = (
    (b"HSN 998311 for Sharma Traders", b"HSN"),
    (b"Order 45231 from Sharma Traders", b"Order"),
    (b"cheque 887654 to Sharma Traders", b"cheque"),
    (b"invoice 4471 for repair charges", b"invoice"),
    (b"batch 0001 from Sharma", b"batch"),
)


@pytest.mark.parametrize(("data", "word"), LABELLED_IDENTIFIERS)
def test_the_word_in_front_of_a_number_says_it_is_not_an_amount(
    data: bytes, word: bytes
) -> None:
    """SANITY CHECK 3, semantic rather than positional. `GT/0001` was caught
    because of the slash; `HSN 998311` was ₹9,98,311 because of the space."""
    record = TypedTextExtractor().extract(data, "text/plain")

    assert record.total_paise is None, data
    assert word.decode() in record.per_field_source["total_paise"]


def test_the_control_a_currency_symbol_beats_the_identifier_words() -> None:
    """THE CONTROL ON THE IDENTIFIER RULE. Nobody writes a reference number
    with rupees in front of it, so `bill Rs 1200` is the bill's amount and
    refusing it would be the fix refusing the case it exists to serve."""
    record = TypedTextExtractor().extract(b"phone bill Rs 1200", "text/plain")

    assert record.total_paise == 120000


def test_ten_digits_is_a_phone_number_even_with_rupees_in_front_of_it() -> None:
    """SANITY CHECK 2, unreachable until now. `money_marked` short-circuited
    every check below it, so `Rs.` in front of a mobile number bought it
    ₹98,76,543.21 and a comma did the same on its own."""
    for data in (b"paid on Rs. 9876543210", b"transfer 9,876,543,210"):
        record = TypedTextExtractor().extract(data, "text/plain")

        assert record.total_paise is None, data
        assert "phone number" in record.per_field_source["total_paise"]


def test_the_control_the_year_check_still_lets_rent_written_as_money_through() -> None:
    """THE CONTROL ON THAT ORDER. The escape hatch was moved, not deleted:
    `Rs. 2000` is a rent and must not be read as a year."""
    record = TypedTextExtractor().extract(b"paid Landlord Rs. 2000 rent", "text/plain")

    assert record.total_paise == 200000


#: Ten ordinary sentences, which is this backend's entire job. Six of them
#: refused at dd96a26 because a date, a quantity or a reference counted as a
#: second number and RULE 3 fired. A refusal is not a wrong total, but a
#: backend that refuses the sentences it exists for has been deleted, not fixed.
ORDINARY_SENTENCES = (
    (b"paid Sharma Traders Rs 4200 on 12/08/2026", 420000),
    (b"bought 50 bags of cement from Sharma Traders for Rs 4200", 420000),
    (b"paid rent 15000 for August 2026", 1500000),
    (b"diesel 3500 for the truck on 5 Aug", 350000),
    (b"repair charges Rs. 900 invoice 4471", 90000),
    (b"paid Sharma Traders 4200 for cement", 420000),
)


@pytest.mark.parametrize(("data", "paise"), ORDINARY_SENTENCES)
def test_a_number_that_cannot_be_an_amount_is_not_a_rival_to_the_one_that_can(
    data: bytes, paise: int
) -> None:
    """RULE 3 counted every number, including the ones RULE 2 would have thrown
    out on sight. A date component and a reference number are not candidates
    for the total, so they cannot make the total ambiguous."""
    record = TypedTextExtractor().extract(data, "text/plain")

    assert record.total_paise == paise, record.per_field_source["total_paise"]
    assert record.per_field_source["total_paise"] == "typed_text"


def test_the_control_two_real_amounts_are_still_ambiguous() -> None:
    """THE CONTROL ON THAT WIDENING, and the one that keeps RULE 3 alive. Two
    numbers that could each be the total is the guess that produced the 20."""
    record = TypedTextExtractor().extract(
        b"paid Sharma Traders 1180 for cement plus 180 GST", "text/plain"
    )

    assert record.total_paise is None
    assert "Multiple numbers were found" in record.per_field_source["total_paise"]


def test_a_month_name_beside_a_number_too_big_to_be_a_day_is_not_a_date() -> None:
    """MEASURED after the date rule landed, and a refusal it caused.

    `salary 45000 August` is a salary for a month, not a date - 45000 is not a
    day of any month. The rule that lets `diesel 3500 on 5 Aug` read was
    excluding both of them, so a number is only part of a date when it is
    SHAPED like one: a day beside the month, or a year after it."""
    record = TypedTextExtractor().extract(b"salary 45000 August", "text/plain")

    assert record.total_paise == 4500000
    assert (
        TypedTextExtractor().extract(b"August 4200", "text/plain").total_paise == 420000
    )


def test_the_control_a_real_date_beside_a_month_name_is_still_a_date() -> None:
    """THE CONTROL. Narrowing the rule to day-shaped and year-shaped numbers
    must not put the price back in competition with the date."""
    day = TypedTextExtractor().extract(
        b"diesel 3500 for the truck on 5 Aug", "text/plain"
    )
    year = TypedTextExtractor().extract(
        b"paid rent 15000 for August 2026", "text/plain"
    )

    assert day.total_paise == 350000
    assert year.total_paise == 1500000
