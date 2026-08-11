"""The Azure backend, measured against responses this author wrote.

WHAT THESE TESTS PROVE, EXACTLY
-------------------------------
That `accountant/extract/azure.py` agrees with the sample responses in this
file, and that every failure mode reaches a person as a sentence rather than a
traceback.

WHAT THEY DO NOT PROVE
----------------------
**That Azure sends anything like these samples.** No request in this repository
has ever reached Azure Document Intelligence. The fixtures below were written
from Azure's documented response shape by the same author as the parser, so a
green run here means the two agree with each other — which is a weaker claim
than it looks, and is exactly the claim being made.

Evidence label: `UNVERIFIED_VENDOR_SHAPE`. It becomes something stronger the
first time a real invoice goes through a real endpoint and the answer is
recorded, and not before. `docs/OWNER_WORK.md` carries that as owner work.

NO NETWORK IS TOUCHED HERE
--------------------------
Every test injects an opener. A test that reached the internet would be a test
that fails when a train goes into a tunnel, and it would spend the owner's money
on every run.
"""

from __future__ import annotations

import datetime
import io
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from accountant.extract import azure
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.extract.service import ALL_REASONS
from accountant.reader import azure as transport

ENDPOINT = "https://example-docint.cognitiveservices.azure.com"
KEY = "not-a-real-key"
CREDS = {transport.ENV_ENDPOINT: ENDPOINT, transport.ENV_KEY: KEY}

BILL = b"%PDF-1.7 a scanned bill"
PDF = "application/pdf"


# ---------------------------------------------------------------------------
# the fake wire
# ---------------------------------------------------------------------------


class FakeResponse:
    """Just enough of an HTTP response for the transport's `Response` Protocol."""

    def __init__(self, body: bytes, location: str | None = None) -> None:
        self._body = body
        self._location = location

    def read(self) -> bytes:
        return self._body

    def getheader(self, name: str, /) -> str | None:
        return self._location if name == "Operation-Location" else None

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *details: object) -> None:
        return None


class Wire:
    """Scripted answers, and a record of what was sent.

    Records rather than merely answers, because two of the tests below are about
    what left this machine — the key in a header, the document in a body — and a
    mock that only replies cannot be asked about that.
    """

    def __init__(self, *answers: FakeResponse | BaseException) -> None:
        self._answers = list(answers)
        self.sent: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(
        self, request: urllib.request.Request, /, *, timeout: float
    ) -> FakeResponse:
        # Recorded rather than ignored: the transport must pass its deadline
        # down to the socket, and a `_` here would let that stop happening
        # without any test noticing.
        self.timeouts.append(timeout)
        self.sent.append(request)
        if not self._answers:
            raise AssertionError("the transport asked more times than scripted")
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def analysed(fields: Mapping[str, object], content: str = "") -> bytes:
    """A succeeded Azure result carrying `fields`, as JSON bytes."""
    body: dict[str, Any] = {
        "status": "succeeded",
        "analyzeResult": {"content": content, "documents": [{"fields": dict(fields)}]},
    }
    return json.dumps(body).encode()


def raw(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode()


def currency(amount: str) -> dict[str, object]:
    """Azure states money as a JSON number. Written as raw JSON text so the
    fixture carries the digits Azure would send rather than a Python float that
    has already lost them."""
    return {"valueCurrency": json.loads(f'{{"amount": {amount}}}')}


def sender(*answers: FakeResponse | BaseException) -> transport.AzureTransport:
    return transport.AzureTransport(
        transport.Credentials(endpoint=ENDPOINT, key=KEY),
        opener=Wire(*answers),
        sleep=lambda _seconds: None,
        monotonic=_ticking(),
    )


def _ticking(step: float = 0.5):
    """A clock that advances on every read, so a poll loop cannot hang a test."""
    now = [0.0]

    def tick() -> float:
        now[0] += step
        return now[0]

    return tick


def extract(*answers: FakeResponse | BaseException, mime: str = PDF) -> ExtractedRecord:
    from accountant.extract.service import ServiceExtractor

    backend = ServiceExtractor(azure.AzureCall(sender(*answers)), name="azure")
    return backend.extract(BILL, mime)


# ---------------------------------------------------------------------------
# 1. a bill Azure could read
# ---------------------------------------------------------------------------

WHOLE_BILL: Mapping[str, object] = {
    "VendorName": {"valueString": "Sharma Traders"},
    "InvoiceDate": {"valueDate": "2026-08-10"},
    "InvoiceTotal": currency("4200.00"),
    "TotalTax": currency("640.68"),
}


def test_a_bill_azure_read_becomes_a_record_with_every_field_filled() -> None:
    record = extract(FakeResponse(analysed(WHOLE_BILL)))

    assert record.party == "Sharma Traders"
    assert record.date == datetime.date(2026, 8, 10)
    assert record.total_paise == 420000
    assert record.tax_paise == 64068
    assert record.backend == "azure"
    assert all(record.per_field_source[f] == "azure" for f in ExtractedRecord.FIELDS), (
        record.per_field_source
    )


def test_azure_transcription_is_carried_and_never_parsed() -> None:
    record = extract(FakeResponse(analysed(WHOLE_BILL, content="SHARMA TRADERS\n4200")))

    assert "SHARMA TRADERS" in record.raw_text
    # Carried, not mined. The total came from the named field, and a number
    # sitting in the transcription is not permitted to become one.
    assert record.total_paise == 420000


# ---------------------------------------------------------------------------
# 2. a field Azure did not find is `not_found`, never blank and never guessed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("absent", sorted(azure.FIELD_NAMES))
def test_a_field_azure_did_not_report_becomes_an_explicit_not_found(
    absent: str,
) -> None:
    fields = {k: v for k, v in WHOLE_BILL.items() if k != azure.FIELD_NAMES[absent]}

    record = extract(FakeResponse(analysed(fields)))

    assert getattr(record, absent) is None
    assert record.per_field_source[absent].startswith(NOT_FOUND), (
        f"{absent} came back blank instead of saying it was not found"
    )
    # The OTHER three still arrived. A missing field is not a failed bill.
    for other in ExtractedRecord.FIELDS:
        if other != absent:
            assert getattr(record, other) is not None


def test_a_document_azure_found_no_invoice_in_is_all_not_found_and_not_an_error() -> (
    None
):
    """Azure read the file and found no invoice. That is an answer, not a fault:
    the person types the bill by hand and nothing is guessed for them."""
    body = raw({"status": "succeeded", "analyzeResult": {"documents": []}})

    record = extract(FakeResponse(body))

    assert record.complete
    assert all(
        record.per_field_source[f].startswith(NOT_FOUND) for f in ExtractedRecord.FIELDS
    )


def test_an_unrecognised_field_shape_is_not_found_rather_than_a_guess() -> None:
    """A field stated with a key this parser does not know is a field it cannot
    read. `not_found` says so; anything else would be inventing a value."""
    fields = dict(WHOLE_BILL) | {"VendorName": {"valueBoolean": True}}

    record = extract(FakeResponse(analysed(fields)))

    assert record.party is None
    assert record.per_field_source["party"].startswith(NOT_FOUND)


# ---------------------------------------------------------------------------
# 3. money. The reason this file is careful.
# ---------------------------------------------------------------------------


def test_an_amount_is_exact_paise_and_never_a_rounded_float() -> None:
    """`4166.67` through binary float is 4166.669999999999... and `round(x*100)`
    has to guess. Decimal does not guess, and this is the assertion that says the
    exact path is the one taken."""
    fields = dict(WHOLE_BILL) | {"InvoiceTotal": currency("4166.67")}

    record = extract(FakeResponse(analysed(fields)))

    assert record.total_paise == 416667


def test_a_very_large_total_does_not_drift_by_a_paise() -> None:
    """The measured failure in `accountant/extract/adapter.py` was one paise of
    drift above roughly fourteen digits. A paid service producing the number
    does not make the drift acceptable."""
    fields = dict(WHOLE_BILL) | {"InvoiceTotal": currency("92233720368547.75")}

    record = extract(FakeResponse(analysed(fields)))

    assert record.total_paise == 9223372036854775


def test_a_fraction_of_a_paise_is_refused_rather_than_rounded() -> None:
    """There is no such thing as a third of a paise in a ledger. Rounding it
    here would put a number in somebody's books that nobody typed."""
    fields = dict(WHOLE_BILL) | {"InvoiceTotal": currency("4200.005")}

    record = extract(FakeResponse(analysed(fields)))

    assert record.total_paise is None
    assert "whole number of paise" in record.per_field_source["total_paise"]


def test_an_amount_that_arrives_as_true_is_refused_rather_than_becoming_one_paise() -> (
    None
):
    """`isinstance(True, int)` is True in Python, so an unguarded conversion
    turns a boolean into one paise."""
    fields = dict(WHOLE_BILL) | {"InvoiceTotal": {"valueCurrency": {"amount": True}}}

    record = extract(FakeResponse(analysed(fields)))

    assert record.total_paise is None
    assert "true/false" in record.per_field_source["total_paise"]


def test_the_transport_decodes_money_as_decimal_and_not_as_float() -> None:
    """The guarantee above is only available if the digits survive decoding.
    Asserted at the transport, because `json.loads` without `parse_float` has
    already lost them by the time the parser sees the value."""
    amount = transport.decode(b'{"amount": 4166.67}')["amount"]

    assert isinstance(amount, Decimal)
    # `str`, not `==`. `Decimal("4166.67") == 4166.67` is False anyway, but the
    # assertion that matters is that the DIGITS AZURE SENT survived, and only
    # the text shows that.
    assert str(amount) == "4166.67"


# ---------------------------------------------------------------------------
# 4. a date
# ---------------------------------------------------------------------------


def test_a_date_that_is_not_a_date_is_refused_rather_than_interpreted() -> None:
    fields = dict(WHOLE_BILL) | {"InvoiceDate": {"valueDate": "10/08/2026"}}

    record = extract(FakeResponse(analysed(fields)))

    assert record.date is None
    assert "not a date" in record.per_field_source["date"]


def test_a_party_of_only_spaces_is_not_found_rather_than_a_blank_name() -> None:
    fields = dict(WHOLE_BILL) | {"VendorName": {"valueString": "   "}}

    record = extract(FakeResponse(analysed(fields)))

    assert record.party is None
    assert record.per_field_source["party"].startswith(NOT_FOUND)


# ---------------------------------------------------------------------------
# 5. every way the service can fail reaches a person as a sentence
# ---------------------------------------------------------------------------


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ENDPOINT, code, "no", {}, io.BytesIO(b""))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, transport.NOT_SIGNED_IN),
        (403, transport.NOT_SIGNED_IN),
        (429, transport.RATE_LIMITED),
        (408, transport.TIMED_OUT),
        (500, transport.UNAVAILABLE),
    ],
)
def test_an_http_status_becomes_the_kind_that_names_it(
    code: int, expected: str
) -> None:
    with pytest.raises(transport.AzureCallFailed) as caught:
        sender(http_error(code)).analyse(BILL, PDF)

    assert caught.value.kind == expected


@pytest.mark.parametrize(
    ("thrown", "expected"),
    [
        (urllib.error.URLError(ConnectionRefusedError()), transport.REFUSED),
        (urllib.error.URLError(TimeoutError()), transport.TIMED_OUT),
        (urllib.error.URLError(PermissionError()), transport.NOT_SIGNED_IN),
        (urllib.error.URLError(OSError()), transport.UNAVAILABLE),
        (TimeoutError(), transport.TIMED_OUT),
    ],
)
def test_a_transport_failure_becomes_the_kind_that_names_it(
    thrown: BaseException, expected: str
) -> None:
    with pytest.raises(transport.AzureCallFailed) as caught:
        sender(thrown).analyse(BILL, PDF)

    assert caught.value.kind == expected


def test_a_refusal_is_not_reported_as_a_generic_outage() -> None:
    """ORDER MATTERS: `ConnectionRefusedError` subclasses `ConnectionError`.
    Tested in the wrong order, every refusal reads as an outage and a person is
    told to wait when nothing is listening."""
    with pytest.raises(transport.AzureCallFailed) as caught:
        sender(urllib.error.URLError(ConnectionRefusedError())).analyse(BILL, PDF)

    assert caught.value.kind != transport.UNAVAILABLE


def test_every_kind_the_transport_can_raise_has_words_a_person_can_read() -> None:
    """THE CONTROL ON THE TWO TESTS ABOVE. Without it, a new failure mode could
    be added to the transport and arrive here as an unhandled default that no
    test ever looks at."""
    missing = [k for k in transport.ALL_KINDS if k not in azure.REASON_FOR_KIND]

    assert missing == [], f"no sentence for transport kind(s): {missing}"


@pytest.mark.parametrize("kind", transport.ALL_KINDS)
def test_a_failed_call_leaves_every_field_not_found_and_names_the_reason(
    kind: str,
) -> None:
    record = extract(transport.AzureCallFailed(kind))

    assert record.complete
    assert all(record.per_field_source[f] for f in ExtractedRecord.FIELDS)
    assert record.total_paise is None


def test_the_service_saying_it_failed_is_not_a_silence() -> None:
    body = raw({"status": "failed"})

    record = extract(FakeResponse(b"", "https://poll"), FakeResponse(body))

    assert record.total_paise is None
    assert all(
        record.per_field_source[f].startswith(NOT_FOUND) for f in ExtractedRecord.FIELDS
    )


def test_an_empty_answer_is_named_as_empty_and_not_as_a_bill_with_no_fields() -> None:
    with pytest.raises(transport.AzureCallFailed) as caught:
        sender(FakeResponse(b"   ")).analyse(BILL, PDF)

    assert caught.value.kind == transport.EMPTY


def test_an_answer_that_is_not_json_is_named_as_malformed() -> None:
    with pytest.raises(transport.AzureCallFailed) as caught:
        sender(FakeResponse(b"<html>gateway</html>")).analyse(BILL, PDF)

    assert caught.value.kind == transport.MALFORMED


def test_an_answer_about_several_documents_is_refused_rather_than_picked_from() -> None:
    """One bill was sent. An answer describing three is not an answer to choose
    a winner from; the first of three would be a guess presented as a reading."""
    body = raw(
        {
            "status": "succeeded",
            "analyzeResult": {"documents": [{"fields": {}}, {"fields": {}}]},
        }
    )

    record = extract(FakeResponse(body))

    assert "2 documents" in record.per_field_source["party"]


def test_every_refusal_a_person_sees_is_one_of_the_sentences_that_exist() -> None:
    """A reason invented at the call site is a reason no test can pin, and an
    unpinnable message is one that can drift into silence."""
    for kind in transport.ALL_KINDS:
        record = extract(transport.AzureCallFailed(kind))
        reason = record.per_field_source["party"]
        assert any(sentence in reason for sentence in ALL_REASONS), reason


# ---------------------------------------------------------------------------
# 6. what leaves this machine
# ---------------------------------------------------------------------------


def test_the_document_is_sent_to_the_pinned_model_and_api_version() -> None:
    wire = Wire(FakeResponse(analysed(WHOLE_BILL)))
    transport.AzureTransport(
        transport.Credentials(endpoint=ENDPOINT, key=KEY), opener=wire
    ).analyse(BILL, PDF)

    sent = wire.sent[0]
    assert sent.full_url.startswith(ENDPOINT)
    assert transport.MODEL in sent.full_url
    assert f"api-version={transport.API_VERSION}" in sent.full_url
    assert sent.data == BILL
    assert sent.get_method() == "POST"


def test_the_key_travels_in_the_header_and_never_in_the_url() -> None:
    """A key in a query string reaches every proxy log between here and Azure."""
    wire = Wire(FakeResponse(analysed(WHOLE_BILL)))
    transport.AzureTransport(
        transport.Credentials(endpoint=ENDPOINT, key=KEY), opener=wire
    ).analyse(BILL, PDF)

    sent = wire.sent[0]
    assert KEY not in sent.full_url
    assert sent.get_header("Ocp-apim-subscription-key") == KEY


def test_the_key_is_not_printed_when_the_credentials_are() -> None:
    """A dataclass prints its fields. A credential printed once into a log or a
    traceback has left the machine, so it is not there to print."""
    printed = repr(transport.Credentials(endpoint=ENDPOINT, key=KEY))

    assert KEY not in printed
    assert "redacted" in printed


def test_the_deadline_reaches_the_socket_rather_than_only_the_poll_loop() -> None:
    """A timeout the poll loop honours but the socket never sees is a timeout
    that does not exist: one hung connection blocks for the operating system's
    default, which is minutes, with a person waiting at an upload form."""
    wire = Wire(FakeResponse(analysed(WHOLE_BILL)))
    transport.AzureTransport(
        transport.Credentials(endpoint=ENDPOINT, key=KEY),
        opener=wire,
        timeout_seconds=7.0,
    ).analyse(BILL, PDF)

    assert wire.timeouts == [7.0]


def test_a_plain_http_endpoint_is_refused() -> None:
    """A bill carries a customer's supplier names and amounts."""
    with pytest.raises(ValueError, match="https only"):
        transport.Credentials(endpoint="http://docint.example.com", key=KEY)


def test_an_endpoint_with_no_host_is_refused() -> None:
    with pytest.raises(ValueError, match="names no host"):
        transport.Credentials(endpoint="https:///nothing", key=KEY)


def test_a_blank_key_is_refused_rather_than_sent() -> None:
    with pytest.raises(ValueError, match="key is blank"):
        transport.Credentials(endpoint=ENDPOINT, key="   ")


# ---------------------------------------------------------------------------
# 7. polling
# ---------------------------------------------------------------------------


def test_a_202_is_polled_until_the_answer_is_ready() -> None:
    record = extract(
        FakeResponse(b"", "https://example.com/operations/1"),
        FakeResponse(raw({"status": "running"})),
        FakeResponse(analysed(WHOLE_BILL)),
    )

    assert record.total_paise == 420000


def test_polling_gives_up_and_says_so_rather_than_waiting_forever() -> None:
    running = raw({"status": "running"})
    sending = transport.AzureTransport(
        transport.Credentials(endpoint=ENDPOINT, key=KEY),
        opener=Wire(*[FakeResponse(b"", "https://poll")] + [FakeResponse(running)] * 8),
        timeout_seconds=2.0,
        sleep=lambda _seconds: None,
        monotonic=_ticking(),
    )

    with pytest.raises(transport.AzureCallFailed) as caught:
        sending.analyse(BILL, PDF)

    assert caught.value.kind == transport.TIMED_OUT


# ---------------------------------------------------------------------------
# 8. the media-type split
# ---------------------------------------------------------------------------


def test_a_sentence_a_person_typed_never_reaches_the_reading_service() -> None:
    """`app.py:1430` gives ONE extractor to the whole runtime, so `/entry` and
    `/upload` share it. Sending typed text to an invoice reader would break the
    core path and charge a fee to parse our own form field."""
    exploded = azure.ByMediaType(
        typed=azure.TypedTextExtractor(),
        documents=azure.refusing("the reading service was reached, and must not be"),
    )

    record = exploded.extract(b"paid Sharma Traders 4200 for cement", "text/plain")

    assert record.backend == "typed_text"
    assert record.total_paise == 420000


def test_the_charset_parameter_does_not_send_typed_text_to_the_reader() -> None:
    """A real form sends `text/plain; charset=utf-8`."""
    exploded = azure.ByMediaType(
        typed=azure.TypedTextExtractor(),
        documents=azure.refusing("the reading service was reached, and must not be"),
    )

    record = exploded.extract(b"paid Sharma Traders 4200", "text/plain; charset=utf-8")

    assert record.backend == "typed_text"


def test_a_document_does_reach_the_reading_service() -> None:
    """THE CONTROL. Without it the two tests above pass on a router that sends
    everything to the typed-text parser, which is the bug that motivated
    `TYPED_TEXT_MIME`: PDF bytes measured as 170 paise, JPEG as 3,133,700."""
    reached: list[str] = []

    class Recording:
        def extract(self, _data: bytes, mime: str) -> ExtractedRecord:
            reached.append(mime)
            return azure.refusing("recorded").extract(b"", mime)

    router = azure.ByMediaType(typed=azure.TypedTextExtractor(), documents=Recording())
    router.extract(BILL, PDF)

    assert reached == [PDF]


# ---------------------------------------------------------------------------
# 9. an unconfigured deployment
# ---------------------------------------------------------------------------


def test_with_no_credentials_a_document_is_refused_with_both_variables_named() -> None:
    """The refusal is the fix instruction. A person meeting it should be able to
    paste rather than investigate."""
    record = azure.build_azure({}).extract(BILL, PDF)

    reason = record.per_field_source["party"]
    assert transport.ENV_ENDPOINT in reason
    assert transport.ENV_KEY in reason


def test_with_no_credentials_typed_text_still_works() -> None:
    """An unconfigured reader must not take the core product down with it."""
    record = azure.build_azure({}).extract(b"paid Sharma Traders 4200", "text/plain")

    assert record.total_paise == 420000


def test_half_a_credential_is_the_same_as_none_and_not_an_attempt() -> None:
    for half in ({transport.ENV_ENDPOINT: ENDPOINT}, {transport.ENV_KEY: KEY}):
        assert transport.credentials_from_env(half) is None


def test_a_misconfigured_endpoint_refuses_rather_than_raising() -> None:
    """`registry.build` is reached from a web request. An exception here is a
    500 for a person whose only problem is a typo in a deployment setting."""
    record = azure.build_azure(
        {transport.ENV_ENDPOINT: "http://docint.example.com", transport.ENV_KEY: KEY}
    ).extract(BILL, PDF)

    assert "misconfigured" in record.per_field_source["party"]


def test_credentials_are_read_from_both_variables_when_both_are_set() -> None:
    """THE CONTROL on the three tests above: without it they all pass against a
    function that returns None unconditionally."""
    creds = transport.credentials_from_env(CREDS)

    assert creds is not None
    assert creds.endpoint == ENDPOINT
