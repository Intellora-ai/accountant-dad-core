"""Which currency a bill printed, and which of its dots and commas is the point.

WHY THIS FILE EXISTS
--------------------
`labels.CURRENCY` is the whole of this repository's currency knowledge today,
and it is one regex::

    (?:RS\\.?|INR|GBP|₹|£)

Five spellings, no ISO code, no normalisation, and no way to say "these
characters could mean two things". Two separate failures come out of that, and
the second one is the reason this file exists rather than a widening of that
regex.

FAILURE ONE: THE SYMBOL IS UNKNOWN, SO THE LINE IS REFUSED
MEASURED 2026-08-15 by calling `labels.amount_on(line, "TOTAL")` on the ten
probe lines below. Eight of the ten come back `None` - not "unparseable", not
"unknown currency", but the same answer the function gives for a line that
states no amount at all::

    TOTAL Rp 39.600          None        TOTAL EUR 1.234,56      None
    TOTAL $18.79             None        TOTAL 1.234,56 EUR      None
    TOTAL US$ 18.79          None        TOTAL AED 250.00        None
    TOTAL USD 18.79          None        TOTAL JPY 1,234         None
    TOTAL INR 1,23,456.78    (12345678, 10, 21)
    TOTAL Rs. 1,234.56       (123456, 10, 18)

A refusal is safe. It costs a question to a person and it posts nothing wrong.

FAILURE TWO: THE SEPARATOR IS ASSUMED, AND THE ASSUMPTION MOVES MONEY
This one is not safe and it is live right now. `labels.paise_or_none` deletes
every comma as thousands grouping before it parses. MEASURED::

    labels.paise_or_none("163,16")            ->    1631600
    labels.amount_on("TOTAL 163,16", "TOTAL") -> (1631600, 6, 12)

`163,16` is how a German, French, Italian, Spanish or Dutch invoice writes one
hundred and sixty-three euro sixteen. This repository reads it as ₹16,316.00 -
a HUNDREDFOLD overstatement, posted silently, with the bill still looking read.
It is not a corner case. MEASURED over the 77 PDFs in `data/real_invoices` and
`data/real_invoices_indian` whose text layer `pypdf` can read (80 PDFs are
present; 3 are unreadable; the remaining 764 corpus files are photographs and
scans that need OCR and were not scanned for this): the shape
`<1-3 digits><comma><exactly 2 digits>` appears **557 times across 40 of those
77 documents**. Twenty-nine of the 77 print `€` and 18 print the code `EUR`;
42 print one or the other.

The mirror of it, from the direction the task brief names::

    labels.paise_or_none("39.600")            ->       3960

`Rp 39.600` is thirty-nine thousand six hundred rupiah. 3960 minor units is a
THOUSANDFOLD understatement. So the same missing piece of knowledge - which
mark is the decimal point - is worth a hundredfold in one direction and a
thousandfold in the other, and neither direction announces itself.

THE RULES, IN THE ORDER THEY ARE APPLIED
-----------------------------------------
Every rule below is convention-free except the fourth, and the fourth is the
only one that ever needs a currency code. Occurrence counts are MEASURED on the
same 77 readable text layers.

1. BOTH `.` AND `,` APPEAR -> the rightmost one is the decimal separator and
   the other groups. This holds in every convention on earth, because no
   convention uses one character for both jobs. Resolves `1.234,56`
   (32 occurrences, 7 documents) and `1,234.56` (12 occurrences, 3 documents).

2. ONE MARK, APPEARING MORE THAN ONCE -> if the digits after the LAST one
   number exactly three, all of them group (`1.234.567`, `1,23,456`). If they
   do not (`1.234.56`), the number is MALFORMED: the same character would have
   to be grouping in one place and the decimal point in another. Refused, not
   guessed.

3. ONE MARK, ONCE, WITH A NUMBER OF DIGITS AFTER IT THAT IS NOT THREE -> it is
   the decimal separator. Grouping is exactly three digits wide in every
   convention including the Indian one, whose narrow groups are the SECOND and
   later ones from the right (`12,34,567` ends in `567`), never the last.
   Resolves `4,55` (557 occurrences, 40 documents) and `4.55` (138 occurrences,
   19 documents) - 695 of the 711 single-separator occurrences on the corpus.

4. ONE MARK, ONCE, WITH EXACTLY THREE DIGITS AFTER IT -> genuinely ambiguous.
   `1.234` is one thousand two hundred and thirty-four, or it is one and
   two-hundred-and-thirty-four thousandths. Nothing in the characters decides
   it. A LEADING ZERO decides it - see below. Otherwise, if a currency code is
   stated, its home convention decides; if none is stated, `paise` is None and
   `ambiguous` is True. **This is the whole point of the module and it never
   guesses.**

   MEASURED, and the method matters because three people counting this have got
   three answers: the shape `<1-3 digits><. or ,><exactly 3 digits>`, bounded by
   `(?<![\\d.,])` and `(?![\\d.,])`, over the 77 readable text layers. SIXTEEN
   occurrences across 10 documents - 8 in 4 with a dot, 8 in 6 with a comma.

   A COUNT OF SHAPES IS NOT A COUNT OF AMOUNTS, and reading all 16 in context is
   the only reason this rule is defensible. SIX of the 16 are not money at all:
   `5,000 kg`, `24.150 kg`, `308,500 KG` twice and `0,750 M3` are weights and a
   volume, and `21,135` is the regex crossing from `14.8x21` into `135g` on a
   paper specification. Of the TEN that are money:

     * NINE are grouping - `$10,000` and `Rs. 25,000` (both fines), `(-) 77,305`
       (a credit note), `1.000` twice (`10% von 1.000`), and `62.000` four times
       (a German sum insured). Every one of them, no exceptions.
     * ONE is a decimal - `0.025 €` in `vendor-samples-042.pdf`, a unit price of
       twenty-five thousandths of a euro - AND IT CARRIES A LEADING ZERO.

   So the evidence for letting a stated code resolve rule 4 is not "three-decimal
   values do not occur"; they occur, mostly as weights. It is that every rule-4
   token on this corpus which is MONEY groups, except one, and that one announces
   itself with a zero in front of the mark. That zero is checked FIRST, before
   any convention is consulted, because no convention writes a grouped number
   whose first group has a leading zero. Without the check
   `read_money("0.025 €")` returned 2500 minor units - EUR 25.00 for a printed
   0.025 euro, with `ambiguous=False` and an empty `why`.

   WHAT THE GUARD DOES NOT COVER, said plainly: a three-decimal value with NO
   leading zero and a stated code whose convention calls that mark a grouping
   mark still reads as grouping. The measured shapes are `5,000 kg` in
   `vendor-samples-030.pdf` - five kilogrammes, since the line reads
   `5,000 kg 5,50 27,50` and five times 5,50 is 27,50 - and `308,500 KG` in
   `vendor-samples-043.pdf`, three hundred and eight and a half kilogrammes in
   `0,750 M3`. Handed over as the bare fragment `USD 5,000` the first reads
   500000 minor units. `EUR 5,000` refuses, because the euro calls a comma its
   decimal point and three digits will not fit in two.

   Nothing in those characters distinguishes them from a real `Rs. 25,000`, which
   is why rule 4 exists rather than a cleverer rule. The defence is that a weight
   is not an amount, and `labels.amount_on` is what decides which run of
   characters is the total. MEASURED by handing all 5248 non-blank corpus lines
   to `read_money`: 287 read to an amount, and rule 4 decided only FIVE of them -
   `Rs. 10,000` three times, `Rs. 25,000` and `Rs. 5,000`, all in
   `gst-portal-and-govt-001.pdf`, all five correct. The wrong readings surface on
   fragments, not on lines.

   `24.150 kg` on `vendor-samples-041.pdf` LOOKS like a counter-example and is
   not, which is worth recording because it was read as one. Its line prints
   `123,45 EUR per 1000 kg` and a total of `2.981,32 EUR`, and 24.15 times 123.45
   is 2981.32 - so the quantity is twenty-four thousand one hundred and fifty
   kilogrammes and the dot is grouping after all. The arithmetic on the line
   settled it; the shape alone could not.

WHY A CODE MAY DECIDE RULE 4 AND A SYMBOL MAY NOT
--------------------------------------------------
A symbol pins a code only when EVERY currency that prints it has the same
number of minor digits. That is the whole test, and it is a test about the
NUMBER rather than about the label:

  * `$` does not pass. USD, CAD, AUD and SGD have two minor digits; CLP has
    zero. A wrong guess there is not a mislabelled amount, it is an amount a
    hundredfold wrong. So `$` normalises to `UNKNOWN` with `ambiguous` True,
    exactly as the brief requires, and `US$` is a token that does pin USD.
  * `¥` does not pass, for the same reason and it is the same size: JPY has
    zero minor digits and CNY has two. Guarding the dollar and not the yen
    would be guarding the example instead of the defect.
  * `£`, `₹`, `€` and `Rs` do pass. Every currency that prints them carries two
    minor digits, so a wrong guess mislabels the amount and leaves the integer
    alone. That is a smaller defect than a wrong integer, it is still a defect,
    and it is named under WHAT THIS FILE DOES NOT PROVE rather than hidden.

"Unless something else in the text states it" is honoured: when the adjacent
symbol cannot pin a code, the whole text is scanned for unambiguous codes, and
if exactly one turns up and it is compatible with the symbol, that one is used.
`$18.79` is UNKNOWN; `$18.79 USD` is USD.

IDR HAS ZERO MINOR DIGITS HERE, AND THAT IS A CHOICE RATHER THAN A FACT
-----------------------------------------------------------------------
ISO 4217 assigns IDR two minor digits - the sen. This module records ZERO, and
it must be said plainly that this repository holds NO evidence either way:
MEASURED, there are zero `Rp` and zero `IDR` amounts in the 77 readable text
layers. So the choice rests on a stated principle, which is written here so it
can be argued with rather than discovered later:

    the minor-unit table records the smallest unit a bill can actually be
    DENOMINATED in, because that is the unit its digits are counting.

The sen was demonetised and no Indonesian invoice prints one. What follows is
tested rather than implied:

  * `Rp 39.600` is 39600 minor units, meaning 39,600 rupiah.
  * `Rp 1.234,56` is REFUSED with a reason naming the sen, because two
    fractional digits in a currency that has no fractional unit is evidence the
    separator was misread, and a refusal is a question while a guess is money.

If a real Indonesian bill ever proves otherwise, the change is one integer in
`MINOR_DIGITS` and the test that pins it.

CONTROL: THIS PARSER MUST AGREE WITH THE ONE ALREADY POSTING TO TALLY
----------------------------------------------------------------------
For every currency with two minor digits, the canonical `<whole>.<fraction>`
string this module builds is handed to `labels.paise_or_none` and the two
answers must be equal. If they are not, the amount is REFUSED and the reason
names both numbers.

This is the strongest kind of check available here: two quantities that must be
equal, computed by two independent routes - integer string concatenation on one
side, `Decimal` arithmetic on the other. It is deliberately NOT a second
implementation of the same idea; it is the boundary between this module's job
(decide which mark is the point) and that function's job (turn a canonical
rupee string into paise). It cannot run for JPY or IDR because
`paise_or_none` multiplies by exactly one hundred, and that limit is the reason
the zero-minor-digit path scales by string padding instead.

It has never fired. It exists so that the day somebody edits either parser, the
disagreement surfaces as a refusal here instead of as a wrong figure in a
ledger, and `tests/test_currency.py` fires it on purpose to prove it is wired.

NO FLOAT, ANYWHERE, AND A TEST THAT PROVES IT
----------------------------------------------
Minor units are produced by concatenating digit strings - `"18"` and `"79"`
become `int("18" + "79")` - so there is no multiplication by 100, no division
by 100, and no binary fraction anywhere near an amount. `tests/test_currency.py`
parses this file's own source with `ast` and asserts zero `Div` nodes, zero
`FloorDiv` nodes, no identifier named `float`, and no float literal.

NO NETWORK, NO CLOCK, NO FILESYSTEM, NO THIRD-PARTY IMPORT. Characters in, one
frozen answer out.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That `code` names the right currency for `£` or `Rs`. Egypt, Lebanon and Syria
print `£`; Pakistan, Sri Lanka, Nepal and Mauritius print `Rs`. All of them
carry two minor digits, so `paise` is right and the label may not be. `Rs` is
read as INR deliberately, because `labels._DECORATION` already reads it that
way and two modules disagreeing about `Rs` is the two-vocabularies defect this
file was written to end.

That rule 4 is right when a document's LOCALE disagrees with its currency's
home convention. A measured counter-example exists in the corpus:
`vendor-samples-067.pdf` prints `Steuerbetrag in GBP 163,16` - a German invoice
denominated in sterling, using a comma decimal. That line reads correctly here,
because rule 3 settles two trailing digits before any convention is consulted.
A document printing `GBP 1.234` would not, and there is no evidence in this
corpus that one exists.

That the amount it found is the amount the caller wanted. This module locates
money-shaped characters; `labels.amount_on` is what knows a total from a
subtotal. Handed characters stating more than one distinct amount, this module
refuses to choose - the same stance `labels.the_one` takes for the same reason.

That an amount it reads was read off the page correctly. That is the reading
engine's problem and the whole content of its confidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from accountant import labels
from accountant.extract.adapter import NOT_FOUND

# =============================================================================
# the table
# =============================================================================

#: What `code` says when this module will not name the currency. A sentinel and
#: not an empty string, because an empty string in a ledger export reads as "the
#: field was never filled in" and this one means "it was filled in with a
#: refusal". No real ISO 4217 code is three capitals plus four more letters, so
#: it can never collide with one.
UNKNOWN: Final = "UNKNOWN"

#: Symbol or code, uppercased, to normalised ISO 4217 code.
#:
#: PUBLIC AND READ-ONLY. Public because a caller deciding whether a line is
#: money-shaped needs the same set of spellings and a second copy of it is a
#: second answer to "what does a currency look like" - the defect this package
#: has already paid for twice. Read-only through `MappingProxyType` for the
#: reason the nine types in `schema.py` are frozen: a table a caller can edit at
#: runtime is not a table, it is a variable, and a currency that means something
#: different on the second bill of a batch is unfindable.
#:
#: `$` AND `¥` MAP TO `UNKNOWN` ON PURPOSE. They are the two entries where the
#: candidate currencies disagree about how many minor digits they have, so
#: guessing moves the integer rather than the label. See the module docstring
#: for the test that decides which symbols pin a code.
#:
#: `RS`/`RS.` MAP TO INR TO MATCH `labels._DECORATION`, which has always
#: stripped `RS.` from a rupee amount. Agreeing with it is the point.
CURRENCY_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        # India
        "INR": "INR",
        "INR.": "INR",
        "RS": "INR",
        "RS.": "INR",
        "₹": "INR",  # ₹ written as an escape: the glyph is a font gamble
        # United States - the code and the qualified symbol only
        "USD": "USD",
        "US$": "USD",
        # Euro area
        "EUR": "EUR",
        "€": "EUR",  # €
        # United Kingdom
        "GBP": "GBP",
        "£": "GBP",  # £
        # Indonesia
        "IDR": "IDR",
        "RP": "IDR",
        "RP.": "IDR",
        # Japan
        "JPY": "JPY",
        # United Arab Emirates
        "AED": "AED",
        # Shared symbols. Recognised, reported, never resolved on their own.
        "$": UNKNOWN,
        "¥": UNKNOWN,  # ¥ - JPY has 0 minor digits, CNY has 2
    }
)

#: Which ISO codes a shared symbol is ALLOWED to turn out to be, once something
#: else in the same characters states one. Keyed by the uppercased symbol.
#:
#: The point of the restriction is that a code standing elsewhere on a line must
#: not override the symbol standing beside the number. `€1.234,56 (USD rate
#: applied)` must not become USD; the euro sign already said what it is, and
#: `€` is not a key here at all because it pins EUR by itself.
_MAY_DENOTE: Final[Mapping[str, frozenset[str]]] = {
    "$": frozenset({"USD"}),
    "¥": frozenset({"JPY"}),
}

#: How many minor units make one whole unit, as a COUNT OF DIGITS rather than a
#: multiplier, because a digit count is what a string pad needs and a multiplier
#: is what a float would need.
#:
#: PUBLIC AND READ-ONLY, and it has to be public: `Money.paise` is minor units
#: of `Money.code`, so a caller showing that integer to a person, or handing it
#: to a ledger, CANNOT place the decimal point without this table. Keeping it
#: private would force every such caller to hard-code "divide by a hundred",
#: which is wrong for JPY and IDR and is the second vocabulary this package has
#: twice paid for. Read-only for the reason `CURRENCY_ALIASES` is.
#:
#: IDR IS 0 AND ISO 4217 SAYS 2. That is the single deliberate departure in this
#: table and the module docstring carries the argument for it, the fact that
#: this repository has zero IDR evidence, and the one integer to edit if a real
#: Indonesian bill ever settles it.
MINOR_DIGITS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "INR": 2,
        "USD": 2,
        "EUR": 2,
        "GBP": 2,
        "AED": 2,
        "IDR": 0,
        "JPY": 0,
    }
)

#: Which character the currency's HOME convention uses as the decimal point.
#: Consulted for exactly one thing - rule 4, a single separator with exactly
#: three digits after it - and never otherwise, so the measured counter-example
#: `Steuerbetrag in GBP 163,16` is settled by rule 3 before this table is read.
_DECIMAL_CHARACTER: Final[Mapping[str, str]] = {
    "INR": ".",
    "USD": ".",
    "GBP": ".",
    "AED": ".",
    "JPY": ".",
    "EUR": ",",
    "IDR": ",",
}

#: The two characters that can separate digits. Named because three places
#: below ask "is this one of them" and a literal `".,"` repeated three times is
#: three places to forget to change.
_MARKS: Final = ".,"

#: Grouping is this many digits wide, in every convention, in the LAST group.
#: The Indian lakh style narrows the second group and later (`12,34,567`), never
#: the one the decimal point would follow, which is why this constant is safe to
#: apply to Indian and European numbers alike.
_GROUP_WIDTH: Final = 3


# =============================================================================
# what was read
# =============================================================================


@dataclass(frozen=True)
class Money:
    """One amount and one currency read off a bill, or a refusal saying why not.

    FROZEN, for the reason `labels.Found` and the nine types in `schema.py` are
    frozen: a reading that can be edited after the fact is not evidence about
    what a document said.

    `paise` IS MINOR UNITS OF `code`, NOT OF THE RUPEE, and the field keeps the
    repository's name for minor units rather than inventing a second one. For
    INR it is paise; for USD, cents; for JPY and IDR it is whole yen and whole
    rupiah, because `MINOR_DIGITS` records zero for both. A caller converting
    between currencies must do that itself with a rate it can name - this module
    does no conversion and would be inventing a rate if it did, which is the
    same sentence `labels._DECORATION` carries.

    `raw` IS THE CHARACTERS, UNTOUCHED. When an amount was located it is the
    exact slice of the input that produced this reading, symbol included, with
    the spacing the document printed. When nothing was located it is the whole
    input, unchanged. It is never stripped, uppercased or normalised: it is the
    evidence, and evidence that has been tidied is no longer evidence.

    `symbol` IS WHAT WAS PRINTED and `code` IS WHAT IT NORMALISES TO. They are
    two fields rather than one because `$` and `USD` are the same code and very
    much not the same evidence, and because a person answering a question about
    this bill needs to see the characters they can go and look at.

    `ambiguous` IS TRUE WHEN THE CHARACTERS ADMIT MORE THAN ONE READING that
    this module will not choose between - an unpinnable currency, an unresolved
    separator, or more than one amount. It is FALSE when nothing money-shaped
    was found at all, because an absent amount is not an ambiguous one.

    `why` IS NON-EMPTY WHENEVER `paise` IS None OR `ambiguous` IS True, and the
    `__post_init__` below enforces that rather than trusting it. It carries the
    `adapter.NOT_FOUND` prefix when nothing usable was read, and plain prose
    when the amount WAS read and only the currency is in doubt - those are
    different facts and a caller routing on the prefix must be able to tell them
    apart.
    """

    paise: int | None
    raw: str
    symbol: str
    code: str
    ambiguous: bool
    why: str

    def __post_init__(self) -> None:
        """Refuse to exist in a state a caller could misread as certainty.

        THE GUARD IS THE POINT OF THE FIELD. A `Money` carrying `paise=None` and
        `why=""` says "no amount, no reason", which is the shape every silent
        failure in this repository has taken: `adapter.TYPED_TEXT_MIME` records
        twenty totals invented as zero, and each of them was a missing value
        that failed to say it was missing. Same for `ambiguous=True` with no
        reason - a flag nobody can act on is a flag nobody will act on.

        Raises rather than repairing, because a default sentence written here
        would be this module claiming to know something it does not, and because
        every construction site is inside this file: the only way to trip it is
        to write a new branch that forgot its reason, which is exactly when it
        should be loud.
        """
        if (self.paise is None or self.ambiguous) and not self.why:
            raise ValueError(
                "Money must state a reason whenever it has no amount or an "
                f"ambiguous one: got paise={self.paise!r}, "
                f"ambiguous={self.ambiguous!r}, why=''"
            )


# =============================================================================
# finding the characters
# =============================================================================

#: A run of digits with the marks that may sit inside it.
#:
#: STARTS AND ENDS ON A DIGIT, so the body handed to `_resolve` can never carry
#: a leading or trailing separator and every rule below can count digits without
#: first checking for one.
#:
#: THE LOOKBEHIND STOPS A MATCH STARTING MID-NUMBER. Without it `2026-08-15`
#: yields `2026`, then `08` starting after a mark that is part of a number, and
#: the count of amounts on the line becomes a function of where the regex
#: happened to resume.
#:
#: THE LOOKAHEAD IS `(?!\\d)` AND NOT `(?![\\d.,])`, WHICH IS ONE CHARACTER OF
#: DIFFERENCE AND A REAL LINE. Under the wider form `Total is 1,234.` matched
#: NOTHING - the sentence's full stop failed the lookahead at every backtrack -
#: so a bill that ended its total with a full stop reported no amount rather
#: than an amount. Under this form the match ends at the last digit and the
#: trailing mark is simply not part of it.
#:
#: THE SIGN ACCEPTS U+2212 AS WELL AS ASCII HYPHEN-MINUS. A minus sign set in a
#: typeface, or recovered by a reading engine, is often the real one, and a
#: credit note whose sign is dropped posts a refund as a charge. It is written
#: as an escape rather than as the glyph for the reason `labels._DECORATION`
#: writes the non-breaking space as one: the two characters are indistinguishable
#: in a diff, and a reviewer cannot approve what they cannot see.
_MINUS_SIGNS: Final = "-\u2212"
_NUMBER: Final = re.compile(rf"(?<![\d.,])([{_MINUS_SIGNS}]?)(\d[\d.,]*\d|\d)(?!\d)")


def _boundaried(token: str) -> str:
    """One alias as a pattern that cannot match inside a longer word.

    A letter alias needs the guard on whichever ends are letters: without it
    `RP` matches inside `CORP` and a corporation becomes Indonesian, and `INR`
    matches inside any word containing those three letters in a row. A symbol
    alias needs no guard - `$` is not a letter and cannot be part of one - and
    `US$` needs the guard only on its left, because its right end is the `$`.
    """
    pattern = re.escape(token)
    if token[0].isalpha():
        pattern = r"(?<![A-Za-z])" + pattern
    if token[-1].isalpha():
        pattern = pattern + r"(?![A-Za-z])"
    return pattern


#: Every alias, LONGEST FIRST. The ordering is load-bearing, not tidiness:
#: Python's alternation takes the first branch that matches at a position, so
#: with `$` before `US$` the token `US$` would read as a bare dollar sign and
#: lose the one piece of evidence that pins it to USD. Same for `RS.` before
#: `RS` and `INR.` before `INR`.
_ALTERNATION: Final = "|".join(
    _boundaried(token) for token in sorted(CURRENCY_ALIASES, key=len, reverse=True)
)

#: The alias standing immediately BEFORE the amount, if any. `\s*$` anchors it
#: to the end of the text preceding the number, so `CORP 1,234` finds nothing
#: while `Rs. 1,234` finds `Rs.`.
_ALIAS_BEFORE: Final = re.compile(rf"({_ALTERNATION})\s*$", re.IGNORECASE)

#: The alias standing immediately AFTER the amount. Real and measured:
#: `vendor-samples-024.pdf` prints `4,55 €` and `vendor-samples-025.pdf` prints
#: `70,00 EUR 518,99`, so a reader that only looked left would read 24 of the 77
#: corpus documents with no currency at all.
_ALIAS_AFTER: Final = re.compile(rf"^\s*({_ALTERNATION})", re.IGNORECASE)

#: Every alias anywhere in the characters, for the "unless something else in the
#: text states it" clause that lets `$18.79 USD` resolve while `$18.79` does not.
_ALIAS_ANYWHERE: Final = re.compile(_ALTERNATION, re.IGNORECASE)


def _candidates(text: str) -> list[re.Match[str]]:
    """Every money-shaped run in the characters, minus the percentages.

    A RATE IS NOT AN AMOUNT, and dropping it here is the same decision
    `labels._RATE` makes: `GST @ 18% 500.00` states one amount and one rate, and
    counting the rate as an amount would make this module refuse the line for
    stating two. Computing tax from a rate would be arithmetic on a number the
    bill already prints, which is why the rate is consumed and never read.
    """
    kept: list[re.Match[str]] = []
    for match in _NUMBER.finditer(text):
        if text[match.end() :].lstrip().startswith("%"):
            continue
        kept.append(match)
    return kept


def _currency(text: str, before: str, after: str) -> tuple[str, str, int, int]:
    """The symbol printed beside the amount, its code, and the symbol's span.

    Returns `(symbol, code, offset_before, offset_after)` where the two offsets
    say how far the symbol extends outside the number's own span, so the caller
    can cut `raw` to cover the characters a person would point at.

    THE SYMBOL BESIDE THE NUMBER WINS OVER A CODE ELSEWHERE. A line reading
    `€1.234,56 (USD rate applied)` is a euro amount, and letting the loose code
    override the adjacent symbol would turn a mention into a denomination. The
    elsewhere-scan runs ONLY when the adjacent symbol cannot pin a code on its
    own, and even then only within `_MAY_DENOTE`.
    """
    leading = _ALIAS_BEFORE.search(before)
    trailing = None if leading else _ALIAS_AFTER.search(after)
    if leading is not None:
        symbol = leading.group(1)
        reach = (len(before) - leading.start(1), 0)
    elif trailing is not None:
        symbol = trailing.group(1)
        reach = (0, trailing.end(1))
    else:
        symbol = ""
        reach = (0, 0)

    direct = CURRENCY_ALIASES[symbol.upper()] if symbol else UNKNOWN
    if direct != UNKNOWN:
        return symbol, direct, reach[0], reach[1]

    stated = {
        CURRENCY_ALIASES[found.group(0).upper()]
        for found in _ALIAS_ANYWHERE.finditer(text)
    }
    stated.discard(UNKNOWN)
    allowed = _MAY_DENOTE.get(symbol.upper()) if symbol else None
    if allowed is not None:
        stated &= allowed
    # ONE, OR NONE. Two distinct codes in the same characters is a document
    # this module cannot read, and picking either is the coin toss
    # `labels.the_one` refuses to make for the same reason.
    resolved = stated.pop() if len(stated) == 1 else UNKNOWN
    return symbol, resolved, reach[0], reach[1]


# =============================================================================
# deciding which mark is the point
# =============================================================================


@dataclass(frozen=True)
class _Split:
    """A number cut into its whole digits and its fractional digits.

    Both fields are BARE DIGITS - every grouping mark is already gone and the
    fraction never contains one, because the cut is made at the last mark in the
    body. `fraction` is `""` when the number stated no fractional part, which is
    a different fact from a fraction of zero and stays different: `Rp 39.600`
    states no fraction, and a currency with no fractional unit can hold it.
    """

    whole: str
    fraction: str


def _cut(body: str, at: int) -> _Split:
    """Split at the mark in position `at`, deleting the grouping marks before it.

    `body` starts on a digit by construction of `_NUMBER`, so `at` is never 0
    and `whole` is never empty.
    """
    whole = body[:at]
    for mark in _MARKS:
        whole = whole.replace(mark, "")
    return _Split(whole=whole, fraction=body[at + 1 :])


def _ungrouped(body: str) -> _Split:
    """Every mark in `body` groups; there is no fractional part."""
    whole = body
    for mark in _MARKS:
        whole = whole.replace(mark, "")
    return _Split(whole=whole, fraction="")


def _resolve(body: str, code: str) -> _Split | str:
    """Which mark is the decimal point, or a sentence saying it cannot be told.

    A `str` return is the refusal reason, which is why the return type is a
    union rather than an optional: `None` would leave the caller to invent a
    sentence, and an invented sentence is this module claiming to know why.

    The four rules are the module docstring's four rules in its order, and the
    comments below name which is which so the two cannot drift apart.
    """
    marks = [index for index, character in enumerate(body) if character in _MARKS]
    if not marks:
        return _ungrouped(body)

    last = marks[-1]
    digits_after = len(body) - last - 1

    # RULE 1 - both characters appear, so the rightmost is the decimal point.
    if len({body[index] for index in marks}) == 2:
        return _cut(body, last)

    # RULE 2 - one character, more than once.
    if len(marks) > 1:
        if digits_after == _GROUP_WIDTH:
            return _ungrouped(body)
        return (
            f"{NOT_FOUND}: '{body}' repeats '{body[last]}' but its last group is "
            f"{digits_after} digits wide rather than {_GROUP_WIDTH}, so that "
            "character would have to group in one place and mark the decimal "
            "point in another; no convention does that and this reader will not "
            "invent one"
        )

    # RULE 3 - one character, once, and the digits after it cannot be a group.
    if digits_after != _GROUP_WIDTH:
        return _cut(body, last)

    # RULE 4 - one character, once, with exactly three digits after it.
    #
    # A LEADING ZERO SETTLES IT BEFORE ANY CONVENTION IS CONSULTED, and this
    # guard is here because its absence was a MEASURED silent overstatement.
    # No convention writes a grouped number whose first group carries a leading
    # zero: `750` is written `750`, never `0,750`, and `01,750` is not how
    # anyone writes 1750. So a zero in front of the mark is proof the mark is
    # the decimal point, and the sub-minor check below then refuses the amount
    # rather than rounding it.
    #
    # THE DEFECT IT CLOSES. `vendor-samples-042.pdf` prints `19% 0.025 €` - a
    # unit price of twenty-five thousandths of a euro. Without this guard the
    # euro's home convention said `.` is not its decimal character, `0.025`
    # read as the grouped number 25, and `read_money("0.025 €")` returned 2500
    # minor units - EUR 25.00, a THOUSANDFOLD overstatement carrying
    # `ambiguous=False` and an empty `why`. Certainty is what made it dangerous:
    # a refusal costs a question, and that figure cost nobody anything to
    # believe. `vendor-samples-043.pdf` prints the other one, `Volume 0,750 M3`.
    if body[:last].startswith("0"):
        return _cut(body, last)

    if code == UNKNOWN:
        grouped = _ungrouped(body).whole
        decimal = body.replace(",", ".")
        return (
            f"{NOT_FOUND}: '{body}' states one '{body[last]}' with exactly "
            f"{_GROUP_WIDTH} digits after it and no currency code is resolved, "
            f"so it reads as {grouped} if that character groups thousands and as "
            f"{decimal} if it marks the decimal point; nothing in these "
            "characters decides between them and this reader will not guess"
        )
    if body[last] == _DECIMAL_CHARACTER[code]:
        return _cut(body, last)
    return _ungrouped(body)


# =============================================================================
# turning digits into minor units
# =============================================================================


def _minor_units(split: _Split, code: str, exponent: int) -> int | str:
    """The magnitude in minor units, or a sentence saying why it is refused.

    NO MULTIPLICATION AND NO DIVISION. The whole digits and the fraction padded
    to the currency's width are CONCATENATED and read as one integer, so
    `18` and `79` at two minor digits become `int("1879")`. There is no rounding
    step to get wrong, no float, and the arithmetic is identical at zero minor
    digits where a multiply by one hundred would be flatly wrong.

    REFUSES SUB-MINOR RATHER THAN ROUNDING, which is `labels.paise_or_none`'s
    rule restated for currencies that are not the rupee. `10.005` is refused for
    INR because there is no half-paise; `1.234,56` is refused for IDR because
    there is no sen in circulation and two fractional digits are evidence the
    separator was misread rather than a fact about the money.
    """
    if len(split.fraction) > exponent:
        return (
            f"{NOT_FOUND}: this amount states {len(split.fraction)} digits after "
            f"the decimal point and {code} is counted in "
            f"{exponent if exponent else 'whole'} "
            f"{'minor digits' if exponent else 'units with no minor unit'}; "
            "rounding it would move money, so it is refused and asked about"
        )
    return int(split.whole + split.fraction.ljust(exponent, "0"))


def _agrees_with_labels(magnitude: int, split: _Split, negative: bool) -> str:
    """CONTROL: the same figure through `labels.paise_or_none`, or a refusal.

    Returns `""` when the two agree and a sentence naming both numbers when they
    do not. Only ever called where the currency has two minor digits, because
    that function multiplies by exactly one hundred and cannot answer for JPY or
    IDR - which is stated here rather than left as a silent precondition.

    WHAT IT IS AND IS NOT. It is not a second implementation of this module's
    job; the canonical string handed over has ALREADY had its separator question
    settled here, and all `paise_or_none` does with it is the rupee arithmetic
    it already does for the PDF rung. It is the seam between the two jobs, and
    two independent routes to one integer that must be equal.

    Called through the module rather than through a `from ... import`, so the
    test that proves the control is wired can substitute the function and watch
    this refuse.
    """
    canonical = (
        ("-" if negative else "") + split.whole + "." + split.fraction.ljust(2, "0")
    )
    theirs = labels.paise_or_none(canonical)
    mine = -magnitude if negative else magnitude
    if theirs == mine:
        return ""
    return (
        f"{NOT_FOUND}: this reader makes '{canonical}' {mine} paise and "
        f"labels.paise_or_none makes it {theirs}; a money parser that disagrees "
        "with the one already posting to Tally is refused rather than trusted"
    )


def _exponent(code: str, split: _Split) -> int | str:
    """How many minor digits to scale by, or a sentence saying it is unknowable.

    WHEN THE CODE IS KNOWN the table answers and the document's own printing is
    not consulted, so `USD 18.7` is 1870 cents rather than 187 of something.

    WHEN THE CODE IS UNKNOWN the only evidence available is how many fractional
    digits the document itself printed, and exactly two is the only count this
    module will act on. The reasoning is arithmetic rather than taste: currencies
    in circulation carry zero, two or three minor digits, so

      * two printed digits can only be a two-minor-digit currency - `$18.79` is
        1879 minor units whether the dollar is American, Canadian or Singaporean,
        and that is why the brief's `$18.79` reads while its currency does not;
      * three could be a three-minor-digit currency (BHD, KWD, TND) at full
        precision OR a two-minor-digit one printed past its precision, and those
        differ by ten;
      * one, or four and up, matches no currency's precision at all;
      * none at all leaves nothing to go on - `$1,000` is 100000 minor units in
        USD and 1000 in CLP, both plausible, so it is refused.

    Refusing `$1,000` costs a question. Guessing it costs a hundredfold.
    """
    if code != UNKNOWN:
        return MINOR_DIGITS[code]
    if len(split.fraction) == 2:
        return 2
    return (
        f"{NOT_FOUND}: no currency code is resolved and this amount prints "
        f"{len(split.fraction)} digits after the decimal point, so this reader "
        "cannot say how many minor units make one whole unit; a currency and an "
        "amount are two facts and only one of them is here"
    )


# =============================================================================
# the one entry point
# =============================================================================


def read_money(text: str) -> Money:
    """One amount and one currency read out of `text`, or a stated refusal.

    NEVER RAISES AND NEVER RETURNS ZERO FOR A MISSING VALUE. Every failure comes
    back as a `Money` whose `paise` is None and whose `why` says what stopped
    it, because a fabricated zero is the defect `adapter.TYPED_TEXT_MIME`
    records twenty instances of and the one this repository refuses hardest.

    REFUSES TO CHOOSE BETWEEN AMOUNTS. Characters stating two different
    money-shaped runs come back ambiguous with both quoted, which is
    `labels.the_one`'s rule and the same reason: one of them is not the figure
    the caller wants, nothing here can say which, and picking the first is a
    coin toss that posts money. Repetition is not disagreement - a continuation
    sheet printing the same total twice reads once.
    """
    found = _candidates(text)
    if not found:
        return Money(
            paise=None,
            raw=text,
            symbol="",
            code=UNKNOWN,
            ambiguous=False,
            why=f"{NOT_FOUND}: nothing in '{text}' is shaped like an amount",
        )

    distinct = list(dict.fromkeys(match.group(0) for match in found))
    if len(distinct) > 1:
        return Money(
            paise=None,
            raw=text,
            symbol="",
            code=UNKNOWN,
            ambiguous=True,
            why=(
                f"{NOT_FOUND}: these characters state more than one amount "
                f"({', '.join(distinct)}) and this reader will not choose "
                "between them"
            ),
        )

    match = found[0]
    negative = bool(match.group(1))
    body = match.group(2)
    symbol, code, reach_left, reach_right = _currency(
        text, text[: match.start()], text[match.end() :]
    )
    raw = text[match.start() - reach_left : match.end() + reach_right]
    unpinned = (
        ""
        if code != UNKNOWN
        else (
            f"the currency is not stated: '{symbol}' is printed by more than one "
            f"currency and nothing else in these characters names a code, so the "
            f"code is {UNKNOWN}"
            if symbol
            else (
                "no currency symbol or code is printed beside this amount, so "
                f"the code is {UNKNOWN} and a caller must not assume rupees"
            )
        )
    )

    def refuse(reason: str) -> Money:
        """One refusal, carrying the currency doubt as well when there is one."""
        return Money(
            paise=None,
            raw=raw,
            symbol=symbol,
            code=code,
            ambiguous=True,
            why=f"{reason}; {unpinned}" if unpinned else reason,
        )

    split = _resolve(body, code)
    if isinstance(split, str):
        return refuse(split)

    exponent = _exponent(code, split)
    if isinstance(exponent, str):
        return refuse(exponent)

    magnitude = _minor_units(split, code, exponent)
    if isinstance(magnitude, str):
        return refuse(magnitude)

    if exponent == 2:
        disagreement = _agrees_with_labels(magnitude, split, negative)
        if disagreement:
            return refuse(disagreement)

    return Money(
        paise=-magnitude if negative else magnitude,
        raw=raw,
        symbol=symbol,
        code=code,
        ambiguous=bool(unpinned),
        why=unpinned,
    )
