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
FROM python:3.14.6-slim

# NOTHING IS INSTALLED, AND THAT IS THE MEASUREMENT, NOT A SHORTCUT.
# `dependencies = []` in pyproject.toml, kept that way deliberately. With no
# runtime dependency there is no resolver step, no build backend, no network
# during the build, and no third-party wheel in the image to audit. The package
# is copied and run.

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

# WHERE THE READING SERVICE IS - NAMED HERE, VALUED NOWHERE. D-23, 2026-08-11.
#
# Azure Document Intelligence reads an uploaded bill. The endpoint is not a
# credential, so it is safe to declare; it is also not a DEFAULT, so it is
# declared empty for the same reason ACCOUNTANT_TENANT is. Empty and absent are
# identical to the code - accountant/reader/azure.py reads both variables and
# returns None if either is missing - but only a declared variable shows up in
# `docker inspect`, and the operator working out what to pass to `docker run`
# should be told by the image rather than by a document they may not have open.
#
# Empty means the reading service is UNCONFIGURED, and unconfigured means an
# uploaded document is refused with a sentence naming both variables. It does
# not mean a fallback. There is no fallback: a reader that quietly degrades to
# guessing is the failure the whole extraction package exists to prevent, and a
# typed bill still works with no reading service at all.
ENV ACCOUNTANT_AZURE_ENDPOINT=""

# ACCOUNTANT_AZURE_KEY IS DELIBERATELY ABSENT, AND MUST STAY ABSENT.
# It is a credential. A credential in a layer is a credential in every registry,
# cache and backup this image ever touches. It is injected at run time, and
# tests/test_deploy_artefacts.py fails if this file ever sets it - the word
# `KEY` was added to that check's list on the day this variable was written,
# because until then only `APIKEY` was watched for and this name would have
# passed.

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
