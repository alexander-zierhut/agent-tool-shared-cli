# agent-tool-shared-cli

> The shared chassis for the **`agent-tool-<x>-cli`** family — agent-ready
> command-line tools that an LLM can drive with no prior knowledge of them.

[![PyPI](https://img.shields.io/pypi/v/agent-tool-shared-cli)](https://pypi.org/project/agent-tool-shared-cli/)
[![CI](https://github.com/alexander-zierhut/agent-tool-shared-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/alexander-zierhut/agent-tool-shared-cli/actions/workflows/ci.yml)
![Python](https://img.shields.io/pypi/pyversions/agent-tool-shared-cli)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

```bash
pip install agent-tool-shared-cli
```

**The tools built on it** — each learns the same contract, so an agent that knows
one knows them all:

| Tool | Install | For |
| --- | --- | --- |
| [**drone-cli**](https://github.com/alexander-zierhut/agent-tool-drone-cli) | `pipx install agent-tool-drone-cli` | Drone CI — builds, failing-step logs, promotions |
| [**grafana-cli**](https://github.com/alexander-zierhut/agent-tool-grafana-cli) | `pipx install agent-tool-grafana-cli` | Grafana — log discovery, health scan, alert routing |
| [**openproject**](https://github.com/alexander-zierhut/agent-tool-openproject-cli) | `pipx install agent-tool-openproject-cli` | OpenProject — work packages, time, invoicing |
| [**lexware-office**](https://github.com/alexander-zierhut/agent-tool-lexware-office-cli) | `pipx install agent-tool-lexware-office-cli` | Lexware Office — invoices, contacts, AR-aging |

This repo also holds the family's engineering docs — read these first if you are
standing up a new tool:

- **[BLUEPRINT.md](BLUEPRINT.md)** — the engineering standard the whole family is built to.
- **[BUILDING-THE-NEXT-CLI.md](BUILDING-THE-NEXT-CLI.md)** — the distilled playbook, and *why similarity is the product*.
- **[SIMILARITY_CHECKLIST.md](SIMILARITY_CHECKLIST.md)** — tick every box before a new CLI is "done", so it lands already looking like its siblings.

**Keywords:** agent CLI chassis, LLM tool contract, exit-code taxonomy, JSON output
CLI, keyring credentials, Python CLI library, AI agent tooling, Claude.

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

MIT — see [LICENSE](LICENSE).
