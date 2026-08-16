"""One thing we read, how sure we are, how we found it, and where it was.

WHY THIS IS NOT A NEW `Field`
------------------------------
`cage/wall.py::Field` already holds a value, a confidence and a provenance
string, and it already enforces the three invariants that matter: a value of
`None` must carry confidence `0.0`, a confidence must sit inside `0.0`-`1.0`,
and the provenance must not be blank. Those rules exist because breaking any of
them lets a post happen on nothing at all.

So this type CONTAINS one rather than replacing it. `ReadField.field` is a real
`wall.Field` and every one of those invariants is enforced by the code that
owns them. What is added here is the three things the wall has no room for:

    method        HOW it was found - under a label, by shape, or worked out
    printed       WHAT was on the page, verbatim, before any conversion
    where         WHICH characters on which line, when the caller knows

`printed` is not decoration. `TOTAL: 1,23,456.00/-` becomes `12345600` paise,
and when somebody disputes that figure in three months the only useful evidence
is the characters the conversion started from. A record holding the answer and
not the input cannot be argued with, which sounds like a strength and is not.

WHY `method` MATTERS MORE THAN IT LOOKS
----------------------------------------
`BY_SHAPE` is the weak one and it is weak in a specific way. A GSTIN found by
shape was found because fifteen characters on the page happen to fit the GSTIN
pattern, and nothing said it was a GSTIN. A GSTIN found `UNDER_A_LABEL` was
printed after the word `GSTIN`. The second is evidence; the first is a
coincidence that is usually right.

Nothing here scores them differently - that would be a weight nobody measured.
It records which one happened so the decision layer, and the person, can.

NO CLOCK, NO IO, NO NETWORK.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from accountant.cage.wall import Field


class Method(StrEnum):
    """How a value came to be in this record."""

    #: Printed after a name this system knows - `INVOICE NO:`, `IGST`.
    #: The strong one: the document said what the value was.
    UNDER_A_LABEL = "under_a_label"

    #: Matched a pattern with nothing naming it. A fifteen-character run in the
    #: GSTIN shape, a sixty-four-character run in the IRN shape. Usually right,
    #: never stated by the document, and recorded as the weaker thing it is.
    BY_SHAPE = "by_shape"

    #: A column of a row under a header this system read. The header named the
    #: column and the row supplied the value.
    IN_A_COLUMN = "in_a_column"

    #: The line under a heading that named a party but carried no value of its
    #: own - `Bill To:` on one line and the customer on the next. POSITIONAL,
    #: which is why it is its own method and not folded into `UNDER_A_LABEL`:
    #: the heading said what the BLOCK is about and said nothing about that
    #: particular line, so a bill printing its address first reads an address
    #: as a name and nothing here can tell.
    BELOW_A_HEADING = "below_a_heading"

    #: Worked out from other values in this same record, never from a default.
    #: Nothing in this package computes a value that the document also states -
    #: see `validate.py`, where a disagreement is RECORDED and never resolved.
    WORKED_OUT = "worked_out"

    #: Nothing was found. The only method a `None` value may carry.
    NOT_READ = "not_read"


class Checked(StrEnum):
    """What validation concluded about this one field.

    Three and not two, for the reason `conservation.Verdict` has three: "we did
    not check this" and "we checked it and it is fine" are different facts, and
    a two-valued flag has to call one of them the other.
    """

    NOT_CHECKED = "not_checked"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class Where:
    """Which characters, on which line of the text that was handed in.

    `[start, end)`, half-open, indexing the line - the same convention
    `labels.py::Found` uses, deliberately, so a caller holding one can
    read the other without converting anything.
    """

    line: int
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.line < 0 or self.start < 0 or self.end < self.start:
            raise ValueError(
                f"a location must be a real half-open range on a real line, not "
                f"line {self.line} [{self.start}, {self.end}). A range nobody "
                "can index is worse than no range, because it looks like one."
            )


@dataclass(frozen=True)
class ReadField:
    """A value, its confidence and provenance, plus how and where it was found.

    Built through `read` or `unread`, never directly, so the two rules this
    type adds on top of `wall.Field` cannot be skipped:

      - a value of `None` carries `Method.NOT_READ` and `Checked.NOT_CHECKED`.
        A field nobody found cannot have been found under a label, and cannot
        have passed a check.
      - a value that IS there never carries `Method.NOT_READ`. The mirror of
        the same rule, and it is the one that catches a caller who filled a
        value in and forgot to say where it came from.
    """

    field: Field
    method: Method
    printed: str = ""
    where: Where | None = None
    checked: Checked = Checked.NOT_CHECKED

    def __post_init__(self) -> None:
        if self.field.value is None and self.method is not Method.NOT_READ:
            raise ValueError(
                f"a field with no value cannot have been found "
                f"{self.method.value.replace('_', ' ')}. Nothing was found."
            )
        if self.field.value is not None and self.method is Method.NOT_READ:
            raise ValueError(
                f"{self.field.value!r} is a value, so something found it. "
                "A value with no provenance cannot be explained to the person "
                "whose books it is about to change."
            )
        if self.field.value is None and self.checked is not Checked.NOT_CHECKED:
            raise ValueError(
                f"nothing was read here, so it cannot be {self.checked.value}. "
                "Not checked is not the same as checked and fine."
            )

    @property
    def value(self) -> object:
        """What we think it is, or `None` because nothing was found."""
        return self.field.value

    @property
    def confidence(self) -> float:
        """How sure the reader was, `0.0` to `1.0`. `0.0` means unread."""
        return self.field.confidence

    @property
    def source(self) -> str:
        """Which reader said so - the provenance `wall.Field` demands."""
        return self.field.source

    @property
    def read(self) -> bool:
        return self.field.value is not None

    def as_checked(self, verdict: Checked) -> ReadField:
        """The same field carrying a validation verdict. A new object.

        Validation never edits a value - `validate.py` says so at length - so
        the only thing this may change is the verdict, and it returns a copy
        rather than mutating, like every other result type here.
        """
        return ReadField(
            field=self.field,
            method=self.method,
            printed=self.printed,
            where=self.where,
            checked=verdict,
        )


def unread(source: str) -> ReadField:
    """The field for something nothing found. Confidence `0.0`, never `None`.

    `source` still has to say WHICH reader failed to find it, because "the text
    layer has no supplier line" and "the photograph reader has no supplier
    line" send a person to two different places.
    """
    return ReadField(
        field=Field(value=None, confidence=0.0, source=source),
        method=Method.NOT_READ,
    )


def read_as(
    value: object,
    *,
    confidence: float,
    source: str,
    method: Method,
    printed: str,
    where: Where | None = None,
) -> ReadField:
    """A field that was found. Every argument after the value is keyword-only.

    Positional arguments here would put `confidence` and a `float` next to each
    other at a call site, and a swapped pair of those is a confidence of `1.0`
    on a value nobody read. Keyword-only makes that unwritable rather than
    merely unlikely.
    """
    if value is None:
        raise ValueError(
            "read_as was handed None. Use `unread` - a field that was not found "
            "must carry confidence 0.0, and this call would have set it to "
            f"{confidence}."
        )
    return ReadField(
        field=Field(value=value, confidence=confidence, source=source),
        method=method,
        printed=printed,
        where=where,
    )
