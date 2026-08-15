"""One reader for the date a bill printed, and the rule it was read under.

WHY THIS FILE EXISTS
--------------------
`textlayer._date_from` reads a numeric date only when the document itself
settles the order of its two leading numbers - one of them over twelve, or the
two of them equal. Everything else is refused as ambiguous. That is a safe rule
and it is the wrong one for the country this product sells into: an Indian
purchase bill prints `01/04/2026` and means the 1st of April, and there are
twelve months in which BOTH numbers are twelve or under.

MEASURED 2026-08-15, `accountant/extract/textlayer._date_from` called directly
on each string on branch `cage/safety-layer`:

    '01/04/2026'    REFUSED   ambiguous (could be 1 April or 4 January)
    '09/07/2014'    REFUSED   ambiguous (could be 9 July or 7 September)
    '06/10/2026'    REFUSED   ambiguous (could be 6 October or 10 June)
    '01-04-2026'    REFUSED   ambiguous, same sentence
    '01.04.2026'    REFUSED   ambiguous, same sentence
    '28/01/26'      REFUSED   'is not a date this reader reads'
    '01/04/26'      REFUSED   'is not a date this reader reads'
    '01-04-26'      REFUSED   'is not a date this reader reads'
    '2026/04/01'    REFUSED   'is not a date this reader reads'
    '2026-04-01'    read      2026-04-01
    '01 Apr 2026'   read      2026-04-01
    '01 April 2026' read      2026-04-01
    '26/02/2026'    read      2026-02-26   26 cannot be a month
    '04/04/2019'    read      2019-04-04   both readings are the same day

THREE of the nine written forms this module is required to accept read today
unconditionally. SIX do not.

ONE CLAIM IN THE BRIEF THAT COMMISSIONED THIS FILE IS WRONG, AND IT IS WRITTEN
DOWN HERE RATHER THAN QUIETLY WORKED AROUND. `04/04/2019` was reported as
refused. It is not. `_ordered_date` carries a `first == second` branch and
returns the 4th of April, and `26/02/2026` reads for the same kind of reason.
So the defect being repaired is NARROWER than reported: it is the dates where
two DIFFERENT real readings stand, plus two-digit years, plus `YYYY/MM/DD`.
The `04/04/2019` test in `tests/test_dates.py` is therefore a NON-REGRESSION
control - a day that reads today and must still read - and it is labelled as
one there. A test that claims to fix something already working is a test that
will be deleted by the first person who checks.

WHAT A LOCALE IS HERE, AND WHY IT IS NEVER INFERRED
----------------------------------------------------
`DateLocale` is a statement by the OWNER about the documents being read, handed
in at the call site. It is not detected, not defaulted and not guessed - for
the reason `labels.Printing` gives about the same shape of argument: a tier
that can be guessed at will be guessed at wrongly, and the wrong guess here
files a return in the wrong month with confidence 1.0 behind it.

The obvious alternative - infer the locale from the supplier's country, or from
a currency symbol on the page - puts the decision in whichever module happens
to hold a country string, and a bill printed in India by a UK supplier would
then read month-first. So the decision stays with the person who owns the
consequences, and this module only reports which rule it used, in
`locale_assumed`.

THE THREE RULES, IN ONE SENTENCE EACH
--------------------------------------
    INDIAN      The first number is the day. Always. The owner said so, so
                nothing here is a guess and `ambiguous` is always False.

    ISO_ONLY    Only forms whose ORDER THE DOCUMENT ITSELF STATES are read - a
                four-digit year in front, or a month printed as a word. A bare
                numeric date is refused WITHOUT EXCEPTION, including
                `04/04/2019`, whose two readings agree, and `26/02/2026`, where
                only one reading is a real day. This is the strictest tier and
                it exists to have no judgement calls in it at all: one
                sentence, no exceptions, nothing to argue about at review. The
                cost is named and measured - those two forms are refused here
                and read under UNKNOWN - and a caller who wants them should
                pass UNKNOWN, which is what that tier is for.

    UNKNOWN     Arithmetic may settle the order and nothing else may. If both
                orders give a real day and those days DIFFER, the value is None
                and a person is asked. If both orders give the SAME day there
                is nothing to choose between and the day is returned. If only
                one order gives a real day, that is not a choice either.

THE CENTURY RULE FOR A TWO-DIGIT YEAR, AND WHY IT IS FIXED
-----------------------------------------------------------
`00`-`68` is 2000-2068. `69`-`99` is 1969-1999. That is POSIX's `strptime('%y')`
rule and Python's, so a date that passes through this reader and a date that
passes through any other tool on the machine agree, and this repository does not
become the second opinion about what `26` means.

IT DOES NOT READ THE CLOCK, and that is the decision rather than an oversight.
A sliding window - "within eighty years of today" - reads better and makes the
same bill parse differently next year, which means an audit re-run of a posted
voucher can disagree with the voucher. A reader whose answer depends on when it
was asked is not a reader. `labels.py` says NO CLOCK for the same reason and
this module holds to it.

WHAT IT COSTS, STATED: a bill genuinely dated 2069 or later reads as 1969 or
later. That is 43 years away, it is a purchase-ledger product, and the pivot is
one constant with one test on it when somebody wants to move it.

WHAT THIS FILE DOES NOT DO
---------------------------
IT DOES NOT FIND THE DATE FIELD. `labels.DATE_LABEL` and `labels.values_for`
own the question "what is a date called on a bill", they are the only answer to
it in this repository, and nothing here duplicates a single label. This module
is handed characters and reports what date they state. It deliberately imports
nothing from `labels.py`: the label vocabulary is about labels and money, the
vocabulary here is about the written shapes of a day, and the two have no term
in common.

IT DOES NOT MEND CHARACTERS. `raw` is the argument, byte for byte, on every
path including every refusal. A reader that tidied its input would be
answering about a string the page does not contain.

IT DOES NOT DECIDE WHETHER A DATE IS PLAUSIBLE. A bill dated 1998 is read as
1998. Whether that belongs in an open year is `gate.py`'s question and it has
the evidence for it; a date reader that also had opinions about accounting
periods would be two things, and the second one would be wrong in a place
nobody looks.

NO NETWORK, NO CLOCK, NO FILESYSTEM, NO THIRD-PARTY IMPORT. Characters in, one
day out, or no day and a sentence saying why.
"""

from __future__ import annotations

import datetime
import enum
import re
from dataclasses import dataclass
from typing import Final

# =============================================================================
# what the owner says about the documents
# =============================================================================


class DateLocale(enum.Enum):
    """Which convention the OWNER states this document's dates are printed in.

    Three members and no fourth. A `BRITISH` or `AMERICAN` member would read
    identically to one of these - day-first is day-first whoever printed it -
    and every extra member is another value the dispatch below has to be
    checked against. When a fourth convention appears that is genuinely
    DIFFERENT, it earns a member; a synonym does not.

    There is no default. See the module docstring: a locale that can be
    defaulted is a locale somebody will get by never having thought about it.
    """

    #: The owner states these bills are Indian. `DD/MM/YYYY`, day first.
    INDIAN = "indian"

    #: The owner states nothing may be assumed about the order of two bare
    #: numbers - not even when arithmetic would settle it. The strictest tier.
    ISO_ONLY = "iso_only"

    #: The owner does not know. Arithmetic may settle the order; a genuine
    #: choice between two real days is refused and a person is asked.
    UNKNOWN = "unknown"


# =============================================================================
# the vocabulary of written days
# =============================================================================

#: The twelve, spelled out. PUBLIC, because `textlayer._MONTH_NAMES` is the
#: same twelve and should import these instead of keeping a second copy - two
#: lists that must agree are one list plus a bug waiting for an editor of one
#: of them. The exact diff that does that is in this task's report.
#:
#: Written down rather than taken from `%B`, which follows the machine's
#: locale. A bill that parses on one machine and not another is not a reading,
#: and a refusal naming a month in the server's language is not a sentence the
#: person holding the bill can act on.
MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: What `input_format` reports for each shape that names its own order.
#: `ISO` is spelled as itself rather than as `YYYY-MM-DD` because that is the
#: name every refusal sentence here asks a person to answer in, and a field
#: reporting one name while the sentence asks for another is two names.
FORMAT_ISO: Final = "ISO"
FORMAT_YEAR_FIRST_SLASH: Final = "YYYY/MM/DD"

#: What `input_format` reports when no date shape was found at all, and when
#: the characters carry more than one date and those dates disagree. Both are
#: refusals, and both need a value in the field that is not a shape.
FORMAT_UNREADABLE: Final = "unrecognised"
FORMAT_SEVERAL: Final = "several"

#: How a month printed as a word is reported. The separator the page used is
#: substituted into these, so `01-Apr-2026` reports `DD-Mon-YYYY`.
_SHAPE_ABBREVIATION: Final = "Mon"
_SHAPE_WORD: Final = "Month"

#: `00`-`68` is 2000-2068 and the rest is 1900s. POSIX's rule and Python's own
#: `strptime('%y')`. See the module docstring for why it is fixed and what
#: that costs. Moving it means editing this one constant and the one test that
#: names it.
CENTURY_PIVOT: Final = 68

#: How long a refusal sentence is allowed to quote back. A whole page of text
#: handed to `read_date` must not become a whole page of refusal in somebody's
#: question queue.
_QUOTE_LIMIT: Final = 60


def _month_words() -> dict[str, tuple[int, str]]:
    """Every spelling of a month this reader accepts, and what it means.

    DERIVED FROM `MONTH_NAMES`, not written twice, for the reason that constant
    gives. The value carries the shape name too, so `01 Apr 2026` and
    `01 April 2026` report different `input_format` without a second lookup.

    STRICTER THAN `textlayer._LONG_DATE`, DELIBERATELY. That pattern is
    `([A-Z]{3})[A-Z]*`, which takes the first three letters and lets anything
    follow - so `01 JANUXXX 2026` reads as January. Here the whole word must be
    a month this table knows. The cost is that a badly misread month word is
    UNREAD instead of read wrong, which is the trade this repository makes
    everywhere else.

    `SEPT` IS THE ONE IRREGULAR SPELLING and it is here by name. It is not
    derivable from `September` by the three-letter rule and suppliers print it.
    """
    words: dict[str, tuple[int, str]] = {}
    for index, name in enumerate(MONTH_NAMES, start=1):
        words[name.upper()] = (index, _SHAPE_WORD)
        words[name[:3].upper()] = (index, _SHAPE_ABBREVIATION)
    words["SEPT"] = (9, _SHAPE_ABBREVIATION)
    return words


_MONTH_BY_WORD: Final[dict[str, tuple[int, str]]] = _month_words()


# =============================================================================
# the shapes
# =============================================================================

#: A four-digit year in front. The document states its own order and no locale
#: can change what it says, so this reads identically under all three.
#:
#: THE SEPARATOR IS A BACKREFERENCE ON PURPOSE. `2026-04/01` is not a date
#: anybody printed, it is two shapes collided by a misread, and reading it
#: would be inventing which half was right.
_YEAR_FIRST: Final = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?P<sep>[-/])(?P<month>\d{1,2})(?P=sep)(?P<day>\d{1,2})(?!\d)"
)

#: Two bare numbers and a year. THE ONE SHAPE WHOSE MEANING DEPENDS ON THE
#: LOCALE, and the whole reason this module takes one.
#:
#: THE YEAR IS EXACTLY TWO OR EXACTLY FOUR DIGITS, never three. `01/04/202` is
#: a misread `2026` or a misread `2020`, and reading it as the year 202 posts a
#: voucher into a period that cannot exist. `\d{2,4}` would have accepted it.
#:
#: WHAT IT STILL MATCHES THAT IS NOT A DATE, MEASURED WHILE PROBING IT: a
#: three-part clause or version number whose last part is two digits - `1.2.34`
#: reads as the 1st of February 2034. Grouped money does NOT match, because a
#: thousands group is three digits and the middle number here is at most two:
#: `1.234.567` and `2,076.76` both come back with no date, and there is a test
#: on the first of those. The clause-number case is left standing because the
#: only tightening that removes it - demanding two digits for the day and the
#: month - would refuse `1/4/2026`, which is a printing real suppliers use. A
#: caller handing over the value it already found under `labels.DATE_LABEL` is
#: not exposed to this at all.
_NUMERIC: Final = re.compile(
    r"(?<!\d)(?P<first>\d{1,2})(?P<sep>[-/.])(?P<second>\d{1,2})(?P=sep)"
    r"(?P<year>\d{4}|\d{2})(?!\d)"
)

#: A month printed as a word. The order is stated by the word, so this too
#: reads identically under all three locales.
#:
#: THE SEPARATORS ARE RUNS AND ARE NOT TIED TO EACH OTHER. A PDF text layer
#: rebuilds a column gap as several spaces, so `01  Apr  2026` is ordinary; and
#: a mixed `01-Apr 2026` still states its order unambiguously, so refusing it
#: would buy nothing. `input_format` reports the FIRST separator when they
#: differ, which is the only lossy thing in this module and it is lossy about
#: punctuation rather than about a day.
_NAMED: Final = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})(?P<gap>[ -]+)(?P<word>[A-Za-z]{3,9})\.?[ -]+"
    r"(?P<year>\d{4})(?!\d)"
)

_KIND_YEAR_FIRST: Final = "year_first"
_KIND_NUMERIC: Final = "numeric"
_KIND_NAMED: Final = "named"


# =============================================================================
# what was read, and under which rule
# =============================================================================


@dataclass(frozen=True)
class ReadDate:
    """One date read off some characters, or no date and the reason.

    FROZEN, for the reason the nine types in `schema.py` are frozen: a reading
    that can be edited after the fact is not evidence about what a page said.

    `value` IS THE ONLY ANSWER. When it is None no date was read, and no other
    field on this object may be substituted for one. That warning is aimed at
    `candidates` specifically - see below.
    """

    #: The day this reader is willing to stand behind, or None. None is never a
    #: near miss and never a default; it means a person must be asked.
    value: datetime.date | None

    #: The `text` argument, byte for byte, on every path including refusals.
    #: Not stripped, not upper-cased, not normalised. A question that quotes a
    #: tidied string asks the person to find characters the page does not have.
    raw: str

    #: The written shape that was read, e.g. `DD/MM/YYYY`, `ISO`, `DD Mon YYYY`.
    #:
    #: IT NAMES THE ORDERS THAT WERE STILL STANDING, which is why it is
    #: sometimes a disjunction. Under UNKNOWN, `26/02/2026` reports
    #: `DD/MM/YYYY` because 26 cannot be a month and only one order survived,
    #: while `04/04/2019` reports `DD/MM/YYYY or MM/DD/YYYY` because both
    #: survived and agreed - the day is certain, the order was never decided,
    #: and claiming one would be reporting a decision nobody made.
    input_format: str

    #: The rule this reading was made under. Always the `locale` argument,
    #: echoed - including for `ISO` and month-name forms, where it changed
    #: nothing. A caller comparing two readings of one document needs to know
    #: which rule produced each, and a field that sometimes reports the
    #: argument and sometimes reports something cleverer is a field nobody can
    #: compare.
    locale_assumed: DateLocale

    #: True IF AND ONLY IF two DIFFERENT real days both stand. It describes the
    #: DOCUMENT, not this reader's mood: `06/10/2026` is ambiguous under
    #: ISO_ONLY as well as under UNKNOWN, and the difference between those two
    #: tiers shows up in `value`, not here.
    #:
    #: It is False for an impossible date, for characters carrying no date, and
    #: for two dates on one line that disagree. Those are all refusals and none
    #: of them is a choice between two readings of one date.
    ambiguous: bool

    #: Every day still standing. One on a successful read; two when the
    #: document could be saying either; none when nothing readable stands.
    #:
    #: THIS IS EVIDENCE FOR A QUESTION AND IT IS NEVER THE ANSWER. A caller
    #: that reaches for `candidates[0]` when `value` is None has re-created the
    #: exact defect this module exists to remove, and it will do it silently,
    #: because a one-element `candidates` beside a None `value` looks like an
    #: oversight and is not: under ISO_ONLY that is a reading this tier was
    #: told not to make.
    candidates: tuple[datetime.date, ...]

    #: A plain sentence, NEVER empty when `value` is None, always empty when it
    #: is not.
    #:
    #: NO `not_found:` PREFIX. `adapter.NOT_FOUND` is the extraction layer's
    #: marker and the caller wiring this in adds it, exactly as
    #: `textlayer._read_date` does today. Prefixing here would produce
    #: `not_found: not_found: ...` at that call site, which is the kind of
    #: defect that ships because it is only visible in a string.
    why: str


# =============================================================================
# reading one written day
# =============================================================================


def _real(year: int, month: int, day: int) -> datetime.date | None:
    """A day that exists, or None. The 32nd of a month is a misread, not a day."""
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _century(printed: str) -> int:
    """The year a two- or four-digit year token states.

    Four digits are themselves. Two digits go through `CENTURY_PIVOT`, whose
    choice and cost the module docstring states.
    """
    if len(printed) == 4:
        return int(printed)
    short = int(printed)
    return 2000 + short if short <= CENTURY_PIVOT else 1900 + short


def _why_impossible(year: int, month: int, day: int) -> str:
    """The reason these three numbers are not a day, named rather than implied.

    `textlayer._real_date` answers all three cases with one sentence -
    `2026-01-32 is not a day that exists` - which is true and leaves the reader
    to work out which of the three numbers is the broken one. Naming it costs
    three lines and turns the refusal into something a person can check against
    the paper in front of them.
    """
    if not 1 <= month <= 12:
        return f"there is no month {month}"
    if day < 1:
        return f"there is no day {day}"
    return f"{MONTH_NAMES[month - 1]} {year} has no day {day}"


def _quoted(text: str) -> str:
    """What a refusal sentence quotes back.

    Whitespace is collapsed and a long string is cut. This is the ONLY place
    anything is tidied and it is tidied for a sentence, never for a reading -
    `ReadDate.raw` still carries the argument byte for byte.
    """
    trimmed = " ".join(text.split())
    if len(trimmed) <= _QUOTE_LIMIT:
        return trimmed
    return trimmed[:_QUOTE_LIMIT] + "..."


def _numeric_shape(separator: str, year_digits: int, orders: str) -> str:
    """`input_format` for a bare numeric date, naming the orders still standing.

    `orders` is `day`, `month` or `either`. See `ReadDate.input_format` for why
    `either` is reported as a disjunction instead of a preference.
    """
    year = "YYYY" if year_digits == 4 else "YY"
    day_first = f"DD{separator}MM{separator}{year}"
    month_first = f"MM{separator}DD{separator}{year}"
    if orders == "day":
        return day_first
    if orders == "month":
        return month_first
    return f"{day_first} or {month_first}"


def _year_first_reading(match: re.Match[str], raw: str, locale: DateLocale) -> ReadDate:
    """`2026-04-01`. The four-digit year in front states the order itself."""
    year, month, day = int(match["year"]), int(match["month"]), int(match["day"])
    shape = FORMAT_ISO if match["sep"] == "-" else FORMAT_YEAR_FIRST_SLASH
    value = _real(year, month, day)
    if value is None:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=shape,
            locale_assumed=locale,
            ambiguous=False,
            candidates=(),
            why=(
                f"The date '{match[0]}' states no day that exists: "
                f"{_why_impossible(year, month, day)}. "
                "It was misread or misprinted."
            ),
        )
    return ReadDate(
        value=value,
        raw=raw,
        input_format=shape,
        locale_assumed=locale,
        ambiguous=False,
        candidates=(value,),
        why="",
    )


def _named_reading(match: re.Match[str], raw: str, locale: DateLocale) -> ReadDate:
    """`01 Apr 2026`. The month names itself, so no order was ever in doubt."""
    month, spelling = _MONTH_BY_WORD[match["word"].upper()]
    year, day = int(match["year"]), int(match["day"])
    gap = match["gap"][0]
    shape = f"DD{gap}{spelling}{gap}YYYY"
    value = _real(year, month, day)
    if value is None:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=shape,
            locale_assumed=locale,
            ambiguous=False,
            candidates=(),
            why=(
                f"The date '{match[0]}' states no day that exists: "
                f"{_why_impossible(year, month, day)}. "
                "It was misread or misprinted."
            ),
        )
    return ReadDate(
        value=value,
        raw=raw,
        input_format=shape,
        locale_assumed=locale,
        ambiguous=False,
        candidates=(value,),
        why="",
    )


def _both_impossible(printed: str, year: int, first: int, second: int) -> str:
    """The sentence for a numeric date that is no day whichever way round it goes.

    BOTH REASONS ARE NAMED. `31/02/2026` fails day-first because February has
    no 31st and fails month-first because there is no month 31, and those are
    different facts about the same eight characters. One of them alone reads
    like a reader that only tried one order, which is the exact suspicion this
    module has to be free of.
    """
    return (
        f"The date '{printed}' states no day that exists: read day first, "
        f"{_why_impossible(year, second, first)}; read month first, "
        f"{_why_impossible(year, first, second)}. It was misread or misprinted."
    )


def _ambiguous_sentence(printed: str, first: int, second: int) -> str:
    """The sentence for a date whose order the document does not state.

    OWNER DECISION 2026-08-13, CARRIED OVER WORD FOR WORD from
    `textlayer._ambiguous`, and the words are the point:

        IT NAMES BOTH READINGS, in months a person recognises. A sentence
        carrying only one of them is a guess with a citation, which is worse
        than a guess.

        IT ASKS FOR ISO. `YYYY-MM-DD` is the one written form of a date that
        cannot be read two ways, so the answer that comes back cannot reopen
        the question the refusal was raised about.

        IT QUOTES WHAT WAS PRINTED, not a normalised form. Four of the twenty
        corpus bills write their dates with hyphens, and asking somebody to
        confirm `07/11/2026` when their document says `07-11-2026` is asking
        them to find a string that is not there.

    NOT IMPORTED FROM `textlayer.py`, and that is a direction rather than a
    duplication: this module is the lower layer and `textlayer` is to import
    the sentence back from here. The exact diff that does that is in this
    task's report. Until it lands there are two copies of one owner decision,
    which is a real debt and is named here so it is not forgotten.
    """
    return (
        f"The date '{printed}' is ambiguous (could be "
        f"{first} {MONTH_NAMES[second - 1]} or {second} {MONTH_NAMES[first - 1]}). "
        "Please confirm the date in YYYY-MM-DD format."
    )


def _numeric_reading(match: re.Match[str], raw: str, locale: DateLocale) -> ReadDate:
    """`01/04/2026` and its relatives. The one shape the locale decides.

    Both orders are computed FIRST and the locale is consulted afterwards. The
    other way round - branch on the locale, then compute the one reading it
    wants - is shorter and cannot fill `candidates` for the tier that refuses,
    so the person being asked would get "this is ambiguous" with nothing to
    choose between.
    """
    first, second = int(match["first"]), int(match["second"])
    year = _century(match["year"])
    separator = match["sep"]
    digits = len(match["year"])
    printed = match[0]

    day_first = _real(year, second, first)
    month_first = _real(year, first, second)
    standing = tuple(
        dict.fromkeys(one for one in (day_first, month_first) if one is not None)
    )

    if locale is DateLocale.INDIAN:
        return _indian_reading(printed, raw, separator, digits, year, first, second)

    if not standing:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=_numeric_shape(separator, digits, "either"),
            locale_assumed=locale,
            ambiguous=False,
            candidates=(),
            why=_both_impossible(printed, year, first, second),
        )

    orders = (
        "day" if month_first is None else "month" if day_first is None else "either"
    )
    shape = _numeric_shape(separator, digits, orders)
    ambiguous = len(standing) > 1

    if locale is DateLocale.ISO_ONLY:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=shape,
            locale_assumed=locale,
            ambiguous=ambiguous,
            candidates=standing,
            why=(
                f"The date '{printed}' does not say whether its first number is "
                "the day or the month, and this reader was told to assume no "
                "order. Please confirm the date in YYYY-MM-DD format."
            ),
        )

    if ambiguous:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=shape,
            locale_assumed=locale,
            ambiguous=True,
            candidates=standing,
            why=_ambiguous_sentence(printed, first, second),
        )
    # `next(iter(...))` AND NOT `standing[0]`. The `if not standing` guard above
    # returns, so the index is safe at runtime - but pyright narrows a falsy
    # tuple to `tuple[()]` and then reports index 0 out of range on the branch
    # that cannot be reached. Taking the first item through the iterator states
    # the same thing in a form the checker follows, with no cast promising
    # something the code has not established.
    return ReadDate(
        value=next(iter(standing)),
        raw=raw,
        input_format=shape,
        locale_assumed=locale,
        ambiguous=False,
        candidates=standing,
        why="",
    )


def _indian_reading(
    printed: str,
    raw: str,
    separator: str,
    digits: int,
    year: int,
    first: int,
    second: int,
) -> ReadDate:
    """Day first, because the owner said so.

    THE MONTH-FIRST READING IS NOT REPORTED AS A CANDIDATE even when it is a
    real day. Under this locale it is not a reading that stands, it is a
    reading the owner ruled out, and putting it in `candidates` would invite a
    caller to offer it back to somebody as a choice the owner already made.
    """
    shape = _numeric_shape(separator, digits, "day")
    value = _real(year, second, first)
    if value is None:
        return ReadDate(
            value=None,
            raw=raw,
            input_format=shape,
            locale_assumed=DateLocale.INDIAN,
            ambiguous=False,
            candidates=(),
            why=(
                f"The date '{printed}' states no day that exists: read day "
                f"first, {_why_impossible(year, second, first)}. "
                "It was misread or misprinted."
            ),
        )
    return ReadDate(
        value=value,
        raw=raw,
        input_format=shape,
        locale_assumed=DateLocale.INDIAN,
        ambiguous=False,
        candidates=(value,),
        why="",
    )


# =============================================================================
# finding the written days in a string
# =============================================================================


def _tokens(text: str) -> tuple[tuple[str, re.Match[str]], ...]:
    """Every date-shaped run of characters in `text`, in the order it prints.

    SCANNING RATHER THAN MATCHING, so `read_date` answers the same for
    `'01/04/2026'` and for `'Invoice Date: 01/04/2026'`. The caller that
    already stripped the label with `labels.values_for` loses nothing, and the
    caller that hands over a whole line does not silently get "no date".

    A MONTH WORD THAT IS NOT A MONTH IS NOT A TOKEN. `01 for 2026` fits the
    shape and `FOR` is not a month, so it is dropped here and the answer is
    "no date recognised" rather than a refusal about a date nobody printed.

    NO OVERLAP FILTER, and that is checked rather than assumed: the three
    patterns cannot match inside one another. `_YEAR_FIRST` needs four leading
    digits, which `(?<!\\d)` denies to `_NUMERIC` anywhere inside a year, and
    `_NAMED` needs letters where the other two need digits.
    """
    found: list[tuple[int, str, re.Match[str]]] = []
    for kind, pattern in (
        (_KIND_YEAR_FIRST, _YEAR_FIRST),
        (_KIND_NAMED, _NAMED),
        (_KIND_NUMERIC, _NUMERIC),
    ):
        for match in pattern.finditer(text):
            if kind == _KIND_NAMED and match["word"].upper() not in _MONTH_BY_WORD:
                continue
            found.append((match.start(), kind, match))
    found.sort(key=lambda one: one[0])
    return tuple((kind, match) for _, kind, match in found)


def read_date(text: str, *, locale: DateLocale) -> ReadDate:
    """The date these characters state, under the rule the owner named.

    `locale` IS KEYWORD-ONLY AND HAS NO DEFAULT, for the reason
    `labels.values_for` gives about `printing`: a positional second argument is
    one edit away from flipping a whole tier at a call site nobody re-reads,
    and a default would mean a caller who never thought about the question
    still got an answer to it.

    REPETITION IS ORDINARY, DISAGREEMENT IS REFUSED. A continuation sheet
    repeats its header, so the same date printed twice is one date. Two
    DIFFERENT dates in one string is a refusal - one of them is wrong, nothing
    here can say which, and picking the first is a coin toss that posts money.
    That is the same rule `labels.the_one` applies to values found under a
    label, and it is applied again here rather than imported because the
    subject is different: that function settles a disagreement between
    SEPARATE PLACES on a page, this one settles a string that carries two
    dates. Importing it would also point this module at the label vocabulary,
    which the module docstring says it must not need.
    """
    # A LOCALE THAT IS NOT A `DateLocale` MUST NOT DEGRADE INTO ONE.
    #
    # Every branch below tests `locale is DateLocale.X`, so the string
    # `"indian"` - which is exactly what a JSON boundary or a config file hands
    # over, and exactly what `DateLocale.INDIAN.value` prints as - would fail
    # all of them and fall through to the UNKNOWN behaviour. That is silent,
    # and it refuses the very dates the owner paid a decision to have read.
    #
    # The type checker sees nothing: the annotation says `DateLocale` and the
    # caller across a boundary is where annotations stop being enforced. So the
    # check is here, at runtime, naming the argument. `labels.values_for`
    # carries the same guard for the same reason, after a nested tuple cost
    # that module 98 tests. The ignore is the evidence rather than a
    # workaround: pyright calls this unnecessary BECAUSE it trusts the
    # annotation, and the annotation is exactly what is wrong at such a call.
    if not isinstance(locale, DateLocale):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(
            f"read_date takes a DateLocale, not {locale!r}. A caller holding "
            f"the stored string should convert it first - "
            f"`DateLocale(stored)` - so an unknown value raises here instead "
            f"of quietly reading every bill as if no locale were known."
        )

    tokens = _tokens(text)
    if not tokens:
        return ReadDate(
            value=None,
            raw=text,
            input_format=FORMAT_UNREADABLE,
            locale_assumed=locale,
            ambiguous=False,
            candidates=(),
            why=(
                f"'{_quoted(text)}' contains no date this reader recognises. "
                "It reads YYYY-MM-DD, YYYY/MM/DD, DD/MM/YYYY, DD-MM-YYYY, "
                "DD.MM.YYYY, DD/MM/YY, DD-MM-YY, DD.MM.YY, DD Mon YYYY and "
                "DD Month YYYY."
            ),
        )

    readings = tuple(_one_reading(kind, match, text, locale) for kind, match in tokens)
    distinct = tuple(
        dict.fromkeys((one.value, one.ambiguous, one.candidates) for one in readings)
    )
    if len(distinct) == 1:
        return readings[0]

    # EVERY READING FROM EVERY TOKEN, not just the ones that became a `value`.
    #
    # MEASURED while probing this module: reading
    # `'Date: 01/04/2026 Received: 05/04/2026'` under UNKNOWN produced a
    # refusal saying the characters state more than one date, with
    # `candidates` EMPTY - because each of the two dates was individually
    # ambiguous, so neither had a `value` to collect. A question that says
    # "there are two dates here" and then offers nothing to choose between is
    # not a question, and it contradicted this field's own definition three
    # screens up. Collecting `candidates` rather than `value` is what that
    # definition always said.
    standing = tuple(dict.fromkeys(day for one in readings for day in one.candidates))
    printed = ", ".join(dict.fromkeys(match[0] for _, match in tokens))
    return ReadDate(
        value=None,
        raw=text,
        input_format=FORMAT_SEVERAL,
        locale_assumed=locale,
        ambiguous=False,
        candidates=standing,
        why=(
            f"These characters state more than one date ({printed}) and the "
            "statements disagree, so nothing is read from them. Please confirm "
            "the date in YYYY-MM-DD format."
        ),
    )


def _one_reading(
    kind: str, match: re.Match[str], raw: str, locale: DateLocale
) -> ReadDate:
    """Dispatch on the shape that matched. Three shapes, three readers."""
    if kind == _KIND_YEAR_FIRST:
        return _year_first_reading(match, raw, locale)
    if kind == _KIND_NAMED:
        return _named_reading(match, raw, locale)
    return _numeric_reading(match, raw, locale)
