"""Phase 7 exit 7.2 — the reader service is down, and nothing bad happens.

TEN WAYS IT CAN FAIL, ONE PLACE THE PERSON LANDS
------------------------------------------------
unavailable · timeout · malformed response · partial response · authentication
failure · rate limit · empty response · connection refused · a response about a
different document · a response missing the named fields.

For every one of them, seven things must hold:

    1. every named field comes back explicitly not_found
    2. the reason is stored on the record AND visible on the draft
    3. no field is silently blank
    4. nothing raises — the system continues
    5. the person can type the entry in instead, and that entry posts
    6. no automatic Tally post happens
    7. zero vouchers on the register and the trial balance unchanged, in exact
       paise

Seven, asserted separately, because "it failed safely" is four different
properties wearing one sentence, and only one of them is about not crashing.

WHY THE PARTIAL RESPONSE ALSO COMES BACK ENTIRELY NOT_FOUND
------------------------------------------------------------
It is the design and not an accident of the assertion. A field the service says
it could not find comes back `null`, which is an ANSWER, and becomes an
explicit not_found for that one field — that case is in
`tests/test_adapter_contract.py` and it posts perfectly well. A field the
service does not mention at all is a different thing: nothing distinguishes
"not on the bill" from "the service stopped halfway", and those two mean
opposite things to the person reading the screen. So the whole answer is
refused rather than half of it trusted. `accountant/extract/service.py` states
the rule and the reasoning at the top.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That a real reader service fails in these ten ways and no eleventh. The
"service" here is a function this file wrote. What is proved is that the
adapter's failure surface is closed: an unplanned exception type still becomes
a record rather than a traceback, which is the property that makes the count
not matter.

That the WEB app survives a reader outage. `accountant/web/app.py` constructs
`TypedTextExtractor` directly and never reaches a service, so there is no HTTP
path to drive here. That import is the one selection site
`tests/test_adapter_contract.py` measures; until it points at
`accountant/extract/registry.py`, an outage of a reader service is not
reachable over HTTP by anybody, including a test.

EVIDENCE CLASS
--------------
Behavioural, against `FakeTally` and against a transport written in this file.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from accountant import pipeline
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, TypedTextExtractor
from accountant.extract.service import (
    ALL_REASONS,
    DOCUMENT_KEY,
    EMPTY,
    INCOMPLETE,
    MALFORMED,
    NOT_SIGNED_IN,
    RATE_LIMITED,
    REFUSED,
    TIMED_OUT,
    UNAVAILABLE,
    UNIDENTIFIED,
    WRONG_DOCUMENT,
    ExtractionFailed,
    ServiceExtractor,
    document_key,
    reason_for,
)
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)
PARTY = "Sharma Traders"
TOTAL = 420000
BILL = b"paid Sharma Traders 4200 for cement"
SOME_OTHER_BILL = b"paid Verma Cement 9900 for sand"


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


def tally() -> FakeTally:
    """A company whose books make the typed fallback below post without asking."""
    t = FakeTally()
    t.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(past(PARTY, "Purchases", n=40)),
        backed_up=True,
    )
    return t


def memory_for(t: FakeTally) -> CompanyMemory:
    return bootstrap(t, COMPANY, MemoryStore(":memory:"))


# ---- a service that fails on purpose ----------------------------------------


def service_saying(payload: object) -> ServiceExtractor:
    def call(_data: bytes, _mime: str, _key: str) -> object:
        return payload

    return ServiceExtractor(call)


def service_raising(
    exc: BaseException, name: str = "reader_service"
) -> ServiceExtractor:
    def call(_data: bytes, _mime: str, _key: str) -> object:
        raise exc

    return ServiceExtractor(call, name=name)


def whole_answer(data: bytes, **fields: object) -> dict[str, object]:
    return {
        DOCUMENT_KEY: document_key(data),
        "date": None,
        "party": None,
        "total_paise": None,
        "tax_paise": None,
    } | fields


@dataclass(frozen=True)
class Outage:
    """One way the reading service can fail, and the sentence it must produce."""

    label: str
    make: Callable[[], ServiceExtractor]
    says: str


def partial_answer() -> dict[str, object]:
    """The service named the vendor and then stopped."""
    answer = whole_answer(BILL, party=PARTY)
    del answer["total_paise"]
    del answer["tax_paise"]
    return answer


def answer_in_the_services_own_words() -> dict[str, object]:
    """A well-formed answer that names none of the four fields we asked about."""
    return {DOCUMENT_KEY: document_key(BILL), "vendor": PARTY, "amount": "4200.00"}


#: The ten. Order follows the brief so the two lists can be read side by side.
OUTAGES: tuple[Outage, ...] = (
    Outage(
        "unavailable",
        lambda: service_raising(ConnectionError("host is down")),
        UNAVAILABLE,
    ),
    Outage(
        "timeout", lambda: service_raising(TimeoutError("no answer in 30s")), TIMED_OUT
    ),
    Outage(
        "malformed response",
        lambda: service_saying("<html>502 Bad Gateway</html>"),
        MALFORMED,
    ),
    Outage("partial response", lambda: service_saying(partial_answer()), INCOMPLETE),
    Outage(
        "authentication failure",
        lambda: service_raising(ExtractionFailed(NOT_SIGNED_IN)),
        NOT_SIGNED_IN,
    ),
    Outage(
        "rate limit",
        lambda: service_raising(ExtractionFailed(RATE_LIMITED)),
        RATE_LIMITED,
    ),
    Outage("empty response", lambda: service_saying({}), EMPTY),
    Outage(
        "connection refused",
        lambda: service_raising(ConnectionRefusedError(61, "Connection refused")),
        REFUSED,
    ),
    Outage(
        "response for a different document",
        lambda: service_saying(
            whole_answer(SOME_OTHER_BILL, party=PARTY, total_paise=TOTAL)
        ),
        WRONG_DOCUMENT,
    ),
    Outage(
        "response missing the named fields",
        lambda: service_saying(answer_in_the_services_own_words()),
        INCOMPLETE,
    ),
)

CASES = pytest.mark.parametrize("outage", OUTAGES, ids=lambda o: o.label)


def not_found_fields(record: ExtractedRecord) -> set[str]:
    return {
        name
        for name, source in record.per_field_source.items()
        if source.startswith(NOT_FOUND)
    }


# =============================================================================
# THE TEN SCENARIOS ARE TEN
# =============================================================================


def test_the_ten_outage_scenarios_the_brief_names_are_all_covered() -> None:
    """A table with nine rows would pass every parametrized test below."""
    assert len(OUTAGES) == 10
    assert len({o.label for o in OUTAGES}) == 10
    assert {o.says for o in OUTAGES} <= set(ALL_REASONS)


def test_two_different_outages_never_produce_the_same_record() -> None:
    """If they did, the reason would be decoration and nobody could act on it."""
    reasons = {
        o.make().extract(BILL, "text/plain").per_field_source["party"] for o in OUTAGES
    }

    assert len(reasons) == 10


# =============================================================================
# 1. EVERY FIELD EXPLICITLY NOT_FOUND
# =============================================================================


@CASES
def test_every_outage_leaves_every_named_field_explicitly_not_found(
    outage: Outage,
) -> None:
    record = outage.make().extract(BILL, "text/plain")

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert (record.date, record.party, record.total_paise, record.tax_paise) == (
        None,
        None,
        None,
        None,
    )


# =============================================================================
# 2. THE REASON IS STORED, AND IT IS THIS OUTAGE'S OWN REASON
# =============================================================================


@CASES
def test_every_outage_stores_its_own_reason_on_every_field(outage: Outage) -> None:
    record = outage.make().extract(BILL, "text/plain")

    for name in ExtractedRecord.FIELDS:
        source = record.per_field_source[name]
        assert source.startswith(f"{NOT_FOUND}: "), name
        assert outage.says in source, f"{name} does not say why: {source!r}"


@CASES
def test_every_outage_reason_is_visible_on_the_draft_the_person_is_shown(
    outage: Outage,
) -> None:
    """Stored is not the same as visible. `Draft.provenance` is what the screen
    reads, and a reason that stops at the record never reaches anybody."""
    t = tally()
    draft = pipeline.build_draft(
        COMPANY, BILL, "text/plain", outage.make(), memory_for(t), today=TODAY
    )

    assert set(draft.provenance) >= set(ExtractedRecord.FIELDS)
    assert all(outage.says in draft.provenance[f] for f in ExtractedRecord.FIELDS)
    assert draft.voucher.provenance is not None
    assert outage.says in draft.voucher.provenance["total_paise"]


# =============================================================================
# 3. ZERO SILENT BLANKS
# =============================================================================


@CASES
def test_no_outage_leaves_a_single_silent_blank(outage: Outage) -> None:
    """A blank source is worse than a wrong one: nothing is there to question."""
    record = outage.make().extract(BILL, "text/plain")

    assert record.complete is True
    assert all(source.strip() for source in record.per_field_source.values())
    assert all(source != NOT_FOUND for source in record.per_field_source.values()), (
        "an unexplained not_found is a blank with a label on it"
    )


# =============================================================================
# 4. NOTHING RAISES — THE SYSTEM CONTINUES
# =============================================================================


@CASES
def test_no_outage_raises_out_of_the_extractor(outage: Outage) -> None:
    record = outage.make().extract(BILL, "text/plain")

    assert isinstance(record, ExtractedRecord)


@CASES
def test_no_outage_raises_out_of_the_pipeline(outage: Outage) -> None:
    """`build_draft` raising is a 503 page for somebody whose only problem is
    that a supplier's website is down."""
    t = tally()
    d = pipeline.run(
        COMPANY, BILL, "text/plain", outage.make(), t, memory_for(t), today=TODAY
    )

    assert d.decision is not None
    assert d.outcome in {Outcome.UNCLEAR, Outcome.NOT_VALID}


# =============================================================================
# 5. THE PERSON CAN TYPE THE ENTRY INSTEAD
# =============================================================================


@CASES
def test_after_any_outage_the_person_is_asked_something_they_can_answer(
    outage: Outage,
) -> None:
    t = tally()
    d = pipeline.run(
        COMPANY, BILL, "text/plain", outage.make(), t, memory_for(t), today=TODAY
    )
    question = pipeline.next_question(d)

    assert d.outcome is Outcome.UNCLEAR
    assert question is not None
    assert question.answers, "a question with no answers is a dead end"


@CASES
def test_after_any_outage_the_person_can_type_the_entry_and_it_posts(
    outage: Outage,
) -> None:
    """The whole point of failing this way: the work still gets done by hand."""
    t = tally()
    before = t.trial_balance(COMPANY)
    pipeline.run(
        COMPANY, BILL, "text/plain", outage.make(), t, memory_for(t), today=TODAY
    )

    assert t.trial_balance(COMPANY) == before

    typed = pipeline.run(
        COMPANY, BILL, "text/plain", TypedTextExtractor(), t, memory_for(t), today=TODAY
    )

    assert typed.outcome is Outcome.VALID
    assert typed.posted_tally_id is not None
    assert t.trial_balance(COMPANY) == {
        **before,
        "Purchases": before["Purchases"] + TOTAL,
        "Cash": before["Cash"] - TOTAL,
    }


# =============================================================================
# 6 and 7. NO AUTOMATIC POST, NO VOUCHER, NOT ONE PAISE OF MOVEMENT
# =============================================================================


@CASES
def test_no_outage_writes_a_voucher_or_moves_the_trial_balance(outage: Outage) -> None:
    t = tally()
    before = t.trial_balance(COMPANY)
    vouchers_before = len(t.read_vouchers(COMPANY))

    d = pipeline.run(
        COMPANY, BILL, "text/plain", outage.make(), t, memory_for(t), today=TODAY
    )

    assert d.posted_tally_id is None
    assert t.list_our_vouchers(COMPANY) == ()
    assert len(t.read_vouchers(COMPANY)) == vouchers_before
    assert t.trial_balance(COMPANY) == before
    assert t.read_by_operation_id(COMPANY, d.operation_id) is None


@CASES
def test_every_outage_is_written_to_the_durable_action_log_with_its_reason(
    outage: Outage,
) -> None:
    """An outage nobody can find afterwards is an outage that gets argued about."""
    t = tally()
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)
    pipeline.run(
        COMPANY,
        BILL,
        "text/plain",
        outage.make(),
        t,
        memory,
        today=TODAY,
        log=store,
        run_id="phase7-outage",
    )
    rows = store.actions(COMPANY)

    assert rows, "the outage left no durable row at all"
    assert [r.action for r in rows] == ["blocked"]
    assert rows[0].reason.strip()
    assert rows[0].outcome == Outcome.UNCLEAR.value


# =============================================================================
# THE FAILURE SURFACE IS CLOSED
# =============================================================================


def test_a_refused_connection_is_not_reported_as_a_generic_outage() -> None:
    """`ConnectionRefusedError` subclasses `ConnectionError`. Checked in the
    wrong order, every refusal reads as "try again later" when the real answer
    is that nothing is listening on that port."""
    assert reason_for(ConnectionRefusedError(61, "Connection refused")) == REFUSED
    assert reason_for(ConnectionError("host is down")) == UNAVAILABLE
    assert REFUSED != UNAVAILABLE


def test_a_permission_error_reads_as_not_being_signed_in() -> None:
    assert reason_for(PermissionError("401 Unauthorized")) == NOT_SIGNED_IN


def test_an_unplanned_exception_from_a_transport_still_gives_a_record() -> None:
    """The eleventh way. The count of known failures is not what keeps this safe."""
    record = service_raising(ZeroDivisionError("a bug in somebody's SDK")).extract(
        BILL, "text/plain"
    )

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert "ZeroDivisionError" in record.per_field_source["party"]


def test_stopping_the_process_is_not_swallowed_as_an_outage() -> None:
    """`KeyboardInterrupt` is somebody stopping the run. Turning it into a tidy
    not_found record would fight them."""
    with pytest.raises(KeyboardInterrupt):
        service_raising(KeyboardInterrupt()).extract(BILL, "text/plain")

    # The state assertion: an ordinary failure on the same transport still
    # becomes a record, so the guard above is narrow rather than a blanket.
    assert not_found_fields(
        service_raising(TimeoutError()).extract(BILL, "text/plain")
    ) == set(ExtractedRecord.FIELDS)


# =============================================================================
# THE FOUR RESPONSE-SHAPE FAILURES, EACH SAYING SOMETHING DIFFERENT
# =============================================================================


def test_a_partial_answer_names_the_fields_the_service_left_out() -> None:
    record = service_saying(partial_answer()).extract(BILL, "text/plain")
    reason = record.per_field_source["party"]

    assert INCOMPLETE in reason
    assert "total_paise, tax_paise" in reason
    assert "date" not in reason.removeprefix(f"{NOT_FOUND}: ")


def test_an_answer_in_the_services_own_field_names_reports_all_four_missing() -> None:
    """Different from a partial answer, and it must read differently: the
    service is answering a question we did not ask."""
    record = service_saying(answer_in_the_services_own_words()).extract(
        BILL, "text/plain"
    )
    reason = record.per_field_source["party"]

    assert INCOMPLETE in reason
    assert "date, party, total_paise, tax_paise" in reason
    assert PARTY not in reason, "a field we cannot account for must not leak a value"


def test_an_answer_about_a_different_bill_says_which_one_we_asked_about() -> None:
    record = service_saying(
        whole_answer(SOME_OTHER_BILL, party=PARTY, total_paise=TOTAL)
    ).extract(BILL, "text/plain")
    reason = record.per_field_source["total_paise"]

    assert WRONG_DOCUMENT in reason
    assert document_key(BILL) in reason
    assert document_key(SOME_OTHER_BILL) in reason
    assert record.total_paise is None, "a value from the wrong bill was kept"


def test_an_answer_that_cannot_say_which_bill_it_is_about_is_refused() -> None:
    """Without the echo, "is this about our bill" has no answer, and the
    wrong-document case above could not be detected at all."""
    nameless = whole_answer(BILL, party=PARTY, total_paise=TOTAL)
    del nameless[DOCUMENT_KEY]

    record = service_saying(nameless).extract(BILL, "text/plain")

    assert UNIDENTIFIED in record.per_field_source["party"]
    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)


def test_an_empty_answer_is_not_reported_as_a_partial_one() -> None:
    """`{}` is missing all four fields too. It means the service had nothing to
    say, which is a different thing to tell somebody than "it stopped halfway"."""
    record = service_saying({}).extract(BILL, "text/plain")

    assert EMPTY in record.per_field_source["date"]
    assert INCOMPLETE not in record.per_field_source["date"]


def test_the_outage_record_says_which_backend_was_down() -> None:
    """A row that cannot name the backend is not evidence about any of them."""
    record = service_raising(TimeoutError(), name="acme_reader").extract(
        BILL, "text/plain"
    )

    assert record.backend == "acme_reader"
    assert TIMED_OUT in record.per_field_source["date"]
