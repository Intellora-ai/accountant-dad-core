"""Two hundred files nobody would ever send on purpose, and what happens to them.

WHY THIS FILE EXISTS
--------------------
The product's promise is "anything in, safe processing out". That sentence has
three failure modes and only one of them is obvious:

    it crashes          a stack trace instead of a refusal. The person is told
                        the application is broken when their upload was merely
                        cut off, and there is nothing on screen to act on.
    it posts            a file nobody could read reaches a ledger. This is the
                        expensive one, and it is the one that does not announce
                        itself.
    it refuses silently a refusal with no sentence on it is a dead end. The
                        person is stopped and not told what would work.

Every existing test in this repository asserts one of those on a handful of
hand-picked byte strings - `tests/test_classify.py` has seven. Seven is a
sample, not a sweep, and the interesting inputs are the ones nobody thought of.
So this file drives TWO HUNDRED NAMED inputs, built by
`scripts/build_chaos_corpus.py`, through the real input layer and counts all
three numbers.

WHAT "DRIVEN THROUGH" MEANS HERE, EXACTLY
------------------------------------------
Each case goes through three stages, and each stage is the shipped one:

    classify    `accountant/cage/classify.py`, with the declared MIME type the
                browser or phone would have sent
    read        the reader the wired path would hand those bytes to -
                `TypedTextExtractor` for text, `TextLayerReader` for a PDF,
                and `PlaceholderReader` otherwise, which is the honest answer
                for an image today (`registry._READY` registers `no_reader`
                and `artifacts/extraction_backends.md` says why)
    decide      `accountant/cage/gate.py`, asked on the most PERMISSIVE posture
                a caller can take

That last word is the whole design. Passing `party_known=None` would block
every one of the two hundred without any of them being examined, and the file
would pass while measuring nothing. So every world fact is supplied in the
posting direction - the books are open, the party is known, GST posting is off,
no questions have been asked - and the only thing left that can stop a post is
that nothing on the file was read. `test_the_control_a_clean_bill_does_post
_through_the_identical_path` is what proves the harness can post at all;
without it, "0 posts" is indistinguishable from a harness wired to refuse.

WHAT WAS MEASURED, 2026-08-13
-----------------------------
Run on all 200, on the permissive posture described above:

    crashes                             0
    posts                               0
    files refused by the classifier    72, every one carrying a sentence
    decisions                         200 - 200 block, 0 ask, 0 post
                                      RE-MEASURED the same day: it read
                                      199 block / 1 ask until the owner made a
                                      conservation FAIL a hard rule. The one ask
                                      was the bill whose line items do not sum,
                                      and it is a block now. Nothing moved
                                      towards the books - 0 posts before and 0
                                      after - which is the only column that
                                      would have been a finding.
    decisions carrying a sentence     200
    entries carried by a refusal        0

And how deep the corpus actually reaches, which is the number that says
whether the three zeros mean anything:

    a total read off the document      46
    a supplier read off the document    5
    a date read off the document        4
    all four fields read                2

Those last four are small ON PURPOSE and are the honest shape of the corpus:
195 of these files die at the classifier or at the first unread field, which
is what a chaos corpus is mostly made of. The depth lives in the five
`NEAR_MISSES` - readable bills with exactly one thing wrong - and they are what
carry the run past the reader into conservation, the confidence band and the
wall. `test_the_near_miss_bills_are_read_deeply_enough_to_reach_the_safety
_layer` is the assertion that keeps them there.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That any of these files is handled WELL. A refusal is the outcome for almost
all of them and a refusal is correct, but this measures that the refusal
happened and carries a sentence - not that the sentence is the best one.

That the images are photographs. They are generated, and
`build_chaos_corpus.PHOTO_LIMITATION` says so in the corpus itself: what a
generated cat exercises is a NON-DOCUMENT IMAGE arriving at the input layer,
not camera noise, lens blur or JPEG artefacts. Real-photograph behaviour is
NOT_MEASURED.

That an OCR tier is safe. `accountant/extract/freeocr.py` shells out to a
tesseract binary, and driving two hundred images through a subprocess would
measure the machine it ran on rather than this repository. It is not on the
default path (`registry.DEFAULT_BACKEND` is `typed_text`) and it is not
exercised here.

NO NETWORK, NO SUBPROCESS, NO FIXTURE FILES. Every byte is generated in memory
by the corpus builder, which reads no clock and no random source.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import struct
import time
import unicodedata
import zlib
from dataclasses import dataclass
from typing import Final

import pytest

from accountant.cage.classify import Classified, FileKind, classify
from accountant.cage.decision import Action, Decided, Moment
from accountant.cage.gate import gate
from accountant.extract.adapter import ExtractedRecord, TypedTextExtractor
from accountant.extract.placeholder import PlaceholderReader
from accountant.extract.textlayer import TextLayerReader
from accountant.pipeline import Draft
from accountant.schema import Voucher
from scripts import build_chaos_corpus as chaos

#: Words that mean a developer's output reached a person's screen. A refusal
#: carrying any of them is a leaked traceback wearing a sentence.
#:
#: `not_found` and `None` are on the list and they are the ones that matter.
#: They are OUR sentinels, they are on every unread field's `per_field_source`,
#: and the whole failure mode is one of them being forwarded verbatim instead
#: of being turned into a sentence. Measured over all 200: zero hits for every
#: word here, so none of them is carrying a false positive today.
DEVELOPER_WORDS: Final = (
    "Traceback",
    "Exception",
    "Error",
    "NoneType",
    "None",
    "not_found",
    "errno",
    "self.",
)

#: The bill the corpus is measured against, which is the one file here that
#: SHOULD post. Built by the generator and deliberately not in the corpus.
CONTROL_TOTAL: Final = 420_000
CONTROL_DATE: Final = datetime.date(2026, 8, 12)
CONTROL_PARTY: Final = "SHARMA TRADERS"

#: The five cases that are read all the way down instead of dying at the
#: classifier. Named here rather than filtered by family, because "the ones
#: that go deep" is the property under test and a family label is not evidence
#: of it - a sixth adversarial case would silently join the set otherwise.
NEAR_MISSES: Final = (
    "a_bill_stating_two_different_totals",
    "a_bill_whose_line_items_do_not_sum",
    "a_bill_with_a_negative_total",
    "a_bill_with_half_a_paisa_on_it",
    "a_bill_whose_date_could_be_read_two_ways",
)


@dataclass(frozen=True)
class Ran:
    """One chaos input and everything the input layer said about it."""

    case: chaos.ChaosCase
    seen: Classified | None
    record: ExtractedRecord | None
    decided: Decided | None
    #: Empty when nothing raised. The exception's type and message otherwise,
    #: so a failure names the file rather than only the count.
    crashed: str


def read_with(kind: FileKind, data: bytes, declared_mime: str) -> ExtractedRecord:
    """The reader the wired path would hand these bytes to.

    `PlaceholderReader` for everything that is not text or a PDF, including the
    files `classify` refused. A refused file has no reader in production, but
    running one anyway is the stronger claim: it says the refusal is not the
    ONLY thing standing between a zip archive and a ledger.
    """
    if kind is FileKind.TEXT:
        return TypedTextExtractor().extract(data, "text/plain")
    if kind is FileKind.PDF:
        return TextLayerReader().extract(data, "application/pdf")
    return PlaceholderReader().extract(data, declared_mime)


def draft_of(record: ExtractedRecord) -> Draft:
    """A draft carrying the record, with both legs named so neither is the block.

    An empty account is a question in `decision.py`, and a question is not a
    post - so leaving them blank would hide a post behind an ASK and the count
    would look clean for the wrong reason.
    """
    voucher = Voucher(
        id="chaos",
        date=record.date or CONTROL_DATE,
        party=record.party or "",
        narration="",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=record.total_paise if type(record.total_paise) is int else 0,
    )
    return Draft(
        id="chaos",
        company="Demo Co",
        voucher=voucher,
        record=record,
        operation_id="op-chaos",
    )


def most_permissive_gate(record: ExtractedRecord) -> Decided:
    """Every world fact in the posting direction. The only brake left is reading.

    `net_paise` is supplied as total minus tax when the reader read both,
    because withholding it would make `net_plus_tax_equals_gross`
    INDETERMINATE and block every case on the harness rather than on the file.
    A number the harness supplies is not evidence about the document, which is
    exactly why it is supplied HERE and never inside `gate`.

    `pdf_repaired=None` is the same posture, added 2026-08-13 when the field
    arrived: `None` means "not a PDF, or nothing to repair", which grants the
    full post and is therefore the permissive answer this harness wants. It is
    also the TRUE answer for this corpus - the extraction path these cases run
    through does not report a repair - and if it ever does, the honest value
    lands here and those cases become questions rather than posts.
    """
    total, tax = record.total_paise, record.tax_paise
    net = total - tax if type(total) is int and type(tax) is int else None
    return gate(
        draft_of(record),
        moment=Moment.BEFORE_THE_WRITE,
        party_known=True,
        period_open=True,
        carries_gst=False,
        pdf_repaired=None,
        questions_asked=0,
        net_paise=net,
    )


def drive(case: chaos.ChaosCase) -> Ran:
    """Classify, read, decide - catching anything that escapes on the way.

    `Exception` and not `BaseException`, matching `registry.GuardedExtractor`:
    a KeyboardInterrupt is somebody stopping the run and answering it with a
    tidy record would fight them.
    """
    seen: Classified | None = None
    record: ExtractedRecord | None = None
    decided: Decided | None = None
    crashed = ""
    try:
        seen = classify(case.data, case.declared_mime)
        record = read_with(seen.kind, case.data, case.declared_mime)
        decided = most_permissive_gate(record)
    # Broad on purpose: the count of what escapes IS the measurement, so
    # narrowing this to the exceptions we already know about would silently
    # stop measuring the ones we do not.
    except Exception as exc:
        crashed = f"{type(exc).__name__}: {exc}"
    return Ran(case=case, seen=seen, record=record, decided=decided, crashed=crashed)


def one_byte_case(value: int) -> chaos.ChaosCase:
    """One byte, wrapped so the 256-value sweep runs the identical `drive`.

    Declared as a PDF on purpose: it is the claim a browser is most likely to
    make about a file it cannot identify, and the whole point of the sweep is
    that the claim decides nothing.
    """
    return chaos.ChaosCase(
        name=f"one_byte_{value:03d}",
        family=chaos.FAMILIES[0],
        filename="one.pdf",
        declared_mime="application/pdf",
        data=bytes([value]),
        why="One byte on its own, from the sweep over every possible value.",
    )


def is_a_plain_sentence(said: str) -> bool:
    """Could a person read this and know what to do next?

    Four conditions, each of which a real defect has produced somewhere: an
    empty string, a bare token with no space in it, a fragment that never ends,
    and a developer's own output forwarded to a stranger.
    """
    text = said.strip()
    if len(text) < 20 or " " not in text or not text.endswith("."):
        return False
    return not any(word in text for word in DEVELOPER_WORDS)


CASES: Final = chaos.build_chaos_cases()
RUNS: Final = tuple(drive(case) for case in CASES)
REFUSED: Final = tuple(
    run
    for run in RUNS
    if run.seen is not None and run.seen.kind is FileKind.UNSUPPORTED
)


# ---- the corpus is what it says it is ---------------------------------------


def test_the_corpus_holds_exactly_two_hundred_chaos_inputs() -> None:
    """The owner asked for 200. A corpus that quietly shrinks to 40 still
    reports "0 crashes" and the number stops meaning anything."""
    assert len(CASES) == 200
    assert chaos.CASE_COUNT == 200


def test_no_two_chaos_inputs_share_a_name() -> None:
    """A name is how a failure is reported back. Two cases called the same
    thing is one of them being invisible in every report forever."""
    names = [case.name for case in CASES]

    assert len(set(names)) == len(names)


def test_every_chaos_input_is_named_for_what_it_actually_is() -> None:
    """ "file_042" is not a name, it is an index. A name that does not describe
    the input cannot tell a reviewer whether the corpus covers anything."""
    for case in CASES:
        assert len(case.name) >= 8, case.name
        assert "_" in case.name, case.name
        assert not case.name.strip("abcdefghijklmnopqrstuvwxyz_0123456789"), case.name


def test_every_chaos_input_says_in_one_plain_sentence_what_it_exercises() -> None:
    """A case with no stated purpose is a case nobody can delete safely, and
    a corpus nobody can prune is one that grows until it is skipped."""
    for case in CASES:
        assert is_a_plain_sentence(case.why), (case.name, case.why)


def test_the_inputs_the_owner_asked_for_by_name_are_all_present() -> None:
    """The named list is the requirement. Building 200 files of one shape
    would satisfy every count in this file and none of the requirement."""
    missing = [
        name for name in chaos.REQUIRED_NAMES if name not in {c.name for c in CASES}
    ]

    assert missing == []


def test_the_corpus_is_two_hundred_different_uploads_not_one_repeated() -> None:
    """THE CONTROL on every count below. 200 copies of an empty file would
    score 0 crashes and 0 posts and would prove nothing at all.

    An upload is its bytes AND the claim the client made about them. The
    `the_liars` family exists to send the same bytes under a different name and
    media type, so byte-distinctness is the wrong measure for it and the right
    one for everything else.
    """
    uploads = {(case.data, case.declared_mime, case.filename) for case in CASES}

    assert len(uploads) == 200


def test_outside_the_liars_family_no_two_cases_share_their_bytes() -> None:
    """The other half of the control, with no threshold invented for it: the
    count is derived from the corpus rather than chosen."""
    honest = [case for case in CASES if case.family != "the_liars"]
    digests = {hashlib.sha256(case.data).hexdigest() for case in honest}

    assert len(digests) == len(honest)


def test_building_the_corpus_twice_produces_the_identical_bytes() -> None:
    """Determinism, rule 11.2.10. No clock and no random source, so a rebuild
    that differs is a real change and the manifest can be trusted."""
    again = chaos.build_chaos_cases()

    assert [c.data for c in again] == [c.data for c in CASES]


def test_building_and_driving_the_whole_corpus_fits_the_owners_time_budget() -> None:
    """The owner's number, not a proxy for it.

    THIS ASSERTED `total_bytes < 4_000_000` UNTIL THE REVIEW PASS, which was a
    size cap nobody set standing in for a time budget somebody did. The budget
    is 300 seconds for this file. A corpus that grew slow enough to be marked
    slow would be skipped, and a skipped sweep measures nothing at all - so the
    thing to assert is the seconds.

    Measured on this machine: about 2 of the 300, a margin of roughly 150x, so
    this is a budget rather than a timing flake waiting to happen. The byte
    total rides along in the failure message because "why is it slow" is
    usually answered by "it got big".
    """
    started = time.perf_counter()
    rebuilt = chaos.build_chaos_cases()
    for case in rebuilt:
        drive(case)
    seconds = time.perf_counter() - started

    assert seconds < 300, (seconds, sum(len(c.data) for c in rebuilt))


def test_the_manifest_lists_every_case_with_a_hash_of_its_real_bytes() -> None:
    """The manifest is what a report cites, and a manifest whose hashes do not
    match the corpus is worse than no manifest: it is a citation to bytes that
    were never built. Written to `tmp_path`, so no committed file is touched.

    `write_corpus` would otherwise be the one function in the generator that
    nothing runs, and untested code that emits evidence is how a stale
    artefact ends up in a report.
    """
    by_name = {row["name"]: row for row in chaos.manifest_rows(CASES)}

    assert len(by_name) == 200
    for case in CASES:
        assert by_name[case.name]["sha256"] == hashlib.sha256(case.data).hexdigest()


def test_writing_the_manifest_puts_it_where_a_report_can_cite_it(
    tmp_path: pathlib.Path,
) -> None:
    """THE CONTROL on the test above: `manifest` being right in memory says
    nothing about the file actually landing on disk with that content."""
    counts = chaos.write_corpus(tmp_path)
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert counts == {"cases": 200, "documents": 0}
    assert written["case_count"] == 200
    assert written["photo_limitation"] == chaos.PHOTO_LIMITATION


def test_every_family_the_corpus_claims_has_cases_in_it() -> None:
    """A family declared and never populated is a coverage claim with nothing
    behind it - the same defect as an AST scan running over an empty set."""
    present = {case.family for case in CASES}

    assert present == set(chaos.FAMILIES)


# ---- pass condition 1: nothing crashes --------------------------------------


def test_no_chaos_input_crashes_anything_it_is_driven_through() -> None:
    """PASS CONDITION 1, measured over all 200.

    A classifier or reader that raises turns a refusal into an HTTP 503 saying
    the application is broken. `accountant/web/app.py` does exactly that with
    any escaping exception, so this is not a hypothetical.
    """
    crashed = [(run.case.name, run.crashed) for run in RUNS if run.crashed]

    assert crashed == []


def test_the_control_the_crash_counter_notices_something_that_does_raise() -> None:
    """THE CONTROL on the count above.

    `drive` swallows exceptions on purpose, so a `drive` that swallowed them
    without recording would report zero crashes for ever. This feeds it bytes
    that DO raise - a `str` where bytes belong - and the counter must see it.
    """
    exploding = chaos.ChaosCase(
        name="a_str_where_bytes_belong",
        family=chaos.FAMILIES[0],
        filename="control.txt",
        declared_mime="text/plain",
        data="a string, not bytes",  # type: ignore[arg-type]
        why="A control input that makes the classifier raise on purpose.",
    )

    assert drive(exploding).crashed.startswith("TypeError")


# ---- pass condition 2: nothing posts ----------------------------------------


def test_no_chaos_input_reaches_a_post() -> None:
    """PASS CONDITION 2, measured over all 200, on the most permissive posture.

    Every world fact is supplied in the posting direction, so the only thing
    left that can refuse these files is that nothing on them was read. This is
    the count that matters: a crash is an outage, a post is somebody's ledger.
    """
    posted = [
        run.case.name
        for run in RUNS
        if run.decided is not None and run.decided.action is Action.POST
    ]

    assert posted == []


def test_the_control_a_clean_bill_does_post_through_the_identical_path() -> None:
    """THE CONTROL on the count above, and the reason it means anything.

    A harness that could never post would report zero posts on any corpus,
    including one made entirely of clean bills. This is real PDF bytes through
    the same `drive` - classify, read, decide - and it posts. So the zero above
    is a fact about the two hundred files, not about the harness.
    """
    run = drive(chaos.the_control_bill())

    assert run.crashed == ""
    assert run.decided is not None and run.decided.action is Action.POST
    assert run.record is not None and run.record.party == CONTROL_PARTY
    assert run.record.date == CONTROL_DATE
    assert run.record.total_paise == CONTROL_TOTAL


def test_the_control_bill_is_not_one_of_the_two_hundred() -> None:
    """A file that should post has no business in a corpus whose whole claim is
    that nothing in it posts. Kept out here so it cannot drift in."""
    assert chaos.the_control_bill().name not in {case.name for case in CASES}


def test_every_chaos_input_reaches_a_decision_rather_than_nothing() -> None:
    """A `None` decision would be counted as "not a post" by the test above
    while meaning the harness fell over before it got there."""
    undecided = [run.case.name for run in RUNS if run.decided is None]

    assert undecided == []


def test_not_one_chaos_input_produces_a_writable_entry() -> None:
    """`Decided.entry` is the postable thing. A blocked decision holding one is
    a careless attribute access away from writing what was just refused."""
    carrying = [
        run.case.name
        for run in RUNS
        if run.decided is not None and run.decided.entry is not None
    ]

    assert carrying == []


# ---- pass condition 3: every refusal carries a sentence ---------------------


def test_every_refused_file_carries_a_non_empty_plain_sentence() -> None:
    """PASS CONDITION 3, over every case the classifier refused.

    A dead end is a defect. Being stopped with no sentence leaves the person
    with a file, a rejection and no idea what would work instead.
    """
    silent = [
        run.case.name
        for run in REFUSED
        if run.seen is None or not is_a_plain_sentence(run.seen.reason)
    ]

    assert silent == []


def test_every_decision_on_a_chaos_input_carries_a_plain_sentence_too() -> None:
    """The refusal a person actually reads comes from the decision layer, not
    from the classifier. Both have to speak or the second one is a blank page."""
    silent = [
        run.case.name
        for run in RUNS
        if run.decided is not None and not is_a_plain_sentence(run.decided.said)
    ]

    assert silent == []


def test_the_control_an_empty_sentence_is_not_accepted_as_a_plain_one() -> None:
    """THE CONTROL. A checker that returned True for everything would pass both
    counts above while every refusal on screen was blank."""
    assert not is_a_plain_sentence("")
    assert not is_a_plain_sentence("   ")
    assert not is_a_plain_sentence("unsupported")


def test_the_control_a_leaked_traceback_is_not_accepted_as_a_plain_sentence() -> None:
    """THE SECOND CONTROL, on the half of the checker the empty string cannot
    reach. This is the shape a real leak takes: long enough, punctuated, and
    still a developer's output on a stranger's screen."""
    leaked = "Traceback (most recent call last): PdfStreamError at offset 12."

    assert not is_a_plain_sentence(leaked)


def test_a_refusal_never_forwards_a_control_character_out_of_the_file() -> None:
    """Files in this corpus carry ANSI escapes and carriage returns on purpose.
    A refusal that echoes one back is our sentence being rewritten by the
    upload it is refusing."""
    for run in RUNS:
        said = (run.seen.reason if run.seen is not None else "") + (
            run.decided.said if run.decided is not None else ""
        )
        bad = [c for c in said if unicodedata.category(c) == "Cc" and c != "\n"]
        assert bad == [], (run.case.name, bad)


# ---- what the classifier concluded ------------------------------------------


def test_every_chaos_input_gets_one_of_the_five_kinds_and_a_detected_label() -> None:
    """A blank `detected` cannot answer "what did the person actually send",
    which is the only question the audit line is there to answer."""
    for run in RUNS:
        assert run.seen is not None, run.case.name
        assert run.seen.kind in set(FileKind), run.case.name
        assert run.seen.detected, run.case.name


def test_a_reason_is_present_exactly_when_the_file_could_not_be_read() -> None:
    """Both directions. A `reason` that is always populated tells the reader
    nothing, and one that is sometimes missing on a refusal is a dead end."""
    for run in RUNS:
        assert run.seen is not None, run.case.name
        readable = run.seen.kind is not FileKind.UNSUPPORTED
        assert bool(run.seen.reason) is not readable, run.case.name


def test_every_single_byte_value_from_zero_to_two_hundred_and_fifty_five_is_safe() -> (
    None
):
    """The sweep the corpus cannot hold as 256 separate cases without becoming
    a corpus of one idea. Every byte value on its own, all the way through.

    IT ONLY CALLED `classify` UNTIL THE REVIEW PASS. A single byte that
    classifies as text and then makes the READER raise would have gone straight
    past it, which is the half of the promise this file exists for.
    """
    for value in range(256):
        run = drive(one_byte_case(value))
        assert run.seen is not None and run.seen.detected, value
        assert run.crashed == "", (value, run.crashed)
        assert run.decided is not None and run.decided.action is not Action.POST


def test_a_str_where_bytes_belong_raises_rather_than_being_classified() -> None:
    """A `str` has an encoding attached and the whole question is what the
    bytes are. Guessing one is how a rupee amount becomes a different number."""
    with pytest.raises(TypeError):
        classify("a string, not bytes")  # type: ignore[arg-type]


def test_a_bool_where_bytes_belong_is_refused_rather_than_classified() -> None:
    """`isinstance(True, int)` is True in Python and `True` is truthy, so a
    flag passed where a file belonged must not fall through to the magic-byte
    scan.

    IT RAISES `AttributeError`, NOT the written `TypeError` a `str` gets. That
    is a rough edge in `classify` and it is pinned here rather than smoothed
    over: this file may not edit that module, and a test that accepted only
    `TypeError` would fail today for a defect it did not find.
    """
    with pytest.raises((TypeError, AttributeError)):
        classify(True)  # type: ignore[arg-type]


# ---- the named inputs the owner asked about ---------------------------------


def named(name: str) -> Ran:
    """The one run with this name. Raises rather than returning a default,
    because a silently missing case makes the assertion below vacuous."""
    for run in RUNS:
        if run.case.name == name:
            return run
    raise LookupError(f"no chaos case named {name!r}")


def test_a_pdf_that_is_really_a_jpeg_is_read_as_a_jpeg() -> None:
    """The bytes are the fact and the extension is a claim. A phone that
    renames a file is a Tuesday, not an attack, so this has to WORK rather
    than merely fail safely."""
    run = named("a_pdf_that_is_really_a_jpeg")

    assert run.seen is not None
    assert run.seen.kind is FileKind.JPEG
    assert run.seen.declared_disagreed is True


def test_a_jpg_that_is_really_a_zip_is_refused_and_never_unzipped() -> None:
    """Refusing to extract from a zip removes the zip-bomb surface as a side
    effect of D-23, which excluded DOCX for a different reason entirely."""
    run = named("a_jpg_that_is_really_a_zip")

    assert run.seen is not None
    assert run.seen.kind is FileKind.UNSUPPORTED
    assert "zip" in run.seen.detected.lower()


def test_a_pdf_with_no_text_layer_is_refused_in_words_rather_than_guessed_at() -> None:
    """A scan has no text layer, and the tier that reads pixels is not wired.
    Inventing a figure here is the exact defect `TYPED_TEXT_MIME` records."""
    run = named("a_pdf_with_no_text_layer")

    assert run.record is not None
    assert run.record.total_paise is None
    assert "no text layer" in run.record.per_field_source["total_paise"]


def png_chunk_crcs(data: bytes) -> list[tuple[bytes, bool]]:
    """Every PNG chunk in `data`, and whether its checksum matches its bytes."""
    out: list[tuple[bytes, bool]] = []
    at = 8
    while at + 12 <= len(data):
        length = struct.unpack(">I", data[at : at + 4])[0]
        body = data[at + 4 : at + 8 + length]
        stated = data[at + 8 + length : at + 12 + length]
        if len(stated) < 4:
            break
        out.append((body[:4], struct.unpack(">I", stated)[0] == zlib.crc32(body)))
        at += 12 + length
    return out


def test_the_patched_pngs_still_carry_a_checksum_that_matches() -> None:
    """ONE DEFECT PER CASE, asserted rather than intended.

    "A PNG declaring a width of zero" that ALSO had a broken checksum could be
    refused for either reason, and the case would stop being able to say which
    of the two the input layer noticed.
    """
    patched = (
        "a_png_declaring_a_width_of_zero",
        "a_png_declaring_more_rows_than_it_carries",
        "a_png_carrying_invoice_text_in_a_text_chunk",
    )
    for name in patched:
        signed = png_chunk_crcs(named(name).case.data)
        assert signed, name
        assert all(ok for _, ok in signed), (name, signed)


def test_the_control_the_png_whose_crc_was_broken_on_purpose_does_not_match() -> None:
    """THE CONTROL on the sweep above. A checksum reader that always answered
    "matches" would pass it while the corpus signed nothing correctly."""
    signed = png_chunk_crcs(named("a_png_with_a_deliberately_wrong_crc").case.data)

    assert any(not ok for _, ok in signed), signed


def test_the_metadata_trap_really_carries_invoice_words_and_still_reads_none() -> None:
    """The image whose PIXELS say nothing and whose METADATA says `TOTAL
    4200.00`. If the words were not really in the file, the sweep below would
    pass because there was nothing there to find."""
    run = named("a_png_carrying_invoice_text_in_a_text_chunk")

    assert b"TOTAL 4200.00" in run.case.data
    assert run.record is not None and run.record.total_paise is None


def test_a_corrupted_image_is_still_a_png_and_still_posts_nothing() -> None:
    """Valid header, truncated body. The header is honest, so refusing it as
    "not a PNG" would be wrong; what must not happen is a figure coming out."""
    run = named("a_png_with_its_idat_cut_in_half")

    assert run.seen is not None and run.seen.kind is FileKind.PNG
    assert run.decided is not None and run.decided.action is not Action.POST


def test_no_image_in_the_corpus_ever_produces_an_amount() -> None:
    """The measured defect: `TypedTextExtractor` handed a PNG once returned
    `total_paise = 420000` sourced `typed_text` - an invented number wearing a
    real backend's name, which is worse than a blank."""
    images = [
        r
        for r in RUNS
        if r.seen is not None and r.seen.kind in (FileKind.PNG, FileKind.JPEG)
    ]
    amounts = [
        r.case.name
        for r in images
        if r.record is not None and r.record.total_paise is not None
    ]

    assert amounts == []
    assert len(images) >= 20


def test_the_control_the_same_harness_does_read_an_amount_out_of_typed_text() -> None:
    """THE CONTROL on the test above. A reader wired to return `None` always
    would pass it while reading nothing on any document ever."""
    record = TypedTextExtractor().extract(
        b"paid Sharma Traders 4200 for cement", "text/plain"
    )

    assert record.total_paise == 420_000


def test_the_mixed_script_bill_is_accepted_as_text_and_not_refused() -> None:
    """Indian bills carry Devanagari and Tamil beside Latin. Refusing them as
    "not text" is a defect that only appears on the customer's own documents."""
    run = named("a_mixed_script_bill")

    assert run.seen is not None and run.seen.kind is FileKind.TEXT
    assert run.decided is not None and run.decided.action is Action.BLOCK


# ---- the near misses, which are where the corpus actually bites -------------
# Everything else in the corpus blocks on an unread field, so its refusal is a
# fact about labels. These five are read to the end - supplier, date, items,
# tax and total - and are refused by the safety layer instead. Without them,
# "0 posts" would only prove that the reader could not read anything.


def test_the_near_miss_bills_are_read_deeply_enough_to_reach_the_safety_layer() -> None:
    """THE ANTI-VACUITY CHECK on the whole file.

    A corpus every case of which died at the classifier would report the same
    three zeros while never reaching conservation, the wall or a confidence
    band. Every one of these five has its supplier read off the document, and
    two of them are read completely - so the laws really do run.
    """
    records = {name: named(name).record for name in NEAR_MISSES}
    read_a_supplier = [n for n, rec in records.items() if rec is not None and rec.party]
    read_everything = [
        run.case.name
        for run in RUNS
        if run.record is not None
        and None not in (run.record.date, run.record.party)
        and None not in (run.record.total_paise, run.record.tax_paise)
    ]

    assert sorted(read_a_supplier) == sorted(NEAR_MISSES)
    assert len(read_everything) >= 2, read_everything


def test_a_bill_whose_line_items_do_not_sum_is_refused_and_not_posted() -> None:
    """Conservation is what stops this one - every field was read and the
    arithmetic is what disagrees. The sentence names both figures, because
    "the numbers do not add up" is not something a person can check.

    CORRECTED AND RENAMED 2026-08-13. It asserted `Action.ASK`, which is what
    `decision.py` did until the owner closed the question that morning:
    "Conservation FAIL -> BLOCK, always. This is now a hard rule." Nothing was
    posted before or after; the label and the sentence moved. The two figures
    are still asserted, and they matter more now than they did - a refusal a
    person cannot check against the bill leaves them nowhere, where a question
    at least invited a reply.
    """
    run = named("a_bill_whose_line_items_do_not_sum")

    assert run.decided is not None and run.decided.action is Action.BLOCK
    assert "₹2,000.00" in run.decided.said
    assert "₹4,200.00" in run.decided.said


def test_a_bill_stating_two_totals_leaves_the_total_unread_rather_than_choosing() -> (
    None
):
    """Picking one of two stated totals is a guess wearing a reading. The
    honest answer is that this bill does not say what it comes to."""
    run = named("a_bill_stating_two_different_totals")

    assert run.record is not None and run.record.total_paise is None
    assert run.decided is not None and run.decided.action is Action.BLOCK


def test_a_bill_with_a_negative_total_is_stopped_and_told_why() -> None:
    """A negative entry is a correction, and corrections are `reversal.py`'s
    job. The refusal names the amount so the person can see what was read."""
    run = named("a_bill_with_a_negative_total")

    assert run.record is not None and run.record.total_paise == -CONTROL_TOTAL
    assert run.decided is not None and run.decided.action is Action.BLOCK
    assert "₹-4,200.00" in run.decided.said


def test_a_bill_with_half_a_paisa_on_it_is_refused_rather_than_rounded() -> None:
    """`4200.005` became 1000 paise once and posted. Rounding invoice
    arithmetic is how a reconciliation breaks three months later."""
    run = named("a_bill_with_half_a_paisa_on_it")

    assert run.record is not None and run.record.total_paise is None
    assert run.decided is not None and run.decided.action is Action.BLOCK


def test_a_bill_whose_date_reads_two_ways_is_not_guessed_at() -> None:
    """03/04/2026 is the third of April in India and the fourth of March in the
    United States. A guess here puts a bill in the wrong tax period."""
    run = named("a_bill_whose_date_could_be_read_two_ways")

    assert run.record is not None and run.record.date is None
    assert run.record.total_paise == CONTROL_TOTAL


def test_the_file_holding_every_byte_value_is_refused_rather_than_read() -> None:
    """256 distinct bytes including NUL. Anything that called this text would
    be guessing at an encoding over binary."""
    run = named("every_byte_value_0_255")

    assert len(run.case.data) == 256
    assert run.seen is not None and run.seen.kind is FileKind.UNSUPPORTED


# =============================================================================
# REVIEW NOTES - written on a second reading, as somebody who did not write it
# =============================================================================
#
# FIVE THINGS WERE WRONG. All five are fixed above; this block says what they
# were, because a defect with no record of it comes back.
#
# 1. A NUMBER NOBODY SET WAS STANDING IN FOR ONE SOMEBODY DID.
#    `test_the_whole_corpus_is_small_enough_to_rebuild_on_every_run` asserted
#    `total_bytes < 4_000_000`. Four megabytes is a figure this file invented.
#    The constraint that actually exists is 300 seconds, so the test now
#    rebuilds and re-drives the whole corpus and asserts the seconds. Measured
#    about 2 of 300, which is a budget rather than a flake.
#
# 2. A CRASHED RUN COULD DISAPPEAR OUT OF THE REFUSAL SWEEP.
#    `Ran.seen` was typed `object` and read everywhere as
#    `getattr(run.seen, "reason", "")`. When `drive` caught an exception,
#    `seen` was `None`, the `getattr` default made the case look like a
#    readable file, and it silently left `REFUSED` instead of failing. It is
#    `Classified | None` now and every read is guarded, so a crash shows up as
#    a crash in both counts rather than as a shrinking denominator in one.
#
# 3. THE 256-VALUE SWEEP ONLY CALLED THE CLASSIFIER.
#    It asserted a kind and a label and stopped there. A single byte that
#    classifies as text and then makes the READER raise would have walked past
#    it - and "the reader does not raise" is half of what this file exists to
#    measure. It runs the identical `drive` now, and asserts no crash and no
#    post on all 256.
#
# 4. THE LEAK LIST DID NOT CONTAIN OUR OWN SENTINELS.
#    `DEVELOPER_WORDS` was `Traceback`, `Exception`, `errno`, `0x7f` - guesses
#    at what a foreign library might print. The strings that really leak here
#    are `not_found` and `None`: they are on every unread field's
#    `per_field_source`, one `f"{source}"` away from a person's screen. Both
#    are on the list now. Measured: zero hits across all 200, so nothing was
#    weakened to make room for them.
#
# 5. `re_crc` SIGNED THE WRONG BYTES, AND THE CORPUS DID NOT NOTICE.
#    Found by writing `test_the_patched_pngs_still_carry_a_checksum_that
#    _matches` rather than by reading the code. Two PNGs meant to carry ONE
#    defect each - a zero width, an impossible height - also carried a broken
#    `IHDR` checksum and four overwritten payload bytes, so a refusal could
#    have been blamed on either. Fixed in `build_chaos_corpus.re_crc`, which
#    now reads the chunk length out of the chunk instead of being told it.
#
# WHAT IS STILL WEAK, SAID RATHER THAN QUIETLY LEFT
# --------------------------------------------------
# `test_every_chaos_input_is_named_for_what_it_actually_is` CANNOT prove a name
# describes its bytes. It checks a length, an underscore and an alphabet, and a
# corpus named `aaaaaaaa_001` through `aaaaaaaa_200` would pass it. What holds
# that line is `REQUIRED_NAMES`, which is the owner's own list, plus a person
# reading `artifacts/chaos_corpus/manifest.json`. Naming this rather than
# inventing a similarity metric, because a score with no falsifier is an
# opinion wearing a number.
#
# `classify(True)` raises `AttributeError` where a `str` gets a written
# `TypeError`. That is a rough edge in `accountant/cage/classify.py`, this file
# may not edit that module, and
# `test_a_bool_where_bytes_belong_is_refused_rather_than_classified` pins the
# behaviour as it is rather than as it should be. Reported, not fixed.
#
# The OCR tier is not exercised. `freeocr.py` shells out to tesseract, and 200
# images through a subprocess would measure the machine rather than the code.
# Stated in the module docstring; it is a gap, not an oversight.
