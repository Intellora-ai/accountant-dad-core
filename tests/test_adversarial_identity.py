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

FOUR TESTS HERE PIN A DEFECT RATHER THAN AN INTENTION
------------------------------------------------------
They are named `..._today_...` and each carries a DEFECT block naming the file,
the line, what happens, and what should happen. They assert what the code does
now so the suite stays green and the behaviour cannot change unnoticed. They
are not endorsements. Nothing here is skipped and nothing is xfailed.
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
from accountant.memory.index import normalise_vendor
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
    log = _rows(store, company)
    assert len(log) == 1
    assert log[0].action == "posted"
    assert log[0].outcome == Outcome.VALID.value
    assert log[0].backend == "FakeTally"
    assert log[0].run_id == RUN_ID
    assert log[0].operation_id == draft.operation_id
    assert log[0].detail == detail
    assert log[0].reason == "nothing unclear and nothing surprising"


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
    assert [p.id for p in draft.problems] == ["which_account"]
    assert draft.voucher.debit_account == ""
    assert draft.posted_tally_id is None

    # the sentence the person is shown, and the key it is really about
    question = pipeline.next_question(draft)
    assert question is not None
    assert question.text == f"What did you get from {CYRILLIC_SHARMA}?"
    assert draft.problems[0].detail == (
        f"{normalise_vendor(CYRILLIC_SHARMA)} has never been posted before"
    )

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


def test_an_accented_vendor_name_today_decides_two_ways_in_nfc_and_nfd() -> None:
    """DEFECT, pinned. An invisible byte decides whether a voucher posts.

    WROTE THIS TEST EXPECTING `normalise_vendor(NFC) == normalise_vendor(NFD)`.
    It failed on the first run with:

        AssertionError: assert 'cafe_supplies' == 'café_supplies'

    WHAT HAPPENS
        `accountant/memory/index.py:36` is `_PUNCT = re.compile(r"[^\\w\\s&]")`
        and `index.py:50` replaces every match with a space. U+0301 COMBINING
        ACUTE ACCENT is category Mn: not `\\w`, not `\\s`. So the decomposed
        form loses its accent to a space, the space collapses, and NFD
        "Café Supplies" keys as `cafe_supplies` - the key of a DIFFERENT,
        unaccented supplier. The precomposed form keeps U+00E9, which IS `\\w`,
        and keys as `café_supplies`.

        Consequence, measured below over identical books: the NFD spelling
        POSTS to the unaccented supplier's account with no question, and the
        NFC spelling of the same visible name stops and asks. One name on the
        screen, two decisions, chosen by an encoding nobody can see.

    WHAT SHOULD HAPPEN
        One visible name, one key, whichever normal form arrived.

    SMALLEST FIX
        `index.py:46`, and the same line in `identity.py:47`: normalise the
        input first - `s = unicodedata.normalize("NFC", name).casefold().strip()`.
        NFD then keys as `café_supplies` too, and the collision with the
        unaccented supplier is gone. Stdlib only, no new dependency.
    """
    assert ACCENTED_NFC != ACCENTED_NFD
    assert unicodedata.normalize("NFC", ACCENTED_NFD) == ACCENTED_NFC

    # pinned: the two forms of one name do NOT share a key today, and the
    # decomposed one lands on the unaccented supplier's key
    assert normalise_vendor(ACCENTED_NFC) == "caf\u00e9_supplies"
    assert normalise_vendor(ACCENTED_NFD) == "cafe_supplies"
    assert normalise_vendor(ACCENTED_NFD) == normalise_vendor(UNACCENTED)
    assert normalise_vendor(ACCENTED_NFC) != normalise_vendor(UNACCENTED)

    # the safe half: precomposed asks, because this company never used it
    t_nfc = _tally(_history(UNACCENTED, "Sundry Expenses"))
    store_nfc = MemoryStore(":memory:")
    mem_nfc = bootstrap(t_nfc, COMPANY, store_nfc)
    nfc = _run(t_nfc, store_nfc, mem_nfc, ACCENTED_NFC)

    assert nfc.outcome is Outcome.UNCLEAR
    assert nfc.voucher.debit_account == ""
    assert nfc.posted_tally_id is None
    assert [p.id for p in nfc.problems] == ["which_account"]
    _assert_nothing_was_written(t_nfc, store_nfc)
    assert _rows(store_nfc)[0].action == "blocked"
    assert _rows(store_nfc)[0].run_id == RUN_ID
    assert pipeline.reverse(nfc, t_nfc) is False

    # the defect half: the SAME visible name, decomposed, posts silently
    t_nfd = _tally(_history(UNACCENTED, "Sundry Expenses"))
    store_nfd = MemoryStore(":memory:")
    mem_nfd = bootstrap(t_nfd, COMPANY, store_nfd)
    nfd = _run(t_nfd, store_nfd, mem_nfd, ACCENTED_NFD)

    assert nfd.outcome is Outcome.VALID
    assert nfd.voucher.debit_account == "Sundry Expenses"
    assert nfd.problems == []
    assert nfd.posted_tally_id == "TALLY-1"
    assert len(t_nfd.list_our_vouchers(COMPANY)) == 1
    _assert_one_posted_row(
        store_nfd, nfd, detail=f"Sundry Expenses {AMOUNT_PAISE} paise"
    )

    # the two visibly identical names reached opposite decisions
    assert nfc.outcome is not nfd.outcome

    # cleanup: the wrong voucher is at least reversible by operation id
    assert pipeline.reverse(nfd, t_nfd) is True
    assert t_nfd.list_our_vouchers(COMPANY) == ()
    assert t_nfd.trial_balance(COMPANY) == _tally(
        _history(UNACCENTED, "Sundry Expenses")
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
    assert [r.action for r in log] == ["posted", "posted"]
    assert [r.vendor_id for r in log] == [SINGULAR_SHARMA, LATIN_SHARMA]
    assert {r.backend for r in log} == {"FakeTally"}
    assert {r.run_id for r in log} == {RUN_ID}
    assert log[0].detail == f"Repairs & Maintenance {AMOUNT_PAISE} paise"
    assert log[1].detail == f"Purchases {AMOUNT_PAISE} paise"
    assert log[0].operation_id != log[1].operation_id

    assert pipeline.reverse(singular, t) is True
    assert pipeline.reverse(plural, t) is True
    assert t.list_our_vouchers(COMPANY) == ()
    assert len(t.read_vouchers(COMPANY)) == seeded


ACME_LTD = "Acme Ltd"
ACME_LLP = "Acme LLP"


def test_an_llp_invoice_today_posts_to_the_limited_companys_account() -> None:
    """DEFECT, pinned. Two legal entities, one vendor key, no question asked.

    WROTE THIS TEST EXPECTING `normalise_vendor("Acme Ltd") != "Acme LLP"`.
    It failed on the first run with:

        AssertionError: assert 'acme' != 'acme'
         +  where 'acme' = normalise_vendor('Acme Ltd')
         +  and   'acme' = normalise_vendor('Acme LLP')

    WHAT HAPPENS
        `accountant/memory/index.py:20-34` lists "llp", "ltd", "limited",
        "private limited", "inc", "corporation", "corp", "company", "& co" and
        "and co" as noise, and `index.py:52-58` strips them off the end in a
        loop. So "Acme Ltd", "Acme LLP", "Acme Private Limited" and "Acme & Co"
        all key as `acme`. A company that has only ever bought from the Ltd has
        an LLP invoice posted to the Ltd's account, VALID, silently.

        `accountant/memory/identity.py:16-21` says the opposite in its own
        words for COMPANY names: "'Acme Ltd' and 'Acme LLP' are two companies,
        two sets of books". A supplier is no different - separate GSTIN,
        separate returns, separate invoices. The two modules disagree, and only
        one of them is enforced.

    WHAT SHOULD HAPPEN
        A legal-form suffix distinguishes entities and must not be stripped.
        Worst case the pair conflicts and the person is asked, which costs one
        question; today it costs a voucher in the wrong ledger.

    SMALLEST FIX
        Remove the entity-distinguishing forms from `_SUFFIXES`
        (`index.py:20-34`): "llp", "inc", "corporation", "corp". Keep the
        Ltd/Limited family only if the owner accepts that "Acme Ltd" and
        "Acme Limited" are one supplier - they are - but "llp" next to "ltd" in
        the same list is the part that merges two taxpayers. Owner decision:
        this is a deliberate design trade-off documented at `index.py:18-19`,
        not an oversight, so it is reported rather than changed.
    """
    t = _tally(_history(ACME_LTD, "Purchases"))
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)

    # pinned: four distinct legal forms, one key
    assert normalise_vendor(ACME_LTD) == "acme"
    assert normalise_vendor(ACME_LLP) == "acme"
    assert normalise_vendor("Acme Private Limited") == "acme"
    assert normalise_vendor("Acme & Co") == "acme"

    # and the lookup answers for a supplier this company has never traded with
    answer = memory.lookup(ACME_LLP)
    assert answer.status is CompanyMatchStatus.MATCH
    assert answer.status is not CompanyMatchStatus.NO_MATCH
    assert answer.accounts == ("Purchases",)
    assert propose_account(memory, ACME_LLP) == "Purchases"

    draft = _run(t, store, memory, ACME_LLP)

    # expected decision UNCLEAR (never traded with this entity).
    # actual decision VALID, and a voucher is written.
    assert draft.outcome is Outcome.VALID
    assert draft.problems == []
    assert draft.voucher.party == ACME_LLP
    assert draft.voucher.debit_account == "Purchases"
    assert draft.posted_tally_id == "TALLY-1"
    assert len(t.list_our_vouchers(COMPANY)) == 1
    assert pipeline.next_question(draft) is None

    _assert_one_posted_row(store, draft, detail=f"Purchases {AMOUNT_PAISE} paise")
    assert _rows(store)[0].vendor_id == ACME_LLP

    # cleanup: reversible by operation id, and the books return to seeded state
    assert pipeline.reverse(draft, t) is True
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.trial_balance(COMPANY) == _tally(
        _history(ACME_LTD, "Purchases")
    ).trial_balance(COMPANY)


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
            ACCOUNTS,
            mem_ours,
            today=TODAY,
        )
    assert "company-scoped memory is never shared" in str(build_error.value)

    good = pipeline.build_draft(
        OTHER_COMPANY,
        LATIN_SHARMA.encode(),
        "text/plain",
        _entry(LATIN_SHARMA),
        ACCOUNTS,
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


def test_two_tally_companies_differing_only_by_brackets_today_share_one_scope() -> None:
    """DEFECT, pinned. The company key that isolates everything is not unique.

    WROTE THIS TEST EXPECTING
    `normalise_company(PAREN_UNIT) != normalise_company(PLAIN_UNIT)`.
    It failed on the first run with:

        AssertionError: assert 'acme_traders_unit_1' != 'acme_traders_unit_1'
         +  where 'acme_traders_unit_1' = normalise_company('Acme Traders (Unit 1)')
         +  and   'acme_traders_unit_1' = normalise_company('Acme Traders Unit 1')

    WHAT HAPPENS
        `accountant/memory/identity.py:37-47` replaces every punctuation
        character with a space and then joins on "_", so brackets, hyphens,
        dots and slashes carry no information. Two DIFFERENT companies open in
        one Tally therefore share one scope key, and everything downstream
        follows:

          * the second `bootstrap` calls `store.forget(key)`
            (`bootstrap.py:193`) and erases the first company's whole index;
          * the first company's LIVE handle then answers with the second
            company's account;
          * `resume(store, "Acme Traders (Unit 1)")` comes back READY carrying
            `display_name` "Acme Traders Unit 1" - the other company's name;
          * `build_draft`'s guard at `pipeline.py:116-121` compares keys, so it
            cannot see the difference and lets the draft through;
          * `store.actions()` returns one merged trail for both companies.

        This is the exact cross-company leak `identity.py` exists to prevent.
        Its docstring at `identity.py:16-21` reasons only about removing WORDS
        being dangerous; removing punctuation is treated as free, and it is
        not.

    WHAT SHOULD HAPPEN
        Two distinct Tally company names never share a memory scope. Refusing
        is enough - nobody needs the two merged, they need the merge noticed.

    SMALLEST FIX
        A collision check in `bootstrap` at `accountant/memory/bootstrap.py:197`,
        where `client.list_companies()` is already in hand: if any OTHER open
        company normalises to this key, return `_incomplete(...)` naming both
        names. That fails closed with no schema change and no new dependency.
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

    # pinned: two companies, one key
    assert normalise_company(PAREN_UNIT) == "acme_traders_unit_1"
    assert normalise_company(PLAIN_UNIT) == "acme_traders_unit_1"

    store = MemoryStore(":memory:")
    mem_paren = bootstrap(t, PAREN_UNIT, store)
    assert mem_paren.lookup(LATIN_SHARMA).accounts == ("Purchases",)

    mem_plain = bootstrap(t, PLAIN_UNIT, store)
    assert mem_plain.lookup(LATIN_SHARMA).accounts == ("Repairs & Maintenance",)

    # the first company's own live handle now answers with the SECOND
    # company's account. Its books say Purchases; it has been overwritten.
    assert mem_paren.identity.key == mem_plain.identity.key
    assert mem_paren.lookup(LATIN_SHARMA).accounts == ("Repairs & Maintenance",)
    assert mem_paren.lookup(LATIN_SHARMA).accounts != ("Purchases",)

    # re-opening the first company from the store hands back the OTHER name
    reopened = resume(store, PAREN_UNIT)
    assert reopened.report.status is BootstrapStatus.READY
    assert reopened.identity.name == PLAIN_UNIT
    assert reopened.identity.name != PAREN_UNIT
    assert reopened.lookup(LATIN_SHARMA).accounts == ("Repairs & Maintenance",)

    # and the cross-company guard cannot fire, because the keys really are equal
    draft = _run(t, store, reopened, LATIN_SHARMA, company=PAREN_UNIT)
    assert draft.outcome is Outcome.VALID
    assert draft.voucher.debit_account == "Repairs & Maintenance"
    assert draft.posted_tally_id == "TALLY-1"
    assert len(t.list_our_vouchers(PAREN_UNIT)) == 1
    assert t.list_our_vouchers(PLAIN_UNIT) == ()

    # one merged audit trail: asking about either company returns the same row
    assert len(_rows(store, PAREN_UNIT)) == 1
    assert _rows(store, PAREN_UNIT) == _rows(store, PLAIN_UNIT)
    assert _rows(store, PAREN_UNIT)[0].backend == "FakeTally"
    assert _rows(store, PAREN_UNIT)[0].run_id == RUN_ID
    assert _rows(store, PAREN_UNIT)[0].company_key == "acme_traders_unit_1"

    # cleanup: the voucher went into the bracketed company and reverses there
    assert pipeline.reverse(draft, t) is True
    assert t.list_our_vouchers(PAREN_UNIT) == ()
    assert len(t.read_vouchers(PAREN_UNIT)) == SEEDED


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
    problem's own `Question` disagree: `problems.py:55` answers the failed
    `accounts_exist` check with `Q.which_purpose`, whose `problem_id` is hard
    coded to "which_account" (`questions.py:143`). Both ids are asserted below
    so the mismatch is written down rather than discovered again.
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
    assert question.problem_id == "which_account"
    assert question.problem_id != draft.problems[0].id

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


def test_a_stale_index_today_posts_an_account_the_live_history_contradicts() -> None:
    """DEFECT, pinned. Cached memory outvotes the customer's current ledger.

    WROTE THIS TEST EXPECTING `draft.outcome is not Outcome.VALID`.
    It failed on the first run with:

        AssertionError: assert <Outcome.VALID: 'valid'> is not <Outcome.VALID: 'valid'>

    WHAT HAPPENS
        Bootstrap reads a history in which this supplier is always Purchases.
        The accountant then reclassifies the supplier in Tally, so all forty
        live vouchers now say Repairs & Maintenance. Memory is not rebuilt.

        `resume` (`accountant/memory/bootstrap.py:255-272`) hands back the
        stored report unchanged: READY, no freshness test of any kind.
        `bootstrapped_at` is recorded at `bootstrap.py:237` and never read
        again by any decision.

        `pipeline.evaluate` (`accountant/pipeline.py:175-184`) then holds BOTH
        halves of the contradiction in one call - the fresh `history` argument
        and the stale `memory.index()` - and never compares them. `vendor_switch`
        reads only the index (`detectors.py:98`), so index and proposal agree
        and it stays silent. Zero flags, zero problems, VALID, posted, and the
        recorded reason is "nothing unclear and nothing surprising" while the
        live ledger contradicts it forty to nil.

    WHAT SHOULD HAPPEN
        A proposal the live history unanimously contradicts is a question, not
        a post.

    SMALLEST FIX
        In `evaluate`, after the lookup at `pipeline.py:180`, compare the
        proposed debit against the accounts this party actually carries in the
        `history` already passed in; on disagreement raise an answerable
        problem. Equivalently, add a `vendor_switch`-shaped detector that reads
        `history` rather than `index` - the signature already carries it
        (`detectors.py:63`), so no plumbing changes.
    """
    before = _tally(_history(LATIN_SHARMA, "Purchases"))
    store = MemoryStore(":memory:")
    bootstrap(before, COMPANY, store)

    after = _tally(_history(LATIN_SHARMA, "Repairs & Maintenance"))
    live = after.read_vouchers(COMPANY)
    assert {v.debit_account for v in live} == {"Repairs & Maintenance"}
    assert len([v for v in live if v.debit_account == "Purchases"]) == 0

    stale = resume(store, COMPANY)
    assert stale.report.status is BootstrapStatus.READY
    assert stale.lookup(LATIN_SHARMA).accounts == ("Purchases",)

    draft = _run(after, store, stale, LATIN_SHARMA)

    # expected decision UNCLEAR (the live books say otherwise, unanimously).
    # actual decision VALID, and a voucher is written to the stale account.
    assert draft.outcome is Outcome.VALID
    assert draft.voucher.debit_account == "Purchases"
    assert draft.flags == []
    assert draft.problems == []
    assert draft.reason == "nothing unclear and nothing surprising"
    assert draft.posted_tally_id == "TALLY-1"
    assert len(after.list_our_vouchers(COMPANY)) == 1

    # the wrong account is now a line in the trial balance that was not there
    balance = after.trial_balance(COMPANY)
    assert balance["Purchases"] == AMOUNT_PAISE
    assert "Purchases" not in _tally(
        _history(LATIN_SHARMA, "Repairs & Maintenance")
    ).trial_balance(COMPANY)

    _assert_one_posted_row(store, draft, detail=f"Purchases {AMOUNT_PAISE} paise")

    # cleanup: reversible by operation id, and the books return to seeded state
    assert pipeline.reverse(draft, after) is True
    assert after.list_our_vouchers(COMPANY) == ()
    assert after.trial_balance(COMPANY) == _tally(
        _history(LATIN_SHARMA, "Repairs & Maintenance")
    ).trial_balance(COMPANY)


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
