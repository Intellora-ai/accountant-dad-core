"""Child 15 — extraction adapter.

WE WRITE AN ADAPTER, NEVER A READER. Not one line of OCR, layout analysis or
field detection lives in this package. If a task here starts to look like reading
a document, it is the wrong task.

Two backends today:
  TypedTextExtractor  parses a line a person typed. Not OCR.
  StubExtractor       fixed answers, for testing everything downstream.

A real third-party reader plugs in behind the same Protocol later, and nothing
outside this package changes.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

NOT_FOUND = "not_found"


@dataclass(frozen=True)
class LineItem:
    description: str
    amount_paise: int


@dataclass(frozen=True)
class ExtractedRecord:
    """Every named field is present, always. A field is either a value or an
    explicit not_found. A silently blank field is a bug (S3)."""

    date: datetime.date | None
    party: str | None
    total_paise: int | None
    tax_paise: int | None
    line_items: tuple[LineItem, ...] = ()
    raw_text: str = ""
    backend: str = "unknown"
    per_field_source: dict[str, str] = field(default_factory=dict[str, str])

    FIELDS = ("date", "party", "total_paise", "tax_paise")

    def __post_init__(self) -> None:
        missing = [f for f in self.FIELDS if f not in self.per_field_source]
        if missing:
            raise ValueError(
                f"incomplete record: no source stated for {', '.join(missing)}"
            )

    @property
    def complete(self) -> bool:
        return all(f in self.per_field_source for f in self.FIELDS)


@runtime_checkable
class Extractor(Protocol):
    def extract(self, data: bytes, mime: str, /) -> ExtractedRecord: ...


# ---- backends ---------------------------------------------------------------

# `\.\d+`, not `\.\d{1,2}`. Capturing at most two decimals meant "10.005"
# matched as "10.00" and the half-paise was gone before any conversion could
# object to it. The truncation was in the pattern, not in the arithmetic.
_AMOUNT = re.compile(r"(?:rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)", re.I)
_GST_PCT = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*gst|gst\s*@?\s*(\d{1,2}(?:\.\d+)?)\s*%", re.I
)
# Party names keep "/" so the Indian "M/s Sharma Traders" form survives, and
# stop at a lowercase word so "paid Sharma Traders 4200 for cement" does not
# swallow "for cement".
_PARTY = re.compile(
    r"(?:from|to|paid|for)\s+((?:M/s\s+)?[A-Z][\w&./\-]*(?:\s+[A-Z][\w&./\-]*)*)", re.U
)


_HUNDRED = Decimal(100)


def _to_paise(text: str) -> int | None:
    """Exact paise, or None when the string is not an amount in paise.

    A1 / A2, FIXED 2026-08-09. This was `round(float(text.replace(",", "")) *
    100)`, and it was wrong in two different ways at once.

    Binary floating point cannot hold 0.07 rupees, so above roughly
    ₹99,999,999,999,999.99 the returned integer is simply a different number:
    "92233720368547.75" came back as 9223372036854776 paise, one paise adrift.

    And `round` turned sub-paise precision into silence. "10.005" became 1000
    paise, VALID, and posted. `tallyio.paise_from_rupees` REFUSES that same
    string and always has, because rounding invoice arithmetic is how a
    reconciliation breaks three months later. Two components, one rule,
    opposite behaviour, and the lenient one was the one a person's typing
    reached first.

    None rather than an exception: an unreadable amount is a question for the
    person, and `checks.amount_is_positive` already turns a missing total into
    one. Raising here would be a 500 in the web app for a typo.
    """
    try:
        rupees = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None

    scaled = rupees * _HUNDRED
    paise = int(scaled)
    return paise if scaled == paise else None


class TypedTextExtractor:
    """A person typed a sentence. Pull the fields out of it.

    This is string parsing, not document reading. It never touches an image.
    """

    name = "typed_text"

    def extract(self, data: bytes, _mime: str) -> ExtractedRecord:
        text = data.decode("utf-8", errors="replace")
        src: dict[str, str] = {}

        amounts = _AMOUNT.findall(text)
        total = _to_paise(amounts[0]) if amounts else None
        src["total_paise"] = self.name if total is not None else NOT_FOUND

        tax = None
        m = _GST_PCT.search(text)
        if m and total is not None:
            # Decimal, not float. 18% of ₹1,180 is exactly ₹180; in binary
            # floating point it is 179.99999999999997, and `round` hides that
            # until the amount where it does not - at which point the tax and
            # the net no longer sum to the total the person typed.
            pct = Decimal(m.group(1) or m.group(2))
            # Amount typed is inclusive of GST: tax = total * pct / (100 + pct)
            tax = int((Decimal(total) * pct / (_HUNDRED + pct)).to_integral_value())
        src["tax_paise"] = self.name if tax is not None else NOT_FOUND

        pm = _PARTY.search(text)
        party = pm.group(1).strip() if pm else None
        src["party"] = self.name if party else NOT_FOUND

        src["date"] = NOT_FOUND  # typed text rarely carries one; never guessed

        return ExtractedRecord(
            date=None,
            party=party,
            total_paise=total,
            tax_paise=tax,
            raw_text=text,
            backend=self.name,
            per_field_source=src,
        )


class StubExtractor:
    """Fixed answers. Lets the whole pipeline be tested with no real reader.

    Slice 5 wires this in before any third-party backend is chosen.
    """

    name = "stub"

    def __init__(
        self,
        date: datetime.date | None = None,
        party: str | None = None,
        total_paise: int | None = None,
        tax_paise: int | None = None,
    ) -> None:
        self.date = date
        self.party = party
        self.total_paise = total_paise
        self.tax_paise = tax_paise

    def extract(self, data: bytes, _mime: str) -> ExtractedRecord:
        # Named fields rather than a dict, so each keeps its own type. A dict
        # collapses them into one union and the record can no longer be built
        # without the type checker guessing.
        supplied: dict[str, object] = {
            "date": self.date,
            "party": self.party,
            "total_paise": self.total_paise,
            "tax_paise": self.tax_paise,
        }
        src = {
            k: (self.name if v is not None else NOT_FOUND) for k, v in supplied.items()
        }
        return ExtractedRecord(
            date=self.date,
            party=self.party,
            total_paise=self.total_paise,
            tax_paise=self.tax_paise,
            raw_text=data.decode("utf-8", errors="replace"),
            backend=self.name,
            per_field_source=src,
        )


class UnavailableExtractor:
    """The backend is down. #15.7: every field comes back not_found with a
    stated reason, and the system carries on so the person can type instead.

    `name` is settable, 2026-08-10. This class is now the ONE place that builds
    an outage record — `ServiceExtractor` hands its own failures here rather
    than restating the shape — and a row that cannot say WHICH backend was down
    is not usable as evidence about any of them. The default is unchanged, so
    every existing caller reads exactly as before.
    """

    name = "unavailable"

    def __init__(
        self, reason: str = "backend unreachable", *, name: str = "unavailable"
    ) -> None:
        self.reason = reason
        self.name = name

    def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
        return ExtractedRecord(
            date=None,
            party=None,
            total_paise=None,
            tax_paise=None,
            raw_text="",
            backend=self.name,
            per_field_source=dict.fromkeys(
                ExtractedRecord.FIELDS, f"{NOT_FOUND}: {self.reason}"
            ),
        )
