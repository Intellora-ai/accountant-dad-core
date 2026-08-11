"""Azure Document Intelligence, on this side of the boundary.

D-23 IS NOT CLOSED BY THIS FILE. `docs/DECISIONS.md:579` asks which INPUT TYPES
must work at first launch — typed text, PDF, PNG, JPG, DOCX — and that is the
owner's answer to give. Selecting a vendor makes the other four possible; it
does not decide that they ship. No account exists either.

WHAT THIS FILE IS
-----------------
The `ServiceCall` that `accountant/extract/service.py` has been waiting for
since `reader_service` was registered as needing a transport. It renames Azure's
fields to the four this application asks about, and it does not open a socket.

The socket is in `accountant/reader/azure.py`. The split is not taste; it is the
only shape two existing guards leave standing:

    tests/test_no_reader.py         no `urllib` inside accountant/extract/
    tests/test_adapter_contract.py  nothing outside accountant/extract/ may
                                    import extraction internals

Network there, meaning here, a plain mapping crossing between. Both drafts that
put the two together failed one guard or the other, and the guards were right
both times.

WHY THERE IS NO PARSING OF ANY KIND HERE
-----------------------------------------
No OCR, no layout, no field detection, no guessing. Azure read the document.
This picks four values out of what it said and refuses anything it cannot
account for. `service.ServiceExtractor` then applies the rule that decides every
case: **an answer we cannot fully account for is not a partial answer, it is a
failed one.**

WHY EVERY NAMED FIELD IS ALWAYS A KEY
--------------------------------------
When Azure does not report a field the value is `None`, which becomes an
explicit `not_found` with a reason a person can read. Leaving the key out
entirely means something different, and `service._adapt` treats it that way: it
refuses the whole response as `INCOMPLETE`.

That distinction is load-bearing. "Azure looked and found no tax line" and
"Azure stopped halfway" lead to opposite actions, and collapsing them is how
half a bill becomes a whole voucher.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
**Written against Azure's documented response shape, never against the live
service.** No request in this repository has ever reached Azure. The tests
supply responses this author wrote, so they prove this file agrees with those
samples — not that either agrees with Azure. The evidence label is
`UNVERIFIED_VENDOR_SHAPE`. `docs/OWNER_WORK.md` records what remains.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from accountant.extract.adapter import (
    TYPED_TEXT_MIME,
    ExtractedRecord,
    Extractor,
    TypedTextExtractor,
)
from accountant.extract.service import (
    DOCUMENT_KEY,
    EMPTY,
    MALFORMED,
    NOT_SIGNED_IN,
    RATE_LIMITED,
    TEXT_KEY,
    TIMED_OUT,
    UNAVAILABLE,
    ExtractionFailed,
    ServiceExtractor,
)
from accountant.reader import azure as transport

#: Azure's field names on the left, ours on the right. The whole mapping in one
#: place, so "which Azure field became the total" is answered by reading rather
#: than by tracing.
FIELD_NAMES: Final[Mapping[str, str]] = {
    "date": "InvoiceDate",
    "party": "VendorName",
    "total_paise": "InvoiceTotal",
    "tax_paise": "TotalTax",
}

#: Every `kind` the transport can raise, in the words a person reads. Exhaustive
#: on purpose: `tests/test_azure_extractor.py` asserts this covers
#: `transport.ALL_KINDS`, so a new failure mode over there cannot arrive here as
#: a generic outage nobody notices.
REASON_FOR_KIND: Final[Mapping[str, str]] = {
    transport.NOT_SIGNED_IN: NOT_SIGNED_IN,
    transport.RATE_LIMITED: RATE_LIMITED,
    transport.TIMED_OUT: TIMED_OUT,
    transport.REFUSED: "the reading service refused the connection",
    transport.UNAVAILABLE: UNAVAILABLE,
    transport.EMPTY: EMPTY,
    transport.MALFORMED: MALFORMED,
    transport.READ_FAILED: (
        f"{UNAVAILABLE}: the reading service says it could not read this document"
    ),
}

#: Azure's own transcription. Carried so a person can see what it thought the
#: bill said, and never parsed. `service.py` states the same rule for `text`.
_CONTENT: Final = "content"

_HUNDRED: Final = Decimal(100)


def _to_paise(value: object, field: str) -> int | None:
    """Exact whole paise, or a refusal. Never a rounded number.

    The transport decoded with `parse_float=Decimal`, so the amount still holds
    the digits Azure sent. `amount * 100` is therefore exact, and a fractional
    paise is visible here instead of absorbed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # `isinstance(True, int)` is True, and True would become one paise.
        raise ExtractionFailed(f"{MALFORMED}: {field} arrived as a true/false value")
    if isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ExtractionFailed(
                f"{MALFORMED}: {field} arrived as {value!r}, which is not an amount"
            ) from None
    else:
        raise ExtractionFailed(
            f"{MALFORMED}: {field} arrived as {type(value).__name__}"
        )
    paise = amount * _HUNDRED
    if paise != paise.to_integral_value():
        raise ExtractionFailed(
            f"{MALFORMED}: {field} is {amount}, which is not a whole number of "
            "paise. Rounding it here would put a number in somebody's books "
            "that nobody typed."
        )
    return int(paise)


def _to_date(value: object) -> datetime.date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionFailed(
            f"{MALFORMED}: the date arrived as {type(value).__name__}"
        )
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ExtractionFailed(
            f"{MALFORMED}: the date {value!r} is not a date"
        ) from None


def _to_party(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionFailed(
            f"{MALFORMED}: the party arrived as {type(value).__name__}"
        )
    return value.strip() or None


def _field_value(field: object) -> object:
    """One Azure field reduced to the value we want, or `None`.

    Azure states a field as one of several typed keys — `valueString`,
    `valueDate`, `valueCurrency`, `valueNumber`. Only the ones this application
    asks for are read. An unrecognised shape returns `None`, which becomes an
    explicit `not_found`, because a field we cannot read is not a field we may
    guess at.
    """
    if not isinstance(field, Mapping):
        return None
    known = cast("Mapping[str, object]", field)
    currency = known.get("valueCurrency")
    if isinstance(currency, Mapping):
        return cast("Mapping[str, object]", currency).get("amount")
    for key in ("valueString", "valueDate", "valueNumber"):
        if key in known:
            return known[key]
    return None


def _fields(body: Mapping[str, object]) -> Mapping[str, object]:
    """The fields of the one analysed document, or an empty mapping.

    Azure returns a list because one upload can hold several invoices. This
    application sends one bill, so a list of anything other than one is not a
    case to pick a winner from: the first of three would be a guess presented as
    a reading.
    """
    result = body.get("analyzeResult")
    if not isinstance(result, Mapping):
        raise ExtractionFailed(f"{MALFORMED}: the answer has no analyzeResult")
    documents = cast("Mapping[str, object]", result).get("documents")
    if not isinstance(documents, list):
        raise ExtractionFailed(f"{MALFORMED}: analyzeResult lists no documents")
    found = cast("list[object]", documents)
    if not found:
        # Not a failure. Azure read the file and found no invoice in it, which
        # is an answer: every field not_found, and the person types it by hand.
        return {}
    if len(found) > 1:
        raise ExtractionFailed(
            f"{MALFORMED}: the answer describes {len(found)} documents and we "
            "sent one bill. Choosing among them would be a guess."
        )
    only = found[0]
    if not isinstance(only, Mapping):
        raise ExtractionFailed(f"{MALFORMED}: the document is not a set of fields")
    fields = cast("Mapping[str, object]", only).get("fields")
    if fields is None:
        return {}
    if not isinstance(fields, Mapping):
        raise ExtractionFailed(f"{MALFORMED}: the document's fields are not named")
    return cast("Mapping[str, object]", fields)


def _content(body: Mapping[str, object]) -> str:
    result = body.get("analyzeResult")
    if not isinstance(result, Mapping):
        return ""
    text = cast("Mapping[str, object]", result).get(_CONTENT)
    return text if isinstance(text, str) else ""


class AzureCall:
    """The `ServiceCall`: ask the transport, rename what comes back."""

    def __init__(self, sender: transport.AzureTransport) -> None:
        self._sender = sender

    def __call__(self, data: bytes, mime: str, document_key: str) -> object:
        try:
            body = self._sender.analyse(data, mime)
        except transport.AzureCallFailed as failed:
            reason = REASON_FOR_KIND.get(failed.kind)
            if reason is None:
                # A kind this file has never heard of. Named rather than
                # smoothed: a silent default here is how a new failure mode
                # spends a year looking like a network blip.
                reason = f"{UNAVAILABLE} (unhandled transport kind {failed.kind!r})"
            raise ExtractionFailed(
                f"{reason}: {failed.detail}" if failed.detail else reason
            ) from None
        fields = _fields(body)
        answer: dict[str, object] = {
            DOCUMENT_KEY: document_key,
            "date": _to_date(_field_value(fields.get(FIELD_NAMES["date"]))),
            "party": _to_party(_field_value(fields.get(FIELD_NAMES["party"]))),
            TEXT_KEY: _content(body),
        }
        for ours in ("total_paise", "tax_paise"):
            answer[ours] = _to_paise(_field_value(fields.get(FIELD_NAMES[ours])), ours)
        return answer


class ByMediaType:
    """Typed text to our own parser, documents to Azure. One `Extractor`.

    WHY THIS EXISTS, MEASURED
    -------------------------
    `accountant/web/app.py:1430` builds ONE extractor and hands it to the whole
    runtime:

        extractor=guarded(default_extractor() if extractor is None else extractor)

    That one object serves both `POST /entry`, where a person types
    "paid Sharma Traders 4200 for cement", and `POST /upload`, where a PDF
    arrives. Pointing `DEFAULT_BACKEND` straight at Azure would send every typed
    sentence to an invoice-reading service: the core path breaks, and a
    deployment pays a per-document fee to parse its own form field.

    Typed text was never a vendor decision. D-23 asks who reads a DOCUMENT.
    `adapter.TYPED_TEXT_MIME` already exists because the typed-text parser was
    measured inventing totals out of PDF and PNG bytes — 3,133,700 paise from a
    JPEG comment — so the two paths were already known to need keeping apart.

    This routes on the caller's own declared media type. It reads no bytes.
    """

    def __init__(self, typed: Extractor, documents: Extractor) -> None:
        self._typed = typed
        self._documents = documents
        self.name = "azure"

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        if mime.split(";", 1)[0].strip().lower() == TYPED_TEXT_MIME:
            return self._typed.extract(data, mime)
        return self._documents.extract(data, mime)


def build_azure(environ: Mapping[str, str] | None = None) -> Extractor:
    """The backend this application runs with: Azure for documents.

    Typed text goes to `TypedTextExtractor` — `ByMediaType` says why that is not
    a second vendor decision. `azure_only` is the Azure path on its own.
    """
    return ByMediaType(TypedTextExtractor(), azure_only(environ))


def azure_only(environ: Mapping[str, str] | None = None) -> Extractor:
    """Azure for every media type, including ones it should not be asked about.

    Separate from `build_azure` so a test can measure the Azure path directly,
    without the router deciding the answer before Azure is reached.

    Never raises. `registry.build` is reached from a web request, and an
    unconfigured deployment should answer the person with a sentence rather than
    a 500. The refusal names both variables, so the fix is a paste rather than
    an investigation.
    """
    # BOTH calls are inside the `try`, not just the second. `Credentials`
    # validates in `__post_init__`, so `credentials_from_env` is itself a place
    # a bad setting raises — and the first draft guarded only the line below it.
    # A deployment with an `http://` endpoint would have met a 500 from this
    # function, which is the one thing its docstring promises cannot happen.
    # Found by `test_a_misconfigured_endpoint_refuses_rather_than_raising`.
    try:
        credentials = transport.credentials_from_env(environ)
        if credentials is None:
            return refusing(
                "the reading service is not configured: set "
                f"{transport.ENV_ENDPOINT} and {transport.ENV_KEY}. Until then a "
                "bill can still be typed in by hand, and nothing is guessed."
            )
        sender = transport.AzureTransport(credentials)
    except ValueError as wrong:
        return refusing(f"the reading service is misconfigured: {wrong}")
    return ServiceExtractor(AzureCall(sender), name="azure")


def refusing(reason: str) -> Extractor:
    """A backend whose every answer is all-`not_found` with `reason` attached."""

    def refuse(_data: bytes, _mime: str, _key: str) -> object:
        raise ExtractionFailed(reason)

    return ServiceExtractor(refuse, name="azure")
