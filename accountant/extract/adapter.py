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
    per_field_source: dict[str, str] = field(default_factory=dict)

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
    def extract(self, data: bytes, mime: str) -> ExtractedRecord: ...


# ---- backends ---------------------------------------------------------------

_AMOUNT = re.compile(r"(?:rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_GST_PCT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*gst|gst\s*@?\s*(\d{1,2}(?:\.\d+)?)\s*%", re.I)
# Party names keep "/" so the Indian "M/s Sharma Traders" form survives, and
# stop at a lowercase word so "paid Sharma Traders 4200 for cement" does not
# swallow "for cement".
_PARTY = re.compile(
    r"(?:from|to|paid|for)\s+((?:M/s\s+)?[A-Z][\w&./\-]*(?:\s+[A-Z][\w&./\-]*)*)", re.U
)


def _to_paise(text: str) -> int:
    return int(round(float(text.replace(",", "")) * 100))


class TypedTextExtractor:
    """A person typed a sentence. Pull the fields out of it.

    This is string parsing, not document reading. It never touches an image.
    """

    name = "typed_text"

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        text = data.decode("utf-8", errors="replace")
        src: dict[str, str] = {}

        amounts = _AMOUNT.findall(text)
        total = _to_paise(amounts[0]) if amounts else None
        src["total_paise"] = self.name if total is not None else NOT_FOUND

        tax = None
        m = _GST_PCT.search(text)
        if m and total is not None:
            pct = float(m.group(1) or m.group(2))
            # Amount typed is inclusive of GST: tax = total * pct / (100 + pct)
            tax = int(round(total * pct / (100 + pct)))
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
        self._values = {
            "date": date,
            "party": party,
            "total_paise": total_paise,
            "tax_paise": tax_paise,
        }

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        src = {
            k: (self.name if v is not None else NOT_FOUND)
            for k, v in self._values.items()
        }
        return ExtractedRecord(
            **self._values,
            raw_text=data.decode("utf-8", errors="replace"),
            backend=self.name,
            per_field_source=src,
        )


class UnavailableExtractor:
    """The backend is down. #15.7: every field comes back not_found with a
    stated reason, and the system carries on so the person can type instead."""

    name = "unavailable"

    def __init__(self, reason: str = "backend unreachable") -> None:
        self.reason = reason

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        return ExtractedRecord(
            date=None,
            party=None,
            total_paise=None,
            tax_paise=None,
            raw_text="",
            backend=self.name,
            per_field_source={f: f"{NOT_FOUND}: {self.reason}" for f in ExtractedRecord.FIELDS},
        )
