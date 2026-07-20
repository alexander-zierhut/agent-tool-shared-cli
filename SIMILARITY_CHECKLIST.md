# Family similarity checklist

Run this before you call a new `agent-tool-<x>-cli` "done". The whole point of the
family is that **an agent (or human) who has learned one tool already knows the
next one** — same output contract, same flags, same repo shape, same docs. Every
box below is something a sibling already does; a new tool that diverges makes the
reader relearn what they shouldn't have to.

Tick every box, or write down *why* this tool is the deliberate exception (a few
genuinely are — see "Allowed differences" at the end).

Reference implementations, freshest first: `grafana/`, `drone/`, `openproject/`,
`lexware/`. When in doubt, copy the newest sibling's shape.

## 1. Repository scaffolding

- [ ] `README.md` — follows the shared chrome (see §2).
- [ ] `AGENTS.md` — the machine contract, titled `# Using \`<cmd>\` from an AI agent`
      (drone/openproject/lexware style). Opens by pointing at `<cmd> guide`.
- [ ] `AGENTS.md` ends with a `## Reporting a problem — open a GitHub issue`
      section (identical wording across the family; only the repo slug and `<cmd>`
      differ) so an agent can file an issue on the right repo when the user asks.
- [ ] `LICENSE` — MIT, `Copyright (c) <year> Zierhut IT / Alexander Zierhut`
      (verbatim from any sibling).
- [ ] `Makefile` — targets `help install test test-unit lint docs clean` (plus any
      infra targets the backend needs, e.g. `up/wait/seed` for a Dockerable server).
- [ ] `docs/COMMANDS.md` — **auto-generated**, never hand-written.
- [ ] `scripts/gen_docs.py` — the generator, with the family-standard HEADER
      (copy a sibling's; change only the module import and the `<cmd>` prog name).
- [ ] `.github/workflows/ci.yml` + `release.yml` — CI runs the hermetic suite, the
      docs-drift check (`gen_docs` then `git diff --exit-code docs/`), and builds;
      release publishes to PyPI on a `v*` tag.
- [ ] `.gitignore` — same base as siblings (`.venv/`, `__pycache__/`, `*.egg-info/`,
      `build/`, `dist/`, `.pytest_cache/`, `.env*`, plus a spike-local line).

## 2. README shared chrome (keep the body tool-specific, standardize the frame)

- [ ] H1: `# <cmd> — the agent-ready <Product> CLI`.
- [ ] One-sentence `>` blockquote tagline naming the killer feature.
- [ ] Badge row: PyPI · CI · Python · `License: MIT` · `Agent ready` (libraries drop
      the agent-ready badge).
- [ ] `**Install:** pipx install <dist> — then run \`<cmd> guide\`.`
- [ ] `## The command surface` — the actual `--help` output in a ```text fence
      (generate with `NO_COLOR=1 COLUMNS=84 <cmd> --help`).
- [ ] `**Keywords:**` line for SEO (product + "AI agent tool, LLM tooling, Claude").
- [ ] `## Part of the family` — the shared chassis note + the family table (all four
      tools, identical across every README).
- [ ] `## License` → `MIT — see [LICENSE](LICENSE).`

## 3. pyproject.toml

- [ ] `name = "agent-tool-<x>-cli"`.
- [ ] `dynamic = ["version"]`, sourced from `<pkg>.__version__` (single source of
      truth — never duplicate the number in pyproject).
- [ ] `description` follows the pattern: *"Agent-ready command-line interface for
      <Product> — <killer feature>."*
- [ ] `keywords` — product terms + `ai-agent, llm, automation`.
- [ ] `classifiers` — Console / Developers / MIT / Py3 + one or two topic-specific.
- [ ] `[project.urls]` — Homepage, Repository, Issues, Changelog (all four).
- [ ] `[project.scripts]` — exactly one command; do not claim a name an upstream
      tool already owns (see §6).
- [ ] Depends on `agent-tool-shared-cli` pinned to a major (`>=X.Y,<X+1`).

## 4. CLI surface (from the shared chassis)

- [ ] Global options work **anywhere on the line**: `-o/--output`
      (json\|table\|markdown\|csv), `--format/-f`, `--fields`/`--columns`,
      `--dry-run`, `--stream`, `--no-context`. Root-only (before the subcommand):
      `--profile/-p`, `--no-color`, `--version/-V`.
- [ ] No command declares one of the popped global names as its own option.
- [ ] Ships these command groups: `guide`, `report`, `auth`, `context`, `settings`,
      `install`, `raw`. Plus the domain groups.
- [ ] `<cmd> guide` works with **no config, no token, no network**.
- [ ] `<cmd> report` works with **no config, no token, no network** — it prints
      `agentcli.build_report(SPEC, __version__)`: the repo, a pre-filled
      `issues/new` link and a `gh` one-liner. Requires `AppSpec(..., repo="owner/name")`.
      This is how an installed binary (no README/AGENTS.md beside it) tells a user
      where to file. Add `report` to the meta/no-prompt set in the root callback.
- [ ] Root help description matches the family voice: *"Agent-friendly CLI for
      <Product>: …"* + the JSON/stderr output note + a "New here? run `<cmd> guide`"
      pointer.

## 5. Output & exit-code contract (never renumber — it's published API)

- [ ] stdout is JSON; errors are `{"error": ..., "status": ...}` on stderr.
- [ ] Any non-JSON output (raw logs, PDF/binary) is a **documented carve-out** on
      the guide's first screen and in AGENTS.md.
- [ ] Exit codes: `0` ok (incl. `--dry-run`) · `1` generic · `3` config · `4` auth ·
      `5` not-found · `6` conflict · `7` validation · `130` SIGINT. `2` is reserved
      for Click/Typer. Tool-specific codes start at `8` and only for an **observed**
      condition.
- [ ] `--dry-run` is intercepted in the transport, so every write gets it.

## 6. Naming

- [ ] The command name does not clobber a binary users already have on PATH
      (drone → `drone-cli` not `drone`; grafana coexists with Grafana's own
      `grafana-cli`). Document any collision in the README.

## Allowed differences (state them, don't hide them)

Some things legitimately differ per tool — flag them explicitly rather than forcing
false uniformity:

- **Transport** (`client.py`, retry matrix, pagination, auth scheme, error-body
  shape) is **never shared** — it inverts between APIs (OpenProject paginates to an
  authoritative `total`; Drone has no total and a short page is the terminator).
- **The killer feature** is unique by design — it's the reason the tool exists.
- **Extra exit codes (`8+`)** are per-tool.
- **Infra Make targets / test backends** differ (Docker server vs. a mock vs. a
  captured-response fixture corpus).

If a box in §1–§6 is unticked, it should be because it's on this list — not because
it was missed.
