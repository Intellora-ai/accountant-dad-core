"""The local connector. Runs on the user's Windows machine, beside TallyPrime.

WHY THIS PACKAGE EXISTS
-----------------------
TallyPrime listens on `localhost:9000` on the machine it runs on, with no
authentication beyond network reachability. A cloud server cannot open a socket
to a port on somebody's laptop, and nobody should want it to: that would mean
asking every customer to expose an unauthenticated accounting API to the
internet.

So the direction is inverted. The connector runs where Tally runs, **dials out**
to the cloud, and asks for work. Port 9000 is never listened on, never
forwarded, and never reachable from outside the machine. The only inbound
connection anywhere in the system is the browser reaching the cloud.

    cloud  ◄──── outbound HTTPS ────  connector ──── loopback HTTP ────►  Tally
           (the connector opens it)                  (never leaves the box)

WHAT IT REFUSES
---------------
Three refusals, and each is checked before the job reaches Tally rather than
after:

    a job for a different tenant     REFUSED_WRONG_TENANT
    a job for a company this
      connector was not paired to    REFUSED_WRONG_COMPANY
    an operation id already executed  ALREADY_EXECUTED

The third one is C5 wearing a different hat. A dropped reply is the normal case
in a polling protocol — the cloud will re-offer a job it never got an answer
for — so "I already did this" has to be a first-class answer rather than an
accident avoided by luck.

WHAT IT DOES NOT DO
-------------------
It does not decide anything. It does not validate an entry, choose an account,
ask a question or judge whether a voucher should exist. Those live in the
cloud, where the memory index and the rules corpus are. The connector is a
door, and a door that formed opinions would be a second place where accounting
decisions are made.

It also never speaks XML. It holds a `TallyClient` and calls methods on it, so
correction C3 is unchanged: `accountant/tallyio/` remains the only package that
knows Tally's wire format exists.
"""

from __future__ import annotations

from accountant.agent.connector import (
    ALREADY_EXECUTED,
    EXECUTED,
    FAILED,
    REFUSED_UNKNOWN_KIND,
    REFUSED_WRONG_COMPANY,
    REFUSED_WRONG_TENANT,
    TALLY_UNAVAILABLE,
    CloudCall,
    Connector,
    ConnectorIdentity,
    ExecutedOperations,
    Job,
    JobResult,
    https_cloud_call,
)

__all__ = [
    "ALREADY_EXECUTED",
    "EXECUTED",
    "FAILED",
    "REFUSED_UNKNOWN_KIND",
    "REFUSED_WRONG_COMPANY",
    "REFUSED_WRONG_TENANT",
    "TALLY_UNAVAILABLE",
    "CloudCall",
    "Connector",
    "ConnectorIdentity",
    "ExecutedOperations",
    "Job",
    "JobResult",
    "https_cloud_call",
]
