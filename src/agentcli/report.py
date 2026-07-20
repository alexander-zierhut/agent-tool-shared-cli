"""`build_report` — the payload behind every tool's ``<cmd> report`` command.

Why this lives in the shared chassis, and why it is data not prose: once a tool
is ``pipx install``ed there is **no README and no AGENTS.md beside the binary**,
so the only place a user (or their agent) can learn *where a problem goes* is the
tool itself. That "how do I report this" answer is identical across the family —
same structure, same fields, only the repo slug and version differ — so it is
contract, and contract is shared.

The command is deliberately inert: no network, no token, no config read. It
prints a pre-filled ``issues/new`` link (opening the form needs no account) and a
``gh`` one-liner for whoever has the CLI. Filing the issue is still a human/agent
act — this only removes the "which repo, and what should I write" friction.
"""

from __future__ import annotations

from .appspec import AppSpec

# The structure we want every report to have. Pre-filled into the issue body so
# the reporter fills blanks instead of facing an empty box — and so triage gets
# the four things that actually make a CLI bug actionable.
BODY_TEMPLATE = """\
## What I was trying to do


## Command(s) I ran

```
<paste the exact command line(s)>
```

## What happened (error message / exit code)


## What would have made it work


---
_Filed via `{name} report` — version {version}_
"""

# What a good report carries, echoed in the payload so an agent composing a `gh`
# issue body knows what to gather without opening the template.
INCLUDE = [
    "what you were trying to do",
    "the exact command(s) you ran",
    "the JSON error and the exit code",
    "the tool version (`{name} --version`)",
    "what would have made it work",
]


def build_report(spec: AppSpec, version: str) -> dict:
    """Everything ``<cmd> report`` prints, as a JSON-contract dict.

    Pure and offline: derives entirely from *spec* and *version*. If the tool has
    no ``repo`` set yet, the URLs come back empty and ``published`` is ``False`` —
    the command still succeeds, it just cannot hand out a link.
    """
    body = BODY_TEMPLATE.format(name=spec.name, version=version)
    published = bool(spec.repo)
    return {
        "tool": spec.name,
        "version": version,
        "published": published,
        "repo": spec.repo or None,
        "issues": spec.issues_url() or None,
        # Pre-filled with the structured body and the version already inserted —
        # this is the token-free link the user just opens.
        "newIssue": spec.new_issue_url(body=body) or None,
        # For whoever has the GitHub CLI: files the issue directly.
        "gh": (
            f"gh issue create --repo {spec.repo} --title '<summary>' --body '<details>'"
            if published
            else None
        ),
        "include": [item.format(name=spec.name) for item in INCLUDE],
        "note": (
            "Opening the newIssue link needs no token or account (GitHub asks you "
            "to sign in only when you submit). Search existing issues first to "
            "avoid duplicates."
            if published
            else "This tool has no published repository set, so there is nowhere "
            "to file yet. Ask the maintainer, or set AppSpec.repo."
        ),
    }
