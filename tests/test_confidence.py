"""The confidence proxy: turning per-word OCR scores into a per-field number.

WHY THIS FILE EXISTS
--------------------
The decision bands are written per field - auto-post needs every field at 0.95
or better. A free OCR engine does not give per-field scores; Tesseract gives a
confidence per *word*, 0-100. Something has to bridge that, and the bridge must
be deterministic arithmetic rather than a learned weight, because a learned
weight is one more thing that can be wrong in a way nobody notices.

THE THREE RULES, AND WHY EACH ONE IS THE SHAPE IT IS
------------------------------------------------------
    field_confidence = min(word_conf)/100  x  format_valid  x  consistency

1. **min, not mean.** One misread digit ruins an amount. A mean of 0.99 and 0.40
   is 0.70, which still looks like a field worth asking about rather than one
   that is certainly wrong. The minimum surfaces the worst character, which is
   the only one that matters.

2. **Format validity is a HARD multiplier, not a penalty.** A date that will not
   parse, or an amount that is not whole paise, scores exactly 0.0 - not a
   reduced score. It is not a low-confidence value; it is not a value.

3. **Consistency is a hard multiplier too.** If net plus tax does not equal
   gross, both amounts go to 0.0 rather than being averaged into plausibility.
   Two numbers that contradict each other are not two-thirds right.

WHAT THIS PROXY CANNOT DO
--------------------------
It cannot detect a CONFIDENTLY misread value - Tesseract reporting 96 on a digit
it got wrong. That is failure mode F-02, and it is exactly why confidence alone
never authorises a post: the decision layer also requires every conservation law
to pass, the party to be known, and the period to be open.

CALIBRATION IS MEASURED, NOT ASSUMED. Whether 0.95 is the right band is a
question for the corpus run, not for this file. If the measurement says the band
is wrong, that is reported to the owner - thresholds are never retuned here to
make a metric pass.

NO OCR ENGINE IS INVOLVED IN THESE TESTS. Word confidences are supplied as
integers, so this file runs on a machine with no Tesseract installed at all.
"""

from __future__ import annotations

import pytest

from accountant.cage.confidence import (
    EXACT,
    field_confidence,
    looks_like_a_date,
    looks_like_paise,
)

# ---- min, not mean ----------------------------------------------------------


def test_the_weakest_word_sets_the_score() -> None:
    assert field_confidence((99, 40, 98), format_valid=True, consistent=True) == 0.40


def test_the_control_a_uniformly_strong_field_scores_high() -> None:
    """THE CONTROL. A proxy returning a constant would pass the test above and
    fail this one."""
    assert field_confidence((99, 97, 98), format_valid=True, consistent=True) == 0.97


def test_the_mean_is_not_used_and_this_is_the_case_that_proves_it() -> None:
    """0.99 and 0.40 average to 0.695, which reads as "worth a question". The
    minimum reads as "this is wrong", which it is."""
    scored = field_confidence((99, 40), format_valid=True, consistent=True)
    assert scored != pytest.approx(0.695)
    assert scored == 0.40


def test_a_single_word_field_scores_that_word() -> None:
    assert field_confidence((88,), format_valid=True, consistent=True) == 0.88


def test_a_field_with_no_words_scores_zero() -> None:
    """Nothing was read. Not a low score - no score."""
    assert field_confidence((), format_valid=True, consistent=True) == 0.0


def test_an_unread_field_scores_zero() -> None:
    assert field_confidence(None, format_valid=True, consistent=True) == 0.0


# ---- format validity is a hard multiplier -----------------------------------


def test_an_unparseable_value_scores_zero_however_clear_the_pixels_were() -> None:
    """Tesseract can be certain it read `12/34/5678`. That is not a date, and a
    high pixel confidence does not make it one."""
    assert field_confidence((99, 99), format_valid=False, consistent=True) == 0.0


def test_the_control_the_same_words_with_a_valid_format_score_high() -> None:
    """THE CONTROL on the test above."""
    assert field_confidence((99, 99), format_valid=True, consistent=True) == 0.99


# ---- consistency is a hard multiplier too -----------------------------------


def test_a_contradicted_value_scores_zero() -> None:
    """Net plus tax does not equal gross. Both amounts were read clearly and at
    least one of them is wrong; averaging them into plausibility would be the
    whole defect."""
    assert field_confidence((99, 99), format_valid=True, consistent=False) == 0.0


def test_both_multipliers_failing_still_scores_zero_not_negative() -> None:
    assert field_confidence((99,), format_valid=False, consistent=False) == 0.0


# ---- the exact tier ---------------------------------------------------------


def test_a_text_layer_read_is_exact() -> None:
    """A PDF text layer was not guessed, it was read. There is no pixel and no
    estimate, so there is nothing to be unsure about."""
    assert EXACT == 1.0


# ---- bad input raises rather than being coerced -----------------------------


def test_a_word_confidence_above_one_hundred_raises() -> None:
    """Tesseract reports 0-100. A number outside that means the caller passed
    something that is not a Tesseract score, and guessing which scale it is on
    would be inventing data."""
    with pytest.raises(ValueError):
        field_confidence((101,), format_valid=True, consistent=True)


def test_a_negative_word_confidence_raises() -> None:
    """Tesseract uses -1 for "no text here". That is a real value with a real
    meaning and it must not be silently read as a score."""
    with pytest.raises(ValueError):
        field_confidence((-1,), format_valid=True, consistent=True)


def test_a_float_word_confidence_raises() -> None:
    with pytest.raises(TypeError):
        field_confidence((99.5,), format_valid=True, consistent=True)  # type: ignore[arg-type]


# ---- the format checks themselves -------------------------------------------


def test_whole_paise_looks_like_paise() -> None:
    assert looks_like_paise("100000") is True


def test_a_decimal_string_does_not_look_like_paise() -> None:
    """Money is integer paise everywhere in this system. A decimal here means
    somebody read rupees and did not convert."""
    assert looks_like_paise("1000.00") is False


def test_letters_do_not_look_like_paise() -> None:
    assert looks_like_paise("1O0O00") is False


def test_an_empty_string_does_not_look_like_paise() -> None:
    assert looks_like_paise("") is False


def test_a_negative_amount_does_not_look_like_paise() -> None:
    assert looks_like_paise("-100") is False


def test_an_iso_date_looks_like_a_date() -> None:
    assert looks_like_a_date("2026-08-12") is True


def test_an_impossible_day_does_not_look_like_a_date() -> None:
    """The 34th is a misread 04th or 14th, not a date. This is one of the four
    forgery checks the product actually claims."""
    assert looks_like_a_date("2026-08-34") is False


def test_an_impossible_month_does_not_look_like_a_date() -> None:
    assert looks_like_a_date("2026-13-01") is False


def test_a_word_that_is_not_a_date_at_all_is_refused() -> None:
    assert looks_like_a_date("Invoice") is False


# ---- determinism ------------------------------------------------------------


def test_the_same_input_scores_the_same_twice() -> None:
    """Rule 11.2.10. Pure arithmetic has no excuse to vary, and a test saying so
    is what stops someone adding a cache or a clock later."""
    first = field_confidence((99, 40, 98), format_valid=True, consistent=True)
    second = field_confidence((99, 40, 98), format_valid=True, consistent=True)
    assert first == second
