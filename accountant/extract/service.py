"""The second backend: an adapter over somebody else's reader.

WE WRITE AN ADAPTER, NEVER A READER. Reading a bill is a commodity —
TallyPrime 7.1 ships it, myBillBook gives it away, others sell it from free to
about Rs 599 a month. Choosing the right ACCOUNT is the unsolved part. So this
module contains no OCR, no image work, no layout work and no field detection.
Somebody else reads the document; this file turns their answer into an
`ExtractedRecord`, or says why it could not.

THE TRANSPORT IS INJECTED, AND IT IS THE ONLY THING THAT TALKS TO ANYONE
------------------------------------------------------------------------
`ServiceCall` is `(bytes, mime, document_key) -> answer`. This module never
opens a socket, so there is no network code to review here and no service to
stand up before the pipeline can be tested end to end. A deployment supplies
the transport; the tests supply a function that returns a dict.

THE ONE RULE THAT DECIDES EVERY CASE BELOW
------------------------------------------
**An answer we cannot fully account for is not a partial answer. It is a failed
one.** The four named fields must all be spoken about. A field the service says
it could not find comes back `null`, which is an answer and becomes an explicit
`not_found`. A field the service does not mention AT ALL is a different thing:
we cannot tell "it is not on the bill" from "the service stopped halfway", and
those two mean opposite things to the person looking at the screen. Collapsing
them is how half a bill becomes a whole voucher.

So the shape is checked as a conservation law — the set of fields we asked
about must equal the set the answer accounts for — and a shortfall refuses the
whole response rather than trusting the half that arrived. Same for a value of
the wrong type: a total that arrives as the string "4200.00" is the service
breaking its contract, and quietly converting it is how a rounded rupee reaches
somebody's books.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any reader service exists, is accurate, or is worth paying for. No
transport ships with this module. This proves only that when one is chosen, the
reading happens on its side of the boundary and every way it can fail lands the
person in the same safe place: every field `not_found`, the reason stated, no
automatic post, and the entry still typeable by hand.
"""

from __future__ import annotations

import datetime
import hashlib
from collections.abc import Callable, Mapping
from typing import Final, cast

from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, UnavailableExtractor

#: `(data, mime, document_key) -> answer`. The answer is `object` on purpose: a
#: service that has broken can return anything at all, and a type that promised
#: otherwise would be a lie the adapter then had to unpick at runtime anyway.
ServiceCall = Callable[[bytes, str, str], object]

#: The key the answer must echo, so "is this about our bill" is answerable.
DOCUMENT_KEY: Final = "document"
#: Optional. The service's own transcription of the bill, carried, never parsed.
TEXT_KEY: Final = "text"

#: Optional. Field name -> how sure the service says it is, 0.0 to 1.0.
#:
#: OPTIONAL, AND WHAT SILENCE COSTS. Most paid readers report a per-field
#: confidence and this is where theirs arrives. A service that reports none says
#: nothing here, the record states no score for any field, and a field with no
#: stated score is treated downstream as an estimate - so its party name is not
#: used as a supplier's identity and the person is asked instead. That is the
#: correct price of silence: the alternative is this adapter inventing a
#: certainty on behalf of a reader that never claimed one, which is exactly the
#: defect `adapter.ExtractedRecord.per_field_confidence` was added to close.
#:
#: NOT REFUSED WHEN ABSENT, unlike the four named fields. The difference is that
#: a missing VALUE is ambiguous - "not on the bill" and "the service stopped
#: halfway" mean opposite things - while a missing SCORE is not: it means nobody
#: stated one, and the safe reading of that is the only reading of it.
CONFIDENCE_KEY: Final = "confidence"

# ---- the ten ways this can go wrong, in the words the person will read -------
#
# Named constants rather than inline strings so the set is enumerable, and so a
# test can assert that a scenario produced THIS reason and not merely some
# reason. A message nobody can pin is a message that can drift into silence.

UNAVAILABLE: Final = "the reading service is not available"
TIMED_OUT: Final = "the reading service did not answer in time"
REFUSED: Final = "the reading service refused the connection"
NOT_SIGNED_IN: Final = "we are not signed in to the reading service"
RATE_LIMITED: Final = "the reading service is not taking more requests just now"
EMPTY: Final = "the reading service sent an empty answer"
MALFORMED: Final = "the reading service sent an answer we cannot use"
UNIDENTIFIED: Final = "the answer does not say which bill it is about"
WRONG_DOCUMENT: Final = "the answer is about a different bill"
INCOMPLETE: Final = "the answer leaves fields out"

#: Every reason above, so a test can prove the ten scenarios are ten and that
#: none of them shares a sentence with another.
ALL_REASONS: Final = (
    UNAVAILABLE,
    TIMED_OUT,
    REFUSED,
    NOT_SIGNED_IN,
    RATE_LIMITED,
    EMPTY,
    MALFORMED,
    UNIDENTIFIED,
    WRONG_DOCUMENT,
    INCOMPLETE,
)


class ExtractionFailed(Exception):
    """The transport could not get an answer, and says why in plain words.

    The reason travels with the exception because the transport is the only
    thing that knows whether a 401 or a 429 came back. Guessing it from an HTTP
    status this module never sees would be inventing evidence.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: Builtin exceptions a transport may raise without knowing about this module.
#: ORDER MATTERS: `ConnectionRefusedError` is a subclass of `ConnectionError`,
#: so the specific one is tested first. Reversed, every refusal would be
#: reported as a generic outage and the person would be told to wait when the
#: real answer is that nothing is listening.
_BUILTIN_REASONS: Final[tuple[tuple[type[BaseException], str], ...]] = (
    (ConnectionRefusedError, REFUSED),
    (TimeoutError, TIMED_OUT),
    (PermissionError, NOT_SIGNED_IN),
    (ConnectionError, UNAVAILABLE),
)


def reason_for(exc: BaseException) -> str:
    """Plain words for one failure. Never empty, never a stack message."""
    if isinstance(exc, ExtractionFailed):
        return exc.reason
    for kind, reason in _BUILTIN_REASONS:
        if isinstance(exc, kind):
            return reason
    return f"{UNAVAILABLE} ({type(exc).__name__})"


def document_key(data: bytes) -> str:
    """A short, stable name for these exact bytes.

    This IDENTIFIES a document. It does not read one: the same sixteen
    characters come back for a PDF, a photograph or a line somebody typed, and
    nothing here looks inside. It exists so that "the service answered about a
    different bill" is a question with an answer instead of an assumption.
    """
    return hashlib.sha256(data).hexdigest()[:16]


def _as_named_fields(answer: object) -> dict[str, object] | None:
    """The answer as name -> value, or None when it is not that shape."""
    if not isinstance(answer, Mapping):
        return None
    # `isinstance` against a generic leaves the parameters Unknown, and strict
    # mode is right to object. The cast states what a Mapping of anything is.
    pairs = cast("Mapping[object, object]", answer)
    out: dict[str, object] = {}
    for key, value in pairs.items():
        if not isinstance(key, str):
            return None
        out[key] = value
    return out


def _as_date(value: object) -> tuple[datetime.date | None, str]:
    """A date, or the sentence explaining why what arrived is not one."""
    if value is None:
        return None, ""
    # A datetime is not a date. `datetime` subclasses `date`, so an unguarded
    # isinstance accepts one, and a voucher date carrying a time of day is a
    # different value that compares unequal to the day it looks like.
    if isinstance(value, datetime.datetime):
        return None, "the date arrived with a time of day attached"
    if isinstance(value, datetime.date):
        return value, ""
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value), ""
        except ValueError:
            return None, f"the date {value!r} is not a date"
    return None, f"the date arrived as {type(value).__name__}"


def _as_paise(field: str, value: object) -> tuple[int | None, str]:
    """Whole paise, or the sentence explaining why what arrived is not that.

    No conversion of any kind. `"4200.00"` is refused rather than parsed, and a
    float is refused rather than rounded: `accountant/extract/adapter.py`
    already carries the measured case where rounding a typed amount put a wrong
    number in somebody's books, and a service is not more trustworthy than a
    person's own typing.
    """
    if value is None:
        return None, ""
    # bool before int. `isinstance(True, int)` is True, and True would become
    # one paise.
    if isinstance(value, bool):
        return None, f"{field} arrived as a true/false value"
    if isinstance(value, int):
        return value, ""
    return None, (
        f"{field} arrived as {type(value).__name__}, and money is only ever whole paise"
    )


def _as_party(value: object) -> tuple[str | None, str]:
    """A name, or the sentence explaining why what arrived is not one.

    A blank or whitespace string becomes None, which becomes an explicit
    not_found. A party field holding `"   "` is a silent blank, which is the
    one thing `ExtractedRecord` exists to make impossible.
    """
    if value is None:
        return None, ""
    if isinstance(value, str):
        return (value.strip() or None), ""
    return None, f"the party arrived as {type(value).__name__}"


def _as_confidence(value: object) -> tuple[dict[str, float], str]:
    """The per-field scores, or the sentence explaining why they are not usable.

    Refused rather than repaired, in three ways, and each one is a way a caller
    could otherwise end up believing a number nobody measured:

      a name we did not ask about   the service is answering a question we did
                                    not put, and a key we silently drop is a
                                    field it thinks it has told us about.
      a bool, or anything but a     `isinstance(True, float)` is False but
      real number                   `bool` is an `int`, and `True` read as a
                                    score is 1.0 - full certainty from a flag.
      outside 0.0 to 1.0            a score above 1.0 clears the auto-post band
                                    by accident. `wall.Field` refuses the same
                                    number for the same reason.

    A partial map is allowed: a service may be sure about the total and say
    nothing about the date. Silence about a field is not a score of zero, and
    the record's own contract already reads an absent score as "nobody said".
    """
    if value is None:
        return {}, ""
    if not isinstance(value, Mapping):
        return {}, f"the confidences arrived as {type(value).__name__}"
    pairs = cast("Mapping[object, object]", value)
    scores: dict[str, float] = {}
    for name, score in pairs.items():
        if name not in ExtractedRecord.FIELDS:
            return {}, f"a confidence was given for {name!r}, which is not a field"
        # bool before the number check, and `type(...) is not` rather than
        # isinstance: `True` is an `int`, and an `int` is accepted below, so an
        # unguarded check turns a flag into full certainty.
        if type(score) is not float and type(score) is not int:
            return {}, (
                f"the confidence for {name} arrived as {type(score).__name__}, "
                "and a confidence is a number from 0.0 to 1.0"
            )
        if not 0.0 <= score <= 1.0:
            return {}, (
                f"the confidence for {name} is {score}, which is outside 0.0 "
                "to 1.0. Guessing which scale it is on would be inventing data"
            )
        scores[cast("str", name)] = float(score)
    return scores, ""


def _as_text(value: object) -> tuple[str, str]:
    if value is None:
        return "", ""
    if isinstance(value, str):
        return value, ""
    return "", f"the text arrived as {type(value).__name__}"


class ServiceExtractor:
    """Somebody else read the bill. This turns their answer into our record.

    `extract` NEVER raises. Every failure becomes a record whose every field is
    `not_found` with the reason attached, because the caller is
    `pipeline.build_draft` and an exception there is a 503 page for a person
    whose only problem is that a supplier's website is down. They should be
    looking at an entry form with a sentence above it.
    """

    def __init__(self, call: ServiceCall, *, name: str = "reader_service") -> None:
        self._call = call
        self.name = name

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        key = document_key(data)
        try:
            answer = self._call(data, mime, key)
        except Exception as exc:
            # `Exception`, not `BaseException`. A KeyboardInterrupt or a
            # SystemExit is somebody stopping the process, and turning that
            # into a tidy not_found record would fight them.
            return self.outage(reason_for(exc))
        return self._adapt(answer, key)

    def outage(self, reason: str) -> ExtractedRecord:
        """The all-not_found record, built in exactly one place.

        Public because the ten scenarios are asserted against it directly as
        well as through `extract`, and because a caller holding a reason of its
        own — a circuit breaker, a queue that gave up — should produce the same
        record rather than a second shape that resembles it.
        """
        return UnavailableExtractor(reason, name=self.name).extract(b"", "")

    def _adapt(self, answer: object, key: str) -> ExtractedRecord:
        named = _as_named_fields(answer)
        if named is None:
            return self.outage(
                f"{MALFORMED}: expected named fields, got {type(answer).__name__}"
            )
        if not named:
            return self.outage(EMPTY)

        stated = named.get(DOCUMENT_KEY)
        if stated is None:
            return self.outage(UNIDENTIFIED)
        if stated != key:
            return self.outage(
                f"{WRONG_DOCUMENT}: we asked about {key} and this answers "
                f"about {stated!r}"
            )

        absent = [f for f in ExtractedRecord.FIELDS if f not in named]
        if absent:
            return self.outage(
                f"{INCOMPLETE}: nothing was said about " + ", ".join(absent)
            )

        date, date_problem = _as_date(named["date"])
        party, party_problem = _as_party(named["party"])
        total, total_problem = _as_paise("total_paise", named["total_paise"])
        tax, tax_problem = _as_paise("tax_paise", named["tax_paise"])
        text, text_problem = _as_text(named.get(TEXT_KEY))
        scores, score_problem = _as_confidence(named.get(CONFIDENCE_KEY))

        wrong = [
            problem
            for problem in (
                date_problem,
                party_problem,
                total_problem,
                tax_problem,
                text_problem,
                score_problem,
            )
            if problem
        ]
        if wrong:
            return self.outage(f"{MALFORMED}: " + "; ".join(wrong))

        supplied: dict[str, object] = {
            "date": date,
            "party": party,
            "total_paise": total,
            "tax_paise": tax,
        }
        return ExtractedRecord(
            date=date,
            party=party,
            total_paise=total,
            tax_paise=tax,
            raw_text=text,
            backend=self.name,
            per_field_source={
                field: (
                    self.name
                    if value is not None
                    else f"{NOT_FOUND}: the reading service found no {field}"
                )
                for field, value in supplied.items()
            },
            # Only for fields that came back with a value. A score attached to
            # a field the service said nothing about would be a certainty about
            # an absence, and `ExtractedRecord.read_exactly` requires both
            # halves anyway - so carrying it would be noise in the evidence row
            # rather than a fact.
            per_field_confidence={
                field: score
                for field, score in scores.items()
                if supplied.get(field) is not None
            },
        )
