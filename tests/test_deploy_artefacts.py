"""The deployment artefacts, read as data.

WHAT THIS FILE PROVES, AND WHAT IT DOES NOT
-------------------------------------------
No image is built, pulled, run or pushed here, so the evidence class is
`NOT MEASURED` and nothing in this file says a container starts. What it does
prove is the part that can be proved before any of that exists: that the
Dockerfile still has the properties it was written for, and that
`scripts/deploy` refuses to invent a registry.

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

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
DEPLOY = ROOT / "scripts" / "deploy"
PYPROJECT = ROOT / "pyproject.toml"
PYTHON_VERSION = ROOT / ".python-version"
APP = ROOT / "accountant" / "web" / "app.py"

#: The variable `scripts/deploy` reads, and the one it must never default.
REGISTRY = "ACCOUNTANT_REGISTRY"

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
