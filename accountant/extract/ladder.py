"""The rungs, as one backend. Routed on the declared media type, never sniffed.

WHY THIS FILE EXISTS
--------------------
`textlayer.py` reads a PDF's text layer and refuses everything else.
`adapter.TypedTextExtractor` reads a sentence a person typed and refuses
everything else. Both are correct and neither is usable on its own, because the
caller does not know in advance which one a document needs — a bill arrives as a
PDF, as typed text, or as a photograph, and choosing between them per file is
exactly the decision `pipeline.build_draft` must not be made to make.

So this is the router, and it is deliberately thin: a media type in, one rung's
own answer out. It reads nothing itself, decides nothing about a document, and
holds no rule that either rung does not already hold.

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

WHY THERE IS NO FALLBACK, AND WHY THAT IS NOT A MISSING FEATURE
----------------------------------------------------------------
"Try the text layer, fall back to the other tier" is the obvious shape and it is
the wrong one here, for two separate reasons.

The first is that falling back requires reading a rung's refusal back out of the
record it returned, by matching on the sentence it wrote. A sentence is prose
for a person; making control flow depend on it means the day somebody improves
the wording is the day a document silently takes a different path.

The second is that the rungs answer DIFFERENT QUESTIONS about a document, not
the same question with different accuracy. A PDF with no text layer is a scan,
and the honest answer to it is "there are no characters in this file", not "let
me guess at the pixels" — that is a second document type, and turning it into
one silently is a decision nobody made about somebody's books.

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
binary, which the container image deliberately does not install, and a machine
without one meets `freeocr.ENGINE_MISSING` — a refusal in plain words, not a
crash. Registering the rung did not install anything.

That a document this refuses is unreadable. It says only that no rung here
declares that media type.
"""

from __future__ import annotations

from typing import Final

from accountant.extract.adapter import (
    TYPED_TEXT_MIME,
    ExtractedRecord,
    Extractor,
    TypedTextExtractor,
    UnavailableExtractor,
    _media_type,  # pyright: ignore[reportPrivateUsage]
)
from accountant.extract.freeocr import READABLE_MEDIA, FreeReader
from accountant.extract.pagereader import READING_DEADLINE_SECONDS, page_reader
from accountant.extract.textlayer import PDF_MIME, TextLayerReader

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
        self._rungs: dict[str, Extractor] = {
            TYPED_TEXT_MIME: TypedTextExtractor(),
            PDF_MIME: TextLayerReader(),
            **dict.fromkeys(READABLE_MEDIA, picture),
        }

    def reads(self) -> tuple[str, ...]:
        """Every media type a rung is wired for, sorted so the list is stable."""
        return tuple(sorted(self._rungs))

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        rung = self._rungs.get(_media_type(mime))
        if rung is None:
            return self._refuse(_media_type(mime))
        return rung.extract(data, mime)

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
