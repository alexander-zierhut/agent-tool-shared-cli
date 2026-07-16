"""AppSpec: the two strings that make the shared chassis tool-specific."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcli import AppSpec

OP = AppSpec(name="op-cli", env_prefix="OPCLI")
DRONE = AppSpec(name="drone-cli", env_prefix="DRONECLI")


def test_env_names():
    assert OP.env("TOKEN") == "OPCLI_TOKEN"
    assert OP.env("BASE_URL") == "OPCLI_BASE_URL"
    assert DRONE.env("TOKEN") == "DRONECLI_TOKEN"


def test_getenv_reads_the_namespaced_var(monkeypatch):
    monkeypatch.setenv("OPCLI_BASE_URL", "https://op.example.com")
    assert OP.getenv("BASE_URL") == "https://op.example.com"
    assert OP.getenv("NOPE") is None
    assert OP.getenv("NOPE", "fallback") == "fallback"
    # A different tool must not see it.
    assert DRONE.getenv("BASE_URL") is None


def test_config_dir_is_relocatable(monkeypatch, tmp_path):
    monkeypatch.setenv("OPCLI_CONFIG_DIR", str(tmp_path))
    assert OP.config_dir() == tmp_path
    assert OP.config_file() == tmp_path / "config.json"
    assert OP.credentials_file() == tmp_path / "credentials.json"


def test_config_dir_is_a_function_not_a_constant(monkeypatch, tmp_path):
    """The property the whole hermetic test strategy rests on.

    As an import-time constant this would freeze before a test could point the
    env var at a tmpdir — and every test run would read the developer's real
    config. Changing the env must change the answer immediately.
    """
    monkeypatch.setenv("OPCLI_CONFIG_DIR", str(tmp_path / "a"))
    first = OP.config_dir()
    monkeypatch.setenv("OPCLI_CONFIG_DIR", str(tmp_path / "b"))
    assert OP.config_dir() != first, "config_dir() must re-read the env on every call"


def test_config_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("OPCLI_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert OP.config_dir() == tmp_path / "op-cli"
    # The tool-specific var wins over XDG.
    monkeypatch.setenv("OPCLI_CONFIG_DIR", str(tmp_path / "explicit"))
    assert OP.config_dir() == tmp_path / "explicit"


def test_config_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("OPCLI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert OP.config_dir() == Path.home() / ".config" / "op-cli"


def test_two_tools_never_collide(monkeypatch, tmp_path):
    """Installed side by side, two tools must not read each other's secrets."""
    monkeypatch.delenv("OPCLI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DRONECLI_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert OP.config_dir() != DRONE.config_dir()
    assert OP.keyring_service != DRONE.keyring_service
    assert OP.env("TOKEN") != DRONE.env("TOKEN")


@pytest.mark.parametrize(
    "name,prefix",
    [
        ("", "OPCLI"),           # empty name
        ("op/cli", "OPCLI"),     # a path, not a directory name
        ("op-cli", ""),          # empty prefix
        ("op-cli", "opcli"),     # lowercase prefix -> would look for opcli_TOKEN
        ("op-cli", "OPCLI_"),    # trailing underscore -> OPCLI__TOKEN
    ],
)
def test_rejects_bad_specs(name, prefix):
    """Fail at construction, not at the call site.

    A typo here silently relocates a user's config or splits their token from
    their profile — the kind of bug that surfaces as "why am I logged out?".
    """
    with pytest.raises(ValueError):
        AppSpec(name=name, env_prefix=prefix)


def test_is_frozen():
    with pytest.raises(Exception):
        OP.name = "other"  # type: ignore[misc]
