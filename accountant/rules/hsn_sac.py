"""HSN and SAC codes, read literally and never approximated.

Owner decision Q2 = C, and the sentence that matters most in it:

    Every unseen code returns NOT_FOUND and is NEVER guessed from a similar code.

There is therefore no nearest-match, no prefix fallback, no chapter-level
default and no "did you mean". Those are the natural thing to reach for when a
lookup misses, and each of them would answer 2524 with 2523's rate — a
twenty-eight per cent tax on something nobody classified. The absence is the
feature; `tests/test_gst_rules_corpus.py` asserts it directly and by mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: Digits only, and only the lengths the tariff actually uses. 4 = heading,
#: 6 = sub-heading, 8 = tariff item.
_SHAPE = re.compile(r"^\d{4}(?:\d{2}(?:\d{2})?)?$")

#: The Scheme of Classification of Services sits in Chapter 99, so every service
#: code starts "99". A four-digit 99xx is the heading form the rate notifications
#: print ("Heading 9972"); six digits is the full SAC.
_SERVICE_CHAPTER = "99"


class CodeKind(StrEnum):
    HSN = "hsn"
    SAC = "sac"


@dataclass(frozen=True)
class Code:
    """A tariff code that passed the shape check. Not a code that exists."""

    value: str
    kind: CodeKind

    def __str__(self) -> str:
        return self.value


def normalise(raw: str | None) -> Code | None:
    """A `Code`, or None when the text is not one. Never a best effort.

    Spaces and dots are removed because they are typography, not information —
    "2523" and "25 23" are the same heading printed two ways. Nothing else is
    repaired: a code with a letter in it, or of a length the tariff does not
    use, comes back as None and the caller reports NOT_FOUND. Repairing it would
    mean choosing which digits the person meant.
    """
    if raw is None:
        return None
    # \u2013 and \u2014 are the en and em dash, written as escapes so the
    # source file carries no ambiguous character of its own.
    cleaned = re.sub("[\\s.\u2013\u2014-]", "", raw)
    if not _SHAPE.match(cleaned):
        return None
    kind = CodeKind.SAC if cleaned.startswith(_SERVICE_CHAPTER) else CodeKind.HSN
    return Code(value=cleaned, kind=kind)
