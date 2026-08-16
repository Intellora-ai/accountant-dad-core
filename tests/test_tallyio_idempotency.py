"""The duplicate guard on the write path, which nothing was holding down.

WHY THIS FILE EXISTS
--------------------
`Vouchers.create_*_voucher` asks TallyPrime whether a voucher already carries
this operation id before it writes one. That guard was built on 2026-08-12,
verified by hand, and shipped with NO TEST. Two mutants were run against the
suite the same day and BOTH SURVIVED:

    the duplicate lookup never finding anything  -> every retry posts again
    an unreadable probe reading as "not there"   -> a dropped packet posts again

Both put a second entry in a real company's books, and the suite stayed green
for both. The behaviour was real; nothing defended it. That is the same shape as
every defect in this repository's history - a thing that works today because
somebody checked it once by hand.

WHY THIS IS THE ONE WORTH DEFENDING
-------------------------------------
It is not hypothetical. `mvp_real_tally.py` ran twice against a live licensed
TallyPrime and the trial balance showed Rs 2,000 against `Test Supplier` for one
Rs 1,000 bill. The duplicate is still in those books. This guard is the fix, so
this file is the thing that keeps the fix.

NO NETWORK. The transports here answer reads with whatever the writes actually
put in, rather than replaying a canned sequence - a canned sequence would pass
even if the code asked entirely the wrong question.
"""

from __future__ import annotations

import pathlib

import pytest

from accountant.tallyio import audit, client, errors
from accountant.tallyio.vouchers import Vouchers

COMPANY = "TANVEER SIDHU"
PARTY = "Test Supplier"
AMOUNT_PAISE = 100_000
DATE = "12082026"

IMPORT_OK = (
    "<ENVELOPE><BODY><DATA><IMPORTRESULT>"
    "<CREATED>1</CREATED><LASTVCHID>1</LASTVCHID>"
    "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
)
NO_VOUCHERS = "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"


def books_holding(operation_id: str) -> str:
    """A company whose one voucher carries this id.

    The narration is built with `client.stamp` rather than typed out. A
    hand-written marker would drift the moment the stamp format changed, and the
    test would then pass by agreeing with itself instead of with the code.
    """
    return f"""<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER>
 <DATE>20260812</DATE>
 <VOUCHERNUMBER>1</VOUCHERNUMBER>
 <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
 <PARTYLEDGERNAME>{PARTY}</PARTYLEDGERNAME>
 <NARRATION>{client.stamp("a bill", operation_id)}</NARRATION>
 <ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>Purchase</LEDGERNAME>
  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
  <AMOUNT>-1000.00</AMOUNT>
 </ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>{PARTY}</LEDGERNAME>
  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
  <AMOUNT>1000.00</AMOUNT>
 </ALLLEDGERENTRIES.LIST>
</VOUCHER></COLLECTION></DATA></BODY></ENVELOPE>"""


class Books:
    """A company that remembers what was written into it.

    Reads answer with the voucher the last write actually posted. This matters:
    a transport returning a fixed "already there" response would let a guard
    that never looks at the id pass anyway.
    """

    def __init__(self) -> None:
        self.imports: list[str] = []
        self.posted_ids: list[str] = []
        # RECORDED, not ignored. A read that does not retry turns one dropped
        # packet into "this company is empty"; a WRITE that retries blindly is
        # how a voucher is posted twice.
        self.retries: list[bool] = []

    def send(self, payload: str, *, retry: bool = False) -> str:
        self.retries.append(retry)
        if "<TALLYREQUEST>Import" in payload:
            self.imports.append(payload)
            self.posted_ids.append(client.operation_id_in(payload) or "")
            return IMPORT_OK
        if not self.posted_ids:
            return NO_VOUCHERS
        return books_holding(self.posted_ids[-1])


class Deaf(Books):
    """Writes land; reads never answer. A gateway that half went away.

    This is the dangerous state, not an obscure one: the write side of a
    connection can be fine while a read times out.
    """

    def send(self, payload: str, *, retry: bool = False) -> str:
        self.retries.append(retry)
        if "<TALLYREQUEST>Import" in payload:
            self.imports.append(payload)
            return IMPORT_OK
        raise OSError("the gateway did not answer the read")


@pytest.fixture
def log(tmp_path: pathlib.Path) -> audit.JsonLineAuditLogger:
    return audit.JsonLineAuditLogger(tmp_path / "logs", tmp_path / "logs" / "xml")


def purchase(
    vouchers: Vouchers, operation_id: str
) -> object:  # VoucherResult, kept loose so a field rename fails on use not here
    return vouchers.create_purchase_voucher(
        DATE,
        PARTY,
        AMOUNT_PAISE,
        operation_id=operation_id,
        narration="a bill",
    )


# ---------------------------------------------------------------------------
# The duplicate lookup. Mutant: `if False` in place of the id comparison.
# ---------------------------------------------------------------------------


def test_the_same_operation_id_twice_sends_exactly_one_import(
    log: audit.JsonLineAuditLogger,
) -> None:
    """THE Rs 2,000 TEST. This is the defect that reached a real company.

    Counted on the wire, not read off the result. A result object can say
    whatever it likes; the number of Import envelopes is what the books see.
    """
    wire = Books()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    first = purchase(vouchers, "same-id")
    second = purchase(vouchers, "same-id")

    assert len(wire.imports) == 1
    assert first.already_posted is False  # type: ignore[attr-defined]
    assert second.already_posted is True  # type: ignore[attr-defined]


def test_two_different_operation_ids_send_two_imports(
    log: audit.JsonLineAuditLogger,
) -> None:
    """THE CONTROL on the test above.

    Without it, a write path that posts NOTHING passes the duplicate test
    perfectly - one import, zero imports, the assertion above cannot tell a
    working guard from a broken write.
    """
    wire = Books()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    purchase(vouchers, "bill-one")
    purchase(vouchers, "bill-two")

    assert len(wire.imports) == 2


def test_the_repeat_is_recognised_by_id_and_not_by_amount(
    log: audit.JsonLineAuditLogger,
) -> None:
    """Identity, not resemblance.

    An earlier version confirmed a write by looking for a matching AMOUNT, which
    cannot tell a voucher from its own duplicate. Two genuinely different bills
    for the same amount on the same day must BOTH post.
    """
    wire = Books()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    purchase(vouchers, "monday-bill")
    second = purchase(vouchers, "tuesday-bill")

    assert len(wire.imports) == 2
    assert second.already_posted is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Failing closed. Mutant: return None when the probe raises.
# ---------------------------------------------------------------------------


def test_a_probe_that_cannot_be_answered_writes_nothing(
    log: audit.JsonLineAuditLogger,
) -> None:
    """FAILS CLOSED, and this is the whole point of the guard.

    Reading "I could not look" as "it is not there" rebuilds the original defect
    exactly: a retry that cannot tell itself apart from a first attempt. The
    write side here works perfectly - so a guard that skipped the probe on error
    would post, and the count is what catches it.
    """
    wire = Deaf()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    refused = purchase(vouchers, "unanswerable")

    assert wire.imports == []
    assert refused.success is False  # type: ignore[attr-defined]
    assert refused.already_posted is False  # type: ignore[attr-defined]


def test_the_same_transport_posts_fine_once_reads_are_answered(
    log: audit.JsonLineAuditLogger,
) -> None:
    """THE CONTROL on the test above.

    Proves the refusal came from the unreadable probe and not from something
    else being broken in the write path. Same shape of transport, reads
    answered, one import.
    """
    wire = Books()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    purchase(vouchers, "answerable")

    assert len(wire.imports) == 1


def test_the_refusal_is_a_connection_error_and_not_a_complaint_about_the_data(
    log: audit.JsonLineAuditLogger,
) -> None:
    """A person reading the failure must not go looking at the wrong thing.

    "nothing answered" and "Tally refused your data" call for opposite next
    actions - fix the gateway, or fix the bill - and only one of them is safe to
    retry unchanged.

    THE KIND IS ASSERTED, not just the failure. Before 2026-08-12 this arrived
    as `real.TallyUnreachable`, from the CONNECTOR's exception tree rather than
    this module's, so `except errors.TallyError` did not catch it and
    `mvp_real_tally.py` printed a traceback where it promises a sentence.
    """
    wire = Deaf()
    vouchers = Vouchers(company=COMPANY, transport=wire, log=log)

    refused = purchase(vouchers, "unanswerable")
    problem = refused.error  # type: ignore[attr-defined]

    assert isinstance(problem, errors.TallyConnectionError)
    assert not isinstance(problem, errors.TallyBusinessError)
    # The code, because the words may be reworded and the code may not.
    assert problem.code == errors.UNREACHABLE
    # The summary tells the reader the retry is safe. That sentence is the
    # difference between a person retrying and a person entering it by hand.
    assert "nothing was sent" in refused.summary  # type: ignore[attr-defined]
