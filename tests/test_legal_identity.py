"""A legal form is part of who was paid. D-05, decided by the owner 2026-08-10.

THE DECISION THIS FILE ENFORCES
-------------------------------
    Treat legal forms as meaningful by default. Do not silently merge Ltd,
    Pvt Ltd, LLP, Inc, Corp, or & Co. If identity is ambiguous, ask or hand
    over.

    Separate technical Unicode/whitespace normalisation from business
    identity. Do not destroy legal-form information during normalisation.

WHY THE TWO HALVES ARE NOT THE SAME HALF
----------------------------------------
"Acme Ltd" and "Acme LLP" can be two firms, two GSTINs, two sets of books and
two bank accounts. Merging them puts one firm's payment against the other's
name and nobody finds it until the year end.

"Bharat Steel Pvt. Ltd." and "Bharat Steel Pvt Ltd" are ONE supplier typed
twice. Splitting them makes the memory index useless: every second invoice
becomes a question and the person stops reading the questions.

So the rule has to cut between them, and the cut is:

    a difference in LEGAL FORM is meaningful
    a difference in PUNCTUATION, SPACING, CASE or UNICODE FORM is not

Punctuation is exactly what makes "Pvt. Ltd." and "Pvt Ltd" look different, so
the technical fold has to run FIRST and the legal form has to survive it. That
is the whole content of the owner's second sentence, and the pair
`Bharat Steel Pvt Ltd` / `Bharat Steel Ltd` is where a rule that only did one
of the two halves would give the wrong answer.

THE THIRD ANSWER, AND WHY IT EXISTS
-----------------------------------
"Acme & Co" and "Acme" are not provably different and not provably the same. A
partnership written short and a sole proprietor of the same name produce the
identical string. The owner named that case and said what to do with it: ask or
hand over. So the comparison has three answers, not two, and AMBIGUOUS is
treated as "not a match" everywhere it is consulted - never silently posted.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here is evidence about TallyPrime. It does not prove that Tally folds a
ledger name the way we do, that a legal form survives Tally's own round trip,
or that two names Tally treats as one supplier compare as one here.

It does not prove the LIVE pipeline honours D-05. Every test in this file
passed on 2026-08-10 while the live product still merged the Ltd and "& Co"
families, because the comparison was never handed the evidence: three writers
in `accountant/memory/company.py` threw the raw name away before the store ever
saw it. That is a storage and data-flow claim, not a comparison claim, and it
is proved end to end in `tests/test_legal_identity_live.py`.

EVIDENCE CLASS: direct calls to the functions under test. No Tally, real or
fake, is involved in any claim made here, apart from
`test_bootstrap_carries_the_raw_supplier_name_into_the_store`, which says so.
"""

from __future__ import annotations

import datetime
import sqlite3
import tempfile
import unicodedata
from pathlib import Path

import pytest

from accountant.memory.bootstrap import bootstrap
from accountant.memory.identity import (
    IdentityEvidence,
    SupplierVerdict,
    compare_recorded_supplier,
    compare_suppliers,
    legal_form,
    normalise_text,
    same_supplier,
)
from accountant.memory.index import MemoryIndex, normalise_vendor
from accountant.memory.store import MemoryStore
from accountant.schema import MatchStatus, Voucher
from accountant.tallyio.fake import FakeTally

SAME = SupplierVerdict.SAME
DIFFERENT = SupplierVerdict.DIFFERENT
AMBIGUOUS = SupplierVerdict.AMBIGUOUS

#: The collider pairs this repository has already measured. Two spellings of
#: ONE supplier every time - the differences are punctuation, spacing, an
#: address prefix written two ways, and a bracket. `Bharat Steel` is the one
#: that also carries a legal form, and it carries the SAME legal form on both
#: sides, which is why it belongs here and not below.
COLLIDING_PAIRS: tuple[tuple[str, str], ...] = (
    ("M/s Sharma Traders", "M.S. Sharma Traders"),
    ("Kumar Motors - Pune", "Kumar Motors Pune"),
    ("Dev Enterprises (Unit-II)", "Dev Enterprises Unit II"),
    ("Bharat Steel Pvt. Ltd.", "Bharat Steel Pvt Ltd"),
    ("Shree Balaji Enterprises [Old]", "Shree Balaji Enterprises Old"),
    ("Ganesh  Textiles", "Ganesh Textiles"),
)

#: STRONG forms: registered legal persons, and mutually exclusive. A firm
#: cannot be a Ltd and an LLP at once, so two different ones prove two
#: different suppliers. Pairwise distinctness is asserted, not just
#: distinctness from `Acme Ltd`, because any two of them colliding is the same
#: defect wearing a different pair of names.
STRONG_FORMS: dict[str, str] = {
    "Acme Ltd": "ltd",
    "Acme Pvt Ltd": "pvt_ltd",
    "Acme LLP": "llp",
    "Acme Inc": "inc",
    "Acme Corp": "corp",
}

#: The WEAK form. "& Co" is a trading style, not a registration: "Acme & Co"
#: can be how "Acme Pvt Ltd" writes itself on an invoice. It therefore never
#: proves difference, only ambiguity. Forced by the owner's own table on
#: 2026-08-10 - row 4 requires `Pvt Ltd` vs `& Co` to be AMBIGUOUS, which is
#: impossible if "& Co" is mutually exclusive with anything.
WEAK_FORM = "Acme & Co"

NAMED_FORMS: dict[str, str] = {**STRONG_FORMS, WEAK_FORM: "and_co"}

#: Two spellings of ONE legal form. A company writes its own form both ways on
#: its own invoices; that is a keyboard difference, not a different taxpayer.
FORM_SPELLINGS: tuple[tuple[str, str], ...] = (
    ("Acme Ltd", "Acme Limited"),
    ("Acme Pvt Ltd", "Acme Private Limited"),
    ("Acme Pvt Ltd", "Acme Pvt. Ltd."),
    ("Acme Corp", "Acme Corporation"),
    ("Acme Inc", "Acme Incorporated"),
    ("Acme & Co", "Acme and Co"),
    ("Acme & Co", "Acme Company"),
)


# ---------------------------------------------------------------------------
# the technical fold: it collapses presentation and keeps the legal form
# ---------------------------------------------------------------------------


def test_two_names_that_differ_only_in_case_are_one_supplier() -> None:
    """Shouting is not a second supplier."""
    assert compare_suppliers("SHARMA TRADERS", "Sharma Traders") is SAME
    assert same_supplier("SHARMA TRADERS", "Sharma Traders") is True


def test_two_names_that_differ_only_in_punctuation_are_one_supplier() -> None:
    """A trailing stop, a comma, a bracket, a hyphen. None changes who was paid."""
    assert compare_suppliers("Sharma Traders.", "Sharma Traders") is SAME
    assert compare_suppliers("Kumar Motors - Pune", "Kumar Motors, Pune") is SAME
    assert same_supplier("Sharma Traders.", "Sharma Traders") is True


def test_two_names_that_differ_only_in_spacing_are_one_supplier() -> None:
    """A double space is a thumb on the space bar."""
    assert compare_suppliers("Ganesh  Textiles", "Ganesh Textiles") is SAME
    assert compare_suppliers("  Ganesh Textiles\t", "Ganesh Textiles") is SAME


def test_the_nfc_and_nfd_spellings_of_an_accented_name_are_one_supplier() -> None:
    """Which encoding arrives depends on the keyboard. A person cannot see it."""
    nfc = unicodedata.normalize("NFC", "Café Supplies")
    nfd = unicodedata.normalize("NFD", "Café Supplies")

    assert nfc != nfd
    assert compare_suppliers(nfc, nfd) is SAME
    assert same_supplier(nfc, nfd) is True


def test_the_unaccented_spelling_is_a_different_supplier() -> None:
    """The disconfirming half of the test above. NFC is a fold, not a strip.

    If the accent were being thrown away rather than normalised, this pair
    would compare SAME and the test above would still pass. It is the pair that
    tells the two implementations apart.
    """
    accented = unicodedata.normalize("NFC", "Café Supplies")

    assert compare_suppliers(accented, "Cafe Supplies") is DIFFERENT
    assert same_supplier(accented, "Cafe Supplies") is False


@pytest.mark.parametrize(("written", "again"), COLLIDING_PAIRS)
def test_every_collider_this_repository_has_measured_is_one_supplier(
    written: str, again: str
) -> None:
    """The six pairs already measured here. One supplier typed twice, each time."""
    assert written != again
    assert compare_suppliers(written, again) is SAME
    assert compare_suppliers(again, written) is SAME
    assert same_supplier(written, again) is True


def test_the_ms_prefix_written_four_ways_reaches_one_supplier() -> None:
    """FIXED 2026-08-10, owner ruling D-05. Prefixes are matched after the fold.

    WHAT IT USED TO DO
        `normalise_vendor` matched the LITERAL strings "m/s", "ms." and
        "messrs" against the head of the raw name. "M.S. Sharma Traders" - the
        same salutation with stops instead of a slash - matched none of them,
        so it keyed as `m_s_sharma_traders` while "M/s Sharma Traders" keyed as
        `sharma_traders`. One supplier, two keys, and the second one never
        found its own history.

    WHAT IT DOES NOW
        The prefix list is matched against the FOLDED WORDS, and both "M/s" and
        "M.S." fold to the words ("m", "s") before anything looks at them. All
        four spellings reach one key and one identity, with no special case for
        the punctuation.
    """
    spellings = (
        "M/s Sharma Traders",
        "M.S. Sharma Traders",
        "Ms. Sharma Traders",
        "Messrs Sharma Traders",
    )
    for written in spellings:
        assert normalise_vendor(written) == "sharma_traders", written
        assert compare_suppliers(written, "Sharma Traders") is SAME, written
        assert same_supplier(written, "Sharma Traders") is True, written


# ---------------------------------------------------------------------------
# the legal form: meaningful by default
# ---------------------------------------------------------------------------


def test_a_limited_company_and_an_llp_are_never_the_same_supplier() -> None:
    """The pair the owner named first. Two taxpayers, two sets of books."""
    assert compare_suppliers("Acme Ltd", "Acme LLP") is DIFFERENT
    assert compare_suppliers("Acme LLP", "Acme Ltd") is DIFFERENT
    assert same_supplier("Acme Ltd", "Acme LLP") is False


def test_a_private_limited_and_a_limited_are_never_the_same_supplier() -> None:
    """A private limited company and a public one are two registrations."""
    assert compare_suppliers("Acme Pvt Ltd", "Acme Ltd") is DIFFERENT
    assert same_supplier("Acme Pvt Ltd", "Acme Ltd") is False


def test_an_inc_and_a_corp_are_never_the_same_supplier() -> None:
    assert compare_suppliers("Acme Inc", "Acme Corp") is DIFFERENT
    assert same_supplier("Acme Inc", "Acme Corp") is False


def test_every_strong_legal_form_is_distinct_from_every_other() -> None:
    """Pairwise, not just against `Acme Ltd`. Any two colliding is the defect."""
    forms = {name: legal_form(name) for name in NAMED_FORMS}

    assert forms == NAMED_FORMS
    assert len(set(forms.values())) == len(NAMED_FORMS)

    names = tuple(STRONG_FORMS)
    for i, one in enumerate(names):
        for other in names[i + 1 :]:
            assert compare_suppliers(one, other) is DIFFERENT, f"{one} / {other}"
            assert same_supplier(one, other) is False, f"{one} / {other}"


def test_a_trading_style_never_proves_difference_only_ambiguity() -> None:
    """ "& Co" against every STRONG form. AMBIGUOUS every time, never DIFFERENT.

    Owner ruling D-05, 2026-08-10, row 4 of the required table:
    `Sharma Traders Pvt Ltd` vs `Sharma Traders & Co` is AMBIGUOUS. That is
    only possible if "& Co" is a trading style rather than a registration - a
    Pvt Ltd may perfectly well trade as "& Co". Treating it as a sixth
    mutually-exclusive form would make every one of these DIFFERENT and would
    automatically SEPARATE two names that may be one firm, which claims
    certainty exactly as automatic merging does.
    """
    for name in STRONG_FORMS:
        assert compare_suppliers(WEAK_FORM, name) is AMBIGUOUS, name
        assert compare_suppliers(name, WEAK_FORM) is AMBIGUOUS, name
        assert same_supplier(WEAK_FORM, name) is False, name


@pytest.mark.parametrize(("written", "again"), FORM_SPELLINGS)
def test_two_spellings_of_one_legal_form_are_one_supplier(
    written: str, again: str
) -> None:
    """`Ltd` and `Limited` are one form. Splitting them costs a question for nothing."""
    assert written != again
    assert legal_form(written) == legal_form(again)
    assert compare_suppliers(written, again) is SAME
    assert same_supplier(written, again) is True


def test_the_pair_that_decides_whether_the_rule_is_worth_having() -> None:
    """Punctuation folds, the legal form does not, and both happen at once.

    `Bharat Steel Pvt. Ltd.` and `Bharat Steel Pvt Ltd` differ ONLY in
    punctuation and are one supplier. `Bharat Steel Pvt Ltd` and
    `Bharat Steel Ltd` differ in the legal form and are two. A rule that only
    folded punctuation would merge the second pair; a rule that only compared
    raw legal-form text would split the first. Get this wrong in either
    direction and the rule is useless.
    """
    punctuated = "Bharat Steel Pvt. Ltd."
    plain = "Bharat Steel Pvt Ltd"
    public = "Bharat Steel Ltd"

    assert compare_suppliers(punctuated, plain) is SAME
    assert compare_suppliers(plain, public) is DIFFERENT
    assert compare_suppliers(punctuated, public) is DIFFERENT

    assert same_supplier(punctuated, plain) is True
    assert same_supplier(plain, public) is False
    assert same_supplier(punctuated, public) is False


def test_a_different_name_is_a_different_supplier_whatever_the_legal_form() -> None:
    """The form only decides the case where the rest of the name already agrees."""
    assert compare_suppliers("Acme Ltd", "Bharat Steel Ltd") is DIFFERENT
    assert compare_suppliers("Acme Ltd", "Acme Traders Ltd") is DIFFERENT
    assert same_supplier("Acme Ltd", "Bharat Steel Ltd") is False


# ---------------------------------------------------------------------------
# the third answer: ambiguity, which is never a match
# ---------------------------------------------------------------------------


def test_a_firm_and_the_bare_name_are_not_merged_and_cannot_be_told_apart() -> None:
    """`Acme & Co` versus `Acme`. Never merged, and the honest answer is not "no".

    A partnership written short and a sole proprietor of the same name produce
    the identical string, so this is not provably different and not provably
    the same. The owner named the case: ask or hand over. What matters for
    safety is the second assertion - it is NOT a match, so nothing posts on it.
    """
    assert compare_suppliers("Acme & Co", "Acme") is AMBIGUOUS
    assert compare_suppliers("Acme", "Acme & Co") is AMBIGUOUS
    assert same_supplier("Acme & Co", "Acme") is False
    assert compare_suppliers("Acme & Co", "Acme") is not SAME


def test_a_stated_legal_form_against_a_bare_name_is_always_ambiguous() -> None:
    """Every form the owner named, each against the bare stem. None is a match."""
    for name in NAMED_FORMS:
        assert compare_suppliers(name, "Acme") is AMBIGUOUS, name
        assert same_supplier(name, "Acme") is False, name


def test_ambiguity_is_never_a_match() -> None:
    """The property the whole third answer exists for, asserted on its own.

    Every verdict that is not SAME must fail `same_supplier`. If AMBIGUOUS ever
    counted as a match, every bare name in the book would silently answer for
    every legal form of it.
    """
    for a, b in (("Acme & Co", "Acme"), ("Acme Ltd", "Acme"), ("Acme", "Acme Inc")):
        verdict = compare_suppliers(a, b)
        assert verdict is AMBIGUOUS
        assert same_supplier(a, b) is (verdict is SAME)
        assert same_supplier(a, b) is False


def test_a_name_that_is_only_a_legal_form_is_never_a_confident_match() -> None:
    """A bare "Ltd" is not a supplier. Two of them are not one supplier either.

    Without this the empty stem matches the empty stem and a row recorded
    against a junk name answers for every other junk name in the book.
    """
    assert compare_suppliers("Ltd", "Ltd") is AMBIGUOUS
    assert compare_suppliers("Ltd", "LLP") is AMBIGUOUS
    assert compare_suppliers("", "") is AMBIGUOUS
    assert compare_suppliers("   ", "Acme Ltd") is AMBIGUOUS
    assert same_supplier("Ltd", "Ltd") is False


def test_a_name_compared_with_itself_is_the_same_supplier() -> None:
    """The floor. Anything that fails this refuses the vendor it just recorded."""
    for name in (*NAMED_FORMS, "Sharma Traders", "Café Supplies"):
        assert compare_suppliers(name, name) is SAME, name
        assert same_supplier(name, name) is True, name


# ---------------------------------------------------------------------------
# the separation the owner asked for, asserted as a property of the fold
# ---------------------------------------------------------------------------


def test_the_technical_fold_removes_no_word_at_all() -> None:
    """The owner's second sentence, as a property: no word is destroyed.

    `normalise_vendor` deletes whole words. This one deletes none: every word
    that went in comes out, and only the characters between them change.
    """
    assert normalise_text("Bharat Steel Pvt. Ltd.") == "bharat steel pvt ltd"
    assert normalise_text("M/s Sharma Traders & Co") == "m s sharma traders co"
    assert normalise_text("Dev Enterprises (Unit-II)") == "dev enterprises unit ii"
    assert normalise_text("Ganesh  Textiles") == "ganesh textiles"


def test_the_technical_fold_keeps_every_legal_form_readable_afterwards() -> None:
    """The fold runs first, so the form has to still be there when it is over."""
    for name, expected in NAMED_FORMS.items():
        assert legal_form(normalise_text(name)) == expected, name
        assert legal_form(name) == expected, name


def test_a_name_with_no_legal_form_reports_none() -> None:
    """`legal_form` says "" rather than guessing at one."""
    assert legal_form("Sharma Traders") == ""
    assert legal_form("Kumar Motors Pune") == ""
    assert legal_form("") == ""


def test_the_technical_fold_is_deterministic_and_idempotent() -> None:
    """Folding twice must not move. A key that drifts is a key that splits."""
    for name in (*NAMED_FORMS, *(a for a, _ in COLLIDING_PAIRS), "Café Supplies"):
        once = normalise_text(name)
        assert normalise_text(once) == once, name


# ---------------------------------------------------------------------------
# the index: two names in one bucket do not have to be one supplier
# ---------------------------------------------------------------------------


def test_the_index_refuses_accounts_recorded_against_a_different_legal_form() -> None:
    """Two legal persons, two keys AND two answers. Belt and braces, on purpose.

    The key CANONICALISES the legal form rather than deleting it, so these two
    never share a bucket in the first place - asserted below, because "they
    cannot collide" is a stronger guarantee than "they collide and we sort it
    out afterwards", and it is the guarantee the store path relies on since it
    applies no identity check of its own.

    The decision layer refuses as well. Both are asserted so that widening the
    key later cannot silently remove the protection.
    """
    assert normalise_vendor("Bharat Steel Pvt Ltd") == "bharat_steel_pvt_ltd"
    assert normalise_vendor("Bharat Steel Ltd") == "bharat_steel_ltd"
    assert normalise_vendor("Bharat Steel Pvt Ltd") != normalise_vendor(
        "Bharat Steel Ltd"
    )

    index = MemoryIndex()
    index.record("Bharat Steel Pvt Ltd", "Purchases")

    refused = index.lookup("Bharat Steel Ltd")

    assert refused.status is MatchStatus.NO_MATCH
    assert refused.status is not MatchStatus.MATCH
    assert refused.accounts == ()
    assert index.times_posted("Bharat Steel Ltd", "Purchases") == 0
    # and the decision layer says so independently of the key
    assert compare_suppliers("Bharat Steel Pvt Ltd", "Bharat Steel Ltd") is DIFFERENT


def test_the_index_answers_for_the_same_legal_form_spelled_two_ways() -> None:
    """The other direction, so the refusal above is not just "refuse everything"."""
    index = MemoryIndex()
    index.record("Bharat Steel Pvt Ltd", "Purchases")

    found = index.lookup("Bharat Steel Pvt. Ltd.")

    assert found.status is MatchStatus.MATCH
    assert found.accounts == ("Purchases",)
    assert index.times_posted("Bharat Steel Pvt. Ltd.", "Purchases") == 1


def test_the_index_keeps_each_legal_forms_history_to_itself() -> None:
    """Two suppliers, one bucket, two histories. Neither answers for the other."""
    index = MemoryIndex()
    for _ in range(3):
        index.record("Acme Pvt Ltd", "Purchases")
    index.record("Acme Ltd", "Rent")

    assert index.lookup("Acme Pvt Ltd").accounts == ("Purchases",)
    assert index.lookup("Acme Ltd").accounts == ("Rent",)
    assert index.lookup("Acme Pvt Ltd").status is MatchStatus.MATCH
    assert index.lookup("Acme Ltd").status is MatchStatus.MATCH
    assert index.times_posted("Acme Pvt Ltd", "Rent") == 0
    assert index.times_posted("Acme Ltd", "Purchases") == 0


def test_the_index_still_conflicts_when_one_supplier_used_two_accounts() -> None:
    """The legal-form filter must not quietly resolve a genuine conflict."""
    index = MemoryIndex()
    index.record("Acme Pvt Ltd", "Purchases")
    index.record("Acme Pvt. Ltd.", "Repairs")

    conflicted = index.lookup("Acme Pvt Ltd")

    assert conflicted.status is MatchStatus.CONFLICTED
    assert set(conflicted.accounts) == {"Purchases", "Repairs"}


def test_the_index_still_answers_a_bare_name_from_a_bare_history() -> None:
    """No legal form on either side is not ambiguity. It is one supplier."""
    index = MemoryIndex()
    index.record("Sharma Traders", "Purchases")

    assert index.lookup("SHARMA TRADERS.").accounts == ("Purchases",)
    assert index.lookup("Sharma Traders").status is MatchStatus.MATCH


# ---------------------------------------------------------------------------
# what is NOT fixed, asserted so it cannot drift
# ---------------------------------------------------------------------------


def test_the_vendor_key_no_longer_merges_the_ltd_family() -> None:
    """REWRITTEN 2026-08-10, owner ruling D-05. It used to pin the merge.

    WHAT THIS TEST USED TO REQUIRE
        That "Acme Ltd", "Acme Private Limited", "Acme & Co" and a bare "Acme"
        all key as `acme`. It pinned the merge deliberately, as a recorded gap,
        because three assertions in files owned elsewhere required it and
        splitting them needed an owner decision.

    WHY THAT CHANGED
        The owner made the decision. Legal forms are identity-bearing and must
        never be silently removed. So the key CANONICALISES the form instead of
        deleting it, and the four names are now four keys.

    WHY CANONICALISE RATHER THAN JUST KEEP THE WORDS
        Because "Acme Ltd" and "Acme Limited" are one supplier writing its own
        form two ways. Keeping the raw words would split them and cost a
        question for nothing; canonicalising merges the spellings and keeps the
        forms apart, which is the whole point.
    """
    keys = {
        "Acme Ltd": "acme_ltd",
        "Acme Limited": "acme_ltd",
        "Acme Private Limited": "acme_pvt_ltd",
        "Acme Pvt. Ltd.": "acme_pvt_ltd",
        "Acme & Co": "acme_and_co",
        "Acme and Co": "acme_and_co",
        "Acme Company": "acme_and_co",
        "Acme Corp": "acme_corp",
        "Acme Corporation": "acme_corp",
        "Acme": "acme",
    }
    for name, expected in keys.items():
        assert normalise_vendor(name) == expected, name

    # five legal persons, five keys - "Acme Ltd", "Acme Pvt Ltd", "Acme & Co",
    # "Acme Corp" and the bare "Acme". The merge this test used to pin folded
    # the first three and the bare name onto one.
    assert len({normalise_vendor(n) for n in keys}) == 5

    # and the identity layer agrees with the key on every one of them
    assert compare_suppliers("Acme Ltd", "Acme Private Limited") is DIFFERENT
    assert compare_suppliers("Acme Ltd", "Acme & Co") is AMBIGUOUS
    assert compare_suppliers("Acme Ltd", "Acme") is AMBIGUOUS
    assert compare_suppliers("Acme Ltd", "Acme Limited") is SAME


def test_a_key_and_the_name_it_came_from_are_the_same_supplier() -> None:
    """The key is identity-preserving, so it compares SAME with its own name.

    This is why the key was never widened: `CompanyMemory.lookup` goes straight
    to the store and applies no identity check, so the key has to carry the
    stem AND the canonical form or two legal persons become one subject on that
    path. Underscores are separators to the fold, so a key round-trips.

    It is NOT a licence to feed a key to the decision layer as though it were a
    name. `CompanyMemory.index` used to do exactly that, and it promoted every
    evidence-less row to a confident match; it now passes the stored key and
    the stored raw name as two fields. See `tests/test_legal_identity_live.py`.
    """
    for name in ("Acme LLP", "Sharma Traders", "Bharat Steel Pvt Ltd", "Acme & Co"):
        assert compare_suppliers(normalise_vendor(name), name) is SAME, name


def test_an_incomplete_row_is_never_a_confident_match() -> None:
    """No raw name means no evidence, and no evidence means no merge.

    A row written before `raw_subject` existed carries only a key the strip has
    already been through. The tempting shortcut is "the query states no form
    either, so nothing is in play" - and that is exactly the inference the
    owner forbade, because the vanished name may well have been a Pvt Ltd and a
    bare name against a Pvt Ltd is itself AMBIGUOUS.
    """
    assert compare_recorded_supplier(None, "Sharma Traders") is AMBIGUOUS
    assert compare_recorded_supplier(None, "Sharma Traders Pvt Ltd") is AMBIGUOUS
    assert compare_recorded_supplier(None, "") is AMBIGUOUS
    assert compare_recorded_supplier("Sharma Traders", "Sharma Traders") is SAME

    index = MemoryIndex()
    index.record_observed(
        normalised_subject="sharma_traders", raw_subject=None, account="Purchases"
    )

    blind = index.lookup("Sharma Traders")

    assert blind.status is MatchStatus.NO_MATCH
    assert blind.accounts == ()
    assert index.times_posted("Sharma Traders", "Purchases") == 0


def test_the_store_keeps_the_raw_name_and_reports_its_own_evidence() -> None:
    """`raw_subject` survives the round trip and `identity_evidence` follows it."""
    store = MemoryStore()
    store.record_vendor(
        "co",
        normalise_vendor("M/s Sharma Traders Pvt Ltd"),
        "Purchases",
        provenance="test",
        raw_subject="M/s Sharma Traders Pvt Ltd",
    )

    (back,) = store.vendor("co", "sharma_traders_pvt_ltd")

    assert back.raw_subject == "M/s Sharma Traders Pvt Ltd"
    assert back.identity_evidence is IdentityEvidence.COMPLETE
    assert compare_recorded_supplier(back.raw_subject, "Sharma Traders Pvt Ltd") is SAME


def test_a_row_written_before_the_column_existed_reads_back_incomplete() -> None:
    """The migration adds the column and leaves old rows NULL, which is the truth.

    Backfilling `raw_subject` from `subject` would manufacture the evidence:
    the key was produced by a strip and the legal form is not in it. So the row
    says INCOMPLETE and is never a confident match.
    """
    store = MemoryStore()
    store.record_vendor("co", "sharma_traders", "Purchases", provenance="test")

    (back,) = store.vendor("co", "sharma_traders")

    assert back.raw_subject is None
    assert back.identity_evidence is IdentityEvidence.INCOMPLETE
    assert compare_recorded_supplier(back.raw_subject, "Sharma Traders") is AMBIGUOUS


def test_the_migration_adds_the_column_to_a_database_that_predates_it() -> None:
    """An old file opens, gains the column, keeps its rows, and reports honestly."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.sqlite"
        old = sqlite3.connect(str(path))
        old.execute(
            "CREATE TABLE vendor_account ("
            "company_key TEXT NOT NULL, subject TEXT NOT NULL, "
            "account TEXT NOT NULL, times INTEGER NOT NULL, "
            "source_voucher_ids TEXT NOT NULL, provenance TEXT NOT NULL, "
            "PRIMARY KEY (company_key, subject, account))"
        )
        old.execute(
            "INSERT INTO vendor_account VALUES ('co', 'sharma_traders', "
            "'Purchases', 4, '[]', 'tally_history')"
        )
        old.commit()
        old.close()

        store = MemoryStore(path)
        (back,) = store.vendor("co", "sharma_traders")

        # the row survived, untouched
        assert back.times == 4
        assert back.account == "Purchases"
        # and it tells the truth about what it cannot prove
        assert back.raw_subject is None
        assert back.identity_evidence is IdentityEvidence.INCOMPLETE
        store.close()


def test_evidence_is_only_ever_gained_never_lost() -> None:
    """A later write that knows no raw name must not erase one already stored."""
    store = MemoryStore()
    store.record_vendor(
        "co",
        "sharma_traders",
        "Purchases",
        provenance="test",
        raw_subject="Sharma Traders",
    )
    store.record_vendor("co", "sharma_traders", "Purchases", provenance="test")

    (back,) = store.vendor("co", "sharma_traders")

    assert back.times == 2
    assert back.raw_subject == "Sharma Traders"
    assert back.identity_evidence is IdentityEvidence.COMPLETE


def test_bootstrap_carries_the_raw_supplier_name_into_the_store() -> None:
    """The producer end, through the public surface. Tally -> store, raw name intact.

    Without this every stored row would be INCOMPLETE and the decision layer
    would be permanently blind: `bootstrap` is the only thing that ever sees
    `Voucher.party` before the key is built.
    """
    tally = FakeTally()
    tally.add_company(
        "Demo Co",
        accounts=("Purchases", "Cash"),
        vouchers=(
            Voucher(
                id="v1",
                date=datetime.date(2026, 3, 1),
                party="M/s Sharma Traders Pvt Ltd",
                narration="cement",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=100000,
            ),
        ),
    )
    store = MemoryStore()
    memory = bootstrap(tally, "Demo Co", store)

    (row,) = store.vendors(memory.identity.key)

    assert row.subject == "sharma_traders_pvt_ltd"
    assert row.raw_subject == "M/s Sharma Traders Pvt Ltd"
    assert row.identity_evidence is IdentityEvidence.COMPLETE
    assert compare_recorded_supplier(row.raw_subject, "Sharma Traders Pvt Ltd") is SAME
    assert compare_recorded_supplier(row.raw_subject, "Sharma Traders") is AMBIGUOUS


# ---------------------------------------------------------------------------
# the owner's required table, D-05, 2026-08-10
# ---------------------------------------------------------------------------

#: Exactly the six rows the owner wrote, in the owner's order.
SHARMA_TABLE: tuple[tuple[str, str, SupplierVerdict], ...] = (
    ("Sharma Traders", "M/s Sharma Traders", SAME),
    ("Sharma Traders Pvt Ltd", "M/s Sharma Traders Pvt Ltd", SAME),
    ("Sharma Traders & Co", "Ms. Sharma Traders & Co", SAME),
    ("Sharma Traders Pvt Ltd", "Sharma Traders & Co", AMBIGUOUS),
    ("Sharma Traders", "Sharma Traders Pvt Ltd", AMBIGUOUS),
    ("Sharma Traders", "Sharma Traders & Co", AMBIGUOUS),
)


@pytest.mark.parametrize(("left", "right", "expected"), SHARMA_TABLE)
def test_the_owners_required_table_row_by_row(
    left: str, right: str, expected: SupplierVerdict
) -> None:
    """The ruling, asserted as the owner wrote it. Symmetric in both directions."""
    assert compare_suppliers(left, right) is expected, f"{left} / {right}"
    assert compare_suppliers(right, left) is expected, f"{right} / {left}"
    assert same_supplier(left, right) is (expected is SAME)


def test_no_row_of_the_owners_table_is_ever_different() -> None:
    """The disconfirming check. A prefix is noise; "& Co" never proves apartness.

    If "& Co" were treated as a registration, rows 4 and 6 would come back
    DIFFERENT and the table above would still look reasonable read quickly.
    This asserts the thing that would actually change.
    """
    for left, right, _ in SHARMA_TABLE:
        assert compare_suppliers(left, right) is not DIFFERENT, f"{left} / {right}"


def test_an_ambiguous_supplier_cannot_reach_an_automatic_posting() -> None:
    """Required regression 12. Ambiguity asks; it never posts.

    Asserted at the index, which is the layer that answers "what account did
    this vendor use". No confident match means no accounts, and no accounts is
    what `propose_account` turns into a question rather than a posting.
    """
    index = MemoryIndex()
    for _ in range(40):
        index.record("Sharma Traders", "Purchases")

    for query in ("Sharma Traders Pvt Ltd", "Sharma Traders & Co"):
        answer = index.lookup(query)
        assert compare_suppliers("Sharma Traders", query) is AMBIGUOUS, query
        assert answer.status is MatchStatus.NO_MATCH, query
        assert answer.accounts == (), query


def test_an_ambiguous_supplier_cannot_silently_merge_a_history() -> None:
    """Required regression 13. Forty vouchers of history answer for nobody else."""
    index = MemoryIndex()
    for _ in range(40):
        index.record("Sharma Traders Pvt Ltd", "Purchases")
    index.record("Sharma Traders & Co", "Repairs")

    assert index.lookup("Sharma Traders Pvt Ltd").accounts == ("Purchases",)
    assert index.lookup("Sharma Traders & Co").accounts == ("Repairs",)
    assert index.times_posted("Sharma Traders & Co", "Purchases") == 0
    assert index.times_posted("Sharma Traders Pvt Ltd", "Repairs") == 0
    assert index.lookup("Sharma Traders").status is MatchStatus.NO_MATCH
