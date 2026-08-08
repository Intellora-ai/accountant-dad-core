"""Child 1 - deterministic bytes, and the two-file split.

"Same seed, byte-identical output" is only a claim you can check if the book
becomes bytes. This module is that step, and it is boring on purpose: JSON
Lines, keys sorted, no spaces, ASCII only, one trailing newline. Nothing here
consults a clock, a locale, a hash seed or a path.

**The two files are the point.** `vouchers.jsonl` is what a detector or a
person is allowed to see. `ground_truth.jsonl` is the answer key. Every voucher
record carries exactly the same keys whether it was corrupted or not, so the
voucher stream on its own says nothing about which entries were touched.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from accountant.generate.inject import Corruption, InjectedBook
from accountant.schema import Voucher

VOUCHERS_FILENAME = "vouchers.jsonl"
GROUND_TRUTH_FILENAME = "ground_truth.jsonl"

ENCODING = "utf-8"


@dataclass(frozen=True)
class BookFiles:
    """Where the two streams landed. Separate paths, never one file."""

    vouchers: Path
    ground_truth: Path


def voucher_record(voucher: Voucher) -> dict[str, object]:
    """Every field of the frozen type, always the same keys.

    A key that appeared only on corrupted vouchers would be a marker, so the
    record is field-complete and shape-identical for every entry, corrupt or
    clean.
    """
    return {
        "id": voucher.id,
        "date": voucher.date.isoformat(),
        "party": voucher.party,
        "narration": voucher.narration,
        "debit_account": voucher.debit_account,
        "credit_account": voucher.credit_account,
        "amount_paise": voucher.amount_paise,
        "gst_paise": voucher.gst_paise,
        "tally_id": voucher.tally_id,
        "provenance": voucher.provenance,
    }


def truth_record(corruption: Corruption) -> dict[str, object]:
    return {
        "voucher_id": corruption.voucher_id,
        "error_type": corruption.error_type,
        "field": corruption.field,
        "was": corruption.was,
        "now": corruption.now,
    }


def _lines(records: Iterable[dict[str, object]]) -> bytes:
    out = "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        for r in records
    )
    return out.encode(ENCODING)


def voucher_bytes(vouchers: Iterable[Voucher]) -> bytes:
    return _lines(voucher_record(v) for v in vouchers)


def truth_bytes(truth: Iterable[Corruption]) -> bytes:
    return _lines(truth_record(c) for c in truth)


def write_book(directory: Path, injected: InjectedBook) -> BookFiles:
    """Write the voucher stream and the answer key to two separate files."""
    directory.mkdir(parents=True, exist_ok=True)
    files = BookFiles(
        vouchers=directory / VOUCHERS_FILENAME,
        ground_truth=directory / GROUND_TRUTH_FILENAME,
    )
    files.vouchers.write_bytes(voucher_bytes(injected.vouchers))
    files.ground_truth.write_bytes(truth_bytes(injected.truth))
    return files
