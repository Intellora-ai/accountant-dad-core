"""How sure we are about one field, computed rather than received.

WHY THIS MODULE EXISTS
----------------------
The decision bands are written per field: auto-post needs every field at 0.95 or
better. A paid invoice model would hand us that number. A free OCR engine does
not - Tesseract reports a confidence per *word*, 0 to 100 - so something has to
bridge word to field.

That bridge is deterministic arithmetic, not a learned weight. A learned weight
is one more thing that can be quietly wrong, needs its own training data, and
cannot be read by a person deciding whether to trust a refusal.

THE FORMULA, AND WHY EACH PART IS THE SHAPE IT IS
---------------------------------------------------
    field_confidence = min(word_conf)/100  x  format_valid  x  consistency

**min, not mean.** One misread digit ruins an amount. A mean of 0.99 and 0.40 is
0.70, which reads as "worth asking about" rather than "certainly wrong". The
minimum surfaces the worst character, which is the only one that matters: a
field is exactly as trustworthy as its least legible part.

**Format validity is a hard multiplier, not a penalty.** A date that will not
parse, or an amount that is not whole paise, scores exactly 0.0 - not a reduced
score. It is not a low-confidence value; it is not a value. Tesseract can be
entirely certain it read `12/34/5678`, and that certainty is about pixels, not
about whether the thing is a date.

**Consistency is a hard multiplier too.** If net plus tax does not equal gross,
both amounts drop to 0.0 rather than being averaged into plausibility. Two
numbers that contradict each other are not two-thirds right.

WHAT THIS CANNOT DO, SAID SO NOBODY RELIES ON IT
--------------------------------------------------
It cannot detect a value the engine misread *confidently* - Tesseract reporting
96 on a digit it got wrong. That is failure mode F-02. It is why confidence
alone never authorises a post: the decision layer also requires every
conservation law to pass, the party to be known and the period to be open.

CALIBRATION IS MEASURED, NOT ASSUMED
--------------------------------------
Whether 0.95 is the right band is a question for the corpus run, not for this
file. If the measurement says the band is wrong, that is **reported to the
owner**. Thresholds are never retuned here to make a metric pass - the project
forbids it in `ARCHITECTURE.md:616` and the reason is that moving a threshold
moves the measurement, not the product.
"""

from __future__ import annotations

import datetime
from typing import Final

#: A text layer was read, not guessed. There is no pixel and no estimate, so
#: there is nothing to be unsure about. Named rather than written as a bare
#: `1.0` so the reason travels with the number.
EXACT: Final = 1.0

#: Tesseract's reporting range. `-1` is its "no text here" marker, which is a
#: real value with a real meaning and must never be read as a score.
WORST_WORD: Final = 0
BEST_WORD: Final = 100


def field_confidence(
    word_confidences: tuple[int, ...] | None,
    *,
    format_valid: bool,
    consistent: bool,
) -> float:
    """One field's score, between 0.0 and 1.0.

    `None` or an empty tuple means nothing was read for this field, which scores
    0.0 - not a low score, no score.
    """
    if not word_confidences:
        return 0.0
    for score in word_confidences:
        if type(score) is not int:
            raise TypeError(
                "a word confidence must be a whole number from "
                f"{WORST_WORD} to {BEST_WORD}, not {type(score).__name__}."
            )
        if not WORST_WORD <= score <= BEST_WORD:
            raise ValueError(
                f"a word confidence of {score} is outside {WORST_WORD}-"
                f"{BEST_WORD}. Tesseract reports -1 for 'no text here', which "
                "is a marker and not a score; guessing which scale a number "
                "outside the range is on would be inventing data."
            )
    if not format_valid or not consistent:
        return 0.0
    return min(word_confidences) / BEST_WORD


def looks_like_paise(text: str) -> bool:
    """Is this string a whole, non-negative number of paise?

    Not "can it be coerced to a number". A decimal point here means somebody
    read rupees and did not convert, and `1O0O00` with letter O's is the single
    commonest OCR error on a printed amount.
    """
    return text.isdigit()


def looks_like_a_date(text: str) -> bool:
    """Is this string a real calendar date in ISO form?

    The 34th of a month is a misread 04th or 14th, not a date. This is also one
    of the four narrow forgery checks the product actually claims - an
    impossible date is checkable, unlike a well-made forgery, which is not.
    """
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True
