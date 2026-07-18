# Building the next agent-tool CLI — everything learned across 5 tools

Companion to `BLUEPRINT.md` (the standard). This is the hard-won, less-obvious
stuff, distilled after openproject · drone · grafana · **lexware-office** + the
**lexware-office-api-mock**. Read this first for a new tool.

## The shape (unchanged, proven 5×)
Chassis on `agent-tool-shared-cli` (import `agentcli`, PyPI **0.1.2**): `spec.py`
(`AppSpec(name, env_prefix, token_env_aliases)` + `credentials`), `errors.py`
(re-export shared taxonomy + codes **from 8 up, only for observed conditions**),
`config.py`, `client.py` (transport — NOT shared), `appctx.py` (DI; NOT
`context.py`), `cli.py`, `__main__.py` (REQUIRED), `commands/` (one module/group +
`_shared.py`, `guide.py`, `auth.py`, `raw.py`, `settings.py`, `context.py`,
`install.py`). Copy the freshest sibling (grafana or lexware); swap names.

Exit codes are family API: `0 1 · 2=Click/never · 3 4 5 6 7 · 130`. 8+ per-tool.
**Reserved globals** stripped by `cli.py::_pop_globals`: `--format/-f --output/-o
--fields/--columns --dry-run --stream --no-context`. No command may declare them →
**file dests are `--out`**. `tests/test_globals_unit.py` enforces tree-wide.

## The killer-feature principle (saved to memory)
Find the number/answer the API **refuses** to give, derive it from primitives.
receivables/AR-aging (lexware), `alert route`/"who gets paged" (grafana),
`logs sources`/"what can I query" (grafana), build-by-commit (drone). Build it
early, top-level — it's the product.

## Reasoning is NOT observation — the single biggest lesson
Live checks disproved confident claims that survived a spike + review + agents,
EVERY tool: grafana "listing datasources needs Admin" (false — Viewer can);
Loki "seconds return nothing" (false — it's ms→1970, switch is digit-count);
level label name is version-dependent (`level` vs `detected_level`);
lexware stale-version is 406+IssueList not 409; sales-docs use regular envelope
not legacy. **Capture verbatim responses. Re-measure. When two agents disagree,
settle it against the live API, not by argument.**

## Test against YOUR OWN backend — two routes
1. **Self-hostable?** Boot it in Docker in CI (openproject/drone/grafana). Seed via
   the real API. Write-safety interlock (`*_ALLOW_WRITES=1`, only the bootstrap sets
   it) + a meta-test asserting it's armed. CI must **fail on silent live-tier skips**.
2. **No test instance? BUILD A STATEFUL MOCK** (lexware → `lexware-office-api-mock`,
   Express, 20 endpoints, 102 tests). It really stores/processes; reproduces edge
   cases (rate 429, 3 error envelopes, optimistic-lock, draft/finalize, derived
   status, PDF). Becomes the CLI's bootable CI backend (conftest boots `node
   src/server.js`, skips cleanly if absent). Publish it as its OWN repo (Docker/GHCR
   + optional npm). This is better than canned fixtures. When docs and mock differ,
   mock follows the REAL API (verify live).
- Hermetic tier: hand-rolled fake client (NOT httpx MockTransport — a transport mock
  tempts you to simulate the API, which is wrong in ways you'd encode wrong).
- conftest: snapshot env BEFORE the autouse hermetic fixture strips it (`live_env`),
  or live tests read an emptied `os.environ`. `make test-unit` = the MARKER, never a
  file list.

## Transport gotchas that DIFFER per API (why client.py is never shared)
- **Pagination inverts:** authoritative total → page-until-`last` (openproject,
  lexware Pageable). No total → short-page=end (drone, grafana search). Copying the
  wrong one truncates or loops forever.
- **Optimistic locking:** version/lockVersion on writes; stale → conflict (may wear a
  406/406-IssueList, not 409 — read the BODY). Read-modify-write the whole object.
- **Error envelopes vary** (lexware has THREE: regular / legacy IssueList / gateway).
  Map by inspecting body, not just status. NotFoundError & ApiError are SIBLINGS →
  end every ladder in `except OpError` (bit drone's doctor).
- **Rate limits** (lexware 2/s, no headers): client MUST self-pace — token bucket,
  **burst=1** (measured: burst>1 429s on the 3rd call), auto-delay every request;
  penalize/relax adapt to shared budget; retry is backstop. **429 can arrive as
  HTTP 500** ("...rate limit exceeded"); **504 may have SUCCEEDED** (don't blind-retry
  non-idempotent POST). Ref impl: `lexware/spike/reference/rate_limiter.py` (+tests).
- Money is NEVER a float — decimals, carried exactly, pure `money.py`. gross/net/tax.

## Command-surface rules
`guide` works with **no config/token/network** (`env -i … guide` → exit 0); has a
`gotchas` topic; a test resolves EVERY command the guide/SKILL names against the real
tree (drone shipped nonexistent `guide gotchas` + a `fields` command). `install
claude` SKILL.md: anchor every trigger to the product noun (bare "logs"/"invoice"
over-fire). `auth status` names WHICH backend spoke (env>keyring>file, never invert).
Findings ≠ failures: observations exit 0; `--exit-code` opts into 20+ band.
Fan out agents for bulk command modules; **orchestrator owns cli.py + chassis +
review**; give each agent the reference module + conventions + explicit file
ownership; reconcile disagreements live.

## Release scars (all cost real time)
- **Dynamic version** — `dynamic=["version"]` + `[tool.setuptools.dynamic] version={attr=…}`.
  Static `version=` in pyproject while bumping `__init__.py` → stale wheel → PyPI
  "File already exists" AFTER the tag/release cut (openproject 0.5.0 died this way).
- **Wheel-version guard** in release.yml: read version from BUILT WHEEL (job installs
  only `build`; importing dies on rich/httpx), fail if ≠ tag, before upload. +
  `skip-existing: true` so binary re-runs don't redden a good publish.
- **Publish shared-lib bumps BEFORE dependents**; PyPI simple-index lags the JSON API
  by minutes → a downstream pinning the just-published floor can fail; re-run fixes.
- **PyInstaller onefile: use an ABSOLUTE-import launcher** (`printf 'from pkg.cli
  import main\nmain()' > _launcher.py; pyinstaller --onefile --collect-submodules pkg
  _launcher.py`). Running `__main__.py` directly → "relative import, no parent package".
- **PyPI Trusted Publishing (OIDC):** pending publisher on pypi.org (Project name /
  Owner / Repo / Workflow `release.yml` / Environment `pypi`) + a GH env named `pypi`.
  No token stored. npm needs an `NPM_TOKEN` secret; GHCR uses built-in `GITHUB_TOKEN`.
- Generated .py: `write_text(..., encoding="utf-8")` + ASCII, or Windows dies.
  Bash `UID` is readonly.

## Security (non-negotiable)
`.gitignore` BEFORE the first secret-adjacent file; verify staged files before every
push. Never commit real keys, org ids, customer/host names, infra topology — those
live in gitignored `.env` / `spike/local-instance.md`. Scrub real data to synthetic
before anything reaches a public repo. Sandbox keys are still credentials.

## Naming
Command specific to the product (`lexware-office` not `lexware-cli` — they have many
products; `graf`/dist grafana because `grafana-cli` is Grafana's own binary). Refuse
PATH collisions with the vendor's own tools.

## Where the artifacts are
`_shared/BLUEPRINT.md` (standard). Per tool: `<tool>/BUILDING.md`, `spike/*`
(API map, VERIFIED/LIVE_FINDINGS, response examples, reference impls). Memory index:
`MEMORY.md` → killer-feature-principle, testing-against-live, per-tool project +
gotchas. lexware has the richest spike (no live instance → captured everything).

## Orchestration (how the work got done efficiently)
- **Scout inline, THEN fan out.** Discover the work-list yourself (list endpoints,
  read the docs, boot a probe), then Workflow-pipeline over it. Don't fan out before
  you know the shape.
- **Orchestrator owns the coherence anchors** — cli.py, the chassis, the shared model
  (e.g. mock `voucher.js`), the review. Agents own leaf modules + their tests, with
  explicit file ownership so they never collide.
- **Transient agent failures (safety-classifier "Stage 2" errors) are common and
  retryable** — resume the workflow (`resumeFromRunId`): completed agents replay from
  cache, only the failed ones re-run. Happened ~3× this session; retry always worked.
- **Verify agent output, don't trust it:** run the tests they claim green; when the
  claim is about API behavior, check it live. Two agents gave opposite envelope
  answers — the sandbox settled it.
- Capture verbatim responses via a fan-out when there's no live instance (lexware's
  6-agent doc-scrape → `spike/research/*.md`, 66 examples = the fixture corpus).

## Open items handed forward (state, not lessons)
- lexware mock: **npm publish needs an `NPM_TOKEN` secret** (GHCR Docker + PyPI CLI
  are LIVE at v0.1.0). npm judged optional — Docker is the real channel.
- lexware: ~9 of 20 endpoints are mock+docs-verified but NOT live-confirmed against
  the sandbox (esp. voucherlist `voucherType` filter values for non-invoice docs).
- Sandbox key is temporary/manually-provisioned; in gitignored `lexware/.env`.
- Full project state is in memory: `lexware-cli-project`, `lexware-ratelimit-retry`,
  `agent-tool-testing-against-live`, `agent-tool-killer-feature-principle`.
