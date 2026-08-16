"""One VISIBLE company name is one company, whatever bytes produced it.

WHY A WHOLE FILE ABOUT ENCODING
-------------------------------
`company_key` is the first column of every primary key in the store, the writer
on the `company` table is `INSERT OR REPLACE`, and both cross-company guards in
`accountant/pipeline.py` compare these same keys. So the key is not a detail of
spelling. Get it wrong in either direction and something breaks that nothing
downstream can catch:

    two names that LOOK the same keying DIFFERENTLY
        the same company is two companies. Its index is split, its audit trail
        is split, and the app can tell a person that the company they are
        looking at in Tally is not open.

    two names that ARE different keying the SAME
        one company's index silently replaces the other's, and the guards that
        exist to notice cannot fire, because they compare the key that
        collided.

`tests/test_adversarial_identity.py` proves the accented case for ONE pair, at
the level of `normalise_company` and `bootstrap`. This file widens it: the
other ways Unicode makes two strings look alike or look different - full width,
zero width, non-breaking space, case, and a fold that emits a combining mark
after the normalisation has already run - and it takes them through the running
app rather than through the function alone.

THE RULE BEING TESTED, IN ONE LINE
----------------------------------
Two names that look the same must resolve the same. Two names that are
different must never collide silently: the collision must FAIL CLOSED and NAME
BOTH ORIGINAL NAMES, so a person can act, and it must never pick one.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real TallyPrime, and nothing about what a real Tally gateway
does with non-ASCII company names over XML. EVIDENCE CLASS: FAKETALLY over real
HTTP, plus direct calls to `normalise_company` and `bootstrap` where the claim
is about the function itself.
"""

from __future__ import annotations

import datetime
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant.memory.bootstrap import bootstrap
from accountant.memory.identity import normalise_company
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app
from tests.test_period_handoff import open_books_for

# Every non-ASCII character below is written as an escape on purpose. Several
# of them are invisible, and a test fixture whose contents cannot be read in the
# source is a fixture nobody can check.
#
# One visible name, three ways. NFC is the precomposed U+00E9; NFD is "e" plus
# U+0301 COMBINING ACUTE ACCENT; PLAIN is a DIFFERENT company that happens to be
# spelt without the accent.
CAFE_NFC = unicodedata.normalize("NFC", "Caf\u00e9 Exports")
CAFE_NFD = unicodedata.normalize("NFD", "Caf\u00e9 Exports")
CAFE_PLAIN = "Cafe Exports"

# U+FF21.. FULLWIDTH LATIN CAPITAL LETTERS. NFC does NOT fold these to ASCII -
# only the compatibility forms do - so they key separately, which is the
# conservative answer and the one this module wants: "removes punctuation and
# nothing else".
FULLWIDTH_ACME = "\uff21\uff23\uff2d\uff25 Traders"
ASCII_ACME = "ACME Traders"

# The three invisibles a copy-paste out of a spreadsheet or a website actually
# carries. None is `\w` and none is `\s`, so `_PUNCT` turns each into a space.
ZERO_WIDTH = {
    "zero width joiner U+200D": "Acme\u200dTraders",
    "zero width non-joiner U+200C": "Acme\u200cTraders",
    "zero width space U+200B": "Acme\u200bTraders",
}
ACME_SPACED = "Acme Traders"

# U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE. `casefold()` expands it to
# "i" + U+0307 COMBINING DOT ABOVE, and `casefold` runs AFTER the NFC fold, so
# a combining mark reaches `_PUNCT` after all and becomes a space.
DOTTED_I_ITC = "\u0130TC Traders"
SPACED_I_TC = "I TC Traders"
DOTTED_I_INCI = "\u0130nci Traders"

#: The collider pairs this repository has already measured, taken through the
#: running app rather than through `bootstrap` alone.
COLLIDING_PAIRS: tuple[tuple[str, str], ...] = (
    ("M/s Sharma Traders", "M.S. Sharma Traders"),
    ("Kumar Motors - Pune", "Kumar Motors Pune"),
    ("Dev Enterprises (Unit-II)", "Dev Enterprises Unit II"),
    ("Bharat Steel Pvt. Ltd.", "Bharat Steel Pvt Ltd"),
    ("Shree Balaji Enterprises [Old]", "Shree Balaji Enterprises Old"),
    ("Ganesh  Textiles", "Ganesh Textiles"),
)

#: Names that reduce to nothing at all. A key of "" would pool every one of
#: them into one scope, which is the worst possible collision.
NO_IDENTITY = {
    "an emoji": "\U0001f3e2",
    "punctuation only": "!!!",
    "invisible only": "\u200d\u200b",
    "whitespace only": "   ",
}

ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")
PARTY = "Sharma Traders"
ENTRY = "paid Sharma Traders 4200 for cement"


def history(account: str = "Purchases") -> tuple[Voucher, ...]:
    """Enough consistent history that `PARTY` posts straight through."""
    return tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
            party=PARTY,
            narration="cement supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=380000 + i * 1000,
        )
        for i in range(12)
    )


def tally_with(*companies: tuple[str, str]) -> FakeTally:
    """One Tally, several companies open, each posting to its own account."""
    tally = FakeTally()
    for name, account in companies:
        tally.add_company(
            name, accounts=ACCOUNTS, vouchers=history(account), backed_up=True
        )
    return tally


def identity_for(company: str, *, visible: int = 1) -> BackendIdentity:
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_company_unicode.py",
        company=company,
        company_exists=True,
        companies_visible=visible,
        run_id=new_run_id(),
    )


@pytest.fixture(autouse=True)
def no_runtime_leaks() -> Iterator[None]:
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()
    yield
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()


def serve_once(tally: FakeTally, company: str, *, visible: int = 1) -> _Live:
    """A real server bound to one company, answering one request per call.

    The store is opened here and SQLite hands a connection to the thread that
    opened it, so the SERVER runs on the test thread and the CLIENT moves off
    it. Same arrangement as `tests/test_company_routes.py`, and for the same
    reason.
    """
    store = MemoryStore(":memory:")
    # Without a reader `Runtime.period_open` stays `None` - "nobody looked" - and
    # the cage refuses every posting with "I could not tell whether the books for
    # this date are still open", so the end-to-end test never reaches a post.
    # THE ARGUMENT IS `company`, THE PARAMETER, AND IT IS NOT NORMALISED HERE.
    # `parse_company_periods` matches the name byte-for-byte against the one the
    # identity carries; handing it `app.COMPANY`, or an NFC-folded copy of an NFD
    # name, is NO MATCH, which reads as UNVERIFIED and blocks exactly as before.
    # That this file's NFD name survives the round trip is the point of the file.
    app.configure(
        tally,
        identity_for(company, visible=visible),
        store=store,
        period_reader=open_books_for(company),
    )
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    httpd.timeout = 5
    return _Live(httpd, store)


class _Live:
    def __init__(self, httpd: HTTPServer, store: MemoryStore) -> None:
        self.httpd = httpd
        self.store = store
        self._base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(self, path: str = "/") -> tuple[int, str]:
        return self._round_trip(path, None)

    def post(self, path: str, **fields: str) -> tuple[int, str]:
        return self._round_trip(path, urllib.parse.urlencode(fields).encode())

    def close(self) -> None:
        self.httpd.server_close()

    def _round_trip(self, path: str, data: bytes | None) -> tuple[int, str]:
        answered: list[tuple[int, str]] = []

        def ask() -> None:
            request = urllib.request.Request(self._base + path, data=data)  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=5) as reply:  # noqa: S310
                    answered.append((reply.status, reply.read().decode()))
            except urllib.error.HTTPError as refused:
                answered.append((refused.code, refused.read().decode()))

        caller = threading.Thread(target=ask, daemon=True)
        caller.start()
        self.httpd.handle_request()
        caller.join(timeout=5)
        assert answered, f"the server never answered {path!r}"
        return answered[0]


# ---- the premise: these strings really are what the tests assume ------------


def test_the_two_encodings_of_one_accented_name_really_are_different_bytes() -> None:
    """Without this, every NFC/NFD claim below could be comparing one string.

    If a future Python or a future editor normalises these literals, this goes
    red and tells the next reader the fixtures need rechoosing, rather than
    letting the encoding tests pass because there was no encoding difference.
    """
    assert CAFE_NFC != CAFE_NFD, "the two spellings are the same bytes"
    assert len(CAFE_NFD) == len(CAFE_NFC) + 1, "NFD carries a separate mark"
    assert unicodedata.normalize("NFC", CAFE_NFD) == CAFE_NFC
    assert CAFE_NFC != CAFE_PLAIN and CAFE_NFD != CAFE_PLAIN


# ---- two names that look the same must resolve the same ---------------------


def test_one_visible_company_name_keys_the_same_whichever_encoding_typed_it() -> None:
    """NFC and NFD of one visible name are one company, and not the plain one."""
    assert normalise_company(CAFE_NFC) == normalise_company(CAFE_NFD)
    assert normalise_company(CAFE_NFC) == "caf\u00e9_exports"
    assert normalise_company(CAFE_PLAIN) == "cafe_exports"
    assert normalise_company(CAFE_NFD) != normalise_company(CAFE_PLAIN), (
        "the accented company borrowed the unaccented company's scope"
    )


def test_case_alone_never_makes_a_second_company() -> None:
    """Tally lets a person retype a company name in a different case."""
    spellings = (
        "Ganesh Textiles",
        "GANESH TEXTILES",
        "ganesh textiles",
        "GaNeSh tExTiLeS",
    )
    keys = {normalise_company(s) for s in spellings}

    assert keys == {"ganesh_textiles"}, f"case split one company into {keys}"


WHITESPACE_SPELLINGS = {
    "leading and trailing": "   Ganesh Textiles   ",
    "doubled": "Ganesh  Textiles",
    "tab": "Ganesh\tTextiles",
    "newline": "Ganesh\nTextiles",
    "non-breaking space U+00A0": "Ganesh\u00a0Textiles",
}


@pytest.mark.parametrize(
    "spelling", list(WHITESPACE_SPELLINGS.values()), ids=list(WHITESPACE_SPELLINGS)
)
def test_whitespace_visible_or_invisible_never_makes_a_second_company(
    spelling: str,
) -> None:
    """All of these render as one name, or as near to it as makes no difference.

    A non-breaking space is the one that arrives by accident: it is what a
    paste out of a web page or a Word document carries, and it is invisible.
    """
    assert normalise_company(spelling) == normalise_company("Ganesh Textiles")


# ---- two names that are different must never collide silently ---------------


def test_a_full_width_company_name_never_borrows_the_ascii_companys_books() -> None:
    """Full width is a compatibility difference, and NFC does not fold it.

    That is the conservative answer and the right one here: `normalise_company`
    removes punctuation and nothing else, because a removed distinction merges
    two ledgers. The two companies key apart, both bootstrap, and each answers
    only for its own account.
    """
    assert normalise_company(FULLWIDTH_ACME) != normalise_company(ASCII_ACME)

    tally = tally_with(
        (ASCII_ACME, "Purchases"), (FULLWIDTH_ACME, "Repairs & Maintenance")
    )
    store = MemoryStore(":memory:")

    plain = bootstrap(tally, ASCII_ACME, store)
    wide = bootstrap(tally, FULLWIDTH_ACME, store)

    assert plain.report.status is BootstrapStatus.READY
    assert wide.report.status is BootstrapStatus.READY
    assert plain.lookup(PARTY).accounts == ("Purchases",)
    assert wide.lookup(PARTY).accounts == ("Repairs & Maintenance",)


def _both_refused(first: str, second: str) -> None:
    """Both companies are refused while both are open, and both are named.

    Both, not just the second. While two names reduce to one key there is no
    reading of EITHER that can be trusted, and the refusal has to name the pair
    or the person is sent hunting through a company list. `{name!r}` is what
    `bootstrap` writes, so an invisible character shows up as its escape - the
    only rendering that tells a reader an invisible character is there at all.
    """
    tally = tally_with((first, "Purchases"), (second, "Repairs & Maintenance"))
    store = MemoryStore(":memory:")

    for asked, other in ((first, second), (second, first)):
        refused = bootstrap(tally, asked, store)
        assert refused.report.status is BootstrapStatus.COMPANY_KEY_COLLISION
        assert not refused.report.ready
        assert repr(asked) in refused.report.detail
        assert repr(other) in refused.report.detail, (
            "the refusal must name BOTH companies, and it named one"
        )

    assert store.state(normalise_company(first)) is None, (
        "a refusal that writes its own row has already done the damage"
    )


@pytest.mark.parametrize("spelling", list(ZERO_WIDTH.values()), ids=list(ZERO_WIDTH))
def test_an_invisible_character_hides_a_collision_and_the_pair_is_refused(
    spelling: str,
) -> None:
    """`Acme<invisible>Traders` renders as one word and keys as two.

    This is the dangerous direction: the two names look DIFFERENT on screen -
    one has a space and one does not - and share a key, so neither the person
    nor the guards downstream can tell the two sets of books apart. It has to
    fail closed at admission.
    """
    assert normalise_company(spelling) == normalise_company(ACME_SPACED)
    assert spelling != ACME_SPACED

    _both_refused(spelling, ACME_SPACED)


def test_a_fold_that_emits_a_combining_mark_still_reaches_the_punctuation_rule() -> (
    None
):
    """The D1 mechanism, one step later than the fix that was applied for it.

    `normalise_company` folds to NFC FIRST so that no combining mark can reach
    `_PUNCT` and be turned into a space. `casefold()` runs AFTER that fold, and
    `casefold` is itself a source of combining marks: U+0130 expands to
    "i" + U+0307 COMBINING DOT ABOVE, which nothing re-composes, so the mark
    reaches `_PUNCT` and becomes a space after all.

    Measured, not argued: the key gains a word break the name does not have,
    and the name therefore collides with a genuinely different one.

    If a future change to the rule stops these aliasing, this goes red and
    tells the next reader the fixture needs rechoosing.
    """
    assert normalise_company(DOTTED_I_INCI) == "i_nci_traders", (
        "the fold put a word break inside a word"
    )
    assert normalise_company(DOTTED_I_ITC) == normalise_company(SPACED_I_TC)
    assert DOTTED_I_ITC != SPACED_I_TC


def test_a_name_whose_casefold_emits_a_combining_mark_still_fails_closed() -> None:
    """The safety property survives the mechanism above, and that is the point.

    The key is wrong, but the pair is caught at admission and named, exactly as
    the punctuation colliders are. Nothing is read and nothing is written.
    """
    _both_refused(DOTTED_I_ITC, SPACED_I_TC)


@pytest.mark.parametrize(("first", "second"), COLLIDING_PAIRS)
def test_every_known_collider_pair_is_refused_by_the_running_app_naming_both(
    first: str, second: str
) -> None:
    """`tests/test_company_collision.py` proves this of `bootstrap`.

    This proves it of the thing a person actually touches: the app answers, the
    page carries the "too alike" banner instead of an entry form that works,
    `/health` says COMPANY_KEY_COLLISION and names both companies, and a typed
    entry writes nothing into either set of books.
    """
    assert normalise_company(first) == normalise_company(second)
    tally = tally_with((first, "Purchases"), (second, "Repairs & Maintenance"))
    live = serve_once(tally, first, visible=2)
    try:
        health = app.health()
        assert health["ready"] is False
        assert health["failure_code"] == "COMPANY_KEY_COLLISION"
        assert repr(first) in str(health["detail"])
        assert repr(second) in str(health["detail"])

        code, body = live.get("/")
        assert code == 200
        assert "too alike" in body, "the person is not told why nothing works"

        live.post("/entry", text=ENTRY)
    finally:
        live.close()

    assert tally.list_our_vouchers(first) == ()
    assert tally.list_our_vouchers(second) == ()


def test_a_refused_unicode_collision_leaves_both_books_exactly_as_they_were() -> None:
    """Counted in paise, in both companies, before and after.

    "Refused" and "wrote nothing" are two claims. Only the second is the one
    that protects a real business, and only a trial balance can settle it.
    """
    first, second = ZERO_WIDTH["zero width joiner U+200D"], ACME_SPACED
    tally = tally_with((first, "Purchases"), (second, "Repairs & Maintenance"))
    before_first = tally.trial_balance(first)
    before_second = tally.trial_balance(second)

    live = serve_once(tally, first, visible=2)
    try:
        live.post("/entry", text=ENTRY)
        live.post("/reverse-all")
        live.post("/reverse", op="op-none")
    finally:
        live.close()

    assert tally.trial_balance(first) == before_first
    assert tally.trial_balance(second) == before_second
    assert tally.list_our_vouchers(first) == ()
    assert tally.list_our_vouchers(second) == ()


def test_the_collision_refusal_never_picks_one_of_the_two_names() -> None:
    """The failure mode that is worse than refusing: choosing.

    Picking either name would give one company the other's index under a shared
    key, and the guards in `pipeline.py` compare that same key, so nothing
    downstream could notice. Neither may be admitted.
    """
    first, second = "Kumar Motors - Pune", "Kumar Motors Pune"
    tally = tally_with((first, "Purchases"), (second, "Repairs & Maintenance"))
    store = MemoryStore(":memory:")

    bootstrap(tally, first, store)
    bootstrap(tally, second, store)

    shared = normalise_company(first)
    assert store.state(shared) is None, (
        f"one of {first!r} / {second!r} was admitted under the shared key "
        f"{shared!r}, so the other company's index is now the wrong one"
    )
    assert store.vendors(shared) == ()
    assert store.chart(shared) == ()


@pytest.mark.parametrize("name", list(NO_IDENTITY.values()), ids=list(NO_IDENTITY))
def test_a_company_name_that_carries_no_identity_is_refused_and_reads_nothing(
    name: str,
) -> None:
    """An empty key would be one scope shared by every such company.

    `pytest.raises` is not the proof - the state after it is. Nothing is
    recorded, nothing is read out of the company, and no runtime is installed,
    so a later request cannot find a half-built one.
    """
    assert normalise_company(name) == ""
    tally = tally_with((name, "Purchases"))
    store = MemoryStore(":memory:")

    with pytest.raises(ValueError, match="carries no identity"):
        bootstrap(tally, name, store)

    assert store.state("") is None
    assert store.vendors("") == ()
    assert tally.list_our_vouchers(name) == ()

    with pytest.raises(ValueError, match="carries no identity"):
        app.configure(tally, identity_for(name), store=store)
    assert app.connected() is False, "a runtime was installed for a nameless company"


# ---- an encoding difference is not a different company ----------------------


def test_an_nfd_company_name_can_be_worked_in_end_to_end_over_http() -> None:
    """The whole path, for a company whose name is not ASCII and not NFC.

    macOS hands typed text to a program in NFD. If the app can only work in NFC
    then a person on a Mac cannot use it for their own company at all.
    """
    tally = tally_with((CAFE_NFD, "Purchases"))
    live = serve_once(tally, CAFE_NFD)
    try:
        code, home = live.get("/")
        assert code == 200
        assert CAFE_NFD in home, "the page does not name the company we connected to"

        code, body = live.post("/entry", text=ENTRY)
        assert code == 200
        # THE BADGE, NOT THE BARE WORD. `render_decision` draws "not posted" for
        # NOT_VALID, so `"posted" in body` was satisfied by the exact refusal this
        # test exists to rule out - and this test WAS refused, on the period, until
        # the reader above was wired in. `class="badge b-valid">posted<` is drawn
        # only for Outcome.VALID. Same form as tests/test_error_responses.py:1115.
        assert 'class="badge b-valid">posted<' in body, (
            f"the entry was not posted:\n{body[:400]}"
        )

        written = tally.list_our_vouchers(CAFE_NFD)
        assert len(written) == 1
        assert written[0].debit_account == "Purchases"

        trail = live.store.actions(CAFE_NFD)
        assert [r.company_key for r in trail] == [normalise_company(CAFE_NFD)] * len(
            trail
        )
    finally:
        live.close()


def test_the_unaccented_company_is_a_stranger_to_the_accented_companys_books() -> None:
    """Both open at once, no collision, and neither one answers for the other."""
    tally = tally_with((CAFE_NFD, "Purchases"), (CAFE_PLAIN, "Repairs & Maintenance"))
    store = MemoryStore(":memory:")

    accented = bootstrap(tally, CAFE_NFD, store)
    plain = bootstrap(tally, CAFE_PLAIN, store)

    assert accented.report.status is BootstrapStatus.READY
    assert plain.report.status is BootstrapStatus.READY
    assert accented.identity.key != plain.identity.key
    assert accented.lookup(PARTY).accounts == ("Purchases",)
    assert plain.lookup(PARTY).accounts == ("Repairs & Maintenance",)


def test_the_same_company_typed_in_two_encodings_is_never_two_companies() -> None:
    """The other direction of the rule, and the one that is reachable by hand.

    `ACCOUNTANT_COMPANY` is typed by a person. On macOS that produces NFD.
    TallyPrime runs on Windows and returns the precomposed NFC spelling from
    `list_companies`. Every comparison between the two is an exact string
    comparison - `real_tally`'s `company not in companies`, and
    `Runtime.confirm_company`'s `self.company not in open_now` - so one visible
    name typed in two encodings is treated as two different companies.

    The refusal that results is unactionable, because both halves of it render
    identically on screen:

        'Café Exports' is no longer open in Tally. 1 company/companies are
        open: ['Café Exports']. ... Open 'Café Exports' in Tally again

    It fails CLOSED, so nothing is written. It also cannot be got past.
    """
    tally = tally_with((CAFE_NFC, "Purchases"))
    live = app.configure(tally, identity_for(CAFE_NFD), store=MemoryStore(":memory:"))

    assert live.memory.report.status is BootstrapStatus.READY, (
        f"the app could not read {CAFE_NFD!r} out of a Tally whose only open "
        f"company is the same visible name spelt {CAFE_NFC!r}"
    )
    live.confirm_company()
    assert tally.list_our_vouchers(CAFE_NFC) == ()
