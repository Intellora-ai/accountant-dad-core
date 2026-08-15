"""The rungs, as one backend. Routed on the declared media type, never sniffed.

WHY THIS FILE EXISTS
--------------------
`textlayer.py` reads a PDF's text layer and refuses everything else.
`adapter.TypedTextExtractor` reads a sentence a person typed and refuses
everything else. Both are correct and neither is usable on its own, because the
caller does not know in advance which one a document needs — a bill arrives as a
PDF, as typed text, or as a photograph, and choosing between them per file is
exactly the decision `pipeline.build_draft` must not be made to make.

So this is the router, and it is thin: a media type in, one rung's own answer
out. It reads nothing itself and holds no label vocabulary, no tolerance and no
score.

IT NOW DECIDES ONE THING ABOUT A DOCUMENT, and the sentence above used to say it
decided none. As of 2026-08-13 a PDF that LOOKS SCANNED also goes to the picture
rung, and the two readings are merged with the characters winning. `looks_scanned`
is the whole of the decision and the section below is the whole argument for it.

WHY IT ROUTES ON THE DECLARED MEDIA TYPE AND NOT ON THE BYTES
--------------------------------------------------------------
Sniffing the bytes would mean this module deciding what a file IS, which is the
first line of a reader. It also has a measured failure behind it:
`TypedTextExtractor` used to accept whatever container it was handed and run a
money regex over the wreckage, and the invented totals that produced are
recorded beside `adapter.TYPED_TEXT_MIME`. The caller's own declaration is the
one fact about a document that arrives with it, and each rung checks that
declaration again for itself — `TextLayerReader` still refuses anything that is
not `application/pdf`, and still checks `%PDF-` before `pypdf` sees a byte. This
router being wrong about a file therefore costs a refusal, never a reading.

THERE IS EXACTLY ONE FALLBACK, AS OF 2026-08-13, AND THIS FILE ARGUED AGAINST IT
---------------------------------------------------------------------------------
Until that day this section said "WHY THERE IS NO FALLBACK, AND WHY THAT IS NOT
A MISSING FEATURE", and it gave two reasons. The owner reversed it, the reversal
is right, and the two reasons are answered rather than dropped — a docstring
that quietly stops matching the code beneath it is worse than one that was
wrong, because the next reader trusts it.

WHAT CHANGED. A PDF that `looks_scanned` — no text layer, or fewer than
`SHORTEST_REAL_TEXT_LAYER` characters, or not one bill label anywhere in it — is
now ALSO given to the picture rung, and `_preferring_the_characters` merges the
two readings with the text layer winning every field it read.

WHY. MEASURED on `data/real_invoices/` before the change: of 77 PDFs, 62 carry a
text layer, 5 are encrypted, and TEN carry none. All ten reached the text-layer
rung, found nothing, and returned nothing — while the rung that reads pixels sat
one line away in this same dictionary, wired, and was never asked. Most real
bills are scans. A rung nothing routes to is a rung nobody has.

THE FIRST OLD REASON WAS THAT FALLING BACK MEANS MATCHING ON A SENTENCE, and it
was a good reason. It is answered by not doing that. `textlayer.read` returns a
`TextLayerReading` carrying an `Outcome` — a three-valued enum, `READ`,
`NO_TEXT_LAYER` or `UNREADABLE`, written for exactly this and documented in that
file as the reason there are three and not two. `looks_scanned` reads that enum,
a character count and a label vocabulary, and never `reading.said`. Somebody
rewording a refusal changes nothing about which path a document takes; deleting
that enum member is a `NameError` here, which is the loud failure the old
objection wanted.

THE SECOND OLD REASON WAS THAT THE RUNGS ANSWER DIFFERENT QUESTIONS, and it is
answered by the MERGE rather than by the routing. The engine can only ADD a
field: `_preferring_the_characters` keeps the text layer's value wherever the
text layer read one, so no matter how wide the routing rule gets, a character
that was READ can never be replaced by a guess at a pixel. That is what lets the
routing be generous without costing the property that matters — the text-layer
tier scores 14/20 date, 20/20 party, 20/20 total, 20/20 tax on the twenty
synthetic corpus PDFs with ZERO WRONG, and it still does, after.

WHAT IT COSTS, MEASURED ON ALL 77 REAL PDFs AFTER THE CHANGE:

    kept on the text layer   42   mean  80 ms
    routed to the engine     35   mean 792 ms, max 2814 ms
                                  10 have no text layer
                                  25 have text and no bill label in it
                                   0 fall on the character count
    whole corpus             77   31.1 s, mean 403 ms a document

That is a REQUEST-PATH COST and it is stated rather than capped: a bound is a
number, no owner has given one for this, and `pagereader.READING_DEADLINE_SECONDS`
already stops any single engine call. The 2814 ms document is the honest shape of
the worst case on this machine.

WHAT THE FALLBACK CANNOT TURN INTO. Every field the picture rung supplied keeps
the picture rung's own source string, because the merge moves the source with the
value. `cage/gate.py::_tiers` reads those per-field sources and
`cage/decision.py` allows auto-post only when EVERY tier named is on its
allowlist, which `free_ocr` is not. So one engine-read field on a merged record
caps the whole bill at ASK, by construction rather than by anybody remembering.

THE PICTURE RUNG IS WIRED, AS OF 2026-08-13
--------------------------------------------
It was not, until that day, and the docstring here said so: `freeocr.FreeReader`
took an injected page reader — something that says which words on the page are
the total, the tax, the date and the party — and nothing in this repository
answered it. `accountant/extract/pagereader.py` now does, by running the SAME
field logic `textlayer.py` uses over the words the engine reports, so there is
one label vocabulary and not two.

It cost exactly what this file predicted: entries in the table below, and no
other change here.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That either rung reads a bill well. It grades nothing. Accuracy is
`docs/EXTRACTION_MEASURED.md`, measured against a synthetic corpus, and the
number that matters there is not the score.

That a picture rung exists on the machine this runs on. It needs the `tesseract`
binary, and registering the rung installs nothing. CORRECTED 2026-08-13: this
said the container image deliberately does not install it. The owner reversed
that the same day and the image now installs `tesseract-ocr` and
`tesseract-ocr-eng`. Every OTHER machine is still its own question, and one
without the binary meets `freeocr.ENGINE_MISSING` — a refusal in plain words,
not a crash.

That a document this refuses is unreadable. It says only that no rung here
declares that media type.
"""

from __future__ import annotations

import datetime
import time
from typing import Final

from accountant.cage.confidence import EXACT
from accountant.extract.adapter import (
    NOT_FOUND,
    TYPED_TEXT_MIME,
    ExtractedRecord,
    Extractor,
    TypedTextExtractor,
    UnavailableExtractor,
    _media_type,  # pyright: ignore[reportPrivateUsage]
)
from accountant.extract.freeocr import READABLE_MEDIA, FreeReader
from accountant.extract.labels import (
    DATE_LABEL,
    TAX_PARTS,
    TAX_WHOLE,
    TOTAL_LABELS,
)
from accountant.extract.pagereader import READING_DEADLINE_SECONDS, page_reader
from accountant.extract.textlayer import (
    PDF_MIME,
    Outcome,
    TextLayerReader,
    TextLayerReading,
    picture_of,
    read,
    record_of,
)
from accountant.observability import log

#: The name this backend is registered and reported under.
NAME: Final = "ladder"

#: What a person is told when a document arrives that no rung reads. Named
#: rather than written inline so a test can prove THIS sentence was produced and
#: not merely some refusal.
#:
#: IT REPLACED `NEEDS_A_PAGE_READER` ON 2026-08-13, when the picture rung was
#: wired. That sentence said reading a photograph "needs something that says
#: which words on the page are the total" and asked the person to type the bill
#: in instead. Both halves stopped being true the moment `pagereader.py` landed,
#: and a refusal that describes a gap somebody has since filled sends a person
#: to retype a bill this system can now read. It says what to do rather than
#: "unsupported", because "unsupported" tells somebody to wait for a feature.
NOT_A_KIND_WE_READ: Final = (
    "Please send it as a PDF, as a photograph or a scan, or type the figures in"
)

#: The one structured log line this file writes, named so a test can prove THIS
#: event was emitted and not merely some line. It is written only on the
#: fall-through path - the 62-of-77 case where a PDF has a text layer produces
#: no line, because the rung that answered is already on the record's `backend`
#: and a line per document is a cost with no question behind it.
FELL_THROUGH_EVENT: Final = "extract_no_text_layer"

#: What a person is told about a PDF that carries neither characters nor a
#: picture anything here can open. Named for the same reason `NOT_A_KIND_WE_READ`
#: is: a test proves THIS sentence was produced, not merely some refusal.
NOTHING_IN_IT: Final = (
    "This PDF has no text in it and no picture we could take out of it, so there "
    "was nothing to read. Please send the original scan or photograph, or type "
    "the figures in"
)

#: OWNER-SET, 2026-08-13. A text layer shorter than this many characters is
#: treated as no text layer at all, and the document goes to the picture rung.
#:
#: NOT MEASURED HERE AND NOT DERIVED FROM ANYTHING IN THIS REPOSITORY. The owner
#: stated it. It is written down once, in this constant, so that the day it is
#: replaced by a measured number the replacement is one line and not a search.
#: `tests/test_scanned_pdf.py` binds the boundary in both directions, so moving
#: it is a visible change rather than a silent one.
SHORTEST_REAL_TEXT_LAYER: Final = 20

#: The labels that say a text layer is a BILL rather than page furniture.
#:
#: READ OFF `labels.py` AND NOT WRITTEN AGAIN. The owner named "Date", "Total"
#: and "GST"; those are three entries in a vocabulary this package already
#: holds, and a second copy here is how one of them learns `AMOUNT PAYABLE` and
#: the other does not - the failure `pagereader.py` was built to avoid. Every
#: family the owner named is included whole, so `GRAND TOTAL`, `AMOUNT PAYABLE`,
#: `CGST` and `IGST` all count as the labels a person means by those words.
#:
#: The PARTY labels are deliberately absent, because the owner did not name
#: them and adding a family changes which documents are called scans.
BILL_LABELS: Final[tuple[str, ...]] = (
    DATE_LABEL,
    *TOTAL_LABELS,
    *TAX_WHOLE,
    *TAX_PARTS,
)


def looks_scanned(reading: TextLayerReading) -> bool:
    """Is this PDF a picture of a bill rather than a bill with characters in it?

    THE OWNER'S RULE, 2026-08-13, THREE CONDITIONS, ANY OF THEM:

        no text layer at all        `Outcome.NO_TEXT_LAYER`, which is
                                    `textlayer.read`'s own `not text.strip()`
        fewer than 20 characters    `SHORTEST_REAL_TEXT_LAYER`, owner-set
        no bill label anywhere      `BILL_LABELS`, which is `labels.py`'s own
                                    vocabulary for the words the owner named

    A DOCUMENT THAT DID NOT PARSE IS NOT A SCAN. `Outcome.UNREADABLE` is a
    truncated upload, an encrypted file or bytes that are not a PDF, and the
    reason `textlayer.Outcome` has three members rather than two is written in
    that file: collapsing them "sends a genuinely broken file to an OCR tier
    that will read noise out of it". So this answers False there, the refusal
    the text rung already wrote is what the person gets, and no engine is run on
    a file nobody could open.

    IT READS AN ENUM AND A LENGTH, NEVER A SENTENCE. The old objection in this
    module's docstring was that falling back means matching on prose, and it was
    right. Nothing here looks at `reading.said`.

    WHAT EACH CONDITION ACTUALLY FIRES ON, MEASURED over the 77 PDFs in
    `data/real_invoices/` rather than reasoned about:

        no text layer     10
        no bill label     25   these carry text and print none of these labels;
                               they are UK government forms and this vocabulary
                               is written for Indian GST bills
        under 20 chars     0   not one document in the corpus falls on the
                               owner's number. It is carried because he set it,
                               and this zero is what says so honestly

    So the third condition is what does the work, and it costs 25 documents
    about 0.8 seconds each to read the same words worse. That cost is real and
    it is reported. What makes it SAFE rather than merely expensive is
    `_preferring_the_characters` below, which is why the two landed together:
    MEASURED on those 35 routed documents, the number of exactly-read fields the
    engine could have overwritten is zero, and the number it is ALLOWED to
    overwrite is also zero, and only the second of those is a property.
    """
    if reading.outcome is Outcome.NO_TEXT_LAYER:
        return True
    if reading.outcome is not Outcome.READ:
        return False
    text = reading.text.strip()
    if len(text) < SHORTEST_REAL_TEXT_LAYER:
        return True
    printed = text.upper()
    return not any(label in printed for label in BILL_LABELS)


def _preferring_the_characters(
    characters: ExtractedRecord, picture: ExtractedRecord
) -> ExtractedRecord:
    """The two readings, merged, with the text layer winning every field it read.

    THE REPLACEMENT RULE, AND IT IS WHAT MAKES THE FALL-THROUGH SAFE. The engine
    can only ADD a field here. It can never overwrite one the characters already
    stated, so the tier this repository measures at 14/20 date, 20/20 party,
    20/20 total, 20/20 tax with ZERO WRONG cannot lose a single one of those to
    a guess - whatever the routing rule above decides about a document.

    That matters most for the owner's third condition. A bill printing
    `SUPPLIER: SHARMA TRADERS` and `AMOUNT DUE: 4,200.00` under labels this
    vocabulary happens not to hold would be called a scan, sent to the engine,
    and - without this - come back with the exactly-read supplier replaced by
    whatever the engine made of the pixels. With this, the supplier is still the
    supplier and the engine's contribution is whatever it added.

    PER FIELD, AND THE SOURCE TRAVELS WITH THE VALUE. `cage/gate.py::_tiers`
    reads `per_field_source` field by field and `cage/decision.py` allows
    auto-post only when EVERY tier named is on its allowlist - so a merged
    record carrying one `free_ocr` field is capped at ASK by construction. A
    merge that kept the values and dropped the sources would have handed the
    engine's guesses the text layer's privileges.

    `raw_text` AND `pdf_repaired` COME FROM THE CHARACTERS. Both are facts about
    the bytes rather than about a reading, the picture rung deliberately reports
    no text at all - see `freeocr.FreeReader.extract` on why a scanned bill must
    not reach `Voucher.narration` - and `pdf_repaired` is `textlayer`'s
    measurement and nothing else's.
    """
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    scores: dict[str, float] = {}
    read_by_characters = False
    read_by_picture = False
    for name in ExtractedRecord.FIELDS:
        exact = characters.value_of(name)
        stated = characters.per_field_source.get(name, "")
        if exact is not None and not stated.startswith(NOT_FOUND):
            read_by_characters = True
            values[name] = exact
            sources[name] = stated
            scores[name] = characters.confidence_of(name) or EXACT
            continue
        guessed = picture.value_of(name)
        values[name] = guessed
        sources[name] = picture.per_field_source.get(name, stated)
        scores[name] = picture.confidence_of(name) or 0.0
        read_by_picture = read_by_picture or guessed is not None
    return ExtractedRecord(
        date=_as_date(values["date"]),
        party=_as_str(values["party"]),
        total_paise=_as_int(values["total_paise"]),
        tax_paise=_as_int(values["tax_paise"]),
        line_items=characters.line_items or picture.line_items,
        raw_text=characters.raw_text,
        # THE RUNG THAT SUPPLIED THE FIELDS, and not this class. A row saying
        # only "ladder" is not evidence about either rung, which is the whole
        # content of `docs/EXTRACTION_MEASURED.md`. A merged record naming the
        # picture rung is the honest answer when the picture rung added
        # anything, because the per-field sources are what say which field came
        # from where and this single string cannot say two things.
        backend=picture.backend
        if read_by_picture or not read_by_characters
        else characters.backend,
        per_field_source=sources,
        per_field_confidence=scores,
        pdf_repaired=characters.pdf_repaired,
    )


def _as_date(value: object) -> datetime.date | None:
    return value if isinstance(value, datetime.date) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class Ladder:
    """Every rung the product has, behind one `adapter.Extractor`.

    THE RECORD NAMES THE RUNG THAT READ IT, NOT THIS CLASS. `backend` on the
    returned record is `typed_text` or `pdf_text_layer`, because a row saying
    only "ladder" cannot be used as evidence about either rung — and telling the
    two apart is the whole content of `docs/EXTRACTION_MEASURED.md`. Only a
    refusal this class made itself is stamped `ladder`.
    """

    name = NAME

    def __init__(self) -> None:
        # Built once, here, and not per call. Every rung is stateless and holds
        # no handle to anything; rebuilding them per document would be a cost
        # with no fact behind it.
        #
        # THE PICTURE RUNG IS ONE OBJECT UNDER FIVE MEDIA TYPES, and the five
        # are read off `freeocr.READABLE_MEDIA` rather than written again here.
        # A second list is how this router ends up claiming to read a TIFF the
        # rung refuses, or refusing one it reads - and the rung checks the
        # declaration again for itself either way, so a disagreement costs a
        # confusing refusal rather than a wrong reading.
        picture = FreeReader(page_reader(deadline_seconds=READING_DEADLINE_SECONDS))
        self._picture = picture
        self._rungs: dict[str, Extractor] = {
            TYPED_TEXT_MIME: TypedTextExtractor(),
            PDF_MIME: TextLayerReader(),
            **dict.fromkeys(READABLE_MEDIA, picture),
        }

    def reads(self) -> tuple[str, ...]:
        """Every media type a rung is wired for, sorted so the list is stable."""
        return tuple(sorted(self._rungs))

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        declared = _media_type(mime)
        if declared == PDF_MIME:
            return self._read_pdf(data)
        rung = self._rungs.get(declared)
        if rung is None:
            return self._refuse(declared)
        return rung.extract(data, mime)

    def _read_pdf(self, data: bytes) -> ExtractedRecord:
        """The text layer, and the picture rung when there is no text layer.

        THE BYTES ARE PARSED ONCE. `textlayer.read` gives the typed reading and
        `textlayer.record_of` turns that same reading into the record - which is
        why `record_of` is a function rather than a method now. Calling
        `TextLayerReader.extract` and then asking the document a second time
        whether it had a text layer would be two parses of untrusted bytes in
        the web process to answer one question.

        THE MEDIA-TYPE CHECK IS NOT LOST BY BYPASSING THE RUNG'S `extract`.
        `read` refuses anything not beginning `%PDF-` before `pypdf` sees a
        byte, which is a check on the BYTES and therefore strictly stronger than
        the check on the declaration it replaces here. Those bytes come back
        `Outcome.UNREADABLE`, which is not `NO_TEXT_LAYER`, so a JPEG somebody
        declared as a PDF is refused exactly as before and does not fall
        through.
        """
        reading = read(data)
        if not looks_scanned(reading):
            return record_of(reading)
        return self._picture_in(data, reading)

    def _picture_in(self, data: bytes, reading: TextLayerReading) -> ExtractedRecord:
        """The fall-through, and the one line that says it happened.

        TIMED, AND THE TIME IS ON THE LINE, because this is the expensive path
        and nothing else in the product can see it. The text layer costs about
        ten milliseconds; the engine costs the better part of a second on a
        full-page scan - MEASURED at 731ms to 1483ms on the eight readable
        no-text-layer PDFs in `data/real_invoices/`. A deployment that starts
        receiving scans should be able to find that out from its own logs rather
        than from a stopwatch.

        NO BYTES AND NO TEXT REACH THE LOG. `observability.log` is given
        identifiers, media types, durations and this module's own words -
        `observability.log` says in its own docstring that a log is the copy of
        your data that ends up in the widest number of places.
        """
        started = time.perf_counter()
        found = picture_of(data)
        if not found.data:
            answered = self.name
            record = self._refuse_nothing_in_it(found.said)
        else:
            answered = self._picture.name
            record = self._picture.extract(found.data, found.media_type)
        record = _preferring_the_characters(record_of(reading), record)
        log(
            FELL_THROUGH_EVENT,
            media=PDF_MIME,
            pages=reading.pages,
            characters=len(reading.text.strip()),
            answered_by=answered,
            answered_with=record.backend,
            picture=found.media_type or "none",
            picture_bytes=len(found.data),
            page=found.page,
            ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return record

    def _refuse_nothing_in_it(self, why: str) -> ExtractedRecord:
        """A PDF with neither characters nor a picture, refused in both halves.

        BOTH FACTS, IN ONE SENTENCE. Returning the text-layer rung's own record
        here would tell the person "reading pixels is the other tier's job" -
        true until this morning, and now a refusal describing a gap this system
        has since filled, which is the exact failure `NOT_A_KIND_WE_READ`
        replaced `NEEDS_A_PAGE_READER` for.
        """
        return UnavailableExtractor(f"{why} {NOTHING_IN_IT}", name=self.name).extract(
            b"", ""
        )

    def _refuse(self, declared: str) -> ExtractedRecord:
        """Every field `not_found`, with one sentence saying why, on each.

        `UnavailableExtractor` and not a second record built here — the argument
        `registry.GuardedExtractor.outage`, `adapter.TypedTextExtractor._refuse`
        and `textlayer.TextLayerReader._refuse` all already make. Two places
        that build this shape is how one of them ends up without a reason on it,
        which is a silent blank wearing a label.
        """
        return UnavailableExtractor(
            f"{self.name} reads {', '.join(self.reads())} and was handed "
            f"{declared or 'no media type'}. {NOT_A_KIND_WE_READ}",
            name=self.name,
        ).extract(b"", "")
