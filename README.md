# agent-tool-shared-cli

The shared chassis for the **`agent-tool-<x>-cli`** family — agent-ready command-line
tools that an LLM can drive with no prior knowledge of them.

Consumers: [`agent-tool-openproject-cli`](https://github.com/alexander-zierhut/agent-tool-openproject-cli),
`agent-tool-drone-cli` (in progress).

This repo also holds **[BLUEPRINT.md](BLUEPRINT.md)** — the engineering standard the
whole family is built to. Read that first if you are standing up a new tool.

```bash
pip install agent-tool-shared-cli
```

## What's in it

This package is deliberately small. It holds **the agent contract** and the pure
utilities that implement it — the things that must be *identical* across every tool,
because an agent learns the contract once and applies it everywhere.

| Module | What |
|---|---|
| `agentcli.errors` | The exit-code taxonomy (`0/1/3/4/5/6/7/130`) + `DryRun` |
| `agentcli.output` | `Emitter`: json/table/markdown/csv, `--fields` projection, NDJSON streaming |
| `agentcli.credentials` | Keyring storage with a `0600` fallback and an env override |
| `agentcli.appspec` | `AppSpec` — the two strings that make all of the above tool-specific |

```python
from agentcli import AppSpec, Credentials, Emitter, OutputFormat, NotFoundError

SPEC = AppSpec(name="drone-cli", env_prefix="DRONECLI")

SPEC.config_dir()            # ~/.config/drone-cli  (or $DRONECLI_CONFIG_DIR)
SPEC.env("TOKEN")            # "DRONECLI_TOKEN"

Credentials(SPEC).get_token("default")     # env > keyring > 0600 file
Emitter(OutputFormat.json, fields=["id", "status"]).emit(rows)
raise NotFoundError("no such build")       # -> exit 5, JSON on stderr
```

## The exit-code contract

Published API. You may leave a code unallocated, or repurpose one deliberately.
**You may never renumber one** — agents branch on these, and they are documented in
three places per tool (README, the in-binary `guide`, and the Claude skill).

| Code | Meaning |
|---|---|
| 0 | success (including a successful `--dry-run`) |
| 1 | generic error |
| 2 | *reserved for Click/Typer usage errors — never allocate* |
| 3 | config error |
| 4 | auth error (401/403) |
| 5 | not found (404) |
| 6 | conflict (409 / optimistic locking) |
| 7 | validation error (422) — see `fieldErrors` |
| 8+ | per-tool, and only for a condition you have **observed** |
| 130 | SIGINT |

## What is deliberately NOT here

`client.py`, `serialize.py`, `resolve.py` and the domain commands stay in each tool.
They *look* shareable and are not:

> OpenProject stops paginating on an authoritative `total` — "never stop on a short
> page". Drone has no total, and a short page **is** the terminator. The rule that
> inverts between tool #1 and tool #2 is exactly the rule you must not hoist.

Auth schemes, retry matrices and error-body shapes are the same story. Pulling them in
would make this a framework with a config object per tool, which is how shared-code
projects die.

**Share the contract, not the transport.**

## Contributing

You need nothing but Python:

```bash
pip install -e '.[test]'
pytest                       # 74 tests, no network, no services
```

Changes here affect every downstream CLI. Two rules:

1. **Never renumber an exit code, and never change an output shape** without treating
   it as a breaking change — downstream agents depend on both.
2. **Don't add a module because two tools happen to share it today.** Wait until a
   third does, and until you can state the rule it obeys. `tests/test_contract.py`
   is the tripwire: if a change makes you edit it, stop and think.

## License

MIT
