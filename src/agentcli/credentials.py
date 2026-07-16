"""Secure storage of API tokens, shared by every agent-tool CLI.

Order of preference for reading a token:

1. The ``<PREFIX>_TOKEN`` environment variable (used by CI / test suites and
   handy for one-off scripting). **This is what makes a tool non-interactive** —
   with it set, nothing touches the keyring and nothing can prompt.
2. The operating-system keyring (Secret Service / macOS Keychain / Windows
   Credential Locker) via the :mod:`keyring` library — the safe default.
3. A ``0600`` fallback file in the config directory, used only when no real
   keyring backend is available (headless boxes without a Secret Service).
   We warn loudly in that case because the token is stored in clear text.

The token is the only secret we persist. Everything else (base URL, options)
lives in the plain-text config file.

Parameterized by :class:`~agentcli.appspec.AppSpec` — the keyring service, the
env var and the fallback path all derive from it, so two tools installed side by
side never read each other's tokens.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from .appspec import AppSpec


def _keyring_available() -> bool:
    try:
        import keyring
        from keyring.backends import fail

        # keyring always "works" — it just installs a null backend that raises on
        # use. Only an isinstance check tells you whether it will actually store.
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


class Credentials:
    """Token storage for one tool, identified by its :class:`AppSpec`."""

    def __init__(self, spec: AppSpec) -> None:
        self.spec = spec

    # ---- internals ---------------------------------------------------

    @property
    def env_token(self) -> str:
        """The canonical token env var. See :meth:`_env_token_hit` for aliases."""
        return self.spec.env("TOKEN")

    def _env_token_hit(self) -> tuple[str, str] | None:
        """The first token env var that is set, as ``(name, value)``.

        Checks ``<PREFIX>_TOKEN`` first, then any ecosystem aliases in order.
        Returns the NAME as well as the value, because an operator who logged in
        via the keyring and is unknowingly being overridden by an exported
        variable needs to be told exactly which one — "it works on my machine"
        lives here.
        """
        for name in self.spec.token_env_names():
            val = os.environ.get(name)
            if val:
                return name, val
        return None

    def _fallback_file(self) -> Path:
        return self.spec.credentials_file()

    def _read_fallback(self) -> dict[str, str]:
        path = self._fallback_file()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _write_fallback(self, data: dict[str, str]) -> None:
        path = self._fallback_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600 from the start so the plaintext token is never briefly
        # world-readable (mode 0o600 has no group/other bits, so umask can't widen it).
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # also fixes a pre-existing file
        except OSError:
            pass

    # ---- api ---------------------------------------------------------

    def backend_name(self) -> str:
        """Human-readable name of the active secret backend (for `auth status`)."""
        hit = self._env_token_hit()
        if hit:
            return f"environment variable ${hit[0]}"
        if _keyring_available():
            try:
                import keyring

                return f"OS keyring ({keyring.get_keyring().__class__.__name__})"
            except Exception:
                pass
        return f"plaintext fallback file ({self._fallback_file()})"

    def store_token(self, profile: str, token: str) -> str:
        """Persist *token* for *profile*. Returns the backend used."""
        if _keyring_available():
            try:
                import keyring

                keyring.set_password(self.spec.keyring_service, profile, token)
                return "keyring"
            except Exception as exc:  # pragma: no cover - depends on host
                print(f"warning: keyring store failed ({exc}); using fallback file", file=sys.stderr)
        data = self._read_fallback()
        data[profile] = token
        self._write_fallback(data)
        print(
            f"warning: no OS keyring available — token stored in clear text at "
            f"{self._fallback_file()} (0600)",
            file=sys.stderr,
        )
        return "file"

    def get_token(self, profile: str) -> str | None:
        """Resolve the token for *profile*, honouring the env override first.

        Precedence is **env > keyring > file**, deliberately: it is what lets a
        tool run non-interactively in CI without touching a keyring that isn't
        there. Do not invert it — but do surface it (`backend_name`), because an
        exported variable silently beating a keyring login is confusing exactly
        when you can least afford it.
        """
        hit = self._env_token_hit()
        if hit:
            return hit[1]
        if _keyring_available():
            try:
                import keyring

                tok = keyring.get_password(self.spec.keyring_service, profile)
                if tok:
                    return tok
            except Exception:
                pass
        return self._read_fallback().get(profile)

    def delete_token(self, profile: str) -> None:
        """Remove any stored token for *profile* from every backend."""
        if _keyring_available():
            try:
                import keyring

                keyring.delete_password(self.spec.keyring_service, profile)
            except Exception:
                pass
        data = self._read_fallback()
        if profile in data:
            del data[profile]
            self._write_fallback(data)
