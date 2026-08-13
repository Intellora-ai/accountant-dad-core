"""The deployment artefacts, read as data.

WHAT THIS FILE PROVES, AND WHAT IT DOES NOT
-------------------------------------------
No image is built, pulled, run or pushed here, so the evidence class is
`NOT MEASURED` and nothing in this file says a container starts. What it does
prove is the part that can be proved before any of that exists: that the
Dockerfile still has the properties it was written for, that `scripts/deploy`
refuses to invent a registry, and — since 2026-08-11 — that no artefact anywhere
invents a TENANT ID while all four of them say the variable has to be set.

That last one is four checks and not one, on purpose. `ACCOUNTANT_TENANT` unset
means every request is refused 403, deliberately, so the thing that goes wrong
is not a wrong value: it is nobody being told there is a value to supply. One
document saying so is one document somebody has not opened.

Every one of those properties is written down in `docs/DEPLOY.md` as well. A
promise in a document is not a check — it is a sentence that stays true until
somebody edits the file it describes, and then stays written. So each promise
is also read out of the artefact itself, here.

WHY EVERY GUARD HAS A CONTROL
-----------------------------
A guard nobody has watched fail is not a guard. Each check below is a named
predicate applied twice: once to the real file, and once to a deliberately
broken one that must be caught. The control uses the SAME predicate, so a
predicate that has quietly stopped looking at anything fails its control rather
than passing both.

WHY THE DOCKERFILE IS PARSED INTO INSTRUCTIONS AND NOT SCANNED AS TEXT
----------------------------------------------------------------------
The Dockerfile explains, in prose, exactly which environment variables it
refuses to set and why — `LOCAL_DEV_MODE` among them. A substring scan would
fail on the explanation and pass on a real `ENV LOCAL_DEV_MODE=1` sitting
inside a comment nobody noticed. A comment cannot set an environment variable
and must not be able to fail a check about one.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from accountant.auth import ENV_LOCAL_DEV_MODE, LOCAL_DEV_TENANT, AuthRefusal
from accountant.tallyio import writedoor
from accountant.web import app

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
DEPLOY = ROOT / "scripts" / "deploy"
DEPLOY_DOC = ROOT / "docs" / "DEPLOY.md"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
PYTHON_VERSION = ROOT / ".python-version"
APP = ROOT / "accountant" / "web" / "app.py"
PACKAGE = ROOT / "accountant"

#: The variable `scripts/deploy` reads, and the one it must never default.
REGISTRY = "ACCOUNTANT_REGISTRY"

#: WHOSE books a running process serves. Read by the app, never valued by any
#: artefact: a tenant id is a customer's identity, and an artefact that carries
#: one has put a customer's name in every registry, cache and backup it reaches.
TENANT = app.ENV_TENANT

#: Names whose presence in an image layer is a leak, whatever the value is.
SECRET_WORDS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "APIKEY")


# ---------------------------------------------------------------------------
# reading the artefacts
# ---------------------------------------------------------------------------


def instructions(text: str) -> list[tuple[str, str]]:
    """`(INSTRUCTION, argument)` pairs, with comments and continuations resolved.

    Comments are dropped, deliberately — see the module docstring. Backslash
    continuations are joined, because `HEALTHCHECK ... \\` and its `CMD` are one
    instruction and reading them as two would find a `CMD` that is not the
    image's command.
    """
    lines: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        lines.append((buffer + line).strip())
        buffer = ""
    if buffer:
        lines.append(buffer.strip())

    parsed: list[tuple[str, str]] = []
    for line in lines:
        head, _, rest = line.partition(" ")
        parsed.append((head.upper(), rest.strip()))
    return parsed


def dockerfile() -> list[tuple[str, str]]:
    return instructions(DOCKERFILE.read_text(encoding="utf-8"))


def ignored() -> set[str]:
    """The `.dockerignore` patterns, trailing slashes normalised away.

    `data/` and `data` exclude the same directory. A test that insisted on one
    spelling would be a test about punctuation.
    """
    patterns: set[str] = set()
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.add(line.rstrip("/"))
    return patterns


# ---------------------------------------------------------------------------
# the predicates. Each one is used twice: on the real file, and on a control.
# ---------------------------------------------------------------------------


def runs_as(instrs: list[tuple[str, str]]) -> str | None:
    """The last `USER`, or None. Last, because a later one wins in Docker too."""
    users = [rest for name, rest in instrs if name == "USER"]
    return users[-1] if users else None


def is_root(user: str | None) -> bool:
    """No USER at all is root — that is Docker's default, not an absence."""
    if user is None:
        return True
    who = user.split(":")[0].strip()
    return who in {"root", "0"}


def volumes(instrs: list[tuple[str, str]]) -> list[str]:
    """Every path declared as a volume, in JSON or shell form."""
    found: list[str] = []
    for name, rest in instrs:
        if name != "VOLUME":
            continue
        found.extend(re.findall(r'"([^"]+)"', rest) or rest.split())
    return [path.strip() for path in found]


def environment(instrs: list[tuple[str, str]]) -> dict[str, str]:
    """`ENV` and `ARG` names and values. Both put a value in the image."""
    values: dict[str, str] = {}
    for name, rest in instrs:
        if name not in {"ENV", "ARG"}:
            continue
        tokens = rest.split()
        if len(tokens) == 2 and "=" not in rest:
            values[tokens[0]] = tokens[1]
            continue
        for token in tokens:
            key, sep, value = token.partition("=")
            if sep:
                values[key] = value
    return values


def healthcheck(instrs: list[tuple[str, str]]) -> str | None:
    """The `HEALTHCHECK` argument, or None. `HEALTHCHECK NONE` counts as none."""
    for name, rest in instrs:
        if name == "HEALTHCHECK":
            return None if rest.strip().upper() == "NONE" else rest
    return None


def baked_secrets(instrs: list[tuple[str, str]]) -> list[str]:
    return [
        key
        for key in environment(instrs)
        if any(word in key.upper() for word in SECRET_WORDS)
    ]


def baked_tenant(instrs: list[tuple[str, str]]) -> str | None:
    """The tenant id the image carries, or None when it carries none.

    An EMPTY value is not a tenant id, and the difference is the whole decision
    the Dockerfile records. `ENV ACCOUNTANT_TENANT=""` is a note to whoever runs
    the image — it shows up in `docker inspect` and `docker history`, and a
    variable nobody wrote down cannot — while behaving exactly as unset does:
    `served_tenant()` strips the value and refuses on anything empty.

    The quotes are stripped because `=""`, `=''` and `=` are three spellings of
    the same empty, and a check that only knew one of them would be a check
    about punctuation.
    """
    value = environment(instrs).get(TENANT)
    if value is None:
        return None
    return value.strip().strip("\"'").strip() or None


def named_tenant(provenance: list[str]) -> str | None:
    """What the startup banner says about the tenant, or None if it is silent.

    Read out of the provenance lines rather than off a substring of the whole
    banner: a line that merely MENTIONS the variable somewhere in a sentence is
    not the banner reporting a resolved value, and the failure this catches is
    the banner going quiet about which customer the process serves.
    """
    for line in provenance:
        name, sep, rest = line.partition("=")
        if sep and name.strip() == TENANT:
            return rest.strip()
    return None


def documented_variables(text: str) -> dict[str, str]:
    """`docs/DEPLOY.md`'s environment table, as `{variable: what if it is missing}`.

    Parsed as a table rather than searched as text for the same reason the
    Dockerfile is parsed into instructions. The document explains the tenant at
    length in prose; a substring scan would pass on the explanation and go on
    passing after the row that states the FAILURE MODE was deleted, which is the
    row an operator actually reads.
    """
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        found = re.fullmatch(r"`([A-Z_]+)`", cells[0])
        if found is not None:
            rows[found.group(1)] = cells[2]
    return rows


def states_the_refusal(sentence: str) -> bool:
    """True when a sentence says what happens with no tenant, not just that one exists.

    "Set ACCOUNTANT_TENANT" is advice, and advice is what gets skipped. "Every
    request is refused 403" is a consequence, and a consequence is what makes
    somebody go and set it. Both halves are required, so deleting the
    consequence and leaving the name is caught.
    """
    lowered = sentence.lower()
    return "403" in lowered and "refus" in lowered


def announces_the_tenant(text: str) -> bool:
    """True when `text` names the variable AND says what happens without it."""
    return TENANT in text and states_the_refusal(text)


def shell_code(text: str) -> str:
    """The script with its comments and heredoc bodies removed.

    The same reason the Dockerfile is parsed rather than scanned. This script's
    refusal message CONTAINS the line

        ACCOUNTANT_REGISTRY=registry.example.invalid/your-namespace ./scripts/deploy

    which is a sentence telling a person what to type, not the script giving
    itself a value. A scan that could not tell those apart would fail on the
    help text, and the obvious fix for that failure would be to delete the help
    text — leaving a worse script and a check that had achieved nothing.
    """
    lines: list[str] = []
    terminator: str | None = None
    for raw in text.splitlines():
        if terminator is not None:
            if raw.strip() == terminator:
                terminator = None
            continue
        if raw.strip().startswith("#"):
            continue
        lines.append(raw)
        here = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", raw)
        if here is not None:
            terminator = here.group(1)
    return "\n".join(lines)


def defaulted(text: str, variable: str) -> bool:
    """True when the script gives `variable` a value of its own.

    Two shapes count, and one deliberately does not.

        VAR=something          an assignment. Counts.
        ${VAR:-something}      a fallback with a value. Counts.
        ${VAR:-}               the standard way to READ a variable that may be
                               unset while `set -u` is on. Supplies nothing,
                               and is how the refusal below is written.
    """
    assignment = re.search(rf"(?m)^\s*(export\s+)?{re.escape(variable)}=", text)
    fallback = re.search(rf"\$\{{{re.escape(variable)}:?-[^}}]", text)
    return bool(assignment or fallback)


# ---------------------------------------------------------------------------
# reading the things the artefacts must agree with
# ---------------------------------------------------------------------------


def requires_python_floor() -> tuple[int, int]:
    with PYPROJECT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    spec = str(data["project"]["requires-python"]).strip()
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)", spec)
    assert match is not None, (
        f"requires-python is {spec!r}. This test only understands a `>=X.Y` "
        f"floor; teach it the new form rather than deleting the check."
    )
    return int(match.group(1)), int(match.group(2))


def base_image() -> str:
    for name, rest in dockerfile():
        if name == "FROM":
            return rest.strip()
    raise AssertionError("the Dockerfile has no FROM instruction")


def serve_defaults() -> dict[str, object]:
    """The default arguments of `accountant.web.app.serve`, read from source.

    From the AST rather than by importing: this test must be able to say what
    the shipped entry point binds without starting anything.
    """
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "serve":
            continue
        names = [arg.arg for arg in node.args.args]
        defaults = node.args.defaults
        paired = dict(zip(names[len(names) - len(defaults) :], defaults, strict=True))
        return {key: ast.literal_eval(value) for key, value in paired.items()}
    raise AssertionError(f"{APP.name} has no serve() to read")


# ---------------------------------------------------------------------------
# running scripts/deploy against a stand-in for the docker CLI
# ---------------------------------------------------------------------------


def fake_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A `docker` on PATH that records its arguments and does nothing else.

    The alternative is a text scan for the words "docker build", which would
    pass on a script that mentions them in a comment and fail on one that
    builds correctly through a variable. This runs the real script.
    """
    log = tmp_path / "docker-calls"
    shim = tmp_path / "docker"
    shim.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    return env, log


def run_deploy(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(DEPLOY)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )


# ---------------------------------------------------------------------------
# 1. the artefacts exist at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [DOCKERFILE, DOCKERIGNORE, DEPLOY, ROOT / "docs" / "DEPLOY.md"]
)
def test_every_deployment_artefact_is_present(path: Path) -> None:
    assert path.exists(), f"{path.relative_to(ROOT)} is missing"


def test_the_deploy_script_is_executable() -> None:
    """A script that has to be run as `bash scripts/deploy` is a script whose
    documented invocation does not work."""
    assert os.access(DEPLOY, os.X_OK), (
        f"{DEPLOY.relative_to(ROOT)} is not executable: chmod +x it"
    )


# ---------------------------------------------------------------------------
# 2. the interpreter is the one the tests ran on
# ---------------------------------------------------------------------------


def test_the_image_is_pinned_to_an_exact_python_patch() -> None:
    """A floating tag is not a pin. `python:3.14-slim` moves under you between
    two builds of the same commit, which is the opposite of what an image tagged
    with a commit promises."""
    assert re.search(r"python:\d+\.\d+\.\d+-", base_image()), (
        f"FROM {base_image()!r} does not pin a patch version"
    )


def test_the_image_python_satisfies_the_floor_pyproject_declares() -> None:
    match = re.search(r"python:(\d+)\.(\d+)\.(\d+)-", base_image())
    assert match is not None
    image = (int(match.group(1)), int(match.group(2)))
    assert image >= requires_python_floor(), (
        f"the image is on {image} and pyproject.toml requires "
        f"{requires_python_floor()} or later"
    )


def test_the_image_python_is_the_series_every_gate_actually_ran_on() -> None:
    """`requires-python` is the floor, not the measurement.

    `.python-version` is what uv resolves in CI and in the local virtualenv, so
    it is the only interpreter any gate has ever executed. An image built on the
    floor instead would ship a runtime nothing has tested, with a green suite to
    say otherwise. Two numbers that must be equal, checked rather than
    remembered.
    """
    match = re.search(r"python:(\d+\.\d+)\.\d+-", base_image())
    assert match is not None
    assert match.group(1) == PYTHON_VERSION.read_text(encoding="utf-8").strip(), (
        f"the image is on {match.group(1)} and .python-version says "
        f"{PYTHON_VERSION.read_text(encoding='utf-8').strip()}"
    )


# ---------------------------------------------------------------------------
# 3. non-root
# ---------------------------------------------------------------------------


def test_the_image_runs_as_a_non_root_user() -> None:
    user = runs_as(dockerfile())
    assert not is_root(user), (
        f"the image runs as {user or 'root (no USER instruction)'}"
    )


def test_the_user_is_numeric_so_a_runtime_can_check_it() -> None:
    """A platform that enforces "must not run as root" compares uids. A name it
    cannot resolve is a check it cannot make."""
    user = runs_as(dockerfile())
    assert user is not None
    assert user.split(":")[0].isdigit(), f"USER {user} is not numeric"


def test_the_control_a_dockerfile_with_no_user_instruction_is_caught() -> None:
    """The control. Docker's default is root, so an ABSENT USER is the failure
    mode, not a malformed one — which is why `is_root(None)` is True."""
    planted = instructions('FROM python:3.14.6-slim\nCMD ["python"]\n')
    assert is_root(runs_as(planted))


def test_the_control_a_dockerfile_that_returns_to_root_is_caught() -> None:
    """A second USER later in the file wins. The predicate reads the last one."""
    planted = instructions(
        "FROM python:3.14.6-slim\nUSER 10001:10001\nRUN true\nUSER root\n"
    )
    assert is_root(runs_as(planted))


# ---------------------------------------------------------------------------
# 4. the durable database is on a declared volume
# ---------------------------------------------------------------------------


def test_the_image_declares_a_volume() -> None:
    assert volumes(dockerfile()), (
        "no VOLUME is declared, so the audit trail would live on the "
        "container's disposable filesystem"
    )


def test_the_declared_volume_is_the_directory_the_database_is_written_to() -> None:
    """Two values that must be equal, so neither can be edited alone.

    `ACCOUNTANT_DB` says where the file goes; `VOLUME` says which directory
    survives the container. If they disagree the image looks correct and loses
    the database anyway.
    """
    database = environment(dockerfile()).get("ACCOUNTANT_DB")
    assert database is not None, "the image does not set ACCOUNTANT_DB"
    assert str(Path(database).parent) in volumes(dockerfile()), (
        f"ACCOUNTANT_DB={database} but the volumes are {volumes(dockerfile())}"
    )


def test_the_database_variable_is_the_one_the_application_reads() -> None:
    """Named from the source, not from memory. A Dockerfile setting a variable
    the app does not read is a setting with no effect and no error."""
    declared = re.search(r'ENV_DB = "([A-Z_]+)"', APP.read_text(encoding="utf-8"))
    assert declared is not None
    assert declared.group(1) in environment(dockerfile())


def test_the_control_a_dockerfile_with_no_volume_is_caught() -> None:
    planted = instructions(
        "FROM python:3.14.6-slim\nENV ACCOUNTANT_DB=/app/data/app.db\n"
    )
    assert not volumes(planted)


def test_the_control_a_volume_on_the_wrong_directory_is_caught() -> None:
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "ENV ACCOUNTANT_DB=/app/data/app.db\n"
        'VOLUME ["/app/cache"]\n'
    )
    database = environment(planted)["ACCOUNTANT_DB"]
    assert str(Path(database).parent) not in volumes(planted)


# ---------------------------------------------------------------------------
# 5. authentication is required, because nothing turns it off
# ---------------------------------------------------------------------------


def test_local_dev_mode_is_never_set_in_the_image() -> None:
    """Unset means a login is required, and that default failing closed is the
    whole point. Set to 1 it means every request runs as tenant `local-dev` and
    anybody who can reach the port can read and write those books."""
    assert "LOCAL_DEV_MODE" not in environment(dockerfile()), (
        "the image sets LOCAL_DEV_MODE, which disables authentication entirely"
    )


def test_the_control_a_dockerfile_that_sets_local_dev_mode_is_caught() -> None:
    planted = instructions("FROM python:3.14.6-slim\nENV LOCAL_DEV_MODE=1\n")
    assert "LOCAL_DEV_MODE" in environment(planted)


def test_the_control_local_dev_mode_in_a_comment_is_not_a_finding() -> None:
    """The other direction, and the reason this file parses instructions.

    The real Dockerfile explains at length why the variable is absent. A scan
    that could not tell prose from an instruction would fail on the explanation,
    and the fix for that failure would be to delete the explanation.
    """
    planted = instructions("FROM python:3.14.6-slim\n# never set LOCAL_DEV_MODE=1\n")
    assert "LOCAL_DEV_MODE" not in environment(planted)


def test_no_secret_is_baked_into_the_image() -> None:
    """A secret in a layer is a secret in every registry, cache and backup that
    image ever touches. The check is on the NAME, so it fires before anybody has
    to judge whether a particular value was a real credential."""
    assert baked_secrets(dockerfile()) == []


def test_the_control_a_dockerfile_that_bakes_a_credential_is_caught() -> None:
    planted = instructions(
        "FROM python:3.14.6-slim\nENV ACCOUNTANT_CONNECTOR_SECRET=placeholder\n"
    )
    assert baked_secrets(planted) == ["ACCOUNTANT_CONNECTOR_SECRET"]


# ---------------------------------------------------------------------------
# 6. the healthcheck measures readiness
# ---------------------------------------------------------------------------


def test_the_image_declares_a_healthcheck() -> None:
    assert healthcheck(dockerfile()) is not None


def test_the_healthcheck_asks_the_readiness_endpoint() -> None:
    """`/health` needs no session — do_GET answers it before `_identify` runs,
    and the company check is deliberately exempt, because a readiness endpoint
    that needs Tally to answer cannot report that Tally is not answering. A
    healthcheck pointed anywhere else would be measuring the login page."""
    check = healthcheck(dockerfile())
    assert check is not None
    assert "/health" in check, check


def test_the_healthcheck_calls_the_port_the_server_actually_binds() -> None:
    """Read from `serve()`'s own defaults. A healthcheck on a port nothing is
    listening on reports unhealthy for ever, and looks like an app fault."""
    check = healthcheck(dockerfile())
    assert check is not None
    found = re.search(r"127\.0\.0\.1:(\d+)/health", check)
    assert found is not None, f"the healthcheck url is not readable: {check}"
    assert int(found.group(1)) == serve_defaults()["port"]


def test_the_control_a_dockerfile_with_no_healthcheck_is_caught() -> None:
    planted = instructions('FROM python:3.14.6-slim\nCMD ["python"]\n')
    assert healthcheck(planted) is None


def test_the_control_healthcheck_none_counts_as_no_healthcheck() -> None:
    """`HEALTHCHECK NONE` is the documented way to switch one off. It is present
    as an instruction and absent as a check, and only one of those matters."""
    planted = instructions("FROM python:3.14.6-slim\nHEALTHCHECK NONE\n")
    assert healthcheck(planted) is None


# ---------------------------------------------------------------------------
# 7. the image advertises only what the app can actually answer
# ---------------------------------------------------------------------------


def test_the_image_advertises_no_port_while_the_server_binds_loopback() -> None:
    """EXPOSE is documentation, and documentation of a port that answers nobody
    is worse than none: it reads as a promise.

    `serve()` binds 127.0.0.1, so the port answers only processes inside the
    container. This test reads that default out of the source, so the day
    `serve()` takes its bind address from the environment the test fails and
    says to add the line — nobody has to remember.
    """
    host = serve_defaults()["host"]
    assert isinstance(host, str)
    loopback = host.startswith("127.") or host in {"localhost", "::1"}
    exposed = [rest for name, rest in dockerfile() if name == "EXPOSE"]
    if loopback:
        assert not exposed, (
            f"the Dockerfile EXPOSEs {exposed} while serve() binds {host}, which "
            f"nothing outside the container can reach"
        )
    else:
        assert exposed, (
            f"serve() now binds {host}, so the image must EXPOSE its port. See "
            f"docs/DEPLOY.md, 'What this image cannot do yet'."
        )


def test_the_image_starts_the_app_the_way_a_person_does() -> None:
    """One startup path and not two. A second, subtly different way to start the
    process is exactly how "it works on mine" happens."""
    commands = [rest for name, rest in dockerfile() if name == "CMD"]
    assert len(commands) == 1, f"the image has {len(commands)} CMD instructions"
    assert "accountant.web" in commands[0]
    assert (ROOT / "accountant" / "web" / "__main__.py").exists()


# ---------------------------------------------------------------------------
# 8. the build context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern", ["data", "*.db", ".venv", "__pycache__", ".git", "tests"]
)
def test_the_dockerignore_keeps_it_out_of_the_build_context(pattern: str) -> None:
    """`data/` and `*.db` are the customer's audit trail, and `.git` carries
    every version of every file ever committed, including anything later
    removed from the tip."""
    assert pattern in ignored(), f"{pattern!r} is not excluded"


def test_the_control_a_dockerignore_that_forgets_the_database_is_caught() -> None:
    planted = {
        line.strip().rstrip("/")
        for line in ".venv/\n__pycache__/\n.git/\ntests/\n".splitlines()
        if line.strip()
    }
    assert "data" not in planted
    assert "*.db" not in planted


# ---------------------------------------------------------------------------
# 9. the deploy script stops rather than continuing wrongly
# ---------------------------------------------------------------------------


def test_the_deploy_script_stops_at_the_first_failure() -> None:
    """`set -euo pipefail`: exit on error, refuse an unset variable, and let a
    failure inside a pipeline fail the pipeline. Without it a failed build is
    followed by a push of whatever image was already tagged."""
    assert "set -euo pipefail" in DEPLOY.read_text(encoding="utf-8")


def test_the_control_a_script_without_the_strict_options_is_caught() -> None:
    assert "set -euo pipefail" not in "#!/usr/bin/env bash\nset -e\ndocker push x\n"


# ---------------------------------------------------------------------------
# 10. the registry is never invented
# ---------------------------------------------------------------------------


def test_the_registry_variable_has_no_default_in_the_script() -> None:
    """A default registry is how a customer's image gets pushed somewhere
    nobody chose."""
    assert not defaulted(shell_code(DEPLOY.read_text(encoding="utf-8")), REGISTRY)


def test_the_control_a_defaulted_registry_is_caught() -> None:
    assert defaulted(f'{REGISTRY}="registry.example.invalid/ours"\n', REGISTRY)
    assert defaulted(f'REF="${{{REGISTRY}:-registry.example.invalid}}/x"\n', REGISTRY)


def test_the_control_a_usage_example_in_the_help_text_is_not_a_default() -> None:
    """The other direction, and the reason the script is stripped first.

    The line the refusal prints is an instruction to a reader. The predicate
    must not confuse "here is what to type" with "here is what I decided for
    you" — and it must still catch the second one on the very next line.
    """
    text = (
        "#!/usr/bin/env bash\n"
        f"#   {REGISTRY}=registry.example.invalid/yours ./scripts/deploy\n"
        "cat >&2 <<'EOF'\n"
        f"    {REGISTRY}=registry.example.invalid/yours ./scripts/deploy\n"
        "EOF\n"
    )
    assert defaulted(text, REGISTRY), "the raw text does contain the shape"
    assert not defaulted(shell_code(text), REGISTRY)
    assert defaulted(shell_code(text + f'{REGISTRY}="ours.invalid"\n'), REGISTRY)


def test_the_control_reading_a_possibly_unset_variable_is_not_a_default() -> None:
    """`${VAR:-}` supplies nothing. It is the standard way to READ a variable
    that may be unset while `set -u` is on, and it is how the refusal itself is
    written. A check that banned it would ban the guard."""
    assert not defaulted(f'if [ -z "${{{REGISTRY}:-}}" ]; then\n', REGISTRY)


def test_the_script_refuses_and_names_the_variable_when_it_is_unset(
    tmp_path: Path,
) -> None:
    env, _ = fake_docker(tmp_path)
    env.pop(REGISTRY, None)

    result = run_deploy(env)

    assert result.returncode != 0, result.stdout
    assert REGISTRY in result.stderr
    assert "REFUSED" in result.stderr


def test_the_script_touches_docker_not_at_all_when_the_registry_is_unset(
    tmp_path: Path,
) -> None:
    """The refusal has to come BEFORE the build. A script that builds an image
    and then discovers it has nowhere to put it has already spent the time and
    already written the layers."""
    env, log = fake_docker(tmp_path)
    env.pop(REGISTRY, None)

    run_deploy(env)

    assert not log.exists(), f"docker was called: {log.read_text(encoding='utf-8')}"


def test_the_script_builds_tags_and_pushes_to_the_registry_it_was_given(
    tmp_path: Path,
) -> None:
    """The real script, run for real, against a recorded stand-in for docker.

    A text scan for the words "docker build" would pass on a script that only
    mentions them in a comment.
    """
    env, log = fake_docker(tmp_path)
    env[REGISTRY] = "registry.example.invalid/placeholder-namespace"

    result = run_deploy(env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("build ") for call in calls), calls
    assert any(call.startswith("push ") for call in calls), calls
    for call in calls:
        assert "registry.example.invalid/placeholder-namespace/accountant-dad:" in call


def test_the_pushed_tag_names_the_commit_the_image_was_built_from(
    tmp_path: Path,
) -> None:
    """A tag that cannot be traced to a commit is an image nobody can reproduce
    or roll back to."""
    env, log = fake_docker(tmp_path)
    env[REGISTRY] = "registry.example.invalid/placeholder-namespace"
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout.strip()

    run_deploy(env)

    pushed = [
        call for call in log.read_text(encoding="utf-8").splitlines() if "push " in call
    ]
    assert pushed, "nothing was pushed"
    for call in pushed:
        assert commit in call, call


def test_the_script_pushes_no_moving_tag(tmp_path: Path) -> None:
    """No `:latest`. A moving tag names whatever was pushed last, including the
    one somebody pushed to test something, and a host that pulls it runs a build
    nobody selected."""
    env, log = fake_docker(tmp_path)
    env[REGISTRY] = "registry.example.invalid/placeholder-namespace"

    run_deploy(env)

    for call in log.read_text(encoding="utf-8").splitlines():
        assert ":latest" not in call, call


def test_the_script_says_plainly_that_nothing_was_deployed(tmp_path: Path) -> None:
    """It pushes an artefact. There is no host, and a script called `deploy`
    that stays quiet about that is a script people will believe deployed."""
    env, _ = fake_docker(tmp_path)
    env[REGISTRY] = "registry.example.invalid/placeholder-namespace"

    result = run_deploy(env)

    assert "NOTHING WAS DEPLOYED" in result.stdout


# ---------------------------------------------------------------------------
# 11. the image names the tenant and values it nowhere
# ---------------------------------------------------------------------------


def test_the_image_carries_no_tenant_id() -> None:
    """A tenant id is a customer's identity, and an image goes to a registry.

    Baking one in would put that customer's name in every registry, cache and
    backup the image ever touches, and would silently make this one image PER
    CUSTOMER — a separate build to tag, push and audit every time somebody signs
    up. The image is the same for everybody.
    """
    assert baked_tenant(dockerfile()) is None, (
        f"the image carries the tenant id {baked_tenant(dockerfile())!r}"
    )


def test_the_image_declares_the_tenant_variable_so_it_cannot_be_missed() -> None:
    """Empty, but PRESENT. `docker inspect` and `docker history` show a declared
    variable and cannot show one nobody wrote down, so the operator working out
    what to pass to `docker run` is told by the image itself."""
    assert TENANT in environment(dockerfile()), (
        f"the image never mentions {TENANT}, so nothing in it says the server "
        f"refuses every request until that variable is set"
    )


def test_the_tenant_variable_is_the_one_the_application_reads() -> None:
    """Named from the source, not from memory. A Dockerfile declaring a variable
    the app does not read is a declaration with no effect and no error."""
    declared = re.search(r'ENV_TENANT = "([A-Z_]+)"', APP.read_text(encoding="utf-8"))
    assert declared is not None
    assert declared.group(1) in environment(dockerfile())


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_tenant_in_the_image_still_means_refuse(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """The property the empty declaration RESTS on, measured rather than assumed.

    Declaring the variable is only safe while a blank value behaves exactly as
    an absent one does. The day `served_tenant()` accepts it, the image ships a
    tenant id of `""` — a value, not an absence — and the fail-closed refusal
    the whole of defect J1 turns on is gone with no other symptom anywhere.

    Whitespace is in the list because it is the same mistake with a space in it:
    a `docker run -e ACCOUNTANT_TENANT=" "`, or a value pasted with a newline,
    must not be a tenant either. Two mutants were needed to prove both halves —
    dropping the `.strip()` survives an empty-string-only check.
    """
    monkeypatch.delenv(ENV_LOCAL_DEV_MODE, raising=False)
    monkeypatch.setenv(TENANT, blank)

    with pytest.raises(AuthRefusal) as refusal:
        app.served_tenant()

    assert refusal.value.status == 403
    assert TENANT in refusal.value.reason


def test_the_control_a_dockerfile_that_bakes_a_tenant_id_is_caught() -> None:
    planted = instructions(
        f"FROM python:3.14.6-slim\nENV {TENANT}=a-real-customers-id\n"
    )
    assert baked_tenant(planted) == "a-real-customers-id"


def test_the_control_a_quoted_tenant_id_is_still_a_tenant_id() -> None:
    """Quoting is punctuation. The obvious way to slip a value past a check that
    only knew the bare spelling is to write the quoted one."""
    planted = instructions(f'FROM python:3.14.6-slim\nENV {TENANT}="a-customer"\n')
    assert baked_tenant(planted) == "a-customer"


def test_the_control_a_dockerfile_that_never_mentions_the_tenant_is_caught() -> None:
    """The other guard's control. Silence is the failure mode here: an image
    that says nothing leaves the operator to find the variable in a document."""
    planted = instructions("FROM python:3.14.6-slim\nENV ACCOUNTANT_DB=/app/x.db\n")
    assert TENANT not in environment(planted)


def test_the_control_an_empty_declaration_is_not_a_baked_tenant() -> None:
    """The direction that keeps the guard usable. All three spellings of empty
    are a note to the operator, not a customer's identity."""
    for empty in ('""', "''", ""):
        planted = instructions(f"FROM python:3.14.6-slim\nENV {TENANT}={empty}\n")
        assert baked_tenant(planted) is None, empty
        assert TENANT in environment(planted), empty


# ---------------------------------------------------------------------------
# 12. the deploy script neither defaults the tenant nor stays quiet about it
# ---------------------------------------------------------------------------


def test_the_tenant_variable_has_no_default_in_the_script() -> None:
    """The same rule as the registry, for the same reason. A default tenant id
    is a customer's books handed to whoever the fallback happened to name."""
    assert not defaulted(shell_code(DEPLOY.read_text(encoding="utf-8")), TENANT)


def test_the_control_a_defaulted_tenant_is_caught() -> None:
    assert defaulted(f'{TENANT}="a-real-customers-id"\n', TENANT)
    assert defaulted(f'REF="${{{TENANT}:-a-real-customers-id}}"\n', TENANT)


def test_the_script_says_the_runtime_must_set_the_tenant(tmp_path: Path) -> None:
    """It builds and pushes; it cannot set a run-time variable on a machine it
    has never heard of. What it CAN do is refuse to be quiet, and the sentence
    has to carry the consequence — advice is what gets skipped."""
    env, _ = fake_docker(tmp_path)
    env[REGISTRY] = "registry.example.invalid/placeholder-namespace"

    result = run_deploy(env)

    assert announces_the_tenant(result.stdout), result.stdout


def test_the_control_a_script_that_never_mentions_the_tenant_is_caught() -> None:
    assert not announces_the_tenant("pushed x\n\nNOTHING WAS DEPLOYED.\n")


def test_the_control_naming_the_tenant_without_the_consequence_is_caught() -> None:
    """The regression this predicate is shaped for: somebody shortens the
    closing message, keeps the variable name and drops what happens without it.
    A reader is then told to set something and never told why."""
    assert not announces_the_tenant(f"Remember to set {TENANT} on the host.\n")


# ---------------------------------------------------------------------------
# 13. the startup banner says which customer this process serves
# ---------------------------------------------------------------------------
#
# Behavioural, not textual. `config_from_environment()` is called for real and
# its provenance lines are read, because the property is "the banner reports the
# tenant", and a source scan would keep passing on a line that had stopped being
# printed.


def provenance_with(
    monkeypatch: pytest.MonkeyPatch, **environ: str | None
) -> list[str]:
    for name, value in environ.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return app.config_from_environment()[3]


def test_the_startup_banner_names_the_tenant_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first thing printed at startup already said WHICH books this process
    opens. Until 2026-08-11 it could not say WHOSE, and those are two halves of
    one statement."""
    lines = provenance_with(
        monkeypatch,
        **{ENV_LOCAL_DEV_MODE: None, TENANT: "placeholder-tenant-id"},
    )

    reported = named_tenant(lines)
    assert reported is not None, lines
    assert "placeholder-tenant-id" in reported
    assert "environment" in reported, reported


def test_the_startup_banner_says_local_dev_rather_than_a_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In LOCAL_DEV_MODE the served tenant is the constant `local-dev` — that is
    what `served_tenant()` returns and what `authenticate` hands out. Printing a
    blank would read as the misconfiguration below on the one setup where it is
    not one."""
    lines = provenance_with(monkeypatch, **{ENV_LOCAL_DEV_MODE: "1", TENANT: None})

    reported = named_tenant(lines)
    assert reported is not None, lines
    assert LOCAL_DEV_TENANT in reported, reported


def test_the_startup_banner_names_the_refusal_when_no_tenant_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset is not a default and must not be printed as one. The line says what
    the server will do about it, which is refuse every request."""
    lines = provenance_with(monkeypatch, **{ENV_LOCAL_DEV_MODE: None, TENANT: None})

    reported = named_tenant(lines)
    assert reported is not None, lines
    assert states_the_refusal(reported), reported
    assert "(default)" not in reported, reported


def test_the_control_a_banner_that_never_reports_the_tenant_is_caught() -> None:
    """The banner as it stood before 2026-08-11: every other resolved value, and
    silence about the customer."""
    assert (
        named_tenant(
            [
                "ACCOUNTANT_DB='/app/data/app.db' (environment)",
                "ACCOUNTANT_COMPANY='Some Company' (default)",
            ]
        )
        is None
    )


def test_the_control_a_tenant_mentioned_in_passing_is_not_a_report() -> None:
    """The other direction. A sentence about the variable is not the banner
    reporting a resolved value, and a predicate that could not tell them apart
    would pass on a banner that had gone quiet."""
    assert named_tenant([f"set {TENANT} before starting this server"]) is None


# ---------------------------------------------------------------------------
# 14. the docs state the tenant's failure mode, not just its name
# ---------------------------------------------------------------------------


def test_the_deploy_document_lists_the_tenant_variable() -> None:
    assert TENANT in documented_variables(DEPLOY_DOC.read_text(encoding="utf-8")), (
        f"docs/DEPLOY.md does not list {TENANT} in its environment table"
    )


def test_the_deploy_document_states_what_happens_when_the_tenant_is_missing() -> None:
    """Naming a variable is advice. "Every request is refused 403" is the
    reason somebody sets it, and it is deliberate rather than a bug to route
    around — which is exactly what a reader who was told only the first half
    would try to do."""
    rows = documented_variables(DEPLOY_DOC.read_text(encoding="utf-8"))
    assert TENANT in rows
    assert states_the_refusal(rows[TENANT]), rows[TENANT]


def test_every_accountant_variable_the_image_sets_is_documented() -> None:
    """Two lists that must agree, checked rather than remembered.

    The image is where a variable becomes invisible: it is set, it works, and
    nobody reading the document knows it exists. Scoped to `ACCOUNTANT_*`
    because the `PYTHON*` settings are the interpreter's and are explained in
    the Dockerfile beside the lines that set them.
    """
    image = {
        name for name in environment(dockerfile()) if name.startswith("ACCOUNTANT")
    }
    documented = set(documented_variables(DEPLOY_DOC.read_text(encoding="utf-8")))
    assert image <= documented, f"undocumented: {sorted(image - documented)}"


def test_the_control_a_document_that_omits_the_tenant_row_is_caught() -> None:
    planted = (
        "| Variable | Set in the image? | If it is missing |\n"
        "|---|---|---|\n"
        "| `ACCOUNTANT_DB` | **Yes** | Falls back to a disposable path. |\n"
    )
    assert TENANT not in documented_variables(planted)


def test_the_control_a_tenant_row_with_no_failure_mode_is_caught() -> None:
    """The likelier regression: the row survives an edit and the consequence in
    it does not."""
    planted = (
        "| Variable | Set in the image? | If it is missing |\n"
        "|---|---|---|\n"
        f"| `{TENANT}` | Declared, empty | Set it to the tenant id. |\n"
    )
    rows = documented_variables(planted)
    assert TENANT in rows
    assert not states_the_refusal(rows[TENANT])


def test_the_control_the_tenant_named_only_in_prose_is_not_a_documented_row() -> None:
    """The reason the table is parsed rather than the file scanned. The document
    explains this variable at length; the explanation is not the row an operator
    reads, and must not be able to stand in for it."""
    planted = (
        f"`{TENANT}` names the customer this process serves, and unset means "
        f"every request is refused 403.\n"
    )
    assert TENANT not in documented_variables(planted)


# ---------------------------------------------------------------------------
# reading the dependency install, and the variables the code actually reads
# ---------------------------------------------------------------------------


def run_commands(instrs: list[tuple[str, str]]) -> list[str]:
    """Every RUN as one string, continuations already joined."""
    return [rest for name, rest in instrs if name == "RUN"]


def copied_paths(instrs: list[tuple[str, str]]) -> str:
    """Every COPY argument, joined. What reaches the image at all."""
    return " ".join(rest for name, rest in instrs if name == "COPY")


def locked_install(instrs: list[tuple[str, str]]) -> str | None:
    """The RUN that installs dependencies FROM THE LOCKFILE, or None.

    `--locked` is the half that makes this a pin rather than a resolution: uv
    installs exactly the versions `uv.lock` names, and refuses when `uv.lock`
    and `pyproject.toml` have drifted apart. `pip install pypdf` would satisfy
    "installs something" and neither of those — it would put whatever version
    the index served that morning into an image tagged with a commit.
    """
    for command in run_commands(instrs):
        if "uv sync" in command and "--locked" in command:
            return command
    return None


def system_installs(instrs: list[tuple[str, str]]) -> list[str]:
    """Every RUN that installs a Debian package. Nothing pins these."""
    return [c for c in run_commands(instrs) if re.search(r"\bapt(-get)?\s+install", c)]


#: The engine and the one language pack this image is allowed to carry.
#: `pytesseract` is a wrapper; `tesseract-ocr` is the binary it shells out to,
#: and `tesseract-ocr-eng` is the trained data it reads English with. English
#: ONLY — "as small as possible" is the constraint the owner set on this
#: install, and each further pack is tens of megabytes of a language no corpus
#: in this repository contains.
OCR_PACKAGES = ("tesseract-ocr", "tesseract-ocr-eng")


def apt_packages(instrs: list[tuple[str, str]]) -> list[str]:
    """Every Debian package NAME an `apt-get install` here asks for.

    Flags are dropped — `-y` and `--no-install-recommends` are not packages —
    and each argument list stops at `&&`, `;` or `|`, so the `rm -rf` ending the
    same RUN is not read as a package somebody asked to install.
    """
    names: list[str] = []
    for command in system_installs(instrs):
        for install in re.finditer(r"\bapt(?:-get)?\s+install\b([^&|;]*)", command):
            names.extend(t for t in install.group(1).split() if not t.startswith("-"))
    return names


def canonical(name: str) -> str:
    """PEP 503 normalisation. `Pillow` and `pillow` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_dependencies() -> set[str]:
    """The runtime dependency NAMES `pyproject.toml` declares."""
    with PYPROJECT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    required = [str(item) for item in data["project"]["dependencies"]]
    return {canonical(re.split(r"[<>=!~;\[ ]", item)[0]) for item in required}


def locked_dependencies() -> set[str]:
    """The runtime dependency names `uv.lock` resolves for this project."""
    with PYPROJECT.open("rb") as fh:
        project = canonical(str(tomllib.load(fh)["project"]["name"]))
    with UV_LOCK.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    for package in data["package"]:
        if canonical(str(package["name"])) == project:
            found = package.get("dependencies", [])
            return {canonical(str(entry["name"])) for entry in found}
    raise AssertionError(f"uv.lock carries no entry for {project!r}")


def _reads_the_environment(func: ast.expr) -> bool:
    """True for `os.environ.get` and `os.getenv`, false for any other `.get`.

    Narrow on purpose. `mapping.get("SOME_KEY")` is not a variable this process
    reads from its environment, and a predicate that could not tell the two
    apart would demand documentation for dictionary keys.
    """
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return isinstance(func.value, ast.Name) and func.value.id == "os"
    inner = func.value
    return (
        func.attr == "get"
        and isinstance(inner, ast.Attribute)
        and inner.attr == "environ"
        and isinstance(inner.value, ast.Name)
        and inner.value.id == "os"
    )


def env_names_in(source: str) -> set[str]:
    """Every environment variable one module reads, from its AST.

    Two shapes, because the package uses both: an `ENV_X = "NAME"` constant
    handed to `os.environ.get` later, and a literal handed to it directly.

    From the AST and not by grep, for the reason the Dockerfile is parsed into
    instructions. These modules explain at length which variables they refuse
    to read — `LOCAL_DEV_MODE` in the Dockerfile, `ACCOUNTANT_TLS_*` in a
    comment about a leg that carries no TLS — and a scan cannot tell an
    explanation from a read.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            named = [t.id for t in targets if isinstance(t, ast.Name)]
            value = node.value
            if (
                any(name.startswith("ENV_") for name in named)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                found.add(value.value)
        elif isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if _reads_the_environment(node.func) and isinstance(first, ast.Constant):
                found.add(str(first.value))
    return found


def variables_the_code_reads() -> set[str]:
    """Every environment variable the SHIPPED package reads.

    `accountant/` only: it is the one directory the image copies. A variable
    read by a test or by `scripts/deploy` is not one an operator has to set.
    """
    names: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        names |= env_names_in(path.read_text(encoding="utf-8"))
    return names


def states_the_default(sentence: str) -> bool:
    """True when a row says which way a flag falls with nobody setting it.

    "Set it to 0 to stop every write" is advice about the value you might
    choose. "Unset, it is ON and writes are permitted" is the fact that decides
    whether a container somebody starts posts into real books, and a reader
    given only the first half will assume the safer half.
    """
    lowered = sentence.lower()
    return "default" in lowered and re.search(r"\b(on|off)\b", lowered) is not None


# ---------------------------------------------------------------------------
# 15. the image installs the code's dependencies, pinned to the lockfile
# ---------------------------------------------------------------------------


def test_the_image_installs_the_runtime_dependencies_the_project_declares() -> None:
    """The defect, 2026-08-13: `dependencies = []` stopped being true and the
    image did not notice.

    D-30 gave the project pypdf, pytesseract and Pillow, and the Dockerfile
    went on installing nothing — its own comment still said "NOTHING IS
    INSTALLED". `accountant/extract/textlayer.py` imports pypdf and
    `freeocr.py` imports pytesseract and PIL, so the image carried code that
    could not import. Nothing in the image was edited to break it, which is
    exactly why this is a check and not a comment.
    """
    assert declared_dependencies(), "pyproject.toml declares no runtime dependency"
    assert locked_install(dockerfile()) is not None, (
        f"nothing in the Dockerfile installs {sorted(declared_dependencies())}, "
        f"so the image ships code that cannot import"
    )


def test_the_lockfile_reaches_the_build_that_reads_it() -> None:
    """An install is only a pin while the file it pins to is in the context.

    Two places lose it and neither says so out loud: a COPY that never mentions
    it, and a `.dockerignore` line that keeps it from the daemon. The second is
    the likelier one here — `.dockerignore` already excludes fifteen paths.
    """
    copied = copied_paths(dockerfile())
    for needed in ("pyproject.toml", "uv.lock"):
        assert needed in copied, f"the build never copies {needed}"
        assert needed not in ignored(), f".dockerignore keeps {needed} out"


def test_the_image_installs_no_development_tooling() -> None:
    """pytest, ruff, pyright and eleven others are an EXTRA here, and CI asks
    for it by name: `uv sync --extra dev --locked`. This image must not.

    `--no-dev` is redundant today — `dev` is an extra, so `uv sync` leaves it
    out unless asked — and it stays anyway: the day `dev` moves to a dependency
    group, uv's default flips to installing it and nothing else would notice.
    """
    command = locked_install(dockerfile())
    assert command is not None
    assert "--extra dev" not in command and "--all-extras" not in command, command
    assert "--no-dev" in command, command


def test_the_lockfile_resolves_exactly_the_dependencies_pyproject_declares() -> None:
    """Two lists that must be equal, checked here rather than on a build day.

    A dependency added to `pyproject.toml` and never locked is one the image
    will not install. `uv sync --locked` does refuse it — inside a `docker
    build` nobody has run yet, which is the wrong place to find out.
    """
    assert locked_dependencies() == declared_dependencies(), (
        f"pyproject.toml declares {sorted(declared_dependencies())} and uv.lock "
        f"resolves {sorted(locked_dependencies())}. Run `uv lock`."
    )


def test_the_image_installs_the_ocr_engine_a_photo_upload_needs() -> None:
    """CORRECTED 2026-08-13, and it asserts the OPPOSITE of what it did.

    This was `test_the_image_installs_no_system_package_including_tesseract`,
    and it asserted `system_installs(dockerfile()) == []`. The measurement it
    rested on has not changed and is exactly why the owner reversed it: with
    `PATH=/usr/bin:/bin` and no `tesseract` binary, `default_extractor()` on a
    corpus PNG returns all four fields unread, each saying `not_found: the text
    reading program is not installed on this machine`. `DEFAULT_BACKEND` is
    `ladder` and `app.py` routes every uploaded image to it, so on the shipped
    artefact a photograph read ZERO of four fields, always.

    Owner decision, 2026-08-13, verbatim: *"This is required because the MVP
    requirement is: a user can upload a photo and get fields read. A Docker
    image where photos are dead by design does not satisfy that requirement."*

    The costs the old docstring listed are ACCEPTED, not refuted — apt pins
    nothing, and the image is bigger and slower to build. The half of that
    guard which was never about tesseract survives below, as
    `test_the_image_installs_no_system_package_beyond_the_ocr_engine`.
    """
    assert "tesseract-ocr" in apt_packages(dockerfile()), (
        f"the image installs {apt_packages(dockerfile())}, so an uploaded "
        f"photograph reads zero of four fields on the artefact that ships"
    )


def test_the_image_installs_the_language_pack_the_engine_reads_with() -> None:
    """An engine with no trained data reads nothing.

    `tesseract-ocr` depends on `tesseract-ocr-eng` on Debian, so this asks apt
    for something it would supply anyway — deliberately. WHICH languages this
    image can read is a decision, and a decision resting on another package's
    dependency list is one no reader of this file can see or defend.
    """
    assert "tesseract-ocr-eng" in apt_packages(dockerfile()), (
        f"the image installs {apt_packages(dockerfile())} and names no "
        f"language pack, so which languages it reads is apt's choice"
    )


def test_the_image_carries_no_language_pack_beyond_english() -> None:
    """Smallest possible is the constraint, and language data is where size is.

    Each `tesseract-ocr-<lang>` is tens of megabytes and `tesseract-ocr-all` is
    every language there is. Nothing in this repository reads a bill in
    anything but English, and no corpus here contains one.
    """
    extra = [
        package
        for package in apt_packages(dockerfile())
        if package.startswith("tesseract-ocr-") and package not in OCR_PACKAGES
    ]
    assert extra == [], f"language packs nothing in this repository reads: {extra}"


def test_the_image_installs_no_system_package_beyond_the_ocr_engine() -> None:
    """What survives of the old guard, and the half that was never about OCR.

    apt pins nothing: every package here is whatever Debian's index served on
    the morning of the build, which is the one property `uv sync --locked`
    exists to give the wheels. TWO named packages is a cost the owner weighed
    and accepted. A third that arrived because somebody needed it once is not.
    """
    strangers = [p for p in apt_packages(dockerfile()) if p not in OCR_PACKAGES]
    assert strangers == [], (
        f"the image installs unpinned system packages nobody decided on: {strangers}"
    )


def test_the_engine_is_installed_without_its_recommended_packages() -> None:
    """`--no-install-recommends`, because "keep it as small as possible" is an
    explicit constraint on this install and not a preference. Without the flag
    apt pulls tesseract-ocr's whole recommends chain into the layer."""
    for command in system_installs(dockerfile()):
        assert "--no-install-recommends" in command, command


def test_the_package_lists_are_downloaded_and_deleted_in_one_layer() -> None:
    """`apt-get update` writes tens of megabytes into `/var/lib/apt/lists`, and
    a layer is immutable: delete them in a LATER `RUN` and the bytes are still
    in the image, hidden under a whiteout, and the image is the same size while
    the Dockerfile reads as if it were not."""
    for command in system_installs(dockerfile()):
        assert "apt-get update" in command, command
        assert "rm -rf /var/lib/apt/lists" in command, command


def test_the_deploy_document_names_the_engine_the_image_now_carries() -> None:
    """The half of this decision a Dockerfile cannot carry.

    The image is bigger and slower to build than it was yesterday, and an
    operator reading only `docs/DEPLOY.md` would not know why. This repository
    has already shipped that failure twice — the Azure rows, and `D-30`'s three
    wheels — so the document is read rather than trusted.
    """
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    for package in OCR_PACKAGES:
        assert package in text, f"docs/DEPLOY.md never mentions {package}"


def test_the_control_a_dockerfile_that_installs_nothing_is_caught() -> None:
    """The image exactly as it stood before 2026-08-13."""
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "COPY accountant/ /app/accountant/\n"
        'CMD ["python", "-m", "accountant.web"]\n'
    )
    assert locked_install(planted) is None


def test_the_control_an_unpinned_install_is_not_a_locked_install() -> None:
    """The likelier wrong fix, and the reason the predicate wants `--locked`.

    Both lines below install the right NAMES. Neither installs the versions the
    4233 tests ran against, and an image tagged with a commit that contains
    versions nobody measured is the drift this whole file exists to catch.
    """
    named = instructions(
        "FROM python:3.14.6-slim\nRUN pip install pypdf pytesseract Pillow\n"
    )
    assert locked_install(named) is None
    unlocked = instructions("FROM python:3.14.6-slim\nRUN uv sync --no-dev\n")
    assert locked_install(unlocked) is None


def test_the_control_a_dockerfile_that_installs_no_engine_is_caught() -> None:
    """The image exactly as it stood until 2026-08-13: the wrapper wheel
    installed, the binary it shells out to absent, and every uploaded
    photograph reading zero of four fields on the artefact that ships."""
    planted = instructions("FROM python:3.14.6-slim\nRUN uv sync --locked --no-dev\n")
    assert apt_packages(planted) == []


def test_the_control_an_unrelated_system_package_is_caught() -> None:
    """CORRECTED 2026-08-13. This planted `tesseract-ocr`, which is now the
    thing the image is supposed to install, so it would have controlled
    nothing. The guard it controls is unchanged: an unpinned Debian package
    that no owner decision named."""
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends curl\n"
    )
    assert system_installs(planted) != []
    assert [p for p in apt_packages(planted) if p not in OCR_PACKAGES] == ["curl"]


def test_the_control_a_language_pack_nothing_here_reads_is_caught() -> None:
    """`tesseract-ocr-all` is the whole point of the size check: one word, every
    language there is, and no test anywhere would otherwise notice."""
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "RUN apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-all\n"
    )
    extra = [
        p
        for p in apt_packages(planted)
        if p.startswith("tesseract-ocr-") and p not in OCR_PACKAGES
    ]
    assert extra == ["tesseract-ocr-all"]


def test_the_control_an_install_that_takes_the_recommends_chain_is_caught() -> None:
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "RUN apt-get update && apt-get install -y tesseract-ocr\n"
    )
    assert all("--no-install-recommends" not in c for c in system_installs(planted))


def test_the_control_package_lists_left_behind_in_the_image_are_caught() -> None:
    """The likelier mistake than forgetting the deletion outright: putting it in
    its own RUN, where it reads as done and removes nothing from the layer
    above it."""
    planted = instructions(
        "FROM python:3.14.6-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends x\n"
        "RUN rm -rf /var/lib/apt/lists/*\n"
    )
    left = [c for c in system_installs(planted) if "rm -rf /var/lib/apt/lists" not in c]
    assert left != []


def test_the_control_tesseract_named_in_a_comment_is_not_an_install() -> None:
    """The other direction, and it matters MORE since 2026-08-13 than it did
    before. The Dockerfile no longer explains why the binary is absent; it
    explains what is installed and what that costs, in prose that names the
    packages. A comment cannot install anything and must not be able to satisfy
    — or fail — a check about what is installed."""
    planted = instructions(
        "FROM python:3.14.6-slim\n# NOT installed: apt-get install -y tesseract-ocr\n"
    )
    assert system_installs(planted) == []
    assert apt_packages(planted) == []


# ---------------------------------------------------------------------------
# 16. every variable the code reads is documented, and no other
# ---------------------------------------------------------------------------


def test_every_environment_variable_the_code_reads_is_documented() -> None:
    """The image is not the only place a variable hides. `ACCOUNTANT_DB` is set
    there and was documented; `ACCOUNTANT_POSTING_ENABLED` is set nowhere,
    defaults to ON, and decides whether this software writes into a real
    business's books — and no document mentioned it until 2026-08-13."""
    documented = set(documented_variables(DEPLOY_DOC.read_text(encoding="utf-8")))
    missing = sorted(variables_the_code_reads() - documented)
    assert not missing, (
        f"the code reads {missing} and docs/DEPLOY.md's environment table does "
        f"not list them. An undocumented variable is one an operator cannot set "
        f"and cannot know the default of."
    )


def test_the_deploy_document_invents_no_variable_the_code_never_reads() -> None:
    """The direction that had actually broken.

    `ACCOUNTANT_AZURE_ENDPOINT` and `ACCOUNTANT_AZURE_KEY` were documented as
    "Declared, empty" in an image that declared neither, for a backend no `.py`
    file in this repository imports. A row for a variable nothing reads is
    worse than no row: an operator sets it, nothing changes, and the document
    says something should have.
    """
    documented = set(documented_variables(DEPLOY_DOC.read_text(encoding="utf-8")))
    invented = sorted(documented - variables_the_code_reads())
    assert not invented, (
        f"docs/DEPLOY.md documents {invented}, which no file under accountant/ "
        f"reads. Delete the row or wire the variable."
    )


@pytest.mark.parametrize("variable", [writedoor.ENV_POSTING, writedoor.ENV_SAFE_MODE])
def test_the_flags_that_decide_whether_books_are_written_state_their_default(
    variable: str,
) -> None:
    """Both FAIL OPEN, and that is the whole reason the default has to be
    written down. Unset, posting is permitted and safe mode is on. A reader who
    is told only "set it to 0 to stop writing" does not learn that a container
    started with neither variable will post."""
    rows = documented_variables(DEPLOY_DOC.read_text(encoding="utf-8"))
    assert variable in rows, f"docs/DEPLOY.md does not list {variable}"
    assert states_the_default(rows[variable]), rows[variable]


def test_the_control_a_variable_named_only_in_prose_is_not_one_the_code_reads() -> None:
    """The reason the source is parsed. This package writes paragraphs about
    variables it deliberately does NOT read."""
    planted = '"""Never set ACCOUNTANT_DANGER=1."""\n# ACCOUNTANT_DANGER is refused\n'
    assert env_names_in(planted) == set()


def test_the_control_a_new_constant_the_document_never_lists_is_caught() -> None:
    planted = 'from typing import Final\n\nENV_NEW: Final = "ACCOUNTANT_NEW"\n'
    assert env_names_in(planted) == {"ACCOUNTANT_NEW"}
    documented = set(documented_variables(DEPLOY_DOC.read_text(encoding="utf-8")))
    assert "ACCOUNTANT_NEW" not in documented


def test_the_control_a_literal_read_straight_from_the_environment_is_caught() -> None:
    """The shape that skips the `ENV_` convention entirely. A scanner that knew
    only the constants would report nothing and pass."""
    planted = (
        "import os\n"
        'first = os.environ.get("ACCOUNTANT_SNEAKY", "")\n'
        'second = os.getenv("ACCOUNTANT_SNEAKIER")\n'
    )
    assert env_names_in(planted) == {"ACCOUNTANT_SNEAKY", "ACCOUNTANT_SNEAKIER"}


def test_the_control_an_ordinary_mapping_lookup_is_not_an_environment_read() -> None:
    """The other direction, and the one that would make this check unusable:
    every `.get("SOME_KEY")` in the package reported as a variable to
    document."""
    planted = 'row = payload.get("ACCOUNTANT_DB")\nvalue = mapping.get("TOTAL")\n'
    assert env_names_in(planted) == set()


def test_the_control_a_row_that_names_a_flag_without_its_default_is_caught() -> None:
    """The likely regression: the row survives an edit and the sentence that
    says which way it falls does not."""
    planted = (
        "| Variable | Set in the image? | If it is missing |\n"
        "|---|---|---|\n"
        f"| `{writedoor.ENV_POSTING}` | No | Set it to 0 to stop every write. |\n"
    )
    rows = documented_variables(planted)
    assert writedoor.ENV_POSTING in rows
    assert not states_the_default(rows[writedoor.ENV_POSTING])
