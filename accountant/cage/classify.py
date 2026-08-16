"""What a pile of bytes actually is. One job, nothing else.

WHY THIS MODULE EXISTS
----------------------
The requirement is "anything in, safe processing out". The user may upload a
phone photo, a PDF, a Word document, a zip, or a file with no extension, and the
interface must never block them up front by type. What it must do is work out
what arrived and either read it or refuse it with a plain sentence.

MAGIC BYTES BEAT THE DECLARED TYPE, ALWAYS
--------------------------------------------
A `.pdf` that is really a JPEG is a Tuesday, not an attack. Browsers guess.
Phones rename. Mail clients relabel. So the declared MIME type and the filename
are recorded but never believed, and a test pins that direction so nobody
reverses it later for convenience.

The disagreement is still worth counting. How often uploads arrive mislabelled
is a fact about the product, and `declared_disagreed` is where the audit line
gets it.

WHAT THIS MODULE NEVER DOES
----------------------------
It does not execute, shell out to, or unzip anything. DOCX and XLSX are zip
archives; refusing to extract from them removes the zip-bomb surface as a side
effect of a decision made for a different reason - D-23 excluded DOCX on
purpose. **Accepting a file is not the same as reading it.**

It also never raises. A classifier that raises turns a refusal into a stack
trace, and the whole point of the input layer is that nothing a user can upload
crashes the process.

WHAT IT DOES NOT ANSWER
------------------------
Whether the file is a bill. A photo of a cat is a perfectly valid JPEG and is
classified as one. Stopping it is the reader's job, and then the decision
layer's. One module, one question.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class FileKind(StrEnum):
    """What we concluded the bytes are."""

    TEXT = "text"
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    UNSUPPORTED = "unsupported"


#: The four we can extract from, named once so the refusal sentence and the
#: reader read the same list. A second copy of this drifts from the first.
READABLE: Final[tuple[FileKind, ...]] = (
    FileKind.TEXT,
    FileKind.PDF,
    FileKind.JPEG,
    FileKind.PNG,
)

#: Said to the person when we cannot read what they sent. One sentence, no
#: jargon, and it names what would work rather than only what did not.
SUPPORTED_SENTENCE: Final = (
    "We can read PDFs, JPEG or PNG photos, and plain text files. "
    "This one is not something we can read yet."
)

#: Signatures we can read. Order matters only in that longer markers are checked
#: before shorter ones could collide; none of these four collide today.
_READABLE_MAGIC: Final[tuple[tuple[bytes, FileKind, str], ...]] = (
    (b"%PDF-", FileKind.PDF, "a PDF"),
    (b"\xff\xd8\xff", FileKind.JPEG, "a JPEG photo"),
    (b"\x89PNG\r\n\x1a\n", FileKind.PNG, "a PNG image"),
)

#: Signatures we recognise but cannot read. Naming them is the difference
#: between "unsupported file" and "this looks like a Word document", and the
#: second one tells the person what to do next.
_KNOWN_BUT_UNREADABLE: Final[tuple[tuple[bytes, str], ...]] = (
    (b"PK\x03\x04", "a zip archive - Word, Excel and OpenDocument files are zips"),
    (b"GIF87a", "a GIF image"),
    (b"GIF89a", "a GIF image"),
    (b"%!PS", "a PostScript file"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"Rar!", "a RAR archive"),
    (b"\x7fELF", "a Linux program"),
    (b"MZ", "a Windows program"),
    (b"\xd0\xcf\x11\xe0", "an old Office file - .doc or .xls"),
    (b"{\\rtf", "an RTF document"),
    (b"OggS", "an Ogg media file"),
    (b"\x00\x00\x01\xba", "an MPEG video"),
)


@dataclass(frozen=True)
class Classified:
    """What the bytes are, what we think they are, and why we refused.

    `reason` is empty exactly when `kind` is readable, and non-empty exactly
    when it is not. Both directions are pinned by tests, because a `reason` that
    is always populated tells the reader nothing.
    """

    kind: FileKind
    detected: str
    reason: str = ""
    declared_disagreed: bool = False


def _looks_like_text(data: bytes) -> bool:
    """Is this something a person typed?

    Two conditions, both necessary. It must decode as UTF-8 - guessing an
    encoding is how a rupee amount becomes a different number - and it must
    contain no NUL byte, which is the reliable tell that bytes are binary even
    when they happen to decode.
    """
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _heic_or_webp(data: bytes) -> str | None:
    """The two container formats whose marker is not at offset zero.

    HEIC is the default for an iPhone photo and WebP is the default for a lot of
    the web, so both of these refusals will actually be seen by people. They are
    named rather than lumped into "unsupported".
    """
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"heim"):
            return "a HEIC photo - the format an iPhone uses by default"
        if brand in (b"avif", b"avis"):
            return "an AVIF image"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "a WebP image"
    return None


def _declared_kind(declared_mime: str) -> FileKind | None:
    """What the caller claimed, mapped onto our own vocabulary.

    Used only to notice a disagreement. It never decides anything.
    """
    return {
        "application/pdf": FileKind.PDF,
        "image/jpeg": FileKind.JPEG,
        "image/jpg": FileKind.JPEG,
        "image/png": FileKind.PNG,
        "text/plain": FileKind.TEXT,
    }.get(declared_mime.split(";")[0].strip().lower())


def classify(data: bytes, declared_mime: str = "") -> Classified:
    """Say what these bytes are. Never raises on content, whatever arrives.

    There is deliberately no `filename` parameter. An extension decides nothing
    here, and a parameter that is accepted and ignored invites a caller to
    believe it matters. When an audit line needs the name it can record it at
    the call site, where it is already in hand.
    """
    if isinstance(data, str):
        raise TypeError(
            f"classify needs bytes, not {type(data).__name__}. A str has an "
            "encoding attached and the whole question here is what the bytes are."
        )

    def answer(kind: FileKind, detected: str, reason: str = "") -> Classified:
        declared = _declared_kind(declared_mime)
        return Classified(
            kind=kind,
            detected=detected,
            reason=reason,
            declared_disagreed=declared is not None and declared is not kind,
        )

    if not data:
        return answer(
            FileKind.UNSUPPORTED,
            "an empty file",
            "That file is empty - there is nothing in it to read.",
        )

    for magic, kind, detected in _READABLE_MAGIC:
        if data.startswith(magic):
            return answer(kind, detected)

    if container := _heic_or_webp(data):
        return answer(FileKind.UNSUPPORTED, container, SUPPORTED_SENTENCE)

    for magic, detected in _KNOWN_BUT_UNREADABLE:
        if data.startswith(magic):
            return answer(FileKind.UNSUPPORTED, detected, SUPPORTED_SENTENCE)

    if _looks_like_text(data):
        return answer(FileKind.TEXT, "plain text")

    return answer(
        FileKind.UNSUPPORTED,
        "a file we do not recognise",
        SUPPORTED_SENTENCE,
    )
