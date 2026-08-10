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

THREE MORE, OVER REAL HTTP — the outage that used to be unreachable
-------------------------------------------------------------------
unavailable · timeout · malformed response, driven through a socket against the
running web app, with the failing backend injected at
`app.configure(extractor=...)`. Five properties each: explicit safe fallback,
the reason recorded, no silent blank, no unsafe VALID, no automatic post.

WHY THIS WAS BLOCKED UNTIL 2026-08-10, AND WHAT UNBLOCKED IT
-------------------------------------------------------------
    was      `accountant/web/app.py` named `TypedTextExtractor` directly, so
             the app could never reach a service at all.
    then     it called `registry.default_extractor()` INSIDE the request
             handler. The backend was chosen inside `accountant/extract/`, but
             it was chosen per request, so nothing could hand the RUNNING app a
             failing one. Recorded as BLOCKED rather than guessed at.
    now      `configure(extractor=...)` stores it on `Runtime`, `_run` uses
             `live.extractor`, and `registry.guarded()` turns a backend that
             RAISES into the same explicit outage record as one that reports a
             failure politely.

The two rejected routes are worth keeping written down. Monkey-patching
`DEFAULT_BACKEND` is patching a `Final` constant and proves something about the
patch. Instantiating a failing extractor inside the route would test a branch
that the shipped path does not have. Neither is used below.

The parameter names no backend — it is annotated with the `Extractor` Protocol
and defaults to `default_extractor()` — so exit 7.1's measured count of
selection sites outside the package is still `{}`.
`tests/test_adapter_contract.py` measures that, not this docstring.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That a real reader service fails in these ten ways and no eleventh. The
"service" here is a function this file wrote. What is proved is that the
adapter's failure surface is closed: an unplanned exception type still becomes
a record rather than a traceback, which is the property that makes the count
not matter.

That a real reader service was ever connected to the running app. It was not.
The HTTP scenarios inject a backend written in this file. What they prove is
that the ROUTE is safe whatever it is given, which is a claim about our code
and is the only claim the seam can support.

EVIDENCE CLASS
--------------
Behavioural, against `FakeTally` and against a transport written in this file.
The three HTTP scenarios are behavioural over a real socket against the shipped
`accountant/web/app.py`.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from accountant import pipeline
from accountant.extract import registry
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    Extractor,
    TypedTextExtractor,
)
from accountant.extract.registry import GuardedExtractor, guarded
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
from accountant.web import app
from tests.test_web import demo_company, fake_backend, get, post_for_status, serving

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


# =============================================================================
# THE SAME OUTAGE, OVER REAL HTTP — three scenarios, five properties each
# =============================================================================
#
# BLOCKED until 2026-08-10 and now measured. The seam is
# `app.configure(extractor=...)`; the route reads `Runtime.extractor` instead of
# building one per request; `registry.guarded()` closes the failure surface of
# whatever it is given.
#
# TWO KINDS OF FAILING BACKEND ARE USED HERE, DELIBERATELY:
#
#   a backend that RAISES        `BackendThatRaises` below. It breaks the
#                                never-raise convention on purpose, because a
#                                third-party object injected through the seam
#                                promises nothing. This is the case that used
#                                to reach `handle_one_request`'s catch-all and
#                                render "Something in Accountant Dad broke".
#   a backend that REPORTS       `ServiceExtractor` with a broken transport.
#                                It keeps its promise and returns an outage
#                                record. This is the shape a real deployment
#                                has, and it must survive HTTP too.
#
# Using only the first would prove the guard and say nothing about the shipped
# adapter. Using only the second would leave the guard unexercised on the HTTP
# path, which is the same as not having measured it.


class BackendThatRaises:
    """A backend that does NOT follow the never-raise rule. On purpose.

    `ServiceExtractor` promises `extract` never raises. Nothing enforces that
    promise on an object a deployment injects — the `Extractor` Protocol says
    only that `extract` exists — so this is what the seam has to survive.

    `name` is set, because an outage row that cannot say which backend was down
    is not evidence about any of them.
    """

    name = "acme_reader"

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
        raise self._failure


@dataclass(frozen=True)
class HttpOutage:
    """One way the backend behind the running web app can fail."""

    label: str
    make: Callable[[], Extractor]
    says: str


#: The three the brief names. Two arrive by raising, one by reporting.
HTTP_OUTAGES: tuple[HttpOutage, ...] = (
    HttpOutage(
        "unavailable",
        lambda: BackendThatRaises(ConnectionError("the reader host is down")),
        UNAVAILABLE,
    ),
    HttpOutage(
        "timeout",
        lambda: BackendThatRaises(TimeoutError("no answer in 30s")),
        TIMED_OUT,
    ),
    HttpOutage(
        "malformed response",
        lambda: service_saying("<html>502 Bad Gateway</html>"),
        MALFORMED,
    ),
)

HTTP_CASES = pytest.mark.parametrize("outage", HTTP_OUTAGES, ids=lambda o: o.label)

#: One row of the "Where each field came from" table. The `<code>` is what
#: separates a provenance row from a voucher row, which has no code element.
PROVENANCE_ROW = re.compile(r"<tr><td>([a-z_]+)</td><td><code>([^<]*)</code></td></tr>")


@dataclass(frozen=True)
class OverHttp:
    """Everything one HTTP outage produced, captured before the server closed."""

    status: int
    body: str
    home: str
    draft: pipeline.Draft
    before: dict[str, int]
    after: dict[str, int]
    ours: tuple[Voucher, ...]
    vouchers_before: int
    vouchers_after: int

    @property
    def shown_provenance(self) -> dict[str, str]:
        return dict(PROVENANCE_ROW.findall(self.body))


def drive(outage: HttpOutage) -> OverHttp:
    """Stand the app up on this failing backend, type one bill, watch.

    The trial balance is read on both sides of the request, from the same
    `FakeTally` the app is holding, so "nothing moved" is a comparison of two
    measurements rather than an assumption about a code path.

    The durable action log is NOT read here. `MemoryStore` opens its SQLite
    connection on the serving thread and SQLite hands a connection to the
    thread that opened it, so reading it from the test thread would fail for a
    reason that has nothing to do with outages. The home page is fetched
    instead: it renders the log ON that thread, which is stronger evidence
    anyway — it is what the person sees.
    """
    with serving(demo_company(), fake_backend(), extractor=outage.make()) as base:
        live = app.runtime()
        company = live.company
        before = live.client.trial_balance(company)
        vouchers_before = len(live.client.read_vouchers(company))

        status, body = post_for_status(base, "/entry", text=BILL.decode())

        drafts = list(app.DRAFTS.values())
        assert len(drafts) == 1, f"expected one draft, saw {len(drafts)}"
        return OverHttp(
            status=status,
            body=body,
            home=get(base),
            draft=drafts[0],
            before=before,
            after=live.client.trial_balance(company),
            ours=live.client.list_our_vouchers(company),
            vouchers_before=vouchers_before,
            vouchers_after=len(live.client.read_vouchers(company)),
        )


def test_the_three_http_outage_scenarios_the_brief_names_are_all_covered() -> None:
    """A table with two rows would pass every parametrized test below."""
    assert len(HTTP_OUTAGES) == 3
    assert {o.label for o in HTTP_OUTAGES} == {
        "unavailable",
        "timeout",
        "malformed response",
    }
    assert len({o.says for o in HTTP_OUTAGES}) == 3
    assert {o.says for o in HTTP_OUTAGES} <= set(ALL_REASONS)


# ---- 1. explicit safe fallback ----------------------------------------------


@HTTP_CASES
def test_every_http_outage_falls_back_to_an_explicit_all_not_found_record(
    outage: HttpOutage,
) -> None:
    """The fallback is EXPLICIT: four fields, four stated not_founds, no guess.

    A backend that raised produced no record at all before this seam existed;
    the request died in `handle_one_request`'s catch-all.
    """
    seen = drive(outage)
    record = seen.draft.record

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert (record.date, record.party, record.total_paise, record.tax_paise) == (
        None,
        None,
        None,
        None,
    )
    assert record.complete is True


# ---- 2. the reason is recorded, and it is this outage's own ------------------


@HTTP_CASES
def test_every_http_outage_records_its_own_reason_where_a_person_can_see_it(
    outage: HttpOutage,
) -> None:
    """Stored, on the draft, AND on the page. Three places, because a reason
    that stops at the record reaches nobody, and this is the surface the person
    is actually looking at."""
    seen = drive(outage)
    shown = seen.shown_provenance

    for name in ExtractedRecord.FIELDS:
        source = seen.draft.record.per_field_source[name]
        assert source.startswith(f"{NOT_FOUND}: "), name
        assert outage.says in source, f"{name} does not say why: {source!r}"
        assert outage.says in seen.draft.provenance[name], name
        assert outage.says in shown.get(name, ""), (
            f"the page does not tell the person why {name} is missing: "
            f"{shown.get(name)!r}"
        )


@HTTP_CASES
def test_every_http_outage_names_the_backend_that_failed(outage: HttpOutage) -> None:
    """`unknown` on an outage row is a row nobody can act on."""
    seen = drive(outage)

    assert seen.draft.record.backend in {"acme_reader", "reader_service"}
    assert seen.draft.record.backend != "unknown"


@HTTP_CASES
def test_every_http_outage_leaves_one_durable_row_the_activity_log_shows(
    outage: HttpOutage,
) -> None:
    """The log is rendered by the serving thread off `MemoryStore`, so a row on
    the home page is a row that was persisted."""
    seen = drive(outage)

    assert 'data-outcome="unclear"' in seen.home, (
        "the entry left no visible row in the activity log"
    )


# ---- 3. no silent blank ------------------------------------------------------


@HTTP_CASES
def test_no_http_outage_leaves_a_single_silent_blank(outage: HttpOutage) -> None:
    """A blank source is worse than a wrong one: nothing is there to question.

    Checked on the record AND on the rendered page, because "the source was
    stored" and "the person can see it" are two properties and the screen is
    the one that matters to them.
    """
    seen = drive(outage)
    record = seen.draft.record

    assert all(source.strip() for source in record.per_field_source.values())
    assert all(source != NOT_FOUND for source in record.per_field_source.values()), (
        "an unexplained not_found is a blank with a label on it"
    )
    shown = seen.shown_provenance
    assert set(shown) >= set(ExtractedRecord.FIELDS), (
        f"the page dropped a field from the provenance table: {sorted(shown)}"
    )
    assert all(value.strip() for value in shown.values()), (
        f"the page rendered an empty provenance cell: {shown}"
    )
    assert "<code></code>" not in seen.body


# ---- 4. no unsafe VALID ------------------------------------------------------


@HTTP_CASES
def test_no_http_outage_reaches_valid(outage: HttpOutage) -> None:
    """VALID means "post this". Nothing was read, so nothing may be posted."""
    seen = drive(outage)

    assert seen.draft.outcome is not Outcome.VALID
    assert seen.draft.outcome is Outcome.UNCLEAR
    assert 'class="badge b-valid"' not in seen.body


@HTTP_CASES
def test_every_http_outage_is_answered_rather_than_reported_as_a_breakage(
    outage: HttpOutage,
) -> None:
    """The failure this seam was built for. A backend that RAISES used to reach
    the handler's catch-all, and the person was told the application broke -
    for an ordinary bill whose only problem was somebody else's outage.

    Failing safely and failing legibly are two properties. This is the second.
    """
    seen = drive(outage)

    assert seen.status == 200
    assert "broke" not in seen.body.lower()
    assert "Traceback" not in seen.body


@HTTP_CASES
def test_after_an_http_outage_the_person_is_asked_something_answerable(
    outage: HttpOutage,
) -> None:
    """The whole point of failing this way: the work still gets done by hand."""
    seen = drive(outage)
    question = pipeline.next_question(seen.draft)

    assert question is not None
    assert question.answers, "a question with no answers is a dead end"
    assert "<div class=opts>" in seen.body


# ---- 5. no automatic post ----------------------------------------------------


@HTTP_CASES
def test_no_http_outage_posts_anything_or_moves_one_paise(outage: HttpOutage) -> None:
    seen = drive(outage)

    assert seen.draft.posted_tally_id is None
    assert seen.ours == ()
    assert seen.vouchers_after == seen.vouchers_before
    assert seen.after == seen.before


# =============================================================================
# THE COMPLETE OUTAGE MATRIX — the numbers, counted rather than described
# =============================================================================


def test_the_complete_outage_matrix_is_thirteen_scenarios_and_every_one_is_safe() -> (
    None
):
    """Ten through the pipeline, three through the running web app.

    One test that COUNTS, next to the parametrized ones that assert. The
    parametrized tests fail one case at a time and say which; this one reports
    the figure the exit is written in, so "13/13" is a number somebody
    measured rather than a number somebody added up by hand from a test list.
    """
    explicit_fallback = 0
    reasons_recorded = 0
    silent_blanks = 0
    unsafe_valid = 0
    automatic_posts = 0

    for outage in OUTAGES:
        t = tally()
        before = t.trial_balance(COMPANY)
        d = pipeline.run(
            COMPANY, BILL, "text/plain", outage.make(), t, memory_for(t), today=TODAY
        )
        sources = d.record.per_field_source
        if not_found_fields(d.record) == set(ExtractedRecord.FIELDS):
            explicit_fallback += 1
        if all(outage.says in sources[f] for f in ExtractedRecord.FIELDS):
            reasons_recorded += 1
        silent_blanks += sum(
            1 for s in sources.values() if not s.strip() or s == NOT_FOUND
        )
        unsafe_valid += d.outcome is Outcome.VALID
        automatic_posts += (
            d.posted_tally_id is not None or t.trial_balance(COMPANY) != before
        )

    for outage in HTTP_OUTAGES:
        seen = drive(outage)
        sources = seen.draft.record.per_field_source
        if not_found_fields(seen.draft.record) == set(ExtractedRecord.FIELDS):
            explicit_fallback += 1
        if all(outage.says in sources[f] for f in ExtractedRecord.FIELDS):
            reasons_recorded += 1
        silent_blanks += sum(
            1 for s in sources.values() if not s.strip() or s == NOT_FOUND
        )
        unsafe_valid += seen.draft.outcome is Outcome.VALID
        automatic_posts += (
            seen.draft.posted_tally_id is not None
            or bool(seen.ours)
            or seen.after != seen.before
        )

    measured = {
        "scenarios": len(OUTAGES) + len(HTTP_OUTAGES),
        "explicit_fallback": explicit_fallback,
        "reasons_recorded": reasons_recorded,
        "silent_blanks": silent_blanks,
        "unsafe_valid": unsafe_valid,
        "automatic_posts": automatic_posts,
    }

    assert measured == {
        "scenarios": 13,
        "explicit_fallback": 13,
        "reasons_recorded": 13,
        "silent_blanks": 0,
        "unsafe_valid": 0,
        "automatic_posts": 0,
    }


# =============================================================================
# THE GUARD ITSELF — the two ways a backend can fail the application
# =============================================================================


def test_a_backend_that_raises_becomes_an_outage_record_instead_of_an_exception() -> (
    None
):
    """`pipeline.build_draft` has no try around `extract`. Without the guard,
    this exception is an HTTP 503 blaming the application."""
    record = guarded(BackendThatRaises(ConnectionError("host is down"))).extract(
        BILL, "text/plain"
    )

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert UNAVAILABLE in record.per_field_source["party"]
    assert record.backend == "acme_reader"


def test_a_backend_that_answers_with_something_that_is_not_a_record_is_refused() -> (
    None
):
    """The other half, and the commoner one: a third-party client that returns
    `None` on failure. Unguarded, that reaches `record.per_field_source` as an
    AttributeError two frames later, which is a 503 with no reason on it."""

    class BackendThatAnswersWithNothing:
        name = "acme_reader"

        def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
            return None  # type: ignore[return-value]

    record = guarded(BackendThatAnswersWithNothing()).extract(BILL, "text/plain")

    assert not_found_fields(record) == set(ExtractedRecord.FIELDS)
    assert MALFORMED in record.per_field_source["date"]
    assert "NoneType" in record.per_field_source["date"]


def test_the_guard_does_not_touch_a_backend_that_works() -> None:
    """A guard that changed a good answer would be a bug wearing safety
    clothing. Same record, field for field, source for source."""
    plain = TypedTextExtractor().extract(BILL, "text/plain")
    through = guarded(TypedTextExtractor()).extract(BILL, "text/plain")

    assert through == plain


def test_stopping_the_process_is_not_swallowed_by_the_guard_either() -> None:
    """Same rule as `ServiceExtractor`: a KeyboardInterrupt is somebody
    stopping the run, and a tidy record would fight them."""
    with pytest.raises(KeyboardInterrupt):
        guarded(BackendThatRaises(KeyboardInterrupt())).extract(BILL, "text/plain")


def test_the_running_app_uses_the_extractor_it_was_configured_with() -> None:
    """The seam, asserted directly rather than only through its consequences.

    Without this, every HTTP test above could pass because the DEFAULT backend
    happens to fail on this input, and nobody would know the injected one was
    ignored.
    """
    injected = BackendThatRaises(TimeoutError("no answer in 30s"))

    with serving(demo_company(), fake_backend(), extractor=injected) as base:
        live = app.runtime()

        assert live.extractor is not injected, "the raw backend was stored unguarded"
        assert isinstance(live.extractor, GuardedExtractor)
        assert live.extractor.name == "acme_reader"

        post_for_status(base, "/entry", text=BILL.decode())
        record = next(iter(app.DRAFTS.values())).record

    assert record.backend == "acme_reader"
    assert TIMED_OUT in record.per_field_source["party"]


def test_the_app_still_chooses_its_own_backend_when_none_is_injected() -> None:
    """The default path, unchanged. `configure()` with no extractor must go on
    resolving `registry.default_extractor()`, or exit 7.1's one-line swap stops
    being the thing that decides what production reads with."""
    with serving(demo_company(), fake_backend()) as base:
        chosen = app.runtime().extractor

        assert isinstance(chosen, GuardedExtractor)
        assert chosen.name == registry.DEFAULT_BACKEND

        status, body = post_for_status(base, "/entry", text=BILL.decode())

    assert status == 200
    assert 'class="badge b-valid"' in body, (
        "a known vendor typed in plainly should still post straight through"
    )
