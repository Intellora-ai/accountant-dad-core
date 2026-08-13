"""I54 — twenty inputs, every outcome stated first, and a plain sentence each.

    .venv/bin/python demo_safety_cage.py

THE ACCEPTANCE CRITERION THIS ANSWERS, VERBATIM
------------------------------------------------
    "I hand you 20 inputs - some good bills, some garbage, some duplicates, one
    with a 1-paisa error, one photo of a cat - and you watch the system post the
    good ones and refuse every bad one with a plain sentence for each."

Twenty inputs, each carrying the outcome it MUST produce and the guard that MUST
produce it, run end to end, printed as a table. The outcome is written down
before the run, never read off it. `tests/test_demo_i54.py` is the proof; this
file is the demonstration, and they are separate on purpose - a demo that judges
its own output is a slideshow.

WHO ACTUALLY DECIDES
---------------------
`accountant.cage.decision.decide` does, on every input that gets as far as
having something to decide about. This file does NOT re-implement it. What this
file owns is the part the decision layer has no opinion about, because it is
about bytes rather than about a bill:

    door        size, from the DECLARED length, before a byte is read
    classify    what the bytes are, never what the name claims they are
    reader      a readable kind we have no reader for is still a refusal
    field       a value that is present and cannot be true
    duplicate   this operation is already in the books

and then the sentence a person reads, which is the cage's own sentence plus the
one thing the cage does not say: what to do next.

WHERE THE CAGE AND THE OWNER'S CRITERION DISAGREE — FOUR ROWS, READ THIS
-------------------------------------------------------------------------
They disagree about one thing, and they disagree about it in both directions:

    the bill is WRONG        owner: refuse it        decision.py: ask about it
    the bill is INCOMPLETE   owner: ask about it     decision.py: refuse it

`decision.py` sends a failed conservation law to ASK ("the numbers do not add
up, so I will not post it without checking with you first") and sends a field it
could not read at all to BLOCK ("too little to even ask about"). The owner's
criterion groups the three arithmetic rows under *refuse* and the missing-field
row under *ask*.

Nothing is posted either way, so no books are at risk on this. What differs is
the label and therefore the sentence. **This demo follows the owner's criterion
on those four rows, records what `decision.decide` said about each of them
anyway, and prints the disagreement.** It is named in `DIVERGENCE`, asserted in
`tests/test_demo_i54.py`, and it is for the owner to settle - not for either
file to quietly win.

THREE OUTCOMES, NOT TWO
------------------------
    POST    every guard cleared. This reaches the books.
    BLOCK   something is WRONG. Refused, with the reason in words.
    ASK     something is MISSING or unsure. Not wrong - unknown. A question.

THE NUMBERS HERE, AND WHERE EACH ONE CAME FROM
------------------------------------------------
    0.95 / 0.70   the confidence bands. Owner-set, read from
                  `decision.AUTO_POST_FLOOR` and `decision.ASK_FLOOR`.
    5             the question cap, `questions.QUESTION_CAP`. Owner-set.
    100 MB        the upload cap, read from `accountant.web.app`. This demo
                  reported the gap rather than papering over it: the owner's
                  closed decision said 100 MB and the shipped constant said
                  10 MiB, a tenth of it. The constant was corrected on
                  2026-08-13 and this line still reads it from the product, so
                  the demo cannot drift from the door it is demonstrating.
    01-04-2026    the date this company's books open. SCENARIO DATA, not a
                  product threshold: nothing in this repository implements a
                  closed period, so the demo states the boundary the way it
                  states the party names and the amounts. Reported as a gap.

WHAT THIS DEMO DOES NOT SHOW
-----------------------------
That anything was READ off a document. No extraction backend is selected
(`D-23` open), so every photo and PDF here is refused for exactly that reason -
including the cat, which is refused because photos cannot be read at all and NOT
because anything recognised a cat. The row says so.

That the confidences are measured. Nothing shipped produces a 0.72 today. Row 19
states one so the bands can be demonstrated; it is scenario data.

That the fourth conservation law caught anything before the write. Pre-write the
balance after is a PREDICTION, so the law is vacuous by construction there. It
is asked again after the write against the register that was actually read back,
and that one can fail.

NO NETWORK, NO REAL TALLY. `FakeTally`, in process.
"""

from __future__ import annotations

import datetime
import pathlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from accountant.cage import conservation
from accountant.cage.classify import SUPPORTED_SENTENCE, Classified, FileKind, classify
from accountant.cage.confidence import (
    EXACT,
    field_confidence,
    looks_like_a_date,
    looks_like_paise,
)
from accountant.cage.decision import (
    ASK_FLOOR,
    AUTO_POST_FLOOR,
    Action,
    Decided,
    Moment,
    Situation,
    decide,
)
from accountant.cage.wall import Field, LedgerEntry, Observation
from accountant.questions import (
    QUESTION_CAP,
    RETYPE,
    YES,
    Answer,
    Question,
    how_much,
    rupees,
    who_was_it,
)
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally
from accountant.tax.decision import POSTING_ENABLED
from accountant.web.app import MAX_UPLOAD_BYTES

REPO = pathlib.Path(__file__).resolve().parent

#: The upload limit, taken from the shipped constant rather than restated here.
CAP_BYTES = MAX_UPLOAD_BYTES

# ---- the company this demo runs against -------------------------------------

COMPANY = "Demo Traders"
EXPENSE = "Purchases"
FUNDING = "Cash"
CHART = (EXPENSE, FUNDING, "Bank", "Printing & Stationery")

PARTY_ONE = "Sharma Stationers"
PARTY_TWO = "Verma Press"
KNOWN_PARTIES = (PARTY_ONE, PARTY_TWO)

#: SCENARIO DATA. No closed-period gate exists in this repository; see the
#: module docstring. The demo states the boundary so the case can be shown.
BOOKS_OPEN_FROM = datetime.date(2026, 4, 1)

#: The four rows where `decision.decide` labels the outcome differently from the
#: owner's acceptance criterion, and the direction of each. Written down so the
#: disagreement is a fact in the code rather than a paragraph in a report, and
#: so a test fails the day either side changes without the other.
DIVERGENCE: dict[int, str] = {
    5: "cage asks about a failed law; the owner refuses a wrong bill",
    6: "cage asks about a failed law; the owner refuses a wrong bill",
    7: "cage asks about a failed law; the owner refuses a wrong bill",
    17: "cage has no duplicate guard at all; the register read is what stopped it",
    20: "cage refuses a field it could not read; the owner asks for it",
}


class Outcome(StrEnum):
    """What happened to one input."""

    POST = "POST"
    BLOCK = "BLOCK"
    ASK = "ASK"


#: `Action` is the cage's vocabulary and `Outcome` is this demo's. One mapping,
#: here, so a reader can see they are the same three things under two names.
AS_OUTCOME: dict[Action, Outcome] = {
    Action.POST: Outcome.POST,
    Action.ASK: Outcome.ASK,
    Action.BLOCK: Outcome.BLOCK,
}

#: What to do next, per guard. THE CAGE SAYS WHAT HAPPENED; THIS SAYS WHAT TO DO.
#: The split is deliberate: `decision.py` refuses in one sentence and several of
#: those sentences stop at the refusal - "GST posting is switched off, so this
#: cannot be posted automatically" leaves a person holding a bill and no next
#: move. A dead end is a defect, so the demo, which is the surface a person
#: touches here, supplies the move. Reported to the owner as a gap in the cage's
#: own wording rather than patched into `decision.py` by this file.
#:
#: THE TAX ROW IS NOW EMPTY, AS OF 2026-08-13. That gap was reported and it has
#: been closed at the source: `decision.GST_IS_OFF` now ends with "Please enter
#: this one in Tally yourself", the same words `checks.py::tax_lines_can_be_posted`
#: already used. Leaving this entry filled printed the same instruction twice in
#: a row at the person, which is its own defect. An empty string here is the
#: demo saying the cage already answered, not the demo giving up.
NEXT_STEP: dict[str, str] = {
    "arithmetic": "Check that against the bill and send it again.",
    "tax": "",
    "party": "Add them in Tally, or tell me which name you already have.",
    "period": "Ask your accountant to open that period, or check the date.",
    "confidence": "",
}


# ---- the body: bytes that can say whether anybody read them -----------------


class Body:
    """A file's bytes, and a counter of how many of them were actually read.

    The counter is the whole point of the oversize row. "Refused before it was
    read" is a claim about ORDER, and order is invisible in an outcome column: a
    door that read ten megabytes and then refused looks identical from outside.
    So the bytes sit behind a method that counts.

    A body may be declared without being materialised. Row 18 is genuinely over
    the cap and genuinely never allocated - `read()` would build it, and nothing
    calls `read()`, which is exactly what that row demonstrates.
    """

    def __init__(self, data: bytes = b"", declared: int | None = None) -> None:
        self._data = data
        self._declared = len(data) if declared is None else declared
        self.bytes_read = 0
        self.was_read = False

    @property
    def declared_length(self) -> int:
        """How big the sender says this is. Checked before anything is read."""
        return self._declared

    def read(self) -> bytes:
        self.was_read = True
        data = (
            self._data if len(self._data) == self._declared else b"x" * self._declared
        )
        self.bytes_read += len(data)
        return data


# ---- one input, and one result ----------------------------------------------


@dataclass(frozen=True)
class Input:
    """One thing handed to the system, and what must happen to it.

    `expected` and `expected_stage` are both written down BEFORE the run. The
    second is what stops a row passing for the wrong reason: an input refused by
    a guard other than the one it was written for still lands in the BLOCK
    column, and without the stage nobody would ever see it.
    """

    number: int
    name: str
    expected: Outcome
    expected_stage: str
    body: Body
    declared_mime: str = ""
    operation_id: str = ""
    #: What the reader reported per word for a named field, when the scenario
    #: says the read was not clean. Scenario data - see the module docstring.
    word_confidences: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass
class Result:
    """What happened to one input, and the sentence the person reads."""

    number: int
    name: str
    expected: Outcome
    expected_stage: str
    actual: Outcome
    stage: str
    sentence: str
    status: int | None = None
    detected: str = ""
    declared_disagreed: bool = False
    amount_paise: int = 0
    reference: str = ""
    bytes_read: int = 0
    was_read: bool = False
    entry: LedgerEntry | None = None
    #: What `decision.decide` said, on every row that got as far as a bill. None
    #: on the rows refused over bytes, where there is nothing to decide about.
    cage_action: Action | None = None
    questions: tuple[str, ...] = field(default_factory=tuple[str, ...])

    @property
    def agreed(self) -> bool:
        return self.actual is self.expected

    @property
    def by_design(self) -> bool:
        return self.agreed and self.stage == self.expected_stage

    @property
    def diverged(self) -> bool:
        """The cage labelled this differently from the owner's criterion."""
        return self.cage_action is not None and AS_OUTCOME[self.cage_action] is not (
            self.actual
        )


# ---- the reader: typed text, and an honest refusal for everything else ------

#: The one thing this product can read today. Everything else classifies fine
#: and is refused by the reader, which is a different fact and a different
#: sentence - `classify.py` draws the same boundary in the same place.
READABLE_BY_US = FileKind.TEXT

NO_READER = (
    "no reader for {what} is set up yet, so nothing on it was read. "
    "Type the bill in and it will go through."
)

FIELDS = ("date", "party", "net", "tax", "total", "paid", "lines")
MONEY = ("net", "tax", "total", "paid")


def parse(text: str) -> dict[str, str]:
    """`key: value` lines into a dictionary. A key that is absent stays absent.

    Deliberately no defaults. A missing `tax:` line becoming `0` is the most
    expensive coercion in this system - it posts a GST bill without its input
    credit, real money lost, nothing on screen to notice. Absent is absent, and
    absent becomes a question.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in FIELDS:
            found[key.strip()] = value.strip()
    return found


def format_valid(name: str, raw: str) -> bool:
    """Is this string the SHAPE the field has to be? Not "can it be coerced"."""
    if name == "date":
        return looks_like_a_date(raw)
    if name == "party":
        return bool(raw.strip())
    if name == "lines":
        return all(looks_like_paise(part.strip()) for part in raw.split(",") if part)
    return looks_like_paise(raw)


def confidence_of(name: str, stated: dict[str, tuple[int, ...]]) -> float:
    """How sure we are about one field we did read.

    Typed text is `EXACT` - a text layer was read, not guessed, so there is
    nothing to be unsure about. A scenario that states word confidences goes
    through `field_confidence` instead, which is the real formula.
    """
    words = stated.get(name)
    if words is None:
        return EXACT
    return field_confidence(words, format_valid=True, consistent=True)


# ---- the questions, for the things that are missing rather than wrong -------


def question_for(name: str, seen: dict[str, str], score: float) -> Question:
    """One question about one uncertain field.

    Below `ASK_FLOOR` the value is not worth confirming, so the question asks
    for it again. Between the bands it IS worth confirming, so the question
    shows what was read and asks whether that is right. Both bands are owner-set
    and read from `decision.py` rather than written again here.
    """
    if score < ASK_FLOOR:
        return who_was_it() if name == "party" else how_much(seen.get("party", ""))
    return Question(
        problem_id="magnitude",
        text=(
            f"That reads as {rupees(int(seen[name]))} and I am not sure I read "
            "it right. Is that the amount?"
        ),
        answers=(
            Answer(label="Yes, that's right", value=YES),
            Answer(label="No, let me type it again", value=RETYPE),
        ),
    )


def said(question: Question) -> str:
    """A question and the ways out of it, in one line a person can read."""
    options = " / ".join(answer.label for answer in question.answers)
    return f"{question.text} ({options})"


# ---- the guards this file owns, in order ------------------------------------


def result_for(
    row: Input, outcome: Outcome, stage: str, sentence: str, **extra: object
) -> Result:
    return Result(
        number=row.number,
        name=row.name,
        expected=row.expected,
        expected_stage=row.expected_stage,
        actual=outcome,
        stage=stage,
        sentence=sentence,
        bytes_read=row.body.bytes_read,
        was_read=row.body.was_read,
        **extra,  # pyright: ignore[reportArgumentType]
    )


def refuse(row: Input, stage: str, sentence: str, **extra: object) -> Result:
    return result_for(row, Outcome.BLOCK, stage, sentence, **extra)


def at_the_door(row: Input) -> Result | None:
    """Size, from the declared length, before a byte is read into memory.

    Reading first and checking afterwards is a check made after the damage: the
    read allocates whatever the sender said, so one request takes the process
    down without needing a credential. This is the only point at which a size
    limit prevents anything.
    """
    if row.body.declared_length <= CAP_BYTES:
        return None
    return refuse(
        row,
        "door",
        f"That file is larger than the {CAP_BYTES // (1024 * 1024)} MB this "
        "accepts, so it was refused before any of it was read. Send a smaller "
        "photo of the bill, or type it in.",
        status=413,
    )


def at_the_reader(row: Input, what: Classified) -> Result | None:
    """Two different refusals, because they are two different facts.

    "We do not know what this is" and "we know exactly what this is and have no
    reader for it" send a person to two different places, and lumping them
    together sends both of them nowhere.
    """
    if what.kind is FileKind.UNSUPPORTED:
        onward = (
            " Send the file again, or type the bill in."
            if what.detected == "an empty file"
            else " Send it as a PDF or a photo, or type the bill in."
        )
        return refuse(
            row,
            "classify",
            (what.reason or SUPPORTED_SENTENCE) + onward,
            detected=what.detected,
            declared_disagreed=what.declared_disagreed,
        )
    if what.kind is not READABLE_BY_US:
        mislabelled = (
            "The name on this says one thing and the contents say another, and "
            "we go by the contents. "
            if what.declared_disagreed
            else ""
        )
        return refuse(
            row,
            "reader",
            mislabelled
            + f"This is {what.detected} and "
            + NO_READER.format(what=what.detected),
            detected=what.detected,
            declared_disagreed=what.declared_disagreed,
        )
    return None


def at_the_fields(row: Input, seen: dict[str, str]) -> Result | None:
    """A value that is present and cannot be true. Wrong, so a refusal.

    Not a low score. `field_confidence` scores an unparseable date 0.0, and 0.0
    would land this in the "could not read it" band, where a person is either
    refused with the wrong reason or asked to confirm something that cannot
    exist. It is also what stops `int("1200.50")` reaching the arithmetic as a
    traceback.
    """
    for name in ("date", *MONEY, "lines"):
        raw = seen.get(name)
        if raw is None or format_valid(name, raw):
            continue
        if name == "date":
            return refuse(
                row,
                "field",
                f"The date on this bill reads {raw}, and there is no such day "
                "on any calendar. Type the date again.",
            )
        return refuse(
            row,
            "field",
            f"The {name} on this bill reads {raw}, and amounts here are whole "
            "paise with no decimal point. Type the amount again in paise.",
        )
    return None


def at_the_duplicate(row: Input, client: FakeTally) -> Result | None:
    """A read, and it FAILS CLOSED. Not knowing whether this was already posted
    is not the same as knowing it was not, and posting on that uncertainty is
    how two identical vouchers reach a live company."""
    if client.read_by_operation_id(COMPANY, row.operation_id) is None:
        return None
    return refuse(
        row,
        "duplicate",
        "This bill is already in your books - it was sent twice and only the "
        "first one counted. Nothing was added a second time. Check the register "
        "if you want to see it.",
    )


# ---- what the cage is given, and what it says -------------------------------


def observed(seen: dict[str, str], scores: dict[str, float]) -> Observation:
    """What we think the bill says, with a score on every field.

    The values are the PARSED ones, not the strings: `decide` refuses an amount
    that is not an `int` with its own sentence, and handing it `"120000"` would
    make it refuse every bill in the demo for a reason that is about this
    function rather than about the bill.
    """

    def one(name: str, value: object) -> Field:
        score = scores[name] if name in seen else 0.0
        return Field(
            value=value if name in seen else None, confidence=score, source="typed text"
        )

    return Observation(
        date=one("date", datetime.date.fromisoformat(seen.get("date", "2026-01-01"))),
        party=one("party", seen.get("party")),
        total_paise=one("total", int(seen.get("total", 0))),
        tax_paise=one("tax", int(seen.get("tax", 0))),
        line_paise=tuple(int(p) for p in seen.get("lines", "").split(",") if p.strip()),
    )


def total_checked(seen: dict[str, str]) -> int | None:
    """The one figure the laws below are run on, and the one `decide` is told.

    Written once and called twice on purpose. `decide` refuses to write an
    amount its checks did not see, so `judge` has to hand it the number
    `laws_for` used - and two separate expressions computing "the total" would
    be exactly the gap that check exists to close.

    `None` when the bill has no total on it. That is the honest answer, and it
    blocks: laws run on nothing have checked nothing.
    """
    return int(seen["total"]) if "total" in seen else None


def laws_for(
    seen: dict[str, str], before: int
) -> tuple[conservation.ConservationResult, ...]:
    """All four, in `LAWS` order, because `decide` refuses anything else.

    The fourth is asked against a PREDICTED balance and is therefore vacuous
    here: a caller who states what the books will look like after its own entry
    cannot fail its own statement. It is asked again in `post`, against the
    register read back after the write, and that one can fail. Saying which is
    which is the only honest way to have both.
    """
    money = {name: int(seen[name]) for name in MONEY if name in seen}
    lines = tuple(int(p) for p in seen.get("lines", "").split(",") if p.strip())
    total = total_checked(seen)
    return conservation.run(
        debit_paise=total,
        credit_paise=money.get("paid"),
        line_paise=lines,
        total_paise=total,
        net_paise=money.get("net"),
        tax_paise=money.get("tax"),
        gross_paise=total,
        balance_before_paise=before,
        balance_after_paise=None if total is None else before + total,
    )


def stage_for(
    seen: dict[str, str],
    laws: tuple[conservation.ConservationResult, ...],
    watched: Observation,
) -> str:
    """Which guard the cage stopped on. A LABEL for the table, not a decision.

    The outcome is `decide`'s and only `decide`'s. This names it, so the table
    can say WHICH rule fired - a thing the `Decided` type does not carry and a
    thing an acceptance demo has to show.

    THE ORDER IS `decide`'S, NOT A CONVENIENT ONE. Everything `_blocking` looks
    at comes before everything `_asking` looks at, so tax, period and party are
    ahead of a failed law even though a failed law feels more serious. Ordering
    it the other way round was the first version and it lied on exactly one row:
    a bill carrying tax AND a broken sum was labelled `arithmetic` while the
    cage had refused it, correctly, for the tax. A label that disagrees with the
    reason is the defect the stage column exists to catch, so it may not be one.
    """
    if int(seen.get("tax", 0)) != 0:
        return "tax"
    if datetime.date.fromisoformat(seen["date"]) < BOOKS_OPEN_FROM:
        return "period"
    if seen.get("party", "") not in KNOWN_PARTIES:
        return "party"
    if any(law.verdict is conservation.Verdict.FAIL for law in laws):
        return "arithmetic"
    if watched.lowest_confidence < AUTO_POST_FLOOR:
        return "confidence"
    return "posted"


def questions_about(seen: dict[str, str], scores: dict[str, float]) -> tuple[str, ...]:
    """One question per field we are not sure enough about, capped at five."""
    unsure = [n for n in FIELDS if n not in seen or scores[n] < AUTO_POST_FLOOR]
    return tuple(
        said(question_for(n, seen, scores.get(n, 0.0) if n in seen else 0.0))
        for n in unsure[:QUESTION_CAP]
    )


# ---- one input, all the way through -----------------------------------------


def judge(row: Input, client: FakeTally) -> Result:
    """The guards this file owns, then the cage, then the write."""
    if refused := at_the_door(row):
        return refused

    data = row.body.read()
    what = classify(data, row.declared_mime)
    if refused := at_the_reader(row, what):
        return refused

    seen = parse(data.decode("utf-8"))
    if refused := at_the_fields(row, seen):
        return refused

    stated = dict(row.word_confidences)
    scores = {name: confidence_of(name, stated) for name in seen}
    watched = observed(seen, scores)
    before = client.trial_balance(COMPANY).get(EXPENSE, 0)
    laws = laws_for(seen, before)
    decided = decide(
        Situation(
            observation=watched,
            conservation=laws,
            # The same figure `laws_for` was run on, from the same function
            # rather than read off `watched` a second time. `decide` compares
            # this against the amount it would write, and a second read of the
            # observation would make it compare a number against itself.
            checked_paise=total_checked(seen),
            party_known=seen.get("party", "") in KNOWN_PARTIES,
            period_open=datetime.date.fromisoformat(seen["date"]) >= BOOKS_OPEN_FROM,
            carries_gst=int(seen.get("tax", 0)) != 0,
            questions_asked=0,
            debit_account=EXPENSE,
            credit_account=FUNDING,
            # Nothing has been written yet. Stated, never inferred - `decide`
            # has no default for this and refuses to guess.
            moment=Moment.BEFORE_THE_WRITE,
        )
    )
    return settle(row, client, seen, scores, laws, watched, decided, before)


def settle(
    row: Input,
    client: FakeTally,
    seen: dict[str, str],
    scores: dict[str, float],
    laws: tuple[conservation.ConservationResult, ...],
    watched: Observation,
    decided: Decided,
    before: int,
) -> Result:
    """Turn what the cage said into what this demo shows, and post if it posts.

    The two overrides are the whole of `DIVERGENCE` and they are here, in one
    place, named, rather than smeared through the guards. Neither of them
    weakens anything: an override that turns ASK into BLOCK refuses more, and an
    override that turns BLOCK into ASK still posts nothing.
    """
    stage = stage_for(seen, laws, watched)
    outcome = AS_OUTCOME[decided.action]
    asked = questions_about(seen, scores)
    extra: dict[str, object] = {"cage_action": decided.action}

    if [n for n in FIELDS if n not in seen] and len(seen) > 1:
        # DIVERGENCE. Absent is not wrong - it is unknown, and unknown is a
        # question. The cage calls it "too little to even ask about".
        return result_for(
            row, Outcome.ASK, "confidence", asked[0], questions=asked, **extra
        )

    if stage == "arithmetic":
        # DIVERGENCE, the other way. The cage asks; the owner refuses a bill
        # whose own numbers contradict each other.
        return refuse(row, stage, f"{decided.said} {NEXT_STEP[stage]}", **extra)

    if outcome is not Outcome.POST:
        # On an ASK the question IS the next step, and the cage does not build
        # one - `_NOT_SURE_ENOUGH` says it needs to check with you and stops
        # there. So the question goes in the same sentence rather than into a
        # column nobody reads.
        onward = (
            asked[0] if outcome is Outcome.ASK and asked else NEXT_STEP.get(stage, "")
        )
        return result_for(
            row,
            outcome,
            stage,
            f"{decided.said} {onward}".strip(),
            questions=asked if outcome is Outcome.ASK else (),
            **extra,
        )

    if refused := at_the_duplicate(row, client):
        refused.cage_action = decided.action
        return refused
    return post(row, client, seen, decided, before, extra)


def post(
    row: Input,
    client: FakeTally,
    seen: dict[str, str],
    decided: Decided,
    before: int,
    extra: dict[str, object],
) -> Result:
    """Write it, then ask the fourth law whether the books moved by exactly it.

    Asked AFTER the write and against the register, because that is the only
    moment it can be answered and the only failure it can catch: a write the far
    side accepted and then applied differently from the request.
    """
    entry = decided.entry
    if entry is None:  # pragma: no cover - `Decided` refuses a POST without one
        return refuse(row, "posted", "The cage cleared this and produced nothing.")
    voucher = Voucher(
        id=f"demo-{row.number:02d}",
        date=datetime.date.fromisoformat(seen["date"]),
        party=entry.party,
        narration=f"demo input {row.number}",
        debit_account=entry.debit_account,
        credit_account=entry.credit_account,
        amount_paise=entry.amount_paise,
    )
    written = client.write_voucher(COMPANY, voucher, row.operation_id)
    after = client.trial_balance(COMPANY).get(EXPENSE, 0)

    moved = conservation.balance_delta_equals_entry(before, after, entry.amount_paise)
    if moved.verdict is not conservation.Verdict.PASS:
        return refuse(
            row, "posted", f"{moved.said} Check the register in Tally.", **extra
        )
    return result_for(
        row,
        Outcome.POST,
        "posted",
        f"{decided.said} Put {rupees(entry.amount_paise)} from {entry.party} "
        f"into your books as {written.tally_id}.",
        amount_paise=entry.amount_paise,
        reference=written.tally_id,
        entry=entry,
        **extra,
    )


# ---- the twenty -------------------------------------------------------------


def bill(
    *,
    date: str = "2026-08-05",
    party: str | None = PARTY_ONE,
    net: str = "120000",
    tax: str = "0",
    total: str = "120000",
    paid: str = "120000",
    lines: str = "45000, 75000",
) -> Body:
    """One typed bill. Every key is present unless a row deliberately drops it."""
    rows = [
        f"date: {date}",
        *([] if party is None else [f"party: {party}"]),
        f"net: {net}",
        f"tax: {tax}",
        f"total: {total}",
        f"paid: {paid}",
        f"lines: {lines}",
    ]
    return Body("\n".join(rows).encode("utf-8"))


#: A one-pixel greyscale PNG. Real magic bytes, a real IHDR, and no cat in it -
#: nothing here reads the picture, which is the point row 12 makes.
CAT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02"
    b"\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)

#: JFIF magic. The row that carries it calls itself a PDF.
A_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"

#: The local file header every DOCX, XLSX and ODT starts with.
A_DOCX = b"PK\x03\x04\x14\x00\x06\x00word/document.xml"

#: No magic, not UTF-8, and a NUL byte - which is the reliable tell that bytes
#: are binary even when they happen to decode.
RANDOM_BYTES = b"\xde\xad\x00\xbe\xef\xfe\xff\x11\x7f\x80\x91\xa2\xb3\xc4"


def the_twenty() -> tuple[Input, ...]:
    """The twenty, rebuilt fresh every run so no counter carries over."""
    return (
        Input(
            1, "clean typed bill", Outcome.POST, "posted", bill(), operation_id="op-01"
        ),
        Input(
            2,
            "clean bill, another party and amount",
            Outcome.POST,
            "posted",
            bill(
                party=PARTY_TWO,
                net="250000",
                total="250000",
                paid="250000",
                lines="100000, 150000",
            ),
            operation_id="op-02",
        ),
        Input(
            3,
            "three lines summing exactly to an awkward total",
            Outcome.POST,
            "posted",
            bill(net="99999", total="99999", paid="99999", lines="33333, 33333, 33333"),
            operation_id="op-03",
        ),
        Input(
            4,
            "a bill carrying GST",
            Outcome.BLOCK,
            "tax",
            bill(
                party=PARTY_TWO,
                net="100000",
                tax="18000",
                total="118000",
                paid="118000",
                lines="118000",
            ),
            operation_id="op-04",
        ),
        Input(
            5,
            "line items out by one paisa",
            Outcome.BLOCK,
            "arithmetic",
            bill(lines="45000, 74999"),
            operation_id="op-05",
        ),
        Input(
            6,
            "net plus tax does not equal the total",
            Outcome.BLOCK,
            "arithmetic",
            # Tax is ZERO on purpose. A non-zero tax makes this a GST bill, and
            # the cage refuses a GST bill for the tax before it ever weighs the
            # sum - so the row would have demonstrated the tax rule twice and
            # `net + tax != gross` not at all. The disagreement is between the
            # stated net and the stated total, which is what the law compares.
            bill(net="100000", total="118000", paid="118000", lines="118000"),
            operation_id="op-06",
        ),
        Input(
            7,
            "what was paid does not equal what was billed",
            Outcome.BLOCK,
            "arithmetic",
            bill(paid="119999"),
            operation_id="op-07",
        ),
        Input(
            8,
            "a party nobody has seen before",
            Outcome.BLOCK,
            "party",
            bill(party="Kumar & Sons"),
            operation_id="op-08",
        ),
        Input(
            9,
            "a date in a closed period",
            Outcome.BLOCK,
            "period",
            bill(date="2026-03-15"),
            operation_id="op-09",
        ),
        Input(
            10,
            "a date that cannot exist",
            Outcome.BLOCK,
            "field",
            bill(date="2026-08-34"),
            operation_id="op-10",
        ),
        Input(
            11,
            "an amount that is not whole paise",
            Outcome.BLOCK,
            "field",
            bill(total="1200.50"),
            operation_id="op-11",
        ),
        Input(
            12,
            "a photo of a cat",
            Outcome.BLOCK,
            "reader",
            Body(CAT_PNG),
            declared_mime="image/png",
            operation_id="op-12",
        ),
        Input(
            13,
            "a Word document",
            Outcome.BLOCK,
            "classify",
            Body(A_DOCX),
            operation_id="op-13",
        ),
        Input(
            14,
            "an empty file",
            Outcome.BLOCK,
            "classify",
            Body(b""),
            operation_id="op-14",
        ),
        Input(
            15,
            "random bytes",
            Outcome.BLOCK,
            "classify",
            Body(RANDOM_BYTES),
            operation_id="op-15",
        ),
        Input(
            16,
            "a .pdf that is really a photo",
            Outcome.BLOCK,
            "reader",
            Body(A_JPEG),
            declared_mime="application/pdf",
            operation_id="op-16",
        ),
        Input(
            17,
            "the same bill as input 1, sent twice",
            Outcome.BLOCK,
            "duplicate",
            bill(),
            operation_id="op-01",
        ),
        Input(
            18,
            "a file over the upload cap",
            Outcome.BLOCK,
            "door",
            Body(b"", declared=CAP_BYTES + 1),
            operation_id="op-18",
        ),
        Input(
            19,
            "a bill with one field read unclearly",
            Outcome.ASK,
            "confidence",
            bill(),
            operation_id="op-19",
            word_confidences=(("total", (72,)),),
        ),
        Input(
            20,
            "a bill with no party on it at all",
            Outcome.ASK,
            "confidence",
            bill(party=None),
            operation_id="op-20",
        ),
    )


def run_demo(client: FakeTally | None = None) -> tuple[Result, ...]:
    """All twenty, in order, against one company that starts empty."""
    tally = FakeTally() if client is None else client
    tally.add_company(COMPANY, accounts=CHART, backed_up=True)
    return tuple(judge(row, tally) for row in the_twenty())


# ---- the table --------------------------------------------------------------


def wrap(text: str, width: int) -> list[str]:
    """Break a sentence at spaces. No `textwrap` import for eight lines of it,
    and this one never splits a word, which matters for amounts."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    return [*lines, current]


def format_table(results: Sequence[Result]) -> str:
    """Input, outcome, and the sentence. The third column is the demonstration -
    the first two without it are the thing this product exists to replace."""
    out = [
        f"{'#':>2}  {'INPUT':<38}  {'WANTED':<6}  {'GOT':<6}  {'GUARD':<10}  OK",
        "-" * 96,
    ]
    for r in results:
        out.append(
            f"{r.number:>2}  {r.name[:38]:<38}  {r.expected:<6}  {r.actual:<6}  "
            f"{r.stage:<10}  {'ok' if r.by_design else 'WRONG'}"
        )
        out.extend("      " + line for line in wrap(r.sentence, 84))
        out.append("")
    return "\n".join(out)


def divergence_report(results: Sequence[Result]) -> str:
    """The four rows where the cage and the owner's criterion disagree.

    Printed every run, in the demo the owner watches, because a disagreement
    about what "refuse" means is exactly the thing that must not live only in a
    comment.
    """
    lines = ["WHERE accountant.cage.decision AND THE OWNER'S CRITERION DISAGREE:"]
    for r in results:
        if r.diverged and r.cage_action is not None:
            lines.append(
                f"  row {r.number:>2}  owner says {r.actual:<5} "
                f"cage says {AS_OUTCOME[r.cage_action]:<5} - {DIVERGENCE[r.number]}"
            )
    lines.append("  Nothing is posted either way. The label and the sentence differ.")
    return "\n".join(lines)


def summary(results: Sequence[Result]) -> str:
    counted = {o: sum(1 for r in results if r.actual is o) for o in Outcome}
    wrong = [r.number for r in results if not r.by_design]
    return "\n".join(
        [
            f"posted {counted[Outcome.POST]}   refused {counted[Outcome.BLOCK]}   "
            f"asked {counted[Outcome.ASK]}   of {len(results)}",
            f"upload cap {CAP_BYTES // (1024 * 1024)} MB, read from the shipped "
            f"constant; automatic GST posting enabled: {POSTING_ENABLED}",
            "every outcome and every guard matched what was written down first."
            if not wrong
            else f"ROWS THAT DID NOT MATCH: {wrong}",
        ]
    )


def main() -> int:
    tally = FakeTally()
    results = run_demo(tally)
    print(format_table(results))
    print(divergence_report(results))
    print()
    print(summary(results))
    print(f"\nvouchers in {COMPANY}: {len(tally.read_vouchers(COMPANY))}")
    print(f"trial balance: {tally.trial_balance(COMPANY)}")
    return 0 if all(r.by_design for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
