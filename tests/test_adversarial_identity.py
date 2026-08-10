"""Adversarial identity: every way to make this system name the wrong party.

WHAT THIS FILE PROVES
---------------------
IMPLEMENTATION BEHAVIOUR ONLY. Every Tally in here is `FakeTally`, an in-memory
double. Nothing in this file is evidence about a real TallyPrime: not that a
name survives the round trip, not that TallyPrime folds a ledger name the way
we do, not that a voucher we wrote would have been accepted there. Live
evidence never comes from this file.

WHAT IT IS HUNTING
------------------
A wrong match is not a weaker answer. It is a voucher posted to the wrong
account in somebody's real statutory books, silently, with no question asked.
Every test here asks one question in a different disguise:

    can two DIFFERENT parties be made to collapse onto one key, or
    can one SAME party be made to look like two?

The two are asserted separately because they cost different things. Failing to
collapse costs a question. Collapsing wrongly costs a voucher in the wrong
ledger, and nobody finds it until the year end.

EVERY TEST ASSERTS THE WHOLE OUTCOME, NOT JUST THE VERDICT
----------------------------------------------------------
Expected decision, actual decision, WRITE COUNT (zero on every refusal path),
the recorded backend identity, the `ActionLog` row if one exists, the sentence
the person is shown, the cleanup result, and the run id. A test that checks
only the verdict passes happily while a voucher is being written behind it.

ONE TEST HERE STILL PINS A DEFECT RATHER THAN AN INTENTION
-----------------------------------------------------------
It is named `..._today_...` and carries a DEFECT block naming the file, the
line, what happens, and what should happen. It asserts what the code does now
so the suite stays green and the behaviour cannot change unnoticed. It is not
an endorsement. Nothing here is skipped and nothing is xfailed.

    D1 accented NFC/NFD  FIXED 2026-08-09, `index.py` folds to NFC
    D2 LLP keyed as Ltd  FIXED 2026-08-09, legal forms left in the name
    D3 bracketed company FIXED 2026-08-09, both companies refused
    D4 stale index       STILL OPEN, `..._today_...`

TWO SMALLER DEFECTS ARE REPORTED HERE AND FIXED NOWHERE
--------------------------------------------------------
Both are named in the test that found them, and asserted so they cannot drift:

    `accountant/memory/identity.py` keys COMPANIES with the same substitution
    that caused D1 and no NFC fold, so NFD "Café Supplies" still collides with
    a different company's key.

    `normalise_vendor` still folds "Acme Ltd", "Acme Private Limited",
    "Acme & Co" and a bare "Acme" onto one key. Splitting them is blocked on
    `tests/test_memory.py:994`, which is owned elsewhere.
"""

from __future__ import annotations

import datetime
import unicodedata

import pytest

from accountant import pipeline
from accountant.extract.adapter import StubExtractor
from accountant.memory.bootstrap import bootstrap, resume
from accountant.memory.company import (
    CompanyMatchStatus,
    CompanyMemory,
    MemoryNotReady,
    propose_account,
)
from accountant.memory.identity import normalise_company
from accountant.memory.index import normalise_phrase, normalise_vendor
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import ActionLog, Outcome, Voucher
from accountant.tallyio.fake import FakeTally
from accountant.web import app

COMPANY = "Kapoor Enterprises"
OTHER_COMPANY = "Deshmukh Timber"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Sundry Expenses", "Cash")
TODAY = datetime.date(2026, 8, 9)
RUN_ID = "run_adversarial_identity"
AMOUNT_PAISE = 420000
HISTORY_PAISE = 380000
SEEDED = 40

# An operation id nothing in this file ever writes. Reversing it must always
# come back False: that is the cleanup assertion on a path that wrote nothing.
NEVER_WRITTEN_OP = "op-never-written-adversarial-identity"


# ---- fixtures, built by hand so each test states its own world -------------


def _history(party: str, account: str, n: int = SEEDED) -> tuple[Voucher, ...]:
    """One company's own posted history, as the accountant typed it."""
    return tuple(
        Voucher(
            id=f"seed-{account}-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=HISTORY_PAISE,
        )
        for i in range(n)
    )


def _tally(
    vouchers: tuple[Voucher, ...] = (),
    *,
    accounts: tuple[str, ...] = ACCOUNTS,
    company: str = COMPANY,
) -> FakeTally:
    t = FakeTally()
    t.add_company(company, accounts=accounts, vouchers=vouchers, backed_up=True)
    return t


def _entry(party: str, amount_paise: int = AMOUNT_PAISE) -> StubExtractor:
    """A reader that returns exactly this party name and nothing invented.

    `TypedTextExtractor` cannot be used here: its party regex is anchored on
    `[A-Z]`, so it silently drops every non-ASCII name this file exists to
    test, and the test would then be measuring that regex instead of the index.
    """
    return StubExtractor(date=TODAY, party=party, total_paise=amount_paise)


def _run(
    t: FakeTally,
    store: MemoryStore,
    memory: CompanyMemory,
    party: str,
    *,
    company: str = COMPANY,
) -> pipeline.Draft:
    return pipeline.run(
        company,
        party.encode(),
        "text/plain",
        _entry(party),
        t,
        memory,
        today=TODAY,
        log=store,
        run_id=RUN_ID,
    )


def _rows(store: MemoryStore, company: str = COMPANY) -> tuple[ActionLog, ...]:
    return store.actions(company)


def _assert_nothing_was_written(
    t: FakeTally,
    store: MemoryStore,
    *,
    company: str = COMPANY,
    seeded: int = SEEDED,
) -> None:
    """Write count zero, ledger untouched, cleanup finds nothing to undo.

    All four together, because each one alone can be true while a voucher
    exists: `list_our_vouchers` misses a write carrying no marker, and a trial
    balance misses two writes that cancel each other out.
    """
    assert t.list_our_vouchers(company) == ()
    assert len(t.read_vouchers(company)) == seeded
    assert t.reverse_by_operation_id(company, NEVER_WRITTEN_OP) is False
    assert [r for r in _rows(store, company) if r.action == "posted"] == []


def _assert_one_posted_row(
    store: MemoryStore,
    draft: pipeline.Draft,
    *,
    company: str = COMPANY,
    detail: str,
) -> None:
    # TWO rows per write since 2026-08-09, not one: `post` records
    # `write_attempted` BEFORE the socket opens, so a write whose outcome is
    # never learned still leaves its operation id somewhere findable. Both
    # rows are checked, because "exactly one posted row" is the claim here and
    # a second posted row hiding behind a loose count is the failure it guards.
    log = _rows(store, company)
    assert [r.action for r in log] == [pipeline.WRITE_ATTEMPTED, "posted"]
    assert {r.operation_id for r in log} == {draft.operation_id}
    assert {r.backend for r in log} == {"FakeTally"}
    assert {r.run_id for r in log} == {RUN_ID}

    posted = log[-1]
    assert posted.outcome == Outcome.VALID.value
    assert posted.detail == detail
    assert posted.reason == "nothing unclear and nothing surprising"


# ============================================================================
# CASE 1 - unicode and homoglyphs
# ============================================================================

LATIN_SHARMA = "Sharma Traders"
# U+0405 CYRILLIC CAPITAL LETTER DZE. Renders identically to Latin "S" in every
# font this name will ever be read in.
CYRILLIC_SHARMA = "\u0405harma Traders"


def test_a_cyrillic_homoglyph_vendor_never_inherits_the_latin_vendors_account() -> None:
    """Two byte-different parties that look identical stay two parties.

    The failure being hunted: the lookalike arrives, memory answers with the
    real supplier's account, and a voucher lands in the wrong ledger with no
    question asked. The Latin half runs first and is asserted to POST, so this
    test cannot pass by refusing everything.

    Passed on the first run. The system already handles this; `\\w` is Unicode
    aware, so a Cyrillic letter survives normalisation as itself.

    Updated 2026-08-09 for the deletion of `_default_credit`. A stranger now
    raises TWO questions, not one - "what was it for?" and then "how did you
    pay?" - because the funding leg is no longer guessed from a hard-coded
    preference list. The order is asserted, not just the membership: the
    purpose is the one the person can read off the bill in their hand.
    """
    # the control: the real supplier posts straight through
    t_ok = _tally(_history(LATIN_SHARMA, "Purchases"))
    store_ok = MemoryStore(":memory:")
    mem_ok = bootstrap(t_ok, COMPANY, store_ok)
    good = _run(t_ok, store_ok, mem_ok, LATIN_SHARMA)

    assert mem_ok.report.status is BootstrapStatus.READY
    assert good.outcome is Outcome.VALID
    assert good.voucher.debit_account == "Purchases"
    assert len(t_ok.list_our_vouchers(COMPANY)) == 1

    # the attack: the same books, a name that only LOOKS like the supplier
    t = _tally(_history(LATIN_SHARMA, "Purchases"))
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    assert normalise_vendor(CYRILLIC_SHARMA) != normalise_vendor(LATIN_SHARMA)
    assert memory.lookup(CYRILLIC_SHARMA).status is CompanyMatchStatus.NO_MATCH
    assert memory.lookup(CYRILLIC_SHARMA).accounts == ()
    assert propose_account(memory, CYRILLIC_SHARMA) is None

    draft = _run(t, store, memory, CYRILLIC_SHARMA)

    # expected decision UNCLEAR (ask), actual decision UNCLEAR, nothing posted
    assert draft.outcome is Outcome.UNCLEAR
    assert [p.id for p in draft.problems] == ["which_account", pipeline.FUNDING_PROBLEM]
    assert draft.voucher.debit_account == ""
    assert draft.voucher.credit_account == ""
    assert draft.posted_tally_id is None

    # Neither leg was inherited from the real supplier, and neither was
    # invented. Both say so in the provenance, which is what makes "no field
    # without a source" checkable rather than a slogan.
    assert draft.voucher.provenance is not None
    assert draft.voucher.provenance["debit_account"] == "not_found"
    assert draft.voucher.provenance["credit_account"] == "not_found"

    # the sentence the person is shown, and the key it is really about
    question = pipeline.next_question(draft)
    assert question is not None
    assert question.text == f"What did you get from {CYRILLIC_SHARMA}?"
    assert draft.problems[0].detail == (
        f"{normalise_vendor(CYRILLIC_SHARMA)} has never been posted before"
    )
    assert draft.problems[1].detail == "nothing says how this was paid"

    # The funding question is asked SECOND and never instead. The Latin
    # supplier's forty vouchers are all credited to Cash, so a key that had
    # collapsed would have proposed Cash here and asked one question fewer -
    # this assertion fails loudly in exactly that case.
    assert draft.problems[1].question is not None
    assert draft.problems[1].question.text != question.text

    # write count, backend identity, the log row, the run id
    _assert_nothing_was_written(t, store)
    log = _rows(store)
    assert len(log) == 1
    assert log[0].action == "blocked"
    assert log[0].outcome == Outcome.UNCLEAR.value
    assert log[0].backend == "FakeTally"
    assert log[0].run_id == RUN_ID
    assert log[0].vendor_id == CYRILLIC_SHARMA
    assert log[0].voucher_id == ""
    assert log[0].detail == f"(none proposed) {AMOUNT_PAISE} paise"

    # cleanup: there is nothing of ours to reverse
    assert pipeline.reverse(draft, t) is False


# U+FF33 FULLWIDTH LATIN CAPITAL LETTER S.
FULLWIDTH_SHARMA = "\uff33harma Traders"
# U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE. Casefolds to TWO code points,
# "i" plus U+0307 COMBINING DOT ABOVE.
TURKISH_ISTANBUL = "\u0130stanbul Traders"
ASCII_ISTANBUL = "Istanbul Traders"


def test_full_width_and_turkish_dotted_i_names_never_borrow_an_ascii_vendor_key() -> (
    None
):
    """Neither form may be folded onto the ASCII spelling of another party.

    Passed on the first run. Both are asserted in the safe direction only:
    failing to collapse costs one question, collapsing wrongly costs a voucher.

    The Turkish key is recorded exactly because it is mangled rather than
    merely different - U+0307 is a combining mark, so it is neither `\\w` nor
    `\\s`, and `_PUNCT` turns it into a space that splits the word in half.
    That is harmless today and is the same mechanism behind the NFD defect
    pinned below, so it is written down where it can be seen.
    """
    seeded = SEEDED + 4
    t = _tally(
        _history(LATIN_SHARMA, "Purchases") + _history(ASCII_ISTANBUL, "Cash", 4)
    )
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    assert normalise_vendor(FULLWIDTH_SHARMA) != normalise_vendor(LATIN_SHARMA)
    assert normalise_vendor(TURKISH_ISTANBUL) != normalise_vendor(ASCII_ISTANBUL)
    assert normalise_vendor(TURKISH_ISTANBUL) == "i_stanbul_traders"

    for lookalike in (FULLWIDTH_SHARMA, TURKISH_ISTANBUL):
        answer = memory.lookup(lookalike)
        assert answer.status is CompanyMatchStatus.NO_MATCH
        assert answer.accounts == ()
        assert propose_account(memory, lookalike) is None

    # and the real spellings still answer, so this does not pass by refusing all
    assert memory.lookup(LATIN_SHARMA).accounts == ("Purchases",)
    assert memory.lookup(ASCII_ISTANBUL).accounts == ("Cash",)

    _assert_nothing_was_written(t, store, seeded=seeded)


ACCENTED_NFC = unicodedata.normalize("NFC", "Caf\u00e9 Supplies")
ACCENTED_NFD = unicodedata.normalize("NFD", "Caf\u00e9 Supplies")
UNACCENTED = "Cafe Supplies"


def test_an_accented_company_name_never_borrows_an_unaccented_companys_scope() -> None:
    """D1's other half, FIXED 2026-08-09. The COMPANY key folds to NFC too.

    WHAT THIS USED TO PIN, one file away from the vendor fix
        `accountant/memory/identity.py` compiled `_PUNCT` as `[^\\w\\s]` and
        replaced every match with a space, with no NFC fold in front of it.
        U+0301 COMBINING ACUTE is category Mn - neither `\\w` nor `\\s` - so
        decomposed "Caf\u00e9 Supplies" lost its accent and keyed as
        `cafe_supplies`: the key of a DIFFERENT company, "Cafe Supplies".

        That is the one thing this module exists to stop. Its own docstring
        says a pooled KEY is a correctness bug, and `company_key` is the FIRST
        column of every primary key in the store, the scope of every lookup,
        and the thing both cross-company guards in `pipeline.py` compare. Two
        companies sharing one key means those guards CANNOT fire, and
        `save_bootstrap` deletes the first company's rows before writing the
        second's.

        It is worse here than it was for vendors. A vendor collision costs one
        voucher in the wrong ledger. A company collision costs a whole index,
        and the two businesses need not have anything to do with each other -
        only an accent.

    WHY THE FIX IS SAFE IN THE DIRECTION THAT MATTERS
        Folding NFC->NFD is not a collapse of two names into one. It makes ONE
        VISIBLE NAME one key, whichever encoding the keyboard, the scanner or
        the operating system produced, and that key is still not the
        unaccented company's. The module is deliberately conservative because
        removing a WORD can merge two companies; this removes nothing.
    """
    assert ACCENTED_NFC != ACCENTED_NFD, "the two spellings really are different bytes"

    # one visible company, one key, whichever normal form arrived...
    assert normalise_company(ACCENTED_NFC) == "caf\u00e9_supplies"
    assert normalise_company(ACCENTED_NFD) == "caf\u00e9_supplies"
    assert normalise_company(ACCENTED_NFC) == normalise_company(ACCENTED_NFD)

    # ...and it is NOT the unaccented company's key.
    assert normalise_company(UNACCENTED) == "cafe_supplies"
    assert normalise_company(ACCENTED_NFD) != normalise_company(UNACCENTED)

    # The damage, end to end, in one store. Two businesses, two Tallys, two
    # sets of books. Bootstrapping the second used to `forget()` the first's
    # key and write its own rows over the top.
    store = MemoryStore(":memory:")
    plain = _tally(_history("Sharma Traders", "Purchases"), company=UNACCENTED)
    fancy = _tally(
        _history("Verma Cement", "Repairs & Maintenance"), company=ACCENTED_NFD
    )

    first = bootstrap(plain, UNACCENTED, store)
    second = bootstrap(fancy, ACCENTED_NFD, store)

    assert first.report.status is BootstrapStatus.READY
    assert second.report.status is BootstrapStatus.READY
    assert first.identity.key != second.identity.key, "two companies, two scopes"

    # Each company still knows its own vendor and nothing about the other's.
    assert first.lookup("Sharma Traders").accounts == ("Purchases",)
    assert first.lookup("Verma Cement").status is CompanyMatchStatus.NO_MATCH
    assert second.lookup("Verma Cement").accounts == ("Repairs & Maintenance",)
    assert second.lookup("Sharma Traders").status is CompanyMatchStatus.NO_MATCH

    # And re-opening the unaccented company from the store alone still gets the
    # unaccented company, not whichever one was written last.
    reopened = resume(store, UNACCENTED)
    assert reopened.identity.name == UNACCENTED
    assert reopened.report.status is BootstrapStatus.READY
    assert reopened.lookup("Sharma Traders").accounts == ("Purchases",)


def test_an_accented_vendor_name_decides_one_way_in_nfc_and_nfd() -> None:
    """D1, FIXED 2026-08-09. One visible name, one key, either encoding.

    WHAT THIS TEST USED TO PIN
        `accountant/memory/index.py` compiled `_PUNCT` as `[^\\w\\s&]` and
        replaced every match with a space. U+0301 COMBINING ACUTE ACCENT is
        category Mn: neither `\\w` nor `\\s`. So the decomposed spelling lost
        its accent to a space, the space collapsed, and NFD "Café Supplies"
        keyed as `cafe_supplies` - the key of a DIFFERENT, unaccented supplier.
        The precomposed spelling kept U+00E9, which IS `\\w`, and keyed as
        `café_supplies`.

        Measured then, over identical books: the NFD spelling POSTED to the
        unaccented supplier's account with no question, and the NFC spelling of
        the same visible name stopped and asked. One name on the screen, two
        decisions, chosen by an encoding nobody can see.

    WHAT HAPPENS NOW
        `normalise_vendor` folds the name to NFC before anything else, so both
        spellings of one visible name give one key - and that key is not the
        unaccented supplier's. BOTH halves are asserted, because agreeing on
        the WRONG key would satisfy the first half on its own.

    THE SAME DEFECT WAS LIVE ONE FILE AWAY UNTIL 2026-08-09, and is now fixed:
    `accountant/memory/identity.py` keyed COMPANIES with the same substitution
    and no NFC fold, so `normalise_company(NFD)` collided with a different
    company's key. The company half is asserted below, and end to end in
    `test_an_accented_company_name_never_borrows_an_unaccented_companys_scope`.

    ONE MORE THING THIS TEST RECORDS AND DOES NOT FIX
        Answering both questions does not make an unknown vendor postable.
        `which_account` is derived from the memory lookup rather than from the
        voucher, so it re-appears on the next `evaluate` even though the person
        has just named the account, and the entry lands on NOT_VALID with
        "you already answered this and it still is not clear". That is
        pre-existing and unrelated to the encoding; it is asserted only as
        `is not Outcome.VALID` so this test does not quietly depend on it.
    """
    assert ACCENTED_NFC != ACCENTED_NFD
    assert unicodedata.normalize("NFC", ACCENTED_NFD) == ACCENTED_NFC

    # one visible name, one key, whichever normal form arrived...
    assert normalise_vendor(ACCENTED_NFC) == "café_supplies"
    assert normalise_vendor(ACCENTED_NFD) == "café_supplies"
    assert normalise_vendor(ACCENTED_NFC) == normalise_vendor(ACCENTED_NFD)

    # ...and it is NOT the unaccented supplier's key. An accent is a letter,
    # not decoration, so those two names may be two different firms.
    assert normalise_vendor(UNACCENTED) == "cafe_supplies"
    assert normalise_vendor(ACCENTED_NFC) != normalise_vendor(UNACCENTED)
    assert normalise_vendor(ACCENTED_NFD) != normalise_vendor(UNACCENTED)

    # Companies now fold the same way, and for a sharper reason: a shared
    # company key merges two indexes, not one voucher.
    assert normalise_company(ACCENTED_NFC) == "café_supplies"
    assert normalise_company(ACCENTED_NFD) == "café_supplies"
    assert normalise_company(ACCENTED_NFD) != normalise_company(UNACCENTED)

    # Books that know only the UNACCENTED supplier. Both accented spellings
    # must ask, and neither may borrow that supplier's account.
    for label, spelling in (("NFC", ACCENTED_NFC), ("NFD", ACCENTED_NFD)):
        t = _tally(_history(UNACCENTED, "Sundry Expenses"))
        store = MemoryStore(":memory:")
        memory = bootstrap(t, COMPANY, store)

        assert memory.lookup(spelling).status is CompanyMatchStatus.NO_MATCH, label
        assert memory.lookup(spelling).accounts == (), label
        assert propose_account(memory, spelling) is None, label

        draft = _run(t, store, memory, spelling)

        assert draft.outcome is Outcome.UNCLEAR, label
        assert draft.outcome is not Outcome.VALID, label
        assert draft.voucher.debit_account == "", label
        assert draft.voucher.credit_account == "", label
        assert draft.posted_tally_id is None, label

        # A stranger owes TWO questions since `_default_credit` was deleted:
        # what it was for, then how it was paid. Both are asserted in order.
        assert [p.id for p in draft.problems] == [
            "which_account",
            pipeline.FUNDING_PROBLEM,
        ], label
        assert draft.problems[0].detail == (
            "café_supplies has never been posted before"
        ), label
        assert draft.problems[1].detail == "nothing says how this was paid", label

        question = pipeline.next_question(draft)
        assert question is not None, label
        assert question.text == f"What did you get from {spelling}?", label

        _assert_nothing_was_written(t, store)
        log = _rows(store)
        assert len(log) == 1, label
        assert log[0].action == "blocked", label
        assert log[0].outcome == Outcome.UNCLEAR.value, label
        assert log[0].backend == "FakeTally", label
        assert log[0].run_id == RUN_ID, label
        assert log[0].detail == f"(none proposed) {AMOUNT_PAISE} paise", label
        assert pipeline.reverse(draft, t) is False, label

        # Answering both questions writes the answers onto the legs the
        # QUESTIONS name, not both onto the expense side, and neither leg ever
        # becomes the unaccented supplier's account. The entry does not reach
        # VALID here and that is not this test's business: the vendor is still
        # a stranger to memory, so `which_account` re-derives from the lookup.
        # See the note at the end of this test.
        pipeline.answer(draft, "Purchases")
        pipeline.answer(draft, "Cash", problem_id=pipeline.FUNDING_PROBLEM)
        pipeline.evaluate(draft, ACCOUNTS, t.read_vouchers(COMPANY), memory)

        assert draft.voucher.debit_account == "Purchases", label
        assert draft.voucher.credit_account == "Cash", label
        assert draft.voucher.debit_account != "Sundry Expenses", label
        assert draft.voucher.provenance is not None, label
        assert draft.voucher.provenance["debit_account"] == "human_answer", label
        assert draft.voucher.provenance["credit_account"] == "human_answer", label

        # still not posted, and still nothing of ours in the books
        assert draft.outcome is not Outcome.VALID, label
        assert draft.posted_tally_id is None, label
        _assert_nothing_was_written(t, store)

    # The positive control, and the half that proves this is ONE SHARED KEY
    # rather than two refusals: books seeded under the PRECOMPOSED spelling
    # answer a DECOMPOSED invoice, and it posts.
    t_ok = _tally(_history(ACCENTED_NFC, "Sundry Expenses"))
    store_ok = MemoryStore(":memory:")
    mem_ok = bootstrap(t_ok, COMPANY, store_ok)

    assert mem_ok.report.status is BootstrapStatus.READY
    assert mem_ok.lookup(ACCENTED_NFD).status is CompanyMatchStatus.MATCH
    assert mem_ok.lookup(ACCENTED_NFD).accounts == ("Sundry Expenses",)
    assert propose_account(mem_ok, ACCENTED_NFD) == "Sundry Expenses"

    posted = _run(t_ok, store_ok, mem_ok, ACCENTED_NFD)

    assert posted.outcome is Outcome.VALID
    assert posted.voucher.debit_account == "Sundry Expenses"
    assert posted.problems == []
    assert posted.posted_tally_id == "TALLY-1"
    assert len(t_ok.list_our_vouchers(COMPANY)) == 1
    _assert_one_posted_row(
        store_ok, posted, detail=f"Sundry Expenses {AMOUNT_PAISE} paise"
    )

    # ...and the UNACCENTED name is still a stranger to those same books
    assert mem_ok.lookup(UNACCENTED).status is CompanyMatchStatus.NO_MATCH
    assert propose_account(mem_ok, UNACCENTED) is None

    assert pipeline.reverse(posted, t_ok) is True
    assert t_ok.list_our_vouchers(COMPANY) == ()
    assert t_ok.trial_balance(COMPANY) == _tally(
        _history(ACCENTED_NFC, "Sundry Expenses")
    ).trial_balance(COMPANY)


# ============================================================================
# CASE 2 - leading, trailing and invisible whitespace
# ============================================================================

WHITESPACE_VARIANTS: dict[str, str] = {
    "leading and trailing ordinary spaces": "   Sharma Traders   ",
    "non-breaking space U+00A0 between words": "Sharma\u00a0Traders",
    "leading non-breaking space U+00A0": "\u00a0Sharma Traders",
    "zero-width space U+200B between words": "Sharma\u200bTraders",
    "trailing zero-width space U+200B": "Sharma Traders\u200b",
    "zero-width joiner U+200D between words": "Sharma\u200dTraders",
    "tab between words": "Sharma\tTraders",
    "newline inside the name": "Sharma\nTraders",
}

SINGULAR_SHARMA = "Sharma Trader"


def test_whitespace_visible_or_invisible_never_changes_which_vendor_this_is() -> None:
    """A name a person reads as one supplier resolves to one supplier.

    Passed on the first run.

    Both directions are asserted. Padding must not turn the supplier into a
    stranger (a needless question), and no amount of padding may turn a
    NEIGHBOURING name into this supplier (a wrong voucher). The second half is
    the one that matters, so it is checked against every variant rather than
    once.
    """
    t = _tally(_history(LATIN_SHARMA, "Purchases"))
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)
    clean = normalise_vendor(LATIN_SHARMA)

    for label, padded in WHITESPACE_VARIANTS.items():
        assert normalise_vendor(padded) == clean, label
        answer = memory.lookup(padded)
        assert answer.status is CompanyMatchStatus.MATCH, label
        assert answer.accounts == ("Purchases",), label
        assert propose_account(memory, padded) == "Purchases", label

        # the same padding applied to the NEIGHBOUR never reaches this key
        neighbour = padded.replace("Traders", "Trader")
        assert normalise_vendor(neighbour) != clean, label
        assert memory.lookup(neighbour).status is CompanyMatchStatus.NO_MATCH, label

    # an invisible character INSIDE a word splits it rather than vanishing, so
    # it lands on neither supplier. Recorded because it is the same `_PUNCT`
    # substitution that produces the NFD defect above.
    assert normalise_vendor("Shar\u200bma Traders") == "shar_ma_traders"
    assert memory.lookup("Shar\u200bma Traders").status is (CompanyMatchStatus.NO_MATCH)

    # end to end on the nastiest padding: an invisible character must not stop
    # a voucher, and must not send it anywhere new
    draft = _run(t, store, memory, "\u00a0Sharma\u200bTraders\u200b")

    assert draft.outcome is Outcome.VALID
    assert draft.voucher.debit_account == "Purchases"
    assert draft.reason == "nothing unclear and nothing surprising"
    assert draft.posted_tally_id == "TALLY-1"
    assert len(t.list_our_vouchers(COMPANY)) == 1
    _assert_one_posted_row(store, draft, detail=f"Purchases {AMOUNT_PAISE} paise")

    # cleanup: this one DID write, so the undo must find it and the books must
    # come back to exactly the seeded state
    assert pipeline.reverse(draft, t) is True
    assert t.list_our_vouchers(COMPANY) == ()
    assert len(t.read_vouchers(COMPANY)) == SEEDED
    assert t.trial_balance(COMPANY) == _tally(
        _history(LATIN_SHARMA, "Purchases")
    ).trial_balance(COMPANY)


# ============================================================================
# CASE 3 - two vendors, nearly identical names
# ============================================================================


def test_posting_sharma_trader_never_returns_sharma_traders_account() -> None:
    """One letter apart, two suppliers, two ledgers, neither answering for the
    other in either direction.

    Passed on the first run.

    A shared key would not make one right and one wrong. It would make BOTH
    CONFLICTED, so `neither is conflicted` is the assertion that breaks first
    if singular and plural ever collapse.
    """
    t = _tally(
        _history(LATIN_SHARMA, "Purchases")
        + _history(SINGULAR_SHARMA, "Repairs & Maintenance", 6)
    )
    seeded = SEEDED + 6
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    assert normalise_vendor(SINGULAR_SHARMA) != normalise_vendor(LATIN_SHARMA)
    assert memory.lookup(LATIN_SHARMA).accounts == ("Purchases",)
    assert memory.lookup(SINGULAR_SHARMA).accounts == ("Repairs & Maintenance",)
    assert memory.lookup(LATIN_SHARMA).status is CompanyMatchStatus.MATCH
    assert memory.lookup(SINGULAR_SHARMA).status is CompanyMatchStatus.MATCH
    assert memory.lookup(LATIN_SHARMA).status is not CompanyMatchStatus.CONFLICTED
    assert memory.lookup(SINGULAR_SHARMA).status is not CompanyMatchStatus.CONFLICTED

    singular = _run(t, store, memory, SINGULAR_SHARMA)
    assert singular.outcome is Outcome.VALID
    assert singular.voucher.debit_account == "Repairs & Maintenance"
    assert singular.voucher.debit_account != "Purchases"

    plural = _run(t, store, memory, LATIN_SHARMA)
    assert plural.outcome is Outcome.VALID
    assert plural.voucher.debit_account == "Purchases"
    assert plural.voucher.debit_account != "Repairs & Maintenance"

    # a trailing full stop is punctuation, not a different supplier
    assert normalise_vendor("Sharma Traders.") == normalise_vendor(LATIN_SHARMA)
    assert memory.lookup("Sharma Traders.").accounts == ("Purchases",)

    log = _rows(store)
    assert [r.action for r in log] == [
        pipeline.WRITE_ATTEMPTED,
        "posted",
        pipeline.WRITE_ATTEMPTED,
        "posted",
    ]
    # Posted rows only. Each write also leaves a `write_attempted` row carrying
    # the same vendor, and counting both would say four suppliers were paid.
    posted = [r for r in log if r.action == "posted"]
    assert [r.vendor_id for r in posted] == [SINGULAR_SHARMA, LATIN_SHARMA]
    assert {r.backend for r in log} == {"FakeTally"}
    assert {r.run_id for r in log} == {RUN_ID}
    assert posted[0].detail == f"Repairs & Maintenance {AMOUNT_PAISE} paise"
    assert posted[1].detail == f"Purchases {AMOUNT_PAISE} paise"
    assert posted[0].operation_id != posted[1].operation_id

    assert pipeline.reverse(singular, t) is True
    assert pipeline.reverse(plural, t) is True
    assert t.list_our_vouchers(COMPANY) == ()
    assert len(t.read_vouchers(COMPANY)) == seeded


ACME_LTD = "Acme Ltd"
ACME_LLP = "Acme LLP"


def test_an_llp_invoice_is_never_posted_to_the_limited_companys_account() -> None:
    """D2, FIXED 2026-08-09. A legal form is not name noise.

    WHAT THIS TEST USED TO PIN
        `_SUFFIXES` in `accountant/memory/index.py` listed "llp", "inc",
        "corporation" and "corp" beside "ltd" and "limited", and every one of
        them was stripped off the end. So "Acme Ltd", "Acme LLP", "Acme Inc"
        and "Acme Corp" all keyed as `acme`. A company that had only ever
        bought from the Ltd got an LLP invoice posted to the Ltd's account,
        VALID, silently.

        `accountant/memory/identity.py:16-21` says the opposite in its own
        words for COMPANY names: "'Acme Ltd' and 'Acme LLP' are two companies,
        two sets of books". A supplier is no different - separate GSTIN,
        separate returns, separate invoices. Two modules disagreed and only one
        of them was enforced.

    WHAT HAPPENS NOW
        "llp", "inc", "corporation" and "corp" are gone from `_SUFFIXES`, so
        each names a distinct legal person and each gets its own key. The LLP
        invoice is a stranger to books that only know the Ltd, and a stranger
        is a question. That costs one question; the old behaviour cost a
        voucher in the wrong ledger.

    THE RESIDUAL, CLOSED 2026-08-10 BY OWNER RULING D-05
        This block used to pin a gap. The Ltd/Limited and "& Co" families were
        STILL stripped, so "Acme Ltd", "Acme Private Limited", "Acme & Co" and
        a bare "Acme" were one key - four different persons in law sharing one
        memory row. It was pinned rather than fixed because
        `tests/test_memory.py` required that merge and was owned elsewhere.

        The owner has now ruled: legal forms are identity-bearing and must
        never be silently removed. `normalise_vendor` CANONICALISES the form
        instead of deleting it, `test_memory.py` has been rewritten, and the
        four names are now four keys. The assertions below flipped from pinning
        the merge to forbidding it.

        One key moved as part of the same ruling: "Acme Corporation" now keys
        as `acme_corp` rather than `acme_corporation`. That is deliberate and
        the owner confirmed it - "Corp" and "Corporation" are one legal form
        written two ways, and splitting them cost a question for nothing. Note
        that this is the SAFE direction of merge: it joins two spellings of one
        registration, never two registrations.
    """
    # Four legal forms, four keys. Distinctness is asserted pairwise, not just
    # against `acme_ltd`, because two of them sharing a key is the same defect.
    keys = {
        ACME_LLP: "acme_llp",
        "Acme Inc": "acme_inc",
        "Acme Corp": "acme_corp",
        "Acme Corporation": "acme_corp",
    }
    for name, expected in keys.items():
        assert normalise_vendor(name) == expected, name
        assert normalise_vendor(name) != normalise_vendor(ACME_LTD), name

    # FORBIDDEN, where it used to be pinned: the Ltd and "& Co" families no
    # longer collapse onto the bare name, nor onto each other.
    assert normalise_vendor(ACME_LTD) == "acme_ltd"
    assert normalise_vendor("Acme Private Limited") == "acme_pvt_ltd"
    assert normalise_vendor("Acme & Co") == "acme_and_co"
    assert normalise_vendor("Acme") == "acme"
    assert (
        len(
            {
                normalise_vendor(n)
                for n in (ACME_LTD, "Acme Private Limited", "Acme & Co", "Acme")
            }
        )
        == 4
    )

    t = _tally(_history(ACME_LTD, "Purchases"))
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    assert memory.report.status is BootstrapStatus.READY

    # the LLP has never traded with this company, and memory says so
    answer = memory.lookup(ACME_LLP)
    assert answer.status is CompanyMatchStatus.NO_MATCH
    assert answer.status is not CompanyMatchStatus.MATCH
    assert answer.accounts == ()
    assert propose_account(memory, ACME_LLP) is None

    draft = _run(t, store, memory, ACME_LLP)

    # expected decision UNCLEAR (never traded with this entity). actual UNCLEAR.
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.outcome is not Outcome.VALID

    # TWO questions, in this order. The Ltd's forty vouchers are all credited
    # to Cash, so a key that had collapsed would have proposed Cash for the
    # funding leg too and asked one question fewer - this list is the tell.
    assert [p.id for p in draft.problems] == ["which_account", pipeline.FUNDING_PROBLEM]
    assert draft.problems[1].detail == "nothing says how this was paid"
    assert draft.voucher.credit_account == ""
    assert draft.voucher.provenance is not None
    assert draft.voucher.provenance["debit_account"] == "not_found"
    assert draft.voucher.provenance["credit_account"] == "not_found"
    assert draft.voucher.party == ACME_LLP
    assert draft.voucher.debit_account == ""
    assert draft.posted_tally_id is None
    assert draft.problems[0].detail == "acme_llp has never been posted before"

    question = pipeline.next_question(draft)
    assert question is not None
    assert question.text == f"What did you get from {ACME_LLP}?"

    _assert_nothing_was_written(t, store)
    log = _rows(store)
    assert len(log) == 1
    assert log[0].action == "blocked"
    assert log[0].outcome == Outcome.UNCLEAR.value
    assert log[0].backend == "FakeTally"
    assert log[0].run_id == RUN_ID
    assert log[0].vendor_id == ACME_LLP
    assert log[0].voucher_id == ""
    assert log[0].detail == f"(none proposed) {AMOUNT_PAISE} paise"
    assert pipeline.reverse(draft, t) is False

    # The positive control: the Ltd it DOES know still posts, so this test
    # cannot pass by refusing everything that arrives.
    good = _run(t, store, memory, ACME_LTD)
    assert good.outcome is Outcome.VALID
    assert good.voucher.debit_account == "Purchases"
    assert good.problems == []
    assert good.posted_tally_id == "TALLY-1"
    assert len(t.list_our_vouchers(COMPANY)) == 1
    assert [r.action for r in _rows(store)] == [
        "blocked",
        pipeline.WRITE_ATTEMPTED,
        "posted",
    ]
    assert _rows(store)[-1].detail == f"Purchases {AMOUNT_PAISE} paise"

    assert pipeline.reverse(good, t) is True
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.trial_balance(COMPANY) == _tally(
        _history(ACME_LTD, "Purchases")
    ).trial_balance(COMPANY)


# The policy in one table. `accountant/memory/index.py` states it in prose;
# this states it as arithmetic. If the two ever disagree, one of them is a lie.

PRESENTATION_ONLY: dict[str, str] = {
    "case only, upper": "SHARMA TRADERS",
    "case only, lower": "sharma traders",
    "case only, mixed": "sHaRmA tRaDeRs",
    "leading and trailing spaces": "   Sharma Traders   ",
    "repeated internal spaces": "Sharma    Traders",
    "tab instead of space": "Sharma\tTraders",
    "newline instead of space": "Sharma\nTraders",
    "non-breaking space U+00A0": "Sharma\u00a0Traders",
    "trailing full stop": "Sharma Traders.",
    "internal comma": "Sharma, Traders",
    "hyphen between words": "Sharma-Traders",
    "wrapped in brackets": "(Sharma Traders)",
    "M/s prefix": "M/s Sharma Traders",
    "Messrs prefix": "Messrs Sharma Traders",
    "Ms. prefix": "Ms. Sharma Traders",
    "everything at once": "  MESSRS   sharma-traders.  ",
}

LEGALLY_MEANINGFUL: dict[str, str] = {
    "LLP is not a private limited company": ACME_LLP,
    "Inc is not a private limited company": "Acme Inc",
    "Corp is not a private limited company": "Acme Corp",
}

DIFFERENT_PARTY: dict[str, str] = {
    "one character shorter": SINGULAR_SHARMA,
    "Cyrillic homoglyph U+0405": CYRILLIC_SHARMA,
    "full-width homoglyph U+FF33": FULLWIDTH_SHARMA,
    "a different firm entirely": "Verma Traders",
}


def test_only_presentation_differences_collapse_and_meaning_never_does() -> None:
    """The whole vendor-key policy, asserted as a table rather than described.

    Three claims, and the third is the expensive one:

        presentation differences MUST collapse   - else a needless question
        a legal form MUST NOT collapse           - else a wrong voucher
        a different party MUST NOT collapse      - else a wrong voucher

    The direction matters. Failing to collapse costs one question, which the
    person answers in a second. Collapsing wrongly costs a voucher in somebody
    else's ledger, and nobody finds it until the year end. So the second and
    third groups are asserted pairwise - every entry distinct from `Sharma
    Traders`/`Acme Ltd` AND from each other - while the first is asserted only
    in the safe direction.

    This test proves the KEY only. It says nothing about what Tally would do
    with any of these names; see the file docstring.
    """
    latin = normalise_vendor(LATIN_SHARMA)
    assert latin == "sharma_traders"

    # deterministic: the same input twice is the same key, always
    for spelling in (LATIN_SHARMA, ACME_LTD, ACCENTED_NFD, CYRILLIC_SHARMA):
        assert normalise_vendor(spelling) == normalise_vendor(spelling), spelling

    # 1. presentation collapses
    for label, spelling in PRESENTATION_ONLY.items():
        assert normalise_vendor(spelling) == latin, label

    # NFC and NFD of one visible name are one supplier - the same claim, but it
    # needs its own line because both spellings render identically.
    assert normalise_vendor(ACCENTED_NFD) == normalise_vendor(ACCENTED_NFC)

    # 2. a legal form never collapses, onto the Ltd or onto each other
    ltd = normalise_vendor(ACME_LTD)
    legal = {label: normalise_vendor(n) for label, n in LEGALLY_MEANINGFUL.items()}
    for label, key in legal.items():
        assert key != ltd, label
        assert key, label
    assert len(set(legal.values())) == len(legal)

    # 3. a different party never collapses, onto Sharma Traders or each other
    others = {label: normalise_vendor(n) for label, n in DIFFERENT_PARTY.items()}
    for label, key in others.items():
        assert key != latin, label
        assert key, label
    assert len(set(others.values())) == len(others)

    # an accented name and its unaccented spelling are two suppliers
    assert normalise_vendor(ACCENTED_NFC) != normalise_vendor(UNACCENTED)
    assert normalise_vendor(ACCENTED_NFD) != normalise_vendor(UNACCENTED)

    # ...and no key from group 2 or 3 has quietly landed on any other
    everything = set(legal.values()) | set(others.values()) | {latin, ltd}
    assert len(everything) == len(legal) + len(others) + 2

    # The narration key obeys the same Unicode rule, because it feeds the same
    # kind of decision: `CompanyMemory.lookup_phrase` (`company.py:197-205`)
    # answers "this phrase was posted to that account" from it. Two encodings
    # of one narration must not be two phrases, and an accented word must not
    # become its unaccented spelling.
    phrase_nfc = unicodedata.normalize("NFC", "Café latte for the office")
    phrase_nfd = unicodedata.normalize("NFD", "Café latte for the office")
    assert phrase_nfc != phrase_nfd
    assert normalise_phrase(phrase_nfc) == "café_latte_for_the_office"
    assert normalise_phrase(phrase_nfd) == normalise_phrase(phrase_nfc)
    assert normalise_phrase("Cafe latte for the office") != normalise_phrase(phrase_nfd)


# ============================================================================
# CASE 4 - the history was read and nothing usable came out
# ============================================================================


def test_history_yielding_no_mapping_is_empty_vendor_index_and_proposes_nothing() -> (
    None
):
    """Forty vouchers in, a full chart read, zero mappings out.

    Passed on the first run.

    Everything a naive readiness check would look at is non-empty: the company
    is open, the chart holds four accounts, the history holds forty rows. Only
    the derived mappings are zero, and that is the one number allowed to
    decide.
    """
    unusable = tuple(
        Voucher(
            id=f"blank-{i}",
            date=datetime.date(2026, 1, 1),
            party="",
            narration="cash payment",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=HISTORY_PAISE,
        )
        for i in range(SEEDED)
    )
    t = _tally(unusable)
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)
    report = memory.report

    assert report.counts.accounts == len(ACCOUNTS)
    assert report.counts.vouchers == SEEDED
    assert report.counts.unusable == SEEDED
    assert report.counts.mappings == 0
    assert report.status is BootstrapStatus.EMPTY_VENDOR_INDEX
    assert report.status is not BootstrapStatus.READY
    assert report.ready is False
    assert report.askable is False
    assert report.bootstrapped_at == ""

    # nothing is proposed, and not-ready is never quietly turned into no-match
    assert memory.lookup(LATIN_SHARMA).status is CompanyMatchStatus.MEMORY_NOT_READY
    assert memory.lookup(LATIN_SHARMA).status is not CompanyMatchStatus.NO_MATCH
    with pytest.raises(MemoryNotReady):
        propose_account(memory, LATIN_SHARMA)
    with pytest.raises(MemoryNotReady):
        memory.lookup(LATIN_SHARMA).as_match_result()

    # expected decision: refuse before a draft exists. actual: MemoryNotReady.
    with pytest.raises(MemoryNotReady) as refusal:
        _run(t, store, memory, LATIN_SHARMA)
    assert "no successful bootstrap for company" in str(refusal.value)

    _assert_nothing_was_written(t, store)

    # the sentence the person is shown names WHICH of the five states this is
    banner = app.bootstrap_banner(report)
    assert "not one past entry says who you paid" in banner
    assert app.CANNOT_HELP in banner

    # ...and a READY company shows no banner at all, so the marker above is not
    # something that is always on the page
    healthy = bootstrap(
        _tally(_history(LATIN_SHARMA, "Purchases")), COMPANY, MemoryStore(":memory:")
    )
    assert app.bootstrap_banner(healthy.report) == ""

    # PINNED, and reported: this refusal leaves NO action-log row. `run` raises
    # out of `build_draft` (`accountant/pipeline.py:276`) before it can reach
    # `record_decision` at `pipeline.py:283` or `:285`, so the durable trail has
    # nothing to say about an entry the system declined to touch.
    assert _rows(store) == ()


# ============================================================================
# CASE 5 - the company identifier is not the expected company
# ============================================================================


def _two_companies() -> FakeTally:
    t = FakeTally()
    t.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=_history(LATIN_SHARMA, "Purchases"),
        backed_up=True,
    )
    t.add_company(
        OTHER_COMPANY,
        accounts=ACCOUNTS,
        vouchers=_history(LATIN_SHARMA, "Repairs & Maintenance"),
        backed_up=True,
    )
    return t


def test_memory_belonging_to_another_company_is_refused_and_writes_nothing() -> None:
    """`build_draft` and `evaluate` both raise, and both leave the books alone.

    Passed on the first run.

    Two checks, not one, because a draft built correctly and then evaluated
    against the wrong company's memory reaches the same leak one function
    later - `evaluate` is where `memory.index()` is actually read.
    """
    t = _two_companies()
    store = MemoryStore(":memory:")
    mem_ours = bootstrap(t, COMPANY, store)
    mem_theirs = bootstrap(t, OTHER_COMPANY, store)

    assert mem_ours.identity.key != mem_theirs.identity.key

    with pytest.raises(ValueError, match=COMPANY) as build_error:
        pipeline.build_draft(
            OTHER_COMPANY,
            LATIN_SHARMA.encode(),
            "text/plain",
            _entry(LATIN_SHARMA),
            mem_ours,
            today=TODAY,
        )
    assert "company-scoped memory is never shared" in str(build_error.value)

    good = pipeline.build_draft(
        OTHER_COMPANY,
        LATIN_SHARMA.encode(),
        "text/plain",
        _entry(LATIN_SHARMA),
        mem_theirs,
        today=TODAY,
    )
    with pytest.raises(ValueError, match=COMPANY) as eval_error:
        pipeline.evaluate(good, ACCOUNTS, t.read_vouchers(OTHER_COMPANY), mem_ours)
    assert "company-scoped memory is never shared" in str(eval_error.value)

    # the draft never reached a decision, so no question and no post
    assert good.decision is None
    assert good.problems == []
    assert pipeline.next_question(good) is None

    with pytest.raises(ValueError, match=COMPANY):
        _run(t, store, mem_ours, LATIN_SHARMA, company=OTHER_COMPANY)

    # write count zero in BOTH companies, and cleanup finds nothing to undo
    _assert_nothing_was_written(t, store)
    _assert_nothing_was_written(t, store, company=OTHER_COMPANY)
    for company in (COMPANY, OTHER_COMPANY):
        assert _rows(store, company) == ()

    # the positive control: the right memory for the right company DOES write a
    # row, so "no row" above is a fact about the refusal, not about the log
    ok = _run(t, store, mem_theirs, LATIN_SHARMA, company=OTHER_COMPANY)
    assert ok.outcome is Outcome.VALID
    _assert_one_posted_row(
        store,
        ok,
        company=OTHER_COMPANY,
        detail=f"Repairs & Maintenance {AMOUNT_PAISE} paise",
    )
    assert _rows(store, OTHER_COMPANY)[0].company_key == mem_theirs.identity.key
    assert _rows(store, COMPANY) == ()
    assert pipeline.reverse(ok, t) is True
    assert t.list_our_vouchers(OTHER_COMPANY) == ()


PAREN_UNIT = "Acme Traders (Unit 1)"
PLAIN_UNIT = "Acme Traders Unit 1"


def test_two_tally_companies_differing_only_by_brackets_are_both_refused() -> None:
    """D3, FIXED 2026-08-09. This test pinned the DEFECT until then.

    WHAT IT USED TO PIN
        `identity.py:37-47` replaces every punctuation character with a space
        and joins on "_", so brackets, hyphens, dots and slashes carry no
        information. Two DIFFERENT companies open in one Tally shared one scope
        key, and everything followed: the second `bootstrap` called
        `store.forget(key)` and erased the first company's whole index; the
        first company's LIVE handle then answered with the second's account;
        `resume` came back READY under the OTHER company's name; the guard at
        `pipeline.py:116-121` compared keys so it could not see the difference;
        and `store.actions()` returned one merged trail.

    WHAT HAPPENS NOW, AND IT IS STRICTER THAN THE RECORDED FIX
        The recorded fix said "a collision check at `bootstrap.py:197`". That
        siting was WRONG: `store.forget()` ran FOUR LINES EARLIER, so a check
        there fired only after the first company's rows were already deleted.
        Every refusal now precedes `forget()`, and the collision path uses
        `_refused`, which writes nothing at all - because `_incomplete` calls
        `save_bootstrap`, and on a collision that write IS the damage.

        And BOTH companies are refused, not just the second. While two names
        that reduce to one key are both open, no reading of either can say
        whose books it read.

    The normalisation rule is deliberately UNCHANGED. Tightening it only
    reshuffles which pairs collide; the key is a many-to-one map and always
    will be. See tests/test_company_collision.py.
    """
    t = FakeTally()
    t.add_company(
        PAREN_UNIT,
        accounts=ACCOUNTS,
        vouchers=_history(LATIN_SHARMA, "Purchases"),
        backed_up=True,
    )
    t.add_company(
        PLAIN_UNIT,
        accounts=ACCOUNTS,
        vouchers=_history(LATIN_SHARMA, "Repairs & Maintenance"),
        backed_up=True,
    )

    # The collision itself is unchanged - the key still cannot tell them apart.
    assert normalise_company(PAREN_UNIT) == "acme_traders_unit_1"
    assert normalise_company(PLAIN_UNIT) == "acme_traders_unit_1"

    store = MemoryStore(":memory:")
    mem_paren = bootstrap(t, PAREN_UNIT, store)
    mem_plain = bootstrap(t, PLAIN_UNIT, store)

    # Neither is admitted, and each refusal names the OTHER company.
    for mem, asked, other in (
        (mem_paren, PAREN_UNIT, PLAIN_UNIT),
        (mem_plain, PLAIN_UNIT, PAREN_UNIT),
    ):
        assert mem.report.status is BootstrapStatus.COMPANY_KEY_COLLISION
        assert not mem.report.ready
        assert asked in mem.report.detail
        assert other in mem.report.detail

    # Nothing was written, so each name resumes as NEVER_RUN carrying ITS OWN
    # name. Before the fix, `resume(PAREN_UNIT)` came back READY under
    # PLAIN_UNIT - the other company's display name, stamped on by the second
    # bootstrap's write.
    for name in (PAREN_UNIT, PLAIN_UNIT):
        again = resume(store, name)
        assert again.report.status is BootstrapStatus.NEVER_RUN
        assert again.identity.name == name

    # No proposal, no post, no audit row, and Tally is untouched on both sides.
    # `lookup` RETURNS a not-ready match; the raise is in `as_match_result`,
    # which is what keeps "we have not read your books" from arriving at the
    # decision as "no match". Both halves asserted.
    for mem in (mem_paren, mem_plain):
        assert not mem.lookup(LATIN_SHARMA).accounts
        with pytest.raises(MemoryNotReady):
            mem.lookup(LATIN_SHARMA).as_match_result()
    assert t.list_our_vouchers(PAREN_UNIT) == ()
    assert t.list_our_vouchers(PLAIN_UNIT) == ()
    assert _rows(store, PAREN_UNIT) == ()
    assert len(t.read_vouchers(PAREN_UNIT)) == SEEDED
    assert len(t.read_vouchers(PLAIN_UNIT)) == SEEDED


# ============================================================================
# CASE 6 - the proposed ledger is not in this company's chart
# ============================================================================

GHOST_LEDGER = "Ghost Ledger"


def test_an_account_missing_from_the_chart_is_asked_about_and_never_posted() -> None:
    """Memory can only propose an account it has SEEN this company use.

    A ledger the accountant has since renamed or deleted is exactly that: in
    the history, absent from the chart. Creating it would be inventing a ledger
    in somebody's books, so the only honest move is a question.

    Failed on the first run, on my expectation and not on the system:

        AssertionError: assert 'which_account' == 'accounts_exist'

    The behaviour was already correct - UNCLEAR, asked, nothing posted. What
    the failure exposed is that `Problem.id` and the id carried by that
    problem's own `Question` disagreed: the failed `accounts_exist` check was
    answered with `Q.which_purpose`, whose `problem_id` was hard coded to
    "which_account". An answer filed under a name nothing looks for never
    retires the problem, so the person is asked the same thing until the run
    budget is spent.

    FIXED upstream 2026-08-09. `problems._from_check` now stamps
    `question.problem_id` with the check's own name for every check, so a new
    check cannot reintroduce the split. Both ids are still asserted here, and
    now asserted EQUAL, because "they agree" is the property that matters and
    it is cheap to keep watching.

    The words are still `which_purpose`'s - shared wording, separate id.
    "which_account" remains the id of the memory NO_MATCH/CONFLICTED problem,
    which is a different problem reached a different way.
    """
    chart = ("Purchases", "Cash")
    t = _tally(_history(LATIN_SHARMA, GHOST_LEDGER), accounts=chart)
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    assert GHOST_LEDGER not in t.read_accounts(COMPANY)
    assert memory.report.status is BootstrapStatus.READY
    assert propose_account(memory, LATIN_SHARMA) == GHOST_LEDGER

    draft = _run(t, store, memory, LATIN_SHARMA)

    assert draft.outcome is Outcome.UNCLEAR
    assert draft.outcome is not Outcome.VALID
    assert [p.id for p in draft.problems] == ["accounts_exist"]
    assert draft.reason == f"not in chart of accounts: {GHOST_LEDGER}"
    assert draft.posted_tally_id is None

    failed = [c for c in draft.checks if not c.passed]
    assert [c.name for c in failed] == ["accounts_exist"]

    # the sentence the person is shown, and S7: it never names the ledger
    question = pipeline.next_question(draft)
    assert question is not None
    assert question.text == f"What did you get from {LATIN_SHARMA}?"
    assert GHOST_LEDGER not in question.text

    # The question is filed under the id of the problem that raised it, so the
    # answer can retire that problem. Asserted both ways round: the literal id,
    # and that it matches the problem - either one alone can be true while the
    # other rots.
    assert question.problem_id == "accounts_exist"
    assert question.problem_id == draft.problems[0].id
    assert question.problem_id != "which_account"

    _assert_nothing_was_written(t, store)
    log = _rows(store)
    assert len(log) == 1
    assert log[0].action == "blocked"
    assert log[0].outcome == Outcome.UNCLEAR.value
    assert log[0].reason == f"not in chart of accounts: {GHOST_LEDGER}"
    assert log[0].backend == "FakeTally"
    assert log[0].run_id == RUN_ID
    assert log[0].detail == f"{GHOST_LEDGER} {AMOUNT_PAISE} paise"
    assert log[0].voucher_id == ""
    assert pipeline.reverse(draft, t) is False


# ============================================================================
# CASE 7 - a cached index the live Tally report contradicts
# ============================================================================


def test_a_stale_index_today_asks_instead_of_posting_what_the_live_books_deny() -> None:
    """Cached memory no longer outvotes the customer's current ledger.

    RENAMED 2026-08-10, from
    `test_a_stale_index_today_posts_an_account_the_live_history_contradicts` -
    the name to grep for in anything written before that date. The old name was
    a true claim about a defect. It is now false, and a test whose name asserts
    the bug is a test nobody can read twice.

    WHAT THIS USED TO ASSERT
        This was a DEFECT PIN. It was written expecting `draft.outcome is not
        Outcome.VALID`, failed on its first run, and was then committed
        asserting the failure so the bug could not drift out of sight:

            assert draft.outcome is Outcome.VALID
            assert draft.problems == []
            assert draft.reason == "nothing unclear and nothing surprising"
            assert draft.posted_tally_id == "TALLY-1"
            assert balance["Purchases"] == AMOUNT_PAISE

        Bootstrap read a history in which this supplier is always Purchases.
        The accountant then reclassified the supplier in Tally, so all forty
        live vouchers say Repairs & Maintenance. `resume` handed the stored
        report back unchanged - READY, with no freshness test of any kind - and
        `pipeline.evaluate` held BOTH halves of the contradiction in one call,
        the fresh `history` argument and the stale `memory.index()`, and never
        compared them. `vendor_switch` reads only the index, so the index and
        the proposal agreed and it stayed silent. Zero flags, zero problems,
        VALID, posted, and the recorded reason was "nothing unclear and nothing
        surprising" while the live ledger contradicted it forty to nil.

    WHY THAT CHANGED
        Owner decision D-06, answered 2026-08-10: live Tally wins over stale
        memory. Where live Tally and memory disagree the entry becomes UNCLEAR
        and asks instead of silently posting, the conflict is shown, both
        sources are recorded, and stale memory never overrides contradictory
        current Tally data.

        The pin asked for exactly this. Its own WHAT SHOULD HAPPEN read "A
        proposal the live history unanimously contradicts is a question, not a
        post", and its own SMALLEST FIX read "In `evaluate`, after the lookup,
        compare the proposed debit against the accounts this party actually
        carries in the `history` already passed in; on disagreement raise an
        answerable problem." That is what landed, as
        `accountant/memory/company.py::disagrees_with_live_history` called from
        `pipeline.evaluate`. So the assertions below are the ones this test
        asked for rather than new ones invented to make it pass.

        The rule's definition, its false-alarm argument and the N1 numbers live
        in `tests/test_stale_memory_conflict.py`. This stays here, unmoved,
        because a pin belongs where the defect was found.
    """
    before = _tally(_history(LATIN_SHARMA, "Purchases"))
    store = MemoryStore(":memory:")
    bootstrap(before, COMPANY, store)

    after = _tally(_history(LATIN_SHARMA, "Repairs & Maintenance"))
    live = after.read_vouchers(COMPANY)
    assert {v.debit_account for v in live} == {"Repairs & Maintenance"}
    assert len([v for v in live if v.debit_account == "Purchases"]) == 0
    seeded_balance = after.trial_balance(COMPANY)

    stale = resume(store, COMPANY)
    assert stale.report.status is BootstrapStatus.READY
    assert stale.lookup(LATIN_SHARMA).accounts == ("Purchases",)
    # Memory would still answer, instantly and confidently. That is the point:
    # the proposal was available and was not taken.
    assert propose_account(stale, LATIN_SHARMA) == "Purchases"

    draft = _run(after, store, stale, LATIN_SHARMA)

    # UNCLEAR and not NOT_VALID, because an answer fixes this.
    assert draft.outcome is Outcome.UNCLEAR
    assert draft.outcome is not Outcome.VALID
    assert [p.id for p in draft.problems] == [pipeline.LIVE_HISTORY_DISAGREES]
    assert draft.posted_tally_id is None
    assert after.list_our_vouchers(COMPANY) == ()

    # Still no flag, and none is expected: `vendor_switch` reads the stale index,
    # agrees with the proposal it came from, and is silent. What stopped this is
    # a comparison against the live history, not a detector.
    assert draft.flags == []

    # both sources and both counts, recorded on the draft
    conflict = draft.memory_conflict
    assert conflict is not None
    assert (conflict.remembered_account, conflict.remembered_times) == (
        "Purchases",
        SEEDED,
    )
    assert (conflict.live_accounts, conflict.live_times) == (
        ("Repairs & Maintenance",),
        (SEEDED,),
    )
    assert draft.reason == conflict.detail

    # the sentence the person is shown, and S7: it names neither ledger
    question = pipeline.next_question(draft)
    assert question is not None
    assert question.problem_id == pipeline.LIVE_HISTORY_DISAGREES
    assert question.problem_id == draft.problems[0].id
    assert question.mentions_any(ACCOUNTS) == []
    assert "Purchases" not in question.text
    assert "Repairs & Maintenance" not in question.text
    # both counts, so what changed is visible without reading a log
    assert question.text.count(str(SEEDED)) == 2
    # and both ledgers are still offered, in plain words, live one first
    assert [a.value for a in question.answers][:2] == [
        "Repairs & Maintenance",
        "Purchases",
    ]
    assert question.answers[-1].label == "something else"

    # the stale account never became a line in the trial balance
    assert "Purchases" not in after.trial_balance(COMPANY)
    assert after.trial_balance(COMPANY) == seeded_balance
    assert after.trial_balance(COMPANY) == _tally(
        _history(LATIN_SHARMA, "Repairs & Maintenance")
    ).trial_balance(COMPANY)

    _assert_nothing_was_written(after, store)

    # What the durable trail records instead of a posted row: one `blocked` row
    # carrying both accounts and both counts, and NO `write_attempted` - `post`
    # was never reached, so there was never a write in flight to be uncertain
    # about.
    log = _rows(store)
    assert [r.action for r in log] == ["blocked"]
    assert log[0].outcome == Outcome.UNCLEAR.value
    assert log[0].reason == conflict.detail
    assert "Purchases" in log[0].reason
    assert "Repairs & Maintenance" in log[0].reason
    assert f"{SEEDED} time(s)" in log[0].reason
    assert log[0].backend == "FakeTally"
    assert log[0].run_id == RUN_ID
    # The draft still CARRIED the stale proposal. It simply never posted it.
    assert log[0].detail == f"Purchases {AMOUNT_PAISE} paise"
    assert log[0].voucher_id == ""
    assert pipeline.reverse(draft, after) is False


def test_rebuilding_from_the_new_history_replaces_the_old_index_and_never_merges() -> (
    None
):
    """The other half of case 7, and the half that works.

    Passed on the first run.

    `bootstrap` calls `store.forget()` before loading. If it merged instead,
    this supplier would come back CONFLICTED across two accounts and start
    asking a question the current books already answer.
    """
    before = _tally(_history(LATIN_SHARMA, "Purchases"))
    store = MemoryStore(":memory:")
    old = bootstrap(before, COMPANY, store)
    assert old.lookup(LATIN_SHARMA).accounts == ("Purchases",)

    after = _tally(_history(LATIN_SHARMA, "Repairs & Maintenance"))
    fresh = bootstrap(after, COMPANY, store)

    assert fresh.report.status is BootstrapStatus.READY
    assert fresh.lookup(LATIN_SHARMA).status is CompanyMatchStatus.MATCH
    assert fresh.lookup(LATIN_SHARMA).status is not CompanyMatchStatus.CONFLICTED
    assert fresh.lookup(LATIN_SHARMA).accounts == ("Repairs & Maintenance",)
    assert {o.account for o in store.vendors(fresh.identity.key)} == {
        "Repairs & Maintenance"
    }
    assert fresh.report.counts.conflicts == 0

    draft = _run(after, store, fresh, LATIN_SHARMA)
    assert draft.outcome is Outcome.VALID
    assert draft.voucher.debit_account == "Repairs & Maintenance"
    assert len(after.list_our_vouchers(COMPANY)) == 1
    _assert_one_posted_row(
        store, draft, detail=f"Repairs & Maintenance {AMOUNT_PAISE} paise"
    )

    assert pipeline.reverse(draft, after) is True
    assert after.list_our_vouchers(COMPANY) == ()
    assert after.trial_balance(COMPANY) == _tally(
        _history(LATIN_SHARMA, "Repairs & Maintenance")
    ).trial_balance(COMPANY)
