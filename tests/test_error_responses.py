"""Owner items 15-16. Everything Tally says is UNTRUSTED INPUT.

Tally's answers arrive over a socket from a program nobody here controls, in a
version nobody here pinned, in a licence mode this gateway will not disclose.
So an answer is evidence of exactly what it says and of nothing adjacent to it.
The three collapses this file exists to prevent:

    a refusal read as data          "Tally would not answer" becoming "this
                                    company has no vouchers", which disables the
                                    duplicate guard and puts two statutory
                                    entries in somebody's books
    transport read as accounting    HTTP 200 taken as "the voucher is stored"
    unreadable read as absent       a body we cannot parse taken as proof the
                                    voucher is not there, which invites a retry

THE FIVE THINGS EVERY FAILURE CASE ASSERTS
------------------------------------------
`refused_safely` below is the whole checklist in one call, because a failure
path that satisfies four of these is not safe:

    no unsafe retry     the import envelope count, taken off the transport
    a durable row       an ActionLog row naming the operation id
    an explicit state   a named outcome, never a shrug
    a truthful report   the words say what happened, in the right direction
    no false COMPLETED  no `posted` row and no COMPLETED batch

WHAT THIS FILE DOES NOT PROVE
-----------------------------
* That real TallyPrime ever sends any of these bodies. Four shapes were
  measured against the live instance and are marked MEASURED where they appear;
  the rest are hypotheses wearing XML, and a simulator built from the
  connector's own assumptions cannot falsify those assumptions. Evidence class:
  FAKETALLY plus `tests.test_real_tally.TallySim`.
* That the connector handles a body nobody has imagined. Every case here is a
  shape somebody wrote down. The general defence is that `parse_read_response`
  refuses anything carrying an error tag and `parse_xml` refuses anything that
  will not parse - both are asserted, neither is a proof of completeness.
* Anything about the licence mode. `tests/test_backend_states.py` owns that.
"""

from __future__ import annotations

import datetime
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from http.server import HTTPServer

import pytest

from accountant import pipeline, reversal
from accountant.memory.store import MemoryStore
from accountant.schema import ActionLog, Voucher
from accountant.tallyio import real
from accountant.tallyio.client import (
    DuplicateOperation,
    WriteResult,
    new_operation_id,
)
from accountant.tallyio.factory import (
    BackendIdentity,
    new_run_id,
)
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests import test_adversarial_write_path as W
from tests import test_real_tally as sim_module
from tests import test_tally_contract as contract
from tests.test_real_tally import TWO_LEGGED, TallySim, import_response

# BORROWED rather than copied, in the idiom `tests/test_adversarial_write_path.py`
# established for `tests/test_runtime_backend.py`. `_voucher_payload` is private
# because it belongs to the file that measured the shape a real TallyPrime sends
# - CMPINFO header and all - and a second copy here would be a second thing to
# keep in step with that measurement. The first time the two drifted, the copy
# would go on passing.
voucher_payload = sim_module._voucher_payload  # pyright: ignore[reportPrivateUsage]

COMPANY = W.COMPANY
ACCOUNTS = W.ACCOUNTS
RUN = "run_error_responses"

IMPORT_MARKER = "<TALLYREQUEST>Import</TALLYREQUEST>"


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------

#: MEASURED 2026-08-08 against the live gateway, quoted in `ci/educational_slice.py`.
#: Not an ENVELOPE, no DATA block, nothing but the sentence.
UNKNOWN_REQUEST = "<RESPONSE>Unknown Request, cannot be processed</RESPONSE>"

#: MEASURED shape: a well-formed envelope whose DATA block is a refusal.
LINE_ERROR = W.EXPORT_ERROR

#: MEASURED 2026-08-09, from the licence probe (A11). The same tag family, in a
#: different request.
ERROR_MSG = (
    "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
    "<BODY><DATA>"
    "<ERRORMSG>Could not find: $$LicenseInfo:IsEducationalMode</ERRORMSG>"
    "<ERRORMSG>Function Execution Failed!</ERRORMSG>"
    "</DATA></BODY></ENVELOPE>"
)

#: HYPOTHESIS. `_ERROR_TAGS` names EXCEPTION and nothing exercised it.
EXCEPTION_ENVELOPE = (
    "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
    "<BODY><DATA><EXCEPTION>Memory access violation</EXCEPTION></DATA></BODY>"
    "</ENVELOPE>"
)

#: HYPOTHESIS, and the nastiest of them: a refusal sitting NEXT TO real data.
#: A parser that reads its own payload tag and stops would return one voucher
#: and never mention that Tally also said it could not do the job.
REFUSAL_BESIDE_DATA = (
    "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
    "<BODY><DATA>"
    "<LINEERROR>Could not set 'SVCURRENTCOMPANY'</LINEERROR>"
    f"<COLLECTION>{TWO_LEGGED}</COLLECTION>"
    "</DATA></BODY></ENVELOPE>"
)

REFUSALS: tuple[tuple[str, str], ...] = (
    ("line_error", LINE_ERROR),
    ("errormsg", ERROR_MSG),
    ("exception", EXCEPTION_ENVELOPE),
    ("unknown_request", UNKNOWN_REQUEST),
    ("refusal_beside_data", REFUSAL_BESIDE_DATA),
)

UNREADABLE: tuple[tuple[str, str], ...] = (
    ("empty_body", ""),
    ("whitespace_only", "   \n\t "),
    ("truncated_xml", "<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER>"),
    ("not_xml_at_all", "Tally is busy, please try later"),
    ("half_a_voucher", "<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER><DATE>2026"),
)

#: DEFECT E1's inputs. Every one of these is WELL-FORMED XML and none of them
#: came from Tally. See the section at the end of this file.
NOT_TALLY_AT_ALL: tuple[tuple[str, str], ...] = (
    ("an_html_404", "<html><body><h1>404 Not Found</h1></body></html>"),
    (
        "a_proxy_sign_in_page",
        "<html><head><title>Sign in</title></head><body>Authenticate</body></html>",
    ),
    ("some_other_service", "<result><status>ok</status></result>"),
)

READ_PARSERS: tuple[tuple[str, Callable[[str], object]], ...] = (
    ("parse_companies", real.parse_companies),
    ("parse_ledger_names", real.parse_ledger_names),
    ("parse_closing_balances", real.parse_closing_balances),
    ("parse_vouchers", real.parse_vouchers),
)

LEDGER_LIST = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    + "".join(f'<LEDGER NAME="{a}"></LEDGER>' for a in ACCOUNTS)
    + "</COLLECTION></DATA></BODY></ENVELOPE>"
)

COMPANY_LIST = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    f'<COMPANY NAME="{COMPANY}"><NAME>{COMPANY}</NAME></COMPANY>'
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def is_import(payload: str) -> bool:
    return IMPORT_MARKER in payload


def is_voucher_export(payload: str) -> bool:
    return f"<ID>{real.COLLECTION_VOUCHERS}</ID>" in payload


class Scripted:
    """A transport that answers each request family from a script it records.

    The recording is the point. "The write was refused" and "the write was
    refused before anything was imported" are different claims, and only the
    second one is worth anything - so every assertion about a refusal below is
    paired with a count of the import envelopes that actually went out.
    """

    def __init__(
        self,
        *,
        vouchers: list[str] | None = None,
        imports: list[str] | None = None,
        ledgers: str = LEDGER_LIST,
        companies: str = COMPANY_LIST,
    ) -> None:
        self.voucher_replies = list(vouchers or [])
        self.import_replies = list(imports or [])
        self.ledgers = ledgers
        self.companies = companies
        self.sent: list[str] = []
        self.retry_flags: list[bool] = []

    @property
    def imports_sent(self) -> int:
        return sum(1 for out in self.sent if is_import(out))

    def send(self, payload: str, *, retry: bool) -> str:
        self.sent.append(payload)
        self.retry_flags.append(retry)
        if is_import(payload):
            assert self.import_replies, "the script ran out of import replies"
            return self.import_replies.pop(0)
        if is_voucher_export(payload):
            assert self.voucher_replies, "the script ran out of voucher replies"
            return self.voucher_replies.pop(0)
        if f"<ID>{real.COLLECTION_COMPANIES}</ID>" in payload:
            return self.companies
        return self.ledgers


class LandsThenLies:
    """Tally really does the work, and then says it did not.

    The inner simulator is called FIRST and its effect is KEPT. That ordering is
    the whole scenario: a body that says the operation failed is not a rollback,
    and a caller that believes it and retries writes the bill twice.
    """

    def __init__(self, inner: TallySim, *, answer: str) -> None:
        self.inner = inner
        self.answer = answer
        self.sent: list[str] = []

    @property
    def imports_sent(self) -> int:
        return sum(1 for out in self.sent if is_import(out))

    def send(self, payload: str, *, retry: bool) -> str:
        reply = self.inner.send(payload, retry=retry)
        self.sent.append(payload)
        return self.answer if is_import(payload) else reply


def a_client(transport: real.Transport) -> real.RealTally:
    """A RealTally over a scripted transport, with this company backed up.

    `RecordedBackups` holding the company is the only reason any write below is
    permitted at all; the default is an empty set that refuses everything.
    """
    return real.RealTally(
        transport=transport, backups=real.RecordedBackups(frozenset({COMPANY}))
    )


def empty_export() -> str:
    return voucher_payload()


def export_of(*bodies: str, answering_for: str | None = None) -> str:
    """A voucher export, optionally echoing the company Tally answered for.

    A12: `SVCURRENTCOMPANY` is the only thing that lets a read-back notice it
    was answered out of the wrong books. `_voucher_payload` does not emit one,
    which is why `page.company` is None on every simulator read - "cannot
    check", never "it matched".
    """
    payload = voucher_payload(*bodies)
    if answering_for is None:
        return payload
    return payload.replace(
        "<BODY>",
        f"<BODY><DESC><STATICVARIABLES><SVCURRENTCOMPANY>{answering_for}"
        "</SVCURRENTCOMPANY></STATICVARIABLES></DESC>",
        1,
    )


def refused_safely(
    store: MemoryStore,
    *,
    operation_id: str,
    imports: int,
    expected_imports: int,
    say: str,
) -> None:
    """The five properties, all of them, in one place.

    Split out because a failure path that satisfies four of the five is not a
    safe failure path, and because writing them out five times per test is how
    one of them quietly goes missing.
    """
    rows: list[ActionLog] = list(store.actions(COMPANY))

    assert imports == expected_imports, "an unsafe retry went out"
    assert rows, "a durable row must exist; a failure nobody can find is not one"
    assert operation_id in {r.operation_id for r in rows}, "the row names the write"
    assert pipeline.WRITE_ATTEMPTED in {r.action for r in rows}
    assert "posted" not in {r.action for r in rows}, "no false COMPLETED"
    assert "valid" not in {r.outcome for r in rows}, "and no outcome claiming success"
    terminal = [r for r in rows if r.action == pipeline.WRITE_OUTCOME_UNKNOWN]
    assert terminal, "the state is explicit, never absent"
    assert say in terminal[-1].reason, f"the report is not truthful: {terminal[-1]}"


# ---------------------------------------------------------------------------
# 1. a refusal is not data, on any read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape,body", REFUSALS, ids=[n for n, _ in REFUSALS])
@pytest.mark.parametrize("parser", READ_PARSERS, ids=[n for n, _ in READ_PARSERS])
def test_every_read_parser_refuses_every_refusal_shape(
    shape: str, body: str, parser: tuple[str, Callable[[str], object]]
) -> None:
    """`tests/test_adversarial_write_path.py` proves this for two of the five
    shapes. The three added here are the two other tags in `_ERROR_TAGS`, which
    nothing exercised, and the case that worries me most: a refusal sitting
    beside real data, where a parser that finds its own payload tag and stops
    would return a voucher AND swallow the sentence saying Tally could not do
    the job.
    """
    del shape
    with pytest.raises(real.TallyResponseError, match="refused"):
        parser[1](body)


@pytest.mark.parametrize("shape,body", REFUSALS, ids=[n for n, _ in REFUSALS])
def test_a_refusal_never_reads_as_a_company_with_no_vouchers(
    shape: str, body: str
) -> None:
    """Through the CLIENT, not the parser. The parser refusing is only useful if
    nothing above it turns the refusal back into an empty tuple."""
    del shape
    client = a_client(Scripted(vouchers=[body, body]))

    with pytest.raises(real.TallyResponseError):
        client.read_vouchers(COMPANY)
    with pytest.raises(real.TallyResponseError):
        client.list_our_vouchers(COMPANY)


def test_a_refusal_on_the_balance_read_never_reads_as_a_balanced_book() -> None:
    """The read whose silent-empty was the most dangerous of the four.

    `pipeline.reverse_operation` compares the trial balance before and after,
    and two empty dicts compare EQUAL - so a reversal that moved nothing would
    have been reported as exact.
    """
    client = a_client(Scripted(ledgers=LINE_ERROR))

    with pytest.raises(real.TallyResponseError, match="refused"):
        client.trial_balance(COMPANY)


def test_a_company_that_really_is_empty_still_reads_as_empty() -> None:
    """The control. Refusing everything would satisfy every test above.

    EMPTY_SOURCE is a legitimate state - a brand-new customer is in it - and the
    whole value of the refusals is that "empty" now means only that.
    """
    client = a_client(Scripted(vouchers=[empty_export()]))

    assert client.read_vouchers(COMPANY) == ()
    assert real.parse_companies(COMPANY_LIST) == (COMPANY,)


def test_a_company_that_really_has_a_voucher_still_reads_as_having_one() -> None:
    """The second control. A refusal that swallowed good data would pass the
    first one and fail here."""
    client = a_client(Scripted(vouchers=[export_of(TWO_LEGGED)]))

    read = client.read_vouchers(COMPANY)
    assert len(read) == 1
    assert read[0].amount_paise == 118000


def test_the_import_direction_still_reads_the_same_tags_as_data() -> None:
    """The asymmetry, asserted rather than assumed.

    A write asks "did you do it", and `<LINEERROR>` is the answer. A read asks
    "what is in the books", and `<LINEERROR>` is a refusal to answer. One tag,
    two directions. Leak the refusal into the direction that reads it as data
    and every rejected import raises instead of reporting which line Tally
    objected to.
    """
    result = real.parse_import_response(LINE_ERROR)

    assert result.ok is False
    assert result.line_errors == ("Could not set 'SVCURRENTCOMPANY'",)
    assert "Could not set" in result.summary()


# ---------------------------------------------------------------------------
# 2. unreadable is not absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape,body", UNREADABLE, ids=[n for n, _ in UNREADABLE])
def test_a_body_we_cannot_read_raises_rather_than_reporting_nothing(
    shape: str, body: str
) -> None:
    """Empty, truncated, plain text, an HTML error page from something that is
    not Tally at all. None of them is evidence about anybody's books."""
    del shape
    client = a_client(Scripted(vouchers=[body]))

    with pytest.raises(real.TallyResponseError):
        client.read_vouchers(COMPANY)


@pytest.mark.parametrize("shape,body", UNREADABLE, ids=[n for n, _ in UNREADABLE])
def test_a_write_is_refused_before_the_import_when_the_register_will_not_read(
    shape: str, body: str
) -> None:
    """The duplicate check is the FIRST read a write makes. A silent `()` there
    is the difference between refusing a duplicate and creating one, so the
    envelope count is what this asserts."""
    del shape
    transport = Scripted(vouchers=[body], imports=[import_response(created=1)])
    client = a_client(transport)

    with pytest.raises(real.TallyResponseError):
        client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())

    assert transport.sent, "the transport was never called, so nothing was proved"
    assert transport.imports_sent == 0, "refused before anything was imported"


def test_a_register_that_stops_parsing_after_the_write_is_not_proof_of_absence() -> (
    None
):
    """MALFORMED_RESPONSE, and it is neither a failure nor a success.

    The import landed. The read-back afterwards came back unreadable. That is
    evidence of nothing, so the write is not recorded as posted AND is not safe
    to write again - the two facts a single boolean cannot carry.
    """
    transport = Scripted(
        vouchers=[empty_export(), "<ENVELOPE><BODY><DATA><COLLECTION>"],
        imports=[import_response(created=1, status=1, last_vch_id="M1")],
    )
    client = a_client(transport)

    with pytest.raises(real.MalformedRegisterResponse) as refused:
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_probe")

    assert refused.value.outcome is real.ReadBackOutcome.MALFORMED_RESPONSE
    assert refused.value.safe_to_retry is False
    assert "not evidence the voucher is missing" in str(refused.value)
    assert transport.imports_sent == 1, "and it was not written a second time"


def test_a_voucher_that_lost_its_ledger_entries_is_unreadable_and_not_skipped() -> None:
    """One of our own marked vouchers, edited in Tally until it is no longer two
    legged. Reversal arithmetic over it cannot be trusted, so it raises rather
    than being counted as one we could not represent."""
    partial = voucher_payload(
        '<VOUCHER MASTERID="M1" VCHTYPE="Journal">'
        "<DATE>20260807</DATE>"
        "<NARRATION>cement bags [ACCOUNTANT_DAD:ad_7]</NARRATION>"
        "</VOUCHER>"
    )

    with pytest.raises(real.TallyDataError, match="no ledger entries at all"):
        real.parse_vouchers(partial)


# ---------------------------------------------------------------------------
# 3. transport success is NOT accounting success
# ---------------------------------------------------------------------------


IMPORT_FAILURES: tuple[tuple[str, str], ...] = (
    (
        "errors_reported",
        import_response(created=0, errors=1, line_errors=("Ledger not found",)),
    ),
    ("created_nothing", import_response(created=0)),
    ("altered_instead_of_created", import_response(created=0, altered=1)),
    ("part_of_it_ignored", import_response(created=1, ignored=1, last_vch_id="M1")),
    ("exception_reported", import_response(created=0, exceptions=1)),
)


@pytest.mark.parametrize(
    "shape,answer", IMPORT_FAILURES, ids=[n for n, _ in IMPORT_FAILURES]
)
def test_an_http_200_whose_body_says_the_write_failed_is_a_failure(
    shape: str, answer: str
) -> None:
    """Five bodies Tally can return over a perfectly healthy connection. Every
    one of them is a refusal, and the connector must never read the 200."""
    del shape
    transport = Scripted(vouchers=[empty_export()], imports=[answer])
    client = a_client(transport)

    with pytest.raises(real.TallyRejected) as refused:
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_probe")

    assert "ad_probe" in str(refused.value)
    assert transport.imports_sent == 1, "one attempt, and no automatic retry"


def test_a_write_that_landed_and_was_reported_as_failed_is_not_written_twice() -> None:
    """THE LOAD-BEARING CASE. Tally created the voucher and said it did not.

    `LandsThenLies` keeps the simulator's effect and replaces only the answer,
    which is exactly what a gateway does when its reply is composed after the
    commit. The caller is told the write failed - correctly, on the evidence it
    has - and the ONLY thing standing between that and a second statutory entry
    is that a retry re-reads the register first.
    """
    sim = W.a_simulated_tally()
    transport = LandsThenLies(
        sim, answer=import_response(created=0, errors=1, line_errors=("rejected",))
    )
    client = W.sim_client(sim, transport)
    op = new_operation_id()

    with pytest.raises(real.TallyRejected):
        client.write_voucher(COMPANY, contract.a_voucher(), op)

    assert len(sim.companies[COMPANY].vouchers) == 1, "it landed anyway"
    assert transport.imports_sent == 1

    honest = W.sim_client(sim)
    with pytest.raises(DuplicateOperation):
        honest.write_voucher(COMPANY, contract.a_voucher(), op)

    assert len(sim.companies[COMPANY].vouchers) == 1, "and never became two"


def test_a_write_tally_says_it_created_but_cannot_show_is_unknown_not_failed() -> None:
    """UNKNOWN_OUTCOME. The one state that must never be flattened either way.

    Reported as failed, somebody retries and the bill lands twice. Reported as
    succeeded, a voucher nobody can find is recorded as posted. So it is its own
    named class, its message says UNKNOWN, and it is not safe to retry.
    """
    sim = W.a_simulated_tally()
    sim.import_override = import_response(created=1, status=1, last_vch_id="M9")
    client = W.sim_client(sim)

    with pytest.raises(real.TallyWriteUnknown) as unknown:
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_probe")

    assert unknown.value.outcome is real.ReadBackOutcome.UNKNOWN_OUTCOME
    assert unknown.value.safe_to_retry is False
    assert "UNKNOWN" in str(unknown.value)
    assert "must never be retried automatically" in str(unknown.value)
    assert sim.companies[COMPANY].vouchers == [], "nothing was created"


def test_no_read_back_verdict_a_write_can_produce_is_ever_safe_to_retry() -> None:
    """Stated over the whole enum rather than over the cases above, because a
    tenth outcome added tomorrow inherits the property instead of needing a new
    test to remember it."""
    for outcome in real.ReadBackOutcome:
        verdict = real.ReadBackVerdict(
            outcome=outcome, company=COMPANY, operation_id="ad_probe"
        )
        assert verdict.safe_to_retry is False
        assert verdict.confirmed is (outcome is real.ReadBackOutcome.EXACT_MATCH)


def test_a_non_2xx_status_is_a_refusal_and_never_an_answer() -> None:
    """The transport layer's own version of the same rule."""

    def five_hundred(url: str, body: bytes, timeout: float, max_bytes: int):
        del url, body, timeout, max_bytes
        return 500, b"<ENVELOPE/>"

    transport = real.HttpTransport(
        real.TallyConfig(), poster=five_hundred, sleep=lambda _: None
    )

    with pytest.raises(real.TallyResponseError, match="HTTP 500"):
        transport.send("<ENVELOPE/>", retry=False)


def test_a_200_carrying_a_refusal_is_carried_up_and_then_refused() -> None:
    """The transport does not read the body, so the 200 arrives intact and the
    PARSER is what refuses it. Asserting both halves keeps the responsibility
    where it is: a transport that started reading XML would be a second place
    for this decision to be made differently."""

    def two_hundred(url: str, body: bytes, timeout: float, max_bytes: int):
        del url, body, timeout, max_bytes
        return 200, LINE_ERROR.encode()

    transport = real.HttpTransport(
        real.TallyConfig(), poster=two_hundred, sleep=lambda _: None
    )

    delivered = transport.send("<ENVELOPE/>", retry=False)

    assert delivered == LINE_ERROR
    with pytest.raises(real.TallyResponseError, match="refused"):
        real.parse_companies(delivered)


# ---------------------------------------------------------------------------
# 4. the company on the other end
# ---------------------------------------------------------------------------


def test_a_company_list_that_names_someone_else_names_them_and_not_us() -> None:
    """Owner item 16, the empty-company case, at the layer that reads it.

    The REFUSAL that acts on this lives in `accountant/tallyio/factory.py` and
    is proved over a real socket by `tests/test_startup_path.py:705` and
    `tests/test_contract_differences.py:818`. `real_tally` builds its own
    connector from a `TallyConfig` and has no transport seam, so calling it here
    would open a socket to localhost:9000 and test the UNREACHABLE branch while
    claiming to test this one - which is the vacuous test this file is supposed
    to be hunting.

    What is asserted here is the input that refusal depends on: the connector
    reports the company Tally actually named, and does not quietly include ours.
    """
    listing = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        '<COMPANY NAME="Someone Else Ltd"><NAME>Someone Else Ltd</NAME></COMPANY>'
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    class OnlySomeoneElse:
        def send(self, payload: str, *, retry: bool) -> str:
            del payload, retry
            return listing

    client = a_client(OnlySomeoneElse())

    assert client.list_companies() == ("Someone Else Ltd",)
    assert COMPANY not in client.list_companies()


def test_a_company_list_that_is_empty_is_empty_and_not_an_error() -> None:
    """The control. A Tally with no company open answers with an empty
    collection, and that is a real answer - it is what the factory's refusal
    reads. Refusing it here would move the decision to the wrong layer."""

    class NoCompanies:
        def send(self, payload: str, *, retry: bool) -> str:
            del payload, retry
            return (
                "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION>"
                "</DATA></BODY></ENVELOPE>"
            )

    assert a_client(NoCompanies()).list_companies() == ()


def test_a_read_back_answered_for_another_company_refuses_the_write() -> None:
    """A12. The register echoed a company that is not the one we wrote to.

    Every other field matches exactly, so this is the case a field-by-field
    comparison alone would pass: the voucher is right and the books are
    somebody else's.
    """
    transport = Scripted(
        vouchers=[
            empty_export(),
            export_of(TWO_LEGGED, answering_for="Someone Else Ltd"),
        ],
        imports=[import_response(created=1, status=1, last_vch_id="M11")],
    )
    client = a_client(transport)

    with pytest.raises(real.TallyWriteMismatch) as refused:
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_7")

    assert refused.value.outcome is real.ReadBackOutcome.WRONG_COMPANY
    assert refused.value.safe_to_retry is False
    assert "Someone Else Ltd" in str(refused.value)
    assert transport.imports_sent == 1, "and it was not written again elsewhere"


def test_a_read_back_that_does_not_name_a_company_is_cannot_check_never_matched() -> (
    None
):
    """The control on the test above, and the state the live gateway is actually
    in: the export carries no `SVCURRENTCOMPANY`, so the company check has
    nothing to compare and must neither pass nor fail on it."""
    page = real.parse_vouchers(export_of(TWO_LEGGED))
    assert page.company is None

    verdict = real.verify_read_back(
        company=COMPANY,
        sent=contract.a_voucher(),
        operation_id="ad_7",
        found=page.vouchers[0],
        found_in_company=page.company,
    )

    assert verdict.outcome is real.ReadBackOutcome.EXACT_MATCH
    assert verdict.confirmed is True


def test_the_app_refuses_every_request_once_its_company_stops_being_open(
    server: str,
) -> None:
    """Owner item 16 at the surface. The company was open when we connected and
    is not open now, which is a thing a person does in TallyPrime by accident.

    It already failed closed, as a traceback and a dropped socket. A 503 that
    NAMES what is open is the difference between that and an answer.
    """
    tally = app.runtime().client
    assert isinstance(tally, FakeTally)
    before = tally.trial_balance(app.COMPANY)
    tally.close_company(app.COMPANY)

    data = urllib.parse.urlencode({"text": "paid Sharma Traders 4200 for cement"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(  # noqa: S310
            server + "/entry", data=data.encode(), timeout=10
        )

    assert refused.value.code == 503
    body = refused.value.read().decode()
    assert app.REFUSAL in body
    assert "no longer open in Tally" in body
    assert "0 company/companies are open" in body, "and it names what IS open"
    assert before, "the fixture really did have books before they were closed"


# ---------------------------------------------------------------------------
# 5. two vouchers wearing one operation id
# ---------------------------------------------------------------------------


def test_a_marker_matching_two_vouchers_is_refused_and_neither_is_touched() -> None:
    """A5. The marker is this system's identity, so two vouchers wearing one is
    an ambiguity and not a menu. Nothing is read back and nothing is deleted
    until a person says which is real."""
    twin = TWO_LEGGED.replace('MASTERID="M11"', 'MASTERID="M12"')
    transport = Scripted(vouchers=[export_of(TWO_LEGGED, twin)] * 3)
    client = a_client(transport)

    with pytest.raises(real.AmbiguousMarker, match="matches 2 vouchers"):
        client.read_by_operation_id(COMPANY, "ad_7")
    with pytest.raises(real.AmbiguousMarker):
        client.reverse_by_operation_id(COMPANY, "ad_7")

    assert transport.imports_sent == 0, "no delete envelope went out"
    assert real.AmbiguousMarker.outcome is real.ReadBackOutcome.MULTIPLE_MATCHES


def test_an_ambiguous_marker_refuses_the_write_before_it_imports_anything() -> None:
    """The write path meets the same pair on its duplicate check."""
    twin = TWO_LEGGED.replace('MASTERID="M11"', 'MASTERID="M12"')
    transport = Scripted(
        vouchers=[export_of(TWO_LEGGED, twin)], imports=[import_response(created=1)]
    )
    client = a_client(transport)

    with pytest.raises(real.AmbiguousMarker):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_7")

    assert transport.imports_sent == 0


def test_the_ambiguity_message_names_both_vouchers_so_a_person_can_choose() -> None:
    """ "Something is ambiguous" sends somebody through a whole ledger. The
    locators are what turn it into two rows to look at."""
    twin = TWO_LEGGED.replace('MASTERID="M11"', 'MASTERID="M12"')
    client = a_client(Scripted(vouchers=[export_of(TWO_LEGGED, twin)]))

    with pytest.raises(real.AmbiguousMarker) as refused:
        client.read_by_operation_id(COMPANY, "ad_7")

    said = str(refused.value)
    assert "M11" in said and "M12" in said
    assert "a person has to decide which is real" in said


# ---------------------------------------------------------------------------
# 6. the connection itself
# ---------------------------------------------------------------------------


def test_a_refused_connection_is_unreachable_and_never_an_empty_company() -> None:
    """ "Your books are unreachable" and "your books are empty" are opposite
    facts. The first must stop the system; the second is a state a new customer
    is legitimately in."""

    def refused(url: str, body: bytes, timeout: float, max_bytes: int):
        del url, body, timeout, max_bytes
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    transport = real.HttpTransport(
        real.TallyConfig(), poster=refused, sleep=lambda _: None
    )

    with pytest.raises(real.TallyUnreachable) as unreachable:
        transport.send("<ENVELOPE/>", retry=False)

    said = str(unreachable.value)
    assert "no response from Tally" in said
    assert "HTTP Server" in said, "the refusal says what to go and check"


def test_a_read_may_retry_and_a_write_may_not() -> None:
    """A connection that dies after Tally committed is indistinguishable from
    one that died before it did, so a retried WRITE is a duplicate voucher. The
    attempt count is the proof, not the flag."""
    attempts: list[int] = []

    def timing_out(url: str, body: bytes, timeout: float, max_bytes: int):
        del url, body, timeout, max_bytes
        attempts.append(1)
        raise TimeoutError("timed out")

    transport = real.HttpTransport(
        real.TallyConfig(), poster=timing_out, sleep=lambda _: None
    )

    with pytest.raises(real.TallyUnreachable):
        transport.send("<ENVELOPE/>", retry=True)
    read_attempts = len(attempts)
    attempts.clear()

    with pytest.raises(real.TallyUnreachable):
        transport.send("<ENVELOPE/>", retry=False)

    assert read_attempts > 1, "a read is safe to retry and does"
    assert len(attempts) == 1, "a write is attempted exactly once"


def test_the_connector_asks_for_no_retry_on_the_envelope_that_writes() -> None:
    """The flag itself, measured at the transport rather than read off the
    source. A write that arrived with `retry=True` would be retried by a
    transport that is behaving perfectly."""
    sim = W.a_simulated_tally()
    client = W.sim_client(sim)

    client.write_voucher(COMPANY, contract.a_voucher(), new_operation_id())

    for payload, retry in zip(sim.sent, sim.retry_flags, strict=True):
        if is_import(payload):
            assert retry is False, "a write envelope must never be retried"


def test_an_oversized_body_is_refused_rather_than_buffered() -> None:
    """A body that will not fit is not an answer, and reading all of it to find
    that out is the failure mode being avoided."""
    cap = real.TallyConfig().max_response_bytes

    def far_too_much(url: str, body: bytes, timeout: float, max_bytes: int):
        del url, body, timeout, max_bytes
        return 200, b"x" * (cap + 1)

    transport = real.HttpTransport(
        real.TallyConfig(), poster=far_too_much, sleep=lambda _: None
    )

    with pytest.raises(real.TallyResponseError, match="byte cap"):
        transport.send("<ENVELOPE/>", retry=False)


# ---------------------------------------------------------------------------
# 7. the five properties, on the path the product actually takes
# ---------------------------------------------------------------------------


class RejectsTheWrite(W.RecordingTally):
    """Tally understood the write and refused it. The books do not move."""

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.writes.append((company, operation_id, voucher.amount_paise))
        raise real.TallyRejected(
            f"Tally rejected operation {operation_id!r}: errors=1 "
            "<LINEERROR>Ledger not found</LINEERROR>"
        )


class AnswersAnUnknownOutcome(W.RecordingTally):
    """Tally said it created one and the register cannot show it."""

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.writes.append((company, operation_id, voucher.amount_paise))
        raise real.TallyWriteUnknown(
            f"UNKNOWN_OUTCOME for operation {operation_id!r}: it must never be "
            "retried automatically",
            real.ReadBackVerdict(
                outcome=real.ReadBackOutcome.UNKNOWN_OUTCOME,
                company=company,
                operation_id=operation_id,
            ),
        )


@pytest.mark.parametrize(
    "double,say",
    [
        (RejectsTheWrite, "TallyRejected"),
        (AnswersAnUnknownOutcome, "TallyWriteUnknown"),
    ],
    ids=["explicit_rejection", "unknown_outcome"],
)
def test_a_refused_write_satisfies_all_five_properties(
    double: type[W.RecordingTally], say: str
) -> None:
    """The checklist, run against the two shapes a live write can fail in."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = double(inner)
    draft = W.valid_draft(client, memory)
    balance = client.trial_balance(COMPANY)

    with pytest.raises(real.TallyError):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    refused_safely(
        store,
        operation_id=draft.operation_id,
        imports=client.write_count,
        expected_imports=1,
        say=say,
    )
    assert client.list_our_vouchers(COMPANY) == (), "and nothing is in the books"
    assert client.trial_balance(COMPANY) == balance
    assert draft.posted_tally_id is None


def test_a_refused_write_is_never_retried_by_the_pipeline_on_its_own() -> None:
    """There is no retry loop anywhere on this path, and that absence is the
    safety property. Asserted by counting, because an absence added back by
    accident is exactly the change nobody reviews."""
    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    client = RejectsTheWrite(inner)
    draft = W.valid_draft(client, memory)

    with pytest.raises(real.TallyRejected):
        pipeline.post(draft, client, log=store, memory=memory, run_id=RUN)

    assert client.write_count == 1


def test_a_bulk_reversal_that_meets_a_refusal_never_reports_completed() -> None:
    """The batch's own version of "no false COMPLETED".

    A delete Tally refuses is EXPLICIT_REJECTION, the batch is PARTIAL_FAILURE,
    and the vouchers that were never reached stay NOT_ATTEMPTED rather than
    being reported as done.
    """

    class RefusesToDelete(W.RecordingTally):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
            self.reversals.append((company, operation_id))
            return False

    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    honest = W.RecordingTally(inner)
    draft = W.valid_draft(honest, memory)
    pipeline.post(draft, honest, log=store, memory=memory, run_id=RUN)

    refusing = RefusesToDelete(inner)
    balance = refusing.trial_balance(COMPANY)
    batch = reversal.execute(
        reversal.confirm(reversal.preview(refusing, COMPANY)), refusing
    )

    assert batch.state is reversal.BatchState.PARTIAL_FAILURE
    assert batch.state is not reversal.BatchState.COMPLETED
    assert [o.state for o in batch.outcomes] == [
        reversal.VoucherState.EXPLICIT_REJECTION
    ]
    assert refusing.trial_balance(COMPANY) == balance
    assert len(refusing.list_our_vouchers(COMPANY)) == 1


def test_a_bulk_reversal_whose_answer_is_unreadable_reports_unknown_not_failed() -> (
    None
):
    """`_classify` maps anything it cannot name to UNKNOWN_OUTCOME, which stops
    the batch pending a read-only reconciliation instead of retrying into it."""

    class GoesAwayMidDelete(W.RecordingTally):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
            self.reversals.append((company, operation_id))
            raise real.TallyResponseError("the register answered with nothing")

    inner = W.tally(W.past())
    store = MemoryStore(":memory:")
    memory = W.memory_for(inner, store)
    honest = W.RecordingTally(inner)
    draft = W.valid_draft(honest, memory)
    pipeline.post(draft, honest, log=store, memory=memory, run_id=RUN)

    failing = GoesAwayMidDelete(inner)
    batch = reversal.execute(
        reversal.confirm(reversal.preview(failing, COMPANY)), failing
    )

    assert batch.state is reversal.BatchState.UNKNOWN_OUTCOME
    assert [o.state for o in batch.outcomes] == [reversal.VoucherState.UNKNOWN_OUTCOME]
    assert "TallyResponseError" in batch.outcomes[0].detail
    assert len(failing.list_our_vouchers(COMPANY)) == 1


# ---------------------------------------------------------------------------
# 8. the person in front of it
# ---------------------------------------------------------------------------


class RefusingTally(FakeTally):
    """A company whose Tally answers every write with a refusal.

    Subclasses the double rather than wrapping it so that `type(client).__name__`
    is a name of its own: `app.configure` refuses an identity that does not match
    the client it names, which is what stops the page and the action log from
    reporting different backends.
    """

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        del voucher
        raise real.TallyRejected(
            f"Tally rejected operation {operation_id!r} in {company!r}: "
            "errors=1 <LINEERROR>Ledger not found</LINEERROR>"
        )


@pytest.fixture
def refusing_server() -> Iterator[tuple[str, RefusingTally]]:
    """The app, in front of a Tally that will not accept a write."""
    tally = RefusingTally()
    tally.add_company(
        app.COMPANY,
        accounts=("Purchases", "Cash"),
        vouchers=tuple(
            Voucher(
                id=f"h{i}",
                date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
                party="Sharma Traders",
                narration="cement supply",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=100_000,
            )
            for i in range(8)
        ),
        backed_up=True,
    )
    identity = BackendIdentity(
        backend="RefusingTally",
        endpoint="memory://tests/test_error_responses.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )
    app.DRAFTS.clear()
    app.BATCHES.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity, store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", tally
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def post_status(base: str, path: str, **fields: str) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=10) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_a_write_tally_refuses_is_answered_with_a_page_and_not_a_dropped_socket(
    refusing_server: tuple[str, RefusingTally],
) -> None:
    """Failing safely and failing legibly are two properties, and a traceback
    only has the first."""
    base, tally = refusing_server

    code, body = post_status(base, "/entry", text="paid Sharma Traders 4200 for cement")

    assert code == 503
    assert "Nothing was written to your Tally" in body
    assert 'class="badge b-valid">posted<' not in body
    assert tally.list_our_vouchers(app.COMPANY) == ()


def test_the_page_a_refused_write_draws_carries_no_internals(
    refusing_server: tuple[str, RefusingTally],
) -> None:
    """Two audiences, two messages. A stack message on a screen a customer sees
    is its own failure, and `note()` already has a field for it."""
    base, _tally = refusing_server

    body = post_status(base, "/entry", text="paid Sharma Traders 4200 for cement")[1]

    assert "TallyRejected" not in body
    assert "LINEERROR" not in body
    assert "Traceback" not in body


def test_a_refused_write_leaves_the_diagnosis_in_the_durable_log(
    refusing_server: tuple[str, RefusingTally],
) -> None:
    """The other audience. The row carries the type and the message, so the
    failure is diagnosable without a screenshot."""
    base, _tally = refusing_server

    post_status(base, "/entry", text="paid Sharma Traders 4200 for cement")

    with urllib.request.urlopen(base, timeout=10) as home:  # noqa: S310
        page = home.read().decode()
    log = page[page.index("<section id=log>") : page.index("</section>")]
    assert 'data-action="failed"' in log
    assert "TallyRejected" in log
    assert 'data-outcome="valid"' not in log


def test_the_refused_entry_never_reaches_the_list_of_what_we_posted(
    refusing_server: tuple[str, RefusingTally],
) -> None:
    """The screen a person checks their work on. A refused write appearing there
    is the worst of the failures in this file, because it is the one they would
    act on."""
    base, tally = refusing_server
    before = tally.trial_balance(app.COMPANY)

    post_status(base, "/entry", text="paid Sharma Traders 4200 for cement")

    with urllib.request.urlopen(base, timeout=10) as home:  # noqa: S310
        page = home.read().decode()
    assert "nothing posted yet" in page
    assert tally.trial_balance(app.COMPANY) == before


# ---------------------------------------------------------------------------
# 9. a well-formed answer from something that is not Tally. DEFECT E1.
# ---------------------------------------------------------------------------
#
# WHAT WAS MEASURED
#     `parse_read_response` refuses a response that carries an error TAG, and
#     `parse_xml` refuses one that will not parse. Nothing anywhere checks that
#     the document is a Tally response at all. A 404 page, a proxy sign-in page
#     and an unrelated service's XML are all well formed, carry no error tag,
#     and therefore read as a company with no companies, no ledgers, no
#     vouchers and a trial balance of `{}` - on every one of the four reads.
#
#     That is the exact collapse `accountant/tallyio/factory.py` forbids in
#     writing: "a fallback would turn 'your books are unreachable' into 'your
#     books are empty', and those are opposite facts."
#
# HOW BAD IT IS, HONESTLY
#     Two layers above the connector catch the common shape of this, and both
#     catch it for the same incidental reason - they check that OUR COMPANY is
#     in the list, and an empty list does not contain it:
#
#       `real_tally`               refuses at startup
#       `Runtime.confirm_company`  refuses on every request, mid-session
#
#     So the app fails closed today. It fails closed by arithmetic on a list
#     that came back empty, not by anyone deciding the answer was not Tally -
#     which is the same shape as the `accounts_exist` accident this codebase
#     already criticises elsewhere. Anything that reads the connector without
#     going through those two - `bootstrap`, a script, a future caller - gets
#     "this company is empty" and believes it.
#
#     The write path is separately protected: `parse_import_response` reads
#     `created=0` out of a non-Tally body and `write_voucher` refuses on
#     `created < 1`. That is asserted below so the claim is not larger than the
#     evidence.


@pytest.mark.parametrize(
    "shape,body", NOT_TALLY_AT_ALL, ids=[n for n, _ in NOT_TALLY_AT_ALL]
)
def test_a_well_formed_answer_from_something_else_reads_as_empty_books_today(
    shape: str, body: str
) -> None:
    """WHAT WAS MEASURED. This test PINS A DEFECT; see the xfail below."""
    del shape
    assert real.parse_companies(body) == ()
    assert real.parse_ledger_names(body) == ()
    assert real.parse_closing_balances(body) == {}
    assert real.parse_vouchers(body).exported == ()


@pytest.mark.parametrize(
    "shape,body", NOT_TALLY_AT_ALL, ids=[n for n, _ in NOT_TALLY_AT_ALL]
)
@pytest.mark.xfail(strict=True, reason="DEFECT E1 - accountant/tallyio/real.py:1179")
def test_a_well_formed_answer_from_something_else_is_never_read_as_books(
    shape: str, body: str
) -> None:
    """DEFECT E1. The behaviour the connector should have, and does not.

    `parse_read_response` already owns the rule "a refusal is not data". The
    rule it is missing is one step earlier: an answer that is not a Tally
    response is not data either, and the cheapest honest test of that is
    whether the document is an `<ENVELOPE>`.

    The fix is a source change and the owner makes it.
    """
    del shape
    with pytest.raises(real.TallyResponseError):
        real.parse_vouchers(body)


@pytest.mark.parametrize(
    "shape,body", NOT_TALLY_AT_ALL, ids=[n for n, _ in NOT_TALLY_AT_ALL]
)
def test_a_write_against_something_that_is_not_tally_still_creates_nothing(
    shape: str, body: str
) -> None:
    """The bound on E1, measured rather than assumed.

    The duplicate pre-check reads the same empty answer, so the write is NOT
    refused there - it proceeds, imports into whatever is listening, and is
    then refused because a non-Tally body parses as `created=0`. One statutory
    entry is not created. The guard that saves it is the created count and not
    the duplicate check, which is worth knowing before anybody relies on either.
    """
    del shape
    transport = Scripted(vouchers=[body, body], imports=[body])
    client = a_client(transport)

    with pytest.raises(real.TallyRejected, match="created"):
        client.write_voucher(COMPANY, contract.a_voucher(), "ad_probe")

    assert transport.imports_sent == 1


def test_the_layer_above_e1_still_sees_no_company_and_therefore_still_refuses() -> None:
    """Why E1 is not an emergency, stated as the fact the mitigation rests on.

    Both mitigations - `real_tally` at startup and `Runtime.confirm_company` on
    every request - do the same arithmetic: is our company in the list Tally
    named? A non-Tally body yields an EMPTY list, our company is not in an empty
    list, and both refuse. Correct, and for a reason that has nothing to do with
    the answer not being Tally, which is why the xfail above stays open.
    """

    class NotTally:
        def send(self, payload: str, *, retry: bool) -> str:
            del payload, retry
            return "<html><body><h1>404 Not Found</h1></body></html>"

    assert a_client(NotTally()).list_companies() == ()
    assert COMPANY not in a_client(NotTally()).list_companies()
