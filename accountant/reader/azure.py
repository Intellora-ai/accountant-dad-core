"""HTTP to Azure Document Intelligence. Nothing else lives here.

WHY THIS FILE KNOWS NOTHING ABOUT `accountant.extract`
-------------------------------------------------------
`tests/test_adapter_contract.py` holds a boundary that is easy to state and was
measured rather than asserted: a module outside `accountant/extract/` may import
only the CONTRACT — `ExtractedRecord`, `Extractor`, `LineItem`, `NOT_FOUND`,
`default_extractor`, `guarded`. The first draft of this transport imported
thirteen other names and the guard named every one of them.

So this file imports none of them. It carries bytes to Azure and brings back
whatever Azure said, decoded. It does not know what an `ExtractedRecord` is,
which four fields the application asks about, or what any of them mean.
`accountant/extract/azure.py` does that, on the other side of the boundary.

The pairing is deliberate. `tests/test_no_reader.py` forbids `urllib` inside
`accountant/extract/`; the contract test forbids extraction internals outside
it. Together they leave exactly one shape: network here, meaning there, and a
plain mapping crossing between them.

FAILURES LEAVE HERE AS A KIND, NOT AS A SENTENCE
-------------------------------------------------
`AzureCallFailed` carries a short `kind` string. It does not carry the words a
person reads, because those words live in `accountant/extract/service.py` and
importing them here is the exact thing the boundary forbids.

The transport is still the only thing that sees the HTTP status, so it is the
only thing that can tell a 401 from a 429 — it reports which, and the other side
turns that into a sentence.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
**Written against Azure's documented shape, never against the live service.**
No request in this repository has ever reached Azure. Its tests supply responses
this author wrote, so they prove the caller agrees with those samples — not that
either agrees with Azure. The evidence label is `UNVERIFIED_VENDOR_SHAPE` and
must not be written down as anything stronger.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Protocol, cast

#: The two settings a deployment must supply. Named like every other setting in
#: this application so `docs/DEPLOY.md` has one convention, not two.
ENV_ENDPOINT: Final = "ACCOUNTANT_AZURE_ENDPOINT"
ENV_KEY: Final = "ACCOUNTANT_AZURE_KEY"

#: Pinned, not floating. An API version resolved at request time is a version
#: nobody reviewed, and a response shape that changes under a running deployment
#: is the same class of problem as an action tag that moves.
API_VERSION: Final = "2024-11-30"

#: The prebuilt invoice model. This application does not train models and does
#: not intend to; a custom model would be a reader, and we do not write readers.
MODEL: Final = "prebuilt-invoice"

#: NUMBERS CHOSEN BY THE IMPLEMENTER, NOT BY THE OWNER, recorded as such in
#: `docs/OWNER_WORK.md`. Constructor arguments, so a deployment that disagrees
#: says so without editing this file.
#:
#: Azure's invoice analysis is asynchronous: the POST returns 202 and a URL to
#: poll. These bound how long a person stands at an upload form waiting.
DEFAULT_TIMEOUT_SECONDS: Final = 60.0
DEFAULT_POLL_SECONDS: Final = 1.0

#: Every `kind` this module can fail with. Enumerable so the other side can be
#: tested for handling all of them, and so a new one cannot appear unnoticed.
NOT_SIGNED_IN: Final = "not_signed_in"
RATE_LIMITED: Final = "rate_limited"
TIMED_OUT: Final = "timed_out"
REFUSED: Final = "refused"
UNAVAILABLE: Final = "unavailable"
EMPTY: Final = "empty"
MALFORMED: Final = "malformed"
READ_FAILED: Final = "read_failed"

ALL_KINDS: Final = (
    NOT_SIGNED_IN,
    RATE_LIMITED,
    TIMED_OUT,
    REFUSED,
    UNAVAILABLE,
    EMPTY,
    MALFORMED,
    READ_FAILED,
)

_HTTPS: Final = "https"


class AzureCallFailed(Exception):
    """No answer, and the short reason why. `kind` is one of `ALL_KINDS`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class Response(Protocol):
    """The part of an HTTP response this module uses, and nothing more."""

    def read(self) -> bytes: ...

    def getheader(self, name: str, /) -> str | None: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *details: object) -> None: ...


class Opener(Protocol):
    """Anything that sends a prepared request and returns a response.

    Wider than the GET-only `Opener` in `accountant/ingest/fetch.py`, which
    cannot carry a body or headers. That module is left alone rather than
    widened for a caller it does not have.
    """

    def __call__(
        self, request: urllib.request.Request, /, *, timeout: float
    ) -> Response: ...


URLLIB_OPENER: Opener = cast("Opener", urllib.request.urlopen)


@dataclass(frozen=True)
class Credentials:
    """Where to send the document, and the key that gets it read.

    `__repr__` is overridden because a dataclass prints its fields, and a
    credential printed once into a log or a traceback has left the machine.
    `accountant/redact.py` scrubs application logs; this makes the value not be
    there to scrub, which is the stronger guarantee.
    """

    endpoint: str
    key: str

    def __repr__(self) -> str:
        return f"Credentials(endpoint={self.endpoint!r}, key=<redacted>)"

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.endpoint)
        if parsed.scheme != _HTTPS:
            raise ValueError(
                f"refusing an Azure endpoint on {parsed.scheme or 'no scheme'!r}; "
                f"{_HTTPS} only. A bill carries a customer's supplier names and "
                "amounts, and plain HTTP would put them on the wire in clear."
            )
        if not parsed.hostname:
            raise ValueError(f"the Azure endpoint {self.endpoint!r} names no host")
        if not self.key.strip():
            raise ValueError("the Azure key is blank")


def credentials_from_env(
    environ: Mapping[str, str] | None = None,
) -> Credentials | None:
    """Both settings, or `None` when either is missing.

    `None` rather than an exception: a missing credential is the ordinary state
    of a machine nobody has configured yet, and the caller turns it into a
    backend that refuses every document with a sentence naming both variables. A
    developer running this locally should meet that sentence, not a traceback at
    import time.
    """
    source = os.environ if environ is None else environ
    endpoint = source.get(ENV_ENDPOINT, "").strip()
    key = source.get(ENV_KEY, "").strip()
    if not endpoint or not key:
        return None
    return Credentials(endpoint=endpoint, key=key)


class AzureTransport:
    """Bytes to Azure, decoded answer back. The only socket in the tree."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        opener: Opener = URLLIB_OPENER,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._credentials = credentials
        self._opener = opener
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def analyse(self, data: bytes, mime: str) -> Mapping[str, object]:
        """POST the document, poll until Azure is done, return what it said."""
        url = (
            f"{self._credentials.endpoint.rstrip('/')}"
            f"/documentintelligence/documentModels/{MODEL}:analyze"
            f"?api-version={API_VERSION}"
        )
        request = urllib.request.Request(  # noqa: S310 - scheme checked in Credentials
            url, data=data, headers=self._headers(mime), method="POST"
        )
        started = self._monotonic()
        with self._send(request) as answer:
            location = answer.getheader("Operation-Location")
            first = answer.read()
        if not location:
            # A synchronous answer is not the documented shape, but refusing a
            # usable body because it arrived early is pedantry, not safety.
            return decode(first)
        return self._await_result(location, started)

    # -- internals ----------------------------------------------------------

    def _headers(self, mime: str = "") -> dict[str, str]:
        headers = {"Ocp-Apim-Subscription-Key": self._credentials.key}
        if mime:
            headers["Content-Type"] = mime
        return headers

    def _await_result(self, location: str, started: float) -> Mapping[str, object]:
        while True:
            request = urllib.request.Request(  # noqa: S310 - Azure's own URL
                location, headers=self._headers(), method="GET"
            )
            with self._send(request) as answer:
                body = decode(answer.read())
            status = body.get("status")
            if status == "succeeded":
                return body
            if status == "failed":
                raise AzureCallFailed(
                    READ_FAILED, "the reading service could not read this document"
                )
            if self._monotonic() - started >= self._timeout:
                raise AzureCallFailed(TIMED_OUT)
            self._sleep(self._poll)

    def _send(self, request: urllib.request.Request) -> Response:
        try:
            return self._opener(request, timeout=self._timeout)
        except urllib.error.HTTPError as refused:
            raise AzureCallFailed(
                _for_status(refused.code), f"HTTP {refused.code}"
            ) from None
        except urllib.error.URLError as unreachable:
            # `URLError.reason` is usually the underlying OSError, and that is
            # the one that says whether nothing is listening or the name is
            # wrong. ORDER MATTERS: ConnectionRefusedError subclasses
            # ConnectionError, so the specific case is tested first. Reversed,
            # every refusal reads as a generic outage and a person is told to
            # wait when nothing is listening.
            reason = unreachable.reason
            if isinstance(reason, ConnectionRefusedError):
                raise AzureCallFailed(REFUSED) from None
            if isinstance(reason, TimeoutError):
                raise AzureCallFailed(TIMED_OUT) from None
            if isinstance(reason, PermissionError):
                raise AzureCallFailed(NOT_SIGNED_IN) from None
            raise AzureCallFailed(UNAVAILABLE, type(reason).__name__) from None
        except TimeoutError:
            raise AzureCallFailed(TIMED_OUT) from None


def decode(raw: bytes) -> Mapping[str, object]:
    """JSON with the money left exact.

    `parse_float=Decimal` because Azure states a total as the JSON number
    `4200.00`, and `json` turns that into a binary float, which cannot hold
    `0.07` rupees. `accountant/extract/adapter.py` carries the measured case
    where `round(float(text) * 100)` put a number one paise adrift into a
    record. Keeping Azure's own digits means the caller can refuse a
    fractional paise instead of rounding it into acceptance.
    """
    if not raw.strip():
        raise AzureCallFailed(EMPTY)
    try:
        body = json.loads(raw, parse_float=Decimal)
    except ValueError:
        raise AzureCallFailed(MALFORMED, "the answer is not JSON") from None
    if not isinstance(body, Mapping):
        raise AzureCallFailed(
            MALFORMED, f"expected named fields, got {type(body).__name__}"
        )
    return cast("Mapping[str, object]", body)


def _for_status(code: int) -> str:
    """An HTTP status as one of `ALL_KINDS`.

    401 and 403 are both `NOT_SIGNED_IN`, deliberately. To a person uploading a
    bill, a wrong key and a key without permission call for the same action:
    tell whoever set up the deployment. Splitting them would be precision nobody
    can act on.
    """
    if code in (401, 403):
        return NOT_SIGNED_IN
    if code == 429:
        return RATE_LIMITED
    if code == 408:
        return TIMED_OUT
    return UNAVAILABLE
