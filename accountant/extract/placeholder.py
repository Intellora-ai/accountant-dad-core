"""The document reader nobody has chosen yet, written down as a backend.

WHY A CLASS AND NOT A MISSING FEATURE
-------------------------------------
Task 9, 2026-08-11. `grep -rn "multipart\\|enctype\\|type=file" accountant/`
returned nothing: there was no way to hand this product a document at all, so
the question "what does it say about a bill it cannot read" had never been
asked over the surface a person touches.

The owner decision is already made and is NOT re-opened here:
`artifacts/extraction_backends.md:3` — *"third-party backend selection =
OWNER_DECISION_REQUIRED"* — and `D-23` is open. No vendor account exists, no
credential exists, and no vendor SDK may be imported into this package
(`tests/test_no_reader.py` allows stdlib and `accountant.*` and nothing else).

So the honest object is this one: a backend that reads NOTHING and says so, in
the same shape every other backend answers in. It exists so that the seam is
BUILT rather than described — `registry.build("no_reader")` produces it today,
and the day a vendor is selected the swap is the same one line
(`DEFAULT_BACKEND`) that every other backend swap already is.

WHAT IT MUST NEVER DO, AND WHY EACH ONE IS A SEPARATE SENTENCE
---------------------------------------------------------------
    never guess          a fabricated total sourced to a real-looking backend
                         is worse than a blank AND worse than a refusal: a
                         refusal asks the person a question and an invented
                         number does not. The three measured fabrications are
                         recorded beside `adapter.TYPED_TEXT_MIME`.

    never blank          S3. Every named field is present, always, and is
                         either a value or an explicit `not_found` carrying the
                         sentence saying why. This backend has no values, so it
                         has four stated `not_found`s and nothing else.

    never a name it is   the record's `backend` is `no_reader`, which is true.
    not                  Calling it `stub` would put it in the same audit rows
                         as the testing double; calling it a vendor's name
                         would make `S2 = NOT_MEASURED` unreadable off the
                         trail. A row that cannot say who wrote it is not
                         evidence about anybody.

    never hold the       `raw_text` stays empty. `pipeline.build_draft` copies
    document             `record.raw_text` into `Voucher.narration`, which
                         reaches the page, the durable action log and — on a
                         VALID entry — Tally itself. A backend that echoed the
                         uploaded bytes would put somebody's scanned bill in
                         all three, and "the uploaded bytes are never logged"
                         would be a property of the route rather than of the
                         data. It is structural here instead.

BUILT BY `UnavailableExtractor`, NOT BY A SECOND COPY OF THE SHAPE
-------------------------------------------------------------------
Exactly the argument `registry.GuardedExtractor.outage` and
`adapter.TypedTextExtractor._refuse` already make, and for the same measured
reason: two places that build an all-`not_found` record is how one of them ends
up without a reason on it, which is a silent blank wearing a label.

`UnavailableExtractor` is worded for an outage and this is not one — nothing is
down, nothing was tried. What the two share is the only thing being reused: the
record shape, and the guarantee that every field carries a stated reason. The
reason itself is this module's, and it says which of the two situations the
person is in.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any document was ever read. Nothing here reads anything, on purpose.
`S2 = NOT_MEASURED` stays true, and the question rate for uploaded documents is
not zero and is not measured — it cannot be, because there is no reader to
measure.
"""

from __future__ import annotations

from accountant.extract.adapter import ExtractedRecord, UnavailableExtractor

#: The one fact the person needs, in the words they need it in. Kept as a
#: constant because the web app quotes it on the page and the tests match it:
#: three copies of a sentence is how the page and the record end up saying
#: different things about the same document.
NO_READER_CONFIGURED = "no document reader is configured"

#: What lands on every field. It says WHICH of the two absences this is —
#: "nobody chose a reader" rather than "the reader looked and found nothing" —
#: because those are different facts about the document and only one of them is
#: about the document at all.
NO_READER_REASON = (
    f"{NO_READER_CONFIGURED}, so nothing on this file was read. The owner has "
    "not selected a third-party reader yet, and this product never guesses at "
    "a figure it did not read"
)


class PlaceholderReader:
    """Reads nothing. Says so, on every field, in the same shape as everything.

    The seam it plugs into is the `Extractor` Protocol, unchanged, and
    `registry.guarded()` wraps it exactly as it wraps any other backend. That
    is the whole point: swapping a real vendor in later changes
    `registry.DEFAULT_BACKEND` and nothing else.
    """

    name = "no_reader"

    def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
        """The uploaded bytes are not read, not decoded, and not carried on.

        Both parameters are ignored deliberately rather than incidentally.
        Touching `_data` at all — even to measure it — would be the first line
        of a reader, and `tests/test_no_reader.py` exists because that line is
        always cheap to add and never cheap to remove.
        """
        return UnavailableExtractor(NO_READER_REASON, name=self.name).extract(b"", "")
