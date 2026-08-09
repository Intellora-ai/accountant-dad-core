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

It does not prove the LIVE pipeline honours D-05 for the Ltd/&Co families.
It does not, and `test_the_vendor_key_still_merges_the_ltd_family_today`
asserts the gap rather than hiding it. `normalise_vendor` still strips those
families, `accountant/memory/company.py:300` feeds `MemoryIndex` the stripped
key rather than the name Tally gave, and the store keeps no raw name, so by the
time the live lookup happens the legal form has already been thrown away. The
three tests that pin the strip are owned elsewhere. See the report.

EVIDENCE CLASS: direct calls to the functions under test. No Tally, real or
fake, is involved in any claim made here.
"""

from __future__ import annotations

import unicodedata

import pytest

from accountant.memory.identity import (
    SupplierVerdict,
    compare_suppliers,
    legal_form,
    normalise_text,
    same_supplier,
)
from accountant.memory.index import MemoryIndex, normalise_vendor
from accountant.schema import MatchStatus

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

#: One stem, six legal forms, six legal persons. Every one of these was named
#: by the owner in D-05. Pairwise distinctness is asserted, not just
#: distinctness from `Acme Ltd`, because any two of them colliding is the same
#: defect wearing a different pair of names.
NAMED_FORMS: dict[str, str] = {
    "Acme Ltd": "ltd",
    "Acme Pvt Ltd": "pvt_ltd",
    "Acme LLP": "llp",
    "Acme Inc": "inc",
    "Acme Corp": "corp",
    "Acme & Co": "and_co",
}

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


def test_the_ms_prefix_written_two_ways_reaches_one_supplier() -> None:
    """Called out because `normalise_vendor` gets this pair WRONG today.

    `M/s` is stripped by a literal prefix match, so `M.S.` - the same
    salutation with stops instead of a slash - misses the match and keys
    separately. The technical fold has no prefix list: it turns both into the
    same characters before anything looks at them, so the pair lands together
    without a special case.
    """
    assert compare_suppliers("M/s Sharma Traders", "M.S. Sharma Traders") is SAME
    assert normalise_vendor("M/s Sharma Traders") != normalise_vendor(
        "M.S. Sharma Traders"
    )


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


def test_every_named_legal_form_is_distinct_from_every_other() -> None:
    """Pairwise, not just against `Acme Ltd`. Any two colliding is the defect."""
    forms = {name: legal_form(name) for name in NAMED_FORMS}

    assert forms == NAMED_FORMS
    assert len(set(forms.values())) == len(NAMED_FORMS)

    names = tuple(NAMED_FORMS)
    for i, one in enumerate(names):
        for other in names[i + 1 :]:
            assert compare_suppliers(one, other) is DIFFERENT, f"{one} / {other}"
            assert same_supplier(one, other) is False, f"{one} / {other}"


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
    """The bucket is shared and the answer is not. This is the whole point.

    `normalise_vendor` strips the Ltd family, so `Bharat Steel Pvt Ltd` and
    `Bharat Steel Ltd` land on ONE key - asserted below so the shared bucket is
    a stated fact rather than an assumption. The index still refuses to answer
    the Ltd with the Pvt Ltd's account, because it kept the name it was given
    and compares it.
    """
    assert normalise_vendor("Bharat Steel Pvt Ltd") == normalise_vendor(
        "Bharat Steel Ltd"
    )

    index = MemoryIndex()
    index.record("Bharat Steel Pvt Ltd", "Purchases")

    refused = index.lookup("Bharat Steel Ltd")

    assert refused.status is MatchStatus.NO_MATCH
    assert refused.status is not MatchStatus.MATCH
    assert refused.accounts == ()
    assert index.times_posted("Bharat Steel Ltd", "Purchases") == 0


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


def test_the_vendor_key_still_merges_the_ltd_family_today() -> None:
    """PINNED, NOT ENDORSED. D-05 is not honoured on the live lookup path.

    WHAT THE OWNER DECIDED
        Do not silently merge Ltd, Pvt Ltd, LLP, Inc, Corp or & Co.

    WHAT `normalise_vendor` STILL DOES
        Strips the Ltd/Limited and "& Co"/Company families off the end of the
        name, so all four names below reach one key.

    WHY IT WAS NOT CHANGED HERE
        Three assertions in two files owned elsewhere require exactly this
        merge, and one of them requires it at the LOOKUP level, not just on the
        key:

            tests/test_memory.py:1001-1006  the key of "M/s Sharma Traders Pvt
                Ltd", "Messrs Sharma Traders Private Limited" and "Ms. Sharma
                Traders & Co" must equal `sharma_traders`
            tests/test_memory.py:646-653    company B's "Sharma Traders" and
                "M/s Sharma Traders Pvt Ltd" must be ONE vendor, posted twice
            tests/test_adversarial_identity.py:772-775  the same merge, pinned
                and already reported there as blocked on an owner decision

        Even with those three changed, the legal form would still not reach the
        live lookup: `accountant/memory/company.py:300` builds the index from
        `Observation.subject`, which is the already-stripped key, and the store
        keeps no raw name. Both files are owned elsewhere. See the report.

    WHY THE INDEX TESTS ABOVE STILL PASS
        They hand `MemoryIndex` the raw name, which is what
        `MemoryIndex.from_vouchers` does. The filter is real wherever the raw
        name survives; it is a no-op wherever the key arrives already stripped,
        because a stripped key states no legal form and states-no-form is
        AMBIGUOUS, not DIFFERENT.
    """
    merged = ("Acme Ltd", "Acme Private Limited", "Acme & Co", "Acme")

    assert {normalise_vendor(n) for n in merged} == {"acme"}

    # and the identity rule disagrees with every one of those merges
    assert compare_suppliers("Acme Ltd", "Acme Private Limited") is DIFFERENT
    assert compare_suppliers("Acme Ltd", "Acme & Co") is DIFFERENT
    assert compare_suppliers("Acme Ltd", "Acme") is AMBIGUOUS
    assert same_supplier("Acme Ltd", "Acme Private Limited") is False


def test_a_stripped_key_states_no_legal_form_so_it_refuses_nothing() -> None:
    """The exact shape of the no-op above, asserted rather than asserted about.

    `CompanyMemory.index()` records `Observation.subject`. By then "Acme LLP"
    is `acme_llp` and "Acme Ltd" is `acme`. The first still compares SAME with
    the name it came from - underscores are separators to the fold - and the
    second states no form at all, so it can only ever reach AMBIGUOUS. Nothing
    the live path does is made stricter by the filter, and nothing is broken by
    it either.
    """
    assert compare_suppliers("acme_llp", "Acme LLP") is SAME
    assert compare_suppliers("sharma_traders", "Sharma Traders") is SAME
    assert compare_suppliers(normalise_vendor("Acme Ltd"), "Acme Ltd") is AMBIGUOUS
    assert compare_suppliers(normalise_vendor("Acme Ltd"), "Acme LLP") is AMBIGUOUS

    index = MemoryIndex()
    index.record(normalise_vendor("Acme Ltd"), "Purchases")

    assert index.lookup("Acme LLP").status is MatchStatus.NO_MATCH  # different key
    assert index.lookup("Acme Ltd").accounts == ("Purchases",)
