# Working agreement for `agent-tool-shared-cli`

The machine-and-human contract for changing this package. This is the **chassis**
every `agent-tool-<x>-cli` sits on, so a change here ripples into every downstream
tool and, through them, into every agent that has learned the contract. Read
[BLUEPRINT.md](BLUEPRINT.md) first — it is the engineering standard the whole
family is built to.

## The one rule

**The contract is published API. You may never break it silently.**

- **Never renumber an exit code.** Agents branch on `0/1/3/4/5/6/7/130`, and the
  numbers are documented in three places per tool (README, the in-binary `guide`,
  and the Claude skill). You may leave a code unallocated or repurpose one
  *deliberately and loudly*; you may never change what an allocated code means.
- **Never change an output shape** (`Emitter` JSON/table/markdown/csv, the
  `{"error": ..., "status": ...}` error envelope, NDJSON streaming) without
  treating it as a breaking change.

`tests/test_contract.py` is the tripwire. If a change makes you edit it, stop and
think — you are changing the thing every downstream tool depends on.

## What lives here — and what must not

Share the **contract**, not the **transport**. This package holds only what must be
*identical* across every tool:

| Module | What |
| --- | --- |
| `agentcli.errors` | The exit-code taxonomy + `DryRun` |
| `agentcli.output` | `Emitter`: json/table/markdown/csv, `--fields`, NDJSON |
| `agentcli.credentials` | Keyring storage with a `0600` fallback + env override |
| `agentcli.appspec` | `AppSpec` — the two strings that make the above tool-specific |

**Do not add a module because two tools happen to share it today.** `client.py`,
`serialize.py`, `resolve.py` and the domain commands stay in each tool — they look
shareable and are not (OpenProject paginates to an authoritative `total`; Drone has
no total and a short page *is* the terminator — the rule inverts). Wait until a
*third* tool needs it, and until you can state the rule it obeys.

## Contributing

You need nothing but Python — no network, no services:

```bash
pip install -e '.[test]'
pytest
```

Keep the package small. A framework with a config object per tool is how
shared-code projects die.
