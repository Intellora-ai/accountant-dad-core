"""Text the engine was confident about and that was never on the page.

WHY THIS FILE EXISTS
--------------------
`cage/confidence.field_confidence` computes a field's score from the ENGINE'S
per-word confidence, gated on two booleans and taking the minimum word. That is
a good score for the question "did the engine read these characters correctly".
It is not an answer to "are these characters a supplier's name", and on a
photograph those two questions come apart.

MEASURED 2026-08-15 over the real corpus, engine confidence beside the text it
was reported for:

    ('|Certificati', 88)                  a table rule glued onto a word
    ('Stozione', 86)
    'cc tillentown, ha, Delle 7 9072'     a printed Allentown, PA letterhead
    'TNoIte Noe eTvan42'                  a 5x7 bitmap font coming apart
    'TNoIte Noe eTvonas'
    'Nolte Noe eTan6o'

Every one of those is a string the engine rated 80-96. `format_valid` is True
for all of them, because ANY string is a syntactically valid party name, and
`consistent` is True because there is nothing to disagree with. So they arrive
at `field_confidence` and come back around 0.88 - above `ASK_FLOOR` of 0.70.
A person is then asked "is |Certificati your supplier?" and one of their five
daily questions is gone.

That is failure mode F-02 with a number on it: a confident read of characters
that are not the thing they are being offered as.

WHAT THIS IS AND IS NOT
-----------------------
IT IS A CEILING, NEVER A FLOOR, and that is the whole of what makes it safe to
add. `ceiling_for` returns a number to pass through `Reading.at_most`, which
`freeocr._judge` applies with `min`. So this file can refuse a field and can
NEVER rescue one. Nothing here can raise a score, so nothing here can widen what
posts - which is the property `docs/ARCHITECTURE.md` calls the replacement rule:
an advisory layer is added, never subtracted.

IT IS NOT A SPELL CHECKER AND MUST NOT BECOME ONE. It never changes a character.
`AQUANCED` stays `AQUANCED`. Correcting a value invents data, and the record has
to say what the page said.

IT CANNOT CATCH EVERYTHING AND THE MEASUREMENT BELOW SAYS SO. `Stozione` is a
misread of the Italian `Stazione` and is a perfectly ordinary-looking word. No
rule in this file can tell it from a real name without a dictionary of real
names, which this repository does not have and should not invent. It is in the
measured set as a MISS, on purpose, so that the number in this docstring is the
honest one rather than the flattering one.

WHY NOT JUST RAISE THE CONFIDENCE FLOOR
----------------------------------------
Because the engine is RIGHT about these characters. It really did see a pipe
followed by `Certificati`. Lowering trust in the engine's character reading
would refuse the good reads along with the bad; what is wrong is the leap from
"these are the characters" to "this is the supplier", and that leap is what gets
a ceiling here.

`ARCHITECTURE.md:671` forbids tuning a threshold to make a metric pass, and this
adds no threshold to the bands: `AUTO_POST_FLOOR` and `ASK_FLOOR` are untouched.
"""

from __future__ import annotations

import re
from typing import Final

#: The score an artifact-looking value is capped at.
#:
#: 0.0 AND NOT A SMALL NUMBER. A value this file objects to is not a weak read,
#: it is a read of something that is not the field. A small non-zero number
#: would leave it eligible for a question, and the whole cost being removed here
#: is a person spending one of five questions on `Qnme`. Zero means the field
#: comes back unread with a stated reason, which is what "we found nothing you
#: can use" should look like.
#:
#: NOT A THRESHOLD ANYBODY TUNES. It is one end of a range, not a dial: the only
#: other value that would mean anything is 1.0, which is no ceiling at all.
REFUSED: Final = 0.0

#: Characters that are page FURNITURE - table rules, box drawing, stray marks -
#: rather than anything a name or an amount is printed with. Measured: the
#: leading `|` on `|Certificati` is a table rule the engine glued to the word.
#:
#: `/` AND `\` ARE DELIBERATELY ABSENT, and the control set is why. `M/s Sharma
#: Traders` is how an Indian bill prints a firm's name and `HSN/SAC` is on every
#: GST line; a rule that called a slash furniture refused the repository's own
#: party fixture. Measured: including `/` cost 1 real name of 10 and caught 0
#: extra artifacts.
_FURNITURE: Final = frozenset("|¦_=~^`*<>{}[]")

#: A word that is mostly letters but carries digits inside it. Measured on
#: `eTvan42` and `eTan6o`.
#:
#: FOUR CHARACTERS MINIMUM, AND THE CONTROL SET SET THAT NUMBER. `A1 TRADERS` is
#: a real name and `A1` is a letter and a digit run together, so a rule with no
#: length floor refused it. The measured artifacts are 6 and 7 characters long,
#: so 4 keeps every one of them and costs nothing. A bare `9072` never matches
#: at all - it is all digits, and an amount is not an artifact.
_LETTERS_WITH_DIGITS: Final = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{4,}$")

#: Case flipping INSIDE a word, at least twice. `TNoIte`, `eTvan`, `Nolte Noe`.
#: A printed name is upper, lower, or Title - it does not alternate. Two flips
#: rather than one, because `McDonald`, `M/s`, `iPhone` and every Indian
#: `MRF`-style abbreviation flip once quite legitimately.
_CASE_CHAOS: Final = re.compile(r"[a-z][A-Z].*?[a-z][A-Z]|[A-Z][a-z][A-Z]")

#: Fewer letters than this and there is no name in it. Measured refusals below
#: it: `ad`, `x.`, `Ny.`.
#:
#: THREE AND NOT FOUR, AND THE CONTROL SET SET THAT NUMBER TOO. `MRF` is a real
#: Indian brand and so are `TVS` and `IBM`; a floor of four refused all of them.
#: The cost of dropping to three is `Qnme`, which is four letters and is
#: therefore not caught by this rule at any setting that keeps `MRF` - it is in
#: the admitted-miss list rather than papered over.
_TOO_SHORT: Final = 3

#: A name has vowels. `ag ans` and `Qnme` do not, or barely. Applied only to the
#: alphabetic characters, and only when the string is short enough that the
#: ratio means anything - over a long string a low vowel count is real evidence,
#: under about eight characters it is noise.
_ENOUGH_TO_JUDGE_VOWELS: Final = 8
_VOWELS: Final = frozenset("AEIOUaeiou")


def _furniture_in(text: str) -> str:
    """Page furniture glued to the value. Measured: `|Certificati`."""
    found = sorted(set(text) & _FURNITURE)
    if not found:
        return ""
    return (
        f"carries page furniture ({''.join(found)}) rather than only the "
        "characters a name is printed with"
    )


def _digits_inside_words(text: str) -> str:
    """A word that is letters AND digits mixed. Measured: `eTvan42`, `eTan6o`."""
    culprits = [word for word in text.split() if _LETTERS_WITH_DIGITS.fullmatch(word)]
    if not culprits:
        return ""
    return f"has letters and digits run together in one word ({culprits[0]})"


def _case_flips(text: str) -> str:
    """Case alternating inside a word. Measured: `TNoIte`, `eTvonas`."""
    culprits = [word for word in text.split() if _CASE_CHAOS.search(word)]
    if not culprits:
        return ""
    return f"changes between capitals and small letters inside a word ({culprits[0]})"


def _too_short(text: str) -> str:
    """Nothing to read. Measured: `ad`, `x.`, `Ny.`."""
    letters = [character for character in text if character.isalpha()]
    if len(letters) >= _TOO_SHORT:
        return ""
    return f"holds {len(letters)} letters, too few to be a name"


def _no_vowels(text: str) -> str:
    """A name has vowels. Measured: `Qnme`, `ag ans`."""
    letters = [character for character in text if character.isalpha()]
    if len(letters) < _ENOUGH_TO_JUDGE_VOWELS:
        return ""
    if any(character in _VOWELS for character in letters):
        return ""
    return "has no vowels in it at all"


#: Every signal, in the order their reasons are reported. Order is fixed so the
#: sentence a person reads is the same for the same text on every run - a
#: refusal whose wording moves is a refusal nobody can pin.
_SIGNALS: Final = (
    _furniture_in,
    _digits_inside_words,
    _case_flips,
    _too_short,
    _no_vowels,
)


def complaints_about(text: str) -> tuple[str, ...]:
    """Every reason this text does not look like something printed on a bill.

    Empty means nothing objected. It does NOT mean the text is correct - see the
    module docstring on `Stozione`, which passes every rule here and is still a
    misreading. Absence of a complaint is absence of evidence.
    """
    if not text or not text.strip():
        return ()
    return tuple(said for signal in _SIGNALS if (said := signal(text.strip())))


def looks_like_an_artifact(text: str) -> bool:
    """Did anything object? One signal is enough.

    ONE AND NOT TWO, and the direction is the reason. This produces a CEILING,
    so a false positive costs a field that comes back unread and a person opens
    the bill - and a false negative costs one of that person's five daily
    questions spent on `|Certificati`. Requiring two signals would have missed
    `|Certificati` itself, which trips furniture and nothing else. Measured
    against the real-name control set in `tests/test_artifacts.py`, one signal
    costs zero real names.
    """
    return bool(complaints_about(text))


def ceiling_for(text: str) -> float | None:
    """The ceiling to put on a field carrying this text, or `None` for no ceiling.

    `None` and not 1.0. `freeocr._judge` treats `None` as "no ceiling stated" and
    leaves the source string as the bare backend name; a stated 1.0 would rename
    the source to a guess while changing no number, which would mark every clean
    read as guessed.
    """
    return REFUSED if looks_like_an_artifact(text) else None


def said_about(text: str) -> str:
    """One plain sentence for a person, or "" when nothing objected.

    NEVER REPEATS THE TEXT ITSELF. This sentence reaches the durable action log,
    and this repository's rule is that document contents are never logged -
    counts, field names and hashes only. The reasons name the SHAPE of the
    problem, and one of them quotes the offending word because a person cannot
    act on "something was wrong" - that quotation is the deliberate exception and
    it is a single word, never the whole value.
    """
    reasons = complaints_about(text)
    if not reasons:
        return ""
    if len(reasons) == 1:
        return f"this does not look like something printed on a bill: it {reasons[0]}"
    joined = "; it ".join(reasons)
    return f"this does not look like something printed on a bill: it {joined}"
