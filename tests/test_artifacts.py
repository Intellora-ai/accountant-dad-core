"""Text the engine was sure about that was never on the page.

WHY THIS FILE EXISTS
--------------------
The engine reports 80-96 confidence on strings that are not the field they are
offered as. `field_confidence` cannot see it: it scores how well the CHARACTERS
were read, and these characters were read perfectly. What is wrong is the leap
from "these are the characters" to "this is the supplier".

MEASURED 2026-08-15 on the real corpus, engine confidence beside the text:

    ('|Certificati', 88)               a table rule glued onto a word
    'TNoIte Noe eTvan42'               a 5x7 bitmap font coming apart
    'cc tillentown, ha, Delle 7 9072'  a printed Allentown, PA letterhead

At 0.88 those clear `ASK_FLOOR` (0.70) and a person is asked whether
`|Certificati` is their supplier. This file is the guard on that.

MEASURED, and both halves matter: 8 of 12 measured artifacts caught, and
0 of 10 real supplier names lost. The four misses are listed with their reasons
in `CANNOT_CATCH` and asserted as misses, so the first figure cannot drift
upward by dropping the hard cases.

THE CONTROL SET IS THE POINT OF THIS FILE
------------------------------------------
Catching garbage is easy - refuse everything and the recall is perfect. The only
number that decides whether this is shippable is how many REAL supplier names it
throws away, so the real-name set below is drawn from what this corpus and this
repository actually produced, never invented to be easy:

    'SHARMA TRADERS'                            the repository's own fixture
    'SUNIL TRADING COMPANY'                     the positional fallback's fixture
    'QINGDAO JINZEPENG IMPORT AND EXPORT'       read by the IGNORECASE fix
    'NORTH BENGAL STATE TRANSPORT CORPORATION'  a real corpus read
    'JNO. M. GRAHAM.'                           a real corpus read
    'M/s Sharma Traders'                        the Indian prefix, in the repo
    'MRF'                                       a three-letter Indian brand

Two of those are deliberately hostile to the rules here: `M/s Sharma Traders`
carries a slash, which is page furniture on every other line of a bill; and
`MRF` is three letters, one under the too-short rule. If either is refused, the
rule that refused it is wrong and must be narrowed - a reader that cannot say
`M/s Sharma Traders` is worse than one that occasionally asks about
`|Certificati`.

WHAT THIS FILE ADMITS IT CANNOT DO
-----------------------------------
`Stozione` is in the measured artifact set and this file does NOT catch it. It
is a misread of the Italian `Stazione` and is an ordinary-looking word; telling
it from a real name needs a dictionary of real names, which this repository does
not have and must not invent. It is asserted as a MISS below, on purpose, so the
recall number here is the honest one. A test suite that quietly dropped its
hardest case would be reporting a score it did not earn.
"""

from __future__ import annotations

import pytest

from accountant.extract.artifacts import (
    REFUSED,
    ceiling_for,
    complaints_about,
    looks_like_an_artifact,
    said_about,
)

#: Every artifact string measured on the real corpus, with the reason it is one.
MEASURED_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("|Certificati", "a table rule glued to the front of a word, engine said 88"),
    ("TNoIte Noe eTvan42", "5x7 bitmap font coming apart, taken for a supplier"),
    ("TNoIte Noe eTvonas", "the same, on a second document"),
    ("Nolte Noe eTan6o", "the same, on a third"),
    ("ad", "engine noise offered as a party"),
    ("x.", "engine noise offered as a party"),
    ("Ny.", "engine noise offered as a party"),
    ("PAR*GsiEMINS DE FRR.", "a French railway masthead coming apart"),
)

#: Real names. Refusing any of these is a defect in this module, not in the name.
REAL_NAMES: tuple[str, ...] = (
    "SHARMA TRADERS",
    "SUNIL TRADING COMPANY",
    "QINGDAO JINZEPENG IMPORT AND EXPORT",
    "NORTH BENGAL STATE TRANSPORT CORPORATION",
    "JNO. M. GRAHAM.",
    "M/s Sharma Traders",
    "MRF",
    "Hotel Vishwanand",
    "A1 TRADERS",
    "SHOP 4 CEMENT SUPPLIERS",
)

#: The measured artifacts this module CANNOT catch, each with the reason. Every
#: one is asserted as a miss below, so the recall figure stays honest and cannot
#: drift upward by quietly dropping the hard cases.
#:
#: MEASURED: 8 of 12 measured artifacts caught, 0 of 10 real names lost.
CANNOT_CATCH: tuple[tuple[str, str], ...] = (
    (
        "Stozione",
        "a misread Italian `Stazione`. An ordinary-looking word - telling it "
        "from a real name needs a dictionary of real names, which this "
        "repository does not have and must not invent.",
    ),
    (
        "Qnme",
        "four Title-case letters with a vowel in them. Every rule that would "
        "catch it also refuses `MRF`, `TVS` and `IBM`, which are real Indian "
        "brands. The control set outranks the artifact set.",
    ),
    (
        "ag ans",
        "five lowercase letters in two short tokens, with vowels. A rule "
        "against all-lowercase short tokens would refuse `M/s` and any "
        "lowercase-printed firm name.",
    ),
    (
        "cc tillentown, ha, Delle 7 9072",
        "a printed Allentown, PA letterhead. It is an ADDRESS, not a mangled "
        "name - every character was read correctly. Nothing about its shape "
        "distinguishes it from a company name plus a street; separating those "
        "needs the label or the position, not the characters.",
    ),
)


# =============================================================================
# the control - real names must survive
# =============================================================================


@pytest.mark.parametrize("name", REAL_NAMES)
def test_a_real_supplier_name_is_never_called_an_artifact(name: str) -> None:
    """THE TEST THAT DECIDES WHETHER THIS SHIPS.

    Refusing everything would score perfectly on the artifact set and destroy
    the reader. `M/s Sharma Traders` (a slash) and `MRF` (three letters) are the
    two that press hardest on these rules and both must survive.
    """
    assert not looks_like_an_artifact(name), (
        f"{name!r} is a real supplier name and was refused because it "
        f"{complaints_about(name)}. Narrow the rule - a reader that cannot say "
        "this name is worse than one that occasionally asks about noise."
    )
    assert ceiling_for(name) is None
    assert said_about(name) == ""


# =============================================================================
# the measured artifacts
# =============================================================================


@pytest.mark.parametrize(("text", "why"), MEASURED_ARTIFACTS)
def test_every_measured_artifact_is_caught(text: str, why: str) -> None:
    """Each string here was produced by the engine off a real document at a
    confidence that cleared `ASK_FLOOR`."""
    assert looks_like_an_artifact(text), f"{text!r} not caught - {why}"
    assert ceiling_for(text) == REFUSED
    assert said_about(text).startswith("this does not look like")


@pytest.mark.parametrize(("text", "why"), CANNOT_CATCH)
def test_the_artifacts_this_module_admits_it_cannot_catch(text: str, why: str) -> None:
    """PINNED AS MISSES, DELIBERATELY.

    Each of these was measured coming off a real document and each defeats every
    rule here that does not also refuse a real supplier name. Asserting them as
    misses is what keeps the recall figure honest: a suite that quietly dropped
    its four hardest cases would report 8 of 8 instead of 8 of 12.

    If one of these ever starts being CAUGHT, that is good news and this list
    shrinks - but only as a deliberate change carrying its own measurement of
    what else the new rule refuses, never as a side effect.
    """
    assert not looks_like_an_artifact(text), (
        f"{text!r} is now caught. That may be an improvement - but re-run the "
        f"real-name control set before celebrating. It was a miss because: {why}"
    )


def test_the_measured_recall_is_what_the_docstring_claims() -> None:
    """The numbers in this file's docstring, asserted rather than typed once and
    left to rot. 8 of 12 measured artifacts caught, 0 of 10 real names lost."""
    caught = sum(1 for text, _ in MEASURED_ARTIFACTS if looks_like_an_artifact(text))
    missed = sum(1 for text, _ in CANNOT_CATCH if not looks_like_an_artifact(text))
    lost = sum(1 for name in REAL_NAMES if looks_like_an_artifact(name))

    assert (caught, missed, lost) == (8, 4, 0), (
        f"caught {caught}, missed {missed}, real names lost {lost}. "
        "Update the docstring in the same commit as the rule change."
    )


# =============================================================================
# the ceiling can only ever refuse
# =============================================================================


def test_the_answer_is_a_ceiling_and_never_a_floor() -> None:
    """The safety property, asserted rather than assumed. `freeocr._judge`
    applies this with `min`, so the only values that are safe here are 0.0 (a
    refusal) and `None` (no opinion). Anything in between would still be a
    ceiling; anything above what the engine said would be laundering."""
    for text, _ in MEASURED_ARTIFACTS:
        assert ceiling_for(text) == REFUSED
    for name in REAL_NAMES:
        assert ceiling_for(name) is None
    for text, _ in CANNOT_CATCH:
        assert ceiling_for(text) is None
    assert REFUSED == 0.0


def test_nothing_here_alters_a_single_character() -> None:
    """`AQUANCED` is a misread `ADVANCED` and this module must not know that.
    Correcting a value invents data. The only outputs are a bool, a float and
    sentences ABOUT the text."""
    for text, _ in MEASURED_ARTIFACTS:
        assert isinstance(looks_like_an_artifact(text), bool)
        assert isinstance(complaints_about(text), tuple)


def test_an_empty_or_blank_value_objects_to_nothing() -> None:
    """Absence is not an artifact. A field nobody read is already 0.0 with its
    own stated reason, and a second refusal here would replace that reason with
    a less useful one."""
    for blank in ("", "   ", "\n"):
        assert not looks_like_an_artifact(blank)
        assert complaints_about(blank) == ()


def test_the_reason_is_the_same_sentence_every_time() -> None:
    """A refusal whose wording moves between runs is a refusal nobody can pin,
    and this text reaches the durable action log."""
    for text, _ in MEASURED_ARTIFACTS:
        assert said_about(text) == said_about(text)
        assert complaints_about(text) == complaints_about(text)


def test_the_sentence_never_prints_the_whole_value() -> None:
    """Document contents are never logged - counts, field names and shapes only.
    One offending WORD is quoted because a person cannot act on "something was
    wrong"; the whole value never is."""
    long_artifact = "TNoIte Noe eTvan42"

    assert long_artifact not in said_about(long_artifact)
    assert said_about(long_artifact) != ""
