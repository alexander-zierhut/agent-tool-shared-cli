"""agentcli — the shared chassis for `agent-tool-<x>-cli` tools.

This package is deliberately small. It holds **the agent contract** and the pure
utilities that implement it — the things that must be *identical* across every
tool we ship, because an agent learns the contract once and applies it everywhere:

* :mod:`agentcli.errors`      — the exit-code taxonomy (0/1/3/4/5/6/7/130) + ``DryRun``
* :mod:`agentcli.output`      — json/table/markdown/csv, ``--fields`` projection, NDJSON
* :mod:`agentcli.credentials` — keyring with a 0600 fallback and an env override
* :mod:`agentcli.appspec`     — the two strings that make the above tool-specific

**What is deliberately NOT here**, and why it matters more than what is:

``client.py``, ``serialize.py``, ``resolve.py`` and the domain command modules
stay in each tool. They *look* shareable and are not — OpenProject's pagination
stops on an authoritative ``total`` ("never stop on a short page"), while Drone
has no total and a short page IS the terminator. The rule that inverts between
tool #1 and tool #2 is exactly the rule you must not hoist. Auth schemes, retry
matrices and error-body shapes are the same story.

    Share the contract, not the transport.

Pulling those in would turn this into a framework with a config object per tool,
which is how shared-code projects die.
"""

from .appspec import AppSpec
from .credentials import Credentials
from .errors import (
    ApiError,
    AuthError,
    ConfigError,
    ConflictError,
    DryRun,
    NotFoundError,
    OpError,
    ValidationError,
)
from .output import Emitter, OutputFormat, print_error  # NDJSON is Emitter.stream_json()

__version__ = "0.1.1"

__all__ = [
    "AppSpec",
    "Credentials",
    "Emitter",
    "OutputFormat",
    "print_error",
    "OpError",
    "ApiError",
    "AuthError",
    "ConfigError",
    "ConflictError",
    "NotFoundError",
    "ValidationError",
    "DryRun",
    "__version__",
]
