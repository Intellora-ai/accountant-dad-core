"""Masters — the ledgers, groups and items a voucher needs before it can exist.

WHY THIS MODULE HAS TO EXIST BEFORE ANY VOUCHER WORK
-----------------------------------------------------
Measured 2026-08-12 against the live TallyPrime: the company `TANVEER SIDHU`
answers `<LEDGER>0</LEDGER>`. It has no ledgers at all. Every voucher names at
least two, so until masters can be created, no voucher can be posted and no
report has anything to report on. This is the first blocking gap, not a
convenience.

WHY IT LIVES INSIDE `accountant/tallyio/`
------------------------------------------
`tests/test_write_door.py::test_nothing_outside_the_connector_builds_a_tally_import_envelope`
forbids `<TALLYREQUEST>Import` anywhere under `accountant/`, `ci/` or
`scripts/` EXCEPT inside this package. Creating a ledger is an Import envelope,
so this module belongs here and nowhere else. No allow-list entry was added and
no guard was widened: `ALLOWED` keys on `write_voucher` and
`reverse_by_operation_id`, and neither appears here.

WHY `urllib` AND NOT `requests`
--------------------------------
`pyproject.toml` declares `dependencies = []`, deliberately, and
`accountant/tallyio/real.py` reaches Tally with `urllib`. Importing `requests`
here would add the package's first runtime dependency to satisfy one module.
The transport is INJECTED anyway - this file opens no socket of its own and
takes a `Transport` whose only method is `send`.

A MASTER IS NOT A VOUCHER, AND IS NOT TREATED LIKE ONE
-------------------------------------------------------
`pipeline.post` guards voucher writes with a Valid-outcome gate, an
operation-id binding, a field-by-field read-back and a register check. None of
those apply to a ledger: there is no extraction to judge, no double entry to
balance, no trial balance to compare. What DOES apply is confirmation, so
`create_ledger` reads the master back by name after writing and reports
`confirmed=False` when Tally accepted the request but the ledger is not there.

`STATUS 1` IS NOT `IT WORKED`
------------------------------
Tally answers HTTP 200 with `STATUS 1` for requests it then declines, and puts
the complaint in `LINEERROR`. Every function here runs the body through
`errors.classify` before reporting success, and reports `created=0` as a
failure rather than as a quiet nothing.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That any of it has run against a licensed TallyPrime. `docs/PROJECT_STATE.md`
§46.4 states that everything so far is FAKETALLY, and this module does not
change that. Evidence class stays `EDUCATIONAL_TALLY` at best until B-01/B-02
are answered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final
from xml.sax.saxutils import escape

from accountant.tallyio import audit, errors, writedoor
from accountant.tallyio.real import (
    HttpTransport,
    TallyConfig,
    TallyResponseError,
    Transport,
    parse_xml,
    rupees_from_paise,
)

# ---------------------------------------------------------------------------
# what a master is
# ---------------------------------------------------------------------------

#: Tally's own group names, spelled exactly as Tally spells them. Wrong
#: spelling is the single most common master failure and Tally's message for it
#: is unhelpful, so the ones this system uses are written down here and checked
#: before a request is sent rather than after it is refused.
#:
#: NOT exhaustive - Tally ships around thirty. These are the groups an
#: automated bookkeeper actually posts to; anything else is passed through
#: untouched and Tally decides.
KNOWN_GROUPS: Final[frozenset[str]] = frozenset(
    {
        "Sundry Creditors",
        "Sundry Debtors",
        "Purchase Accounts",
        "Sales Accounts",
        "Direct Expenses",
        "Indirect Expenses",
        "Direct Incomes",
        "Indirect Incomes",
        "Bank Accounts",
        "Cash-in-Hand",
        "Duties & Taxes",
        "Current Assets",
        "Current Liabilities",
        "Fixed Assets",
    }
)

#: A ledger name Tally will not accept. Checked here so the refusal names the
#: character rather than arriving as an unexplained import failure.
_FORBIDDEN_IN_NAME: Final = re.compile(r'[<>"]')


@dataclass(frozen=True)
class MasterSummary:
    """One master as Tally reports it. `parent` is its group."""

    name: str
    parent: str = ""


@dataclass(frozen=True)
class MasterResult:
    """What happened when a master was created.

    `confirmed` is separate from `success` deliberately. `success` means Tally
    did not complain; `confirmed` means the master was afterwards found by
    name. Those came apart in this repository before - a write Tally accepted
    and did not keep - and collapsing them is how that goes unnoticed.
    """

    success: bool
    name: str
    confirmed: bool = False
    already_existed: bool = False
    raw_xml: str = ""
    request_xml: str = ""
    summary: str = ""
    error: errors.TallyError | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "name": self.name,
            "confirmed": self.confirmed,
            "already_existed": self.already_existed,
            "summary": self.summary,
            "error": self.error.as_dict() if self.error else None,
        }


@dataclass
class Problem:
    """One reason a request was not sent. Carries the code, never a bare string."""

    code: str
    said: str
    entity: str | None = None


@dataclass
class Validation:
    """The dry-run answer: is this sendable, and what would be sent."""

    ok: bool
    problems: list[Problem] = field(default_factory=list[Problem])
    would_send_xml: str = ""

    @property
    def errors_text(self) -> list[str]:
        return [f"{p.code}: {p.said}" for p in self.problems]


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def _envelope_import(company: str, body: str) -> str:
    """An Import envelope for masters, addressed to one company."""
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Import</TALLYREQUEST>"
        "<TYPE>Data</TYPE>"
        "<ID>All Masters</ID>"
        "</HEADER>"
        "<BODY><DESC><STATICVARIABLES>"
        f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        "</STATICVARIABLES></DESC>"
        f'<DATA><TALLYMESSAGE xmlns:UDF="TallyUDF">{body}</TALLYMESSAGE></DATA>'
        "</BODY></ENVELOPE>"
    )


def _envelope_collection(
    company: str, name: str, tally_type: str, methods: Iterable[str]
) -> str:
    """An Export envelope asking for every master of one type."""
    fetched = "".join(f"<NATIVEMETHOD>{m}</NATIVEMETHOD>" for m in methods)
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST>"
        "<TYPE>Collection</TYPE>"
        f"<ID>{escape(name)}</ID>"
        "</HEADER>"
        "<BODY><DESC>"
        "<STATICVARIABLES>"
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        "</STATICVARIABLES>"
        "<TDL><TDLMESSAGE>"
        f'<COLLECTION NAME="{escape(name)}" ISINITIALIZE="Yes">'
        f"<TYPE>{escape(tally_type)}</TYPE>{fetched}"
        "</COLLECTION>"
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY></ENVELOPE>"
    )


def ledger_xml(company: str, name: str, group: str, *, opening_paise: int = 0) -> str:
    """The exact Import XML for one ledger. Public so a dry run can show it.

    OPENING BALANCE IS INTEGER PAISE, converted to rupees only in the last
    step before the string. `accountant/extract/adapter.py` carries the measured
    case where float rupees put a number one paise adrift into a record, and a
    master's opening balance is the one number every later balance inherits.
    """
    opening = ""
    if opening_paise:
        # `real.rupees_from_paise`, NOT a second copy. This line used to be
        # `f"{opening_paise // 100}.{abs(opening_paise) % 100:02d}"`, and `//`
        # FLOORS: -125050 paise rendered as "-1251.50" instead of "-1250.50",
        # a whole rupee wrong, and -5 rendered as "-1.05" instead of "-0.05".
        # Positive values were fine, which is why nothing caught it until the
        # negative case was written down. One converter, in one place.
        rupees = rupees_from_paise(opening_paise)
        opening = f"<OPENINGBALANCE>{rupees}</OPENINGBALANCE>"
    return _envelope_import(
        company,
        f'<LEDGER NAME="{escape(name)}" ACTION="Create">'
        f"<NAME>{escape(name)}</NAME>"
        f"<PARENT>{escape(group)}</PARENT>"
        f"{opening}"
        "</LEDGER>",
    )


# ---------------------------------------------------------------------------
# validation, which is also the dry run
# ---------------------------------------------------------------------------


def validate_ledger(
    name: str, group: str, *, opening_paise: int = 0, company: str = ""
) -> Validation:
    """Everything checkable without asking Tally.

    Deliberately the SAME function the live path uses, so a dry run cannot
    drift from what actually happens. A dry run that disagrees with the real
    thing is worse than no dry run, because it gets believed.
    """
    problems: list[Problem] = []

    if not name.strip():
        problems.append(Problem("NAME_EMPTY", "a ledger needs a name"))
    if len(name) > 100:
        problems.append(
            Problem("NAME_TOO_LONG", f"Tally caps a ledger name; this is {len(name)}")
        )
    if _FORBIDDEN_IN_NAME.search(name):
        problems.append(
            Problem(
                "NAME_HAS_MARKUP",
                'a ledger name may not contain < > or ", because the request '
                "is XML and Tally reads those as structure",
                name,
            )
        )
    if not group.strip():
        problems.append(Problem("GROUP_EMPTY", "a ledger must sit under a group"))
    elif group not in KNOWN_GROUPS:
        problems.append(
            Problem(
                errors.GROUP_UNKNOWN,
                f"{group!r} is not one of the groups this system knows. Tally's "
                "own spelling is exact - 'Sundry Creditors', not 'Sundry "
                f"Creditor'. Known: {', '.join(sorted(KNOWN_GROUPS))}",
                group,
            )
        )
    # pyright calls this unnecessary because the annotation says `int`. It is
    # not: annotations are not enforced at runtime, and `create_ledger(...,
    # opening_paise=1000.0)` from a CSV row or an LLM tool-call reaches here as
    # a float. `accountant/extract/adapter.py` carries the measured case where
    # float rupees put a number one paise adrift into a record, and an opening
    # balance is the number every later balance inherits. bool is caught too:
    # `isinstance(True, int)` is True, and True would become one paise.
    if type(opening_paise) is not int:  # pyright: ignore[reportUnnecessaryIsInstance]
        problems.append(
            Problem(
                "OPENING_NOT_PAISE",
                f"an opening balance is whole paise as an int; got "
                f"{type(opening_paise).__name__}. A float here is how a rounded "
                "rupee reaches a real ledger.",
            )
        )

    return Validation(
        ok=not problems,
        problems=problems,
        would_send_xml=(
            ledger_xml(company, name, group, opening_paise=opening_paise)
            if not problems
            else ""
        ),
    )


# ---------------------------------------------------------------------------
# reading Tally's answer
# ---------------------------------------------------------------------------


def _counts(raw: str) -> dict[str, int]:
    """`<CREATED>1</CREATED>` and its siblings, as numbers.

    Tally reports what it did in these counters. `CREATED 0` with no complaint
    is the quiet case that matters: nothing was wrong and nothing happened, and
    reporting that as success is how an empty company looks populated.
    """
    out: dict[str, int] = {}
    for tag in ("CREATED", "ALTERED", "IGNORED", "ERRORS", "EXCEPTIONS"):
        found = re.search(rf"<{tag}>\s*(-?\d+)\s*</{tag}>", raw, re.I)
        if found:
            out[tag] = int(found.group(1))
    return out


def _names_from_collection(raw: str) -> list[MasterSummary]:
    """Every master in a collection response, with its parent group."""
    # `real.parse_xml`, not `ElementTree.fromstring`. That function is this
    # package's ONLY entry point to a parser, and it is hardened: it refuses a
    # DOCTYPE or ENTITY declaration, caps the response size, and repairs the
    # bare ampersands TallyPrime emits in party names. Calling ElementTree
    # directly here would reintroduce the XXE and billion-laughs exposure that
    # function exists to close, and would also fail on any supplier called
    # "Sharma & Sons".
    try:
        root = parse_xml(raw)
    except TallyResponseError as exc:
        raise errors.for_unusable_body(raw) from exc

    found: list[MasterSummary] = []
    for element in root.iter():
        if element.tag.upper() not in {"LEDGER", "STOCKITEM", "GODOWN", "COSTCENTRE"}:
            continue
        name = element.get("NAME") or ""
        if not name:
            child = element.find("NAME")
            name = (child.text or "").strip() if child is not None else ""
        parent = element.find("PARENT")
        found.append(
            MasterSummary(
                name=name.strip(),
                parent=(parent.text or "").strip() if parent is not None else "",
            )
        )
    return [m for m in found if m.name]


# ---------------------------------------------------------------------------
# the agent
# ---------------------------------------------------------------------------


class Masters:
    """Read and create masters in one company, over an injected transport."""

    def __init__(
        self,
        company: str,
        *,
        transport: Transport | None = None,
        config: TallyConfig | None = None,
        log: audit.JsonLineAuditLogger | None = None,
    ) -> None:
        if not company.strip():
            raise errors.TallyPolicyError(
                errors.COMPANY_NOT_OPEN,
                "no company was named. The gateway serves whichever company is "
                "open, so sending without naming one writes into whatever "
                "happens to be on screen.",
            )
        self.company = company
        self._transport = transport or HttpTransport(config or TallyConfig())
        self._log = log or audit.JsonLineAuditLogger()

    # -- reading ------------------------------------------------------------

    def list_ledgers(self, group: str | None = None) -> list[MasterSummary]:
        """Every ledger, or every ledger under one group."""
        raw = self._send(
            _envelope_collection(
                self.company, "AD List of Ledgers", "Ledger", ("NAME", "PARENT")
            )
        )
        found = _names_from_collection(raw)
        return [m for m in found if group is None or m.parent == group]

    def list_groups(self) -> list[MasterSummary]:
        raw = self._send(
            _envelope_collection(
                self.company, "AD List of Groups", "Group", ("NAME", "PARENT")
            )
        )
        return _names_from_collection(raw)

    def exists(self, name: str) -> bool:
        """Whether a ledger of exactly this name is present.

        Exact, not case-folded. Tally treats 'Purchase' and 'purchase' as one
        name but reports whichever spelling was created first, and a caller
        that matches loosely will believe it created something it did not.
        """
        return any(m.name == name for m in self.list_ledgers())

    # -- writing ------------------------------------------------------------

    def create_ledger(
        self,
        name: str,
        group: str,
        *,
        opening_paise: int = 0,
        dry_run: bool = False,
    ) -> MasterResult:
        """Create one ledger, then read it back to confirm it is really there."""
        check = validate_ledger(
            name, group, opening_paise=opening_paise, company=self.company
        )
        if not check.ok:
            first = check.problems[0]
            return MasterResult(
                success=False,
                name=name,
                request_xml=check.would_send_xml,
                summary=f"refused before sending: {first.said}",
                error=errors.TallyPolicyError(first.code, first.said, first.entity),
            )

        if dry_run:
            return MasterResult(
                success=True,
                name=name,
                request_xml=check.would_send_xml,
                summary=(
                    f"dry run: would create ledger {name!r} under {group!r}. "
                    "Nothing was sent."
                ),
            )

        # THE DOOR. Asked before any bytes leave, so a refusal sends nothing.
        writedoor.allow_write("create_ledger", self.company)

        with self._log.record(
            "create_ledger", company=self.company, name=name, group=group
        ) as entry:
            entry.request_xml = check.would_send_xml
            raw = self._send(check.would_send_xml)
            entry.response_xml = raw
            entry.status = "success" if not errors.classify(raw) else "failure"

        if failure := errors.classify(raw):
            return MasterResult(
                success=False,
                name=name,
                raw_xml=raw,
                request_xml=check.would_send_xml,
                summary=f"Tally refused {name!r}: {failure.message}",
                error=failure,
                already_existed=failure.code == errors.DUPLICATE_NAME,
            )

        counts = _counts(raw)
        created = counts.get("CREATED", 0)
        confirmed = self.exists(name)

        if created == 0 and not confirmed:
            return MasterResult(
                success=False,
                name=name,
                raw_xml=raw,
                request_xml=check.would_send_xml,
                summary=(
                    f"Tally reported no complaint and created nothing. {name!r} "
                    "is not in the company afterwards. Treated as a failure: a "
                    "silent nothing is the one outcome that gets believed."
                ),
                error=errors.TallyBusinessError(
                    errors.UNCLASSIFIED,
                    f"CREATED=0 with no error text. Counters: {counts}",
                    name,
                ),
            )

        return MasterResult(
            success=True,
            name=name,
            confirmed=confirmed,
            raw_xml=raw,
            request_xml=check.would_send_xml,
            summary=(
                f"Created ledger {name!r} under {group!r}"
                + ("" if confirmed else " - but read-back could not find it")
            ),
        )

    def ensure_ledger(self, name: str, group: str) -> MasterResult:
        """Create it only if absent. Safe to call repeatedly."""
        if self.exists(name):
            return MasterResult(
                success=True,
                name=name,
                confirmed=True,
                already_existed=True,
                summary=f"ledger {name!r} was already there; nothing sent",
            )
        return self.create_ledger(name, group)

    # -- transport ----------------------------------------------------------

    def _send(self, xml: str) -> str:
        """One request. Every failure leaves here as a `TallyError`."""
        try:
            raw = self._transport.send(xml, retry=False)
        except errors.TallyError:
            raise
        except (ConnectionError, TimeoutError, OSError) as unreachable:
            raise errors.TallyConnectionError(
                "UNREACHABLE",
                f"nothing answered at the Tally gateway: {unreachable}. Check "
                "Tally is running, a company is open, and the port is reachable.",
            ) from unreachable
        except Exception as unknown:
            raise errors.TallyRequestError(
                errors.UNCLASSIFIED,
                f"the transport failed in a way this code does not recognise: "
                f"{type(unknown).__name__}: {unknown}",
            ) from unknown
        if not raw.strip():
            raise errors.for_unusable_body(raw)
        return raw
