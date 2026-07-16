"""Credentials: env override, the 0600 fallback, and per-tool isolation.

These run with no OS keyring (the fallback path) — which is also the CI shape.
"""

from __future__ import annotations

import json
import stat

import pytest

from agentcli import AppSpec, Credentials
from agentcli import credentials as credmod

OP = AppSpec(name="op-cli", env_prefix="OPCLI")
DRONE = AppSpec(name="drone-cli", env_prefix="DRONECLI")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """No real keyring, no real config dir. Never touch the developer's secrets."""
    monkeypatch.setattr(credmod, "_keyring_available", lambda: False)
    monkeypatch.setenv("OPCLI_CONFIG_DIR", str(tmp_path / "op"))
    monkeypatch.setenv("DRONECLI_CONFIG_DIR", str(tmp_path / "drone"))
    monkeypatch.delenv("OPCLI_TOKEN", raising=False)
    monkeypatch.delenv("DRONECLI_TOKEN", raising=False)


def test_env_token_wins_over_everything(monkeypatch):
    c = Credentials(OP)
    c.store_token("default", "from-file")
    monkeypatch.setenv("OPCLI_TOKEN", "from-env")
    assert c.get_token("default") == "from-env"
    assert "environment variable $OPCLI_TOKEN" in c.backend_name()


def test_store_and_get_roundtrip():
    c = Credentials(OP)
    assert c.store_token("default", "tok-1") == "file"
    assert c.get_token("default") == "tok-1"


def test_missing_token_is_none():
    assert Credentials(OP).get_token("nope") is None


def test_backend_name_reports_the_fallback():
    """`auth status` must tell the user their token is in clear text."""
    name = Credentials(OP).backend_name()
    assert "plaintext fallback file" in name
    assert str(OP.credentials_file()) in name


def test_fallback_file_is_0600():
    """The token is clear text here — the mode is the only thing protecting it."""
    c = Credentials(OP)
    c.store_token("default", "secret")
    path = OP.credentials_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_fallback_file_is_0600_even_if_it_existed_world_readable():
    """A pre-existing 0644 file must be tightened, not trusted."""
    c = Credentials(OP)
    path = OP.credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    path.chmod(0o644)
    c.store_token("default", "secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_profiles_are_independent():
    c = Credentials(OP)
    c.store_token("prod", "tok-prod")
    c.store_token("staging", "tok-staging")
    assert c.get_token("prod") == "tok-prod"
    assert c.get_token("staging") == "tok-staging"


def test_delete_removes_only_that_profile():
    c = Credentials(OP)
    c.store_token("prod", "tok-prod")
    c.store_token("staging", "tok-staging")
    c.delete_token("prod")
    assert c.get_token("prod") is None
    assert c.get_token("staging") == "tok-staging"


def test_delete_is_idempotent():
    Credentials(OP).delete_token("never-existed")  # must not raise


def test_two_tools_do_not_share_tokens():
    """The isolation guarantee: op-cli must never read drone-cli's token."""
    op, drone = Credentials(OP), Credentials(DRONE)
    op.store_token("default", "op-token")
    drone.store_token("default", "drone-token")
    assert op.get_token("default") == "op-token"
    assert drone.get_token("default") == "drone-token"
    assert OP.credentials_file() != DRONE.credentials_file()


def test_env_tokens_do_not_bleed(monkeypatch):
    monkeypatch.setenv("OPCLI_TOKEN", "op-env")
    assert Credentials(OP).get_token("default") == "op-env"
    assert Credentials(DRONE).get_token("default") is None


def test_corrupt_fallback_file_is_not_fatal():
    """A truncated/garbage file must read as 'no token', not crash the CLI."""
    c = Credentials(OP)
    path = OP.credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert c.get_token("default") is None


def test_store_warns_when_falling_back_to_plaintext(capsys):
    Credentials(OP).store_token("default", "secret")
    err = capsys.readouterr().err
    assert "no OS keyring" in err and "clear text" in err, "the plaintext fallback must be loud"


def test_stored_file_is_json_keyed_by_profile():
    Credentials(OP).store_token("default", "tok")
    assert json.loads(OP.credentials_file().read_text()) == {"default": "tok"}
