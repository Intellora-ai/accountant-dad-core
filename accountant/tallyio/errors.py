"""What Tally said went wrong, in words a person can act on.

WHY A SEPARATE MODULE, AND WHY IT PARSES RATHER THAN GUESSES
-------------------------------------------------------------
Tally answers almost everything with **HTTP 200**. A ledger that does not exist,
a date outside the financial year, a company that is not open - all of them come
back 200 with the complaint inside the XML body. So "did it work" cannot be read
off the status code, and a client that trusts the status code reports success
for a voucher that was refused.

This module turns that body into one of four exceptions. Nothing else in the
package is allowed to decide what an error means, so there is one place to look
when a new failure appears and one place to fix.

THE FOUR KINDS, AND WHY THE SPLIT IS THIS SPLIT
-----------------------------------------------
The split is by WHAT THE READER SHOULD DO, not by where the failure happened:

    TallyConnectionError   nothing answered          -> check Tally is running
    TallyRequestError      answered, unusable        -> a bug here, or a proxy
    TallyBusinessError     answered, and said no     -> fix the data and retry
    TallyPolicyError       we refused before asking  -> a decision, not a fault

A split by layer would put "ledger missing" and "malformed XML" together because
both arrive over HTTP, and those two need completely different people.

UNCLASSIFIED IS COUNTED, NEVER SWALLOWED
-----------------------------------------
`classify` returns a `TallyBusinessError` carrying `code=UNCLASSIFIED` when it
finds a complaint it does not recognise. It never returns `None` for text that
plainly contains an error, and it never invents a code that looks specific.

That matters because the target is stated as a percentage - "98% of errors
classified" - and a percentage needs a denominator. `UNCLASSIFIED` IS the
denominator, so the number is measured rather than asserted, and a new Tally
release that renames a message shows up as a rising count rather than as
silence.

WHAT THIS MODULE DOES NOT DO
-----------------------------
It does not open a socket, retry, or decide whether an operation should be
attempted. It reads text and returns an exception object. `raise` is the
caller's word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

# ---------------------------------------------------------------------------
# the exceptions
# ---------------------------------------------------------------------------


class TallyError(Exception):
    """Base. Always carries a code, a sentence, and what it was about.

    `entity` is the thing a person has to go and fix - a ledger name, a date, a
    company. It is `None` only when Tally genuinely did not say.
    """

    def __init__(self, code: str, message: str, entity: str | None = None) -> None:
        super().__init__(f"{code}: {message}" + (f" [{entity}]" if entity else ""))
        self.code = code
        self.message = message
        self.entity = entity

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "entity": self.entity}


class TallyConnectionError(TallyError):
    """Nothing answered. Tally is closed, the port is shut, or the host is wrong."""


class TallyRequestError(TallyError):
    """Something answered, and what came back is not usable.

    Non-200, an empty body, or bytes that are not XML. This is a fault in this
    code or in something between here and Tally - never a fault in the data.
    """


class TallyBusinessError(TallyError):
    """Tally understood the request and refused it. The data is wrong."""


class TallyPolicyError(TallyError):
    """This code refused before Tally was asked.

    Distinct from a business error on purpose: nothing was sent, so nothing was
    attempted, and the fix is a decision rather than a correction. A caller
    retrying a policy refusal unchanged will be refused identically for ever.
    """


# ---------------------------------------------------------------------------
# codes
# ---------------------------------------------------------------------------

UNCLASSIFIED: Final = "TALLY_UNCLASSIFIED"

LEDGER_UNKNOWN: Final = "LEDGER_UNKNOWN"
COMPANY_NOT_OPEN: Final = "COMPANY_NOT_OPEN"
COMPANY_MISMATCH: Final = "COMPANY_MISMATCH"
DATE_OUT_OF_RANGE: Final = "DATE_OUT_OF_RANGE"
VOUCHER_TYPE_UNKNOWN: Final = "VOUCHER_TYPE_UNKNOWN"
DUPLICATE_NAME: Final = "DUPLICATE_NAME"
GROUP_UNKNOWN: Final = "GROUP_UNKNOWN"
UNBALANCED: Final = "UNBALANCED"
LICENCE_RESTRICTED: Final = "LICENCE_RESTRICTED"

NOT_XML: Final = "NOT_XML"
EMPTY_BODY: Final = "EMPTY_BODY"
HTTP_STATUS: Final = "HTTP_STATUS"

#: Nothing answered at all. Added 2026-08-12, because there was no code for the
#: commonest failure a person actually meets: TallyPrime closed, or the port
#: shut. `TallyConnectionError` existed with nothing that raised it, so a
#: gateway that was down escaped this module entirely and reached callers as
#: `real.TallyUnreachable` - a DIFFERENT exception tree that `except
#: errors.TallyError` does not catch. The result was a raw traceback where a
#: sentence was promised.
UNREACHABLE: Final = "UNREACHABLE"


@dataclass(frozen=True)
class Pattern:
    """One recognisable complaint.

    `entity_group` names the regex group holding the thing to fix, so the name
    of the missing ledger reaches the reader instead of being described.
    """

    code: str
    regex: re.Pattern[str]
    said: str
    entity_group: int | None = None


#: ORDER MATTERS. The first match wins, so the specific sit above the general.
#:
#: Written from TallyPrime's observed message text. It is a list rather than a
#: dict so the order is visible, and each entry says what a person should do -
#: a code with no next action is a code nobody can use.
PATTERNS: Final[tuple[Pattern, ...]] = (
    Pattern(
        LEDGER_UNKNOWN,
        re.compile(r"Ledger\s+'([^']+)'\s+does\s+not\s+exist", re.I),
        "that ledger does not exist in this company. Create it first, or turn "
        "on master auto-creation.",
        1,
    ),
    Pattern(
        LEDGER_UNKNOWN,
        re.compile(r"Could\s+not\s+(?:find|set)\s+ledger[:\s]+'?([^'<\n]+)", re.I),
        "that ledger does not exist in this company. Create it first, or turn "
        "on master auto-creation.",
        1,
    ),
    Pattern(
        DUPLICATE_NAME,
        re.compile(r"(?:already\s+exists|duplicate\s+(?:name|entry))", re.I),
        "something with that name is already there. Creating it again would "
        "make a second master with the same name, which Tally refuses.",
    ),
    Pattern(
        GROUP_UNKNOWN,
        re.compile(r"Group\s+'([^']+)'\s+does\s+not\s+exist", re.I),
        "that accounting group does not exist. Check the spelling against "
        "Tally's own list - 'Sundry Creditors', not 'Sundry Creditor'.",
        1,
    ),
    Pattern(
        COMPANY_NOT_OPEN,
        re.compile(
            r"(?:No\s+company|company\s+is\s+not)\s+(?:open|loaded|active)", re.I
        ),
        "no company is open in Tally. The gateway only serves a company that is "
        "loaded on screen.",
    ),
    Pattern(
        COMPANY_MISMATCH,
        re.compile(r"Company\s+'([^']+)'\s+(?:not\s+found|is\s+not\s+open)", re.I),
        "that company is not the one Tally has open. The name must match "
        "character for character, including spaces.",
        1,
    ),
    Pattern(
        DATE_OUT_OF_RANGE,
        re.compile(
            r"date.{0,40}(?:out\s+of\s+range|beyond|outside|not\s+within)", re.I
        ),
        "that date is outside the company's financial year, so the voucher has "
        "nowhere to go.",
    ),
    Pattern(
        VOUCHER_TYPE_UNKNOWN,
        re.compile(r"Voucher\s+Type\s+'([^']+)'\s+does\s+not\s+exist", re.I),
        "that voucher type does not exist in this company.",
        1,
    ),
    Pattern(
        UNBALANCED,
        re.compile(
            r"(?:debit|credit).{0,30}(?:not\s+equal|mismatch|do(?:es)?\s+not\s+(?:match|tally))",
            re.I,
        ),
        "the debits and the credits are not equal, so this is not a "
        "double entry and Tally will not hold it.",
    ),
    Pattern(
        LICENCE_RESTRICTED,
        re.compile(
            r"(?:educational|licen[cs]e).{0,40}(?:restrict|not\s+allow|expired)", re.I
        ),
        "the licence mode refuses this. Educational mode only accepts vouchers "
        "dated the 1st, 2nd or 31st of a month.",
    ),
)

#: Where Tally puts a complaint. Checked as element names rather than searched
#: as loose words: the word "error" appears in perfectly successful responses
#: that happen to carry a field named for it.
#:
#: `DESC` WAS IN THIS LIST AND IS NOT ANY MORE. Measured 2026-08-12 against the
#: live TallyPrime: a successful ledger import answers with `CREATED 1` and a
#: `<DESC>` holding a long row of zero counters. `DESC` is a generic container -
#: it appears in requests too - so scraping it reported every successful import
#: as an unclassified refusal. Two ledgers were genuinely created while this
#: code said `success: False`.
#:
#: A false negative is the worse direction here: it makes a caller retry a
#: write that already happened. Tally puts real complaints in `LINEERROR`.
ERROR_ELEMENTS: Final = ("LINEERROR", "ERRORS", "EXCEPTIONS")


@dataclass
class Report:
    """What one response contained. Used to measure classification, not to raise."""

    classified: list[TallyError] = field(default_factory=list[TallyError])
    unclassified: list[str] = field(default_factory=list[str])

    @property
    def rate(self) -> float:
        """Fraction classified. 1.0 when there was nothing to classify."""
        total = len(self.classified) + len(self.unclassified)
        return 1.0 if total == 0 else len(self.classified) / total


def _complaints(raw: str) -> list[str]:
    """Every sentence in the response that looks like a complaint.

    Read from the named elements rather than by scanning the whole body: a
    party name containing the word "error" is not an error, and a check that
    cannot tell those apart is a check that fires on customer data.
    """
    found: list[str] = []
    for name in ERROR_ELEMENTS:
        for match in re.finditer(rf"<{name}[^>]*>(.*?)</{name}>", raw, re.S | re.I):
            text = re.sub(r"<[^>]+>", " ", match.group(1))
            text = " ".join(text.split())
            if text:
                found.append(text)
    # `<LINEERROR/>` and friends: an empty self-closing tag is Tally saying
    # "no error on this line", so it is not a complaint.
    #
    # A run of digits and spaces is not a complaint either. Tally's import
    # counters arrive as "0 0 1 0 0 ...", and `str.isdigit()` answers False for
    # that because of the spaces - which is how the counter row was read as a
    # refusal on 2026-08-12. Whitespace is stripped before the test.
    return [f for f in found if f and not f.replace(" ", "").isdigit()]


def classify(raw: str) -> TallyError | None:
    """The one error in this response, or None when there is none.

    Returns the FIRST recognised complaint. A response carrying three
    complaints is one refusal to a person, and handing back three exceptions
    would make the caller pick.
    """
    report = describe(raw)
    if report.classified:
        return report.classified[0]
    if report.unclassified:
        return TallyBusinessError(
            UNCLASSIFIED,
            "Tally refused this and said something this code does not recognise "
            f"yet: {report.unclassified[0][:200]}",
        )
    return None


def describe(raw: str) -> Report:
    """Every complaint in the response, split into recognised and not.

    This is the function the measurement uses. `classify` answers "what do I
    tell the caller"; this answers "how much of what Tally says do we actually
    understand", which is the number that has to stay above the target.
    """
    report = Report()
    for said in _complaints(raw):
        for pattern in PATTERNS:
            found = pattern.regex.search(said)
            if not found:
                continue
            entity = (
                found.group(pattern.entity_group)
                if pattern.entity_group and found.lastindex
                else None
            )
            report.classified.append(
                TallyBusinessError(pattern.code, pattern.said, entity)
            )
            break
        else:
            report.unclassified.append(said)
    return report


def parse_tally_error(xml: str) -> TallyError | None:
    """The name the specification uses. `classify` is the implementation."""
    return classify(xml)


def for_status(status: int, body: str) -> TallyRequestError:
    """A non-200 as an exception carrying enough of the body to diagnose it."""
    return TallyRequestError(
        HTTP_STATUS,
        f"Tally answered HTTP {status} rather than 200: {body[:300]}",
    )


def for_unreachable(cause: BaseException) -> TallyConnectionError:
    """Nothing answered. The cause is kept verbatim rather than summarised.

    NOT a business error and never a data problem: nothing reached TallyPrime,
    so nothing was committed. The connector's own message already names the
    address and the remedy, and rewording it here would only lose detail.
    """
    return TallyConnectionError(UNREACHABLE, str(cause))


def for_unusable_body(raw: str) -> TallyRequestError:
    """An answer that is not XML, or is nothing at all."""
    if not raw.strip():
        return TallyRequestError(
            EMPTY_BODY,
            "Tally answered with an empty body. That is not a refusal and not a "
            "success - nothing was said.",
        )
    return TallyRequestError(NOT_XML, f"Tally's answer is not XML: {raw[:200]!r}")
