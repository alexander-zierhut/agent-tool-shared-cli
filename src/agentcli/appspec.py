"""`AppSpec` — the two strings that make a shared chassis tool-specific.

Every tool in the `agent-tool-<x>-cli` family needs the same four things from
its identity:

* a **config directory**       ``~/.config/<name>/``
* a **keyring service name**   ``<name>``
* an **env-var namespace**     ``<PREFIX>_TOKEN``, ``<PREFIX>_CONFIG_DIR``, …
* a **relocatable config dir**, so tests are hermetic

So that is all `AppSpec` carries. It is deliberately not a plugin system, a
registry or a settings framework — two strings and a few pure functions.

Why this exists at all: in `opcli` the config-dir logic was **duplicated** in
both ``config.py`` and ``credentials.py``. Two copies of "where do I live?" that
nothing forced to agree — relocate one and the token and the profile end up in
different directories. Here there is exactly one.

    SPEC = AppSpec(name="op-cli", env_prefix="OPCLI")
    SPEC.config_dir()          # -> ~/.config/op-cli   (or $OPCLI_CONFIG_DIR)
    SPEC.env("TOKEN")          # -> "OPCLI_TOKEN"
    SPEC.getenv("BASE_URL")    # -> os.environ.get("OPCLI_BASE_URL")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSpec:
    """Identity of one CLI. Frozen: this is configuration, not state."""

    name: str
    """Directory + keyring service name, e.g. ``op-cli``, ``drone-cli``."""

    env_prefix: str
    """Env-var namespace WITHOUT the trailing underscore, e.g. ``OPCLI``."""

    token_env_aliases: tuple[str, ...] = ()
    """Extra token env vars to honour, in order, AFTER ``<PREFIX>_TOKEN``.

    For wrapping a product that already has an established variable its users
    export — Drone's ``DRONE_TOKEN``, Jira's ``JIRA_API_TOKEN``, GitLab's
    ``GITLAB_TOKEN``. Adopting the ecosystem's name is worth more than prefix
    purity: people (and their CI) already have it set.

    Ours wins when both are present — the more specific name is the more
    deliberate one. Note the hazard this creates and surface it in `auth status`:
    an exported alias **silently overrides a keyring login**, and for Drone the
    ``DRONE_*`` namespace is also what the runner injects into every build step.
    """

    repo: str = ""
    """GitHub ``owner/name`` slug for THIS tool, e.g. ``alexander-zierhut/agent-tool-drone-cli``.

    Carried here because it is part of the **contract**, not the transport: once
    installed there is no README or ``AGENTS.md`` beside the binary, so the tool
    itself must be able to say where a problem gets reported (``<cmd> report``).
    Empty means "not published yet" — the report command still runs, it just has
    no link to hand out.
    """

    def __post_init__(self) -> None:
        # These two are the whole contract; a typo here silently relocates a
        # user's config or splits their token from their profile.
        if not self.name or "/" in self.name:
            raise ValueError(f"AppSpec.name must be a bare directory name, got {self.name!r}")
        if not self.env_prefix or not self.env_prefix.isupper():
            raise ValueError(
                f"AppSpec.env_prefix must be UPPERCASE and non-empty, got {self.env_prefix!r}"
            )
        if self.env_prefix.endswith("_"):
            raise ValueError(
                f"AppSpec.env_prefix must not end with '_' (it is added for you), got {self.env_prefix!r}"
            )
        # A typo here sends bug reports into the void; catch the obvious shape error.
        if self.repo and self.repo.count("/") != 1:
            raise ValueError(
                f"AppSpec.repo must be a bare 'owner/name' slug, got {self.repo!r}"
            )

    # ---- env ---------------------------------------------------------

    def env(self, suffix: str) -> str:
        """The full env-var name for *suffix*: ``env("TOKEN") -> "OPCLI_TOKEN"``."""
        return f"{self.env_prefix}_{suffix}"

    def token_env_names(self) -> tuple[str, ...]:
        """Every token env var this tool honours, in precedence order."""
        return (self.env("TOKEN"), *self.token_env_aliases)

    def getenv(self, suffix: str, default: str | None = None) -> str | None:
        """Read ``<PREFIX>_<SUFFIX>`` from the environment."""
        return os.environ.get(self.env(suffix), default)

    # ---- paths -------------------------------------------------------

    def config_dir(self) -> Path:
        """Where this tool's config lives.

        A **function, not a module constant** — and that single property is what
        makes the test suites hermetic. As a constant it would freeze at import
        time, before a test could point ``<PREFIX>_CONFIG_DIR`` at a tmpdir, and
        every test run would read and write the developer's real config.

        Precedence: ``<PREFIX>_CONFIG_DIR`` > ``XDG_CONFIG_HOME``/<name> > ``~/.config/<name>``.
        """
        base = self.getenv("CONFIG_DIR")
        if base:
            return Path(base)
        xdg = os.environ.get("XDG_CONFIG_HOME")
        root = Path(xdg) if xdg else Path.home() / ".config"
        return root / self.name

    def config_file(self) -> Path:
        return self.config_dir() / "config.json"

    def credentials_file(self) -> Path:
        """The 0600 fallback used only when no OS keyring is available."""
        return self.config_dir() / "credentials.json"

    # ---- keyring -----------------------------------------------------

    @property
    def keyring_service(self) -> str:
        return self.name

    # ---- issue reporting (the contract, carried in the binary) -------

    def repo_url(self) -> str:
        """``https://github.com/<owner>/<name>`` — empty string if no repo is set."""
        return f"https://github.com/{self.repo}" if self.repo else ""

    def issues_url(self) -> str:
        """The issue tracker for this tool."""
        return f"{self.repo_url()}/issues" if self.repo else ""

    def new_issue_url(self, *, title: str | None = None, body: str | None = None) -> str:
        """A GitHub ``issues/new`` link, optionally pre-filling title and body.

        No token or account is needed to *open* the form (GitHub asks the human
        to sign in only at submit), so this is the token-free path an installed
        binary can always offer.
        """
        if not self.repo:
            return ""
        from urllib.parse import urlencode

        base = f"{self.repo_url()}/issues/new"
        query = {k: v for k, v in (("title", title), ("body", body)) if v}
        return f"{base}?{urlencode(query)}" if query else base
