"""A minimal, strict `multipart/form-data` reader. Bytes in, parts out.

WHY THIS FILE EXISTS AT ALL
---------------------------
Task 9, 2026-08-11. `python3.14 -c "import cgi"` is `ModuleNotFoundError`:
`cgi` was removed in Python 3.13 and `.python-version` here says `3.14`. So the
one stdlib function that used to parse a browser file upload is gone, and
`pyproject.toml` declares `dependencies = []` — adding `multipart` or
`werkzeug` to read one form field would be a runtime dependency bought with a
hundred lines of code.

`email` is still stdlib and is used HERE, for the one job it is genuinely good
at: parsing a header block and its quoted parameters, which is where a
hand-rolled parser gets `filename="my bill; final.pdf"` wrong. The framing —
finding the delimiters and cutting the parts out of the byte string — is done
here, on BYTES, because `email` wants text and a scanned bill is not text.

STRICT, AND THAT IS THE FEATURE
--------------------------------
Every refusal below is a `MalformedUpload` carrying a sentence a person can
read. Nothing is guessed at, nothing is repaired, and no branch falls through
to "assume it was fine":

    no boundary            we were not told where the parts start
    no delimiter in body   nothing here is a multipart body
    no closing marker      the upload stopped partway; the last part may be
                           half a file and there is no way to tell
    no blank line          a part whose headers never end has no content, and
                           reading its headers as content is how a filename
                           becomes an invoice total
    no `name`              RFC 7578 requires it; a part nobody can address is
                           not a form field
    too many parts         a bounded loop, so a body of ten thousand empty
                           delimiters costs a refusal rather than a machine

CRLF ONLY, deliberately. Every browser sends CRLF; a body with bare LF line
endings is hand-made, and accepting both would mean guessing which of two
framings a truncated body was using. It is refused with a sentence saying so.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not touch the disk, start a process, or decide anything about size —
the caller refuses on the DECLARED length before a byte is read, which is the
only place a size limit can do any good. See `accountant/web/app.py::_upload`.

It does not decode part content. A part's bytes come out exactly as they went
in and are handed to the extraction seam unread. Deciding what a PDF says is
the third-party reader's job and no reader is selected — see
`accountant/extract/placeholder.py`.
"""

from __future__ import annotations

import email.message
import email.parser
from dataclasses import dataclass

#: The line ending the format is defined in terms of, and the only one accepted.
CRLF = b"\r\n"

#: Headers end at a blank line. Two CRLFs, never one.
HEADERS_END = CRLF + CRLF

#: How many parts a body may contain before it is refused unread.
#:
#: The form on the home page posts ONE file. Sixteen leaves room for a browser
#: that adds fields of its own without leaving an unbounded loop in a request
#: handler: `b"--x\r\n" * 10_000_000` is a small compressed body and a large
#: number of iterations.
MAX_PARTS = 16


class MalformedUpload(ValueError):
    """This body is not a multipart form, and the message says how it is not.

    A `ValueError` rather than a new hierarchy: the caller turns it into one
    HTTP answer and never branches on the kind, so a hierarchy would be a
    distinction nothing reads.
    """


@dataclass(frozen=True)
class Part:
    """One section of the form. `data` is exactly the bytes that were sent."""

    #: The form field name. Never empty — a part without one is refused.
    name: str
    #: True when the part declared a `filename` parameter, however empty. This
    #: is the ONLY way to tell a file input from a text input: a browser posts
    #: `filename=""` for a file input nobody chose a file in, so an empty
    #: string is a file part and not the absence of one.
    is_file: bool
    #: Best effort, and used for nothing load-bearing. Nothing is ever written
    #: to disk, so this never becomes a path.
    filename: str
    #: The part's DECLARED media type, or "" when it declared none. Never
    #: defaulted to `text/plain`: `email` defaults it, and a caller checking an
    #: allow-list cannot tell a default from a declaration.
    media_type: str
    data: bytes


def _headers_of(block: bytes) -> email.message.Message[str, str]:
    """Parse a part's header block. `latin-1` because it cannot fail.

    Header bytes are ASCII by specification, and a byte that is not is a
    malformed header rather than an interesting one. `latin-1` maps every byte
    to exactly one character and back, so the parse cannot raise and cannot
    silently replace a letter — which `errors="replace"` would, and which is
    how a supplier's name loses a character elsewhere in this codebase.
    """
    return email.parser.Parser().parsestr(block.decode("latin-1"), headersonly=True)


def _text_param(headers: email.message.Message[str, str], name: str) -> str | None:
    """A Content-Disposition parameter, or None when it is absent.

    `Message.get_param` answers with a 3-tuple for the RFC 2231 encoded form,
    which nothing here supports and which no browser sends for these two
    parameters. A tuple is therefore treated as absent rather than flattened
    into a string that would look like a filename and not be one.
    """
    value = headers.get_param(name, header="content-disposition")
    return value if isinstance(value, str) else None


def _readable(raw: str) -> str:
    """A `latin-1`-decoded header value, read back as the UTF-8 it probably was.

    Used for the filename and NOTHING else. It is displayed nowhere, opened
    nowhere and joined to no path — it exists so a refusal can be about a file
    rather than about "a part". A name that is not UTF-8 becomes empty rather
    than becoming different letters.
    """
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return ""


def _one_part(chunk: bytes) -> Part:
    """One delimited section, from just after a delimiter to just before the next."""
    # RFC 2046: the delimiter may be followed by whitespace before its CRLF.
    body = chunk.lstrip(b" \t")
    if not body.startswith(CRLF):
        raise MalformedUpload(
            "one section of this upload did not end its boundary line properly, "
            "so where that section starts cannot be known"
        )
    body = body[len(CRLF) :]

    block, separator, data = body.partition(HEADERS_END)
    if not separator:
        raise MalformedUpload(
            "one section of this upload has no blank line after its headers, so "
            "there is no way to tell its description from its contents"
        )

    headers = _headers_of(block)
    name = _text_param(headers, "name")
    if name is None:
        raise MalformedUpload(
            "one section of this upload does not say which form field it is "
            "for, so it cannot be matched to anything on the page"
        )

    filename = _text_param(headers, "filename")
    return Part(
        name=_readable(name),
        is_file=filename is not None,
        filename=_readable(filename) if filename is not None else "",
        # `headers.get_content_type()` returns `text/plain` for a part that
        # declared nothing, and an allow-list cannot tell that default from a
        # caller who really said `text/plain`. The raw header is read instead,
        # so "declared nothing" stays its own answer and gets refused as one.
        media_type=(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower(),
        data=data,
    )


def parse(body: bytes, boundary: str) -> tuple[Part, ...]:
    """Every part of `body`, or `MalformedUpload` saying why there are none.

    `boundary` comes from the request's own `Content-Type` header. It is the
    caller's declaration about its own body, exactly as the media type is, and
    it is checked against the body rather than trusted: a boundary that appears
    nowhere is a refusal, not an empty form.
    """
    if not boundary:
        raise MalformedUpload(
            "this upload did not say what separates its sections, so nothing "
            "in it could be found"
        )
    try:
        marker = b"--" + boundary.encode("ascii")
    except UnicodeEncodeError as exc:
        raise MalformedUpload(
            "the marker separating this upload's sections is not plain text, "
            "so it cannot be matched against the body"
        ) from exc

    # Every delimiter but the first is preceded by CRLF. Prefixing one when the
    # body opens with the marker makes the split uniform, rather than special-
    # casing the first part — which is the branch a truncated body takes.
    normalised = CRLF + body if body.startswith(marker) else body
    chunks = normalised.split(CRLF + marker)
    if len(chunks) < 2:
        raise MalformedUpload(
            "this upload does not contain the marker it said separates its "
            "sections, so it is not a file upload at all"
        )
    if not chunks[-1].startswith(b"--"):
        raise MalformedUpload(
            "this upload has no closing marker, so it arrived incomplete and "
            "the last file in it may be only part of a file"
        )

    middle = chunks[1:-1]
    if len(middle) > MAX_PARTS:
        raise MalformedUpload(
            f"this upload has {len(middle)} sections and at most {MAX_PARTS} "
            f"are accepted, so it was refused without being read"
        )
    return tuple(_one_part(chunk) for chunk in middle)
