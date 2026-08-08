"""The input contract — the narrowest shape this harness needs from a generator.

The synthetic book generator is a separate package and is built separately.
Nothing here imports it. This module states, once, exactly what the harness
consumes: a chart of accounts, prior history to learn from, the entries to
score, and a ground truth saying which of those entries were deliberately
broken and how.

Everything validates on construction. A ground truth that names an entry the
book does not contain is a wiring bug that would silently deflate the catch
rate, so it raises here rather than producing a confident wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass

from accountant.schema import Voucher

MAX_ERROR_RATE_PER_10_000 = 10_000


@dataclass(frozen=True)
class InjectedError:
    """One deliberately broken entry, and what was done to it.

    `error_type` is whatever string the generator uses. The harness groups by it
    and never interprets it, so the two packages share no vocabulary.
    """

    voucher_id: str
    error_type: str

    def __post_init__(self) -> None:
        if not self.voucher_id.strip():
            raise ValueError("injected error names no voucher")
        if not self.error_type.strip():
            raise ValueError(f"injected error on {self.voucher_id!r} names no type")


@dataclass(frozen=True)
class GroundTruth:
    """What the generator knows and the harness is not allowed to guess."""

    seed: int
    error_rate_per_10_000: int
    injected: tuple[InjectedError, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.error_rate_per_10_000 <= MAX_ERROR_RATE_PER_10_000:
            raise ValueError(
                f"error rate {self.error_rate_per_10_000} is outside "
                f"0..{MAX_ERROR_RATE_PER_10_000} per 10,000"
            )
        ids = [e.voucher_id for e in self.injected]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ValueError(
                f"one entry cannot carry two injected errors: {', '.join(repeated)}"
            )

    def by_voucher(self) -> dict[str, str]:
        """voucher id -> error type, for the entries that were broken."""
        return {e.voucher_id: e.error_type for e in self.injected}


@dataclass(frozen=True)
class Book:
    """One generated book, ready to score.

    `history` is the prior posted history the memory index learns from.
    `entries` are the entries being scored. They do not overlap.
    """

    company: str
    accounts: tuple[str, ...]
    history: tuple[Voucher, ...]
    entries: tuple[Voucher, ...]
    truth: GroundTruth

    def __post_init__(self) -> None:
        ids = [v.id for v in self.entries]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ValueError(f"duplicate entry ids in the book: {', '.join(repeated)}")

        unknown = sorted(set(self.truth.by_voucher()) - set(ids))
        if unknown:
            raise ValueError(
                f"ground truth names entries the book does not contain: "
                f"{', '.join(unknown)}"
            )

    @property
    def injected_count(self) -> int:
        return len(self.truth.injected)

    @property
    def clean_count(self) -> int:
        return len(self.entries) - self.injected_count
