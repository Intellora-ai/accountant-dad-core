"""G5.4 — the live acceptance command, and the one label it cannot apply.

The whole value of this command is negative: it must not be able to produce
LIVE evidence from an environment that has not earned it. Everything else here
is plumbing.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the acceptance run has been performed against a licensed TallyPrime. It
has not. `RealTally acceptance test: REQUIRED, NOT YET RUN`. This proves the
command is ready and that it refuses to lie.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from accountant.tallyio.factory import BackendIdentity, RealTallyRequired
from accountant.tallyio.fake import FakeTally
from accountant.tallyio.real import LicenceMode
from ci import acceptance, acceptance_cli

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")


def identity(mode: str = LicenceMode.UNKNOWN.value) -> BackendIdentity:
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_acceptance_cli.py",
        company=COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id="run-acc-cli",
        licence_mode=mode,
        licence_detail="substituted for the test",
    )


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    def substituted(*_a: object, **_k: object) -> tuple[FakeTally, BackendIdentity]:
        return t, identity()

    monkeypatch.setattr(acceptance_cli, "real_tally", substituted)
    return t


ARGS = [
    "--company",
    COMPANY,
    "--backed-up",
    "--evidence-class",
    acceptance.EDUCATIONAL_TALLY,
]


# ---- the refusal that matters -----------------------------------------------


def test_the_live_evidence_class_is_refused_while_the_licence_is_unknown(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    """The separation between compatibility and live proof, enforced by code.

    A11 measured that this gateway will not answer `$$LicenseInfo`, so the mode
    is UNKNOWN by design. A tool that would accept the LIVE label anyway leaves
    the project's last open question closeable by whoever writes the report.
    """
    code = acceptance_cli.main(
        [
            "--company",
            COMPANY,
            "--backed-up",
            "--evidence-class",
            acceptance.LICENSED_REALTALLY,
            "--yes",
        ]
    )

    assert code == acceptance_cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "refusing to label" in err
    assert "UNKNOWN by design" in err
    assert tally.list_our_vouchers(COMPANY) == (), "and nothing was written"


def test_the_live_class_is_allowed_once_the_connector_measures_a_licence(
    monkeypatch: pytest.MonkeyPatch,
):
    """The control. Without it the refusal above could be an unconditional no,
    which would prove the tool is broken rather than careful."""
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    def licensed(*_a: object, **_k: object) -> tuple[FakeTally, BackendIdentity]:
        return t, identity(LicenceMode.LICENSED.value)

    monkeypatch.setattr(acceptance_cli, "real_tally", licensed)

    code = acceptance_cli.main(
        [
            "--company",
            COMPANY,
            "--backed-up",
            "--evidence-class",
            acceptance.LICENSED_REALTALLY,
            "--yes",
        ]
    )
    assert code == acceptance_cli.EXIT_OK


def test_an_educational_run_is_never_refused_for_understating_itself():
    """Only one direction is dangerous. Filing a licensed run as compatibility
    evidence harms nobody."""
    honest, why = acceptance_cli.class_is_honest(
        acceptance.EDUCATIONAL_TALLY, LicenceMode.LICENSED.value
    )
    assert honest and why == ""


def test_faketally_is_not_an_offered_evidence_class():
    """This command only ever talks to a real connector. Offering a class that
    means "no Tally was involved" would let a real run be filed under it."""
    assert acceptance.FAKETALLY not in acceptance_cli.LIVE_CLASSES
    assert acceptance.UNIT_TEST not in acceptance_cli.LIVE_CLASSES

    with pytest.raises(SystemExit):
        acceptance_cli.main(
            ["--company", COMPANY, "--evidence-class", acceptance.FAKETALLY]
        )


# ---- the pre-flight ----------------------------------------------------------


def test_the_preflight_shows_every_item_the_autonomy_boundary_requires(
    tally: FakeTally,  # noqa: ARG001 - substitutes real_tally
    capsys: pytest.CaptureFixture[str],
):
    code = acceptance_cli.main(ARGS)

    assert code == acceptance_cli.EXIT_OK
    out = capsys.readouterr().out
    for required in (
        "backend identity",
        "company identity",
        "backup identity",
        "licence mode",
        "write enabled",
        "voucher set",
        "expected movement",
        "operation ids",
        "cleanup plan",
        "reconciliation plan",
    ):
        assert required in out, f"the pre-flight does not show {required!r}"


def test_without_yes_nothing_is_written(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    before = tally.trial_balance(COMPANY)

    assert acceptance_cli.main(ARGS) == acceptance_cli.EXIT_OK

    assert "nothing was written" in capsys.readouterr().out
    assert tally.trial_balance(COMPANY) == before
    assert tally.list_our_vouchers(COMPANY) == ()


def test_the_preflight_states_the_exact_expected_trial_balance_movement(
    tally: FakeTally,  # noqa: ARG001 - substitutes real_tally
    capsys: pytest.CaptureFixture[str],
):
    acceptance_cli.main(ARGS)

    total = sum(
        acceptance.controlled_voucher(i, acceptance.DEFAULT_DATE).amount_paise
        for i in range(acceptance.N)
    )
    out = capsys.readouterr().out
    assert f"Purchases +{total}, Cash -{total} paise" in out


# ---- the run itself ----------------------------------------------------------


def test_with_yes_it_runs_and_the_books_come_back(
    tally: FakeTally, capsys: pytest.CaptureFixture[str]
):
    before = tally.trial_balance(COMPANY)

    assert acceptance_cli.main([*ARGS, "--yes"]) == acceptance_cli.EXIT_OK

    out = capsys.readouterr().out
    assert "VERDICT: PASSED" in out
    assert acceptance.EDUCATIONAL_TALLY in out
    assert tally.trial_balance(COMPANY) == before
    assert tally.list_our_vouchers(COMPANY) == ()


def test_a_run_that_did_not_pass_does_not_exit_zero(
    tally: FakeTally,  # noqa: ARG001 - substitutes real_tally
    monkeypatch: pytest.MonkeyPatch,
):

    def refuses_everything(_self: FakeTally, _company: str, _operation_id: str) -> bool:
        return False

    monkeypatch.setattr(FakeTally, "reverse_by_operation_id", refuses_everything)

    assert acceptance_cli.main([*ARGS, "--yes"]) == acceptance_cli.EXIT_NOT_PASSED


def test_the_evidence_bundle_is_written_where_it_was_asked_for(
    tally: FakeTally,  # noqa: ARG001 - substitutes real_tally
    tmp_path: pathlib.Path,
):
    out = tmp_path / "nested" / "bundle.json"

    acceptance_cli.main([*ARGS, "--yes", "--out", str(out)])

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["n"] == 10
    assert saved["evidence_class"] == acceptance.EDUCATIONAL_TALLY
    assert saved["verdict"] == "PASSED"
    assert len(saved["operation_ids"]) == 10
    assert len(saved["conditions"]) == 15


def test_an_unreachable_tally_refuses_rather_than_tracing_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def unreachable(*_a: object, **_k: object) -> object:
        raise RealTallyRequired("REAL TALLY REQUIRED: no operation performed. nope")

    monkeypatch.setattr(acceptance_cli, "real_tally", unreachable)

    assert acceptance_cli.main([*ARGS, "--yes"]) == acceptance_cli.EXIT_REFUSED
    assert "REAL TALLY REQUIRED" in capsys.readouterr().err


def test_the_default_date_is_one_educational_mode_permits(
    tally: FakeTally,  # noqa: ARG001 - substitutes real_tally
    capsys: pytest.CaptureFixture[str],
):
    """Educational mode accepts the 1st, 2nd and 31st and nothing else. A
    default the environment refuses would mean the day of the live run is spent
    discovering that."""
    assert acceptance.DEFAULT_DATE.day in (1, 2, 31)

    acceptance_cli.main(ARGS)
    assert "2026-08-31" in capsys.readouterr().out


def test_the_frozen_fixture_date_is_not_what_this_command_posts():
    """`tests/test_tally_contract.py` posts on 2026-08-07 and that fixture is
    never edited. This command uses a date the environment permits, and the
    distance between the two is what keeps the live question open."""
    assert acceptance.DEFAULT_DATE.isoformat() == "2026-08-31"

    contract = pathlib.Path("tests/test_tally_contract.py").read_text(encoding="utf-8")
    assert "datetime.date(2026, 8, 7)" in contract, "the fixture is unchanged"
