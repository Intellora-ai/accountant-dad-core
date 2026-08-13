# The production image for the web app.
#
# WHAT THIS IS, ON 2026-08-11
# ---------------------------
# There is no cloud account, no host and no domain (docs/OWNER_WORK.md,
# "Deployment target"). This file exists so the image is BUILDABLE and
# ARGUABLE before any of that is bought, and so the decisions in it are made
# once, here, rather than in a hurry on the day somebody has a server. It
# deploys nowhere. docs/DEPLOY.md lists what the owner must supply and, more
# importantly, what this image cannot do yet.
#
# WHY THIS BASE, AND WHY THE PATCH IS PINNED
# ------------------------------------------
# pyproject.toml declares `requires-python = ">=3.12"`. That is the FLOOR - the
# oldest interpreter the source claims to support - and it is not the one
# anything here was measured on. `.python-version` says 3.14, uv resolves 3.14
# in CI, and the virtualenv that runs the suite is 3.14.6. An image built on
# the floor would ship an interpreter no gate has ever executed, and a green
# test suite would say nothing about it.
#
# So this pins the patch of the interpreter the tests actually ran on.
# tests/test_deploy_artefacts.py reads pyproject.toml, .python-version and this
# line together and fails the day they drift apart.
#
# The tag is not a digest. A digest is stronger and it is the next step, but a
# digest has to be READ from a registry, and inventing one in a file whose
# whole job is to be exact would be a fabricated fact. docs/DEPLOY.md names the
# single command that produces the real one.
#
# TWO STAGES, AND ONLY THE SECOND ONE SHIPS
# -----------------------------------------
# The first stage exists to turn uv.lock into installed wheels. uv itself,
# pyproject.toml and the lockfile stay behind in it; what crosses into the
# image is the virtualenv and nothing else. Both stages are the SAME pinned
# base, so the interpreter the wheels were installed against and the one that
# runs them are the same interpreter.
FROM python:3.14.6-slim AS dependencies

# WHY ANYTHING IS INSTALLED AT ALL. 2026-08-13.
# This block used to read "NOTHING IS INSTALLED, AND THAT IS THE MEASUREMENT",
# and it was true: `dependencies = []` in pyproject.toml meant no resolver, no
# build backend and no third-party wheel to audit. D-30 ended that on
# 2026-08-13 - pypdf, pytesseract and Pillow. accountant/extract/textlayer.py
# imports pypdf at module level and freeocr.py imports pytesseract and PIL, so
# from that commit an image installing nothing carried code that could not
# import. Nothing here was edited to break it. That is exactly why the property
# now lives in tests/test_deploy_artefacts.py, which reads these instructions,
# rather than in a comment that went on sounding correct while being false.

# uv, pinned, and present in THIS STAGE ONLY. 0.12.2 is the version measured on
# the machine these files were written on. It is not resolved from uv.lock and
# cannot be: uv is the tool that reads the lock, so it is not in it.
RUN pip install --no-cache-dir uv==0.12.2

# The interpreter is this image's, never one uv fetched. Without this, uv is
# free to download its own Python when it dislikes the system one, and the
# virtualenv copied out below would point at a binary that does not exist in
# the final stage - an ImportError at run time, from a decision taken at build
# time, on the exact question this file's header spends fifteen lines settling.
ENV UV_PYTHON_DOWNLOADS=never

# Only the two files the resolver reads. The source is deliberately not here:
# `--no-install-project` below means the package itself is NOT installed. It is
# copied and run, exactly as before, and PYTHONPATH is what finds it.
COPY pyproject.toml uv.lock /app/
WORKDIR /app

# `--locked` IS THE PIN. uv installs the exact versions uv.lock names, and
# refuses outright when uv.lock and pyproject.toml have drifted apart, so this
# image cannot contain a version no test ever ran against. CI runs
# `uv sync --extra dev --locked` against the same lockfile; the difference is
# the extra, because a container needs the three runtime wheels and not pytest,
# ruff, pyright and eleven other tools.
#
# `--no-dev` IS REDUNDANT TODAY AND STAYS ANYWAY. `dev` is an extra here
# (`provides-extras = ["dev"]` in uv.lock), so uv leaves it out unless asked
# for it by name. The day somebody moves it to a dependency group, uv's default
# flips to INSTALLING it, and this flag is the only line that would stop a test
# runner and a linter shipping into production.
RUN uv sync --locked --no-dev --no-install-project


# --------------------------------------------------------------------------
# The image that ships.
# --------------------------------------------------------------------------
FROM python:3.14.6-slim

# Unbuffered on purpose. serve() prints the resolved configuration and, when
# Tally cannot be reached, the refusal - and then exits. Python block-buffers
# stdout when it is not a terminal, so a container that exits quickly would
# lose precisely the message that explains why it exited.
ENV PYTHONUNBUFFERED=1
# No .pyc files written back into the image or, worse, into the mounted volume
# if a runtime ever starts the process from somewhere else.
ENV PYTHONDONTWRITEBYTECODE=1
# So the interpreter finds the package regardless of the working directory the
# runtime chooses. `python -m accountant.web` otherwise depends on cwd being
# /app, and cwd is something an orchestrator can change without telling anyone.
ENV PYTHONPATH=/app

# Where the audit trail goes, stated rather than defaulted. app.py falls back to
# `data/app.db` RELATIVE TO THE WORKING DIRECTORY, which is right on a laptop
# and wrong here: a relative path lands wherever the process happened to start,
# and a database on a container's disposable filesystem is the exact defect
# fixed in commit 69191e2 ("The audit trail was written to a database that died
# with the process"). This path and the VOLUME below must be the same path -
# tests/test_deploy_artefacts.py asserts that they are, because two places to
# write the mount point is one place to get it wrong.
ENV ACCOUNTANT_DB=/app/data/app.db

# WHOSE BOOKS THIS PROCESS SERVES - NAMED HERE, VALUED NOWHERE. 2026-08-11.
#
# A tenant id is a customer's identity. Baking a real one in would put that
# customer's name in every registry, cache and backup this image ever touches,
# and it would quietly make this ONE IMAGE PER CUSTOMER: a separate build to
# tag, push and audit every time somebody signs up. The image is the same for
# everybody. WHO it serves is a run-time fact and is injected at run time.
#
# So the variable is declared EMPTY rather than left out, and the two are not
# the same thing to a reader. They are identical to the CODE - `served_tenant()`
# reads `os.environ.get(ENV_TENANT, "").strip()`, so empty and absent both
# refuse every request 403 - but only a declared variable shows up in
# `docker inspect` and `docker history`. The operator working out what to pass
# to `docker run` is told by the image itself instead of by a document they may
# not have open. tests/test_deploy_artefacts.py asserts it stays empty, because
# the day it holds a real id it has stopped being a hint and become a leak.
#
# NOTHING THAT LOOKS LIKE AN ID GOES HERE, not even as an example. A value that
# reads as real is a value somebody deploys.
ENV ACCOUNTANT_TENANT=""

# LOCAL_DEV_MODE IS DELIBERATELY ABSENT, AND MUST STAY ABSENT.
# Unset, authentication is required. Set to 1 it means every request runs as
# tenant "local-dev" and anybody who can reach the port can read and write
# somebody's books (accountant/auth/identity.py:201). A default that fails
# closed only stays closed while nobody writes the other value down, so nothing
# in this image writes it down. The test that keeps it out reads this file's
# INSTRUCTIONS, not this comment - a comment cannot set an environment variable
# and must not be able to fail a check about one.
#
# No credential of any kind is set here either. ACCOUNTANT_CONNECTOR_SECRET
# belongs to the connector, which runs on the customer's Windows machine and
# not in this image (docs/CONNECTOR.md). Anything else a deployment needs is
# injected at run time, because a secret in an image layer is a secret in every
# registry, cache and backup that image ever touches.

# A system account with a fixed uid, created before anything is copied so the
# copy can be owned by it in one layer rather than chowned in a second one.
RUN groupadd --system --gid 10001 accountant \
 && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home accountant

WORKDIR /app

# The wheels, and nothing else from the stage that built them - no uv, no
# lockfile, no pyproject.toml. Copied to the SAME ABSOLUTE PATH they were
# installed at, because a virtualenv records its own location and one moved to
# a different directory stops finding the interpreter it was built against.
COPY --from=dependencies --chown=10001:10001 /app/.venv /app/.venv

# So that `python` means the interpreter which can see those wheels. The CMD
# and the HEALTHCHECK below both say `python` and both resolve it through PATH,
# so this single line is what lets `python -m accountant.web` import pypdf.
ENV PATH="/app/.venv/bin:$PATH"

# TESSERACT IS NOT INSTALLED, AND THAT IS A DECISION. 2026-08-13.
#
# `pytesseract` is a wrapper, not an engine: the wheel is here, the `tesseract`
# BINARY it shells out to is not. Four reasons, in the order that decided it.
#
# 1. It would have no caller. Nothing in this repository builds
#    `freeocr.FreeReader` - `registry.build("free_ocr")` raises, and
#    `registry._NEEDS_WIRING` says why: it needs a page reader nobody has
#    written, because one cannot be checked without a corpus of bills whose
#    answers are known (H-02). Installing an engine for a rung that is not
#    wired is shipping a dependency to satisfy a comment.
# 2. apt cannot be pinned to anything this repository measured. `uv sync
#    --locked` above installs the exact versions the 4233 tests ran against;
#    `apt-get install tesseract-ocr` installs whatever Debian's index serves on
#    the morning of the build. One unpinned binary would undo the property the
#    stage above exists to give.
# 3. Without it the app STARTS and refuses in words, and that is designed
#    behaviour rather than luck: `pytesseract.TesseractNotFoundError` is mapped
#    to `freeocr.ENGINE_MISSING` in `_REFUSAL_FOR`. Installing the binary to
#    "be safe" would quietly convert a designed refusal into a hard system
#    requirement that no test covers.
# 4. What it costs is nothing that works today. A photographed bill cannot be
#    read in this image - and cannot be read outside it either, for reason 1. A
#    born-digital PDF goes through pypdf, which IS installed, and typed entry
#    never touched an engine.
#
# CI IS THE OTHER WAY ROUND, AND THE ASYMMETRY IS THE POINT.
# docs/CI_OCR_INSTALL.md asks for `apt-get install -y tesseract-ocr` in the two
# jobs that run the whole suite, because a test suite without the binary SKIPS
# the OCR tests and a skipped test measures nothing. A container without it
# skips nothing: it runs a rung that has no caller. Same binary, two different
# questions, two different answers.
#
# THE DAY THE PAGE READER IS WIRED, this changes: install it here, pin the
# version, and add it to docs/DEPLOY.md. tests/test_deploy_artefacts.py fails
# until all three happen, so the decision cannot be reversed quietly.

# Only the package. Not the tests, not ci/, not docs/, not scripts/ - none of
# them run in production, and every file that ships is a file somebody has to
# reason about when the image is audited. .dockerignore keeps them out of the
# build context as well, so they are not even sent to the daemon.
COPY --chown=10001:10001 accountant/ /app/accountant/

# Created and owned BEFORE the VOLUME line, and that order is load-bearing.
# Docker seeds a new named volume from the image's content and ownership at the
# mount point; declare the volume first and the seed is a root-owned directory
# that the non-root process cannot write, which shows up as a database error on
# first run rather than as a permissions error at build time. A BIND mount takes
# the host's ownership instead and no line in this file can change that -
# docs/DEPLOY.md says what the owner has to chown.
RUN mkdir -p /app/data && chown 10001:10001 /app/data
VOLUME ["/app/data"]

# Numeric, not the name. A runtime that enforces "must not run as root"
# compares uids, and a name it cannot resolve is a check it cannot make.
USER 10001:10001

# NO EXPOSE, AND THAT IS A STATEMENT ABOUT THE APP RATHER THAN AN OMISSION.
# accountant/web/app.py `serve()` binds 127.0.0.1, so the server answers only
# processes inside this container. An EXPOSE line would document a port that
# answers nobody outside it, which is worse than no line at all: it reads as a
# promise. The day serve() takes its bind address from the environment, this
# port becomes real and EXPOSE has to be added - tests/test_deploy_artefacts.py
# reads serve()'s default host straight out of the source and fails when the
# two stop agreeing, so nobody has to remember.

# /health is the app's own readiness gate and needs no session: do_GET answers
# it before _identify runs, and _confirm_company is deliberately exempt, because
# a readiness endpoint that needs Tally to answer cannot report that Tally is
# not answering. So this measures the thing, not the login page.
#
# It returns 503 until the runtime is connected and bootstrapped. urlopen raises
# on 503, the exit status is non-zero, and the container reports unhealthy.
# "Not ready" and "unhealthy" are the same statement here, which is the point -
# readiness means safe to receive work.
#
# 127.0.0.1 rather than localhost: localhost can resolve to ::1 first, where
# nothing is listening, and the check would then fail for a reason that has
# nothing whatever to do with the app.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"]

# The same command README.md gives a person, and the reason
# accountant/web/__main__.py exists. One startup path and not two: a second,
# subtly different way to start the process is exactly how "it works on mine"
# happens. It exits non-zero when TallyPrime cannot be reached, which today is
# every time this image is run in a cloud - see docs/DEPLOY.md, "What this
# image cannot do yet".
CMD ["python", "-m", "accountant.web"]
