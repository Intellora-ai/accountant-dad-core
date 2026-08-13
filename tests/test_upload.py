"""Task 9 — a person can hand this product a document, and be told the truth.

THE MEASURED GAP THIS CLOSES
----------------------------
Before 2026-08-11, `grep -rn "multipart\\|enctype\\|type=file" accountant/`
returned nothing. Every way into this system was typed text, so somebody
holding a paper bill had no way in at all, and one question had never been
asked over the surface a person actually touches: what does this say about a
document it cannot read?

It cannot read one. The third-party selection is the owner's
(`artifacts/extraction_backends.md:3`, `D-23` open), so an uploaded file meets
`accountant/extract/placeholder.py::PlaceholderReader` and comes back four
stated `not_found`s carrying the reason. That is the whole product behaviour
being asserted here, and it is deliberately NOT "the upload works".

WHAT IS PROVED HERE
-------------------
    the route exists      a real multipart POST over a real socket, through
                          the one spin-up path `tests/test_web.py::serving`
    it refuses safely     413 on size, 415 on kind, 401 on nobody, 400 on a
                          body that is not a multipart form
    it never crashes      seven malformed bodies, each answered with a
                          sentence and a status rather than a dropped socket
    it keeps nothing      the working tree is unchanged, no data directory
                          appears, and the uploaded bytes are in no row of the
                          durable database
    the person is told    the placeholder's `not_found` reaches the page as a
                          sentence, on every one of the four fields

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any document was read. Nothing read anything. `S2 = NOT_MEASURED` stays
true, the question rate for uploaded documents is not zero and is not measured,
and the placeholder's output is not extraction evidence — it is the recorded
absence of a decision the owner has not made.

That a real vendor reader behaves this way. None is connected. What is proved
is that the ROUTE is safe whatever backend it is given, which is a claim about
our code and the only claim the seam can support — the same limit
`tests/test_extract_outage.py` states about its own HTTP scenarios.

EVIDENCE CLASS
--------------
Behavioural, over a real socket, against the shipped `accountant/web/app.py`
and a `FakeTally`. Two structural assertions read the AST of the shipped upload
path, and say so where they sit.
"""

from __future__ import annotations

import ast
import pathlib
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from io import BufferedIOBase
from typing import cast

import pytest

from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.extract.placeholder import (
    NO_READER_CONFIGURED,
    PlaceholderReader,
)
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome
from accountant.web import app, multipart
from tests.test_web import demo_company, fake_backend, get, serving

REPO = pathlib.Path(__file__).resolve().parent.parent

#: A marker no page, log row or database byte may contain afterwards.
#:
#: Inside the uploaded bytes rather than in a filename, because the claim is
#: about the DOCUMENT: a scanned bill carries somebody's supplier names and
#: amounts, and "the uploaded bytes are never logged" is only checkable if the
#: bytes carry something findable. Deliberately ASCII and deliberately odd, so
#: a match cannot be a coincidence and a substring search cannot miss it
#: through an encoding.
MARKER = b"SECRET-BILL-CONTENTS-8f13c2a4"

#: A small file that is honestly a PDF as far as its first bytes go, and is not
#: a real one. Nothing here parses it — that is the point.
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + MARKER + b"\ntrailer\n%%EOF\n"

BOUNDARY = "----AccountantDadTest9f2a"


# ---- building and sending a real multipart body ------------------------------


def multipart_body(
    *,
    field: str = app.UPLOAD_FIELD,
    filename: str | None = "bill.pdf",
    media_type: str | None = "application/pdf",
    data: bytes = PDF,
    boundary: str = BOUNDARY,
    closed: bool = True,
) -> bytes:
    """One form part, assembled by hand exactly as a browser assembles it.

    Built here rather than with a library because there is no library: `cgi`
    was removed in Python 3.13, `.python-version` says 3.14, and
    `pyproject.toml` declares `dependencies = []`. Writing the body out is also
    what lets the malformed cases below be malformed in one named way each,
    which a builder that always produced valid output could not do.
    """
    disposition = f'form-data; name="{field}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    head = f"--{boundary}\r\nContent-Disposition: {disposition}\r\n"
    if media_type is not None:
        head += f"Content-Type: {media_type}\r\n"
    head += "\r\n"
    tail = f"\r\n--{boundary}--\r\n" if closed else "\r\n"
    return head.encode() + data + tail.encode()


def send(
    base: str,
    body: bytes,
    *,
    path: str = "/upload",
    content_type: str = f"multipart/form-data; boundary={BOUNDARY}",
    token: str = "",
) -> tuple[int, str]:
    """POST raw bytes with a caller-chosen content type, and return the status.

    NOT `tests/test_web.py::post_for_status`, and the difference is the whole
    subject: that helper form-encodes its fields, which is precisely the
    encoding this route must not receive. The status is returned rather than
    raised for the same reason it states — here the STATUS IS THE MEASUREMENT,
    and a helper that turned a 413 into an exception could not assert one.
    """
    request = urllib.request.Request(  # noqa: S310
        base + path, data=body, headers={"Content-Type": content_type}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode()


def base_host(base: str) -> str:
    """`127.0.0.1:54321` off the fixture's URL. A raw request needs a Host."""
    return base.removeprefix("http://")


@pytest.fixture
def uploading() -> Iterator[str]:
    """The demo company, served, with the app choosing its own backend.

    The shipped default is what a person meets, so it is what most of this file
    drives. The tests that need the placeholder specifically inject it through
    the same `configure(extractor=...)` seam and say so.
    """
    with serving(demo_company(), fake_backend()) as base:
        yield base


# ---- the route exists at all, and a person can reach it ----------------------


def test_the_home_page_offers_a_file_input_so_the_route_is_reachable(
    uploading: str,
) -> None:
    """A route with no way to reach it is a route nobody has.

    Three separate facts, because a form missing any one of them silently posts
    the filename instead of the file: the enctype, the input type, and the
    field name the route reads.
    """
    body = get(uploading)

    assert 'enctype="multipart/form-data"' in body
    assert "type=file" in body
    assert f"name={app.UPLOAD_FIELD}" in body
    assert "/upload" in body


def test_the_home_page_says_plainly_that_no_reader_is_chosen_yet(
    uploading: str,
) -> None:
    """The limitation is stated BEFORE the person spends time on a scan.

    Telling them only after they have uploaded is technically honest and
    practically useless.
    """
    body = get(uploading)

    assert "no document reader is chosen yet" in body.lower()
    assert "does not guess" in body.lower()


def test_a_real_multipart_upload_is_answered_rather_than_dropped(
    uploading: str,
) -> None:
    """The whole route, over a socket: 200, a decision page, nothing broken."""
    status, body = send(uploading, multipart_body())

    assert status == 200
    assert "Something in Accountant Dad broke" not in body
    assert "Where each field came from" in body


# ---- what the person is told, and what the record says -----------------------


def test_an_uploaded_document_leaves_every_named_field_explicitly_not_found(
    uploading: str,
) -> None:
    """S3: four fields, four stated `not_found`s, no silent blank, no guess."""
    send(uploading, multipart_body())
    drafts = list(app.DRAFTS.values())

    assert len(drafts) == 1
    evidence = drafts[0].record
    assert evidence.complete is True
    for name in ExtractedRecord.FIELDS:
        source = evidence.per_field_source[name]
        assert source.startswith(f"{NOT_FOUND}: "), (name, source)
        assert source.strip() != NOT_FOUND
    assert (
        evidence.date,
        evidence.party,
        evidence.total_paise,
        evidence.tax_paise,
    ) == (None, None, None, None)


def test_the_page_tells_the_person_in_one_sentence_that_no_reader_is_configured(
    uploading: str,
) -> None:
    """The banner is the claim. Four provenance rows are the evidence for it.

    A per-field table alone cannot say the thing that matters here: that the
    four absences have ONE cause and the cause is a decision nobody has made.
    """
    _status, body = send(uploading, multipart_body())

    assert "data-unread=document" in body
    assert "Nothing was read from that file." in body
    assert "No document reader is configured" in body
    assert "Nothing was written to your Tally" in body


def test_the_placeholder_reaches_the_person_through_the_same_seam_typed_text_uses(
    tmp_path: pathlib.Path,
) -> None:
    """The placeholder's own words, on the page, through `configure(extractor=)`.

    Injected rather than made the default, deliberately.
    `registry.DEFAULT_BACKEND` is `typed_text` and stays there: a deployment
    that swapped it for `no_reader` would lose the typed-entry box, which is
    the only working route this product has. What is asserted is that the seam
    CARRIES this backend's answer all the way to the screen — which is the
    property that makes the vendor swap a one-line change later.
    """
    with serving(
        demo_company(),
        fake_backend(),
        extractor=PlaceholderReader(),
        store_path=tmp_path / "app.db",
    ) as base:
        status, body = send(base, multipart_body())
        drafts = list(app.DRAFTS.values())

    assert status == 200
    assert NO_READER_CONFIGURED in body, (
        "the placeholder's reason never reached the page the person reads"
    )
    assert len(drafts) == 1
    assert drafts[0].record.backend == "no_reader"
    for name in ExtractedRecord.FIELDS:
        assert NO_READER_CONFIGURED in drafts[0].record.per_field_source[name], name


def test_the_placeholder_never_claims_a_backend_name_it_is_not() -> None:
    """A row that cannot say who wrote it is not evidence about anybody, and a
    row that says the WRONG name is worse — it is evidence about somebody
    else."""
    evidence = PlaceholderReader().extract(PDF, "application/pdf")

    assert evidence.backend == "no_reader"
    assert evidence.backend not in {"unknown", "stub", "typed_text", "unavailable"}


def test_the_placeholder_carries_none_of_the_document_it_was_handed() -> None:
    """`raw_text` becomes `Voucher.narration`, which reaches the page, the
    durable log and — on a VALID entry — Tally itself. A backend that echoed
    the file would put somebody's scanned bill in all three."""
    evidence = PlaceholderReader().extract(PDF, "application/pdf")

    assert evidence.raw_text == ""
    assert MARKER.decode("latin-1") not in repr(evidence)


def test_an_uploaded_document_is_never_posted_to_tally(uploading: str) -> None:
    """Nothing was read, so nothing may be written. Measured in exact paise on
    both sides of the request rather than assumed from the code path."""
    live = app.runtime()
    before = live.client.trial_balance(live.company)
    vouchers_before = len(live.client.read_vouchers(live.company))

    send(uploading, multipart_body())

    assert live.client.trial_balance(live.company) == before
    assert live.client.list_our_vouchers(live.company) == ()
    assert len(live.client.read_vouchers(live.company)) == vouchers_before
    assert next(iter(app.DRAFTS.values())).outcome is not Outcome.VALID


# ---- too big -----------------------------------------------------------------


def test_a_file_over_the_limit_is_refused_with_413_and_a_plain_sentence(
    uploading: str,
) -> None:
    """Refused on the DECLARED length, before the body is read into memory.

    The body really is oversized rather than merely claiming to be: a test that
    lied in the header would pass against a route that read everything first
    and checked afterwards, which is the exact failure this guard exists for.
    """
    huge = multipart_body(data=b"x" * (app.MAX_UPLOAD_BYTES + 1))
    assert len(huge) > app.MAX_UPLOAD_BYTES

    status, body = send(uploading, huge)

    assert status == 413
    assert "larger than" in body
    assert "Nothing was written to your Tally" in body
    assert app.DRAFTS == {}


# ---- a body that says whether anybody read it --------------------------------


class ReadRecorder:
    """The request body, wrapped, remembering every read anybody asked for.

    "Refused before it was read" is a claim about ORDER, and order is invisible
    in a status code: a door that buffers a hundred megabytes and refuses
    afterwards answers 413 exactly like a door that never touched the body. So
    the body sits behind something that counts.

    ONLY `read` IS RECORDED, and the split is the stdlib's own, not ours:
    `BaseHTTPRequestHandler` takes the request line and the headers with
    `readline`, and the body with `read`. Recording `read` alone therefore
    records exactly the question worth asking - did anything touch the body,
    and how much did it ask for in one go.

    `asked_for` matters more than the byte count. The whole hazard is
    `rfile.read(n)`, which allocates whatever `n` says before a single byte
    arrives, so a limit that lets `n` be the declared length prevents nothing.
    """

    def __init__(self, wrapped: BufferedIOBase) -> None:
        self._wrapped = wrapped
        #: Every `n` in every `read(n)`, in order.
        self.asked_for: list[int] = []
        #: How many bytes of body actually came back.
        self.delivered = 0

    def read(self, size: int = -1) -> bytes:
        self.asked_for.append(size)
        data = self._wrapped.read(size)
        self.delivered += len(data)
        return data

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ReadRecorder]]:
    """Wrap every connection's body, and hand the test the recorders.

    Installed on `Handler.setup`, which is where `BaseHTTPRequestHandler` makes
    `rfile` in the first place, so this is the real shipped route with one
    counter around it rather than a re-implementation of the route. NOT a second
    server: `tests/test_web.py::serving` is still the one spin-up path, and the
    handler class it serves is patched here for the length of one test.
    """
    seen: list[ReadRecorder] = []
    original = app.Handler.setup

    def setup(handler: app.Handler) -> None:
        original(handler)
        recorder = ReadRecorder(handler.rfile)
        seen.append(recorder)
        # The recorder is a `read`/`readline` stand-in, not a `BufferedIOBase`
        # subclass, so the cast is what lets it sit where the real body sat.
        handler.rfile = cast(BufferedIOBase, recorder)

    monkeypatch.setattr(app.Handler, "setup", setup)
    yield seen


def raw_post(base: str, request: bytes) -> str:
    """Send exact bytes at the server the fixture already started, and read back.

    NOT a second server — the one spin-up path is still
    `tests/test_web.py::serving`, and this is a request helper, the same kind of
    thing `post_for_status` is. It exists because `urllib` will not send a POST
    without a `Content-Length`, and the branch that refuses exactly that request
    is otherwise unreachable and would be asserted only in the abstract.
    """
    host, _, port = base.removeprefix("http://").partition(":")
    with socket.create_connection((host, int(port)), timeout=10) as wire:
        wire.sendall(request)
        wire.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while piece := wire.recv(65536):
            chunks.append(piece)
    return b"".join(chunks).decode("latin-1")


def an_upload_declaring(base: str, declared: int, body: bytes) -> str:
    """A real `POST /upload` whose `Content-Length` says `declared`.

    `declared` and `len(body)` are allowed to disagree, and that is the point:
    urllib cannot send such a request, and the refusal under test is made from
    the header alone. Written out here rather than imported from
    `tests/test_app_coverage_c.py` for the reason that file gives about
    `raw_post` - a test that depends on another test file's helper fails for
    two reasons at once and neither of them is the product.
    """
    return raw_post(
        base,
        b"POST /upload HTTP/1.1\r\n"
        + f"Host: {base_host(base)}\r\n".encode()
        + f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n".encode()
        + f"Content-Length: {declared}\r\n\r\n".encode()
        + body,
    )


def test_the_cap_is_the_hundred_megabytes_the_owner_set() -> None:
    """Owner decision, closed: any type, up to 100 MB, 413 before the body is
    read. The shipped constant was 10 MiB - a tenth of it - so a phone
    photograph of a multi-page bill was refused by a limit nobody chose.

    Written as `100 * 1024 * 1024` rather than `104857600` so a reader can see
    it is a hundred and not a ten, which is the digit that was wrong.
    """
    assert app.MAX_UPLOAD_BYTES == 100 * 1024 * 1024


def test_the_page_and_the_refusal_both_say_the_size_the_constant_holds(
    uploading: str,
) -> None:
    """The number is stated once and read everywhere. A page advertising 100 MB
    over a door that refuses at 10 is worse than either limit on its own."""
    offered = get(uploading)
    refused = an_upload_declaring(uploading, app.MAX_UPLOAD_BYTES + 1, b"x")

    assert "100 MB" in offered
    assert "larger than the 100 MB" in refused


def test_the_413_is_decided_from_the_header_and_no_read_asks_for_the_body(
    uploading: str, recording: list[ReadRecorder]
) -> None:
    """THE ORDER, measured rather than asserted from the code path.

    `rfile.read(n)` allocates whatever `n` says before a byte arrives, so a
    size check made after the read is a check made after the damage - one
    request takes the process down without a credential. The only read this
    route may make on an oversized body is a bounded drain chunk, which is
    never accumulated and never kept, and which exists so the browser gets the
    sentence instead of a dropped connection.
    """
    answer = an_upload_declaring(uploading, app.MAX_UPLOAD_BYTES + 1, MARKER)
    asked = [n for r in recording for n in r.asked_for]

    assert answer.startswith("HTTP/1.0 413 ")
    assert asked, "the recorder saw no request at all"
    assert max(asked) <= app.UPLOAD_DRAIN_CHUNK
    assert sum(r.delivered for r in recording) < app.UPLOAD_DRAIN_CHUNK
    assert MARKER.decode() not in answer
    assert app.DRAFTS == {}


def test_the_control_an_upload_inside_the_cap_really_does_have_its_body_read(
    uploading: str, recording: list[ReadRecorder]
) -> None:
    """THE CONTROL on the test above, and without it that test proves nothing.

    A recorder wired to a body nobody could read, or one whose counter never
    moved, would report "never touched" for every request ever made and pass
    the 413 test while the door buffered a hundred megabytes. Here the size
    gate passes, the route reads the whole declared length in one go because
    that is what an accepted upload does, and the counter says so.
    """
    body = multipart_body()

    status, _ = send(uploading, body)
    asked = [n for r in recording for n in r.asked_for]

    assert status == 200
    assert len(body) in asked
    assert sum(r.delivered for r in recording) >= len(body)


def test_an_upload_declaring_exactly_the_cap_is_not_refused_for_its_size(
    uploading: str,
) -> None:
    """The boundary is `>`, not `>=`. A file of exactly the stated maximum is
    inside the stated maximum, and a person who trims a photo to the advertised
    number and is refused anyway has been lied to by the page.

    Declared, not sent: proving where the boundary sits needs no hundred
    megabytes on a socket. The size gate passes, the truncated body reaches the
    parser, and the parser refuses it in its own words - a DIFFERENT refusal,
    which is what tells the two paths apart.
    """
    answer = an_upload_declaring(uploading, app.MAX_UPLOAD_BYTES, multipart_body()[:40])

    assert not answer.startswith("HTTP/1.0 413 ")
    assert "larger than" not in answer
    assert "Something in Accountant Dad broke" not in answer


def test_an_upload_that_does_not_say_how_big_it_is_is_refused_unread(
    uploading: str,
) -> None:
    """A size limit that treats "unknown" as "fine" is not a limit.

    Chunked bodies land here: `BaseHTTPRequestHandler` does not decode them, so
    reading one would hand the parser the chunk framing as though it were the
    file, and there is no length to check the limit against in the first place.
    """
    live = app.runtime()
    before = live.client.trial_balance(live.company)
    host = base_host(uploading)

    answer = raw_post(
        uploading,
        b"POST /upload HTTP/1.1\r\n"
        + f"Host: {host}\r\n".encode()
        + f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n".encode()
        + b"Transfer-Encoding: chunked\r\n\r\n"
        + b"5\r\nhello\r\n0\r\n\r\n",
    )

    assert answer.startswith("HTTP/1.0 411 ")
    assert "did not say how big it is" in answer
    assert "Nothing was written to your Tally" in answer
    assert app.DRAFTS == {}
    assert live.client.trial_balance(live.company) == before


def test_a_length_that_is_not_a_number_is_refused_rather_than_crashing(
    uploading: str,
) -> None:
    """`int("banana")` is a `ValueError`, and a `ValueError` in a request
    handler is the 503 that says the application broke. An unreadable length is
    the caller's mistake, so it is answered as one."""
    host = base_host(uploading)

    answer = raw_post(
        uploading,
        b"POST /upload HTTP/1.1\r\n"
        + f"Host: {host}\r\n".encode()
        + f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n".encode()
        + b"Content-Length: banana\r\n\r\n",
    )

    assert answer.startswith("HTTP/1.0 411 ")
    assert "Something in Accountant Dad broke" not in answer


# ---- the wrong kind of thing -------------------------------------------------


def test_a_body_that_is_not_a_file_upload_at_all_is_refused_with_415(
    uploading: str,
) -> None:
    """A typed form posted at the upload route. It has a route of its own."""
    status, body = send(
        uploading,
        urllib.parse.urlencode({"text": "paid Sharma Traders 4200"}).encode(),
        content_type="application/x-www-form-urlencoded",
    )

    assert status == 415
    assert "not sent as a file upload" in body
    assert app.DRAFTS == {}


@pytest.mark.parametrize(
    ("label", "media_type"),
    [
        ("a spreadsheet", "application/vnd.ms-excel"),
        ("a zip of bills", "application/zip"),
        ("a program", "application/x-msdownload"),
        ("a video", "video/mp4"),
        # `text/plain` is refused ON PURPOSE even though the shipped backend
        # reads exactly that: its bytes would be decoded into `raw_text`, and
        # from there into `narration`, the page and the durable log. Allowing
        # it would make "an uploaded file's bytes are never logged" depend on
        # what people upload.
        ("a text file", "text/plain"),
        ("a part that declares nothing", None),
    ],
)
def test_a_kind_of_file_that_is_not_on_the_allow_list_is_refused_with_415(
    uploading: str, label: str, media_type: str | None
) -> None:
    status, body = send(uploading, multipart_body(media_type=media_type))

    assert status == 415, label
    assert "cannot be read here" in body, label
    assert app.DRAFTS == {}, label


@pytest.mark.parametrize("allowed", sorted(app.UPLOAD_MEDIA_TYPES))
def test_every_kind_on_the_allow_list_is_actually_accepted(
    uploading: str, allowed: str
) -> None:
    """The disconfirming case. An allow-list that admits nothing would pass
    every refusal test above and be indistinguishable from a broken route."""
    status, body = send(uploading, multipart_body(media_type=allowed))

    assert status == 200, allowed
    assert "cannot be read here" not in body


# ---- nobody --------------------------------------------------------------


@pytest.fixture
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """This section runs with authentication REQUIRED.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the whole suite so the sixty
    pre-existing HTTP tests keep measuring what they were written for. Deleted
    rather than set to "0", exactly as `tests/test_auth.py::production_auth`
    does, because an unset variable is the case that ships.

    NOT autouse. The rest of this file is about the upload route and wants the
    suite's default; the two tests below are about the credential and ask for
    this by name, so a reader can see which mode each test is in.
    """
    monkeypatch.delenv(app.ENV_LOCAL_DEV_MODE, raising=False)


@pytest.mark.usefixtures("production_auth")
def test_an_upload_from_nobody_is_refused_with_401() -> None:
    """Like every other route. A file drop-box that skipped the check would be
    the one unauthenticated write surface in the product.

    THE STATE IS READ AFTER THE SERVER HAS STOPPED, and that is not tidiness.
    Asserted inside the block it was a RACE: `_identify` answers 401 before the
    rest of the handler runs, so `send` returns while the request is still
    being served and `app.DRAFTS` is empty because nothing has happened YET.
    Measured 2026-08-11 by reverting the guard — the route carried straight on
    and both assertions still passed. `HTTPServer` serves one request at a
    time and `serving` joins its thread, so out here the handler has finished
    and an empty `DRAFTS` means it did nothing rather than not yet.
    """
    tally = demo_company()
    with serving(tally, fake_backend()) as base:
        before = tally.trial_balance(app.COMPANY)
        status, body = send(base, multipart_body())

    assert status == 401
    assert "no session token" in body
    assert app.DRAFTS == {}, "a stranger's upload was read and turned into a draft"
    assert tally.list_our_vouchers(app.COMPANY) == ()
    assert tally.trial_balance(app.COMPANY) == before


@pytest.mark.usefixtures("production_auth")
def test_an_upload_from_nobody_gets_one_answer_and_the_handler_stops() -> None:
    """The refusal ENDS the request. Nothing after it runs.

    The falsifying case this exists for, measured: a `_identify()` whose answer
    is ignored still writes its 401 first, so a client reads 401 and is
    satisfied while the handler goes on to parse the file, build a draft and
    write a durable row — then writes a SECOND response into the same socket
    that nobody reads.

    So the socket is read to the end and the responses are counted. One status
    line means the handler stopped; two means it answered and carried on, which
    no status code can express.
    """
    host_and_port = ""
    with serving(demo_company(), fake_backend()) as base:
        host_and_port = base_host(base)
        answer = raw_post(
            base,
            b"POST /upload HTTP/1.1\r\n"
            + f"Host: {host_and_port}\r\n".encode()
            + f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n".encode()
            + f"Content-Length: {len(multipart_body())}\r\n\r\n".encode()
            + multipart_body(),
        )

    assert answer.count("HTTP/1.0 ") == 1, (
        "the handler answered and then kept going, so the refusal refused "
        f"nothing: {answer[:400]!r}"
    )
    assert answer.startswith("HTTP/1.0 401 ")
    assert "no session token" in answer
    assert MARKER.decode() not in answer
    assert app.DRAFTS == {}


@pytest.mark.usefixtures("production_auth")
def test_an_upload_from_nobody_is_refused_before_it_is_parsed() -> None:
    """The refusal is the credential's, not the parser's.

    A malformed body from a stranger must still come back 401: answering 400
    would tell them the parser ran, which is work done for somebody who was
    never allowed to ask for it.
    """
    with serving(demo_company(), fake_backend()) as base:
        status, body = send(base, b"not a multipart body at all")

    assert status == 401
    assert "no session token" in body
    assert app.DRAFTS == {}


# ---- a body that is broken ---------------------------------------------------


@pytest.mark.parametrize(
    ("label", "body", "content_type"),
    [
        (
            "no boundary parameter",
            multipart_body(),
            "multipart/form-data",
        ),
        (
            "a boundary that appears nowhere in the body",
            multipart_body(),
            "multipart/form-data; boundary=somethingelse",
        ),
        (
            "no closing marker, so the last file may be half a file",
            multipart_body(closed=False),
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
        (
            "headers that never end",
            f"--{BOUNDARY}\r\nContent-Disposition: form-data; "
            f'name="file"\r\n--{BOUNDARY}--\r\n'.encode(),
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
        (
            "a section that says which field it is not for",
            f"--{BOUNDARY}\r\nContent-Type: application/pdf\r\n\r\nx\r\n"
            f"--{BOUNDARY}--\r\n".encode(),
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
        (
            "bare LF line endings, which no browser sends",
            f'--{BOUNDARY}\nContent-Disposition: form-data; name="file"; '
            f'filename="b.pdf"\nContent-Type: application/pdf\n\nx\n'
            f"--{BOUNDARY}--\n".encode(),
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
        (
            "raw bytes that are not a form at all",
            b"\x00\x01\x02\xff\xfe not a form",
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
        (
            "an empty body claiming to be a form",
            b"",
            f"multipart/form-data; boundary={BOUNDARY}",
        ),
    ],
)
def test_a_malformed_upload_is_answered_with_a_sentence_rather_than_a_crash(
    uploading: str, label: str, body: bytes, content_type: str
) -> None:
    """Eight ways to be broken, one place the person lands.

    400 and not 503: the request is wrong, not the service. A 503 here would
    say the application broke, for a person whose only problem is a browser
    that gave up halfway through a scan.
    """
    live = app.runtime()
    before = live.client.trial_balance(live.company)

    status, page = send(uploading, body, content_type=content_type)

    assert status == 400, (label, status)
    assert "Something in Accountant Dad broke" not in page, label
    assert "Traceback" not in page, label
    assert "Nothing was written to your Tally" in page, label
    assert app.DRAFTS == {}, label
    assert live.client.trial_balance(live.company) == before, label


def test_a_form_with_no_file_in_it_says_so_rather_than_reading_nothing(
    uploading: str,
) -> None:
    """A browser posts `filename=""` for a file input nobody chose a file in.

    An empty part is not a document, and treating it as one would produce a
    decision about a file that does not exist.
    """
    status, body = send(uploading, multipart_body(data=b""))

    assert status == 400
    assert "No file was attached" in body
    assert app.DRAFTS == {}


def test_a_text_field_posted_where_a_file_belongs_is_not_treated_as_a_file(
    uploading: str,
) -> None:
    """`filename` absent means a text input, whatever else the part carries."""
    status, body = send(uploading, multipart_body(filename=None))

    assert status == 400
    assert "No file was attached" in body


# ---- nothing is kept ---------------------------------------------------------

#: Directories whose contents change for reasons that have nothing to do with
#: an upload — caches, the virtual environment, git's own bookkeeping.
VOLATILE = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def tree(root: pathlib.Path) -> dict[str, int]:
    """Every non-volatile file under `root`, with its size."""
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not any(part in VOLATILE for part in path.parts)
    }


def test_an_upload_writes_nothing_to_the_working_tree_or_a_data_directory(
    tmp_path: pathlib.Path,
) -> None:
    """The file is never on this machine's disk, and there is no branch where
    it could be: no path is built, no file is opened, no temporary directory is
    asked for. Measured as a before-and-after of the tree rather than read off
    the code — the structural half is the test below this one."""
    before = tree(REPO)
    data_dir_before = (REPO / "data").exists()

    with serving(
        demo_company(), fake_backend(), store_path=tmp_path / "app.db"
    ) as base:
        status, _body = send(base, multipart_body())

    assert status == 200
    assert tree(REPO) == before, "an upload changed a file in the working tree"
    assert (REPO / "data").exists() is data_dir_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app.db"], (
        "an upload left a file beside the database"
    )


def test_the_uploaded_bytes_reach_no_row_of_the_durable_database(
    tmp_path: pathlib.Path,
) -> None:
    """The strongest form of "never logged": the whole database file is
    searched, not one column of it.

    The store is opened on the serving thread and SQLite gives a connection to
    the thread that opened it, so the file is read AFTER shutdown — sequential
    access, needing no locking argument. That is the same reason
    `tests/test_web.py::serving` grew `store_path` in the first place.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        status, body = send(base, multipart_body())
        home = get(base)

    assert status == 200
    assert MARKER not in db.read_bytes(), "the uploaded bytes are in the database"
    assert MARKER.decode() not in body, "the uploaded bytes came back on the page"
    assert MARKER.decode() not in home, "the uploaded bytes are in the activity log"

    rows = MemoryStore(db).actions(app.COMPANY)
    assert rows, "the upload left no durable row at all, so nothing was recorded"
    for row in rows:
        for field in (row.action, row.outcome, row.reason, row.detail):
            assert MARKER.decode() not in field, field


def test_the_upload_leaves_a_durable_row_saying_what_happened(
    tmp_path: pathlib.Path,
) -> None:
    """The document is not recorded; the DECISION is. A route that kept nothing
    at all would pass the test above and leave no trail either."""
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        send(base, multipart_body())
        home = get(base)

    assert 'data-outcome="unclear"' in home or 'data-outcome="not_valid"' in home
    assert MemoryStore(db).actions(app.COMPANY)


# ---- the structural half: the upload path cannot touch a disk ----------------

#: Names that put bytes on a disk, start a process, or open a socket of their
#: own. An upload route needs none of them; a route that quietly kept a copy
#: needs at least one.
KEEPS_THINGS = frozenset(
    {
        "open",
        "mkdir",
        "write_bytes",
        "write_text",
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "mkdtemp",
        "copyfileobj",
        "unlink",
    }
)

#: The functions an uploaded file's bytes actually pass through.
UPLOAD_PATH = ("_upload", "_discard_body", "_declared_length", "_plain_refusal")


def called_names(node: ast.AST) -> set[str]:
    """Every function called by name or by attribute inside this tree."""
    found: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        if isinstance(inner.func, ast.Name):
            found.add(inner.func.id)
        elif isinstance(inner.func, ast.Attribute):
            found.add(inner.func.attr)
    return found


def function_named(tree_: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree_):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in the module, so this guard scans nothing")


def test_the_upload_path_calls_nothing_that_could_keep_a_copy() -> None:
    """The behavioural test above says no file appeared. This says there is no
    branch that could have made one — including branches no request took.

    Scanned off the AST rather than trusted from a docstring, the same way
    `tests/test_no_reader.py` scans `accountant/extract/`. Written as an
    allow-nothing list of CALLS so a route that gained a `tempfile` next month
    fails here rather than the first time somebody looks.
    """
    source = ast.parse((REPO / "accountant" / "web" / "app.py").read_text())
    offenders = {
        name: sorted(called_names(function_named(source, name)) & KEEPS_THINGS)
        for name in UPLOAD_PATH
        if called_names(function_named(source, name)) & KEEPS_THINGS
    }

    assert offenders == {}, (
        f"the upload path can put an uploaded file somewhere: {offenders}"
    )


def test_the_multipart_reader_touches_no_disk_process_or_socket() -> None:
    """It is a parser. Bytes in, parts out, and nothing else at all."""
    source = ast.parse((REPO / "accountant" / "web" / "multipart.py").read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(source)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert called_names(source) & KEEPS_THINGS == set()
    assert imported & {"subprocess", "socket", "tempfile", "shutil", "pathlib"} == set()


# ---- the parser, driven directly on bodies HTTP cannot express ---------------


def test_the_parser_reads_a_body_a_browser_would_actually_send() -> None:
    """The disconfirming case for every refusal below: a good body parses, and
    the bytes come out byte-for-byte as they went in."""
    parts = multipart.parse(multipart_body(), BOUNDARY)

    assert len(parts) == 1
    assert parts[0].name == app.UPLOAD_FIELD
    assert parts[0].is_file is True
    assert parts[0].filename == "bill.pdf"
    assert parts[0].media_type == "application/pdf"
    assert parts[0].data == PDF


def test_the_parser_keeps_a_filename_with_a_semicolon_in_it_whole() -> None:
    """The case a hand-rolled parameter split gets wrong, and the reason
    `email` does the quoting rather than this codebase."""
    parts = multipart.parse(multipart_body(filename="bill; final.pdf"), BOUNDARY)

    assert parts[0].filename == "bill; final.pdf"


def test_the_parser_refuses_a_body_with_no_boundary_to_split_on() -> None:
    with pytest.raises(multipart.MalformedUpload, match="what separates its sections"):
        multipart.parse(multipart_body(), "")


def test_the_parser_refuses_a_boundary_that_is_not_plain_text() -> None:
    """A caller's own declaration about its own body, checked rather than used."""
    with pytest.raises(multipart.MalformedUpload, match="not plain text"):
        multipart.parse(multipart_body(), "böundary")


def test_the_parser_refuses_more_sections_than_it_will_look_at() -> None:
    """An unbounded loop in a request handler is a small body and a large
    machine bill. `b"--x\\r\\n"` repeated is exactly that shape."""
    many = (
        b"".join(
            f"--{BOUNDARY}\r\nContent-Disposition: form-data; "
            f'name="f{i}"\r\n\r\nx\r\n'.encode()
            for i in range(multipart.MAX_PARTS + 1)
        )
        + f"--{BOUNDARY}--\r\n".encode()
    )

    with pytest.raises(multipart.MalformedUpload, match="sections and at most"):
        multipart.parse(many, BOUNDARY)


def test_the_parser_accepts_exactly_as_many_sections_as_it_says_it_will() -> None:
    """The ratchet's other side. A cap that refused at the boundary value would
    be a different cap from the one the message names."""
    at_the_limit = (
        b"".join(
            f"--{BOUNDARY}\r\nContent-Disposition: form-data; "
            f'name="f{i}"\r\n\r\nx\r\n'.encode()
            for i in range(multipart.MAX_PARTS)
        )
        + f"--{BOUNDARY}--\r\n".encode()
    )

    assert len(multipart.parse(at_the_limit, BOUNDARY)) == multipart.MAX_PARTS


def test_the_parser_reports_a_declared_media_type_and_never_invents_one() -> None:
    """`email` defaults an absent Content-Type to `text/plain`, and an
    allow-list cannot tell that default from a caller who really said it. A
    part that declared nothing must stay its own answer."""
    silent = multipart.parse(multipart_body(media_type=None), BOUNDARY)
    spoken = multipart.parse(multipart_body(media_type="TEXT/Plain"), BOUNDARY)

    assert silent[0].media_type == ""
    assert spoken[0].media_type == "text/plain"


def test_the_parser_never_lets_a_declared_kind_reach_the_allow_list_uncased() -> None:
    """`Application/PDF` is the same media type as `application/pdf`, and a
    case-sensitive allow-list would refuse a browser for its capitalisation."""
    parts = multipart.parse(multipart_body(media_type="Application/PDF"), BOUNDARY)

    assert parts[0].media_type in app.UPLOAD_MEDIA_TYPES
