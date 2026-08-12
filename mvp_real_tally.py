#!/usr/bin/env python3
"""The MVP, end to end, against a real TallyPrime. No fakes, no mocks.

    ledgers exist  ->  post a Purchase  ->  read it back out of the books

Every write asks `accountant.tallyio.writedoor` first, and every operation -
read or write - writes one JSON line and two raw XML files to `logs/`.

    python3 mvp_real_tally.py

Settings, all overridable from the environment:

    ACCOUNTANT_TALLY_HOST      default 127.0.0.1
    ACCOUNTANT_TALLY_PORT      default 9000
    ACCOUNTANT_COMPANY         default "TANVEER SIDHU"
    ACCOUNTANT_LOG_DIR         default logs/
    ACCOUNTANT_POSTING_ENABLED set 0 to make every write refuse
    ACCOUNTANT_SAFE_MODE       destructive writes need confirm=True

WHAT THIS PROVES, AND WHAT IT DOES NOT
---------------------------------------
It proves the chain works against a live gateway. It does NOT prove the GST
engine, the rules corpus, or anything about a document reader - none of which
this script touches. `docs/PROJECT_STATE.md` §46.4 says every earlier result was
FAKETALLY; this run is the first that is not, and it is labelled
`LICENSED_REALTALLY` only because the owner confirmed the licence on 2026-08-12.
"""

from __future__ import annotations

import datetime
import os
import sys

from accountant.tallyio import audit, errors, writedoor
from accountant.tallyio.masters import Masters
from accountant.tallyio.real import HttpTransport, TallyConfig
from accountant.tallyio.reports import Reports
from accountant.tallyio.vouchers import PURCHASE_LEDGER, Vouchers

COMPANY = os.environ.get("ACCOUNTANT_COMPANY", "TANVEER SIDHU")
HOST = os.environ.get("ACCOUNTANT_TALLY_HOST", "127.0.0.1")
PORT = int(os.environ.get("ACCOUNTANT_TALLY_PORT", "9000"))

PARTY = "Test Supplier"
AMOUNT_PAISE = 100_000  # Rs 1,000.00. Integer paise, never float rupees.

#: The masters a Purchase needs. Party first, because a supplier who does not
#: exist is the failure a person meets most often.
NEEDED: tuple[tuple[str, str], ...] = (
    (PARTY, "Sundry Creditors"),
    (PURCHASE_LEDGER, "Purchase Accounts"),
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    when = datetime.date.today()
    stamp = when.strftime("%d%m%Y")

    print(f"Tally     http://{HOST}:{PORT}/")
    print(f"Company   {COMPANY!r}")
    print(f"Date      {when:%d-%m-%Y}  (sent as {when:%Y%m%d})")
    print(f"Posting   {'ON' if writedoor.posting_enabled() else 'OFF'}")

    config = TallyConfig(host=HOST, port=PORT)
    transport = HttpTransport(config)
    log = audit.JsonLineAuditLogger()

    masters = Masters(COMPANY, transport=transport, log=log)
    vouchers = Vouchers(company=COMPANY, transport=transport, log=log)
    reports = Reports(company=COMPANY, transport=transport, log=log)

    # -- 1. masters ---------------------------------------------------------
    rule("1. Masters")
    for name, group in NEEDED:
        result = masters.ensure_ledger(name, group)
        if not result.success:
            print(f"  FAILED  {name!r}: {result.summary}")
            return 1
        state = "already there" if result.already_existed else "created"
        print(f"  ok      {name!r} under {group!r} - {state}")

    # -- 2. the voucher -----------------------------------------------------
    rule("2. Purchase voucher")
    # DERIVED FROM THE DAY, NOT FRESH PER RUN, AND THAT IS THE WHOLE POINT.
    #
    # An id generated per run makes every re-run post another Rs 1,000 into a
    # real company. That is not hypothetical: this script ran twice on
    # 2026-08-12 and the trial balance showed Rs 2,000 against Test Supplier
    # for one bill. A derived id makes the second run find the first run's
    # voucher and report `already_posted` instead of writing again.
    #
    # The day is the unit because the entry is dated to the day. Two genuinely
    # different bills on one day would need two ids - which is exactly the
    # question a real caller has to answer, and a demo script does not.
    operation_id = f"ad_mvp_{stamp}_{AMOUNT_PAISE}"

    posted = vouchers.create_purchase_voucher(
        stamp,
        PARTY,
        AMOUNT_PAISE,
        operation_id=operation_id,
        narration="MVP end-to-end run",
    )
    print(f"  {posted.summary}")
    if posted.already_posted:
        print(f"  (id {operation_id!r} was already in the books - nothing sent)")
    if not posted.success:
        print(f"  error   {posted.error.as_dict() if posted.error else None}")
        print(f"  raw     {posted.raw_xml[:300]}")
        return 1
    if not posted.confirmed:
        # Not a hard failure: the entry may be there and the read-back may have
        # asked the wrong question. Reported loudly rather than smoothed, because
        # "posted" and "confirmed" are different facts.
        print("  WARNING read-back did not find it; see the Day Book below")

    # -- 3. read it back ----------------------------------------------------
    rule("3. The books, read back")
    ledger = reports.ledger_vouchers(PARTY, when, when)
    print(f"  {PARTY!r} has {len(ledger.entries)} voucher(s) on {when:%d-%m-%Y}")
    for entry in ledger.entries:
        print(
            f"    {entry.date or '?'}  {entry.voucher_type or 'Purchase':10}"
            f"  {entry.amount_paise / 100:>12,.2f}  {entry.narration}"
        )

    book = reports.day_book(when, when)
    print(f"  Day Book shows {len(book.entries)} entry(ies) for the same day")

    ours = [e for e in ledger.entries if e.amount_paise == AMOUNT_PAISE]
    print(
        f"\n  our voucher is {'PRESENT' if ours else 'NOT FOUND'} in {PARTY!r}'s ledger"
    )

    # -- 4. the audit trail -------------------------------------------------
    rule("4. Audit")
    stats = audit.summary()
    print(f"  {stats['operations_count']} operation(s) logged to {log.path}")
    # Narrowed rather than cast. `summary()` is typed `dict[str, object]`, and
    # `float(...)` on an object that is not a number raises INSIDE the audit
    # section - which would crash the script after the voucher had already been
    # written, reporting failure for a run that succeeded.
    rate = stats["success_rate"]
    print(f"  success rate {rate:.0%}" if isinstance(rate, float) else f"  {rate!r}")
    print(f"  raw XML under {audit.xml_dir()}")

    return 0 if ours else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except errors.TallyError as refused:
        # Every failure this system understands arrives here with a code and a
        # sentence. Printed, never a traceback: a traceback is for a bug in this
        # code, and these are answers from Tally.
        print(f"\nREFUSED  {refused.code}: {refused.message}", file=sys.stderr)
        if refused.entity:
            print(f"         about: {refused.entity}", file=sys.stderr)
        sys.exit(2)
