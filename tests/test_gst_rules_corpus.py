"""The rules corpus: what loads, what is refused, and what is never guessed.

Every test here is about a way a wrong rate could get into somebody's books. The
load guards are the boring half; the interesting half is the three refusals —
an unknown code, a code the notification prints twice, and a date past the last
day anybody checked the amendment chain.
"""

from __future__ import annotations

import ast
import datetime
import pathlib
from dataclasses import replace

import pytest

from accountant.rules.effective_dates import EffectiveWindow, WindowVerdict
from accountant.rules.gst_rates import (
    AMENDMENTS_CHECKED_THROUGH,
    IN_FORCE_FROM,
    OFFICIAL_RULES,
    RateOutcome,
    RuleCorpus,
    TaxType,
    official_corpus,
)
from accountant.rules.hsn_sac import Code, CodeKind, normalise
from accountant.rules.provenance import (
    AuthorityRank,
    RuleStatus,
    Source,
)

IN_WINDOW = datetime.date(2017, 7, 15)


def a_rule(**overrides: object):
    """One well-formed rule, so each test can spoil exactly one thing."""
    base = OFFICIAL_RULES[0]
    return replace(base, **overrides)  # pyright: ignore[reportArgumentType]


# ---- the corpus loads what it should ---------------------------------------


def test_the_official_corpus_loads_every_rule_and_rejects_none():
    corpus = official_corpus()
    assert len(corpus.loaded) == len(OFFICIAL_RULES)
    assert corpus.rejected == ()


def test_every_loaded_rule_carries_all_eight_required_fields():
    """Owner decision Q1 = A lists eight. A rule missing one is untraceable."""
    for rule in official_corpus().loaded:
        assert rule.source.url.startswith("https://"), rule.rule_id
        assert rule.source.title.strip(), rule.rule_id
        assert rule.source.issuing_authority.strip(), rule.rule_id
        assert rule.source.notification_number.strip(), rule.rule_id
        assert rule.source.retrieval_date is not None, rule.rule_id
        assert rule.window.effective_from is not None, rule.rule_id
        assert rule.rule_version.strip(), rule.rule_id
        assert rule.jurisdiction.strip(), rule.rule_id


def test_every_source_is_a_cbic_notification_and_names_where_it_was_read():
    for rule in official_corpus().loaded:
        assert rule.source.authority_rank is AuthorityRank.CBIC_NOTIFICATION
        assert rule.source.may_stand_alone
        assert "cbic" in rule.source.url
        # "Schedule IV - 14%, S. No. 18" — not "the rate schedule".
        assert rule.source.document_reference.strip(), rule.rule_id


def test_the_corpus_holds_exactly_the_codes_the_evaluation_uses():
    """Owner decision Q2 = C. Four codes, and none added to look better."""
    corpus = official_corpus()
    assert corpus.codes == ("2523", "4820", "9972", "9987")
    assert corpus.codes_of_kind(CodeKind.HSN) == ("2523", "4820")
    assert corpus.codes_of_kind(CodeKind.SAC) == ("9972", "9987")


def test_unretrievable_sources_are_recorded_with_the_exact_error():
    unverified = official_corpus().unverified
    assert unverified, "a corpus with gaps must name them"
    assert any("unable to verify the first certificate" in u.error for u in unverified)
    for source in unverified:
        assert source.status is RuleStatus.SOURCE_UNVERIFIED
        assert source.would_have_supported.strip()
        assert source.error.strip()


def test_an_unverified_source_never_becomes_a_loaded_rule():
    """The TLS failure costs a capability. It does not cost a citation."""
    corpus = official_corpus()
    unverified_urls = {u.url for u in corpus.unverified}
    assert not {r.source.url for r in corpus.loaded} & unverified_urls


# ---- the four load guards, one test each -----------------------------------


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("url", "", "no source URL"),
        ("notification_number", "  ", "no notification number"),
        ("retrieval_date", None, "no retrieval date"),
    ],
)
def test_a_rule_with_a_missing_source_field_is_rejected_at_load(
    field: str, value: object, expected: str
):
    spoiled = a_rule(source=replace(OFFICIAL_RULES[0].source, **{field: value}))
    corpus = RuleCorpus.build([spoiled])
    assert corpus.loaded == ()
    assert [r.reason for r in corpus.rejected] == [expected]


def test_a_rule_with_no_effective_date_is_rejected_at_load():
    spoiled = a_rule(window=EffectiveWindow(effective_from=None))
    corpus = RuleCorpus.build([spoiled])
    assert corpus.loaded == ()
    assert corpus.rejected[0].reason == "no effective date"


def test_a_rule_whose_only_source_may_not_stand_alone_is_rejected():
    """GST Council material is context. It is never the whole authority."""
    council = replace(
        OFFICIAL_RULES[0].source,
        authority_rank=AuthorityRank.GST_COUNCIL_SUPPLEMENTARY,
    )
    corpus = RuleCorpus.build([a_rule(source=council)])
    assert corpus.loaded == ()
    assert "may not be the sole authority" in corpus.rejected[0].reason


def test_a_source_unverified_rule_is_rejected_and_says_so_in_its_own_words():
    corpus = RuleCorpus.build([a_rule(status=RuleStatus.SOURCE_UNVERIFIED)])
    assert corpus.loaded == ()
    assert corpus.rejected[0].reason == "rule status is source_unverified"


def test_an_unversioned_or_negative_rule_is_rejected():
    assert RuleCorpus.build([a_rule(rule_version=" ")]).rejected[0].reason == (
        "no rule version"
    )
    assert (
        "negative rate"
        in RuleCorpus.build([a_rule(rate_basis_points=-1)]).rejected[0].reason
    )


def test_a_rejection_keeps_the_url_so_the_gap_can_be_chased():
    corpus = RuleCorpus.build([a_rule(window=EffectiveWindow(effective_from=None))])
    assert corpus.rejected[0].url == OFFICIAL_RULES[0].source.url


# ---- the rates themselves, against the retrieved documents ------------------


@pytest.mark.parametrize(
    ("code", "tax", "basis_points", "notification"),
    [
        ("2523", TaxType.CGST, 1400, "1/2017-Central Tax (Rate)"),
        ("2523", TaxType.UTGST, 1400, "1/2017-Union Territory Tax (Rate)"),
        ("2523", TaxType.IGST, 2800, "1/2017-Integrated Tax (Rate)"),
        ("9972", TaxType.CGST, 900, "11/2017-Central Tax (Rate)"),
        ("9972", TaxType.IGST, 1800, "8/2017-Integrated Tax (Rate)"),
        ("9987", TaxType.CGST, 900, "11/2017-Central Tax (Rate)"),
        ("9987", TaxType.UTGST, 900, "11/2017-Union Territory Tax (Rate)"),
        ("9987", TaxType.IGST, 1800, "8/2017-Integrated Tax (Rate)"),
    ],
)
def test_each_rate_matches_the_notification_it_cites(
    code: str, tax: TaxType, basis_points: int, notification: str
):
    """These eight numbers are the corpus. One drifting breaks everything after it."""
    found = official_corpus().lookup(normalise(code), tax, IN_WINDOW)
    assert found.outcome is RateOutcome.FOUND
    assert found.rate_basis_points == basis_points
    assert found.rule is not None
    assert found.rule.source.notification_number == notification


# ---- never guessed from a similar code -------------------------------------


def test_an_unknown_code_is_not_found_and_says_no_similar_code_was_used():
    missing = official_corpus().lookup(normalise("8471"), TaxType.CGST, IN_WINDOW)
    assert missing.outcome is RateOutcome.NOT_FOUND
    assert missing.rule is None
    assert missing.rate_basis_points is None
    assert "no similar code is used in its place" in missing.reason


def test_no_neighbour_of_a_known_code_inherits_its_rate():
    """The mutation this guards: any prefix, chapter or nearest-match fallback.

    Every code one digit away from a code the corpus knows is generated and
    looked up. A fallback of any kind makes at least one of these return a rate,
    and 2523's twenty-eight per cent landing on 2524 is a real invoice with a
    real wrong tax on it.
    """
    corpus = official_corpus()
    known = set(corpus.codes)
    neighbours: set[str] = set()
    for code in known:
        for position in range(len(code)):
            for digit in "0123456789":
                candidate = code[:position] + digit + code[position + 1 :]
                if candidate not in known:
                    neighbours.add(candidate)
    assert len(neighbours) >= 100
    for candidate in sorted(neighbours):
        for tax in (TaxType.CGST, TaxType.UTGST, TaxType.IGST):
            look = corpus.lookup(normalise(candidate), tax, IN_WINDOW)
            assert look.outcome is RateOutcome.NOT_FOUND, candidate
            assert look.rate_basis_points is None, candidate


def test_a_chapter_prefix_is_not_a_code():
    """ "25" is a chapter. It is not 2523 and it has no rate here."""
    for text in ("25", "2", "252", "252300000"):
        assert normalise(text) is None


def test_a_code_with_letters_or_punctuation_is_refused_rather_than_repaired():
    for text in ("25AB", "twenty-five", "", "  "):
        assert normalise(text) is None
    assert normalise(None) is None


def test_typography_is_removed_but_digits_are_never_changed():
    assert normalise("25 23") == Code("2523", CodeKind.HSN)
    assert normalise("2523.00.00") == Code("25230000", CodeKind.HSN)
    assert normalise("9972") == Code("9972", CodeKind.SAC)


def test_a_code_lookup_with_nothing_at_all_is_not_found():
    look = official_corpus().lookup(None, TaxType.CGST, IN_WINDOW)
    assert look.outcome is RateOutcome.NOT_FOUND
    assert "no usable HSN/SAC code" in look.reason


# ---- the conflict that is in the source document ----------------------------


@pytest.mark.parametrize("tax", [TaxType.CGST, TaxType.UTGST, TaxType.IGST])
def test_heading_4820_conflicts_and_the_corpus_refuses_to_choose(tax: TaxType):
    """Notification 1/2017 prints 4820 twice, in Schedule II and Schedule III.

    Not a fixture. Schedule II, S. No. 123 is exercise books and notebooks;
    Schedule III, S. No. 154 is registers and account books "[other than note
    books and exercise books]". Four digits cannot tell them apart.
    """
    look = official_corpus().lookup(normalise("4820"), tax, IN_WINDOW)
    assert look.outcome is RateOutcome.CONFLICT
    assert look.rule is None
    assert len(look.conflicting_rule_ids) == 2
    assert "does not choose between them" in look.reason


def test_a_conflict_names_both_rules_so_it_can_be_resolved_later():
    look = official_corpus().lookup(normalise("4820"), TaxType.CGST, IN_WINDOW)
    assert set(look.conflicting_rule_ids) == {
        "gst.goods.4820.sch2-123.cgst.v1",
        "gst.goods.4820.sch3-154.cgst.v1",
    }


# ---- dates -----------------------------------------------------------------


def test_a_supply_before_the_notification_came_into_force_gets_no_rate():
    look = official_corpus().lookup(
        normalise("2523"), TaxType.CGST, IN_FORCE_FROM - datetime.timedelta(days=1)
    )
    assert look.outcome is RateOutcome.NOT_YET_IN_FORCE
    assert "takes effect on 2017-07-01" in look.reason


def test_the_last_checked_day_still_works_and_the_next_one_does_not():
    """The boundary itself, both sides of it, so an off-by-one cannot hide."""
    corpus = official_corpus()
    last = corpus.lookup(normalise("2523"), TaxType.CGST, AMENDMENTS_CHECKED_THROUGH)
    assert last.outcome is RateOutcome.FOUND
    day_after = corpus.lookup(
        normalise("2523"),
        TaxType.CGST,
        AMENDMENTS_CHECKED_THROUGH + datetime.timedelta(days=1),
    )
    assert day_after.outcome is RateOutcome.BEYOND_AMENDMENT_CHECK
    assert "may be stale" in day_after.reason


def test_a_supply_dated_today_gets_no_rate_rather_than_a_2017_one():
    """The whole point of `amendments_checked_through`, stated as a test.

    Notification 1/2017-Central Tax (Rate) records no end date, and treating
    that as "valid forever" is how a 2017 rate lands on a 2026 invoice.
    """
    look = official_corpus().lookup(
        normalise("2523"), TaxType.CGST, datetime.date(2026, 8, 10)
    )
    assert look.outcome is RateOutcome.BEYOND_AMENDMENT_CHECK
    assert look.rate_basis_points is None


def test_a_window_that_has_ended_reports_that_and_not_something_vaguer():
    window = EffectiveWindow(
        effective_from=datetime.date(2017, 7, 1),
        effective_to=datetime.date(2018, 1, 1),
        amendments_checked_through=datetime.date(2030, 1, 1),
    )
    assert window.verdict(datetime.date(2019, 1, 1)) is WindowVerdict.ENDED
    assert "ended on 2018-01-01" in window.explain(datetime.date(2019, 1, 1))
    corpus = RuleCorpus.build([a_rule(window=window)])
    look = corpus.lookup(normalise("2523"), TaxType.CGST, datetime.date(2019, 1, 1))
    assert look.outcome is RateOutcome.ENDED


def test_a_window_with_no_effective_date_explains_itself_rather_than_defaulting():
    window = EffectiveWindow(effective_from=None)
    assert window.verdict(IN_WINDOW) is WindowVerdict.NO_EFFECTIVE_DATE
    assert "carries no effective date" in window.explain(IN_WINDOW)


def test_every_rule_records_how_far_the_amendment_chain_was_checked():
    for rule in official_corpus().loaded:
        assert rule.window.amendments_checked_through == AMENDMENTS_CHECKED_THROUGH
        assert "cbic-gst.gov.in" in rule.window.amendment_check_note


def test_a_rule_with_an_unchecked_amendment_chain_still_answers_in_its_window():
    """`amendments_checked_through = None` means no edge was declared.

    It is allowed, because a rule whose source states its own end date does not
    need one — but every rule in the official corpus declares one anyway, which
    the test above pins.
    """
    window = EffectiveWindow(effective_from=IN_FORCE_FROM)
    corpus = RuleCorpus.build([a_rule(window=window)])
    look = corpus.lookup(normalise("2523"), TaxType.CGST, datetime.date(2030, 1, 1))
    assert look.outcome is RateOutcome.FOUND


# ---- the tax nobody can cite ------------------------------------------------


def test_the_corpus_holds_no_sgst_rate_at_all_and_says_why():
    """An SGST rate is a State's notification, and Q1 = A admits no such source."""
    look = official_corpus().lookup(normalise("2523"), TaxType.SGST, IN_WINDOW)
    assert look.outcome is RateOutcome.SOURCE_UNVERIFIED
    assert "holds no SGST rate for any code" in look.reason
    assert look.rate_basis_points is None


# ---- no runtime dependency on anything that could fetch a rate --------------

_FORBIDDEN_IMPORTS = frozenset(
    {
        "http",
        "http.client",
        "httplib",
        "urllib",
        "urllib.request",
        "socket",
        "ssl",
        "requests",
        "httpx",
        "aiohttp",
        "xmlrpc",
        "ftplib",
        "telnetlib",
    }
)

_PACKAGES = ("accountant/rules", "accountant/tax")


def _imports(path: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_rules_or_tax_module_can_reach_the_network():
    """Owner decision Q1 = A: no commercial API, at build time or at runtime.

    A rate that can be fetched is a rate that can change without a citation, and
    an outage becomes a tax outage. Everything here is committed data.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for package in _PACKAGES:
        for path in sorted((root / package).glob("*.py")):
            scanned += 1
            bad = sorted(_imports(path) & _FORBIDDEN_IMPORTS)
            if bad:
                offenders[str(path.relative_to(root))] = bad
    assert scanned >= 7, "the scan found almost nothing, so it is guarding nothing"
    assert offenders == {}, f"network-capable imports in the rules engine: {offenders}"


def test_the_forbidden_import_scan_can_actually_see_an_offender(tmp_path: pathlib.Path):
    """The control. Without it the scan above could be reading the wrong files."""
    guilty = tmp_path / "guilty.py"
    guilty.write_text("import urllib.request\n")
    assert _imports(guilty) & _FORBIDDEN_IMPORTS


def test_no_rule_source_url_points_at_a_commercial_rate_api():
    for rule in official_corpus().loaded:
        host = rule.source.url.split("/")[2]
        assert host.endswith("cbic-gst.gov.in") or host.endswith("cbic.gov.in"), host


def test_a_source_can_be_built_blank_so_the_load_guard_has_something_to_catch():
    """The guards live in the loader, not in `Source`, and this pins that choice."""
    blank = Source(
        url="",
        title="",
        issuing_authority="",
        notification_number="",
        retrieval_date=None,
        authority_rank=AuthorityRank.CBIC_NOTIFICATION,
    )
    assert blank.may_stand_alone
    assert RuleCorpus.build([a_rule(source=blank)]).loaded == ()
