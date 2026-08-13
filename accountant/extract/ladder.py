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

The second is the one that actually decides it: THE SECOND RUNG IS NOT WIRED.
`freeocr.FreeReader` satisfies the same Protocol and takes an injected page
reader — something that says which words on the page are the total, the tax, the
date and the party. Nothing in this repository does that, on purpose: it is
field detection, it cannot be checked without a pile of bills whose answers are
already known (`H-02`), and a heuristic written without one would be unmeasured,
unfalsifiable and confident. So the picture rung refuses in a sentence naming
what it still needs, and `registry._NEEDS_WIRING` carries the same sentence.

Adding it later is one entry in the table below and no other change here.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That either rung reads a bill well. It grades nothing. Accuracy is
`docs/EXTRACTION_MEASURED.md`, measured against a synthetic corpus, and the
number that matters there is not the score.

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
from accountant.extract.textlayer import PDF_MIME, TextLayerReader

#: The name this backend is registered and reported under.
NAME: Final = "ladder"

#: What a person is told when a picture arrives. Named rather than written
#: inline so a test can prove THIS sentence was produced and not merely some
#: refusal, and so the same words appear here and in `registry._NEEDS_WIRING`.
#:
#: It says what is missing rather than "unsupported", because the two lead to
#: completely different next actions: one is a person retyping the bill, the
#: other is somebody waiting for a feature that is not coming until `H-02` does.
NEEDS_A_PAGE_READER: Final = (
    "reading a picture of a bill needs something that says which words on the "
    "page are the total, the tax, the date and the supplier, and nothing here "
    "does that yet. Please type this one in instead"
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
        # Built once, here, and not per call. Both rungs are stateless and hold
        # no handle to anything; rebuilding them per document would be a cost
        # with no fact behind it.
        self._rungs: dict[str, Extractor] = {
            TYPED_TEXT_MIME: TypedTextExtractor(),
            PDF_MIME: TextLayerReader(),
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
            f"{declared or 'no media type'}. {NEEDS_A_PAGE_READER}",
            name=self.name,
        ).extract(b"", "")
